"""CI #681 forge journey: after a draft pull request opened, the model's whole answer was "done"
and that is what the person saw. A closing word alone is replaced by the tool's own result; any
answer with content of its own is kept (tests/repoops/test_forge_served_journey.py is the served
proof)."""
from __future__ import annotations

import pytest

from core.agent_runtime.research_tool_loop_facade import _BARE_CLOSING_RE


@pytest.mark.parametrize("text", ["done", "Done.", "ok", "Okay!", "all done", "Finished.", "completed", "It's done."])
def test_a_closing_word_alone_says_nothing(text):
    assert _BARE_CLOSING_RE.match(text)


@pytest.mark.parametrize("text", [
    "Done: the draft pull request #31 is open.",
    "ok, I opened it at https://github.local/o/r/pull/31",
    "Finished reading the file; it has 3 lines.",
    "Not done yet: the forge refused.",
])
def test_an_answer_with_content_is_kept(text):
    assert not _BARE_CLOSING_RE.match(text)
