"""What the whole message establishes, extracted once and shared with every node that asks for it.

The conductor's founding rule is that an adapter sees ONLY its own clause. That rule is right and
stays: it is what makes `location='price of bitcoin'` unreachable rather than merely unlikely. But
it was written for messages whose clauses are *independent* -- weather here, a price there -- and it
is exactly wrong for a message whose clauses are all about one story:

    "I exchanged 8,500 kr for $1,240, then spent $300 in Singapore and came home with 2,100 kr."
    Calculate how much money they effectively spent during the trip, both in kr and USD.

The second sentence is the clause. Every number is in the first. Handed its clause alone, the
calculation adapter reports "found nothing to act on", and measured on the shipped build all seven
clauses of that message failed closed for exactly this reason -- the plan was declined whole and a
model answered the message with none of its arithmetic done.

So the fix is not to widen what an adapter sees. It is to extract, ONCE and deterministically, the
facts the *message* establishes, and to hand that to the operations that declare they need it. Two
properties make that safe where handing over the raw text would not be:

* **It is typed, not textual.** A node receives numbered `NumericFact` values with their units, not
  a second copy of the message to run its recognizers over. A weather adapter given this could not
  turn any of it into a place name, and it does not receive it: sharing is opt-in per operation
  (`OperationSpec.wants_shared_context`), so every clause-scoped adapter stays clause-scoped.
* **It states what is NOT determined.** An ambiguous unit is recorded as ambiguous with its
  candidate list rather than resolved to the most likely one. "kr" is four currencies and "$" is
  six; a runtime that silently picks SEK and USD has invented the two facts the rest of the answer
  depends on. `missing_information()` turns those into the sentences the user asked for.

Nothing here is keyed to any particular message. The unit tables are the same class of asset as the
live-data lane's own alias tables, the benchmark rule is structural ("this clause judges a value
against a reference the message never supplies"), and no rule mentions travel, Singapore or
exchange. A message with no numbers in it produces an empty context and changes nothing.
"""
from __future__ import annotations

import ast
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# --- units ------------------------------------------------------------------------------------
#
# Two tables and no third. `_CANONICAL_UNIT` says what token a written unit IS; `_AMBIGUOUS_UNITS`
# says which real currencies that token could name. Keeping them apart is what lets "kronor",
# "krona" and "kr" collapse to one ambiguity rather than three, without the ambiguity table
# learning about spelling.

#: written form -> canonical unit token. Symbols and ISO codes included so one lookup serves all.
_CANONICAL_UNIT: dict[str, str] = {
    "$": "$", "dollar": "$", "dollars": "$", "buck": "$", "bucks": "$",
    "kr": "kr", "krona": "kr", "kronor": "kr", "krone": "kr", "kroner": "kr", "kronur": "kr",
    "£": "£", "pound": "£", "pounds": "£", "quid": "£",
    "¥": "¥", "yen": "¥", "yuan": "¥",
    "₹": "rs", "rs": "rs", "rupee": "rs", "rupees": "rs",
    "€": "EUR", "euro": "EUR", "euros": "EUR",
    "peso": "peso", "pesos": "peso",
    "franc": "franc", "francs": "franc",
    "dinar": "dinar", "dinars": "dinar",
    "%": "%", "percent": "%", "pct": "%",
}
for _iso in (
    "USD", "SGD", "CAD", "AUD", "NZD", "HKD", "SEK", "DKK", "NOK", "ISK", "EUR", "GBP", "JPY",
    "CNY", "CHF", "INR", "PKR", "LKR", "MXN", "PHP", "ARS", "BRL", "PLN", "CZK", "HUF", "ZAR",
):
    _CANONICAL_UNIT[_iso.lower()] = _iso

#: canonical unit token -> the real currencies it could name. A token absent from this table names
#: exactly one currency and is never reported as ambiguous.
_AMBIGUOUS_UNITS: dict[str, tuple[str, ...]] = {
    "$": ("AUD", "CAD", "HKD", "NZD", "SGD", "USD"),
    "kr": ("DKK", "ISK", "NOK", "SEK"),
    "£": ("EGP", "GBP", "LBP", "SYP"),
    "¥": ("CNY", "JPY"),
    "rs": ("INR", "LKR", "NPR", "PKR"),
    "peso": ("ARS", "CLP", "COP", "MXN", "PHP", "UYU"),
    "franc": ("CHF", "XAF", "XOF"),
    "dinar": ("BHD", "DZD", "IQD", "JOD", "KWD", "RSD", "TND"),
}

