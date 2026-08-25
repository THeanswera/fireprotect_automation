"""Exceptions raised by LIRA force-table adapters."""


class LiraImportError(ValueError):
    """Base error for invalid LIRA import configuration or data."""


class LiraMappingError(LiraImportError):
    """The caller-supplied column or unit mapping is invalid."""


class LiraFormatError(LiraImportError):
    """A source table cannot be interpreted as a rectangular table."""


class LiraRowError(LiraImportError):
    """A source row contains a missing or invalid value."""


class LiraDependencyError(LiraImportError):
    """An optional dependency required by a source adapter is unavailable."""
