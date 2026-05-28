from __future__ import annotations

import os
from contextlib import nullcontext
from typing import Any


class LangSmithTracer:
    def __init__(self) -> None:
        self.api_key = os.getenv("LANGSMITH_API_KEY")
        self.tracing_enabled = os.getenv("LANGSMITH_TRACING", "false").strip().lower() in {"1", "true", "yes", "on"}
        self.project_name = os.getenv("LANGSMITH_PROJECT", "grabon-loop")
        self.endpoint = os.getenv("LANGSMITH_ENDPOINT") or None
        self.workspace_id = os.getenv("LANGSMITH_WORKSPACE_ID") or None
        self._client = None
        self._enabled = False
        self._init_client()

    def _init_client(self) -> None:
        if not self.api_key or not self.tracing_enabled:
            return
        try:
            from langsmith import Client
            kwargs: dict[str, Any] = {"api_key": self.api_key}
            if self.endpoint:
                kwargs["api_url"] = self.endpoint
            self._client = Client(**kwargs)
            self._enabled = True
        except Exception:
            self._client = None
            self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled and self._client is not None

    @property
    def client(self):
        return self._client

    def span(
        self,
        name: str,
        *,
        run_type: str = "chain",
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        parent=None,
    ):
        if not self.enabled:
            return nullcontext()
        from langsmith.run_helpers import trace

        return trace(
            name=name,
            run_type=run_type,
            inputs=inputs,
            metadata=metadata,
            tags=tags,
            project_name=self.project_name,
            parent=parent,
            client=self._client,
        )

    def flush(self) -> None:
        if not self.enabled:
            return
        try:
            self._client.flush()
        except Exception:
            pass


_TRACER: LangSmithTracer | None = None


def get_langsmith_tracer() -> LangSmithTracer:
    global _TRACER
    if _TRACER is None:
        _TRACER = LangSmithTracer()
    return _TRACER
