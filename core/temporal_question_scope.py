"""The temporal reference of a question: is it asking about NOW, or about a past state?

Why this exists
----------------
Measured on the frozen base e821457d (LongMemEval packet, 2026-09-28): a saved
clinic-arrival question -- "What time did I reach the clinic on Monday?" -- was
claimed by the deterministic clock lane and answered "Current time is 13:40
EEST." (case q051a848). A tenure question -- "How long have I been working
before I started my current job at Google?" -- classified GROUNDED with
``current_information_required=True``, and the unsourced-current guard withdrew
a correct memory-backed "about 6 years" in favour of "I could not obtain a
current reading" (case q7db408b). Both turns asked about a PAST state and were
answered as if they had asked about the present.

The same defect class has two faces: a lane that should never claim the turn
claims it, and a requirement that should not apply to it applies. One decision
fixes both, so it lives in one place and is consumed everywhere the question's
temporal reference matters:

* ``core.agent_runtime.fast_paths_utility.date_time_fast_path`` -- the runtime
  clock answers questions ABOUT THE CLOCK. A past-event or habitual time
  question is out of contract for that lane whatever words it contains.
* ``core.execution_requirements.requirements_for`` -- a request anchored to a
  past state does not require a CURRENT observation. External evidence may
  still be required (GROUNDED); "current" is not "external".

What this is NOT
-----------------
Not a topic list. The signals are grammatical constructions -- past-tense
interrogatives, subordinate past-time clauses, calendar anchors, habitual
adverbs -- the same law ``core.task_router`` states for its ask-construction
recognizers: decide what the sentence ASKS FOR, never which nouns appear in
it. "When did I buy the boat?" and "When does the shop open?" share every
topic word that matters and differ only in tense.

Not a clock. This module never reads the wall clock and never resolves an
expression to an instant. Recording time, event time, effective time and the
question's as-of date are distinct, and only the QUESTION's own reference is
decided here; ``core.time_authority`` owns the actual clock.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any


class TimeReference(Enum):
    """Which point (or span) of time a question's subject is anchored to."""

    #: The present state of the world: "now", "today", "latest", "current".
    CURRENT = auto()
    #: A past event or past state: "when did I arrive", "what was it worth in 2019".
    PAST = auto()
    #: A repeated pattern across time, not any single reading: "what time do I
    #: usually leave". Habitual asks are answerable from history, never from a
    #: clock read or a single live observation.
    HABITUAL = auto()


@dataclass(frozen=True)
class QuestionTimeScope:
    """Every temporal reference found in the question, and whose past it is."""

    #: Discovery order, deduplicated. A question can carry several ("what did I
    #: pay then, and what is it worth now" is PAST + CURRENT); consumers decide
    #: what their own contract does with the combination.
    references: tuple[TimeReference, ...] = ()
    #: Whose past a PAST reference is about: "user" (the operator's own
    #: history), "assistant" (what this assistant said/did), "world" (any
    #: other past state), or "" when no PAST reference was found.
    past_subject: str = ""
    #: Offsets of CURRENT modifiers in an ordinary proposal's input argument,
    #: relative to the question or retrieval-eligible text, as stamped below.
    #: These identify background material,
    #: not a requested fresh observation. Unknown/multipart shapes retain the
    #: original conservative reading. No subject-noun exception list is used.
    current_input_spans: tuple[tuple[int, int], ...] = ()
    current_input_span_basis: str = "question"

    @property
    def current_input_only(self) -> bool:
        """The request's currency belongs only to a proposal's input frame."""
        return bool(self.current_input_spans) and not self.asks_current

    @property
    def asks_current(self) -> bool:
        return TimeReference.CURRENT in self.references

    @property
    def asks_past(self) -> bool:
        return TimeReference.PAST in self.references

    @property
    def asks_habitual(self) -> bool:
        return TimeReference.HABITUAL in self.references

    @property
    def past_only(self) -> bool:
        """True when the question is anchored to the past (or a habit) and NOT
        also to the present. This is the conjunction that withdraws a turn from
        current-clock/current-observation contracts: a mixed question keeps its
        current requirements for its current half."""
        return bool(self.references) and self.asks_current is False and (
            self.asks_past or self.asks_habitual
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "references": [ref.name for ref in self.references],
            "past_subject": self.past_subject,
            "past_only": self.past_only,
            "current_input_spans": [list(span) for span in self.current_input_spans],
            "current_input_only": self.current_input_only,
            "current_input_span_basis": self.current_input_span_basis,
        }


