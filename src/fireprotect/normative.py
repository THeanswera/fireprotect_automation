from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NormativeTrace:
    document_id: str
    edition: str
    clause: str
    formula_or_table: str | None
    description: str

    def __post_init__(self) -> None:
        for name in ("document_id", "edition", "clause", "description"):
            if not getattr(self, name).strip():
                raise ValueError(f"NormativeTrace.{name} must not be empty")


class NormativeInputError(ValueError):
    pass


def require_engineering_input(name: str, value):
    """Block silent substitution of an engineering-significant value."""
    if value is None or value == "":
        raise NormativeInputError(
            f"Не задано обязательное инженерное значение: {name}. "
            "Автоматическая подстановка запрещена."
        )
    return value
