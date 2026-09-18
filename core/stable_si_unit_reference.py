"""Versioned local reference for explicit SI unit-symbol membership questions.

The finite authority owned here is the seven base-unit symbols and the 22 special-name derived
unit symbols in the BIPM SI Brochure.  Decimal-prefix composition is recognized mechanically;
arbitrary compound derived-unit expressions remain outside this small resolver.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SIReferenceMetadata:
    authority: str
    title: str
    edition: str
    version: str
    publication_year: int
    source_url: str


@dataclass(frozen=True)
class NamedSIUnit:
    symbol: str
    name: str
    kind: str


SI_REFERENCE = SIReferenceMetadata(
    authority="Bureau International des Poids et Mesures (BIPM)",
    title="The International System of Units (SI)",
    edition="9th edition",
    version="4.01",
    publication_year=2026,
    source_url="https://doi.org/10.59161/AUEZ1291",
)

_BASE_UNITS = (
    NamedSIUnit("s", "second", "base"),
    NamedSIUnit("m", "metre", "base"),
    NamedSIUnit("kg", "kilogram", "base"),
    NamedSIUnit("A", "ampere", "base"),
    NamedSIUnit("K", "kelvin", "base"),
    NamedSIUnit("mol", "mole", "base"),
    NamedSIUnit("cd", "candela", "base"),
)
_SPECIAL_NAME_DERIVED_UNITS = (
    NamedSIUnit("rad", "radian", "derived-special-name"),
    NamedSIUnit("sr", "steradian", "derived-special-name"),
    NamedSIUnit("Hz", "hertz", "derived-special-name"),
    NamedSIUnit("N", "newton", "derived-special-name"),
    NamedSIUnit("Pa", "pascal", "derived-special-name"),
    NamedSIUnit("J", "joule", "derived-special-name"),
    NamedSIUnit("W", "watt", "derived-special-name"),
    NamedSIUnit("C", "coulomb", "derived-special-name"),
    NamedSIUnit("V", "volt", "derived-special-name"),
    NamedSIUnit("F", "farad", "derived-special-name"),
    NamedSIUnit("Ω", "ohm", "derived-special-name"),
    NamedSIUnit("S", "siemens", "derived-special-name"),
    NamedSIUnit("Wb", "weber", "derived-special-name"),
    NamedSIUnit("T", "tesla", "derived-special-name"),
    NamedSIUnit("H", "henry", "derived-special-name"),
    NamedSIUnit("°C", "degree Celsius", "derived-special-name"),
    NamedSIUnit("lm", "lumen", "derived-special-name"),
    NamedSIUnit("lx", "lux", "derived-special-name"),
    NamedSIUnit("Bq", "becquerel", "derived-special-name"),
    NamedSIUnit("Gy", "gray", "derived-special-name"),
    NamedSIUnit("Sv", "sievert", "derived-special-name"),
    NamedSIUnit("kat", "katal", "derived-special-name"),
)

SI_NAMED_UNITS = {
    unit.symbol: unit for unit in (*_BASE_UNITS, *_SPECIAL_NAME_DERIVED_UNITS)
}

# Resolution 3 of the 27th CGPM (2022) completes the currently defined prefix range.  Longest
# symbols are checked first so ``da`` is not split as deci + ampere.
_SI_PREFIXES = (
    "da", "Q", "R", "Y", "Z", "E", "P", "T", "G", "M", "k", "h", "d", "c", "m",
    "µ", "n", "p", "f", "a", "z", "y", "r", "q",
)
_WHICH_OF_RE = re.compile(
    r"\bwhich\s+of\s+(?P<candidates>[^?\n]{1,240}?)\s+(?:is|are)\b",
    re.IGNORECASE,
)
_SI_SCOPE_RE = re.compile(r"\b(?:si|international\s+system\s+of\s+units)\b", re.IGNORECASE)
_SYMBOL_SCOPE_RE = re.compile(r"\b(?:unit\s+)?symbols?\b", re.IGNORECASE)
_CANDIDATE_SPLIT_RE = re.compile(r"\s*(?:,|;|\band\b|\bor\b)\s*", re.IGNORECASE)
_ATOMIC_SYMBOL_RE = re.compile(r"(?:[A-Za-zµμΩ]+|°C)\Z")


def _atomic_symbol_unit(symbol: str) -> NamedSIUnit | None:
    normalized = symbol.replace("μ", "µ")
    direct = SI_NAMED_UNITS.get(normalized)
    if direct is not None:
        return direct

    # The gram is the special base-unit exception: prefixed mass symbols are formed from ``g``,
    # not by prefixing ``kg``.  Both g itself and one-prefix forms such as mg are SI units.
    if normalized == "g":
        return NamedSIUnit("g", "gram", "prefixed-mass")
    for prefix in _SI_PREFIXES:
        if not normalized.startswith(prefix):
            continue
        remainder = normalized[len(prefix) :]
        if remainder == "g":
            return NamedSIUnit(normalized, f"{prefix}-prefixed gram", "prefixed-mass")
        unit = SI_NAMED_UNITS.get(remainder)
        if unit is not None and unit.symbol != "kg":
            return NamedSIUnit(normalized, f"prefixed {unit.name}", "prefixed")
    return None


def _join_items(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])}, and {items[-1]}"


def stable_si_unit_reference_response(user_text: str) -> str | None:
    """Classify bounded lists of atomic SI symbols from the versioned BIPM reference pack."""

    text = str(user_text or "").strip()
    match = _WHICH_OF_RE.search(text)
    if not match or not _SI_SCOPE_RE.search(text) or not _SYMBOL_SCOPE_RE.search(text):
        return None

    candidates: list[str] = []
    for raw in _CANDIDATE_SPLIT_RE.split(match.group("candidates")):
        candidate = raw.strip(" \t\"'‘’“”`")
        # An Oxford comma followed by ``and`` produces one empty split between the two
        # delimiters. It carries no candidate and is safe to ignore.
        if not candidate:
            continue
        if not _ATOMIC_SYMBOL_RE.fullmatch(candidate):
            return None
        if candidate not in candidates:
            candidates.append(candidate)
    if not 2 <= len(candidates) <= 20:
        return None

    admitted: list[str] = []
    rejected: list[str] = []
    for candidate in candidates:
        unit = _atomic_symbol_unit(candidate)
        if unit is None:
            rejected.append(candidate)
        else:
            admitted.append(f"{candidate} ({unit.name})")

    source = (
        f"the BIPM SI Brochure {SI_REFERENCE.edition}, version {SI_REFERENCE.version} "
        f"({SI_REFERENCE.publication_year})"
    )
    clauses: list[str] = []
    if admitted:
        clauses.append(f"Under {source}, {_join_items(admitted)} are recognized SI unit symbols.")
    if rejected:
        names = _join_items(rejected)
        clauses.append(
            f"The other candidates—{names}—do not match that SI symbol registry; "
            "they are not SI symbols."
        )
    return " ".join(clauses)


__all__ = [
    "SI_NAMED_UNITS",
    "SI_REFERENCE",
    "NamedSIUnit",
    "SIReferenceMetadata",
    "stable_si_unit_reference_response",
]
