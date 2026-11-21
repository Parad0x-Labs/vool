"""A long pasted message must reach the model, not end the turn.

Measured on the first-day sweep (Nov 21): a message of about 1,500 words with no punctuation
(dictated speech, a pasted keyword or name list) ended with
"I couldn't finish that reply". The paste is kept as a chat document, and its type check asked
Python's parser whether the text is code; on long prose CPython gives up with MemoryError
("Parser stack overflowed - Python source too complex to parse"), which nothing caught, so the
whole turn stopped. Text the parser cannot read is simply not code.
"""
from __future__ import annotations

import pytest

from core import chat_attachments as ca
from core import runtime_paths
from core import tool_call_recovery as tcr

SESSION = "openclaw:long-paste-bbbbbbbbbbbbb"
# A long run of words with no punctuation, as dictation or a pasted keyword list gives.
PROSE = " ".join(["river", "kept", "rising", "through", "night", "nobody", "slept"] * 260)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


def test_the_parser_really_overflows_on_this_prose() -> None:
    import ast

    with pytest.raises(MemoryError):
        ast.parse(PROSE)


def test_long_prose_is_typed_as_text() -> None:
    assert ca.document_type_for(PROSE)[2] == "text"


def test_long_prose_is_kept_as_a_document_without_ending_the_turn() -> None:
    bound = ca.retain_inline_message(session_id=SESSION, turn_id="turn-long-1", text=PROSE)
    assert bound, "the long paste should be kept as a chat document"


def test_real_python_is_still_typed_as_python() -> None:
    code = "import os\n\n\ndef main():\n    return os.getcwd()\n" * 3
    assert ca.document_type_for(code)[2] == "python"


def test_long_model_prose_is_not_read_as_a_python_tool_call() -> None:
    assert tcr._candidates_from_python(PROSE) is None
