"""An audit finding the operator cannot check is not a finding.

From a live drive on 2026-08-01 against `~/Desktop/openclaw-skills`, asking for the highest-risk bug
in `api/apache/liquefy_apache_repetition_v1.py` under the explicit rule *"Cite the exact file and
line range."* The reply cited **lines 147-160** for "the decompression loop reconstructs patterns by
iterating only while `p_ip < len(c_ip)` (line 147)" and "the helper `next_v` (line 152)".

In the real 210-line file that loop is at **132**, `next_v` is at **137**, and 147-160 is a different
block entirely -- the `raise ValueError` arms of the RLE expander. The mechanism described was real
code; every coordinate pointing at it was invented. Three causes, each verified by execution:

* The excerpt handed to the model is read with ``verbatim=True``
  (`workspace_audit.py`), which is the branch of `_read_file` that renders the body with **no line
  numbers**. The prompt then says "cite line content" while the model has no line to cite. It
  guessed, and a guess inside a 210-line file is indistinguishable from a citation.
* `audit_claim_verifier` only range-checks a citation (`line > total`), so 147 in a 210-line file
  passes. The verifier cannot catch what the excerpt never made decidable.
* The follow-up turn -- *"Prove the bug you identified"* -- reached the build lane with **no
  subject**. `_audited_subject_paths` reads `workspace_audit_evidence`, which
  `run_workspace_audit` stashes on the *current* turn's `source_context`; the API rebuilds that dict
  per request, so on the next turn it is gone. Measured: `()`. The builder invented a subject and
  wrote an unrelated path-traversal `app.py`.

The stored `sources` stay verbatim on purpose -- `audit_claim_verifier.line_at` indexes them with
`splitlines()[n - 1]`, so numbering them at the source would corrupt the verifier. Numbering happens
at render time, for the model only.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.agent_runtime.builder.app_builder import _pick_test_command
from core.agent_runtime.builder.controller import _audited_subject_paths
from core.agent_runtime.workspace_audit import audit_target_in, run_workspace_audit

# A file shaped like the one that was misreported: the interesting line sits well down the file, so
# a guessed number and the real number cannot coincide by luck.
CODEC = """\
#!/usr/bin/env python3
\"\"\"A codec.\"\"\"
import struct


class Codec:
    def __init__(self):
        self.state = 0

    def compress(self, raw: bytes) -> bytes:
        out = bytearray()
        for chunk in raw.split(b"\\n"):
            out.extend(struct.pack("<I", len(chunk)))
            out.extend(chunk)
        return bytes(out)

    def decompress(self, blob: bytes) -> bytes:
        out = bytearray()
        pos = 0
        while pos < len(blob):
            size = struct.unpack("<I", blob[pos:pos + 4])[0]
            pos += 4
            out.extend(blob[pos:pos + size])
            pos += size
        return bytes(out)
