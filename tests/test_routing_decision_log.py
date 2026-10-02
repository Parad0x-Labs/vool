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


def test_supported_secret_shapes_never_persist_as_the_session_identity() -> None:
    """A credential-shaped handle persists only as its ``openclaw:<digest>`` fold.

    Alert 155's reachability question, answered at the owner: no supported secret class
    (EVM private key, provider API key, recovery phrase) survives into either telemetry
    sink as the session identity, whatever a caller names its chat by. The synthetic
    values below are shape-valid fabrications, never real credentials."""
    synthetic_secrets = [
        "0x" + "a1b2c3d4" * 8,  # EVM private-key shape (0x + 64 hex)
        "sk-proj-9f83kd02lwuxnm27aapos",  # provider API-key shape
        "abandon ability able about above absent absorb abstract absurd abuse",  # phrase shape
    ]
    for handle in synthetic_secrets:
        rdl.record_decision(session_id=handle, user_input="turn", family="f", handled=True)
    stored = rdl.decisions_path().read_text()
    for handle in synthetic_secrets:
        assert handle not in stored, "a secret-shaped handle must never persist verbatim"
    folded_ids = [row["session_id"] for row in rdl.recent_decisions()]
    assert len(folded_ids) == len(synthetic_secrets)
    for row_id in folded_ids:
        assert row_id.startswith("openclaw:") and len(row_id) == len("openclaw:") + 20
        assert row_id == row_id.lower()
    # Distinct handles keep distinct identities, and each folds to exactly the identity
    # the shared authority computes for it (correlation + deletion match on these bytes).
    from core.chat_session_identity import canonical_chat_session_id

    for handle, row_id in zip(synthetic_secrets, folded_ids, strict=True):
        assert row_id == canonical_chat_session_id(handle)


def test_the_canonical_shape_is_exactly_the_digest_namespace() -> None:
    """The only handle text this log persists verbatim is exactly ``openclaw:`` + 20
    lowercase hex — the digest namespace the runtime itself mints (``secrets.token_hex``
    or the fold's SHA-256 truncation). Anything one character off that shape is a
    caller-asserted string, and it is folded, never honoured: this is the validated
    boundary the alert's static path rests on, pinned here so a future widening of the
    canonical shape cannot silently start persisting arbitrary caller text."""
    minted = "openclaw:" + "0123456789abcdef0123"  # the exact runtime-minted shape
    near_misses = [
        "openclaw:" + "0123456789abcdef012",  # 19 hex
        "openclaw:" + "0123456789abcdef01234",  # 21 hex
        "openclaw:" + "0123456789ABCDEF0123",  # uppercase hex
        "OPENCLAW:" + "0123456789abcdef0123",  # uppercase prefix
    ]
    rdl.record_decision(session_id=minted, user_input="turn", family="f", handled=True)
    for handle in near_misses:
        rdl.record_decision(session_id=handle, user_input="turn", family="f", handled=True)
    stored_ids = [row["session_id"] for row in rdl.recent_decisions()]
    assert stored_ids[0] == minted, "the minted digest-namespace id passes through verbatim"
    for handle in near_misses:
        assert handle not in stored_ids, "only the exact canonical shape is ever honoured"
    import secrets as _secrets

    fresh = f"openclaw:{_secrets.token_hex(10)}"
    assert len(fresh) == len(minted)  # the minter and the boundary agree on the shape
