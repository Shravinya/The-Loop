from __future__ import annotations

from pydantic import BaseModel

from observability.langsmith_tracer import get_langsmith_tracer
from shared.schemas import MerchantConfig
from tools.base import ToolExecution, tool


class ReportInput(BaseModel):
    merchant: MerchantConfig
    state: dict


class ReportOutput(BaseModel):
    markdown: str
    structured: dict


@tool(input_model=ReportInput, output_model=ReportOutput, timeout=10.0, cost_usd=0.0002, tags=["report"])
def write_report(payload: ReportInput, runtime) -> ToolExecution:
    merchant = payload.merchant
    state = payload.state
    tracer = get_langsmith_tracer()
    deals = state.get("audited_deals", [])
    score = state.get("health_score")
    status = state.get("status", "PENDING")

    markdown = "\n".join(
        [
            f"## {merchant.name}",
            f"- Status: {status}",
            f"- Health score: {score}",
            f"- Deals audited: {len(deals)}",
            f"- Provider calls used: {len(state.get('provider_calls', []))}",
        ]
    )
    tokens_used = 0
    provider_attempts = state.get("tool_attempts", {}).get("write_report", 0)
    if not runtime.scenario and runtime.model_router.can_use_report_provider() and provider_attempts <= 1:
        try:
            provider_markdown, tokens_used = runtime.model_router.gemini_report(
                merchant.name,
                status,
                score,
                [deal if isinstance(deal, dict) else deal.model_dump(mode="json") for deal in deals],
            )
            if provider_markdown:
                markdown = provider_markdown
            runtime.record_provider_call(runtime.model_router.report_model.provider, runtime.model_router.report_model.model, "report", tokens_used)
        except Exception as exc:
            with tracer.span(
                "provider_fallback:write_report",
                run_type="chain",
                inputs={"merchant": merchant.name, "provider": "gemini", "reason": type(exc).__name__},
                metadata={"fallback": "local_template"},
                tags=["fallback", "provider", "report"],
            ) as span:
                if hasattr(span, "add_outputs"):
                    span.add_outputs({"fallback_used": "local_template", "error": str(exc)})
            runtime.state_store.add_reasoning(merchant.name, "Provider report generation failed; fell back to template.")
    elif not runtime.scenario and runtime.model_router.can_use_report_provider() and provider_attempts > 1:
        with tracer.span(
            "provider_fallback:write_report",
            run_type="chain",
            inputs={"merchant": merchant.name, "provider": "gemini", "reason": "provider_attempt_limit"},
            metadata={"fallback": "local_template"},
            tags=["fallback", "provider", "report"],
        ) as span:
            if hasattr(span, "add_outputs"):
                span.add_outputs({"fallback_used": "local_template", "attempts": provider_attempts})
        runtime.state_store.add_reasoning(merchant.name, "Skipped provider report generation after 1 attempt; used template.")

    structured = {"merchant": merchant.name, "status": status, "health_score": score, "deal_count": len(deals)}
    if tokens_used == 0:
        tokens_used = max(len(markdown) // 6, 1)
    return ToolExecution(data={"markdown": markdown, "structured": structured}, tokens_used=tokens_used)
