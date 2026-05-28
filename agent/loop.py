from __future__ import annotations

from datetime import datetime
from typing import Any

from agent.budget import BudgetExceeded, BudgetSentinel
from agent.decider import DecisionAction, Decider
from agent.observer import Observer
from agent.planner import Planner, StepPlan
from observability.langsmith_tracer import get_langsmith_tracer
from observability.state_store import StateStore
from shared.schemas import AuditReport, LoopIteration, MerchantAuditResult, MerchantConfig, ToolResult
from tools.registry import ToolRegistry


class ToolRuntime:
    def __init__(self, state_store: StateStore, model_router, mock_db: dict[str, list[dict[str, Any]]], scenario: dict[str, Any] | None = None):
        self.state_store = state_store
        self.model_router = model_router
        self.mock_db = mock_db
        self.scenario = scenario or {}

    def fixture_html(self, merchant_name: str, source: str) -> str:
        merchant_fixture = self.scenario.get("merchant_fixtures", {}).get(merchant_name, {})
        if "html" in merchant_fixture:
            return merchant_fixture["html"]
        first_deal = self.mock_db_for(merchant_name)[:1]
        title = first_deal[0]["title"] if first_deal else f"{merchant_name} deal"
        return f"<html><body><h1>{merchant_name}</h1><div data-source='{source}'>{title}</div></body></html>"

    def fixture_deals(self, merchant_name: str) -> list[dict[str, Any]]:
        merchant_fixture = self.scenario.get("merchant_fixtures", {}).get(merchant_name, {})
        if "extracted_deals" in merchant_fixture:
            return merchant_fixture["extracted_deals"]
        return self.mock_db_for(merchant_name)[:2]

    def mock_db_for(self, merchant_name: str) -> list[dict[str, Any]]:
        return list(self.mock_db.get(merchant_name, []))

    def days_since_last_update(self, merchant_name: str) -> int:
        merchant_fixture = self.scenario.get("merchant_fixtures", {}).get(merchant_name, {})
        return merchant_fixture.get("days_since_last_update", 5)

    def record_provider_call(self, provider: str, model: str, purpose: str, tokens_used: int) -> None:
        calls = self.state_store.runtime_scenario.setdefault("_provider_calls", [])
        calls.append({"provider": provider, "model": model, "purpose": purpose, "tokens_used": tokens_used})


