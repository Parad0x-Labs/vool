"""Person-path fast-path capture tests: which lane owns an ask, on a fake person home.

Every ask is driven through the daemon's own turn function (the shared rig's ``turn``) with
``~`` bound to a temp person home, so "my Desktop" / "Pictures" resolve inside the test tree
while VOOL's runtime home stays a different directory. Each test pins LANE OWNERSHIP and the
on-disk / on-state outcome, never one canned reply string keyed to the visible wording.

Ported to v0.8 from fastpath-capture-tests 987722a8. Two defects that suite pinned as strict
xfails are fixed on v0.8 (the slow-laptop temp cleanup reaches the cleanup lane; "switch on web
search" no longer pretends a web check), so those tests now run as plain tests. Two stay strict
xfails because v0.8 still has them, the same as the 0.7.7 train head: moving a photo from
Pictures to the Desktop, and making a folder on the Desktop from "set up a folder called ...".
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from tests._person_turn_rig import (
    bind_person_home,
    make_agent,
    person_env,
    reply,
    turn,
)

_MODEL_UNAVAILABLE = "couldn't get a live model response"
#: The memory governor's own phrases — an ask about temp FILES must never be answered with
#: the machine's memory story, whichever branch of that report rendered.
_GOVERNOR_PHRASES = (
    "loaded in ollama",
    "memory is tight",
    "nothing looks squeezed",
    "free up memory",
    "live memory state",
)


def _pending_operator_action_count() -> int:
    """Rows the operator lane staged for approval (its own request table)."""
    from storage.db import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM operator_action_requests WHERE status = 'pending_approval'"
        ).fetchone()
        return int(row["n"] or 0)
    finally:
        conn.close()


def _drive_approval_if_staged(agent, env: dict[str, Any], first_reply: str) -> None:
    """Send the person's own follow-up yes when turn 1 staged a request or asked for one."""
    if _pending_operator_action_count() > 0 or "reply with" in first_reply.lower():
        turn(agent, "yes please", session="person-chat")


@pytest.mark.parametrize(
    "ask",
    [
        "my laptop is slow, can you clear out my temp files?",
        "can you clear out my temp files? my laptop is slow",
    ],
)
def test_slow_laptop_temp_cleanup_ask_reaches_cleanup_lane_not_governor(person_env, ask):
    env = person_env
    agent = make_agent()
    temp_root = env["tmp_path"] / "fake-temp-root"
    temp_root.mkdir()
    seeded = temp_root / "junk.tmp"
    seeded.write_bytes(b"j" * 512)
    with mock.patch(
        "core.local_operator_actions.tempfile.gettempdir", return_value=str(temp_root)
    ):
        result = turn(agent, ask)
    text = reply(result)

    # The memory governor must not own this ask: not its route, none of its report phrases
    # (whichever branch of the hog report rendered, including its own failure fallback).
    assert result.get("route") != "deterministic:governor_hog_report"
    lowered = text.lower()
    for phrase in _GOVERNOR_PHRASES:
        assert phrase not in lowered

    # The temp-cleanup lane owns it: an operator tool turn that stages the cleanup for
    # approval (its request table row / the seeded temp root named in the preview) or
    # already executed it against the seeded root.
    assert result.get("mode") in {"tool_preview", "tool_executed"}
    assert (
        _pending_operator_action_count() > 0
        or not seeded.exists()
        or "cleanup" in lowered
    )


@pytest.mark.xfail(
    strict=True,
    reason="defect: 'move the cat photo from Pictures to my Desktop' is refused by the write guard as an unresolvable target; no move is ever staged, so the two-turn approval cannot complete",
)
def test_move_photo_two_turn_approval_completes_inside_home(person_env, monkeypatch):
    env = person_env
    photo = env["person_home"] / "Pictures" / "cat.jpg"
    photo.write_bytes(b"cat-photo-bytes")
    bind_person_home(monkeypatch, env)
    agent = make_agent()

    first = turn(agent, "move the cat photo from Pictures to my Desktop?")
    # Turn 1 captures a move: a staged operator request or a move preview, not a refusal.
    assert _pending_operator_action_count() > 0 or first.get("mode") == "tool_preview"

    second = turn(agent, "yes please")
    text2 = reply(second)
    # The file lands inside the person's home and leaves Pictures.
    assert list((env["person_home"] / "Desktop").rglob("cat.jpg")), "cat.jpg must land on the person's Desktop"
    assert not photo.exists(), "cat.jpg must be gone from Pictures"
    # Turn 2 completed the approval: it is not another approval demand and not another refusal.
    lowered2 = text2.lower()
    assert "pending action id" not in lowered2
    assert "approve" not in lowered2
    assert "bounded local write lane" not in lowered2


