"""Changes in the clean loop: rehearsed on a copy, shown as a card, applied through VOOL's own machine
tools on the owner's yes, and put back byte for byte on "undo"."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core import os_consent_gate
from core.clean_loop import changes, route
from core.clean_loop.registry import Registry, ToolContext
from core.clean_loop.tools import CARD_NOTICE, install_builtin_tools

SESSION = "s-changes"
OWNER = {"surface": "cli", "session_id": SESSION}


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    root = (tmp_path / "home").resolve()
    for name in ("Desktop", "Downloads", "Documents", "Pictures", "Library/Caches"):
        (root / name).mkdir(parents=True)
    for i in range(3):
        (root / "Desktop" / f"Screenshot 2026-10-2{i}.png").write_bytes(b"png%d" % i)
    (root / "Desktop" / "todo.txt").write_text("call the plumber\n", encoding="utf-8")
    (root / "Library" / "Caches" / "big.bin").write_bytes(b"x" * 1024)
    monkeypatch.setenv("HOME", str(root))
    changes.reset_for_tests()
    os_consent_gate.set_consent_override_for_tests(lambda reason: True)
    from core import mode_permission_policy as modes

    modes.reset_mode_permission_state()
    modes.set_active_mode(SESSION, "auto")
    yield root
    os_consent_gate.set_consent_override_for_tests(None)
    modes.reset_mode_permission_state()


def _snapshot(root: Path) -> dict[str, bytes | None]:
    return {
        p.relative_to(root).as_posix(): (p.read_bytes() if p.is_file() else None)
        for p in sorted(root.rglob("*"))
    }


def _call(home: Path, *, real: bool = False):
    """The clean loop's own `_call`; with real=False the boundary is a stand-in that makes the change
    on disk and approves it, so these tests measure the transaction, not the permission mode."""
    from core.clean_loop.tools import _call as boundary_call

    ctx = ToolContext(home=home, source_context=dict(OWNER), execute=None if real else _approving_boundary)

    def call(intent: str, arguments: dict[str, Any]):
        return boundary_call(ctx, intent, arguments)

    call.ctx = ctx  # type: ignore[attr-defined]
    return call


def _approving_boundary(intent, arguments, **_kwargs):
    from core.runtime_execution_tools import RuntimeExecutionResult

    if intent == "machine.ensure_directory":
        Path(arguments["path"]).mkdir(parents=True, exist_ok=True)
    elif intent == "machine.write_file":
        Path(arguments["path"]).parent.mkdir(parents=True, exist_ok=True)
        with open(arguments["path"], "x" if arguments.get("create_only") else "w", encoding="utf-8") as handle:
            handle.write(arguments["content"])
    elif intent == "machine.move_path":
        if not os_consent_gate.require_os_user_consent("move"):
            return RuntimeExecutionResult(handled=True, ok=False, status="consent_declined", response_text="declined")
        Path(arguments["source"]).rename(arguments["destination"])
    return RuntimeExecutionResult(handled=True, ok=True, status="executed", response_text="ok")


TIDY = [
    {"op": "mkdir", "path": "Desktop/Screenshots"},
    {"op": "move", "src": "Desktop/Screenshot*.png", "dst": "Desktop/Screenshots/"},
]


def test_a_preview_shows_the_change_and_touches_nothing(home):
    before = _snapshot(home)
    plan = changes.preview(home, TIDY)
    assert _snapshot(home) == before
    assert plan.roots == ["Desktop"], "only the folders the change touches are copied and checked"
    assert plan.lines[0] == "new folder: Desktop/Screenshots"
    assert "move: Desktop/Screenshot 2026-10-20.png -> Desktop/Screenshots/Screenshot 2026-10-20.png" in plan.lines
    assert len([line for line in plan.lines if line.startswith("move:")]) == 3


@pytest.mark.parametrize(
    "ops",
    [
        [{"op": "move", "src": "Pictures/cat.png", "dst": "Desktop/cat.png"}],
        [{"op": "mkdir", "path": "Library/Thing"}],
        [{"op": "write", "path": "../outside.txt", "text": "x"}],
        [{"op": "move", "src": "Desktop/todo.txt", "dst": "/etc/todo.txt"}],
        [{"op": "delete", "path": "Desktop/todo.txt"}],
        [{"op": "mkdir", "path": "Desktop"}],
        [{"op": "write", "path": "Desktop/todo.txt", "text": "overwrite"}],
    ],
)
def test_a_change_outside_the_write_folders_or_a_delete_is_refused(home, ops):
    before = _snapshot(home)
    with pytest.raises(changes.ChangeRefused):
        changes.preview(home, ops)
    assert _snapshot(home) == before


def test_a_link_cannot_carry_a_change_out_of_the_write_folders(home, tmp_path):
    (tmp_path / "outside").mkdir()
    (home / "Desktop" / "escape").symlink_to(tmp_path / "outside")
    with pytest.raises(changes.ChangeRefused):
        changes.preview(home, [{"op": "write", "path": "Desktop/escape/x.txt", "text": "x"}])


def test_apply_makes_exactly_the_previewed_change_and_undo_puts_it_back(home):
    before = _snapshot(home)
    plan = changes.preview(home, TIDY)
    call = _call(home)
    done = changes.apply(plan, call)
    assert done.matches_preview
    assert sorted(p.name for p in (home / "Desktop" / "Screenshots").iterdir()) == [
        "Screenshot 2026-10-20.png", "Screenshot 2026-10-21.png", "Screenshot 2026-10-22.png"
    ]
    intents = [r["tool_name"] for r in call.ctx.receipts]
    assert set(intents) == {"machine.ensure_directory", "machine.move_path"}, intents
    assert changes.undo(done, call) == []
    assert _snapshot(home) == before


def test_apply_refuses_when_the_folder_changed_since_the_card(home):
    plan = changes.preview(home, TIDY)
    (home / "Desktop" / "new.txt").write_text("new", encoding="utf-8")
    with pytest.raises(changes.ChangeRefused, match="changed since"):
        changes.apply(plan, _call(home))
    assert not (home / "Desktop" / "Screenshots").exists()


def test_a_failed_step_puts_back_what_already_happened(home):
    before = _snapshot(home)
    plan = changes.preview(home, TIDY)
    asked: list[str] = []

    def consent(reason: str) -> bool:
        asked.append(reason)
        return len(asked) != 3  # the third move is declined; putting the first two back is allowed

    os_consent_gate.set_consent_override_for_tests(consent)
    with pytest.raises(changes.ChangeRefused, match="put everything back"):
        changes.apply(plan, _call(home))
    os_consent_gate.set_consent_override_for_tests(lambda reason: True)
    assert _snapshot(home) == before


def test_a_new_file_is_written_and_undo_removes_it_only_if_unedited(home):
    plan = changes.preview(home, [{"op": "write", "path": "Documents/Lists/shopping.txt", "text": "milk\nbread\n"}])
    assert plan.lines == ['new file: Documents/Lists/shopping.txt (11 bytes): "milk / bread"', "new folder: Documents/Lists"]
    call = _call(home)
    done = changes.apply(plan, call)
    assert (home / "Documents" / "Lists" / "shopping.txt").read_text(encoding="utf-8").startswith("milk")
    assert changes.undo(done, call) == []
    assert not (home / "Documents" / "Lists").exists()

    plan = changes.preview(home, [{"op": "write", "path": "Documents/notes.txt", "text": "a\n"}])
    done = changes.apply(plan, call)
    (home / "Documents" / "notes.txt").write_text("edited by the owner\n", encoding="utf-8")
    problems = changes.undo(done, call)
    assert problems and "edited after I wrote it" in problems[0]
    assert (home / "Documents" / "notes.txt").read_text(encoding="utf-8") == "edited by the owner\n"


def test_undo_never_overwrites_something_new(home):
    plan = changes.preview(home, [{"op": "move", "src": "Desktop/todo.txt", "dst": "Documents/todo.txt"}])
    call = _call(home)
    done = changes.apply(plan, call)
    (home / "Desktop" / "todo.txt").write_text("a new todo\n", encoding="utf-8")
    problems = changes.undo(done, call)
    assert problems and "something new" in problems[0]
    assert (home / "Desktop" / "todo.txt").read_text(encoding="utf-8") == "a new todo\n"
    assert (home / "Documents" / "todo.txt").exists()


@pytest.mark.parametrize(
    ("text", "verdict"),
    [
        ("yes", "yes"), ("yes please", "yes"), ("ok do it", "yes"), ("go ahead", "yes"),
        ("no", "no"), ("leave it", "no"),
        ("undo", "undo"), ("put them back", "undo"), ("undo that please", "undo"), ("revert it", "undo"),
        ("yes and also move my invoices to documents", ""), ("what is in my downloads", ""),
        ("ok, but not the pdfs", ""), ("yes?", ""), ("yes but leave the newest one", ""), ("ok, do it", "yes"),
        ("Yes please!", "yes"), ("no, leave it", "no"), ("do it except the pdfs", ""),
    ],
)
def test_change_replies(text, verdict):
    assert route.change_reply(text) == verdict


def test_write_asks_come_in_only_with_the_changes_flag():
    on = {route.FLAG_ENV: "1"}
    ask = "tidy my screenshots on the desktop into a folder"
    resolve = lambda: object()  # noqa: E731
    assert route.decide(ask, source_context=OWNER, resolve_brain=resolve, env=on).reason == "write_turn"
    both = {**on, route.CHANGES_ENV: "1"}
    assert route.decide(ask, source_context=OWNER, resolve_brain=resolve, env=both).admit
    assert route.decide("delete my screenshots", source_context=OWNER, resolve_brain=resolve, env=both).reason == "red_turn"


def test_an_amount_alone_is_not_red():
    assert not route.is_red("find the €129 plumber invoice")
    assert route.is_red("send €50 to anna")


# --- the whole flow through the hook --------------------------------------------------------


class _Agent:
    memory_router = object()

    def __init__(self):
        self.events: list[dict[str, Any]] = []

    def _emit_runtime_event(self, source_context, *, event_type, message, **details):
        self.events.append({"event_type": event_type, **details})

    def _fast_path_result(self, **kwargs):
        return {"response": kwargs["response"], "route_reason": kwargs["reason"], "route_skips": []}


def test_tidy_then_yes_then_put_them_back(home, monkeypatch):
    from core.clean_loop import brain as brain_module
    from core.clean_loop import hook

    monkeypatch.setenv(route.FLAG_ENV, "1")
    monkeypatch.setenv(route.CHANGES_ENV, "1")
    monkeypatch.setattr(brain_module, "resolve_local_manifest", lambda router, ctx: "manifest")
    monkeypatch.setattr(brain_module, "model_label", lambda manifest: ("ollama-local", "qwen3:8b"))
    replies = iter(
        [
            '{"tool": "list_dir", "path": "Desktop"}',
            '{"tool": "propose_change", "what": "tidy screenshots", "ops": [{"op": "mkdir", "path": "Desktop/Screenshots"}, '
            '{"op": "move", "src": "Desktop/Screenshot*", "dst": "Desktop/Screenshots/"}]}',
            '{"answer": "I can put your 3 screenshots in a Screenshots folder."}',
        ]
    )
    monkeypatch.setattr(brain_module, "make_brain", lambda router, manifest, ctx: (lambda messages: next(replies)))
    from core import authorized_tool_execution

    real_execute = authorized_tool_execution.execute_authorized_runtime_tool

    def execute(intent, arguments, **kwargs):
        if intent.startswith("machine.") and intent in {"machine.ensure_directory", "machine.move_path", "machine.write_file"}:
            return _approving_boundary(intent, arguments)
        return real_execute(intent, arguments, **kwargs)

    monkeypatch.setattr(authorized_tool_execution, "execute_authorized_runtime_tool", execute)
    before = _snapshot(home)
    agent = _Agent()
    card = hook.maybe_answer(agent, text="tidy the screenshots on my desktop", effective_input="tidy",
                             session_id=SESSION, source_context=dict(OWNER))
    assert card is not None and "Here is what I will do:" in card["response"] and CARD_NOTICE in card["response"]
    assert _snapshot(home) == before, "the card changes nothing"

    done = hook.maybe_answer(agent, text="yes please", effective_input="yes please", session_id=SESSION,
                             source_context=dict(OWNER))
    assert done is not None and done["response"].startswith("Done: 4 changes, exactly as shown.")
    assert len(list((home / "Desktop" / "Screenshots").iterdir())) == 3

    back = hook.maybe_answer(agent, text="put them back", effective_input="put them back", session_id=SESSION,
                             source_context=dict(OWNER))
    assert back is not None and back["response"] == "Put back: everything is where it was before."
    assert _snapshot(home) == before
    again = hook.maybe_answer(agent, text="yes", effective_input="yes", session_id=SESSION, source_context=dict(OWNER))
    assert again is None or "Done" not in again["response"], "a used card is never applied twice"


def test_no_leaves_everything_as_it_was(home, monkeypatch):
    from core.clean_loop import hook

    monkeypatch.setenv(route.FLAG_ENV, "1")
    monkeypatch.setenv(route.CHANGES_ENV, "1")
    before = _snapshot(home)
    changes.remember_pending(SESSION, changes.preview(home, TIDY))
    result = hook.maybe_answer(_Agent(), text="no", effective_input="no", session_id=SESSION, source_context=dict(OWNER))
    assert result is not None and result["response"] == "Okay, I left everything as it was."
    assert not changes.has_pending(SESSION) and _snapshot(home) == before


def test_the_real_boundary_makes_folders_and_files_in_auto(home):
    plan = changes.preview(home, [{"op": "write", "path": "Documents/Lists/shopping.txt", "text": "milk\n"}])
    call = _call(home, real=True)
    done = changes.apply(plan, call)
    assert done.matches_preview and (home / "Documents" / "Lists" / "shopping.txt").exists()
    assert {r["tool_name"] for r in call.ctx.receipts} == {"machine.write_file"}
    assert changes.undo(done, call) == [] and not (home / "Documents" / "Lists").exists()


def test_a_move_keeps_vools_own_approval_and_the_card(home, monkeypatch):
    # Moves want VOOL's own approval for each step, even in Auto: the card's yes never stands in for it.
    from core.clean_loop import hook

    monkeypatch.setenv(route.FLAG_ENV, "1")
    monkeypatch.setenv(route.CHANGES_ENV, "1")
    before = _snapshot(home)
    changes.remember_pending(SESSION, changes.preview(home, TIDY))
    result = hook.maybe_answer(_Agent(), text="yes", effective_input="yes", session_id=SESSION, source_context=dict(OWNER))
    assert result is not None and "approve each file move one by one" in result["response"]
    assert _snapshot(home) == before
    assert changes.has_pending(SESSION), "the change is still ready"


def test_a_failed_put_back_is_said_not_hidden(home):
    plan = changes.preview(home, TIDY)
    asked: list[str] = []

    def consent(reason: str) -> bool:
        asked.append(reason)
        return len(asked) < 3  # the third move and every put-back are declined

    os_consent_gate.set_consent_override_for_tests(consent)
    with pytest.raises(changes.ChangeRefused, match="could not put all of it back"):
        changes.apply(plan, _call(home))


# --- review findings, 2026-10-28 --------------------------------------------------------------


@pytest.mark.parametrize(
    "ops",
    [
        [{"op": "move", "src": "Desktop/todo.txt", "dst": "Desktop/escape/todo.txt"}],
        [{"op": "move", "src": "Desktop/todo.txt", "dst": "Desktop/escape/"}],
        [{"op": "mkdir", "path": "Desktop/escape/new"}],
        [{"op": "move", "src": "Desktop/escape", "dst": "Documents/escape"}],
    ],
)
def test_no_step_goes_through_a_link(home, tmp_path, ops):
    (tmp_path / "outside").mkdir()
    (home / "Desktop" / "escape").symlink_to(tmp_path / "outside")
    with pytest.raises(changes.ChangeRefused, match="link"):
        changes.preview(home, ops)
    assert list((tmp_path / "outside").iterdir()) == []


def test_a_write_folder_that_is_a_link_is_refused(home, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    (home / "Downloads").rmdir()
    (home / "Downloads").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(changes.ChangeRefused, match="link"):
        changes.preview(home, [{"op": "write", "path": "Downloads/x.txt", "text": "x"}])


def test_a_pattern_never_picks_up_a_link(home, tmp_path):
    (tmp_path / "secret.png").write_bytes(b"secret")
    (home / "Desktop" / "Screenshot link.png").symlink_to(tmp_path / "secret.png")
    plan = changes.preview(home, TIDY)
    assert not any("link" in line for line in plan.lines)
    assert len([line for line in plan.lines if line.startswith("move:")]) == 3


def test_the_preview_writes_nothing_anywhere(home, tmp_path):
    before = {p for p in tmp_path.rglob("*")}
    changes.preview(home, TIDY + [{"op": "write", "path": "Documents/a/b.txt", "text": "x"}])
    assert {p for p in tmp_path.rglob("*")} == before


def test_a_file_saved_after_the_card_is_never_overwritten(home):
    plan = changes.preview(home, [{"op": "write", "path": "Documents/notes.txt", "text": "from the card\n"}])
    (home / "Documents" / "notes.txt").write_text("the owner's own\n", encoding="utf-8")
    with pytest.raises(changes.ChangeRefused):
        changes.apply(plan, _call(home))
    assert (home / "Documents" / "notes.txt").read_text(encoding="utf-8") == "the owner's own\n"


def test_the_write_tool_itself_refuses_to_replace_with_create_only(home):
    (home / "Documents" / "notes.txt").write_text("the owner's own\n", encoding="utf-8")
    call = _call(home, real=True)
    outcome = call("machine.write_file", {"path": str(home / "Documents" / "notes.txt"), "content": "x", "create_only": True})
    assert not outcome.ok
    assert (home / "Documents" / "notes.txt").read_text(encoding="utf-8") == "the owner's own\n"


def test_an_undo_that_was_blocked_can_be_asked_again(home):
    before = _snapshot(home)
    plan = changes.preview(home, TIDY)
    call = _call(home)
    done = changes.apply(plan, call)
    blocker = home / "Desktop" / "Screenshot 2026-10-21.png"
    blocker.write_bytes(b"a new one")
    problems = changes.undo(done, call)
    assert problems and "something new" in problems[0]
    assert not done.undone and len(done.inverse) == 2, "the blocked move and the folder it sits in"
    blocker.unlink()
    assert changes.undo(done, call) == []
    assert done.undone and _snapshot(home) == before


def test_undo_does_not_follow_a_link_swapped_in_for_a_written_file(home, tmp_path):
    keep = tmp_path / "keep.txt"
    keep.write_text("milk\n", encoding="utf-8")
    plan = changes.preview(home, [{"op": "write", "path": "Documents/shopping.txt", "text": "milk\n"}])
    call = _call(home)
    done = changes.apply(plan, call)
    (home / "Documents" / "shopping.txt").unlink()
    (home / "Documents" / "shopping.txt").symlink_to(keep)
    assert changes.undo(done, call)
    assert keep.read_text(encoding="utf-8") == "milk\n" and not done.undone


def test_the_card_shows_what_a_new_file_will_say(home):
    plan = changes.preview(home, [{"op": "write", "path": "Desktop/list.txt", "text": "a" * 400}])
    assert plan.lines[0].startswith('new file: Desktop/list.txt (400 bytes): "aaa') and plan.lines[0].endswith('..."')


def test_without_the_changes_flag_no_card_is_made(home, monkeypatch):
    from core.clean_loop.tools import propose_change

    monkeypatch.delenv(route.CHANGES_ENV, raising=False)
    ctx = ToolContext(home=home, source_context=dict(OWNER))
    propose_change({"what": "tidy", "ops": TIDY}, ctx)
    assert ctx.plans == []
    monkeypatch.setenv(route.CHANGES_ENV, "1")
    propose_change({"what": "tidy", "ops": TIDY}, ctx)
    assert len(ctx.plans) == 1


def _with_card(home, monkeypatch):
    from core.clean_loop import brain as brain_module

    monkeypatch.setenv(route.FLAG_ENV, "1")
    monkeypatch.setenv(route.CHANGES_ENV, "1")
    monkeypatch.setattr(brain_module, "resolve_local_manifest", lambda router, ctx: None)
    changes.remember_pending(SESSION, changes.preview(home, [{"op": "mkdir", "path": "Desktop/Screenshots"}]))


def test_a_card_answers_only_the_next_turn(home, monkeypatch):
    from core.clean_loop import hook

    _with_card(home, monkeypatch)
    other = hook.maybe_answer(_Agent(), text="how big is my desktop folder", effective_input="x", session_id=SESSION,
                              source_context=dict(OWNER))
    assert other is None and not changes.has_pending(SESSION)
    late = hook.maybe_answer(_Agent(), text="yes", effective_input="yes", session_id=SESSION, source_context=dict(OWNER))
    assert late is None or "Done" not in late["response"]
    assert not (home / "Desktop" / "Screenshots").exists()


def test_a_conditional_yes_is_not_an_approval(home, monkeypatch):
    from core.clean_loop import hook

    _with_card(home, monkeypatch)
    result = hook.maybe_answer(_Agent(), text="ok, but not the pdfs", effective_input="x", session_id=SESSION,
                               source_context=dict(OWNER))
    assert result is None or "Done" not in result["response"]
    assert not (home / "Desktop" / "Screenshots").exists() and not changes.has_pending(SESSION)


def test_a_yes_from_a_turn_the_owner_does_not_own_applies_nothing(home, monkeypatch):
    from core.clean_loop import hook

    _with_card(home, monkeypatch)
    result = hook.maybe_answer(_Agent(), text="yes", effective_input="yes", session_id=SESSION,
                               source_context={**OWNER, "_trusted_project_id": "p1"})
    assert result is None
    assert not (home / "Desktop" / "Screenshots").exists() and not changes.has_pending(SESSION)


def test_without_the_changes_flag_a_yes_is_an_ordinary_turn(home, monkeypatch):
    from core.clean_loop import hook

    _with_card(home, monkeypatch)
    monkeypatch.delenv(route.CHANGES_ENV)
    result = hook.maybe_answer(_Agent(), text="yes", effective_input="yes", session_id=SESSION, source_context=dict(OWNER))
    assert result is None and not (home / "Desktop" / "Screenshots").exists()


# --- second review, 2026-10-28 -----------------------------------------------------------------


def test_a_yes_on_a_browser_turn_applies_nothing(home, monkeypatch):
    from core.clean_loop import hook

    _with_card(home, monkeypatch)
    result = hook.maybe_answer(_Agent(), text="ok", effective_input="ok", session_id=SESSION,
                               source_context=dict(OWNER), browser_turn=True)
    assert result is None and not (home / "Desktop" / "Screenshots").exists() and not changes.has_pending(SESSION)


def test_undo_after_other_turns_asks_first(home, monkeypatch):
    from core.clean_loop import hook

    _with_card(home, monkeypatch)
    from core.clean_loop import hook as hook_module

    monkeypatch.setattr(hook_module, "_boundary_call", lambda ctx: _call(home))
    done = hook.maybe_answer(_Agent(), text="yes", effective_input="yes", session_id=SESSION, source_context=dict(OWNER))
    assert done is not None and done["response"].startswith("Done")
    hook.maybe_answer(_Agent(), text="how big is my desktop folder", effective_input="x", session_id=SESSION,
                      source_context=dict(OWNER))
    ask = hook.maybe_answer(_Agent(), text="undo", effective_input="undo", session_id=SESSION, source_context=dict(OWNER))
    assert ask is not None and "Say yes to undo it" in ask["response"]
    assert (home / "Desktop" / "Screenshots").exists(), "asking changes nothing"
    back = hook.maybe_answer(_Agent(), text="yes", effective_input="yes", session_id=SESSION, source_context=dict(OWNER))
    assert back is not None and back["response"].startswith("Put back")
    assert not (home / "Desktop" / "Screenshots").exists()


def test_undo_right_after_the_change_needs_no_question(home, monkeypatch):
    from core.clean_loop import hook
    from core.clean_loop import hook as hook_module

    _with_card(home, monkeypatch)
    monkeypatch.setattr(hook_module, "_boundary_call", lambda ctx: _call(home))
    hook.maybe_answer(_Agent(), text="yes", effective_input="yes", session_id=SESSION, source_context=dict(OWNER))
    back = hook.maybe_answer(_Agent(), text="undo", effective_input="undo", session_id=SESSION, source_context=dict(OWNER))
    assert back is not None and back["response"].startswith("Put back")
    assert any(r["tool_name"] == "clean_loop.change_put_back" for r in back["tool_receipts"]), "removals are receipts"


@pytest.mark.parametrize("name", ["Open me.command", "run.sh", "Desktop helper.app", "link.webloc"])
def test_a_change_never_writes_a_file_that_runs_when_opened(home, name):
    with pytest.raises(changes.ChangeRefused, match="runs when opened"):
        changes.preview(home, [{"op": "write", "path": f"Desktop/{name}", "text": "x"}])


def test_a_long_card_never_hides_a_new_file(home):
    for i in range(50):
        (home / "Downloads" / f"photo {i:02d}.jpg").write_bytes(b"x")
    plan = changes.preview(home, [
        {"op": "move", "src": "Downloads/photo*.jpg", "dst": "Documents/Photos/"},
        {"op": "write", "path": "Documents/Photos/readme.txt", "text": "sorted photos"},
    ])
    assert plan.lines[0].startswith("new file: Documents/Photos/readme.txt")
    assert plan.lines[1] == "new folder: Documents/Photos"
    assert plan.lines[-1] == "...and 20 more moves into the same folders"


def test_a_tool_fault_mid_change_still_puts_back_what_happened(home):
    before = _snapshot(home)
    plan = changes.preview(home, TIDY)
    inner = _call(home)
    moves: list[int] = []

    def call(intent, arguments):
        if intent == "machine.move_path":
            moves.append(1)
            if len(moves) == 2:
                raise RuntimeError("disk went away")
        return inner(intent, arguments)

    with pytest.raises(changes.ChangeRefused, match="put everything back"):
        changes.apply(plan, call)
    assert _snapshot(home) == before


def test_a_move_parked_for_approval_is_withdrawn_not_left_queued(home, monkeypatch):
    from core import mode_permission_policy as modes
    from core.clean_loop import hook

    monkeypatch.setenv(route.FLAG_ENV, "1")
    monkeypatch.setenv(route.CHANGES_ENV, "1")
    changes.remember_pending(SESSION, changes.preview(home, TIDY))
    result = hook.maybe_answer(_Agent(), text="yes", effective_input="yes", session_id=SESSION, source_context=dict(OWNER))
    assert result is not None and "approve each file move one by one" in result["response"]
    modes._ensure_approvals_restored()
    with modes._LOCK:
        statuses = [str(a.get("status")) for a in modes._APPROVALS.values() if "move_path" in str(a.get("intent") or a)]
    assert statuses and "pending" not in statuses, statuses


def test_a_question_about_deleting_is_still_red():
    assert route.is_red("is it ok to delete everything in Downloads")
    assert not route.is_red("when do i have to pay the plumber by")


def test_the_privacy_question_is_answered_on_this_computer(home):
    from core.clean_loop.tools import privacy_pre_step

    on = {route.FLAG_ENV: "1"}
    decision = route.decide("how much of my stuff went to online AI today", source_context=OWNER,
                            resolve_brain=lambda: object(), env=on)
    assert decision.admit
    ctx = ToolContext(home=home, source_context=dict(OWNER))
    assert "online AI models" in privacy_pre_step("how much of my stuff went to online AI today", ctx)
    assert privacy_pre_step("whats on my desktop", ctx) is None
