"""Temporal eligibility of memory candidates: which statements may answer NOW
(or AS OF a date the question names), and which have been superseded, expired,
or retracted.

Why this exists
----------------
Measured on the frozen candidate 9174b42c (mission memory-quality90, 2026-09-29):
capsule composition packs records by term coverage with no notion of temporal
state. A correction that shares its subject with the statement it replaces was
skipped by the coverage law (its query terms were already "covered" by the
very record it supersedes), so the OLD value shipped while the correction sat
retained-but-unpacked — F01-03/04/05/09, F07-06/10, F12-06. As-of questions
got every historical value at once (F07-01/04/05/13), future-effective
announcements rode alongside the standing value (F07-03/11/12), and no
retraction ever retired a commitment (F12-06: the scratched Fauré pick
resurrected after restart).

Laws this module owns (one contract, consumed by capsule composition and the
default-path evidence append):

* Time truth is three clocks, never one: ``event_at`` (when the described
  change takes effect), ``statement_at`` (when it was said), ``recorded_at``
  (when the system wrote it). Effective order for state questions is
  event → statement → recorded. A record with NO known time stays UNKNOWN:
  it never filters out and never supersedes (missing dates remain unknown,
  never guessed).
* Import order is not event order; backfilled statements order by their
  event time. The machine clock is never the benchmark's as-of date: a
  plumbed ``question_as_of`` wins, and the wall clock is used only to resolve
  a bare month-day phrase the QUESTION itself carries (most recent past
  reading of that calendar day).
* An AS-OF question is a state question about one date: exactly one value
  per slot applies. A past-EVENT question with no as-of ("when did I buy
  the boat?") keeps all its history, attributed — retention is not
  applicability, and this module never deletes what the store retains.
* Supersession needs a CONFLICT: two slot-mates asserting disjoint values,
  or a later undo/replace marker statement. Coexisting slot-mates (a base
  fare and its reversion notice; an assistant echo of the winning value)
  both stay eligible.
* Retraction and correction are grammatical closed classes (like the
  habitual adverbs in core.temporal_question_scope), not phrase-specific
  exceptions: a small set of un-doing/replace-ing constructions lets a later
  statement target the chain it belongs to even when token overlap alone
  would not pair them ("Recount says 4,900 euros." shares only its unit).
* Authority: within a slot, an observed user statement outranks derivative
  assistant acknowledgments — the semantic index never admits assistant
  text, so an assistant line is evidence of an acknowledgment, not the
  statement of record — unless the question asks what the assistant said.

What this is NOT: not a store, not a ledger, not a clock. It reads occurrence
and node fields that already exist and returns verdicts; persistence,
deletion, and grants stay with their owning seams.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Mapping, Sequence

__all__ = [
    "AsOfIntent",
    "TemporalCandidate",
    "EligibilityVerdict",
    "resolve_question_as_of",
    "QuestionDateWindow",
    "question_date_window",
    "relative_reference_day",
    "apply_temporal_selection",
    "effective_time",
    "slot_signature",
    "value_tokens",
]

#: UTC is the canonical comparison frame for eligibility. ``as_of`` dates are
#: calendar days; their day-END is the inclusive boundary ("as of April 1"
#: includes a change effective April 1 — boundary dates are inclusive).
_UTC = timezone.utc


# ───────────────────────────── as-of intent ────────────────────────────────

@dataclass(frozen=True)
class AsOfIntent:
    """The question's own as-of reference, if it has one."""

    #: Inclusive end of the as-of calendar day (UTC), or None when the
    #: question names no date (then current/ historic laws apply instead).
    as_of_end: datetime | None = None
    #: How the date was obtained: "plumbed" (caller-supplied metadata),
    #: "question-text", or "" (none).
    origin: str = ""

    @property
    def present(self) -> bool:
        return self.as_of_end is not None


# ───────────────────── current-observation contract ────────────────────────

#: A past-tense REPORT of a measured value: the record reports what something
#: read/registered/stood at an earlier time ("the bulletin pegged the wind at
#: 60 km/h during the January storm", "last month's tables put the high at
#: 19:40"). A standing state keeps its present tense ("the old barn is 40
#: meters long") and is NOT a past report — attributes like "old" alone never
#: trigger this law.
_PAST_REPORT_RE = re.compile(
    r"\b(?:was|were|had|put|pegged|noted|logged|recorded|measured|"
    r"showed|read|stood|reached|topped|averaged|settled)\b",
    re.IGNORECASE,
)
#: A measured VALUE shape: quantity with a unit of measure, or a time of day.
#: Bare counts ("12 platters") and identifiers ("SALT-4") are deliberately
#: NOT measured values — they are not live-observation subjects.
_MEASURED_VALUE_RE = re.compile(
    r"(?:\b\d[\d,]*(?:\.\d+)?\s*(?:km/h|kph|mph|knots?|°?\s?[cf]\b|meters?|"
    r"metres?|centimeters?|centimetres?|millimeters?|millimetres?|"
    r"kilometers?|kilometres?|miles?|kilograms?|kgs?|grams?|tonnes?|tons?|"
    r"liters?|litres?|crowns?|euros?|dollars?|percent|degrees?|bars?|"
    r"watts?|volts?|amps?|hours?|minutes?|seconds?)\b"
    r"|\b\d{1,2}:\d{2}\b(?:\s*(?:a\.?m\.?|p\.?m\.?))?)",
    re.IGNORECASE,
)


def is_stale_observation_for_current_ask(body: str) -> bool:
    """Whether *body* is a past report of a measured value.

    The current-observation contract (sealed acceptance F15-02/F15-08): a
    question anchored to NOW ("right now", "this evening", "at the moment")
    asks for a live observation; a stored record that itself reports a PAST
    measurement must not ride as answer evidence for it — the turn routes to
    tools/abstains instead. Only the conjunction qualifies: no measured value
    (identity facts, preferences, codes) or no past-tense report (standing
    present-tense states) means the record stays eligible.
    """
    text = str(body or "")
    if not text:
        return False
    return bool(_PAST_REPORT_RE.search(text) and _MEASURED_VALUE_RE.search(text))


_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
    "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
}
_MONTH_RE = "|".join(_MONTHS)

#: Anchors that introduce an explicit as-of date in the QUESTION, capturing
#: the date phrase that follows: "as of April 12", "back in September 2025",
#: "say on May 24", "in 2019".
#: The month alternation is GROUPED: an ungrouped ``january|...|december``
#: bound the day/year digits to "december" alone, so "on April 11, 2024"
#: captured the bare word "April" and the year was lost to the deictic
#: month-day fallback (resolved to the most recent past April 11). A written
#: 4-digit year is part of the phrase, with or without its comma.
_AS_OF_ANCHOR_RE = re.compile(
    rf"\b(?:as\s+of|as\s+at|dating\s+to|back\s+in)\s+(?P<rest>\d{{4}}-\d{{2}}-\d{{2}}|[A-Za-z0-9,]+(?:\s+[A-Za-z0-9,]+){{0,2}})"
    rf"|\b(?:on|in)\s+(?P<rest2>\d{{4}}-\d{{2}}-\d{{2}}"
    rf"|(?:{_MONTH_RE})\.?\s+\d{{1,2}}\b(?:,?\s+(?:19|20)\d{{2}}\b)?"
    rf"|(?:{_MONTH_RE})\.?,?\s+(?:19|20)\d{{2}}\b|(?:19|20)\d{{2}})",
    re.IGNORECASE,
)
_BARE_MONTH_DAY_RE = re.compile(
    rf"\b({_MONTH_RE})\s+(\d{{1,2}})\b(?:,?\s+((?:19|20)\d{{2}})\b)?", re.IGNORECASE
)


def resolve_question_as_of(
    question: str,
    *,
    plumbed: Any = None,
    now_utc: datetime | None = None,
) -> AsOfIntent:
    """Decide the as-of instant a question is anchored to, if any.

    A plumbed value (caller-supplied question metadata) is authoritative —
    the machine clock never overrides it. Otherwise the question's own text
    is parsed with the existing ``core.time_range_resolver`` authority
    wherever its grammar reaches, plus a most-recent-past year rule for bare
    month-day phrases. Unresolvable text yields no as-of (never a guessed
    date); CURRENT/ historic laws then apply.
    """
    plumbed_dt = _coerce_as_of_datetime(plumbed)
    if plumbed_dt is not None:
        return AsOfIntent(as_of_end=_end_of_day(plumbed_dt), origin="plumbed")

    text = str(question or "")
    if text.strip():
        for match in _AS_OF_ANCHOR_RE.finditer(text):
            phrase = (match.group("rest") or match.group("rest2") or "")
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", phrase):
                # ISO dates count only as the request's temporal frame. A
                # quoted example or an incidental future deadline is not an
                # as-of instruction, even when it contains the same bytes.
                quote_spans = [quoted.span() for quoted in re.finditer(
                    r'"(?:\\.|[^"\\])*"|[\u201c][^\u201d]*[\u201d]|`[^`]*`', text)]
                if any(start <= match.start() < end for start, end in quote_spans):
                    continue
                if match.group("rest2") and text[:match.start()].strip():
                    from core.temporal_question_scope import question_time_scope

                    if not question_time_scope(text).past_only:
                        continue
            resolved = _resolve_date_phrase(phrase, now_utc)
            if resolved is not None:
                return AsOfIntent(as_of_end=_end_of_day(resolved), origin="question-text")
        for match in _BARE_MONTH_DAY_RE.finditer(text):
            ymd = _month_day_year(match.group(0), now_utc)
            if ymd is not None:
                return AsOfIntent(
                    as_of_end=_end_of_day(datetime(*ymd, tzinfo=_UTC)),
                    origin="question-text",
                )
    return AsOfIntent()


def _coerce_as_of_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=_UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=_UTC)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=_UTC)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=_UTC)
    except ValueError:
        pass
    try:
        return datetime.fromtimestamp(float(text), tz=_UTC)
    except (OverflowError, OSError, ValueError):
        return None


_MONTH_YEAR_RE = re.compile(rf"\b({_MONTH_RE})\.?,?\s+((?:19|20)\d{{2}})\b", re.IGNORECASE)


#: Partitive month phrases ("mid July", "early June", "late September",
#: "end of March"): a real ask anchors itself inside a month without a day
#: numeral. Deterministic convention: early = the 1st, mid = the 15th,
#: late/end-of = the month's last day. Year-less phrases resolve to the most
#: recent PAST occurrence (same deictic rule as bare month-day phrases).
_PARTITIVE_MONTH_RE = re.compile(
    rf"\b(early|mid|late|end\s+of)\s+(?P<month>{_MONTH_RE})\.?"
    rf"(?:\s+(?P<year>(?:19|20)\d{{2}}))?\b",
    re.IGNORECASE,
)
_PARTITIVE_DAY = {"early": 1, "mid": 15}


def _resolve_date_phrase(phrase: str, now_utc: datetime | None) -> datetime | None:
    """Resolve one captured date phrase via the existing range resolver, with
    a local month-day fallback for phrases its bounded grammar refuses."""
    text = str(phrase or "").strip().strip(",.")
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        # This is an explicit calendar day, not the exclusive end of a
        # time-range resolver interval. Invalid dates remain unresolved.
        try:
            return datetime.fromisoformat(text).replace(tzinfo=_UTC)
        except ValueError:
            return None
    pm = _PARTITIVE_MONTH_RE.search(text)
    if pm:
        import calendar as _calendar

        month = _MONTHS[pm.group("month").lower()]
        part = pm.group(1).lower().replace(" ", "")
        day = _PARTITIVE_DAY.get(part)
        year = int(pm.group("year")) if pm.group("year") else None
        if year is None:
            now = now_utc or datetime.now(_UTC)
            for candidate_year in (now.year, now.year - 1):
                try:
                    anchor = datetime(candidate_year, month, 1, tzinfo=_UTC)
                    if anchor <= now:
                        year = candidate_year
                        break
                except ValueError:
                    continue
            if year is None:
                return None
        if day is None:  # late / end of -> the month's last day
            day = _calendar.monthrange(year, month)[1]
        try:
            return datetime(year, month, day, tzinfo=_UTC)
        except ValueError:
            return None
    my = _MONTH_YEAR_RE.fullmatch(text)
    if my:
        # Month-grain anchor: changes made during that month are part of it,
        # so the inclusive anchor is the month's END day.
        import calendar

        year, month = int(my.group(2)), _MONTHS[my.group(1).lower()]
        last = calendar.monthrange(year, month)[1]
        return datetime(year, month, last, tzinfo=_UTC)
    # An explicit month-day phrase is a calendar DAY: resolve it here, before
    # the range resolver, whose half-open interval end is the NEXT day
    # ("April 11 2024" -> [Apr 11, Apr 12) read as Apr 12).
    ymd = _month_day_year(text, now_utc)
    if ymd is not None:
        return datetime(*ymd, tzinfo=_UTC)
    try:
        from core.time_range_resolver import resolve as resolve_range

        result = resolve_range(text, now_utc=now_utc)
        start = getattr(result, "start_utc", None)
        end = getattr(result, "end_utc", None)
        if isinstance(start, datetime):
            # A month-grain phrase ("September 2025") includes changes made
            # during that month; a day-grain phrase resolves to that day.
            anchor = end if isinstance(end, datetime) else start
            return anchor.replace(tzinfo=anchor.tzinfo or _UTC)
    except Exception:
        pass
    return None


def _month_day_year(
    phrase: str, now_utc: datetime | None
) -> tuple[int, int, int] | None:
    text = str(phrase or "").strip().strip(",.")
    m = re.fullmatch(
        rf"({_MONTH_RE})\.?\s+(\d{{1,2}})(?:\s*,?\s*((?:19|20)\d{{2}}))?",
        text, re.IGNORECASE,
    )
    month_group, day_group, year_group = (1, 2, 3) if m else (None, None, None)
    if not m:
        m = re.fullmatch(
            rf"(\d{{1,2}})\s+({_MONTH_RE})\.?(?:\s*,?\s*((?:19|20)\d{{2}}))?",
            text, re.IGNORECASE,
        )
        if m:
            month_group, day_group, year_group = 2, 1, 3
    if not m:
        return None
    year = int(m.group(year_group)) if m.group(year_group) else None
    month = _MONTHS[m.group(month_group).lower()]
    day = int(m.group(day_group))
    if year is not None:
        try:
            date(year, month, day)
            return (year, month, day)
        except ValueError:
            return None
    # Bare month-day phrase: the QUESTION refers to a calendar day; take the
    # most recent PAST reading (deictic resolution of the question's own
    # anchor — this is not a stored-record date and never a guess about
    # record time).
    now = now_utc or datetime.now(_UTC)
    for candidate_year in (now.year, now.year - 1):
        try:
            if date(candidate_year, month, day) <= now.date():
                return (candidate_year, month, day)
        except ValueError:
            continue
    return None


def _end_of_day(moment: datetime) -> datetime:
    moment = moment.astimezone(_UTC)
    return datetime.combine(moment.date(), time.max, tzinfo=_UTC)


# ───────────────────── explicit question date (time leg) ───────────────────

#: Days on each side of a named calendar DAY that the occurrence time leg
#: reads: a conversation about a day is often held a day or two after it
#: ("Yesterday I met ..." stated the next morning).
DAY_WINDOW_MARGIN_DAYS = 2

_ORDINAL_SUFFIX = r"(?:st|nd|rd|th)?"
_YEAR_TAIL = r"(?:,?\s+(?P<y>(?:19|20)\d{2})\b)?"
_Q_ISO_DAY_RE = re.compile(r"\b(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})\b")
_Q_MONTH_FIRST_RE = re.compile(
    rf"\b(?P<mn>{_MONTH_RE})\.?\s+(?P<d>\d{{1,2}}){_ORDINAL_SUFFIX}\b{_YEAR_TAIL}",
    re.IGNORECASE,
)
_Q_DAY_FIRST_RE = re.compile(
    rf"\b(?P<d>\d{{1,2}}){_ORDINAL_SUFFIX}\s+(?:of\s+)?(?P<mn>{_MONTH_RE})\b\.?{_YEAR_TAIL}",
    re.IGNORECASE,
)
_Q_MONTH_YEAR_RE = re.compile(
    rf"\b(?P<mn>{_MONTH_RE})\.?,?\s+(?P<y>(?:19|20)\d{{2}})\b", re.IGNORECASE
)
_Q_QUOTED_RE = re.compile(r'"(?:\\.|[^"\\])*"|[\u201c][^\u201d]*[\u201d]|`[^`]*`')


