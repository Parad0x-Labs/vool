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
        for word in phrase.split():  # word-BOUNDED: 'age' must not fail on 'message'
            assert not _re.search(rf"\b{_re.escape(word)}\b", stored), word
        assert "[redacted-mnemonic]" in rows[-1]["message"]
    finally:
        runtime_paths.configure_runtime_home(None)