#: A past-tense INTERROGATIVE head. The ask itself is about something that
#: already happened: "when did ...", "what time did ...", "how much did ...",
#: "where was ...". The head must actually ask -- "what did you say the price
#: was" is included deliberately (assistant history), while "did" inside a
#: statement ("I did buy it") is not an interrogative head and does not match
#: because the question word is required in front of it.
#:
#: The was/were arm excludes the IMPERSONAL subject ("what time was it in
#: Berlin 2h ago"): that construction asks what the CLOCK read at some instant
#: -- pure clock arithmetic, owned by the date/time fast path -- while "what
#: time was the ferry" and "when was I told" ask about an event or a person
#: and are recall. The dummy subjects "it"/"there" are the closed class that
#: carries the arithmetic reading.
# Interrogatives may name their object before the past auxiliary:
# "which repair parts did you recommend" and "what target height was the
# archway" have the same tense as their object-free counterparts. This
# bounded noun-phrase arm cannot consume another auxiliary, cross punctuation
# or turn a future/advice predicate into a past one.
_WH_NOUN_PHRASE = (
    r"(?:what|which|how\s+(?:much|many))"
    r"(?:\s+(?!(?:did|was|were|had|have|has|do|does|is|are|am|"
    r"will|would|can|could|should|may|might)\b)[a-z][a-z'-]*){0,6}"
)
_PAST_QUESTION_HEAD = rf"(?:when|where|why|how\s+long|{_WH_NOUN_PHRASE})"
_PAST_INTERROGATIVE_RE = re.compile(
    rf"\b{_PAST_QUESTION_HEAD}\s+did\b"
    rf"|\b{_PAST_QUESTION_HEAD}\s+(?:was|were)\s+(?!it\b|there\b)",
    re.IGNORECASE,
)

#: An anaphoric pre-event value ask: "what was it before the autumn
#: revision?" names a superseding event and asks for the value it replaced.
#: The plain "what was it" is deliberately NOT a past cue (false positives on
#: "what was it called"), but anchored to a "before <event>" comparative it
#: unambiguously asks history.
_BEFORE_EVENT_PAST_RE = re.compile(
    r"\bwhat\s+was\s+(?:it|that|this|there|the\s+\w+)\s+before\b"
    r"|\bwas\s+(?:it|that|this)\s+(?:like\s+)?before\b"
    r"|\bbefore\s+the\s+\w+\s+"
    r"(?:revision|update|change|correction|amendment|switch|move)\b",
    re.IGNORECASE,
)

#: A first/second-person past-tense interrogative -- the ask is about the
#: OPERATOR's or the ASSISTANT's own past. "did I reach the clinic", "was I
#: told", "did you tell me". Impersonal subjects are absent by construction.
_PERSONAL_PAST_INTERROGATIVE_RE = re.compile(
    rf"\b{_PAST_QUESTION_HEAD}\s+(?:did|was|were|had)\s+(?:i|we|you|he|she|they)\b",
    re.IGNORECASE,
)

#: A duration ask spanning past-to-now ("how long have I been working here") --
#: the answer is a span that started in the past. History-dependent, never a
#: single live reading. Bounded to the duration construction on purpose: "what
#: have you found" is a status question, not a duration.
_PRESENT_PERFECT_DURATION_RE = re.compile(
    r"\bhow\s+long\s+(?:have|has)\s+(?:i|we|you|he|she|they|it)\s+been\b",
    re.IGNORECASE,
)

