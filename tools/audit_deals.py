from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from shared.schemas import ClassifiedDeal, DealStatus, MerchantConfig
from tools.base import ToolExecution, tool


class AuditInput(BaseModel):
    merchant: MerchantConfig
    state: dict


class AuditOutput(BaseModel):
    classified_deals: list[ClassifiedDeal]
    health_score: float
    stale_count: int
    missing_count: int
    updated_count: int
    fresh_count: int


@tool(input_model=AuditInput, output_model=AuditOutput, timeout=3.0, cost_usd=0.0, tags=["audit", "deterministic"])
def audit_deals(payload: AuditInput, runtime) -> ToolExecution:
    merchant = payload.merchant
    state = payload.state
    extracted = state.get("extracted_deals", [])
    db_rows = runtime.mock_db_for(merchant.name)
    db_by_title = {row["title"].lower(): row for row in db_rows}
    extracted_by_title = {row["title"].lower(): row for row in extracted}

    classified: list[ClassifiedDeal] = []
    stale_count = 0
    missing_count = 0
    updated_count = 0
    fresh_count = 0
    today = date.today()

    for title_key, db_row in db_by_title.items():
        extracted_row = extracted_by_title.get(title_key)
        if extracted_row is None:
            missing_count += 1
            classified.append(
                ClassifiedDeal(
                    title=db_row["title"],
                    status=DealStatus.MISSING,
                    discount_pct=db_row.get("discount_pct"),
                    coupon_code=db_row.get("coupon_code"),
                    reason="Present in GrabOn DB but not found on merchant page.",
                )
            )
            continue

        db_discount = db_row.get("discount_pct")
        page_discount = extracted_row.get("discount_pct")
        expiry = db_row.get("expiry")
        expired = bool(expiry and expiry < today.isoformat())
        if expired or db_discount != page_discount:
            stale_count += 1
            classified.append(
                ClassifiedDeal(
                    title=extracted_row["title"],
                    status=DealStatus.STALE,
                    discount_pct=page_discount,
                    coupon_code=extracted_row.get("coupon_code"),
                    reason="DB record expired or merchant discount changed.",
                )
            )
        else:
            fresh_count += 1
            classified.append(
                ClassifiedDeal(
                    title=extracted_row["title"],
                    status=DealStatus.FRESH,
                    discount_pct=page_discount,
                    coupon_code=extracted_row.get("coupon_code"),
                    reason="Page and DB match.",
                )
            )

    for title_key, extracted_row in extracted_by_title.items():
        if title_key not in db_by_title:
            updated_count += 1
            classified.append(
                ClassifiedDeal(
                    title=extracted_row["title"],
                    status=DealStatus.UPDATED,
                    discount_pct=extracted_row.get("discount_pct"),
                    coupon_code=extracted_row.get("coupon_code"),
                    reason="Found on page but not in GrabOn DB.",
                )
            )

    total = max(len(classified), 1)
    days_since_last_update = runtime.days_since_last_update(merchant.name)
    score = 100
    score -= (stale_count / total) * 40
    score -= (missing_count / total) * 35
    score -= (days_since_last_update / 30) * 25
    score = round(min(max(score, 0), 100), 2)
    return ToolExecution(
        data={
            "classified_deals": classified,
            "health_score": score,
            "stale_count": stale_count,
            "missing_count": missing_count,
            "updated_count": updated_count,
            "fresh_count": fresh_count,
        }
    )
