from __future__ import annotations

from pydantic import BaseModel

from shared.schemas import MerchantConfig
from tools.base import ToolExecution, tool


class RenderInput(BaseModel):
    merchant: MerchantConfig
    state: dict


class RenderOutput(BaseModel):
    html: str
    source: str


@tool(input_model=RenderInput, output_model=RenderOutput, timeout=30.0, cost_usd=0.0, tags=["fetch", "playwright"])
def render_js(payload: RenderInput, runtime) -> ToolExecution:
    merchant = payload.merchant
    injected = runtime.state_store.consume_tool_injection(merchant.name, "render_js")
    if injected and "error_type" in injected:
        raise RuntimeError(f"{injected['error_type']}::{injected.get('message', 'Injected failure')}")
    return ToolExecution(data={"html": runtime.fixture_html(merchant.name, source="render_js"), "source": "render_js"})
