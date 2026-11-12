"""The everyday folder-create lane ("set up a folder called X on my desktop") stays in the person's own folders.

Security review of 446d89da: a Desktop that is a link elsewhere created the folder at the link's target, including
inside this runtime's own data directory; the preview's own line "approve folder <id>" approved nothing; and the
handler created a pending folder for any request that carried its id. Each case below is a real turn on the rig.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from tests._person_turn_rig import bind_person_home, make_agent, person_env, reply, turn  # noqa: F401

ASK = "please set up a folder named Projects on the desktop"
_ID = re.compile(r"approve folder ([0-9a-f]{8}-[0-9a-f-]{27,})")


def _link_desktop_to(env, target: Path) -> Path:
    desk = env["person_home"] / "Desktop"
    if desk.exists():
        os.rmdir(desk)
    desk.symlink_to(target, target_is_directory=True)
    return desk


def test_a_desktop_linked_into_runtime_state_creates_nothing_there(person_env, monkeypatch):
    env = person_env
    bind_person_home(monkeypatch, env)
    from core import runtime_paths

    state = Path(runtime_paths.active_data_dir())
    _link_desktop_to(env, state)
    agent = make_agent()
    first = reply(turn(agent, ASK))
    second = reply(turn(agent, "yes please"))
    assert not (state / "Projects").exists(), (first, second)
    assert "Folder ready" not in second, second


def test_a_desktop_linked_outside_the_home_creates_nothing_there(person_env, monkeypatch, tmp_path):
    env = person_env
    bind_person_home(monkeypatch, env)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    _link_desktop_to(env, outside)
    agent = make_agent()
    first = reply(turn(agent, ASK))
    second = reply(turn(agent, "yes please"))
    assert not (outside / "Projects").exists(), (first, second)
    assert "Folder ready" not in second, second


def test_a_name_that_is_already_a_link_is_refused(person_env, monkeypatch, tmp_path):
    env = person_env
    bind_person_home(monkeypatch, env)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    desk = env["person_home"] / "Desktop"
    desk.mkdir(exist_ok=True)
    (desk / "Projects").symlink_to(outside, target_is_directory=True)
    from core.local_operator_actions import _folder_target

    try:
        _folder_target("Desktop", "Projects")
    except ValueError as exc:
        assert "points elsewhere" in str(exc)
    else:
        raise AssertionError("a folder name that is a link elsewhere was accepted")


def test_a_desktop_linked_inside_the_home_still_works(person_env, monkeypatch):
    env = person_env
    bind_person_home(monkeypatch, env)
    real = env["person_home"] / "Synced Desk"
    real.mkdir()
    _link_desktop_to(env, real)
    agent = make_agent()
    reply(turn(agent, ASK))
    done = reply(turn(agent, "yes please"))
    assert (real / "Projects").is_dir(), done


def test_the_preview_line_typed_back_creates_the_folder(person_env, monkeypatch):
    env = person_env
    bind_person_home(monkeypatch, env)
    agent = make_agent()
    preview = reply(turn(agent, ASK))
    match = _ID.search(preview)
    assert match, preview
    done = reply(turn(agent, f"approve folder {match.group(1)}"))
    assert (env["person_home"] / "Desktop" / "Projects").is_dir(), done
    assert "Folder ready" in done, done


def test_a_withheld_preview_line_creates_nothing(person_env, monkeypatch):
    env = person_env
    bind_person_home(monkeypatch, env)
    agent = make_agent()
    preview = reply(turn(agent, ASK))
    action_id = _ID.search(preview).group(1)
    for said in (f"don't approve folder {action_id}", f"do not approve folder {action_id}", f"folder {action_id}",
                 f"what is folder {action_id}?", f"approve folder {action_id} tomorrow"):
        answer = reply(turn(agent, said))
        assert not (env["person_home"] / "Desktop" / "Projects").exists(), (said, answer)


def test_the_handler_needs_the_approval_itself():
    from core.operator.handlers import handle_make_directory
    from core.operator.parser import OperatorActionIntent

    pending = {"action_id": "a" * 8 + "-0000-0000-0000-000000000000", "scope_json": '{"target_path": "/x/Desktop/P"}'}

    class Gate:
        requires_user_approval = False
        mode = "execute"
        reason = ""

    def refuse(*_args, **_kwargs):
        raise AssertionError("resolved a folder without approval")

    result = handle_make_directory(
        OperatorActionIntent(kind="make_directory", action_id=pending["action_id"], approval_requested=False),
        task_id="t",
        session_id="s",
        load_pending_action_fn=lambda **_k: pending,
        create_pending_action_fn=refuse,
        mark_action_executed_fn=refuse,
        evaluate_local_action_fn=lambda *_a, **_k: Gate(),
        resolve_machine_directory_fn=refuse,
        home_label_fn=str,
        audit_log_fn=lambda *_a, **_k: None,
    )
    assert result.status == "approval_required", result
