"""Temporal claim binder (v14.3): the past-time guard's decision on MEMORY answers, made from typed receipts.

The v14 past-time guard (core.model_output_guard.replace_unsupported_past_time_claims) reads the reply against the
evidence text lexically and withdraws a whole sentence when one temporal value in it has no stated source. On the
second sealed run that withdrew five correct answers: "16 April" derived from "two weeks ago" said on 30 April; "17 March
2025" against a yearless record whose typed event day carries the year; "10 weeks" from today back to that derived day;
and two replies whose first clause stated the recorded duration and whose second clause inferred a value for today.

This module binds every temporal value of the reply to the receipt packet's typed operands (event days, statement
days, durations, each fact's own relative offsets) and to the reference day, derives what the records derive
(relative days, intervals, weekday of a stated day, year qualification), and qualifies at CLAUSE level: a clause whose
values are all unsupported is dropped, the clause that answers the question stays when it is supported, and the
sentence is withdrawn only when the answering clause itself is unsupported. Never writes a value; never invents one.
Off (VOOL_EVIDENCE_KERNEL unset) nothing here runs and v14.1's verify_answer keeps its job.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

_UTC = timezone.utc
_MONTHS = {m: i + 1 for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"))}
_MONTHS.update({m[:3]: i for m, i in list(_MONTHS.items())})
_MONTHS["sept"] = 9
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_NUM_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
              "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
              "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "couple of": 2, "few": 3, "half a": 0.5}
_TENS = "twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety"
_ONES = "one|two|three|four|five|six|seven|eight|nine"
_UNIT_DAYS = {"day": 1.0, "week": 7.0, "month": 30.4375, "year": 365.25, "hour": 1 / 24.0, "minute": 1 / 1440.0}
_TOL_DAYS = {"day": 1.0, "week": 3.5, "month": 15.5, "year": 46.0, "hour": 0.5 / 24.0, "minute": 0.5 / 1440.0}
_MON = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DATE_RE = re.compile(
    rf"\b(?P<d1>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<m1>{_MON})\.?(?:,?\s+(?P<y1>(?:19|20)\d{{2}}))?\b"
    rf"|\b(?P<m2>{_MON})\.?\s+(?P<d2>\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(?P<y2>(?:19|20)\d{{2}}))?\b"
    r"|\b(?P<y3>(?:19|20)\d{2})-(?P<m3>\d{2})-(?P<d3>\d{2})\b",
    re.IGNORECASE)
_NUM = rf"(?:\d+(?:[.,]\d+)?|(?:{_TENS})[\s-](?:{_ONES})|{_TENS}|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|couple of|few|half a)"
_DUR_RE = re.compile(rf"\b(?:about\s+|around\s+|roughly\s+|approximately\s+|nearly\s+|almost\s+|just\s+over\s+|over\s+)?(?P<n>{_NUM})(?:\s*(?:–|-|to)\s*(?P<n2>\d+(?:\.\d+)?))?[\s-]+(?P<u>days?|weeks?|months?|years?|hours?|minutes?|mins?)\b", re.IGNORECASE)
_RATE_DENOMINATOR_RE = re.compile(r"(?:a|an|per|each|every)\s+(?:day|week|month|year|hour)", re.IGNORECASE)
_WEEKDAY_RE = re.compile(r"\b(" + "|".join(_WEEKDAYS) + r")\b", re.IGNORECASE)
_AGO_RE = re.compile(rf"\b(?P<n>{_NUM})\s+(?P<u>days?|weeks?|months?|years?)\s+ago\b", re.IGNORECASE)
_LAST_RE = re.compile(r"\b(?:last|this\s+past|the\s+previous)\s+(?P<u>week|month|year|weekend)\b|\byesterday\b", re.IGNORECASE)
_CLAUSE_SPLIT_RE = re.compile(r"\s+[—–]\s+|;\s+|,\s+(?=(?:so|which|that'?s|inferred|roughly|about|meaning|i\.e\.|or\b))|\s+\((?=[^)]*\))|\binferred\b", re.IGNORECASE)
_TRAILING_CONNECTOR_RE = re.compile(r"(?:\s*[—–,;:]?\s*(?:so|inferred|which|that'?s|roughly|about|meaning|i\.e\.|and|but|or)\b\s*[,;:]?)+\s*$", re.IGNORECASE)
# a question about a difference of two durations: a comparison word, or a margin against a goal or a record ("by how many
# minutes did I beat/miss my goal time", "how far off my target was I"); a plain "how many minutes did the run take" is not one
_DIFFERENCE_ASK_RE = re.compile(
    r"\b(?:longer|shorter|more|less|fewer|difference|differ|than|compared|gap|margin|faster|slower|ahead\s+of|behind|off\s+(?:my|the|your)|beat|beaten|missed?|short\s+of|over\s+(?:my|the)\s+(?:goal|target)|under\s+(?:my|the)\s+(?:goal|target))\b"
    r"|\bby\s+how\s+(?:many|much)\b", re.IGNORECASE)
_ASK_INTERVAL_RE = re.compile(r"\bhow\s+(?:many|much)\s+(?:days?|weeks?|months?|years?|time)\b|\bhow\s+long\s+(?:ago|before|after|between|since|until)\b", re.IGNORECASE)
_ASK_HOWLONG_RE = re.compile(r"\bhow\s+long\s+(?:have|has|had|did|do|does|were|was)\b", re.IGNORECASE)
_ASK_WEEKDAY_RE = re.compile(r"\b(?:which|what)\s+day\s+of\s+the\s+week\b|\bwhat\s+weekday\b", re.IGNORECASE)
_ASK_DATE_RE = re.compile(r"\bwhen\b|\b(?:which|what)\s+(?:day|date|month|year)\b|\bon\s+what\s+day\b", re.IGNORECASE)


def _num(s: str) -> float | None:
    s = s.strip().lower()
    if s in _NUM_WORDS:
        return float(_NUM_WORDS[s])
    cm = re.fullmatch(rf"({_TENS})[\s-]({_ONES})", s)
    if cm:
        return float(_NUM_WORDS[cm.group(1)] + _NUM_WORDS[cm.group(2)])
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None


def _day(ts: Any) -> date | None:
    try:
        return datetime.fromtimestamp(float(ts), tz=_UTC).date()
    except Exception:
        return None


@dataclass
class TValue:
    kind: str                 # date | weekday | interval | duration
    text: str
    start: int
    end: int
    day: date | None = None
    yearless: tuple[int, int] | None = None
    days: float | None = None   # interval/duration in days
    unit: str = ""
    status: str = "unsupported"
    rule: str = ""
    operands: str = ""
    ago: bool = False
    difference_ask: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "text": self.text, "status": self.status, "rule": self.rule, "operands": self.operands}


def reply_temporal_values(text: str) -> list[TValue]:
    out: list[TValue] = []
    taken: list[tuple[int, int]] = []
    for m in _DATE_RE.finditer(text):
        g = m.groupdict()
        try:
            if g.get("y3"):
                d = date(int(g["y3"]), int(g["m3"]), int(g["d3"])); out.append(TValue("date", m.group(0), m.start(), m.end(), day=d))
            else:
                mon = _MONTHS[(g.get("m1") or g.get("m2")).lower().rstrip(".")]; dd = int(g.get("d1") or g.get("d2")); yy = g.get("y1") or g.get("y2")
                if yy:
                    out.append(TValue("date", m.group(0), m.start(), m.end(), day=date(int(yy), mon, dd)))
                else:
                    out.append(TValue("date", m.group(0), m.start(), m.end(), yearless=(mon, dd)))
            taken.append((m.start(), m.end()))
        except (ValueError, KeyError):
            continue
    for m in _WEEKDAY_RE.finditer(text):
        out.append(TValue("weekday", m.group(0), m.start(), m.end()))
    last_duration_end = -1
    for m in _DUR_RE.finditer(text):
        if any(s <= m.start() < e for s, e in taken):
            continue
        if _RATE_DENOMINATOR_RE.fullmatch(m.group(0)) and last_duration_end >= 0 and not text[last_duration_end:m.start()].strip():
            # "an hour a day", "20 minutes a week": the second phrase is the rate's denominator, not a duration claim
            continue
        n = _num(m.group("n")); n2 = _num(m.group("n2")) if m.group("n2") else None
        if n is None:
            continue
        last_duration_end = m.end()
        unit = m.group("u").lower().rstrip("s")
        window = text[max(0, m.start() - 40): m.end() + 24]
        kind = "interval" if re.search(r"\bago\b|\bbefore\b|\bafter\b|\bbetween\b|\blater\b|\bearlier\b|\bapart\b", window, re.I) else "duration"
        ago = bool(re.match(r"\s*ago\b", text[m.end(): m.end() + 6], re.I))
        out.append(TValue(kind, m.group(0), m.start(), m.end(), days=n * _UNIT_DAYS[unit], unit=unit, ago=ago))
        if n2 is not None:  # "22–23 months": the range's second bound is a value of its own
            out.append(TValue(kind, m.group(0), m.start(), m.end(), days=n2 * _UNIT_DAYS[unit], unit=unit, ago=ago))
    return sorted(out, key=lambda v: v.start)


@dataclass
class Operands:
    days: dict[date, str] = field(default_factory=dict)        # day -> provenance
    day_text: dict[date, str] = field(default_factory=dict)    # event day -> the record's own words (for the actor check)
    durations: list[tuple[float, str, str]] = field(default_factory=list)  # (days, unit, provenance)


def packet_operands(packet_facts: Sequence[Mapping[str, Any]], reference_day: date | None) -> Operands:
    """Every day and duration the receipts state or imply. Relative offsets ("two weeks ago") resolve against the
    SAME fact's statement day only; nothing crosses facts."""
    ops = Operands()
    for f in packet_facts or []:
        if not isinstance(f, Mapping):
            continue
        if str(f.get("role") or "user") != "user":
            continue   # v14.6 item 6: an assistant's durations and days are not the user's operands
        line = str(f.get("sentence") or f.get("value") or f.get("text") or "")
        said = _day(f.get("statement_at")); ev = _day(f.get("event_at"))
        rid = str(f.get("receipt_id") or "")
        if ev is not None:
            # an event-grade day outranks a statement-day or reference-day registration of the same date (two records
            # can share a date: one said that day, the other dated its event to it)
            if ev not in ops.days or ops.days[ev].startswith("statement day") or ops.days[ev].startswith("reference day"):
                ops.days[ev] = f"event day of {rid}"
                ops.day_text[ev] = line
        if said is not None:
            ops.days.setdefault(said, f"statement day of {rid}")
            for m in _AGO_RE.finditer(line):
                n = _num(m.group("n"))
                if n is not None and m.group("u").lower().rstrip("s") in ("day", "week", "month", "year"):
                    unit = m.group("u").lower().rstrip("s")
                    ops.days.setdefault(said - timedelta(days=round(n * _UNIT_DAYS[unit])), f"{m.group(0)} from statement day {said.isoformat()} of {rid}")
            if re.search(r"\byesterday\b", line, re.I):
                ops.days.setdefault(said - timedelta(days=1), f"yesterday from {said.isoformat()} of {rid}")
        for m in _DUR_RE.finditer(line):
            n = _num(m.group("n"))
            if n is not None:
                unit = m.group("u").lower().rstrip("s"); unit = "minute" if unit == "min" else unit
                ops.durations.append((n * _UNIT_DAYS[unit], unit, f"{m.group(0)} stated in {rid}"))
        if str(f.get("value_type") or "") == "duration":
            try:
                mins = float(str(f.get("norm") or "").rstrip("min"))
                ops.durations.append((mins / 1440.0, "min", f"typed duration of {rid}"))
            except ValueError:
                pass
    if reference_day is not None:
        ops.days.setdefault(reference_day, "reference day (today)")
    return ops


