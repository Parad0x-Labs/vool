"""Diagnostic failures must not retain the raw request they were meant to redact."""
import logging
from types import SimpleNamespace

from core import routing_decision_log as rdl
from core import runtime_paths, secret_redaction
from core.agent_runtime import answer_coverage, demand_ownership


def test_redactor_failure_does_not_persist_the_unredacted_message(tmp_path, monkeypatch):
    runtime_paths.configure_runtime_home(tmp_path / "home")
    try:
        def broken(_text):
            raise RuntimeError("synthetic redactor failure")
        monkeypatch.setattr(secret_redaction, "redact_secrets", broken)
        rdl.record_decision(session_id="s", user_input="private-marker", family="fixture", handled=True)
        rows = rdl.recent_decisions()
        assert rows and rows[0]["handled"] is True
        assert "private-marker" not in rdl.decisions_path().read_text()
    finally:
        runtime_paths.configure_runtime_home(None)


def test_redaction_happens_before_truncation_can_split_a_credential():
    value = "p " * 95 + "sk-or-v1-" + "z" * 48
    assert "sk-or-v1-" not in rdl._clean_message(value)
    assert len(rdl._clean_message(value)) <= 200


def test_shadow_disagreement_retains_counts_not_request_content(monkeypatch, caplog):
    monkeypatch.setattr(answer_coverage, "interpret_request", lambda _text: SimpleNamespace(requests=(), units=("fragment",)))
    monkeypatch.setattr(demand_ownership, "_legacy_execution_unit_spans", lambda *_args: ("fragment",))
    original = list(demand_ownership.SHADOW_DISAGREEMENTS)
    try:
        with caplog.at_level(logging.DEBUG, logger="vool.interpretation"):
            demand_ownership.execution_unit_spans("private-marker")
        row = demand_ownership.SHADOW_DISAGREEMENTS[-1]
        assert row["interpretation"] == 0 and row["legacy_over_fragments"] == 1
        assert "private-marker" not in str(row)
        assert "private-marker" not in caplog.text
    finally:
        demand_ownership.SHADOW_DISAGREEMENTS.clear()
        demand_ownership.SHADOW_DISAGREEMENTS.extend(original)


def test_a_recovery_phrase_typed_in_chat_never_reaches_the_decision_log(tmp_path):
    """Alert 155's actual secret-bearing flow: an UNLABELLED BIP-39 phrase in a dispatched
    message must not persist, in whole or in part, to routing_decisions.jsonl."""
    from core.wallet.mnemonic import generate_mnemonic

    runtime_paths.configure_runtime_home(tmp_path / "home")
    try:
        phrase = generate_mnemonic(strength_bits=128)
        rdl.record_decision(
            session_id="s",
            user_input=f"i wrote this down somewhere: {phrase} -- is that safe?",
            family="fixture",
            handled=True,
        )
        import re as _re

        stored = rdl.decisions_path().read_text()
        rows = rdl.recent_decisions()
        assert rows and rows[-1]["family"] == "fixture"
        assert phrase not in stored
        # check the row's message VALUE, not the raw JSON line: keys like "message" and
        # "family" are themselves BIP-39 words and must not be mistaken for phrase content
        message_value = str(rows[-1]["message"])
        assert "[redacted-mnemonic]" in message_value
        for word in phrase.split():
            assert not _re.search(rf"\b{_re.escape(word)}\b", message_value), word
    finally:
        runtime_paths.configure_runtime_home(None)


