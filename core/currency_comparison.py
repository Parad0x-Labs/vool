"""Comparing several currencies: exchange VALUE and local PURCHASING POWER are two questions.

QA-050-027. The reported turn, verbatim:

    "Compare 500 kr, $500, and ¥500 in terms of value and purchasing power. Which is worth most
     today, and which would go furthest locally? If inflation is 5%, which is best to hold?"

and then, one turn later:

    "Actually the kr is in Copenhagen and the ¥ is in Shanghai."

VOOL answered the first with `kr = NOK` and `¥ = JPY`, and answered the second by writing
"Copenhagen (NOK)" and "Shanghai (JPY)" — keeping both wrong readings after being handed the two
facts that settle them. Traced on the untouched base 03b04b39 through the real seams:

    currency_semantics_present("Compare 500 kr, $500, and ¥500 …")   -> False
    currency_fast_path(...)                                          -> None
    location_bindings / any city table                               -> did not exist

Read those three rows and the whole reported answer is accounted for:

* the turn is a currency turn and **nothing in the runtime said so**. `currency_definition_intent`
  wants the whole message to be one definition; `fx_conversion_intent` wants `<amount> <unit> to
  <unit>`. A three-way comparison is neither, so no currency lane claimed it, no grounding
  requirement attached, and the model answered a rate-and-price-level question from parametric
  memory. NOK and JPY are what filling that vacuum looks like;
* `core/conductor/shared_context.py` *did* see all three symbols as ambiguous — and correctly — but
  the conductor's ambiguity report never reached this turn, and even where it does, nothing in the
  runtime could turn "Copenhagen" into DKK. `_UNIT_RESOLVERS` knew ISO codes, nationalities and
  countries. Not one city. So the correction turn carried no new information as far as the runtime
  was concerned, and the model repeated itself;
* purchasing power was answered at all. It is a measurement against a local basket of goods, and
  this runtime holds no price-level data of any kind. Ranking it was invention twice over.

The invariant this module exists to hold
-----------------------------------------

    Exchange VALUE and local PURCHASING POWER are different measurements, taken from different
    data, and answering one does not answer the other. Neither may be stated from a number this
    runtime was not given.

That is why supplying FX rates ranks value and still refuses purchasing power, and why supplying
PPP factors ranks purchasing power and still refuses "worth most today". A single answer that did
both from one set of numbers would be the defect wearing a table.

Where the numbers come from, and where they do not
---------------------------------------------------
Three sources, all external, none stored: a rate the user typed, a PPP factor the user typed, and
`live_rates` / `live_ppp` passed in by a caller that has a feed. This module holds no rate and no
price level, and `tests/test_v050_complex_currency_ppp_reasoning.py` walks its AST to keep it that
way — the same structural guard `core/currency_intent.py` carries, for the same reason. A stored
rate makes the whole family go green and is wrong the next morning.

Correction is not restart
--------------------------
When a later turn pins a symbol, the answer does NOT start again from the top. It names what the
earlier reading claimed, says which conclusions that reading carried and voids them, and only then
states what is now pinned. `Invalidation` is that, typed: a test can assert WHICH prior reading was
retracted rather than matching prose. Silently producing a better second answer, with no statement
that the first one was wrong, is how a runtime teaches a reader to trust the wrong one.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from core.currency_intent import (
    ISO_4217,
    SYMBOL_FAMILIES,
    UNIT_WORD_FAMILIES,
    LocationBinding,
    codes_named,
    currency_transaction_intent,
    location_bindings,
    names_named,
    resolve_currency_literal,
)
from core.entity_consistency import EntityContradiction, entity_consistency_report

# =================================================================================================
# What a comparison turn asks for. Three asks, because they need three different kinds of data.
# =================================================================================================

#: "which is worth most" — answered by an exchange rate and nothing else.
ASK_MARKET_VALUE = "market_value"
#: "which goes furthest locally" — answered by price levels, NOT by an exchange rate.
ASK_PURCHASING_POWER = "purchasing_power"
#: "which is best to hold" — answered by inflation AND nominal interest AND an expected FX path.
ASK_HOLD = "hold"

_MARKET_VALUE_CUES: tuple[str, ...] = (
    "worth most", "worth the most", "worth more", "worth least", "in terms of value",
    "most valuable", "market value", "exchange value", "which is worth", "who is worth",
    "highest value", "biggest amount", "largest amount", "converted value", "nominal value",
    "strongest currency", "which currency is stronger", "face value",
)
_PURCHASING_POWER_CUES: tuple[str, ...] = (
    "purchasing power", "buying power", "purchase power", "go furthest", "goes furthest",
    "go the furthest", "go further", "goes further", "buy more", "buy the most", "buys more",
    "cost of living", "locally", "local prices", "in real terms", "ppp",
    "purchasing-power", "parity",
)
_HOLD_CUES: tuple[str, ...] = (
    "best to hold", "better to hold", "which to hold", "should i hold", "worth holding",
    "store of value", "best to keep", "keep my money", "hold my money", "park my money",
    "best to save", "which would i rather hold",
)
#: Deliberately NOT "which is" or "which one". Those are generic enough to appear in any question
#: ("tell me which is newest"), and a coverage check that treats them as currency framing reports
#: no residue for a mixed turn — which is exactly how a gate ends up swallowing the other half.
_COMPARE_CUES: tuple[str, ...] = (
    "compare", "comparison", "compared", " vs ", " vs.", "versus",
    "which of", "rank them", "ranked by", "difference between", "stack up", "side by side",
)

#: "if inflation is 5%" — the number is real, and the point is that ONE number cannot rank three
#: economies. Detected so the answer can say that, not so it can be used.
_INFLATION_RE = re.compile(
    r"inflation[^.?!%]{0,40}?(?P<rate>\d+(?:\.\d+)?)\s*%|(?P<rate2>\d+(?:\.\d+)?)\s*%[^.?!]{0,20}?inflation",
    re.IGNORECASE,
)
#: "DKK inflation 3%", "inflation in China is 1%" — per-currency figures, which CAN discriminate.
_PER_CODE_INFLATION_RE = re.compile(
    r"\b(?P<code>[A-Z]{3})\b[^.?!%]{0,24}?inflation[^.?!%]{0,16}?(?P<rate>\d+(?:\.\d+)?)\s*%"
    r"|inflation[^.?!%]{0,16}?\b(?P<code2>[A-Z]{3})\b[^.?!%]{0,16}?(?P<rate2>\d+(?:\.\d+)?)\s*%",
)

# =================================================================================================
# Scanning the amounts. One scanner, built from the SAME tables the rest of the currency lane uses.
# =================================================================================================

_SYMBOL_LITERALS = sorted(
    (token for token in SYMBOL_FAMILIES if not token[:1].isalpha()), key=len, reverse=True
)
_WORD_LITERALS = sorted(
    {token for token in SYMBOL_FAMILIES if token[:1].isalpha()}
    | set(UNIT_WORD_FAMILIES)
    | {code.lower() for code in ISO_4217},
    key=len,
    reverse=True,
)

_SYMBOL_ALT = "|".join(re.escape(token) for token in _SYMBOL_LITERALS)
_WORD_ALT = "|".join(rf"{re.escape(token)}\b" for token in _WORD_LITERALS)
_NUMBER = r"\d{1,3}(?:[,   ]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"

_AMOUNT_RE = re.compile(
    rf"(?P<prefix>{_SYMBOL_ALT})?\s*(?P<number>{_NUMBER})\s*(?P<suffix>{_SYMBOL_ALT}|{_WORD_ALT})?",
    re.IGNORECASE,
)

#: "1 USD = 6.9 DKK", "USD/DKK = 6.9", "1 USD to 6.9 DKK". A rate the user typed, in the two forms
#: people actually type it. Nothing here produces a number; it only reads one back out.
_PAIR_RATE_FRAMES: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:1\s*)?\b(?P<base>[A-Za-z]{3})\b\s*(?:=|:|is|→|->|\bto\b|\bbuys\b)\s*"
        r"(?P<rate>\d+(?:\.\d+)?)\s*\b(?P<quote>[A-Za-z]{3})\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?P<base>[A-Za-z]{3})\b\s*/\s*\b(?P<quote>[A-Za-z]{3})\b\s*(?:=|:|is|at|\bof\b)?\s*"
        r"(?P<rate>\d+(?:\.\d+)?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<rate>\d+(?:\.\d+)?)\s*\b(?P<quote>[A-Za-z]{3})\b\s*(?:per|/|\bto the\b)\s*"
        r"\b(?P<base>[A-Za-z]{3})\b",
        re.IGNORECASE,
    ),
)

#: The marker that routes a pair to PPP instead of to the market. Read per SENTENCE, so one message
#: may carry both kinds and neither leaks into the other.
_PPP_MARKER_RE = re.compile(
    r"\b(?:ppp|purchasing[- ]power[- ]parit(?:y|ies)|price level|price levels|"
    r"conversion factor|basket)\b",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+")


@dataclass(frozen=True)
class ComparedCurrency:
    """One amount in the comparison, and how sure the runtime is about which currency it is."""

    written: str
    unit: str
    amount: Decimal | None
    code: str = ""
    candidates: tuple[str, ...] = ()
    basis: str = ""

    @property
    def pinned(self) -> bool:
        return bool(self.code)

    def describe(self) -> str:
        if self.code:
            fact = ISO_4217.get(self.code)
            return f"{self.code} — {fact.name}" if fact else self.code
        if self.candidates:
            return "not pinned — " + " / ".join(self.candidates)
        return "not pinned"

    def label(self) -> str:
        if self.amount is None:
            return self.written
        return f"{_format_decimal(self.amount)} {self.code or self.unit}"


@dataclass(frozen=True)
class Invalidation:
    """A reading the previous answer carried, and why it no longer holds.

    `kind` is `narrowed` when a symbol went from a candidate family to one currency, and `replaced`
    when a currency the previous answer NAMED is now a different one. The second is the reported
    defect's shape: an answer that said NOK and must now say DKK.
    """

    unit: str
    kind: str
    previous: tuple[str, ...]
    now: str
    because: str

    def statement(self) -> str:
        now_fact = ISO_4217.get(self.now)
        now_label = f"{self.now} ({now_fact.name})" if now_fact else self.now
        because = self.because[:1].upper() + self.because[1:] if self.because else ""
        if self.kind == "replaced":
            was = self.previous[0]
            was_fact = ISO_4217.get(was)
            was_label = f"{was} ({was_fact.name})" if was_fact else was
            return (
                f"“{self.unit}” was read as {was_label}. {because}, so it is {now_label}. "
                f"Every ranking, conversion and recommendation that used {was} is void."
            )
        listed = ", ".join(self.previous)
        gone = [code for code in self.previous if code != self.now]
        dropped = ", ".join(gone)
        verb = "is" if len(gone) == 1 else "are"
        them = "it" if len(gone) == 1 else "them"
        return (
            f"“{self.unit}” was left open across {listed}. {because}, so it is {now_label} — "
            f"{dropped} {verb} out, and anything said about this amount that ranged over {them} no "
            f"longer applies."
        )


@dataclass(frozen=True)
class CurrencyComparisonRequest:
    """A comparison turn, read. Carries no rate and no price level it was not handed."""

    text: str
    currencies: tuple[ComparedCurrency, ...]
    asks: frozenset[str]
    stated_inflation: Decimal | None = None
    market_rates: Mapping[tuple[str, str], Decimal] = field(default_factory=dict)
    ppp_rates: Mapping[tuple[str, str], Decimal] = field(default_factory=dict)
    per_code_inflation: Mapping[str, Decimal] = field(default_factory=dict)
    bindings: tuple[LocationBinding, ...] = ()
    conflicts: tuple[LocationBinding, ...] = ()
    #: Pairings an authority table says cannot hold ("Copenhagen NOK"). Ranking is refused while
    #: any of these stands — every figure computed from a poisoned binding inherits the poison.
    contradictions: tuple[EntityContradiction, ...] = ()

    @property
    def poisoned(self) -> bool:
        return bool(self.contradictions or self.conflicts)

    @property
    def unpinned(self) -> tuple[ComparedCurrency, ...]:
        return tuple(item for item in self.currencies if not item.pinned)

    @property
    def pinned(self) -> tuple[ComparedCurrency, ...]:
        return tuple(item for item in self.currencies if item.pinned)

    def codes(self) -> tuple[str, ...]:
        seen: list[str] = []
        for item in self.currencies:
            if item.code and item.code not in seen:
                seen.append(item.code)
        return tuple(seen)


# =================================================================================================
# Parsing
# =================================================================================================


def _flat(text: str) -> str:
    return " ".join(str(text or "").split())


def _to_decimal(written: str) -> Decimal | None:
    cleaned = re.sub(r"[,   ]", "", str(written or ""))
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _format_decimal(value: Decimal) -> str:
    quantized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if quantized == quantized.to_integral_value():
        return f"{int(quantized):,}"
    return f"{quantized:,.2f}"


def rate_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Where in `text` a supplied rate is written.

    "1 USD = 6.4 DKK" contains two perfectly good `<amount> <currency>` readings and neither is an
    amount being compared — they are the conversion factor itself. Scanning them as amounts is how
    a clarification turn carrying rates got re-read as a brand new four-way comparison of the rate
    literals, so the spans are excluded by position rather than by any guess about intent.
    """

    spans: list[tuple[int, int]] = []
    for frame in _PAIR_RATE_FRAMES:
        spans.extend(match.span() for match in frame.finditer(str(text or "")))
    return tuple(spans)