# the reply frames a date as the day a thing was SAID (a session, "as of", "stated", "recorded", "first mentioned"), so a
# record's statement day supports it; "by 6 April" or "on 6 April" frame an event and need an event-grade day
_SESSION_FRAME_RE = re.compile(r"\b(?:session|as\s+of|said|told|mentioned|wrote|noted|stated|recorded|logged|reported|chat|conversation|on\s+your|you\s+(?:said|told|mentioned|stated|noted))\b[^.]{0,40}$", re.IGNORECASE)


_NAME_RE = re.compile(r"(?<![.!?]\s)\b([A-Z][a-z]{2,})\b")


def _actor_mismatch(question: str, record_text: str) -> bool:
    """The question names a person and the record names a different person only: 'When did Neri finish the mural?'
    is not dated by 'Theo finished his gallery mural on 19 May 2025' (port native case wrong-actor-date)."""
    q_names = {n.lower() for n in _NAME_RE.findall(" " + str(question or ""))} - {"when", "what", "which", "how", "did", "does", "the", "where", "who"}
    if not q_names:
        return False
    r_names = {n.lower() for n in _NAME_RE.findall(" " + str(record_text or ""))}
    return bool(r_names) and not (q_names & r_names)


def _bind_value(v: TValue, ops: Operands, reply_days: dict[date, str], *, before: str = "", question: str = "") -> None:
    all_days = {**ops.days, **reply_days}
    if v.kind == "date":
        # A date the reply states as an EVENT binds to an event-grade day (a typed event day, or a day a fact's own
        # offset resolves). A record's statement day supports the date only when the reply frames it as the session
        # ("as of your 28 January session"): the day a thing was said is not the day it happened.
        session_frame = bool(_SESSION_FRAME_RE.search(before))
        candidates = {d: why for d, why in ops.days.items() if session_frame or not (why.startswith("statement day") or why.startswith("reference day"))}
        candidates = {d: why for d, why in candidates.items() if not _actor_mismatch(question, ops.day_text.get(d, ""))}
        if v.day is not None:
            for d, why in candidates.items():
                if d == v.day:
                    v.status, v.rule, v.operands = "stated", "day_in_records", why; return
        elif v.yearless is not None:
            for d, why in candidates.items():
                if (d.month, d.day) == v.yearless:
                    v.day = d; v.status, v.rule, v.operands = "stated", "yearless_day_in_records", why; return
        return
    if v.kind == "weekday":
        # the weekday of an EVENT: event-grade days and the reply's own supported dates, never a bare statement day
        for d, why in all_days.items():
            if why.startswith("statement day") or why.startswith("reference day"):
                continue
            if _WEEKDAYS[d.weekday()] == v.text.lower():
                v.status, v.rule, v.operands = "derived", "weekday_of_supported_day", f"{d.isoformat()} ({why})"; return
        return
    if v.kind in ("interval", "duration"):
        tol = _TOL_DAYS.get(v.unit, 1.0)
        for d, u, why in ops.durations:
            if abs(d - v.days) <= (0.5 if v.unit == "day" else (tol if v.unit in ("hour", "minute") else tol / 4)):
                v.status, v.rule, v.operands = "stated", "duration_in_records", why; return
        # Operand pools: an "ago" interval counts back from the reference day to an event-grade day; any other
        # interval is between two event-grade days (event days, relative days resolved from a fact's own offset,
        # or days the reply itself states and the records support). A plain statement day is not an event: two
        # unrelated statement days 70 days apart must not support "10 weeks".
        event_grade = {d: why for d, why in all_days.items() if not why.startswith("statement day") and not why.startswith("reference day")}
        ref = next((d for d, why in all_days.items() if why.startswith("reference day")), None)
        pairs: list[tuple[date, date]] = []
        if v.ago and ref is not None:
            pairs = [(d, ref) for d in event_grade if d < ref]
        elif not v.ago:
            days = sorted(event_grade)
            pairs = [(a, b) for i, a in enumerate(days) for b in days[i + 1:]]
        for a, b in pairs:
            diff = (b - a).days
            if diff > 0 and abs(diff - v.days) <= tol:
                v.status, v.rule = "derived", "interval_between_supported_days"
                v.operands = f"{a.isoformat()} ({all_days[a]}) .. {b.isoformat()} ({all_days[b]}) = {diff} days"; return
        if v.difference_ask:
            # only a question about a difference ("how much longer", "the difference between") derives one; a
            # plain "how many days was the trip" is a stated value or nothing
            for d1, _u1, w1 in ops.durations:
                for d2, _u2, w2 in ops.durations:
                    if d1 > d2 and abs((d1 - d2) - v.days) <= tol:
                        v.status, v.rule, v.operands = "derived", "difference_of_stated_durations", f"{w1} - {w2}"; return


