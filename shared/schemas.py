from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class MerchantConfig(BaseModel):
    name: str
    deals_url: str
    category: str
    js_rendered: bool = False
    known_blocks: list[str] = Field(default_factory=list)


class DealStatus(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    MISSING = "missing"
    UPDATED = "updated"


class DealRecord(BaseModel):
    merchant: str
    title: str
    discount_pct: float | None = None
    deal_type: str
    conditions: str | None = None
    min_order: float | None = None
    expiry: date | None = None
    coupon_code: str | None = None
    extracted_at: datetime | None = None
    confidence: float = 1.0


class ClassifiedDeal(BaseModel):
    title: str
    status: DealStatus
    discount_pct: float | None = None
    coupon_code: str | None = None
    reason: str


class ToolError(BaseModel):
    error_type: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    tool_name: str
    success: bool
    data: dict[str, Any] | None = None
    error: ToolError | None = None
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    tokens_used: int = 0

    @classmethod
    def ok(
        cls,
        tool_name: str,
        data: dict[str, Any],
        *,
        latency_ms: float = 0.0,
        cost_usd: float = 0.0,
        tokens_used: int = 0,
    ) -> "ToolResult":
        return cls(
            tool_name=tool_name,
            success=True,
            data=data,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            tokens_used=tokens_used,
        )

    @classmethod
    def fail(
        cls,
        tool_name: str,
        error_type: str,
        message: str,
        *,
        latency_ms: float = 0.0,
        cost_usd: float = 0.0,
        tokens_used: int = 0,
        details: dict[str, Any] | None = None,
    ) -> "ToolResult":
        return cls(
            tool_name=tool_name,
            success=False,
            error=ToolError(error_type=error_type, message=message, details=details or {}),
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            tokens_used=tokens_used,
        )

    @classmethod
    def tool_unavailable(cls, tool_name: str) -> "ToolResult":
        return cls.fail(tool_name, "NO_TOOL", f"{tool_name} is unavailable.")


class MerchantAuditResult(BaseModel):
    merchant: str
    status: Literal["DONE", "IMPOSSIBLE", "DEGRADED", "PENDING"]
    health_score: float | None = None
    summary: str = ""
    deals: list[ClassifiedDeal] = Field(default_factory=list)
    reason: str | None = None


class PartialMerchantResult(BaseModel):
    merchant: str
    phase: str
    reason: str


class MerchantResultSummary(BaseModel):
    merchant: str
    status: str
    score: float | None = None


class HaltReport(BaseModel):
    reason: Literal["MAX_TOKENS", "WALL_CLOCK", "MAX_TOOL_CALLS", "CONSECUTIVE_FAILURES"]
    completed_merchants: list[MerchantResultSummary] = Field(default_factory=list)
    remaining_merchants: list[str] = Field(default_factory=list)
    partial_merchants: list[PartialMerchantResult] = Field(default_factory=list)
    tokens_used: int
    elapsed_seconds: float
    tool_calls_made: int
    consecutive_failures_at_halt: int
    halted_at: datetime = Field(default_factory=datetime.utcnow)


class LoopIteration(BaseModel):
    step_number: int
    merchant: str
    phase: Literal["PLAN", "ACT", "OBSERVE", "DECIDE"]
    action: str
    tool_called: str | None = None
    observation: str | None = None
    decision: str | None = None
    tokens_consumed: int = 0
    wall_clock_ms: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class AuditReport(BaseModel):
    model_config = {"protected_namespaces": ()}

    generated_at: datetime = Field(default_factory=datetime.utcnow)
    model_routing: dict[str, str]
    merchant_results: list[MerchantAuditResult]
    halt_report: HaltReport | None = None
    iterations: list[LoopIteration] = Field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
