"""Exact money amounts for the wallet.

A human amount ("0.1") becomes an integer of atomic units (lamports, wei) without ever passing through
binary floating point, and an amount more precise than the asset can represent is refused, never
rounded. Every amount the wallet persists must also fit the signed 64-bit INTEGER columns of its store:
that is about 9.22 ETH or BNB in wei, a pilot ceiling stated as a typed refusal rather than a silent
precision loss (SQLite stores an over-large integer literal as REAL).
"""
from __future__ import annotations

import re
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal
from typing import Any

from core.wallet.security import wallet_fault

AUTHORITY = "core.wallet.amounts"

INT64_MAX = 2**63 - 1
#: No sign, no exponent, no thousands separators, no spaces inside: "0.1", "1", ".5", "12.000".
_DECIMAL_RE = re.compile(r"^(\d*)(?:\.(\d*))?$")
#: A float the model sent is accepted only when its shortest repr is an exact decimal the sender could
#: have meant; beyond 15 significant digits a binary double no longer round-trips a decimal literal.
_MAX_FLOAT_DIGITS = 15


def _refuse(reason: str, *, source_context: dict[str, Any] | None, **context: Any) -> Exception:
    return wallet_fault("wallet_amount_invalid", authority=AUTHORITY, context={"reason": reason, **context}, source_context=source_context)


def _text_of(value: Any, *, source_context: dict[str, Any] | None) -> str:
    if isinstance(value, bool) or value is None:
        raise _refuse("amount_missing_or_not_a_number", source_context=source_context)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        text = repr(value)
        if not all(ch.isdigit() or ch in ".-" for ch in text):
            raise _refuse("amount_not_a_plain_decimal", source_context=source_context)
        if len(text.replace("-", "").replace(".", "").lstrip("0")) > _MAX_FLOAT_DIGITS:
            raise _refuse("amount_float_precision_ambiguous", source_context=source_context, hint="send the amount as a decimal string")
        return text
    if isinstance(value, str):
        return value.strip()
    raise _refuse("amount_missing_or_not_a_number", source_context=source_context)


def parse_human_amount(value: Any, *, decimals: int, symbol: str = "", source_context: dict[str, Any] | None = None) -> int:
    """Atomic units for a human amount of an asset with ``decimals`` decimals. Refuses, never rounds."""
    text = _text_of(value, source_context=source_context)
    match = _DECIMAL_RE.match(text)
    if not text or match is None or not (match.group(1) or match.group(2)):
        raise _refuse("amount_not_a_plain_decimal", source_context=source_context, symbol=symbol[:12])
    whole, fraction = match.group(1) or "0", match.group(2) or ""
    places = int(decimals)
    if len(fraction) > places:
        if fraction[places:].strip("0"):
            raise _refuse("amount_precision_exceeds_asset", source_context=source_context, symbol=symbol[:12], decimals=places)
        fraction = fraction[:places]
    minor = int(whole) * 10**places + int(fraction.ljust(places, "0") or "0")
    if minor <= 0:
        raise _refuse("amount_must_be_positive", source_context=source_context, symbol=symbol[:12])
    if minor > INT64_MAX:
        raise _refuse("amount_exceeds_pilot_storage_ceiling", source_context=source_context, symbol=symbol[:12])
    return minor


def format_minor(minor: Any, decimals: int) -> str:
    """The exact human decimal for an atomic integer, without trailing zeros ("0.1", "25", "0.000005")."""
    value = int(minor)
    places = int(decimals)
    sign = "-" if value < 0 else ""
    digits = str(abs(value)).rjust(places + 1, "0")
    whole, fraction = (digits[:-places], digits[-places:]) if places else (digits, "")
    fraction = fraction.rstrip("0")
    return f"{sign}{whole}.{fraction}" if fraction else f"{sign}{whole}"


