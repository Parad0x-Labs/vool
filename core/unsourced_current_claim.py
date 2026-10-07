"""A current value may not be stated unless this turn actually observed one.

The defect this module exists to close
--------------------------------------
Measured live on c6eed761 (2026-08-14). A turn that needed a live reading, reaching the model with
no evidence, answered anyway -- and the prose was entirely plausible::

    "The current temperature in Tromso is 9 C."          <- the fetch, one turn earlier, said 10 C
    "10 C. According to wttr.in, as of 08:30 AM."        <- wttr.in was never contacted this turn
    "The current water temperature in the North Sea is around 18-20 C."   <- web_calls: 0

`core.live_data_continuation` closed the ROUTE that produced these: a bare nudge now re-enters the
live-data lane and fetches. It did not close the CLASS. Any path that leaves a live question with
the model and no observation can still produce a confident number, and the runtime had nothing that
would notice.

The invariant
-------------
Three things have to be true together before this module objects:

  1. **The turn required a current observation.** Decided by
     `core.execution_requirements.requirements_for(...).current_information_required` -- the repo's
     existing authority on that question, which already understands price/weather recognizers,
     escalation rules and (since the continuation repair) bare follow-ups. It is deliberately NOT
     re-derived here, and it is NOT read off the answer's wording: scanning a reply for "current"
     or "right now" is the phrase list this design exists to avoid, and it would fire on
     "water freezes at 0 C at standard pressure" the moment someone phrased the question badly.
  2. **The turn produced no authoritative evidence.** See `turn_has_current_evidence`; web
     retrieval is one source among several, not the definition.
  3. **The answer asserts an observation** -- a measured value, or an attribution to a source.
     Both tested STRUCTURALLY (a number bound to a unit; a URL, link or domain token), never by
     matching sentences.

Miss any one and this module is silent. That ordering matters: conjunct 1 is what keeps static and
historical facts safe. "Water freezes at 0 C" is a specific value bound to a unit and would trip
conjunct 3 on its own -- it never gets there, because the question that produced it required no
current observation.

What it does NOT do
-------------------
It does not judge whether a number is right, does not ban numbers, does not ban attribution, and
does not try to separate the supported half of an answer from the unsupported half. When it fires,
the whole answer is withdrawn in favour of a truthful statement of what could not be verified: an
answer carrying a fabricated current value has already told the user something false, and salvaging
the correct sentence beside it is not worth shipping the wrong one. Failing toward "I could not
check" is the only direction that cannot mislead.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: A number bound to something that makes it a MEASUREMENT rather than a quantity: a unit, a
#: currency, a percentage, a degree sign. "3 files" and "eleven maintainers" are counts and do not
#: match; "9 C", "$64,102", "23 knots", "93%", "402 km" do.
#:
#: This IS an enumeration, and that is a deliberate exception to the "no unbounded list" rule this
#: repo applies elsewhere -- the members are drawn from FORMAL STANDARDS (SI base and common derived
#: units, ISO 4217 currency codes) rather than from the vocabulary of any reported failure. Formal
#: membership is exactly the case where an exact list is authoritative domain knowledge instead of
#: benchmark patching.
#:
#: Its limit is stated plainly rather than hidden: a measurement expressed in a unit outside these
#: standards -- a domain-specific or informal one -- is not recognised, and such an answer reaches
#: the user unguarded on the value arm. The source-attribution arm below is fully structural and
#: has no such limit. Found while proving this module: "402 kilometers" went unmatched until the SI
#: set was adopted, which is what motivated grounding the list in a standard.
_UNIT_WORDS = (
    # SI base + common derived, and the everyday spellings of each.
    r"m|km|cm|mm|nm|um|metres?|meters?|kilometres?|kilometers?|centimetres?|centimeters?|"
    r"g|kg|mg|t|grams?|kilograms?|tonnes?|tons?|lbs?|pounds?|oz|ounces?|"
    r"s|ms|h|hr|hrs|hours?|min|mins|minutes?|seconds?|days?|weeks?|months?|years?|"
    r"a|amps?|v|volts?|w|kw|mw|watts?|j|kj|joules?|n|newtons?|"
    r"pa|hpa|kpa|mbar|bar|psi|"
    r"l|ml|litres?|liters?|gal|gallons?|"
    r"hz|khz|mhz|ghz|b|kb|mb|gb|tb|bytes?|bits?|"
    r"c|f|k|celsius|fahrenheit|kelvin|deg|degrees?|"
    r"knots?|kmh|km/h|mph|m/s|kn|"
    # ISO 4217 codes in circulation here, plus their everyday names.
    r"usd|eur|gbp|jpy|chf|sek|nok|dkk|pln|cad|aud|nzd|czk|huf|isk|"
    r"dollars?|euros?|cents?|kronor|kroner|"
    r"pts?|points?|goals?|runs?|sets?|games?|%"
)
#: Word-form scale prefixes may sit between the number and its unit ("1.4 million litres"). Also a
#: formal set rather than an open list.
_SCALE_WORDS = r"(?:thousand|million|billion|trillion)"
_MEASURED_VALUE_RE = re.compile(
    r"(?:[$€£¥]\s?\d[\d,._]*)"
    r"|(?:\d[\d,._]*\s?(?:%|°|℃|℉))"
    r"|(?:\d[\d,._]*\s?(?:" + _SCALE_WORDS + r"\s+)?°?\s?(?:" + _UNIT_WORDS + r")\b)",
    re.IGNORECASE,
)

#: The PROSE-SAFE members of the same SI/ISO vocabulary: every unit spelling of TWO or more
#: characters, plus the degree sign. The single-letter arms (c, f, k, g, t, s, h, ...) are
#: deliberately absent: on the whole-answer path (guarded above) `requires_current` has already
#: narrowed the turn to a live ask, but the reply-side live-claim guard convicts on ANY
#: zero-observation turn, where "section 3 c" or "plan b" prose would be a false conviction.
#: Currency and percent stay out too: the live-claim guard's own price/percent kinds own those
#: shapes with their full anchor set. Same formal-standard grounding as `_UNIT_WORDS`; this is
#: the same vocabulary read at a stricter tolerance, not a second opinion.
_PROSE_SAFE_UNIT_WORDS = (
    r"km|cm|mm|nm|um|metres?|meters?|kilometres?|kilometers?|centimetres?|centimeters?|"
    r"kg|mg|grams?|kilograms?|tonnes?|tons?|lbs?|pounds?|ounces?|"
    r"ms|hr|hrs|hours?|min|mins|minutes?|seconds?|days?|weeks?|months?|years?|"
    r"amps?|volts?|kw|mw|watts?|kj|joules?|newtons?|"
    r"hpa|kpa|mbar|bar|psi|"
    r"ml|litres?|liters?|gallons?|"
    r"khz|mhz|ghz|kb|mb|gb|tb|bytes?|bits?|"
    r"celsius|fahrenheit|kelvin|degrees?|"
    r"knots?|kmh|km/h|mph|"
    r"usd|eur|gbp|jpy|chf|sek|nok|dkk|pln|cad|aud|nzd|czk|huf|isk|"
    r"dollars?|euros?|cents?|kronor|kroner|"
    r"pts?|points?|goals?|runs?|sets?|games?|percent"
)
#: The word "percent" (and the symbol arm below) sits in the prose-safe set for the
#: reply-side measured-quantity kind, which additionally requires an EXPLICIT nowness
#: anchor ("currently holds 68 percent of capacity"): the unsigned-percent exclusion that
#: the percent-change kind needs for math answers does not apply once the reply itself
#: claims now. The symbol form keeps the same strong-anchor protection for the same reason.
_PROSE_SAFE_MEASURED_RE = re.compile(
    r"\d[\d,._]*\s?(?:" + _SCALE_WORDS + r"\s+)?°?\s?(?:" + _PROSE_SAFE_UNIT_WORDS + r")\b"
    r"|\d[\d,._]*\s?%",
    re.IGNORECASE,
)

#: The question-side ask shape this module's own failure family arrives in: a question whose
#: head asks for an AMOUNT or COUNT of something ("how much gravel is in the yard", "how many
#: tonnes ... "). Closed interrogative heads only -- never topic nouns -- so it cannot become a
#: subject list. Together with a CURRENT anchor (the temporal authority's decision, not a word
#: list here) this is what gives the requirements authority jurisdiction over a present-anchored
#: quantity ask on a plain chat route (F15-05: "How much graded gravel is in the quarry yard at
#: the moment?" classified DIRECT/stable_knowledge and the invented 12,400-tonne reading shipped
#: unguarded; the same question with "right now" was already guarded -- a marker drift, not a
#: semantic difference).
_AMOUNT_ASK_HEAD_RE = re.compile(
    r"\bhow\s+(?:much|many|full|heavy|deep|tall|high)\b"
    r"|\bwhat(?:'s|’s|\s+is|\s+are)\s+the\s+(?:amount|level|total|quantity|volume|weight|number|count)\b",
    re.IGNORECASE,
)


def prose_safe_measured_value_matches(text: Any) -> tuple[str, ...]:
    """The prose-safe measured-value strings in `text`, normalized for equality checks.

    Used by the reply-side live-claim guard (its value shapes) and by the user-fact
    exemption (does the value the answer asserts appear in the user's own words this
    turn?). Normalized by collapsing whitespace so "12,400 tonnes" == "12,400  tonnes".
    """

    body = str(text or "")
    return tuple(
        " ".join(match.group(0).split())
        for match in _PROSE_SAFE_MEASURED_RE.finditer(body)
    )


#: SI dimension families over the prose-safe vocabulary. Formal-standard knowledge (the
#: SI organizes units by dimension), used for ONE thing: recognizing that a reply value
#: is a UNIT CONVERSION of a value the user supplied this turn ("3 tonnes" -> "3,000
#: kilograms", the calculation class the user-fact exemption exists to retain). The
#: predicate does not verify conversion CORRECTNESS (12.4 tonnes -> "12,400 tonnes" is
#: wrong math but scales like a conversion); its job is separating conversion-shaped
#: replies from invented readings, and a model with its arithmetic wrong is a different
#: defect than a model inventing a live reading.
_DIMENSION_FAMILIES: tuple[frozenset[str], ...] = (
    frozenset({"g", "kg", "mg", "grams", "kilograms", "tonnes", "tons", "lbs", "pounds", "ounces"}),
    frozenset({"km", "cm", "mm", "nm", "um", "metres", "meters", "kilometres", "kilometers",
               "centimetres", "centimeters"}),
    frozenset({"ml", "litres", "liters", "gallons"}),
    frozenset({"ms", "min", "mins", "minutes", "seconds", "hr", "hrs", "hours",
               "days", "weeks", "months", "years"}),
    frozenset({"kb", "mb", "gb", "tb", "bytes", "bits"}),
    frozenset({"kj", "joules"}),
    frozenset({"kw", "mw", "watts"}),
    frozenset({"hpa", "kpa", "mbar", "bar", "psi"}),
    frozenset({"usd", "eur", "gbp", "jpy", "chf", "sek", "nok", "dkk", "pln", "cad",
               "aud", "nzd", "czk", "huf", "isk", "dollars", "euros", "cents",
               "kronor", "kroner"}),
)


def _number_and_unit(match_text: str) -> tuple[float | None, str]:
    """Split a prose-safe match ("3,000 kilograms") into its number and unit."""

    parts = " ".join(str(match_text or "").split()).split()
    if not parts:
        return None, ""
    number: float | None = None
    try:
        number = float(parts[0].replace(",", ""))
    except ValueError:
        scale = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}
        if len(parts) >= 2 and parts[0].lower() in scale:
            try:
                number = float(parts[1].replace(",", "")) * scale[parts[0].lower()]
            except ValueError:
                number = None
    unit = parts[-1].lower().lstrip("°") if parts else ""
    return number, unit


def _same_dimension(unit_a: str, unit_b: str) -> bool:
    return any(unit_a in family and unit_b in family for family in _DIMENSION_FAMILIES)


def reply_match_is_user_supplied(match_text: str, user_turn_text: Any) -> bool:
    """Whether a reply's measured value is user material: stated verbatim this turn, or
    a power-of-ten conversion of a stated value in the same SI dimension.

    "I have 3 tonnes at the moment -- how many kilos is that?" answered "That is 3,000
    kilograms" is a calculation on the user's own number, not a live reading. The scale
    must be an exact power of ten and the units must share an SI dimension; currency
    families are included for exact echoes only in spirit -- a currency CONVERSION needs
    a live rate, and power-of-ten scaling between currencies (dollars->cents) is the
    only shape that qualifies.
    """

    user_text = str(user_turn_text or "")
    if not user_text.strip():
        return False
    import math

    normalized = " ".join(str(match_text or "").split())
    if normalized in set(prose_safe_measured_value_matches(user_text)):
        return True
    number_b, unit_b = _number_and_unit(normalized)
    if number_b is None or number_b == 0:
        return False
    for user_match in prose_safe_measured_value_matches(user_text):
        number_a, unit_a = _number_and_unit(user_match)
        if number_a is None or number_a == 0:
            continue
        if not _same_dimension(unit_a, unit_b):
            continue
        log_ratio = math.log10(number_b / number_a)
        if abs(log_ratio - round(log_ratio)) < 1e-9 and abs(round(log_ratio)) <= 12:
            return True
    return False


def question_asks_for_measured_amount(text: Any) -> bool:
    """Whether the ask-shape is an amount/count question about something in the world."""

    return bool(_AMOUNT_ASK_HEAD_RE.search(str(text or "")))


# --- Recorded personal state vs a same-turn sensor observation ---------------------------------
#
# Measured 2026-09-30 (LongMemEval case q48c0fce9504f8410): the capsule held two dated
# user-stated records -- "my highest score in Ticket to Ride - 132 points" (2023-05-25)
# and "my highest score so far is 124 points" (2023-05-23) -- the reader answered the
# "current highest score" question correctly from them, and the reply-side live-claim
# seam withdrew the 132 sentence as an unobserved current measured quantity.
#
# The distinction this section owns is SEMANTIC, not lexical:
#
#   * A RECORD is a value accumulated over time under a superlative ("highest score",
#     "best time", "longest streak"). Its CURRENT value is defined as the latest
#     admitted record entry; a later entry supersedes an earlier one; and no new entry
#     can exist without the user acting again. Restating the latest record is RETENTION
#     of recorded state, not a fabricated observation.
#   * A VOLATILE quantity (a temperature reading, a market price, a balance, telemetry)
#     changes without the user acting; its last recorded value is a PAST observation
#     that goes stale immediately, and "current <quantity>" requires a same-turn
#     observation. Dated memory stays a non-channel for those, exactly as before.
#
# The discriminator is structural on both edges: a closed grammatical class of
# superlative record modifiers (not a domain or noun list), and value-for-value
# admitted support with supersession by statement date. A reply that presents a
# number as CURRENT without record vocabulary is a live-reading claim regardless of
# what memory holds, and a record value that a later admitted record supersedes is
# not the current record. The module's existing law is unchanged: dated memory is
# still NOT a current-observation channel for `turn_has_current_evidence`.
_RECORD_MODIFIER_RE = re.compile(
    r"\b(?:highest|lowest|best|worst|most|fewest|least|longest|shortest|largest|smallest|"
    r"biggest|greatest|quickest|fastest|slowest|heaviest|lightest|top|record|"
    r"personal\s+best|maximum|minimum)\b"
    # The productive English superlative arm: ANY regular -est form is a record
    # modifier by GRAMMAR (deepest, widest, tallest, ...), not by enumeration —
    # the law must not require the user or the model to pick a particular
    # synonym (owner-review R06 principle; found by the fresh acceptance pack:
    # "deepest" scored no record vocabulary at all). The few -est words that
    # are not superlatives are excluded by a small closed stoplist. Containment:
    # record vocabulary alone retains nothing — an admitted same-subject
    # supported value that is also the record's current value is still required.
    r"|\b(?!interest\b|earnest\b|request\b|harvest\b|contest\b|honest\b|forest\b|priest\b)[a-z]{3,}est\b",
    re.IGNORECASE,
)
#: Capsule fact-line role prefix: only the user's OWN admitted statement establishes a
#: personal record. Assistant echoes, quotes and third-party text never do (the
#: source-authority law: quoted/source-only content is not user-owned profile state).
_USER_SAID_LINE_PREFIX = "- user said"
#: Statement dates inside a provenance suffix, ISO ("2023-05-25") and slashed
#: ("2023/05/25"). Dates are read ONLY from the canonical provenance suffix —
#: never from the fact text, where a planned date, a quoted date or a deadline
#: is prose, not the statement time (owner-review R04: a replacement planned
#: for 2028-03-12 must not make a line stated 2026-09-02 the newest record).
_STATEMENT_DATE_RE = re.compile(r"(\d{4})[-/](\d{2})[-/](\d{2})")
#: Generic tokens that cannot bind two record statements to the same subject: the
#: record vocabulary itself, unit words, and function words. Anything else
#: (a game name, a mountain, an instrument) is subject-bearing.
_GENERIC_RECORD_TOKENS = frozenset(
    """
    best worst highest lowest most fewest least longest shortest largest smallest biggest
    greatest quickest fastest slowest heaviest lightest top record records maximum minimum
    personal score scores point points goal goals run runs set sets game games time times
    total totals count counts level levels your yours current currently now present latest
    earlier previous prior just only been have has had having with from this that these
    those said told noted mentioned minutes minute hours hour seconds days weeks months
    years
    """.split()
)
_RECORD_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
#: Negation scope inside a fact line: a value is NEGATED evidence when a negation
#: cue stands between the value's own clause boundary and the value ("is not 29
#: kilometres", "41 points, not 47 points"). A negated value never supports a
#: claim and never remains the record's current value — the digits occurring in
#: a user-said line are not positive evidence by themselves (owner-review R02).
_NEGATION_CUE_RE = re.compile(r"\b(?:not|never|no\s+longer)\b|n't\b", re.IGNORECASE)
#: Clause boundaries used to scope negation inside a fact line (the same clause
#: idea `core.model_output_guard._VALUE_CLAUSE_BOUNDARY_RE` applies to reply
#: clauses, read at fact-line tolerance).
_LINE_CLAUSE_BOUNDARY_RE = re.compile(
    r"[,;()]|(?<=[.!?])\s+|\bbut\b|\byet\b|\bhowever\b|\band\b", re.IGNORECASE
)

# New asserted subjects split the value ledger; a bare anaphoric continuation
# inherits its preceding subject. Commas inside grouped numbers are not splits.
_RECORD_CLAUSE_SPLIT_RE = re.compile(
    r"(?<=[.!?])\s+|[;\n]|(?:\b(?:and|but|while|whereas)\s+|,\s*)"
    r"(?=(?:my|your|our|the|his|her)\s)",
    re.IGNORECASE,
)
_RECORD_ANAPHOR_RE = re.compile(r"^(?:but\s+)?(?:it|that|this)\s+(?:is|was|has|had)\b", re.IGNORECASE)
_APOSTROPHE_ANALYSIS = str.maketrans({"‘": "'", "’": "'", "ʼ": "'", "＇": "'"})
_RECORD_QUOTE_RE = re.compile(r'''(?<!\w)(?:"[^"\n]*"|“[^”\n]*”|'[^'\n]*'|‘[^’\n]*’)''')


def _capsule_fact_lines(admitted_evidence: Any) -> list[str]:
    """The user-said fact lines of an admitted capsule block, in order."""

    return [
        line.strip()
        for line in str(admitted_evidence or "").splitlines()
        if line.strip().startswith(_USER_SAID_LINE_PREFIX)
    ]


def _bare_statement_text(line: str) -> str:
    """The fact line minus its canonical provenance suffix.

    The suffix grammar has one owner (`core.context_retrieval._PROVENANCE_SUFFIX_RE`:
    "; "-joined ``stated: …`` / ``recorded: …`` parts in a trailing parenthesis);
    importing it here keeps this law and the capsule builder on the same grammar
    instead of a second copy that can drift.
    """

    from core.context_retrieval import _PROVENANCE_SUFFIX_RE

    return _PROVENANCE_SUFFIX_RE.sub("", str(line or ""), count=1)


def _line_provenance_date(line: str) -> str:
    """The line's canonical recency date ("" when unknown).

    The latest source-supported statement time in the provenance suffix; the
    system recorded time only when no statement time is known. Dates inside the
    fact text itself are deliberately not consulted: a planned date, a quoted
    date or a deadline in prose is not the statement time, and unknown time
    stays unknown rather than borrowing one (owner-review R04/R08).
    """

    from core.context_retrieval import _PROVENANCE_SUFFIX_RE

    match = _PROVENANCE_SUFFIX_RE.search(str(line or ""))
    if not match:
        return ""
    suffix = match.group(0)
    for role in ("stated", "recorded"):
        dates = [
            f"{a}-{b}-{c}"
            for a, b, c in re.findall(
                role + r":[^;()]*?" + _STATEMENT_DATE_RE.pattern, suffix
            )
        ]
        if dates:
            return max(dates)
    return ""


def _value_is_negated(bare_line: str, value_start: int) -> bool:
    """Whether the value occurrence at ``value_start`` sits in a negation scope:
    a negation cue between its own clause boundary and the value."""

    segment_start = 0
    for m in _LINE_CLAUSE_BOUNDARY_RE.finditer(bare_line, 0, value_start):
        segment_start = m.end()
    return bool(_NEGATION_CUE_RE.search(
        bare_line[segment_start:value_start].translate(_APOSTROPHE_ANALYSIS)
    ))


def _distinctive_subject_tokens(text: str) -> set[str]:
    """Subject-bearing tokens of a record statement, AFTER removing its measured
    values and provenance: a shared unit ("kilograms") or a shared number is not
    subject identity (owner-review R01: a winch-lift record in kilograms must not
    bind to a clay-haul claim)."""

    from core.temporal_selection import slot_signature, _stem

    bare = _bare_statement_text(str(text or ""))
    # Subject identity precedes this assertion's value, not arbitrary prose
    # following it. Use the temporal owner's tokenizer (including short names
    # and attribute tokens), rather than the old four-letter overlap heuristic.
    match = _PROSE_SAFE_MEASURED_RE.search(bare)
    if match:
        bare = bare[:match.start()]
    bare = _RECORD_MODIFIER_RE.sub(" ", bare)
    generic = {_stem(token) for token in _GENERIC_RECORD_TOKENS} | {"user", "correction"}
    return {
        token for token in slot_signature(bare.translate(_APOSTROPHE_ANALYSIS))
        if token not in generic
    }


def _same_record_subject(claim_text: str, line_text: str) -> bool:
    """Whether two record statements are about the same subject.

    Subject-bearing tokens must overlap; when either side carries NO distinctive
    token ("my best so far is 124 points" names no subject), the statements are
    treated as competing -- the conservative direction for supersession.
    """

    claim_tokens = _distinctive_subject_tokens(claim_text)
    line_tokens = _distinctive_subject_tokens(line_text)
    if not claim_tokens or not line_tokens:
        return True
    return claim_tokens <= line_tokens


#: The lower-is-better arm of the record vocabulary: a superlative whose extremum is
#: the MINIMUM of the admitted record entries. Everything else in the record
#: vocabulary (highest, most, longest, ...) is read as higher-is-better unless the
#: sentence itself names the lower-is-better direction — or names no direction at
#: all while measuring a DURATION: a "best time" is the shortest time (a personal
#: best over 8 minutes improving to 6 must survive without the user adding
#: "shortest" or the reply choosing a synonym; owner-review R06).
_LOWER_BETTER_RECORD_RE = re.compile(
    r"\b(?:lowest|least|fewest|smallest|shortest|quickest|fastest|lightest|minimum)\b",
    re.IGNORECASE,
)
_DIRECTIONLESS_RECORD_RE = re.compile(
    r"\b(?:best|record|personal\s+best|top)\b", re.IGNORECASE
)


def _record_direction(clean_sentence: str, unit: str) -> str:
    """How a record's extremum reads: "min" when lower is better."""

    if _LOWER_BETTER_RECORD_RE.search(clean_sentence):
        return "min"
    if _DIRECTIONLESS_RECORD_RE.search(clean_sentence) and _same_dimension(unit, "minutes"):
        return "min"
    return "max"


