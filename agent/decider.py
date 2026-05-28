from __future__ import annotations

from copy import deepcopy
from enum import Enum
from time import sleep

from pydantic import BaseModel

from agent.model_router import ModelRouter
from observability.langsmith_tracer import get_langsmith_tracer
from recovery.error_classifier import ErrorClassifier, ErrorType
from recovery.strategies import GracefulDegrade, ReplanAltTool, RetryBackoff


class DecisionAction(str, Enum):
    CONTINUE = "CONTINUE"
    REPLAN = "REPLAN"
    DONE = "DONE"
    IMPOSSIBLE = "IMPOSSIBLE"
    DEGRADED = "DEGRADED"


class Decision(BaseModel):
    action: DecisionAction
    reason: str


class Decider:
    def __init__(self, model_router: ModelRouter | None = None) -> None:
        self.model_router = model_router
        self.error_classifier = ErrorClassifier()
        self.last_provider_tokens = 0
        self.tracer = get_langsmith_tracer()

    def decide(self, merchant_state: dict, observation, merchant_name: str) -> Decision:
        self.last_provider_tokens = 0
        fallback_state = deepcopy(merchant_state)
        fallback_decision = self._deterministic_decide(fallback_state, observation, merchant_name)

        if self.model_router is not None and self.model_router.can_use_error_provider():
            try:
                payload, tokens = self.model_router.groq_decide(
                    merchant_name,
                    merchant_state,
                    {
                        "success": observation.success,
                        "summary": observation.summary,
                        "tool_name": observation.tool_result.tool_name,
                        "error_type": observation.tool_result.error.error_type if observation.tool_result.error else None,
                    },
                )
                self.last_provider_tokens = tokens
                action = DecisionAction(str(payload["action"]))
                reason = str(payload["reason"])
                if action == fallback_decision.action and reason:
                    self._deterministic_decide(merchant_state, observation, merchant_name)
                    return Decision(action=action, reason=reason)
            except Exception:
                pass

        return self._deterministic_decide(merchant_state, observation, merchant_name)

    def _deterministic_decide(self, merchant_state: dict, observation, merchant_name: str) -> Decision:
        if observation.success:
            merchant_state["next_tool"] = None
            if merchant_state.get("report_written"):
                return Decision(action=DecisionAction.DONE, reason=f"{merchant_name} report completed.")
            return Decision(action=DecisionAction.CONTINUE, reason="Move to the next planned step.")

        result = observation.tool_result
        error_type = self.error_classifier.classify(result.error)
        current_tool = result.tool_name

        if error_type in {ErrorType.TRANSIENT_RATE_LIMIT, ErrorType.TRANSIENT_TIMEOUT}:
            attempt = merchant_state["tool_attempts"].get(current_tool, 1)
            if attempt <= len(RetryBackoff.delays):
                delay_seconds = RetryBackoff.delays[attempt - 1]
                sleep(min(delay_seconds, 0.01))
                merchant_state["next_tool"] = current_tool
                decision = Decision(
                    action=DecisionAction.REPLAN,
                    reason=f"Transient {error_type.value}; retry {current_tool} with backoff attempt {attempt}.",
                )
                self._trace_recovery(merchant_name, current_tool, error_type, decision, {"attempt": attempt, "delay_seconds": delay_seconds})
                return decision
            decision = Decision(
                action=DecisionAction.DEGRADED,
                reason=f"{GracefulDegrade.status()} after exhausting retries for {current_tool}.",
            )
            self._trace_recovery(merchant_name, current_tool, error_type, decision, {"attempt": attempt})
            return decision

        if error_type in {ErrorType.PERSISTENT_BLOCK, ErrorType.JS_RENDER_REQUIRED, ErrorType.TOOL_UNAVAILABLE}:
            next_tool = ReplanAltTool.next_tool(current_tool, merchant_state.get("failed_tools", []))
            if next_tool:
                merchant_state["next_tool"] = next_tool
                decision = Decision(
                    action=DecisionAction.REPLAN,
                    reason=f"Persistent failure {error_type.value}; switching from {current_tool} to {next_tool}.",
                )
                self._trace_recovery(merchant_name, current_tool, error_type, decision, {"next_tool": next_tool})
                return decision
            decision = Decision(
                action=DecisionAction.DEGRADED,
                reason=f"No alternative tool left after {current_tool} failed with {error_type.value}.",
            )
            self._trace_recovery(merchant_name, current_tool, error_type, decision, {"failed_tools": merchant_state.get("failed_tools", [])})
            return decision

        if error_type == ErrorType.NO_STRUCTURE:
            attempt = merchant_state["tool_attempts"].get(current_tool, 1)
            if attempt < 2:
                merchant_state["next_tool"] = "fetch_google_cache"
                merchant_state.pop("page_html", None)
                decision = Decision(
                    action=DecisionAction.REPLAN,
                    reason="Extraction failed; re-fetch via cache once before marking impossible.",
                )
                self._trace_recovery(merchant_name, current_tool, error_type, decision, {"next_tool": "fetch_google_cache", "attempt": attempt})
                return decision
            decision = Decision(
                action=DecisionAction.IMPOSSIBLE,
                reason="No extractable structure found after two attempts.",
            )
            self._trace_recovery(merchant_name, current_tool, error_type, decision, {"attempt": attempt})
            return decision

        if error_type == ErrorType.PERMANENT_NOT_FOUND:
            decision = Decision(action=DecisionAction.IMPOSSIBLE, reason="Permanent 404; stop immediately.")
            self._trace_recovery(merchant_name, current_tool, error_type, decision, {})
            return decision

        decision = Decision(action=DecisionAction.DEGRADED, reason=f"Unhandled error type {error_type.value}; degraded.")
        self._trace_recovery(merchant_name, current_tool, error_type, decision, {})
        return decision

    def _trace_recovery(
        self,
        merchant_name: str,
        current_tool: str,
        error_type: ErrorType,
        decision: Decision,
        details: dict,
    ) -> None:
        with self.tracer.span(
            f"recovery:{decision.action.value.lower()}",
            run_type="chain",
            inputs={
                "merchant": merchant_name,
                "tool": current_tool,
                "error_type": error_type.value,
                **details,
            },
            tags=["recovery", "fallback", decision.action.value.lower()],
        ) as span:
            if hasattr(span, "add_outputs"):
                span.add_outputs({"action": decision.action.value, "reason": decision.reason})
