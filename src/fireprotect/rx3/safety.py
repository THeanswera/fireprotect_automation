from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Mapping

from ..execution import ExecutionMode
from ..model import Dimension, ProjectElement, Quantity, Unit
from ..release import BlockerCode, ReleaseBlocker
from .parser import Rx38Record
from .schema import TCONSTR_FIELD_COUNT, field_spec


class Rx3SafetyError(ValueError):
    """A calculation-significant RX3 value lacks sufficient evidence."""


class UnverifiedRx38ActionMappingError(Rx3SafetyError):
    """A non-zero action cannot be represented by a confirmed RX38 field."""


class ForceConventionError(Rx3SafetyError):
    pass


class SteelCompatibilityError(Rx3SafetyError):
    pass


class TemplateProfileError(Rx3SafetyError):
    pass


class EvidenceStatus(str, Enum):
    UNVERIFIED = "UNVERIFIED"
    ENGINEER_CONFIRMED = "ENGINEER_CONFIRMED"
    VERIFIED = "VERIFIED"


class Rx3TemplateUseCase(str, Enum):
    AXIAL_ONLY = "AXIAL_ONLY"


class ForceConventionStatus(str, Enum):
    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"


class SteelCompatibilityStatus(str, Enum):
    VERIFIED = "VERIFIED"
    LEGACY_NUMERIC_MATCH = "LEGACY_NUMERIC_MATCH"
    INCOMPATIBLE = "INCOMPATIBLE"


class GuiExecutionEvidence(str, Enum):
    NOT_PROVIDED = "NOT_PROVIDED"
    ENGINEER_CONFIRMED = "ENGINEER_CONFIRMED"
    HASH_ONLY = "HASH_ONLY"
    SCREENSHOT_REFERENCED = "SCREENSHOT_REFERENCED"
    OTHER = "OTHER"


def rx38_record_fingerprint(record: Rx38Record) -> str:
    """Return a stable fingerprint covering every positional field."""

    if record.record_type != "Tconstr" or len(record.fields) != TCONSTR_FIELD_COUNT:
        raise ValueError("Template fingerprint requires one 200-field Tconstr record")
    return sha256("\0".join(record.fields).encode("utf-8")).hexdigest()


