"""An exact-count request over the bound workspace gets counted facts, never a tree.

What this file pins
-------------------
"Count how many Python files exist in this project, determine which Python file has the most lines,
and give me its exact path and line count. Do not estimate." was answered, in 0s, with
`build_folder_overview`'s tree: "Top level (30 folders, 48 files)" plus a folder listing. The
tree's own folder/file counts read like an answer to a count request -- an earlier form of the
count assertion accepted "any number greater than 10", which the tree satisfied -- while NONE of
the three requested facts (the typed count, the largest file, its exact path, its line count) were
present. Recorded as two strict xfails in
`tests/gauntlet/test_release_conversation_gauntlet.py` (G12), both promoted.

The repair is `maybe_handle_folder_overview_request` resolving a measurement ask with
`core.folder_overview.build_exact_file_facts`, which walks the bound workspace and reads every
matching file: the answer carries the exact count and the largest-by-LINE-count file with its
path, states its scope and exclusions so the number is reproducible, and never renders a listing.

The controls below keep the seam honest in both directions:

* POSITIVE: every fact is computed from files this test wrote, so a plausible-looking summary of
  files the runtime never read cannot pass;
* NEGATIVE: an overview ask still gets the tree, an advice-shaped sentence that merely mentions
  counting still declines, a generic "how many files" with no type still gets today's behaviour;
* ADVERSARIAL: no estimate phrase is required; the largest file is ranked by LINES not bytes;
  binary .py files are counted but excluded from the line ranking, stated in the answer; a
  symlinked directory never extends the count beyond the workspace; an empty workspace yields an
  honest zero.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from apps.vool_agent import VoolAgent
from core.agent_runtime.fast_paths_utility import _measured_file_type, maybe_handle_folder_overview_request
from core.folder_overview import build_exact_file_facts

# Distinct, unguessable values: an assertion that sees them is looking at files this test wrote.
_ROOT_MARKER_FILE = "leaf_module.py"
_ROOT_MARKER_LINES = 7
_DEEP_MARKER_LINES = 19


@pytest.fixture()
def agent():
    return VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")


@pytest.fixture()
def counted_project(tmp_path: Path) -> Path:
    """A real tree with known counts: 3 .py files, the deepest one the largest BY LINES."""
    (tmp_path / "leaf_module.py").write_text(
        "\n".join(f"line {n}" for n in range(1, _ROOT_MARKER_LINES + 1)) + "\n", encoding="utf-8"
    )
    deep = tmp_path / "deep" / "nested"
    deep.mkdir(parents=True)
    (deep / "core_impl.py").write_text(
        "\n".join(f"row {n}" for n in range(1, _DEEP_MARKER_LINES + 1)) + "\n", encoding="utf-8"
    )
    (tmp_path / "tiny.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not python\n", encoding="utf-8")
    return tmp_path


def _context(workspace) -> dict[str, object]:
    return {"surface": "channel", "workspace": str(workspace), "project_id": "regression"}


def _overview_lane(agent, text, workspace):
    return maybe_handle_folder_overview_request(
        agent, text, session_id="exact-count", source_surface="channel", source_context=_context(workspace)
    )


def _reply(result) -> str:
    return str((result or {}).get("response") or "")


# ------------------------------------------------------------------------------------------ POSITIVE


def test_an_exact_count_request_is_answered_with_the_counted_facts(agent, counted_project):
    result = _overview_lane(
        agent,
        "Count how many Python files exist in this project, and tell me which Python file has "
        "the most lines, with its exact path and line count. Do not estimate.",
        counted_project,
    )

    assert result is not None, "the measurement ask was not claimed at all"
    assert result.get("route") == "deterministic:workspace_measurement_fast_path", result.get("route")
    reply = _reply(result)
    assert "top level" not in reply.lower(), reply
    assert "\U0001F4C1" not in reply, reply
    assert "folders," not in reply.lower(), reply
    assert "python files" in reply.lower(), reply
    assert ".py" in reply, reply
    assert "3" in reply, reply
    assert "deep/nested/core_impl.py" in reply, reply
    assert "19" in reply, reply
    assert "line" in reply.lower(), reply


def test_the_dotted_form_of_the_type_is_counted_the_same_way(agent, counted_project):
    result = _overview_lane(agent, "how many .py files are in this project?", counted_project)

    assert result is not None
    reply = _reply(result)
    assert "3" in reply, reply
    assert "workspace_measurement_fast_path" in str(result.get("route")), result.get("route")


def test_the_direct_fact_builder_reports_what_is_on_disk(tmp_path: Path):
    (tmp_path / "a.py").write_text("one\ntwo\n", encoding="utf-8")
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "b.py").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")

    reply = build_exact_file_facts(str(tmp_path), "py")

    assert reply is not None
    assert "2" in reply, reply
    assert "pkg/b.py" in reply, reply
    assert "4" in reply, reply
    assert "line" in reply.lower(), reply


# ------------------------------------------------------------------------------------------ NEGATIVE


def test_an_overview_ask_still_gets_the_tree(agent, counted_project):
    """The tree is the right answer to an overview, and must keep owning it."""
    result = _overview_lane(agent, "What is this project about?", counted_project)

    assert result is not None
    assert "workspace_measurement_fast_path" not in str(result.get("route"))
    assert "top level" in _reply(result).lower(), _reply(result)


def test_an_advice_sentence_that_mentions_counting_is_not_measured(agent, counted_project):
    result = _overview_lane(
        agent, "Is it a good idea to count all the python files in this project?", counted_project
    )

    assert result is None, _reply(result)


def test_a_generic_file_count_with_no_named_type_keeps_todays_behaviour(agent, counted_project):
    """No concrete type, no measurement: the loose-arm behaviour is unchanged."""
    result = _overview_lane(agent, "How many files are in this project?", counted_project)

    assert result is not None
    assert "workspace_measurement_fast_path" not in str(result.get("route"))
    assert "top level" in _reply(result).lower(), _reply(result)


def test_a_typeless_line_count_ask_does_not_measure(agent, counted_project):
    result = _overview_lane(agent, "How many lines of code would you say this project needs?", counted_project)

    assert result is None or "workspace_measurement_fast_path" not in str(result.get("route"))


# ---------------------------------------------------------------------------------------- ADVERSARIAL


def test_the_measurement_does_not_need_an_estimate_disclaimer(agent, counted_project):
    result = _overview_lane(
        agent, "how many python files are in this project? give the number", counted_project
    )

    assert result is not None
    assert "workspace_measurement_fast_path" in str(result.get("route"))
    assert "3" in _reply(result), _reply(result)


def test_largest_is_ranked_by_lines_not_bytes(tmp_path: Path):
    (tmp_path / "fat.py").write_text("x = 'A' * 200000\ny = 2\n", encoding="utf-8")
    (tmp_path / "many_lines.py").write_text("\n".join(f"l{n}" for n in range(60)) + "\n", encoding="utf-8")

    reply = build_exact_file_facts(str(tmp_path), "py")

    assert reply is not None
    assert "many_lines.py" in reply, reply
    assert "fat.py" not in reply, reply
    assert "60" in reply, reply


def test_a_binary_py_file_is_counted_but_excluded_from_the_line_ranking(tmp_path: Path):
    (tmp_path / "text.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    (tmp_path / "blob.py").write_bytes(b"\x00\x01\x02binary\n")

    reply = build_exact_file_facts(str(tmp_path), "py")

    assert reply is not None
    assert "2" in reply, reply  # both files counted
    assert "text.py" in reply, reply
    assert "binary" in reply.lower(), reply


def test_a_symlinked_directory_never_extends_the_count(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("hidden = True\n", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / "real.py").write_text("a = 1\n", encoding="utf-8")
    os.symlink(outside, project / "linked")

    reply = build_exact_file_facts(str(project), "py")

    assert reply is not None
    assert "1" in reply, reply
    assert "secret" not in reply, reply


def test_an_empty_workspace_is_answered_with_an_honest_zero(tmp_path: Path):
    reply = build_exact_file_facts(str(tmp_path), "py")

    assert reply is not None
    lowered = reply.lower()
    assert "0" in reply, reply
    assert "python files" in lowered or ".py files" in lowered, reply
    assert "top level" not in lowered, reply


def test_an_unbound_chat_keeps_todays_guidance(agent, tmp_path):
    """No usable root: the measurement declines and the existing guidance is what the user sees."""
    context = {"surface": "channel", "workspace": "", "project_id": ""}

    result = maybe_handle_folder_overview_request(
        agent,
        "Count how many Python files exist in this project. Do not estimate.",
        session_id="unbound",
        source_surface="channel",
        source_context=context,
    )

    assert result is not None
    assert "workspace_measurement_fast_path" not in str(result.get("route"))
    assert "top level" not in _reply(result).lower()


def test_the_type_detector_is_structural_not_phrasal():
    assert _measured_file_type("Count how many Python files exist in this project")[0] == "py"
    assert _measured_file_type("how many .md files are here")[0] == "md"
    assert _measured_file_type("how many files are in this project") == ("", "")
    assert _measured_file_type("which python file has the most lines")[0] == "py"
    assert _measured_file_type("tell me about this project") == ("", "")
