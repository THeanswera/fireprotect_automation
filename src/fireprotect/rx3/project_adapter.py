from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
import tempfile
from typing import Any

from ..execution import ExecutionMode
from ..files import ExclusiveInstallError, install_file_no_overwrite
from ..model import EffectiveLengthParameters, ProjectElement, Quantity, Unit
from .diff import diff_records
from .parser import (
    Rx38Construction,
    Rx38Document,
    Rx38Record,
    _with_compatibility_checked_field,
    read_rx38_document,
    write_rx38,
)
from .profiles import normalize_profile_name, normalize_standard
from .schema import TCONSTR_FIELD_COUNT, WritePolicy, field_spec
from .safety import (
    HeatingExposureError,
    HeatingExposureVerification,
    Rx3CalculationProfile,
    Rx3SafetyContext,
    SteelCompatibilityError,
    SteelCompatibilityReport,
    TemplateProfileError,
    build_rx3_input,
    evaluate_calculation_profile,
    evaluate_steel_compatibility,
    rx38_record_fingerprint,
)


class Rx38ProjectAdapterError(ValueError):
    pass


class Rx38TemplateMismatchError(Rx38ProjectAdapterError):
    pass


class Rx38EngineeringConflictError(Rx38ProjectAdapterError):
    pass


class DerivedFieldWritePolicy(str, Enum):
    PRESERVE_DERIVED_IF_INPUTS_UNCHANGED = (
        "PRESERVE_DERIVED_IF_INPUTS_UNCHANGED"
    )
    RECOMPUTE_VERIFIED = "RECOMPUTE_VERIFIED"


@dataclass(frozen=True)
class Rx38FieldChange:
    index: int
    name: str
    old_value: str
    new_value: str


@dataclass(frozen=True)
class Rx38CreationReport:
    template: str
    output: str
    template_mark: str
    output_mark: str
    target_record_position: int
    template_record_sha256: str
    output_record_sha256: str
    changed_fields: tuple[Rx38FieldChange, ...]
    preserved_fields_count: int
    unknown_fields_count: int
    warnings: tuple[str, ...]
    round_trip_valid: bool
    safety_mode: str
    rx3_input: dict[str, Any]
    steel_compatibility: dict[str, Any]
    template_profile: dict[str, Any]
    heating_exposure: dict[str, Any]
    stale_template_result_indices: tuple[int, ...]
    steel_production_evidence: SteelCompatibilityReport
    template_production_evidence: Rx3CalculationProfile
    heating_production_evidence: HeatingExposureVerification

    def as_dict(self) -> dict[str, Any]:
        return {
            "template": self.template,
            "output": self.output,
            "template_mark": self.template_mark,
            "output_mark": self.output_mark,
            "target_record_position": self.target_record_position,
            "template_record_sha256": self.template_record_sha256,
            "output_record_sha256": self.output_record_sha256,
            "changed_fields": [change.__dict__ for change in self.changed_fields],
            "preserved_fields_count": self.preserved_fields_count,
            "unknown_fields_count": self.unknown_fields_count,
            "warnings": list(self.warnings),
            "round_trip_valid": self.round_trip_valid,
            "safety_mode": self.safety_mode,
            "rx3_input": self.rx3_input,
            "steel_compatibility": self.steel_compatibility,
            "template_profile": self.template_profile,
            "heating_exposure": self.heating_exposure,
            "stale_template_result_indices": list(
                self.stale_template_result_indices
            ),
        }


def _format_decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return (text or "0").replace(".", ",")


def _format_decimal_preserving_scale(value: Decimal) -> str:
    return format(value, "f").replace(".", ",")


def _as_decimal(quantity: Quantity, unit: Unit) -> Decimal:
    return quantity.to(unit).value


def _same_text(left: str, right: str) -> bool:
    return " ".join(left.casefold().split()) == " ".join(right.casefold().split())


