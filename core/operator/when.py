"""Timezone-aware due-time parsing for the operator scheduling lane.

One deterministic authority that turns "remind me tomorrow at 9am", "2026-09-14 09:30",
"in 20 minutes" and "next monday 8:15 Europe/Athens" into an exact UTC instant, and --
just as important -- says WHY it could not when it cannot. It never guesses: an ambiguous
or nonexistent local time (DST fold/gap), an unknown zone, or a missing time yields a typed
clarification request, not an invented instant.

All clock and zone access is injected, so tests drive fixed instants and the parser stays
pure. The returned payload carries both the UTC instant and the wall-clock it was read as,
so every artifact and receipt can show the operator their own words back.
"""
from __future__ import annotations

import calendar
import contextlib
import math
import re
import unicodedata
from calendar import monthrange
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from core.closing_words import CLOSING_COURTESY_WORDS
from core.time_authority import CLOCK

# Zone words users actually type. An IANA path always contains "/" (no English word does),
# so a slash token anywhere is an unambiguous zone name; the "... time" phrasings cover
# "9:00 Vilnius time". Bare "EST"/"CET" style military-zone abbreviations are ambiguous
# across jurisdictions and deliberately NOT mapped -- a named zone is required for them
# (an honest clarification instead of a silently wrong offset).
_ZONE_RE = re.compile(
    r"\b(?:in|at|to)\s+((?:[A-Za-z_]+(?:/[A-Za-z_\-]+){1,2}))\s+(?:time|timezone|time zone)\b"
    r"|\b((?:[A-Za-z_]+(?:/[A-Za-z_\-]+){1,2}))\s+(?:time|timezone|time zone)\b",
    re.IGNORECASE,
)
_ZONE_SLASH_RE = re.compile(r"\b([A-Za-z][A-Za-z_]*(?:/[A-Za-z0-9_+\-]+){1,2})\b")
_ZONE_ROOTS = frozenset(z.split("/")[0] for z in available_timezones() if "/" in z)
_ISO_DATETIME_RE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})(?:[ T](\d{1,2}):(\d{2}))?\b")
# What may FOLLOW a number that names a time: a day word ("at 9 tomorrow"), a part of the
# day ("at 3 in the afternoon"), the prepositions a time phrase itself uses ("at", "in",
# "on"), "of this/next month", a word that starts the subject or a new clause (to, about,
# for, that, and, so, please) and the clock suffix "o'clock". Anything else -- and that is
# every noun ("the 2nd show", "the 1st aid kit", "at 9 Elm Street", "my 2nd attempt") --
# makes the number a count, a label or an address, never a date or a clock time. No noun
# list can name them all, so the follower decides.
_TIME_END_WORDS = (
    r"(?:today|tonight|tomorrow|yesterday"
    r"|monday|tuesday|wednesday|thursday|friday|saturday|sunday"
    r"|morning|noon|midday|afternoon|evening|night|midnight"
    r"|this\s+(?:morning|afternoon|evening|night|monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"|at|in|on|to|about|for|that|and|so"
    # "at 8 pls", "at 8 thanks": a closing courtesy ends the time phrase, never a noun after it. Mac gate on
    # e36daa6a9: "renew my library books tomorrow evening at 8 pls" was set for 18:00, the bare evening.
    r"|" + "|".join(r"\s+".join(map(re.escape, word.split())) for word in CLOSING_COURTESY_WORDS) +
    r"|o'?clock"
    r"|of\s+(?:this|next)\s+month)\b"
)
# A space-then-word that is not one of those: what a noun after a number looks like.
_FOLLOWED_BY_A_NOUN = r"\s+(?!" + _TIME_END_WORDS + r")\w"
# The word midnight as a clock time, never a label: a noun straight after it ("the midnight
# launch", "a midnight snack") makes it a modifier of that noun, exactly as a noun after a
# number does. Only a midnight a sentence says as a time can end the day it is said with.
_MIDNIGHT_WORD_RE = re.compile(r"\bmidnight\b(?!" + _FOLLOWED_BY_A_NOUN + r")", re.IGNORECASE)
# Straight after a day word that is not the name of an event ("the Friday at 10 workshop"): one fixed-width lookbehind
# per word, since Python's lookbehind takes no alternatives of different lengths.
_AFTER_A_DAY_WORD = "(?:" + "|".join(
    rf"(?<=\b{word}\s)(?<!\bthe\s{word}\s)(?<!\ba\s{word}\s)"
    for word in ("today", "tonight", "tomorrow", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
                 "sunday", "morning", "afternoon", "evening", "night")
) + ")"
_TIME_RE = re.compile(
    r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b|\b(?:at\s+)(\d{1,2}):(\d{2})\b"
    # A bare 24-hour clock ("tomorrow 15:00"), the form this lane's own clarifications suggest.
    r"|(?<![\d:.\-/])\b(\d{1,2}):(\d{2})\b(?![:.]?\d)"
    # A bare hour after "at" ("tomorrow at 9", "the 21st at 11"): no minutes, no am/pm. A decimal
    # is its own alternative below ("at 9.30"), a sentence period ("at 9.") is fine, and a number
    # that is not after "at" is never a clock time ("buy 9 eggs"). A noun after the hour
    # ("I live at 9 Elm Street") is an address, so the FIRST "at 9" is skipped and a later
    # "at 10" in the same sentence is the time.
    r"|\bat\s+(\d{1,2})(?![\d:]|\.\d|\s*(?:am|pm)\b|\s*(?:st|nd|rd|th)\b|" + _FOLLOWED_BY_A_NOUN + r")"
    # A decimal clock after "at" ("at 9.30 tomorrow"): the point is the colon people did not
    # type, with an optional am/pm, and the same not-followed-by-a-noun rule.
    r"|\bat\s+(\d{1,2})\.(\d{2})(?!\d)(?:\s*(am|pm)\b)?(?!" + _FOLLOWED_BY_A_NOUN + r")"
    # A bare hour said straight after the day it falls on ("on saturday at 10 remind me to ...", "tomorrow at 9 water
    # the plants") is the clock time whatever word follows: an address or a count is never said right after a day,
    # and the order that follows starts with a verb no list of end words can hold. Mac gate on 7ce02be05: "and on
    # saturday at 10 remind me to water the balcony plants" was asked "Which time on saturday?". A day said as the
    # name of an event ("the Friday at 10 workshop", "a Monday at 3 call") keeps the noun rule above.
    r"|" + _AFTER_A_DAY_WORD + r"at\s+(\d{1,2})(?![\d:]|\.\d|\s*(?:am|pm)\b|\s*(?:st|nd|rd|th)\b)",
    re.IGNORECASE,
)
_IN_RELATIVE_RE = re.compile(
    r"\bin\s+(\d+(?:\.\d+)?)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?|h|days?|d|weeks?|w)\b",
    re.IGNORECASE,
)
# The same delay said in words, not digits: "in an hour" and "in half an hour" read like
# "in 60 minutes" / "in 30 minutes". Only the hour ("in a week" is a day-word matter). An
# "and a half" after the hour adds half an hour ("in an hour and a half" = 90 minutes) and an
# "and a quarter" adds a quarter ("in an hour and a quarter" = 75 minutes). Both halves at
# once ("in half an hour and a half") name no delay anyone says, so that reading is asked about.
# A part of the hour said before it reads the same way: "in a quarter of an hour" is 15 minutes and "in three
# quarters of an hour" 45 (iMac gate40 on 50f8bed75: "a quarter of an hour from now" was read as "an hour from now",
# with "a quarter of" left in the reminder's text).
_IN_WORDY_RELATIVE_RE = re.compile(
    r"\bin\s+(?:(?P<half>half\s+(?:of\s+)?)|(?P<part>(?:a|one)\s+quarter|three\s+quarters)\s+of\s+)?an?\s+hour\b"
    r"(?:\s+and\s+a\s+(?P<more>half|quarter)\b)?",
    re.IGNORECASE,
)
#: The minutes a part of the hour said before it names.
_HOUR_PARTS = {"quarter": 900.0, "quarters": 2700.0}
# A calendar date in words: "5 November", "5th of Nov", "November 5th", with an optional year.
# The day number must stand alone (not part of a clock time like "18:30" or a decimal), so
# "may" or "march" next to a clock time is never a date.
_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
_MONTH_WORD = r"(?P<{name}>" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\b\.?"
# A number followed by a unit ("march 3 miles", "may 2 people") is a quantity, not a day.
_NOT_A_DAY_UNIT = (
    r"miles?|mi|km|kms|kilomet(?:er|re)s?|meters?|metres?|m|kg|kgs|kilos?|grams?|g|lbs?|pounds?|steps?|laps?"
    r"|reps?|sets?|times|people|persons?|guests?|kids?|items?|pieces?|pcs|percent|%|euros?|dollars?|usd|eur"
    r"|seconds?|secs?|minutes?|mins?|hours?|hrs?|h|days?|weeks?|months?|years?"
)
_DAY_NUMBER = (
    r"(?<![\d:.\-/])(?P<{name}>\d{{1,2}})(?:st|nd|rd|th)?"
    r"(?![\d:]|\.\d|\s*(?:am|pm)\b|\s*(?:" + _NOT_A_DAY_UNIT.replace("{", "{{").replace("}", "}}") + r")(?!\w))"
)
_YEAR = r"(?:,?\s+(?P<{name}>20\d{{2}})\b(?!:))?"
_WORD_DATE_RE = re.compile(
    r"\b" + _DAY_NUMBER.format(name="day_a") + r"\s+(?:of\s+)?" + _MONTH_WORD.format(name="month_a") + _YEAR.format(name="year_a")
    + r"|\b" + _MONTH_WORD.format(name="month_b") + r"\s+(?:the\s+)?" + _DAY_NUMBER.format(name="day_b") + _YEAR.format(name="year_b"),
    re.IGNORECASE,
)
# A month and year with no day ("May 2026") names no single date: asked about, never dropped.
_MONTH_YEAR_RE = re.compile(r"\b" + _MONTH_WORD.format(name="month") + r",?\s+20\d{2}\b(?!:)", re.IGNORECASE)
_DAY_RE = re.compile(
    # "the day after tomorrow" is two days on, never "tomorrow": read as "tomorrow" (gate v3 on
    # 07803d62d) the reminder fell due a day early. The longer phrase starts further left, so a
    # search takes it whole before the "tomorrow" inside it.
    r"\b(day\s+after\s+tomorrow|day\s+before\s+yesterday|yesterday|today|tonight|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday"
    r"|last\s+(?:night|monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b",
    re.IGNORECASE,
)
# A day of the month with no month word ("on the 3rd at 11", "the 21st"). The ordinal is a date
# only when what FOLLOWS it could follow a date: the end of the sentence, punctuation, a clock
# time or "at", a day word, "of this/next month", or a word that starts the subject or a new
# clause. A noun after it ("the 2nd show", "the 1st aid kit", "the 3rd time", "the 4th floor")
# is a count or a label, not a date -- no noun list can name every noun, so the follower
# decides (the same rule the bare hour after "at" follows). A part-of-day noun straight after
# the ordinal counts a thing too ("book the 3rd night", "the 2nd morning of the course"): the
# part word must bring its own preposition to name a time ("the 3rd at night", "the 3rd in
# the evening" stay dates).
_ORDINAL_END_WORDS = (
    r"(?:today|tonight|tomorrow|yesterday"
    r"|monday|tuesday|wednesday|thursday|friday|saturday|sunday"
    r"|in|on|to|about|for|that|and|so|please"
    r"|o'?clock"
    r"|this\s+(?:morning|afternoon|evening|night|monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"|of\s+(?:this|next)\s+month)\b"
)
_DAY_OF_MONTH_RE = re.compile(
    r"\b(?:on\s+)?the\s+(\d{1,2})(?:st|nd|rd|th)\b"
    r"(?=\s*(?:$|[.,;:!?)]|\"|\b(?:at|am|pm)\b"
    r"|\b\d{1,2}\s*(?:am|pm)\b|\b\d{1,2}:\d{2}\b"
    r"|" + _ORDINAL_END_WORDS + r"))",
    re.IGNORECASE,
)
# A part of the day people say instead of a clock time ("tomorrow afternoon", "this evening",
# "remind me tonight"): the hour each reads as. "this evening" and "tonight" mean today.
_PART_SEED_HOURS = {
    "morning": 9, "noon": 12, "midday": 12, "afternoon": 15,
    "evening": 18, "night": 20, "tonight": 20, "midnight": 0,
}
_PART_OF_DAY_RE = re.compile(
    r"\b(tonight|morning|noon|midday|afternoon|evening|midnight|night)\b", re.IGNORECASE,
)
_THIS_PART_RE = re.compile(r"\bthis\s+(morning|afternoon|evening|night)\b", re.IGNORECASE)
# An ordinal straight before a part-of-day word, with nothing between them ("the 3rd night",
# "the 2nd morning"): the two together count a thing, so the word is a label, not a time.
_ORDINAL_BEFORE_PART_RE = re.compile(r"\b(?:the\s+)?\d{1,2}(?:st|nd|rd|th)\s+$")
_WEEKDAY_ORDER = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