def _decimal(value: object, *, name: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError(f"{name} must be Decimal, int or str, not binary float")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise TypeError(f"{name} must be a finite Decimal") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def _record_decimal(record: Rx38Record, index: int) -> Decimal:
    raw = record.fields[index].strip().replace(",", ".")
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise TemplateProfileError(
            f"Template field {index} ({field_spec(index).name}) is not decimal"
        ) from exc
    if not value.is_finite():
        raise TemplateProfileError(
            f"Template field {index} ({field_spec(index).name}) must be finite"
        )
    return value


@dataclass(frozen=True, slots=True)
class ActionZeroTolerance:
    force: Quantity
    moment: Quantity

    def __post_init__(self) -> None:
        if not isinstance(self.force, Quantity) or not isinstance(
            self.moment, Quantity
        ):
            raise TypeError("ActionZeroTolerance values must be Quantity")
        if self.force.dimension is not Dimension.FORCE:
            raise ValueError("ActionZeroTolerance.force must be a force")
        if self.moment.dimension is not Dimension.MOMENT:
            raise ValueError("ActionZeroTolerance.moment must be a moment")
        if self.force.si_value < 0 or self.moment.si_value < 0:
            raise ValueError("Action zero tolerances must not be negative")

    @classmethod
    def strict(cls) -> "ActionZeroTolerance":
        return cls(
            Quantity.of(Decimal("0"), Unit.KILONEWTON),
            Quantity.of(Decimal("0"), Unit.KILONEWTON_METER),
        )

    def significant(self, name: str, value: Quantity) -> bool:
        threshold = self.moment if name in {"Mx", "My"} else self.force
        return abs(value.si_value) > threshold.si_value

    def as_dict(self) -> dict[str, dict[str, str]]:
        return {
            "force": {"value": str(self.force.value), "unit": self.force.unit.value},
            "moment": {
                "value": str(self.moment.value),
                "unit": self.moment.unit.value,
            },
        }


@dataclass(frozen=True, slots=True)
class Rx3TemplateEvidence:
    use_case: Rx3TemplateUseCase
    status: EvidenceStatus
    source: str
    engineer_confirmation: bool
    confirmed_by: str | None
    confirmed_at: date | None
    version: str | None
    calculation_profile_verified: bool = False
    template_record_sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.use_case, Rx3TemplateUseCase):
            object.__setattr__(self, "use_case", Rx3TemplateUseCase(self.use_case))
        if not isinstance(self.status, EvidenceStatus):
            object.__setattr__(self, "status", EvidenceStatus(self.status))
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("Rx3TemplateEvidence.source must not be empty")
        if not isinstance(self.engineer_confirmation, bool):
            raise TypeError("template engineer_confirmation must be bool")
        if not isinstance(self.calculation_profile_verified, bool):
            raise TypeError("calculation_profile_verified must be bool")
        if self.confirmed_at is not None and not isinstance(self.confirmed_at, date):
            raise TypeError("template confirmed_at must be date or None")
        if self.template_record_sha256 is not None:
            if not isinstance(self.template_record_sha256, str):
                raise TypeError("template_record_sha256 must be str or None")
            fingerprint = self.template_record_sha256.lower()
            if len(fingerprint) != 64 or any(
                char not in "0123456789abcdef" for char in fingerprint
            ):
                raise ValueError("template_record_sha256 must be a SHA-256 hex digest")
            object.__setattr__(self, "template_record_sha256", fingerprint)
        if self.status is EvidenceStatus.VERIFIED:
            if (
                not self.engineer_confirmation
                or not isinstance(self.confirmed_by, str)
                or not self.confirmed_by.strip()
                or self.confirmed_at is None
                or not isinstance(self.version, str)
                or not self.version.strip()
            ):
                raise ValueError(
                    "VERIFIED template evidence requires engineer, date and version"
                )

    @property
    def verified_axial_only(self) -> bool:
        return (
            self.use_case is Rx3TemplateUseCase.AXIAL_ONLY
            and self.status is EvidenceStatus.VERIFIED
            and self.engineer_confirmation
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "use_case": self.use_case.value,
            "status": self.status.value,
            "source": self.source,
            "engineer_confirmation": self.engineer_confirmation,
            "confirmed_by": self.confirmed_by,
            "confirmed_at": (
                self.confirmed_at.isoformat() if self.confirmed_at else None
            ),
            "version": self.version,
            "calculation_profile_verified": self.calculation_profile_verified,
            "template_record_sha256": self.template_record_sha256,
        }


