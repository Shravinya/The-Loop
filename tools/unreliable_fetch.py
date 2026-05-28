from __future__ import annotations

from random import Random

from pydantic import BaseModel

from shared.schemas import MerchantConfig
from tools.base import ToolExecution, tool

_rng = Random(7)


class UnreliableInput(BaseModel):
    merchant: MerchantConfig
    state: dict


class UnreliableOutput(BaseModel):
    html: str
    source: str


@tool(input_model=UnreliableInput, output_model=UnreliableOutput, timeout=10.0, cost_usd=0.0, tags=["eval", "chaos"])
def unreliable_fetch(payload: UnreliableInput, runtime) -> ToolExecution:
    merchant = payload.merchant
    injected = runtime.state_store.consume_tool_injection(merchant.name, "unreliable_fetch")
    if injected and "error_type" in injected:
        raise RuntimeError(f"{injected['error_type']}::{injected.get('message', 'Injected failure')}")
    if _rng.random() < 0.3:
        raise RuntimeError("TIMEOUT::Injected flaky failure.")
    html = runtime.fixture_html(merchant.name, source="unreliable_fetch")
    return ToolExecution(data={"html": html, "source": "unreliable_fetch"})
