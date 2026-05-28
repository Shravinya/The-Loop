from __future__ import annotations

from rich.console import Group
from rich.panel import Panel
from rich.table import Table


def _token_curve(token_points: list[dict]) -> str:
    if not token_points:
        return "No token usage yet."
    return " ".join(f"{point['step_number']}:{point['tokens_used']}" for point in token_points[-10:])


def build_dashboard(state_store, budget):
    merchants = Table(title="Merchant State")
    merchants.add_column("Merchant")
    merchants.add_column("Phase")
    merchants.add_column("Last Tool")
    merchants.add_column("Latency(ms)")
    merchants.add_column("Status")
    for merchant, state in state_store.merchants.items():
        merchants.add_row(
            merchant,
            str(state.get("current_phase", "-")),
            str(state.get("last_tool", "-")),
            str(round(state.get("last_latency_ms", 0.0), 2)),
            str(state.get("status", "PENDING")),
        )

    history = Table(title="Recent Tool Calls")
    history.add_column("Merchant")
    history.add_column("Tool")
    history.add_column("Success")
    history.add_column("Latency")
    history.add_column("Error")
    for item in state_store.tool_history[-10:]:
        history.add_row(
            item["merchant"],
            item["tool"],
            "yes" if item["success"] else "no",
            str(item["latency_ms"]),
            str(item["error_type"] or "-"),
        )

    reasoning_lines = []
    for merchant, state in list(state_store.merchants.items())[-5:]:
        if state.get("reasoning"):
            reasoning_lines.append(f"{merchant}: {state['reasoning'][-1]}")
    reasoning = "\n".join(reasoning_lines) if reasoning_lines else "No decisions recorded yet."

    summary = Panel(
        f"Elapsed={budget.elapsed_seconds:.2f}s | Tokens={budget.tokens_used} | Tool calls={budget.tool_calls_made} | Failures={budget.consecutive_failures}",
        title="Budget",
    )
    curve = Panel(_token_curve(state_store.token_curve), title="Token Curve")
    reasons = Panel(reasoning, title="Latest Decide Reasoning")
    return Group(summary, merchants, history, curve, reasons)