@dataclass(frozen=True)
class QuestionDateWindow:
    """One calendar period the QUESTION names explicitly, as a read window.

    ``first_day``/``last_day`` are the named period itself (one day for a
    day-grain date, the whole month for month+year); ``start``/``end`` are
    the inclusive UTC bounds the time leg reads — the day widened by
    ``DAY_WINDOW_MARGIN_DAYS`` on each side, the month as-is.
    """

    first_day: date
    last_day: date
    grain: str
    start: datetime
    end: datetime

    def day_distance(self, day: date) -> int:
        """Whole days between *day* and the named period (0 inside it)."""
        if day < self.first_day:
            return (self.first_day - day).days
        if day > self.last_day:
            return (day - self.last_day).days
        return 0


def question_date_window(
    question: str, *, now_utc: datetime | None = None
) -> QuestionDateWindow | None:
    """The single calendar day (or month+year) the question names, if any.

    Grammar (closed): ISO ``2024-04-11``; month-first ``April 11[th][,]
    [2024]``; day-first ``11[th] [of] April[,] [2024]``; month-grain
    ``April[,] 2024``. A written 4-digit year is honored; a year-less day
    takes the most recent PAST reading (the as-of resolver's deictic rule).
    Quoted text is not the question's frame, the modal "may" is not a month,
    and a question naming two different periods (a range, a comparison) has
    no single window — None, never a guess.
    """
    text = str(question or "")
    if not text.strip():
        return None
    quoted = [m.span() for m in _Q_QUOTED_RE.finditer(text)]

    def _in_quote(pos: int) -> bool:
        return any(start <= pos < end for start, end in quoted)

    def _is_modal_may(match: re.Match[str]) -> bool:
        return match.group("mn") == "may" or match.group("mn") == "MAY"

    found: list[tuple[str, date, date]] = []
    day_spans: list[tuple[int, int]] = []
    for match in _Q_ISO_DAY_RE.finditer(text):
        if _in_quote(match.start()):
            continue
        try:
            day = date(int(match.group("y")), int(match.group("m")), int(match.group("d")))
        except ValueError:
            continue
        found.append(("day", day, day))
        day_spans.append(match.span())
    for pattern in (_Q_MONTH_FIRST_RE, _Q_DAY_FIRST_RE):
        for match in pattern.finditer(text):
            if _in_quote(match.start()) or _is_modal_may(match):
                continue
            if any(s < match.end() and match.start() < e for s, e in day_spans):
                continue
            phrase = f"{match.group('mn')} {int(match.group('d'))}"
            if match.group("y"):
                phrase += f" {match.group('y')}"
            ymd = _month_day_year(phrase, now_utc)
            if ymd is None:
                continue
            day = date(*ymd)
            found.append(("day", day, day))
            day_spans.append(match.span())
    for match in _Q_MONTH_YEAR_RE.finditer(text):
        if _in_quote(match.start()) or _is_modal_may(match):
            continue
        if any(s < match.end() and match.start() < e for s, e in day_spans):
            continue
        import calendar as _calendar

        year, month = int(match.group("y")), _MONTHS[match.group("mn").lower()]
        found.append((
            "month", date(year, month, 1),
            date(year, month, _calendar.monthrange(year, month)[1]),
        ))
    periods = {(first, last) for _grain, first, last in found}
    if len(periods) != 1:
        return None
    grain, first, last = found[0]
    margin = timedelta(days=DAY_WINDOW_MARGIN_DAYS if grain == "day" else 0)
    return QuestionDateWindow(
        first_day=first,
        last_day=last,
        grain=grain,
        start=datetime.combine(first - margin, time.min, tzinfo=_UTC),
        end=datetime.combine(last + margin, time.max, tzinfo=_UTC),
    )


_REL_DAY_BEFORE_YESTERDAY_RE = re.compile(r"\bthe\s+day\s+before\s+yesterday\b", re.IGNORECASE)
_REL_YESTERDAY_RE = re.compile(r"\b(?:yesterday|last\s+night)\b", re.IGNORECASE)
_REL_DAYS_AGO_RE = re.compile(
    r"\b(?P<n>[1-6]|one|two|three|four|five|six)\s+days?\s+ago\b", re.IGNORECASE
)
_REL_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def relative_reference_day(body: str, statement_at: float | None) -> date | None:
    """The calendar day a record's OWN words date its content to, relative
    to its statement time: "yesterday"/"last night" (-1), "the day before
    yesterday" (-2), "N days ago" (N <= 6). Closed class; a record whose
    phrases point at different days is ambiguous and yields None, as does a
    record without a statement time (nothing to count back from)."""
    if statement_at is None:
        return None
    text = str(body or "")
    if _is_hedged(text):
        # a hedged back-reference ("Maybe yesterday the swarm went ..., I am not sure") dates nothing: the capsule's
        # hedge class (the correction law's) applies to anchoring too, so a speculative record is never the record of
        # the asked day (tests/test_question_date_time_leg_20261002.py::test_hedged_next_day_record_does_not_ride)
        return None
    offsets: set[int] = set()
    stripped = _REL_DAY_BEFORE_YESTERDAY_RE.sub(" ", text)
    if stripped != text:
        offsets.add(2)
    if _REL_YESTERDAY_RE.search(stripped):
        offsets.add(1)
    for match in _REL_DAYS_AGO_RE.finditer(stripped):
        raw = match.group("n").lower()
        offsets.add(int(raw) if raw.isdigit() else _REL_NUMBER_WORDS[raw])
    if len(offsets) != 1:
        return None
    stated = datetime.fromtimestamp(float(statement_at), tz=_UTC).date()
    return stated - timedelta(days=offsets.pop())


#: Ordinal asks: the question names WHICH occurrence of a repeated subject
#: it wants ("when did I first put it on record", "the last time I logged").
#: Closed class; decides earliest/latest selection for that slot.
_ORDINAL_EARLIEST_RE = re.compile(
    r"\b(?:when|what|which)\b[^.?]{0,40}\bfirst\b"
    r"|\b(?:first|earliest)\s+(?:time|occasion|record|entry)\b"
    r"|\boriginally\b|\binitially\b|\bto\s+begin\s+with\b",
    re.IGNORECASE,
)
_ORDINAL_LATEST_RE = re.compile(
    r"\b(?:when|what|which)\b[^.?]{0,40}\b(?:last|latest|most\s+recent)\b"
    r"|\b(?:last|latest|most\s+recent)\s+(?:time|occasion|record|entry)\b",
    re.IGNORECASE,
)
#: A deictic calendar reference ("last year", "next week", "this weekend",
#: "last Friday", "last few months") dates an event relative to when it was
#: said; it does not ask WHICH occurrence of a repeated subject is wanted.
#: Removed before the ordinal grammar reads the question, so "the car he
#: restored last year" is not an ask for the latest record.
_DEICTIC_PERIOD_RE = re.compile(
    r"\b(?:last|next|this|past|coming)\s+"
    r"(?:(?:few|couple(?:\s+of)?|several|two|three|four|five|six|\d{1,2})\s+)?"
    r"(?:years?|months?|weeks?|weekends?|nights?|days?|evenings?|mornings?"
    r"|afternoons?|summers?|winters?|springs?|autumns?|falls?|seasons?"
    r"|semesters?|terms?|quarters?|decades?|centur(?:y|ies)|holidays?"
    r"|(?:mon|tues|wednes|thurs|fri|satur|sun)days?"
    r"|january|february|march|april|may|june|july|august|september"
    r"|october|november|december)\b",
    re.IGNORECASE,
)


def question_ordinal(text: str) -> str:
    """"" earliest", "latest", or "" when the ask carries no ordinal."""
    body = _DEICTIC_PERIOD_RE.sub(" ", str(text or ""))
    if _ORDINAL_EARLIEST_RE.search(body):
        return "earliest"
    if _ORDINAL_LATEST_RE.search(body):
        return "latest"
    return ""


# ───────────────────────────── candidates ──────────────────────────────────

@dataclass
class TemporalCandidate:
    """One packed-candidate view over an existing occurrence or node.

    ``key`` is the occurrence id when the record has one (a node and its
    source occurrence are ONE candidate), else the node id. Both legs of
    capsule composition build these from store rows; nothing is copied or
    re-ledgered.
    """

    key: str
    body: str
    role: str = "user"
    authority: str = ""
    statement_at: float | None = None
    event_at: float | None = None
    recorded_at: float | None = None
    source_kind: str = ""
    # Source capture order, when known. Retrieval enumeration is never a
    # chronology substitute; zero is a valid explicit sequence value.
    seq: int | None = None
    has_node: bool = False
    #: How the candidate entered evaluation: "query-leg" (a retrieval leg
    # matched it), "neighbor" (actual adjacent source context), or "chain"
    # (the bounded chain-completion probe fetched it
    # to complete a supersession chain). Validity is not relevance: a
    # chain-probed record only rides when its slot also holds a query-leg
    # record — otherwise the anchor probe's shared vocabulary ("tally")
    # would inject unrelated subjects the question never asked about.
    origin: str = "query-leg"
    #: Who said the record, when the source attributes it: the stored
    # speaker column, else the single reported source label ("Name:") the
    # retrieval layer binds to the record. Empty = unattributed. A record
    # supersedes another only within one speaker's statements: in a relayed
    # multi-party transcript a later turn by one person is never an update
    # of what ANOTHER person said (measured: a conversation stored as one
    # user stream let one participant's later turn withdraw the other's).
    speaker: str = ""


@dataclass
class EligibilityVerdict:
    key: str
    eligible: bool
    reason: str
    slot: str = ""
    effective_time: float | None = None
    superseded_by: str | None = None


#: A single declared effective date ("Effective April 6:", "from June 1",
#: "starting March 18") — the record says WHEN its change applies without
#: an explicit window end. A backfilled record whose write seam carried no
#: times still declares its applicability in text.
#: Declared single effective dates. Two verb families, one date shape set
#: (month-first "November 14", day-first "14 November", ISO): the
#: prepositions ("effective/from/starting/beginning November 14") and the
#: event-start verbs, which take "on" ("starts/kicks off/opens on 14
#: November" — a scheduled fact that is not yet in force before its date).
_EFFECTIVE_DATE_RE = re.compile(
    rf"\b(?:"
    rf"(?:effective|from|since|starting|beginning)\s+"
    rf"|(?:begins?|starts?|kicks?\s+off|launch(?:es|ed)?|opens?"
    rf"|commences?|falls?|lands?)\s+on\s+"
    rf")((?:{_MONTH_RE})\.?\s+\d{{1,2}}(?:\s+(?:19|20)\d{{2}})?"
    rf"|\d{{1,2}}\.?\s+(?:{_MONTH_RE})(?:\s+(?:19|20)\d{{2}})?"
    rf"|\d{{4}}-\d{{2}}-\d{{2}})\b",
    re.IGNORECASE,
)
#: Month-grain declarations ("in June", "from September", "effective
#: March 2027"): the record says WHICH MONTH its change applies without a
#: calendar day. The month's END day is the anchor (inclusive convention,
#: mirroring the question-side month parser).
_EFFECTIVE_MONTH_RE = re.compile(
    rf"\b(?P<prep>in|from|starting|beginning|effective)\s+(?:the\s+)?"
    rf"(?P<month>{_MONTH_RE})\.?(?:\s+(?P<year>(?:19|20)\d{{2}}))?"
    rf"(?![\s,]*\d)",
    re.IGNORECASE,
)
#: Present/future-tense change verbs: they announce a rise that has not
#: happened yet, so a bare "in <month>" beside them is a FORWARD
#: declaration. Past-tense forms (rose/climbed/jumped/went/dropped/moved)
#: are deliberately absent — they narrate completed changes.
_FORWARD_CHANGE_VERB_RE = re.compile(
    r"\b(?:goes?|go(?:es)?\s+up|rises?|rise|climbs?|jumps?|leaps?|"
    r"becomes?|become|hits?|hit|increases?|increase|jumps?\s+up|"
    r"will\s+(?:rise|go|climb|jump|increase|become|be|hit)|"
    r"steps?\s+up|moves?\s+up)\b",
    re.IGNORECASE,
)
#: Year-grain declarations ("from the 2025 season", "for the 2026 season"):
#: the record says WHICH year its change applies without a calendar day.
#: The year's first day is the conservative effective anchor — the change
#: is inside that year, never before it.
_EFFECTIVE_YEAR_RE = re.compile(
    r"\b(?:from|for|starting|beginning|effective)\s+(?:the\s+)?((?:19|20)\d{2})"
    r"(?:\s+\w+)?",
    re.IGNORECASE,
)
#: Present-moment declarations ("from now", "as of today"): the change is
#: effective at the statement moment itself — the machine write time IS the
#: statement time for a live turn (never for an imported one).
_EFFECTIVE_NOW_RE = re.compile(
    r"\b(?:from|as\s+of|starting)\s+(?:now|today|tonight|this\s+morning|"
    r"this\s+afternoon|this\s+evening)\b",
    re.IGNORECASE,
)


def declared_effective_date(
    text: str,
    year_hint: int | None = None,
    *,
    statement_at: float | None = None,
) -> "date | None":
    """The record's own single effective date, when its text declares one
    and no explicit window already governs it. An EXPLICIT year in the text
    is the speaker's own dating and wins. A yearless phrase resolves
    deixtically against the record's statement moment when the caller
    provides it (``statement_at``), else the resolution clock — the year
    hint remains the statement-year fallback for records whose seam time
    is all the caller has."""
    body = str(text or "")
    win_start, win_end = effective_window(body, year_hint)
    if win_end is not None or win_start is not None:
        return None  # an explicit window already governs applicability
    m = _EFFECTIVE_DATE_RE.search(body)
    if m:
        return _parse_record_date(m.group(1), year_hint)
    mm = _EFFECTIVE_MONTH_RE.search(body)
    if mm:
        # Month-grain declaration ("rose to 8 credits in June", "9 credits
        # from September"): the change is in force by the month's END - the
        # same inclusive anchor the question-side month parser uses. An
        # as-of mid-month AFTER the month sees it; one inside it does not
        # (measured, fresh cohort F07-05: the ladder rungs carried no day
        # digits and no seam times, so every rung was time-less).
        #
        # Deictic year rule for a bare month: a FORWARD declaration
        # ("from/starting/beginning/effective October") names a window that
        # may still be ahead and keeps the current/hinted year; a
        # RETROSPECTIVE "in October" narrates a past event and resolves to
        # the most recent PAST month-end - without this, "we pressed cider
        # in October" said in September resolved to the FUTURE October and
        # the aggregate boundary dropped the operand as not-yet-happened
        # (measured, aggregate grant tally suite).
        import calendar as _calendar
        month = _MONTHS[mm.group("month").lower()]
        year = int(mm.group("year")) if mm.group("year") else None
        forward = str(mm.group("prep") or "").lower() in (
            "from", "starting", "beginning", "effective")
        if not forward:
            # A PRESENT/FUTURE-tense change verb makes a bare "in <month>"
            # forward too: "goes to 17 dram in December" announces a rise
            # that has not happened, exactly like "from December". Past
            # tense (rose/climbed/went/dropped) stays retrospective
            # narration (measured, challenge C01 family vs the cider
            # aggregate operand).
            forward = _FORWARD_CHANGE_VERB_RE.search(body) is not None
        if year is None:
            if year_hint is not None:
                year = year_hint
            elif statement_at is not None:
                year = datetime.fromtimestamp(statement_at, tz=_UTC).year
            else:
                year = datetime.now(_UTC).year
            # Deictic anchor: the RECORD'S OWN statement moment when the
            # caller knows it, else the wall clock for a timeless record.
            # Resolving against the statement makes the record's meaning a
            # function of its own text + statement time — stable at every
            # later recall. Resolving against the wall clock made "from
            # March" said in 2026 mean 2027-03-31 when asked in 2026 but
            # collapse to 2026-03-31 when asked in 2028, shifting the
            # stored source's meaning solely because the clock moved
            # (owner review target, demonstrated 2026-09-30).
            _ref = (
                datetime.fromtimestamp(statement_at, tz=_UTC)
                if statement_at is not None else datetime.now(_UTC)
            )
            if not forward:
                for _cand in (year, year - 1):
                    try:
                        _last = _calendar.monthrange(_cand, month)[1]
                        if datetime(_cand, month, _last, tzinfo=_UTC) <= _ref:
                            year = _cand
                            break
                    except ValueError:
                        continue
            else:
                # A forward declaration of a month whose reference-year
                # instance already passed at the anchor means the NEXT one
                # ("climbs to 6 pa'anga from March", said in September =
                # next March) — a statement made before that month-end
                # keeps the current year ("said in January, from March" =
                # this March).
                try:
                    _last = _calendar.monthrange(year, month)[1]
                    if datetime(year, month, _last, tzinfo=_UTC) <= _ref:
                        year += 1
                except ValueError:
                    pass
        try:
            last = _calendar.monthrange(year, month)[1]
            return date(year, month, last)
        except ValueError:
            return None
    my = _EFFECTIVE_YEAR_RE.search(body)
    if my:
        try:
            return date(int(my.group(1)), 1, 1)
        except ValueError:
            return None
    return None


