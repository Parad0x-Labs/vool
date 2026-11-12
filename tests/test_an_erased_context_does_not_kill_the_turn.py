"""A refused durable write of the turn's context must not kill the turn.

Measured on the 0.7.8 release sweep (2026-11-12, blocks 1 and 1c): the runner deleted a chat whose
opening question was "name the planets closer to the sun than mars"; a new chat asking the same
question then failed its second turn as FAILED_PROVIDER, with no answer and no error on the page,
because the checkpoint context update raised CheckpointTransitionRefused out of the turn.
Contributor: sls_0x.
"""
from __future__ import annotations

import pytest

from core.runtime_continuity import CheckpointTransitionRefused, create_runtime_checkpoint, update_runtime_checkpoint

ERASED_QUESTION = "name the planets closer to the sun than mars"


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
    # The erasure verdict for the deleted chat's question, as the A8 store reports it.
    import core.finalization as finalization

    monkeypatch.setattr(finalization, "writer_may_persist_text", lambda text: ERASED_QUESTION not in str(text or ""))
    yield
    reset_default_connection()
    configure_default_db_path(None)
    runtime_paths.configure_runtime_home(None)


def _context(checkpoint_id: str) -> dict:
    return {"runtime_checkpoint_id": checkpoint_id, "session_id": "new-chat",
            "history": [{"role": "user", "content": ERASED_QUESTION}]}


def test_the_store_itself_still_refuses_the_write():
    checkpoint_id = str(create_runtime_checkpoint(session_id="new-chat", request_text="and the hottest one?",
                                                  source_context=None)["checkpoint_id"])
    with pytest.raises(CheckpointTransitionRefused):
        update_runtime_checkpoint(checkpoint_id, task_id="t", source_context=_context(checkpoint_id))


def test_the_turn_s_context_update_survives_the_refusal():
    from core.agent_runtime.checkpoints import update_runtime_checkpoint_context

    checkpoint_id = str(create_runtime_checkpoint(session_id="new-chat", request_text="and the hottest one?",
                                                  source_context=None)["checkpoint_id"])
    update_runtime_checkpoint_context(_context(checkpoint_id), task_id="t", task_class="chat",
                                      update_runtime_checkpoint_fn=update_runtime_checkpoint)