#: canonical unit token -> {marker found in the message: the currency it pins the token to}.
#: An ISO code or a nationality is a statement; a country merely being mentioned is not. "spent
#: $300 in Singapore" does NOT make the dollar Singaporean -- travellers carry their own money --
#: and resolving it there would invent the exchange the rest of the answer is computed from.
_UNIT_RESOLVERS: dict[str, dict[str, str]] = {
    "$": {
        "usd": "USD", "us dollar": "USD", "u.s. dollar": "USD", "american dollar": "USD",
        "sgd": "SGD", "singapore dollar": "SGD", "singaporean dollar": "SGD",
        "cad": "CAD", "canadian dollar": "CAD", "aud": "AUD", "australian dollar": "AUD",
        "nzd": "NZD", "new zealand dollar": "NZD", "hkd": "HKD", "hong kong dollar": "HKD",
    },
    "kr": {
        "sek": "SEK", "swedish": "SEK", "sweden": "SEK",
        "dkk": "DKK", "danish": "DKK", "denmark": "DKK",
        "nok": "NOK", "norwegian": "NOK", "norway": "NOK",
        "isk": "ISK", "icelandic": "ISK", "iceland": "ISK",
    },
    "£": {"gbp": "GBP", "british": "GBP", "sterling": "GBP", "egp": "EGP", "egyptian": "EGP"},
    "¥": {"jpy": "JPY", "japanese": "JPY", "cny": "CNY", "chinese": "CNY", "yuan": "CNY"},
    "rs": {"inr": "INR", "indian": "INR", "pkr": "PKR", "pakistani": "PKR", "lkr": "LKR"},
    "peso": {"mxn": "MXN", "mexican": "MXN", "php": "PHP", "philippine": "PHP", "ars": "ARS"},
    "franc": {"chf": "CHF", "swiss": "CHF", "xof": "XOF", "xaf": "XAF"},
    "dinar": {"kwd": "KWD", "kuwaiti": "KWD", "jod": "JOD", "jordanian": "JOD", "tnd": "TND"},
}

#: Every written unit form, longest first so "kronor" wins over "kr" in the alternation.
_UNIT_WORDS = sorted((w for w in _CANONICAL_UNIT if w.isalpha()), key=len, reverse=True)
_UNIT_SYMBOLS = [re.escape(s) for s in _CANONICAL_UNIT if not s.isalpha()]

_NUMBER_BODY = r"\d{1,3}(?:[,   ]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"

_FACT_RE = re.compile(
    r"(?P<prefix>" + "|".join(_UNIT_SYMBOLS) + r")?\s*"
    r"(?P<number>" + _NUMBER_BODY + r")"
    r"\s*(?P<suffix>" + "|".join(_UNIT_SYMBOLS + [rf"\b{w}\b" for w in _UNIT_WORDS]) + r")?",
    re.IGNORECASE,
)

# A closing quote or bracket after the stop is still the end of the sentence. Without this the
# quoted story and the request that follows it fuse into one "sentence", and every same-sentence
# test below silently widens to the whole message.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])[\"'”’»)\]]*\s+")


@dataclass(frozen=True)
class NumericFact:
    """One number the message states, with the unit it was written in.

    `label` is what an expression refers to. Stable within one extraction and derived from position,
    so a step that says ``fact_1 / fact_2`` names the same two numbers on every run.
    """

    label: str
    raw: str
    value: float
    unit: str
    unit_kind: str
    sentence: str
    position: int

    def describe(self) -> str:
        unit = f" {self.unit}" if self.unit and self.unit != "%" else ("%" if self.unit == "%" else "")
        return f"{self.label} = {self.value:g}{unit}  (written {self.raw!r})"


@dataclass(frozen=True)
class UnitAmbiguity:
    """A unit token that names more than one real thing, and whether the message pinned it."""

    token: str
    candidates: tuple[str, ...]
    resolved: str = ""
    why: str = ""

    @property
    def unresolved(self) -> bool:
        return not self.resolved