def state_time(candidate: TemporalCandidate) -> float | None:
    """When the record's content BECAME true: event time, else statement
    time, else the effective date its own text declares. The system write
    time is deliberately absent — import time is not event time, and
    dating a record at its import moment made every times-less historical
    record 'future' to an as-of question (measured: the as-of family
    emptied its capsules under an ingestion that carries no seam times;
    recorded_at orders, it never dates)."""
    for value in (candidate.event_at, candidate.statement_at):
        if value is not None:
            return float(value)
    year_hint = (
        datetime.fromtimestamp(candidate.statement_at, tz=_UTC).year
        if candidate.statement_at is not None else None
    )
    declared = declared_effective_date(
        candidate.body, year_hint, statement_at=candidate.statement_at)
    if declared is not None:
        return datetime(
            declared.year, declared.month, declared.day, tzinfo=_UTC
        ).timestamp()
    if _EFFECTIVE_NOW_RE.search(str(candidate.body or "")):
        # "from now" dates the change at the statement moment — the machine
        # write time for a LIVE turn (an imported record never says 'now'
        # about its own ingest; its envelope or seam times govern).
        if candidate.recorded_at is not None and candidate.source_kind != "historical-import":
            return float(candidate.recorded_at)
    return None


def effective_time(candidate: TemporalCandidate) -> float | None:
    """Ordering time: state time first, then the system write time (which
    orders live turns whose statement moment IS the write moment)."""
    for value in (candidate.event_at, candidate.statement_at, candidate.recorded_at):
        if value is not None:
            return float(value)
    return None


# ─────────────────── signature, values, markers (closed classes) ──────────

_STOPWORDS = frozenset({
    "a", "an", "the", "this", "that", "these", "those", "it", "its", "he",
    "she", "they", "them", "him", "her", "we", "you", "i", "me", "us", "my",
    "your", "our", "his", "their", "is", "are", "was", "were", "am", "be",
    "been", "being", "do", "does", "did", "done", "have", "has", "had",
    "will", "would", "shall", "should", "can", "could", "may", "might",
    "must", "of", "in", "on", "at", "to", "for", "with", "by", "and", "or",
    "but", "so", "as", "if", "then", "than", "when", "what", "which", "who",
    "whom", "whose", "where", "why", "how", "not", "no", "nor", "there",
    "here", "also", "just", "only", "please", "still", "per", "up", "out",
    "over", "into", "about", "after", "before", "from", "until", "till",
    "while", "during", "recorded", "noted", "logged", "say", "says", "said",
    "tell", "told", "ask", "asked",
})

#: Calendar vocabulary is temporal context, not subject identity. Month and
#: weekday names stay out of slot signatures (they are values/conditions).
_TEMPORAL_WORDS = frozenset(
    list(_MONTHS.keys())
    + ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
       "sunday", "today", "tonight", "tomorrow", "yesterday", "now",
       "week", "weeks", "month", "months", "year", "years", "day", "days",
       "morning", "afternoon", "evening", "night", "noon", "midnight",
       "winter", "spring", "summer", "autumn", "fall", "season", "seasons",
       "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
       "oct", "nov", "dec", "weekend", "weekdays", "hour", "hours",
       "minute", "minutes", "time", "date", "oclock", "am", "pm"]
)

#: Units pair numbers to measures; they recur inside one value chain, so they
#: are neither signature nor value tokens — but a SHARED unit can anchor a
#: marker-carrying correction to its target ("Recount says 4,900 euros.").
#: Subject-ATTRIBUTE words (fee, deposit, dose…) are deliberately NOT units:
#: they are exactly the predicate material that makes two statements the
#: same slot ("hammer deposit is 10" vs "hammer deposit goes to 15").
_UNIT_WORDS = frozenset({
    "euro", "euros", "dollar", "dollars", "cent", "cents", "pound", "pounds",
    "krona", "kronor", "kroner", "zloty", "yen", "yuan", "rupee", "ruble",
    "percent", "litre", "litres", "liter", "liters", "millilitre",
    "millilitres", "milliliter", "milliliters", "milligram", "milligrams",
    "gram", "grams", "kilogram", "kilograms", "tonne", "tonnes", "ton",
    "tons", "metre", "metres", "meter", "meters", "kilometre", "kilometres",
    "kilometer", "kilometers", "mile", "miles", "foot", "feet", "inch",
    "inches", "yard", "yards", "hectare", "hectares", "acre", "acres",
    "vehicle", "vehicles", "person", "persons", "people", "second",
    "seconds", "degree", "degrees", "celsius", "fahrenheit", "knot", "knots",
    "kwh", "mwh", "watt", "watts", "volt", "volts", "amp", "amps",
    "minute", "minutes", "hour", "hours", "day", "days", "week", "weeks",
    "month", "months", "year", "years",
})

#: Word-numerals and frequency measures are VALUES, never signature tokens.
_NUMBER_WORDS = frozenset({
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty",
    "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred",
    "thousand", "million", "billion", "dozen", "couple", "few", "several",
    "monthly", "fortnightly", "biweekly", "semiweekly", "weekly", "daily",
    "nightly", "annually", "yearly", "annual", "quarterly", "hourly",
})

_TOKEN_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9'’\-]*")
_VALUE_TOKEN_RE = re.compile(
    r"\d+(?:[.,]\d+)+|\b\d{1,2}:\d{2}\b|\d+|\b(?:st|nd|rd|th)\b",
)

#: Closed grammatical class: constructions that UNDO or REPLACE a previous
#: statement. Whether a retraction-with-no-replacement-value or a correction
#: is decided by the value conflict law, not by the marker itself.
_RETRACTION_MARKER_RE = re.compile(
    r"\b(?:scratch\s+that|never\s+mind|pretend\s+(?:i|we)\s+never\s+said|"
    r"forget\s+(?:that|the|it|what\s+(?:i|we)\s+said)|disregard(?:\s+that)?|"
    r"ignore\s+(?:that|it|this|what\s+(?:i|we)\s+(?:said|told\s+you))|"
    r"cancel\s+(?:that|it)|on\s+second\s+thought|retract(?:ion|ions|ed|s)?|"
    r"withdraw(?:n|s|ing)?|undo(?:ne|ne)?|"
    # removal requests for pasted/quoted material: "take that paste down"
    r"(?:take|pull|tear)\s+(?:that|this|it|those)\s+"
    r"(?:[a-z-]+\s+){0,2}?down\b|"
    r"remove\s+(?:that|this|it)\s+(?:paste|quote|quotation|log|note|post)\b)\b",
    re.IGNORECASE,
)
#: A retraction un-does prior SPEECH. Two grammatical conditions keep the
#: closed class above from firing on ordinary prose (measured on imported
#: dialogue, c1-recall dev replay: a reminder of the "don't forget the ..."
#: shape and a "... helps me forget the ..." description each withdrew every
#: same-speaker record of their slot - 14 answer turns of 200 dev questions,
#: several at lexical rank 1-2):
#: * negation reverses the act: "don't forget the receipts" / "never ignore
#:   the alarm" ask the listener to KEEP something - a reminder, not a
#:   withdrawal;
#: * the speech verbs of the class (forget / ignore / disregard) withdraw
#:   speech only as a DIRECTIVE to the listener: clause-initial (the
#:   imperative; a speaker label's colon opens the clause), optionally after
#:   discourse words ("oh", "actually", "wait, ok so") or politeness
#:   ("please", "kindly", "just"), or inside a request frame that hands the
#:   verb to the listener ("could you ...", "can u just ...", "I'd like you
#:   to ...", "you should ...", "let's ..."). Any other subject or governing
#:   verb before them makes a description ("a long walk helps me forget the
#:   commute", "I forget the name", "do you ever forget it?", "my brother
#:   said to forget it").
#: "cancel that / cancel it" is not speech-only: a plan reported cancelled
#: is withdrawn however it is reported ("we had to cancel it", "I'll cancel
#: it"), so it obeys the negation rule - with one exception: a SERVICE
#: REQUEST, a directive that hands the cancelling to the listener for the
#: speaker's benefit ("cancel that booking for me", "could you cancel it for
#: us"), asks for an action in the world and un-says nothing the speaker
#: stated.
#: "scratch that" is a retraction only as a correction speech act: a
#: directive by the same clause law as the speech verbs ("Scratch that, ...",
#: "actually, scratch that", "you can scratch that"), whose "that" points
#: back at what was said - standing alone ("scratch that about the kiln",
#: "scratch that entirely", "scratch that i'm moving") or determining a word
#: for something said, planned or chosen ("scratch that last bit", "scratch
#: that plan", "scratch that 9:40 pickup"). Anywhere else the verb takes an
#: ordinary object: "a long hike will scratch that" has a subject before
#: it, and "scratch that lottery ticket for me" names a thing,
#: not a statement (external review, 2026-10-06: the figurative idiom read
#: as a retraction and withdrew the speaker's earlier claims of its slot).
_RETRACTION_DIRECTIVE_VERB_RE = re.compile(
    r"(?:forget|disregard|ignore)\b", re.IGNORECASE)
_RETRACTION_NEGATION_TAIL_RE = re.compile(
    r"(?:\b(?:not|never|cannot|dont|doesnt|didnt|wont|cant|shouldnt|wouldnt"
    r"|couldnt|mustnt)|n['’]t)\s*$", re.IGNORECASE)
_RETRACTION_CLAUSE_BOUNDARY_RE = re.compile(
    r"[.!?;:\n—–(\[\"“]|,\s|\s-\s")
_RETRACTION_DISCOURSE_WORDS = frozenset({
    "oh", "ok", "okay", "so", "and", "but", "or", "actually", "wait", "no",
    "nah", "nope", "hmm", "hm", "um", "umm", "uh", "er", "erm", "well",
    "please", "pls", "plz", "kindly", "just", "simply", "also", "then",
    "sorry", "oops", "yeah", "yes", "yep", "right", "alright", "hey", "ah",
    "look", "listen", "fine", "anyway", "anyways", "now", "plus", "ugh",
    "hold", "on", "hang",
    # typed interjections ("lol scratch that", "nvm ignore it")
    "lol", "lmao", "haha", "jk", "nvm", "omg", "welp", "whoops", "shoot",
    "dang", "darn", "damn", "dammit", "crap", "gosh", "jeez", "geez", "oof",
    "whoa", "yikes", "argh", "hmmm", "ummm", "uhh", "err", "ahh", "ohh", "eh",
})
#: Multi-word discourse openers that may precede a directive like the single
#: discourse words do ("i mean scratch that", "my bad, ignore it").
_RETRACTION_DISCOURSE_PHRASES = (
    ("you", "know", "what"), ("you", "know"), ("i", "mean"), ("my", "bad"),
    ("come", "to", "think", "of", "it"),
)
#: Request, permission and advice frames addressed to the listener, read
#: over the clause's own words (lower case, single spaces) after any
#: discourse words; only discourse words may follow the frame ("could you
#: please just ..."). Longer alternatives first.
_RETRACTION_REQUEST_FRAME_RE = re.compile(
    r"(?:let's|lets|let us|feel free to|go ahead and"
    r"|(?:could|can|would|will) (?:you|u|ya)"
    r"|(?:you|u) (?:might want to|may want to|will want to|had better"
    r"|need to|have to|ought to|got to|gotta|better|can|may|could|should"
    r"|must)"
    r"|you'd better|you'll want to"
    r"|(?:i'd|id|i would|we'd|we would|i|we) (?:like|love|want|need) (?:you|u) to"
    r"|(?:i'd|id|i would|we'd|we would) rather (?:you|u))"
    r"(?= |$)")
#: The end of the words that follow a marker in its own clause. A colon or
#: period between digits is part of a value ("the 9:40 pickup", "4.5").
_RETRACTION_TAIL_BOUNDARY_RE = re.compile(
    r",\s|[!?;\n—–(\[\"“”…]|\.(?!\d)|:(?!\d)|\s-(?:\s|$)")