@dataclass(frozen=True, slots=True)
class LiraRx3ForceConvention:
    source_system: str
    target_system: str
    positive_n_meaning: str
    negative_n_meaning: str
    local_axes: str
    moment_mapping: str
    shear_mapping: str
    multipliers: Mapping[str, Decimal]
    rule_name: str
    evidence_source: str | None
    status: ForceConventionStatus
    engineer_confirmation: bool
    confirmed_by: str | None
    confirmed_at: date | None
    version: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ForceConventionStatus):
            object.__setattr__(self, "status", ForceConventionStatus(self.status))
        if not isinstance(self.engineer_confirmation, bool):
            raise TypeError("force convention engineer_confirmation must be bool")
        if self.confirmed_at is not None and not isinstance(self.confirmed_at, date):
            raise TypeError("force convention confirmed_at must be date or None")
        for name in (
            "source_system",
            "target_system",
            "positive_n_meaning",
            "negative_n_meaning",
            "local_axes",
            "moment_mapping",
            "shear_mapping",
            "rule_name",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"LiraRx3ForceConvention.{name} must not be empty")
        values = dict(self.multipliers)
        expected = {"N", "Mx", "My", "Qx", "Qy"}
        if set(values) != expected:
            raise ValueError("Force convention multipliers must contain N/Mx/My/Qx/Qy")
        converted = {name: _decimal(value, name=name) for name, value in values.items()}
        if any(value not in {Decimal("-1"), Decimal("1")} for value in converted.values()):
            raise ValueError("Force convention multipliers must be exactly -1 or 1")
        if self.status is ForceConventionStatus.VERIFIED:
            if (
                not isinstance(self.evidence_source, str)
                or not self.evidence_source.strip()
                or not self.engineer_confirmation
                or not isinstance(self.confirmed_by, str)
                or not self.confirmed_by.strip()
                or self.confirmed_at is None
                or not isinstance(self.version, str)
                or not self.version.strip()
            ):
                raise ValueError(
                    "VERIFIED force convention requires evidence, engineer, date and version"
                )
        object.__setattr__(self, "multipliers", MappingProxyType(converted))

    @property
    def verified(self) -> bool:
        return (
            self.status is ForceConventionStatus.VERIFIED
            and self.engineer_confirmation
        )

    def transform(self, name: str, value: Quantity) -> Quantity:
        try:
            multiplier = self.multipliers[name]
        except KeyError as exc:
            raise KeyError(f"Unknown force component: {name}") from exc
        return Quantity(value.value * multiplier, value.unit)

    def audit(self, name: str, source: Quantity, target: Quantity) -> dict[str, Any]:
        return {
            "component": name,
            "source": {"value": str(source.value), "unit": source.unit.value},
            "target": {"value": str(target.value), "unit": target.unit.value},
            "multiplier": str(self.multipliers[name]),
            "rule": self.rule_name,
            "status": self.status.value,
            "evidence_source": self.evidence_source,
        }


@dataclass(frozen=True, slots=True)
class SteelCalculationProperties:
    steel_grade: str
    nominal_yield_strength: Quantity | None
    design_yield_strength: Quantity | None
    rx3_stored_strength_parameter: Quantity | None
    thickness_min: Quantity | None
    thickness_max: Quantity | None
    elastic_modulus: Quantity
    density: Quantity
    temperature_model: str | None
    source_document: str
    clause_or_table: str
    material_standard: str
    confidence: EvidenceStatus
    provenance: str
    rx3_strength_mapping_verified: bool

    def __post_init__(self) -> None:
        if not isinstance(self.steel_grade, str) or not self.steel_grade.strip():
            raise ValueError("steel_grade must not be empty")
        for name in ("source_document", "clause_or_table", "material_standard", "provenance"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"SteelCalculationProperties.{name} must not be empty")
        if not isinstance(self.confidence, EvidenceStatus):
            object.__setattr__(self, "confidence", EvidenceStatus(self.confidence))
        if not isinstance(self.rx3_strength_mapping_verified, bool):
            raise TypeError("rx3_strength_mapping_verified must be bool")
        for name in (
            "nominal_yield_strength",
            "design_yield_strength",
            "rx3_stored_strength_parameter",
        ):
            value = getattr(self, name)
            if value is not None:
                if not isinstance(value, Quantity) or value.dimension is not Dimension.PRESSURE:
                    raise ValueError(f"{name} must be a pressure")
                if value.si_value <= 0:
                    raise ValueError(f"{name} must be greater than zero")
        for name in ("thickness_min", "thickness_max"):
            value = getattr(self, name)
            if value is not None:
                if not isinstance(value, Quantity) or value.dimension is not Dimension.LENGTH:
                    raise ValueError(f"{name} must be a length")
                if value.si_value <= 0:
                    raise ValueError(f"{name} must be greater than zero")
        if (
            self.thickness_min is not None
            and self.thickness_max is not None
            and self.thickness_min.si_value > self.thickness_max.si_value
        ):
            raise ValueError("thickness_min must not exceed thickness_max")
        if not isinstance(self.elastic_modulus, Quantity):
            raise TypeError("elastic_modulus must be Quantity")
        if self.elastic_modulus.dimension is not Dimension.PRESSURE:
            raise ValueError("elastic_modulus must be a pressure")
        if self.elastic_modulus.si_value <= 0:
            raise ValueError("elastic_modulus must be greater than zero")
        if not isinstance(self.density, Quantity):
            raise TypeError("density must be Quantity")
        if self.density.dimension is not Dimension.DENSITY:
            raise ValueError("density must be a density")
        if self.density.si_value <= 0:
            raise ValueError("density must be greater than zero")
        if self.temperature_model is not None and (
            not isinstance(self.temperature_model, str)
            or not self.temperature_model.strip()
        ):
            raise ValueError("temperature_model must be non-empty when provided")
        if self.rx3_strength_mapping_verified and (
            self.confidence is not EvidenceStatus.VERIFIED
            or self.rx3_stored_strength_parameter is None
        ):
            raise ValueError(
                "Verified RX3 strength mapping requires VERIFIED evidence and a stored parameter"
            )


