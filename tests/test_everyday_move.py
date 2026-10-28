"""'move the cat photo from Pictures to my Desktop', then 'yes please', moves that one file (audit 2026-10-27, F55).

The ask was refused by the machine write guard ("did not resolve to a safe local target"), and the operator
move lane only read quoted paths and only 'approve move <id>' as the approval.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import core.local_operator_actions as loa
from core.local_operator_actions import approval_the_answer_offered, resolve_everyday_move
from core.operator.parser import parse_operator_action_intent


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "home"
    for name in ("Desktop", "Downloads", "Documents", "Pictures/Family"):
        (root / name).mkdir(parents=True)
    (root / "Pictures" / "cat_on_sofa.png").write_bytes(b"\x89PNG cat")
    (root / "Pictures" / "Family" / "mia_birthday.jpg").write_bytes(b"\xff\xd8 mia")
    (root / "Downloads" / "mia_birthday_invite.pdf").write_bytes(b"%PDF mia")
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: root))
    return root


@pytest.mark.parametrize("text", [
    "can you move the cat photo from Pictures to my Desktop?",
    "move cat_on_sofa.png to my desktop",
    "move the birthday picture out of my pictures folder into documents",
])
def test_an_everyday_move_is_the_move_lane(text):
    intent = parse_operator_action_intent(text)
    assert intent is not None and intent.kind == "move_path"


@pytest.mark.parametrize("text", ["move my meeting to friday", "how do I move photos to my desktop on a mac"])
def test_other_moves_are_not(text):
    intent = parse_operator_action_intent(text)
    assert intent is None or intent.kind != "move_path"


def test_the_one_file_meant_is_found(home):
    moved = resolve_everyday_move("can you move the cat photo from Pictures to my Desktop?")
    assert moved == {"source_path": str(home / "Pictures" / "cat_on_sofa.png"), "destination_dir": str(home / "Desktop")}
    named = resolve_everyday_move("move cat_on_sofa.png to my desktop")
    assert named["source_path"] == str(home / "Pictures" / "cat_on_sofa.png")


def test_several_or_no_files_are_said_and_nothing_is_picked(home):
    several = resolve_everyday_move("move the mia birthday thing to my desktop")
    assert isinstance(several, str) and "~/Pictures/Family/mia_birthday.jpg" in several and "mia_birthday_invite.pdf" in several
    only_pictures = resolve_everyday_move("move the mia birthday photo from pictures to my desktop")
    assert only_pictures["source_path"].endswith("mia_birthday.jpg")
    assert "couldn't find" in resolve_everyday_move("move the dog photo from Pictures to my Desktop")


def _preview(action_id: str) -> list[dict]:
    return [
        {"role": "user", "content": "can you move the cat photo from Pictures to my Desktop?"},
        {"role": "assistant", "content": f"Move preview ready.\n- Source: a\n- Destination: b\n\nReply with: approve move {action_id}"},
    ]


@pytest.fixture
def pending(monkeypatch):
    rows = {}

    def load(*, session_id, action_kind, action_id=None):
        row = rows.get((session_id, action_kind, action_id))
        return dict(row) if row else None

    monkeypatch.setattr(loa, "_load_pending_action", load)
    return rows


def test_yes_please_after_the_preview_approves_that_move(pending):
    action_id = "0f0e8d1c-1111-2222-3333-444455556666"
    pending[("chat", "move_path", action_id)] = {"action_id": action_id, "created_at": datetime.now(timezone.utc).isoformat()}
    context = {"client_conversation_history": _preview(action_id)}
    for words in ("yes please", "Yes.", "ok", "go ahead", "yes move it"):
        intent = approval_the_answer_offered(words, session_id="chat", source_context=context)
        assert intent is not None and intent.kind == "move_path" and intent.action_id == action_id and intent.approval_requested


@pytest.mark.parametrize("words", ["no", "no thanks", "don't", "wait", "yes but move the dog photo instead", "no proceed"])
def test_anything_but_a_bare_go_ahead_is_not_an_approval(pending, words):
    action_id = "0f0e8d1c-1111-2222-3333-444455556666"
    pending[("chat", "move_path", action_id)] = {"action_id": action_id, "created_at": datetime.now(timezone.utc).isoformat()}
    assert approval_the_answer_offered(words, session_id="chat", source_context={"client_conversation_history": _preview(action_id)}) is None


def test_a_yes_binds_only_a_fresh_preview_this_chat_still_holds(pending):
    action_id = "0f0e8d1c-1111-2222-3333-444455556666"
    context = {"client_conversation_history": _preview(action_id)}
    assert approval_the_answer_offered("yes", session_id="chat", source_context=context) is None  # not pending here
    pending[("other-chat", "move_path", action_id)] = {"action_id": action_id, "created_at": datetime.now(timezone.utc).isoformat()}
    assert approval_the_answer_offered("yes", session_id="chat", source_context=context) is None  # another chat's
    old = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    pending[("chat", "move_path", action_id)] = {"action_id": action_id, "created_at": old}
    assert approval_the_answer_offered("yes", session_id="chat", source_context=context) is None  # stale
    pending[("chat", "move_path", action_id)]["created_at"] = datetime.now(timezone.utc).isoformat()
    later = [*_preview(action_id), {"role": "user", "content": "what's the weather"}, {"role": "assistant", "content": "Sunny."}]
    assert approval_the_answer_offered("yes", session_id="chat", source_context={"client_conversation_history": later}) is None
    assert approval_the_answer_offered("yes", session_id="chat", source_context=None) is None


def test_the_write_guard_leaves_a_move_to_the_move_lane():
    from types import SimpleNamespace

    from core.agent_runtime.fast_paths_machine import maybe_handle_safe_machine_write_guard

    agent = SimpleNamespace(_fast_path_result=lambda **kw: kw)
    assert maybe_handle_safe_machine_write_guard(
        agent, "can you move the cat photo from Pictures to my Desktop?", session_id="s", source_surface="channel",
        source_context={},
    ) is None


# Review 2026-10-28 (independent pass over this lane).
@pytest.mark.parametrize("text", [
    "let's move on to the documents", "did you move the cat photo to my desktop?", "why did you move it to my desktop",
    "move the cat photo to my desktop and then email it to bob", "don't move the cat photo to my desktop",
    "move on to my documents",
])
def test_questions_and_mixed_asks_are_not_an_everyday_move(text):
    intent = parse_operator_action_intent(text)
    assert intent is None or intent.kind != "move_path"


def test_all_or_plural_never_picks_one_file_and_words_are_whole(home):
    (home / "Desktop" / "allison_headshot.png").write_bytes(b"\x89PNG a")
    (home / "Desktop" / "Screenshot 2026-10-01.png").write_bytes(b"\x89PNG s")
    assert "one file at a time" in resolve_everyday_move("move all screenshots from desktop to documents")
    assert "couldn't find" in resolve_everyday_move("move the son photo from desktop to documents")  # not allison


def test_a_new_move_never_runs_an_older_pending_one(home, monkeypatch):
    from core.operator import handlers
    from core.operator.models import OperatorActionIntent

    older = {"action_id": "A", "scope_json": '{"source_path": "x", "destination_dir": "y", "destination_path": "z"}'}
    seen = {}

    def load(*, session_id, action_kind, action_id=None):
        return older if action_id == "A" else None

    def create(**kwargs):
        seen["scope"] = kwargs["scope"]
        return "B"

    result = handlers.handle_move_path(
        OperatorActionIntent(kind="move_path", approval_requested=True,
                             raw_text="please move the cat photo from pictures to my desktop, go ahead"),
        task_id="t", session_id="chat", load_pending_action_fn=load,
        parse_move_request_fn=lambda *a, **k: None, validate_move_scope_fn=lambda *a: None,
        resolved_move_target_fn=lambda source, dest: dest / source.name, create_pending_action_fn=create,
        mark_action_executed_fn=lambda *a, **k: None, evaluate_local_action_fn=lambda *a, **k: None,
        move_fn=lambda *a: pytest.fail("nothing may move before its own preview"), audit_log_fn=lambda *a, **k: None,
        resolve_everyday_move_fn=lambda text: {"source_path": str(home / "Pictures" / "cat_on_sofa.png"),
                                               "destination_dir": str(home / "Desktop")},
    )
    assert result.status == "approval_required" and "approve move B" in result.response_text
    assert seen["scope"]["source_path"].endswith("cat_on_sofa.png")


@pytest.mark.parametrize("words", ["yes?", "ok?"])
def test_a_question_never_approves(pending, words):
    action_id = "0f0e8d1c-1111-2222-3333-444455556666"
    pending[("chat", "move_path", action_id)] = {"action_id": action_id, "created_at": datetime.now(timezone.utc).isoformat()}
    assert approval_the_answer_offered(words, session_id="chat", source_context={"client_conversation_history": _preview(action_id)}) is None


def test_the_preview_must_be_the_last_message(pending):
    action_id = "0f0e8d1c-1111-2222-3333-444455556666"
    pending[("chat", "move_path", action_id)] = {"action_id": action_id, "created_at": datetime.now(timezone.utc).isoformat()}
    with_reply = [*_preview(action_id), {"role": "user", "content": "actually the dog one"}]
    assert approval_the_answer_offered("yes", session_id="chat", source_context={"client_conversation_history": with_reply}) is None
    echoed = [*_preview(action_id), {"role": "user", "content": "yes"}]
    assert approval_the_answer_offered("yes", session_id="chat", source_context={"client_conversation_history": echoed}) is not None


def test_the_same_move_asked_again_keeps_its_waiting_preview_and_runs_nothing(home):
    from core.operator import handlers
    from core.operator.models import OperatorActionIntent

    source, desktop = home / "Pictures" / "cat_on_sofa.png", home / "Desktop"
    waiting = {"action_id": "A", "scope_json": json.dumps({"source_path": str(source), "destination_dir": str(desktop),
                                                           "destination_path": str(desktop / source.name)})}

    def load(*, session_id, action_kind, action_id=None):
        return waiting if action_id in (None, "A") else None

    result = handlers.handle_move_path(
        OperatorActionIntent(kind="move_path", approval_requested=True,
                             raw_text="yes, move the cat photo from pictures to my desktop tomorrow"),
        task_id="t", session_id="chat", load_pending_action_fn=load,
        parse_move_request_fn=lambda *a, **k: None, validate_move_scope_fn=lambda *a: None,
        resolved_move_target_fn=lambda src, dest: dest / src.name,
        create_pending_action_fn=lambda **k: pytest.fail("the waiting preview must not be replaced"),
        mark_action_executed_fn=lambda *a, **k: None, evaluate_local_action_fn=lambda *a, **k: None,
        move_fn=lambda *a: pytest.fail("a request without its approval id never runs a move"), audit_log_fn=lambda *a, **k: None,
        resolve_everyday_move_fn=lambda text: {"source_path": str(source), "destination_dir": str(desktop)},
    )
    assert result.status == "approval_required" and result.response_text.endswith("approve move A")
