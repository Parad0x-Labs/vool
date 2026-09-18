"""``delete_conversation_session`` finishes the delete instead of leaving the chat half-removed.

Scope, stated up front because the reported symptom has a different cause: the operator saw a
project drop from 3 to 2 while General rose from 97 to 98 on a single delete. That is the UI
opening a replacement chat in General after deleting the chat you are IN
(``core/vool_chat_page.py``), and it is fixed there. These tests are not about that.

What they cover is a real gap found while chasing it. ``delete_conversation_session`` removed the
transcript, the learned memory and the session-meta entry, and left the chat namespace ``active``.
The HTTP endpoint happens to transition the namespace itself before calling this function, so the
UI path was never broken -- but any other caller deletes a chat that keeps existing, because
``list_conversation_sessions`` re-seeds every listing from ``list_chat_namespaces()``. A function
named delete leaving the record that decides existence is a trap for the next caller, not a
reported bug.

``test_chat_delete_endpoint_removes_the_chat`` in the sibling file cannot catch this: it drives the
endpoint, which does the transition, so it passes either way.
"""
from __future__ import annotations

import pytest

from core import runtime_paths
from core.context_namespace import ensure_chat_namespace, list_chat_namespaces, load_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.persistent_memory import (
    append_conversation_event,
    delete_conversation_session,
    list_conversation_sessions,
    set_session_meta,
)

_BOUND = "openclaw:" + "c" * 20
_LOOSE = "openclaw:" + "d" * 20
_KEEP = "openclaw:" + "e" * 20


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    runtime_paths.configure_runtime_home(tmp_path / "home")
    yield
    runtime_paths.configure_runtime_home(None)


def _chat(sid: str, *, project_id: str = "") -> None:
    ensure_chat_namespace(sid, grant_current_receipts=False)
    append_conversation_event(
        session_id=sid,
        user_input="hi",
        assistant_output="hey",
        access_policy=resolve_memory_access_policy(chat_id=sid),
    )
    if project_id:
        set_session_meta(sid, project_id=project_id)


def _general() -> list[str]:
    """What the sidebar shows under General: every listed chat bound to no project."""
    return [
        str(s["session_id"])
        for s in list_conversation_sessions(limit=500)
        if not str(s.get("project_id") or "")
    ]


def test_deleting_a_project_chat_does_not_add_one_to_general() -> None:
    """The reported symptom, stated as a property: General must not grow when a chat is deleted."""
    _chat(_BOUND, project_id="proj_openclaw")
    _chat(_KEEP)
    before = len(_general())

    delete_conversation_session(_BOUND)

    after = len(_general())
    assert after <= before, (
        f"deleting a project chat moved it to General: General went {before} -> {after}"
    )
    assert _BOUND not in _general(), "the deleted chat is now listed under General"


def test_a_deleted_chat_is_gone_from_the_listing_entirely() -> None:
    _chat(_BOUND, project_id="proj_openclaw")

    delete_conversation_session(_BOUND)

    listed = [str(s["session_id"]) for s in list_conversation_sessions(limit=500)]
    assert _BOUND not in listed


def test_a_deleted_chat_stops_being_counted() -> None:
    """The second symptom: the chat disappears from view but the number does not move.

    Counted the way the runtime counts -- live namespaces -- rather than by what the sidebar
    happens to render, so a chat that is merely hidden still fails this.
    """
    _chat(_LOOSE)
    _chat(_KEEP)
    before = len(list_chat_namespaces(limit=500))

    delete_conversation_session(_LOOSE)

    after = len(list_chat_namespaces(limit=500))
    assert after == before - 1, (
        f"the deleted chat is still a live namespace: count went {before} -> {after}"
    )


def test_the_namespace_is_transitioned_not_left_behind() -> None:
    """The mechanism, asserted directly, so this test names the defect rather than its shadow."""
    _chat(_LOOSE)

    delete_conversation_session(_LOOSE)

    namespace = load_chat_namespace(_LOOSE)
    assert namespace is None or namespace.lifecycle_state == "deleted", (
        "the chat namespace survived the delete as "
        f"{namespace.lifecycle_state if namespace else '?'}"
    )




def test_deleting_one_chat_leaves_its_neighbours_alone() -> None:
    """The repair must not overshoot: a sibling in the same project keeps its binding."""
    _chat(_BOUND, project_id="proj_openclaw")
    _chat(_KEEP, project_id="proj_openclaw")

    delete_conversation_session(_BOUND)

    listed = {str(s["session_id"]): s for s in list_conversation_sessions(limit=500)}
    assert _KEEP in listed, "deleting one chat removed its neighbour"
    assert str(listed[_KEEP].get("project_id") or "") == "proj_openclaw", (
        "the surviving chat lost its project binding"
    )
