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

    @property
    def unsupported(self) -> bool:
        return (
            self.requires_current
            and not self.has_evidence
            and (self.asserts_measured_value or self.attributes_source)
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "requires_current": self.requires_current,
            "has_evidence": self.has_evidence,
            "asserts_measured_value": self.asserts_measured_value,
            "attributes_source": self.attributes_source,
            "unsupported": self.unsupported,
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
) -> CurrentClaimVerdict:
    """Whether `answer` states a current observation this turn has no standing to make."""

    if not requires_current:
        # The single most important early return: everything static, historical or explanatory
        # leaves here untouched, whatever numbers or domains it happens to contain.
        return CurrentClaimVerdict(requires_current=False, has_evidence=True)

    has_evidence = turn_has_current_evidence(
        notes=notes, session_id=session_id, turn_id=turn_id, source_context=source_context
    )
    return CurrentClaimVerdict(
        requires_current=True,
        has_evidence=has_evidence,
        asserts_measured_value=answer_asserts_a_measured_value(answer),
        attributes_source=answer_attributes_a_source(answer),
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
    "turn_has_current_evidence",
    "unverified_current_answer",
]