#: A subordinate past-time clause: the question carries its own past anchor.
#: "when I bought the headphones", "before I started my job", "after we moved",
#: "that I paid", "when you told me", "on the day I arrived". The subject is
#: captured so `past_subject` can distinguish user history from assistant
#: history from world events.
_SUBORDINATE_PAST_RE = re.compile(
    r"\b(?:when|before|after|until|since|that|if)\s+(?P<subj>i|we|you|he|she|they)\s+"
    r"(?:have\s+|had\s+|already\s+|just\s+|recently\s+)*"
    r"(?:bought|paid|spent|ordered|booked|arrived|left|moved|started|finished|completed|"
    r"told|said|asked|mentioned|explained|promised|suggested|recommended|visited|attended|"
    r"joined|applied|submitted|received|got|went|came|did|saw|met|called|wrote|chose|"
    r"installed|built|adopted|switched|cancelled|canceled|signed|enrolled|registered|"
    r"renewed|graduated|relocated|hired|transferred|subscribed)\b",
    re.IGNORECASE,
)

#: A bare calendar anchor naming a PAST time, without any verb agreement.
#: "yesterday", "last Tuesday", "last week", "in 2019", "back then", "at the
#: time", "on my wedding day". "on Monday" (no "last") is deliberately absent:
#: bare weekday mentions are ambiguous between recurring and specific past use,
#: and the interrogative/possessive constructions above already carry the
#: clinic-class questions that pair with them.
_CALENDAR_PAST_RE = re.compile(
    r"\byesterday\b"
    r"|\blast\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|week|month|year|"
    r"spring|summer|autumn|fall|winter|january|february|march|april|may|june|july|"
    r"august|september|october|november|december)\b"
    r"|\bin\s+(?:19|20)\d{2}\b"
    r"|\bback\s+then\b"
    r"|\bat\s+the\s+time\b"
    r"|\bthe\s+other\s+day\b"
    r"|\bmy\s+last\s+(?:visit|trip|appointment|checkup|order|purchase)\b",
    re.IGNORECASE,
)

#: "as of" binds to whatever follows: "as of now" is CURRENT, "as of 2019" is
#: PAST. Decided here rather than listing every follower.
_AS_OF_RE = re.compile(r"\bas\s+of\s+(?P<rest>\S[^,.!?]*)", re.IGNORECASE)
_AS_OF_NOW_RE = re.compile(r"\b(?:now|today|this\s+moment|right\s+now)\b", re.IGNORECASE)

#: A habitual adverb: the ask is about a pattern, not a reading. These are
#: closed-class function words, not topic vocabulary.
_HABITUAL_RE = re.compile(
    r"\b(?:usually|typically|normally|always|generally|most\s+days|every\s+(?:day|week|morning|evening)|"
    r"on\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sun)day?s)\b",
    re.IGNORECASE,
)

#: A SCHEDULE interrogative: "what time" carried by do-support or a modal over
#: a subject that is not the clock itself. The time asked for belongs to a
#: planned/recurring EVENT the runtime can only know from memory -- never to
#: the wall clock. Measured on the frozen head e1dfdb63 (dev corpus F01-13 /
#: F13-13-family probes): "What time does the old tram head out?" and "What
#: time does the earliest boat leave during the cold months?" were claimed by
#: date_time_fast_path and answered with the current clock -- a deterministic,
#: confident, wrong answer with no path to the stored schedule. The negative
#: lookahead keeps genuine clock asks ("what time is it", "what time is it in
#: Tokyo", "what time is now") claimed by that lane: only an auxiliary
#: followed by a real subject declines. Past-tense "what time did ..." is
#: already PAST via the interrogative head; this pattern makes the
#: present/future schedule twin HABITUAL so `past_only` withdraws it from
#: current-clock contracts the same way "usually" asks already are.
_SCHEDULE_TIME_INTERROGATIVE_RE = re.compile(
    r"\bwhat\s+time\s+(?:do|does|will|would|can|could|should|must|have|has)\s+"
    r"(?!it\b|now\b|right\s+now\b)",
    re.IGNORECASE,
)

