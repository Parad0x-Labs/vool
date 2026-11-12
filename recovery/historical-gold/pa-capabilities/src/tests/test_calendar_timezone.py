"""B7 (PA lane): timezone/DST-correct event math on the calendar parser.

Pure-zone tests — no providers, no network. Europe/Vilnius DST 2026: spring
forward 2026-03-29 03:00 EET → 04:00 EEST; fall back 2026-10-25 04:00 EEST →
03:00 EET. America/New_York 2026: forward 2026-03-08, back 2026-11-01.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from core.operator.calendar import parse_calendar_request, render_ics


def _extract_quoted(text):
    import re

    return [m.group(1).strip() for m in re.finditer(r"""["']([^"']+)["']""", text)]


def _data_path(*parts):
    from pathlib import Path

    return Path("/tmp/nulla-test").joinpath(*parts)


def _parse(text, *, now, preferred=None):
    return parse_calendar_request(
        text,
        extract_quoted_values_fn=_extract_quoted,
        data_path_fn=_data_path,
        now_fn=lambda: now,
        preferred_tz_name=preferred,
    )


def test_user_timezone_overrides_machine_zone() -> None:
    # Machine clock is Vilnius; the user's saved zone is New York. "tomorrow at 9am"
    # must be 9am NEW YORK, and the day boundary must come from New York's today.
    now = datetime(2026, 6, 1, 23, 30, tzinfo=ZoneInfo("Europe/Vilnius"))  # 16:30 NY — still June 1 there
    parsed = _parse('schedule a meeting "sync" tomorrow at 9am', now=now, preferred="America/New_York")
    assert parsed is not None
    assert parsed["timezone"] == "America/New_York" and parsed["tz_source"] == "user_preference"
    start = datetime.fromisoformat(parsed["start_iso"])
    assert start.utcoffset() == ZoneInfo("America/New_York").utcoffset(start)  # June: UTC-4
    assert (start.hour, start.minute) == (9, 0)
    # June 1 23:30 Vilnius is June 1 16:30 NY, so "tomorrow" = June 2 in NY.
    assert start.date().isoformat() == "2026-06-02"
    assert parsed["start_utc_iso"].startswith("2026-06-02T13:00")  # 9am EDT = 13:00Z


def test_explicit_place_wins_over_both_zones() -> None:
    # "9am Berlin" for a user in New York: the event instant is 9am BERLIN; the
    # scope renders it in both zones (B7 acceptance journey).
    now = datetime(2026, 6, 10, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    parsed = _parse('schedule a meeting "launch" tomorrow at 9am Berlin', now=now, preferred="America/New_York")
    assert parsed is not None
    assert parsed["timezone"] == "Europe/Berlin" and parsed["tz_source"] == "explicit"
    assert parsed["start_utc_iso"].startswith("2026-06-11T07:00")  # 9am CEST = 07:00Z
    assert "03:00" in parsed["rendered_home"]  # 07:00Z = 03:00 EDT for the New York user
    assert "Europe/Berlin" in parsed["rendered_local"]


def test_spring_forward_nonexistent_time_is_flagged() -> None:
    # 03:30 on 2026-03-29 does not exist in Vilnius (clocks jump 03:00→04:00).
    now = datetime(2026, 3, 28, 12, 0, tzinfo=ZoneInfo("Europe/Vilnius"))
    parsed = _parse('schedule a meeting "gap" on 2026-03-29 03:30 for 30m', now=now, preferred="Europe/Vilnius")
    assert parsed is not None
    assert "does not exist" in parsed.get("dst_note", "")
    # And the rendered instant is the shifted wall time (04:30 local), not a lie.
    start = datetime.fromisoformat(parsed["start_iso"]).astimezone(ZoneInfo("Europe/Vilnius"))
    assert (start.hour, start.minute) == (4, 30)


def test_fall_back_ambiguous_time_is_flagged_first_occurrence() -> None:
    # 03:30 on 2026-10-25 happens twice in Vilnius (04:00 EEST → 03:00 EET).
    now = datetime(2026, 10, 24, 12, 0, tzinfo=ZoneInfo("Europe/Vilnius"))
    parsed = _parse('schedule a meeting "overlap" on 2026-10-25 03:30 for 30m', now=now, preferred="Europe/Vilnius")
    assert parsed is not None
    assert "occurs twice" in parsed.get("dst_note", "")
    start = datetime.fromisoformat(parsed["start_iso"])
    assert start.utcoffset().total_seconds() == 3 * 3600  # fold=0: the first (EEST, +03:00) pass
    assert parsed["start_utc_iso"].startswith("2026-10-25T00:30")  # 03:30+03:00 = 00:30Z


def test_normal_times_across_dst_boundary_are_exact() -> None:
    # A meeting the evening BEFORE each transition: pinned UTC instants.
    now = datetime(2026, 3, 20, 12, 0, tzinfo=ZoneInfo("Europe/Vilnius"))
    parsed = _parse('schedule a meeting "edge" on 2026-03-29 09:00 for 45m', now=now, preferred="Europe/Vilnius")
    assert parsed is not None and "dst_note" not in parsed  # 09:00 exists and is unique
    assert parsed["start_utc_iso"].startswith("2026-03-29T06:00")  # after jump: EEST +03:00
    assert parsed["end_utc_iso"].startswith("2026-03-29T06:45")

    now = datetime(2026, 10, 20, 12, 0, tzinfo=ZoneInfo("Europe/Vilnius"))
    parsed = _parse('schedule a meeting "edge2" on 2026-10-25 09:00 for 45m', now=now, preferred="Europe/Vilnius")
    assert parsed is not None and "dst_note" not in parsed
    assert parsed["start_utc_iso"].startswith("2026-10-25T07:00")  # after fall-back: EET +02:00


def test_ics_renders_the_exact_resolved_instants() -> None:
    parsed = _parse(
        'schedule a meeting "ics" on 2026-10-25 03:30 for 30m',
        now=datetime(2026, 10, 24, 12, 0, tzinfo=ZoneInfo("Europe/Vilnius")),
        preferred="Europe/Vilnius",
    )
    assert parsed is not None
    ics = render_ics(title="ics", start_iso=parsed["start_iso"], end_iso=parsed["end_iso"],
                     now_fn=lambda: datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc))
    assert "DTSTART:20261025T003000Z" in ics  # the first-pass instant, in UTC


def test_machine_zone_still_used_without_preferences() -> None:
    # Back-compat: no explicit place, no saved zone -> the machine zone interprets.
    now = datetime(2026, 6, 1, 10, 0, tzinfo=ZoneInfo("Europe/Vilnius"))
    parsed = _parse('schedule a meeting "plain" on 2026-06-02 15:30 for 45m', now=now)
    assert parsed is not None
    assert parsed["timezone"] == "Europe/Vilnius" and parsed["tz_source"] == "machine"
    assert parsed["start_utc_iso"].startswith("2026-06-02T12:30")


def test_unknown_place_falls_back_without_lying() -> None:
    now = datetime(2026, 6, 1, 10, 0, tzinfo=ZoneInfo("Europe/Vilnius"))
    parsed = _parse('schedule a meeting "x" tomorrow at 9am in Zzyzx', now=now, preferred="Europe/Vilnius")
    assert parsed is not None
    assert parsed["tz_source"] in {"user_preference", "machine"}  # never a guessed zone