@dataclass(frozen=True, slots=True)
class SteelCompatibilityReport:
    status: SteelCompatibilityStatus
    template_grade: str
    project_grade: str
    template_strength_mpa: Decimal
    project_legacy_ry_mpa: Decimal
    write_values: Mapping[int, str]
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    evidence: Mapping[str, Any]

    @property
    def compatible(self) -> bool:
        return self.status is not SteelCompatibilityStatus.INCOMPATIBLE

    @property
    def verified(self) -> bool:
        return self.status is SteelCompatibilityStatus.VERIFIED

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "template_grade": self.template_grade,
            "project_grade": self.project_grade,
            "template_strength_mpa": str(self.template_strength_mpa),
            "project_legacy_ry_mpa": str(self.project_legacy_ry_mpa),
            "write_values": {str(k): v for k, v in self.write_values.items()},
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "evidence": dict(self.evidence),
        }


def _same_text(left: str, right: str) -> bool:
    return "".join(left.casefold().split()) == "".join(right.casefold().split())


def evaluate_steel_compatibility(
    element: ProjectElement,
    template: Rx38Record,
    properties: SteelCalculationProperties | None,
) -> SteelCompatibilityReport:
    element.require_fields("steel_grade", "Ry", "E", "density")
    project_grade = element.steel_grade or ""
    template_grade = template.fields[42]
    template_strength = _record_decimal(template, 33)
    template_elastic_modulus = _record_decimal(template, 34)
    template_density = _record_decimal(template, 32)
    project_ry = element.Ry.to(Unit.MEGAPASCAL).value  # type: ignore[union-attr]
    project_elastic_modulus = element.E.to(Unit.MEGAPASCAL).value  # type: ignore[union-attr]
    project_density = element.density.to(Unit.KILOGRAM_PER_CUBIC_METER).value  # type: ignore[union-attr]
    write_values: dict[int, str] = {}
    blockers: list[str] = []
    warnings: list[str] = []
    evidence: dict[str, Any] = {
        "mapping": "LEGACY_UNVERIFIED",
        "project_E": {
            "value": str(element.E.value),  # type: ignore[union-attr]
            "unit": element.E.unit.value,  # type: ignore[union-attr]
        },
        "project_density": {
            "value": str(element.density.value),  # type: ignore[union-attr]
            "unit": element.density.unit.value,  # type: ignore[union-attr]
        },
    }

    if properties is None or not properties.rx3_strength_mapping_verified:
        if not _same_text(project_grade, template_grade):
            blockers.append(
                "Steel grade differs while RX3 stored-strength compatibility is unverified"
            )
        if project_ry != template_strength:
            blockers.append(
                "ProjectElement.Ry differs from template field 33 while its semantics remain ambiguous"
            )
        if project_elastic_modulus != template_elastic_modulus:
            blockers.append("ProjectElement.E differs from template field 34")
        if project_density != template_density:
            blockers.append("ProjectElement.density differs from template field 32")
        warnings.append(
            "LEGACY_STEEL_RY_AMBIGUITY: numeric equality is not proof that ProjectElement.Ry and RX38 field 33 have identical normative semantics"
        )
        status = (
            SteelCompatibilityStatus.INCOMPATIBLE
            if blockers
            else SteelCompatibilityStatus.LEGACY_NUMERIC_MATCH
        )
    else:
        stored = properties.rx3_stored_strength_parameter
        if stored is None:
            raise SteelCompatibilityError(
                "Verified RX3 strength mapping has no stored strength parameter"
            )
        if not _same_text(properties.steel_grade, project_grade):
            blockers.append("Verified steel profile grade differs from ProjectElement")
        if not _same_text(template_grade, project_grade):
            blockers.append(
                "Steel grade change is blocked because the template thermal/numeric profile is not independently mapped"
            )
        if properties.design_yield_strength is None:
            blockers.append(
                "Verified steel profile has no design_yield_strength for ProjectElement.Ry compatibility"
            )
        elif properties.design_yield_strength.si_value != element.Ry.si_value:  # type: ignore[union-attr]
            blockers.append(
                "Verified steel profile design_yield_strength differs from ProjectElement.Ry"
            )
        if properties.elastic_modulus.si_value != element.E.si_value:  # type: ignore[union-attr]
            blockers.append("Verified steel profile E differs from ProjectElement")
        if properties.density.si_value != element.density.si_value:  # type: ignore[union-attr]
            blockers.append("Verified steel profile density differs from ProjectElement")
        write_values = {
            33: format(stored.to(Unit.MEGAPASCAL).value, "f").replace(".", ","),
            42: project_grade,
        }
        evidence = {
            "mapping": "VERIFIED_TYPED_STEEL_PROPERTIES",
            "source_document": properties.source_document,
            "clause_or_table": properties.clause_or_table,
            "material_standard": properties.material_standard,
            "temperature_model": properties.temperature_model,
            "confidence": properties.confidence.value,
            "provenance": properties.provenance,
        }
        status = (
            SteelCompatibilityStatus.INCOMPATIBLE
            if blockers
            else SteelCompatibilityStatus.VERIFIED
        )

    if blockers:
        raise SteelCompatibilityError("; ".join(blockers))
    return SteelCompatibilityReport(
        status,
        template_grade,
        project_grade,
        template_strength,
        project_ry,
        MappingProxyType(write_values),
        tuple(blockers),
        tuple(warnings),
        MappingProxyType(evidence),
    )