#: A current reference. Closed-class deictic anchors only; "current"/"now" as
#: parts of other words cannot match because every alternative is
#: word-bounded. "latest" and "newest" are present-state asks.
_CURRENT_RE = re.compile(
    r"\b(?:right\s+now|now|current(?:ly)?|today|tonight|this\s+morning|this\s+afternoon|"
    r"this\s+evening|this\s+week(?:end)?|latest|newest|at\s+the\s+moment|present)\b",
    re.IGNORECASE,
)

#: "current" IDENTIFIES a standing state of the user rather than asking for a
#: reading: "my current job", "our current address". The tenure question
#: "How long have I been working before I started my current job at Google?"
#: carries exactly this shape -- the "current" is a pointer to which job, not a
#: request for a present observation, and reading it as CURRENT kept that
#: question inside the current-data contract that rewrote its answer (case
#: q7db408b). These nouns name STATES a person holds, not MEASURED VALUES of
#: the world, and the value-reading nouns (price/rate/weather/score/...) are
#: deliberately absent. A closed grammatical class, bounded the same way
#: `_EVENT_TIME_RE` in fast_paths_utility bounds "time off" and "flight time".
_CURRENT_STATE_NOUN_RE = re.compile(
    r"\bcurrent\s+(?:job|role|position|post|title|employer|company|workplace|office|"
    r"address|home|apartment|flat|house|city|town|country|residence|partner|spouse|"
    r"phone(?:\s+number)?|number|email|team|manager|boss|supervisor|project|assignment|"
    r"major|university|school|study|studies|subscription|plan|carrier|provider|"
    r"membership|landlord|dentist|doctor|gp|vet)\b",
    re.IGNORECASE,
)


# A proposal head asks the reader to suggest something, rather than observe
# a world value. The CURRENT modifier is exempt only inside that proposal's
# grammatical INPUT argument ("for my current ...", "complement our current
# ..."). Personal possessives alone, a proposal verb alone, and arbitrary
# uses of CURRENT are never exemptions. Additional questions/actions decline
# this bounded reading, so mixed requests keep their original requirements.
_PROPOSAL_HEAD_RE = re.compile(
    r"^(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?"
    r"(?:suggest|recommend)\b", re.IGNORECASE,
)
_CURRENT_INPUT_ARGUMENT_RE = re.compile(
    r"\b(?:for|with|using|given|complement(?:s|ing)?|compatible\s+with|"
    r"suited\s+to|based\s+on)\s+(?:my|our|your|his|her|their)\s+"
    r"(?P<marker>current)\s+(?=[a-z][a-z'-]*\b)", re.IGNORECASE,
)
_ADDITIONAL_REQUEST_RE = re.compile(
    r"[;:]|[.!?]\s*\S"
    r"|\b(?:what|which|how|when|where|who)\b"
    r"|\b(?:and|but|also|then)\s+(?:please\s+)?"
    r"(?:tell|show|check|find|fetch|look|measure|verify|install|run|execute|"
    r"buy|order|delete|send|open|create|can|could|would|will|should|is|are)\b",
    re.IGNORECASE,
)


