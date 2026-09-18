"""The exact coding task keeps its exact scope (mission 2026-09-18, Priority 3).

Original failure, reproduced from the operator transcript: "Create a new folder named xxx …
Inside it, create bot.py, requirements.txt, README.md. Do not build the bot yet." produced
xxx/src/bot.py, an extra .env.example, an unsolicited compile, and repeated approval loops.
Root cause chain pinned here at its owners: backtick-wrapped filenames were invisible to the
plan reader, a colon-enumerated file list carried no bound this module recognized, and the
Telegram platform probe outranked the request's own bounded plan in controller_profile.
"""
from core.agent_runtime.builder.mutation_scope import resolve_mutation_scope
from core.agent_runtime.builder.small_project_plan import (
    bounds_its_file_set,
    enumerated_files,
    resolve_small_project_plan,
)

SCAFFOLD = """Create a new folder named `xxx` in the current workspace.

We will use this folder for a small Telegram bot coding test.

Inside it, create:
- `bot.py`
- `requirements.txt`
- `README.md`

Do not build the bot yet. Just create the folder and files, then report exactly what was created."""

MATERIALLY_DIFFERENT = """Make a folder called notifier for a Discord webhook helper.

Files to create:
- `main.py`
- `notes.md`

Don't implement anything yet — empty scaffolding only, nothing else."""


def test_backtick_wrapped_filenames_are_visible_to_the_plan():
    assert enumerated_files(SCAFFOLD) == ["bot.py", "requirements.txt", "README.md"]


def test_colon_enumerated_file_list_bounds_the_file_set():
    assert bounds_its_file_set(SCAFFOLD) is True
    assert bounds_its_file_set(MATERIALLY_DIFFERENT) is True


def test_scaffold_plan_preserves_requested_paths_and_forbids_commands():
    plan = resolve_small_project_plan(SCAFFOLD, workspace_root="/tmp")
    assert plan is not None
    assert plan.root == "xxx"
    assert list(plan.files) == ["xxx/bot.py", "xxx/requirements.txt", "xxx/README.md"]
    # "Do not build the bot yet" withdrew nothing to run: no compile, no test run.
    assert plan.allow_commands is False


def test_mutation_scope_is_exact_with_only_the_requested_files():
    scope = resolve_mutation_scope(SCAFFOLD, workspace_root="/tmp")
    assert scope.is_exact
    data = scope.as_dict()
    assert data["authorized_paths"] == ["xxx/bot.py", "xxx/requirements.txt", "xxx/README.md"]
    assert data["allow_commands"] is False


def test_materially_different_scaffold_resolves_the_same_way():
    plan = resolve_small_project_plan(MATERIALLY_DIFFERENT, workspace_root="/tmp")
    assert plan is not None
    assert plan.root == "notifier"
    assert list(plan.files) == ["notifier/main.py", "notifier/notes.md"]
    assert plan.allow_commands is False


def test_a_platform_word_alone_does_not_steal_a_bounded_request_from_the_plan():
    """The controller must not answer an enumerated scaffold with the platform application
    template. The routing decision is stub-level here: the plan the platform shortcut must
    yield to is the one this test pins, for the same verbatim request."""
    plan = resolve_small_project_plan(SCAFFOLD, workspace_root="/tmp")
    assert plan is not None and "telegram" in SCAFFOLD.lower()
    # The exact-scope authority agrees with the plan for the same text: whatever lane runs,
    # its authorized mutation set is the three requested files under xxx/, never src/ or
    # .env.example.
    scope = resolve_mutation_scope(SCAFFOLD, workspace_root="/tmp")
    assert all(path.startswith("xxx/") for path in scope.as_dict()["authorized_paths"])
    assert not any("env" in path or "src/" in path for path in scope.as_dict()["authorized_paths"])


def test_a_genuine_platform_scaffold_without_a_file_list_stays_unbounded():
    """Control: "build me a telegram bot that posts weather" names no files; the plan reader
    must leave it to the platform scaffold lane exactly as before."""
    request = "Build me a telegram bot that posts the weather every morning."
    assert resolve_small_project_plan(request, workspace_root="/tmp") is None
    assert enumerated_files(request) == []