_RETRACTION_TAIL_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’:.\-]*")
#: Words a determiner "that" cannot introduce as its head: right after
#: "scratch that" they leave the pronoun standing alone and open a modifier
#: or the next clause ("scratch that about the kiln", "scratch that we're
#: staying", "scratch that the party is off", "scratch that entirely").
_RETRACTION_PRONOUN_FOLLOWERS = frozenset({
    # prepositions and particles
    "about", "re", "regarding", "concerning", "on", "from", "off", "in", "at",
    "for", "with", "to", "of", "as", "like", "after", "before", "by", "over",
    "into", "onto", "out", "since", "until", "till", "above", "below",
    # conjunctions
    "and", "but", "or", "so", "because", "cause", "cuz", "though", "tho",
    "although", "yet", "plus", "nor", "if", "unless", "than",
    # pronouns, possessives and demonstratives
    "i", "i'm", "im", "i'll", "i've", "ive", "i'd", "me", "my", "mine", "we",
    "we're", "we'll", "we've", "we'd", "our", "ours", "us", "you", "you're",
    "youre", "you'll", "you've", "your", "u", "ur", "he", "he's", "his",
    "him", "she", "she's", "her", "hers", "it", "it's", "its", "they",
    "they're", "theyre", "they'll", "their", "them", "this", "these",
    "those", "there", "there's", "theres", "here", "here's", "what",
    "what's", "which", "who", "whom", "how", "where", "when", "why", "let's",
    "lets",
    # determiners and quantifiers
    "the", "a", "an", "all", "any", "some", "no", "every", "each", "both",
    "either", "neither", "another",
    # auxiliaries, copulas, modals and negation
    "is", "isn't", "isnt", "are", "aren't", "arent", "was", "wasn't",
    "wasnt", "were", "weren't", "be", "been", "being", "am", "will", "won't",
    "wont", "would", "wouldn't", "should", "shouldn't", "can", "can't",
    "cant", "could", "couldn't", "may", "might", "must", "shall", "do",
    "don't", "dont", "does", "doesn't", "doesnt", "did", "didn't", "didnt",
    "has", "hasn't", "have", "haven't", "had", "hadn't", "gonna", "wanna",
    "not", "never",
    # degree and sentence adverbs
    "entirely", "completely", "totally", "fully", "wholly", "altogether",
    "outright", "really", "definitely", "absolutely", "seriously",
    "literally", "basically", "obviously", "too", "again", "already",
    "instead", "quickly", "immediately", "permanently", "earlier",
    "previously",
}) | _RETRACTION_DISCOURSE_WORDS
#: Finite verbs that show the words after "that" were the SUBJECT of the
#: next clause ("scratch that meeting is moved", "scratch that flight got
#: cancelled"): "that" stood alone. Regular past forms (-ed) count too.
_RETRACTION_FINITE_VERBS = frozenset({
    "is", "isn't", "isnt", "are", "aren't", "arent", "was", "wasn't",
    "wasnt", "were", "weren't", "will", "won't", "wont", "would", "has",
    "have", "had", "can", "can't", "cant", "could", "should", "does",
    "doesn't", "doesnt", "did", "didn't", "didnt", "got", "went", "came",
    "left", "took", "fell", "ran", "became", "stays", "stayed",
})
_RETRACTION_OBJECT_PRONOUNS = frozenset({"it", "them", "me", "us", "him", "her"})
#: Heads a determiner "that" introduces when it points back at something the
#: speaker said, planned or chose ("scratch that last bit", "scratch that
#: plan", "scratch that booking"). A word for an ordinary thing ("scratch
#: that table", "that lottery ticket") is the object of an ordinary verb.
_RETRACTION_SAID_THING_HEADS = frozenset({
    # what was said
    "part", "bit", "piece", "line", "sentence", "word", "phrase", "message",
    "msg", "text", "note", "comment", "remark", "statement", "answer",
    "reply", "response", "post", "entry", "item", "point", "detail", "info",
    "information", "mention", "quote", "paste", "link", "request",
    "question", "instruction", "command", "order", "suggestion", "proposal",
    "idea", "thought", "claim", "number", "figure", "value", "amount",
    "total", "price", "estimate", "guess", "address", "name", "list",
    "change", "update", "edit", "correction", "version", "draft", "stuff",
    "thing", "one", "last",
    # what was planned or chosen
    "plan", "promise", "offer", "assignment", "booking", "reservation",
    "appointment", "arrangement", "schedule", "date", "time", "trip",
    "visit", "dinner", "lunch", "breakfast", "meeting", "call", "party",
    "session", "class", "lesson", "workshop", "event", "flight", "ride",
    "pickup", "delivery", "purchase", "move", "switch", "choice", "pick",
    "option", "decision", "step", "place", "venue", "restaurant", "hotel",
    "route",
})
#: A first-person benefactive right after a cancel's object: the listener
#: is asked to do the cancelling ("cancel that booking for me").
_CANCEL_SERVICE_BENEFACTIVE_RE = re.compile(
    r"^\s*(?:[A-Za-z0-9'’\-]+\s+){0,4}?for\s+(?:me|us)\b", re.IGNORECASE)


def _retraction_clause_prefix(text: str, start: int) -> str:
    """The words of a marker's own clause that precede it."""
    head = str(text or "")[:start]
    boundaries = list(_RETRACTION_CLAUSE_BOUNDARY_RE.finditer(head))
    return head[boundaries[-1].end():] if boundaries else head


def _retraction_prefix_is_directive(words: list[str]) -> bool:
    """Whether the clause words before a speech verb leave it a directive to
    the listener: discourse words only, or discourse words around one
    request frame."""
    index = 0
    while index < len(words):
        if words[index] in _RETRACTION_DISCOURSE_WORDS:
            index += 1
            continue
        opener = next((phrase for phrase in _RETRACTION_DISCOURSE_PHRASES
                       if tuple(words[index:index + len(phrase)]) == phrase), None)
        if opener is None:
            break
        index += len(opener)
    rest = " ".join(words[index:])
    if not rest:
        return True
    frame = _RETRACTION_REQUEST_FRAME_RE.match(rest)
    return frame is not None and all(
        word in _RETRACTION_DISCOURSE_WORDS for word in rest[frame.end():].split())


def _retraction_tail_words(text: str, end: int) -> list[str]:
    """The words after a marker, up to the end of its clause (lower case)."""
    tail = str(text or "")[end:]
    stop = _RETRACTION_TAIL_BOUNDARY_RE.search(tail)
    if stop is not None:
        tail = tail[:stop.start()]
    return [w.replace("’", "'").lower().rstrip(".:'-")
            for w in _RETRACTION_TAIL_WORD_RE.findall(tail)]


def _names_a_said_thing(word: str) -> bool:
    """A head noun for something said, planned or chosen (plural folded)."""
    word = re.sub(r"'s$", "", word)
    forms = {word, _stem(word)}
    if word.endswith("es"):
        forms.add(word[:-2])
    return bool(forms & _RETRACTION_SAID_THING_HEADS)


def _scratch_that_points_back(text: str, end: int) -> bool:
    """Whether the "that" of a "scratch that" match points back at speech:
    it stands alone, or it determines a word for something said, planned or
    chosen (see the grammar above)."""
    words = _retraction_tail_words(text, end)
    if not words or words[0] in _RETRACTION_PRONOUN_FOLLOWERS:
        return True
    phrase: list[str] = []
    for word in words:
        if word in _RETRACTION_PRONOUN_FOLLOWERS or (
                phrase and (word in _RETRACTION_FINITE_VERBS
                            or (word.endswith("ed") and len(word) > 4))):
            break
        phrase.append(word)
    after = words[len(phrase)] if len(phrase) < len(words) else ""
    if len(phrase) == 1 and (after in _RETRACTION_FINITE_VERBS
                             or (after.endswith("ed") and len(after) > 4)):
        # the word after "that" is the subject of the next clause ("scratch
        # that meeting got moved"); a longer phrase before a verb is an object
        # with a reduced relative ("that ticket everyone wants")
        return True
    if len(phrase) == 1 and after in _RETRACTION_OBJECT_PRONOUNS:
        # an imperative verb opens the next clause ("scratch that make it 6")
        return True
    if phrase[-1].endswith("'s") and (not after or after in _RETRACTION_PRONOUN_FOLLOWERS):
        # "scratch that key's in the shed": the 's is a copula, not a possessive
        return True
    if any(ch.isdigit() for ch in phrase[0]) or phrase[0] in _NUMBER_WORDS:
        # "that" determines a stated value ("scratch that 9:40 pickup",
        # "scratch that two"); a number after the noun is not what it points at
        return True
    return _names_a_said_thing(phrase[-1])


def _cancel_is_service_request(text: str, end: int) -> bool:
    """A cancel whose object is followed by a first-person benefactive
    ("cancel that booking for me"): the listener is asked to cancel."""
    tail = str(text or "")[end:]
    stop = _RETRACTION_TAIL_BOUNDARY_RE.search(tail)
    if stop is not None:
        tail = tail[:stop.start()]
    return bool(_CANCEL_SERVICE_BENEFACTIVE_RE.match(tail))


def retraction_marker_is_live(text: str, match: re.Match[str]) -> bool:
    """Whether one closed-class retraction match actually withdraws speech
    (see the grammar above): never when negated; for the speech verbs only
    as a directive to the listener; for "scratch that" only as a directive
    whose "that" points back at what was said; and never for a cancel that
    asks the listener to perform a service for the speaker."""
    prefix = _retraction_clause_prefix(text, match.start())
    if _RETRACTION_NEGATION_TAIL_RE.search(prefix):
        return False
    marker = match.group(0).lower()
    words = [w.replace("’", "'") for w in re.findall(r"[A-Za-z'’]+", prefix.lower())]
    if _RETRACTION_DIRECTIVE_VERB_RE.match(marker):
        return _retraction_prefix_is_directive(words)
    if marker.startswith("scratch"):
        return (_retraction_prefix_is_directive(words)
                and _scratch_that_points_back(text, match.end()))
    if marker.startswith("cancel"):
        return not (_retraction_prefix_is_directive(words)
                    and _cancel_is_service_request(text, match.end()))
    return True


def retraction_marker(text: str) -> re.Match[str] | None:
    """The first LIVE closed-class retraction in *text* (see
    :func:`retraction_marker_is_live`), or None."""
    body = str(text or "")
    for match in _RETRACTION_MARKER_RE.finditer(body):
        if retraction_marker_is_live(body, match):
            return match
    return None


#: The ACT words of the retraction class ("scratch that", "never mind",
#: "forget what I said", "on second thought", "take that paste down"): they
#: name the withdrawing, never what is withdrawn, so a live retraction's act
#: words are not subject identity when slots are joined or a withdrawal is
#: tied (external review, 2026-10-06: "never mind the bread maker" shared
#: "never" with "I never skip breakfast" and withdrew it). The object of a
#: removal request ("paste", "quote") is not an act word: it names the thing
#: taken back.
_RETRACTION_ACT_WORDS = frozenset({
    "scratch", "never", "mind", "pretend", "forget", "disregard", "ignore",
    "cancel", "second", "thought", "retract", "retraction", "retracted",
    "withdraw", "withdrawn", "withdrawing", "undo", "undone", "take", "pull",
    "tear", "down", "remove",
})


def _retraction_act_words(text: str) -> frozenset[str]:
    """The act words (stemmed like :func:`slot_signature`) of the LIVE
    retraction markers in *text*; empty for a record that retracts nothing."""
    body = str(text or "")
    words: set[str] = set()
    for match in _RETRACTION_MARKER_RE.finditer(body):
        if retraction_marker_is_live(body, match):
            words.update(_stem(token.lower()) for token in _TOKEN_RE.findall(match.group(0)))
    return frozenset(words & _RETRACTION_ACT_WORDS)


_CORRECTION_MARKER_RE = re.compile(
    r"\b(?:correction|corrected|recount|revised|revision|update(?:d|s)?|"
    r"instead|after\s+all|no\s+longer|not\s+any\s*more|not\s+anymore|"
    r"change(?:d|s)?|new\s+(?:total|number|figure|value|price|rate|name)|"
    r"final\s+update|one\s+more\s+change|actually|"
    r"(?:are|is)\s+(?:back|over)|back\s+to\s+normal|"
    # restorations: an explicit UNDO of a correction re-states the original
    # value and must supersede the wrong correction like any replacement
    # (measured, fresh cohort F01-04: "No wait, the first reading was
    # right" formed its own slot and the dead correction kept serving)
    r"no\s+wait|was\s+(?:right|correct)|"
    # value-change verbs reassign a value — they are replacements, not
    # subject identity ("It rose to 8 credits" supersedes the 6 it rose
    # from; the REASSIGNMENT_TOKENS law already treats them as reassigning)
    r"(?:rose|rises|risen|climbed|went\s+up|jumped|increased|dropped|"
    r"fell|was\s+raised|raised|hiked|boosted|was\s+cut|cut\s+to|"
    r"lowered|moved|moves|switched|switches|swapped|swaps|converted|"
    r"converts|goes|swap|switch|move|convert)\s+to)\b",
    re.IGNORECASE,
)


def _stem(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(str(text or ""))]


def value_tokens(text: str) -> set[str]:
    """The measurable/identifier values a statement asserts: numbers (decimal
    and grouped), clock times, bare integers, ordinals, number words and
    frequency measures."""
    values: set[str] = set()
    for match in _VALUE_TOKEN_RE.finditer(str(text or "")):
        values.add(match.group(0).replace(",", ""))
    lowered = {t.lower() for t in _tokens(text)}
    values |= lowered & _NUMBER_WORDS
    return values


def slot_signature(text: str) -> frozenset[str]:
    """Subject+predicate identity of a statement: content tokens minus
    stopwords, temporal vocabulary, units and asserted values. A tokenizer
    fragment of a value ("22:00" → "22"/"00", "0.9" → "0"/"9") is value
    material, never subject identity."""
    values = value_tokens(text)
    value_fragments: set[str] = set()
    for value in values:
        value_fragments.update(t.lower() for t in _TOKEN_RE.findall(value))
    sig: set[str] = set()
    tokens = _tokens(text)
    for idx, token in enumerate(tokens):
        if token in _TEMPORAL_WORDS and _is_attributive(tokens, idx):
            # A day/season word that MODIFIES a following subject noun
            # ("the Monday ferry", "the weekend inspection slot", "the
            # winter timetable") is part of the subject's identity, not a
            # temporal qualifier of the value: stripping it made parallel
            # subjects identical and recency supersession collapsed them
            # ("Monday ferry 09:15" vs "Thursday ferry 10:40"; exposed
            # corpus F07-13/F15-10). Adverbial uses ("leaves on Monday")
            # stay temporal — there the next token is not the subject noun.
            sig.add(_stem(token))
            continue
        if (token in _STOPWORDS or token in _TEMPORAL_WORDS
                or token in _UNIT_WORDS or token in _NUMBER_WORDS
                or token.isdigit() or token in value_fragments):
            continue
        sig.add(_stem(token))
    return frozenset(sig - values)


#: Time-of-day vocabulary stays temporal even in attributive position:
#: successive readings of ONE series are marked by it ("the morning log
#: says 27" / "the afternoon log says 28.5" - the SAME gauge observed
#: twice; measured F11-04), unlike weekday/season words which name WHICH
#: subject ("the Monday ferry" vs "the Thursday ferry").
_TIME_OF_DAY_SUBJECT_WORDS = frozenset({
    "dawn", "morning", "noon", "afternoon", "evening", "night", "midnight",
    "dusk", "daybreak", "sunrise", "sunset",
})


def _is_attributive(tokens: list[str], idx: int) -> bool:
    """A temporal token used as an attributive modifier: immediately
    followed by another content word that is itself not stopword/temporal/
    unit/number/value material — i.e. it qualifies a NOUN, so it names
    WHICH subject, not WHEN."""
    if idx + 1 >= len(tokens):
        return False
    if tokens[idx] in _TIME_OF_DAY_SUBJECT_WORDS:
        return False
    nxt = tokens[idx + 1]
    return not (nxt in _STOPWORDS or nxt in _TEMPORAL_WORDS
                or nxt in _UNIT_WORDS or nxt in _NUMBER_WORDS
                or nxt.isdigit())


def _strip_reversion(text: str) -> str:
    return _REVERSION_RE.sub(" ", str(text or ""))


#: Reversion constructions: the exception ENDED and the standing value
#: resumed. A reversion restores — it supersedes nothing itself (the closed
#: window's record expires by its own dates); it must coexist with the
#: standing value it confirms.
_REVERSION_RE = re.compile(
    r"\b(?:(?:are|is)\s+(?:back|over)|back\s+to\s+normal)\b",
    re.IGNORECASE,
)

#: Epistemic hedges: a SPECULATIVE alternative is not a correction. The
#: speaker has not decided ("might instead book the 11:15 — no decision
#: yet"), so the statement withdraws nothing (measured: the Q07 control —
#: the hedge rode as the answer and superseded the actual booking).
_HEDGE_RE = re.compile(
    r"\b(?:might|may|maybe|perhaps|possibly|consider(?:ing)?|thinking\s+of|"
    r"alternativ(?:e|ely)|option(?:ally)?|if\s+it\s+looks|no\s+decision|"
    r"not\s+(?:yet\s+)?decided|undecided|tentative|provisional|"
    r"we'll\s+see|see\s+how)\b",
    re.IGNORECASE,
)


def _is_hedged(text: str) -> bool:
    # Month names are not modality: "in May" is a DATE, and the bare token
    # "may" in the hedge regex read "It climbed to 85 tonnes a week in May."
    # as speculative — suppressing the correction marker, splitting the
    # ladder and serving the stale value (challenge N21). A month in a
    # prepositional date slot is stripped before matching; genuine modality
    # ("that may change next week") survives — its "may" stands alone.
    body = str(text or "")
    stripped = re.sub(
        rf"\b(?:in|of|on|by|since|from|to|through|late|early|mid)\s+"
        rf"(?:{'|'.join(_MONTHS)})\b\.?,?",
        " ", body, flags=re.IGNORECASE)
    return bool(_HEDGE_RE.search(stripped))


def carries_undo_marker(text: str) -> bool:
    """A closed-class un-doing / replace-ing construction. A hedged,
    speculative statement carries no CORRECTION semantics — it replaces
    nothing ("might instead book the 11:15 — no decision yet"). An explicit
    RETRACTION outranks the hedge: "scratch that — the piece is undecided"
    declares the withdrawal, and "undecided" is its stated replacement
    value, not speculation (measured: the Fauré retraction stopped
    superseding and the commitment resurrected)."""
    body = str(text or "")
    if retraction_marker(body):
        return True
    if _is_hedged(body):
        return False
    return bool(_CORRECTION_MARKER_RE.search(body))


