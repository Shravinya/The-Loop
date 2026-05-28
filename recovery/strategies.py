from __future__ import annotations


class RetryBackoff:
    delays = [1, 2, 4]


class ReplanAltTool:
    fallback_chain = {
        "fetch_html": ["render_js", "fetch_google_cache"],
        "render_js": ["fetch_google_cache"],
        "unreliable_fetch": ["fetch_html", "fetch_google_cache"],
        "extract_deals": ["fetch_google_cache"],
    }

    @classmethod
    def next_tool(cls, tool_name: str, failed_tools: list[str]) -> str | None:
        for candidate in cls.fallback_chain.get(tool_name, []):
            if candidate not in failed_tools:
                return candidate
        return None


class GracefulDegrade:
    @staticmethod
    def status() -> str:
        return "DEGRADED"