"""

# 1-indexed line of `while pos < len(blob):` in CODEC above.
LOOP_LINE = 20
LOOP_TEXT = "while pos < len(blob):"


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "codec.py").write_text(CODEC, encoding="utf-8")
    (tmp_path / "README.md").write_text("# Codec\n", encoding="utf-8")
    return tmp_path


def _audit(root: Path, request: str) -> str:
    from core.runtime_execution_tools import execute_runtime_tool

    report, _steps = run_workspace_audit(
        str(root),
        source_context={"workspace": str(root)},
        execute_tool=lambda intent, arguments=None, source_context=None, **kw: execute_runtime_tool(
            intent, arguments or {}, source_context=source_context
        ),
        emit=lambda *a, **k: None,
        target_path=audit_target_in(request),
    )
    return report


REQUEST = "audit api/codec.py and cite the exact file and line range for the worst bug"


def test_the_line_the_model_is_told_to_cite_is_a_line_it_can_see(project: Path) -> None:
    """The failure verbatim: the model was asked for a line range and shown unnumbered source."""

    report = _audit(project, REQUEST)
    assert "## Requested file" in report, "the named file must get its own section"
    # The real coordinate of the loop must be attached to the loop, in the text the model reads.
    numbered = [
        line
        for line in report.splitlines()
        if LOOP_TEXT in line and str(LOOP_LINE) in line.split(LOOP_TEXT)[0]
    ]
    assert numbered, (
        f"line {LOOP_LINE} carries `{LOOP_TEXT}` in the real file, but the excerpt handed to the "
        f"model does not attach that number to it -- so any line range it cites is a guess"
    )


def test_the_excerpt_instruction_asks_for_a_coordinate_not_a_quote(project: Path) -> None:
    """`cite line content` is satisfiable without a line number; the operator asked for a range."""

    report = _audit(project, REQUEST)
    section = report.split("## Requested file", 1)[1].split("```", 1)[0].lower()
    assert "line number" in section or "path:line" in section, (
        "the instruction must ask for a checkable coordinate, since the excerpt now carries one"
    )


def test_a_follow_up_build_knows_which_file_was_just_audited() -> None:
    """"Prove the bug you identified" arrives on a NEW turn; source_context is rebuilt per request.

    The audit's own stash is same-turn only, so the subject has to survive in what the next turn
    actually carries -- the conversation history the API stamps onto every request.
    """

    followup = {
        "surface": "api",
        "workspace": "/tmp/ws",
        "workspace_root": "/tmp/ws",
        "conversation_history": [
            {"role": "user", "content": "Inspect this workspace and audit: api/apache/codec_v1.py"},
            {"role": "assistant", "content": "Highest-risk bug: silent truncation on decompress."},
            {"role": "user", "content": "Prove the bug you identified before fixing it."},
        ],
    }
    assert _audited_subject_paths(followup) == ("api/apache/codec_v1.py",), (
        "with no subject the builder invents one -- measured 2026-08-01, a request about an Apache "
        "codec produced an unrelated path-traversal app.py"
    )


def test_the_same_turn_stash_still_wins_when_it_is_there() -> None:
    """The history fallback must not displace real evidence from an audit in this same turn."""

    ctx = {
        "workspace_audit_evidence": {"inspected_paths": ("api/real_target.py",)},
        "conversation_history": [{"role": "user", "content": "audit other/decoy.py"}],
    }
    assert _audited_subject_paths(ctx) == ("api/real_target.py",)


def test_the_build_runs_the_tests_it_wrote_not_whatever_is_in_the_folder() -> None:
    """A re-run of the same request lands in the same folder, which is where this bit.

    `_unrooted_build_dir` digests the request text, so asking twice reuses one directory by design
    (a retry should overwrite its own attempt). But attempt 1 wrote `test_auth.py` and attempt 2
    wrote `test_app.py`, and `unittest discover` then ran BOTH -- so the reported run included a
    password-timing suite from a different question, and the receipt named files this build never
    wrote.
    """

    command = _pick_test_command(["generated/proof-abc123/test_app.py"])
    assert command, "a written test file must still produce a run"
    assert "discover" not in command, (
        "discover collects every test*.py in the reused directory, including a stale suite from an "
        "earlier attempt at the same request"
    )
    assert "test_app" in command, "the run must name the module this build actually wrote"


# --------------------------------------------------------------------------------------
# "Bug reproduced" is a claim about a FAILURE, not about an exit code
# --------------------------------------------------------------------------------------

# The verbatim run from 2026-08-01. Exit code 1, and the receipt read
# "✅ **Bug reproduced.** The test fails as required, so the finding holds."
# Nothing was reproduced: the test blew up before its assertion because its own fixture was never
# created, and the second error belongs to a password-timing suite left in the folder by an earlier
# attempt at the same request.
ERRORED_RUN = """\
Correct password mean: 7441.08 µs
Wrong password mean:   7460.34 µs

======================================================================
ERROR: test_path_traversal_reads_outside_user_directory (test_app.TestPathTraversal)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "test_app.py", line 68, in test_path_traversal_reads_outside_user_directory
    result = app.read_user_file('alice', '../../etc/passwd')
FileNotFoundError: [Errno 2] No such file or directory: 'users/alice/../../etc/passwd'

======================================================================
ERROR: test_wrong_password_fails_fast_on_comparison (test_auth.TestTimingAttackVulnerability)
----------------------------------------------------------------------
Traceback (most recent call last):
AssertionError: nope

----------------------------------------------------------------------
Ran 3 tests in 0.104s

FAILED (errors=2)
"""

# What an actual reproduction looks like: the test ran to its assertion and the assertion was false.
FAILING_RUN = """\
======================================================================
FAIL: test_truncated_column_corrupts_output (test_codec.CodecTests)
----------------------------------------------------------------------
Traceback (most recent call last):
    self.assertEqual(round_tripped, original)
AssertionError: b'GET /a' != b'GET /admin'

----------------------------------------------------------------------
Ran 1 test in 0.002s

