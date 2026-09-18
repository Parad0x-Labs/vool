"""Asking for a file by name finds it.

The live session this pins, verbatim:

    USER: hey, can you find Vool_agent.PY file pls?
    VOOL: Incomplete — model synthesis failed (empty_synthesis).
    USER: its somewhwere on my desktop in one of folders i think
    VOOL: No folder matching 'somewhwere one think' found on / (searched to depth 6)
    USER: now i mean vool_agent.py is somewhere on my desktop right
    VOOL: File vool_agent.py does not exist.

`apps/vool_agent.py` existed, in a repo on that Desktop. `find ~/Desktop -maxdepth 4 -iname
"vool_agent.py"` returns three copies in 0.044 seconds.

Traced, the runtime had no tool that could answer it. `machine.find_folder` matches DIRECTORIES
only; `workspace.list_files` can match files but is confined to the bound project; and
`workspace.read_file` does no searching at all — it stats one path and reports that single miss as
"does not exist". The capability fell in the gap between two lanes and nothing owned the gap.

These tests are written against generated temporary trees, never against the reporter's machine, so
they pass on any box and cannot be satisfied by recognising a fixture name.
"""
from __future__ import annotations

import pytest

from core import runtime_paths
from core.runtime_execution_tools import execute_runtime_tool


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    runtime_paths.configure_runtime_home(tmp_path / "home")
    yield
    runtime_paths.configure_runtime_home(None)


@pytest.fixture()
def machine_tree(tmp_path, monkeypatch):
    """A stand-in for the operator's Desktop, with the subject buried a few levels down."""
    desktop = tmp_path / "Desktop"
    (desktop / "some-project" / "apps").mkdir(parents=True)
    (desktop / "some-project" / "apps" / "orbital_agent.py").write_text("# subject\n")
    (desktop / "other" / "deep" / "deeper").mkdir(parents=True)
    (desktop / "other" / "deep" / "deeper" / "notes.md").write_text("decoy\n")
    (desktop / "node_modules" / "pkg").mkdir(parents=True)
    (desktop / "node_modules" / "pkg" / "orbital_agent.py").write_text("# must be skipped\n")
    monkeypatch.setattr(
        "core.runtime_execution_tools._safe_machine_roots", lambda: [str(desktop)]
    )
    return desktop


def _find(name: str, **kwargs):
    return execute_runtime_tool("machine.find_file", {"name": name, **kwargs})


def test_a_file_is_found_by_its_exact_name(machine_tree) -> None:
    result = _find("orbital_agent.py")

    assert result is not None, "machine.find_file is not a registered intent"
    assert result.ok is True
    assert "orbital_agent.py" in result.response_text
    assert "some-project" in result.response_text


def test_the_search_is_case_insensitive(machine_tree) -> None:
    """The operator typed `Vool_agent.PY`. A search that only matches the exact case answers
    'does not exist' about a file that is right there."""
    result = _find("ORBITAL_AGENT.PY")

    assert result.ok is True
    assert "orbital_agent.py" in result.response_text


def test_a_partial_name_still_finds_it(machine_tree) -> None:
    result = _find("orbital_agent")

    assert result.ok is True
    assert "orbital_agent.py" in result.response_text


def test_generated_and_vendor_directories_are_skipped(machine_tree) -> None:
    """`node_modules` holds a same-named copy. Returning it as a hit would be a wrong answer."""
    result = _find("orbital_agent.py")

    assert "node_modules" not in result.response_text


def test_a_genuine_miss_says_so_without_claiming_the_file_cannot_exist(machine_tree) -> None:
    result = _find("no_such_file_anywhere.py")

    assert result.ok is True, "a miss is a completed search, not a tool failure"
    lowered = result.response_text.lower()
    assert "no file" in lowered or "no match" in lowered
    # It must name where it looked, so the operator can tell a real absence from a narrow search.
    assert "desktop" in lowered or "searched" in lowered


def test_the_search_never_leaves_the_safe_roots(tmp_path, monkeypatch) -> None:
    """The machine lane is allowlisted to Desktop/Downloads/Documents. A file outside must not be
    returned even when it matches perfectly."""
    safe = tmp_path / "Desktop"
    safe.mkdir()
    (safe / "keep.txt").write_text("x\n")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "secret_agent.py").write_text("must not be found\n")
    monkeypatch.setattr("core.runtime_execution_tools._safe_machine_roots", lambda: [str(safe)])

    result = _find("secret_agent.py")

    # The miss message legitimately echoes the query, so the property is about the MATCHES, not
    # about the name appearing anywhere in the text.
    assert result.details.get("matches") == []
    assert str(outside) not in result.response_text


def test_a_sentence_is_not_a_filename() -> None:
    """`_find_folder_name_from_phrase` reduced "its somewhwere on my desktop in one of folders i
    think" to the folder name 'somewhwere one think' — a typo, a quantifier and a verb — because its
    stopword list is an allowlist and anything unlisted is treated as a proper noun. A phrase that
    reduces to nothing recognisable must not become a search term."""
    from core.runtime_execution_tools import _find_folder_name_from_phrase

    name, _scope = _find_folder_name_from_phrase(
        "its somewhwere on my desktop in one of folders i think"
    )

    assert name != "somewhwere one think"
    assert not name, f"a vague sentence produced the search term {name!r}"


@pytest.mark.parametrize(
    "phrase,expected",
    [
        ("find the folder called orbital-probe", "orbital-probe"),
        ("where is my invoices folder", "invoices"),
    ],
)
def test_a_real_folder_name_still_survives_the_phrase_reader(phrase: str, expected: str) -> None:
    """The guard above must not cost the cases that already worked."""
    from core.runtime_execution_tools import _find_folder_name_from_phrase

    name, _scope = _find_folder_name_from_phrase(phrase)

    assert expected in name


def test_read_file_miss_points_at_the_search_instead_of_denying_existence(tmp_path) -> None:
    """`workspace.read_file` stats ONE path. Reporting that single miss as "does not exist" is a
    claim about the whole machine that the tool never checked."""
    workspace = tmp_path / "ws"
    (workspace / "apps").mkdir(parents=True)
    (workspace / "apps" / "orbital_agent.py").write_text("# here\n")

    result = execute_runtime_tool(
        "workspace.read_file",
        {"path": "orbital_agent.py"},
        source_context={"workspace": str(workspace)},
    )

    lowered = str(result.response_text).lower()
    assert "does not exist" not in lowered, (
        "one failed stat was reported as the file not existing"
    )
    assert "apps/orbital_agent.py" in str(result.response_text) or "search" in lowered
