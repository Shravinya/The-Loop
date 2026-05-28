from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any

from shared.schemas import HaltReport, LoopIteration, MerchantResultSummary, PartialMerchantResult


class StateStore:
    def __init__(self) -> None:
        self.merchants: dict[str, dict[str, Any]] = defaultdict(dict)
        self.iterations: list[LoopIteration] = []
        self.halt_report: HaltReport | None = None
        self.tool_history: list[dict[str, Any]] = []
        self.token_curve: list[dict[str, Any]] = []
        self.runtime_scenario: dict[str, Any] = {}

    def configure_scenario(self, scenario: dict[str, Any] | None) -> None:
        self.runtime_scenario = deepcopy(scenario or {})

    def merchant_state(self, merchant: str) -> dict[str, Any]:
        state = self.merchants[merchant]
        state.setdefault("status", "PENDING")
        state.setdefault("reasoning", [])
        state.setdefault("tool_attempts", {})
        state.setdefault("failed_tools", [])
        state.setdefault("next_tool", None)
        state.setdefault("report_written", False)
        return state

    def update(self, merchant: str, **kwargs) -> None:
        self.merchant_state(merchant).update(kwargs)

    def increment_attempt(self, merchant: str, tool_name: str) -> int:
        state = self.merchant_state(merchant)
        attempts = state["tool_attempts"].get(tool_name, 0) + 1
        state["tool_attempts"][tool_name] = attempts
        return attempts

    def add_reasoning(self, merchant: str, message: str) -> None:
        self.merchant_state(merchant)["reasoning"].append(message)

    def record_tool_result(self, merchant: str, result) -> None:
        state = self.merchant_state(merchant)
        state["last_tool"] = result.tool_name
        state["last_latency_ms"] = result.latency_ms
        state["last_success"] = result.success
        if not result.success:
            state["last_error_type"] = result.error.error_type if result.error else "UNKNOWN"
            state["failed_tools"].append(result.tool_name)
        self.tool_history.append(
            {
                "merchant": merchant,
                "tool": result.tool_name,
                "success": result.success,
                "latency_ms": round(result.latency_ms, 2),
                "error_type": result.error.error_type if result.error else None,
            }
        )

    def record_iteration(self, iteration: LoopIteration) -> None:
        self.iterations.append(iteration)

    def record_token_point(self, step_number: int, tokens_used: int) -> None:
        self.token_curve.append({"step_number": step_number, "tokens_used": tokens_used})

    def mark_done(self, merchant: str) -> None:
        self.merchant_state(merchant)["status"] = "DONE"

    def mark_impossible(self, merchant: str, reason: str) -> None:
        self.merchant_state(merchant).update(status="IMPOSSIBLE", reason=reason)

    def mark_degraded(self, merchant: str, reason: str) -> None:
        self.merchant_state(merchant).update(status="DEGRADED", reason=reason)

    def consume_tool_injection(self, merchant: str, tool_name: str) -> dict[str, Any] | None:
        merchant_overrides = self.runtime_scenario.get("tool_outcomes", {}).get(merchant, {})
        outcomes = merchant_overrides.get(tool_name, [])
        return outcomes.pop(0) if outcomes else None

    def build_halt_report(self, reason: str, merchant_order: list[str], current_merchant: str | None, budget) -> HaltReport:
        completed: list[MerchantResultSummary] = []
        partial: list[PartialMerchantResult] = []
        remaining: list[str] = []
        current_seen = False

        for merchant in merchant_order:
            state = self.merchant_state(merchant)
            status = state.get("status", "PENDING")
            if status == "DONE":
                completed.append(MerchantResultSummary(merchant=merchant, status=status, score=state.get("health_score")))
                continue
            if merchant == current_merchant or status not in {"DONE", "PENDING"}:
                current_seen = True
                partial.append(
                    PartialMerchantResult(
                        merchant=merchant,
                        phase=state.get("current_phase", "UNKNOWN"),
                        reason=state.get("reason", "Execution halted before completion."),
                    )
                )
                continue
            if not current_seen and status == "PENDING":
                remaining.append(merchant)
            elif current_seen:
                remaining.append(merchant)

        return HaltReport(
            reason=reason,
            completed_merchants=completed,
            remaining_merchants=remaining,
            partial_merchants=partial,
            tokens_used=budget.tokens_used,
            elapsed_seconds=budget.elapsed_seconds,
            tool_calls_made=budget.tool_calls_made,
            consecutive_failures_at_halt=budget.consecutive_failures,
        )