@pytest.mark.xfail(
    strict=True,
    reason="defect: 'set up a folder called Invoices on my desktop' ends in a toolless model reply; no folder is created anywhere",
)
def test_invoices_folder_created_on_person_desktop_never_in_vool_home(person_env, monkeypatch):
    env = person_env
    bind_person_home(monkeypatch, env)
    agent = make_agent()

    first = turn(agent, "set up a folder called Invoices on my desktop would you")
    _drive_approval_if_staged(agent, env, reply(first))

    invoices = env["person_home"] / "Desktop" / "Invoices"
    assert invoices.is_dir(), "the Invoices folder must exist on the person's Desktop"
    # Never inside VOOL's own runtime home, at any depth.
    assert not list(Path(env["home"]).rglob("Invoices")), "no Invoices folder may appear under VOOL's own home"
    # The turn ended in a real folder action, not a toolless model reply.
    assert _MODEL_UNAVAILABLE not in reply(first).lower()


def test_invoices_folder_listing_reads_person_home_not_disk_root(person_env, monkeypatch):
    env = person_env
    invoices = env["person_home"] / "Desktop" / "Invoices"
    invoices.mkdir(parents=True)
    (invoices / "rent-march.pdf").write_bytes(b"rent")
    (invoices / "water-bill.pdf").write_bytes(b"water")
    # A same-named folder OUTSIDE the person home, with different files: if any lane walked
    # outward from the named folder, these files are what it would find.
    decoy = env["tmp_path"] / "elsewhere" / "Invoices"
    decoy.mkdir(parents=True)
    (decoy / "not-the-persons-file.txt").write_bytes(b"decoy")
    bind_person_home(monkeypatch, env)
    agent = make_agent()

    result = turn(agent, "can you check my Invoices folder and tell me whats in it?")
    text = reply(result)

    assert "rent-march.pdf" in text
    assert "water-bill.pdf" in text
    assert "not-the-persons-file.txt" not in text
    # A real listing action owned the turn, not the model lane.
    assert "model" not in str(result.get("route") or "")


def test_switch_on_web_search_flips_state_or_refuses_plainly(person_env):
    from core import policy_engine

    env = person_env
    original = policy_engine.allow_web_fallback()
    policy_engine.set_operator_policy_values({"system.allow_web_fallback": False})
    agent = make_agent()
    try:
        result = turn(agent, "can you switch on web search for me?")
        text = reply(result)
        after = policy_engine.allow_web_fallback()

        assert result is not None
        if after is True:
            # Flipped: the reply confirms the new state; it never narrates a web check.
            assert "checked the live web" not in text.lower()
        else:
            # Not flipped: a plain refusal about the setting — never a pretend web check,
            # never model-lane prose, never an explanation of web search from memory.
            lowered = text.lower()
            assert "checked the live web" not in lowered
            assert _MODEL_UNAVAILABLE not in lowered
            assert "add a cloud key" not in lowered
    finally:
        policy_engine.set_operator_policy_values({"system.allow_web_fallback": original})


def test_new_emails_ask_with_model_lane_down_is_never_the_well_known_answer_text(person_env):
    agent = make_agent()

    result = turn(agent, "did i get any new emails today?")
    text = reply(result)

    assert text, "the person gets some reply"
    # Never the entity-ambiguity canned family ("single well-known answer"), in either its
    # unresolved-check form or its clarification form.
    assert "well-known answer" not in text
    assert "more than one place or thing" not in text
    if _MODEL_UNAVAILABLE not in text:
        # Then it must be the email lane's own plain outcome about the person's mailbox,
        # not prose about email in general.
        lowered = text.lower()
        assert any(word in lowered for word in ("mailbox", "inbox", "email account", "email"))
