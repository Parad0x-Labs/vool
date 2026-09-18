"""One canonical time authority for the entire runtime.

Establishes a single narrow clock abstraction that replaces the ~95 ad-hoc
``_utcnow()`` duplicates and ~8 overlapping clock families.

Usage::

    from core.time_authority import CLOCK

    now_utc = CLOCK.now_utc()
    now_local = CLOCK.now_for_timezone("Europe/Berlin")
    elapsed = CLOCK.monotonic_now()

Law:
    - ``now_utc()`` and ``now_for_timezone()`` return aware datetimes.
    - ``now_for_timezone()`` raises ``ZoneInfoNotFoundError`` for unknown zones.
    - ``monotonic_now()`` uses ``time.monotonic()`` — safe for elapsed measurement,
      NOT for persistence or calendar arithmetic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


class TimeAuthority:
    """Single trusted clock source.

    Every new temporal primitive must bind through this class rather than calling
    ``datetime.now(...)`` directly. Existing call sites are grandfathered but
    tracked in ``remaining-direct-clock-callers.tsv``.
    """

    #: The Python stdlib zoneinfo provider this authority delegates to.
    #: Override for testing by swapping the class attribute on the *class*:
    #: ``TimeAuthority.ZONE_INFO = my_fake``.
    ZONE_INFO: type[ZoneInfo] = ZoneInfo

    def now_utc(self) -> datetime:
        """Return the current UTC instant as an aware datetime.

        This is the ONE source for every new UTC timestamp.  No model call, no
        web call, no tool planner loop.
        """
        return datetime.now(timezone.utc)

    def now_for_timezone(self, timezone_name: str) -> datetime:
        """Return the current instant in the given IANA timezone.

        Raises ``ZoneInfoNotFoundError`` if *timezone_name* is not a known IANA
        identifier.  Never silently falls back to UTC or local time.
        """
        tz = self.ZONE_INFO(timezone_name)
        return datetime.now(tz)

    def monotonic_now(self) -> float:
        """Return a monotonic float suitable for measuring elapsed time.

        NOT safe for persistence, calendar arithmetic, or display.  Only use
        for intra-process duration measurement.
        """
        import time

        return time.monotonic()

    def now_info(self, timezone_name: str | None = None) -> dict[str, Any]:
        """Return a structured description of the current instant.

        When *timezone_name* is ``None`` or empty, only UTC fields are
        returned.  When a valid IANA zone is given, the local representation
        is also populated.
        """
        utc_dt = self.now_utc()
        result: dict[str, Any] = {
            "utc_iso": utc_dt.isoformat(),
            "utc_timestamp": utc_dt.timestamp(),
            "source": "time_authority.clock",
        }
        if timezone_name:
            tz = self.ZONE_INFO(timezone_name)
            local_dt = utc_dt.astimezone(tz)
            result["local_iso"] = local_dt.isoformat()
            result["timezone"] = timezone_name
        return result


#: Module-level singleton — the canonical clock for the whole runtime.
CLOCK = TimeAuthority()