_DERIVED_FIELD_INPUTS: dict[int, tuple[int, ...]] = {
    22: (20, 21),
    23: (20, 21),
    24: (21, 14),
    25: (21, 14, 15),
    66: (20, 14, 32),
    67: (20, 14, 15, 32),
}
_DERIVED_ABSOLUTE_TOLERANCE = Decimal("1e-12")
_DERIVED_RELATIVE_TOLERANCE = Decimal("2e-15")


def _decimal_token(value: str) -> Decimal | None:
    try:
        parsed = Decimal(value.strip().replace(",", "."))
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def _numeric_tokens_equal(left: str, right: str) -> bool:
    left_value = _decimal_token(left)
    right_value = _decimal_token(right)
    return (
        left_value is not None
        and right_value is not None
        and left_value == right_value
    )


def _derived_value_is_compatible(existing: str, calculated: str) -> bool:
    existing_value = _decimal_token(existing)
    calculated_value = _decimal_token(calculated)
    if existing_value is None or calculated_value is None:
        return False
    difference = abs(existing_value - calculated_value)
    tolerance = max(
        _DERIVED_ABSOLUTE_TOLERANCE,
        abs(calculated_value) * _DERIVED_RELATIVE_TOLERANCE,
    )
    return difference <= tolerance


def _apply_derived_field_policy(
    template: Rx38Record,
    values: dict[int, str],
    policy: DerivedFieldWritePolicy,
) -> None:
    for derived_index, input_indices in _DERIVED_FIELD_INPUTS.items():
        if policy is DerivedFieldWritePolicy.RECOMPUTE_VERIFIED:
            continue
        inputs_unchanged = all(
            _numeric_tokens_equal(template.fields[index], values[index])
            for index in input_indices
        )
        if not inputs_unchanged:
            continue
        calculated = values[derived_index]
        existing = template.fields[derived_index]
        if not _derived_value_is_compatible(existing, calculated):
            raise Rx38EngineeringConflictError(
                f"Derived field {derived_index} ({field_spec(derived_index).name}) "
                "is incompatible with its unchanged authoritative inputs; "
                "refusing an implicit rewrite"
            )
        values[derived_index] = existing


def _select_template_record(
    document: Rx38Document,
    element: ProjectElement,
    template_mark: str | None,
    template_record_sha256: str | None,
) -> tuple[int, Rx38Record]:
    indexed = [(index, record) for index, record in enumerate(document.records) if record.record_type == "Tconstr"]
    if template_record_sha256 is not None:
        matches = [
            (index, record)
            for index, record in indexed
            if rx38_record_fingerprint(record) == template_record_sha256
        ]
        if len(matches) != 1:
            raise Rx38TemplateMismatchError(
                "Template record fingerprint matched "
                f"{len(matches)} constructions; exact unique resolution is required"
            )
        index, record = matches[0]
        if template_mark is not None and record.mark != template_mark:
            raise Rx38TemplateMismatchError(
                f"Template fingerprint resolved mark {record.mark!r}, not {template_mark!r}"
            )
        return index, record
    selector = template_mark or element.mark
    matches = [(index, record) for index, record in indexed if record.mark == selector]
    if len(matches) == 1:
        return matches[0]
    if template_mark is None and len(indexed) == 1:
        return indexed[0]
    raise Rx38TemplateMismatchError(
        f"Template selector {selector!r} matched {len(matches)} constructions; "
        "provide an unambiguous --template-mark"
    )


