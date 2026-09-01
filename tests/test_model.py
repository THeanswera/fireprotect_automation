from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from fireprotect.model import (
    Dimension,
    EffectiveLengthParameters,
    ModelValidationError,
    ProjectElement,
    ProvenanceType,
    Quantity,
    Unit,
    ValueProvenance,
)


def _source(field: str) -> ValueProvenance:
    return ValueProvenance(
        kind=ProvenanceType.SOURCE,
        file="forces.csv",
        sheet=None,
        row=3,
        field=field,
        document=None,
        clause=None,
        formula=None,
        date=None,
    )


def _element(**updates) -> ProjectElement:
    values = dict(
        project_id="P-1",
        element_id="17",
        mark="K1",
        element_type="column",
        source_file="forces.csv",
        source_type="LIRA_CSV",
        source_element_id="17",
        source_row=3,
        timestamp=datetime(2026, 8, 25, tzinfo=timezone.utc),
        section_type="I",
        profile_standard="GOST R 57837",
        profile_name="30K1",
        area=Quantity.of("46.52", Unit.SQUARE_CENTIMETER),
        full_perimeter=Quantity.of("1.20", Unit.METER),
        heated_perimeter=Quantity.of("0.90", Unit.METER),
        ptm=Quantity.of("5.169", Unit.MILLIMETER),
        length=Quantity.of("3.6", Unit.METER),
        quantity=2,
        steel_grade="C345",
        Ry=Quantity.of("325", Unit.MEGAPASCAL),
        E=Quantity.of("206", Unit.GIGAPASCAL),
        density=Quantity.of("7850", Unit.KILOGRAM_PER_CUBIC_METER),
        load_case="LC1",
        combination="C1",
        N=Quantity.of("-420", Unit.KILONEWTON),
        Mx=Quantity.of("12", Unit.KILONEWTON_METER),
        My=Quantity.of("0", Unit.KILONEWTON_METER),
        Qx=Quantity.of("3", Unit.KILONEWTON),
        Qy=Quantity.of("0", Unit.KILONEWTON),
        governing_combination="C1",
        required_fire_resistance=Quantity.of("90", Unit.MINUTE),
        stress_state="compression+bending",
        heating_sides=4,
        support_condition="pinned-pinned",
        effective_length_parameters=EffectiveLengthParameters(
            buckling_length_x=Quantity.of("3.6", Unit.METER),
            buckling_length_y=Quantity.of("3.6", Unit.METER),
            factor_x=Decimal("1"),
            factor_y=Decimal("1"),
        ),
        critical_temperature=Quantity.of("500", Unit.CELSIUS),
        unprotected_fire_resistance=Quantity.of("15", Unit.MINUTE),
        material_id="FP-1",
        coating_type="intumescent",
        required_thickness=Quantity.of("1.2", Unit.MILLIMETER),
        specific_consumption=Quantity.of(
            "1.8", Unit.KILOGRAM_PER_SQUARE_METER
        ),
        protected_area=Quantity.of("8.1", Unit.SQUARE_METER),
        total_consumption=Quantity.of("14.58", Unit.KILOGRAM),
    )
    explicit_provenance = updates.pop("provenance", None)
    values.update(updates)
    traced = {
        name: _source(name)
        for name, value in values.items()
        if name
        not in {
            "source_file",
            "source_type",
            "source_element_id",
            "source_row",
            "timestamp",
        }
        and value is not None
    }
    if explicit_provenance is not None:
        traced = explicit_provenance
    values["provenance"] = traced
    return ProjectElement(**values)


def test_unit_conversion_to_si_is_decimal_and_dimension_safe() -> None:
    force = Quantity.of("12.5", "kN")
    area = Quantity.of("46.52", "cm2")

    assert force.dimension is Dimension.FORCE
    assert force.as_si() == Quantity(Decimal("12500.0"), Unit.NEWTON)
    assert area.as_si() == Quantity(Decimal("0.004652"), Unit.SQUARE_METER)
    assert Quantity.of("500", Unit.CELSIUS).si_value == Decimal("773.15")
    assert Quantity.of("90", Unit.MINUTE).to(Unit.HOUR).value == Decimal("1.5")
    with pytest.raises(ValueError, match="Cannot convert"):
        force.to(Unit.METER)


def test_project_element_carries_all_groups_and_per_value_provenance() -> None:
    element = _element()

    assert element.area.si_value == Decimal("0.004652")
    assert element.trace_for("area").field == "area"
    assert element.trace_for("required_thickness").kind is ProvenanceType.SOURCE
    assert set(element.provenance).issuperset(
        {"mark", "area", "N", "required_fire_resistance", "material_id"}
    )
    with pytest.raises(TypeError):
        element.provenance["area"] = _source("other")  # type: ignore[index]


def test_bare_float_is_rejected_for_physical_quantity() -> None:
    with pytest.raises(TypeError, match="explicit unit"):
        _element(area=46.52)
    with pytest.raises(TypeError, match="binary float"):
        Quantity.of(46.52, Unit.SQUARE_CENTIMETER)  # type: ignore[arg-type]


def test_wrong_dimension_and_impossible_geometry_are_rejected() -> None:
    with pytest.raises(ModelValidationError, match="expects area"):
        _element(area=Quantity.of(2, Unit.METER))
    with pytest.raises(ModelValidationError, match="expects length"):
        _element(ptm=Quantity.of("193.47", Unit.PER_METER))
    with pytest.raises(ModelValidationError, match="cannot exceed"):
        _element(
            full_perimeter=Quantity.of("0.8", Unit.METER),
            heated_perimeter=Quantity.of("900", Unit.MILLIMETER),
        )


def test_populated_value_without_provenance_is_rejected() -> None:
    provenance = {
        name: _source(name)
        for name in ProjectElement.field_names()
        if name
        not in {
            "source_file",
            "source_type",
            "source_element_id",
            "source_row",
            "timestamp",
            "area",
        }
    }
    with pytest.raises(ModelValidationError, match="area"):
        _element(provenance=provenance)


def test_missing_engineering_input_never_gets_a_silent_default() -> None:
    element = _element(support_condition=None)

    with pytest.raises(ModelValidationError, match="support_condition"):
        element.require_fields("support_condition", "heating_sides")


def test_effective_length_factor_rejects_bare_float() -> None:
    with pytest.raises(TypeError, match="bare float"):
        EffectiveLengthParameters(
            buckling_length_x=None,
            buckling_length_y=None,
            factor_x=1.0,  # type: ignore[arg-type]
            factor_y=Decimal("1"),
        )


def test_provenance_kind_specific_validation() -> None:
    with pytest.raises(ValueError, match="requires formula"):
        ValueProvenance(kind=ProvenanceType.CALCULATED)
    with pytest.raises(ValueError, match="document and clause"):
        ValueProvenance(kind=ProvenanceType.NORMATIVE_TABLE, document="SP 16")
