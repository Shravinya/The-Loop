from __future__ import annotations

from pydantic import BaseModel

from shared.schemas import MerchantConfig
from tools.base import ToolExecution, tool


class FetchInput(BaseModel):
    merchant: MerchantConfig
    state: dict


class FetchOutput(BaseModel):
    html: str
    source: str


@tool(input_model=FetchInput, output_model=FetchOutput, timeout=10.0, cost_usd=0.0, tags=["fetch", "http"])
def fetch_html(payload: FetchInput, runtime) -> ToolExecution:
    merchant = payload.merchant
    injected = runtime.state_store.consume_tool_injection(merchant.name, "fetch_html")
    if injected and "error_type" in injected:
        raise RuntimeError(f"{injected['error_type']}::{injected.get('message', 'Injected failure')}")
    if "cloudflare" in merchant.known_blocks:
        raise RuntimeError("403_CF::Cloudflare block detected.")
    if merchant.deals_url.endswith("/404"):
        raise RuntimeError("404::Merchant page not found.")
    return ToolExecution(data={"html": runtime.fixture_html(merchant.name, source="fetch_html"), "source": "fetch_html"})