def scan_amounts(
    text: str, *, exclude: Sequence[tuple[int, int]] = ()
) -> tuple[ComparedCurrency, ...]:
    """Every `<amount> <currency>` the message writes, in order, with its ambiguity intact."""

    raw = str(text or "")
    excluded = tuple(exclude)
    found: list[ComparedCurrency] = []
    for match in _AMOUNT_RE.finditer(raw):
        literal = (match.group("prefix") or match.group("suffix") or "").strip()
        if not literal:
            continue
        if any(start < match.end() and match.start() < end for start, end in excluded):
            continue
        reference = resolve_currency_literal(literal, raw=raw)
        if reference is None:
            continue
        amount = _to_decimal(match.group("number"))
        if amount is None:
            continue
        found.append(
            ComparedCurrency(
                written=_flat(match.group(0)),
                unit=literal.lower(),
                amount=amount,
                code=reference.code,
                candidates=reference.candidates,
                basis=reference.basis,
            )
        )
    return tuple(found)


def parse_supplied_rates(text: str) -> tuple[dict[tuple[str, str], Decimal], dict[tuple[str, str], Decimal]]:
    """The market rates and the PPP factors the USER typed, kept apart.

    Routing is per sentence and by marker: a pair written in a sentence that says PPP, price level,
    conversion factor or basket is a PPP factor; every other pair is a market rate. Reading them as
    the same thing is the mistake this whole module exists to refuse.
    """

    market: dict[tuple[str, str], Decimal] = {}
    ppp: dict[tuple[str, str], Decimal] = {}
    for sentence in _SENTENCE_SPLIT_RE.split(str(text or "")):
        target = ppp if _PPP_MARKER_RE.search(sentence) else market
        for frame in _PAIR_RATE_FRAMES:
            for match in frame.finditer(sentence):
                base = (match.group("base") or "").upper()
                quote = (match.group("quote") or "").upper()
                if base not in ISO_4217 or quote not in ISO_4217 or base == quote:
                    continue
                rate = _to_decimal(match.group("rate"))
                if rate is None or rate <= 0:
                    continue
                target.setdefault((base, quote), rate)
    return market, ppp


