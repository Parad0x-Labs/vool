"""Lead-owned pin tests for two store-layer seams surfaced by lane-2 transfer
requests (paired300-memory-repair-20260930).

These are mechanism pins for the store contract, using domains unrelated to
any captured benchmark case:

1. `occurrence_neighbors` under a tied clock (`recorded_at` identical for
   back-to-back writes, e.g. a frozen evaluation clock or a fast live burst):
   neighbors must still order as spoken. The strict `recorded_at > ?` boundary
   used to exclude the same-tick successor entirely, and the `<=` before-arm
   treated same-tick successors as predecessors.
2. `occurrence_search(roles=...)` binds SQL parameters in the order the SQL
   text places them: the MATCH string is the MATCH string and role values are
   role values. The old binding order used the first role value as the FTS
   query.
"""

from __future__ import annotations

import pytest


def _mem(db_path):
    from core.vool_memory import VoolMemory

    db_path.parent.mkdir(parents=True, exist_ok=True)
    return VoolMemory(db_path=db_path)


def _store(mem, chat: str, body: str, role: str = "user", recorded_at=None):
    return mem.occurrence_store(
        chat_scope=chat,
        role=role,
        body=body,
        authority="observed-user-statement" if role == "user" else "assistant-output",
        recorded_at=recorded_at,
    )


TIED = 1_700_000_000.0


def test_tied_clock_after_neighbor_is_the_same_turn_answer(tmp_path):
    mem = _mem(tmp_path / "mem.db")
    try:
        ask = _store(
            mem, "harbor-log", "skipper asked: which crane is due for inspection", "user", TIED
        )
        answer = _store(
            mem,
            "harbor-log",
            "the gantry crane number four is due for inspection in april",
            "assistant",
            TIED,
        )
        after = mem.occurrence_neighbors(ask, before=0, after=1)
        assert [n.occurrence_id for n in after] == [answer.occurrence_id]
    finally:
        mem.close()


def test_tied_clock_before_neighbor_is_not_a_same_turn_successor(tmp_path):
    mem = _mem(tmp_path / "mem.db")
    try:
        earlier = _store(
            mem, "harbor-log", "log entry: the dredge winch was serviced", "user", TIED
        )
        anchor = _store(
            mem, "harbor-log", "skipper asked: which crane is due for inspection", "user", TIED
        )
        _store(
            mem,
            "harbor-log",
            "the gantry crane number four is due for inspection in april",
            "assistant",
            TIED,
        )
        before = mem.occurrence_neighbors(anchor, before=1, after=0)
        # the same-tick ASSISTANT answer speaks after the anchor and must not
        # masquerade as its predecessor
        assert [n.occurrence_id for n in before] == [earlier.occurrence_id]
    finally:
        mem.close()


def test_ordered_clock_neighbors_are_unchanged(tmp_path):
    mem = _mem(tmp_path / "mem.db")
    try:
        first = _store(mem, "harbor-log", "log entry: the dredge winch was serviced", "user", TIED)
        second = _store(
            mem, "harbor-log", "skipper asked: which crane is due for inspection", "user", TIED + 5
        )
        third = _store(
            mem,
            "harbor-log",
            "the gantry crane number four is due for inspection in april",
            "assistant",
            TIED + 9,
        )
        got = mem.occurrence_neighbors(second)
        assert [n.occurrence_id for n in got] == [first.occurrence_id, third.occurrence_id]
    finally:
        mem.close()


def test_search_roles_filter_binds_match_and_roles_correctly(tmp_path):
    mem = _mem(tmp_path / "mem.db")
    try:
        _store(
            mem,
            "greenhouse-notes",
            "the tomato rows got drip irrigation lines this tuesday",
            "user",
        )
        assistant_note = _store(
            mem,
            "greenhouse-notes",
            "the drip irrigation timer should run at dawn for the tomato rows",
            "assistant",
        )
        hits = mem.occurrence_search("drip irrigation tomato", chat_scope="greenhouse-notes")
        assert len(hits) == 2
        assistant_only = mem.occurrence_search(
            "drip irrigation tomato", chat_scope="greenhouse-notes", roles=("assistant",)
        )
        # the MATCH string must still select by content and the role filter
        # must narrow it — the old binding order used the role value as the
        # FTS query and matched nothing
        assert [occ.occurrence_id for occ, _score in assistant_only] == [
            assistant_note.occurrence_id
        ]
    finally:
        mem.close()


def test_search_roles_filter_excludes_role_without_hits(tmp_path):
    mem = _mem(tmp_path / "mem.db")
    try:
        _store(
            mem,
            "greenhouse-notes",
            "the tomato rows got drip irrigation lines this tuesday",
            "user",
        )
        assistant_only = mem.occurrence_search(
            "drip irrigation", chat_scope="greenhouse-notes", roles=("assistant",)
        )
        assert assistant_only == []
    finally:
        mem.close()


@pytest.mark.parametrize("roles", [("user",), ("assistant",), ("user", "assistant")])
def test_search_multi_role_binding_covers_each_placeholder(tmp_path, roles):
    mem = _mem(tmp_path / "mem.db")
    try:
        _store(mem, "toolbench", "the lathe chuck key lives on the pegboard", "user")
        _store(mem, "toolbench", "the lathe chuck key should be oiled monthly", "assistant")
        hits = mem.occurrence_search("lathe chuck", chat_scope="toolbench", roles=roles)
        wanted = set(roles)
        assert hits, "the query must still match with a role filter present"
        assert all(occ.role in wanted for occ, _score in hits)
    finally:
        mem.close()
