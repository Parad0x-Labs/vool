"""A chat opened after a deletion keeps its own answer, even when the words match the deleted one.

Ledger V-058 (2026-11-12, real chat page on the v0.8 candidate): "Calculate 31 * 17" answered
"31 * 17 = 527."; the chat was deleted; the same question in a new chat answered the same words,
but its record was born ERASED by the deleted chat's tombstone. The page showed the answer once,
then a reload served nothing ("Reply interrupted") and the export said "unavailable by privacy
policy". The checkpoint half of erase lineage (test_an_erasure_binds_to_its_writers_not_to_text.py)
did not reach the answer's own record.

Owner decision 2026-11-12: a deletion binds to the chats that existed when it ran. A turn that was
already running when the chat was deleted is still refused, a withheld answer still binds, and a
turn with no request on record keeps the old rule. Real SQLite, real finalization and erasure.
"""
from __future__ import annotations

import time
import uuid

import pytest

from core.finalization import (
    AVAILABILITY_AVAILABLE,
    AVAILABILITY_ERASED,
    AVAILABILITY_WITHHELD,
    get_finalization_by_request_id,
    payload_availability_for_request_id,
    payload_availability_for_text,
)
from tests.operator_profile_rig import admit_finalize, erase, withhold

ANSWER = "31 * 17 = 527."


@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_path, monkeypatch):
    from core import runtime_paths
    from storage.db import configure_default_db_path, reset_default_connection

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    configure_default_db_path(tmp_path / "data" / "test.db")
    reset_default_connection()
    from storage.migrations import run_migrations

    run_migrations()
    yield
    reset_default_connection()
    configure_default_db_path(None)
    runtime_paths.configure_runtime_home(None)


def _sent() -> str:
    """A message arriving at the chat door: the request the turn's answer is bound to."""
    from core.invocation.ledger import accept_invocation

    accepted = accept_invocation(external_kind="http", external_value=uuid.uuid4().hex, principal="owner_local")
    time.sleep(0.01)
    return str(accepted["request_id"])


def _answer_of(request_id: str) -> dict:
    row = get_finalization_by_request_id(request_id, principal="owner_local")
    assert row is not None, request_id
    return dict(row)


def _deleted_chat_answered_it() -> None:
    first = _sent()
    finalized = admit_finalize(ANSWER, request_id=first)
    assert _answer_of(first)["availability"] == AVAILABILITY_AVAILABLE
    erase(str(finalized["finalization_id"]))
    assert payload_availability_for_text(ANSWER) == AVAILABILITY_ERASED
    time.sleep(0.01)


def test_a_chat_opened_after_the_deletion_keeps_its_answer() -> None:
    _deleted_chat_answered_it()
    later = _sent()
    admit_finalize(ANSWER, request_id=later)
    row = _answer_of(later)
    assert row["availability"] == AVAILABILITY_AVAILABLE, row
    assert row.get("canonical_content") == ANSWER, row
    # The chat page and the export read an answer by its turn: that verdict is the new one.
    assert payload_availability_for_request_id(later) == AVAILABILITY_AVAILABLE
    # Bytes read with no turn attached still resolve to the erasure: an old copy of the deleted
    # chat's answer in some store must never be served because a new chat said the same.
    assert payload_availability_for_text(ANSWER) == AVAILABILITY_ERASED

    # The chat after that keeps its answer too: the second chat's live record is no veto.
    third = _sent()
    admit_finalize(ANSWER, request_id=third)
    assert _answer_of(third)["availability"] == AVAILABILITY_AVAILABLE
    assert _answer_of(third).get("canonical_content") == ANSWER


def test_a_turn_already_running_when_the_chat_was_deleted_is_still_refused() -> None:
    running = _sent()
    _deleted_chat_answered_it()
    admit_finalize(ANSWER, request_id=running)
    row = _answer_of(running)
    assert row["availability"] == AVAILABILITY_ERASED, row
    assert not row.get("canonical_content"), row


def test_a_withheld_answer_still_binds_a_new_turn() -> None:
    first = _sent()
    finalized = admit_finalize(ANSWER, request_id=first)
    withhold(str(finalized["finalization_id"]))
    later = _sent()
    admit_finalize(ANSWER, request_id=later)
    assert _answer_of(later)["availability"] == AVAILABILITY_WITHHELD


def test_a_withheld_copy_elsewhere_still_binds_after_a_deletion() -> None:
    """Deleting one chat never frees words that another answer still holds withheld."""
    deleted = admit_finalize(ANSWER, request_id=_sent())
    held = admit_finalize(ANSWER, request_id=_sent())
    withhold(str(held["finalization_id"]))
    erase(str(deleted["finalization_id"]))
    time.sleep(0.01)
    later = _sent()
    admit_finalize(ANSWER, request_id=later)
    row = _answer_of(later)
    assert row["availability"] in (AVAILABILITY_WITHHELD, AVAILABILITY_ERASED), row
    assert not row.get("canonical_content"), row


def test_the_checkpoint_of_a_third_chat_may_write_the_words_too() -> None:
    """The checkpoint half of erase lineage reads the same rule: a live row is no veto."""
    from core.runtime_continuity import create_runtime_checkpoint, update_runtime_checkpoint

    _deleted_chat_answered_it()
    admit_finalize(ANSWER, request_id=_sent())
    checkpoint = str(create_runtime_checkpoint(session_id="third-chat", request_text="q", source_context=None)["checkpoint_id"])
    update_runtime_checkpoint(checkpoint, status="completed", final_response=ANSWER)


def test_a_turn_with_no_request_on_record_keeps_the_old_rule() -> None:
    _deleted_chat_answered_it()
    unknown = "req:http:" + uuid.uuid4().hex   # never accepted at the door
    admit_finalize(ANSWER, request_id=unknown)
    assert _answer_of(unknown)["availability"] == AVAILABILITY_ERASED
