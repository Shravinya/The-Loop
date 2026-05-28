from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter

import yaml
from dotenv import load_dotenv
from rich.console import Console
from rich.live import Live

from agent.budget import BudgetConfig, BudgetSentinel
from agent.loop import AgentLoop
from agent.model_router import ModelRouter
from observability.dashboard import build_dashboard
from observability.langsmith_tracer import get_langsmith_tracer
from observability.state_store import StateStore
from shared.schemas import MerchantConfig
from tools.registry import ToolRegistry


def load_merchants(path: Path) -> list[MerchantConfig]:
    rows = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [MerchantConfig.model_validate(row) for row in rows]


def load_mock_db(path: Path) -> dict[str, list[dict]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["merchant"], []).append(row)
    return grouped


def write_outputs(root: Path, report, state_store) -> None:
    output_dir = root / "outputs"
    output_dir.mkdir(exist_ok=True)
    if report is not None:
        (output_dir / "audit_report.json").write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    (output_dir / "token_curve.jsonl").write_text(
        "\n".join(json.dumps(row) for row in state_store.token_curve),
        encoding="utf-8",
    )
    (output_dir / "tool_history.jsonl").write_text(
        "\n".join(json.dumps(row) for row in state_store.tool_history),
        encoding="utf-8",
    )
    (output_dir / "iterations.jsonl").write_text(
        "\n".join(json.dumps(item.model_dump(mode="json")) for item in state_store.iterations),
        encoding="utf-8",
    )
    (output_dir / "merchant_states.json").write_text(
        json.dumps(state_store.merchants, indent=2, default=str),
        encoding="utf-8",
    )
    provider_calls = state_store.runtime_scenario.get("_provider_calls", [])
    (output_dir / "provider_calls.json").write_text(
        json.dumps(provider_calls, indent=2),
        encoding="utf-8",
    )


def main() -> None:
    root = Path(__file__).resolve().parent
    load_dotenv(root / ".env")
    tracer = get_langsmith_tracer()
    merchants = load_merchants(root / "data" / "merchants.yaml")
    mock_db = load_mock_db(root / "data" / "mock_db.json")

    registry = ToolRegistry()
    registry.load_all()
    state_store = StateStore()
    budget = BudgetSentinel(BudgetConfig())
    console = Console()
    live = Live(build_dashboard(state_store, budget), console=console, refresh_per_second=4)

    last_write_at = perf_counter()

    def _on_iteration() -> None:
        nonlocal last_write_at
        live.update(build_dashboard(state_store, budget))
        now = perf_counter()
        if now - last_write_at >= 2.0:
            write_outputs(root, report=None, state_store=state_store)  # partial snapshot during run
            last_write_at = now

    loop = AgentLoop(
        registry=registry,
        budget=budget,
        state_store=state_store,
        model_router=ModelRouter(),
        mock_db=mock_db,
        on_iteration=_on_iteration,
    )

    report = None
    try:
        with live:
            with tracer.span(
                "grabon_main",
                run_type="chain",
                inputs={"merchant_count": len(merchants)},
                metadata={"entrypoint": "main.py", "mode": "merchant_deal_audit"},
            ) as run:
                report = loop.run(merchants)
                live.update(build_dashboard(state_store, budget))
                if hasattr(run, "add_outputs"):
                    run.add_outputs(
                        {
                            "completed_merchants": sum(1 for item in report.merchant_results if item.status == "DONE"),
                            "halted": report.halt_report is not None,
                        }
                    )
    finally:
        # Always persist the latest snapshot, even if interrupted.
        write_outputs(root, report, state_store)
        tracer.flush()


if __name__ == "__main__":
    main()