def _parse_inflation(text: str) -> tuple[Decimal | None, dict[str, Decimal]]:
    raw = str(text or "")
    per_code: dict[str, Decimal] = {}
    for match in _PER_CODE_INFLATION_RE.finditer(raw):
        code = (match.group("code") or match.group("code2") or "").upper()
        rate = _to_decimal(match.group("rate") or match.group("rate2") or "")
        if code in ISO_4217 and rate is not None:
            per_code.setdefault(code, rate)
    stated: Decimal | None = None
    for match in _INFLATION_RE.finditer(raw):
        stated = _to_decimal(match.group("rate") or match.group("rate2") or "")
        if stated is not None:
            break
    return stated, per_code


# =================================================================================================
# Whole-turn coverage. A front-door gate may claim a turn only if it answers ALL of it.
# =================================================================================================

_SENTENCE_RE = re.compile(r"[^.;!?\n]+")
#: Symbols and unit words only — no ISO codes, because "all", "top" and "try" are codes AND
#: ordinary words, and coverage read from a raw token match would call any sentence containing
#: "all" a currency sentence. Codes come from `codes_named`, which holds the homograph rule.
_UNIT_MENTION_RE = re.compile(
    "|".join(
        (rf"\b{re.escape(token)}\b" if token[:1].isalpha() else re.escape(token))
        for token in sorted(
            set(SYMBOL_FAMILIES) | set(UNIT_WORD_FAMILIES), key=len, reverse=True
        )
    ),
    re.IGNORECASE,
)


