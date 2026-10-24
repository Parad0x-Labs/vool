"""Zoned clock views preserve the canonical UTC instant, including DST changes."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfoNotFoundError

import pytest

from core.time_authority import TimeAuthority


@pytest.mark.parametrize("instant, expected", [
    (datetime(2026, 10, 24, 14, tzinfo=timezone.utc), "2026-10-24T17:00:00+03:00"),
    (datetime(2026, 10, 25, 15, tzinfo=timezone.utc), "2026-10-25T17:00:00+02:00"),
])
def test_zoned_now_is_a_view_of_utc(monkeypatch, instant, expected):
    clock = TimeAuthority()
    monkeypatch.setattr(clock, "now_utc", lambda: instant)
    local = clock.now_for_timezone("Europe/Athens")
    assert local.isoformat() == expected
    assert local.astimezone(timezone.utc) == instant
    assert clock.now_info("Europe/Athens")["local_iso"] == expected


def test_unknown_timezone_still_raises():
    with pytest.raises(ZoneInfoNotFoundError):
        TimeAuthority().now_for_timezone("Invalid/Timezone")