@dataclass(frozen=True, slots=True)
class Rx3Input:
    mark: str
    axial_force: Quantity
    Mx: Quantity
    My: Quantity
    Qx: Quantity
    Qy: Quantity
    force_transformations: tuple[Mapping[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        def q(value: Quantity) -> dict[str, str]:
            return {"value": str(value.value), "unit": value.unit.value}

        return {
            "mark": self.mark,
            "N": q(self.axial_force),
            "Mx": q(self.Mx),
            "My": q(self.My),
            "Qx": q(self.Qx),
            "Qy": q(self.Qy),
            "force_transformations": [dict(item) for item in self.force_transformations],
        }


@dataclass(frozen=True, slots=True)
class Rx3SafetyContext:
    mode: ExecutionMode
    action_zero_tolerance: ActionZeroTolerance
    template_evidence: Rx3TemplateEvidence | None
    force_convention: LiraRx3ForceConvention | None
    steel_properties: SteelCalculationProperties | None
    controlled_experiment: bool = False
    allow_unverified_force_convention: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.mode, ExecutionMode):
            raise TypeError("Rx3SafetyContext.mode must be ExecutionMode")
        if not isinstance(self.action_zero_tolerance, ActionZeroTolerance):
            raise TypeError("action_zero_tolerance must be ActionZeroTolerance")
        if self.template_evidence is not None and not isinstance(
            self.template_evidence, Rx3TemplateEvidence
        ):
            raise TypeError("template_evidence must be Rx3TemplateEvidence or None")
        if self.force_convention is not None and not isinstance(
            self.force_convention, LiraRx3ForceConvention
        ):
            raise TypeError("force_convention must be LiraRx3ForceConvention or None")
        if self.steel_properties is not None and not isinstance(
            self.steel_properties, SteelCalculationProperties
        ):
            raise TypeError("steel_properties must be SteelCalculationProperties or None")
        if not isinstance(self.controlled_experiment, bool):
            raise TypeError("controlled_experiment must be bool")
        if not isinstance(self.allow_unverified_force_convention, bool):
            raise TypeError("allow_unverified_force_convention must be bool")
        if self.mode is ExecutionMode.PRODUCTION and (
            self.action_zero_tolerance.force.si_value != 0
            or self.action_zero_tolerance.moment.si_value != 0
        ):
            raise ValueError("PRODUCTION requires exact-zero action tolerances")

    @classmethod
    def draft(cls) -> "Rx3SafetyContext":
        return cls(
            ExecutionMode.DRAFT,
            ActionZeroTolerance.strict(),
            None,
            None,
            None,
        )


def build_rx3_input(
    element: ProjectElement,
    context: Rx3SafetyContext,
) -> tuple[Rx3Input, tuple[ReleaseBlocker, ...], tuple[str, ...]]:
    element.require_fields("N", "Mx", "My", "Qx", "Qy")
    components = {
        name: getattr(element, name) for name in ("N", "Mx", "My", "Qx", "Qy")
    }
    action_blockers: list[ReleaseBlocker] = []
    for name in ("Mx", "My", "Qx", "Qy"):
        value = components[name]
        if not isinstance(value, Quantity):
            raise UnverifiedRx38ActionMappingError(
                f"{name} must be an explicit Quantity"
            )
        if context.action_zero_tolerance.significant(name, value):
            code = BlockerCode[f"UNVERIFIED_RX38_{name.upper()}_MAPPING"]
            action_blockers.append(
                ReleaseBlocker(
                    code,
                    f"{name}={value.value} {value.unit.value} is significant and has no confirmed writable RX38 mapping",
                    element.element_id,
                )
            )
    if action_blockers:
        details = "; ".join(item.message for item in action_blockers)
        raise UnverifiedRx38ActionMappingError(details)

    if context.mode in {ExecutionMode.VALIDATION, ExecutionMode.PRODUCTION}:
        if context.template_evidence is None or not context.template_evidence.verified_axial_only:
            raise TemplateProfileError(
                "Pure axial RX38 generation requires separately verified AXIAL_ONLY template evidence"
            )

    n = components["N"]
    if not isinstance(n, Quantity):
        raise ForceConventionError("N must be an explicit Quantity")
    convention = context.force_convention
    significant_n = context.action_zero_tolerance.significant("N", n)
    if (
        context.mode is ExecutionMode.PRODUCTION
        and (convention is None or not convention.verified)
    ):
        raise ForceConventionError(
            "Production requires a verified LIRA -> RX3 force convention"
        )
    controlled_exception = (
        context.mode is ExecutionMode.VALIDATION
        and context.controlled_experiment
        and context.allow_unverified_force_convention
    )
    if significant_n and (convention is None or not convention.verified):
        if not controlled_exception:
            raise ForceConventionError(
                "A significant N cannot be written without a verified LIRA -> RX3 force convention"
            )
        target_n = n
        transformations: tuple[Mapping[str, Any], ...] = (
            {
                "component": "N",
                "source": {"value": str(n.value), "unit": n.unit.value},
                "target": {"value": str(n.value), "unit": n.unit.value},
                "rule": "controlled_experiment_identity_not_verified",
                "status": "UNVERIFIED_VALIDATION_EXCEPTION",
            },
        )
        warnings: tuple[str, ...] = (
            "UNVERIFIED_FORCE_CONVENTION accepted only for this controlled VALIDATION experiment",
        )
    elif convention is not None:
        target_n = convention.transform("N", n)
        transformations = (convention.audit("N", n, target_n),)
        warnings = ()
    else:
        target_n = n
        transformations = ()
        warnings = ()

    zeros = {name: components[name] for name in ("Mx", "My", "Qx", "Qy")}
    return (
        Rx3Input(
            element.mark,
            target_n,
            zeros["Mx"],
            zeros["My"],
            zeros["Qx"],
            zeros["Qy"],
            transformations,
        ),
        tuple(action_blockers),
        warnings,
    )


@dataclass(frozen=True, slots=True)
class ProfileDifference:
    index: int
    name: str
    template_value: str
    required_value: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "template_value": self.template_value,
            "required_value": self.required_value,
        }