def uncovered_residue(text: str) -> tuple[str, ...]:
    """Sentences of `text` this lane does not answer, so a mixed turn is never swallowed whole.

    A deterministic front-door gate that claims a turn ENDS it: nothing downstream runs, and any
    other request in the message is silently dropped. So the claim needs a coverage proof, not a
    recognition. A sentence counts as covered when it carries a currency amount, a currency code
    or symbol, a comparison/value/purchasing-power/holding cue, or a location that binds one of
    those — and a sentence of two or fewer content words is filler either way.

    Everything else is residue. "Compare 500 kr and $500, then list the files in /tmp" leaves one
    sentence uncovered, and one is enough: the turn belongs to whatever composes several intents,
    and this lane hands its findings over instead of answering.
    """

    residue: list[str] = []
    for sentence in _SENTENCE_RE.findall(str(text or "")):
        stripped = sentence.strip(" \t\r\n,-—:")
        if not stripped:
            continue
        words = [
            word
            for word in re.findall(r"[a-z0-9]+", stripped.lower())
            if len(word) > 2
        ]
        if len(words) < 3:
            continue
        if scan_amounts(stripped) or codes_named(stripped):
            continue
        if _UNIT_MENTION_RE.search(stripped):
            continue
        if _asks_in(stripped) or _comparison_framed(stripped):
            continue
        if location_bindings(stripped):
            continue
        residue.append(" ".join(stripped.split()))
    return tuple(residue)


def covers_whole_turn(text: str) -> bool:
    return not uncovered_residue(text)


def _asks_in(text: str) -> frozenset[str]:
    padded = " " + _flat(text).lower() + " "
    asks: set[str] = set()
    if any(cue in padded for cue in _MARKET_VALUE_CUES):
        asks.add(ASK_MARKET_VALUE)
    if any(cue in padded for cue in _PURCHASING_POWER_CUES):
        asks.add(ASK_PURCHASING_POWER)
    if any(cue in padded for cue in _HOLD_CUES):
        asks.add(ASK_HOLD)
    return frozenset(asks)


def _comparison_framed(text: str) -> bool:
    padded = " " + _flat(text).lower() + " "
    return any(cue in padded for cue in _COMPARE_CUES)


def currency_comparison_intent(
    text: str, *, chat_codes: Sequence[str] = ()
) -> CurrencyComparisonRequest | None:
    """The multi-currency comparison this turn asks for, or None.

    Claims only a turn that names TWO OR MORE different currencies and asks something about their
    relative value, their purchasing power, or which to hold. "explain purchasing power parity"
    names no currency and is not claimed; "what is kr?" names one and is not claimed; a request to
    MOVE money is refused outright and goes back to the ordinary path with its own safeguards.
    """

    raw = str(text or "")
    if not raw.strip() or currency_transaction_intent(raw):
        return None

    amounts = scan_amounts(raw, exclude=rate_spans(raw))
    units = {item.unit for item in amounts}
    if len(units) < 2:
        return None

    asks = _asks_in(raw)
    if not asks and not _comparison_framed(raw):
        return None
    if not asks:
        # "Compare 500 kr and $500" with nothing said about what to compare on. Value is the only
        # reading a bare comparison of two amounts has.
        asks = frozenset({ASK_MARKET_VALUE})

    market, ppp = parse_supplied_rates(raw)
    stated_inflation, per_code = _parse_inflation(raw)
    request = CurrencyComparisonRequest(
        text=raw,
        currencies=amounts,
        asks=asks,
        stated_inflation=stated_inflation,
        market_rates=market,
        ppp_rates=ppp,
        per_code_inflation=per_code,
    )
    return apply_location_evidence(request, raw, chat_codes=chat_codes)


# =================================================================================================
# Location evidence — the half that turns "Copenhagen" into DKK, and refuses to turn it into NOK.
# =================================================================================================


