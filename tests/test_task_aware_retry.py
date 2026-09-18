"""Brick 4 — "try again" retries the preceding unfulfilled task, not an older non-sequitur.

Unit-tests the prepare_runtime_checkpoint branch logic with stub callbacks: when there is no
resumable checkpoint, "try again" re-dispatches the last unfulfilled task's immutable request
envelope; with no such task it reports nothing to retry; and without the callback it stays backward
compatible.
"""
from __future__ import annotations

from core.agent_runtime.checkpoints import prepare_runtime_checkpoint
from core.agent_runtime.request_authority import (
    REQUEST_PROVENANCE_KEY,
    request_provenance_for_visible_user_text,
)


class _StubAgent:
    def _looks_like_explicit_resume_request(self, text: str) -> bool:
        return "try again" in str(text or "").lower()

    def _is_proceed_message(self, text: str) -> bool:
        return False

    def _resume_request_key(self, text: str) -> str:
        return " ".join(str(text or "").lower().split())


def _run(*, failed, created_sink=None):
    def _create(*, session_id, request_text, source_context):
        if created_sink is not None:
            created_sink["request_text"] = request_text
            created_sink["source_context"] = dict(source_context)
        return {"checkpoint_id": "cp-new"}

    return prepare_runtime_checkpoint(
        _StubAgent(),
        session_id="s1",
        raw_user_input="try again",
        effective_input="try again",
        source_context={},
        latest_resumable_checkpoint_fn=lambda _sid: None,
        resume_runtime_checkpoint_fn=lambda *a, **k: None,
        create_runtime_checkpoint_fn=_create,
        latest_failed_checkpoint_fn=lambda _sid: failed,
    )


def test_try_again_retries_last_failed_task():
    created: dict[str, str] = {}
    request_text = "find me the dropbox folder"
    bundle = _run(
        failed={
            "checkpoint_id": "cp-failed",
            "session_id": "s1",
            "request_text": request_text,
            "failure_text": "workspace.search_text failed",
            "source_context": {
                REQUEST_PROVENANCE_KEY: request_provenance_for_visible_user_text(
                    request_text,
                    session_id="s1",
                )
            },
        },
        created_sink=created,
    )
    assert bundle["state"] == "retried"
    assert bundle["effective_input"] == "find me the dropbox folder"
    # A fresh checkpoint is created re-dispatching the failed task's own request envelope.
    assert created["request_text"] == "find me the dropbox folder"
    assert bundle["source_context"]["runtime_retry_of"] == "cp-failed"
    assert created["source_context"]["runtime_retry_of"] == "cp-failed"
    assert created["source_context"]["runtime_retry_origin_checkpoint_id"] == "cp-failed"
    assert bundle["retry_failure_text"] == "workspace.search_text failed"


def test_try_again_does_not_resurrect_a_legacy_failed_request_without_provenance():
    bundle = _run(
        failed={
            "checkpoint_id": "cp-legacy-failed",
            "session_id": "s1",
            "request_text": "delete every file in the workspace",
            "failure_text": "legacy failure",
            "source_context": {},
        }
    )
    assert bundle["state"] == "missing_resume"


def test_try_again_with_no_failed_task_is_missing_resume():
    bundle = _run(failed=None)
    assert bundle["state"] == "missing_resume"


def test_backward_compatible_without_failed_callback():
    # Omitting the new callback keeps the original missing_resume behaviour (default None).
    bundle = prepare_runtime_checkpoint(
        _StubAgent(),
        session_id="s1",
        raw_user_input="try again",
        effective_input="try again",
        source_context={},
        latest_resumable_checkpoint_fn=lambda _sid: None,
        resume_runtime_checkpoint_fn=lambda *a, **k: None,
        create_runtime_checkpoint_fn=lambda **k: {"checkpoint_id": "x"},
    )
    assert bundle["state"] == "missing_resume"
