"""Explicit, fail-closed LIRA to RX3 force convention registry."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from .errors import LiraMappingError
from .types import LIRA_NATIVE_FORCE_COMPONENTS
from .units import to_review_unit


class ConventionStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    ENGINEER_CONFIRMED = "ENGINEER_CONFIRMED"
    VALIDATED = "VALIDATED"


class ValueTransform(str, Enum):
    """How one already-selected signed source value becomes an RX3 input."""

    SIGNED_LINEAR = "SIGNED_LINEAR"
    MAGNITUDE = "MAGNITUDE"

    def apply(self, source_signed_value: Decimal) -> Decimal:
        value = _exact_decimal(
            source_signed_value,
            field="source_signed_value",
        )
        if self is ValueTransform.MAGNITUDE:
            return abs(value)
        return value


class Rx3ForceTarget(str, Enum):
    """Explicit direct-writer targets; derived field 78 is intentionally absent."""

    FIELD49_AXIAL_FORCE = "FIELD49_AXIAL_FORCE"
    FIELD50_MAX_MAJOR_AXIS_MOMENT = "FIELD50_MAX_MAJOR_AXIS_MOMENT"
    FIELD79_MAX_MINOR_AXIS_MOMENT = "FIELD79_MAX_MINOR_AXIS_MOMENT"
    FIELD92_MAX_SHEAR_Q = "FIELD92_MAX_SHEAR_Q"

    @property
    def field_index(self) -> int:
        return {
            Rx3ForceTarget.FIELD49_AXIAL_FORCE: 49,
            Rx3ForceTarget.FIELD50_MAX_MAJOR_AXIS_MOMENT: 50,
            Rx3ForceTarget.FIELD79_MAX_MINOR_AXIS_MOMENT: 79,
            Rx3ForceTarget.FIELD92_MAX_SHEAR_Q: 92,
        }[self]

    @property
    def unit(self) -> str:
        if self in {
            Rx3ForceTarget.FIELD50_MAX_MAJOR_AXIS_MOMENT,
            Rx3ForceTarget.FIELD79_MAX_MINOR_AXIS_MOMENT,
        }:
            return "kN*m"
        return "kN"


class LiraConventionError(LiraMappingError):
    """A force row cannot cross the LIRA to RX3 convention gate."""


class LiraGoverningSelectionError(LiraMappingError):
    """RX38 generation cannot proceed without governing-result evidence."""


@dataclass(frozen=True, slots=True)
class LiraRx3EvidenceScope:
    """Exact member scope for one piece of native-to-RX3 axis evidence."""

    profile_standard: str
    profile_name: str
    source_local_axis: str
    target_section_axis: str
    rx3_template: str
    stress_state: str
    member_length_m: Decimal
    member_rotation_degrees: Decimal
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "profile_standard",
            "profile_name",
            "source_local_axis",
            "target_section_axis",
            "rx3_template",
            "stress_state",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise LiraMappingError(f"{name} must be non-empty")
            object.__setattr__(self, name, value.strip())
        length = _exact_decimal(
            self.member_length_m,
            field="member_length_m",
        )
        if length <= 0:
            raise LiraMappingError("member_length_m must be positive")
        object.__setattr__(self, "member_length_m", length)
        rotation = _exact_decimal(
            self.member_rotation_degrees,
            field="member_rotation_degrees",
        )
        object.__setattr__(self, "member_rotation_degrees", rotation)
        references = self.evidence_references
        if isinstance(references, (str, bytes)) or not isinstance(references, tuple):
            raise LiraMappingError("evidence_references must be a tuple")
        normalized_references: list[str] = []
        for reference in references:
            if not isinstance(reference, str) or not reference.strip():
                raise LiraMappingError(
                    "evidence_references must contain non-empty strings"
                )
            normalized_references.append(reference.strip())
        if not normalized_references:
            raise LiraMappingError("evidence_references must not be empty")
        if len(normalized_references) != len(set(normalized_references)):
            raise LiraMappingError("evidence_references must not contain duplicates")
        object.__setattr__(self, "evidence_references", tuple(normalized_references))

    @classmethod
    def from_dict(cls, payload: object) -> LiraRx3EvidenceScope:
        if not isinstance(payload, Mapping):
            raise LiraMappingError("evidence_scope must be an object")
        allowed = {
            "profile_standard",
            "profile_name",
            "source_local_axis",
            "target_section_axis",
            "rx3_template",
            "stress_state",
            "member_length_m",
            "member_rotation_degrees",
            "evidence_references",
        }
        extra = set(payload) - allowed
        if extra:
            raise LiraMappingError(
                f"unknown evidence_scope fields: {sorted(extra)}"
            )
        missing = allowed - set(payload)
        if missing:
            raise LiraMappingError(f"incomplete evidence_scope: {sorted(missing)}")
        raw_references = payload["evidence_references"]
        if isinstance(raw_references, (str, bytes)) or not isinstance(
            raw_references, (list, tuple)
        ):
            raise LiraMappingError("evidence_references must be an array")
        return cls(
            profile_standard=_required_text(
                payload["profile_standard"], field="profile_standard"
            ),
            profile_name=_required_text(
                payload["profile_name"], field="profile_name"
            ),
            source_local_axis=_required_text(
                payload["source_local_axis"], field="source_local_axis"
            ),
            target_section_axis=_required_text(
                payload["target_section_axis"], field="target_section_axis"
            ),
            rx3_template=_required_text(
                payload["rx3_template"], field="rx3_template"
            ),
            stress_state=_required_text(
                payload["stress_state"], field="stress_state"
            ),
            member_length_m=payload["member_length_m"],
            member_rotation_degrees=payload["member_rotation_degrees"],
            evidence_references=tuple(raw_references),
        )

    def matches(
        self,
        *,
        profile_standard: str,
        profile_name: str,
        rx3_template: str,
        stress_state: str,
        member_length_m: Decimal,
        member_rotation_degrees: Decimal,
    ) -> bool:
        try:
            length = _exact_decimal(
                member_length_m,
                field="member_length_m",
            )
            rotation = _exact_decimal(
                member_rotation_degrees,
                field="member_rotation_degrees",
            )
        except LiraMappingError:
            return False
        return (
            isinstance(profile_standard, str)
            and profile_standard.strip() == self.profile_standard
            and isinstance(profile_name, str)
            and profile_name.strip() == self.profile_name
            and isinstance(rx3_template, str)
            and rx3_template.strip() == self.rx3_template
            and isinstance(stress_state, str)
            and stress_state.strip() == self.stress_state
            and length == self.member_length_m
            and rotation == self.member_rotation_degrees
        )

    def as_dict(self) -> dict[str, str | list[str]]:
        return {
            "profile_standard": self.profile_standard,
            "profile_name": self.profile_name,
            "source_local_axis": self.source_local_axis,
            "target_section_axis": self.target_section_axis,
            "rx3_template": self.rx3_template,
            "stress_state": self.stress_state,
            "member_length_m": str(self.member_length_m),
            "member_rotation_degrees": str(self.member_rotation_degrees),
            "evidence_references": list(self.evidence_references),
        }


@dataclass(frozen=True, slots=True)
class LiraRx3TransformedValue:
    """Auditable transformation of one caller-selected source observation."""

    source_component: str
    target_rx3_component: Rx3ForceTarget
    value_transform: ValueTransform
    source_parsed_value: Decimal
    source_signed_value: Decimal
    target_rx3_value: Decimal
    source_row: int
    source_sheet_or_table: str | None
    source_raw_token: str
    source_unit: str
    source_normalized_unit: str

    def __post_init__(self) -> None:
        if self.source_component not in LIRA_NATIVE_FORCE_COMPONENTS:
            raise LiraMappingError(
                f"Unknown LIRA force component: {self.source_component!r}"
            )
        if not isinstance(self.target_rx3_component, Rx3ForceTarget):
            raise LiraMappingError("target_rx3_component must be Rx3ForceTarget")
        if not isinstance(self.value_transform, ValueTransform):
            raise LiraMappingError("value_transform must be ValueTransform")
        source_value = _exact_decimal(
            self.source_signed_value,
            field="source_signed_value",
        )
        parsed_value = _exact_decimal(
            self.source_parsed_value,
            field="source_parsed_value",
        )
        target_value = _exact_decimal(
            self.target_rx3_value,
            field="target_rx3_value",
        )
        if target_value != self.value_transform.apply(source_value):
            raise LiraMappingError("target_rx3_value does not match value_transform")
        object.__setattr__(self, "source_signed_value", source_value)
        object.__setattr__(self, "source_parsed_value", parsed_value)
        object.__setattr__(self, "target_rx3_value", target_value)
        if isinstance(self.source_row, bool) or self.source_row < 1:
            raise LiraMappingError("source_row must be a positive integer")
        if self.source_sheet_or_table is not None and (
            not isinstance(self.source_sheet_or_table, str)
            or not self.source_sheet_or_table.strip()
        ):
            raise LiraMappingError(
                "source_sheet_or_table must be non-empty when provided"
            )
        for name in (
            "source_raw_token",
            "source_unit",
            "source_normalized_unit",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise LiraMappingError(f"{name} must be non-empty")
        if self.source_normalized_unit != self.target_rx3_component.unit:
            raise LiraMappingError(
                "source_normalized_unit must equal the RX3 target unit"
            )
        normalized_value, normalized_unit = to_review_unit(
            self.source_component,
            parsed_value,
            self.source_unit,
        )
        if normalized_unit != self.source_normalized_unit:
            raise LiraMappingError("source unit normalization is inconsistent")
        if normalized_value != source_value:
            raise LiraMappingError(
                "source_parsed_value does not normalize to source_signed_value"
            )

    def as_dict(self) -> dict[str, object]:
        return {
            "source": {
                "component": self.source_component,
                "parsed_value": str(self.source_parsed_value),
                "signed_value": str(self.source_signed_value),
                "signed_unit": self.source_normalized_unit,
                "raw_token": self.source_raw_token,
                "raw_unit": self.source_unit,
                "row": self.source_row,
                "sheet_or_table": self.source_sheet_or_table,
            },
            "target": {
                "component": self.target_rx3_component.value,
                "field_index": self.target_rx3_component.field_index,
                "value": str(self.target_rx3_value),
                "unit": self.target_rx3_component.unit,
            },
            "value_transform": self.value_transform.value,
            "governing_result_selection": "CALLER_SELECTED_UNVALIDATED",
        }


@dataclass(frozen=True, slots=True)
class LiraRx3ComponentConvention:
    source_component: str
    target_rx3_component: Rx3ForceTarget | None
    value_transform: ValueTransform | None
    axis_interpretation: str | None
    verification_status: ConventionStatus
    evidence_reference: str | None
    evidence_scope: LiraRx3EvidenceScope | None = None

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
        if self.target_rx3_component is not None and not isinstance(
            self.target_rx3_component, Rx3ForceTarget
        ):
            try:
                object.__setattr__(
                    self,
                    "target_rx3_component",
                    Rx3ForceTarget(self.target_rx3_component),
                )
            except (TypeError, ValueError) as exc:
                raise LiraMappingError("Invalid RX3 force writer target") from exc
        if self.value_transform is not None and not isinstance(
            self.value_transform, ValueTransform
        ):
            try:
                object.__setattr__(
                    self,
                    "value_transform",
                    ValueTransform(self.value_transform),
                )
            except (TypeError, ValueError) as exc:
                raise LiraMappingError("Invalid value transform") from exc
        for name in ("axis_interpretation", "evidence_reference"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, str) or not value.strip()
            ):
                raise LiraMappingError(f"{name} must be non-empty when provided")
        if self.evidence_scope is not None and not isinstance(
            self.evidence_scope, LiraRx3EvidenceScope
        ):
            raise LiraMappingError(
                "evidence_scope must be LiraRx3EvidenceScope when provided"
            )
        if self.verification_status is not ConventionStatus.UNKNOWN:
            missing = [
                name
                for name in (
                    "target_rx3_component",
                    "axis_interpretation",
                    "evidence_reference",
                    "evidence_scope",
                )
                if getattr(self, name) is None
            ]
            if (
                self.verification_status is ConventionStatus.VALIDATED
                and self.value_transform is None
            ):
                missing.append("value_transform")
            if missing:
                raise LiraMappingError(
                    f"{self.verification_status.value} convention is incomplete: {missing}"
                )

    @property
    def resolved(self) -> bool:
        return (
            self.verification_status is ConventionStatus.VALIDATED
            and self.target_rx3_component is not None
            and self.value_transform is not None
            and self.axis_interpretation is not None
            and self.evidence_reference is not None
            and self.evidence_scope is not None
        )

    def resolved_for(
        self,
        *,
        profile_standard: str | None,
        profile_name: str | None,
        rx3_template: str | None,
        stress_state: str | None,
        member_length_m: Decimal | None,
        member_rotation_degrees: Decimal | None,
    ) -> bool:
        if (
            not self.resolved
            or self.evidence_scope is None
            or profile_standard is None
            or profile_name is None
            or rx3_template is None
            or stress_state is None
            or member_length_m is None
            or member_rotation_degrees is None
        ):
            return False
        return self.evidence_scope.matches(
            profile_standard=profile_standard,
            profile_name=profile_name,
            rx3_template=rx3_template,
            stress_state=stress_state,
            member_length_m=member_length_m,
            member_rotation_degrees=member_rotation_degrees,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "source_component": self.source_component,
            "target_rx3_component": (
                None
                if self.target_rx3_component is None
                else self.target_rx3_component.value
            ),
            "value_transform": (
                None if self.value_transform is None else self.value_transform.value
            ),
            "axis_interpretation": self.axis_interpretation,
            "verification_status": self.verification_status.value,
            "evidence_reference": self.evidence_reference,
            "evidence_scope": (
                None if self.evidence_scope is None else self.evidence_scope.as_dict()
            ),
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
                    value_transform=None,
                    axis_interpretation=None,
                    verification_status=ConventionStatus.UNKNOWN,
                    evidence_reference=None,
                    evidence_scope=None,
                )
                for name in LIRA_NATIVE_FORCE_COMPONENTS
            }
        )

    @classmethod
    def validated_22p_one_plane_xx(cls) -> LiraRx3ConventionRegistry:
        """Validated magnitude transforms for the exact controlled 22П scope."""

        evidence_references = (
            "untouched LIRA force export with exact selected-row provenance",
            "element identity, stiffness/profile and nodes/length exports",
            "local-axis GUI observation and section-stiffness axis correspondence",
            "negative Mx and Q rejection plus positive-value acceptance in RX3",
            "persisted target-aware RX38 diff and unrelated-input invariance",
        )
        def evidence_scope(source_local_axis: str) -> LiraRx3EvidenceScope:
            return LiraRx3EvidenceScope(
                profile_standard="ГОСТ 8240-97",
                profile_name="22П",
                source_local_axis=source_local_axis,
                target_section_axis="X-X",
                rx3_template="Б2",
                stress_state="ONE_PLANE_BENDING",
                member_length_m=Decimal("3.00"),
                member_rotation_degrees=Decimal("0"),
                evidence_references=evidence_references,
            )

        unresolved = dict(cls.unresolved().components)
        unresolved["My"] = LiraRx3ComponentConvention(
            source_component="My",
            target_rx3_component=Rx3ForceTarget.FIELD50_MAX_MAJOR_AXIS_MOMENT,
            value_transform=ValueTransform.MAGNITUDE,
            axis_interpretation="LIRA local My -> RX3 X-X maximum Mx",
            verification_status=ConventionStatus.VALIDATED,
            evidence_reference="LIRA-RX3-22P-XX-MAGNITUDE",
            evidence_scope=evidence_scope("local My"),
        )
        unresolved["Qz"] = LiraRx3ComponentConvention(
            source_component="Qz",
            target_rx3_component=Rx3ForceTarget.FIELD92_MAX_SHEAR_Q,
            value_transform=ValueTransform.MAGNITUDE,
            axis_interpretation="LIRA local Qz -> RX3 X-X maximum Q",
            verification_status=ConventionStatus.VALIDATED,
            evidence_reference="LIRA-RX3-22P-XX-MAGNITUDE",
            evidence_scope=evidence_scope("local Qz"),
        )
        return cls(unresolved)

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
                "value_transform",
                "axis_interpretation",
                "verification_status",
                "evidence_reference",
                "evidence_scope",
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
                target_rx3_component=(
                    None
                    if raw.get("target_rx3_component") is None
                    else Rx3ForceTarget(
                        _required_text(
                            raw["target_rx3_component"],
                            field="target_rx3_component",
                        )
                    )
                ),
                value_transform=(
                    None
                    if raw.get("value_transform") is None
                    else ValueTransform(
                        _required_text(
                            raw["value_transform"],
                            field="value_transform",
                        )
                    )
                ),
                axis_interpretation=_optional_text(raw.get("axis_interpretation")),
                verification_status=ConventionStatus(str(status)),
                evidence_reference=_optional_text(raw.get("evidence_reference")),
                evidence_scope=(
                    None
                    if raw.get("evidence_scope") is None
                    else LiraRx3EvidenceScope.from_dict(raw["evidence_scope"])
                ),
            )
        return cls(components)

    def unresolved_components(
        self,
        available: Mapping[str, object],
        *,
        profile_standard: str | None = None,
        profile_name: str | None = None,
        rx3_template: str | None = None,
        stress_state: str | None = None,
        member_length_m: Decimal | None = None,
        member_rotation_degrees: Decimal | None = None,
    ) -> tuple[str, ...]:
        return tuple(
            name
            for name in LIRA_NATIVE_FORCE_COMPONENTS
            if available.get(name) is not None
            and not self.components[name].resolved_for(
                profile_standard=profile_standard,
                profile_name=profile_name,
                rx3_template=rx3_template,
                stress_state=stress_state,
                member_length_m=member_length_m,
                member_rotation_degrees=member_rotation_degrees,
            )
        )

    def require_rx38_generation_ready(
        self,
        available: Mapping[str, object],
        *,
        profile_standard: str | None = None,
        profile_name: str | None = None,
        rx3_template: str | None = None,
        stress_state: str | None = None,
        member_length_m: Decimal | None = None,
        member_rotation_degrees: Decimal | None = None,
    ) -> None:
        unresolved = self.unresolved_components(
            available,
            profile_standard=profile_standard,
            profile_name=profile_name,
            rx3_template=rx3_template,
            stress_state=stress_state,
            member_length_m=member_length_m,
            member_rotation_degrees=member_rotation_degrees,
        )
        if unresolved:
            raise LiraConventionError(
                "LIRA_RX3_FORCE_CONVENTION blocks RX38 force generation; "
                f"unresolved components: {', '.join(unresolved)}"
            )
        raise LiraGoverningSelectionError(
            "LIRA_GOVERNING_RESULT_SELECTION_UNRESOLVED blocks RX38 force "
            "generation; scoped component transformation does not select a "
            "governing load case, combination, or station"
        )

    def transform_selected_value(
        self,
        source_component: str,
        source_signed_value: Decimal,
        *,
        source_parsed_value: Decimal,
        source_row: int,
        source_sheet_or_table: str | None,
        source_raw_token: str,
        source_unit: str,
        normalized_unit: str,
        profile_standard: str,
        profile_name: str,
        rx3_template: str,
        stress_state: str,
        member_length_m: Decimal,
        member_rotation_degrees: Decimal,
    ) -> LiraRx3TransformedValue:
        """Transform one explicit row without performing envelope selection."""

        if source_component not in self.components:
            raise LiraMappingError(
                f"Unknown LIRA force component: {source_component!r}"
            )
        convention = self.components[source_component]
        if not convention.resolved_for(
            profile_standard=profile_standard,
            profile_name=profile_name,
            rx3_template=rx3_template,
            stress_state=stress_state,
            member_length_m=member_length_m,
            member_rotation_degrees=member_rotation_degrees,
        ):
            raise LiraConventionError(
                "LIRA_RX3_FORCE_CONVENTION blocks selected-value transformation; "
                f"{source_component} is unresolved for the supplied scope"
            )
        target = convention.target_rx3_component
        transform = convention.value_transform
        assert target is not None
        assert transform is not None
        if normalized_unit != target.unit:
            raise LiraMappingError(
                f"normalized_unit for {source_component} must be {target.unit!r}"
            )
        source_value = _exact_decimal(
            source_signed_value,
            field="source_signed_value",
        )
        return LiraRx3TransformedValue(
            source_component=source_component,
            target_rx3_component=target,
            value_transform=transform,
            source_parsed_value=source_parsed_value,
            source_signed_value=source_value,
            target_rx3_value=transform.apply(source_value),
            source_row=source_row,
            source_sheet_or_table=source_sheet_or_table,
            source_raw_token=source_raw_token,
            source_unit=source_unit,
            source_normalized_unit=normalized_unit,
        )

    def as_dict(self) -> dict[str, dict[str, object]]:
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


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LiraMappingError(f"{field} must be non-empty")
    return value.strip()


def _exact_decimal(value: object, *, field: str) -> Decimal:
    if isinstance(value, (bool, float)):
        raise LiraMappingError(f"{field} must be an exact decimal")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise LiraMappingError(f"{field} must be an exact decimal") from exc
    if not result.is_finite():
        raise LiraMappingError(f"{field} must be finite")
    return result
