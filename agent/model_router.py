from __future__ import annotations

import json
import os
from dataclasses import dataclass

from observability.langsmith_tracer import get_langsmith_tracer

@dataclass(frozen=True)
class ModelChoice:
    provider: str
    model: str
    purpose: str


class ModelRouter:
    """
    Small optional provider layer.

    Default behavior is deterministic and does not require API keys.
    If provider keys are present, extraction and report-writing may use them.
    If a provider fails, callers must fall back to local deterministic logic.
    """

    def __init__(self) -> None:
        has_groq = bool(os.getenv("GROQ_API_KEY"))
        error_provider = os.getenv("ERROR_MODEL_PROVIDER", "deterministic").lower()
        self.plan_model = ModelChoice(
            "groq" if has_groq else "deterministic",
            os.getenv("GROQ_PLAN_MODEL", "llama-3.3-70b-versatile") if has_groq else "rule-based-planner",
            "planning",
        )
        self.extract_model = ModelChoice("groq", os.getenv("GROQ_EXTRACT_MODEL", "llama-3.1-8b-instant"), "extraction")
        self.report_model = ModelChoice("gemini", os.getenv("GEMINI_REPORT_MODEL", "gemini-2.0-flash"), "reporting")
        self.classify_model = ModelChoice("deterministic", "rule-based-audit", "classification")
        self.error_model = ModelChoice(
            "groq" if has_groq and error_provider == "groq" else "deterministic",
            os.getenv("ERROR_MODEL_NAME", "llama-3.1-8b-instant") if has_groq and error_provider == "groq" else "rule-based-error-routing",
            "error_parsing",
        )
        self.provider_timeout_seconds = float(os.getenv("PROVIDER_TIMEOUT_SECONDS", "4"))
        self.tracer = get_langsmith_tracer()

    def describe(self) -> dict[str, str]:
        return {
            "plan": f"{self.plan_model.provider}:{self.plan_model.model}",
            "extract": f"{self.extract_model.provider}:{self.extract_model.model}",
            "classify": f"{self.classify_model.provider}:{self.classify_model.model}",
            "report": f"{self.report_model.provider}:{self.report_model.model}",
            "error": f"{self.error_model.provider}:{self.error_model.model}",
        }

    def can_use_extract_provider(self) -> bool:
        return bool(os.getenv("GROQ_API_KEY"))

    def can_use_plan_provider(self) -> bool:
        return self.plan_model.provider == "groq" and bool(os.getenv("GROQ_API_KEY"))

    def can_use_act_provider(self) -> bool:
        return self.can_use_plan_provider()

    def can_use_observe_provider(self) -> bool:
        return self.can_use_plan_provider()

    def can_use_error_provider(self) -> bool:
        return self.error_model.provider == "groq" and bool(os.getenv("GROQ_API_KEY"))

    def can_use_report_provider(self) -> bool:
        return bool(os.getenv("GEMINI_API_KEY"))

    def _groq_client(self):
        from groq import Groq

        return Groq(api_key=os.getenv("GROQ_API_KEY"), timeout=self.provider_timeout_seconds)

    def groq_extract(self, html: str, merchant_name: str) -> tuple[list[dict], int]:
        client = self._groq_client()
        prompt = (
            "Extract current merchant offers from the HTML and return strict JSON with key 'deals'. "
            "Each deal must include title, discount_pct, deal_type, conditions, min_order, expiry, coupon_code, confidence. "
            f"Merchant: {merchant_name}. HTML:\n{html[:12000]}"
        )
        with self.tracer.span(
            "groq_extract",
            run_type="llm",
            inputs={"merchant": merchant_name, "html_preview": html[:1000]},
            metadata={"provider": "groq", "purpose": "extract", "model": self.extract_model.model},
        ) as span:
            response = client.chat.completions.create(
                model=self.extract_model.model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": "You extract ecommerce deal data into strict JSON only."},
                    {"role": "user", "content": prompt},
                ],
            )
        payload = json.loads(response.choices[0].message.content)
        usage = getattr(response, "usage", None)
        tokens = int(getattr(usage, "total_tokens", 0) or 0)
        if hasattr(span, "add_outputs"):
            span.add_outputs({"deals": payload.get("deals", []), "tokens": tokens})
        return payload.get("deals", []), tokens

    def groq_plan(self, merchant_name: str, merchant_state: dict, merchant_context: dict, available_tools: list[str]) -> tuple[dict, int]:
        client = self._groq_client()
        prompt = {
            "merchant": merchant_name,
            "merchant_state": merchant_state,
            "merchant_context": merchant_context,
            "available_tools": available_tools,
            "task": "Choose the next best tool call for the merchant audit loop.",
            "output_schema": {
                "action": "string",
                "tool_name": "string",
                "rationale": "string",
            },
        }
        with self.tracer.span(
            "groq_plan",
            run_type="llm",
            inputs=prompt,
            metadata={"provider": "groq", "purpose": "planning", "model": self.plan_model.model},
        ) as span:
            response = client.chat.completions.create(
                model=self.plan_model.model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": "You are a planning module for a merchant audit agent. Return strict JSON only with keys action, tool_name, rationale.",
                    },
                    {"role": "user", "content": json.dumps(prompt)},
                ],
            )
        payload = json.loads(response.choices[0].message.content)
        usage = getattr(response, "usage", None)
        tokens = int(getattr(usage, "total_tokens", 0) or 0)
        if hasattr(span, "add_outputs"):
            span.add_outputs({"plan": payload, "tokens": tokens})
        return payload, tokens

    def groq_decide(self, merchant_name: str, merchant_state: dict, observation: dict) -> tuple[dict, int]:
        client = self._groq_client()
        prompt = {
            "merchant": merchant_name,
            "merchant_state": merchant_state,
            "observation": observation,
            "allowed_actions": ["CONTINUE", "REPLAN", "DONE", "IMPOSSIBLE", "DEGRADED"],
            "task": "Choose the next loop decision for the merchant audit agent.",
            "output_schema": {
                "action": "string",
                "reason": "string",
            },
        }
        with self.tracer.span(
            "groq_decide",
            run_type="llm",
            inputs=prompt,
            metadata={"provider": "groq", "purpose": "decision", "model": self.error_model.model},
        ) as span:
            response = client.chat.completions.create(
                model=self.error_model.model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": "You are a recovery and decision module for a merchant audit agent. Return strict JSON only with keys action and reason.",
                    },
                    {"role": "user", "content": json.dumps(prompt)},
                ],
            )
        payload = json.loads(response.choices[0].message.content)
        usage = getattr(response, "usage", None)
        tokens = int(getattr(usage, "total_tokens", 0) or 0)
        if hasattr(span, "add_outputs"):
            span.add_outputs({"decision": payload, "tokens": tokens})
        return payload, tokens

    def groq_act(self, merchant_name: str, tool_name: str, merchant_state: dict) -> tuple[dict, int]:
        client = self._groq_client()
        prompt = {
            "merchant": merchant_name,
            "tool_name": tool_name,
            "merchant_state": merchant_state,
            "task": "Confirm the next tool execution and provide a brief execution note.",
            "output_schema": {
                "proceed": "boolean",
                "tool_name": "string",
                "note": "string",
            },
        }
        with self.tracer.span(
            "groq_act",
            run_type="llm",
            inputs=prompt,
            metadata={"provider": "groq", "purpose": "act", "model": self.plan_model.model},
        ) as span:
            response = client.chat.completions.create(
                model=self.plan_model.model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": "You are an execution coordinator for a merchant audit agent. Return strict JSON only with keys proceed, tool_name, note.",
                    },
                    {"role": "user", "content": json.dumps(prompt)},
                ],
            )
        payload = json.loads(response.choices[0].message.content)
        usage = getattr(response, "usage", None)
        tokens = int(getattr(usage, "total_tokens", 0) or 0)
        if hasattr(span, "add_outputs"):
            span.add_outputs({"act": payload, "tokens": tokens})
        return payload, tokens

    def groq_observe(self, result: dict) -> tuple[dict, int]:
        client = self._groq_client()
        prompt = {
            "tool_result": result,
            "task": "Summarize the tool result into a success flag and concise observation.",
            "output_schema": {
                "success": "boolean",
                "tool_name": "string",
                "summary": "string",
            },
        }
        with self.tracer.span(
            "groq_observe",
            run_type="llm",
            inputs=prompt,
            metadata={"provider": "groq", "purpose": "observe", "model": self.plan_model.model},
        ) as span:
            response = client.chat.completions.create(
                model=self.plan_model.model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": "You are an observation module for a merchant audit agent. Return strict JSON only with keys success, tool_name, summary.",
                    },
                    {"role": "user", "content": json.dumps(prompt)},
                ],
            )
        payload = json.loads(response.choices[0].message.content)
        usage = getattr(response, "usage", None)
        tokens = int(getattr(usage, "total_tokens", 0) or 0)
        if hasattr(span, "add_outputs"):
            span.add_outputs({"observe": payload, "tokens": tokens})
        return payload, tokens

    def gemini_report(self, merchant_name: str, status: str, score: float | None, deals: list[dict]) -> tuple[str, int]:
        from google import genai

        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        payload = {
            "merchant": merchant_name,
            "status": status,
            "health_score": score,
            "deals": deals,
        }
        with self.tracer.span(
            "gemini_report",
            run_type="llm",
            inputs=payload,
            metadata={"provider": "gemini", "purpose": "report", "model": self.report_model.model},
        ) as span:
            response = client.models.generate_content(
                model=self.report_model.model,
                contents="Write a short merchant audit summary in markdown with 3 to 5 bullet points.\n\n"
                + json.dumps(payload),
            )
        text = (response.text or "").strip()
        usage = getattr(response, "usage_metadata", None)
        tokens = int(getattr(usage, "total_token_count", 0) or 0) if usage is not None else 0
        if hasattr(span, "add_outputs"):
            span.add_outputs({"text": text, "tokens": tokens})
        return text, tokens
