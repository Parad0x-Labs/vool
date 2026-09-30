"""Routing telemetry: owner-local decision log — append, metadata projection, rotate, stats, fail-soft."""
from __future__ import annotations

import hashlib
import json

import pytest

from core import routing_decision_log as rdl
from core import runtime_paths


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    # the shadow store is a path-cached singleton: rebind it to THIS test's home so a
    # decision recorded here never lands in a previous test's store (the PR97 law)
    monkeypatch.setattr(rdl, "_SHADOW_STORE", None)
    runtime_paths.configure_runtime_home(tmp_path / "home")
    yield
    runtime_paths.configure_runtime_home(None)


def test_record_and_read_back() -> None:
    rdl.record_decision(session_id="s1", user_input="check token hunter folder", family="machine_read_fast_path", handled=True)
    rdl.record_decision(session_id="s1", user_input="hello", family="model_lane", handled=False, claims=["folder_search", "machine_specs"], arbiter="picked:folder_search")
    rows = rdl.recent_decisions()
    assert len(rows) == 2
    assert rows[0]["family"] == "machine_read_fast_path" and rows[0]["handled"] is True
    assert rows[1]["claims"] == ["folder_search", "machine_specs"]
    assert rows[1]["arbiter"] == "picked:folder_search"


def test_the_row_is_structured_metadata_never_message_text() -> None:
    """Schema 2 (alert 155's real class): arbitrary user text has no column at all — no
    prefix, no fragment, no digest or length surrogate. The dispatched message reaches
    neither sink as text; the redacted text exists only as the shadow record's digest."""
    sentence = "my voicemail pin is 4-4-9-1 and my password is Swordfish42"
    rdl.record_decision(session_id="s", user_input=sentence, family="f", handled=True)
    stored = rdl.decisions_path().read_text()
    row = rdl.recent_decisions()[0]
    assert sentence not in stored
    assert "Swordfish42" not in stored and "4-4-9-1" not in stored
    assert "message" not in row
    assert "message_digest" not in row and "message_len" not in row
    assert row["record_schema"] == 2
    # The one user-input-derived durable value, stated exactly: the shadow digest of the
    # REDACTED, truncated text (guess-checkable for low-entropy input, not plaintext).
    expected = hashlib.sha256(rdl._clean_message(sentence).encode("utf-8")).hexdigest()
    rows = [json.loads(line) for line in stored.splitlines()]
    assert rows[0]["session_id"].startswith("openclaw:")
    import sqlite3

    with sqlite3.connect(str(rdl.data_path("routing_authority_v2_shadow.sqlite"))) as conn:
        shadow = [
            json.loads(bytes(r[0]))
            for r in conn.execute(
                "SELECT canonical_bytes FROM routing_authority_v2_shadow_records"
                " WHERE record_type = 'RoutingDecisionShadowV2'"
            )
        ]
    assert len(shadow) == 1 and shadow[0]["message_redacted_digest"] == expected
    assert sentence not in json.dumps(shadow)


def test_rotation_keeps_newest_tail() -> None:
    for i in range(60):
        rdl.record_decision(session_id="s", user_input=f"msg {i} " + "pad " * 40, family=f"f{i:02d}", handled=True)
    # force a tiny rotate threshold by monkey-free direct call
    rdl._ROTATE_BYTES, saved = 1, rdl._ROTATE_BYTES
    try:
        rdl.record_decision(session_id="s", user_input="newest", family="f_newest", handled=True)
    finally:
        rdl._ROTATE_BYTES = saved
    rows = rdl.recent_decisions(limit=5000)
    assert rows, "rotation must keep the newest tail"
    assert rows[-1]["family"] == "f_newest"


def test_stats_counts_families_and_ambiguity() -> None:
    rdl.record_decision(session_id="s", user_input="a", family="x", handled=True)
    rdl.record_decision(session_id="s", user_input="b", family="x", handled=True)
    rdl.record_decision(session_id="s", user_input="c", family="model_lane", handled=False, claims=["a", "b"])
    stats = rdl.decision_stats()
    assert stats["total"] == 3 and stats["families"]["x"] == 2 and stats["ambiguous"] == 1


def test_fail_soft_never_raises(monkeypatch) -> None:
    monkeypatch.setattr(rdl, "decisions_path", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    rdl.record_decision(session_id="s", user_input="x", family="f", handled=True)  # must not raise
    assert rdl.recent_decisions() == []
