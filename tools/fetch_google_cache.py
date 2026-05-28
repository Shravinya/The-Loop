from __future__ import annotations

from pydantic import BaseModel

from shared.schemas import MerchantConfig
from tools.base import ToolExecution, tool


class CacheInput(BaseModel):
    merchant: MerchantConfig
    state: dict


class CacheOutput(BaseModel):
    html: str
    source: str


@tool(input_model=CacheInput, output_model=CacheOutput, timeout=10.0, cost_usd=0.0, tags=["fallback", "cache"])
def fetch_google_cache(payload: CacheInput, runtime) -> ToolExecution:
    merchant = payload.merchant
    injected = runtime.state_store.consume_tool_injection(merchant.name, "fetch_google_cache")
    if injected and "error_type" in injected:
        raise RuntimeError(f"{injected['error_type']}::{injected.get('message', 'Injected failure')}")
    return ToolExecution(data={"html": runtime.fixture_html(merchant.name, source="fetch_google_cache"), "source": "fetch_google_cache"})
