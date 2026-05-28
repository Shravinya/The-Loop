from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from time import perf_counter


@dataclass
class BudgetConfig:
    max_tokens: int = 100_000
    max_wall_clock_seconds: float = 900.0
    max_tool_calls: int = 200
    max_consecutive_failures: int = 5


class BudgetExceeded(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class _BudgetCheck(AbstractContextManager):
    def __init__(self, sentinel: "BudgetSentinel"):
        self.sentinel = sentinel

    def __enter__(self) -> "_BudgetCheck":
        self.sentinel._ensure_within_budget()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.sentinel._ensure_within_budget()
        return None


class BudgetSentinel:
    def __init__(self, config: BudgetConfig):
        self.config = config
        self.started_at = perf_counter()
        self.tokens_used = 0
        self.tool_calls_made = 0
        self.consecutive_failures = 0

    def check(self) -> _BudgetCheck:
        return _BudgetCheck(self)

    @property
    def elapsed_seconds(self) -> float:
        return perf_counter() - self.started_at

    def record_tool_call(self, *, tokens_used: int = 0, success: bool = True) -> None:
        self.tool_calls_made += 1
        self.tokens_used += max(tokens_used, 0)
        self.consecutive_failures = 0 if success else self.consecutive_failures + 1
        self._ensure_within_budget()

    def _ensure_within_budget(self) -> None:
        if self.tokens_used >= self.config.max_tokens:
            raise BudgetExceeded("MAX_TOKENS")
        if self.elapsed_seconds >= self.config.max_wall_clock_seconds:
            raise BudgetExceeded("WALL_CLOCK")
        if self.tool_calls_made >= self.config.max_tool_calls:
            raise BudgetExceeded("MAX_TOOL_CALLS")
        if self.consecutive_failures >= self.config.max_consecutive_failures:
            raise BudgetExceeded("CONSECUTIVE_FAILURES")
