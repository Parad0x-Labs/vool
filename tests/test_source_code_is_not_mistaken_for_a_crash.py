"""A file the operator asked to read is not a crash dump just because it handles errors.

Measured live 2026-08-03. The operator sent `pdf_rebuild.py in depth anylyse this for me` and got
back, in 6ms with no model call:

    I couldn't resolve that cleanly.

Nothing failed. `workspace.read_file` ran, returned `ok=True`, and rendered the source correctly.
The chat sanitizer then threw the whole thing away, because `looks_like_runtime_traceback` scored
any multi-line text containing `error:` and (`line ` or `exception`) as runtime crash output — and
that is a description of ordinary Python with a `try/except` in it. The measured file tripped all
three: `error:` once inside a print, `exception` twice in its handlers, `line ` once.

README.md in the same folder read back fine, which is why the failure looked intermittent rather
than categorical: markdown does not contain those words, and Python routinely does.

The guard itself earns its place — it stops VOOL leaking its own stack traces into chat, and
`tests/test_an_audit_cannot_monologue_its_way_out.py` pins a report that was eaten the same way on
2026-08-01. The defect is that it matched LEXICALLY, on words appearing anywhere in a blob. Python
emits an exception with the class name STARTING a line, so the test is structural now.
"""
from __future__ import annotations

import uuid

import pytest

from core.agent_runtime.response import looks_like_runtime_traceback

# Every trigger word, used the way source code uses them: mid-line, inside strings and handlers.
SOURCE_WITH_ERROR_HANDLING = '''\
File `pdf_rebuild.py`:
1: import sys
2:
3:
4: def rebuild(path):
5:     """Rebuild a PDF, one line at a time."""
6:     try:
7:         with open(path, "rb") as handle:
8:             return handle.read()
9:     except OSError as exc:
10:         print(f"error: cannot read {path}: {exc}")
11:         raise
12:
13:
14: def main(argv):
15:     # An exception here is fatal; an exception below is not.
16:     if len(argv) < 2:
17:         print("usage: pdf_rebuild.py FILE")
18:         return 2
19:     return 0
'''


# --------------------------------------------------------------------------------------
# The heuristic. Source in one direction, real crashes in the other.
# --------------------------------------------------------------------------------------


def test_python_source_that_handles_errors_is_not_a_traceback() -> None:
    """The measured failure, reduced. This returned True and cost the operator their answer."""

    assert not looks_like_runtime_traceback(SOURCE_WITH_ERROR_HANDLING)


@pytest.mark.parametrize(
    "source",
    [
        'log.write("error: disk full")\nreturn 1\n# line 4 of the header\n',
        "def f():\n    raise ValueError('bad')\n# raising an exception is not having one\n",
        "1: try:\n2:     pass\n3: except Exception:\n4:     print('error: nope')\n",
    ],
)
def test_code_that_merely_mentions_errors_survives(source: str) -> None:
    assert not looks_like_runtime_traceback(source)


@pytest.mark.parametrize(
    "crash",
    [
        'Traceback (most recent call last):\n  File "a.py", line 3\nValueError: bad',
        '  File "/x/y.py", line 42, in run\n    raise ValueError\nValueError: nope',
        # No `File "..."` header at all. This form is the ONLY reason the loose branch exists, so
        # deleting the branch instead of tightening it would have shipped a leak.
        "  File missing\nSyntaxError: invalid syntax\n    ^",
        "reading config\njson.decoder.JSONDecodeError: Expecting value: line 1\nabort",
        "worker died\nRuntimeWarning: coroutine was never awaited\nshutting down",
    ],
)
def test_a_real_crash_is_still_caught(crash: str) -> None:
    """The protection this guard exists for. A regression here leaks internals into chat."""

    assert looks_like_runtime_traceback(crash)


def test_the_word_error_alone_does_not_convict() -> None:
    """`Error:` has to open a line AND name something. A bare colon is prose."""

    assert not looks_like_runtime_traceback("Error:\n\nsomething went wrong\nsee above")


# --------------------------------------------------------------------------------------
# The wiring. The tests above pass whether or not the sanitizer is the thing that eats it.
# --------------------------------------------------------------------------------------


def test_the_sanitizer_returns_the_file_instead_of_the_canned_sentence() -> None:
    """Driven through `sanitize_user_chat_text`, which is what actually replaced the answer.

    Asserting on the heuristic alone passes even if some other branch of the sanitizer eats the
    same text — and the canned sentence has four separate sources in this module. This pins the
    one the operator hit: content in, content out.
    """

    from apps.vool_agent import ResponseClass as ChatResponseClass
    from core.agent_runtime.tool_result_text_surface import ToolResultTextSurfaceMixin

    # The real mixin, not a stub with hand-written helpers: `sanitize_user_chat_text` calls back
    # into `_strip_runtime_preamble` and `_unwrap_summary_or_action_payload`, and a stub that
    # returned its input unchanged would be testing the stub.
    class _Agent(ToolResultTextSurfaceMixin):
        ResponseClass = ChatResponseClass

    cleaned = _Agent()._sanitize_user_chat_text(
        SOURCE_WITH_ERROR_HANDLING,
        response_class=ChatResponseClass.UTILITY_ANSWER,
    )

    assert "def rebuild" in cleaned, (
        "the sanitizer replaced a successful file read with a generic failure sentence"
    )
    assert "couldn't" not in cleaned.lower()


def test_a_real_workspace_read_reaches_the_operator(tmp_path) -> None:
    """End to end through the fast path that served the measured turn.

    The sanitizer test above uses a hand-written render. This one writes a real file, asks for it
    the way the operator did, and asserts the source comes back — so a change to how reads are
    rendered cannot quietly reintroduce the same collision.
    """

    import core.agent_runtime.fast_paths_utility as fp

    (tmp_path / "pdf_rebuild.py").write_text(
        "def rebuild(path):\n"
        "    try:\n"
        "        return open(path, 'rb').read()\n"
        "    except OSError as exc:\n"
        '        print(f"error: {exc}")\n'
        "        raise\n"
        "# an exception on line 3 is handled\n",
        encoding="utf-8",
    )

    captured: dict = {}

    class _Agent:
        def _fast_path_result(self, **kwargs):
            captured.update(kwargs)
            return {"output_text": kwargs.get("response") or kwargs.get("fallback_response") or ""}

        def _emit_runtime_event(self, source_context, *, event_type, message, **details):
            # The read is recorded as typed Activity steps (tests/
            # test_workspace_read_is_auditable_in_activity.py). The real agent always carries this
            # method; this double needs it to reach the assertion below.
            return None

    handled = fp.maybe_handle_direct_workspace_runtime_request(
        _Agent(),
        "read pdf_rebuild.py",
        session_id=f"openclaw:{uuid.uuid4().hex[:20]}",
        source_surface="api",
        source_context={
            "surface": "api",
            "workspace": str(tmp_path),
            "workspace_root": str(tmp_path),
            "workspace_binding": "project",
            "project_id": tmp_path.name,
        },
    )

    assert handled is not None, "the read path no longer claims this request"
    rendered = " ".join(str(value) for value in captured.values())
    assert "def rebuild" in rendered
    assert not looks_like_runtime_traceback(rendered), (
        "a real file read still scores as crash output, so the sanitizer will eat it downstream"
    )
