from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .rx3.parser import Rx38Record


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    mark: str | None
    message: str


def validate_rx38_record(record: Rx38Record) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if record.record_type != "Tconstr":
        return issues
    if not record.mark:
        issues.append(ValidationIssue("ERROR", None, "Отсутствует марка элемента"))
    if not record.profile:
        issues.append(ValidationIssue("ERROR", record.mark, "Не указан профиль"))
    if record.area_mm2 is None or record.area_mm2 <= 0:
        issues.append(ValidationIssue("ERROR", record.mark, "Некорректная площадь сечения"))
    if record.perimeter_mm is None or record.perimeter_mm <= 0:
        issues.append(ValidationIssue("ERROR", record.mark, "Некорректный обогреваемый периметр"))
    if record.ptm_mm is not None and record.area_mm2 and record.perimeter_mm:
        expected = record.area_mm2 / record.perimeter_mm
        if abs(expected - record.ptm_mm) > max(
            Decimal("0.02"), expected * Decimal("0.002")
        ):
            issues.append(
                ValidationIssue(
                    "WARN", record.mark,
                    f"ПТМ в RX3 ({record.ptm_mm:.6g} мм) отличается от A/P ({expected:.6g} мм). "
                    "Проверьте схему и фактически обогреваемый периметр.",
                )
            )
    return issues
