"""Deleting a project unbinds its chats from BOTH stores, so recreating it does not hand them back.

Reported: the openclaw-skills project was deleted and created again on the same folder, and it
still showed 61 chats.

Measured on that runtime, on the live API:

    chats listed under the project : 61
      bound via session META       :  1
      bound via NAMESPACE          : 61
      namespace-only               : 60

A chat's project binding lives in two places -- the session-meta entry and ``project_id`` on its
context namespace -- and ``list_conversation_sessions`` falls back to the namespace when the meta
has none. Two consequences, and the project-delete handler hit both:

1. It enumerated the project's chats from session meta ALONE, so it found 1 of 61. The other 60
   were never unbound and never forgotten.
2. ``set_session_meta(project_id="")`` cleared only the meta, so even that 1 kept its namespace
   binding and the fallback re-resolved it to the same project.

A project id is derived from its folder path, so deleting a project and recreating it on that
folder produces the SAME id -- and every chat still carrying it reappears. That is the reported
behaviour, and it is why the existing ``test_project_delete_forgets_memory_and_unbinds_chats``
passes: it asserts the meta entry was cleared, which was always true, and never looks at the
namespace or at a chat bound only there.
"""
from __future__ import annotations

import json

import pytest

from core import runtime_paths
from core.context_namespace import (
    ensure_chat_namespace,
    load_chat_namespace,
    set_chat_namespace_project,
)
from core.memory.entries import resolve_memory_access_policy
from core.persistent_memory import (
    append_conversation_event,
    list_conversation_sessions,
    load_session_meta,
    set_session_meta,
)
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_post

_META_BOUND = "openclaw:" + "1" * 20
_NS_BOUND = "openclaw:" + "2" * 20
_BOTH_BOUND = "openclaw:" + "3" * 20
_OTHER = "openclaw:" + "4" * 20


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    runtime_paths.configure_runtime_home(tmp_path / "home")
    yield
    runtime_paths.configure_runtime_home(None)


def _chat(sid: str) -> None:
    ensure_chat_namespace(sid, grant_current_receipts=False)
    append_conversation_event(
        session_id=sid,
        user_input="hi",
        assistant_output="hey",
        access_policy=resolve_memory_access_policy(chat_id=sid),
    )


@pytest.fixture()
def project(tmp_path) -> str:
    """A real project, created the way the app creates one, so its id is derived from its folder
    exactly as in production -- which is what makes a recreated project reclaim stale bindings."""
    root = tmp_path / "openclaw-skills"
    root.mkdir()
    res = _api("/api/projects", {"name": "openclaw-skills", "root": str(root)})
    return str(res["project"]["id"])


def _seed_a_project_bound_three_ways(pid: str) -> None:
    """The live shape: most chats bound by namespace only, one by meta only, one by both."""
    _chat(_META_BOUND)
    set_session_meta(_META_BOUND, project_id=pid)

    _chat(_NS_BOUND)
    set_chat_namespace_project(_NS_BOUND, pid)

    _chat(_BOTH_BOUND)
    set_session_meta(_BOTH_BOUND, project_id=pid)
    set_chat_namespace_project(_BOTH_BOUND, pid)

    _chat(_OTHER)


def _api(path: str, body: dict) -> dict:
    res = dispatch_post(
        path=path,
        body=body,
        headers={"content-type": "application/json"},
        runtime=RuntimeServices(display_name="VOOL"),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        client_host="127.0.0.1",
    )
    return json.loads(res.body.decode("utf-8"))


def _delete_project(pid: str) -> dict:
    return _api("/api/projects/delete", {"id": pid})


def _listed_under_project(pid: str) -> list[str]:
    return [
        str(s["session_id"])
        for s in list_conversation_sessions(limit=5000)
        if str(s.get("project_id") or "") == pid
    ]


def test_every_chat_of_the_project_is_found_however_it_was_bound(project: str) -> None:
    """Scanning one store reported 1 chat where there were 3."""
    _seed_a_project_bound_three_ways(project)

    body = _delete_project(project)

    assert body["ok"] is True
    assert body["chats"] == 3, f"the delete only found {body['chats']} of the project's 3 chats"


def test_no_chat_is_still_bound_after_the_project_is_deleted(project: str) -> None:
    _seed_a_project_bound_three_ways(project)

    _delete_project(project)

    assert _listed_under_project(project) == []


def test_the_namespace_binding_is_cleared_not_just_the_meta_entry(project: str) -> None:
    """The mechanism. Clearing meta alone leaves the fallback pointing at the dead project."""
    _seed_a_project_bound_three_ways(project)

    _delete_project(project)

    for sid in (_META_BOUND, _NS_BOUND, _BOTH_BOUND):
        namespace = load_chat_namespace(sid)
        assert namespace is not None
        assert str(namespace.project_id or "") == "", (
            f"{sid} still carries project_id={namespace.project_id!r} on its namespace"
        )
        assert str((load_session_meta().get(sid) or {}).get("project_id") or "") == ""


def test_recreating_the_project_on_the_same_id_does_not_hand_the_chats_back(project: str) -> None:
    """The reported symptom end to end: a project id is derived from its folder, so recreating it
    yields the same id. Any binding that survived the delete resurrects with it."""
    _seed_a_project_bound_three_ways(project)

    _delete_project(project)
    # Recreating on the same folder mints the same id; nothing needs to be re-registered for the
    # listing to resolve a stale binding, which is precisely the failure.
    still_bound = _listed_under_project(project)

    assert still_bound == [], f"{len(still_bound)} chat(s) came back with the recreated project"


def test_a_chat_outside_the_project_is_untouched(project: str) -> None:
    _seed_a_project_bound_three_ways(project)

    _delete_project(project)

    listed = {str(s["session_id"]) for s in list_conversation_sessions(limit=5000)}
    assert _OTHER in listed, "deleting a project removed a chat that was never in it"


def test_binding_a_chat_to_a_project_writes_both_stores(project: str) -> None:
    """The general property behind the fix: the two stores move together, so neither can outlive
    the other and resurrect a binding."""
    _chat(_META_BOUND)

    set_session_meta(_META_BOUND, project_id=project)

    namespace = load_chat_namespace(_META_BOUND)
    assert str(namespace.project_id or "") == project, (
        "binding through the meta left the namespace unbound"
    )