def _explicit_code_pins(
    text: str,
    families: Mapping[str, tuple[str, ...]],
    *,
    chat_codes: Sequence[str] = (),
) -> tuple[dict[str, str], dict[str, str]]:
    """Units pinned by an ISO code, split into (named in THIS turn, carried from the chat).

    The codes come from `core.currency_intent.codes_named` and the spelled-out names from
    `names_named` — the two places that already decide what a token means, so "the ¥ is RMB" and
    "the kr is the Danish krone" pin exactly as an ISO code does. `codes_named` is also what holds
    the homograph rule, and uppercasing the turn to go looking for codes would undo it: "the cup is
    on the table" would pin the dollar family to the Cuban peso, and "let me try USD mode" would
    name the lira. The turn is never uppercased anywhere in this module; a candidate is uppercased
    for the table lookup only.

    `chat_codes` is what the conversation has already settled. It ranks BELOW anything the current
    turn says, and it is kept separate in the return so the answer can say which of the two pinned
    a unit rather than presenting a carried-over code as something the user just typed.
    """

    written = str(text or "")
    named = set(codes_named(written)) | set(names_named(written))
    carried = {str(code).upper() for code in chat_codes} - named
    here: dict[str, str] = {}
    remembered: dict[str, str] = {}
    for unit, candidates in families.items():
        hits = sorted(named & set(candidates))
        if len(hits) == 1:
            here[unit] = hits[0]
            continue
        if hits:
            continue
        recalled = sorted(carried & set(candidates))
        if len(recalled) == 1:
            remembered[unit] = recalled[0]
    return here, remembered


def apply_location_evidence(
    request: CurrencyComparisonRequest,
    text: str,
    *,
    chat_codes: Sequence[str] = (),
) -> CurrencyComparisonRequest:
    """Pin what `text` pins, record what it contradicts, and leave the rest open.

    A place binds a unit only when the unit was written BARE beside it — the rule lives in
    `core.currency_intent.location_bindings` so this lane and the conductor's shared context read
    one implementation of it. A place whose currency is not in the unit's family is a contradiction
    and is recorded as one; it never pins, and it never silently disappears either.
    """

    families = {
        item.unit: item.candidates for item in request.currencies if len(item.candidates) > 1
    }
    bindings = location_bindings(text)
    pins = {binding.unit: binding.code for binding in bindings if binding.pins}
    conflicts = tuple(binding for binding in bindings if not binding.pins)
    reasons = {binding.unit: binding.place for binding in bindings if binding.pins}

    # Two kinds of evidence for the same unit that DISAGREE is less determined than one, not more.
    # "the kr is in Copenhagen … 1 USD = 4.0 NOK" pins nothing; picking either would be the runtime
    # choosing which of the user's own statements to believe.
    disagreements: list[EntityContradiction] = []
    here, remembered = _explicit_code_pins(text, families, chat_codes=chat_codes)
    for unit, code in here.items():
        located = pins.get(unit)
        if located and located != code:
            place = reasons.get(unit, "")
            disagreements.append(
                EntityContradiction(
                    domain="currency_evidence",
                    child=place or unit,
                    child_label="place",
                    named_parent=code,
                    actual_parent=located,
                    parent_label="currency",
                    relation="uses",
                    written=f"{place.title()} … {code}" if place else f"“{unit}” … {code}",
                    child_written=place.title() if place else unit,
                    parent_preposition="that uses",
                )
            )
            del pins[unit]
            reasons.pop(unit, None)
            continue
        pins.setdefault(unit, code)
        reasons.setdefault(unit, code)

    # Lowest precedence, and only where the turn itself said nothing: a code this chat already
    # settled on. Carried, not re-typed, so the reading says where it came from.
    carried_units = set()
    for unit, code in remembered.items():
        if unit not in pins:
            pins[unit] = code
            reasons[unit] = code
            carried_units.add(unit)

    updated: list[ComparedCurrency] = []
    for item in request.currencies:
        code = pins.get(item.unit, "")
        if code and code != item.code and code in (item.candidates or (code,)):
            place = reasons.get(item.unit, "")
            if item.unit in carried_units:
                basis = "chat_context"
            elif place.upper() in ISO_4217:
                basis = "explicit_code"
            else:
                basis = f"location:{place}"
            updated.append(
                ComparedCurrency(
                    written=item.written,
                    unit=item.unit,
                    amount=item.amount,
                    code=code,
                    candidates=item.candidates,
                    basis=basis,
                )
            )
            continue
        updated.append(item)

    return replace(
        request,
        currencies=tuple(updated),
        bindings=request.bindings + bindings,
        conflicts=request.conflicts + conflicts,
        contradictions=request.contradictions
        + entity_consistency_report(text).contradictions
        + tuple(disagreements),
    )


def comparison_followup(
    prior: CurrencyComparisonRequest, text: str, *, chat_codes: Sequence[str] = ()
) -> CurrencyComparisonRequest | None:
    """The prior comparison, updated by a turn that adds locations, codes, rates or PPP factors.

    Returns None when the new turn adds nothing this lane can use — a follow-up that changes
    nothing must not be claimed, because claiming it would answer some other question with a
    currency table.
    """

    raw = str(text or "")
    if not raw.strip() or currency_transaction_intent(raw):
        return None

    market, ppp = parse_supplied_rates(raw)
    stated_inflation, per_code = _parse_inflation(raw)
    families = {
        item.unit: item.candidates for item in prior.currencies if len(item.candidates) > 1
    }
    bindings = location_bindings(raw)
    here, remembered = _explicit_code_pins(raw, families, chat_codes=chat_codes)
    broken = entity_consistency_report(raw).contradictions
    if not (bindings or here or remembered or market or ppp or per_code or broken):
        return None

    carried = replace(
        prior,
        text=raw,
        market_rates={**dict(prior.market_rates), **market},
        ppp_rates={**dict(prior.ppp_rates), **ppp},
        per_code_inflation={**dict(prior.per_code_inflation), **per_code},
        stated_inflation=stated_inflation if stated_inflation is not None else prior.stated_inflation,
        bindings=(),
        conflicts=(),
        contradictions=(),
    )
    return apply_location_evidence(carried, raw, chat_codes=chat_codes)