def current_input_frame_spans(text: str) -> tuple[tuple[int, int], ...]:
    """CURRENT input modifiers in a bounded, single ordinary proposal.

    This does not certify the input facts or make memory a live observation.
    Source authority still decides what the reader may know; the reply guard
    still rejects fabricated present values. It only scopes the QUESTION's
    modifier to its input argument. Unrecognized constructions fail closed.
    """
    body = str(text or "")
    if not _PROPOSAL_HEAD_RE.search(body.lstrip()):
        return ()
    if _ADDITIONAL_REQUEST_RE.search(body):
        return ()
    spans = tuple(match.span("marker") for match in _CURRENT_INPUT_ARGUMENT_RE.finditer(body))
    remainder = body
    for start, end in spans:
        remainder = remainder[:start] + " " * (end - start) + remainder[end:]
    # A second deictic marker may qualify the requested output. Keep these
    # unknown/mixed cases conservative, including calendar currency that the
    # grounded care-level reader recognizes ("this month/quarter/year").
    if _CURRENT_RE.search(remainder) or re.search(r"\bthis\s+(?:month|quarter|year)\b", remainder, re.IGNORECASE):
        return ()
    return spans


def current_observation_text(text: str) -> str:
    """Question text with only proven CURRENT input modifiers masked.

    Offsets are preserved for diagnostics. All remaining anchors keep their
    ordinary meaning, including CURRENT on requested output or a mixed ask.
    """
    body = str(text or "")
    for start, end in current_input_frame_spans(body):
        body = body[:start] + " " * (end - start) + body[end:]
    return body


def _asks_current(text: str) -> bool:
    """A CURRENT anchor, ignoring "current" when it merely identifies a state.

    The state-noun exclusion is applied only to the `current` arm: "now" and
    "today" keep their meaning next to any noun.
    """

    if _CURRENT_STATE_NOUN_RE.search(text):
        stripped = _CURRENT_STATE_NOUN_RE.sub(" ", text)
        remainder_anchors = re.compile(
            r"\b(?:right\s+now|now|currently|today|tonight|this\s+morning|this\s+afternoon|"
            r"this\s+evening|this\s+week(?:end)?|latest|newest|at\s+the\s+moment|present)\b",
            re.IGNORECASE,
        )
        bare_current = re.compile(r"\bcurrent\b", re.IGNORECASE)
        after_stripped = bare_current.search(stripped) is not None
        return bool(remainder_anchors.search(stripped) or after_stripped)
    return bool(_CURRENT_RE.search(text))