@dataclass(frozen=True)
class MissingInformation:
    """Something an answer would need that the message does not contain.

    `kind` is the structural rule that found it, so a test can assert WHICH rule fired rather than
    matching prose, and `statement` is the sentence a reader sees.
    """

    kind: str
    statement: str


@dataclass(frozen=True)
class SharedTurnContext:
    """Everything the message establishes, computed once per plan.

    Frozen and never mutated. A node that learns something new returns it in its own result; the
    scheduler forwards that to dependents as `derived_facts`, along declared edges only, so what a
    node can read stays a function of the graph rather than of scheduling order.
    """

    original_request: str = ""
    facts: tuple[NumericFact, ...] = ()
    ambiguities: tuple[UnitAmbiguity, ...] = ()
    missing: tuple[MissingInformation, ...] = ()

    @property
    def has_numbers(self) -> bool:
        return bool(self.facts)

    @property
    def unresolved_ambiguities(self) -> tuple[UnitAmbiguity, ...]:
        return tuple(a for a in self.ambiguities if a.unresolved)

    def fact_values(self) -> dict[str, float]:
        return {fact.label: fact.value for fact in self.facts}

    def to_dict(self) -> dict[str, Any]:
        return {
            "facts": [
                {
                    "label": f.label, "raw": f.raw, "value": f.value,
                    "unit": f.unit, "unit_kind": f.unit_kind,
                }
                for f in self.facts
            ],
            "ambiguities": [
                {"token": a.token, "candidates": list(a.candidates), "resolved": a.resolved}
                for a in self.ambiguities
            ],
            "missing": [{"kind": m.kind, "statement": m.statement} for m in self.missing],
        }


def _to_float(text: str) -> float | None:
    cleaned = re.sub(r"[,   ]", "", str(text or ""))
    try:
        return float(cleaned)
    except ValueError:
        return None


def _sentence_of(text: str, position: int) -> str:
    offset = 0
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        end = offset + len(sentence)
        if offset <= position <= end:
            return sentence.strip()
        offset = end + 1
    return text.strip()


def extract_numeric_facts(text: str) -> tuple[NumericFact, ...]:
    """Content numbers with their written units, excluding structural list labels.

    A number with no recognized unit is still a fact -- "spent 300 in Singapore" is a quantity the
    answer needs, and dropping it because its unit was not typed would lose exactly the information
    an ambiguity report exists to name.
    """
    from core.turn_ir import parse_turn_ir

    source = str(text or "")
    label_spans = tuple(
        (clause.start, clause.request_start)
        for clause in parse_turn_ir(source, response_shape_parser=None).clauses
        if clause.start < clause.request_start
    )
    facts: list[NumericFact] = []
    label_index = 0
    for match in _FACT_RE.finditer(source):
        position = match.start("number")
        while label_index < len(label_spans) and label_spans[label_index][1] <= position:
            label_index += 1
        if label_index < len(label_spans) and label_spans[label_index][0] <= position:
            continue
        value = _to_float(match.group("number"))
        if value is None:
            continue
        prefix = (match.group("prefix") or "").strip().lower()
        suffix = (match.group("suffix") or "").strip().lower()
        written = _CANONICAL_UNIT.get(prefix) or _CANONICAL_UNIT.get(suffix) or ""
        if written == "%":
            kind = "percent"
        elif written:
            kind = "currency"
        else:
            kind = "plain"
        facts.append(
            NumericFact(
                label=f"fact_{len(facts) + 1}",
                raw=match.group(0).strip(),
                value=value,
                unit=written,
                unit_kind=kind,
                sentence=_sentence_of(source, match.start()),
                position=match.start(),
            )
        )
    return tuple(facts)


def _location_pins(text: str) -> dict[str, tuple[str, str]]:
    """Units a BARE mention beside a place pins, keyed by this module's canonical unit token.

    Delegated to `core.currency_intent.location_bindings` so the rule lives in one place: a unit
    written with an amount attached ("$300 in Singapore") is money being spent somewhere and pins
    nothing, while a bare unit beside a place ("the kr is in Copenhagen") is the user saying which
    currency it is. The Singapore invariant this module was built around is that first case, and it
    is unchanged — see `test_the_dollar_is_ambiguous_until_the_message_names_which_dollar`.
    """
    from core.currency_intent import location_bindings

    pins: dict[str, tuple[str, str]] = {}
    for binding in location_bindings(text):
        if not binding.pins:
            continue
        token = _CANONICAL_UNIT.get(binding.unit)
        if token and binding.code in _AMBIGUOUS_UNITS.get(token, ()):
            pins.setdefault(token, (binding.code, binding.place))
    return pins


