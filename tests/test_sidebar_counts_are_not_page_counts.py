"""The sidebar's group counts describe the chats, not the page of chats it was sent.

Reported: 3 chats in a project and 97 in General; delete one project chat and General rises to 98.
Deleting inside General removes the row and the number never falls.

Measured against the live runtime, driving the API rather than reading it:

    BEFORE                  total=100  General=99  project=1
    after CREATE in project total=100  General=98  project=2
    after DELETE            total=100  General=99  project=1

``total`` never moves, because ``/api/chat/sessions`` returned ``list_conversation_sessions(limit=100)``
against 2,005 real chats. The list is a window. The sidebar counted the window: the group headers
rendered ``general.length`` over the rows it happened to receive.

So deleting a chat frees a slot, the next chat by recency slides into the window, and since the
overwhelming majority of chats are unbound that arrival is almost always a General one -- the
project drops by one and General rises by one, which reads exactly like the chat moved. Delete
inside General and one leaves as another arrives, so the number holds still.

Neither number was ever wrong about the page. Both were wrong about the chats.

These tests pin the property that survives paging: a count is over everything, and it keeps being
right when there are more chats than fit.
"""
from __future__ import annotations

import json

import pytest

from core import runtime_paths
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.persistent_memory import append_conversation_event, set_session_meta
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get

_PAGE = 100
_PROJECT = "proj_counts"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    runtime_paths.configure_runtime_home(tmp_path / "home")
    yield
    runtime_paths.configure_runtime_home(None)


def _chat(index: int, *, project_id: str = "") -> str:
    sid = f"openclaw:{index:020d}"
    ensure_chat_namespace(sid, grant_current_receipts=False)
    append_conversation_event(
        session_id=sid,
        user_input="hi",
        assistant_output="hey",
        access_policy=resolve_memory_access_policy(chat_id=sid),
    )
    if project_id:
        set_session_meta(sid, project_id=project_id)
    return sid


def _sessions_payload() -> dict:
    res = dispatch_get(
        path="/api/chat/sessions",
        query={},
        runtime=RuntimeServices(),
        model_name="test-model",
    )
    return json.loads(res.body.decode("utf-8"))


def _seed_more_than_one_page() -> None:
    """One project chat, and enough unbound chats that the project cannot fit in the window.

    The project chat is created FIRST so it is the oldest, which puts it outside a page ordered by
    recency -- the shape that made the live numbers move.
    """
    _chat(0, project_id=_PROJECT)
    for i in range(1, _PAGE + 40):
        _chat(i)


def test_the_count_describes_every_chat_not_the_page() -> None:
    _seed_more_than_one_page()

    payload = _sessions_payload()

    assert len(payload["sessions"]) <= _PAGE, "the page is expected to stay capped"
    assert payload["counts"][""] == _PAGE + 39, "General count is not the number of unbound chats"
    assert payload["counts"][_PROJECT] == 1, "the project's chat fell out of the page and was lost"
    assert payload["total"] == _PAGE + 40


def test_a_project_chat_outside_the_page_is_still_counted() -> None:
    """The exact live shape: the project's only chat is too old to be on the page. Counting the
    page reports the project as empty."""
    _seed_more_than_one_page()

    payload = _sessions_payload()
    on_page = [s for s in payload["sessions"] if str(s.get("project_id") or "") == _PROJECT]

    assert on_page == [], "fixture is not exercising truncation; the project chat is on the page"
    assert payload["counts"][_PROJECT] == 1


def test_deleting_a_project_chat_does_not_raise_the_general_count() -> None:
    """The reported symptom as a property. Under page-counting this rose by one every time."""
    from core.persistent_memory import delete_conversation_session

    _seed_more_than_one_page()
    before = _sessions_payload()["counts"][""]

    delete_conversation_session(f"openclaw:{0:020d}")

    after = _sessions_payload()["counts"][""]
    assert after == before, f"deleting a project chat changed the General count: {before} -> {after}"


def test_deleting_a_general_chat_lowers_the_general_count() -> None:
    """The second symptom. Under page-counting the number never moved, because a chat from beyond
    the window took the freed slot."""
    from core.persistent_memory import delete_conversation_session

    _seed_more_than_one_page()
    before = _sessions_payload()["counts"][""]

    delete_conversation_session(f"openclaw:{1:020d}")

    after = _sessions_payload()["counts"][""]
    assert after == before - 1, f"deleting a General chat left the count at {after}"


def test_archived_chats_are_not_counted() -> None:
    """The sidebar groups only unarchived chats, so a count that included archived ones would
    disagree with the list underneath it."""
    _chat(0, project_id=_PROJECT)
    sid = _chat(1)
    set_session_meta(sid, archived=True)

    counts = _sessions_payload()["counts"]

    assert counts.get("", 0) == 0, "an archived chat was counted in General"
    assert counts[_PROJECT] == 1