class AgentLoop:
    def __init__(
        self,
        registry: ToolRegistry,
        budget: BudgetSentinel,
        state_store: StateStore,
        model_router,
        mock_db: dict[str, list[dict[str, Any]]],
        scenario: dict[str, Any] | None = None,
        on_iteration=None,
    ):
        self.registry = registry
        self.budget = budget
        self.state_store = state_store
        self.model_router = model_router
        self.loop_model_router = None if scenario else model_router
        self.runtime = ToolRuntime(state_store, model_router, mock_db, scenario)
        self.tracer = get_langsmith_tracer()
        self.planner = Planner()
        self.observer = Observer()
        self.decider = Decider(self.loop_model_router)
        self._step_number = 0
        self._merchant_order: list[str] = []
        self._current_merchant: str | None = None
        self.on_iteration = on_iteration

    def run(self, merchants: list[MerchantConfig]) -> AuditReport:
        self._merchant_order = [merchant.name for merchant in merchants]
        try:
            for merchant in merchants:
                self._current_merchant = merchant.name
                self._bootstrap_state(merchant)
                self._run_merchant(merchant)
        except BudgetExceeded as exc:
            self.state_store.halt_report = self.state_store.build_halt_report(exc.reason, self._merchant_order, self._current_merchant, self.budget)
        return self._compile_report()

    def _bootstrap_state(self, merchant: MerchantConfig) -> None:
        scenario_state = self.runtime.scenario.get("initial_state", {}).get(merchant.name, {})
        if scenario_state:
            self.state_store.merchant_state(merchant.name).update(scenario_state)

    def _run_merchant(self, merchant: MerchantConfig) -> None:
        while not self._is_done(merchant.name):
            with self.budget.check():
                self._step_number += 1
                plan = self.plan(merchant)
                result = self.act(plan, merchant)
                observation = self.observe(result)
                decision = self.decide(observation, merchant.name)
                if decision.action == DecisionAction.DONE:
                    self.state_store.mark_done(merchant.name)
                    break
                if decision.action == DecisionAction.IMPOSSIBLE:
                    self.state_store.mark_impossible(merchant.name, decision.reason)
                    break
                if decision.action == DecisionAction.DEGRADED:
                    self.state_store.mark_degraded(merchant.name, decision.reason)
                    break

    def _is_done(self, merchant: str) -> bool:
        return self.state_store.merchant_state(merchant).get("status") in {"DONE", "IMPOSSIBLE", "DEGRADED"}

    def _record_phase(
        self,
        *,
        merchant: str,
        phase: str,
        action: str,
        tool_called: str | None = None,
        observation: str | None = None,
        decision: str | None = None,
        tokens: int = 0,
        wall_clock_ms: float = 0.0,
    ) -> None:
        self.state_store.update(merchant, current_phase=phase)
        self.state_store.record_iteration(
            LoopIteration(
                step_number=self._step_number,
                merchant=merchant,
                phase=phase,
                action=action,
                tool_called=tool_called,
                observation=observation,
                decision=decision,
                tokens_consumed=tokens,
                wall_clock_ms=round(wall_clock_ms, 2),
                timestamp=datetime.utcnow(),
            )
        )
        if self.on_iteration is not None:
            self.on_iteration()

    def plan(self, merchant: MerchantConfig) -> StepPlan:
        state = self.state_store.merchant_state(merchant.name)
        available = {meta.name for meta in self.registry.list_available()}
        with self.tracer.span(
            f"phase:PLAN:{merchant.name}",
            run_type="chain",
            inputs={"merchant": merchant.name, "state_keys": list(state.keys()), "available_tools": sorted(available)},
            metadata={"phase": "PLAN", "merchant": merchant.name},
        ) as span:
            fallback_plan = self.planner.next_step(merchant, state, available)
            plan = fallback_plan
            if self.loop_model_router is not None and self.loop_model_router.can_use_plan_provider():
                try:
                    payload, tokens = self.loop_model_router.groq_plan(
                        merchant.name,
                        state,
                        merchant.model_dump(mode="json"),
                        sorted(available),
                    )
                    self.runtime.record_provider_call(self.loop_model_router.plan_model.provider, self.loop_model_router.plan_model.model, "loop_plan", tokens)
                    if (
                        str(payload.get("action")) == fallback_plan.action
                        and str(payload.get("tool_name")) == fallback_plan.tool_name
                        and str(payload.get("rationale", "")).strip()
                    ):
                        plan = StepPlan(
                            merchant=merchant.name,
                            action=fallback_plan.action,
                            tool_name=fallback_plan.tool_name,
                            rationale=str(payload["rationale"]).strip(),
                        )
                except Exception:
                    plan = fallback_plan
            if hasattr(span, "add_outputs"):
                span.add_outputs(plan.model_dump(mode="json"))
        self._record_phase(merchant=merchant.name, phase="PLAN", action=plan.action, tool_called=plan.tool_name, decision=plan.rationale)
        return plan

    def act(self, plan: StepPlan, merchant: MerchantConfig) -> ToolResult:
        with self.tracer.span(
            f"phase:ACT:{merchant.name}",
            run_type="chain",
            inputs={"merchant": merchant.name, "tool": plan.tool_name, "action": plan.action},
            metadata={"phase": "ACT", "merchant": merchant.name, "tool": plan.tool_name},
        ) as span:
            act_note = "Tool executed."
            if self.loop_model_router is not None and self.loop_model_router.can_use_act_provider():
                try:
                    payload, tokens = self.loop_model_router.groq_act(
                        merchant.name,
                        plan.tool_name,
                        self.state_store.merchant_state(merchant.name),
                    )
                    self.runtime.record_provider_call(self.loop_model_router.plan_model.provider, self.loop_model_router.plan_model.model, "loop_act", tokens)
                    if bool(payload.get("proceed")) and str(payload.get("tool_name")) == plan.tool_name and str(payload.get("note", "")).strip():
                        act_note = str(payload["note"]).strip()
                except Exception:
                    act_note = "Tool executed."
            tool = self.registry.get(plan.tool_name)
            if tool is None:
                result = ToolResult.tool_unavailable(plan.tool_name)
            else:
                self.state_store.increment_attempt(merchant.name, plan.tool_name)
                result = tool.invoke({"merchant": merchant.model_dump(mode="python"), "state": self.state_store.merchant_state(merchant.name)}, self.runtime)
                self._apply_tool_result(merchant.name, plan.tool_name, result)
            self.state_store.record_tool_result(merchant.name, result)
            self.budget.record_tool_call(tokens_used=result.tokens_used, success=result.success)
            self.state_store.record_token_point(self._step_number, self.budget.tokens_used)
            if hasattr(span, "add_outputs"):
                span.add_outputs({"note": act_note, "result": result.model_dump(mode="json")})
            self._record_phase(
                merchant=merchant.name,
                phase="ACT",
                action=plan.action,
                tool_called=plan.tool_name,
                observation=act_note if tool is not None else "Tool unavailable.",
                tokens=result.tokens_used,
                wall_clock_ms=result.latency_ms,
            )
            return result

    def _apply_tool_result(self, merchant: str, tool_name: str, result: ToolResult) -> None:
        if not result.success or not result.data:
            return
        if tool_name in {"fetch_html", "render_js", "fetch_google_cache", "unreliable_fetch"}:
            self.state_store.update(merchant, page_html=result.data["html"], page_source=result.data["source"], next_tool=None)
        elif tool_name == "extract_deals":
            self.state_store.update(merchant, extracted_deals=result.data["deals"], next_tool=None)
        elif tool_name == "audit_deals":
            self.state_store.update(merchant, audited_deals=result.data["classified_deals"], health_score=result.data["health_score"], next_tool=None)
        elif tool_name == "write_report":
            self.state_store.update(
                merchant,
                report_markdown=result.data["markdown"],
                report_structured=result.data["structured"],
                report_written=True,
                provider_calls=self.state_store.runtime_scenario.get("_provider_calls", []),
                next_tool=None,
            )

    def observe(self, result: ToolResult):
        with self.tracer.span(
            f"phase:OBSERVE:{result.tool_name}",
            run_type="chain",
            inputs=result.model_dump(mode="json"),
            metadata={"phase": "OBSERVE", "tool": result.tool_name},
        ) as span:
            fallback_observation = self.observer.observe(result)
            observation = fallback_observation
            if self.loop_model_router is not None and self.loop_model_router.can_use_observe_provider():
                try:
                    payload, tokens = self.loop_model_router.groq_observe(
                        {
                            "tool_name": result.tool_name,
                            "success": result.success,
                            "error_type": result.error.error_type if result.error else None,
                            "message": result.error.message if result.error else None,
                        }
                    )
                    self.runtime.record_provider_call(self.loop_model_router.plan_model.provider, self.loop_model_router.plan_model.model, "loop_observe", tokens)
                    if (
                        bool(payload.get("success")) == fallback_observation.success
                        and str(payload.get("tool_name")) == result.tool_name
                        and str(payload.get("summary", "")).strip()
                    ):
                        observation = type(fallback_observation)(
                            success=fallback_observation.success,
                            summary=str(payload["summary"]).strip(),
                            tool_result=result,
                        )
                except Exception:
                    observation = fallback_observation
            if hasattr(span, "add_outputs"):
                span.add_outputs({"summary": observation.summary, "success": observation.success})
        self._record_phase(
            merchant=self._current_merchant or "UNKNOWN",
            phase="OBSERVE",
            action="observe_result",
            tool_called=result.tool_name,
            observation=observation.summary,
            tokens=result.tokens_used,
            wall_clock_ms=result.latency_ms,
        )
        return observation

    def decide(self, observation, merchant: str):
        with self.tracer.span(
            f"phase:DECIDE:{merchant}",
            run_type="chain",
            inputs={"merchant": merchant, "observation": observation.summary, "success": observation.success},
            metadata={"phase": "DECIDE", "merchant": merchant},
        ) as span:
            decision = self.decider.decide(self.state_store.merchant_state(merchant), observation, merchant)
            if self.loop_model_router is not None and self.loop_model_router.can_use_error_provider() and self.decider.last_provider_tokens:
                self.runtime.record_provider_call(
                    self.loop_model_router.error_model.provider,
                    self.loop_model_router.error_model.model,
                    "loop_decide",
                    self.decider.last_provider_tokens,
                )
            self.state_store.add_reasoning(merchant, decision.reason)
            if hasattr(span, "add_outputs"):
                span.add_outputs({"action": decision.action.value, "reason": decision.reason})
            self._record_phase(
                merchant=merchant,
                phase="DECIDE",
                action="choose_next_step",
                tool_called=observation.tool_result.tool_name,
                observation=observation.summary,
                decision=decision.reason,
                tokens=observation.tool_result.tokens_used,
                wall_clock_ms=observation.tool_result.latency_ms,
            )
            return decision

    def _compile_report(self) -> AuditReport:
        return AuditReport(
            model_routing=self.model_router.describe(),
            merchant_results=[
                MerchantAuditResult(
                    merchant=merchant_name,
                    status=self.state_store.merchant_state(merchant_name).get("status", "PENDING"),
                    health_score=self.state_store.merchant_state(merchant_name).get("health_score"),
                    summary=self.state_store.merchant_state(merchant_name).get("report_markdown", ""),
                    deals=self.state_store.merchant_state(merchant_name).get("audited_deals", []),
                    reason=self.state_store.merchant_state(merchant_name).get("reason"),
                )
                for merchant_name in self._merchant_order or list(self.state_store.merchants.keys())
            ],
            halt_report=self.state_store.halt_report,
            iterations=self.state_store.iterations,
        )