def _record_assertions(admitted_evidence: Any) -> list[dict[str, Any]]:
    """Value occurrences with their own assertion subject, polarity and time.

    A fact line is a carrier, not one undifferentiated authority pool. Only a
    record assertion or its immediate anaphoric continuation contributes values.
    Incidental mentions in a following sentence cannot become record entries.
    """
    from core.temporal_selection import carries_undo_marker
    from core.memory.admission import (
        classify_user_text, _FIRST_PERSON_POSSESSIVE_RE,
        _THIRD_PERSON_POSSESSIVE_RE, _normalize_possessive_typography,
        _quote_occupies_value_slot,
    )
    from core.context_retrieval import _user_owned_statement_body

    assertions: list[dict[str, Any]] = []
    for line_index, line in enumerate(_capsule_fact_lines(admitted_evidence)):
        bare = _bare_statement_text(line)
        bare = re.sub(r"^- user said[^:]*:\s*", "", bare)
        if not _user_owned_statement_body(bare):
            continue
        # A user-role source quotation is retained history, not the user's
        # asserted record. Reuse admission's source/hypothetical boundary.
        bare = classify_user_text(bare).authored_text
        # Mixed messages can contain both an owned assertion and an incidental
        # quotation. Mask each quote independently; only an owned scalar value
        # in the record's declaration slot is evidence ("my best is '39 points'").
        # The analysis mask preserves offsets and never modifies stored text.
        for quote in list(_RECORD_QUOTE_RE.finditer(bare))[::-1]:
            prefix = _RECORD_SENTENCE_SPLIT_RE.split(bare[:quote.start()])[-1]
            scalar = _PROSE_SAFE_MEASURED_RE.fullmatch(quote.group(0)[1:-1].strip())
            if not (scalar and _RECORD_MODIFIER_RE.search(prefix)
                    and _quote_occupies_value_slot(prefix)):
                bare = bare[:quote.start()] + " " * len(quote.group(0)) + bare[quote.end():]
        inherited_subject: set[str] | None = None
        for clause in _RECORD_CLAUSE_SPLIT_RE.split(bare):
            clause = clause.strip()
            matches = list(_PROSE_SAFE_MEASURED_RE.finditer(clause))
            if not matches:
                continue
            if _RECORD_MODIFIER_RE.search(clause):
                prefix = _normalize_possessive_typography(clause[:matches[0].start()])
                first = [m.start() for m in _FIRST_PERSON_POSSESSIVE_RE.finditer(prefix)]
                third = [m.start() for m in _THIRD_PERSON_POSSESSIVE_RE.finditer(prefix)]
                # The same rightmost-possessor law as admission: mentioning
                # "my brother's record" does not make that record mine.
                if max(first, default=-1) <= max(third, default=-1):
                    inherited_subject = None
                    continue
                subject = _distinctive_subject_tokens(clause)
                inherited_subject = subject
            elif inherited_subject is not None and _RECORD_ANAPHOR_RE.search(clause):
                subject = inherited_subject
            else:
                inherited_subject = None
                continue
            correction = carries_undo_marker(clause)
            for match in matches:
                value = " ".join(match.group(0).split())
                number, unit = _number_and_unit(value)
                assertions.append({
                    "subject": subject, "number": number, "unit": unit,
                    "negated": _value_is_negated(clause, match.start()),
                    "date": _line_provenance_date(line),
                    "correction": correction, "line": line_index,
                })
    return assertions