MAX_FUTURE_DAYS = 366 * 2

_NO_WHEN_PROBLEM = (
    "I could not read a date or time in that. Try 'tomorrow at 9:00', 'in 20 minutes', or "
    "'2026-09-14 09:30' (you can add a named timezone like Europe/Athens)."
)


@dataclass
class WhenResolution:
    ok: bool
    due_at_utc: str = ""
    due_wall: str = ""
    tz_name: str = ""
    basis: str = ""  # human phrase the instant was read from
    problem: str = ""  # typed clarification request when ok is False
    kind: str = ""  # absolute | relative | wallclock
    details: dict[str, Any] = field(default_factory=dict)


def format_due_time(due_at_utc: str, tz_name: str = "", due_wall: str = "") -> str:
    """Render the stored instant without changing scheduling or inventing a zone."""
    raw = str(due_at_utc or due_wall or "")
    try:
        instant = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    if instant.tzinfo is None:
        return raw
    if tz_name:
        # Preserve the stored offset if a legacy zone cannot be resolved.
        with contextlib.suppress(ZoneInfoNotFoundError, ValueError):
            instant = instant.astimezone(_named_or_offset_zone(tz_name))
    return instant.strftime("%d %b %Y at %H:%M:%S %Z")


def _named_or_offset_zone(tz_name: str) -> tzinfo:
    """The zone a reminder was stored with: a named zone, or a typed offset kept as "UTC+05:30" (`_zone_from_text`),
    which no named zone reads and so was shown in UTC (iMac gate47 on 6ce598256)."""
    offset = re.fullmatch(r"(?:UTC|GMT)([+-])(\d{1,2}):?(\d{2})?", str(tz_name or "").strip(), re.IGNORECASE)
    if offset:
        delta = timedelta(hours=int(offset.group(2)), minutes=int(offset.group(3) or 0))
        return timezone(-delta if offset.group(1) == "-" else delta)
    return ZoneInfo(tz_name)