def question_time_scope(text: str) -> QuestionTimeScope:
    """Decide which points in time a question refers to. Never reads a clock.

    Deterministic and textual. Prohibition clauses ("don't tell me the current
    price") are stripped by the same retrieval-constraint authority every other
    route consumer uses, so a negated half cannot resurrect a requirement the
    user explicitly ruled out.
    """

    raw = str(text or "")
    if not raw.strip():
        return QuestionTimeScope()

    from core.retrieval_constraints import analyze_retrieval_constraints

    constraints = analyze_retrieval_constraints(raw)
    eligible = str(constraints.eligible_text or "").strip()

    # PAST and HABITUAL anchors are read off the RAW question: they name what
    # the ask is ABOUT, and a prohibition clause does not rewrite the subject
    # of the ask. CURRENT is read off the ELIGIBLE text when a prohibition
    # exists -- a clause the user ruled out ("don't check the current rate")
    # must not keep the current-observation requirement alive -- and off the
    # raw text otherwise.
    past_text = raw
    current_text = eligible if (constraints.has_prohibition and eligible) else raw

    references: list[TimeReference] = []
    past_subjects: list[str] = []

    def _add(ref: TimeReference) -> None:
        if ref not in references:
            references.append(ref)

    # Past: the interrogative head, the personal form deciding the subject.
    personal_match = _PERSONAL_PAST_INTERROGATIVE_RE.search(past_text)
    if personal_match:
        _add(TimeReference.PAST)
        subj = (personal_match.group(0).split()[-1] or "").lower()
        past_subjects.append("assistant" if subj == "you" else "user")
    elif (_PAST_INTERROGATIVE_RE.search(past_text)
            or _BEFORE_EVENT_PAST_RE.search(past_text)):
        _add(TimeReference.PAST)
        past_subjects.append("world")

    if _PRESENT_PERFECT_DURATION_RE.search(past_text):
        _add(TimeReference.PAST)
        past_subjects.append("user")

    subordinate = _SUBORDINATE_PAST_RE.search(past_text)
    if subordinate:
        _add(TimeReference.PAST)
        subj = str(subordinate.group("subj") or "").lower()
        past_subjects.append("assistant" if subj == "you" else "user")

    if _CALENDAR_PAST_RE.search(past_text):
        _add(TimeReference.PAST)
        past_subjects.append("world")

    as_of = _AS_OF_RE.search(past_text)
    if as_of is not None:
        # "as of now" is a CURRENT anchor; "as of <anything past-looking>" is PAST.
        rest = str(as_of.group("rest") or "")
        if _AS_OF_NOW_RE.search(rest):
            _add(TimeReference.CURRENT)
        else:
            _add(TimeReference.PAST)
            past_subjects.append("world")

    if _HABITUAL_RE.search(past_text):
        _add(TimeReference.HABITUAL)

    if _SCHEDULE_TIME_INTERROGATIVE_RE.search(past_text):
        # A schedule ask: the event's time lives in memory/history, not on the
        # clock. Grouped with HABITUAL -- a recurring/planned event time -- so
        # every past_only consumer (clock lane, current-observation
        # requirements, unsupported-claim guards) treats it as not-now.
        _add(TimeReference.HABITUAL)

    if _asks_current(current_observation_text(current_text)):
        _add(TimeReference.CURRENT)

    # A personal anchor anywhere outranks a generic world one: "What was the
    # rent ... before we renewed the lease?" is about the operator's past even
    # though the interrogative head "what was" alone reads as world.
    past_subject = ""
    for candidate in past_subjects:
        if candidate in {"user", "assistant"}:
            past_subject = candidate
            break
    if not past_subject and past_subjects:
        past_subject = past_subjects[0]
    return QuestionTimeScope(
        references=tuple(references),
        past_subject=past_subject,
        current_input_spans=current_input_frame_spans(current_text),
        current_input_span_basis=(
            "retrieval_eligible_text" if constraints.has_prohibition and eligible else "question"
        ),
    )


#: A question head that asks WHEN an event happened: a past-tense "when" ask, a calendar-unit ask
#: ("what date", "which month", "in what year"), or an elapsed "ago" ask. "What did I buy when I
#: visited the coast?" asks for a thing, not a time: a "when" that opens a subordinate clause is not
#: followed by a past auxiliary and does not match.
_EVENT_TIME_ASK_RE = re.compile(
    r"\bwhen\s+(?:did|was|were|had)\b"
    r"|\b(?:what|which)\s+(?:day|date|week|weekend|month|year|season)\b"
    r"|\bhow\s+long\s+ago\b"
    r"|\bhow\s+many\s+(?:days|weeks|months|years)\s+ago\b",
    re.IGNORECASE,
)
_AGO_ASK_RE = re.compile(r"\bago\b", re.IGNORECASE)


def asks_event_time(text: str) -> bool:
    """The question asks when a past event happened. Never reads a clock.

    Two conjuncts: a head that asks for a time point (:data:`_EVENT_TIME_ASK_RE`) and a PAST
    reference (:func:`question_time_scope`); an "ago" ask is past by its own word. "When does the
    shop open?" and "What day is it?" ask the present or a schedule, and "What did I buy?" asks for
    no time; none of them is an event-time ask.
    """
    body = str(text or "")
    head = _EVENT_TIME_ASK_RE.search(body)
    if head is None:
        return False
    if _AGO_ASK_RE.search(head.group(0)):
        return True
    return question_time_scope(body).asks_past


__all__ = [
    "QuestionTimeScope", "TimeReference", "asks_event_time", "current_input_frame_spans",
    "current_observation_text", "question_time_scope",
]
