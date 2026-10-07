"""Memory receipts: one appended, never-rewritten record per stored turn, beside the verbatim occurrence.

Why (LongMemEval diagnosis, 2026-10-06): the store keeps every turn verbatim (layer 1) and decides at READ time,
per question, which record replaced which (core.temporal_selection). Nothing writes that decision down, the answer
finalization names no evidence (``evidence_references`` was empty on 280 of 280 benchmark answers), and a turn's
own dating ("three weeks ago I fixed the fence") is never resolved to a calendar day at write time, so every
temporal question re-derives it in the reader's head. A receipt fixes the three at write time, once, by rule:

  RECEIPT  r:…  for occurrence u:…  said 2023-05-29 by user
  SAID     "I've been dedicating about two hours each day to coding exercises…"
  FACTS    [coding exercises / day] duration = "about two hours"  (as of 2023-05-29)
  CHANGES  [coding exercises / day] "about an hour" (u:33, 2023-05-20) -> "about two hours"   replaced
  WITHDRAWS none
  --- machine part: facts/changes/withdraws JSON, event dates, writer, cost ---

Rules the module keeps:
* Lossless evidence, lossy indexes: the receipt is a derivative of the occurrence; the body is never edited.
* A change needs a CONFLICT inside one slot by the same role: disjoint values of the same kind (the temporal
  contract's law), never shared words alone. An assistant echo never replaces a user statement.
* An event date comes from the turn's own words resolved against the turn's statement time, never from the
  machine clock; a record that dates nothing keeps ``event_at`` empty (unknown, never guessed).
* The writer is rule-based and costs no model call; ``writer`` and ``cost`` are recorded so a model-written
  receipt (not implemented here) stays distinguishable.

Switched on by VOOL_MEMORY_RECEIPTS=1 (NULLA_MEMORY_RECEIPTS honoured); off, no table is touched and v14
behaves exactly as before.
"""
from __future__ import annotations

import calendar
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from core.temporal_selection import (
    _stem,
    _value_conflict,
    retraction_marker,
    slot_signature,
    value_tokens,
)

LOGGER = logging.getLogger(__name__)
_UTC = timezone.utc
WRITER = "receipt-rules-v1"
ENV_NAMES = ("VOOL_MEMORY_RECEIPTS", "NULLA_MEMORY_RECEIPTS")

SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_receipts (
    receipt_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    chat_scope TEXT NOT NULL,
    occurrence_id TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL,
    speaker TEXT NOT NULL DEFAULT '',
    statement_at REAL,
    event_at REAL,
    event_grain TEXT NOT NULL DEFAULT '',
    facts_json TEXT NOT NULL,
    changes_json TEXT NOT NULL,
    withdraws_json TEXT NOT NULL,
    replaced_by_json TEXT NOT NULL DEFAULT '[]',
    head_text TEXT NOT NULL,
    writer TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    cost_calls INTEGER NOT NULL DEFAULT 0,
    cost_tokens INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_receipts_scope ON memory_receipts(agent_id, chat_scope, statement_at);
"""


def enabled(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    for name in ENV_NAMES:
        value = str(env.get(name, "") or "").strip().lower()
        if value:
            return value in ("1", "true", "yes", "on")
    return False


# ─── value and date grammar (closed classes) ───────────────────────────────────────────────────

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "a": 1, "an": 1, "couple": 2, "few": 3, "half": 0.5,
}
_TENS = "twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety"
_ONES = "one|two|three|four|five|six|seven|eight|nine"
_COMPOUND_NUM = rf"(?:{_TENS})[\s-](?:{_ONES})"   # forty-five, twenty five
_NUM = rf"(?:\d+(?:\.\d+)?|{_COMPOUND_NUM}|{_TENS}|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|a couple of|couple of|a few|few|half an?)"
_UNIT_DAY = r"(?:days?|weeks?|months?|years?|hours?|minutes?|mins?|hrs?)"
_AGO_RE = re.compile(rf"\b(?P<n>{_NUM})\s+(?P<u>{_UNIT_DAY})\s+ago\b", re.IGNORECASE)
_LAST_RE = re.compile(r"\b(?:last|this past|the past)\s+(?P<u>night|week|weekend|month|year|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.IGNORECASE)
_DEICTIC_DAY_RE = re.compile(r"\b(?P<w>yesterday|today|tonight|this morning|this afternoon|this evening|the day before yesterday|earlier today|the other day)\b", re.IGNORECASE)
#: "recently" / "just <did>" / "this week": the event is close to the statement day without a day of its own; dated to
#: the statement day at grain "recent" so ordering against a dated event still works, never for arithmetic.
_RECENT_RE = re.compile(r"\b(?:recently|just\s+(?:got|came|came back|returned|finished|started|bought|upgraded|set up|installed|moved|signed|booked|had|went|did|ran|completed|adopted|received|joined)|this\s+week|earlier\s+this\s+week|a\s+few\s+days\s+ago|couple\s+of\s+days\s+ago)\b", re.IGNORECASE)
_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1)}
_MONTHS.update({m[:3]: i for m, i in list(_MONTHS.items())})
_MONTHS.update({"sept": 9})
_MONTH_RE = "|".join(sorted(_MONTHS, key=len, reverse=True))
_EXPLICIT_DATE_RE = re.compile(
    rf"\b(?:on\s+|since\s+|from\s+|until\s+|by\s+|at\s+)?(?P<d>(?:{_MONTH_RE})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+(?:19|20)\d{{2}})?"
    rf"|\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:{_MONTH_RE})\.?(?:,?\s+(?:19|20)\d{{2}})?"
    rf"|(?:19|20)\d{{2}}-\d{{2}}-\d{{2}}|\d{{4}}/\d{{2}}/\d{{2}})\b", re.IGNORECASE)
_DURATION_HM_RE = re.compile(r"\b(?P<h>\d{1,2}):(?P<m>[0-5]\d)\b(?!\s*(?:am|pm|a\.m\.|p\.m\.))", re.IGNORECASE)
_DURATION_WORDS_RE = re.compile(
    rf"\b(?P<n>{_NUM})\s+(?P<u>hours?|hrs?|minutes?|mins?|days?|weeks?|months?|years?|seconds?)\b(?!\s+ago)"
    rf"(?:\s+(?:and|&)\s+(?P<n2>{_NUM})\s+(?P<u2>minutes?|mins?|hours?|days?))?", re.IGNORECASE)
_AMOUNT_RE = re.compile(r"(?:\$|€|£)\s?(?P<v>\d[\d,]*(?:\.\d+)?)(?:\s?(?:k|K|million|m)\b)?|\b(?P<v2>\d[\d,]*(?:\.\d+)?)\s+(?:dollars|euros|pounds|bucks|usd|eur|gbp)\b", re.IGNORECASE)
_CLOCK_RE = re.compile(r"\b(?P<h>\d{1,2})(?::(?P<m>[0-5]\d))?\s?(?P<ap>am|pm|a\.m\.|p\.m\.)\b", re.IGNORECASE)
_AGE_RE = re.compile(r"\b(?:i(?:'m| am)|turned|turning|i was|is|was|he's|she's|they're)\s+(?P<n>\d{1,3})(?:\s+years?\s+old)?\b|\b(?P<n2>\d{1,3})\s+years?\s+old\b|\b(?:my|her|his|their)\s+(?P<who>\w+)\s+is\s+(?P<n3>\d{1,3})\b", re.IGNORECASE)
_COUNT_RE = re.compile(rf"\b(?P<n>\d+|{'|'.join(k for k in _NUMBER_WORDS if k not in ('a', 'an', 'half'))})\s+(?:new\s+|more\s+|different\s+|other\s+)?(?P<noun>[a-z][a-z\-]{{2,}})\b(?!\s+ago)(?!\s*(?:am|pm|:))", re.IGNORECASE)
_LIST_ITEM_RE = re.compile(r"(?m)^\s*(?:(?P<n>\d{1,2})[.)]|[-*•])\s+(?P<item>\S[^\n]*)")
#: Preferences the user states about themselves (closed cues): diet, allergies, likes and dislikes, style, travel mode.
_PREFERENCE_ACK_RE = re.compile(r"\bi\s+(?:love|like|really\s+like)\s+(?:the|these|those|that|your|all\s+(?:of\s+)?(?:these|those)|this)\s+(?:\w+\s+){0,2}(?:idea|ideas|suggestion|suggestions|concept|tips?|plan|approach|options?|recipes?|rule|one|thought|list)\b|\bi\s+(?:love|like)\s+(?:these|those|that|it|how|that\s+you)\b|\bi\s+(?:love|like)\s+the\s+thought\s+of\b", re.IGNORECASE)
_PREFERENCE_RE = re.compile(r"\b(?:(?:i|we)\s+(?:prefer|like|love|enjoy|hate|dislike|avoid|can't\s+stand|always\s+(?:choose|go\s+for|pick|take)|never\s+(?:eat|drink|wear|buy|fly))|i(?:'m|\s+am)\s+(?:allergic|intolerant|vegetarian|vegan|pescatarian|a\s+vegetarian|a\s+vegan|strictly)|i\s+(?:eat|follow|keep|stick\s+to|am\s+on|'m\s+on)\s+(?:a\s+)?(?:strictly\s+|mostly\s+)?[\w\-]+\s+diet|my\s+(?:style|taste|preference|preferences|go-to)\s+(?:is|are)|free\s+of|no\s+(?:soy|gluten|dairy|nuts|meat|patterns|prints)\b|rather\s+than|over\s+(?:flying|driving|taking)|i\s+(?:only|mostly|usually)\s+(?:wear|eat|drink|buy|listen|travel|take)|i\s+dress\s+in|(?:need\s+to\s+|have\s+to\s+|try\s+to\s+|make\s+sure\s+to\s+)?avoid\s+(?:anything\s+with|all|any)\b|i\s+(?:don't|dont|do\s+not|can't|cant|cannot|never)\s+(?:eat|drink|have|wear|tolerate|do|touch|use)\b|allerg(?:y|ies|ic)|intoleran(?:t|ce)|my\s+wardrobe|i(?:'m|m|\s+am)\s+(?:an?\s+)?(?:early\s+riser|early\s+bird|morning\s+person|night\s+owl|late\s+sleeper)|who\s+(?:likes?|loves?|prefers?|hates?|avoids?|dislikes?)\s+to\b|i\s+(?:like|love|prefer|want|try)\s+to\s+(?:finish|start|work\s+out|exercise|train|run|eat|sleep|wake)\b[^.?!]{0,60}\b(?:before|after|by|around)\s+(?:\w+\s+){0,2}(?:morning|evening|noon|night|am|pm|o'clock|\d))\b", re.IGNORECASE)
#: State the user holds (closed verbs) with the object phrase as the value: what they drive, do, live in, own.
_FORMER_STATE_RE = re.compile(r"\b(?:i|we)\s+used\s+to\s+(?P<verb>drive|work\s+as|work\s+at|live\s+in|play\s+for|study\s+at|commute\s+by)\s+(?:a\s+|an\s+|the\s+)?(?P<obj>[A-Za-z][\w'\-]*(?:\s+[A-Za-z0-9][\w'\-]*){0,4})", re.IGNORECASE)
_STATE_RE = re.compile(r"\b(?:(?:i|we)\s+(?:now\s+|currently\s+|still\s+|also\s+|mostly\s+|usually\s+|mainly\s+)?(?P<verb>drive|work\s+as|work\s+at|live\s+in|own|use|rent|play\s+for|study\s+at|commute\s+by)|(?:i|we)\s+(?:just\s+|recently\s+)?(?P<verb2>moved\s+to|switched\s+to|started\s+(?:working\s+)?as|got\s+a\s+new\s+job\s+as|bought\s+a\s+new|replaced\s+my\s+\w+\s+with|upgraded\s+to|changed\s+my\s+\w+\s+to|took\s+a\s+(?:new\s+)?job\s+as|became)|my\s+(?:new\s+)?(?P<noun>car|job|role|city|apartment|flat|home|phone|laptop|bike|employer|team)\s+is(?:\s+now)?)\s+(?:a\s+|an\s+|the\s+)?(?P<obj>[A-Za-z][\w'\-]*(?:\s+[A-Za-z0-9][\w'\-]*){0,4})", re.IGNORECASE)
_TRANSITION_RE = re.compile(r"\b(?:(?:got|bought|picked\s+up|have)\s+(?:a|an|my)\s+(?:new\s+)?(?P<new1>[A-Za-z][\w'\-]*(?:\s+[A-Za-z0-9][\w'\-]*){0,3})\s+(?:after|when)\s+trading\s+in\s+(?:my\s+)?(?:old\s+)?(?P<old1>[A-Za-z][\w'\-]*(?:\s+[A-Za-z0-9][\w'\-]*){0,3})"
                            r"|(?:transition(?:ed|ing)?|moved|switched|changed|went|moving|switching)\s+(?:from\s+(?:being\s+)?(?:a\s+|an\s+|my\s+)?(?P<old2>[A-Za-z][\w'\-]*(?:\s+[A-Za-z0-9][\w'\-]*){0,3})\s+)?to\s+(?:being\s+|becoming\s+)?(?:a\s+|an\s+)?(?P<new2>[A-Za-z][\w'\-]*(?:\s+[A-Za-z0-9][\w'\-]*){0,3})"
                            r"|(?:traded|swapped)\s+(?:in\s+)?(?:my\s+)?(?P<old3>[A-Za-z][\w'\-]*(?:\s+[A-Za-z0-9][\w'\-]*){0,3})\s+for\s+(?:a\s+|an\s+)?(?P<new3>[A-Za-z][\w'\-]*(?:\s+[A-Za-z0-9][\w'\-]*){0,3}))", re.IGNORECASE)
_STATE_KEYS = {"drive": "vehicle", "bought a new": "vehicle?", "replaced my": "replace", "work as": "job", "work at": "employer", "started as": "job", "started working as": "job",
               "got a new job as": "job", "took a job as": "job", "took a new job as": "job", "became": "job", "live in": "home_city", "moved to": "home_city", "own": "owns",
               "use": "uses", "rent": "rents", "play for": "team", "study at": "school", "commute by": "commute", "switched to": "switch", "upgraded to": "switch", "changed my": "switch"}
_STATE_NOUN_KEYS = {"car": "vehicle", "job": "job", "role": "job", "city": "home_city", "apartment": "home", "flat": "home", "home": "home", "phone": "phone", "laptop": "laptop", "bike": "bike", "employer": "employer", "team": "team"}
_STATE_STOP_OBJ = frozenset("lot bit little long time while much more way home there here now today".split())
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])|\n+")
_ENVELOPE_RE = re.compile(r"^\s*Session date:\s*(?P<d>\d{4}/\d{2}/\d{2})[^\n]*\n?", re.IGNORECASE)
_STOP = frozenset("the a an and or of to in on at for with is was were be been are i my me you your it its this that these those as by from about not no yes also they them their he she his her we our us have has had do does did will would can could should may might than then there here when where which who what how into over after before during up down out off so if but just really very some any all each every both get got been being am".split())
_COUNT_NOUN_STOP = frozenset("days weeks months years hours minutes mins hrs seconds times time ago am pm percent dollars euros pounds year month week day hour minute".split())


@dataclass
class Fact:
    sentence: str
    slot: list[str]
    value_type: str
    value: str
    norm: str
    event_at: float | None = None
    event_grain: str = ""
    span: tuple[int, int] = (0, 0)
    replaced_by: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"sentence": self.sentence, "slot": list(self.slot), "value_type": self.value_type, "value": self.value,
                "norm": self.norm, "event_at": self.event_at, "event_grain": self.event_grain, "span": list(self.span),
                "replaced_by": self.replaced_by}


def _number(text: str) -> float | None:
    t = str(text or "").strip().lower()
    if t in ("a couple of", "couple of"):
        return 2.0
    if t in ("a few", "few"):
        return 3.0
    if t in ("half an", "half a"):
        return 0.5
    if t in _NUMBER_WORDS:
        return float(_NUMBER_WORDS[t])
    cm = re.fullmatch(rf"({_TENS})[\s-]({_ONES})", t)
    if cm:
        return float(_NUMBER_WORDS[cm.group(1)] + _NUMBER_WORDS[cm.group(2)])   # forty-five = 45, not five
    try:
        return float(t.replace(",", ""))
    except ValueError:
        return None


def _unit_days(unit: str) -> float | None:
    u = unit.lower().rstrip("s")
    return {"day": 1.0, "week": 7.0, "month": 30.4375, "year": 365.25}.get(u)


def _duration_subkind(value_text: str) -> str:
    """'span' for calendar spans (days, weeks, months, years), 'effort' for seconds, minutes and hours; a habit's
    daily hours and a plan's "next few weeks" are different measures and never conflict."""
    return "span" if re.search(r"\b(?:days?|weeks?|months?|years?)\b", str(value_text or ""), re.IGNORECASE) else "effort"


def _minutes(n: float, unit: str) -> float | None:
    u = unit.lower().rstrip("s")
    return {"second": n / 60.0, "minute": n, "min": n, "hour": n * 60.0, "hr": n * 60.0,
            "day": n * 1440.0, "week": n * 10080.0, "month": n * 43830.0, "year": n * 525960.0}.get(u)


def _statement_day(statement_at: float | None) -> date | None:
    if statement_at is None:
        return None
    try:
        return datetime.fromtimestamp(float(statement_at), tz=_UTC).date()
    except (OverflowError, OSError, ValueError):
        return None


def _epoch(day: date) -> float:
    return float(calendar.timegm(datetime(day.year, day.month, day.day, tzinfo=_UTC).timetuple()))


def _strip_envelope(body: str) -> tuple[str, float | None]:
    """A benchmark import prefixes the turn with its session date; keep the text clean and read the date."""
    text = str(body or "")
    match = _ENVELOPE_RE.match(text)
    if not match:
        return text, None
    try:
        day = datetime.strptime(match.group("d"), "%Y/%m/%d").date()
    except ValueError:
        return text[match.end():], None
    return text[match.end():], _epoch(day)


def resolve_event_day(sentence: str, statement_day: date | None) -> tuple[date | None, str]:
    """The calendar day a sentence dates its own event to, with its grain (day, week, month, year, explicit).

    Relative phrases count back from the statement day; an explicit date resolves in the statement's year (the
    most recent past reading). Two different relative phrases in one sentence are ambiguous: nothing is dated."""
    text = str(sentence or "")
    found: list[tuple[date, str]] = []
    if statement_day is not None:
        for m in _DEICTIC_DAY_RE.finditer(text):
            w = m.group("w").lower()
            off = 2 if ("before yesterday" in w or w == "the other day") else (1 if w == "yesterday" else 0)
            found.append((statement_day - timedelta(days=off), "day"))
        for m in _AGO_RE.finditer(text):
            n = _number(m.group("n")); per = _unit_days(m.group("u"))
            if n is None or per is None:
                continue  # hours/minutes ago: same day
            grain = m.group("u").lower().rstrip("s")
            found.append((statement_day - timedelta(days=round(n * per)), grain if grain != "day" else "day"))
        for m in _LAST_RE.finditer(text):
            u = m.group("u").lower()
            if u == "night":
                found.append((statement_day - timedelta(days=1), "day"))
            elif u == "week":
                found.append((statement_day - timedelta(days=7), "week"))
            elif u == "weekend":
                back = (statement_day.weekday() - 5) % 7 or 7
                found.append((statement_day - timedelta(days=back), "weekend"))
            elif u == "month":
                found.append((statement_day - timedelta(days=30), "month"))
            elif u == "year":
                found.append((statement_day - timedelta(days=365), "year"))
            else:
                names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
                back = (statement_day.weekday() - names.index(u)) % 7 or 7
                found.append((statement_day - timedelta(days=back), "day"))
    prefer_past = bool(_PAST_CUE_RE.search(text))
    for m in _EXPLICIT_DATE_RE.finditer(text):
        resolved_day = _explicit_day(m.group("d"), statement_day, prefer_past=prefer_past)
        if resolved_day is not None:
            found.append((resolved_day, "explicit"))
    if not found and statement_day is not None and _RECENT_RE.search(text):
        return statement_day, "recent"
    if not found:
        return None, ""
    days = {d for d, _g in found}
    if len(days) != 1:
        explicit = [(d, g) for d, g in found if g == "explicit"]
        if len({d for d, _g in explicit}) == 1:
            return explicit[0]
        return None, "ambiguous"
    grains = [g for _d, g in found]
    return found[0][0], ("explicit" if "explicit" in grains else grains[0])


_MD_RE = re.compile(rf"(?:(?P<mon1>{_MONTH_RE})\.?\s+(?P<d1>\d{{1,2}})(?:st|nd|rd|th)?|(?P<d2>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<mon2>{_MONTH_RE})\.?)(?:,?\s+(?P<y>(?:19|20)\d{{2}}))?", re.IGNORECASE)


_PAST_CUE_RE = re.compile(r"\b(?:just|ago|last|went|attended|participated|bought|visited|was|were|had|did|got|finished|ran|took|saw|met|started|moved|signed|booked|returned|completed|adopted|joined|celebrated|received|donated|volunteered|graduated|hosted|threw)\b", re.IGNORECASE)


def _explicit_day(phrase: str, statement_day: date | None, *, prefer_past: bool = False) -> date | None:
    """A written calendar day. ISO and Y/M/D carry their year; a month-day phrase without a year resolves in the
    statement's year, or the neighbouring year when that reading lies more than half a year from the statement
    (a May record saying "on January 22nd" means the January before it; a December record saying "on January
    5th" means the January after it). Without a statement day the phrase stays undated."""
    text = str(phrase or "").strip()
    iso = re.fullmatch(r"(\d{4})[-/](\d{2})[-/](\d{2})", text)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            return None
    m = _MD_RE.search(text)
    if not m:
        return None
    month = _MONTHS[(m.group("mon1") or m.group("mon2")).lower()[:3]] if (m.group("mon1") or m.group("mon2")).lower()[:3] in _MONTHS else _MONTHS.get((m.group("mon1") or m.group("mon2")).lower())
    day = int(m.group("d1") or m.group("d2"))
    if m.group("y"):
        try:
            return date(int(m.group("y")), month, day)
        except ValueError:
            return None
    if statement_day is None:
        return None
    best: date | None = None
    candidates = []
    for year in (statement_day.year - 1, statement_day.year, statement_day.year + 1):
        try:
            candidates.append(date(year, month, day))
        except ValueError:
            continue
    if prefer_past:
        # a sentence that narrates a completed event ("I just ran ... on May 1st") means the most recent past reading
        past = [c for c in candidates if c <= statement_day]
        if past:
            return max(past)
    for candidate in candidates:
        if best is None or abs((candidate - statement_day).days) < abs((best - statement_day).days):
            best = candidate
    return best


def _slot(sentence: str) -> list[str]:
    sig = slot_signature(sentence)
    return sorted(t for t in sig if t not in _STOP and len(t) > 1)


_CLAUSE_BOUNDARY_RE = re.compile(r",?\s+(?:and|but|while|then|after which|before that)\s+(?=(?:i|we|my|then|just|also|afterwards?|recently|yesterday|last)\b)|;\s+", re.IGNORECASE)


def _clause_event_days(sentence: str, statement_day: date | None) -> list[tuple[int, int, date | None, str]]:
    """(start, end, event day, grain) per clause of a sentence in which at least two clauses carry their own time
    phrase and those phrases name different days. Empty otherwise, so single-event sentences are untouched."""
    bounds = [0] + [m.start() for m in _CLAUSE_BOUNDARY_RE.finditer(sentence)] + [len(sentence)]
    if len(bounds) < 3:
        return []
    out = []
    for i in range(len(bounds) - 1):
        a, b = bounds[i], bounds[i + 1]
        d, g = resolve_event_day(sentence[a:b], statement_day)
        out.append((a, b, d, g))
    days = {d for _a, _b, d, _g in out if d is not None}
    if len(days) < 2:
        return []
    return out


def extract_facts(body: str, statement_at: float | None, role: str) -> list[Fact]:
    """Typed values a turn asserts, one Fact per (sentence, value): durations, amounts, counts, ages, clock times,
    and dated events (a sentence whose own words date an event, even without another value)."""
    text, envelope_at = _strip_envelope(body)
    stmt_day = _statement_day(statement_at if statement_at is not None else envelope_at)
    facts: list[Fact] = []
    pos = 0
    items = [m.group("item").strip() for m in _LIST_ITEM_RE.finditer(text)]
    if len(items) >= 3:
        first = text.strip().split("\n", 1)[0].strip()
        facts.append(Fact(sentence=first[:200], slot=_slot(first + " " + " ".join(items[:6])), value_type="list",
                          value=json.dumps(items), norm=f"{len(items)} items", span=(0, len(text))))
    for raw in _SENTENCE_RE.split(text):
        sentence = raw.strip()
        if not sentence:
            continue
        start = text.find(sentence, pos); pos = start + len(sentence) if start >= 0 else pos
        span = (start, start + len(sentence)) if start >= 0 else (0, 0)
        slot = _slot(sentence)
        # Only the user's own words date an event: an assistant reply's "today" or "last week" is generic prose
        # (measured on a real history: "the diversity of species today" became a dated event).
        event_day, grain = resolve_event_day(sentence, stmt_day) if role == "user" else (None, "")
        # v14.3: a sentence narrating two events with two time phrases ("I went to the dentist on 25 April and just
        # got back two weeks ago from Groningen") dates each CLAUSE on its own, and a value takes the day of the
        # clause it sits in (set two miss q33b4b: both facts carried the dentist's day, the derived interval was wrong).
        clause_days = _clause_event_days(sentence, stmt_day) if role == "user" else []
        if clause_days:
            event_day, grain = None, "per-clause"
        event_at = _epoch(event_day) if event_day is not None else None
        seen: set[tuple[str, str]] = set()

        def add(value_type: str, value: str, norm: str, *, at: int | None = None) -> None:
            key = (value_type, norm)
            if key in seen:
                return
            seen.add(key)
            f_day, f_grain = event_day, grain
            if clause_days:
                f_day, f_grain = None, ""
                for a, b, d, g in clause_days:
                    if at is not None and a <= at < b:
                        f_day, f_grain = d, g
                        break
            facts.append(Fact(sentence=sentence, slot=slot, value_type=value_type, value=value.strip(), norm=norm,
                              event_at=_epoch(f_day) if f_day is not None else None, event_grain=f_grain, span=span))

        for m in _AMOUNT_RE.finditer(sentence):
            v = m.group("v") or m.group("v2")
            n = _number(v)
            if n is not None:
                add("amount", m.group(0), f"{n:g}", at=m.start())
        for m in _DURATION_HM_RE.finditer(sentence):
            h, mm = int(m.group("h")), int(m.group("m"))
            if h <= 12 and re.search(r"\b(?:finish|finished|time|took|ran|run|pace|result|clock|in)\b", sentence, re.I):
                add("duration", m.group(0), f"{h * 60 + mm:g}min", at=m.start())
        for m in _DURATION_WORDS_RE.finditer(sentence):
            n = _number(m.group("n"))
            if n is None:
                continue
            mins = _minutes(n, m.group("u"))
            if mins is None:
                continue
            if m.group("n2"):
                n2 = _number(m.group("n2")); extra = _minutes(n2, m.group("u2")) if n2 is not None else None
                mins += extra or 0.0
            add("duration", m.group(0), f"{mins:g}min", at=m.start())
        for m in _CLOCK_RE.finditer(sentence):
            h = int(m.group("h")) % 12 + (12 if m.group("ap").lower().startswith("p") else 0)
            add("clock", m.group(0), f"{h:02d}:{int(m.group('m') or 0):02d}", at=m.start())
        for m in _AGE_RE.finditer(sentence):
            n = m.group("n") or m.group("n2") or m.group("n3")
            if n and 0 < int(n) < 120:
                add("age", m.group(0), n, at=m.start())
        for m in _COUNT_RE.finditer(sentence):
            if m.group("noun")[0].isupper():
                continue  # "61 Revisited", "7 Wonders": a number inside a proper name is not a count
            noun = m.group("noun").lower()
            if noun in _COUNT_NOUN_STOP or _stem(noun) in _COUNT_NOUN_STOP or noun in _STOP or len(noun) < 3:
                continue
            n = _number(m.group("n"))
            if n is None:
                continue
            add("count", m.group(0), f"{n:g} {_stem(noun)}", at=m.start())
        if role == "user":
            plain = re.sub(r"[*_`]+", "", sentence)  # markdown emphasis never splits a phrase
            if _PREFERENCE_RE.search(plain) and not _PREFERENCE_ACK_RE.search(plain) and len(sentence) <= 400:
                add("preference", plain[:300], norm_preference(plain))
            m = _STATE_RE.search(plain)
            if m:
                key, obj = state_key_and_object(m, plain)
                if key and obj:
                    add("state", obj, key + "=" + obj.lower())
            fm = _FORMER_STATE_RE.search(plain)
            if fm:
                # "I used to live in Rijeka": a former value of the slot, delivered in the chain as former, never current
                fkey = _STATE_KEYS.get(re.sub(r"\s+", " ", fm.group("verb").lower()), "")
                fobj = _cut_object(fm.group("obj"))
                if fkey and fobj:
                    add("state", fobj, fkey + "=" + fobj.lower() + "|former")
            t = _TRANSITION_RE.search(plain)
            if t:
                new_v = next((t.group(k) for k in ("new1", "new2", "new3") if t.group(k)), "")
                old_v = next((t.group(k) for k in ("old1", "old2", "old3") if t.group(k)), "")
                new_v = _cut_object(new_v); old_v = _cut_object(old_v)
                if new_v and old_v and new_v.lower() != old_v.lower():
                    # the state key is unknown here; find_changes resolves it from the earlier state fact that
                    # holds the old value (a traded-in car, a former job) and rewrites the norm to key=new
                    add("state_transition", new_v, "?=" + new_v.lower() + "|old=" + old_v.lower())
        if clause_days:
            # one event fact per dated clause, so each narrated event is its own operand
            for a, b, d, g in clause_days:
                if d is not None and not any(f.sentence == sentence and f.event_at == _epoch(d) for f in facts):
                    facts.append(Fact(sentence=sentence, slot=_slot(sentence[a:b]) or slot, value_type="event", value=sentence[a:b].strip()[:120], norm=d.isoformat(),
                                      event_at=_epoch(d), event_grain=g, span=span))
        elif event_at is not None and not any(f.sentence == sentence for f in facts):
            add("event", sentence[:120], event_day.isoformat())
    return facts


def norm_preference(sentence: str) -> str:
    """A compact key for a preference statement: its content words, so two statements of one preference share a norm."""
    return " ".join(sorted(set(_slot(sentence))))[:160]


def _cut_object(obj: str) -> str:
    obj = re.sub(r"[*_]+", "", obj or "").strip()
    obj = re.sub(r"^(?:the|my|our|a|an|his|her|their)\s+", "", obj, flags=re.IGNORECASE)   # "traded in the Renault Clio": the article is not the car
    obj = re.split(r"\s+(?:and|but|which|that|because|since|so|when|where|while|for|with|to|in|on|at|from|now|these|lately|recently|again|last|this|next|yesterday|today|tonight|about|after|before|every|each|as|who)\b", obj, 1)[0].strip(" ,.;:")
    words = obj.split()
    return "" if (not words or words[0].lower() in _STATE_STOP_OBJ) else obj


def state_key_and_object(m: "re.Match[str]", sentence: str) -> tuple[str, str]:
    """(state key, object phrase) for a state sentence: 'drive' + 'a Skoda Octavia' -> ('vehicle', 'Skoda Octavia')."""
    verb = (m.group("verb") or m.group("verb2") or "").lower()
    noun = (m.group("noun") or "").lower()
    obj = re.sub(r"[*_]+", "", m.group("obj") or "").strip()
    # cut the object at a clause boundary or at a trailing adverbial
    obj = re.split(r"\s+(?:and|but|which|that|because|since|so|when|where|while|for|with|to|in|on|at|from|now|these|lately|recently|again|last|this|next|yesterday|today|tonight|about|after|before|every|each|as)\b", obj, 1)[0].strip(" ,.;:")
    words = obj.split()
    if not words or words[0].lower() in _STATE_STOP_OBJ:
        return "", ""
    if noun:
        return _STATE_NOUN_KEYS.get(noun, noun), obj
    verb_key = re.sub(r"\s+", " ", verb)
    if verb_key.startswith("replaced my"):
        # "replaced my car with a Peugeot": the noun names the state
        n = re.search(r"replaced\s+my\s+(\w+)", sentence, re.IGNORECASE)
        return (_STATE_NOUN_KEYS.get(n.group(1).lower(), n.group(1).lower()) if n else "replace"), obj
    if verb_key.startswith("changed my"):
        n = re.search(r"changed\s+my\s+(\w+)", sentence, re.IGNORECASE)
        return (_STATE_NOUN_KEYS.get(n.group(1).lower(), n.group(1).lower()) if n else "switch"), obj
    if verb_key == "bought a new":
        n = words[0].lower()
        return (_STATE_NOUN_KEYS.get(n, "") or ""), " ".join(words[1:]) if n in _STATE_NOUN_KEYS else ""
    return _STATE_KEYS.get(verb_key, verb_key.replace(" ", "_")), obj


def _jaccard(a: Sequence[str], b: Sequence[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _value_head(sentence: str, value: str) -> set[str]:
    """Content words right before (6) and after (2) a value in its sentence: the thing the value measures."""
    text = re.sub(r"[*_`]+", "", str(sentence or "")); v = re.sub(r"[*_`]+", "", str(value or "")).strip()
    i = text.find(v) if v else -1
    if i < 0:
        return set()
    before = re.findall(r"[A-Za-z][A-Za-z'-]*", text[:i])[-6:]; after = re.findall(r"[A-Za-z][A-Za-z'-]*", text[i + len(v):])[:2]
    return {w.lower() for w in before + after if len(w) > 2 and w.lower() not in _STOP and w.lower() not in _HEAD_STOP}


_HEAD_STOP = frozenset("bought buy purchased got picked paid spent cost costs recently just last week weekend today yesterday new another also even finally treat myself decided".split())


def _same_head(fact: Fact, old: Mapping[str, Any]) -> bool:
    a = _value_head(fact.sentence, fact.value); b = _value_head(str(old.get("sentence") or ""), str(old.get("value") or ""))
    if not a or not b:
        return True   # no head to read: fall back to the slot rule above
    return len(a & b) >= min(2, len(a), len(b))


def find_changes(new_facts: Sequence[Fact], earlier: Sequence[dict[str, Any]], *, role: str) -> list[dict[str, Any]]:
    """Facts of earlier receipts this turn replaces: same role, same value kind, overlapping slot, disjoint values
    (the temporal contract's conflict law, re-checked on the sentences). Assistant turns replace nothing."""
    if role != "user":
        return []
    changes: list[dict[str, Any]] = []
    for fact in new_facts:
        if fact.value_type == "state_transition":
            old_v = fact.norm.split("|old=", 1)[-1]
            match = None
            for r in earlier:
                if r.get("role") != "user":
                    continue
                for f in r.get("facts") or []:
                    if f.get("value_type") == "state" and not f.get("replaced_by") and str(f.get("value") or "").lower() == old_v:
                        match = (r, f)
            if match is None:
                # no earlier state holds the old value: keep the transition as a state with an unknown key,
                # so the new value is at least visible with its "replaces old" line
                key = "state"
                fact.value_type = "state"; fact.norm = key + "=" + fact.value.lower()
                changes.append({"slot": [key], "old_occurrence": None, "old_receipt": None, "old_value": old_v, "old_statement_at": None,
                                "new_value": fact.value, "value_type": "state", "kind": "replaced", "rule": "transition-stated-in-turn", "slot_overlap": 1.0})
                continue
            r, old = match
            key = str(old.get("norm") or "").split("=", 1)[0]
            fact.value_type = "state"; fact.norm = key + "=" + fact.value.lower()
            changes.append({"slot": [key], "old_occurrence": r["occurrence_id"], "old_receipt": r["receipt_id"], "old_value": old.get("value"),
                            "old_statement_at": r.get("statement_at"), "new_value": fact.value, "value_type": "state", "kind": "replaced",
                            "rule": "transition-from-old-to-new", "slot_overlap": 1.0})
            continue
        if fact.value_type == "state":
            key = fact.norm.split("=", 1)[0]
            if fact.norm.endswith("|former"):
                continue   # a former state is already past: it replaces nothing
            cands = [(r, f) for r in earlier if r.get("role") == "user" for f in (r.get("facts") or [])
                     if f.get("value_type") == "state" and str(f.get("norm") or "").split("=", 1)[0] == key and f.get("norm") != fact.norm and not f.get("replaced_by")
                     and not str(f.get("norm") or "").endswith("|former")]
            if cands:
                r, old = max(cands, key=lambda x: x[0].get("statement_at") or 0)
                changes.append({"slot": [key], "old_occurrence": r["occurrence_id"], "old_receipt": r["receipt_id"], "old_value": old.get("value"),
                                "old_statement_at": r.get("statement_at"), "new_value": fact.value, "value_type": "state", "kind": "replaced",
                                "rule": "same-state-key-newer-statement", "slot_overlap": 1.0})
            continue
        if fact.value_type in ("event", "preference") or len(fact.slot) < 2:
            continue
        best: tuple[float, dict[str, Any], dict[str, Any]] | None = None
        for receipt in earlier:
            if receipt.get("role") != "user":
                continue
            for old in receipt.get("facts") or []:
                if old.get("value_type") != fact.value_type or old.get("norm") == fact.norm or old.get("replaced_by"):
                    continue
                overlap = _jaccard(fact.slot, old.get("slot") or [])
                shared = len(set(fact.slot) & set(old.get("slot") or []))
                if shared < 2 or (overlap < 0.34 and shared < 3):
                    continue
                # v14.3: the words right around the two values must name the same thing ("a bike helmet for $95" and
                # "a bike computer for $165" share a sentence shape and "bike", not a subject; set three q1806/qc5b9)
                if fact.value_type in ("amount", "count", "duration", "age") and not _same_head(fact, old):
                    continue
                if fact.value_type == "duration" and _duration_subkind(fact.value) != _duration_subkind(str(old.get("value") or "")):
                    continue
                # conflict = the same kind of typed value with a different normalized reading (the contract's
                # disjoint-values law, read on the typed values rather than on raw number tokens so "an hour"
                # and "two hours" conflict); count facts must also name the same thing
                if fact.value_type == "count" and fact.norm.split(" ", 1)[-1] != str(old.get("norm") or "").split(" ", 1)[-1]:
                    continue
                if fact.value_type == "list":
                    continue
                if best is None or overlap > best[0]:
                    best = (overlap, receipt, old)
        if best is not None:
            overlap, receipt, old = best
            changes.append({"slot": list(fact.slot), "old_occurrence": receipt["occurrence_id"], "old_receipt": receipt["receipt_id"],
                            "old_value": old.get("value"), "old_statement_at": receipt.get("statement_at"), "new_value": fact.value,
                            "value_type": fact.value_type, "kind": "replaced", "rule": "same-slot-same-role-disjoint-value",
                            "slot_overlap": round(overlap, 3)})
    return changes


def head_text(receipt: dict[str, Any]) -> str:
    def day(ts: float | None) -> str:
        d = _statement_day(ts)
        return d.isoformat() if d else "undated"
    lines = [f"RECEIPT {receipt['receipt_id']}  occurrence {receipt['occurrence_id']}  said {day(receipt.get('statement_at'))} by {receipt['role']}"]
    said = str(receipt.get("said") or "").strip().replace("\n", " ")
    lines.append(f"SAID   \"{said[:160]}{'…' if len(said) > 160 else ''}\"")
    facts = receipt.get("facts") or []
    if facts:
        for f in facts[:8]:
            ev = f" (event {f['value']})" if f.get("value_type") == "event" else (f" (event {date.fromtimestamp(f['event_at']).isoformat()})" if f.get("event_at") else "")
            if f.get("value_type") == "event":
                lines.append(f"FACT   [{' '.join(f['slot'][:6])}] dated event: {f['value'][:80]}")
            else:
                lines.append(f"FACT   [{' '.join(f['slot'][:6])}] {f['value_type']} = \"{f['value']}\"{ev}")
    else:
        lines.append("FACTS  none typed")
    for c in receipt.get("changes") or []:
        lines.append(f"CHANGE [{' '.join(c['slot'][:6])}] \"{c['old_value']}\" ({c['old_occurrence'][:12]}, {day(c.get('old_statement_at'))}) -> \"{c['new_value']}\"   {c['kind']}")
    for w in receipt.get("withdraws") or []:
        lines.append(f"WITHDRAWS \"{w['marker']}\"")
    return "\n".join(lines)


# ─── store ─────────────────────────────────────────────────────────────────────────────────────

def ensure_schema(mem: Any) -> bool:
    try:
        with mem._lock:
            mem._conn.executescript(SCHEMA)
            mem._conn.commit()
        return True
    except Exception:
        LOGGER.debug("memory_receipts schema failed", exc_info=True)
        return False


def _row_to_receipt(row: Any) -> dict[str, Any]:
    r = dict(row) if not isinstance(row, dict) else row
    out = {k: r.get(k) for k in ("receipt_id", "agent_id", "chat_scope", "occurrence_id", "role", "speaker", "statement_at",
                                 "event_at", "event_grain", "head_text", "writer", "model", "cost_calls", "cost_tokens", "created_at")}
    out["facts"] = json.loads(r.get("facts_json") or "[]")
    out["changes"] = json.loads(r.get("changes_json") or "[]")
    out["withdraws"] = json.loads(r.get("withdraws_json") or "[]")
    out["replaced_by"] = json.loads(r.get("replaced_by_json") or "[]")
    return out


def receipts_for_scope(mem: Any, chat_scope: str, *, role: str | None = None) -> list[dict[str, Any]]:
    """Every receipt of one chat, in statement order (then write order). Pure read."""
    try:
        with mem._lock:
            cur = mem._conn.execute(
                "SELECT * FROM memory_receipts WHERE agent_id = ? AND chat_scope = ?" + (" AND role = ?" if role else "")
                + " ORDER BY COALESCE(statement_at, created_at), rowid",
                (mem._agent_id, str(chat_scope or "")) + ((role,) if role else ()))
            cur.row_factory = None
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception:
        LOGGER.debug("memory_receipts read failed", exc_info=True)
        return []
    return [_row_to_receipt(r) for r in rows]


def write_receipt(mem: Any, occurrence: Any, *, chat_scope: str, said: str | None = None) -> dict[str, Any] | None:
    """Write the receipt for one stored occurrence (idempotent per occurrence). Returns the receipt or None."""
    occ_id = str(getattr(occurrence, "occurrence_id", "") or "")
    if not occ_id or not ensure_schema(mem):
        return None
    role = str(getattr(occurrence, "role", "") or "user")
    body = str(said if said is not None else getattr(occurrence, "body", "") or "")
    statement_at = getattr(occurrence, "statement_at", None)
    try:
        with mem._lock:
            if mem._conn.execute("SELECT 1 FROM memory_receipts WHERE occurrence_id = ?", (occ_id,)).fetchone():
                return None
    except Exception:
        return None
    facts = extract_facts(body, statement_at, role)
    earlier = receipts_for_scope(mem, chat_scope, role="user") if role == "user" else []
    changes = find_changes(facts, earlier, role=role)
    withdraws = []
    marker = retraction_marker(_strip_envelope(body)[0])
    if marker is not None:
        withdraws.append({"marker": marker.group(0), "kind": "retraction"})
    dated = [f for f in facts if f.event_at is not None]
    event_at = dated[0].event_at if dated and len({f.event_at for f in dated}) == 1 else None
    event_grain = dated[0].event_grain if event_at is not None else ("ambiguous" if dated else "")
    receipt = {"receipt_id": "r:" + uuid.uuid4().hex[:16], "agent_id": mem._agent_id, "chat_scope": str(chat_scope or ""),
               "occurrence_id": occ_id, "role": role, "speaker": str(getattr(occurrence, "speaker", "") or ""),
               "statement_at": float(statement_at) if statement_at is not None else None, "event_at": event_at, "event_grain": event_grain,
               "facts": [f.as_dict() for f in facts], "changes": changes, "withdraws": withdraws, "replaced_by": [],
               "said": _strip_envelope(body)[0], "writer": WRITER, "model": "", "cost_calls": 0, "cost_tokens": 0, "created_at": time.time()}
    receipt["head_text"] = head_text(receipt)
    try:
        with mem._lock:
            mem._conn.execute(
                "INSERT INTO memory_receipts (receipt_id, agent_id, chat_scope, occurrence_id, role, speaker, statement_at, event_at, event_grain,"
                " facts_json, changes_json, withdraws_json, replaced_by_json, head_text, writer, model, cost_calls, cost_tokens, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (receipt["receipt_id"], receipt["agent_id"], receipt["chat_scope"], occ_id, role, receipt["speaker"], receipt["statement_at"],
                 event_at, event_grain, json.dumps(receipt["facts"]), json.dumps(changes), json.dumps(withdraws), "[]", receipt["head_text"],
                 WRITER, "", 0, 0, receipt["created_at"]))
            # back-reference on every replaced receipt, so outdating is a written fact on both ends
            for change in changes:
                old = mem._conn.execute("SELECT facts_json, replaced_by_json FROM memory_receipts WHERE receipt_id = ?", (change["old_receipt"],)).fetchone()
                if old is None:
                    continue
                old_facts = json.loads(old[0] or "[]"); old_rb = json.loads(old[1] or "[]")
                for f in old_facts:
                    if f.get("value") == change["old_value"] and f.get("value_type") == change["value_type"] and not f.get("replaced_by"):
                        f["replaced_by"] = receipt["receipt_id"]
                old_rb.append({"receipt_id": receipt["receipt_id"], "occurrence_id": occ_id, "slot": change["slot"], "new_value": change["new_value"],
                               "statement_at": receipt["statement_at"]})
                mem._conn.execute("UPDATE memory_receipts SET facts_json = ?, replaced_by_json = ? WHERE receipt_id = ?",
                                  (json.dumps(old_facts), json.dumps(old_rb), change["old_receipt"]))
            mem._conn.commit()
    except Exception:
        LOGGER.debug("memory_receipts write failed", exc_info=True)
        return None
    return receipt


_QUESTION_NOISE = frozenset("remind previous conversation conversations mention mentioned mentioning earlier follow following discussed discuss told said ask asked want wanted know think like remember recall recently back going go come came thing things way one ones still now long many much time times first last ago before after between since until day days week weeks month months year years hour hours minute minutes mins did do does have has had was were".split())


def question_terms(question: str) -> list[str]:
    # a token may start with a digit when it carries a letter ("10K", "401k", "3D"): the race the question names is a
    # content term, the same way the slot tokenizer keeps it
    return sorted({_stem(t) for t in re.findall(r"[a-zA-Z0-9][a-zA-Z0-9'\-]+", str(question or "").lower())
                   if t not in _STOP and t not in _QUESTION_NOISE and len(t) > 2 and re.search(r"[a-z]", t)})


def match_receipts(receipts: Sequence[dict[str, Any]], question: str, *, extra_terms: Sequence[str] = (), limit: int = 12,
                   value_types: Sequence[str] = (), skip_types: Sequence[str] = (), target_day: date | None = None,
                   roles: Sequence[str] = ()) -> list[tuple[float, dict[str, Any], dict[str, Any]]]:
    """Facts whose slot shares content terms with the question (and the search phrases), best first.
    Returns (score, receipt, fact). A fact replaced by a later receipt scores lower but stays visible. A fact with a
    long slot (a list) needs two shared terms and is damped, so one generic word cannot rank an unrelated list. With
    *target_day* (the day a relative phrase in the question points at) a fact whose event day lies within three days
    of it is boosted: "the event two weeks ago" is found by its date as well as its words."""
    terms = set(question_terms(question)) | {_stem(t) for t in extra_terms if t}
    scored: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    for receipt in receipts:
        if roles and receipt.get("role") not in roles:
            continue
        for fact in receipt.get("facts") or []:
            if value_types and fact.get("value_type") not in value_types:
                continue
            if skip_types and fact.get("value_type") in skip_types and fact.get("event_at") is None:
                continue  # a dated sentence is an operand whatever its incidental value kind
            slot = set(fact.get("slot") or [])
            shared = len(slot & terms)
            near = False
            if target_day is not None and fact.get("event_at") is not None:
                try:
                    near = abs((_statement_day(fact["event_at"]) - target_day).days) <= 3
                except Exception:
                    near = False
            if shared == 0 and not near:
                continue
            is_list = fact.get("value_type") == "list"
            if is_list and shared < 2 and not near:
                continue
            score = shared / (len(terms) ** 0.5 or 1.0) + (0.2 if fact.get("event_at") else 0.0) + (1.0 if near else 0.0)
            if is_list and len(slot) > 12:
                score *= (12.0 / len(slot)) ** 0.5
            if fact.get("replaced_by"):
                score *= 0.6
            scored.append((score, receipt, fact))
    scored.sort(key=lambda x: (-x[0], x[1].get("statement_at") or 0))
    return scored[:limit]
