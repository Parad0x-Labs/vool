"""A retry of a refused read works even when nothing has built the capability graph yet.

CI #697 (ff3afc71, shard 1 order): after tests/test_a_skill_match_needs_a_real_ask.py reset the capability graph,
"ok tools are on now, go" and "try again but only jpgs" after a refused "list the pics in my downloads" resolved to
('none', ''): conversation_state._read_only read the graph without building it, so the recorded machine.list_directory
had no capability and counted as not read-only. In the app the same holds for a retry sent as the first turn after a
restart, before anything else built the graph.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def _refused_listing_with_an_unbuilt_graph(tmp_path):
    from core import capability_graph as cg
    from core import conversation_state
    from core import runtime_continuity as rc

    rc.configure_runtime_continuity_db_path(tmp_path / "state.db")
    conversation_state.record_sealed_turn(
        "retry-unbuilt", "list the pics in my downloads",
        {"status": "blocked_by_mode", "success": False, "mode": "tool_failed",
         "tool_receipts": [{"tool_name": "machine.list_directory", "execution": {"ok": False, "status": "blocked_by_mode"}}]},
    )
    cg.reset()
    yield
    cg.reset()
    rc.configure_runtime_continuity_db_path(None)


@pytest.mark.parametrize("asked, change", [("ok tools are on now, go", None), ("try again but only jpgs", "only jpgs")])
def test_a_retry_after_the_graph_was_reset_still_redoes_the_request(asked, change, _refused_listing_with_an_unbuilt_graph):
    from core import conversation_state

    got = conversation_state.resolve("retry-unbuilt", asked)
    if change is None:
        assert (got.kind, got.request) == ("retry", "list the pics in my downloads")
    else:
        assert got.kind == "retry_changed" and change in got.text
