"""Brick 5 — project binding fails closed in the chat page.

A swallowed bind failure used to leave a false "bound" project chip while the server treated the
chat as unbound (and unbound chats receive GLOBAL memory) -- a silent cross-project leak. The page
JS must gate on the bind POST's r.ok and reflect the true (unbound) state on failure, surfacing it
rather than continuing as if isolation succeeded.
"""
from __future__ import annotations

from core.vool_chat_page import render_vool_chat_html

HTML = render_vool_chat_html()


def test_new_chat_in_project_gates_on_bind_ok():
    # The optimistic bind must be gated on r.ok, not fire-and-forget.
    assert "bound = r.ok" in HTML
    # On failure it clears the project and tells the user it is NOT isolated.
    assert "view.projectId = ''" in HTML   # the bound project is per-chat state (DISPATCHER phase 1)
    assert "it is NOT isolated" in HTML


def test_assign_session_gates_on_bind_ok_and_surfaces_failure():
    assert "ok = r.ok" in HTML
    assert "Could not move this chat" in HTML


def test_bind_post_result_is_inspected_not_swallowed():
    # The two binding sites no longer fire-and-forget: each captures the response and branches on it.
    # (The delete-chat path deliberately stays fire-and-forget and is not a binding call.)
    assert "bound = r.ok" in HTML and "ok = r.ok" in HTML


def test_creating_a_project_auto_creates_a_bound_chat():
    # The global "+ Project" flow mints + focuses a fresh chat bound to the new project (reusing the
    # fail-closed newChatInProject), instead of just re-rendering the old session list.
    assert "else { await newChatInProject(d.project.id); }" in HTML
