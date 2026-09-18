from __future__ import annotations

try:
    from enum import StrEnum
except ImportError:  # pragma: no cover - exercised by the Python 3.10 CI row
    from enum import Enum

    class StrEnum(str, Enum):
        """Python 3.10-compatible subset of enum.StrEnum."""

        def __str__(self) -> str:
            return str(self.value)


__all__ = ["StrEnum"]