def _asked_kind(question: str) -> str:
    q = str(question or "")
    if _ASK_WEEKDAY_RE.search(q):
        return "weekday"
    if _ASK_INTERVAL_RE.search(q):
        return "interval"
    if _ASK_HOWLONG_RE.search(q):
        return "duration"
    if _ASK_DATE_RE.search(q):
        return "date"
    return ""


@dataclass
class TemporalBinding:
    attempted: bool
    reason: str
    text: str                       # the reply to ship (qualified), or "" when the sentence must stay withdrawn
    values: list[TValue] = field(default_factory=list)
    dropped_clauses: list[str] = field(default_factory=list)
    answering_value: str = ""

    @property
    def restored(self) -> bool:
        return bool(self.text)

    def as_dict(self) -> dict[str, Any]:
        return {"attempted": self.attempted, "reason": self.reason, "restored": self.restored, "answering_value": self.answering_value,
                "values": [v.as_dict() for v in self.values], "dropped_clauses": list(self.dropped_clauses)}


def bind_temporal_claims(*, question: str, reply: str, packet_facts: Sequence[Mapping[str, Any]], reference_day: date | None) -> TemporalBinding:
    """Bind the reply's temporal values to the packet and qualify at clause level. Returns text="" when the clause that
    answers the question is itself unsupported (the guard's withdrawal stands)."""
    reply = str(reply or "").strip()
    if not reply or not packet_facts:
        return TemporalBinding(False, "no_reply_or_no_packet", "")
    ops = packet_operands(packet_facts, reference_day)
    values = reply_temporal_values(reply)
    if not values:
        return TemporalBinding(False, "no_temporal_value_in_reply", "")
    # two passes: stated days of the reply first, so a weekday or an interval can bind to a day the reply itself states
    for v in values:
        if v.kind == "date":
            _bind_value(v, ops, {}, before=reply[max(0, v.start - 48): v.start], question=str(question or ""))
    reply_days = {v.day: f"reply date {v.text}" for v in values if v.kind == "date" and v.day is not None and v.status != "unsupported"}
    diff_ask = bool(_DIFFERENCE_ASK_RE.search(str(question or "")))
    for v in values:
        if v.kind != "date":
            v.difference_ask = diff_ask
            _bind_value(v, ops, reply_days)
    asked = _asked_kind(question)
    # clause-level qualification over the first sentence (memory answers are one sentence; later sentences follow the same rule)
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", reply)
    kept: list[str] = []; dropped: list[str] = []; answering = ""
    for s_index, sentence in enumerate(sentences):
        offset = reply.index(sentence)
        bounds = [0] + [m.start() for m in _CLAUSE_SPLIT_RE.finditer(sentence)] + [len(sentence)]
        clauses = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]
        good: list[str] = []
        if s_index == 0:
            # the answering value: the first value of the asked kind anywhere in the first sentence ("...26 March
            # 2025, which was a Wednesday" answers a weekday ask in its second clause)
            in_sentence = [v for v in values if offset <= v.start < offset + len(sentence)]
            same = {"interval": ("interval", "duration"), "duration": ("duration", "interval")}.get(asked, (asked,))
            primary = next((v for v in in_sentence if v.kind in same), None) if asked else (in_sentence[0] if in_sentence else None)
            answering = primary.text if primary else ""
            if primary is not None and primary.status == "unsupported":
                return TemporalBinding(True, "answering_value_unsupported", "", values, [], answering)
            if primary is None and in_sentence and all(v.status == "unsupported" for v in in_sentence):
                return TemporalBinding(True, "first_sentence_unsupported", "", values, [], answering)
        for c_index, (a, b) in enumerate(clauses):
            inside = [v for v in values if offset + a <= v.start < offset + b]
            unsupported = [v for v in inside if v.status == "unsupported"]
            if s_index == 0 and primary is not None and any(v is primary for v in inside) and unsupported:
                # the answering clause itself carries an unsupported value beside the answer: withdraw, do not trim
                return TemporalBinding(True, "answering_clause_has_unsupported_value", "", values, [], answering)
            if unsupported:
                dropped.append(sentence[a:b].strip(" ,;—–(")); continue
            good.append(sentence[a:b])
        if good:
            text = "".join(good).strip()
            if len(good) < len(clauses):
                text = _TRAILING_CONNECTOR_RE.sub("", text)
                if text.count("(") > text.count(")"):
                    text = text[: text.rfind("(")].rstrip()
            text = text.strip().strip(",;—– ").strip()
            if text and text[-1] not in ".!?":
                text += "."
            kept.append(text)
    if not kept or not answering:
        return TemporalBinding(True, "nothing_kept" if not kept else "no_answering_value", "", values, dropped, answering)
    return TemporalBinding(True, "bound", " ".join(kept), values, dropped, answering)
