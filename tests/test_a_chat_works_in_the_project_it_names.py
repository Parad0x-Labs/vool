"""A chat that names its project works in that project, this turn and the next.

Measured on 1cbf433 (cloud tool re-audit, 2026-10-29, coding W07/W09/S1/B1): with
``~/Projects/garden-planner/plan.py`` on the machine, "show me what's in plan.py in the garden planner
project" answered "There is no file at `plan.py`, and no file of that name elsewhere in this project"
-- the turn ran in the default workspace, and the project the person named was never looked up.

Driven through the real chat door with only the model wire faked. The read itself is VOOL's own
workspace fast path; nothing here needs a model to answer.
"""

from __future__ import annotations

import json

import pytest

from core.named_project_binding import named_project_reference, resolve_named_project

_PLAN = "MONTHS = {'March': ['peas'], 'April': ['carrots', 'beets']}\n"
_WATERING = "DRY = 'a bit'\n\ndef water_needed(crop):\n    return DRY\n"


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "person"
    project = home / "Projects" / "garden-planner"
    project.mkdir(parents=True)
    (project / "plan.py").write_text(_PLAN)
    (project / "watering.py").write_text(_WATERING)
    (home / "Documents").mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def say(home, monkeypatch, tmp_path):
    from tests.gauntlet import harness

    harness.install(monkeypatch)
    default_workspace = tmp_path / "default-workspace"
    default_workspace.mkdir()

    def _say(text: str, *, session: str = "garden-chat", client_host: str = "127.0.0.1") -> str:
        from core.web.api.service import dispatch_post

        res = dispatch_post(
            path="/api/chat",
            body={"stream": False, "session_id": session, "turn_id": f"t-{abs(hash((session, text)))}",
                  "messages": [{"role": "user", "content": text}]},
            headers={},
            runtime=harness.runtime(),
            model_name="gauntlet",
            workspace_root_provider=lambda: str(default_workspace),
            client_host=client_host,
        )
        assert res.status == 200, res.body[:400]
        return str((json.loads(res.body.decode("utf-8")).get("message") or {}).get("content") or "")

    return _say


@pytest.mark.parametrize(
    "text",
    [
        "show me what's in plan.py in the garden planner project",
        "what files are in my garden planner project in the Projects folder?",
        "in the garden planner project, where do we plant carrots?",
        "i've got a little project called garden planner in my Projects folder, can you take a peek",
        "when i run watering.py in the garden-planner project i get an error about dry, can you fix it?",
    ],
)
def test_the_named_project_is_read_from_the_message(text) -> None:
    assert named_project_reference(text).replace("-", " ").lower() == "garden planner"


@pytest.mark.parametrize(
    "text",
    ["what is a project manager", "in this project, list files", "fix the bug in the same project",
     "start a new project for me", "read notes.txt in Documents"],
)
def test_a_message_that_names_no_project_names_none(text) -> None:
    assert named_project_reference(text) == ""


def test_the_folder_is_found_by_its_name_however_it_is_written(home) -> None:
    for spoken in ("garden planner", "Garden Planner", "garden_planner", "garden-planner"):
        found, reason = resolve_named_project(spoken, home=str(home))
        assert reason == "" and found is not None, spoken
        assert found.root == str((home / "Projects" / "garden-planner").resolve())


def test_two_folders_of_that_name_are_never_guessed_between(home) -> None:
    (home / "Documents" / "Garden Planner").mkdir()

    found, reason = resolve_named_project("garden planner", home=str(home))

    assert found is None and reason == "ambiguous"


def test_a_standard_folder_is_not_a_project(home) -> None:
    found, reason = resolve_named_project("documents", home=str(home))
    assert found is None and reason == "not_found"


def test_the_file_in_the_named_project_is_read_and_the_chat_stays_there(home, say) -> None:
    from core import project_store

    first = say("show me what's in plan.py in the garden planner project")

    assert "MONTHS" in first and "peas" in first, first
    assert "There is no file" not in first
    roots = [p["root"] for p in project_store.list_projects()]
    assert str((home / "Projects" / "garden-planner").resolve()) in roots

    # The follow-up names no project: it works where the chat now is.
    second = say("show me watering.py")
    assert "water_needed" in second, second


def test_a_chat_already_in_a_project_is_not_moved_by_naming_another(home, say, tmp_path) -> None:
    from core import project_store
    from core.chat_session_identity import canonical_chat_session_id
    from core.memory.entries import set_session_meta

    other = home / "Projects" / "recipe-box"
    other.mkdir()
    (other / "plan.py").write_text("RECIPES = ['soup']\n")
    ok, project = project_store.create_project("Recipe Box", str(other))
    assert ok
    say("hello", session="recipe-chat")
    set_session_meta(canonical_chat_session_id("recipe-chat"), project_id=project["id"])

    reply = say("show me what's in plan.py in the garden planner project", session="recipe-chat")

    assert "MONTHS" not in reply
    roots = [p["root"] for p in project_store.list_projects()]
    assert str((home / "Projects" / "garden-planner").resolve()) not in roots
    from core.persistent_memory import load_session_meta

    assert (load_session_meta().get(canonical_chat_session_id("recipe-chat")) or {}).get("project_id") == project["id"]


def test_a_turn_from_another_device_binds_nothing(home, say) -> None:
    from core import project_store

    seeds = home / "Projects" / "seed-tracker"
    seeds.mkdir()
    (seeds / "seeds.py").write_text("SEEDS = ['basil']\n")

    say("show me seeds.py in the seed tracker project", session="phone-chat", client_host="203.0.113.7")

    roots = [p["root"] for p in project_store.list_projects()]
    assert str(seeds.resolve()) not in roots
