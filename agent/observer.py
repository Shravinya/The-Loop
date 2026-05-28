from __future__ import annotations

from dataclasses import dataclass

from shared.schemas import ToolResult


@dataclass
class Observation:
    success: bool
    summary: str
    tool_result: ToolResult


class Observer:
    def observe(self, result: ToolResult) -> Observation:
        if result.success:
            return Observation(True, f"{result.tool_name} succeeded", result)
        error_type = result.error.error_type if result.error else "UNKNOWN"
        return Observation(False, f"{result.tool_name} failed: {error_type}", result)