def fits_storage(*values: Any) -> bool:
    """Whether every value, and their sum, fits the wallet's signed 64-bit amount columns."""
    try:
        numbers = [int(v) for v in values]
    except (TypeError, ValueError):
        return False
    return all(0 <= n <= INT64_MAX for n in numbers) and sum(numbers) <= INT64_MAX


def decimal_of(minor: Any, decimals: int) -> Decimal:
    return Decimal(int(minor)).scaleb(-int(decimals))


# --- the one presentation owner for shortened money ------------------------------------------------------
#
# Exact values stay exact (`format_minor`): the requested transfer amount, everything in a Details section, every
# digest, reservation and signed byte. What a person reads at the top of a preview is a SHORTENED form of the same
# integer, computed here in decimal arithmetic and never in the page:
#   * an estimate rounds to a few significant digits and carries the approximation marker (`~0.0000926`);
#   * a maximum (a fee ceiling, a maximum debit) rounds OUTWARD (up), so the short form never understates the cap;
#   * a guaranteed minimum (the least balance left) rounds DOWN, so the short form never overstates it;
#   * a positive amount too small for the shortened places is never shown as zero: the exact value is shown instead.

ROUND_NEAREST = "nearest"
ROUND_UP = "up"
ROUND_DOWN = "down"
APPROX_MARK = "\u2248"
#: significant digits a shortened estimate keeps; enough to compare fees, few enough to read
SHORT_SIGNIFICANT = 3
#: the most decimal places a shortened form shows; below that the exact value is shown, never a zero
SHORT_MAX_PLACES = 8


def _rounding_for(mode: str) -> str:
    return {ROUND_UP: ROUND_CEILING, ROUND_DOWN: ROUND_FLOOR}.get(mode, ROUND_HALF_EVEN)


def _plain(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def shorten_minor(minor: Any, decimals: int, *, mode: str = ROUND_NEAREST, significant: int = SHORT_SIGNIFICANT, max_places: int = SHORT_MAX_PLACES) -> str:
    """The shortened decimal of an atomic integer: `significant` digits, at most `max_places` places, rounded per
    `mode` (nearest / up for maximums / down for minimums). Exact when it already fits; the exact value when it is
    smaller than the shortened places can show (so dust never reads as zero). Decimal arithmetic only."""
    value = decimal_of(minor, decimals)
    if value == 0:
        return "0"
    exact = _plain(value)
    magnitude = value.copy_abs()
    exponent = magnitude.adjusted()  # position of the leading significant digit
    places = max(0, min(int(max_places), int(significant) - 1 - exponent))
    if exponent < -int(max_places):
        return exact  # too small for the shortened form: show it exactly rather than as 0
    quantum = Decimal(1).scaleb(-places)
    rounded = value.quantize(quantum, rounding=_rounding_for(mode))
    return _plain(rounded)


def display_amount(minor: Any, decimals: int, symbol: str, *, mode: str = ROUND_NEAREST, exact: bool = False) -> str:
    """A short human line: the exact value when `exact` (or when shortening changes nothing), otherwise the
    shortened value with the marker that says how it was shortened (`~` estimate, `at most`, `at least`)."""
    full = format_minor(minor, decimals)
    if exact:
        return f"{full} {symbol}".strip()
    short = shorten_minor(minor, decimals, mode=mode)
    if short == full:
        return f"{full} {symbol}".strip()
    if mode == ROUND_UP:
        return f"at most {short} {symbol}".strip()
    if mode == ROUND_DOWN:
        return f"at least {short} {symbol}".strip()
    return f"{APPROX_MARK}{short} {symbol}".strip()


__all__ = [
    "APPROX_MARK", "INT64_MAX", "ROUND_DOWN", "ROUND_NEAREST", "ROUND_UP", "SHORT_MAX_PLACES", "SHORT_SIGNIFICANT",
    "decimal_of", "display_amount", "fits_storage", "format_minor", "parse_human_amount", "shorten_minor",
]