def _require_template_compatibility(element: ProjectElement, template: Rx38Record) -> None:
    element.require_fields("section_type", "profile_standard", "profile_name", "area")
    if not _same_text(element.section_type or "", template.section_name or ""):
        raise Rx38TemplateMismatchError(
            f"Section type mismatch: ProjectElement={element.section_type!r}, template={template.section_name!r}"
        )
    if normalize_standard(element.profile_standard or "") != normalize_standard(template.standard or ""):
        raise Rx38TemplateMismatchError(
            f"Profile standard mismatch: ProjectElement={element.profile_standard!r}, template={template.standard!r}"
        )
    if normalize_profile_name(element.profile_name or "") != normalize_profile_name(template.profile or ""):
        raise Rx38TemplateMismatchError(
            f"Profile mismatch: ProjectElement={element.profile_name!r}, template={template.profile!r}"
        )
    template_area = Decimal(template.fields[20].replace(",", "."))
    project_area = _as_decimal(element.area, Unit.SQUARE_MILLIMETER)  # type: ignore[arg-type]
    if abs(template_area - project_area) > Decimal("0.001"):
        raise Rx38EngineeringConflictError(
            f"Profile area conflicts with template: {project_area} mm² vs {template_area} mm²"
        )


def _single_effective_value(
    parameters: EffectiveLengthParameters,
) -> tuple[Quantity | None, Decimal | None]:
    lengths = [value for value in (parameters.buckling_length_x, parameters.buckling_length_y) if value is not None]
    factors = [value for value in (parameters.factor_x, parameters.factor_y) if value is not None]
    if len(lengths) == 2 and lengths[0].si_value != lengths[1].si_value:
        raise Rx38EngineeringConflictError(
            "RX38 has one confirmed effective-length field but ProjectElement has different x/y lengths; governing axis requires engineer input"
        )
    if len(factors) == 2 and factors[0] != factors[1]:
        raise Rx38EngineeringConflictError(
            "RX38 has one confirmed effective-length factor but ProjectElement has different x/y factors; governing axis requires engineer input"
        )
    return (lengths[0] if lengths else None, factors[0] if factors else None)


def project_element_to_rx38_record(
    element: ProjectElement,
    template: Rx38Record,
    *,
    safety_context: Rx3SafetyContext | None = None,
    derived_field_policy: DerivedFieldWritePolicy = (
        DerivedFieldWritePolicy.PRESERVE_DERIVED_IF_INPUTS_UNCHANGED
    ),
) -> tuple[Rx38Record, tuple[str, ...]]:
    record, warnings, _ = _prepare_rx38_record(
        element,
        template,
        safety_context=safety_context,
        derived_field_policy=derived_field_policy,
    )
    return record, warnings


