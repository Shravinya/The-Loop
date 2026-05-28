from __future__ import annotations

from enum import Enum


class ErrorType(Enum):
    TRANSIENT_RATE_LIMIT = "429"
    TRANSIENT_TIMEOUT = "TIMEOUT"
    PERSISTENT_BLOCK = "403_CF"
    PERMANENT_NOT_FOUND = "404"
    JS_RENDER_REQUIRED = "JS_WALL"
    NO_STRUCTURE = "PARSE_FAIL"
    TOOL_UNAVAILABLE = "NO_TOOL"
    UNKNOWN = "UNKNOWN"


class ErrorClassifier:
    def classify(self, error) -> ErrorType:
        if error is None:
            return ErrorType.UNKNOWN
        for item in ErrorType:
            if item.value == error.error_type:
                return item
        return ErrorType.UNKNOWN

