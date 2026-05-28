from __future__ import annotations

from datetime import datetime
import re

from pydantic import BaseModel

from observability.langsmith_tracer import get_langsmith_tracer
from shared.schemas import DealRecord, MerchantConfig
from tools.base import ToolExecution, tool


class ExtractInput(BaseModel):
    merchant: MerchantConfig
    state: dict


class ExtractOutput(BaseModel):
    deals: list[DealRecord]


def _extract_candidates_from_html(html: str) -> list[dict]:
    collapsed = re.sub(r"\s+", " ", html)
    chunks = re.split(r"[.!?]|</div>|</li>|</p>", collapsed)
    discount_re = re.compile(r"(\d{1,2})\s*%\s*(off|discount|cashback)", re.IGNORECASE)
    coupon_re = re.compile(r"(?:code|coupon)\s*[:\-]?\s*([A-Z0-9]{4,15})", re.IGNORECASE)
    expiry_re = re.compile(r"(?:valid till|expires?|till)\s*[:\-]?\s*([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})", re.IGNORECASE)
    deals: list[dict] = []
    for raw in chunks:
        text = raw.strip()
        if len(text) < 20:
            continue
        discount_match = discount_re.search(text)
        if not discount_match:
            continue
        deals.append(
            {
                "title": text[:100],
                "discount_pct": float(discount_match.group(1)),
                "deal_type": "percentage",
                "conditions": text[:180],
                "coupon_code": coupon_re.search(text).group(1).upper() if coupon_re.search(text) else None,
                "expiry": expiry_re.search(text).group(1) if expiry_re.search(text) else None,
                "confidence": 0.7,
            }
        )
        if len(deals) >= 6:
            break
    return deals


def _sanitize_deals(raw_deals: list[dict], html: str) -> list[dict]:
    coupon_pattern = re.compile(r"^[A-Z0-9]{4,15}$")
    seen_titles: set[str] = set()
    clean: list[dict] = []
    html_upper = html.upper()
    for deal in raw_deals:
        title = str(deal.get("title", "")).strip()[:120]
        if not title:
            continue
        title_key = title.lower()
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)

        discount = deal.get("discount_pct")
        try:
            discount = float(discount) if discount is not None else None
        except (TypeError, ValueError):
            discount = None
        if discount is not None and not 0 <= discount <= 100:
            discount = None

        coupon = deal.get("coupon_code")
        confidence = float(deal.get("confidence", 0.8))
        if coupon:
            coupon = str(coupon).upper().strip()
            if not coupon_pattern.match(coupon):
                coupon = None
            elif coupon not in html_upper:
                coupon = None
                confidence = min(confidence, 0.5)

        clean.append(
            {
                "title": title,
                "discount_pct": discount,
                "deal_type": deal.get("deal_type", "percentage"),
                "conditions": deal.get("conditions"),
                "min_order": deal.get("min_order"),
                "expiry": deal.get("expiry"),
                "coupon_code": coupon,
                "confidence": confidence,
            }
        )
    return clean


