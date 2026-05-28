from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agent.budget import BudgetConfig, BudgetSentinel
from agent.loop import AgentLoop
from agent.model_router import ModelRouter
from observability.langsmith_tracer import get_langsmith_tracer
from observability.state_store import StateStore
from shared.schemas import MerchantConfig
from tools.registry import ToolRegistry


@dataclass
class ScenarioResult:
    name: str
    passed: bool
    expected: dict[str, Any]
    actual: dict[str, Any]
    failures: list[str]


class EvalRunner:
    def __init__(self, project_root: Path, scenarios_dir: Path):
        self.project_root = project_root
        self.scenarios_dir = scenarios_dir
        rows = json.loads((project_root / "data" / "mock_db.json").read_text(encoding="utf-8"))
        self.mock_db: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            self.mock_db.setdefault(row["merchant"], []).append(row)

    def run_scenario(self, scenario_path: Path) -> ScenarioResult:
        scenario = yaml.safe_load(scenario_path.read_text(encoding="utf-8"))
        merchants = [MerchantConfig(**merchant) for merchant in scenario["merchants"]]
        state_store = StateStore()
        state_store.configure_scenario(scenario)
        registry = ToolRegistry()
        registry.load_all()
        loop = AgentLoop(
            registry=registry,
            budget=BudgetSentinel(BudgetConfig(**scenario.get("budget", {}))),
            state_store=state_store,
            model_router=ModelRouter(),
            mock_db=self.mock_db,
            scenario=scenario,
        )
        tracer = get_langsmith_tracer()
        with tracer.span(
            f"eval:{scenario['name']}",
            run_type="chain",
            inputs={"scenario": scenario["name"], "merchant_count": len(merchants)},
            metadata={"entrypoint": "eval.runner", "scenario_path": str(scenario_path)},
        ) as run:
            report = loop.run(merchants)
            if hasattr(run, "add_outputs"):
                run.add_outputs(
                    {
                        "passed": True,
                        "merchant_statuses": {item.merchant: item.status for item in report.merchant_results},
                        "halt_reason": report.halt_report.reason if report.halt_report else None,
                    }
                )
        actual = self._collect_actual(report, state_store, merchants)
        expected = scenario.get("expected", {})
        failures = self._compare(expected, actual)
        if tracer.enabled:
            try:
                tracer.flush()
            except Exception:
                pass
        return ScenarioResult(
            name=scenario["name"],
            passed=not failures,
            expected=expected,
            actual=actual,
            failures=failures,
        )

    def run_all(self) -> list[ScenarioResult]:
        return [self.run_scenario(path) for path in sorted(self.scenarios_dir.glob("*.yaml"))]

    def _collect_actual(self, report, state_store, merchants) -> dict[str, Any]:
        statuses = {item.merchant: item.status for item in report.merchant_results}
        tools_used: dict[str, list[str]] = {}
        for item in state_store.tool_history:
            tools_used.setdefault(item["merchant"], []).append(item["tool"])
        reasoning = {merchant.name: state_store.merchant_state(merchant.name).get("reasoning", []) for merchant in merchants}
        phase_trace: dict[str, list[str]] = {}
        step_trace: dict[str, list[dict[str, Any]]] = {}
        for iteration in state_store.iterations:
            phase_trace.setdefault(iteration.merchant, []).append(iteration.phase)
            step_trace.setdefault(iteration.merchant, []).append(
                {
                    "step_number": iteration.step_number,
                    "phase": iteration.phase,
                    "action": iteration.action,
                    "tool_called": iteration.tool_called,
                    "observation": iteration.observation,
                    "decision": iteration.decision,
                }
            )
        return {
            "statuses": statuses,
            "halt_reason": report.halt_report.reason if report.halt_report else None,
            "tools_used": tools_used,
            "reasoning": reasoning,
            "phase_trace": phase_trace,
            "step_trace": step_trace,
        }

    def _compare(self, expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
        failures: list[str] = []
        for merchant, status in expected.get("statuses", {}).items():
            if actual["statuses"].get(merchant) != status:
                failures.append(f"{merchant} expected status {status} but got {actual['statuses'].get(merchant)}")
        if expected.get("halt_reason") != actual.get("halt_reason"):
            if expected.get("halt_reason") is not None:
                failures.append(f"Expected halt reason {expected['halt_reason']} but got {actual.get('halt_reason')}")
        for merchant, required_tools in expected.get("tools_used_contains", {}).items():
            used = actual["tools_used"].get(merchant, [])
            for tool_name in required_tools:
                if tool_name not in used:
                    failures.append(f"{merchant} did not use expected tool {tool_name}")
        for merchant, snippets in expected.get("reasoning_contains", {}).items():
            reasoning_text = " ".join(actual["reasoning"].get(merchant, []))
            for snippet in snippets:
                if snippet not in reasoning_text:
                    failures.append(f"{merchant} reasoning missing snippet: {snippet}")
        return failures