def detect_unit_ambiguities(text: str, facts: Sequence[NumericFact]) -> tuple[UnitAmbiguity, ...]:
    """Which units in `facts` name more than one currency, and whether the message pinned them."""
    lowered = " ".join(str(text or "").lower().split())
    pins = _location_pins(text)
    seen: list[str] = []
    out: list[UnitAmbiguity] = []
    for fact in facts:
        token = fact.unit
        if token not in _AMBIGUOUS_UNITS or token in seen:
            continue
        seen.append(token)
        candidates = _AMBIGUOUS_UNITS[token]
        pinned_code, pinned_place = pins.get(token, ("", ""))
        hits = sorted(
            {
                currency
                for marker, currency in _UNIT_RESOLVERS.get(token, {}).items()
                if re.search(rf"(?<![a-z]){re.escape(marker)}(?![a-z])", lowered)
            }
            | ({pinned_code} if pinned_code else set())
        )
        if len(hits) == 1:
            why = (
                f"the message places it in {pinned_place.title()}"
                if pinned_code == hits[0] and pinned_place
                else f"the message names {hits[0]}"
            )
            out.append(
                UnitAmbiguity(
                    token=token, candidates=candidates, resolved=hits[0], why=why,
                )
            )
        elif len(hits) > 1:
            # Two pins that disagree is LESS determined than none, not more. Reporting the first
            # would be picking, which is the behaviour this whole module exists to refuse.
            out.append(
                UnitAmbiguity(
                    token=token, candidates=candidates, resolved="",
                    why=f"the message names {' and '.join(hits)}, which do not agree",
                )
            )
        else:
            out.append(
                UnitAmbiguity(
                    token=token, candidates=candidates, resolved="",
                    why="the message names no ISO code or nationality that pins it",
                )
            )
    return tuple(out)


# --- structural missing-information rules --------------------------------------------------------
#
# Each rule answers "what would an answer need that the message does not contain", from the SHAPE of
# what was asked. None of them reads a topic.

_BENCHMARK_CUES = (
    "normal", "typical", "usual", "standard", "average", "market", "mid-market", "midmarket",
    "official", "going", "fair", "true", "proper", "real",
)
_BENCHMARK_NOUNS = ("rate", "rates", "price", "prices", "cost", "value", "fee", "fees", "spread")
_BENCHMARK_RE = re.compile(
    r"\b(?P<cue>" + "|".join(_BENCHMARK_CUES) + r")\b(?P<gap>(?:\s+\w+){0,3}?)\s+"
    r"\b(?P<noun>" + "|".join(_BENCHMARK_NOUNS) + r")\b",
    re.IGNORECASE,
)
_FEE_WORDS = ("fee", "fees", "spread", "commission", "markup", "mark-up", "charge", "charges")

#: A benchmark is SUPPLIED when a figure follows the phrase that names it -- "the normal rate was
#: 6.5 kr per dollar". Directional and immediate on purpose: "exchanged 8,500 kr for $1,240 on a day
#: when the normal rate applied" has a number four words away that belongs to the story, and any
#: proximity window wide enough to catch the real case counts that one too. The gap then vanishes
#: into the very facts it is a gap in, which is the failure this rule exists to prevent.
_BENCHMARK_SUPPLIED_RE = re.compile(
    r"^[\s,:—-]*(?:is|was|were|of|at|about|around|approximately|roughly|~|≈)?[\s,:]*"
    r"(?:[$€£¥₹]\s*)?\d",
    re.IGNORECASE,
)


