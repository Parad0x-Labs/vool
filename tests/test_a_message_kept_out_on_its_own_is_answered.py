"""A first message that keeps only itself out is answered, not stopped while it works.

"my colleague Tadas owns a tortoise named Kiaukutis, don't save that, though" as the first message of a chat makes
that turn private without switching the chat. The chat then has no namespace row, only the in-process one a private
chat gets. The turn's own thread saw it; the page's preview poll, on another thread, did not, read the chat's
disclosure scope as changed, and stopped the turn: "Cancelled before publication" (iMac gate on 3c92e8df3).
"""
from __future__ import annotations

import threading
import uuid

import pytest

from core import runtime_paths
from core.context_namespace import ensure_chat_namespace, load_chat_namespace
from core.private_chat import end_private_request, is_private, observe_private_door
from core.semantic.semantic_admissions import bound_request_context


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    runtime_paths.configure_runtime_home(tmp_path / "home")
    yield
    runtime_paths.configure_runtime_home(None)


def _on_another_thread(fn):
    box: dict = {}

    def run() -> None:
        try:
            box["value"] = fn()
        except Exception as exc:  # surfaced below
            box["error"] = exc

    worker = threading.Thread(target=run)
    worker.start()
    worker.join(30)
    if "error" in box:
        raise box["error"]
    return box.get("value")


@pytest.mark.parametrize("message", [
    "my colleague Tadas owns a tortoise named Kiaukutis, don't save that, though",
    "don't save this: my landlady Birute has a canary called Saule",
])
def test_the_chat_reads_the_same_on_every_thread_while_the_turn_runs(message):
    chat = f"chat-{uuid.uuid4().hex[:12]}"
    request = f"req-{uuid.uuid4().hex[:12]}"
    with bound_request_context(request):
        assert observe_private_door(message, session_id=chat, source_context={}) is True
        assert not is_private(chat), "a turn-only don't-save must not switch the chat"
        own = ensure_chat_namespace(chat)
        # The page's poll: another thread, no request bound. The disclosure scope is read from this namespace
        # (core.disclosure.scope.current_version); none there read as a changed chat and stopped the turn.
        seen = _on_another_thread(lambda: load_chat_namespace(chat))
        assert seen is not None
        assert (seen.project_id, seen.lifecycle_state) == (own.project_id, own.lifecycle_state) == ("", "active")
    end_private_request(request)
    # Once the turn has ended, the chat that saved nothing leaves no namespace behind.
    assert _on_another_thread(lambda: load_chat_namespace(chat)) is None