def invalidations(
    prior: CurrencyComparisonRequest, updated: CurrencyComparisonRequest
) -> tuple[Invalidation, ...]:
    """Which of the earlier answer's readings the new turn retracts, and why."""

    before = {item.unit: item for item in prior.currencies}
    out: list[Invalidation] = []
    seen: set[str] = set()
    places = {binding.unit: binding.place for binding in updated.bindings if binding.pins}
    for item in updated.currencies:
        was = before.get(item.unit)
        if was is None or not item.code or item.unit in seen:
            continue
        if was.code == item.code:
            continue
        seen.add(item.unit)
        place = places.get(item.unit, "")
        fact = ISO_4217.get(item.code)
        if place:
            because = f"{place.title()} is in {fact.region}" if fact else f"{place.title()} names {item.code}"
        else:
            because = f"the message names {item.code} outright"
        if was.code:
            out.append(
                Invalidation(
                    unit=item.unit, kind="replaced", previous=(was.code,),
                    now=item.code, because=because,
                )
            )
        else:
            out.append(
                Invalidation(
                    unit=item.unit, kind="narrowed", previous=tuple(was.candidates),
                    now=item.code, because=because,
                )
            )
    return tuple(out)


# =================================================================================================
# Ranking — arithmetic over supplied numbers only. Nothing here can produce a rate.
# =================================================================================================


def _anchor_values(
    codes: Sequence[str], pairs: Mapping[tuple[str, str], Decimal]
) -> tuple[str, dict[str, Decimal]]:
    """Value of one unit of each code, expressed in one anchor currency, via the supplied pairs."""

    if not codes:
        return "", {}
    anchor = codes[0]
    per: dict[str, Decimal] = {anchor: Decimal(1)}
    frontier = [anchor]
    while frontier:
        current = frontier.pop()
        for (base, quote), rate in pairs.items():
            if base == current and quote not in per:
                per[quote] = per[current] / rate
                frontier.append(quote)
            elif quote == current and base not in per:
                per[base] = per[current] * rate
                frontier.append(base)
    return anchor, per


def _rank(
    currencies: Sequence[ComparedCurrency], pairs: Mapping[tuple[str, str], Decimal]
) -> tuple[str, list[tuple[ComparedCurrency, Decimal]], list[str]]:
    usable = [item for item in currencies if item.pinned and item.amount is not None]
    anchor, per = _anchor_values([item.code for item in usable], pairs)
    ranked: list[tuple[ComparedCurrency, Decimal]] = []
    unreachable: list[str] = []
    for item in usable:
        if item.code not in per:
            unreachable.append(item.code)
            continue
        assert item.amount is not None
        ranked.append((item, item.amount * per[item.code]))
    ranked.sort(key=lambda pair: pair[1], reverse=True)
    return anchor, ranked, unreachable


# =================================================================================================
# Rendering
# =================================================================================================

_TABLE_HEADER = ("| Written | Currency | How it was read |", "| --- | --- | --- |")

_BASIS_PROSE = {
    "iso_code": "written as an ISO 4217 code",
    "currency_name": "named in full",
    "explicit_code": "the message names the ISO code",
    "symbol": "",
    "unit_word": "",
    "place": "the message names the country",
    "chat_context": "a code this chat had already settled on",
}


def _reading_row(item: ComparedCurrency) -> str:
    if item.pinned:
        if item.basis.startswith("location:"):
            place = item.basis.split(":", 1)[1]
            fact = ISO_4217.get(item.code)
            why = f"{place.title()} is in {fact.region}" if fact else f"{place.title()} names it"
        else:
            why = _BASIS_PROSE.get(item.basis) or "named directly"
        return f"| {item.written} | {item.describe()} | {why} |"
    listed = ", ".join(item.candidates)
    return (
        f"| {item.written} | not pinned — {listed} | “{item.unit}” is written by all of them and "
        f"nothing here names a country, city or ISO code |"
    )


def _missing_lines(request: CurrencyComparisonRequest, *, have_market: bool, have_ppp: bool) -> list[str]:
    lines: list[str] = []
    unpinned = request.unpinned
    if unpinned:
        for item in unpinned:
            lines.append(
                f"- which currency “{item.unit}” is. It could be {', '.join(item.candidates)}. "
                f"Name the country, the city or the ISO code and it is pinned."
            )
    if ASK_MARKET_VALUE in request.asks and not have_market:
        pinned = request.codes()
        # Name the pair ONLY when the codes are actually pinned. Printing "the current SEK/USD
        # rate" for a message that said "kr" would invent the very fact the line above just said
        # is missing — the same trap `render_fx_conversion` refuses.
        pair = (
            f"the current {'/'.join(pinned)} cross rates"
            if len(pinned) > 1
            else "the current cross rates between those currencies"
        )
        lines.append(
            f"- {pair}, for a specific date and time. No live FX source is wired up here, and a "
            f"rate recalled from training data would be months stale and stated with false "
            f"confidence."
        )
    if ASK_PURCHASING_POWER in request.asks and not have_ppp:
        lines.append(
            "- price-level data for each currency's home economy — a PPP conversion factor, or the "
            "local cost of the same basket of goods. Purchasing power is measured against local "
            "prices, not against an exchange rate, so no set of FX rates answers it."
        )
    if ASK_HOLD in request.asks and not request.per_code_inflation:
        if request.stated_inflation is not None:
            lines.append(
                f"- whose inflation the {_format_decimal(request.stated_inflation)}% is. One figure "
                f"applied to every currency at once cannot rank them: it erodes all three by the "
                f"same fraction and leaves the order exactly as it was."
            )
        lines.append(
            "- each currency's own inflation rate, its nominal interest rate, and the horizon you "
            "are holding over. Which is best to hold is a real-return question, and it turns on "
            "all three plus the expected path of the exchange rate — none of which is stated here."
        )
    return lines