@dataclass(frozen=True, slots=True)
class Rx3CalculationProfile:
    confirmed_matches: tuple[int, ...]
    confirmed_mismatches: tuple[ProfileDifference, ...]
    probable_preserved: tuple[int, ...]
    unknown_preserved_count: int
    unknown_fingerprint: str
    unverified_calculation_settings: tuple[int, ...]
    evidence: Rx3TemplateEvidence | None

    @property
    def verified(self) -> bool:
        return (
            not self.confirmed_mismatches
            and not self.unverified_calculation_settings
            and self.evidence is not None
            and self.evidence.calculation_profile_verified
            and self.evidence.status is EvidenceStatus.VERIFIED
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "confirmed_match": list(self.confirmed_matches),
            "confirmed_mismatch": [item.as_dict() for item in self.confirmed_mismatches],
            "probable_preserved": list(self.probable_preserved),
            "unknown_preserved_count": self.unknown_preserved_count,
            "unknown_fingerprint": self.unknown_fingerprint,
            "unverified_calculation_setting": list(self.unverified_calculation_settings),
            "verified": self.verified,
            "evidence": None if self.evidence is None else self.evidence.as_dict(),
        }


_CRITICAL_TEMPLATE_SETTINGS = frozenset(
    {35, 36, 37, 38, 39, 40, 41, 82, 83, 84, 104, 188, 189}
)


