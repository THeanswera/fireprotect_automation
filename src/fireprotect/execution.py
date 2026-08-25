from __future__ import annotations

from enum import Enum


class ExecutionMode(str, Enum):
    """Controls which unresolved evidence may enter a workflow."""

    DRAFT = "DRAFT"
    VALIDATION = "VALIDATION"
    PRODUCTION = "PRODUCTION"

    @classmethod
    def parse(cls, value: object) -> "ExecutionMode":
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise TypeError("execution_mode must be DRAFT, VALIDATION or PRODUCTION")
        try:
            return cls(value.strip().upper())
        except ValueError as exc:
            raise ValueError(
                "execution_mode must be DRAFT, VALIDATION or PRODUCTION"
            ) from exc
