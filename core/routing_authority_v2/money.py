"""Checked, fixed-point pico-USD money for routing authority V2."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, ClassVar

PICOS_PER_USD = 10**12
MAX_PICO_USD = (2**128) - 1
MAX_SOURCE_FRACTION_DIGITS = 18
_FIXED_DECIMAL_RE = re.compile(r"(?:0|[1-9][0-9]*)(?:\.([0-9]+))?\Z")


def _coefficient_from_digits(digits: tuple[int, ...]) -> int:
    coefficient = 0
    for digit in digits:
        coefficient = (coefficient * 10) + digit
    return coefficient


def _picos_from_decimal_parts(*, coefficient: int, exponent: int) -> int:
    """Scale exact decimal parts without consulting the process Decimal context."""

    if coefficient == 0:
        return 0
    pico_exponent = exponent + 12
    if pico_exponent >= 0:
        return coefficient * (10**pico_exponent)
    divisor = 10 ** (-pico_exponent)
    return (coefficient + divisor - 1) // divisor


class PicoUSDValidationError(ValueError):
    """A decimal source is not valid canonical USD authority."""


@dataclass(frozen=True, slots=True, order=True)
class PicoUSD:
    """Unsigned checked 128-bit conceptual pico-USD value.

    Decimal source strings use fixed notation only.  Up to 18 fractional digits are
    accepted so positive sub-pico amounts remain representable and round upward.
    """

    picos: int

    CURRENCY: ClassVar[str] = "USD"

    def __post_init__(self) -> None:
        if isinstance(self.picos, bool) or not isinstance(self.picos, int):
            raise PicoUSDValidationError("pico-USD storage must be an integer")
        if not 0 <= self.picos <= MAX_PICO_USD:
            raise PicoUSDValidationError("pico-USD is outside the unsigned 128-bit range")

    @classmethod
    def from_picos(cls, value: int) -> PicoUSD:
        return cls(value)

    @classmethod
    def parse(cls, source: Any, *, currency: str = "USD") -> PicoUSD:
        if currency != cls.CURRENCY:
            raise PicoUSDValidationError("only canonical USD currency is supported")
        if source is None:
            raise PicoUSDValidationError("money is required")
        if isinstance(source, (bool, float)):
            raise PicoUSDValidationError("bool and binary float money sources are forbidden")

        coefficient: int
        exponent: int
        if isinstance(source, int):
            if source < 0:
                raise PicoUSDValidationError("money cannot be negative")
            coefficient = source
            exponent = 0
        elif isinstance(source, str):
            if source.startswith("-"):
                if source.lstrip("-").strip("0.") == "":
                    raise PicoUSDValidationError("negative zero is forbidden")
                raise PicoUSDValidationError("money cannot be negative")
            match = _FIXED_DECIMAL_RE.fullmatch(source)
            if match is None:
                raise PicoUSDValidationError("money must use plain fixed decimal notation")
            fraction = match.group(1) or ""
            if len(fraction) > MAX_SOURCE_FRACTION_DIGITS:
                raise PicoUSDValidationError("money source has unsupported precision")
            integer = source.split(".", 1)[0]
            if len(integer) > len(str(MAX_PICO_USD // PICOS_PER_USD)):
                raise PicoUSDValidationError("pico-USD is outside the unsigned 128-bit range")
            coefficient = int(integer + fraction, 10)
            exponent = -len(fraction)
        elif isinstance(source, Decimal):
            if not source.is_finite():
                raise PicoUSDValidationError("NaN and Infinity money are forbidden")
            parts = source.as_tuple()
            if not isinstance(parts.exponent, int):
                raise PicoUSDValidationError("money has an unsupported exponent")
            if not any(parts.digits) and parts.sign:
                raise PicoUSDValidationError("negative zero is forbidden")
            if parts.sign:
                raise PicoUSDValidationError("money cannot be negative")
            exponent = parts.exponent
            if exponent > 0:
                raise PicoUSDValidationError("money has an unsupported exponent")
            if -exponent > MAX_SOURCE_FRACTION_DIGITS:
                raise PicoUSDValidationError("money source has unsupported precision")
            if max(1, len(parts.digits) + exponent) > len(str(MAX_PICO_USD // PICOS_PER_USD)):
                raise PicoUSDValidationError("pico-USD is outside the unsigned 128-bit range")
            coefficient = _coefficient_from_digits(parts.digits)
        else:
            raise PicoUSDValidationError("unsupported money source type")

        return cls(_picos_from_decimal_parts(coefficient=coefficient, exponent=exponent))

    def canonical_value(self) -> str:
        return str(self.picos)

    def __add__(self, other: object) -> PicoUSD:
        if not isinstance(other, PicoUSD):
            return NotImplemented
        return PicoUSD(self.picos + other.picos)

    def __sub__(self, other: object) -> PicoUSD:
        if not isinstance(other, PicoUSD):
            return NotImplemented
        return PicoUSD(self.picos - other.picos)

    def __mul__(self, multiplier: object) -> PicoUSD:
        if isinstance(multiplier, bool) or not isinstance(multiplier, int):
            return NotImplemented
        if multiplier < 0:
            raise PicoUSDValidationError("money multiplier cannot be negative")
        return PicoUSD(self.picos * multiplier)


__all__ = [
    "MAX_PICO_USD",
    "MAX_SOURCE_FRACTION_DIGITS",
    "PICOS_PER_USD",
    "PicoUSD",
    "PicoUSDValidationError",
]