def evaluate_calculation_profile(
    template: Rx38Record,
    required_values: Mapping[int, str],
    evidence: Rx3TemplateEvidence | None,
    *,
    evidence_template: Rx38Record | None = None,
) -> Rx3CalculationProfile:
    matches: list[int] = []
    mismatches: list[ProfileDifference] = []
    for index, value in sorted(required_values.items()):
        if template.fields[index] == value:
            matches.append(index)
        else:
            mismatches.append(
                ProfileDifference(
                    index,
                    field_spec(index).name,
                    template.fields[index],
                    value,
                )
            )
    probable = tuple(
        index
        for index in range(TCONSTR_FIELD_COUNT)
        if field_spec(index).confidence == "probable"
    )
    unknown_tokens = [
        f"{index}:{template.fields[index]}"
        for index in range(TCONSTR_FIELD_COUNT)
        if field_spec(index).confidence == "unknown"
    ]
    fingerprint = sha256("\0".join(unknown_tokens).encode("utf-8")).hexdigest()
    unverified: tuple[int, ...] = ()
    if (
        evidence is None
        or not evidence.calculation_profile_verified
        or evidence.template_record_sha256
        != rx38_record_fingerprint(evidence_template or template)
    ):
        unverified = tuple(sorted(_CRITICAL_TEMPLATE_SETTINGS))
    return Rx3CalculationProfile(
        tuple(matches),
        tuple(mismatches),
        probable,
        len(unknown_tokens),
        fingerprint,
        unverified,
        evidence,
    )