def test_a_broken_install_fails_the_row_closed_instead_of_storing_a_phrase(tmp_path, monkeypatch):
    """The wordlist ships as package data; if an install is broken and detection cannot arm,
    the routing row must refuse to persist message text at all — 'redacted' must never be a
    plaintext success for a shape the log cannot see."""
    import core.secret_redaction as sr
    from core.wallet.mnemonic import generate_mnemonic

    def broken_read():
        raise OSError("wordlist absent (broken install)")

    monkeypatch.setattr(sr, "_read_bip39_wordlist", broken_read)
    monkeypatch.setattr(sr, "_BIP39_INDEX", None)
    runtime_paths.configure_runtime_home(tmp_path / "home")
    assert sr.mnemonic_redaction_available() is False  # the outage is active for this row
    try:
        phrase = generate_mnemonic(strength_bits=128)
        rdl.record_decision(session_id="s", user_input=f"i saved this: {phrase} ok", family="fixture", handled=True)
        import json as _json

        stored = rdl.decisions_path().read_text()
        # the row's message VALUE is the refusal (checked on the parsed row: JSON keys like
        # "message" are themselves BIP-39 words and must not be mistaken for phrase content)
        row = _json.loads(stored.strip().splitlines()[-1])
        assert row["message"] == "[message unavailable: secret-shape protection unavailable]"
        assert phrase not in stored  # and the phrase, as a whole, is nowhere in the file
    finally:
        runtime_paths.configure_runtime_home(None)
        monkeypatch.setattr(sr, "_BIP39_INDEX", None)


def test_a_secret_bearing_session_handle_persists_only_as_its_folded_identity(tmp_path, monkeypatch):
    """Alert 155's identifier residual: the message column was redacted, but the session id
    was persisted exactly as the caller provided it — so a client that names its chat by
    secret-bearing content (here: a synthetic checksum-valid recovery phrase as the handle)
    wrote that text into BOTH durable sinks: routing_decisions.jsonl and the append-only
    routing_authority_v2_shadow.sqlite canonical bytes. The owner now folds every handle
    through the one identity authority, so a secret-bearing handle persists nowhere as
    plaintext while a canonical id still passes through byte-identical."""
    import json as _json
    import sqlite3 as _sqlite

    from core.chat_session_identity import canonical_chat_session_id
    from core.wallet.mnemonic import generate_mnemonic

    runtime_paths.configure_runtime_home(tmp_path / "home")
    # the shadow store is a path-cached singleton: rebind it to THIS test's home
    monkeypatch.setattr(rdl, "_SHADOW_STORE", None)
    try:
        phrase = generate_mnemonic(strength_bits=128)
        canonical = "openclaw:0123456789abcdef0123"
        rdl.record_decision(session_id=phrase, user_input="what time is it", family="model_lane", handled=False)
        rdl.record_decision(session_id=canonical, user_input="third turn", family="model_lane", handled=False)

        rows = rdl.recent_decisions()
        stored = rdl.decisions_path().read_text()
        folded = canonical_chat_session_id(phrase)

        # neither durable sink carries the plaintext handle, and both carry the folded identity
        assert phrase not in stored
        assert rows[0]["session_id"] == folded and folded.startswith("openclaw:")
        conn = _sqlite.connect(str(rdl.data_path("routing_authority_v2_shadow.sqlite")))
        try:
            shadow = [
                _json.loads(bytes(r[0]))
                for r in conn.execute(
                    "SELECT canonical_bytes FROM routing_authority_v2_shadow_records"
                    " WHERE record_type = 'RoutingDecisionShadowV2'"
                )
            ]
        finally:
            conn.close()
        assert phrase not in _json.dumps(shadow)
        assert shadow and shadow[0]["session_ref"] == folded
        # the served path is unchanged: a canonical id is its own identity, not re-hashed
        assert rows[1]["session_id"] == canonical
        assert shadow[1]["session_ref"] == canonical
    finally:
        runtime_paths.configure_runtime_home(None)