def anchor_terms(text: str) -> set[str]:
    """The vocabulary a record's chain mates share: its slot signature plus
    its unit words. Retrieval uses this to fetch same-chat statements that
    supersede a hit but share no vocabulary with the QUESTION itself."""
    lowered = {t.lower() for t in _tokens(text)}
    return set(slot_signature(text)) | (lowered & _UNIT_WORDS)


# ───────────────────── explicit effective windows in record text ──────────

_WINDOW_RE = re.compile(
    rf"\b(?:from|between)\s+(?P<start>(?:{_MONTH_RE})\.?\s+\d{{1,2}}(?:\s+(?:19|20)\d{{2}})?|\d{{4}}-\d{{2}}-\d{{2}})"
    rf"\s+(?:through|thru|to|until|till|and)\s+(?P<end>(?:{_MONTH_RE})\.?\s+\d{{1,2}}(?:\s+(?:19|20)\d{{2}})?|\d{{4}}-\d{{2}}-\d{{2}})\b",
    re.IGNORECASE,
)
_UNTIL_RE = re.compile(
    rf"\b(?:through|until|till)\s+(?P<end>(?:{_MONTH_RE})\.?\s+\d{{1,2}}(?:\s+(?:19|20)\d{{2}})?|\d{{4}}-\d{{2}}-\d{{2}})\b",
    re.IGNORECASE,
)
#: Month-grain until windows ("ran through March — 30 percent off"): the
#: window ends at the named month's END (the same inclusive anchor the
#: effective-date month parser uses). Without it an expired discount
#: stayed eligible forever and its dead value served as current (exposed
#: corpus F07-08).
_UNTIL_MONTH_RE = re.compile(
    rf"\b(?:through|until|till)\s+(?P<month>{_MONTH_RE})\.?(?:\s+(?P<year>(?:19|20)\d{{2}}))?(?![\s,]*\d)",
    re.IGNORECASE,
)


def _parse_record_date(phrase: str, year_hint: int | None) -> date | None:
    text = str(phrase or "").strip().strip(",.")
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    # An EXPLICIT year in the phrase is the speaker's own dating and takes
    # precedence over year_hint (the statement's calendar year): "starts on
    # 14 November 2028" said in 2026 is 2028-11-14, never 2026-11-14
    # (owner probes 2026-09-30; historical years like 2019 equally). Only a
    # yearless phrase falls back to the hint.
    m = re.fullmatch(
        rf"({_MONTH_RE})\.?\s+(\d{{1,2}})(?:\s+((?:19|20)\d{{2}}))?", text, re.IGNORECASE
    )
    if m:
        year = int(m.group(3)) if m.group(3) else (year_hint or datetime.now(_UTC).year)
        try:
            return date(year, _MONTHS[m.group(1).lower()], int(m.group(2)))
        except ValueError:
            return None
    # day-first ("14 November"): the same date, spoken the other way round
    m = re.fullmatch(
        rf"(\d{{1,2}})\.?\s+({_MONTH_RE})(?:\s+((?:19|20)\d{{2}}))?", text, re.IGNORECASE
    )
    if m:
        year = int(m.group(3)) if m.group(3) else (year_hint or datetime.now(_UTC).year)
        try:
            return date(year, _MONTHS[m.group(2).lower()], int(m.group(1)))
        except ValueError:
            return None
    return None


def effective_window(
    text: str, year_hint: int | None = None
) -> tuple[date | None, date | None]:
    """An explicit applicability window the record's own text declares
    ("free of charge from April 10 through April 24"). Inclusive end; the
    year comes from the record's own statement time when it has one."""
    body = str(text or "")
    m = _WINDOW_RE.search(body)
    if m:
        start = _parse_record_date(m.group("start"), year_hint)
        end = _parse_record_date(m.group("end"), year_hint)
        if start or end:
            return start, end
    m = _UNTIL_RE.search(body)
    if m:
        end = _parse_record_date(m.group("end"), year_hint)
        if end is not None:
            return None, end
    mm = _UNTIL_MONTH_RE.search(body)
    if mm:
        import calendar as _calendar
        month = _MONTHS[mm.group("month").lower()]
        year = int(mm.group("year")) if mm.group("year") else (
            year_hint if year_hint is not None else datetime.now(_UTC).year)
        try:
            last = _calendar.monthrange(year, month)[1]
            return None, date(year, month, last)
        except ValueError:
            return None, None
    return None, None


# An explicit request for recorded interval bounds asks for source history,
# including endpoints that prove a person was no longer assigned. Readability
# of those bounds does not declare that every interval is active on the ask's
# date. Ordinary scalar/current requests retain their applicability policy.
_INTERVAL_EVIDENCE_REQUEST_RE = re.compile(
    r"\b(?:recorded|documented|stated|exact|dated|date)\s+"
    r"(?:(?:date|recorded|assignment|effective)\s+){0,2}"
    r"(?:intervals?|ranges?|bounds?)\b",
    re.IGNORECASE,
)


def _declared_interval_windows(
    text: str, year_hint: int | None = None,
) -> tuple[tuple[date, date], ...]:
    """All valid explicit windows, without imposing the first on the body.

    Each range retains its own bounds. Invalid/reversed ranges establish no
    interval authority; one person's ended range cannot date another range
    in the same authorized message.
    """
    windows = []
    for match in _WINDOW_RE.finditer(str(text or "")):
        start = _parse_record_date(match.group("start"), year_hint)
        end = _parse_record_date(match.group("end"), year_hint)
        if start is not None and end is not None and start <= end:
            windows.append((start, end))
    return tuple(windows)


#: Successive readings of one measured subject share a frame noun and carry
#: distinct time-of-day markers ("The morning log says 27" / "The afternoon
#: log says 28.5"): they are ONE observation series whose later member is
#: the newer state, not parallel facts. Signature containment (one side
#: subject-poor/anaphoric) keeps different subjects' same-frame readings
#: apart ("the morning pool log" vs "the afternoon till log").
_TIME_OF_DAY_RE = re.compile(
    r"\b(?:dawn|morning|noon|afternoon|evening|night|midnight)\b",
    re.IGNORECASE,
)
_READING_FRAME_NOUNS = frozenset({
    "log", "logs", "reading", "readings", "report", "reports",
    "measurement", "measurements", "check", "checks", "meter", "meters",
    "gauge", "gauges", "sensor", "sensors", "scan", "scans",
})


def _frame_nouns(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(str(text or ""))} & _READING_FRAME_NOUNS


def is_reading_frame_observation(text: str) -> bool:
    body = str(text or "")
    return bool(_TIME_OF_DAY_RE.search(body)) and bool(_frame_nouns(body))


def _same_reading_series(a: str, b: str) -> bool:
    if not (is_reading_frame_observation(a) and is_reading_frame_observation(b)):
        return False
    if not (_frame_nouns(a) & _frame_nouns(b)):
        return False
    sig_a, sig_b = slot_signature(a), slot_signature(b)
    return sig_a <= sig_b or sig_b <= sig_a


# ──────────────────────────── slot resolution ──────────────────────────────

def _order_key(candidate: TemporalCandidate) -> tuple[float, float, float, int]:
    state = state_time(candidate)
    stmt = candidate.statement_at if candidate.statement_at is not None else (
        candidate.recorded_at if candidate.recorded_at is not None else float("inf")
    )
    rec = candidate.recorded_at if candidate.recorded_at is not None else float("inf")
    # State-dated records order by their state date and outrank state-less
    # ones (which sort last and stably by write order; they never win by
    # order over a dated sibling).
    state_key = state if state is not None else float("inf")
    return (state_key, stmt if stmt is not None else float("inf"),
            rec if rec is not None else float("inf"),
            candidate.seq if candidate.seq is not None else -1)


def _spoken_before(a: TemporalCandidate, b: TemporalCandidate) -> bool:
    """Known utterance/capture order, independent of state/effective dates."""
    if a.seq is not None and b.seq is not None:
        return a.seq < b.seq
    for left, right in ((a.statement_at, b.statement_at), (a.recorded_at, b.recorded_at)):
        if left is not None and right is not None and left != right:
            return left < right
    return False


def _slots_overlap(a: frozenset[str], b: frozenset[str]) -> bool:
    if not a or not b:
        return False
    shared = a & b
    return len(shared) >= 2 and (len(shared) / min(len(a), len(b))) >= 0.4


def _token_kin(a: str, b: str) -> bool:
    """Morphological kinship for JOINING a marker statement to its target:
    one token is a prefix-inflection of the other (paste/pasting,
    quote/quoted), folding the silent e. Four characters minimum keeps
    unrelated short words apart."""
    ca, cb = a.rstrip("e"), b.rstrip("e")
    m = min(len(ca), len(cb))
    return m >= 4 and ca[:m] == cb[:m]


#: A from-dated anaphoric continuation: the statement opens by declaring
#: when its state began and refers to its subject by pronoun — "From 22
#: July 1997 it has run 22-passenger rafts." It continues an earlier
#: statement's subject, so it joins that chain like a marker does.
_ANAPHORIC_FROM_DATE_RE = re.compile(
    rf"(?is)^\s*(?:from|since)\s+(?:the\s+)?"
    rf"(?:{_MONTH_RE}|\d|this|that|last|next)",
)


def _speaker_key(candidate: TemporalCandidate) -> str:
    """Case- and spacing-insensitive speaker identity ('' = unattributed)."""
    return " ".join(str(candidate.speaker or "").rstrip(":").split()).casefold()


def _build_slots(candidates: Sequence[TemporalCandidate]) -> dict[str, list[int]]:
    """Group candidate indexes into slots (see :func:`_build_slots_with_ties`)."""
    return _build_slots_with_ties(candidates)[0]


def _build_slots_with_ties(
    candidates: Sequence[TemporalCandidate],
) -> tuple[dict[str, list[int]], frozenset[frozenset[int]]]:
    """Group candidate indexes into slots. Two laws: signature collision, and
    marker-carrying statements joining the most recent earlier chain whose
    signature shares a token or a unit word with them (a terse correction
    often shares nothing but its subject's unit: "Recount says 4,900 euros.").

    Also returns the DIRECT ties: the index pairs that one of the join laws
    links on their own, as opposed to pairs that only share a slot through
    the transitive closure. Slot membership is transitive (a marker
    statement joins every chain it shares one word with, and each chain
    brings its own members), so a correction winner may supersede only the
    members it is directly tied to: a value-less "I actually stayed at the
    hotel" reached an unrelated race time two hops away through a shared
    word with a bridge record and withdrew it (measured, exposed holdout:
    19 records superseded by one correction, the decisive operand among
    them)."""
    ties: set[frozenset[int]] = set()
    # A live retraction's act words ("never mind", "scratch", "take ...
    # down") name the withdrawing, not its subject: they never join slots.
    acts = [_retraction_act_words(c.body) for c in candidates]
    sigs = [slot_signature(c.body) - act for c, act in zip(candidates, acts)]
    # Unit vocabulary includes the components of hyphenated compounds: a
    # value-bearing token like "7.9-metre" carries the unit 'metre', and a
    # join that missed it split successive readings of one measure into
    # parallel slots (challenge N23: "a 7.9-metre high" vs "a 7.2-metre
    # equinoctial high" never joined and the stale 2018 value served).
    units = [
        (({t.lower() for t in _tokens(c.body)}
          | {part.lower() for t in _tokens(c.body)
             for part in t.split("-") if len(part) > 2}) & _UNIT_WORDS) - act
        for c, act in zip(candidates, acts)
    ]
    parent = list(range(len(candidates)))
    # Slot identity is per speaker: a slot never holds statements attributed
    # to two different speakers. Each component carries the speaker its
    # attributed members share ('' while none is attributed); a join that
    # would put a second speaker into it is refused, so an unattributed
    # bridge record cannot carry one person's turn into another's slot
    # through the transitive closure either.
    component_speaker = [_speaker_key(c) for c in candidates]

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> bool:
        ri, rj = find(i), find(j)
        if ri == rj:
            return True
        si, sj = component_speaker[ri], component_speaker[rj]
        if si and sj and si != sj:
            return False
        low, high = min(ri, rj), max(ri, rj)
        parent[high] = low
        component_speaker[low] = si or sj
        return True

    for i in range(len(candidates)):
        for j in range(i):
            if _slots_overlap(sigs[i], sigs[j]) and union(i, j):
                ties.add(frozenset((i, j)))

    def _content_bigrams(text: str, act: frozenset[str]) -> set[tuple[str, str]]:
        toks = [t for t in _tokens(text) if t not in _STOPWORDS]
        return {(toks[k], toks[k + 1]) for k in range(len(toks) - 1)
                if _stem(toks[k]) not in act and _stem(toks[k + 1]) not in act}

    bigrams = [_content_bigrams(c.body, act) for c, act in zip(candidates, acts)]
    # A marker statement joins the chains SPOKEN BEFORE it, which is
    # sequence order - not state order. State-dated rungs sort EARLIER than
    # the state-less value they replace ("It rose to 8 credits in June" has
    # a June state; "Binding was 6 credits for years" has none), so a
    # state-ordered scan never met the target chain and the ladder split
    # into singleton slots (measured, fresh cohort F07-05).
    order = sorted(range(len(candidates)), key=lambda i: (
        candidates[i].seq if candidates[i].seq is not None else float("inf"),
        candidates[i].statement_at if candidates[i].statement_at is not None else (
            candidates[i].recorded_at if candidates[i].recorded_at is not None else float("inf")),
        candidates[i].key,
    ))
    for pos, i in enumerate(order):
        # A from-dated anaphoric CONTINUATION ("From 22 July 1997 it has
        # run 22-passenger rafts.") joins earlier chains under the same
        # anchors as a marker statement: the pronoun subject has no chain
        # of its own, and the declared from-date is the start of the very
        # state the earlier statement held (challenge N10: the 1997 rung
        # stayed a singleton, the packer covered it with the stale base,
        # and the as-of ask starved).
        joins_like_marker = (
            carries_undo_marker(candidates[i].body)
            or _ANAPHORIC_FROM_DATE_RE.search(candidates[i].body))
        if not joins_like_marker:
            continue
        # Join EVERY earlier chain the marker statement anchors to (a shared
        # signature token, unit word, or subject PHRASE). Stopping at the
        # first match split a measured chain in two: the correction anchored
        # to an assistant echo of the subject and never reached the statement
        # it superseded. The phrase arm reaches reversion statements whose
        # signature drifted from the exception they close ("Open weekend: …
        # free …" vs "Open weekend over — normal tickets are back") — they
        # share the subject phrase, not its content tokens.
        for j in reversed(order):
            if not _spoken_before(candidates[j], candidates[i]):
                continue
            # The join test runs even when the pair already shares a slot:
            # the DIRECT tie is recorded for supersession, which a pair
            # linked only through other members must not get.
            if (bool(sigs[i] & sigs[j]) or bool(units[i] & units[j])
                    or bool(bigrams[i] & bigrams[j])
                    # a marker statement that RE-STATES an earlier record's
                    # value is about that record's subject matter even when
                    # its wording shares nothing ("No wait, the first
                    # reading was right: it is 55-014" joins the card slot
                    # through the value it restores; measured F01-04)
                    or bool(value_tokens(candidates[i].body)
                            & value_tokens(candidates[j].body))
                    # morphological tolerance at the JOIN only: "pasting"
                    # and "paste" are the same subject word to a speaker
                    # asking for its removal (the stemmer does not strip
                    # -ing/-ed; measured, F03-12: the withdrawal never
                    # reached the paste's slot and the quote kept serving)
                    or any(
                        _token_kin(a, b)
                        for a in sigs[i] for b in sigs[j]
                    )) and union(i, j):
                ties.add(frozenset((i, j)))
        # A PRONOUN-subject from-dated continuation has no lexical subject
        # to share ("Since 14 August 2019 it has carried an LED cluster."):
        # its antecedent is the statement spoken immediately before it —
        # the canonical anaphora resolution when nothing lexical remains
        # (challenge N10 amendment wording: 'carry'/'carried' share no
        # stem the tokenizer can see).
        if (pos > 0
                and _ANAPHORIC_FROM_DATE_RE.search(candidates[i].body)
                and re.search(
                    r"\b(?:it|they|this|that|these|those)\b",
                    candidates[i].body, re.IGNORECASE)):
            # the statement opens with its from-date clause, so a pronoun
            # anywhere in it is the anaphoric subject
            antecedents = [j for j in order if _spoken_before(candidates[j], candidates[i])]
            if antecedents:
                nearest = antecedents[-1]
                # Without a sequence, tied preceding timestamps do not identify
                # one antecedent. Preserve the ambiguity instead of rank-binding.
                tied = [j for j in antecedents if _order_key(candidates[j]) == _order_key(candidates[nearest])]
                if len(tied) == 1 and union(i, nearest):
                    ties.add(frozenset((i, nearest)))

    # Reading-frame series: same frame noun, both time-of-day marked, one
    # side's subject vocabulary contained in the other's (the anaphoric
    # "The afternoon log says 28.5." against "The morning log says the
    # water temperature was 27." - measured, fresh cohort F11-04: the two
    # readings formed separate slots and the stale morning value served).
    for i in range(len(candidates)):
        for j in range(i):
            if (_same_reading_series(candidates[i].body, candidates[j].body)
                    and union(i, j)):
                ties.add(frozenset((i, j)))

    slots: dict[str, list[int]] = {}
    for i in range(len(candidates)):
        slots.setdefault(f"slot:{find(i):d}", []).append(i)
    return slots, frozenset(ties)


