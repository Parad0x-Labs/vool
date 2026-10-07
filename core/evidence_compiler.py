"""Evidence compiler: the control plane over v14 retrieval (LongMemEval diagnosis, 2026-10-06).

v14 finds candidates (capsule, whole-turn lane, search phrases). This layer asks a different question: is what was
found SUFFICIENT for this question? It derives the question's evidence obligations (which typed operands an answer
needs), reads the memory receipts (core.memory_receipts) the stored turns carry, checks completeness, runs ONE
targeted search for a missing operand, and delivers a compact receipt packet beside the capsule: each line a typed
fact with the day its event happened and what replaced what. After the reply, ``verify_answer`` labels a computed
value as supported when the packet's operands derive it, so a correct interval is never withdrawn by the past-time
guard for lack of a literal match (CORRECT-BUT-WITHDRAWN, 18 of 112 misses on the old sets).

Funnel telemetry per turn (``telemetry["evidence_compiler"]``): obligation kind, operands required, found before and
after the hop, complete, hop fired, packet lines and tokens. No model call is made here.

Switches: VOOL_EVIDENCE_COMPILER=1 (packet + hop), VOOL_EVIDENCE_VERIFY=1 (post-answer labelling). Off, v14 is
unchanged.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from core.memory_receipts import _slot as _slot_terms, _stem, _statement_day, match_receipts, question_terms, receipts_for_scope, resolve_event_day

LOGGER = logging.getLogger(__name__)
_UTC = timezone.utc
COMPILER_ENV = ("VOOL_EVIDENCE_COMPILER", "NULLA_EVIDENCE_COMPILER")
HOP_ENV = ("VOOL_EVIDENCE_HOP", "NULLA_EVIDENCE_HOP")  # default on when the compiler is on; 0 = packet only, no second search
VERIFY_ENV = ("VOOL_EVIDENCE_VERIFY", "NULLA_EVIDENCE_VERIFY")
PACKET_MAX_TOKENS = 1400
PACKET_MAX_LINES = 14
PACKET_HEADER = ("Evidence receipts (typed facts read from the records above and below; each carries the day the record "
                 "was stated and, when its own words date the event, the event day; a replaced value is marked):")


def _flag(names: Sequence[str], env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    for name in names:
        value = str(env.get(name, "") or "").strip().lower()
        if value:
            return value in ("1", "true", "yes", "on")
    return False


def compiler_enabled(env: Mapping[str, str] | None = None) -> bool:
    return _flag(COMPILER_ENV, env)


def verify_enabled(env: Mapping[str, str] | None = None) -> bool:
    return _flag(VERIFY_ENV, env)


def hop_enabled(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    for name in HOP_ENV:
        value = str(env.get(name, "") or "").strip().lower()
        if value:
            return value in ("1", "true", "yes", "on")
    return True


# ─── obligations ──────────────────────────────────────────────────────────────────────────────

_ASSISTANT_RE = re.compile(r"\b(?:you|your)\b[^.?]{0,80}\b(?:listed|recommended|recommendation|suggested|suggestion|told|said|mentioned|gave|provided|shared|explained|wrote|described|outlined|advised|proposed)\b|\b(?:the\s+(?:list|steps?|recipe|tips?|advice|plan|ideas?)\s+you\s+(?:gave|provided|shared|listed|wrote|suggested|made))\b|\bdid\s+you\s+(?:tell|say|recommend|suggest|list|give|mention|advise|propose)\b|\b(?:remind me|what was the (?:\d+(?:st|nd|rd|th)|first|second|third|last) (?:item|job|step|point|tip|one))\b", re.IGNORECASE)
_AGO_RE = re.compile(r"\bhow\s+(?:many\s+(?:days?|weeks?|months?|years?)|long)\s+ago\b|\bhow\s+long\s+(?:has\s+it\s+been|since)\b", re.IGNORECASE)
_BETWEEN_RE = re.compile(r"\bhow\s+(?:many\s+(?:days?|weeks?|months?|years?|hours?|minutes?)|long|much\s+time)\b[^.?]{0,60}\b(?:between|after|before|since|until|passed|elapsed|apart|from)\b|\bhow\s+long\s+(?:did|was|were|had)\b", re.IGNORECASE)
_ORDER_RE = re.compile(r"\bwhich\b[^.?]{0,80}\b(?:first|earlier|earliest|later|latest|last|before|after)\b|\b(?:before|after)\s+or\s+(?:before|after)\b|\bwhat\s+(?:did|was)\s+(?:i|my)[^.?]{0,40}\bfirst\b|\bwhat\s+(?:happened|came)\s+(?:first|before)\b", re.IGNORECASE)
_AGGREGATE_RE = re.compile(r"\b(?:how\s+many|how\s+much|total|in\s+total|altogether|combined|average|overall|sum|count)\b", re.IGNORECASE)
_PREFERENCE_RE = re.compile(r"\b(?:suggest(?:ions?)?|recommend(?:ations?)?|tips?|ideas?|advice|(?:what|how|where|which)\s+should\s+(?:i|we)|should\s+(?:i|we)\s+(?:wear|bring|cook|take|book|get|go|pick|choose)|any\s+(?:good|new)|help\s+me\s+(?:plan|pick|choose|find)|can\s+you\s+(?:help|give)|looking\s+for|what\s+(?:to|can\s+i)\s+(?:wear|bring|cook|serve|pack|make)|(?:put\s+together|plan|build|create|design|draft|make|come\s+up\s+with|work\s+out|set\s+up)\s+(?:a|an|some|my|me)?\s*(?:[\w\-]+\s+){0,3}(?:routine|schedule|itinerary|menu|plan|program|regimen|playlist|list|timetable|workout))\b", re.IGNORECASE)
_EXISTENCE_RE = re.compile(r"\b(?:did|have|has)\s+(?:i|we)\s+ever\b|\bhave\s+i\s+(?:been|tried|visited|mentioned|told|used|seen|read|watched)\b", re.IGNORECASE)
_CURRENT_RE = re.compile(r"\b(?:currently|now|these\s+days|at\s+the\s+moment|nowadays|latest|current)\b|\b(?:what|which|how\s+(?:much|many|often|long))\b[^.?]{0,50}\b(?:do|does|am|is|are)\s+(?:i|my|we|our)\b", re.IGNORECASE)
_WHEN_RE = re.compile(r"\b(?:when|what\s+(?:date|day|time)|which\s+(?:date|day|month|year))\b", re.IGNORECASE)
# a sequence ask: the order in which several things came up across sessions ("in what order did I bring up X, Y and Z",
# "walk me through the order in which I mentioned ...", "the timeline of ...", "chronologically")
_SEQUENCE_RE = re.compile(r"\b(?:the\s+order\s+in\s+which|in\s+(?:what|which)\s+order|what\s+order|the\s+sequence\s+(?:in\s+which|of)|chronolog(?:ical(?:ly)?|y)|(?:the\s+)?timeline\s+of|step\s+by\s+step\s+(?:order|history)|one\s+after\s+the\s+other)\b", re.IGNORECASE)


# v14.6 typed completeness (ASTRA Pro hardening item 1): every obligation carries a CLASS that says what complete evidence
# is. Operand presence in a top-k view can prove EXISTS, PAIR, ORDER, SINGLE and QUOTE_SPEAKER; ABSENCE, EXTREMUM, LIST_ALL,
# CURRENT_STATE and AGGREGATE also need COVERAGE: the scoped candidate set (every receipt sharing a subject term with the
# question) must have been shown in full, or the packet says "coverage: partial" and the question is incomplete.
_COVERAGE_CLASSES = frozenset({"ABSENCE", "EXTREMUM", "LIST_ALL", "CURRENT_STATE", "AGGREGATE"})
_EXTREMUM_RE = re.compile(r"\b(?:first|earliest|oldest|very\s+first|latest|last|most\s+recent(?:ly)?|newest|final)\b", re.IGNORECASE)
_PAST_EVENT_VERB_RE = re.compile(r"\b(?:bought|purchased|picked\s+up|got|did|went|visited|read|watched|took|made|started|finished|tried|saw|had|ate|ran|booked|ordered|attended|joined|moved)\b", re.IGNORECASE)
_TWO_EVENT_ORDER_RE = re.compile(r"\b(?:come|came|happen|happened|was|were|is|occur|occurred)\s+(?:before|after)\b|\b(?:before|after)\s+or\s+(?:before|after)\b", re.IGNORECASE)
_EARLIEST_RE = re.compile(r"\b(?:first|earliest|oldest|very\s+first)\b", re.IGNORECASE)
_ABSENCE_RE = re.compile(r"\b(?:never|have\s+i\s+(?:not|n't)|haven'?t\s+i|did\s+i\s+(?:not|n't)|didn'?t\s+i|any\s+(?:mention|record|time)\s+of|is\s+there\s+any|at\s+any\s+point|at\s+all)\b", re.IGNORECASE)
_LIST_ALL_RE = re.compile(r"\b(?:all|every|each|list\s+(?:all|every|the)|which\s+(?:ones?|of\s+them)|how\s+many\s+(?:different|distinct)|everything)\b", re.IGNORECASE)


def obligation_class(kind: str, question: str) -> str:
    q = str(question or "")
    if kind == "assistant_output":
        return "QUOTE_SPEAKER"
    if kind in ("interval_ago", "interval_between", "when"):
        return "PAIR" if kind == "interval_between" else "SINGLE"
    if kind == "sequence":
        return "LIST_ALL"   # the whole ordered set, so coverage is required
    if kind == "order":
        # two named candidates ("the lamp or the desk", "X before Y") are an ORDER; "what did I buy first" over an open set is an EXTREMUM
        return "EXTREMUM" if _EXTREMUM_RE.search(q) and not re.search(r"\b(?:or|than|versus|vs\.?)\b|\b(?:before|after)\s+(?:the|my|i)\b", q, re.IGNORECASE) else "ORDER"
    if kind == "existence":
        return "ABSENCE" if _ABSENCE_RE.search(q) else "EXISTS"
    if _EXTREMUM_RE.search(q) and _PAST_EVENT_VERB_RE.search(q):
        return "EXTREMUM"   # "the latest camera I picked up": the edge of an event scope, not the current value of a slot
    if _TWO_EVENT_ORDER_RE.search(q):
        return "ORDER"
    if kind == "current_value":
        return "CURRENT_STATE"
    if kind == "aggregate":
        return "AGGREGATE"
    if kind == "preference":
        return "LIST_ALL"  # every stated preference on the subject, not one semantic match
    if _EXTREMUM_RE.search(q):
        return "EXTREMUM"
    if _ABSENCE_RE.search(q):
        return "ABSENCE"
    if _LIST_ALL_RE.search(q):
        return "LIST_ALL"
    return "SINGLE"


@dataclass
class Obligation:
    kind: str
    required: list[str]
    note: str = ""
    cls: str = "SINGLE"          # EXISTS, ABSENCE, PAIR, ORDER, EXTREMUM, CURRENT_STATE, LIST_ALL, QUOTE_SPEAKER, AGGREGATE, SINGLE

    @property
    def needs_coverage(self) -> bool:
        return self.cls in _COVERAGE_CLASSES

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "required": list(self.required), "note": self.note, "class": self.cls, "needs_coverage": self.needs_coverage}


def obligations(question: str) -> Obligation:
    """What typed evidence an answer to *question* needs (closed kinds; the first match wins), with its completeness class."""
    ob = _obligation_kind(question)
    ob.cls = obligation_class(ob.kind, question)
    return ob


def _obligation_kind(question: str) -> Obligation:
    q = str(question or "")
    if _SEQUENCE_RE.search(q) and re.search(r"\bI\s+(?:brought|mentioned|raised|talked|asked|discussed|came|started|told|said|wrote)\b|\bmy\b", q, re.IGNORECASE):
        # "walk me through the order in which I mentioned ...": the user's own sequence, even though "you ... mentioned"
        # also reads as an assistant ask
        return Obligation("sequence", ["dated_events>=2"], "every dated record of the named things, earliest first, one per line")
    if _ASSISTANT_RE.search(q):
        return Obligation("assistant_output", ["assistant_turn"], "the assistant's own earlier reply")
    if _AGO_RE.search(q):
        return Obligation("interval_ago", ["event_date", "reference_day"], "one dated event counted back from today")
    if _SEQUENCE_RE.search(q):
        return Obligation("sequence", ["dated_events>=2"], "every dated record of the named things, earliest first, one per line")
    if _ORDER_RE.search(q):
        return Obligation("order", ["event_date_a", "event_date_b"], "two dated events, ordered")
    if _BETWEEN_RE.search(q):
        return Obligation("interval_between", ["event_date_a", "event_date_b"], "two dated events, differenced")
    past = re.search(r"\b(?:did|have|has|had|was|were)\b", q, re.IGNORECASE)
    now_cue = re.search(r"\b(?:now|currently|these\s+days|at\s+the\s+moment|nowadays|current|latest)\b", q, re.IGNORECASE)
    if _CURRENT_RE.search(q) and not _WHEN_RE.search(q) and (not past or now_cue):
        # a present-tense habit or state ask ("how much ... do I", "what is my current ...") wants the latest
        # value, not a sum over records
        return Obligation("current_value", ["latest_unreplaced_value"], "the latest value in the slot, with its chain")
    if _AGGREGATE_RE.search(q):
        return Obligation("aggregate", ["typed_values>=2"], "every record with a value of the asked kind")
    if _PREFERENCE_RE.search(q):
        return Obligation("preference", ["user_preference"], "the user's own stated preferences on the subject")
    if _CURRENT_RE.search(q) and not _WHEN_RE.search(q):
        return Obligation("current_value", ["latest_unreplaced_value"], "the latest value in the slot, with its chain")
    if _WHEN_RE.search(q):
        return Obligation("when", ["event_date"], "the dated record")
    if _EXISTENCE_RE.search(q):
        return Obligation("existence", ["record_or_absence"], "a matching record, or verified absence")
    return Obligation("single_fact", ["record"], "the record that states it")


# ─── packet ───────────────────────────────────────────────────────────────────────────────────

@dataclass
class Packet:
    obligation: Obligation
    lines: list[str] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)   # the facts behind the lines, for verification
    tokens: int = 0
    telemetry: dict[str, Any] = field(default_factory=dict)


def _day(ts: float | None) -> str:
    d = _statement_day(ts)
    return d.isoformat() if d else "undated"


def _found_operands(ob: Obligation, selected: Sequence[tuple[float, dict[str, Any], dict[str, Any]]]) -> tuple[list[str], list[str]]:
    """(found, missing) operand names for the obligation given the selected (score, receipt, fact) rows."""
    facts = [f for _s, _r, f in selected]
    dated = [f for f in facts if f.get("event_at") is not None or f.get("value_type") == "event" or (ob.kind == "sequence" and f.get("statement_at") is not None)]
    typed = [f for f in facts if f.get("value_type") not in ("event",)]
    assistant = [r for _s, r, _f in selected if r.get("role") == "assistant"]
    user = [f for _s, r, f in selected if r.get("role") == "user"]
    kind = ob.kind
    if kind == "assistant_output":
        found = ["assistant_turn"] if assistant else []
    elif kind == "interval_ago" or kind == "when":
        found = ["event_date"] if dated else []
        if kind == "interval_ago":
            found.append("reference_day")  # the capsule header carries today's date
    elif kind in ("order", "interval_between"):
        distinct = {f.get("event_at") for f in dated if f.get("event_at") is not None}
        found = ["event_date_a"] if distinct else []
        if len(distinct) >= 2:
            found.append("event_date_b")
        # a duration pair (goal time vs finish time) answers a "how long after/by how much" ask too
        if kind == "interval_between" and len([f for f in typed if f.get("value_type") == "duration"]) >= 2:
            found = ["event_date_a", "event_date_b"]
    elif kind == "sequence":
        # a mention row is dated by its receipt's statement day; two distinct days make a sequence
        seq_days = {f.get("event_at") or r.get("statement_at") for _s, r, f in selected if r.get("role") == "user"}
        found = ["dated_events>=2"] if len({d for d in seq_days if d is not None}) >= 2 else []
    elif kind == "aggregate":
        found = ["typed_values>=2"] if len(typed) >= 2 else []
    elif kind == "preference":
        found = ["user_preference"] if any(f.get("value_type") == "preference" for f in user) else []
    elif kind == "current_value":
        states = [f for f in user if f.get("value_type") == "state"]
        found = ["latest_unreplaced_value"] if (any(not f.get("replaced_by") for f in states) if states else any(not f.get("replaced_by") for f in user)) else []
    elif kind == "existence":
        found = ["record_or_absence"]
    else:
        found = ["record"] if facts else []
    missing = [r for r in ob.required if r not in found]
    return found, missing


def _render_line(receipt: dict[str, Any], fact: dict[str, Any], receipts_by_id: Mapping[str, dict[str, Any]]) -> str:
    role = receipt.get("role") or "user"
    stated = _day(receipt.get("statement_at"))
    if fact.get("value_type") == "list":
        try:
            items = json.loads(fact.get("value") or "[]")
        except Exception:
            items = []
        body = "; ".join(f"{i + 1}. {it}" for i, it in enumerate(items[:20]))
        line = f"- [{stated}] {role} listed: {body}"
    elif fact.get("value_type") == "state":
        key = str(fact.get("norm") or "").split("=", 1)[0]
        tag = f"<{key} = {fact.get('value')}>" if not str(fact.get("norm") or "").endswith("|former") else f"<former {key} = {fact.get('value')}>"
        line = f"- [{stated}] {role} said: \"{str(fact.get('sentence') or '').strip()[:300]}\"  {tag}"
    elif fact.get("value_type") == "mention":
        line = f"- [{stated}] {role} said: \"{str(fact.get('sentence') or '').strip()[:240]}\"  <mention>"
    elif fact.get("value_type") == "preference":
        # a stated preference or constraint is an operand the answer has to respect, not a detail it may drop: set five
        # (v14.4) had three penicillin-allergy lines in the packet and a snack list that ignored them
        line = f"- [{stated}] {role} said: \"{str(fact.get('sentence') or '').strip()[:300]}\"  <stated preference: respect it in the answer>"
    else:
        sentence = str(fact.get("sentence") or "").strip()
        line = f"- [{stated}] {role} said: \"{sentence[:400]}\""
        if fact.get("value_type") not in ("event",):
            line += f"  <{fact.get('value_type')}: {fact.get('value')}>"
    ev = fact.get("event_at")
    if ev is not None:
        line += f"  (event day {_day(ev)}, {fact.get('event_grain') or 'dated'})"
    for change in receipt.get("changes") or []:
        if change.get("new_value") == fact.get("value"):
            line += f"  [replaces \"{change.get('old_value')}\" stated {_day(change.get('old_statement_at'))}]"
    if fact.get("replaced_by"):
        later = receipts_by_id.get(str(fact.get("replaced_by")))
        if later is not None:
            newer = next((c.get("new_value") for c in later.get("changes") or [] if c.get("old_value") == fact.get("value")), None)
            line += f"  [REPLACED later by \"{newer}\" stated {_day(later.get('statement_at'))}; not current]"
    return line


def _span_days(days: int) -> str:
    """A day count with its week and month readings, so the reader can answer in the asked unit."""
    parts = [f"{days} day{'s' if days != 1 else ''}"]
    if days >= 7:
        weeks = days / 7.0
        parts.append(f"about {weeks:.0f} week{'s' if round(weeks) != 1 else ''}" if abs(weeks - round(weeks)) < 0.15 else f"about {weeks:.1f} weeks")
    if days >= 28:
        months = days / 30.4375
        parts.append(f"about {months:.0f} month{'s' if round(months) != 1 else ''}" if abs(months - round(months)) < 0.2 else f"about {months:.1f} months")
    if days >= 335:
        years = days / 365.25
        parts.append(f"about {years:.1f} years")
    return ", ".join(parts)


def _label(fact: Mapping[str, Any]) -> str:
    words = [w for w in (fact.get("slot") or []) if len(w) > 2][:5]
    return " ".join(words) if words else str(fact.get("sentence") or "")[:40]


_ORDINAL_RE = re.compile(r"\b(?P<o>first|earliest|initial|last|latest|most\s+recent|final|second|third|fourth)\b", re.IGNORECASE)
_ORDINAL_INDEX = {"first": 0, "earliest": 0, "initial": 0, "second": 1, "third": 2, "fourth": 3, "last": -1, "latest": -1, "most recent": -1, "final": -1}
_SIDE_SPLIT_RE = re.compile(r"\s+(?:or|and)\s+(?=(?:my|the|our|when|i\b))|\s+(?:before|after)\s+(?=(?:my|the|our|i\b))|,\s+(?=(?:my|the|our)\b)", re.IGNORECASE)
_SIDE_STOP = frozenset("which came first what how many days weeks between did i my the a an of to in on at for and or before after was were is are when go went attend attended have had this that there".split())


def _operand_sides(question: str) -> list[str]:
    """The two event phrases a two-operand question names ("the intro to pottery workshop or my first dentist
    appointment"; "between my first two dermatologist appointments" has one phrase and no sides)."""
    q = str(question or "").strip().rstrip("?")
    m = re.search(r"\b(?:between|first,|which came first,|came first:)\s+(.*)$", q, re.I) or re.search(r"\b(?:before|after|from|since|until)\s+(.*)$", q, re.I)
    body = m.group(1) if m else q
    parts = [p.strip(" ,") for p in _SIDE_SPLIT_RE.split(body) if p and p.strip(" ,")]
    parts = [p for p in parts if {w for w in re.findall(r"[a-z]+", p.lower())} - _SIDE_STOP]
    return parts[:2] if len(parts) == 2 else []


def _pick_side_operand(side: str, dated: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The dated fact for one side: the facts whose slot shares a content word with the side's phrase, ordered by
    event day when the side carries an ordinal, else the best-ranked."""
    om = _ORDINAL_RE.search(side)
    words = {w for w in re.findall(r"[a-z]+", side.lower()) if w not in _SIDE_STOP and len(w) > 2} - {(om.group("o").lower() if om else "")}
    stems = {w[:5] for w in words}
    cands = [f for f in dated if any(str(w)[:5] in stems for w in (f.get("slot") or []))]
    if not cands:
        return None
    if om is None:
        return cands[0]
    by_day = sorted(cands, key=lambda f: float(f["event_at"]))
    distinct: list[Mapping[str, Any]] = []
    for f in by_day:
        if all(datetime.fromtimestamp(float(f["event_at"]), tz=_UTC).date() != datetime.fromtimestamp(float(g["event_at"]), tz=_UTC).date() for g in distinct):
            distinct.append(f)
    idx = _ORDINAL_INDEX.get(om.group("o").lower().replace("  ", " "), 0)
    try:
        return distinct[idx]
    except IndexError:
        return None


def derived_lines(ob: Obligation, facts: Sequence[Mapping[str, Any]], *, reference_day: date | None, question_text_for_derivation: str = "") -> list[str]:
    """Runtime arithmetic over the packet's dated operands, stated with its operands so the reader can check it:
    the day count between the two best-matching dated facts (interval_between, order), or between the best dated
    fact and the reference day (interval_ago). Never a guess: no operands, no line."""
    dated = [f for f in facts if f.get("event_at") is not None]
    out: list[str] = []
    if ob.kind in ("interval_between", "order"):
        seen: list[Mapping[str, Any]] = []
        if re.search(r"\b(?:first\s+two|first\s+and\s+second|earliest\s+two|two\s+earliest)\b", question_text_for_derivation, re.I):
            dated = sorted(dated, key=lambda f: float(f["event_at"]))  # the two earliest, not the two best-ranked
        # v14.3: the question's two sides pick their own operands, each with its ordinal ("my FIRST dentist
        # appointment" is the earliest dentist record, not the best-ranked one; set two miss q4bac36e6fd9eebe4)
        sides = _operand_sides(question_text_for_derivation)
        if sides:
            picked = [_pick_side_operand(side, dated) for side in sides]
            if all(picked) and picked[0] is not picked[1]:
                seen = list(picked)  # type: ignore[arg-type]
        for f in dated:  # facts arrive best first; keep the first two with distinct event days
            if len(seen) == 2:
                break
            if all(datetime.fromtimestamp(float(f["event_at"]), tz=_UTC).date() != datetime.fromtimestamp(float(g["event_at"]), tz=_UTC).date() for g in seen):
                seen.append(f)
        if len(seen) == 2:
            a, b = sorted(seen, key=lambda f: float(f["event_at"]))
            da, db = (datetime.fromtimestamp(float(x["event_at"]), tz=_UTC).date() for x in (a, b))
            days = (db - da).days
            if ob.kind == "order":
                out.append(f"- derived order from the receipts: \"{_label(a)}\" ({da.isoformat()}) came before \"{_label(b)}\" ({db.isoformat()}), {days} days earlier.")
            else:
                out.append(f"- derived from the receipts: {_span_days(days)} between \"{_label(a)}\" ({da.isoformat()}) and \"{_label(b)}\" ({db.isoformat()}).")
    elif ob.kind == "interval_ago" and dated and reference_day is not None:
        f = dated[0]
        d = datetime.fromtimestamp(float(f["event_at"]), tz=_UTC).date()
        days = (reference_day - d).days
        if days >= 0:
            out.append(f"- derived from the receipts: \"{_label(f)}\" was {_span_days(days)} ago ({d.isoformat()} to today {reference_day.isoformat()}).")
    return out


_QUESTION_STATE_KEY = [(re.compile(r"\b(?:car|vehicle|drive|driving)\b", re.I), "vehicle"), (re.compile(r"\b(?:work\s+for|employer|company|who\s+employs)\b", re.I), "employer"),
                       (re.compile(r"\b(?:job|work|occupation|profession|employ|career|role)\b", re.I), "job"),
                       (re.compile(r"\b(?:live|living|city|town|home|where .* based|reside|grew\s+up|grow\s+up|hometown|home\s+town|born|raised|originally\s+from)\b", re.I), "home_city"), (re.compile(r"\b(?:employer|company)\b", re.I), "employer"),
                       (re.compile(r"\b(?:phone)\b", re.I), "phone"), (re.compile(r"\b(?:laptop|computer)\b", re.I), "laptop"), (re.compile(r"\b(?:team|club)\b", re.I), "team"),
                       (re.compile(r"\b(?:commute)\b", re.I), "commute"), (re.compile(r"\b(?:bike|bicycle)\b", re.I), "bike")]


def question_state_key(question: str) -> str:
    for rx, key in _QUESTION_STATE_KEY:
        if rx.search(str(question or "")):
            return key
    return ""


def scoped_coverage(receipts: Sequence[Mapping[str, Any]], question: str, shown_facts: Sequence[Mapping[str, Any]], *,
                    extra_terms: Sequence[str] = (), state_key: str = "") -> dict[str, Any]:
    """How much of the scoped candidate set the packet shows. Candidates: every user fact in the chat whose slot shares a
    content term with the question (or the state facts of the asked state key). Exhaustive when every candidate is in the
    packet; a chat with no candidate at all is exhaustive too (there is nothing unseen), so a clean refusal stays clean."""
    terms = set(question_terms(question)) | {_stem(t) for t in extra_terms if t}
    shown = {(str(f.get("receipt_id")), str(f.get("sentence") or f.get("value"))) for f in shown_facts}
    candidates = 0; seen = 0; unseen_ids: list[str] = []
    for r in receipts:
        if r.get("role") != "user":
            continue
        for f in r.get("facts") or []:
            slot = set(f.get("slot") or [])
            is_state = bool(state_key) and f.get("value_type") == "state" and str(f.get("norm") or "").split("=", 1)[0] == state_key
            if not (slot & terms) and not is_state:
                continue
            candidates += 1
            key = (str(r.get("receipt_id")), str(f.get("sentence") or f.get("value")))
            if key in shown:
                seen += 1
            elif len(unseen_ids) < 8:
                unseen_ids.append(str(r.get("receipt_id")))
    return {"candidates": candidates, "shown": seen, "exhaustive": seen >= candidates, "unseen_receipts": unseen_ids}


def compile_packet(mem: Any, chat_scope: str, question: str, *, expansions: Sequence[str] = (),
                   estimate_tokens: Callable[[str], int] | None = None, allow_hop: bool = True,
                   max_tokens: int = PACKET_MAX_TOKENS, max_lines: int = PACKET_MAX_LINES,
                   reference_day: date | None = None, q_vec: Sequence[float] | None = None, q_backend: str = "") -> Packet:
    """Read the chat's receipts, check completeness for the question's obligation, hop once for a missing operand,
    and render the packet. Pure reads on the store."""
    est = estimate_tokens or (lambda t: max(1, len(t) // 4))
    ob = obligations(question)
    packet = Packet(obligation=ob)
    from core.memory_receipts import rebuild_missing_receipts

    rebuild_missing_receipts(mem, chat_scope)
    receipts = receipts_for_scope(mem, chat_scope)
    by_id = {r["receipt_id"]: r for r in receipts}
    by_occ = {r["occurrence_id"]: r for r in receipts}
    extra = [t for phrase in expansions for t in question_terms(phrase)]
    if reference_day is None:
        try:
            import time as _time

            reference_day = datetime.fromtimestamp(_time.time(), tz=_UTC).date()  # the serving clock (frozen to the question date in a benchmark)
        except Exception:
            reference_day = None
    # a relative phrase in the question ("two weeks ago", "last month") points at a day; facts dated near it match
    target_day, _grain = resolve_event_day(question, reference_day) if reference_day is not None else (None, "")
    wants_lists = ob.kind == "assistant_output" or bool(re.search(r"\b(?:list|listed|steps?|items?|options?|tips?|recipe|ingredients?|objectives?)\b", question, re.I))
    # value kinds the question can use: a count only for an aggregate ask, a clock only for a time ask, an amount
    # only for a money ask, so generic typed values ("few ways", "5 GHz", "$20") do not crowd the packet
    skip: set[str] = set() if wants_lists else {"list"}
    if ob.kind != "aggregate" and not re.search(r"\bhow\s+many\b", question, re.I):
        skip.add("count")
    if not re.search(r"\b(?:what\s+time|when|o'?clock|\bam\b|\bpm\b|wake|bed|morning|evening|schedule)\b", question, re.I):
        skip.add("clock")
    if not re.search(r"\b(?:cost|price|pay|paid|spend|spent|charge|fee|budget|dollars?|euros?|\$|money|save|saved|earn|raise|raised)\b", question, re.I):
        skip.add("amount")
    selected = match_receipts(receipts, question, extra_terms=extra, limit=max_lines * 3,
                              skip_types=tuple(skip), target_day=target_day)

    if ob.kind != "assistant_output":
        # the user's own statements first; an assistant typed value rides only when it is the only match
        selected = sorted(selected, key=lambda x: (0 if x[1].get("role") == "user" else 1, -x[0]))
    if ob.kind == "assistant_output":
        # the assistant's own reply is the operand: prefer assistant receipts, lists first
        selected = sorted(selected, key=lambda x: (0 if x[1].get("role") == "assistant" else 1, 0 if x[2].get("value_type") == "list" else 1, -x[0]))
    # kernel operands that lexical matching cannot reach: the user's stated preferences for a preference ask (every
    # one, newest first, at most 6: a diet or a style is the operand whatever the dinner or the party is called), and
    # the state the question names for a current-value ask (latest unreplaced first, replaced ones after it with their
    # marks, so the chain x = new y is in front of the reader)
    kernel_rows: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    seen_ids = {id(f) for _s, _r, f in selected}
    if ob.kind == "preference":
        prefs = [(r, f) for r in receipts if r.get("role") == "user" for f in (r.get("facts") or []) if f.get("value_type") == "preference"]
        prefs.sort(key=lambda x: -(x[0].get("statement_at") or 0))
        for r, f in prefs[:6]:
            if id(f) not in seen_ids:
                kernel_rows.append((3.0, r, f)); seen_ids.add(id(f))
    state_key = question_state_key(question) if ob.kind in ("current_value", "single_fact", "when") else ""
    if state_key:
        states = [(r, f) for r in receipts if r.get("role") == "user" for f in (r.get("facts") or [])
                  if f.get("value_type") == "state" and str(f.get("norm") or "").split("=", 1)[0] == state_key]
        former = lambda f: str(f.get("norm") or "").endswith("|former")
        states.sort(key=lambda x: (0 if (not x[1].get("replaced_by") and not former(x[1])) else 1, -(x[0].get("statement_at") or 0)))
        for r, f in states[:4]:
            if id(f) not in seen_ids:
                kernel_rows.append((4.0 if not f.get("replaced_by") else 2.5, r, f)); seen_ids.add(id(f))
    selected = kernel_rows + selected
    found, missing = _found_operands(ob, selected)
    telemetry: dict[str, Any] = {"obligation": ob.as_dict(), "receipts_in_chat": len(receipts), "matched_facts": len(selected),
                                 "found_before_hop": list(found), "missing_before_hop": list(missing), "hop_fired": False, "hop_new_facts": 0}
    allow_hop = allow_hop and hop_enabled()
    # The hop is two local searches (BM25 and the question's embedding over whole turns), no model call: it fires for
    # a missing operand, and also to widen the pool for asks whose completeness cannot be judged by a count (how many
    # instruments, the current value of a slot, a preference), where a paraphrase ("instruments" asked, "guitar" said)
    # is reachable only through the semantic leg.
    widen = ob.kind in ("aggregate", "current_value", "preference", "existence", "single_fact", "when")
    if (missing or widen) and allow_hop and receipts:
        telemetry["hop_fired"] = True
        telemetry["hop_reason"] = "missing_operand" if missing else "widen_pool"
        covered = {t for _s, _r, f in selected for t in (f.get("slot") or [])}
        terms = [t for t in question_terms(question) if t not in covered] or question_terms(question)
        roles = ("assistant",) if ob.kind == "assistant_output" else None
        hop_query = " ".join(terms + extra)
        new_rows: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
        try:
            lexical = mem.occurrence_search(hop_query, chat_scope=str(chat_scope or ""), limit=12, roles=roles) or []
        except TypeError:
            lexical = mem.occurrence_search(hop_query, chat_scope=str(chat_scope or ""), limit=12) or []
        except Exception:
            lexical = []
        semantic: list = []
        # the semantic leg of the same hop (the question's own embedding over occurrence bodies, both roles): a
        # paraphrase ("instruments" asked, "guitar" and "drum set" said) is reachable only this way
        if q_vec and str(q_backend or "").startswith("ollama:"):
            try:
                floor = mem.semantic_floor(q_backend) if hasattr(mem, "semantic_floor") else None
                semantic = mem.occurrence_search_semantic(list(q_vec), chat_scope=str(chat_scope or ""), backend=q_backend, floor=floor, limit=12) or []
                telemetry["hop_semantic_hits"] = len(semantic)
            except Exception:
                telemetry["hop_semantic_hits"] = None
        # reciprocal-rank fusion of the two legs (their scores live on different scales); a turn adds at most two
        # facts so one long turn cannot fill the packet
        fused: dict[str, float] = {}
        occ_by_id: dict[str, Any] = {}
        for leg in (lexical, semantic):
            for rank, (occurrence, _score) in enumerate(leg, start=1):
                oid = str(getattr(occurrence, "occurrence_id", "") or "")
                if oid:
                    fused[oid] = fused.get(oid, 0.0) + 1.0 / (60 + rank)
                    occ_by_id.setdefault(oid, occurrence)
        hits = [(occ_by_id[oid], fused[oid] * 60.0) for oid in sorted(fused, key=lambda k: -fused[k])]
        qterms_hop = set(question_terms(question)) | {_stem(t) for t in extra}
        lexical_ids = {str(getattr(o, "occurrence_id", "") or "") for o, _s in lexical}
        semantic_ids = {str(getattr(o, "occurrence_id", "") or "") for o, _s in semantic}
        new_rows: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
        seen = {id(f) for _s, _r, f in selected}
        for occurrence, score in hits:
            oid = str(getattr(occurrence, "occurrence_id", "") or "")
            receipt = by_occ.get(oid)
            if receipt is None:
                continue
            if roles and receipt.get("role") not in roles:
                continue
            added = 0
            for fact in receipt.get("facts") or []:
                if id(fact) in seen or added >= 2:
                    continue
                if ob.kind in ("interval_ago", "interval_between", "order", "when") and fact.get("event_at") is None and fact.get("value_type") != "duration":
                    continue
                # when the hop only widens a weak query, a lexical-only hit must name the question in the fact
                # itself (one shared content term); for a missing operand the whole-turn match is the point
                if not missing and oid not in semantic_ids and not (set(fact.get("slot") or []) & qterms_hop):
                    continue
                if fact.get("value_type") in skip and fact.get("event_at") is None:
                    continue
                seen.add(id(fact)); added += 1
                new_rows.append((float(score), receipt, {**fact, "via_hop": True, "hop_leg": "semantic" if oid in semantic_ids else "lexical"}))
            if ob.kind == "sequence" and added == 0 and receipt.get("role") == "user":
                # a sequence ask is about WHEN each thing came up: a user turn that names one of the asked things but
                # carries no typed value is still a dated mention; its first sentence naming a question term is the row
                body = str(getattr(occurrence, "body", "") or getattr(occurrence, "text", "") or "")
                for sentence in re.split(r"(?<=[.!?])\s+", body):
                    if set(_slot_terms(sentence)) & qterms_hop:
                        mention = {"sentence": sentence.strip()[:240], "slot": _slot_terms(sentence), "value_type": "mention", "value": sentence.strip()[:240],
                                   "norm": "", "event_at": None, "event_grain": "", "via_hop": True, "hop_leg": "semantic" if oid in semantic_ids else "lexical"}
                        new_rows.append((float(score), receipt, mention)); break
        telemetry["hop_query"] = hop_query
        telemetry["hop_new_facts"] = len(new_rows)
        selected = selected + new_rows
        found, missing = _found_operands(ob, selected)
    if ob.kind == "current_value" and re.search(r"\b(?:right\s+now|currently|at\s+the\s+moment|these\s+days|nowadays|now)\b", question, re.I):
        # a live-reading ask: a past measured report ("the March log pegged the flow at 210 l/h") is not the current
        # value of anything; the current-observation contract keeps it out of the packet (after the hop, so a hop
        # cannot bring it back)
        selected = [row for row in selected if not (row[2].get("value_type") == "measure" and str(row[2].get("norm") or "").endswith("|past"))]
    # an extremum ask ("first", "latest") is answered from the edge of the dated scope: dated user facts render in time
    # order (earliest or latest first) so a partial view still carries the candidate that matters
    if ob.cls == "EXTREMUM" or ob.kind == "sequence":
        earliest = bool(_EARLIEST_RE.search(question)) or ob.kind == "sequence"
        def _when(row):
            f = row[2]; r = row[1]
            t = f.get("event_at") if f.get("event_at") is not None else r.get("statement_at")
            return float(t or 0)
        dated_rows = sorted([row for row in selected if row[1].get("role") == "user" and (row[2].get("event_at") is not None or row[2].get("value_type") in ("event", "mention") or ob.kind == "sequence")], key=_when, reverse=not earliest)
        rest = [row for row in selected if not any(row[2] is d[2] for d in dated_rows)]
        selected = dated_rows + rest
    # render within the allowance, best first; one line per (receipt, fact)
    used = 0
    seen_sentences: set[tuple[str, str]] = set()
    for score, receipt, fact in selected:
        if len(packet.lines) >= max_lines:
            break
        key = (str(receipt.get("receipt_id")), str(fact.get("sentence") or fact.get("value")))
        if key in seen_sentences:
            continue
        seen_sentences.add(key)
        line = _render_line(receipt, fact, by_id)
        cost = est(line + "\n")
        if used + cost > max_tokens:
            continue
        packet.lines.append(line)
        packet.facts.append({**fact, "receipt_id": receipt.get("receipt_id"), "occurrence_id": receipt.get("occurrence_id"),
                             "role": receipt.get("role"), "statement_at": receipt.get("statement_at"), "score": round(float(score), 3)})
        used += cost
    derived: list[str] = []
    if packet.lines:
        qterms = set(question_terms(question)) | {_stem(t) for t in extra}
        # operands: the user's own dated facts that the question names (a shared content term), or that the
        # targeted hop fetched for a missing operand (tied to the question through its whole turn)
        operands = [f for f in packet.facts if f.get("role") == "user" and f.get("event_at") is not None
                    and ((set(f.get("slot") or []) & qterms) or f.get("via_hop"))]
        derived = derived_lines(ob, operands, reference_day=reference_day, question_text_for_derivation=question)
        for line in derived:
            cost = est(line + "\n")
            if used + cost <= max_tokens + 120:
                packet.lines.append(line)
                used += cost
    # coverage: of every receipt fact in this chat that shares a subject term with the question (the scoped candidate
    # set), how many are in the packet. A class that needs coverage is complete only when the scope is exhausted.
    coverage = scoped_coverage(receipts, question, packet.facts, extra_terms=extra, state_key=state_key)
    complete = (not missing) and (coverage["exhaustive"] if ob.needs_coverage else True)
    if packet.lines or (ob.needs_coverage and not coverage["exhaustive"]):
        status = ("complete" if not missing else "incomplete: missing " + ", ".join(missing))
        line = f"- This question needs: {', '.join(ob.required)} ({ob.note}). Receipts found: {status}."
        if ob.kind == "sequence":
            line += " Answer shape: one line per item, each a short name of one topic or event with the day it first came up, earliest first, nothing bundled."
        if ob.needs_coverage:
            if coverage["exhaustive"]:
                line += f" Coverage: exhaustive, all {coverage['candidates']} matching records are shown."
            else:
                line += (f" Coverage: partial, {coverage['shown']} of {coverage['candidates']} matching records shown; "
                         + {"ABSENCE": "do not conclude that something was never said", "EXTREMUM": "do not conclude which was first or latest",
                            "LIST_ALL": "do not present the list as complete", "CURRENT_STATE": "do not conclude that no later change exists",
                            "AGGREGATE": "do not total or average as if every record were here"}[ob.cls] + ".")
        packet.lines.append(line)
    telemetry.update({"found": list(found), "missing": list(missing), "complete": complete, "obligation_class": ob.cls, "coverage": coverage})
    telemetry["derived_lines"] = derived
    telemetry["target_day"] = target_day.isoformat() if target_day else None
    packet.tokens = used + (est(PACKET_HEADER) if packet.lines else 0)
    telemetry.update({"packet_lines": len(packet.lines), "packet_tokens": packet.tokens})
    packet.telemetry = telemetry
    return packet


def render(packet: Packet) -> str:
    if not packet.lines:
        return ""
    return PACKET_HEADER + "\n" + "\n".join(packet.lines)


# ─── verification (labelling, never deletion) ─────────────────────────────────────────────────

_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
              "eleven": 11, "twelve": 12, "a": 1, "an": 1, "half": 0.5}
_ANSWER_INTERVAL_RE = re.compile(r"\b(?:about\s+|around\s+|roughly\s+|approximately\s+|~)?(?P<n>\d+(?:\.\d+)?|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|half)\s+(?:and\s+a\s+half\s+)?(?P<u>days?|weeks?|months?|years?|hours?|minutes?)\b", re.IGNORECASE)
_ANSWER_HM_RE = re.compile(r"\b(?P<h>\d{1,2}):(?P<m>[0-5]\d)\b")
_UNIT_DAYS = {"day": 1.0, "week": 7.0, "month": 30.4375, "year": 365.25}


def _answer_values(answer: str) -> list[tuple[str, float, str]]:
    """(kind, value, text) the answer asserts: intervals in days (kind 'days'), durations in minutes ('min')."""
    out: list[tuple[str, float, str]] = []
    for m in _ANSWER_INTERVAL_RE.finditer(str(answer or "")):
        n = _NUM_WORDS.get(m.group("n").lower())
        n = float(m.group("n")) if n is None else float(n)
        if "and a half" in m.group(0).lower():
            n += 0.5
        u = m.group("u").lower().rstrip("s")
        if u in _UNIT_DAYS:
            out.append(("days", n * _UNIT_DAYS[u], m.group(0)))
            out.append(("unit:" + u, n, m.group(0)))
        elif u in ("hour", "minute"):
            out.append(("min", n * (60.0 if u == "hour" else 1.0), m.group(0)))
    for m in _ANSWER_HM_RE.finditer(str(answer or "")):
        out.append(("min", int(m.group("h")) * 60.0 + int(m.group("m")), m.group(0)))
    return out


def _tolerance_days(unit: str) -> float:
    return {"day": 1.0, "week": 3.5, "month": 15.5, "year": 183.0}.get(unit, 1.0)


def verify_answer(question: str, raw_answer: str, packet_facts: Sequence[Mapping[str, Any]], *,
                  reference_day: date | None = None) -> dict[str, Any]:
    """Whether the raw reply's computed value derives from the packet's typed operands.

    Supported when: an interval in the reply equals the difference between two event days in the packet (or
    between an event day and the reference day), within the unit's rounding; or a duration equals the difference
    of two packet durations, or equals one packet value outright. Returns a decision dict; never edits text."""
    values = _answer_values(raw_answer)
    if not values:
        return {"verified": False, "supported": False, "chain_stage": "ASSERTED", "reason": "no_computed_value_in_reply"}
    days = sorted({date.fromtimestamp(float(f["event_at"]), tz=_UTC) if False else datetime.fromtimestamp(float(f["event_at"]), tz=_UTC).date()
                   for f in packet_facts if f.get("event_at") is not None})
    if reference_day is not None:
        days_with_ref = days + [reference_day]
    else:
        days_with_ref = days
    diffs: list[tuple[float, str]] = []
    for i, a in enumerate(days_with_ref):
        for b in days_with_ref[i + 1:]:
            diffs.append((abs((a - b).days), f"{a.isoformat()}..{b.isoformat()}"))
    durations = [float(str(f.get("norm") or "0min").rstrip("min") or 0) for f in packet_facts if f.get("value_type") == "duration"]
    dur_diffs = [(abs(x - y), f"{x:g}-{y:g} min") for i, x in enumerate(durations) for y in durations[i + 1:]]
    for kind, value, text in values:
        if kind.startswith("unit:"):
            unit = kind.split(":", 1)[1]
            per = _UNIT_DAYS[unit]
            for d, label in diffs:
                if abs(d - value * per) <= _tolerance_days(unit) and d > 0:
                    return {"verified": True, "supported": True, "chain_stage": "SUPPORTED", "rule": "interval_from_two_event_days", "value": text, "operands": label, "days": d}
        elif kind == "min":
            for d, label in dur_diffs:
                if d > 0 and abs(d - value) <= 1.0:
                    return {"verified": True, "supported": True, "chain_stage": "SUPPORTED", "rule": "duration_difference", "value": text, "operands": label}
            for x in durations:
                if abs(x - value) <= 0.5:
                    return {"verified": True, "supported": True, "chain_stage": "SUPPORTED", "rule": "duration_stated", "value": text, "operands": f"{x:g} min"}
    return {"verified": False, "supported": False, "chain_stage": "ASSERTED", "reason": "no_derivation_matched", "candidates": [t for _k, _v, t in values][:4],
            "event_days": [d.isoformat() for d in days][:8]}
