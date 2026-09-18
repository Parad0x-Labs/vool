"""Final Repair 4 (Mnemosyne final review, 2026-08-07): a timezone-naive `completed_at` caused
`TypeError: can't subtract offset-naive and offset-aware datetimes` and permanently blocked retry
planning for that whole attempt chain. `_parse_iso` parsed a naive timestamp SUCCESSFULLY (Python's
`datetime.fromisoformat` does not require an offset and raises no error for one) and returned it as
a naive `datetime`; the TypeError happened later, outside any try/except, when the caller subtracted
it from an aware `now`.

Fixed: `_parse_iso` now treats a successfully-parsed-but-NAIVE datetime the same as a missing or
malformed one -- unknown age, never reinterpreted as local machine time, never a crash, never
auto-declared stale.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from core.agent_runtime.attempt_retry import _parse_iso, evaluate_subtask_freshness

_NOW = datetime(2026, 8, 6, 12, 0, 0, tzinfo=timezone.utc)
_NOW_ISO = _NOW.isoformat()
_MARKET_TTL_SECONDS = 15 * 60


class ParseIsoTimestampTests(unittest.TestCase):
    """Pure-function tests against `_parse_iso` directly -- every input shape the mission
    requires, none of which may raise."""

    def test_utc_z_timestamp_parses_as_aware(self) -> None:
        parsed = _parse_iso("2026-08-06T11:50:00Z")
        self.assertIsNotNone(parsed)
        self.assertIsNotNone(parsed.tzinfo)

    def test_explicit_positive_offset_parses_as_aware(self) -> None:
        parsed = _parse_iso("2026-08-06T14:50:00+03:00")
        self.assertIsNotNone(parsed)
        self.assertIsNotNone(parsed.tzinfo)

    def test_explicit_negative_offset_parses_as_aware(self) -> None:
        parsed = _parse_iso("2026-08-06T07:50:00-04:00")
        self.assertIsNotNone(parsed)
        self.assertIsNotNone(parsed.tzinfo)

    def test_naive_timestamp_is_treated_as_unknown_not_reinterpreted(self) -> None:
        """The exact confirmed defect: a naive timestamp parses successfully via
        datetime.fromisoformat but must never be treated as a real, comparable instant."""
        parsed = _parse_iso("2026-08-06T11:50:00")  # no offset, no "Z"
        self.assertIsNone(parsed)

    def test_malformed_timestamp_is_unknown(self) -> None:
        self.assertIsNone(_parse_iso("not-a-timestamp"))
        self.assertIsNone(_parse_iso("2026-13-99T99:99:99"))

    def test_missing_timestamp_is_unknown(self) -> None:
        self.assertIsNone(_parse_iso(""))
        self.assertIsNone(_parse_iso(None))


class EvaluateSubtaskFreshnessTimestampHardeningTests(unittest.TestCase):
    """`evaluate_subtask_freshness` must never raise regardless of `completed_at`'s shape, and
    unknown age must never be auto-declared stale."""

    def _row(self, completed_at) -> dict:
        return {"operation": "market_quote", "completed_at": completed_at}

    def test_naive_completed_at_does_not_crash_and_is_not_stale(self) -> None:
        """The exact confirmed defect, at the call site that used to raise TypeError."""
        try:
            refresh_required, reason = evaluate_subtask_freshness(
                self._row("2026-08-06T11:00:00"), now_iso=_NOW_ISO,
            )
        except TypeError as exc:
            self.fail(f"evaluate_subtask_freshness raised on a naive timestamp: {exc}")
        self.assertFalse(refresh_required)
        self.assertEqual(reason, "")

    def test_malformed_completed_at_does_not_crash_and_is_not_stale(self) -> None:
        refresh_required, _ = evaluate_subtask_freshness(self._row("garbage"), now_iso=_NOW_ISO)
        self.assertFalse(refresh_required)

    def test_missing_completed_at_does_not_crash_and_is_not_stale(self) -> None:
        refresh_required, _ = evaluate_subtask_freshness(self._row(""), now_iso=_NOW_ISO)
        self.assertFalse(refresh_required)

    def test_utc_z_timestamp_is_evaluated_normally(self) -> None:
        stale_completed_at = (_NOW - timedelta(minutes=20)).isoformat().replace("+00:00", "Z")
        refresh_required, reason = evaluate_subtask_freshness(self._row(stale_completed_at), now_iso=_NOW_ISO)
        self.assertTrue(refresh_required)
        self.assertIn("market_quote", reason)

    def test_explicit_offset_timestamp_is_evaluated_normally(self) -> None:
        # 12:00 UTC minus 20 minutes = 11:40 UTC = 14:40 at +03:00 -- still stale (> 15 min TTL).
        offset_completed_at = "2026-08-06T14:40:00+03:00"
        refresh_required, _ = evaluate_subtask_freshness(self._row(offset_completed_at), now_iso=_NOW_ISO)
        self.assertTrue(refresh_required)

    def test_exactly_at_ttl_is_not_yet_stale(self) -> None:
        completed_at = (_NOW - timedelta(seconds=_MARKET_TTL_SECONDS)).isoformat()
        refresh_required, _ = evaluate_subtask_freshness(self._row(completed_at), now_iso=_NOW_ISO)
        self.assertFalse(refresh_required, "exactly at the TTL boundary must not be stale (<=, not <)")

    def test_one_second_past_ttl_is_stale(self) -> None:
        completed_at = (_NOW - timedelta(seconds=_MARKET_TTL_SECONDS + 1)).isoformat()
        refresh_required, _ = evaluate_subtask_freshness(self._row(completed_at), now_iso=_NOW_ISO)
        self.assertTrue(refresh_required)

    def test_future_timestamp_does_not_crash_and_is_not_stale(self) -> None:
        """A completed_at somehow in the future (clock skew, test fixture bug) yields a negative
        age, which is <= any positive TTL -- not stale, and critically, must not crash or be
        treated as a hostile input."""
        future_completed_at = (_NOW + timedelta(hours=1)).isoformat()
        try:
            refresh_required, _ = evaluate_subtask_freshness(self._row(future_completed_at), now_iso=_NOW_ISO)
        except Exception as exc:
            self.fail(f"evaluate_subtask_freshness raised on a future timestamp: {exc}")
        self.assertFalse(refresh_required)


class SabotageNaiveTimestampCrashTests(unittest.TestCase):
    """Sabotage: revert `_parse_iso` to only catch ValueError (the pre-repair shape) -- the naive-
    timestamp test must reproduce the exact TypeError."""

    @staticmethod
    def _sabotaged_parse_iso(value):
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None

    def test_sabotage_reproduces_the_typeerror_on_a_naive_timestamp(self) -> None:
        naive = self._sabotaged_parse_iso("2026-08-06T11:00:00")
        aware_now = self._sabotaged_parse_iso(_NOW_ISO)
        self.assertIsNotNone(naive)
        self.assertIsNone(naive.tzinfo, "sabotage should let the naive datetime through unchanged")
        with self.assertRaises(TypeError):
            _ = (aware_now - naive).total_seconds()

    def test_control_the_real_parse_iso_returns_none_for_the_same_input(self) -> None:
        self.assertIsNone(_parse_iso("2026-08-06T11:00:00"))


if __name__ == "__main__":
    unittest.main()