def _prepare_rx38_record(
    element: ProjectElement,
    template: Rx38Record,
    *,
    safety_context: Rx3SafetyContext | None,
    derived_field_policy: DerivedFieldWritePolicy,
) -> tuple[Rx38Record, tuple[str, ...], dict[str, Any]]:
    context = safety_context or Rx3SafetyContext.draft()
    element.require_fields(
        "mark", "section_type", "profile_standard", "profile_name", "area",
        "heated_perimeter", "ptm", "length", "quantity", "steel_grade", "E",
        "density", "N", "stress_state", "support_condition",
        "effective_length_parameters", "required_fire_resistance",
    )
    _require_template_compatibility(element, template)
    if context.mode in {ExecutionMode.VALIDATION, ExecutionMode.PRODUCTION} and not _same_text(
        element.stress_state or "", template.fields[45]
    ):
        raise Rx38TemplateMismatchError(
            "Stress state differs from template while its paired RX38 code is unconfirmed"
        )
    if context.mode in {ExecutionMode.VALIDATION, ExecutionMode.PRODUCTION} and not _same_text(
        element.support_condition or "", template.fields[48]
    ):
        raise Rx38TemplateMismatchError(
            "Support condition differs from template while its paired RX38 code is unconfirmed"
        )
    rx3_input, _, action_warnings = build_rx3_input(element, context)
    if context.mode in {ExecutionMode.VALIDATION, ExecutionMode.PRODUCTION}:
        evidence = context.template_evidence
        if (
            evidence is None
            or evidence.template_record_sha256 is None
            or evidence.template_record_sha256 != rx38_record_fingerprint(template)
        ):
            raise TemplateProfileError(
                "AXIAL_ONLY evidence is not bound to the exact RX38 template record"
            )
    heating_evidence = context.heating_exposure
    heating_verification = HeatingExposureVerification.evaluate(
        heating_evidence, element, template
    )
    heating_verified = heating_verification.verified
    if (
        context.mode in {ExecutionMode.VALIDATION, ExecutionMode.PRODUCTION}
        and not heating_verified
    ):
        raise HeatingExposureError(
            "HEATING_EXPOSURE_UNVERIFIED: VALIDATION/PRODUCTION requires typed "
            "evidence binding ProjectElement.heating_sides to the exact RX38 "
            "template record"
        )
    steel_report: SteelCompatibilityReport = evaluate_steel_compatibility(
        element,
        template,
        context.steel_properties,
    )
    if context.mode is ExecutionMode.PRODUCTION and not steel_report.verified:
        raise SteelCompatibilityError(
            "Production requires a verified RX3 steel-strength mapping; legacy numeric equality is insufficient"
        )

    template_mx = Decimal(template.fields[50].replace(",", ".") or "0")
    mx_tolerance = context.action_zero_tolerance.moment.to(
        Unit.KILONEWTON_METER
    ).value
    if abs(template_mx) > mx_tolerance:
        raise TemplateProfileError(
            "AXIAL_ONLY template conflicts with non-zero RX38 field 50; "
            "the value cannot be cleared through the safe typed API"
        )

    area_mm2 = _as_decimal(element.area, Unit.SQUARE_MILLIMETER)  # type: ignore[arg-type]
    perimeter_mm = _as_decimal(element.heated_perimeter, Unit.MILLIMETER)  # type: ignore[arg-type]
    ptm_mm = _as_decimal(element.ptm, Unit.MILLIMETER)  # type: ignore[arg-type]
    length_m = _as_decimal(element.length, Unit.METER)  # type: ignore[arg-type]
    quantity = Decimal(element.quantity)  # type: ignore[arg-type]
    calculated_ptm = area_mm2 / perimeter_mm
    if abs(calculated_ptm - ptm_mm) > max(Decimal("0.001"), calculated_ptm * Decimal("0.0002")):
        raise Rx38EngineeringConflictError(
            f"PTM conflict: ProjectElement={ptm_mm} mm, A/P={calculated_ptm} mm"
        )
    protected_one = perimeter_mm * length_m / Decimal(1000)
    protected_total = protected_one * quantity
    if element.protected_area is not None:
        project_protected = _as_decimal(element.protected_area, Unit.SQUARE_METER)
        if abs(project_protected - protected_total) > Decimal("0.001"):
            raise Rx38EngineeringConflictError(
                f"Protected-area conflict: ProjectElement={project_protected} m², geometry={protected_total} m²"
            )

    parameters = element.effective_length_parameters
    if parameters is None:
        raise Rx38EngineeringConflictError(
            "RX38 creation requires explicit effective-length parameters"
        )
    effective_length, effective_factor = _single_effective_value(parameters)
    if effective_length is None or effective_factor is None:
        raise Rx38EngineeringConflictError(
            "RX38 creation requires one explicit effective length and factor"
        )

    values: dict[int, str] = {
        1: element.mark,
        3: element.mark,
        5: element.section_type or "",
        14: _format_decimal(length_m),
        15: str(element.quantity),
        17: element.profile_standard or "",
        19: element.profile_name or "",
        20: _format_decimal(area_mm2),
        21: _format_decimal(perimeter_mm),
        22: _format_decimal(calculated_ptm),
        23: _format_decimal(Decimal(1000) * perimeter_mm / area_mm2),
        24: _format_decimal(protected_one),
        25: _format_decimal(protected_total),
        32: _format_decimal(_as_decimal(element.density, Unit.KILOGRAM_PER_CUBIC_METER)),  # type: ignore[arg-type]
        34: _format_decimal(_as_decimal(element.E, Unit.MEGAPASCAL)),  # type: ignore[arg-type]
        42: element.steel_grade or "",
        45: element.stress_state or "",
        48: element.support_condition or "",
        49: _format_decimal_preserving_scale(
            _as_decimal(rx3_input.axial_force, Unit.KILONEWTON)
        ),
        51: _format_decimal(_as_decimal(effective_length, Unit.METER)),
        55: _format_decimal(_as_decimal(element.required_fire_resistance, Unit.MINUTE)),  # type: ignore[arg-type]
        66: _format_decimal(area_mm2 * Decimal("0.000001") * length_m * _as_decimal(element.density, Unit.KILOGRAM_PER_CUBIC_METER)),  # type: ignore[arg-type]
        67: _format_decimal(area_mm2 * Decimal("0.000001") * length_m * quantity * _as_decimal(element.density, Unit.KILOGRAM_PER_CUBIC_METER)),  # type: ignore[arg-type]
        141: _format_decimal(effective_factor),
    }
    values.update(steel_report.write_values)
    _apply_derived_field_policy(template, values, derived_field_policy)

    warnings: list[str] = list(action_warnings) + list(steel_report.warnings)
    warnings.append(
        "Mx production writing and My/Qx/Qy mappings remain unverified; only a verified AXIAL_ONLY profile with zero actions may proceed"
    )
    if element.heating_sides is not None and not heating_verified:
        warnings.append(
            "HEATING_EXPOSURE_UNVERIFIED: heating_sides has no confirmed RX38 "
            "index; heated_perimeter was written and template heating flags were "
            "preserved for DRAFT inspection only"
        )
    for name in (
        "material_id", "coating_type", "required_thickness",
        "specific_consumption", "total_consumption",
    ):
        if getattr(element, name) is not None:
            warnings.append(f"{name} preserved from template: no safe confirmed RX38 mapping")
    stale_indices = tuple(
        index for index in (44, 54) if template.fields[index].strip()
    )
    if stale_indices:
        warnings.append(
            "STALE_TEMPLATE_RESULT: RX3 output fields remain physically present but are excluded from Rx3Input and cannot be accepted before a proven recalculation"
        )

    updated = template
    for index, value in values.items():
        if field_spec(index).write_policy is WritePolicy.SAFE_DIRECT:
            updated = updated.with_typed_field(index, value)
        else:
            updated = _with_compatibility_checked_field(updated, index, value)
    profile = evaluate_calculation_profile(
        updated,
        values,
        context.template_evidence,
        evidence_template=template,
        steel_compatibility=steel_report,
    )
    if context.mode.value in {"VALIDATION", "PRODUCTION"} and not profile.verified:
        raise TemplateProfileError(
            "RX3 calculation profile is not verified for VALIDATION/PRODUCTION"
        )
    safety = {
        "mode": context.mode.value,
        "rx3_input": rx3_input.as_dict(),
        "steel_compatibility": steel_report.as_dict(),
        "template_profile": profile.as_dict(),
        "heating_exposure": heating_verification.as_dict(),
        "stale_template_result_indices": list(stale_indices),
        "_steel_production_evidence": steel_report,
        "_template_production_evidence": profile,
        "_heating_production_evidence": heating_verification,
    }
    return updated, tuple(warnings), safety


