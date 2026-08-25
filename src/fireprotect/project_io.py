from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping
import json

from .model import (
    EffectiveLengthParameters,
    ProjectElement,
    Quantity,
    ValueProvenance,
)


class ProjectDataError(ValueError):
    pass


def _quantity(value: object, field_name: str) -> Quantity | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"value", "unit"}:
        raise ProjectDataError(
            f"{field_name} must be an object with exactly 'value' and 'unit'"
        )
    try:
        magnitude = value["value"]
        if isinstance(magnitude, str):
            magnitude = magnitude.replace(",", ".")
        return Quantity.of(magnitude, value["unit"])
    except (TypeError, ValueError) as exc:
        raise ProjectDataError(f"Invalid quantity for {field_name}: {exc}") from exc


def _provenance(value: object, field_name: str) -> ValueProvenance:
    if not isinstance(value, Mapping):
        raise ProjectDataError(f"provenance.{field_name} must be an object")
    data = dict(value)
    if isinstance(data.get("date"), str):
        try:
            data["date"] = (
                datetime.fromisoformat(data["date"])
                if "T" in data["date"]
                else date.fromisoformat(data["date"])
            )
        except ValueError as exc:
            raise ProjectDataError(
                f"provenance.{field_name}.date must be ISO-8601"
            ) from exc
    try:
        return ValueProvenance(**data)
    except (TypeError, ValueError) as exc:
        raise ProjectDataError(f"Invalid provenance for {field_name}: {exc}") from exc


def project_element_from_dict(payload: Mapping[str, Any]) -> ProjectElement:
    if not isinstance(payload, Mapping):
        raise ProjectDataError("ProjectElement JSON root must be an object")
    data = dict(payload)
    known = set(ProjectElement.field_names()) | {"provenance"}
    unknown = set(data) - known
    if unknown:
        raise ProjectDataError(f"Unknown ProjectElement fields: {', '.join(sorted(unknown))}")
    missing = set(ProjectElement.field_names()) - set(data)
    if missing:
        raise ProjectDataError(
            "ProjectElement JSON must state every field explicitly; missing: "
            + ", ".join(sorted(missing))
        )

    values = {name: data[name] for name in ProjectElement.field_names()}
    for name in ProjectElement._QUANTITY_FIELDS:
        values[name] = _quantity(values[name], name)

    timestamp = values["timestamp"]
    if not isinstance(timestamp, str):
        raise ProjectDataError("timestamp must be an ISO-8601 string")
    try:
        values["timestamp"] = datetime.fromisoformat(timestamp)
    except ValueError as exc:
        raise ProjectDataError("timestamp must be an ISO-8601 string") from exc

    effective = values["effective_length_parameters"]
    if effective is not None:
        if not isinstance(effective, Mapping):
            raise ProjectDataError("effective_length_parameters must be an object or null")
        allowed = {"buckling_length_x", "buckling_length_y", "factor_x", "factor_y"}
        if set(effective) - allowed:
            raise ProjectDataError("Unknown effective_length_parameters fields")
        values["effective_length_parameters"] = EffectiveLengthParameters(
            buckling_length_x=_quantity(effective.get("buckling_length_x"), "buckling_length_x"),
            buckling_length_y=_quantity(effective.get("buckling_length_y"), "buckling_length_y"),
            factor_x=effective.get("factor_x"),
            factor_y=effective.get("factor_y"),
        )

    raw_provenance = data.get("provenance")
    if not isinstance(raw_provenance, Mapping):
        raise ProjectDataError("provenance must be an object")
    values["provenance"] = {
        name: _provenance(value, name) for name, value in raw_provenance.items()
    }
    try:
        return ProjectElement(**values)
    except (TypeError, ValueError) as exc:
        raise ProjectDataError(str(exc)) from exc


def read_project_element_json(path: str | Path) -> ProjectElement:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectDataError(f"Cannot read ProjectElement JSON {path}: {exc}") from exc
    return project_element_from_dict(payload)


def project_element_to_dict(element: ProjectElement) -> dict[str, Any]:
    if not isinstance(element, ProjectElement):
        raise TypeError("element must be ProjectElement")
    payload: dict[str, Any] = {}
    for name in ProjectElement.field_names():
        value = getattr(element, name)
        if isinstance(value, Quantity):
            payload[name] = {"value": str(value.value), "unit": value.unit.value}
        elif isinstance(value, EffectiveLengthParameters):
            payload[name] = {
                "buckling_length_x": None
                if value.buckling_length_x is None
                else {
                    "value": str(value.buckling_length_x.value),
                    "unit": value.buckling_length_x.unit.value,
                },
                "buckling_length_y": None
                if value.buckling_length_y is None
                else {
                    "value": str(value.buckling_length_y.value),
                    "unit": value.buckling_length_y.unit.value,
                },
                "factor_x": None if value.factor_x is None else str(value.factor_x),
                "factor_y": None if value.factor_y is None else str(value.factor_y),
            }
        elif isinstance(value, datetime):
            payload[name] = value.isoformat()
        else:
            payload[name] = value
    payload["provenance"] = {
        name: {
            "kind": trace.kind.value,
            "file": trace.file,
            "sheet": trace.sheet,
            "row": trace.row,
            "field": trace.field,
            "document": trace.document,
            "clause": trace.clause,
            "formula": trace.formula,
            "date": trace.date.isoformat() if trace.date is not None else None,
        }
        for name, trace in element.provenance.items()
    }
    return payload


def write_project_element_json(
    element: ProjectElement, path: str | Path, *, overwrite: bool = False
) -> Path:
    destination = Path(path).resolve(strict=False)
    if destination.exists() and not overwrite:
        raise ProjectDataError(f"ProjectElement JSON already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(project_element_to_dict(element), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return destination
