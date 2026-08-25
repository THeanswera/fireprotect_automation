from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import os
from pathlib import Path
import tempfile
from typing import Any

from ..execution import ExecutionMode
from ..model import EffectiveLengthParameters, ProjectElement, Quantity, Unit
from .diff import diff_records
from .parser import (
    Rx38Construction,
    Rx38Document,
    Rx38Record,
    read_rx38_document,
    write_rx38,
)
from .profiles import normalize_profile_name, normalize_standard
from .schema import TCONSTR_FIELD_COUNT, field_spec
from .safety import (
    Rx3SafetyContext,
    SteelCompatibilityError,
    SteelCompatibilityReport,
    TemplateProfileError,
    build_rx3_input,
    evaluate_calculation_profile,
    evaluate_steel_compatibility,
)


class Rx38ProjectAdapterError(ValueError):
    pass


class Rx38TemplateMismatchError(Rx38ProjectAdapterError):
    pass


class Rx38EngineeringConflictError(Rx38ProjectAdapterError):
    pass


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
    changed_fields: tuple[Rx38FieldChange, ...]
    preserved_fields_count: int
    unknown_fields_count: int
    warnings: tuple[str, ...]
    round_trip_valid: bool
    safety_mode: str
    rx3_input: dict[str, Any]
    steel_compatibility: dict[str, Any]
    template_profile: dict[str, Any]
    stale_template_result_indices: tuple[int, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "template": self.template,
            "output": self.output,
            "template_mark": self.template_mark,
            "output_mark": self.output_mark,
            "changed_fields": [change.__dict__ for change in self.changed_fields],
            "preserved_fields_count": self.preserved_fields_count,
            "unknown_fields_count": self.unknown_fields_count,
            "warnings": list(self.warnings),
            "round_trip_valid": self.round_trip_valid,
            "safety_mode": self.safety_mode,
            "rx3_input": self.rx3_input,
            "steel_compatibility": self.steel_compatibility,
            "template_profile": self.template_profile,
            "stale_template_result_indices": list(
                self.stale_template_result_indices
            ),
        }


def _format_decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return (text or "0").replace(".", ",")


def _as_decimal(quantity: Quantity, unit: Unit) -> Decimal:
    return quantity.to(unit).value


def _same_text(left: str, right: str) -> bool:
    return " ".join(left.casefold().split()) == " ".join(right.casefold().split())


def _select_template_record(
    document: Rx38Document, element: ProjectElement, template_mark: str | None
) -> tuple[int, Rx38Record]:
    indexed = [(index, record) for index, record in enumerate(document.records) if record.record_type == "Tconstr"]
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
) -> tuple[Rx38Record, tuple[str, ...]]:
    record, warnings, _ = _prepare_rx38_record(
        element,
        template,
        safety_context=safety_context,
    )
    return record, warnings


def _prepare_rx38_record(
    element: ProjectElement,
    template: Rx38Record,
    *,
    safety_context: Rx3SafetyContext | None,
) -> tuple[Rx38Record, tuple[str, ...], dict[str, Any]]:
    context = safety_context or Rx3SafetyContext.draft()
    element.require_fields(
        "mark", "section_type", "profile_standard", "profile_name", "area",
        "heated_perimeter", "ptm", "length", "quantity", "steel_grade", "E",
        "density", "N", "stress_state", "support_condition",
        "effective_length_parameters", "required_fire_resistance",
    )
    _require_template_compatibility(element, template)
    rx3_input, _, action_warnings = build_rx3_input(element, context)
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
            "AXIAL_ONLY template conflicts with non-zero probable RX38 field 50; "
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
        22: _format_decimal(ptm_mm),
        23: _format_decimal(Decimal(1000) / ptm_mm),
        24: _format_decimal(protected_one),
        25: _format_decimal(protected_total),
        32: _format_decimal(_as_decimal(element.density, Unit.KILOGRAM_PER_CUBIC_METER)),  # type: ignore[arg-type]
        34: _format_decimal(_as_decimal(element.E, Unit.MEGAPASCAL)),  # type: ignore[arg-type]
        42: element.steel_grade or "",
        45: element.stress_state or "",
        48: element.support_condition or "",
        49: _format_decimal(_as_decimal(rx3_input.axial_force, Unit.KILONEWTON)),
        51: _format_decimal(_as_decimal(effective_length, Unit.METER)),
        55: _format_decimal(_as_decimal(element.required_fire_resistance, Unit.MINUTE)),  # type: ignore[arg-type]
        66: _format_decimal(area_mm2 * Decimal("0.000001") * length_m * _as_decimal(element.density, Unit.KILOGRAM_PER_CUBIC_METER)),  # type: ignore[arg-type]
        67: _format_decimal(area_mm2 * Decimal("0.000001") * length_m * quantity * _as_decimal(element.density, Unit.KILOGRAM_PER_CUBIC_METER)),  # type: ignore[arg-type]
        141: _format_decimal(effective_factor),
    }
    values.update(steel_report.write_values)

    warnings: list[str] = list(action_warnings) + list(steel_report.warnings)
    warnings.append(
        "Mx/My/Qx/Qy mappings remain unconfirmed; only a verified AXIAL_ONLY profile with zero actions may proceed"
    )
    if element.heating_sides is not None:
        warnings.append(
            "heating_sides has no confirmed RX38 index; heated_perimeter was written, template heating flags were preserved"
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
        updated = updated.with_typed_field(
            index,
            value,
            compatibility_verified=True,
        )
    profile = evaluate_calculation_profile(
        updated,
        values,
        context.template_evidence,
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
        "stale_template_result_indices": list(stale_indices),
    }
    return updated, tuple(warnings), safety


def project_element_to_rx38_construction(
    element: ProjectElement,
    template: Rx38Record,
    *,
    safety_context: Rx3SafetyContext | None = None,
) -> Rx38Construction:
    record, _ = project_element_to_rx38_record(
        element,
        template,
        safety_context=safety_context,
    )
    return Rx38Construction.from_record(record)


def create_rx38_from_project_element(
    element: ProjectElement,
    template_path: str | Path,
    output_path: str | Path,
    *,
    template_mark: str | None = None,
    safety_context: Rx3SafetyContext | None = None,
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
    record_index, template = _select_template_record(document, element, template_mark)
    updated, warnings, safety = _prepare_rx38_record(
        element,
        template,
        safety_context=safety_context,
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
        os.replace(temporary_path, output_path)
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
    return Rx38CreationReport(
        template=str(template_path), output=str(output_path),
        template_mark=template.mark or "", output_mark=element.mark,
        changed_fields=changes,
        preserved_fields_count=TCONSTR_FIELD_COUNT - len(changes),
        unknown_fields_count=unknown_count,
        warnings=warnings,
        round_trip_valid=True,
        safety_mode=safety["mode"],
        rx3_input=safety["rx3_input"],
        steel_compatibility=safety["steel_compatibility"],
        template_profile=safety["template_profile"],
        stale_template_result_indices=tuple(
            safety["stale_template_result_indices"]
        ),
    )