def recorded_state_retention(
    reply_text: Any, admitted_evidence: Any, question_text: Any = "",
) -> bool:
    """Retain only supported current records, never a new volatile observation.

    Each claim binds to its own asserted subject and unit. Canonical provenance
    orders entries; a later affirmation can reinstate an older negated value.
    Within an unordered date bucket an explicit correction controls, followed
    by the record's extremum fallback. Ambiguous conflicts refuse retention.
    The caller still restricts this exemption to general measured quantities.
    """
    body = str(reply_text or "")
    if not body.strip() or answer_attributes_a_source(body):
        return False
    assertions = _record_assertions(admitted_evidence)
    if not assertions:
        return False
    # The record frame may live in the QUESTION ("What is my current highest score in
    # Ticket to Ride?") while a concise reply names only the value ("132 points, set on
    # 25 May"). The frame is the question-answer pair, not one reply clause: a reply
    # clause without record vocabulary keeps the question's frame and binds to the record
    # assertion through the question's own distinctive tokens. Measured 2026-10-06 on the
    # official LME run 1 (q48c0fce9504f8410): the reply "132 points (set on 2023/05/25)"
    # was withdrawn as an unobserved current reading although the admitted capsule carried
    # the user's own record statement with that value.
    question_frame = str(question_text or "")
    frame_subject: set[str] | None = (
        _distinctive_subject_tokens(question_frame)
        if _RECORD_MODIFIER_RE.search(question_frame)
        else None
    )
    checked = False
    for sentence in _RECORD_SENTENCE_SPLIT_RE.split(body):
        for clause in _RECORD_CLAUSE_SPLIT_RE.split(sentence):
            matches = list(_PROSE_SAFE_MEASURED_RE.finditer(clause))
            if not matches:
                continue
            checked = True
            if _RECORD_MODIFIER_RE.search(clause):
                subject = _distinctive_subject_tokens(clause)

                def _binds(a: dict[str, Any], _s: set[str] = subject) -> bool:
                    return (_s <= a["subject"]) if _s else not a["subject"]
            elif frame_subject is not None:
                # Question-framed reply: the value-only clause binds through any
                # distinctive question token the record assertion shares. The reply's
                # own tokens cannot narrow a frame it does not restate.
                subject = frame_subject

                def _binds(a: dict[str, Any], _s: set[str] = subject) -> bool:
                    return bool(_s & a["subject"]) if _s else not a["subject"]
            else:
                return False
            for match in matches:
                number, unit = _number_and_unit(match.group(0))
                candidates = [a for a in assertions
                              if a["unit"] == unit and _binds(a)]
                if not candidates or number is None:
                    return False
                # Negations are events, not permanent vetoes: later positive
                # ownership of the same value wins over an earlier denial.
                positives = []
                for a in candidates:
                    if a["negated"]:
                        continue
                    withdrawals = [n for n in candidates if n["negated"]
                                   and n["number"] == a["number"]]
                    if any(n["date"] >= a["date"] for n in withdrawals):
                        continue
                    positives.append(a)
                if not positives:
                    return False
                dates = {a["date"] for a in positives if a["date"]}
                if len(dates) > 1:
                    positives = [a for a in positives if a["date"] == max(dates)]
                corrections = [a for a in positives if a["correction"]]
                if corrections:
                    # Capsule ranking order is not source chronology. Multiple
                    # contradictory same-date corrections remain ambiguous.
                    values = {a["number"] for a in corrections}
                    if len(values) != 1 or number not in values:
                        return False
                else:
                    values = [a["number"] for a in positives if a["number"] is not None]
                    if not values:
                        return False
                    direction = _record_direction(clause, unit)
                    if number != (min(values) if direction == "min" else max(values)):
                        return False
    return checked


