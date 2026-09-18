"""Naming two files and getting one, with no mention of the other, is a wrong answer.

`_WORKSPACE_READ_FILE_RE` is a `.search()`, so the direct-read lane bound the FIRST filename in a
message and discarded the rest without recording anything. Measured 2026-08-03:

    "read notes.txt and todo.txt"                        -> {'path': 'notes.txt'}
    "Read README.md and SECURITY.md ... what each one
     covers"                                             -> {'path': 'README.md'}

The reply then describes "each" of two files on the strength of having opened one. The comment
above `_EXPLICIT_READ_VERB_RE` (fast_paths_utility.py) had promised the opposite since it was
written — *"'read notes.txt and todo.txt' names two files joined by 'and' and is still two reads,
not a comparison"* — so the intent was never in doubt, only the delivery.

This is the same defect class as the local tool-batch drop fixed earlier on this branch, where
`_extract_ollama_tool_call_text` took `first` and the model answered as though the whole batch had
run. A partial answer presented as a complete one is what both cost the operator.

The cap is the same trap one file further along: reading four of six and saying nothing about the
other two would be this bug with a bigger number, so it is stated in the reply.
"""
from __future__ import annotations

import uuid

import pytest

import core.agent_runtime.fast_paths_utility as fp


def _workspace(tmp_path, files: dict[str, str]):
    for name, body in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return {
        "surface": "api",
        "workspace": str(tmp_path),
        "workspace_root": str(tmp_path),
        "workspace_binding": "project",
        "project_id": tmp_path.name,
    }


def _drive(tmp_path, prompt: str, files: dict[str, str]) -> str:
    """Through the real handler, not the planner — the planner passes even when nothing calls it."""

    captured: dict = {}

    class _Agent:
        def _fast_path_result(self, **kwargs):
            captured.update(kwargs)
            return {"output_text": kwargs.get("response") or ""}

        def _emit_runtime_event(self, source_context, *, event_type, message, **details):
            # Each read is recorded as typed Activity steps (tests/
            # test_workspace_read_is_auditable_in_activity.py). The real agent always carries this
            # method; this double needs it to reach the assertions below.
            return None

    handled = fp.maybe_handle_direct_workspace_runtime_request(
        _Agent(),
        prompt,
        session_id=f"openclaw:{uuid.uuid4().hex[:20]}",
        source_surface="api",
        source_context=_workspace(tmp_path, files),
    )
    assert handled is not None, "the read lane declined a request it used to claim"
    return str(captured.get("response") or "")


# --------------------------------------------------------------------------------------
# Both files are read.
# --------------------------------------------------------------------------------------


def test_both_named_files_are_read(tmp_path) -> None:
    """The measured failure. `todo.txt` was never opened and never mentioned."""

    out = _drive(
        tmp_path,
        "read notes.txt and todo.txt",
        {"notes.txt": "alpha note\n", "todo.txt": "TODO: ship it\n"},
    )

    assert "alpha note" in out
    assert "TODO: ship it" in out, "the second named file was dropped with nothing recorded"


def test_the_second_file_is_named_in_the_reply(tmp_path) -> None:
    """Content alone is not enough — the reader has to be able to tell which file it came from."""

    out = _drive(
        tmp_path,
        "Read README.md and SECURITY.md and tell me what each one covers",
        {"README.md": "# the readme\n", "SECURITY.md": "# reporting policy\n"},
    )

    assert "README.md" in out and "SECURITY.md" in out


def test_a_single_file_request_is_unchanged(tmp_path) -> None:
    """The control. 521 of the 700 prompts that reach this lane name exactly one file."""

    out = _drive(tmp_path, "read notes.txt", {"notes.txt": "alpha note\n"})

    assert "alpha note" in out
    assert out.count("File `") == 1


def test_a_missing_second_file_does_not_lose_the_first(tmp_path) -> None:
    """A read that fails is a reported failure, not a reason to drop the file that worked."""

    out = _drive(tmp_path, "read notes.txt and absent.txt", {"notes.txt": "alpha note\n"})

    assert "alpha note" in out
    assert "absent.txt" in out


# --------------------------------------------------------------------------------------
# The cap states itself.
# --------------------------------------------------------------------------------------


def test_files_beyond_the_cap_are_named_rather_than_dropped(tmp_path) -> None:
    """Reading four of six silently would be this same bug with a larger number."""

    files = {f"f{i}.py": f"value = {i}\n" for i in range(1, 7)}
    out = _drive(tmp_path, "cat f1.py and f2.py and f3.py and f4.py and f5.py and f6.py", files)

    assert "value = 1" in out and "value = 4" in out
    assert "f5.py" in out and "f6.py" in out, "the files past the cap vanished without a word"
    assert "not read" in out.lower()


def test_no_cap_notice_when_nothing_was_capped(tmp_path) -> None:
    """A notice about files nobody asked for reads as a failure that did not happen."""

    out = _drive(tmp_path, "read a.py and b.py", {"a.py": "x = 1\n", "b.py": "y = 2\n"})

    assert "not read" not in out.lower()


# --------------------------------------------------------------------------------------
# The planner, directly. Boundaries the handler test cannot isolate.
# --------------------------------------------------------------------------------------


def test_a_rooted_path_is_skipped_but_does_not_sink_the_turn() -> None:
    """A host path belongs to the machine-read lane; the workspace file in the same sentence
    should still be answered rather than the whole turn standing down."""

    plan = fp._direct_workspace_read_requests("read notes.txt and /etc/hosts")

    assert [item["path"] for item in plan] == ["notes.txt"]


def test_a_repeated_filename_is_read_once() -> None:
    plan = fp._direct_workspace_read_requests("read notes.txt and notes.txt again")

    assert [item["path"] for item in plan] == ["notes.txt"]


def test_the_display_budget_is_shared_not_multiplied() -> None:
    """Four files must not emit four full modules; one file keeps the tool's own default."""

    one = fp._direct_workspace_read_requests("read solo.py")
    four = fp._direct_workspace_read_requests("cat a.py and b.py and c.py and d.py")

    assert one[0]["max_lines"] == fp._READ_DISPLAY_LINES
    assert sum(item["max_lines"] for item in four) <= fp._READ_DISPLAY_LINES
    assert all(item["max_lines"] >= fp._MIN_SHARED_READ_LINES for item in four), (
        "a share this thin truncates every file to its imports, which answers nobody"
    )


@pytest.mark.parametrize("prompt", ["read pdf_rebuild.py", "what is in notes.txt"])
def test_a_single_file_keeps_the_tools_own_default(prompt: str) -> None:
    """160 until 2026-08-03: this lane overrode `_READ_FILE_DEFAULT_LINES` (2000) downward with no
    stated reason, so a 350-line module returned its first 160 lines under `handled=True`."""

    plan = fp._direct_workspace_read_requests(prompt)

    assert plan[0]["max_lines"] == 2000