def _is_user_record(candidate: TemporalCandidate) -> bool:
    if str(candidate.role).lower() == "user":
        return True
    return "user" in str(candidate.authority).lower() and "assistant" not in str(
        candidate.authority
    ).lower()


def _measure_kinds(text: str) -> frozenset[str]:
    """The measure units a statement's values are denominated in. Two
    statements conflict only when their values are the same KIND of measure:
    "takes 25 minutes" and "costs 3 euro" are different aspects of one
    subject, not competing values (measured: the causeway pair). Unit-less
    values (codes, clock times, cadences) are their own compatible kind."""
    return frozenset({t.lower() for t in _tokens(text)} & _UNIT_WORDS)


def _value_conflict(a: str, b: str) -> bool:
    """Two statements assert DIFFERENT measurable values for their slot: both
    carry values, the sets are disjoint, and the values are the same kind of
    measure. Repeated values coexist; different measures coexist."""
    va, vb = value_tokens(a), value_tokens(b)
    if not va or not vb or (va & vb):
        return False
    ka, kb = _measure_kinds(a), _measure_kinds(b)
    if ka and kb and not (ka & kb):
        return False
    return True


#: Closed reassignment vocabulary: verbs/adverbs of REASSIGNING, not subject
#: identity. A distinctive-token comparison that counts them would read
#: "Bottling moved to the 21st" as a different subject from "bottling is
#: the 14th" and let the stale value coexist for a current ask.
_REASSIGNMENT_TOKENS = frozenset({
    "move", "moves", "moved", "moving", "switch", "switched", "swap",
    "swapped", "reschedule", "rescheduled", "rebook", "rebooked",
    "reassign", "reassigned", "change", "changed", "update", "updated",
    "instead", "back", "now", "make", "final", "correct", "correction",
    "corrected", "recount", "revised", "revision",
    # value-change verbs: "rises to 55", "dropped to 3" reassign a value —
    # they are not subject identity (measured: the badge-renewal season
    # ladder must supersede, not coexist)
    "rise", "rises", "rose", "raise", "raised", "raising",
    "increase", "increases", "increased", "jump", "jumps", "jumped",
    "boost", "boosted", "drop", "drops", "dropped", "fall", "falls",
    "fell", "cut", "cuts", "hike", "hikes", "hiked", "reduce", "reduced",
    "lower", "lowered", "goes", "went", "goe", "new",
})


#: Light/continuation verbs carry no subject identity: "it has run X" vs
#: "the fleet ran Y" differ only in tense of RUN, and counting 'run' as a
#: distinctive subject token read one continuing subject as parallel facts
#: (challenge N10). Nouns remain the parallel detectors.
_LIGHT_VERB_TOKENS = frozenset({
    "run", "runs", "ran", "running", "hold", "holds", "held", "holding",
    "carry", "carries", "carried", "carrying", "serve", "serves", "served",
    "serving", "publish", "publishes", "published", "publishing", "keep",
    "keeps", "kept", "keeping", "offer", "offers", "offered", "offering",
    "pour", "pours", "poured", "pouring", "lift", "lifts", "lifted",
    "lifting", "use", "used", "uses", "using", "take", "takes", "took",
    "taken", "taking", "leave", "leaves", "left", "leaving", "sell",
    "sells", "sold", "selling",
})

#: A closed reassignment construction ("switches to X", "moves to Y",
#: "rises to N"): the winner ANNOUNCES replacement of the value, like a
#: correction. The object it reassigns to is value material, not subject
#: identity — without this arm a "switches to six induction furnaces"
#: winner read 'induction' as a second subject and the stale furnace type
#: coexisted with its own replacement (challenge N04).
_REASSIGNMENT_CONSTRUCTION_RE = re.compile(
    r"\b(?:switch\w*|move\w*|go(?:es)?\s+up|went\s+up|rise|rises|rose|"
    r"climb\w*|jump\w*|drop\w*|fall\w*|fell|increase\w*|convert\w*|"
    r"change\w*|swap\w*|switch(?:ed)?\s+over)\s+(?:up\s+)?to\b",
    re.IGNORECASE,
)


def _parallel_subject_facts(loser_body: str, winner_body: str) -> bool:
    """Two statements about DIFFERENT subjects under a shared template.

    Each side carries a distinctive signature token the other lacks (beacon
    vs lighthouse, reminder-one vs its restatement): they are parallel facts
    of one family, not competing values of one subject. Reassignment and
    correction vocabulary is not subject identity. Only a winner that UNDOES
    or CORRECTS (closed marker class) may supersede across that boundary —
    pure recency never does. Measured 2026-09-29 (q90 dev corpus): all 12
    rota notes collapsed into one 'latest wins' slot (F16-05) and the first
    parking reminder was superseded by its own restatement (F10-12).
    """
    if carries_undo_marker(winner_body):
        return False
    if _REASSIGNMENT_CONSTRUCTION_RE.search(winner_body):
        # an explicit "switches/moves/rises to <value>" winner replaces the
        # value it names, across the template boundary — same license as a
        # correction (challenge N04: 'switches to six induction furnaces'
        # must supersede 'runs two cupola furnaces')
        return False
    if _same_reading_series(loser_body, winner_body):
        # successive readings of one series are the SAME subject observed
        # twice, not parallel facts (measured F11-04)
        return False
    def _subject_view(body: str) -> set[str]:
        return {
            tok for tok in slot_signature(_strip_reversion(body))
            if tok not in _REASSIGNMENT_TOKENS
            and tok not in _LIGHT_VERB_TOKENS
            # a digit-bearing token is value material (88-110, 07:00), never
            # subject identity — counting it as distinctive read two codes
            # of ONE keypad as parallel subjects (measured: keypad restate)
            and not any(ch.isdigit() for ch in tok)
        }
    lo_sig = _subject_view(loser_body)
    win_sig = _subject_view(winner_body)
    if not lo_sig or not win_sig:
        return False
    return bool((lo_sig - win_sig) and (win_sig - lo_sig))


#: A fractional reassignment moves only its fraction: the complement of a
#: "half of them moved" statement stays true, so the general assignment it
#: narrows is NOT superseded (measured, fresh cohort F05-11: "Half of them
#: actually go to the hill yard now." deleted "The extra supers go to the
#: river yard." from the answer).
_PARTITIVE_QUANTIFIER_RE = re.compile(
    r"\b(?:half|some|most|a\s+third|one\s+third|two\s+thirds|a\s+quarter|"
    r"a\s+fifth|the\s+rest|the\s+remainder|the\s+others)\s+of\b",
    re.IGNORECASE,
)

#: Two statements carrying DIFFERENT calendar years over a shared subject
#: noun are parallel editions, not competing values: "The 2025 audit found
#: 5" does not supersede "The 2026 audit found 7" — both audits happened
#: (measured, fresh cohort F10-06; year digits are values, so the ordinary
#: subject-signature view cannot distinguish editions of one series).
_EDITION_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")


def _edition_distinct_subjects(loser_body: str, winner_body: str) -> bool:
    lo_years = {m.group(1) for m in _EDITION_YEAR_RE.finditer(str(loser_body or ""))}
    win_years = {m.group(1) for m in _EDITION_YEAR_RE.finditer(str(winner_body or ""))}
    if not lo_years or not win_years or (lo_years & win_years):
        return False
    return bool(slot_signature(loser_body) & slot_signature(winner_body))


#: An explicit restoration revives what a retraction killed ("no wait,
#: the first reading was right"). Also a member of the correction marker
#: class, kept separate so chain adjudication can recognize revival.
_RESTORATION_MARKER_RE = re.compile(
    r"\b(?:no\s+wait|was\s+(?:right|correct))\b", re.IGNORECASE,
)


def _authority_party(candidate: TemporalCandidate) -> str:
    """The side of the conversation that said a record: "user", or the
    non-user role that produced it ("assistant", "tool", ...)."""
    if _is_user_record(candidate):
        return "user"
    return str(candidate.role or "").strip().lower() or "assistant"


def _may_take_back(speaker_record: TemporalCandidate, record: TemporalCandidate) -> bool:
    """Whether *speaker_record* may withdraw (or restore) *record*: a speaker
    takes back only their OWN statement. A retraction un-says the speaker's
    prior speech; an assistant line never un-says what the user stated, a
    user line never un-says what the assistant stated, and two attributed
    speakers never un-say each other."""
    if _authority_party(speaker_record) != _authority_party(record):
        return False
    own, other = _speaker_key(speaker_record), _speaker_key(record)
    return not (own and other and own != other)


def _withdrawn_in_chain(
    i: int,
    members: list[int],
    normalized: list[TemporalCandidate],
) -> int | None:
    """Index of the later same-slot RETRACTION that withdraws record *i*.

    Winner-only conflict adjudication lets a dead record resurrect when a
    later non-marker statement wins its slot: the paste a withdrawal
    killed, then a restate superseded the withdrawal itself, came back as
    "coexisting" with the winner (measured, fresh cohort F03-11). A
    retraction's kill is chain-stable — only an explicit later restoration
    revives it.

    Authority: only the party that said record *i* withdraws or restores it
    (:func:`_may_take_back`). Slot membership pairs a user statement with
    the assistant lines about it, so without this a later assistant
    "never mind" withdrew the user's own claim, and a user's "scratch that"
    withdrew what the assistant had stated.
    """
    rank = {idx: pos for pos, idx in enumerate(members)}
    if rank.get(i) is None:
        return None
    loser = normalized[i]
    lo_sig = slot_signature(loser.body)
    lo_values = value_tokens(loser.body)
    for j in members:
        if rank.get(j) is None or not _spoken_before(loser, normalized[j]):
            continue
        if not _may_take_back(normalized[j], loser):
            continue
        jbody = normalized[j].body
        if not retraction_marker(jbody):
            continue
        # the retraction's own act words are not what it is about
        j_sig = slot_signature(jbody) - _retraction_act_words(jbody)
        j_values = value_tokens(jbody)
        if (_RESTORATION_MARKER_RE.search(jbody)
                and lo_values and lo_values <= j_values):
            # "Ignore that correction; the first value was right" retracts
            # the correction, not the very value it explicitly restores.
            continue
        tied = (
            bool(lo_sig & j_sig)
            or any(_token_kin(a, b) for a in lo_sig for b in j_sig)
            or bool(lo_values & value_tokens(jbody))
        )
        if not tied:
            continue
        for k in members:
            if (rank.get(k) is not None and _spoken_before(normalized[j], normalized[k])
                    and _may_take_back(normalized[k], loser)
                    and _RESTORATION_MARKER_RE.search(normalized[k].body)
                    and (not lo_values or lo_values <= value_tokens(normalized[k].body))):
                return None  # the same speaker's later explicit restoration revived it
        return j
    return None



def _conditional_procedure_preserves_base(
    base: str, amendment: str, question: str,
) -> bool:
    """A complete procedure needs its explicitly unchanged predecessor steps.

    Conditional replacement applies inside its stated condition. Readability
    of both source units does not assert that the condition holds; synthesis
    retains their wording. Narrow value queries keep the existing state law.
    """
    if not re.search(
        r"\b(?:full|complete|entire|whole)\b[^.?!\n]{0,48}"
        r"\b(?:procedure|process|sequence|steps?)\b"
        r"|\b(?:all|every)\b[^.?!\n]{0,48}\bsteps?\b",
        str(question or ""), re.IGNORECASE,
    ):
        return False
    clauses = re.split(r"[.!?\n]+", str(amendment or ""))
    conditional_replacement = any(re.search(
        r"\b(?:if|unless|when)\b[^.!?\n]{1,480}"
        r"\b(?:instead|rather\s+than)\b", clause, re.IGNORECASE,
    ) for clause in clauses)
    if not conditional_replacement:
        return False
    base_terms = slot_signature(base)
    for clause in clauses:
        preserved = re.search(
            r"^(?P<steps>.*?)\b(?:still|remain(?:s)?)\b"
            r"[^.!?\n]{0,96}\b(?:original|same)\s+(?:order|sequence)\b",
            clause.strip(), re.IGNORECASE,
        )
        if not preserved:
            continue
        step_terms = slot_signature(preserved.group("steps"))
        linked = {term for term in step_terms
                  if any(_token_kin(term, original) for original in base_terms)}
        if len(linked) >= 2:
            return True
    return False

#: Sentence boundaries for marker scope: terminal punctuation followed by
# space, or a line break.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+|\n+")
#: A pronoun subject beside the marker refers back to the record's other
# sentences ("Actually it moved to the cafe.", "they changed it"). A
# demonstrative counts only directly before the marker ("that changed"):
# after it, it is a determiner of a new subject ("all change this
# quarter").
_ANAPHORIC_SUBJECT_TOKENS = frozenset({"it", "they"})
_DEMONSTRATIVE_SUBJECT_TOKENS = frozenset({"this", "that", "these", "those"})


def _marker_sentence_bears_on(loser_body: str, winner_body: str) -> bool:
    """Whether the winner's CORRECTION marker is about the loser's subject.

    A correction corrects what its own sentence talks about. A record that
    narrates several things ("I can imagine how rewarding it must be to
    create a space for growth and change. I just want to share a photo of
    my snake.") carries a marker word in one sentence and the subject it
    shares with a slot-mate in another: the marker is not a correction of
    that slot-mate, and value-empty slot-mates must not be withdrawn by it
    (measured, zero-spend probe replay: "growth and change", "more updates
    to share" withdrew the very records a question asked about).

    The marker reaches the whole record when: the winner retracts (a
    retraction withdraws by reference: "Scratch that."); a marker sentence
    carries no subject content of its own (a bare "Correction:"/"Actually."
    heading the record); a marker sentence shares subject, unit or value
    vocabulary with the loser; or a pronoun subject stands beside the
    marker ("Actually it moved ...") and so refers back."""
    if retraction_marker(winner_body):
        return True
    loser_sig = slot_signature(loser_body)
    loser_values = value_tokens(loser_body)
    loser_units = {t for t in _tokens(loser_body)} & _UNIT_WORDS
    any_marker_sentence = False
    for sentence in _SENTENCE_SPLIT_RE.split(str(winner_body or "")):
        marker = _CORRECTION_MARKER_RE.search(sentence)
        if not marker or _is_hedged(sentence):
            continue
        any_marker_sentence = True
        content = {tok for tok in slot_signature(sentence)
                   if tok not in _REASSIGNMENT_TOKENS
                   and tok not in _LIGHT_VERB_TOKENS
                   and not _CORRECTION_MARKER_RE.fullmatch(tok)}
        if not content:
            return True
        if (content & loser_sig
                or value_tokens(sentence) & loser_values
                or ({t for t in _tokens(sentence)} & _UNIT_WORDS & loser_units)):
            return True
        before = _tokens(sentence[:marker.start()])[-2:]
        after = _tokens(sentence[marker.end():])[:2]
        if (_ANAPHORIC_SUBJECT_TOKENS & set(before + after)
                or (before and before[-1] in _DEMONSTRATIVE_SUBJECT_TOKENS)):
            return True
    # no marker sentence survived the split (a marker spanning a boundary):
    # keep the whole-record reading
    return not any_marker_sentence


