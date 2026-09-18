"""Decimal arithmetic for a user-stipulated future currency peg.

The request supplies the fictional unit, its peg, and the purchase amount.  Search cannot improve
that evidence because the premise exists only inside the turn, so this closed shape is answered
without a planner, model, or retrieval call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from core.hypothetical_frame import SEARCH_MISMATCH_EXPLANATION, detect_hypothetical_frame

_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_CURRENCY_RE = re.compile(
    r"\b(?:currency\s+is(?:\s+now)?|uses)\s+(?:the\s+)?"
    r'["“](?P<name>[A-Za-z][A-Za-z -]{1,60})["”]'
    r"(?:\s*\((?P<code>[A-Z]{2,6})\))?",
    re.IGNORECASE,
)
_PEG_RE = re.compile(
    rf"\bpegged\s+at\s+1\s+(?P<unit>[A-Za-z][A-Za-z -]{{0,60}}|[A-Z]{{2,6}})\s*=\s*"
    rf"(?P<rate>{_NUMBER})\s+(?P<base>Euro|Euros|USD|EUR)\b",
    re.IGNORECASE,
)
_COST_RE = re.compile(
    rf"\b(?:buy|costs?)\b[^.!?\n]{{0,120}}?\b(?:for\s+|costs?\s+)"
    rf"(?P<cost>{_NUMBER})\s+(?P<base>Euro|Euros|USD|EUR)\b",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(r"\bhow\s+many\b[^?\n]{0,100}\b(?:spend|spent|cost)\b", re.I)
_SEARCH_LIMIT_RE = re.compile(
    r"\b(?:why|explain)\b[^.!?\n]{0,180}\b(?:web\s+)?search\b[^.!?\n]{0,100}\b(?:fail|inaccurate|answer)\b",
    re.I,
)


@dataclass(frozen=True)
class HypotheticalCurrencyCalculation:
    name: str
    code: str
    base: str
    rate: Decimal
    cost: Decimal

    @property
    def spent(self) -> Decimal:
        return self.cost / self.rate


def _decimal(value: str) -> Decimal | None:
    try:
        parsed = Decimal(value.replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed > 0 else None


def _number(value: Decimal) -> str:
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def parse_hypothetical_currency_calculation(
    user_text: str,
) -> HypotheticalCurrencyCalculation | None:
    text = str(user_text or "")
    if not detect_hypothetical_frame(text).supplies_premises:
        return None
    if not _QUESTION_RE.search(text) or not _SEARCH_LIMIT_RE.search(text):
        return None
    currency = _CURRENCY_RE.search(text)
    peg = _PEG_RE.search(text)
    cost = _COST_RE.search(text)
    if currency is None or peg is None or cost is None:
        return None
    peg_base = peg.group("base").upper().rstrip("S")
    cost_base = cost.group("base").upper().rstrip("S")
    if peg_base == "EURO":
        peg_base = "EUR"
    if cost_base == "EURO":
        cost_base = "EUR"
    if peg_base != cost_base:
        return None
    rate = _decimal(peg.group("rate"))
    amount = _decimal(cost.group("cost"))
    if rate is None or amount is None:
        return None
    name = " ".join(currency.group("name").split())
    code = str(currency.group("code") or "").upper()
    peg_unit = " ".join(peg.group("unit").split())
    if code and peg_unit.casefold() not in {code.casefold(), name.casefold()}:
        return None
    if not code and peg_unit.casefold() != name.casefold():
        return None
    return HypotheticalCurrencyCalculation(name, code, peg_base, rate, amount)


def hypothetical_currency_response(user_text: str) -> str | None:
    calculation = parse_hypothetical_currency_calculation(user_text)
    if calculation is None:
        return None
    unit = (
        f"{calculation.name} ({calculation.code})"
        if calculation.code
        else calculation.name
    )
    spent = calculation.spent
    return (
        f"{_number(calculation.cost)} {calculation.base} ÷ "
        f"({_number(calculation.rate)} {calculation.base} per {unit}) = "
        f"{_number(spent)} {unit} spent.\n"
        "This is a fictional, hypothetical premise supplied only in this prompt. "
        + SEARCH_MISMATCH_EXPLANATION
    )


__all__ = [
    "HypotheticalCurrencyCalculation",
    "hypothetical_currency_response",
    "parse_hypothetical_currency_calculation",
]
