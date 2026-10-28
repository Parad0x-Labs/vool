"""A chat binds the project folder the person names in words (core.workspace_from_words).

Every test builds a real temp home with real folders, pins the VOOL runtime to a temp
profile and re-derives every expectation from paths the test itself created. The words
below are this file's own set: they span space, dash, underscore and camelCase folder
names, container mentions ("in Projects", "on my Desktop", "under Documents") and
work-ask framings, plus the refusals that must bind nothing (ordinary chat and casual
mentions that stay silent, an idea that is not a folder ask, a folder that exists
nowhere, words with no name at all, several matches, the home's standard folders and
containers themselves, secret-named folders, a system tree inside the home, a link that
leaves the home, the home folder itself, a remote surface) and what a bound chat then
sees on its next turn.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from core import project_store, runtime_paths
from core.context_namespace import (
    authoritative_chat_workspace,
    list_context_imports,
    load_chat_namespace,
)
from core.workspace_from_words import (
    FolderWordsResolution,
    apply_workspace_words_binding,
    resolve_folder_from_words,
)


def _unique_chat(label: str) -> str:
    return f"chat:wf-{label}-{uuid.uuid4().hex[:10]}"


@pytest.fixture()
def person_home(tmp_path, monkeypatch):
    """A real temp home with the folders the wordings name, plus the traps.

    The VOOL runtime profile is a SEPARATE temp folder, so the product's own data never
    lands inside the person's home tree, and HOME is pointed at the temp home so nothing
    on the real machine is read through ``~``.
    """
    home = tmp_path / "home"
    folders = (
        # the one-folder-per-name set the positive wordings name
        "Projects/garden planner",
        # a longer near-name: the exact name must win over it, with a plain-words aside
        "Projects/garden planner 2",
        "Projects/plant catalog",
        # a secret word in the name: never bound, refused in plain words
        "Projects/seed catalog",
        "Code/recipe-app",
        "Code/BudgetTracker",
        "Documents/Notes Sync",
        "Documents/knitting patterns",
        # the generic-noun wording ("the tax 2025 stuff") names this folder
        "Documents/Tax 2025",
        "Desktop/trip_photos",
        "Developer",
        "renovations-2027",
        # the home's standard folders: never bound, refused in plain words
        "Downloads",
        "Music",
        "Pictures",
        "Movies",
        "Public",
        # a secret-named folder in the home: never bound
        "Passwords",
        # two sibling spellings of one name: the several-matches ask
        "Projects/invoice-tool",
        "Code/invoice_tool",
        # four sibling spellings of one name: the at-most-three listing cap
        "Desktop/archive-sorter",
        "Documents/archive_sorter",
        "Projects/ArchiveSorter",
        "Code/archive sorter",
        # a system tree inside the home: never searched, never bound
        "Library/sync keystore",
    )
    for relative in folders:
        (home / relative).mkdir(parents=True, exist_ok=True)
    # a file where only folders count
    (home / "reading list.txt").write_text("books\n", encoding="utf-8")
    # a link that leaves the home entirely: never matched, never bound
    outside = tmp_path / "outside pond"
    outside.mkdir()
    (home / "Projects" / "escape-hatch").symlink_to(outside)

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VOOL_HOME", str(runtime))
    runtime_paths.configure_runtime_home(runtime)
    try:
        yield home
    finally:
        runtime_paths.configure_runtime_home(None)


# ----------------------------------------------------------------------------------
# Work asks that must resolve to the one folder they name
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("words", "expected_relative"),
    [
        ("open my garden planner project", "Projects/garden planner"),
        ("look at the garden-planner thing in Projects", "Projects/garden planner"),
        ("work on my Projects/garden planner", "Projects/garden planner"),
        ("fix the recipe app in my Code folder", "Code/recipe-app"),
        ("review my BudgetTracker project in Code", "Code/BudgetTracker"),
        ("open my Notes Sync folder under Documents", "Documents/Notes Sync"),
        ("look at the trip_photos folder on my Desktop", "Desktop/trip_photos"),
        ("run the tests in my knitting patterns project in Documents", "Documents/knitting patterns"),
        ("change the plant catalog project", "Projects/plant catalog"),
        ("edit my renovations-2027 folder", "renovations-2027"),
    ],
)
def test_a_work_ask_resolves_to_the_one_folder_it_names(
    person_home, words, expected_relative
) -> None:
    resolution = resolve_folder_from_words(words, person_home)
    expected = os.path.realpath(person_home / expected_relative)
    assert isinstance(resolution, FolderWordsResolution)
    assert resolution.status == "found", (words, resolution)
    assert resolution.path == expected, (words, resolution.path)
    assert resolution.name == Path(expected_relative).name
    assert resolution.message == ""
    assert resolution.matches == ()


# ----------------------------------------------------------------------------------
# Ordinary chat never gets a notice and never binds
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "words",
    [
        # the reviewer's four measured sentences
        "why is the sky blue",
        "what's the weather in London",
        "the dog ate my homework",
        "my mom called me today",
        # this file's own ordinary chat
        "what should I make for dinner tonight",
        "who won the game last night",
        "how far away is the moon",
        "I finished that book yesterday",
        "the cat knocked over a plant",
        "what time is it in Tokyo",
        "is coffee bad for you",
        "tell me a joke about cats",
        "I'm thinking about getting a haircut",
        "remind me to water the plants tomorrow",
        "can elephants swim",
        "my back hurts after the gym",
    ],
)
def test_ordinary_chat_stays_silent_and_unbound(person_home, words) -> None:
    resolution = resolve_folder_from_words(words, person_home)
    assert resolution.status == "no_ask", (words, resolution)
    assert resolution.path == ""
    assert resolution.matches == ()
    assert resolution.message == ""
    assert resolution.note == ""

    chat = _unique_chat("ordinary")
    result = apply_workspace_words_binding(
        chat, words, home=person_home, owner_local=True
    )
    assert result == {"status": "no_ask", "bound": False, "notice": ""}
    assert project_store.list_projects() == []
    assert authoritative_chat_workspace(chat) == ("", "unbound")


# ----------------------------------------------------------------------------------
# Casual mentions of a name never bind
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "words",
    [
        # the reviewer's casual mentions
        "how do I file my taxes this year",
        "play my music",
        "clean up my downloads",
        "where are my passwords",
        "my mom called",
        # this file's own casual mentions
        "where did I put my keys this morning",
        "I need to back up my photos tonight",
        "my sister borrowed my laptop charger",
        "how do I print labels from my computer",
    ],
)
def test_a_casual_mention_of_a_name_never_binds(person_home, words) -> None:
    resolution = resolve_folder_from_words(words, person_home)
    assert resolution.status == "no_ask", (words, resolution)
    assert resolution.path == ""
    assert resolution.message == ""

    chat = _unique_chat("casual")
    result = apply_workspace_words_binding(
        chat, words, home=person_home, owner_local=True
    )
    assert result["bound"] is False
    assert result["notice"] == ""
    assert project_store.list_projects() == []
    namespace = load_chat_namespace(chat)
    assert namespace is None or namespace.project_id == ""
    assert authoritative_chat_workspace(chat) == ("", "unbound")


# ----------------------------------------------------------------------------------
# A bind needs a work ask AND a folder pointer
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "words",
    [
        # a named folder with no work ask around it
        "my garden planner project",
        # a work ask with no folder pointer
        "fix the login bug tonight",
        # a name with no pointer noun and no container or quote around it
        "open my garden planner",
    ],
)
def test_without_both_a_work_ask_and_a_folder_pointer_nothing_happens(
    person_home, words
) -> None:
    resolution = resolve_folder_from_words(words, person_home)
    assert resolution.status == "no_ask", (words, resolution)
    chat = _unique_chat("no-pointer")
    result = apply_workspace_words_binding(
        chat, words, home=person_home, owner_local=True
    )
    assert result["bound"] is False
    assert result["notice"] == ""
    assert project_store.list_projects() == []
    assert authoritative_chat_workspace(chat) == ("", "unbound")


# ----------------------------------------------------------------------------------
# Lookalikes that must not resolve
# ----------------------------------------------------------------------------------


def test_an_idea_is_not_a_folder_ask(person_home) -> None:
    resolution = resolve_folder_from_words("plant a garden project idea", person_home)
    assert resolution.status in {"no_ask", "none"}
    assert resolution.path == ""


def test_a_folder_that_exists_nowhere_is_a_plain_no(person_home) -> None:
    resolution = resolve_folder_from_words(
        "open the underwater basket weaving project", person_home
    )
    assert resolution.status == "none"
    assert resolution.path == ""
    assert resolution.matches == ()
    assert resolution.message != ""
    assert str(person_home) not in resolution.message


def test_words_with_no_folder_name_at_all_are_no_ask(person_home) -> None:
    resolution = resolve_folder_from_words("let's cook dinner tonight", person_home)
    assert resolution.status == "no_ask"
    assert resolution.path == ""
    assert resolution.matches == ()
    assert resolution.message == ""


@pytest.mark.parametrize(
    "words",
    [
        "plant a garden project idea",
        "open the underwater basket weaving project",
        "let's cook dinner tonight",
    ],
)
def test_a_lookalike_wording_binds_nothing(person_home, words) -> None:
    chat = _unique_chat("lookalike")
    result = apply_workspace_words_binding(
        chat, words, home=person_home, owner_local=True
    )
    assert result["status"] in {"no_ask", "none"}
    assert result["bound"] is False
    assert project_store.list_projects() == []
    namespace = load_chat_namespace(chat)
    assert namespace is None or namespace.project_id == ""
    assert authoritative_chat_workspace(chat) == ("", "unbound")


# ----------------------------------------------------------------------------------
# The exact name wins over a longer near-name, with a plain-words aside
# ----------------------------------------------------------------------------------


def test_an_exact_name_wins_over_a_longer_near_name(person_home) -> None:
    words = "open my garden planner project"
    resolution = resolve_folder_from_words(words, person_home)
    expected = os.path.realpath(person_home / "Projects" / "garden planner")
    assert resolution.status == "found"
    assert resolution.path == expected
    assert resolution.note != ""
    assert "garden planner 2" in resolution.note
    assert "/" not in resolution.note

    chat = _unique_chat("exact-wins")
    result = apply_workspace_words_binding(
        chat, words, home=person_home, owner_local=True
    )
    assert result["bound"] is True
    assert result["root"] == expected
    assert "garden planner 2" in result["notice"]
    assert str(person_home) not in result["notice"]
    assert "/" not in result["notice"]


# ----------------------------------------------------------------------------------
# Generic nouns are never part of the name
# ----------------------------------------------------------------------------------


def test_stuff_after_a_name_is_not_part_of_the_name(person_home) -> None:
    resolution = resolve_folder_from_words(
        "work on the tax 2025 stuff in documents", person_home
    )
    expected = os.path.realpath(person_home / "Documents" / "Tax 2025")
    assert resolution.status == "found"
    assert resolution.path == expected
    assert resolution.name == "Tax 2025"


# ----------------------------------------------------------------------------------
# Several matches
# ----------------------------------------------------------------------------------


def test_two_sibling_folders_with_one_name_ask_which_one(person_home) -> None:
    words = "open my invoice tool project"
    resolution = resolve_folder_from_words(words, person_home)
    assert resolution.status == "several"
    assert resolution.path == ""
    listed = [display for _path, display in resolution.matches]
    assert sorted(listed) == ["invoice-tool", "invoice_tool"]
    assert resolution.message != ""

    chat = _unique_chat("several")
    result = apply_workspace_words_binding(
        chat, words, home=person_home, owner_local=True
    )
    assert result["status"] == "several"
    assert result["bound"] is False
    assert result["notice"] != ""
    assert str(person_home) not in result["notice"]
    assert "/" not in result["notice"]
    assert project_store.list_projects() == []
    assert authoritative_chat_workspace(chat) == ("", "unbound")


def test_many_matches_list_at_most_three_folder_names(person_home) -> None:
    resolution = resolve_folder_from_words("review the archive sorter project", person_home)
    assert resolution.status == "several"
    assert len(resolution.matches) <= 3
    listed = [display for _path, display in resolution.matches]
    assert len(listed) == len(set(listed))
    real_names = {"archive-sorter", "archive_sorter", "ArchiveSorter", "archive sorter"}
    for display in listed:
        assert display in real_names


# ----------------------------------------------------------------------------------
# The home's standard folders and containers themselves are refused, never bound
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "words",
    [
        "open my downloads folder",
        "look at my pictures folder",
        "open my music folder",
        "open my movies folder",
        "open my public folder",
    ],
)
def test_a_standard_home_folder_is_refused_in_plain_words(
    person_home, words
) -> None:
    resolution = resolve_folder_from_words(words, person_home)
    assert resolution.status == "refused", (words, resolution)
    assert resolution.path == ""
    assert resolution.message != ""
    assert "/" not in resolution.message
    assert str(person_home) not in resolution.message

    chat = _unique_chat("standard")
    result = apply_workspace_words_binding(
        chat, words, home=person_home, owner_local=True
    )
    assert result["status"] == "refused"
    assert result["bound"] is False
    assert result["notice"] == resolution.message
    assert "/" not in result["notice"]
    assert project_store.list_projects() == []
    assert authoritative_chat_workspace(chat) == ("", "unbound")


@pytest.mark.parametrize("words", ["open my projects folder", "open my documents folder"])
def test_a_container_folder_itself_is_refused(person_home, words) -> None:
    resolution = resolve_folder_from_words(words, person_home)
    assert resolution.status == "refused", (words, resolution)
    assert resolution.path == ""
    assert resolution.message != ""
    assert "/" not in resolution.message

    chat = _unique_chat("container")
    result = apply_workspace_words_binding(
        chat, words, home=person_home, owner_local=True
    )
    assert result["bound"] is False
    assert project_store.list_projects() == []
    assert authoritative_chat_workspace(chat) == ("", "unbound")


# ----------------------------------------------------------------------------------
# Secret-named folders are refused in plain words, never bound
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "words",
    [
        "open my passwords folder",
        "work on the seed catalog project in Projects",
        "fix my wallet code",
        "open my 2fa folder",
        "run the tests in my backup codes project",
    ],
)
def test_a_secret_name_is_refused_in_plain_words(person_home, words) -> None:
    resolution = resolve_folder_from_words(words, person_home)
    assert resolution.status == "refused", (words, resolution)
    assert resolution.path == ""
    assert resolution.message != ""
    assert "/" not in resolution.message
    assert str(person_home) not in resolution.message

    chat = _unique_chat("secret")
    result = apply_workspace_words_binding(
        chat, words, home=person_home, owner_local=True
    )
    assert result["status"] == "refused"
    assert result["bound"] is False
    assert "/" not in result["notice"]
    assert project_store.list_projects() == []
    namespace = load_chat_namespace(chat)
    assert namespace is None or namespace.project_id == ""
    assert authoritative_chat_workspace(chat) == ("", "unbound")


# ----------------------------------------------------------------------------------
# Secret, system, outside-home and too-broad folders never bind
# ----------------------------------------------------------------------------------


def test_a_system_tree_inside_the_home_is_never_a_match(person_home) -> None:
    words = "look at the sync keystore project in my Library"
    resolution = resolve_folder_from_words(words, person_home)
    assert resolution.status == "none"
    assert resolution.path == ""
    chat = _unique_chat("system-tree")
    result = apply_workspace_words_binding(
        chat, words, home=person_home, owner_local=True
    )
    assert result["bound"] is False
    assert project_store.list_projects() == []
    assert authoritative_chat_workspace(chat) == ("", "unbound")


def test_a_link_leaving_the_home_is_never_a_match(person_home) -> None:
    words = "look at the escape-hatch thing in Projects"
    resolution = resolve_folder_from_words(words, person_home)
    assert resolution.status not in {"found", "several"}
    assert resolution.path == ""
    chat = _unique_chat("outside-link")
    result = apply_workspace_words_binding(
        chat, words, home=person_home, owner_local=True
    )
    assert result["bound"] is False
    assert project_store.list_projects() == []
    assert authoritative_chat_workspace(chat) == ("", "unbound")


def test_the_home_folder_itself_is_never_a_match(person_home) -> None:
    words = "open my home folder"
    resolution = resolve_folder_from_words(words, person_home)
    assert resolution.status not in {"found", "several"}
    assert resolution.path == ""
    chat = _unique_chat("whole-home")
    result = apply_workspace_words_binding(
        chat, words, home=person_home, owner_local=True
    )
    assert result["bound"] is False
    assert project_store.list_projects() == []
    assert authoritative_chat_workspace(chat) == ("", "unbound")


def test_the_project_store_refuses_too_broad_roots(person_home) -> None:
    # The registry the words-lane reuses refuses the home folder and its system tree on
    # its own, with real folders present.
    ok_home, _detail = project_store.create_project("everything", str(person_home))
    assert ok_home is False
    ok_library, _detail = project_store.create_project(
        "system", str(person_home / "Library")
    )
    assert ok_library is False


def test_a_refused_project_registration_binds_nothing(person_home) -> None:
    # The registry write itself is made impossible, so the store refuses mid-binding:
    # the chat must be left with no project and no namespace change, and the refusal
    # notice must be one plain sentence with no path in it.
    registry = project_store.projects_path()
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.mkdir()
    chat = _unique_chat("store-refusal")
    try:
        result = apply_workspace_words_binding(
            chat, "open my garden planner project", home=person_home, owner_local=True
        )
    except OSError:
        result = None
    if result is not None:
        assert result.get("bound") is False
        if result.get("notice"):
            assert "/" not in result["notice"]
    assert project_store.list_projects() == []
    namespace = load_chat_namespace(chat)
    assert namespace is None or namespace.project_id == ""
    assert authoritative_chat_workspace(chat) == ("", "unbound")


# ----------------------------------------------------------------------------------
# A remote surface never binds
# ----------------------------------------------------------------------------------


def test_a_remote_surface_never_binds(person_home) -> None:
    chat = _unique_chat("remote")
    result = apply_workspace_words_binding(
        chat, "open my garden planner project", home=person_home, owner_local=False
    )
    assert result["status"] == "skip"
    assert result["bound"] is False
    assert result["notice"] == ""
    assert project_store.list_projects() == []
    assert authoritative_chat_workspace(chat) == ("", "unbound")


# ----------------------------------------------------------------------------------
# The binding and what the next turn sees
# ----------------------------------------------------------------------------------


def test_binding_from_words_sets_the_workspace_the_next_turn_sees(person_home) -> None:
    chat = _unique_chat("next-turn")
    result = apply_workspace_words_binding(
        chat, "open my garden planner project", home=person_home, owner_local=True
    )
    expected_root = os.path.realpath(person_home / "Projects" / "garden planner")
    assert result["status"] == "found"
    assert result["bound"] is True
    assert result["root"] == expected_root

    project_id = result["project_id"]
    assert project_id
    assert project_store.project_root(project_id) == expected_root

    namespace = load_chat_namespace(chat)
    assert namespace is not None
    assert namespace.project_id == project_id

    grants = list_context_imports(chat)
    assert any(
        grant.scope == "project" and grant.source_id == f"project:{project_id}"
        for grant in grants
    )

    notice = result["notice"]
    assert "garden planner" in notice.lower()
    assert str(person_home) not in notice
    assert "/" not in notice

    root, reason = authoritative_chat_workspace(chat)
    assert root == expected_root
    assert reason == "project"

    # Binding a workspace is never standing approval for writes.
    assert project_store.get_project_low_risk_approval(project_id) == {}


# ----------------------------------------------------------------------------------
# Skip paths: already bound, explicit workspace, pre-bound project id
# ----------------------------------------------------------------------------------


def test_an_already_bound_chat_skips_a_new_word_binding(person_home) -> None:
    chat = _unique_chat("already-bound")
    first = apply_workspace_words_binding(
        chat, "open my garden planner project", home=person_home, owner_local=True
    )
    assert first["bound"] is True

    # The turn's caller hands the chat's existing binding in, as the turn dispatch does.
    second = apply_workspace_words_binding(
        chat,
        "fix the recipe app in my Code folder",
        home=person_home,
        owner_local=True,
        bound_project_id=first["project_id"],
    )
    assert second["status"] == "skip"
    assert second["bound"] is False
    assert second["notice"] == ""

    root, reason = authoritative_chat_workspace(chat)
    assert root == first["root"]
    assert reason == "project"


def test_an_explicit_workspace_wins_over_the_words(person_home) -> None:
    chat = _unique_chat("explicit-workspace")
    result = apply_workspace_words_binding(
        chat,
        "open my garden planner project",
        home=person_home,
        owner_local=True,
        requested_workspace=str(person_home / "Projects" / "plant catalog"),
    )
    assert result["status"] == "skip"
    assert result["bound"] is False
    assert result["notice"] == ""
    assert project_store.list_projects() == []
    assert authoritative_chat_workspace(chat) == ("", "unbound")


def test_a_previously_bound_project_id_skips_the_word_binding(person_home) -> None:
    chat = _unique_chat("pre-bound")
    result = apply_workspace_words_binding(
        chat,
        "open my garden planner project",
        home=person_home,
        owner_local=True,
        bound_project_id="proj_prebound0001",
    )
    assert result["status"] == "skip"
    assert result["bound"] is False
    assert result["notice"] == ""
    assert project_store.list_projects() == []
    assert authoritative_chat_workspace(chat) == ("", "unbound")


# ----------------------------------------------------------------------------------
# The docs-folder ask inside a bound project
# ----------------------------------------------------------------------------------


def test_a_docs_folder_ask_in_a_bound_project_targets_the_projects_own_docs_folder(
    person_home,
) -> None:
    # Pins the folder-name reading of a "make sure there's a docs folder ..." ask once the
    # chat is bound: the planned directory is the project's own docs folder, never a stray
    # word lifted out of the sentence.
    from core.execution.planner import plan_tool_workflow

    chat = _unique_chat("docs-ask")
    binding = apply_workspace_words_binding(
        chat, "open my garden planner project", home=person_home, owner_local=True
    )
    assert binding["bound"] is True
    root = binding["root"]

    decision = plan_tool_workflow(
        user_text="make sure there's a docs folder in garden planner",
        task_class="unknown",
        executed_steps=[],
        source_context={"workspace": root, "workspace_root": root},
    )
    payload = decision.next_payload or {}
    assert payload.get("intent") == "workspace.ensure_directory"
    arguments = payload.get("arguments") or {}
    assert arguments.get("path") == "docs"
