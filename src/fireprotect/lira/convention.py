"""Explicit, fail-closed LIRA to RX3 force convention registry."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from .errors import LiraMappingError
from .types import LIRA_NATIVE_FORCE_COMPONENTS


class ConventionStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    ENGINEER_CONFIRMED = "ENGINEER_CONFIRMED"
    VALIDATED = "VALIDATED"


class LiraConventionError(LiraMappingError):
    """A force row cannot cross the LIRA to RX3 convention gate."""


@dataclass(frozen=True, slots=True)
class LiraRx3ComponentConvention:
    source_component: str
    target_rx3_component: str | None
    sign_multiplier: Decimal | None
    axis_interpretation: str | None
    verification_status: ConventionStatus
    evidence_reference: str | None

    def __post_init__(self) -> None:
        if self.source_component not in LIRA_NATIVE_FORCE_COMPONENTS:
            raise LiraMappingError(
                f"Unknown LIRA force component: {self.source_component!r}"
            )
        if not isinstance(self.verification_status, ConventionStatus):
            try:
                object.__setattr__(
                    self,
                    "verification_status",
                    ConventionStatus(self.verification_status),
                )
            except (TypeError, ValueError) as exc:
                raise LiraMappingError("Invalid convention verification status") from exc
        for name in (
            "target_rx3_component",
            "axis_interpretation",
            "evidence_reference",
        ):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, str) or not value.strip()
            ):
                raise LiraMappingError(f"{name} must be non-empty when provided")
        multiplier = self.sign_multiplier
        if multiplier is not None:
            if isinstance(multiplier, (bool, float)):
                raise LiraMappingError("sign_multiplier must be exact Decimal -1 or 1")
            try:
                multiplier = (
                    multiplier
                    if isinstance(multiplier, Decimal)
                    else Decimal(str(multiplier))
                )
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise LiraMappingError(
                    "sign_multiplier must be exact Decimal -1 or 1"
                ) from exc
            if multiplier not in {Decimal("-1"), Decimal("1")}:
                raise LiraMappingError("sign_multiplier must be exactly -1 or 1")
            object.__setattr__(self, "sign_multiplier", multiplier)
        if self.verification_status is not ConventionStatus.UNKNOWN:
            missing = [
                name
                for name in (
                    "target_rx3_component",
                    "sign_multiplier",
                    "axis_interpretation",
                    "evidence_reference",
                )
                if getattr(self, name) is None
            ]
            if missing:
                raise LiraMappingError(
                    f"{self.verification_status.value} convention is incomplete: {missing}"
                )

    @property
    def resolved(self) -> bool:
        return (
            self.verification_status is ConventionStatus.VALIDATED
            and self.target_rx3_component is not None
            and self.sign_multiplier is not None
            and self.axis_interpretation is not None
            and self.evidence_reference is not None
        )

    def as_dict(self) -> dict[str, str | None]:
        return {
            "source_component": self.source_component,
            "target_rx3_component": self.target_rx3_component,
            "sign_multiplier": (
                None if self.sign_multiplier is None else str(self.sign_multiplier)
            ),
            "axis_interpretation": self.axis_interpretation,
            "verification_status": self.verification_status.value,
            "evidence_reference": self.evidence_reference,
        }


@dataclass(frozen=True, slots=True)
class LiraRx3ConventionRegistry:
    components: Mapping[str, LiraRx3ComponentConvention]

    def __post_init__(self) -> None:
        if not isinstance(self.components, Mapping):
            raise LiraMappingError("convention registry must be a mapping")
        expected = set(LIRA_NATIVE_FORCE_COMPONENTS)
        if set(self.components) != expected:
            raise LiraMappingError(
                "convention registry must explicitly contain native "
                "N/Mk/My/Mz/Qy/Qz components"
            )
        normalized: dict[str, LiraRx3ComponentConvention] = {}
        for name, convention in self.components.items():
            if not isinstance(convention, LiraRx3ComponentConvention):
                raise LiraMappingError(
                    f"convention {name} must be LiraRx3ComponentConvention"
                )
            if convention.source_component != name:
                raise LiraMappingError(
                    f"convention key {name} differs from source component "
                    f"{convention.source_component}"
                )
            normalized[name] = convention
        object.__setattr__(self, "components", MappingProxyType(normalized))

    @classmethod
    def unresolved(cls) -> LiraRx3ConventionRegistry:
        return cls(
            {
                name: LiraRx3ComponentConvention(
                    source_component=name,
                    target_rx3_component=None,
                    sign_multiplier=None,
                    axis_interpretation=None,
                    verification_status=ConventionStatus.UNKNOWN,
                    evidence_reference=None,
                )
                for name in LIRA_NATIVE_FORCE_COMPONENTS
            }
        )

    @classmethod
    def from_dict(cls, payload: object) -> LiraRx3ConventionRegistry:
        if payload is None:
            return cls.unresolved()
        if not isinstance(payload, Mapping):
            raise LiraMappingError("convention must be an object or null")
        unknown = set(payload) - set(LIRA_NATIVE_FORCE_COMPONENTS)
        if unknown:
            raise LiraMappingError(f"unknown convention components: {sorted(unknown)}")
        components: dict[str, LiraRx3ComponentConvention] = {}
        for name in LIRA_NATIVE_FORCE_COMPONENTS:
            raw = payload.get(name)
            if raw is None:
                components[name] = cls.unresolved().components[name]
                continue
            if not isinstance(raw, Mapping):
                raise LiraMappingError(f"convention.{name} must be an object")
            allowed = {
                "source_component",
                "target_rx3_component",
                "sign_multiplier",
                "axis_interpretation",
                "verification_status",
                "evidence_reference",
            }
            extra = set(raw) - allowed
            if extra:
                raise LiraMappingError(
                    f"unknown convention.{name} fields: {sorted(extra)}"
                )
            source_component = raw.get("source_component", name)
            status = raw.get("verification_status", ConventionStatus.UNKNOWN.value)
            components[name] = LiraRx3ComponentConvention(
                source_component=str(source_component),
                target_rx3_component=_optional_text(raw.get("target_rx3_component")),
                sign_multiplier=raw.get("sign_multiplier"),
                axis_interpretation=_optional_text(raw.get("axis_interpretation")),
                verification_status=ConventionStatus(str(status)),
                evidence_reference=_optional_text(raw.get("evidence_reference")),
            )
        return cls(components)

    def unresolved_components(self, available: Mapping[str, object]) -> tuple[str, ...]:
        return tuple(
            name
            for name in LIRA_NATIVE_FORCE_COMPONENTS
            if available.get(name) is not None and not self.components[name].resolved
        )

    def require_rx38_generation_ready(self, available: Mapping[str, object]) -> None:
        unresolved = self.unresolved_components(available)
        if unresolved:
            raise LiraConventionError(
                "LIRA_RX3_FORCE_CONVENTION blocks RX38 force generation; "
                f"unresolved components: {', '.join(unresolved)}"
            )

    def as_dict(self) -> dict[str, dict[str, str | None]]:
        return {
            name: self.components[name].as_dict()
            for name in LIRA_NATIVE_FORCE_COMPONENTS
        }


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise LiraMappingError("optional convention text must be non-empty")
    return value.strip()