def when_text_from_iso(value: str) -> str:
    """A typed ISO 8601 date-time ("2026-11-23T15:00", "2026-11-23T15:00:00+02:00", "...Z") as the words
    `parse_when_expression` reads, so a typed due time crosses the same zone, daylight-saving and range checks as a
    spoken one. An offset is kept as "UTC+HH:MM"; no offset is the owner's own wall clock. "" when the value is not
    an ISO date-time with a clock (a bare date names no time to be reminded at)."""
    raw = _iso_zone_words_as_offset(str(value or "").strip())
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        return ""
    if not re.search(r"\d[T ]\d{1,2}:\d{2}", raw):
        return ""
    wall = moment.strftime("%Y-%m-%d %H:%M")
    offset = moment.utcoffset()
    if offset is None:
        return wall
    minutes = int(offset.total_seconds() // 60)
    sign = "-" if minutes < 0 else "+"
    hours, rest = divmod(abs(minutes), 60)
    return f"{wall} UTC{sign}{hours:02d}:{rest:02d}"


_ISO_ZONE_WORD_RE = re.compile(r"\s*\b(?:UTC|GMT)\s*([+-]\d{1,2}(?::?\d{2})?)?\s*$", re.IGNORECASE)


def _iso_zone_words_as_offset(raw: str) -> str:
    """A typed time that names its zone in words ("2026-11-27T16:00 UTC", "2026-11-27 16:00 GMT+2"), as VOOL's own
    reminder list shows times, read as the offset it names. Audit on 02ff11496: a move typed "...16:00 UTC" was
    refused with the raw reader error, which reached the person."""
    match = _ISO_ZONE_WORD_RE.search(raw)
    if not match or not re.search(r"\d[T ]\d{1,2}:\d{2}", raw):
        return raw
    offset = match.group(1) or "+00:00"
    sign, digits = offset[0], offset[1:].replace(":", "")
    hours, minutes = (digits[:-2], digits[-2:]) if len(digits) > 2 else (digits, "00")
    return raw[: match.start()].rstrip() + f"{sign}{int(hours):02d}:{minutes}"


#: English weekday names (and their three-letter forms) a tool call types for the day the person named.
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def typed_weekday(value: str) -> int | None:
    """The weekday (0 = Monday) a tool call typed ("friday", "Fri"), or None for anything else."""
    word = str(value or "").strip().casefold().rstrip(".")
    if len(word) < 3:
        return None
    for index, name in enumerate(_WEEKDAYS):
        if name.startswith(word) or word == name:
            return index
    return None


#: Weekday names in the languages VOOL's own pages speak (core/i18n/catalogs), casefolded, Monday first. A word
#: matches a name it equals or, for a name ending in "-", one it starts with (inflected forms: "penktadienį",
#: "пятницу", "piątku"). Names with no word breaks around them (Chinese, Japanese, Korean, Arabic, Hebrew, Hindi,
#: Vietnamese) are found anywhere in the text. Calendar names from CLDR; a reading of fact, not of phrasing.
_WEEKDAY_WORDS: tuple[tuple[str, ...], ...] = (
    ("monday", "pirmadien-", "montag-", "lundi", "lunes", "lunedì", "lunedi", "segunda-feira", "poniedział-",
     "понедельник-", "понеділ-", "pazartesi-"),
    ("tuesday", "antradien-", "dienstag-", "mardi", "martes", "martedì", "martedi", "terça-feira", "terca-feira",
     "wtorek", "wtorku", "вторник-", "вівтор-", "salı-", "sali"),
    ("wednesday", "trečiadien-", "treciadien-", "mittwoch-", "mercredi", "miércoles", "miercoles", "mercoledì",
     "mercoledi", "quarta-feira", "środa", "środę", "środy", "środzie", "среда", "среду", "среды", "середа", "середу",
     "середи", "çarşamba-", "carsamba-"),
    ("thursday", "ketvirtadien-", "donnerstag-", "jeudi", "jueves", "giovedì", "giovedi", "quinta-feira", "czwartek", "czwartku",
     "четверг-", "четвер", "четверга", "perşembe-", "persembe-"),
    ("friday", "penktadien-", "freitag-", "vendredi", "viernes", "venerdì", "venerdi", "sexta-feira", "piątek",
     "piątku", "пятниц-", "п'ятниц-", "пʼятниц-", "cuma", "cumaya", "cumadan", "cuma'ya"),
    ("saturday", "šeštadien-", "sestadien-", "samstag-", "sonnabend-", "samedi", "sábado", "sabado", "sabato",
     "sobota", "sobotę", "soboty", "sobocie", "суббот-", "субот-", "cumartesi-"),
    ("sunday", "sekmadien-", "sonntag-", "dimanche", "domingo", "domenica", "niedziel-", "воскресень-", "неділ-",
     "pazar", "pazara", "pazardan", "pazar'a"),
)
_WEEKDAY_ANYWHERE: tuple[tuple[str, ...], ...] = (
    ("星期一", "周一", "礼拜一", "月曜", "월요일", "الاثنين", "الإثنين", "יום שני", "सोमवार", "thứ hai"),
    ("星期二", "周二", "礼拜二", "火曜", "화요일", "الثلاثاء", "יום שלישי", "मंगलवार", "thứ ba"),
    ("星期三", "周三", "礼拜三", "水曜", "수요일", "الأربعاء", "الاربعاء", "יום רביעי", "बुधवार", "thứ tư"),
    ("星期四", "周四", "礼拜四", "木曜", "목요일", "الخميس", "יום חמישי", "गुरुवार", "thứ năm"),
    ("星期五", "周五", "礼拜五", "金曜", "금요일", "الجمعة", "יום שישי", "शुक्रवार", "thứ sáu"),
    ("星期六", "周六", "礼拜六", "土曜", "토요일", "السبت", "יום שבת", "शनिवार", "thứ bảy"),
    ("星期日", "星期天", "周日", "礼拜天", "日曜", "일요일", "الأحد", "الاحد", "יום ראשון", "रविवार", "chủ nhật"),
)
_WORD_RE = re.compile(r"[^\W\d_][\w'ʼ-]*", re.UNICODE)


def weekday_named_in(text: str) -> int | None:
    """The one weekday (0 = Monday) the person's words name, in any of VOOL's languages; None for none or several.

    The tool call's own "day" field is the first reading (``typed_weekday``); this is for a call that left it out.
    iMac gate on 88337f6b8 (qwen3:8b): "primink penktadienį 17 val. paimti siuntinį iš paštomato" (Friday) was typed as
    Thursday 26 November with no day, and stored there; "šeštadienį" (Saturday) the run before the same."""
    clean = unicodedata.normalize("NFC", str(text or "")).casefold()
    if not clean:
        return None
    named: set[int] = set()
    words = _WORD_RE.findall(clean)
    for index, names in enumerate(_WEEKDAY_WORDS):
        for word in words:
            if any(word == name or (name.endswith("-") and word.startswith(name[:-1])) for name in names):
                named.add(index)
                break
    for index, names in enumerate(_WEEKDAY_ANYWHERE):
        if any(name in clean for name in names):
            named.add(index)
    return next(iter(named)) if len(named) == 1 else None


def align_to_weekday(iso: str, weekday: int, today: date) -> str:
    """``iso`` moved to the weekday the person named, when its date falls on another day; the clock is kept.

    A small model counts days wrong even with the coming dates listed beside the clock (Mac gate on f3771b6b7 and the
    audit on 02ff11496, a Monday: "this Friday 4pm" was typed as Thursday 26 Nov, "Saturday" as Friday 27). Naming the
    day is easy for it in any language; the date is worked out here. A typed date already on that weekday stands when it
    is today or later (it may be a week later on purpose, "next Friday"); otherwise the first such day after today is
    taken. A date on that weekday that has already gone is the model counting backwards: iMac gate47 on 6ce598256, a
    Friday, "book a haircut in my calendar for Thursday 11am" was typed as Thursday 26 Nov, the day before."""
    raw = _iso_zone_words_as_offset(str(iso or "").strip())
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if not match:
        return iso
    try:
        typed_day = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return iso
    if typed_day.weekday() == weekday and typed_day >= today:
        return iso
    ahead = (weekday - today.weekday()) % 7 or 7
    meant = today + timedelta(days=ahead)
    return meant.isoformat() + raw[match.end():]


def has_passed(resolution: WhenResolution) -> bool:
    """True when a resolved instant is before the instant it was read at.

    "In 0 minutes" resolves to the read instant itself: that is due now, not passed. Only a
    caller that needs a FUTURE instant (a reminder) asks this; a calendar search or move may
    legitimately name a past time, so the parser itself never refuses one.
    """
    if not resolution.ok:
        return False
    try:
        due = datetime.fromisoformat(str(resolution.due_at_utc).replace("Z", "+00:00"))
        read_at = datetime.fromisoformat(str(resolution.details.get("read_at_utc") or ""))
    except ValueError:
        return False
    return due < read_at


_TIME_SHORTHAND = (
    (re.compile(r"\b(?:tmrw|tmr|tmw|tomoz|2moro|2morrow)\b", re.IGNORECASE), "tomorrow"),
    (re.compile(r"\b(?:tonite|2nite)\b", re.IGNORECASE), "tonight"),
    (re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s+in\s+the\s+morning\b", re.IGNORECASE), lambda m: f"{m.group(1)}{':' + m.group(2) if m.group(2) else ''}am"),
    (re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s+in\s+the\s+(?:afternoon|evening)\b", re.IGNORECASE), lambda m: f"{m.group(1)}{':' + m.group(2) if m.group(2) else ''}pm"),
    (re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s+at\s+night\b", re.IGNORECASE), lambda m: f"{m.group(1)}{':' + m.group(2) if m.group(2) else ''}pm"),
)


_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
    "forty-five": 45, "fifty": 50, "sixty": 60, "ninety": 90, "a couple of": 2, "a few": 3,
}
#: A delay said with a number word ("in two days", "in ten minutes") reads like its digits. Read
#: as nothing, "remind me in two days at 7pm" fell due at 7pm TODAY (gate v3 wording, 2026-11-22).
_SPELLED_DELAY_RE = re.compile(
    r"\bin\s+(" + "|".join(sorted((re.escape(word) for word in _NUMBER_WORDS), key=len, reverse=True)) + r")\s+"
    r"(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?)\b",
    re.IGNORECASE,
)


#: A delay said as an amount "from now" ("25 minutes from now", "for two hours from now", "in 10 minutes from now") is the
#: same delay as "in 25 minutes". iMac gate35 on 584863ad5: "set a reminder for 25 minutes from now to call the plumber"
#: was answered "I could not read a date or time in that".
_FROM_NOW_RE = re.compile(
    r"\b(?:(?:in|for|after)\s+)?(\d+(?:\.\d+)?|"
    + "|".join(sorted((re.escape(word) for word in _NUMBER_WORDS), key=len, reverse=True))
    + r"|an?)\s+(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?)\s+from\s+now\b",
    re.IGNORECASE,
)


#: The worded hour delays "from now" ("half an hour from now", "an hour and a half from now") read as the same words
#: after "in", which the parser knows. iMac gate38: "half an hour from now" was read as "half" + "an hour from now" and
#: set for an hour later, with "half" as the reminder's text.
_WORDED_HOUR_FROM_NOW_RE = re.compile(
    r"\b(?:(?:in|for|after)\s+)?((?:half\s+(?:of\s+)?|(?:a|one)\s+quarter\s+of\s+|three\s+quarters\s+of\s+)?"
    r"an?\s+hour(?:\s+and\s+a\s+(?:half|quarter))?)\s+from\s+now\b",
    re.IGNORECASE,
)


def _from_now_as_delay(match: re.Match) -> str:
    amount = " ".join(match.group(1).lower().split())
    number = "1" if amount in {"a", "an"} else str(_NUMBER_WORDS.get(amount, amount))
    return f"in {number} {match.group(2)}"


def normalize_time_words(text: str) -> str:
    """Everyday shorthand for times read as the clock words the parser knows: "tmrw" -> "tomorrow",
    "10 in the morning" -> "10am". 0.7.8 sweep block 3: "remind me tmrw 10am…" was scheduled for
    today and kept "tmrw" in its title; "tomorrow 10 in the morning" was asked back for a time."""
    clean = str(text or "")
    for pattern, replacement in _TIME_SHORTHAND:
        clean = pattern.sub(replacement, clean)
    clean = _WORDED_HOUR_FROM_NOW_RE.sub(lambda m: f"in {' '.join(m.group(1).split())}", clean)
    clean = _FROM_NOW_RE.sub(_from_now_as_delay, clean)
    return _SPELLED_DELAY_RE.sub(lambda m: f"in {_NUMBER_WORDS[' '.join(m.group(1).lower().split())]} {m.group(2)}", clean)


def parse_when_expression(
    text: str,
    *,
    now_fn: Callable[[], datetime] | None = None,
) -> WhenResolution:
    now = (now_fn or CLOCK.now_utc)()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    resolution = _parse_when_expression(text, now_fn=lambda: now)
    if resolution.ok:
        resolution.details = {**resolution.details, "read_at_utc": now.astimezone(timezone.utc).isoformat()}
    return resolution


def _parse_when_expression(
    text: str,
    *,
    now_fn: Callable[[], datetime],
) -> WhenResolution:
    text = str(text or "").strip()
    if not text:
        return WhenResolution(ok=False, problem="When should this happen? Give me a date and time (for example 'tomorrow at 9:00' or '2026-09-14 09:30').")

    now = now_fn()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    host_zone = now.tzinfo

    try:
        tz_name, zone = _resolve_zone(text, host_zone)
    except ValueError as exc:
        return WhenResolution(ok=False, problem=str(exc))

    relative = _IN_RELATIVE_RE.search(text)
    wordy = _IN_WORDY_RELATIVE_RE.search(text)
    if relative or wordy:
        if relative:
            value = float(relative.group(1))
            unit = relative.group(2).lower()
            if unit.startswith(("second", "sec")):
                seconds = value
            elif unit.startswith(("minute", "min")):
                seconds = value * 60
            elif unit.startswith(("hour", "hr", "h")):
                seconds = value * 3600
            elif unit.startswith(("day", "d")):
                seconds = value * 86400
            else:
                seconds = value * 7 * 86400
            basis = relative.group(0)
        else:
            # "in an hour" / "in half an hour": the word form of the same delay reading.
            # "and a half" after the hour adds half an hour ("in an hour and a half" = 90 min),
            # "and a quarter" a quarter ("in an hour and a quarter" = 75 min).
            more = (wordy.group("more") or "").lower()
            part = (wordy.group("part") or "").lower().split()
            if (wordy.group("half") or part) and more:
                return WhenResolution(
                    ok=False, tz_name=tz_name,
                    problem=(
                        f"I could not tell how long \"{wordy.group(0).strip()}\" is. "
                        "Say it in minutes, for example 'in 45 minutes' or 'in 90 minutes'."
                    ),
                )
            seconds = 1800.0 if wordy.group("half") else _HOUR_PARTS[part[-1]] if part else 3600.0
            if more == "half":
                seconds += 1800.0
            elif more == "quarter":
                seconds += 900.0
            basis = wordy.group(0)
        if not math.isfinite(seconds) or seconds > MAX_FUTURE_DAYS * 86400:
            return WhenResolution(ok=False, problem="That is too far in the future to schedule reliably; give me a date within the next two years.")
        if seconds < 0:
            return WhenResolution(ok=False, problem="Give me a delay that is not negative.")
        delta = timedelta(seconds=seconds)
        due = now.astimezone(timezone.utc) + delta
        return WhenResolution(
            ok=True,
            due_at_utc=due.isoformat(),
            due_wall=due.astimezone(zone).isoformat(),
            tz_name=tz_name,
            basis=basis,
            kind="relative",
            details={"relative_seconds": delta.total_seconds()},
        )

    iso = _ISO_DATETIME_RE.search(text)
    if iso:
        year, month, day = int(iso.group(1)), int(iso.group(2)), int(iso.group(3))
        if not iso.group(4):
            time_match = _TIME_RE.search(text)
            if time_match is None:
                return WhenResolution(ok=False, problem="Which time on that date? Include a clock time.")
            try:
                hour, minute = _read_clock(time_match, text)
            except ValueError as exc:
                return WhenResolution(ok=False, problem=str(exc))
        else:
            hour, minute = int(iso.group(4)), int(iso.group(5))
        return _resolve_wall(year, month, day, hour, minute, zone, tz_name, basis=iso.group(0), now=now, kind="absolute")

    word_date = _WORD_DATE_RE.search(text)
    if word_date:
        return _resolve_word_date(word_date, text, zone, tz_name, now=now)
    month_year = _MONTH_YEAR_RE.search(text)
    if month_year:
        return WhenResolution(
            ok=False, tz_name=tz_name,
            problem=f"Which day in {month_year.group(0).strip()}? Give me the date and a clock time (for example '5 {month_year.group(0).strip()} at 9:00').",
        )

    ordinal = _DAY_OF_MONTH_RE.search(text)
    if ordinal:
        return _resolve_day_of_month(ordinal, text, zone, tz_name, now=now)

    day_match = _DAY_RE.search(text)
    time_match = _TIME_RE.search(text)
    part_match = _part_of_day_match(text)
    hour: int | None = None
    minute = 0
    if time_match is not None:
        try:
            hour, minute = _read_clock(time_match, text)
        except ValueError as exc:
            return WhenResolution(ok=False, problem=str(exc), tz_name=tz_name)
    if day_match is not None:
        day_word = day_match.group(1)
        zone_now = now.astimezone(zone)
        target_date = _date_for_day_word(day_word, zone_now)
        if day_word.lower() == "tomorrow" and zone_now.hour < _SMALL_HOURS_END:
            # Said in the small hours, "tomorrow" means the morning still ahead as often as the next calendar date:
            # the person has not slept yet. Read either way, one of them sets the thing a whole day off, so the
            # person is asked, with both dates named (owner decision 2026-11-23, tracker row 105).
            same_day, next_day = zone_now.date(), zone_now.date() + timedelta(days=1)
            return WhenResolution(
                ok=False, tz_name=tz_name,
                problem=(
                    f"It's just after midnight, so \"tomorrow\" could mean {_spoken_date(same_day)} or "
                    f"{_spoken_date(next_day)}. Which day do you mean? Say \"today\" for {_spoken_date(same_day)}, or "
                    f"\"the {_ordinal(next_day.day)}\" for {_spoken_date(next_day)}."
                ),
                details={"ambiguous_dates": [same_day.isoformat(), next_day.isoformat()]},
            )
        if _is_ending_midnight(time_match, part_match) and day_word.lower() in ("today", "tonight"):
            # "at 12 tonight" is the midnight that ends tonight: 00:00 tomorrow, not the
            # already-passed first minute of today.
            target_date = target_date + timedelta(days=1)
        if time_match is None:
            # A past day with no clock time is asked about as a day that has passed, never
            # offered a time on it ("remind me yesterday ..."). With a clock time the parser
            # still reads the past instant; the caller that needs a future one refuses it.
            if day_word.lower() in ("today", "tonight") and _MIDNIGHT_WORD_RE.search(text):
                # "tonight at midnight", "midnight today": the word midnight, in any order and
                # with no "at", names the minute that ENDS the day said, so on today or tonight
                # it reads 00:00 tomorrow, never the already-passed first minute of today. A
                # day the sentence names itself ("tomorrow at midnight") keeps its own day.
                target_date = target_date + timedelta(days=1)
                hour, minute = _PART_SEED_HOURS["midnight"], 0
            elif target_date < zone_now.date():
                return WhenResolution(
                    ok=False, tz_name=tz_name,
                    problem=(
                        f"{day_word.lower()} has already passed. "
                        "Give me a time in the future, for example 'tomorrow at 9:00' or 'in 20 minutes'."
                    ),
                )
            elif part_match is not None:
                hour, minute = _PART_SEED_HOURS[part_match.group(1).lower()], 0
            else:
                return WhenResolution(
                    ok=False, tz_name=tz_name,
                    problem=f"Which time on {day_word}? Give me a clock time too (for example '{day_word} at 9:00').",
                )
        time_basis = (
            time_match.group(0).strip() if time_match is not None
            else str(part_match.group(0)).strip() if part_match is not None
            else ""
        )
        if time_match is None and part_match is not None and part_match.group(1).lower() == day_word.lower():
            time_basis = ""  # "tonight" already names both the day and the part
        return _resolve_wall(
            target_date.year, target_date.month, target_date.day, hour, minute,
            zone, tz_name, basis=f"{day_word} {time_basis}".strip(), now=now, kind="wallclock",
        )
    if hour is not None:
        zone_now = now.astimezone(zone)
        target = zone_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        # "this evening at 7" means today, so it never rolls to tomorrow; a bare clock time does.
        if target <= zone_now and not _says_this_part(text, part_match):
            target = target + timedelta(days=1)
        if _is_ending_midnight(time_match, part_match) and _says_this_part(text, part_match):
            # "at 12 this evening" is the midnight that ends today: 00:00 tomorrow.
            target = target + timedelta(days=1)
        return _resolve_wall(
            target.year, target.month, target.day, hour, minute,
            zone, tz_name, basis=time_match.group(0), now=now, kind="wallclock",
        )
    if part_match is not None:
        midnight_word = _MIDNIGHT_WORD_RE.search(text)
        hour, minute = (
            (_PART_SEED_HOURS["midnight"], 0) if midnight_word
            else (_PART_SEED_HOURS[part_match.group(1).lower()], 0)
        )
        zone_now = now.astimezone(zone)
        target = zone_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        this_part = _THIS_PART_RE.search(text)
        # A midnight word ends the part it is said with, so it is never anchored to the part
        # word's own day ("this evening at midnight" is 00:00 tomorrow, not 18:00 today).
        anchored = (
            not midnight_word
            and this_part is not None
            and this_part.group(1).lower() == part_match.group(1).lower()
        )
        if target <= zone_now and not anchored:
            target = target + timedelta(days=1)
        return _resolve_wall(
            target.year, target.month, target.day, hour, minute,
            zone, tz_name,
            basis=this_part.group(0) if anchored else str(part_match.group(0)).strip(),
            now=now, kind="wallclock",
        )

    return WhenResolution(
        ok=False,
        problem=_NO_WHEN_PROBLEM,
        tz_name=tz_name,
    )


def time_expression_spans(text: str, *, word_dates: bool = True) -> tuple[tuple[int, int], ...]:
    """Where `parse_when_expression` could read a time in `text`: relative delays, ISO dates, dates in words, clock times and day words.

    Recognition only, with no clock. A reader that needs to know WHETHER a request names a time uses this instead of a second
    copy of these patterns. The operator's approval reading does (`core.operator.approval_polarity`): the time a calendar
    request takes is the event's time, and any other time a request names defers the action.

    `word_dates=False` leaves out dates written in words ("May 5"): in "remind me what I told you about the May 5 trip"
    the date names the topic being recalled, not when to remind.
    """
    value = str(text or "")
    patterns = (_IN_RELATIVE_RE, _ISO_DATETIME_RE, _TIME_RE, _DAY_RE) + (
        (_WORD_DATE_RE, _DAY_OF_MONTH_RE) if word_dates else ()
    )
    return tuple(sorted({match.span() for pattern in patterns for match in pattern.finditer(value)}))


def _resolve_word_date(
    match: re.Match, text: str, zone: Any, tz_name: str, *, now: datetime,
) -> WhenResolution:
    """A date written in words. With no year it is the next time that date comes round:
    today or later this year, else next year ("5 March" said in October is 5 March next
    year; the resolved year is in the basis and the wall time, so replies show it). A
    weekday or day word that names a different day than the date is a contradiction to
    ask about, never one to pick from."""
    day = int(match.group("day_a") or match.group("day_b"))
    month = _MONTHS[(match.group("month_a") or match.group("month_b")).lower()]
    year_text = match.group("year_a") or match.group("year_b")
    zone_now = now.astimezone(zone)
    said = match.group(0).strip()
    # A yearless 29 February looks ahead to the next leap year.
    years = (int(year_text),) if year_text else tuple(range(zone_now.year, zone_now.year + 5))
    target = None
    for year in years:
        try:
            candidate = datetime(year, month, day).date()
        except ValueError:
            continue
        if year_text or candidate >= zone_now.date():
            target = candidate
            break
    if target is None:
        return WhenResolution(ok=False, problem=f"{said} is not a real date. Check the day and month and tell me again.", tz_name=tz_name)
    rest = text[:match.start()] + " " + text[match.end():]
    for day_word in _DAY_RE.finditer(rest):
        if _date_for_day_word_on(day_word.group(1), target, zone_now) is False:
            return WhenResolution(
                ok=False, tz_name=tz_name,
                problem=f"{target:%A %d %B %Y} is not {day_word.group(1)}. Which day did you mean?",
            )
    time_match = _TIME_RE.search(rest)
    if time_match is not None:
        try:
            hour, minute = _read_clock(time_match, text)
        except ValueError as exc:
            return WhenResolution(ok=False, problem=str(exc))
        time_basis = time_match.group(0).strip()
    else:
        # A part of the day said with the date ("5 March at noon", "March 5th in the morning")
        # reads as that part's hour, as it does after "the 5th" and after a day word.
        part_match = _part_of_day_match(rest)
        if part_match is None:
            return WhenResolution(ok=False, problem=f"Which time on {said}? Give me a clock time too (for example '{said} at 9:00').", tz_name=tz_name)
        hour, minute = _PART_SEED_HOURS[part_match.group(1).lower()], 0
        time_basis = str(part_match.group(0)).strip()
    return _resolve_wall(
        target.year, target.month, target.day, hour, minute,
        zone, tz_name, basis=f"{target.day} {target:%B %Y} {time_basis}", now=now, kind="absolute",
    )


def _date_for_day_word_on(day_word: str, target: Any, zone_now: datetime) -> bool:
    """Whether a day word ("Thursday", "tomorrow") agrees with a written date."""
    word = day_word.strip().lower()
    if word in _WEEKDAY_ORDER:
        return target.weekday() == _WEEKDAY_ORDER.index(word)
    return _date_for_day_word(word, zone_now) == target


def _resolve_zone(text: str, host_zone: Any) -> tuple[str, Any]:
    match = _ZONE_RE.search(text)
    if match:
        candidate = (match.group(1) or match.group(2) or "").strip()
        try:
            return candidate, ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            raise ValueError(f"I could not recognize timezone {candidate}. Give me a valid named timezone.") from None
    # An IANA path token ("Europe/Athens") with no "time" word after it.
    for slash_match in _ZONE_SLASH_RE.finditer(text):
        candidate = slash_match.group(1)
        try:
            return candidate, ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            if candidate.split("/")[0] in _ZONE_ROOTS:
                raise ValueError(f"I could not recognize timezone {candidate}. Give me a valid named timezone.") from None
    utc = re.search(r"\b(?:UTC|GMT)([+-])(\d{1,2})(?::?(\d{2}))?\b|\b(?:UTC|GMT)\b|(?<=\d)Z\b", text, re.IGNORECASE)
    if utc:
        if utc.group(1):
            hours, minutes = int(utc.group(2)), int(utc.group(3) or 0)
            if hours > 23 or minutes > 59:
                raise ValueError('Give a valid UTC offset, such as UTC+02:00.')
            delta = timedelta(hours=hours, minutes=minutes) * (-1 if utc.group(1) == '-' else 1)
            return utc.group(0), timezone(delta)
        return 'UTC', timezone.utc
    return str(getattr(host_zone, 'key', '') or host_zone), host_zone


def _clock_from_match(match: re.Match) -> tuple[int, int]:
    if match.group(1) is not None:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        meridiem = str(match.group(3) or "").lower()
        if not 1 <= hour <= 12 or not 0 <= minute <= 59:
            raise ValueError("Give me a valid clock time: hours 1–12 with am/pm and minutes 00–59.")
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        return hour, minute
    if match.group(8) is not None or match.group(12) is not None:
        # A bare hour after "at" ("at 9", "at 21"): which side of noon it falls on is decided by
        # a part-of-day word or the defaults in `_bare_hour_wall`, which the caller applies.
        hour = int(match.group(8) if match.group(8) is not None else match.group(12))
        if not 1 <= hour <= 23:
            raise ValueError("Give me a valid clock time: a bare hour after 'at' is 1 to 23, or use am/pm or HH:MM.")
        return hour, 0
    if match.group(9) is not None:
        # A decimal clock after "at" ("at 9.30"): the point is the colon people did not type.
        hour, minute = int(match.group(9)), int(match.group(10))
        meridiem = str(match.group(11) or "").lower()
        if not 1 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("Give me a valid clock time: hours 00-23 and minutes 00-59.")
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        return hour, minute
    if match.group(4) is not None:
        hour, minute = int(match.group(4)), int(match.group(5))
    else:
        hour, minute = int(match.group(6)), int(match.group(7))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("Give me a valid clock time: hours 00–23 and minutes 00–59.")
    return hour, minute


def _part_of_day_match(text: str) -> re.Match | None:
    """The first part-of-day word that names a time, skipping greetings ("good morning") and
    labels counted by an ordinal ("the 3rd night", "the 2nd morning"): an ordinal straight
    before the word counts a thing, so that word never names the part of the day a time
    lands in."""
    value = str(text or "")
    for match in _PART_OF_DAY_RE.finditer(value):
        before = value[max(0, match.start() - 12):match.start()].lower()
        if before.endswith(("good ", "hi ", "hello ", "top of the ")):
            continue
        if _ORDINAL_BEFORE_PART_RE.search(before):
            continue
        return match
    return None


def _says_this_part(text: str, part_match: re.Match | None) -> bool:
    """Whether the part of day is anchored to today by "this" ("this evening at 7" is today)."""
    if part_match is None:
        return False
    this_part = _THIS_PART_RE.search(str(text or ""))
    return this_part is not None and this_part.group(1).lower() == part_match.group(1).lower()


def _read_clock(time_match: re.Match, text: str) -> tuple[int, int]:
    """A clock match read against its whole sentence, so a bare hour after "at" lands on the
    side of noon the sentence names ("at 9 tonight" is 21:00)."""
    hour, minute = _clock_from_match(time_match)
    bare_after_at = time_match.group(8) is not None or time_match.group(12) is not None or (
        time_match.group(9) is not None and time_match.group(11) is None
    )
    if bare_after_at:
        part = _part_of_day_match(text)
        hour = _bare_hour_wall(hour, part.group(1).lower() if part is not None else None)
        return hour, minute
    # An H:MM clock with no am/pm and no leading zero ("tonight at 11:30", "this evening 8:15") is the 12-hour clock
    # people say: the part of the day names its side of noon, as for a bare hour. It read as the morning, so
    # "tonight at 11:30" said in the evening was set for 11:30 that morning, already past. "07:30" and "19:30" are the
    # 24-hour clock and stand as written; with no part word "at 9:30" stands as written too.
    hour_text = time_match.group(4) if time_match.group(4) is not None else time_match.group(6)
    if hour_text is not None and not hour_text.startswith("0") and 1 <= hour <= 11:
        part = _part_of_day_match(text)
        if part is not None and part.group(1).lower() in ("afternoon", "evening", "night", "tonight"):
            hour += 12
    return hour, minute


def _is_ending_midnight(time_match: re.Match | None, part_match: re.Match | None) -> bool:
    """A clock time that IS midnight ("at 12 tonight", "12am", "at 12.00", "00:00") names the
    midnight that ENDS the day it is said with, not that day's first minute: on today that
    reads 00:00 tomorrow, an instant still ahead, where 00:00 today has already passed. A
    bare 12 needs an evening part word to say which side of noon it is; "12am" and "00:00"
    say it themselves."""
    if time_match is None:
        return False
    hour_text = next((time_match.group(n) for n in (8, 12, 9) if time_match.group(n) is not None), None)
    if hour_text is not None:
        if int(hour_text) != 12:
            return False
        if time_match.group(9) is not None and time_match.group(11) is not None:
            # "at 12.00 am" is anchored to a side of noon by its own am; "at 12.00 pm" is noon.
            return str(time_match.group(11)).lower() == "am"
        return part_match is not None and part_match.group(1).lower() in ("evening", "night", "tonight", "midnight")
    if time_match.group(1) is not None:
        # "12am" in any minute form is midnight itself; "12pm" is noon and never this.
        return int(time_match.group(1)) == 12 and str(time_match.group(3) or "").lower() == "am"
    hour_g = time_match.group(4) if time_match.group(4) is not None else time_match.group(6)
    minute_g = time_match.group(5) if time_match.group(4) is not None else time_match.group(7)
    return hour_g is not None and int(hour_g) == 0 and int(minute_g or 0) == 0


def _bare_hour_wall(hour: int, part_word: str | None) -> int:
    """A bare hour after "at" on the 24-hour clock. 13 to 23 is the 24-hour clock. For 1 to 12
    a part-of-day word decides; with none, 7 to 11 is the morning, 12 is noon, and 1 to 6 is
    the afternoon or evening (13:00 to 18:00)."""
    if hour >= 13:
        return hour
    if part_word is None:
        return hour if hour >= 7 else hour + 12
    if part_word == "morning":
        return hour
    if part_word in ("afternoon", "evening", "night", "tonight"):
        return 0 if hour == 12 else hour + 12
    if part_word == "midnight":
        return 0
    return hour  # noon or midday: the hour stands


def _resolve_day_of_month(
    match: re.Match, text: str, zone: Any, tz_name: str, *, now: datetime,
) -> WhenResolution:
    """A day of the month with no month word ("on the 3rd", "the 21st at 11"): the next time
    that day comes round, this month if the time is still ahead, else next month -- unless the
    sentence names the month ("the 30th of next month"): that month is the one, and a "this
    month" day already read stays in the past for `has_passed` to refuse. A month without that
    day (the 31st in November) is asked about, never moved to another day."""
    day = int(match.group(1))
    if not 1 <= day <= 31:
        return WhenResolution(
            ok=False, tz_name=tz_name,
            problem=_NO_WHEN_PROBLEM,
        )
    said = re.sub(r"^on\s+", "", match.group(0).strip(), flags=re.IGNORECASE)
    zone_now = now.astimezone(zone)
    rest = text[:match.start()] + " " + text[match.end():]
    # "the 30th of next month" names WHICH month, so the day is not rolled to the next time it
    # comes round: "next" is the month after this one, and "this" is this one even when that day
    # has already passed (a caller that needs a future instant refuses it, as for "yesterday").
    month_of = re.match(r"\s*of\s+(this|next)\s+month\b", text[match.end():], re.IGNORECASE)
    if month_of:
        offsets = (0,) if month_of.group(1).lower() == "this" else (1,)
    else:
        offsets = (0, 1)
    time_match = _TIME_RE.search(text)
    if time_match is not None:
        try:
            hour, minute = _read_clock(time_match, text)
        except ValueError as exc:
            return WhenResolution(ok=False, problem=str(exc), tz_name=tz_name)
        time_basis = time_match.group(0).strip()
    else:
        part_match = _part_of_day_match(text)
        if part_match is None:
            return WhenResolution(
                ok=False, tz_name=tz_name,
                problem=f"Which time on {said}? Give me a clock time too (for example '{said} at 9:00').",
            )
        hour, minute = _PART_SEED_HOURS[part_match.group(1).lower()], 0
        time_basis = str(part_match.group(0)).strip()
    for month_offset in offsets:
        year = zone_now.year + (zone_now.month - 1 + month_offset) // 12
        month = (zone_now.month - 1 + month_offset) % 12 + 1
        month_name = calendar.month_name[month]
        if day > monthrange(year, month)[1]:
            return WhenResolution(
                ok=False, tz_name=tz_name,
                problem=f"{month_name} has no day {day}. Tell me the date you mean, with the day, the month and a clock time.",
            )
        candidate = zone_now.replace(year=year, month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0)
        if month_offset == 0 and candidate <= zone_now and month_of is None:
            continue  # that day already came and went this month: the next one it comes round
        for day_word in _DAY_RE.finditer(rest):
            if _date_for_day_word_on(day_word.group(1), candidate.date(), zone_now) is False:
                return WhenResolution(
                    ok=False, tz_name=tz_name,
                    problem=f"{candidate:%A %d %B %Y} is not {day_word.group(1)}. Which day did you mean?",
                )
        return _resolve_wall(
            candidate.year, candidate.month, candidate.day, hour, minute,
            zone, tz_name, basis=f"{candidate.day} {candidate:%B %Y} {time_basis}", now=now, kind="absolute",
        )
    return WhenResolution(ok=False, tz_name=tz_name, problem=_NO_WHEN_PROBLEM)


#: Before this hour, a "tomorrow" is asked about (see `_parse_when_expression`).
_SMALL_HOURS_END = 4


def _spoken_date(day: Any) -> str:
    """'Tuesday 24 November': the weekday and the date, as the question names them."""
    return f"{day.strftime('%A')} {day.day} {day.strftime('%B')}"


def _ordinal(day: int) -> str:
    suffix = "th" if 10 <= day % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def _date_for_day_word(day_word: str, zone_now: datetime) -> datetime:
    word = " ".join(day_word.strip().lower().split())
    if word == "day after tomorrow":
        return zone_now.date() + timedelta(days=2)
    if word == "day before yesterday":
        return zone_now.date() - timedelta(days=2)
    if word in ("today", "tonight"):
        return zone_now.date()
    if word == "tomorrow":
        return zone_now.date() + timedelta(days=1)
    if word == "yesterday":
        # Read as said: a past day stays in the past, and a caller that needs a future
        # instant refuses it (see `has_passed`), instead of rolling it into the future.
        return zone_now.date() - timedelta(days=1)
    if word.startswith("last "):
        # "last night" and "last friday" name a day that has already happened, exactly like
        # "yesterday": read them as said (the night before, the previous weekday) and let the
        # past-day reply and `has_passed` see the past, never a rolled-forward day.
        named = word.split(None, 1)[1]
        if named == "night":
            return zone_now.date() - timedelta(days=1)
        back = (zone_now.weekday() - _WEEKDAY_ORDER.index(named)) % 7 or 7
        return zone_now.date() - timedelta(days=back)
    target_weekday = _WEEKDAY_ORDER.index(word)
    days_ahead = (target_weekday - zone_now.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7  # "next monday" said on a monday means the NEXT one
    return zone_now.date() + timedelta(days=days_ahead)


def _resolve_wall(
    year: int, month: int, day: int, hour: int, minute: int,
    zone: Any, tz_name: str, *, basis: str, now: datetime, kind: str,
) -> WhenResolution:
    try:
        naive = datetime(year, month, day, hour, minute)
    except ValueError as exc:
        return WhenResolution(ok=False, problem=f"That date does not exist ({exc}). Check the day and month and tell me again.")
    try:
        aware = naive.replace(tzinfo=zone)
        # Detect DST gap: the round-trip through UTC shifts the wall clock.
        roundtrip = aware.astimezone(timezone.utc).astimezone(zone)
    except (OverflowError, OSError, ValueError) as exc:
        return WhenResolution(ok=False, problem=f"That local time is not representable in {tz_name or 'your timezone'} ({exc}). Pick a time on either side of the change.")
    if roundtrip.replace(tzinfo=None) != naive:
        return WhenResolution(
            ok=False,
            problem=(
                f"{naive.strftime('%Y-%m-%d %H:%M')} does not exist in {tz_name or 'the host timezone'} -- "
                "the clocks jump across it (daylight-saving change). Tell me a time that exists, for example 30 minutes later."
            ),
            tz_name=tz_name,
        )
    # Both UTC instants are valid: the operator must choose, not the timezone default.
    try:
        second = naive.replace(tzinfo=zone, fold=1).astimezone(timezone.utc)
        if second != aware.astimezone(timezone.utc):
            return WhenResolution(
                ok=False, tz_name=tz_name, details={"dst": "ambiguous"},
                problem=f"{naive:%Y-%m-%d %H:%M} occurs twice in {tz_name or 'your timezone'}. "
                f"Choose an unambiguous time or give the intended UTC time ({aware.astimezone(timezone.utc):%H:%M} or {second:%H:%M}).",
            )
    except (OverflowError, OSError, ValueError):
        pass
    if aware.astimezone(timezone.utc) - now.astimezone(timezone.utc) > timedelta(days=MAX_FUTURE_DAYS):
        return WhenResolution(ok=False, problem="That is too far in the future to schedule reliably; give me a date within the next two years.")
    return WhenResolution(
        ok=True,
        due_at_utc=aware.astimezone(timezone.utc).isoformat(),
        due_wall=aware.isoformat(),
        tz_name=tz_name,
        basis=basis,
        kind=kind,
    )
