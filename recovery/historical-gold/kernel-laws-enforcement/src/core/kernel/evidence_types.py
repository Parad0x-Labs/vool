"""Law 2 — claims are typed: a live-world sentence renders only with checked evidence.

The incident this law exists to stop (2026-08-19 audit, live 0.5.0): the runtime shipped
"the Mercedes-Benz Golf is better" — a fabricated car fact — as flat assistant prose,
indistinguishable from an observed one, and a currency fast path answered one of four
requested rates while presenting the reply as complete and grounded. Both defects share
one root: nothing at the render boundary distinguished a sentence backed by a receipt
from a sentence the model made up. Style prompts ("cite your sources") do not close that
gap on local models; a type system enforced at render time does.

So every claim carries one of five evidence types, and the renderer refuses — as a type
error, not a style note — any claim whose type does not check out:

- ``timeless``    — true independent of the live world (arithmetic, definitions); no ref.
- ``observed``    — read from the live world THIS turn; ref names the receipt, and every
  number in the claim must literally appear in that receipt (the cheapest fabrication is
  a real receipt id glued to an invented figure).
- ``memory``      — recalled from the durable store; ref names the memory node.
- ``stipulated``  — assumed because the user stipulated it ("suppose the fee is 2%").
- ``unverified``  — model recall with no backing; renders, but only wearing its mark.

Validation is atomic across the whole answer: one bad claim refuses the render entirely,
because a partially-marked answer teaches the reader that unmarked sentences are safe —
the exact lie the audited turn told.

Number matching is by whole normalized token, not digit-substring containment: '1,420'
equals '1420' (thousands separators are formatting), but '1.75' must NOT match a receipt
containing only '175', and '75' must not match '1.75' — a decimal point changes the
quantity, so digit overlap is not evidence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "CLAIM_TYPES",
    "EvidenceTypeError",
    "TypedClaim",
    "render_typed_answer",
    "validate_claims",
    "_NUMBER_RE",
]

#: The closed set of evidence types. Closed on purpose: a new type of claim is a design
#: decision about what counts as evidence, never something a caller invents inline.
# "conversational" added 2026-08-20: a greeting or a question back to the user is not a
# claim about the world, and forcing it through evidence typing produced "yo" refused as
# a fabrication. Conversation renders bare; a ref on it is still an error.
CLAIM_TYPES: tuple[str, ...] = ("timeless", "observed", "memory", "stipulated", "unverified", "conversational")

#: Types that must NOT carry a ref — they have nothing to resolve, so a ref on them is
#: a category error (usually a claim mistyped away from `observed` to dodge validation).
_REFLESS_TYPES = frozenset({"timeless", "stipulated", "unverified", "conversational"})

# Thousands-grouped numbers first so '1,420.75' is one token, not three; then plain
# integers/decimals. Trailing sentence punctuation is never consumed ('1420,' -> '1420').
# The optional leading sign is part of the quantity: "shrank -5" asserting against a
# receipt that says "grew 5" was measured passing (finding D4) when the sign was
# outside the token. A signed claim number must find its signed form in the receipt.
# The sign is captured ONLY at a token start (finding D7, measured live 2026-08-20):
# a bare "-?" also fired mid-token, so the date "2026-08-19" tokenized as 2026, -08,
# -19, the range "0.0118-0.0129" grew a phantom negative, and scientific notation
# "7.33e-05" yielded a "-05" no receipt could contain — false refusals of true claims.
_NUMBER_RE = re.compile(
    r"(?<![0-9A-Za-z.])-\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![0-9A-Za-z.])-\d+(?:\.\d+)?"
    r"|(?<![0-9A-Za-z.])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![0-9A-Za-z.])\d+(?:\.\d+)?"
)
# BLOCK-C seam 5 (evidence lexer unification, t45 Neo4j class): the unsigned
# alternatives now carry the same lookbehind as the signed ones — a digit LED by
# a letter (or glued to a preceding digit/dot fragment) is part of an IDENTIFIER,
# never a quantity: Neo4j, PS5, x402, B12, v2.47 lex to NO numbers, on the claim
# side and the receipt side symmetrically. This both stops "Neo4j" prose being
# rejected as an unbound number AND stops an identifier's embedded digits from
# GROUNDING a fabricated standalone number. A digit-led token keeps its quantity
# ("24GB" still grounds a "24 GB" claim; "5pm" is still a 5).


class EvidenceTypeError(RuntimeError):
    """A claim failed evidence typing. Rendering it would ship an unbacked sentence."""

    def __init__(self, claim_text: str, reason: str) -> None:
        super().__init__(f"evidence type error: {reason} (claim: {claim_text!r})")
        self.claim_text = claim_text
        self.reason = reason


@dataclass(frozen=True)
class TypedClaim:
    """One sentence of an answer, carrying its evidence type and reference(s).

    ``ref`` is the single-source form; ``refs`` (2026-08-20) is the comparative form — a
    sentence like "the Passat tops 239 km/h vs the Aygo's 160" legitimately draws numbers
    from two receipts, and the live rounds proved refusing that shape refuses ordinary
    comparison questions. Every number must still ground in at least one CITED receipt;
    breadth of citation is visible in the rendered marker, so a many-receipt citation
    reads as exactly what it is.
    """

    text: str
    ctype: str
    ref: str = ""
    refs: tuple[str, ...] = ()

    def cited(self) -> tuple[str, ...]:
        return self.refs if self.refs else ((self.ref,) if self.ref else ())


_UNIT_SUFFIXES = frozenset((
    # BLOCK-C seam 5 furnace hardening: a digit-led token glued to letters is a
    # QUANTITY only when the letter tail is a unit ("24GB", "5pm", "90kmh") --
    # otherwise it is an identifier ("2FA", "3GPP"). Unit identities, not verbs.
    "gb", "mb", "kb", "tb", "pb", "ghz", "mhz", "khz", "hz", "pm", "am",
    "km", "kg", "kw", "kwh", "kmh", "mph", "mi", "lb", "lbs", "oz", "ml",
    "mg", "cm", "mm", "nm", "min", "mins", "ms", "hr", "hrs", "sec", "secs",
    "m", "s", "h", "d", "g", "l", "x", "k", "c", "f", "v", "w", "a"))


def _number_tokens(text: str) -> set[str]:
    """Whole number tokens with thousands separators normalized away ('1,420' -> '1420').

    Delegates to the canonical lexical span authority. The decimal point is kept:
    matching is exact-token equality downstream, so '1.75' and '175' stay distinct
    quantities instead of colliding as digit soup.
    """
    from core.kernel.lexical_spans import quantity_values
    return quantity_values(text)


def _check_one(claim: TypedClaim, receipts: dict[str, str], memory_nodes: dict[str, str]) -> None:
    if claim.ctype not in CLAIM_TYPES:
        raise EvidenceTypeError(
            claim.text, f"unknown claim type {claim.ctype!r}; expected one of {CLAIM_TYPES}"
        )
    if claim.ctype in _REFLESS_TYPES:
        if claim.ref or claim.refs:
            raise EvidenceTypeError(
                claim.text,
                f"a {claim.ctype} claim carries no reference, but ref {claim.ref!r} was supplied",
            )
        return
    if claim.ctype == "observed":
        cited = claim.cited()
        if not cited:
            raise EvidenceTypeError(
                claim.text,
                "observed claim has no receipt ref — an observation nothing recorded is a fabrication",
            )
        for one in cited:
            if one not in receipts:
                raise EvidenceTypeError(
                    claim.text, f"observed claim cites receipt {one!r}, which does not exist"
                )
        receipt_numbers: set[str] = set()
        for one in cited:
            receipt_numbers |= _number_tokens(receipts[one])
        for number in sorted(_number_tokens(claim.text)):
            if number not in receipt_numbers and not _is_rounding_of_any(number, receipt_numbers):
                raise EvidenceTypeError(
                    claim.text,
                    f"observed claim states the number {number!r}, "
                    f"but none of its cited receipts {list(cited)} contain it",
                )
        # Finding D11, measured live 2026-08-20: "the weather in Berlin is 65°C" cited a
        # receipt that said 65°F — the value grounded, the UNIT flipped, and the claim
        # asserted lethal heat with a straight citation. A temperature unit is part of
        # the quantity's identity exactly as the sign is (D4): the same number wearing
        # the OTHER scale in every cited receipt is a contradiction, not a grounding.
        receipt_blob = " ".join(receipts[one] for one in cited)
        for number, claim_unit in _unit_pairs(claim.text):
            # Kernel-computed receipts carry no units by construction — their unit
            # semantics live in the derive label, so adjacency applies to WORLD
            # receipts only (the D9 rounding pin caught the over-reach: "1.98 grams"
            # citing "computed locally: ... = 1.982228298" is honest).
            if any(receipts[one].startswith("computed locally:")
                   and (number in _number_tokens(receipts[one])
                        or _is_rounding_of_any(number, _number_tokens(receipts[one])))
                   for one in cited):
                world_units = {u for held, u in _unit_pairs(receipt_blob) if held == number}
                if world_units and claim_unit not in world_units:
                    # CONSENSUS-5 pending-tier sharpening (measured: a 460 km route
                    # distance shipped as "460 kWh" through the calc exemption): a
                    # number the WORLD receipts attach to a different unit cannot wear
                    # a computed costume to change units.
                    raise EvidenceTypeError(
                        claim.text,
                        f"claim states {number} {claim_unit}, but world receipts attach "
                        f"{'/'.join(sorted(world_units))} to {number} — a computed receipt "
                        "cannot relabel a world quantity's unit (D12)",
                    )
                continue
            receipt_pairs = _unit_pairs(receipt_blob)
            grounded_unit = any(
                unit == claim_unit and (held == number or _is_rounding_of_any(number, {held}))
                for held, unit in receipt_pairs
            )
            if not grounded_unit:
                raise EvidenceTypeError(
                    claim.text,
                    f"observed claim states {number} {claim_unit}, but no cited receipt "
                    f"attaches {claim_unit} to {number} — the unit is ungrounded (D12)",
                )
        for number, claim_unit in _temperature_units(claim.text):
            receipt_units = {u for n, u in _temperature_units(receipt_blob) if n == number}
            if receipt_units and claim_unit not in receipt_units:
                raise EvidenceTypeError(
                    claim.text,
                    f"observed claim states {number}°{claim_unit}, but its cited receipts "
                    f"give {number} only in °{'/'.join(sorted(receipt_units))} — the unit "
                    "contradicts the evidence",
                )
            if not receipt_units and not _degree_adjacent(receipt_blob, number):
                # D11-strict (consensus review-20260820-035058): the value must wear its
                # unit IN the evidence, not merely exist near it. Measured live: "a
                # temperature of 213490°C" — the digits were a station id; set-membership
                # grounded the number and the claim invented the scale. A bare degree
                # marker or "degrees" adjacent to the number in the receipt still counts:
                # absence of a stated scale is not evidence of either.
                raise EvidenceTypeError(
                    claim.text,
                    f"observed claim states {number}°{claim_unit}, but no cited receipt "
                    f"attaches degrees to {number} at all — the unit is ungrounded",
                )
        return
    # memory — the only remaining member of CLAIM_TYPES.
    if not claim.ref:
        raise EvidenceTypeError(
            claim.text, "memory claim has no node ref — a recall nothing stored is a fabrication"
        )
    if claim.ref not in memory_nodes:
        raise EvidenceTypeError(
            claim.text, f"memory claim cites node {claim.ref!r}, which does not exist"
        )


# Finding D12 (consensus-2, measured live across 7 turns of one fuzz run): grams
# shipped as mAh, inches as mAh, a model year as kW, a URL id as grams. The units
# below are STRUCTURAL FORMS, not a domain dictionary: the rule is string adjacency —
# a claim stating "X <unit>" requires a cited receipt to attach the same unit form
# to X itself. Conversions (kg vs g) go through derive; adjacency is never inferred.
_UNIT_GROUPS: tuple[tuple[str, ...], ...] = (
    # Consensus-3 fix 1: alias groups extended so TRUE claims stop dying on text
    # form ("70 w" was refused against a receipt saying "70 watt-hours" — the
    # filter killed truth and garbage alike). One canonical name per group; both
    # sides of every comparison normalize through this same table.
    ("mah", "milliamp-hour", "milliamp-hours"),
    ("kwh", "kilowatt-hour", "kilowatt-hours"),
    ("kw", "kilowatt", "kilowatts"),
    ("w", "watt", "watts", "wh", "watt-hour", "watt-hours"),
    ("hz",), ("ghz",),
    ("gb", "gigabyte", "gigabytes"), ("tb", "terabyte", "terabytes"),
    ("kg", "kilogram", "kilograms"), ("g", "gram", "grams"),
    ("miles", "mile", "mi"), ("km", "kilometer", "kilometers", "kilometre", "kilometres"),
    ("nits", "nit"),
    ("l", "liter", "liters", "litre", "litres"),
    ("inch", "inches", "in\""),
)
_UNIT_ALIAS = {alias: group[0] for group in _UNIT_GROUPS for alias in group}
_UNIT_RE = re.compile(
    r"(-?\d[\d,]*(?:\.\d+)?)[\s-]*(" + "|".join(sorted(_UNIT_ALIAS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


# The comma connective REQUIRES trailing whitespace: "1,420" is one thousands-
# grouped number, never a two-item list (measured immediately by the D4/thousands
# pins when this regex first landed without the space).
_UNIT_LIST_RE = re.compile(
    r"(-?\d[\d,]*(?:\.\d+)?)((?:\s*(?:,\s+|\band\b|\bor\b|\bagainst\b|\bvs\.?\s|\bto\b)\s*-?\d[\d,]*(?:\.\d+)?)+)[\s-]*("
    + "|".join(sorted(_UNIT_ALIAS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def _unit_pairs(text: str) -> set[tuple[str, str]]:
    """Every (number, canonical-unit) pair the text explicitly states — including
    units DISTRIBUTED over a connective-chained number list ("70 against 57
    watt-hours" states both in watt-hours; consensus-3 fix 1, measured: a true
    70-Wh claim was refused because only 57 sat adjacent to the unit). The chain
    must be numbers and connectives only — a word inside breaks distribution, so
    "wind 10 and temperature 25 degrees" never mislabels the 10.

    Round-020: regex matches whose number falls inside a URL or IDENTIFIER
    canonical lexical span are excluded — opaque bytes are not eligible for
    quantity semantics.
    """
    from core.kernel.lexical_spans import opaque_ranges

    _opaque = opaque_ranges(text)
    def _in_opaque(pos: int) -> bool:
        return any(a <= pos < b for a, b in _opaque)

    pairs = set()
    for m in _UNIT_RE.finditer(text):
        if _in_opaque(m.start()):
            continue
        pairs.add((m.group(1).replace(",", ""), _UNIT_ALIAS[m.group(2).lower()]))
    for m in _UNIT_LIST_RE.finditer(text):
        if _in_opaque(m.start()):
            continue
        unit = _UNIT_ALIAS[m.group(3).lower()]
        pairs.add((m.group(1).replace(",", ""), unit))
        for num in re.findall(r"-?\d[\d,]*(?:\.\d+)?", m.group(2)):
            pairs.add((num.replace(",", ""), unit))
    return pairs


_TEMP_UNIT_RE = re.compile(
    r"(-?\d+(?:\.\d+)?)\s*(?:°\s*|degrees?\s+)([CF])(?![a-z])|"
    r"(-?\d+(?:\.\d+)?)\s*degrees?\s+(celsius|fahrenheit)",
    re.IGNORECASE,
)


def _degree_adjacent(text: str, number: str) -> bool:
    """True when the receipt attaches ANY degree marker to this exact number.

    Round-020: degree adjacency inside URL/IDENTIFIER opaque spans is
    excluded — the marker is part of opaque bytes, not a temperature reading.
    """
    from core.kernel.lexical_spans import opaque_ranges

    _opaque = opaque_ranges(text)
    def _in_opaque(pos: int) -> bool:
        return any(a <= pos < b for a, b in _opaque)

    escaped = re.escape(number)
    for m in re.finditer(escaped + r"\s*(?:°|degrees?\b)", text, re.IGNORECASE):
        if not _in_opaque(m.start()):
            return True
    return False


def _temperature_units(text: str) -> set[tuple[str, str]]:
    """Every (number, C|F) pair the text explicitly states. Bare numbers and bare
    degree signs carry no unit and are never returned — absence of a stated unit is
    not evidence of either scale.

    Round-020: matches inside URL/IDENTIFIER opaque spans are excluded.
    """
    from core.kernel.lexical_spans import opaque_ranges

    _opaque = opaque_ranges(text)
    def _in_opaque(pos: int) -> bool:
        return any(a <= pos < b for a, b in _opaque)

    pairs: set[tuple[str, str]] = set()
    for m in _TEMP_UNIT_RE.finditer(text):
        if _in_opaque(m.start()):
            continue
        if m.group(1):
            pairs.add((m.group(1).replace(",", ""), m.group(2).upper()))
        else:
            pairs.add((m.group(3).replace(",", ""), "C" if m.group(4).lower() == "celsius" else "F"))
    return pairs


def _is_rounding_of_any(claim_number: str, receipt_numbers: set[str]) -> bool:
    """True when the claim's number is an honest rounding of a number the receipt holds.

    Finding D9, measured live 2026-08-20: a computation receipt held 1.982228298 and the
    synthesized "1.98" was refused — exact-token matching punished truthful precision
    reduction. Equivalence is deterministic: round the receipt's number to the claim
    number's own decimal places and compare. Nothing looser — 1.98 never matches 1.9,
    and a rounding may only DROP precision (a claim more precise than its receipt is
    still asserting digits the evidence does not hold).
    """
    try:
        claimed = float(claim_number)
    except ValueError:
        return False
    places = len(claim_number.split(".")[1]) if "." in claim_number else 0
    for raw in receipt_numbers:
        try:
            held = float(raw)
        except ValueError:
            continue
        held_places = len(raw.split(".")[1]) if "." in raw else 0
        if held_places <= places:
            continue  # equal precision is exact-match territory; more precise claims never round-match
        if round(held, places) == claimed:
            return True
    return False


def validate_claims(
    claims: list[TypedClaim],
    receipts: dict[str, str],
    memory_nodes: dict[str, str] | None = None,
) -> None:
    """Refuse (raise) on the first claim whose evidence type does not check out.

    Atomic by design: the caller gets no per-claim verdict list to partially honor —
    either the whole answer types, or nothing renders.
    """
    nodes = memory_nodes if memory_nodes is not None else {}
    for claim in claims:
        _check_one(claim, receipts, nodes)


def render_typed_answer(
    claims: list[TypedClaim],
    receipts: dict[str, str],
    memory_nodes: dict[str, str] | None = None,
) -> str:
    """Render the answer, each claim wearing its evidence marker. Validation comes first
    and is not optional — an invalid claim cannot render, so no unmarked or unbacked
    sentence can reach the user through this function."""
    validate_claims(claims, receipts, memory_nodes)
    lines: list[str] = []
    for claim in claims:
        if claim.ctype == "timeless":
            lines.append(claim.text)
        elif claim.ctype == "observed":
            cited = claim.cited()
            marker = cited[0] if len(cited) == 1 else "+".join(cited)
            lines.append(f"{claim.text} [receipt:{marker}]")
        elif claim.ctype == "memory":
            lines.append(f"{claim.text} [memory:{claim.ref}]")
        elif claim.ctype == "stipulated":
            lines.append(f"{claim.text} [stipulated]")
        elif claim.ctype == "conversational":
            lines.append(claim.text)
        else:  # unverified — validated above, so no other type can reach here.
            lines.append(f"{claim.text} [unverified - model memory]")
    return "\n".join(lines)
