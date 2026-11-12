"""An erasure removes what was written; it must not make the same words unwritable for new writers.

Measured on the 0.7.8 release sweep (2026-11-12): the runner deleted a chat whose answer was "The
planets closer to the Sun than Mars are Mercury, Venus, and Earth."; the next chat produced the
same sentence, and every durable write carrying it was refused as a resurrection. A writer that
began before the erasure is still refused. Real SQLite, real finalization and erasure.
Contributor: sls_0x.
"""
from __future__ import annotations

import time

import pytest

from core.runtime_continuity import CheckpointTransitionRefused, create_runtime_checkpoint, update_runtime_checkpoint
from tests.operator_profile_rig import admit_finalize, erase

ANSWER = "The planets closer to the Sun than Mars are Mercury, Venus, and Earth."


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


def _checkpoint(session: str) -> str:
    return str(create_runtime_checkpoint(session_id=session, request_text="q", source_context=None)["checkpoint_id"])


def _erase_the_answer() -> None:
    finalized = admit_finalize(ANSWER)
    fid = str(finalized.get("finalization_id") or "")
    assert fid, finalized
    erase(fid)


def test_a_writer_that_began_before_the_erasure_is_still_refused():
    old = _checkpoint("deleted-chat")
    time.sleep(0.01)
    _erase_the_answer()
    with pytest.raises(CheckpointTransitionRefused):
        update_runtime_checkpoint(old, status="completed", final_response=ANSWER)


def test_a_new_chat_may_write_the_same_words_after_the_erasure():
    _erase_the_answer()
    time.sleep(0.01)
    new = _checkpoint("new-chat")
    update_runtime_checkpoint(new, task_id="t", source_context={"history": [{"role": "assistant", "content": ANSWER}]})
    update_runtime_checkpoint(new, status="completed", final_response=ANSWER)