def _ranking_lines(
    request: CurrencyComparisonRequest,
    pairs: Mapping[tuple[str, str], Decimal],
    *,
    heading: str,
    attribution: str,
    caveat: str,
) -> list[str]:
    anchor, ranked, unreachable = _rank(request.currencies, pairs)
    if not ranked:
        return []
    lines = [f"{heading} ({attribution}, expressed in {anchor}):"]
    for position, (item, value) in enumerate(ranked, start=1):
        lines.append(f"  {position}. {item.written} = {_format_decimal(value)} {anchor}  [{item.code}]")
    if unreachable:
        lines.append(
            f"  Not ranked: {', '.join(sorted(set(unreachable)))} — no supplied rate connects "
            f"{'it' if len(set(unreachable)) == 1 else 'them'} to {anchor}."
        )
    lines.append(f"  {caveat}")
    return lines


def render_currency_comparison(
    request: CurrencyComparisonRequest,
    *,
    prior: CurrencyComparisonRequest | None = None,
    live_rates: Mapping[tuple[str, str], Decimal] | None = None,
    rates_asof: str = "",
    live_ppp: Mapping[tuple[str, str], Decimal] | None = None,
    ppp_asof: str = "",
) -> str:
    """The answer. A number appears only when a number was supplied or passed in.

    `live_rates` and `live_ppp` are parameters rather than lookups so that wiring a real feed later
    is a change at the CALLER, and nothing in this module moves. Same arrangement as
    `render_fx_conversion`, for the same blast-radius reason.
    """

    market = {**dict(request.market_rates), **dict(live_rates or {})}
    ppp = {**dict(request.ppp_rates), **dict(live_ppp or {})}
    market_attribution = (
        "the rates you supplied"
        if not live_rates
        else f"a live rate set{f' as of {rates_asof}' if rates_asof else ''}"
    )
    ppp_attribution = (
        "the PPP factors you supplied"
        if not live_ppp
        else f"a live PPP set{f' as of {ppp_asof}' if ppp_asof else ''}"
    )

    # Rule: never rank, convert or explain from a binding an authority table says cannot hold.
    # A contradiction is not a missing input that arithmetic can route around — it is a wrong
    # premise, and every figure computed on top of it carries the wrongness forward silently.
    poisoned = request.poisoned

    market_lines: list[str] = []
    if market and ASK_MARKET_VALUE in request.asks and not poisoned:
        market_lines = _ranking_lines(
            request, market,
            heading="Ranked by market exchange value",
            attribution=market_attribution,
            caveat=(
                "That is exchange value only. It does not say which goes furthest locally — "
                "an exchange rate is not a price level."
                if not live_rates
                else "Arithmetic on the supplied rate set; the ordering is only as current as it is."
            ),
        )
    ppp_lines: list[str] = []
    if ppp and ASK_PURCHASING_POWER in request.asks and not poisoned:
        ppp_lines = _ranking_lines(
            request, ppp,
            heading="Ranked by local purchasing power",
            attribution=ppp_attribution,
            caveat=(
                "That is what the money buys at home. It is a different ordering from exchange "
                "value and it does not replace one."
            ),
        )
    inflation_lines: list[str] = []
    if request.per_code_inflation and ASK_HOLD in request.asks and not poisoned:
        stated = sorted(request.per_code_inflation.items())
        inflation_lines.append("Real erosion over one year, from the inflation figures you supplied:")
        for code, rate in stated:
            inflation_lines.append(
                f"  {code}: −{_format_decimal(rate)}% of purchasing power held in cash"
            )
        inflation_lines.append(
            "  Lowest inflation is not automatically the best to hold: nominal interest and the "
            "expected exchange-rate path both move the answer, and neither is stated."
        )

    have_market = bool(market_lines)
    have_ppp = bool(ppp_lines)

    lines: list[str] = []

    if prior is not None:
        voided = invalidations(prior, request)
        if voided:
            lines.append("Original conclusions invalidated")
            lines += [f"- {item.statement()}" for item in voided]
            lines.append("")
        elif not poisoned:
            lines.append("Nothing in that changes what was pinned.")
            lines.append("")

    if poisoned:
        lines.append("Contradiction in what was given — nothing below is computed from it")
        for conflict in request.conflicts:
            listed = ", ".join(conflict.candidates) or "any currency it could name"
            fact = ISO_4217.get(conflict.code)
            names = f"{conflict.code} ({fact.name})" if fact else conflict.code
            lines.append(
                f"- “{conflict.unit}” can only be {listed}, but {conflict.place.title()} uses "
                f"{names}. I have not picked one — say which you meant."
            )
        for broken in request.contradictions:
            lines.append(f"- {broken.statement()} {broken.question()}")
        lines.append(
            "I have not ranked, converted or explained anything while that stands. A wrong "
            "binding does not stay contained: it lands under every figure computed after it."
        )
        lines.append("")

    if prior is None:
        distinct = len({item.unit for item in request.currencies})
        lines.append(
            f"{distinct} amounts written with the same number in {distinct} different currencies. "
            f"The number being equal is the one thing here that carries no information."
        )
        lines.append("")

    lines.append("How each amount reads")
    lines += list(_TABLE_HEADER)
    lines += [_reading_row(item) for item in request.currencies]
    lines.append("")

    lines.append("Known")
    if prior is None:
        lines.append(
            "- Market exchange value and local purchasing power are two different measurements. "
            "Exchange value is what a market will trade the amount for right now; purchasing power "
            "is what it buys where it is spent. They routinely rank the same currencies in "
            "different orders, so “worth most” and “goes furthest” are two answers, not one."
        )
    pinned = request.pinned
    if pinned:
        lines.append(
            "- Pinned so far: "
            + "; ".join(f"{item.written} = {item.describe()}" for item in pinned)
            + "."
        )
    if request.unpinned:
        lines.append(
            "- Still open: "
            + "; ".join(f"“{item.unit}” ({', '.join(item.candidates)})" for item in request.unpinned)
            + "."
        )
    if market_lines:
        lines.append("")
        lines += market_lines
    if ppp_lines:
        lines.append("")
        lines += ppp_lines
    if inflation_lines:
        lines.append("")
        lines += inflation_lines
    lines.append("")

    missing = _missing_lines(request, have_market=have_market, have_ppp=have_ppp)
    if missing:
        lines.append("Cannot determine without")
        lines += missing
        lines.append("")

    lines.append("If using only provided/supplied rates")
    if have_market or have_ppp or inflation_lines:
        lines.append(
            "- Every figure above is arithmetic on the numbers in this thread. Nothing was checked "
            "against a market, and nothing was recalled from training data."
        )
    if not have_market:
        lines.append(
            "- Give me the cross rates — “1 USD = <rate> DKK”, one line per pair — and I will rank "
            "exchange value exactly, labelled as your numbers rather than as a market check."
        )
    if not have_ppp and ASK_PURCHASING_POWER in request.asks:
        lines.append(
            "- Give me PPP conversion factors or the local cost of one comparable basket per "
            "country, and I will rank purchasing power from those figures alone."
        )
    if ASK_HOLD in request.asks and not request.per_code_inflation:
        lines.append(
            "- Give me each currency's inflation and nominal interest rate plus your horizon, and "
            "I will work the real return per currency and show the arithmetic."
        )
    return "\n".join(lines).strip()