def _conflicts_with_winner(loser_body: str, winner_body: str) -> bool:
    # A pure REVERSION winner replaces nothing: the closed exception
    # expires by its own dates and the standing value COEXISTS with the
    # statement that confirms its return (measured: the lane-price pair —
    # the reversion line superseded the very fare it restored).
    if _REVERSION_RE.search(winner_body) and not retraction_marker(
            winner_body) and not _CORRECTION_MARKER_RE.search(
            _strip_reversion(winner_body)):
        return _value_conflict(loser_body, winner_body)
    if _value_conflict(loser_body, winner_body):
        # Same-subject law: recency supersession requires the statements to
        # be about ONE subject; parallel template facts (each side carrying
        # a distinctive subject token) coexist unless the winner corrects —
        # and year-distinct editions of one series are parallel facts by
        # the same law.
        return not (
            _parallel_subject_facts(loser_body, winner_body)
            or _edition_distinct_subjects(loser_body, winner_body)
        )
    # A marker-carrying winner withdraws value-empty slot-mates (a retraction
    # with no replacement, or a polarity flip naming the same value words).
    # A correction that MENTIONS the value it replaces ("now departs at
    # 23:40, the 23:10 slot went to the freight run") supersedes it too:
    # only a pure restatement — identical value sets on both sides —
    # coexists (measured: the recency-sensitive ferry pair both rode).
    if (_RESTORATION_MARKER_RE.search(winner_body)
            and carries_undo_marker(loser_body)
            and value_tokens(winner_body) != value_tokens(loser_body)):
        # A restoration replaces the correction it un-does. Both sides carry
        # markers, so the plain arms never reach this pair, and digit-split
        # values ("55-014" vs "55-041") share fragments that read as
        # coexisting values (measured, fresh cohort F01-04: the dead
        # correction rode alongside its own restoration).
        return True
    if carries_undo_marker(winner_body) and not carries_undo_marker(loser_body):
        winner_values = value_tokens(winner_body)
        loser_values = value_tokens(loser_body)
        if (winner_values and loser_values):
            if (winner_values != loser_values
                    and _PARTITIVE_QUANTIFIER_RE.search(winner_body)):
                # a fractional reassignment narrows, it does not replace
                return False
            return winner_values != loser_values
        if not (winner_values & loser_values):
            if _PARTITIVE_QUANTIFIER_RE.search(winner_body):
                return False
            # value-empty withdrawal: the marker alone decides, so it must
            # be a correction OF this slot-mate's subject
            return _marker_sentence_bears_on(loser_body, winner_body)
    return False



def _slot_has_in_force_member(
    members: list[int],
    normalized: list[TemporalCandidate],
    clock_now: float,
    current_index: int,
) -> bool:
    """Whether ANOTHER member of this slot is (or may already be) in force
    at the ask moment: undated, or carrying no forward-declared effective
    date still ahead of now. Only then may a future-declared member be
    displaced — a slot whose every member is future-declared is a
    scheduled fact, and a present ask about it is answered by that
    record, not starved of it."""
    for i in members:
        if i == current_index:
            continue
        cand = normalized[i]
        year_hint = (
            datetime.fromtimestamp(cand.statement_at, tz=_UTC).year
            if cand.statement_at is not None else None
        )
        declared = declared_effective_date(
            cand.body, year_hint, statement_at=cand.statement_at)
        if declared is None:
            return True
        declared_ts = datetime(
            declared.year, declared.month, declared.day, tzinfo=_UTC
        ).timestamp()
        if declared_ts <= clock_now:
            return True
    return False


def _unique_order_endpoint(
    pool: list[int], normalized: list[TemporalCandidate], *, earliest: bool = False,
) -> int | None:
    """A temporal endpoint exists only when supplied metadata orders its ties."""
    if not pool:
        return None
    choose = min if earliest else max
    winner = choose(pool, key=lambda i: _order_key(normalized[i]))
    tied = [i for i in pool if _order_key(normalized[i])[:3] == _order_key(normalized[winner])[:3]]
    if len(tied) > 1 and (
        any(normalized[i].seq is None for i in tied)
        or sum(normalized[i].seq == normalized[winner].seq for i in tied) > 1
    ):
        return None
    return winner


def _slot_winner_index(pool: list[int], normalized: list[TemporalCandidate]) -> int | None:
    """Newest STATE-dated member wins; write-time-only records never win a
    slot over a dated sibling (the import moment is not the state moment),
    and fully unknown records never win by order at all."""
    dated = [i for i in pool if state_time(normalized[i]) is not None]
    comparable = dated or [i for i in pool if effective_time(normalized[i]) is not None]
    if not comparable:
        return pool[0] if len(pool) == 1 else None
    return _unique_order_endpoint(comparable, normalized)

#: Ack-register vocabulary: carries no subject/predicate content of its
#: own, so it never counts as elaboration when deciding whether an
#: assistant record is a derivative acknowledgment.
_ACK_REGISTER_TOKENS = frozenset({
    "note", "noted", "noting", "log", "logged", "logging", "got", "it",
    "ok", "okay", "sure", "right", "done", "confirmed", "ack", "acknowledged",
    "understood", "roger", "thanks", "thank", "yes", "yeah", "yep",
})


def _demote_assistant_echoes(
    normalized: list[TemporalCandidate],
    verdicts: dict[str, EligibilityVerdict],
) -> None:
    """An assistant echo of a SUPERSEDED value is not state.

    A short acknowledgment ("05:40 interim launch — noted.") often shares too
    little vocabulary to join the slot of the statement it acknowledges, so
    it survives as its own singleton-slot winner and re-serves the dead value.
    An assistant record whose values are entirely covered by a superseded
    record it also shares a subject token with is an echo, not evidence of
    current state. Echoes of the WINNING value are untouched (harmless).
    """
    by_key = {cand.key: cand for cand in normalized}
    superseded = [
        cand for cand in normalized
        if not verdicts.get(cand.key, EligibilityVerdict(cand.key, True, "")).eligible
    ]
    for cand in normalized:
        verdict = verdicts.get(cand.key)
        if verdict is None or not verdict.eligible:
            continue
        if _is_user_record(cand):
            continue
        echo_values = value_tokens(cand.body)
        echo_sig = slot_signature(cand.body)
        for dead in superseded:
            dead_values = value_tokens(dead.body)
            if dead_values and echo_values and not echo_values <= dead_values:
                continue
            # A VALUE-LESS dead statement still has value-less echoes: a
            # restoration withdrew "it runs on the depot time now" and the
            # assistant's "Depot time now, noted." kept serving the dead
            # value as words (exposed corpus F15-09) - an echo whose whole
            # signature is covered by the dead statement's is dead with it.
            if not dead_values and not echo_values:
                if not (echo_sig
                        and echo_sig - _ACK_REGISTER_TOKENS
                        <= slot_signature(dead.body)):
                    continue
            # An echo with no subject content of its own ("40 minutes —
            # logged.") is a pure echo: values alone tie it to the dead
            # record. Otherwise require a shared subject token.
            if not echo_sig or echo_sig & slot_signature(dead.body):
                verdicts[cand.key] = EligibilityVerdict(
                    key=cand.key, eligible=False,
                    reason="echo-of-superseded", slot=verdict.slot,
                    effective_time=verdict.effective_time,
                    superseded_by=dead.key,
                )
                break


def _apply_chain_relevance_bar(
    normalized: list[TemporalCandidate],
    slots: dict[str, list[int]],
    verdicts: dict[str, EligibilityVerdict],
) -> None:
    """Validity is not question relevance: a chain-probed candidate rides
    only when its slot also holds a query-leg candidate (the anchor probe's
    shared vocabulary fetched it to COMPLETE a chain, not because the
    question asked about its subject)."""
    for slot_id, indexes in slots.items():
        if any(normalized[i].origin != "chain" for i in indexes):
            continue
        for i in indexes:
            if normalized[i].origin == "chain":
                verdicts[normalized[i].key] = EligibilityVerdict(
                    key=normalized[i].key, eligible=False,
                    reason="chain-unlinked", slot=slot_id,
                    effective_time=effective_time(normalized[i]),
                )


# ────────────────────────── the selection contract ─────────────────────────

#: A "what was it before <event>" ask wants the superseded value (see the
#: past-only branch; mirrors temporal_question_scope's before-event cue).
_BEFORE_EVENT_PAST_RE = re.compile(
    r"\bwhat\s+was\s+(?:it|that|this|there|the\s+\w+)\s+before\b"
    r"|\bwas\s+(?:it|that|this)\s+(?:like\s+)?before\b"
    r"|\bbefore\s+the\s+\w+\s+"
    r"(?:revision|update|change|correction|amendment|switch|move)\b",
    re.IGNORECASE,
)