def _benchmark_missing(text: str) -> list[MissingInformation]:
    """A request to judge a value against a reference the message never supplies.

    Structural: the message names "the <cue> <noun>" and never states one, so the benchmark is
    invoked but not given. What is missing is not only the number -- a rate without the date it
    applied on and the source it came from cannot be checked either, and saying so is the
    difference between naming a gap and gesturing at one.
    """
    out: list[MissingInformation] = []
    seen: set[str] = set()
    for match in _BENCHMARK_RE.finditer(str(text or "")):
        phrase = " ".join(match.group(0).split()).lower()
        if phrase in seen:
            continue
        if _BENCHMARK_SUPPLIED_RE.match(str(text or "")[match.end() : match.end() + 30]):
            continue
        seen.add(phrase)
        out.append(
            MissingInformation(
                kind="external_benchmark",
                statement=(
                    f"the {phrase} itself — the message states no value for it, no date it applied "
                    f"on, and no source it would be read from"
                ),
            )
        )
    return out


def _conversion_fee_missing(text: str, facts: Sequence[NumericFact]) -> list[MissingInformation]:
    """A stated conversion between two units, with nothing said about what it cost to make.

    Two different currency units inside one sentence is a conversion. Whether the figures are before
    or after the fee decides whether an implied rate is the rate that was offered or the rate net of
    a spread, and the two answer "was this a good exchange" differently.
    """
    lowered = " ".join(str(text or "").lower().split())
    if any(re.search(rf"\b{word}\b", lowered) for word in _FEE_WORDS):
        return []
    by_sentence: dict[str, set[str]] = {}
    for fact in facts:
        if fact.unit_kind != "currency":
            continue
        by_sentence.setdefault(fact.sentence, set()).add(fact.unit)
    for units in by_sentence.values():
        if len(units) < 2:
            continue
        pair = " and ".join(sorted(units))
        return [
            MissingInformation(
                kind="conversion_cost",
                statement=(
                    f"whether the {pair} figures are before or after conversion cost — the message "
                    f"names no fee, spread or commission on the exchange"
                ),
            )
        ]
    return []


def _ambiguity_missing(ambiguities: Sequence[UnitAmbiguity]) -> list[MissingInformation]:
    return [
        MissingInformation(
            kind="ambiguous_unit",
            statement=(
                f"which currency {ambiguity.token!r} means — it could be "
                f"{', '.join(ambiguity.candidates)}, and {ambiguity.why}"
            ),
        )
        for ambiguity in ambiguities
        if ambiguity.unresolved
    ]


def _entity_contradiction_missing(text: str) -> list[MissingInformation]:
    """Pairings an authority table says cannot hold — "Berlin in France", "Copenhagen NOK".

    Not a gap in the data: a gap in the PREMISE. A node handed one of these and left to smooth it
    over produces an answer whose every figure rests on a binding that is wrong, so the report says
    which side is authoritative and that neither may be assumed. `core/entity_consistency.py` holds
    the tables; nothing about currency is special here, and cars and cities go through the same
    check.
    """
    from core.entity_consistency import entity_consistency_report

    return [
        MissingInformation(
            kind="entity_contradiction",
            statement=(
                f"which half of “{item.written}” is right — {item.statement()} "
                f"Do not answer from either reading until it is settled."
            ),
        )
        for item in entity_consistency_report(text).contradictions
    ]


def extract_shared_context(text: str) -> SharedTurnContext:
    """The facts, ambiguities and gaps `text` establishes. Deterministic; consults no model."""
    original = str(text or "").strip()
    if not original:
        return SharedTurnContext()
    facts = extract_numeric_facts(original)
    ambiguities = detect_unit_ambiguities(original, facts)
    missing = (
        _entity_contradiction_missing(original)
        + _ambiguity_missing(ambiguities)
        + _benchmark_missing(original)
        + _conversion_fee_missing(original, facts)
    )
    return SharedTurnContext(
        original_request=original,
        facts=facts,
        ambiguities=ambiguities,
        missing=tuple(missing),
    )


# --- the briefing handed to a generation node ------------------------------------------------


def percentage_share_bindings(facts: Sequence[NumericFact]) -> dict[str, float]:
    """Derived, dimensionless shares; raw fact_N bindings retain stated magnitudes."""
    return {f"{fact.label}_share": fact.value / 100.0
            for fact in facts if fact.unit_kind == "percent"}