@tool(input_model=ExtractInput, output_model=ExtractOutput, timeout=15.0, cost_usd=0.0005, tags=["extract", "llm"])
def extract_deals(payload: ExtractInput, runtime) -> ToolExecution:
    merchant = payload.merchant
    state = payload.state
    tracer = get_langsmith_tracer()
    injected = runtime.state_store.consume_tool_injection(merchant.name, "extract_deals")
    if injected and "error_type" in injected:
        raise RuntimeError(f"{injected['error_type']}::{injected.get('message', 'Injected failure')}")
    if "page_html" not in state:
        raise RuntimeError("PARSE_FAIL::No page content available.")

    page_html = state.get("page_html", "")
    tokens_used = 0
    extracted: list[dict]
    provider_attempts = state.get("tool_attempts", {}).get("extract_deals", 0)

    if runtime.scenario:
        extracted = runtime.fixture_deals(merchant.name)
    else:
        extracted = []
        if runtime.model_router.can_use_extract_provider() and provider_attempts <= 1:
            try:
                extracted, tokens_used = runtime.model_router.groq_extract(page_html, merchant.name)
                runtime.record_provider_call(runtime.model_router.extract_model.provider, runtime.model_router.extract_model.model, "extract", tokens_used)
                extracted = _sanitize_deals(extracted, page_html)
                if not extracted:
                    with tracer.span(
                        "provider_fallback:extract_deals",
                        run_type="chain",
                        inputs={"merchant": merchant.name, "provider": "groq", "reason": "empty_or_untrusted_output"},
                        metadata={"fallback": "local_parser"},
                        tags=["fallback", "provider", "extract"],
                    ) as span:
                        if hasattr(span, "add_outputs"):
                            span.add_outputs({"fallback_used": "local_parser", "provider_tokens": tokens_used})
                    runtime.state_store.add_reasoning(merchant.name, "Provider extraction returned no trusted deals; fell back to local parser.")
            except Exception as exc:
                with tracer.span(
                    "provider_fallback:extract_deals",
                    run_type="chain",
                    inputs={"merchant": merchant.name, "provider": "groq", "reason": type(exc).__name__},
                    metadata={"fallback": "local_parser"},
                    tags=["fallback", "provider", "extract"],
                ) as span:
                    if hasattr(span, "add_outputs"):
                        span.add_outputs({"fallback_used": "local_parser", "error": str(exc)})
                runtime.state_store.add_reasoning(merchant.name, "Provider extraction failed; fell back to local parser.")
        elif runtime.model_router.can_use_extract_provider() and provider_attempts > 1:
            with tracer.span(
                "provider_fallback:extract_deals",
                run_type="chain",
                inputs={"merchant": merchant.name, "provider": "groq", "reason": "provider_attempt_limit"},
                metadata={"fallback": "local_parser"},
                tags=["fallback", "provider", "extract"],
            ) as span:
                if hasattr(span, "add_outputs"):
                    span.add_outputs({"fallback_used": "local_parser", "attempts": provider_attempts})
            runtime.state_store.add_reasoning(merchant.name, "Skipped provider extraction after 1 attempt; used local parser.")

        if not extracted:
            with tracer.span(
                "fallback:local_extract_parser",
                run_type="chain",
                inputs={"merchant": merchant.name, "html_length": len(page_html)},
                tags=["fallback", "parser", "extract"],
            ) as span:
                extracted = _extract_candidates_from_html(page_html)
                extracted = _sanitize_deals(extracted, page_html)
                if hasattr(span, "add_outputs"):
                    span.add_outputs({"deals_found": len(extracted)})

        if not extracted:
            with tracer.span(
                "fallback:fixture_deals",
                run_type="chain",
                inputs={"merchant": merchant.name, "reason": "local_parser_empty"},
                tags=["fallback", "fixture", "extract"],
            ) as span:
                extracted = runtime.fixture_deals(merchant.name)
                if hasattr(span, "add_outputs"):
                    span.add_outputs({"deals_found": len(extracted)})

    extracted = _sanitize_deals(extracted, page_html)
    if not extracted:
        raise RuntimeError("PARSE_FAIL::No extractable deal structure.")

    now = datetime.utcnow()
    deals = [
        DealRecord(
            merchant=merchant.name,
            title=deal["title"],
            discount_pct=deal.get("discount_pct"),
            deal_type=deal.get("deal_type", "percentage"),
            conditions=deal.get("conditions"),
            min_order=deal.get("min_order"),
            expiry=deal.get("expiry"),
            coupon_code=deal.get("coupon_code"),
            extracted_at=now,
            confidence=deal.get("confidence", 0.9),
        )
        for deal in extracted
    ]
    if tokens_used == 0:
        tokens_used = max(len(str(deals)) // 8, 1)
    return ToolExecution(data={"deals": deals}, tokens_used=tokens_used)