def apply_temporal_selection(
    candidates: Sequence[TemporalCandidate | Mapping[str, Any]],
    *,
    intent: AsOfIntent,
    past_only: bool = False,
    asks_assistant_history: bool = False,
    now_utc: datetime | None = None,
    question: str = "",
    count_shaped: bool = False,
    multi_record: bool = False,
    mixed_current_past: bool = False,
) -> dict[str, EligibilityVerdict]:
    """Decide which candidates may be packed for this question.

    Laws:

    * as-of question (``intent.present``): a record is inapplicable when its
      effective time falls after the as-of day's end (future relative to the
      ask) or its declared window closed before the asked day; the latest
      remaining record of each conflicted slot wins, earlier conflicting
      records are superseded, coexisting records stay.
    * current question (no as-of, not a past-event ask): same resolution
      against the whole timeline; a window that closed before the slot's
      newest statement is expired.
    * past-EVENT question (``past_only``, no as-of): every candidate stays
      eligible — old evidence is valid history unless deleted or revoked;
      attribution happens at render time, not here.
    * unknown-time records never filter out and never supersede; ordering
      among them is ingest order (stable), which is statement order for live
      turns.
    * within a slot, the statement of record is the newest USER statement
      when one exists (assistant output is a derivative acknowledgment);
      assistant records only carry slots the user never stated.
    """
    normalized: list[TemporalCandidate] = []
    for cand in candidates:
        if isinstance(cand, TemporalCandidate):
            item = replace(cand)
        else:
            data = dict(cand)
            item = TemporalCandidate(
                key=str(data.get("key") or ""),
                body=str(data.get("body") or ""),
                role=str(data.get("role") or "user"),
                authority=str(data.get("authority") or ""),
                statement_at=data.get("statement_at"),
                event_at=data.get("event_at"),
                recorded_at=data.get("recorded_at"),
                source_kind=str(data.get("source_kind") or ""),
                seq=data.get("seq"),
                has_node=bool(data.get("has_node")),
                origin=str(data.get("origin") or "query-leg"),
                speaker=str(data.get("speaker") or ""),
            )
        normalized.append(item)

    # A stored QUESTION asserts nothing (the product's stored-question law):
    # it can never be a slot's statement of record, never supersede, and
    # never outrank an assistant statement that ANSWERED it. Measured: the
    # asked question collided with its own answer's slot, won it by user
    # authority, and the answer was demoted as derivative (F01-08, F12-13).
    # Questions stay packable through their own seams; they just do not
    # participate in state resolution.
    normalized = [
        item for item in normalized
        if not str(item.body or "").strip().endswith("?")
    ]

    verdicts: dict[str, EligibilityVerdict] = {}

    as_of_end = intent.as_of_end
    as_of_start = (
        datetime(as_of_end.year, as_of_end.month, as_of_end.day, tzinfo=_UTC)
        if as_of_end is not None else None
    )
    slots, direct_ties = _build_slots_with_ties(normalized)

    def _correction_reaches(i: int, winner_index: int | None) -> bool:
        """A correction winner supersedes only the slot members it is
        DIRECTLY tied to by a join law; a non-correction winner keeps the
        slot-wide value laws."""
        if winner_index is None or i == winner_index:
            return True
        if not carries_undo_marker(normalized[winner_index].body):
            return True
        return frozenset((i, winner_index)) in direct_ties

    # Ordinal asks select WHICH occurrence of a repeated subject they want:
    # "when did I first put it on record" keeps the earliest statement of
    # the slot and drops the rest from packing (they stay retained and are
    # the answer to the opposite ordinal).
    # Aggregate/enumeration asks: composition's operand-level supersede
    # (statement-time ordered, slot-bound) is the finer instrument and needs
    # BOTH operand lines; the temporal contract still applies its DATE laws
    # (as-of future/window filters) and never suppresses an operand for
    # current-intent reasons (measured: c-aquarium — the correction that
    # re-mentions the untouched operand suppressed it and starved the sum).
    ordinal = question_ordinal(question)
    if multi_record and not ordinal:
        # A present-intent aggregate ("how many in total?") cannot count what
        # has not happened yet: with no as-of in the question, the boundary is
        # the end of TODAY (now_utc). Measured 2026-09-29 (q90 dev corpus
        # F08-13): a June-25 record packed into a June-15 clock's total
        # (14+9+11=34 derived; the ask's own total is 14+9=23). Point
        # questions keep future-dated records — a scheduled closing date is a
        # legitimate answer — so this law lives only in the aggregate branch.
        boundary_end = (
            as_of_end
            if as_of_end is not None
            else (_end_of_day(now_utc) if now_utc is not None else None)
        )
        for slot_id, indexes in slots.items():
            members = sorted(indexes, key=lambda i: _order_key(normalized[i]))
            interval_history = bool(
                past_only and as_of_start is not None
                and _INTERVAL_EVIDENCE_REQUEST_RE.search(str(question or ""))
            )
            # A readable historical interval must still honor an explicit
            # correction known by the cutoff. Reuse this owner's existing
            # authority/slot/conflict rules; a post-cutoff update cannot
            # withdraw or provide evidence for this dated answer.
            historical_winner = None
            if interval_history:
                known = [i for i in members
                         if state_time(normalized[i]) is None
                         or state_time(normalized[i]) <= as_of_end.timestamp()]
                users = [i for i in known if _is_user_record(normalized[i])]
                historical_winner = _slot_winner_index(users or known, normalized)
            for i in members:
                cand = normalized[i]
                eff = effective_time(cand)
                year_hint = (
                    datetime.fromtimestamp(cand.statement_at, tz=_UTC).year
                    if cand.statement_at is not None else None
                )
                _ws, win_end = effective_window(cand.body, year_hint)
                if boundary_end is not None:
                    state = state_time(cand)
                    if state is not None and state > boundary_end.timestamp():
                        verdicts[cand.key] = EligibilityVerdict(
                            key=cand.key, eligible=False,
                            reason=(
                                "future-relative-to-as-of"
                                if as_of_end is not None
                                else "future-relative-to-now"
                            ),
                            slot=slot_id,
                            effective_time=eff,
                        )
                        continue
                    # A current aggregate's future boundary is not an
                    # explicit historical cutoff. Completed historical
                    # operands do not expire merely because today's clock
                    # is later than their applicability window.
                    if win_end is not None and as_of_start is not None:
                        win_end_dt = datetime.combine(win_end, time.max, tzinfo=_UTC)
                        if win_end_dt < as_of_start:
                            interval_bounds = (
                                _declared_interval_windows(cand.body, year_hint)
                                if interval_history else ()
                            )
                            winner = (normalized[historical_winner]
                                      if historical_winner is not None else None)
                            withdrawn = bool(
                                interval_bounds and winner is not None
                                and i != historical_winner
                                and carries_undo_marker(winner.body)
                                and _correction_reaches(i, historical_winner)
                                and _conflicts_with_winner(cand.body, winner.body)
                            )
                            verdicts[cand.key] = EligibilityVerdict(
                                key=cand.key,
                                eligible=bool(interval_bounds and not withdrawn),
                                reason=("superseded-by-correction" if withdrawn
                                        else "historical-interval-evidence" if interval_bounds
                                        else "window-expired-before-as-of"),
                                slot=slot_id, effective_time=eff,
                                superseded_by=winner.key if withdrawn else None,
                            )
                            continue
                verdicts[cand.key] = EligibilityVerdict(
                    key=cand.key, eligible=True, reason="operand-kept",
                    slot=slot_id, effective_time=eff,
                )
        _apply_chain_relevance_bar(normalized, slots, verdicts)
        return verdicts

    if ordinal:
        for slot_id, indexes in slots.items():
            members = sorted(indexes, key=lambda i: _order_key(normalized[i]))
            user_members = [i for i in members if _is_user_record(normalized[i])]
            authority_pool = user_members or members
            dated = [i for i in authority_pool
                     if effective_time(normalized[i]) is not None]
            pick_from = dated or authority_pool
            chosen = _unique_order_endpoint(pick_from, normalized, earliest=ordinal == "earliest")
            for i in members:
                cand = normalized[i]
                verdicts[cand.key] = EligibilityVerdict(
                    key=cand.key,
                    eligible=(chosen is None or i == chosen),
                    reason=("chronology-undetermined" if chosen is None
                            else f"slot-winner-ordinal-{ordinal}" if i == chosen
                            else "ordinal-not-selected"),
                    slot=slot_id,
                    effective_time=effective_time(cand),
                )
        _apply_chain_relevance_bar(normalized, slots, verdicts)
        return verdicts

    # Past-EVENT asks (no as-of): attributed history, with ONE law kept — a
    # marker correction or retraction is globally true (a recount reveals
    # what the total ALWAYS was; "scratch that" withdraws the commitment for
    # every tense). Date-bound updates do NOT supersede here: "what was the
    # fee before the increase?" needs the earlier value.
    if past_only and not intent.present:
        # A "what was it BEFORE <event>" ask wants precisely the value the
        # event superseded: a rise/reassignment marker must not kill the
        # pre-event reading as a correction would (measured: "It rose to 6
        # guilders in the spring revision" superseded the 4 the ask needed).
        _before_event_ask = bool(_BEFORE_EVENT_PAST_RE.search(str(question or "")))
        for slot_id, indexes in slots.items():
            members = sorted(indexes, key=lambda i: _order_key(normalized[i]))
            user_members = [i for i in members if _is_user_record(normalized[i])]
            authority_pool = user_members or members
            winner = _slot_winner_index(authority_pool, normalized)
            for i in members:
                cand = normalized[i]
                if (winner is not None and i != winner
                        and carries_undo_marker(normalized[winner].body)
                        and not _before_event_ask
                        and _correction_reaches(i, winner)
                        and _conflicts_with_winner(cand.body, normalized[winner].body)):
                    verdicts[cand.key] = EligibilityVerdict(
                        key=cand.key, eligible=False,
                        reason="superseded-by-correction", slot=slot_id,
                        effective_time=effective_time(cand),
                        superseded_by=normalized[winner].key,
                    )
                elif winner is not None and i == winner and carries_undo_marker(
                        normalized[winner].body):
                    # The correcting statement is this slot's statement of
                    # record even for a past-tense ask — it must ride.
                    verdicts[cand.key] = EligibilityVerdict(
                        key=cand.key, eligible=True, reason="slot-winner",
                        slot=slot_id, effective_time=effective_time(cand),
                    )
                else:
                    verdicts[cand.key] = EligibilityVerdict(
                        key=cand.key, eligible=True, reason="historical-attributed",
                        slot=slot_id, effective_time=effective_time(cand),
                    )
        if not asks_assistant_history:
            _demote_assistant_echoes(normalized, verdicts)
        return verdicts

    for slot_id, indexes in slots.items():
        members = sorted(indexes, key=lambda i: _order_key(normalized[i]))
        newest_effective = max(
            (effective_time(normalized[i]) for i in members
             if effective_time(normalized[i]) is not None),
            default=None,
        )

        pool: list[int] = []
        # Current-ask displacement guard: a forward-declared successor
        # ("…rises to N from November") must not win a TODAY ask while an
        # in-force rung exists to answer it — but when EVERY member of the
        # slot is future-dated (a scheduled event: "the show starts on
        # 14 November") the future record IS the present answer, so it is
        # kept (measured, challenge C01 vs the scheduled-event shape).
        clock_now = (now_utc or datetime.now(_UTC)).timestamp()
        for i in members:
            cand = normalized[i]
            eff = effective_time(cand)
            year_hint = (
                datetime.fromtimestamp(cand.statement_at, tz=_UTC).year
                if cand.statement_at is not None else None
            )
            win_start, win_end = effective_window(cand.body, year_hint)
            if as_of_end is not None:
                state = state_time(cand)
                if state is not None and state > as_of_end.timestamp():
                    verdicts[cand.key] = EligibilityVerdict(
                        key=cand.key, eligible=False,
                        reason="future-relative-to-as-of", slot=slot_id,
                        effective_time=eff,
                    )
                    continue
                if win_end is not None:
                    win_end_dt = datetime.combine(win_end, time.max, tzinfo=_UTC)
                    if win_end_dt < as_of_start:
                        verdicts[cand.key] = EligibilityVerdict(
                            key=cand.key, eligible=False,
                            reason="window-expired-before-as-of", slot=slot_id,
                            effective_time=eff,
                        )
                        continue
            else:
                # current-state ask: a rung whose own text FORWARD-DECLARES
                # its effective date ("…rises to N from November") must not
                # win a TODAY ask while an in-force rung exists. Keyed on
                # the DECLARED date, never the statement time — a statement
                # can only be written in the past, and a future-declared
                # CHANGE is the only future a stored record can carry.
                declared = declared_effective_date(
            cand.body, year_hint, statement_at=cand.statement_at)
                if (declared is not None
                        and datetime(
                            declared.year, declared.month, declared.day,
                            tzinfo=_UTC).timestamp() > clock_now
                        and _slot_has_in_force_member(members, normalized,
                                                      clock_now, i)):
                    verdicts[cand.key] = EligibilityVerdict(
                        key=cand.key, eligible=False,
                        reason="future-relative-to-now", slot=slot_id,
                        effective_time=eff,
                    )
                    continue
                if win_end is not None:
                    # current-state ask: a declared window that has closed
                    # is expired — against the slot's newest statement (a
                    # stated reversion) AND against the wall clock itself
                    # ("current" is the one date the clock legitimately
                    # owns; an as-of ask never reaches this branch). A
                    # window that has not opened yet is not the current
                    # value either.
                    win_end_epoch = datetime.combine(
                        win_end, time.max, tzinfo=_UTC
                    ).timestamp()
                    if ((newest_effective is not None
                            and win_end_epoch < newest_effective)
                            or win_end_epoch < clock_now):
                        verdicts[cand.key] = EligibilityVerdict(
                            key=cand.key, eligible=False,
                            reason="window-expired", slot=slot_id,
                            effective_time=eff,
                        )
                        continue
                if win_start is not None and as_of_end is None:
                    win_start_epoch = datetime.combine(
                        win_start, time.min, tzinfo=_UTC
                    ).timestamp()
                    if (win_start_epoch > clock_now
                            and _slot_has_in_force_member(
                                members, normalized, clock_now, i)):
                        verdicts[cand.key] = EligibilityVerdict(
                            key=cand.key, eligible=False,
                            reason="window-not-yet-active", slot=slot_id,
                            effective_time=eff,
                        )
                        continue
            pool.append(i)

        user_pool = [i for i in pool if _is_user_record(normalized[i])]
        authority_pool = (
            user_pool if (user_pool and not asks_assistant_history) else pool
        )
        winner = _slot_winner_index(authority_pool, normalized)
        if ordinal == "earliest" and authority_pool:
            dated_pool = [i for i in authority_pool
                          if effective_time(normalized[i]) is not None]
            winner = (dated_pool or authority_pool)[0]

        for i in pool:
            cand = normalized[i]
            if winner is None or i == winner:
                is_retraction_winner = (
                    i == winner and retraction_marker(cand.body)
                    and not value_tokens(cand.body)
                )
                verdicts[cand.key] = EligibilityVerdict(
                    key=cand.key, eligible=True,
                    reason=("chronology-undetermined" if winner is None
                            else "slot-winner-retracted" if is_retraction_winner
                            else "slot-winner"),
                    slot=slot_id, effective_time=effective_time(cand),
                )
                continue
            chain_withdrawer = _withdrawn_in_chain(i, members, normalized)
            if chain_withdrawer is not None:
                verdicts[cand.key] = EligibilityVerdict(
                    key=cand.key, eligible=False, reason="withdrawn",
                    slot=slot_id, effective_time=effective_time(cand),
                    superseded_by=normalized[chain_withdrawer].key,
                )
                continue
            if _conditional_procedure_preserves_base(
                cand.body, normalized[winner].body, question,
            ):
                # The latest source narrows a condition and explicitly
                # keeps linked earlier steps; it is not a whole-procedure
                # supersession. Retractions were adjudicated above.
                verdicts[cand.key] = EligibilityVerdict(
                    key=cand.key, eligible=True, reason="eligible-coexists",
                    slot=slot_id, effective_time=effective_time(cand),
                )
            elif not _correction_reaches(i, winner):
                # chain-linked only: the correction is about another subject
                verdicts[cand.key] = EligibilityVerdict(
                    key=cand.key, eligible=True, reason="eligible-coexists",
                    slot=slot_id, effective_time=effective_time(cand),
                )
            elif (effective_time(cand) is not None
                    and _conflicts_with_winner(cand.body, normalized[winner].body)):
                # An ASSISTANT restatement whose subject vocabulary is
                # covered by the winner is the same assertion in the
                # assistant's normalized register ("ten in the evening" ->
                # "22:00"), not a competing value: it coexists (the pairing
                # gate at packing adds it only when it carries the
                # normalized form; measured, F04-01). A competing assistant
                # statement with subject content of its own still superseds.
                loser_is_restatement = (
                    not _is_user_record(cand)
                    and (slot_signature(cand.body) - _ACK_REGISTER_TOKENS)
                    <= slot_signature(normalized[winner].body)
                )
                if loser_is_restatement:
                    verdicts[cand.key] = EligibilityVerdict(
                        key=cand.key, eligible=True,
                        reason="eligible-coexists", slot=slot_id,
                        effective_time=effective_time(cand),
                    )
                elif mixed_current_past:
                    # A question asking BOTH the current value and its
                    # history ("what is it now, and what was it before the
                    # revision?") needs the superseded value as attributed
                    # history - the current-half winner still serves, and
                    # the merge's coexist/facet laws deliver the history
                    # half (measured, fresh cohort F15-06).
                    verdicts[cand.key] = EligibilityVerdict(
                        key=cand.key, eligible=True,
                        reason="history-kept-for-mixed-ask",
                        slot=slot_id, effective_time=effective_time(cand),
                        superseded_by=normalized[winner].key,
                    )
                else:
                    verdicts[cand.key] = EligibilityVerdict(
                        key=cand.key, eligible=False, reason="superseded",
                        slot=slot_id, effective_time=effective_time(cand),
                        superseded_by=normalized[winner].key,
                    )
            elif ((_REASSIGNMENT_CONSTRUCTION_RE.search(
                        normalized[winner].body)
                        or _ANAPHORIC_FROM_DATE_RE.search(
                            normalized[winner].body))
                    and value_tokens(cand.body)
                    != value_tokens(normalized[winner].body)):
                # A DIGIT-LESS pair still replaces: "moves to a ten o'clock
                # opening" / "has carried an LED cluster since 2019" carry
                # their values as noun phrases with no numerals, so the
                # value-conflict gate never fired and the replaced state
                # coexisted with its own replacement (challenge N04 family,
                # amendment wording). Within ONE slot an explicit
                # reassignment construction announces replacement; a pure
                # restatement (identical value sets) still coexists.
                verdicts[cand.key] = EligibilityVerdict(
                    key=cand.key, eligible=False,
                    reason="superseded-by-reassignment", slot=slot_id,
                    effective_time=effective_time(cand),
                    superseded_by=normalized[winner].key,
                )
            else:
                verdicts[cand.key] = EligibilityVerdict(
                    key=cand.key, eligible=True, reason="eligible-coexists",
                    slot=slot_id, effective_time=effective_time(cand),
                )

        # Assistant derivatives never carry a slot the user stated themselves
        # (they remain eligible when no user record exists in the pool).
        if user_pool and not asks_assistant_history:
            user_value_union: set[str] = set()
            for i in user_pool:
                user_value_union |= value_tokens(normalized[i].body)
            for i in pool:
                cand = normalized[i]
                if _is_user_record(cand):
                    continue
                existing = verdicts.get(cand.key)
                if existing is not None and not existing.eligible:
                    continue
                # Only ACKNOWLEDGMENTS are derivatives: an assistant record
                # whose values the user records already carry (or that
                # carries none) adds no statement of its own. An assistant
                # ELABORATION that adds values the user never stated ("18/3
                # is the usual atlas weight") is evidence in its own right
                # and coexists (measured: F02-07 — the spec line rode at
                # base and the blanket demotion starved the question).
                assistant_values = value_tokens(cand.body)
                if assistant_values and not assistant_values <= user_value_union:
                    continue
                # A word-form elaboration is the same escape for numerals the
                # user never used: the assistant's normalized restatement
                # ("smoking optional", "8 ml", "22:00" for "ten in the
                # evening") carries subject/predicate vocabulary beyond the
                # user's own words and beyond the ack register (measured,
                # F06-11: "Nectar flow: smoking optional" was demoted as a
                # derivative because value_tokens saw no numerals).
                if not assistant_values:
                    user_word_union: set[str] = set()
                    for j in user_pool:
                        user_word_union |= slot_signature(normalized[j].body)
                    elaboration_words = (
                        slot_signature(cand.body) - user_word_union
                        - _ACK_REGISTER_TOKENS
                    )
                    if elaboration_words:
                        continue
                verdicts[cand.key] = EligibilityVerdict(
                    key=cand.key, eligible=False,
                    reason="assistant-derivative", slot=slot_id,
                    effective_time=effective_time(cand),
                )

        # Byte-identical duplicates collapse to ONE rider: two stored
        # copies of the SAME statement are one fact (retained, countable at
        # the store, never double-packed — measured: the duplicate-seed
        # capsule rode the same inventory line twice). A differently-worded
        # restatement is NOT a duplicate: it is a real second mention
        # (count-shaped questions need every one of them) and coexists by
        # the repeated-value law.
        if winner is not None and not count_shaped:
            winner_body_norm = " ".join(
                str(normalized[winner].body or "").lower().split()
            )
            for i in pool:
                if i == winner:
                    continue
                cand = normalized[i]
                verdict = verdicts.get(cand.key)
                if verdict is None or not verdict.eligible:
                    continue
                if (" ".join(str(cand.body or "").lower().split())
                        == winner_body_norm and winner_body_norm):
                    verdicts[cand.key] = EligibilityVerdict(
                        key=cand.key, eligible=False,
                        reason="duplicate-of-winner", slot=slot_id,
                        effective_time=effective_time(cand),
                        superseded_by=normalized[winner].key,
                    )

    _apply_chain_relevance_bar(normalized, slots, verdicts)
    # Historical assistant output is readable as attributed source even when
    # an assertion it echoed is superseded. Current-state recall still drops
    # echoes; this does not restore a source rejected by its own slot verdict.
    if not asks_assistant_history:
        _demote_assistant_echoes(normalized, verdicts)
    return verdicts