#: An attribution: a URL, a markdown link, or a bare host-shaped token. A source NAME in the answer
#: is a claim about where the value came from, and on a turn that contacted nothing it is a claim
#: the runtime cannot support.
_URL_RE = re.compile(r"https?://\S+|\[[^\]]+\]\([^)]+\)", re.IGNORECASE)
_HOST_RE = re.compile(
    r"\b[a-z0-9][a-z0-9-]*(?:\.[a-z0-9][a-z0-9-]*)+\.(?:in|com|org|net|io|ai|co|gov|edu|info)\b"
    r"|\b[a-z0-9][a-z0-9-]*\.(?:in|com|org|net|io|ai|co|gov|edu)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CurrentClaimVerdict:
    """Whether this answer states an observation the turn never made."""

    requires_current: bool = False
    has_evidence: bool = True
    asserts_measured_value: bool = False
    attributes_source: bool = False
    #: The answer already carries one of the runtime's own withdrawal notices and no
    #: residual live claim remains beside it: the turn's fabrication problem has been
    #: answered once, and a re-run of the guarded path (the draft-verification pass)
    #: must not convict the runtime's own honesty output again.
    declined_live_claims: bool = False
    # The reply's value claims bound to the user's own records in the admitted evidence
    # (core.evidence_kernel.claim_binder). Present only when binding was attempted.
    claim_binding: dict[str, Any] | None = None

    @property
    def qualify_only(self) -> bool:
        """v14.6: the binder bound the reply's values to the user's records and found none contradicted, but not all
        supported. The reply is not withdrawn; its unsupported or ambiguous values are marked (qualify_reply)."""
        b = self.claim_binding or {}
        return bool(self.requires_current and not self.has_evidence and not self.declined_live_claims and b.get("attempted") and b.get("qualifiable"))

    @property
    def unsupported(self) -> bool:
        return (
            self.requires_current
            and not self.has_evidence
            and (self.asserts_measured_value or self.attributes_source)
            and not self.declined_live_claims
            and not self.qualify_only
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "requires_current": self.requires_current,
            "has_evidence": self.has_evidence,
            "asserts_measured_value": self.asserts_measured_value,
            "attributes_source": self.attributes_source,
            "declined_live_claims": self.declined_live_claims,
            "unsupported": self.unsupported,
            "qualify_only": self.qualify_only,
            "claim_binding": self.claim_binding,
        }


def answer_asserts_a_measured_value(text: Any) -> bool:
    """A number bound to a unit, currency or percentage -- a reading, not a count."""

    return bool(_MEASURED_VALUE_RE.search(str(text or "")))


def answer_attributes_a_source(text: Any) -> bool:
    """A URL, markdown link, or host-shaped token presented as where the answer came from."""

    body = str(text or "")
    return bool(_URL_RE.search(body) or _HOST_RE.search(body))


def _turn_has_governed_web_receipt(context: Mapping[str, Any]) -> bool:
    """Whether some search on this turn left a successful, provider-named receipt.

    Fail-soft on import: this predicate narrows what counts as evidence, so an
    import failure here must not silently widen it back — but it must also not
    take down the guard. Returning False on error is the strict direction.
    """
    try:
        from core.retrieval_provenance import retrieval_supports_current_claims

        return bool(retrieval_supports_current_claims(dict(context)))
    except Exception:
        return False


def turn_has_current_evidence(
    *,
    notes: Sequence[Any] | None = None,
    session_id: str = "",
    turn_id: str = "",
    source_context: Mapping[str, Any] | None = None,
) -> bool:
    """Whether this turn actually observed something it could ground a current claim on.

    Web retrieval is ONE source. A deterministic tool observation (`machine.*`, a live-data plan
    subtask) and material the user supplied in this turn are equally authoritative -- the question
    is whether the runtime observed the world, not which door it used. A model call is never
    evidence: generating plausible prose about an external fact is the failure being guarded, not a
    substitute for having looked.

    Neither is a FAILED look. Until eca76ff9 every branch below answered from the presence of a
    record rather than its outcome, so the receipt of a weather lookup that returned nothing
    (`status='failed' source_count=0 failure_class='no observation returned'`) reported this turn as
    evidenced -- and `inspect_unsourced_current_claim`, which had already found a measured value and
    a source attribution in the answer, declined to convict on that one input. The success question
    is owned by `core.observation_evidence` and shared with
    `core.model_output_guard.turn_ran_observations` so the two predicates cannot drift.
    """

    from core.observation_evidence import channel_has_a_usable_observation, records_a_usable_observation

    context = dict(source_context or {})
    # THE RESCUE RULE. A web-derived note is a snippet somebody's search produced;
    # on its own it says nothing about whether that search was governed, or even
    # which provider ran. That was the hidden split: a typed tool reported
    # failure, a separate un-receipted search returned snippets anyway, and this
    # predicate accepted them — so the answer spoke as though the typed tool had
    # worked. A web note now counts only when the turn also holds a SUCCESSFUL
    # governed retrieval receipt naming a provider.
    #
    # Scoped to `web_derived` notes on purpose: a live quote, a deterministic
    # tool observation and material the user supplied are evidence by their own
    # route and are not touched. And scoped to callers that HAVE a turn context —
    # with none there is no account to consult, which is a fact about the
    # reader's position, exactly as `remote_fetch_scope_active` treats a zero.
    web_notes_need_a_receipt = bool(source_context) and not _turn_has_governed_web_receipt(context)

    for note in list(notes or []):
        if isinstance(note, Mapping):
            if not records_a_usable_observation(note):
                # A note that records its own failure is an attempt, not a reading.
                continue
            if web_notes_need_a_receipt and str(note.get("source_type") or "").strip() == "web_derived":
                continue
            if any(str(note.get(field) or "").strip() for field in ("summary", "live_quote", "result_title")):
                return True
        elif str(note or "").strip():
            return True

    if any(
        channel_has_a_usable_observation(context.get(key))
        for key in ("attachments", "user_material", "supplied_files", "media_attachments")
    ):
        return True
    for key in ("web_retrieval_receipts", "fresh_data_retrieval_receipts"):
        if channel_has_a_usable_observation(context.get(key)):
            return True

    clean_session = str(session_id or context.get("runtime_session_id") or context.get("session_id") or "").strip()
    clean_turn = str(turn_id or context.get("cancel_turn_id") or context.get("turn_id") or "").strip()
    if clean_session and clean_turn:
        try:
            from core import execution_records

            # `.ok` is the record's own outcome, and the rest of `execution_records` already reads it
            # this way (`records_for` callers at lines 325 and 363 both filter on it). This branch was
            # the one that did not.
            if any(entry.ok for entry in execution_records.records_for_turn(clean_session, clean_turn)):
                return True
        except Exception:
            return False
    # No session-wide fallback. This branch used to read `records_for(clean_session)` whenever the
    # turn id was missing, which let the PREVIOUS turn's successful fetch ground THIS turn's claim
    # -- session membership posing as evidence. A turn whose identity nobody stamped cannot prove
    # it observed the world, exactly as `records_for_turn` refuses an unattributed record: the
    # missing-id direction fails closed, and the durable channels above (this turn's own receipts,
    # notes and user material) remain the evidence for such a turn.
    return False


def inspect_unsourced_current_claim(
    *,
    answer: Any,
    requires_current: bool,
    notes: Sequence[Any] | None = None,
    session_id: str = "",
    turn_id: str = "",
    source_context: Mapping[str, Any] | None = None,
    user_turn_text: Any = "",
) -> CurrentClaimVerdict:
    """Whether `answer` states a current observation this turn has no standing to make.

    ``user_turn_text`` is the CURRENT turn's own user message. A value the user stated in
    this turn is a user-provided present fact, not an invention: "I have 3 tonnes of gravel
    at the moment -- how many kilos is that?" answered "That is 3,000 kg" is legitimate, and
    the exemption reads it structurally (the answer's prose-safe measured-value strings
    appear in the user's own words), not by phrase matching. A stored volatile
    reading is never a current observation. A personal record's current value
    is defined by admitted record assertions: that narrow law is shared with
    the final response seam, without changing ``turn_has_current_evidence``.
    """

    if not requires_current:
        # The single most important early return: everything static, historical or explanatory
        # leaves here untouched, whatever numbers or domains it happens to contain.
        return CurrentClaimVerdict(requires_current=False, has_evidence=True)

    has_evidence = turn_has_current_evidence(
        notes=notes, session_id=session_id, turn_id=turn_id, source_context=source_context
    )
    asserts_measured_value = answer_asserts_a_measured_value(answer)
    attributes_source = answer_attributes_a_source(answer)
    if not has_evidence and str(user_turn_text or "").strip():
        # The user-supplied present-fact exemption above. EVERY prose-safe value the
        # answer asserts must be user material (verbatim or a power-of-ten conversion in
        # the same SI dimension): one invented quantity beside an echoed one is still an
        # invented quantity.
        from core.unsourced_current_claim import reply_match_is_user_supplied

        answer_values = prose_safe_measured_value_matches(answer)
        if answer_values and all(
            reply_match_is_user_supplied(value, user_turn_text)
            for value in answer_values
        ):
            has_evidence = True

    if not has_evidence and not attributes_source:
        from core.bootstrap_context import admitted_capsule_evidence_text
        from core.model_output_guard import unobserved_live_value_claims

        kinds = set(unobserved_live_value_claims(
            str(answer or ""), user_turn_text=str(user_turn_text or "")
        ))
        if kinds == {"measured-quantity"} and recorded_state_retention(
            answer, admitted_capsule_evidence_text(source_context or {}),
            question_text=user_turn_text,
        ):
            has_evidence = True

    # A memory answer whose every value claim is stated in, or derived from
    # (sum, average, extremum, count, day count), the user's OWN records in the admitted evidence is
    # memory-sourced, not a live reading this turn failed to observe. Measured 2026-10-07 on a
    # held-out memory run: 19 correct purchase totals ("$283 total: shoes $140, lights $48, helmet $95")
    # were replaced by the notice below. The binder labels; it never rewrites. A live-world ask, an
    # assistant line, a stored market reading or an underivable value leave this branch untouched.
    claim_binding: dict[str, Any] | None = None
    if not has_evidence and not attributes_source:
        try:
            from core.bootstrap_context import admitted_capsule_evidence_text as _admitted_text
            from core.evidence_kernel.claim_binder import bind_claims as _bind_claims
            from core.evidence_kernel.claim_binder import qualify_reply as _qualify_reply

            from core.evidence_kernel.revocation import without_revoked as _without_revoked
            from core.evidence_kernel.snapshot import packet_facts_for as _packet_facts_for

            _admitted = _admitted_text(source_context or {}, session_id, question=str(user_turn_text or ""))
            # One memory revision: the packet the turn's evidence carries, never a later retrieval's.
            _packet_facts = _packet_facts_for(_admitted, str(session_id or ""))
            _evidence_text, _packet_facts = _without_revoked(_admitted, _packet_facts, str(session_id or ""))
            _binding = _bind_claims(
                question=user_turn_text, reply=answer,
                evidence_text=_evidence_text,
                packet_facts=_packet_facts,
            )
            claim_binding = _binding.as_dict()
            if _binding.all_supported:
                has_evidence = True
            elif _binding.qualifiable:
                claim_binding["qualified_text"] = _qualify_reply(answer, _binding)
            claim_binding["kernel_receipt"] = _kernel_claim_envelope(session_id, user_turn_text, answer, claim_binding, _packet_facts)
        except Exception:
            claim_binding = {"attempted": False, "reason": "binder_error"}
        if isinstance(source_context, dict):
            source_context["claim_binding"] = claim_binding

    # The runtime's own withdrawal notice, with no residual live claim beside it, is the
    # answered state of this defect -- not a fresh fabrication. The guarded block runs
    # more than once per turn on the served path (the draft-verification pass), and the
    # second pass measured on the S3 mixed turn convicted the split the first pass had
    # just produced, swapping "1) The 2019 survey counted 11,000 tonnes. <notice>" for
    # the whole-answer notice and destroying the supported half. A residual live claim
    # (a SECOND invented reading beside the notice) still convicts: the sentence-level
    # recognizer owns that question.
    declined = False
    if not has_evidence:
        from core.model_output_guard import (
            delivers_a_withdrawal_notice,
            unobserved_live_value_claims,
        )

        if delivers_a_withdrawal_notice(str(answer or "")) and not unobserved_live_value_claims(
            str(answer or ""), user_turn_text=str(user_turn_text or "")
        ):
            declined = True
    return CurrentClaimVerdict(
        requires_current=True,
        has_evidence=has_evidence,
        asserts_measured_value=answer_asserts_a_measured_value(answer),
        attributes_source=answer_attributes_a_source(answer),
        declined_live_claims=declined,
        claim_binding=claim_binding,
    )


#: The withdrawal on a question about this chat's own records: nothing was looked up live, so the notice says the
#: value is not in the records rather than that a current reading could not be obtained.
RECORDS_WITHDRAWAL_NOTICE = (
    "That is not mentioned in the records I have from our conversations, so I am not going to state one."
)


def unverified_current_answer(request_text: Any = "") -> str:
    """What to say instead. States the limit; promises nothing and names no value.

    Deliberately does not tell the user to "try again" or name a website: the turn already failed
    to observe, and inventing a recommendation is the same class of unearned confidence.
    """

    subject = " ".join(str(request_text or "").split())
    if len(subject) > 120:
        subject = subject[:117].rstrip() + "..."
    if subject:
        return (
            "I could not obtain a current reading for this on this turn, so I am not going to state "
            f"one. The request was: {subject}"
        )
    return "I could not obtain a current reading for this on this turn, so I am not going to state one."


__all__ = [
    "CurrentClaimVerdict",
    "answer_asserts_a_measured_value",
    "answer_attributes_a_source",
    "inspect_unsourced_current_claim",
    "prose_safe_measured_value_matches",
    "question_asks_for_measured_amount",
    "recorded_state_retention",
    "reply_match_is_user_supplied",
    "turn_has_current_evidence",
    "unverified_current_answer",
    "RECORDS_WITHDRAWAL_NOTICE",
]


def _claim_envelope_status(binding: Mapping[str, Any]) -> str:
    """The claim envelope's status vocabulary (v14.6 item 8): supported | contradicted | qualified | unsupported |
    not_attempted. 'supported' means every value claim is stated by, or derived from, an occurrence of the user's own
    records; it never means the statement is true. The receipt chain is ASKED -> DISCOVERED -> ACTIVATED -> DELIVERED ->
    ASSERTED -> SUPPORTED; there is no USED stage, because a model's use of an occurrence is not observable."""
    if not binding.get("attempted"):
        return "not_attempted"
    if binding.get("all_supported"):
        return "supported"
    if binding.get("contradicted"):
        return "contradicted"
    if binding.get("qualifiable"):
        return "qualified"
    return "unsupported"


def _kernel_claim_envelope(session_id: Any, question: Any, reply: Any, binding: dict[str, Any], packet_facts: Any) -> dict[str, Any] | None:
    """Claim envelope (vool.memory.claim.v1, VOOL_EVIDENCE_KERNEL=1): every value claim of the reply with its status
    and the evidence lines it bound to, by digest. Best-effort on the answer path; an unwritten envelope is recorded."""
    try:
        from core.evidence_kernel.receipts import EvidenceRef, issue, kernel_enabled, keyed_digest
    except Exception:
        return None
    if not kernel_enabled():
        return None
    try:
        claims = list(binding.get("claims") or [])
        refs = []
        for c in claims:
            for line in list(c.get("evidence") or [])[:4]:
                refs.append(EvidenceRef(occurrence_id="", digest=keyed_digest(str(line)), role="user", kind=str(c.get("status") or "")))
        env = issue(kind="vool.memory.claim.v1", session_id=str(session_id or ""), subject_type="claim_binding",
                    subject={"question_digest": keyed_digest(str(question or "")),
                             "reply_digest": keyed_digest(str(reply or "")), "claims": claims},
                    evidence_refs=refs, status=_claim_envelope_status(binding),
                    reason_codes=[str(binding.get("reason") or ""), f"claims:{len(claims)}", f"packet_facts:{len(list(packet_facts or []))}"], commit=False)
        return {"receipt_id": env.receipt_id, "status": env.status, "assurance": env.assurance}
    except Exception:
        return {"status": "error"}
