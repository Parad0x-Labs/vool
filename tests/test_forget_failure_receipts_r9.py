"""Failed forget outcomes and receipt publication failures retain truthful durable status."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import core.honesty_receipt as ledger
from core.agent_runtime.action_honesty_validator import emit_turn_honesty_receipt, enforce_final_action_honesty
from tests.test_question_date_time_leg_20261002 import _profile


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    _profile(tmp_path)
    monkeypatch.setenv("VOOL_HONESTY_RECEIPTS", "1")


def _issued(chat="receipt-publication", **kwargs):
    return ledger.issue_honesty_receipt(session_id=chat, turn_index=0, **kwargs)


def test_recorded_duplicate_and_failed_are_distinct_and_do_not_duplicate_ledger(monkeypatch):
    receipt = _issued()
    assert ledger.record_honesty_receipt(receipt).status == "written"
    assert ledger.record_honesty_receipt(receipt).status == "already_exists"
    assert ledger.list_honesty_receipts(receipt.session_id) == [receipt.to_dict()]
    path = ledger._ledger_path(receipt.session_id)
    before = path.read_bytes()
    original = ledger.os.replace

    def denied(source, target):
        if Path(target) == path:
            raise OSError("receipt publication denied")
        return original(source, target)

    monkeypatch.setattr(ledger.os, "replace", denied)
    next_receipt = ledger.issue_honesty_receipt(
        session_id=receipt.session_id, turn_index=1, prev_hash=receipt.content_hash,
    )
    outcome = ledger.record_honesty_receipt(next_receipt)
    assert outcome.status == "failed" and outcome.receipt is None and outcome.reason
    assert path.read_bytes() == before


@pytest.mark.parametrize("failures", [1, 2, None])
def test_finalization_retries_and_rebuilds_one_issued_receipt_without_claiming_failed_persistence(monkeypatch, failures):
    chat = "receipt-rebuild"
    path = ledger._ledger_path(chat)
    original = ledger.os.replace
    attempted = []

    def refused(source, target):
        if Path(target) == path:
            rows = [json.loads(line) for line in Path(source).read_text().splitlines()]
            attempted.append(rows[-1]["receipt_id"])
            if failures is None or len(attempted) <= failures:
                raise OSError("receipt publication unavailable")
        return original(source, target)

    monkeypatch.setattr(ledger.os, "replace", refused)
    response = "Nothing was forgotten. The forget did not start. Please try again."
    out = emit_turn_honesty_receipt({"response": response}, session_id=chat)
    assert out["response"] == response
    assert "honesty_receipt_write_error" in out["index_failures"]
    assert out["receipt_rebuild_attempted"] is True
    assert len(set(attempted)) == 1
    rows = ledger.list_honesty_receipts(chat)
    if failures is None:
        assert out["receipt_write_status"] == "failed"
        assert out["receipt_pending"] is True
        assert "honesty_receipt" not in out
        assert out["receipt_pending_id"] == attempted[0]
        assert rows == []
    else:
        assert out["receipt_write_status"] in {"written", "already_exists"}
        assert out["receipt_pending"] is False
        assert len(rows) == 1 and rows[0]["receipt_id"] == attempted[0]
        assert out["honesty_receipt"]["receipt_id"] == rows[0]["receipt_id"]
        assert ledger.verify_honesty_receipt(rows[0]) == (True, "ok")


def test_failed_verification_reuses_already_persisted_receipt_on_retry(monkeypatch):
    chat = "receipt-reread"
    path = ledger._ledger_path(chat)
    original = Path.read_text
    denied = []

    def refuse_first_readback(selected, *args, **kwargs):
        if selected == path and selected.exists() and not denied:
            denied.append(selected)
            raise OSError("receipt verification read unavailable")
        return original(selected, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", refuse_first_readback)
    out = emit_turn_honesty_receipt({"response": "No operation was requested."}, session_id=chat)
    assert denied
    assert out["receipt_write_status"] == "already_exists"
    assert out["receipt_pending"] is False
    assert len(ledger.list_honesty_receipts(chat)) == 1


def test_malformed_ledger_cannot_be_reported_as_recorded():
    receipt = _issued("invalid-receipt-ledger")
    path = ledger._ledger_path(receipt.session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"truncated":')
    before = path.read_bytes()
    outcome = ledger.record_honesty_receipt(receipt)
    assert outcome.status == "failed" and outcome.receipt is None
    assert path.read_bytes() == before


def test_not_started_executor_outcome_is_signed_without_erasure_action(tmp_path, monkeypatch):
    from core.memory import revocation
    from core.persistent_memory import maybe_handle_memory_command

    home = _profile(tmp_path / "not-started")
    revocation.prepare(home, allow_initialize=True, arm=False)

    def no_publication(path, payload):
        raise OSError("barrier publication unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(revocation, "_durable_write", no_publication)
        chat, prompt = "not-started-signed", "forget GARDEN77"
        context = {"chat_id": chat, "runtime_home": str(home), "client_turn_id": "failed-request"}
        handled, reply = maybe_handle_memory_command(prompt, session_id=chat, source_context=context)
    assert handled and "Nothing was forgotten" in reply and "try again" in reply
    output = {"response": reply, "_memory_command_outcome": reply}
    final = enforce_final_action_honesty(output, user_input=prompt, session_id=chat, source_context=context)
    out = emit_turn_honesty_receipt(final, user_input=prompt, session_id=chat, source_context=context)
    assert out["response"] == reply
    assert out["receipt_write_status"] == "written" and out["receipt_pending"] is False
    assert out["honesty_receipt"]["memory_command_status"] == "not_started"
    row, = ledger.list_honesty_receipts(chat)
    assert row["claimed_actions"] == [] and "not_started" in row["verdict_detail"]
    assert ledger.verify_honesty_receipt(row) == (True, "ok")


def test_stale_chain_cannot_append_a_second_receipt_at_the_same_position():
    first = _issued("concurrent-receipts")
    second = _issued("concurrent-receipts")
    assert ledger.record_honesty_receipt(first).status == "written"
    assert ledger.record_honesty_receipt(second).status == "failed"
    assert ledger.list_honesty_receipts(first.session_id) == [first.to_dict()]


def test_caller_turn_indexes_can_start_at_one_and_have_gaps():
    first = ledger.issue_honesty_receipt(session_id="caller-indexes", turn_index=1)
    assert ledger.record_honesty_receipt(first).status == "written"
    later = ledger.issue_honesty_receipt(
        session_id="caller-indexes", turn_index=4, prev_hash=first.content_hash,
    )
    assert ledger.record_honesty_receipt(later).status == "written"
    assert ledger.list_honesty_receipts(first.session_id) == [first.to_dict(), later.to_dict()]
    assert ledger.verify_honesty_chain(ledger.list_honesty_receipts(first.session_id)) == (True, "ok")
