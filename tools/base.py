from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable, Type

from pydantic import BaseModel, ValidationError

from observability.langsmith_tracer import get_langsmith_tracer
from shared.schemas import ToolResult


@dataclass(frozen=True)
class ToolMeta:
    name: str
    timeout: float
    cost_usd: float
    tags: list[str]
    input_schema: str
    output_schema: str


@dataclass
class ToolExecution:
    data: dict[str, Any]
    tokens_used: int = 0
    cost_usd: float | None = None


class RegisteredTool:
    def __init__(
        self,
        fn: Callable,
        input_model: Type[BaseModel],
        output_model: Type[BaseModel],
        timeout: float,
        cost_usd: float,
        tags: list[str],
    ):
        self.fn = fn
        self.input_model = input_model
        self.output_model = output_model
        self.meta = ToolMeta(
            name=fn.__name__,
            timeout=timeout,
            cost_usd=cost_usd,
            tags=tags,
            input_schema=input_model.__name__,
            output_schema=output_model.__name__,
        )

    def invoke(self, payload: dict[str, Any], runtime: Any) -> ToolResult:
        started = perf_counter()
        tracer = get_langsmith_tracer()
        try:
            validated = self.input_model.model_validate(payload)
        except ValidationError as exc:
            result = ToolResult.fail(
                self.meta.name,
                "SCHEMA_ERROR",
                "Invalid tool input.",
                latency_ms=(perf_counter() - started) * 1000,
                details={"errors": exc.errors()},
            )
            if tracer.enabled:
                with tracer.span(
                    f"tool:{self.meta.name}",
                    run_type="tool",
                    inputs=payload,
                    metadata={"tool_name": self.meta.name, "status": "invalid_input"},
                ) as span:
                    if hasattr(span, "add_outputs"):
                        span.add_outputs(result.model_dump(mode="json"))
            return result

        def _run() -> ToolExecution:
            result = self.fn(validated, runtime)
            if isinstance(result, ToolExecution):
                return result
            if isinstance(result, dict):
                return ToolExecution(data=result)
            raise ValueError("Tool functions must return ToolExecution or dict.")

        with tracer.span(
            f"tool:{self.meta.name}",
            run_type="tool",
            inputs=payload,
            metadata={"tool_name": self.meta.name, "timeout_s": self.meta.timeout, "cost_usd": self.meta.cost_usd},
        ) as span:
            try:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    execution = executor.submit(_run).result(timeout=self.meta.timeout)
                validated_output = self.output_model.model_validate(execution.data)
                result = ToolResult.ok(
                    self.meta.name,
                    validated_output.model_dump(mode="json"),
                    latency_ms=(perf_counter() - started) * 1000,
                    cost_usd=self.meta.cost_usd if execution.cost_usd is None else execution.cost_usd,
                    tokens_used=execution.tokens_used,
                )
            except FuturesTimeoutError:
                result = ToolResult.fail(
                    self.meta.name,
                    "TIMEOUT",
                    f"{self.meta.name} timed out after {self.meta.timeout}s.",
                    latency_ms=(perf_counter() - started) * 1000,
                    cost_usd=self.meta.cost_usd,
                )
            except ValidationError as exc:
                result = ToolResult.fail(
                    self.meta.name,
                    "SCHEMA_ERROR",
                    "Invalid tool output.",
                    latency_ms=(perf_counter() - started) * 1000,
                    cost_usd=self.meta.cost_usd,
                    details={"errors": exc.errors()},
                )
            except Exception as exc:  # noqa: BLE001
                message = str(exc)
                error_type = "TOOL_ERROR"
                error_message = message
                if "::" in message:
                    error_type, error_message = message.split("::", 1)
                result = ToolResult.fail(
                    self.meta.name,
                    error_type,
                    error_message,
                    latency_ms=(perf_counter() - started) * 1000,
                    cost_usd=self.meta.cost_usd,
                )
            if hasattr(span, "add_outputs"):
                span.add_outputs(result.model_dump(mode="json"))
            return result


def tool(*, input_model: Type[BaseModel], output_model: Type[BaseModel], timeout: float, cost_usd: float, tags: list[str]):
    def decorator(fn: Callable):
        fn._tool = RegisteredTool(
            fn=fn,
            input_model=input_model,
            output_model=output_model,
            timeout=timeout,
            cost_usd=cost_usd,
            tags=tags,
        )
        return fn

    return decorator