def render_briefing(
    context: SharedTurnContext,
    *,
    derived_facts: Mapping[str, Any] | None = None,
    clause: str = "",
) -> str:
    """What a node that reasons over the message is shown: the story, typed, plus what is not known.

    The original message is included verbatim and FIRST. A node answering "which countries use kr"
    needs the sentence it was asked in, not only the numbers -- the defect being repaired is a node
    reasoning with the story removed, and replacing the story with a fact table would repeat it in
    a tidier form.
    """
    lines: list[str] = ["The user's full message:", context.original_request.strip(), ""]
    if clause.strip():
        lines += ["The part of it you are answering:", clause.strip(), ""]
    if context.facts:
        lines.append("Numbers the message states (refer to these by label in any expression):")
        lines += [f"  {fact.describe()}" for fact in context.facts]
        shares = percentage_share_bindings(context.facts)
        if shares:
            lines.append("Percentage shares computed by the runtime (dimensionless multipliers):")
            lines += [f"  {name} = {value:g}" for name, value in shares.items()]
            lines.append("Raw fact_N is the stated magnitude; use fact_N_share as the fractional multiplier.")
        lines.append("")
    if derived_facts:
        lines.append("Values already computed by earlier steps of this same answer:")
        lines += [f"  {name} = {value}" for name, value in sorted(dict(derived_facts).items())]
        lines.append("")
    if context.ambiguities:
        lines.append("Units that are NOT determined by the message:")
        for ambiguity in context.ambiguities:
            if ambiguity.resolved:
                lines.append(f"  {ambiguity.token!r} = {ambiguity.resolved} ({ambiguity.why})")
            else:
                lines.append(
                    f"  {ambiguity.token!r} is ambiguous: {', '.join(ambiguity.candidates)} — "
                    f"{ambiguity.why}"
                )
        lines.append("")
    if context.missing:
        lines.append("Already established as NOT determinable from the message:")
        lines += [f"  - {item.statement}" for item in context.missing]
        lines.append("")
    return "\n".join(lines).strip()


# --- grounded arithmetic ------------------------------------------------------------------------
#
# The division of labour that runs through this whole package: a model may propose which numbers to
# combine and how, and the runtime computes the result. A model that also produced the value would
# be the only thing standing behind it, which is the arrangement every other operation here refuses.


class UngroundedExpressionError(ValueError):
    """An expression referred to a number the message never established."""


_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Load,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.UAdd,
)

#: Constants that carry no claim: an identity, a zero, and the base of a percentage. Anything else
#: in an expression must be a number the message actually stated.
_NEUTRAL_CONSTANTS = (0.0, 1.0, 100.0)
_TOLERANCE = 1e-9


def _literal_is_grounded(value: float, facts: Sequence[NumericFact]) -> bool:
    if any(abs(value - neutral) < _TOLERANCE for neutral in _NEUTRAL_CONSTANTS):
        return True
    for fact in facts:
        if abs(value - fact.value) < _TOLERANCE:
            return True
        if fact.unit_kind == "percent":
            # A percentage is written "12" and used as 0.12, 1.12 or 0.88. Those three readings are
            # the fact restated, not new information -- and nothing else is.
            share = fact.value / 100.0
            if any(abs(value - form) < _TOLERANCE for form in (share, 1 + share, 1 - share)):
                return True
    return False


