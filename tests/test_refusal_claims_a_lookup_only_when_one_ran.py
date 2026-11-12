"""A refusal may say "I looked this up" only when a lookup ran in the turn (not a read of the owner's files)."""
from __future__ import annotations

from core.grounding_lifecycle import STAGE_SUPPORTED, GroundingLifecycle, TurnIdentity
from core.grounding_publication import _NO_LOOKUP_REFUSAL_LEAD, _REFUSAL_LEAD, typed_refusal


def _lifecycle(rows):
    return GroundingLifecycle(
        lifecycle_id="l1", identity=TurnIdentity(session_id="s", turn_id="t"), request_text="what do rooftop bars cost in lima",
        typed_observations=tuple(rows),
    )


def test_a_file_read_of_the_clean_loop_is_not_a_lookup():
    rows = [{"ok": True, "tool_name": "find", "summary": "Downloads/a.txt", "lane": "clean_local_loop"}]
    text = typed_refusal(_lifecycle(rows), failed_stage=STAGE_SUPPORTED)
    assert not text.startswith(_REFUSAL_LEAD)
    assert "looked this up" not in text


def test_a_real_lookup_observation_still_says_so():
    rows = [{"ok": True, "tool_name": "web.search", "summary": "bars list", "lane": "web"}]
    assert typed_refusal(_lifecycle(rows), failed_stage=STAGE_SUPPORTED).startswith(_REFUSAL_LEAD)


def test_no_observation_at_all_says_nothing_was_looked_up():
    assert typed_refusal(_lifecycle([]), failed_stage=STAGE_SUPPORTED).startswith(_NO_LOOKUP_REFUSAL_LEAD)