def currency_grounding_facts(
    request: CurrencyComparisonRequest,
) -> tuple[dict[str, Any], ...]:
    """What this lane knows, typed, for a turn it is NOT going to answer.

    Declining must not mean discarding. When a mixed turn is handed on — or when any other lane
    takes it — the ISO ambiguity is still a fact the runtime established deterministically, and a
    model left to rediscover it is a model left to guess at it. That guess is the reported defect.
    So the reading travels as data: which symbol, which candidates, what pinned it if anything.
    """

    facts: list[dict[str, Any]] = []
    for item in request.currencies:
        entry: dict[str, Any] = {
            "written": item.written,
            "unit": item.unit,
            "amount": str(item.amount) if item.amount is not None else "",
            "resolved": item.code,
            "candidates": list(item.candidates),
            "basis": item.basis,
        }
        facts.append(entry)
    for broken in request.contradictions:
        facts.append(
            {
                "written": broken.written,
                "unit": broken.child,
                "amount": "",
                "resolved": "",
                "candidates": [broken.actual_parent, broken.named_parent],
                "basis": f"contradiction:{broken.domain}",
            }
        )
    return tuple(facts)


def render_currency_grounding_note(request: CurrencyComparisonRequest) -> str:
    """The same facts as one instruction line, for a prompt channel that carries text."""

    lines: list[str] = []
    for item in request.currencies:
        if item.pinned:
            lines.append(f"{item.written} = {item.code} ({item.basis or 'named directly'})")
        else:
            lines.append(
                f"{item.written} is NOT determined — “{item.unit}” could be "
                f"{', '.join(item.candidates)}"
            )
    for broken in request.contradictions:
        lines.append(broken.statement())
    if not lines:
        return ""
    return (
        "Currency readings this runtime resolved deterministically for this turn:\n  "
        + "\n  ".join(lines)
        + "\nUse these exactly. Do not pick a currency for an undetermined symbol, and do not "
        "state an exchange rate, a PPP factor or an inflation figure — none is available here."
    )


__all__ = [
    "ASK_HOLD",
    "ASK_MARKET_VALUE",
    "ASK_PURCHASING_POWER",
    "ComparedCurrency",
    "CurrencyComparisonRequest",
    "Invalidation",
    "apply_location_evidence",
    "comparison_followup",
    "covers_whole_turn",
    "currency_comparison_intent",
    "currency_grounding_facts",
    "invalidations",
    "parse_supplied_rates",
    "rate_spans",
    "render_currency_comparison",
    "render_currency_grounding_note",
    "scan_amounts",
    "uncovered_residue",
]