def project_element_to_rx38_construction(
    element: ProjectElement,
    template: Rx38Record,
    *,
    safety_context: Rx3SafetyContext | None = None,
    derived_field_policy: DerivedFieldWritePolicy = (
        DerivedFieldWritePolicy.PRESERVE_DERIVED_IF_INPUTS_UNCHANGED
    ),
) -> Rx38Construction:
    record, _ = project_element_to_rx38_record(
        element,
        template,
        safety_context=safety_context,
        derived_field_policy=derived_field_policy,
    )
    return Rx38Construction.from_record(record)


def create_rx38_from_project_element(
    element: ProjectElement,
    template_path: str | Path,
    output_path: str | Path,
    *,
    template_mark: str | None = None,
    safety_context: Rx3SafetyContext | None = None,
    derived_field_policy: DerivedFieldWritePolicy = (
        DerivedFieldWritePolicy.PRESERVE_DERIVED_IF_INPUTS_UNCHANGED
    ),
) -> Rx38CreationReport:
    template_path = Path(template_path).resolve(strict=True)
    output_path = Path(output_path).resolve(strict=False)
    if template_path == output_path:
        raise Rx38ProjectAdapterError(
            "Template and output RX38 must be different files"
        )
    if output_path.exists():
        raise Rx38ProjectAdapterError(
            f"Output RX38 already exists; refusing to overwrite it: {output_path}"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = read_rx38_document(template_path)
    template_record_sha256 = (
        safety_context.template_evidence.template_record_sha256
        if safety_context is not None and safety_context.template_evidence is not None
        else None
    )
    record_index, template = _select_template_record(
        document,
        element,
        template_mark,
        template_record_sha256,
    )
    updated, warnings, safety = _prepare_rx38_record(
        element,
        template,
        safety_context=safety_context,
        derived_field_policy=derived_field_policy,
    )
    output_document = document.replace_record(record_index, updated)
    handle = tempfile.NamedTemporaryFile(
        prefix=f".{output_path.stem}.",
        suffix=output_path.suffix,
        dir=output_path.parent,
        delete=False,
    )
    temporary_path = Path(handle.name)
    handle.close()
    temporary_path.unlink()
    try:
        write_rx38(output_document, temporary_path)
        reparsed_document = read_rx38_document(temporary_path)
        reparsed = reparsed_document.records[record_index]
        if (
            reparsed.fields != updated.fields
            or len(reparsed.fields) != TCONSTR_FIELD_COUNT
        ):
            raise Rx38ProjectAdapterError("RX38 round-trip verification failed")
        if output_path.exists():
            raise Rx38ProjectAdapterError(
                f"Output RX38 appeared during creation; refusing to overwrite it: {output_path}"
            )
        try:
            install_file_no_overwrite(temporary_path, output_path)
        except ExclusiveInstallError as exc:
            raise Rx38ProjectAdapterError(
                f"Output RX38 appeared during creation; refusing to overwrite it: {output_path}"
            ) from exc
        temporary_path.unlink()
    finally:
        if temporary_path.exists():
            temporary_path.unlink()

    differences = diff_records(template, reparsed)
    changes = tuple(
        Rx38FieldChange(item.index, item.field_name, item.old_value, item.new_value)
        for item in differences
    )
    unknown_count = sum(
        field_spec(index).confidence == "unknown" for index in range(TCONSTR_FIELD_COUNT)
    )
    target_record_position = sum(
        record.record_type == "Tconstr"
        for record in document.records[: record_index + 1]
    )
    return Rx38CreationReport(
        template=str(template_path), output=str(output_path),
        template_mark=template.mark or "", output_mark=element.mark,
        target_record_position=target_record_position,
        template_record_sha256=rx38_record_fingerprint(template),
        output_record_sha256=rx38_record_fingerprint(reparsed),
        changed_fields=changes,
        preserved_fields_count=TCONSTR_FIELD_COUNT - len(changes),
        unknown_fields_count=unknown_count,
        warnings=warnings,
        round_trip_valid=True,
        safety_mode=safety["mode"],
        rx3_input=safety["rx3_input"],
        steel_compatibility=safety["steel_compatibility"],
        template_profile=safety["template_profile"],
        heating_exposure=safety["heating_exposure"],
        stale_template_result_indices=tuple(
            safety["stale_template_result_indices"]
        ),
        steel_production_evidence=safety["_steel_production_evidence"],
        template_production_evidence=safety["_template_production_evidence"],
        heating_production_evidence=safety["_heating_production_evidence"],
    )
