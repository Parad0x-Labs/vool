"""Guard: naming a workspace file must bind to the real read tool, never to the model's imagination.

_direct_workspace_read_request used to require one of five literal verbs (" read ", " open ",
" inspect ", " show ", " tell me "). Every other phrasing fell through to the model, which INVENTED
the file's contents -- an audit caught it emitting a DIFFERENT workspace's file as this one's, three
runs in a row, with zero tool events. The two failure modes are not symmetric: missing a read verb
produces fabrication; over-matching produces a harmless extra read.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.fast_paths_utility import _direct_workspace_read_request


@pytest.mark.parametrize(
    "phrasing",
    [
        # The originals, which must keep working.
        "read notes.txt", "open notes.txt", "show me notes.txt", "inspect notes.txt",
        # The ones that fabricated. "cat" is what any developer types.
        "print the contents of notes.txt",
        "cat notes.txt",
        "what is in notes.txt?",
        "what does notes.txt say?",
        "display notes.txt",
        "output notes.txt",
        "view notes.txt",
        "look at notes.txt",
        "check notes.txt",
        "pls just give me notes.txt",
    ],
)
def test_named_workspace_file_binds_to_the_read_tool(phrasing):
    result = _direct_workspace_read_request(phrasing)
    assert result is not None, f"{phrasing!r} falls through to the model, which will invent contents"
    assert result["path"] == "notes.txt"


def test_nested_paths_bind_too():
    assert _direct_workspace_read_request("what does src/calc.py contain?")["path"] == "src/calc.py"


@pytest.mark.parametrize(
    "phrasing",
    [
        "delete notes.txt",
        "create notes.txt with hello",
        "write notes.txt",
        "rename notes.txt to old.txt",
        "append a line to notes.txt",
        "update config.json",
        "move notes.txt into archive",
        "overwrite notes.txt",
    ],
)
def test_mutation_intent_is_not_a_read(phrasing):
    assert _direct_workspace_read_request(phrasing) is None, f"{phrasing!r} treated as a read"


def test_a_message_with_no_filename_is_not_a_read():
    # The file reference IS the signal -- a bare question must not bind.
    assert _direct_workspace_read_request("what is in the workspace?") is None
    assert _direct_workspace_read_request("how are you?") is None
