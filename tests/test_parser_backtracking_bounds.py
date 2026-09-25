"""Adversarial non-matches must not make request parsing explore equivalent partitions."""
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("name,text", [
    ("_VERB_NAME_FOLDER_RE", "create " + "a/" * 30 + "!"),
    ("_CREATE_PATH_RE", "create " + "/" * 30 + "!"),
])
def test_folder_path_nonmatch_finishes_without_exponential_backtracking(name, text):
    script = "from core.execution import constants; import sys; getattr(constants, sys.argv[1]).search(sys.argv[2])"
    subprocess.run([sys.executable, "-c", script, name, text], check=True,
        timeout=3, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


def test_response_word_owners_keep_the_same_clock_and_word_contract():
    from core import raw_output_contract, response_constraints
    for text, expected in [("06:49 2026-08-15", ["06:49", "2026-08-15"]),
                           ("alpha:123xyz beta", ["alpha:123xyz", "beta"]),
                           ("can't naïve", ["can't", "naïve"])]:
        assert response_constraints._WORD_RE.findall(text) == expected
        assert raw_output_contract._WORD_RE.findall(text) == expected


@pytest.mark.parametrize("name,text,path", [
    ("_NAMED_PATH_RE", "named `src/a-b/file.txt`", "src/a-b/file.txt"),
    ("_VERB_NAME_FOLDER_RE", "create a src/a-b folder", "src/a-b"),
    ("_CREATE_PATH_RE", "create ../sandbox/sub", "../sandbox/sub"),
    ("_INTO_PATH_RE", "inside '/tmp/my_dir'", "/tmp/my_dir"),
])
def test_path_extraction_keeps_existing_spelling_and_capture(name, text, path):
    from core.execution import constants
    match = getattr(constants, name).search(text)
    assert match and match.group("path") == path


@pytest.mark.parametrize("script", [
    "from core.task_router import _direct_math_expression; _direct_math_expression('1+1 ' + 'ty '*36 + 'X')",
    "from core.agent_runtime.answer_coverage import _COMPARISON_ATTRIBUTE_TAIL_RE as p; p.search(' on ' + 'a    ,'*24 + 'a!')",
    "from core.agent_runtime.hive_topic_draft_parsing import extract_hive_topic_create_draft; from tests.test_agent_runtime_hive_topic_drafting import _DraftingAgent; a=_DraftingAgent(); a._looks_like_hive_topic_create_request=lambda _: True; extract_hive_topic_create_draft(a, 'lets '*32 + 'bogus')",
])
def test_request_grammar_nonmatches_do_not_explore_equivalent_partitions(script):
    subprocess.run([sys.executable, "-c", script], check=True, timeout=5,
                   env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


@pytest.mark.parametrize("module,name,prefix", [
    ("core.self_update_offer", "_STRICT_YES", "yes"),
    ("core.vool_agent_brake", "_STOP_RE", "freeze"),
])
def test_whole_command_suffixes_do_not_repartition_whitespace(module, name, prefix):
    script = "import importlib,sys; p=getattr(importlib.import_module(sys.argv[1]), sys.argv[2]); assert p.match(sys.argv[3] + ' '*100000 + 'X') is None"
    subprocess.run([sys.executable, "-c", script, module, name, prefix],
                   check=True, timeout=3, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


def test_write_demand_filename_normalization_is_bounded():
    script = ("from core.execution.write_demand import resolve_write_demand; "
              "assert resolve_write_demand('a'*100000+'!') is None")
    subprocess.run([sys.executable, "-c", script], check=True, timeout=3,
                   env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


def test_split_extensions_preserve_successive_path_segments():
    from core.execution.write_demand import _normalize_split_file_extensions

    assert _normalize_split_file_extensions("create foo. py/bar. txt containing hi") == "create foo.py/bar.txt containing hi"
    assert _normalize_split_file_extensions("notes. md then plain prose. nope") == "notes.md then plain prose. nope"


@pytest.mark.parametrize("script", [
    "from core.incomplete_answer import inspect_answer_completeness; inspect_answer_completeness('| A | B |\\n|' + ' '*100000 + 'x')",
    "from core.grounding_publication import _trim_unsupported_spans; _trim_unsupported_spans('remove' + '\\u00a0'*100000 + 'tail', ['remove'])",
    "from core.memory.entries import is_user_correction; is_user_correction('not something '*10000 + '!')",
])
def test_remaining_guard_nonmatches_do_not_revisit_suffixes(script):
    subprocess.run([sys.executable, "-c", script], check=True, timeout=3,
                   env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


@pytest.mark.parametrize("text,expected", [
    ("not red, it is blue", True),
    ("not x\nit is blue", True),
    ("not \nit is blue", False),
    ("not x\nunrelated it is blue", False),
    ("not x; it's blue", True),
    ("not something, maybe later", False),
])
def test_correction_scanner_preserves_line_and_payload_boundaries(text, expected):
    from core.memory.entries import is_user_correction
    assert is_user_correction(text) is expected
