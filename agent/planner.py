from __future__ import annotations

from pydantic import BaseModel

from shared.schemas import MerchantConfig


class StepPlan(BaseModel):
    merchant: str
    action: str
    tool_name: str
    rationale: str


class Planner:
    def next_step(self, merchant: MerchantConfig, merchant_state: dict, available_tools: set[str]) -> StepPlan:
        forced_tool = merchant_state.get("next_tool")
        if "page_html" not in merchant_state:
            preferred = forced_tool or ("render_js" if merchant.js_rendered else "fetch_html")
            tool_name = preferred if preferred in available_tools else "fetch_google_cache"
            return StepPlan(
                merchant=merchant.name,
                action="fetch_page",
                tool_name=tool_name,
                rationale="Need a deal page snapshot before extraction.",
            )
        if "extracted_deals" not in merchant_state:
            return StepPlan(
                merchant=merchant.name,
                action="extract_deals",
                tool_name="extract_deals",
                rationale="Need typed deal records from page content.",
            )
        if "audited_deals" not in merchant_state:
            return StepPlan(
                merchant=merchant.name,
                action="audit_deals",
                tool_name="audit_deals",
                rationale="Need classification against the mock GrabOn database and a health score.",
            )
        return StepPlan(
            merchant=merchant.name,
            action="write_report",
            tool_name="write_report",
            rationale="Need the final merchant audit output.",
        )
