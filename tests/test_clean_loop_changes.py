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
        Path(arguments["path"]).write_text(arguments["content"], encoding="utf-8")
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
    assert plan.lines == ["new folder: Documents/Lists", "new file: Documents/Lists/shopping.txt"]
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