FAILED (failures=1)
"""


def test_an_error_is_not_a_reproduction() -> None:
    """An ERROR means the test never reached its assertion. That proves nothing about the subject."""

    from core.agent_runtime.builder.app_builder import unittest_failure_counts

    assert unittest_failure_counts(ERRORED_RUN) == (0, 2)
    assert unittest_failure_counts(FAILING_RUN) == (1, 0)


@pytest.mark.parametrize(
    "output, returncode, proven",
    [
        (FAILING_RUN, 1, True),
        (ERRORED_RUN, 1, False),
        ("", None, False),
    ],
    ids=["a real assertion failure proves it", "errors prove nothing", "a run that never happened"],
)
def test_only_a_real_failure_counts_as_proof(output: str, returncode: int | None, proven: bool) -> None:
    from core.agent_runtime.builder.app_builder import proof_holds

    assert proof_holds(output=output, returncode=returncode) is proven


def test_the_receipt_never_claims_a_bug_it_did_not_reproduce() -> None:
    """The live receipt claimed proof twice: once on an errored run, once before the command ran.

    The first round sat at "Manual mode requires approval for this exact action" -- the test command
    had not executed at all -- and the reply still opened with "✅ Bug reproduced."
    """

    from core.agent_runtime.builder.app_builder import AppBuildReport, render_app_build_response

    errored = AppBuildReport(
        handled=True,
        target_dir="generated/proof",
        expected_test_outcome="fail",
        test_returncode=1,
        files_written=["generated/proof/test_app.py"],
    )
    errored.tests_ran = True
    errored.test_output = ERRORED_RUN
    errored.test_command = "python -m unittest -v test_app"
    errored.proof_failed = True
    errored.proof_note = "errored"
    text = render_app_build_response(errored)
    assert "Bug reproduced" not in text, "an errored suite is not a reproduction"
    assert "error" in text.lower(), "the reply must say the test errored rather than failed"

    never_ran = AppBuildReport(
        handled=True,
        target_dir="generated/proof",
        expected_test_outcome="fail",
        test_returncode=None,
        files_written=["generated/proof/test_app.py"],
    )
    never_ran.tests_ran = True
    never_ran.test_output = "Manual mode requires approval for this exact action."
    never_ran.test_command = "python -m unittest -v test_app"
    never_ran.proof_failed = True
    never_ran.proof_note = "did_not_run"
    text = render_app_build_response(never_ran)
    assert "Bug reproduced" not in text, "a command awaiting approval reproduced nothing"


def _drive_build(test_output: str, returncode: int | None):
    """Drive the REAL `build_app_from_spec` with a scripted model and runner.

    Written after a sabotage went unnoticed: reverting the wiring to `proof_failed = passed` broke
    nothing, because the tests above cover the pure verdict function and the renderer but never the
    line that joins them. A build lane is where the two meet, so that is where this has to run.
    """

    from core.agent_runtime.builder.app_builder import ToolOutcome, build_app_from_spec

    def generate_fn(prompt: str) -> str:
        if "test_codec.py" in prompt and "file list" not in prompt.lower():
            return "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.assertTrue(False)\n"
        if '["' in prompt or "file list" in prompt.lower() or "JSON" in prompt:
            return '["test_codec.py"]'
        return "import unittest\n"

    def run_tool_fn(intent, arguments=None, **kwargs):
        if intent == "sandbox.run_command":
            details = {} if returncode is None else {"returncode": returncode}
            return ToolOutcome(ok=returncode == 0, response_text=test_output, details=details)
        return ToolOutcome(ok=True, response_text="", details={})

    return build_app_from_spec(
        request="Prove the bug you identified before fixing it.",
        target_rel="generated/proof",
        source_context={"workspace": "/tmp/ws"},
        generate_fn=generate_fn,
        run_tool_fn=run_tool_fn,
        expected_test_outcome="fail",
    )


# A discover run that matched nothing still exits 0 and prints OK.
EMPTY_RUN = "\n----------------------------------------------------------------------\nRan 0 tests in 0.000s\n\nOK\n"


@pytest.mark.parametrize(
    "output, returncode, expect_reproduced, expect_note",
    [
        (FAILING_RUN, 1, True, ""),
        (ERRORED_RUN, 1, False, "errored"),
        ("Manual mode requires approval for this exact action.", None, False, "did_not_run"),
        (EMPTY_RUN, 0, False, "passed"),
    ],
    ids=["assertion failed", "suite errored", "command never ran", "zero tests collected"],
)
def test_the_build_lane_wires_the_verdict_into_the_receipt(
    output: str, returncode: int | None, expect_reproduced: bool, expect_note: str
) -> None:
    from core.agent_runtime.builder.app_builder import render_app_build_response

    report = _drive_build(output, returncode)
    assert report.files_written, "the scripted build must actually write its test file"
    assert report.proof_note == expect_note
    assert report.proof_failed is not expect_reproduced
    text = render_app_build_response(report)
    assert ("Bug reproduced" in text) is expect_reproduced, text.splitlines()[0]


def test_a_prove_it_run_that_collected_nothing_says_so_in_the_headline() -> None:
    """Zero tests collected used to print NO verdict line at all for a prove-it build.

    The headline block was gated on `ran_anything`, so the one state where the operator most needs
    telling -- the suite matched nothing, nothing was exercised -- produced a receipt that opened
    with the file table and left the verdict to be inferred.
    """

    from core.agent_runtime.builder.app_builder import render_app_build_response

    text = render_app_build_response(_drive_build(EMPTY_RUN, 0))
    headline = text.splitlines()[0]
    assert "Not proven" in headline and "no tests were collected" in headline, headline