def evaluate_grounded_expression(
    expression: str,
    *,
    facts: Sequence[NumericFact],
    symbols: Mapping[str, float] | None = None,
) -> float:
    """Evaluate `expression` over the message's own numbers. Raises rather than guessing.

    Every name must resolve to a stated fact or an already-computed step, and every literal must be
    a stated fact, a restatement of a stated percentage, or a neutral constant. An expression that
    introduces 6.85 as a literal -- a rate nobody stated -- is refused, which is what stops a model
    from smuggling an answer through as an operand.
    """
    text = str(expression or "").strip()
    if not text:
        raise UngroundedExpressionError("empty expression")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise UngroundedExpressionError(f"not an arithmetic expression: {exc.msg}") from exc
    # A whole expression that is a single LEAF is not a calculation, it is an assertion. The
    # neutral-constant carve-out below exists so percentage arithmetic (`250 * 10 / 100`) can use
    # 0, 1 and 100 as OPERANDS; a whole expression that IS one of them smuggles a knowledge claim
    # through the arithmetic lane -- measured live: a quantitative plan for "what is the boiling
    # point of water at sea level in Celsius?" shipped the step `100` (neutral, therefore
    # "grounded") rendered as "Boiling point of water at sea level: 100 = 100 °C", a world-
    # knowledge fact the model asserted as if the runtime had derived it.
    #
    # A bare NAME is the same assertion wearing an operand's clothes, and refusing only the
    # literal left that shape open: measured live 2026-09-09 on the same question, the plan came
    # back with the step `label="boiling_point", expression="fact_2"`, and because `fact_2` is a
    # stated fact -- 250, from the SIBLING clause "10 percent of 250" -- it resolved cleanly and
    # shipped as "boiling_point: fact_2 = 250 Celsius". The runtime asserted, in its own voice,
    # that water boils at 250 °C. A name restates one operand under a new label, which is worse
    # than a literal: the label is what makes it read as derived.
    #
    # So the rule is the principle the literal case already stated, not one more shape: a step
    # that references nothing and computes nothing establishes nothing. `x` and `250` are both
    # leaves; neither is arithmetic. Anything with an actual operator in it is untouched.
    if isinstance(tree.body, (ast.Constant, ast.Name)):
        raise UngroundedExpressionError(
            "a bare operand is not a calculation; it names no stated fact and derives nothing"
        )

    bindings: dict[str, float] = {fact.label: fact.value for fact in facts}
    for name, value in dict(symbols or {}).items():
        if str(name) in bindings:
            # The source quantity owns both its value and its unit provenance.
            continue
        try:
            bindings[str(name)] = float(value)
        except (TypeError, ValueError):
            continue

    # These are derived from this message, not delegated to upstream/model labels.
    bindings.update(percentage_share_bindings(facts))

    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mult, ast.Div)):
            # An identity operand is not arithmetic, it is a placeholder. `x / 1` and `x * 1`
            # evaluate cleanly and mean nothing, and that is exactly the shape a model reaches for
            # when a required operand is missing and it has no symbol to name: measured live as
            # `bitcoin_price / 1`, presented as an answer. A `1` the MESSAGE states is a real
            # quantity and stays legal; a `1` nobody wrote is filler standing where a value should
            # be. Only multiplication and division are checked, so `1 + share` and `1 - share`
            # percentage forms are untouched.
            for side in (node.left, node.right):
                if (
                    isinstance(side, ast.Constant)
                    and isinstance(side.value, (int, float))
                    and not isinstance(side.value, bool)
                    and abs(float(side.value) - 1.0) < _TOLERANCE
                    and not any(abs(1.0 - fact.value) < _TOLERANCE for fact in facts)
                ):
                    raise UngroundedExpressionError(
                        "multiplying or dividing by 1 is not a calculation; a required value is "
                        "missing rather than equal to one"
                    )
        if not isinstance(node, _ALLOWED_NODES):
            raise UngroundedExpressionError(
                f"{type(node).__name__} is not allowed in a grounded expression"
            )
        if isinstance(node, ast.Name) and node.id not in bindings:
            raise UngroundedExpressionError(f"{node.id!r} is not a fact this message established")
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float)) or isinstance(node.value, bool):
                raise UngroundedExpressionError("only numbers may appear in an expression")
            if not _literal_is_grounded(float(node.value), facts):
                raise UngroundedExpressionError(
                    f"{node.value:g} is not a number this message established"
                )

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.Name):
            return bindings[node.id]
        if isinstance(node, ast.UnaryOp):
            operand = _eval(node.operand)
            return -operand if isinstance(node.op, ast.USub) else operand
        if isinstance(node, ast.BinOp):
            left, right = _eval(node.left), _eval(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                if abs(right) < _TOLERANCE:
                    raise UngroundedExpressionError("division by zero")
                return left / right
        raise UngroundedExpressionError(f"{type(node).__name__} is not allowed")

    return _eval(tree)


__all__ = [
    "MissingInformation",
    "NumericFact",
    "SharedTurnContext",
    "UngroundedExpressionError",
    "UnitAmbiguity",
    "detect_unit_ambiguities",
    "evaluate_grounded_expression",
    "extract_numeric_facts",
    "extract_shared_context",
    "render_briefing",
]