def test_an_evm_private_key_typed_in_chat_never_reaches_either_sink(tmp_path, monkeypatch):
    """Alert 155's EVM residual: the wallet's BACKUP_FORMAT_EVM (0x + 64 hex) shares its shape
    with a public tx hash, so no earlier rule masked it — the key persisted verbatim to
    routing_decisions.jsonl (the shadow already stored only a digest). The redactor now masks
    unregistered runs of that shape in BOTH prefix spellings the wallet owner accepts
    (pilot_custody decodes a backup by text[:2].lower(), so 0X… is the same key); the
    surrounding prose the diagnostic exists for survives."""
    import json as _json
    import sqlite3 as _sqlite

    # Restore the exact override that was active before: the pin below must not discard an
    # override another test armed (the PR97 law) — including on an assertion failure.
    prior_override = runtime_paths._VOOL_HOME_OVERRIDE
    runtime_paths.configure_runtime_home(tmp_path / "home")
    try:
        monkeypatch.setattr(rdl, "_SHADOW_STORE", None)
        body = "4c0883a694529ec3b3d6d5f0a2e7d9b41c2f8a6d3e5c7b9a1f4d2e8c6b0a3d5f"
        keys = ("0x" + body, "0X" + body, "0X" + body.upper())
        for key in keys:
            rdl.record_decision(
                session_id="s",
                user_input=f"is this my evm key {key} or did i copy the tx hash",
                family="fixture",
                handled=True,
            )
        rows = rdl.recent_decisions()
        stored = rdl.decisions_path().read_text()
        for key in keys:
            assert key not in stored, key[:6]  # each typed spelling, as typed
        assert "[redacted-key]" in str(rows[-1]["message"])
        assert "is this my evm key" in str(rows[-1]["message"])  # the diagnostic keeps its prose
        conn = _sqlite.connect(str(rdl.data_path("routing_authority_v2_shadow.sqlite")))
        try:
            shadow = [
                _json.loads(bytes(r[0]))
                for r in conn.execute(
                    "SELECT canonical_bytes FROM routing_authority_v2_shadow_records"
                    " WHERE record_type = 'RoutingDecisionShadowV2'"
                )
            ]
        finally:
            conn.close()
        assert all(key not in _json.dumps(shadow) for key in keys)
    finally:
        runtime_paths.configure_runtime_home(prior_override)


def test_the_evm_sink_test_restores_a_preexisting_home_override(tmp_path, monkeypatch):
    """The override law above, proven the PR97 way: driving the REAL sink test — through its
    normal path and through a mid-body failure — must leave an override another test armed
    exactly as it found it."""
    from core import runtime_paths as rp

    armed = tmp_path / "armed-home"
    armed.mkdir()
    prior = rp._VOOL_HOME_OVERRIDE
    rp.configure_runtime_home(armed)
    try:
        test_an_evm_private_key_typed_in_chat_never_reaches_either_sink(tmp_path, monkeypatch)
        assert armed == rp._VOOL_HOME_OVERRIDE  # survives normal completion

        def boom(*_args, **_kwargs):
            raise RuntimeError("synthetic mid-test failure")

        monkeypatch.setattr(rdl, "recent_decisions", boom)
        try:
            test_an_evm_private_key_typed_in_chat_never_reaches_either_sink(tmp_path, monkeypatch)
        except RuntimeError:
            pass
        else:
            raise AssertionError("the raising seam never fired")
        assert armed == rp._VOOL_HOME_OVERRIDE  # survives the failure path too
    finally:
        rp.configure_runtime_home(prior)


def test_routing_rows_for_one_chat_stay_correlated_across_handle_shapes(tmp_path, monkeypatch):
    """The fold must not cost the operator the correlation the log exists for: a raw handle
    and its canonical form are ONE identity in both sinks, so rows written by a door that
    folded and a door that had not still land on the same greppable session id."""
    import json as _json
    import sqlite3 as _sqlite

    from core.chat_session_identity import canonical_chat_session_id

    runtime_paths.configure_runtime_home(tmp_path / "home")
    monkeypatch.setattr(rdl, "_SHADOW_STORE", None)
    try:
        raw = "my-project-chat"
        folded = canonical_chat_session_id(raw)
        rdl.record_decision(session_id=raw, user_input="first turn", family="model_lane", handled=False)
        rdl.record_decision(session_id=folded, user_input="second turn", family="model_lane", handled=False)

        rows = rdl.recent_decisions()
        assert rows[0]["session_id"] == folded
        assert rows[1]["session_id"] == folded
        conn = _sqlite.connect(str(rdl.data_path("routing_authority_v2_shadow.sqlite")))
        try:
            refs = [
                str(_json.loads(bytes(r[0])).get("session_ref") or "")
                for r in conn.execute(
                    "SELECT canonical_bytes FROM routing_authority_v2_shadow_records"
                    " WHERE record_type = 'RoutingDecisionShadowV2'"
                )
            ]
        finally:
            conn.close()
        assert refs == [folded, folded]
        assert rdl.decision_stats()["total"] == 2  # ordinary diagnostics see both rows
    finally:
        runtime_paths.configure_runtime_home(None)
