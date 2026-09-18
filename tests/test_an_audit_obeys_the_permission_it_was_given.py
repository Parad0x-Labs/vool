"""An audit does what it was permitted to do, says only what it proved, and never falls out of
its own lane.

These pin the P0 repair of the audit/proof drift incident. The frozen incident truth, from
AGENT_HANDOVER.md §1A:

* the read-only audit turn WROTE a generated test and ran it;
* a proof test that EXITED 0 disproved the candidate, and the run stopped there and then
  contradicted the disproof in prose ("NOT reproduced" under a "Highest-risk bug" headline);
* the follow-up "Prove the bug you identified before fixing it." was misrouted into the generic
  app builder instead of resuming the same audit;
* every dead end returned `None` into the old single-shot lane, which is the hallucination path
  the stepped lane exists to close.

The behaviour asserted here is driven through the real `run_stepped_audit` with a scripted
provider and a scripted tool executor, so a test passes because the runtime did the thing — not
because a mirror of the runtime did.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from core.agent_runtime.stepped_audit import run_stepped_audit

# A subject shaped like the incident's Apache codec: a bare-except decode loop that swallows a
# malformed record, plus an empty-input branch that does NOT crash (`bytes.startswith` on b"" is
# False, never IndexError) — the refuted hypothesis, present so it can be nominated and disproved.
_CODEC_LINES = (
    ["#!/usr/bin/env python3", '"""A repetition codec."""', "import struct", "", "class Codec:"]
    + [f"    def helper_{i}(self):" if i % 2 else f"        return {i}" for i in range(6, 28)]
    + [
        "    def decompress(self, blob):",
        "        if blob.startswith(b'RPT1'):",
        "            blob = blob[4:]",
        "        out = bytearray()",
        "        while blob:",
        "            try: pid = blob[0]",
        "            except Exception: break",
        "            out.extend(blob[:pid])",
        "            blob = blob[pid:]",
        "        return bytes(out)",
    ]
)
CODEC = "\n".join(_CODEC_LINES)
TARGET = "api/apache/liquefy_apache_repetition_v1.py"

_TRUNCATION_LINE = "            except Exception: break"
_EMPTY_INPUT_LINE = "        if blob.startswith(b'RPT1'):"
TRUNCATION_LINE_NO = _CODEC_LINES.index(_TRUNCATION_LINE) + 1
EMPTY_INPUT_LINE_NO = _CODEC_LINES.index(_EMPTY_INPUT_LINE) + 1

MANIFEST = SimpleNamespace(
    provider_id="openrouter-byok:nvidia/nemotron",
    model_name="nvidia/nemotron-3-ultra-550b-a55b:free",
)

# The exact incident prompt shape: inspect and audit, explicitly without modifying anything.
READ_ONLY_PROMPT = (
    "Inspect this workspace and audit " + TARGET + " — find the single highest-risk real bug. "
    "Do not modify anything, and use only what is on this machine."
)
# The exact follow-up from acceptance gate C.
PROVE_FOLLOW_UP = "Prove the bug you identified before fixing it."
PROVE_WITH_PRODUCTION_BAN = (
    "Prove the bug you identified before fixing it. Create the smallest deterministic regression "
    "test that reproduces the failure. Rules: Do not modify production code yet. Use a temporary "
    "test file or the project's existing test structure. Run the test locally. Show the exact "
    "command. Show the relevant failing output. If the test does not reproduce the claimed bug, "
    "admit the finding was wrong and investigate again."
)
# One message that both opens the audit and authorizes the proof — three model calls in one turn,
# which is what the usage-completeness gate needs to measure.
AUDIT_AND_PROVE_PROMPT = (
    "Audit " + TARGET + " and prove the single highest-risk bug with a failing test."
)


def _finding(*, title, line_no, line_text, scenario):
    return json.dumps(
        {
            "title": title,
            "file": TARGET,
            "line_start": line_no,
            "line_end": line_no + 2,
            "cited_line_text": line_text,
            "failure_scenario": scenario,
        }
    )


TRUNCATION_FINDING = _finding(
    title="Malformed record silently truncates the output",
    line_no=TRUNCATION_LINE_NO,
    line_text=_TRUNCATION_LINE,
    scenario="A trailing 0x80 varint after a valid record makes the bare except break the loop, "
    "so decompress returns the first 89 bytes and raises nothing.",
)
# The REFUTED hypothesis from the frozen incident truth. bytes.startswith(b"") is False; it does
# not raise. Any proof test for it passes, which disproves it.
EMPTY_INPUT_FINDING = _finding(
    title="Empty input crashes at startswith",
    line_no=EMPTY_INPUT_LINE_NO,
    line_text=_EMPTY_INPUT_LINE,
    scenario="decompress(b'') indexes an empty buffer at startswith and raises IndexError.",
)

EMPTY_INPUT_REJECTED = json.dumps(
    {
        "verdict": "refuted",
        "reason": "The cited operation is bytes.startswith, not indexing; empty bytes return False.",
        "counterexample": "b''.startswith(b'RPT1') returns False without raising.",
    }
)
TRUNCATION_SUPPORTED = json.dumps(
    {
        "verdict": "supported",
        "reason": "The cited exception handler breaks the decode loop and the function returns the prefix.",
        "counterexample": "A truncated record reaches the handler after earlier output was appended.",
    }
)

# The exact live failure shape: the only cited source is the compressor's empty-input return at
# line 55, while the scenario silently relies on a different, uncited line in decompress(). A
# source-backed citation is not source-backed causality.
UNCITED_EMPTY_INPUT_FINDING = _finding(
    title="Decompression fails on empty input due to missing early return",
    line_no=EMPTY_INPUT_LINE_NO,
    line_text=_EMPTY_INPUT_LINE,
    scenario=(
        f"The cited branch at line {EMPTY_INPUT_LINE_NO} accepts empty bytes. The failure then "
        f"depends on an uncited operation at line {EMPTY_INPUT_LINE_NO + 20}, which allegedly "
        "indexes the empty buffer and raises IndexError."
    ),
)

FUTURE_ONLY_FINDING = _finding(
    title="Decompression fails when the chunk format changes",
    line_no=TRUNCATION_LINE_NO,
    line_text=_TRUNCATION_LINE,
    scenario=(
        "If the serialization ever changes to emit more chunks, or if future versions add "
        "metadata chunks, this hardcoded count will misalign parsing and corrupt output."
    ),
)

PERFORMANCE_ONLY_FINDING = _finding(
    title="Regex fallback loses deduplication benefit",
    line_no=EMPTY_INPUT_LINE_NO,
    line_text=_EMPTY_INPUT_LINE,
    scenario=(
        "A log line with trailing spaces takes the raw fallback, inflating compressed size and "
        "losing the deduplication benefit for that line. Decompression still returns every byte."
    ),
)

_EXTEND_LINE = "            out.extend(blob[:pid])"
_ADVANCE_LINE = "            blob = blob[pid:]"
EXTEND_FINDING = _finding(
    title="Unbounded slice on a short buffer",
    line_no=_CODEC_LINES.index(_EXTEND_LINE) + 1,
    line_text=_EXTEND_LINE,
    scenario="A pid larger than the remaining buffer copies fewer bytes than the record claims.",
)
ADVANCE_FINDING = _finding(
    title="Zero-length record spins the loop",
    line_no=_CODEC_LINES.index(_ADVANCE_LINE) + 1,
    line_text=_ADVANCE_LINE,
    scenario="A pid of 0 never advances the cursor, so the loop never terminates.",
)

# One reproduction artifact per candidate claim. A generic stand-in is no longer a valid stand-in:
# the runtime now checks an artifact against the claim it is supposed to prove BEFORE writing it,
# so a "test" that exercises some other behaviour is rejected — which is the whole repair. Each of
# these exercises the input its own claim names and asserts the CORRECT contract, so it fails while
# the claimed bug is present.
TEST_FILE = (
    "class TestBug(unittest.TestCase):\n"
    "    def test_it(self):\n"
    "        self.assertEqual(subject.Codec().decompress(b'\\x80'), b'raise instead')\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    unittest.main()\n"
)
EMPTY_INPUT_TEST_FILE = (
    "class TestEmptyInput(unittest.TestCase):\n"
    "    def test_empty_input_decompresses_without_crashing(self):\n"
    "        self.assertEqual(subject.Codec().decompress(b''), b'')\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    unittest.main()\n"
)
EXTEND_TEST_FILE = (
    "class TestShortBuffer(unittest.TestCase):\n"
    "    def test_a_pid_larger_than_the_remaining_buffer_copies_every_claimed_byte(self):\n"
    "        out = subject.Codec().decompress(b'\\x20ab')\n"
    "        self.assertEqual(len(out), 32)\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    unittest.main()\n"
)
ADVANCE_TEST_FILE = (
    "class TestZeroLengthRecord(unittest.TestCase):\n"
    "    def test_a_zero_pid_record_does_not_spin_the_loop(self):\n"
    "        self.assertEqual(subject.Codec().decompress(b'\\x00rest'), b'')\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    unittest.main()\n"
)

# The exact polarity error emitted by the live cloud proof turn. The first method asserts the
# REPORTED BUG as the expected behaviour, while the second asserts the correct contract. When the
# first assertion fails and the second passes, unittest exits 1 — but that failure DISPROVES the
# exception claim. A proof artifact with competing or backwards oracles must never be executed.
BACKWARDS_EMPTY_INPUT_TEST_FILE = (
    "class TestEmptyInputDecompression(unittest.TestCase):\n"
    "    def test_decompress_empty_bytes_raises_index_error(self):\n"
    "        codec = liquefy_apache_repetition_v1.Codec()\n"
    "        with self.assertRaises(IndexError):\n"
    "            codec.decompress(b'')\n"
    "\n"
    "    def test_decompress_empty_bytes_should_return_empty_bytes(self):\n"
    "        codec = liquefy_apache_repetition_v1.Codec()\n"
    "        result = codec.decompress(b'')\n"
    "        self.assertEqual(result, b'')\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    unittest.main()\n"
)

REWORDED_EMPTY_INPUT_FINDING = _finding(
    title="Startswith crashes while decompressing an empty input",
    line_no=EMPTY_INPUT_LINE_NO,
    line_text=_EMPTY_INPUT_LINE,
    scenario=(
        "Passing zero bytes reaches the prefix check and throws IndexError before decoding."
    ),
)
SYNTHESIS = "The decoder swallows malformed records and returns a short buffer with no exception."

FAILING_RUN = "test_it ... FAIL\n\nRan 1 test in 0.002s\n\nFAILED (failures=1)"
GREEN_RUN = "test_it ... ok\n\nRan 1 test in 0.002s\n\nOK"


class _ScriptedRouter:
    """`_invoke_manifest` with queued replies; records every ModelRequest it was handed."""

    def __init__(self, replies, *, manifest=MANIFEST):
        self.replies = list(replies)
        self.requests = []
        self.manifest = manifest

    def _requested_model_manifest(self, _context):
        return self.manifest

    def _invoke_manifest(self, *, manifest, request, output_mode, task, source_context):
        self.requests.append(request)
        step = str(dict(getattr(request, "metadata", None) or {}).get("stepped_audit_step") or "")
        item = None
        if step == "challenge":
            # Most tests in this module exercise a boundary other than semantic falsification. Give
            # their already-source-backed fixture a default supported review without consuming the
            # next scripted nomination/proof reply. Tests of the challenge itself provide an
            # explicit JSON verdict, which is consumed normally.
            if self.replies:
                possible = self.replies[0]
                possible_text = possible.get("text", "") if isinstance(possible, dict) else str(possible)
                try:
                    possible_json = json.loads(possible_text)
                except (TypeError, ValueError, json.JSONDecodeError):
                    possible_json = None
                if isinstance(possible_json, dict) and possible_json.get("verdict"):
                    item = self.replies.pop(0)
            if item is None:
                item = TRUNCATION_SUPPORTED
        elif self.replies:
            item = self.replies.pop(0)
        if item is None:
            return (None, None, "script_exhausted")
        if isinstance(item, str) and item.startswith("ERROR:"):
            return (None, None, item[len("ERROR:") :])
        text = item["text"] if isinstance(item, dict) else str(item)
        if isinstance(item, dict) and "usage" in item:
            usage = item["usage"]
        else:
            usage = {"prompt_tokens": 100, "completion_tokens": 20}
        return (
            None,
            SimpleNamespace(
                output_text=text,
                usage=usage,
                provider_id=manifest.provider_id,
                model_name=manifest.model_name,
                model_call_id="call-1",
                response_id="resp-1",
            ),
            None,
        )

    def steps(self):
        return [
            str(dict(getattr(request, "metadata", None) or {}).get("stepped_audit_step") or "")
            for request in self.requests
        ]


class _ToolRunner:
    """`agent._execute_tool_intent` with a queue of sandbox verdicts; records every intent."""

    def __init__(self, *, runs=((1, FAILING_RUN),), write_ok=True):
        self.runs = list(runs)
        self.write_ok = write_ok
        self.calls = []

    def intents(self):
        return [intent for intent, _args in self.calls]

    def __call__(self, payload, **kwargs):
        intent = str(payload.get("intent") or "")
        self.calls.append((intent, dict(payload.get("arguments") or {})))
        if intent == "workspace.write_file" and not self.write_ok:
            return SimpleNamespace(
                ok=False, handled=True, response_text="", details={},
                status="approval_required", mode="tool_failed",
            )
        if intent == "sandbox.run_command":
            returncode, output = self.runs.pop(0) if self.runs else (1, FAILING_RUN)
            return SimpleNamespace(
                ok=returncode == 0, handled=True, response_text=output,
                details={"returncode": returncode}, status="executed", mode="tool_executed",
            )
        return SimpleNamespace(
            ok=True, handled=True, response_text="", details={}, status="executed",
            mode="tool_executed",
        )


def _evidence_context(**overrides):
    context = {
        "workspace_audit_evidence_collected": True,
        "workspace_audit_evidence": {
            "all_paths": (TARGET, "README.md"),
            "inspected_paths": (TARGET,),
            "sources": {TARGET: CODEC},
            "workspace_root": "/tmp/audited-ws",
            "incomplete_files": (),
        },
        "requested_model": MANIFEST.model_name,
    }
    context.update(overrides)
    return context


def _drive(replies, *, prompt=READ_ONLY_PROMPT, tool_runner=None, context=None, session_id="audit-1"):
    router = _ScriptedRouter(replies)
    tools = tool_runner if tool_runner is not None else _ToolRunner()
    agent = SimpleNamespace(
        memory_router=router,
        _execute_tool_intent=tools,
        hive_activity_tracker=None,
        public_hive_bridge=None,
    )
    decision = run_stepped_audit(
        agent,
        task=SimpleNamespace(task_id="task-audit"),
        effective_input=prompt,
        source_context=context if context is not None else _evidence_context(),
        session_id=session_id,
    )
    return decision, router, tools


def _stepped(decision):
    return dict(dict(getattr(decision, "details", None) or {}).get("stepped_audit") or {})


def _report(decision):
    return str(getattr(decision, "output_text", "") or "")


# --------------------------------------------------------------------------------------
# Gate A / rule 2 — a read-only audit is byte-for-byte read-only
# --------------------------------------------------------------------------------------


def test_a_read_only_audit_writes_nothing_and_runs_no_command() -> None:
    """The incident: the audit turn wrote generated/<slug>/test_*.py and ran it, unasked."""
    decision, _router, tools = _drive([TRUNCATION_FINDING, TEST_FILE, SYNTHESIS])

    assert decision is not None
    mutating = [
        intent
        for intent in tools.intents()
        if intent in {"workspace.ensure_directory", "workspace.write_file", "sandbox.run_command"}
    ]
    assert mutating == [], f"a read-only audit performed mutating tool calls: {mutating}"


def test_a_read_only_audit_never_reaches_the_prove_step() -> None:
    _decision, router, _tools = _drive([TRUNCATION_FINDING, TEST_FILE, SYNTHESIS])

    assert "prove" not in router.steps(), (
        "the test-writing model call ran during an explicitly read-only audit"
    )


def test_the_tool_boundary_denies_a_read_only_audits_write_even_if_called_directly(tmp_path) -> None:
    """Permission must be enforced by the tool boundary, not only by the happy-path driver."""
    from core.agent_runtime.audit_policy import READ_ONLY_AUDIT
    from core.runtime_execution_tools import execute_runtime_tool

    result = execute_runtime_tool(
        "workspace.ensure_directory",
        {"path": "must-not-exist"},
        source_context={
            "workspace": str(tmp_path),
            "audit_execution_policy": READ_ONLY_AUDIT.as_dict(),
        },
    )

    assert result is not None
    assert result.status == "permission_denied"
    assert not (tmp_path / "must-not-exist").exists()


# --------------------------------------------------------------------------------------
# Gate B / rule 3 — a candidate is not a bug
# --------------------------------------------------------------------------------------


def test_an_unproven_candidate_is_labelled_candidate_unproven() -> None:
    decision, _router, _tools = _drive([TRUNCATION_FINDING, TEST_FILE, SYNTHESIS])

    assert _stepped(decision).get("terminal_state") == "candidate_unproven"


def test_an_unproven_candidate_is_never_called_the_bug() -> None:
    decision, _router, _tools = _drive([TRUNCATION_FINDING, TEST_FILE, SYNTHESIS])
    report = _report(decision).lower()

    for claim in ("highest-risk bug", "the bug is real", "reproduced by a failing test"):
        assert claim not in report, f"an unproven candidate was reported as {claim!r}"
    assert "candidate" in report
    assert "not proven" in report or "unproven" in report


def test_an_unproven_report_states_what_proof_remains() -> None:
    decision, _router, _tools = _drive([TRUNCATION_FINDING, TEST_FILE, SYNTHESIS])

    assert "what proof remains" in _report(decision).lower(), (
        "an unproven audit must state what proof remains, per rule 2"
    )


def test_an_unproven_footer_does_not_claim_an_executed_result() -> None:
    decision, _router, _tools = _drive([TRUNCATION_FINDING], session_id="truthful-footer")
    report = _report(decision).lower()

    assert "executed result" not in report
    assert "no proof command ran" in report


def test_a_read_only_audit_challenges_semantics_before_showing_a_candidate() -> None:
    """A matching citation cannot launder a false explanation into an operator-visible LIVE finding.

    This is the exact live hallucination: ``bytes.startswith`` was described as indexing an empty
    buffer and raising ``IndexError``. The audit must adversarially falsify that claim, exclude it
    from standing as a live candidate, and continue to a different source-supported candidate in
    the same read-only turn.

    UPDATED 2026-08-04 (Finding E point 6, RUNTIME_UPGRADE_LEDGER.md): challenge-refuted candidates
    now thread into the report's own "## Ruled out" section instead of only reaching internal
    telemetry (`details.stepped_audit.screened_out`) -- a real, executed falsification is no longer
    silently thrown away. This is the opposite failure mode from the incident: the runtime states,
    in its own words, that the claim was PROPOSED and DISPROVEN, and prints the correct
    counter-reasoning rather than the model's fabricated one. What must still never happen -- and
    is still pinned below -- is the ORIGINAL hallucinated MECHANISM (``IndexError`` on an empty
    buffer) reaching the report, and the disproved claim reading as a live, unrefuted finding
    rather than a ruled-out one.
    """
    decision, router, _tools = _drive(
        [
            EMPTY_INPUT_FINDING,
            EMPTY_INPUT_REJECTED,
            TRUNCATION_FINDING,
            TRUNCATION_SUPPORTED,
        ],
        session_id="semantic-challenge",
    )
    report = _report(decision).lower()

    assert router.steps() == ["nominate", "challenge", "nominate", "challenge"]
    assert _stepped(decision).get("terminal_state") == "candidate_unproven"
    # 2026-08-06: CANDIDATE_UNPROVEN no longer inlines the primary's own title into the chat
    # report (see that branch's own comment) -- which candidate won is checked against the
    # structured record instead, the same pattern already used for NO_FINDING elsewhere in this
    # file.
    assert "truncat" in str(_stepped(decision).get("finding", {}).get("title", "")).lower()
    # The fabricated MECHANISM must never reach the report, in either direction.
    assert "indexerror" not in report
    assert "indexes an empty buffer" not in report
    # 2026-08-06: CANDIDATE_UNPROVEN no longer inlines "## Ruled out"/"## Other candidates" into
    # chat at all (the whole candidate-by-candidate breakdown moved to Activity) -- the guarantee
    # this test exists for (a refuted claim is recorded as disproven, never as a live, unrefuted
    # finding) is checked against the structured `screened_out` record instead.
    screened = list(_stepped(decision).get("screened_out") or [])
    refuted_rows = [row for row in screened if "empty input crashes" in str(row.get("title", "")).lower()]
    if refuted_rows:
        assert all(row.get("verdict") == "refuted" for row in refuted_rows), (
            "the refuted claim must be recorded as refuted, never as a live finding: " + str(refuted_rows)
        )
        assert all(row.get("lifecycle_state") == "rejected" for row in refuted_rows), refuted_rows
    # And it must never BE the promoted primary either.
    assert "empty input crashes" not in str(_stepped(decision).get("finding", {}).get("title", "")).lower()


def test_an_absolute_never_extracted_claim_is_refuted_by_the_cited_assignment() -> None:
    source = "\n".join(
        [
            "def decompress(blob):",
            "    p = 0",
            "    chunks = []",
            "    for _ in range(10): chunks.append(get_chunk(p))",
            "    c_ip, c_ts, c_req, c_ref, c_ua, c_eol, s_pats, s_code, s_size, s_raw = chunks",
            "    out = bytearray()",
            "    out.extend(s_raw)",
            "    if not blob.startswith(b'UNI\\x01'): return b''",
            "    return bytes(out)",
        ]
    )
    false_claim = _finding(
        title="Decoder ignores the raw chunk",
        line_no=2,
        line_text="    p = 0",
        scenario=(
            "The loop reads ten chunks but assigns only through s_size. "
            "s_raw was never extracted from the payload, so raw lines are silently dropped."
        ),
    )
    false_obj = json.loads(false_claim)
    false_obj["line_end"] = 5
    false_claim = json.dumps(false_obj)
    current_bug = _finding(
        title="Unknown input silently becomes empty output",
        line_no=8,
        line_text="    if not blob.startswith(b'UNI\\x01'): return b''",
        scenario=(
            "decompress(b'not-a-container') returns empty bytes instead of rejecting the invalid "
            "input, so a caller can persist silent data loss as a successful decode."
        ),
    )
    current_obj = json.loads(current_bug)
    current_obj["line_end"] = 9
    current_bug = json.dumps(current_obj)

    decision, router, _tools = _drive(
        [false_claim, current_bug, TRUNCATION_SUPPORTED],
        context=_evidence_context(
            workspace_audit_evidence={
                "all_paths": (TARGET,),
                "inspected_paths": (TARGET,),
                "sources": {TARGET: source},
                "workspace_root": "/tmp/audited-ws",
                "incomplete_files": (),
            }
        ),
        session_id="absolute-never-claim",
    )
    report = _report(decision).lower()

    assert router.steps() == ["nominate", "nominate", "challenge"]
    # 2026-08-06: the winning primary's title no longer reaches chat for CANDIDATE_UNPROVEN.
    assert "unknown input silently" in str(_stepped(decision).get("finding", {}).get("title", "")).lower()
    assert "s_raw was never extracted" not in report


def test_valid_explicit_causal_lines_expand_the_canonical_citation_range() -> None:
    """The model may under-declare its range while naming another real line in the same source.

    The runtime owns citation truth, so it should expand the range to cover both lines and then
    challenge the combined mechanism. Rejecting a real line reference only creates a correction
    loop without improving truth.
    """
    finding = _finding(
        title="Malformed input silently truncates decompression",
        line_no=EMPTY_INPUT_LINE_NO,
        line_text=_EMPTY_INPUT_LINE,
        scenario=(
            f"The container branch at line {EMPTY_INPUT_LINE_NO} accepts the blob, then malformed "
            f"data reaches line {TRUNCATION_LINE_NO}, where the exception handler breaks and "
            "returns truncated output without an error."
        ),
    )
    decision, router, _tools = _drive(
        [finding, TRUNCATION_SUPPORTED],
        session_id="citation-expansion",
    )

    assert router.steps() == ["nominate", "challenge"]
    # 2026-08-06: the citation range and primary title no longer reach chat for CANDIDATE_UNPROVEN
    # -- checked against the structured finding record instead.
    stepped_finding = _stepped(decision).get("finding", {})
    assert stepped_finding.get("file", "").lower() == TARGET.lower()
    assert stepped_finding.get("line_start") == EMPTY_INPUT_LINE_NO
    assert stepped_finding.get("line_end") == TRUNCATION_LINE_NO
    assert "truncat" in str(stepped_finding.get("title", "")).lower()


def test_a_nonexistent_causal_line_is_rejected_before_semantic_challenge() -> None:
    decision, router, _tools = _drive(
        [UNCITED_EMPTY_INPUT_FINDING, TRUNCATION_FINDING, TRUNCATION_SUPPORTED],
        session_id="citation-nonexistent",
    )
    report = _report(decision).lower()

    assert router.steps() == ["nominate", "nominate", "challenge"]
    # 2026-08-06: CANDIDATE_UNPROVEN no longer inlines the primary's own title into the chat
    # report (see that branch's own comment) -- which candidate won is checked against the
    # structured record instead, the same pattern already used for NO_FINDING elsewhere in this
    # file.
    assert "truncat" in str(_stepped(decision).get("finding", {}).get("title", "")).lower()
    assert f"line {EMPTY_INPUT_LINE_NO + 20}" not in report


def test_a_future_only_format_change_cannot_be_reported_as_a_real_current_bug() -> None:
    """A contingent redesign is not a failing input for the code that exists now.

    The live weak model replaced its refuted empty-input claim with exactly this shape. Reject it
    deterministically before the same model can rubber-stamp its own speculation as supported.
    """
    decision, router, _tools = _drive(
        [FUTURE_ONLY_FINDING, TRUNCATION_FINDING, TRUNCATION_SUPPORTED],
        session_id="future-only-claim",
    )
    report = _report(decision).lower()

    assert router.steps() == ["nominate", "nominate", "challenge"]
    # 2026-08-06: CANDIDATE_UNPROVEN no longer inlines the primary's own title into the chat
    # report (see that branch's own comment) -- which candidate won is checked against the
    # structured record instead, the same pattern already used for NO_FINDING elsewhere in this
    # file.
    assert "truncat" in str(_stepped(decision).get("finding", {}).get("title", "")).lower()
    assert "future versions" not in report
    assert "serialization ever changes" not in report


def test_a_performance_only_observation_cannot_fill_an_output_integrity_audit() -> None:
    decision, router, _tools = _drive(
        [PERFORMANCE_ONLY_FINDING, TRUNCATION_FINDING, TRUNCATION_SUPPORTED],
        session_id="performance-only-claim",
    )
    report = _report(decision).lower()

    assert router.steps() == ["nominate", "nominate", "challenge"]
    # 2026-08-06: CANDIDATE_UNPROVEN no longer inlines the primary's own title into the chat
    # report (see that branch's own comment) -- which candidate won is checked against the
    # structured record instead, the same pattern already used for NO_FINDING elsewhere in this
    # file.
    assert "truncat" in str(_stepped(decision).get("finding", {}).get("title", "")).lower()
    assert "deduplication benefit" not in report


def test_rejected_future_claims_report_the_real_rejection_not_a_citation_mismatch() -> None:
    decision, router, _tools = _drive(
        [FUTURE_ONLY_FINDING, FUTURE_ONLY_FINDING, FUTURE_ONLY_FINDING],
        session_id="future-only-terminal",
    )
    report = _report(decision).lower()

    assert router.steps() == ["nominate", "nominate", "nominate"]
    assert _stepped(decision).get("terminal_state") in {"no_finding", "blocked"}
    assert "cited line did not match" not in report
    assert "future" in report
    assert "current source" in report


# --------------------------------------------------------------------------------------
# Gate C / rule 5 — the follow-up resumes THIS audit, it does not start a build
# --------------------------------------------------------------------------------------


def test_the_prove_follow_up_is_recognised_as_a_continuation_not_a_build() -> None:
    from core.agent_runtime.audit_session import audit_follow_up_resumes

    _decision, _router, _tools = _drive(
        [TRUNCATION_FINDING, TEST_FILE, SYNTHESIS], session_id="capsule-session"
    )

    assert audit_follow_up_resumes(PROVE_FOLLOW_UP, session_id="capsule-session") is not None


def test_the_follow_up_capsule_preserves_the_target_and_the_model() -> None:
    from core.agent_runtime.audit_session import load_audit_capsule

    _decision, _router, _tools = _drive(
        [TRUNCATION_FINDING, TEST_FILE, SYNTHESIS], session_id="capsule-target"
    )
    capsule = load_audit_capsule("capsule-target")

    assert capsule is not None
    assert capsule.target_path == TARGET
    assert capsule.model_name == MANIFEST.model_name
    assert capsule.source_hash, "the capsule must pin the inspected source identity"


def test_the_follow_up_is_bound_to_the_audit_lane_before_the_builder_sees_it() -> None:
    """The production routing, not the driver: the front-door audit gate rejects a sentence with no
    path in it, and the generic builder claimed the turn. It must not."""
    from core.agent_runtime.workspace_audit import maybe_handle_workspace_audit_request

    _decision, _router, _tools = _drive(
        [TRUNCATION_FINDING, TEST_FILE, SYNTHESIS], session_id="route-1"
    )
    context: dict = {"workspace": "/tmp/audited-ws", "surface": "api"}
    result = maybe_handle_workspace_audit_request(
        SimpleNamespace(),
        PROVE_FOLLOW_UP,
        session_id="route-1",
        source_surface="api",
        source_context=context,
    )

    assert result is None, "the continuation must be answered by the audit lane, not a fast path"
    assert context.get("workspace_audit_evidence_collected") is True
    assert context.get("workspace_audit_continuation") is True
    assert dict(context.get("workspace_audit_evidence") or {}).get("sources")


def test_the_builder_refuses_a_turn_the_audit_lane_owns() -> None:
    from core.agent_runtime.builder.controller import maybe_run_builder_controller

    def _explode(**_kwargs):
        raise AssertionError("the builder profiled a turn that belongs to the audit lane")

    result = maybe_run_builder_controller(
        SimpleNamespace(_builder_controller_profile=_explode),
        task=SimpleNamespace(task_id="t"),
        effective_input=PROVE_FOLLOW_UP,
        classification={},
        interpretation=None,
        web_notes=[],
        session_id="route-2",
        source_context={"workspace_audit_evidence_collected": True},
        render_capability_truth_response_fn=lambda _report: "",
        load_active_persona_fn=lambda *_a, **_k: None,
    )

    assert result is None


def test_a_prove_verb_alone_never_opens_an_audit() -> None:
    """The mirror-image bug: routing on a verb would drag ordinary turns into the audit lane."""
    from core.agent_runtime.audit_session import audit_follow_up_resumes

    # Seed our OWN capsule. This previously read `route-1` left behind by the test above and only
    # passed because the module-level capsule store outlived a test -- the same cross-test leak the
    # global reset now closes. A test that needs state must create it.
    _drive([TRUNCATION_FINDING, TEST_FILE, SYNTHESIS], session_id="route-1")

    # No capsule: the sentence is a continuation, but there is nothing to continue.
    assert audit_follow_up_resumes(PROVE_FOLLOW_UP, session_id="no-such-session") is None
    # A live capsule, but these are ordinary turns that merely contain the verb.
    for ordinary in (
        "prove that you can write Python",
        "show me how to verify that a signature is valid",
        "demonstrate that the API is faster than the old one",
        "can you prove the Pythagorean theorem",
    ):
        assert audit_follow_up_resumes(ordinary, session_id="route-1") is None, ordinary
    # And these must still resume it.
    for continuation in (
        PROVE_FOLLOW_UP,
        "prove it",
        "reproduce that first",
        "prove the bug you found",
    ):
        assert audit_follow_up_resumes(continuation, session_id="route-1") is not None, continuation


def test_the_follow_up_proves_the_stored_candidate_without_renominating_it() -> None:
    """Resuming means proving the candidate the operator was already shown — not asking the model
    to pick again, which is how a follow-up silently changes subject."""
    _decision, _router, _tools = _drive(
        [TRUNCATION_FINDING, TEST_FILE, SYNTHESIS], session_id="capsule-authz"
    )
    decision, router, tools = _drive(
        [TEST_FILE, SYNTHESIS], prompt=PROVE_FOLLOW_UP, session_id="capsule-authz"
    )

    assert "nominate" not in router.steps(), "the follow-up re-nominated instead of resuming"
    assert "prove" in router.steps(), "the explicit proof request did not authorize the test"
    assert "sandbox.run_command" in tools.intents()
    assert _stepped(decision).get("terminal_state") == "proven"


def test_the_exact_proof_follow_up_does_not_replay_the_candidate_instantly() -> None:
    """The live wording authorizes a temporary test while banning production edits.

    Treating "do not modify production code" as "write nothing" returned the stored candidate in
    0.23 seconds with zero model calls, which looked like a scripted answer because it was one.
    """
    _drive([TRUNCATION_FINDING], session_id="exact-proof-follow-up")
    decision, router, tools = _drive(
        [TEST_FILE, SYNTHESIS],
        prompt=PROVE_WITH_PRODUCTION_BAN,
        session_id="exact-proof-follow-up",
    )

    assert router.steps() == ["prove", "synthesize"]
    assert "workspace.write_file" in tools.intents()
    assert "sandbox.run_command" in tools.intents()
    assert _stepped(decision).get("terminal_state") == "proven"
    assert _stepped(decision).get("model_calls", 0) >= 2


def test_the_follow_up_still_refuses_production_edits() -> None:
    """Rule 1: a proof request authorizes a test and a command. Nothing else."""
    _decision, _router, _tools = _drive(
        [TRUNCATION_FINDING, TEST_FILE, SYNTHESIS], session_id="capsule-scope"
    )
    _decision2, _router2, tools = _drive(
        [TEST_FILE, SYNTHESIS], prompt=PROVE_FOLLOW_UP, session_id="capsule-scope"
    )

    written = [args.get("path", "") for intent, args in tools.calls if intent == "workspace.write_file"]
    assert written, "the authorized proof wrote no test at all"
    for path in written:
        assert TARGET not in str(path), "the proof step edited the production file under audit"


def test_proof_authorization_is_restricted_to_generated_artifacts_at_the_tool_boundary(tmp_path) -> None:
    from core.agent_runtime.audit_policy import READ_ONLY_AUDIT, derive_execution_policy
    from core.runtime_execution_tools import execute_runtime_tool

    policy = derive_execution_policy(PROVE_WITH_PRODUCTION_BAN, prior=READ_ONLY_AUDIT)
    assert policy.proof_authorized is True

    result = execute_runtime_tool(
        "workspace.write_file",
        {"path": TARGET, "content": "must not land"},
        source_context={
            "workspace": str(tmp_path),
            "audit_execution_policy": policy.as_dict(),
        },
    )

    assert result is not None
    assert result.status == "permission_denied"
    assert not (tmp_path / TARGET).exists()


# --------------------------------------------------------------------------------------
# Gate D / rule 4 — a passing proof refutes the candidate and advances the search
# --------------------------------------------------------------------------------------


def _seed_with_the_refuted_hypothesis(session_id):
    """Gate D: the empty-input hypothesis is nominated FIRST, so its proof is what disproves it."""
    return _drive([EMPTY_INPUT_FINDING, TEST_FILE, SYNTHESIS], session_id=session_id)


def test_a_passing_proof_records_the_candidate_as_refuted() -> None:
    _seed_with_the_refuted_hypothesis("refute-1")
    decision, _router, _tools = _drive(
        [EMPTY_INPUT_TEST_FILE, TRUNCATION_FINDING, TEST_FILE, SYNTHESIS],
        prompt=PROVE_FOLLOW_UP,
        tool_runner=_ToolRunner(runs=[(0, GREEN_RUN), (1, FAILING_RUN)]),
        session_id="refute-1",
    )
    refuted = list(_stepped(decision).get("refuted") or [])

    assert refuted, "a proof test that exited 0 did not refute its candidate"
    assert any("empty input" in str(item).lower() for item in refuted)


def test_a_refuted_candidate_advances_to_a_different_candidate() -> None:
    _seed_with_the_refuted_hypothesis("refute-2")
    decision, router, _tools = _drive(
        [EMPTY_INPUT_TEST_FILE, TRUNCATION_FINDING, TEST_FILE, SYNTHESIS],
        prompt=PROVE_FOLLOW_UP,
        tool_runner=_ToolRunner(runs=[(0, GREEN_RUN), (1, FAILING_RUN)]),
        session_id="refute-2",
    )

    assert router.steps().count("nominate") >= 1, (
        "the run stopped after one disproved candidate instead of nominating the next"
    )
    assert router.steps().count("prove") == 2
    assert _stepped(decision).get("terminal_state") == "proven"
    assert "truncat" in str(_stepped(decision).get("finding", {}).get("title", "")).lower()


def test_a_refuted_claim_is_banned_from_renomination() -> None:
    _seed_with_the_refuted_hypothesis("refute-3")
    decision, router, _tools = _drive(
        # The model re-nominates the SAME refuted claim, then finally a different one.
        [EMPTY_INPUT_TEST_FILE, EMPTY_INPUT_FINDING, TRUNCATION_FINDING, TEST_FILE, SYNTHESIS],
        prompt=PROVE_FOLLOW_UP,
        tool_runner=_ToolRunner(runs=[(0, GREEN_RUN), (1, FAILING_RUN)]),
        session_id="refute-3",
    )

    assert router.steps().count("prove") == 2, "a banned claim was proved a second time"
    assert "truncat" in str(_stepped(decision).get("finding", {}).get("title", "")).lower()


def test_a_backwards_or_mixed_oracle_test_cannot_be_scored_as_proof() -> None:
    """Live regression: assertRaises(IndexError) failed because NO IndexError was raised, but the
    generic ``unittest failed`` detector promoted that disproof to a proven finding. The same file
    also carried a second, opposite oracle, making the artifact internally contradictory."""
    _seed_with_the_refuted_hypothesis("backwards-proof")
    decision, router, tools = _drive(
        [BACKWARDS_EMPTY_INPUT_TEST_FILE, BACKWARDS_EMPTY_INPUT_TEST_FILE],
        prompt=PROVE_WITH_PRODUCTION_BAN,
        session_id="backwards-proof",
    )

    assert router.steps().count("prove") == 2, "an invalid proof artifact did not get one correction"
    assert "workspace.write_file" not in tools.intents()
    assert "sandbox.run_command" not in tools.intents()
    assert _stepped(decision).get("terminal_state") != "proven"
    assert "reproduced by a failing test" not in _report(decision)


def test_a_refuted_claim_cannot_return_with_reworded_title_and_scenario() -> None:
    """A semantic key made from eight sorted words is lossy: harmless wording drift can change
    the key even when the candidate still alleges the same empty-input startswith crash."""
    _seed_with_the_refuted_hypothesis("refute-reworded")
    decision, router, _tools = _drive(
        [
            EMPTY_INPUT_TEST_FILE,
            REWORDED_EMPTY_INPUT_FINDING,
            REWORDED_EMPTY_INPUT_FINDING,
            TRUNCATION_FINDING,
            TEST_FILE,
            SYNTHESIS,
        ],
        prompt=PROVE_FOLLOW_UP,
        tool_runner=_ToolRunner(runs=[(0, GREEN_RUN), (1, FAILING_RUN)]),
        session_id="refute-reworded",
    )

    assert router.steps().count("prove") == 2, "the reworded disproven claim was proved again"
    assert "truncat" in str(_stepped(decision).get("finding", {}).get("title", "")).lower()
    report = _report(decision).lower()
    assert not ("starts with crashes" in report and "highest-risk bug" in report)


def test_a_generated_test_that_crashes_on_import_is_not_a_proof() -> None:
    """Found while driving acceptance gate C against the frozen fixture.

    A generated test with a SyntaxError never reaches the runner: `python3 -m unittest` prints a
    traceback, NO "Ran N tests" line, no "FAILED (...)" line, and exits 1. `tests_actually_ran`
    treats a missing count line as "some other runner, trust the exit code" — correct for
    `npm test`, catastrophic here — so the combination scored as a proof. The generated test
    crashing on itself was reported as a reproduced bug in the audited code.
    """
    crash = (
        'Traceback (most recent call last):\n'
        '  File "test_subject_bug.py", line 18\n'
        "    b'%d' % (i %% 250)\n"
        "                 ^\n"
        "SyntaxError: invalid syntax\n"
    )
    _seed_with_the_refuted_hypothesis("import-crash")
    decision, _router, _tools = _drive(
        [EMPTY_INPUT_TEST_FILE, "ERROR:done"],
        prompt=PROVE_FOLLOW_UP,
        tool_runner=_ToolRunner(runs=[(1, crash)]),
        session_id="import-crash",
    )
    stepped = _stepped(decision)

    assert stepped["terminal_state"] != "proven", "an import crash was scored as a proof"
    assert stepped["proof"]["proven"] is False
    assert stepped["proof"]["note"] == "errored"
    assert "reproduced by a failing test" not in _report(decision)


def test_the_proof_verdict_requires_a_unittest_verdict_line() -> None:
    from core.agent_runtime.builder.app_builder import proof_holds

    crash = "SyntaxError: invalid syntax\n"
    real_failure = "test_it ... FAIL\n\nRan 1 test in 0.01s\n\nFAILED (failures=1)"

    assert proof_holds(output=crash, returncode=1, unittest_runner=True) is False
    assert proof_holds(output=real_failure, returncode=1, unittest_runner=True) is True
    # A non-unittest runner keeps the weaker exit-code signal — it offers nothing else.
    assert proof_holds(output=crash, returncode=1) is True


def test_a_refuted_candidate_is_never_rendered_as_a_bug() -> None:
    _seed_with_the_refuted_hypothesis("refute-4")
    decision, _router, _tools = _drive(
        [EMPTY_INPUT_TEST_FILE, "ERROR:no_more_candidates"],
        prompt=PROVE_FOLLOW_UP,
        tool_runner=_ToolRunner(runs=[(0, GREEN_RUN)]),
        session_id="refute-4",
    )
    report = _report(decision).lower()

    assert _stepped(decision).get("terminal_state") in {"refuted", "no_finding", "blocked"}
    assert "highest-risk bug" not in report
    # The refuted claim may be NAMED — it is useful to know what was ruled out — but only ever
    # alongside the word for what happened to it.
    assert "empty input" not in report or any(
        word in report for word in ("disproved", "refuted", "not reproduced")
    )


def test_the_candidate_budget_is_bounded_at_three() -> None:
    _seed_with_the_refuted_hypothesis("refute-5")
    _decision, router, _tools = _drive(
        [
            EMPTY_INPUT_TEST_FILE, TRUNCATION_FINDING, TEST_FILE,
            EXTEND_FINDING, EXTEND_TEST_FILE, ADVANCE_FINDING, ADVANCE_TEST_FILE,
        ],
        prompt=PROVE_FOLLOW_UP,
        tool_runner=_ToolRunner(runs=[(0, GREEN_RUN)] * 4),
        session_id="refute-5",
    )

    # Exactly three: fewer means the loop stops after one disproof (the incident); more means the
    # "at most three distinct candidates" ceiling in rule 4 is not enforced.
    assert router.steps().count("prove") == 3, "the candidate loop is not bounded at exactly three"


# --------------------------------------------------------------------------------------
# Gate E / rule 6 — no rendered report may contradict its own proof result
# --------------------------------------------------------------------------------------


def test_no_terminal_state_renders_a_contradiction() -> None:
    from core.agent_runtime.audit_verdict import (
        TERMINAL_STATES,
        asserts_reproduction,
        render_audit_report,
        sample_verdict,
    )

    for state in TERMINAL_STATES:
        report = render_audit_report(sample_verdict(state))
        lowered = report.lower()
        affirmative = any(
            phrase in lowered
            for phrase in ("the bug is real", "highest-risk bug", "reproduced by a failing test")
        )
        negative = any(
            phrase in lowered for phrase in ("not reproduced", "not proven", "refuted", "unproven")
        )
        assert not (affirmative and negative), f"{state} renders both claims: {report[:200]!r}"
        assert affirmative == asserts_reproduction(state), (
            f"{state} rendering disagrees with its own typed verdict"
        )


def test_model_prose_cannot_reach_a_report_that_did_not_prove_anything() -> None:
    """The structural half of rule 6, pinned directly.

    The driver only ASKS for synthesis when the proof held, so this guarantee is invisible from the
    outside — and an invisible guarantee is one a later refactor deletes without a test failing.
    Here the verdict is constructed with analysis attached to every non-proven state, which is what
    a future caller doing the wrong thing would produce.
    """
    from core.agent_runtime.audit_verdict import (
        TERMINAL_STATES,
        AuditVerdict,
        VerdictFinding,
        render_audit_report,
    )

    prose = "This is definitely a real bug and it is the highest-risk one in the file."
    for state in TERMINAL_STATES:
        verdict = AuditVerdict(
            state=state,
            finding=VerdictFinding(
                title="t", file="a.py", line_start=1, line_end=2,
                cited_line_text="x", failure_scenario="y",
            ),
            analysis=prose,
        )
        if state == "proven":
            assert prose in render_audit_report(verdict)
            continue
        assert verdict.analysis == "", f"{state} kept model prose on the verdict"
        assert prose not in render_audit_report(verdict), f"{state} rendered model prose"


def test_prove_finding_refuses_on_its_own_without_the_drivers_gate() -> None:
    """Defence in depth, pinned: `_prove_finding` must refuse an unauthorized policy even when
    called directly, so the read-only guarantee does not rest on one caller remembering to ask."""
    from core.agent_runtime.audit_policy import READ_ONLY_AUDIT
    from core.agent_runtime.stepped_audit import SteppedFinding, _prove_finding

    tools = _ToolRunner()
    proof = _prove_finding(
        SimpleNamespace(memory_router=_ScriptedRouter([TEST_FILE]), _execute_tool_intent=tools),
        manifest=MANIFEST,
        task=SimpleNamespace(task_id="t"),
        source_context={},
        session_id="direct",
        evidence=SimpleNamespace(workspace_root="/tmp/ws", sources={TARGET: CODEC}),
        finding=SteppedFinding(
            title="t", file=TARGET, line_start=1, line_end=2,
            cited_line_text="x", failure_scenario="y",
        ),
        budget=SimpleNamespace(exhausted=lambda: False, calls=0, last_error=""),
        usages=[],
        policy=READ_ONLY_AUDIT,
    )

    assert proof.unauthorized is True
    assert proof.attempted is False
    assert tools.calls == [], "an unauthorized proof step touched the workspace"


def test_only_a_proven_verdict_may_assert_reproduction() -> None:
    from core.agent_runtime.audit_verdict import TERMINAL_STATES, asserts_reproduction

    asserting = {state for state in TERMINAL_STATES if asserts_reproduction(state)}
    assert asserting == {"proven"}


def test_a_refuted_report_carries_no_model_synthesis() -> None:
    """Rule 6: remove model synthesis from refuted/unproven terminal reports."""
    _seed_with_the_refuted_hypothesis("synth-1")
    decision, router, _tools = _drive(
        [TEST_FILE, "ERROR:exhausted"],
        prompt=PROVE_FOLLOW_UP,
        tool_runner=_ToolRunner(runs=[(0, GREEN_RUN)]),
        session_id="synth-1",
    )

    assert "synthesize" not in router.steps()
    assert SYNTHESIS not in _report(decision)


# --------------------------------------------------------------------------------------
# Gate H / rule 7 — every dead end terminates honestly inside the stepped lane
# --------------------------------------------------------------------------------------


def test_malformed_model_json_terminates_inside_the_stepped_lane() -> None:
    decision, _router, _tools = _drive(["not json at all", "still not json"])

    assert decision is not None, "malformed JSON returned None into the single-shot lane"
    assert _stepped(decision).get("terminal_state") in {"no_finding", "blocked"}


def test_an_invalid_citation_location_terminates_inside_the_stepped_lane() -> None:
    invented = _finding(
        title="Invented",
        line_no=len(CODEC.splitlines()) + 50,
        line_text="for pid, count in self.read_runs(blob):",
        scenario="Not the line that is there.",
    )
    decision, _router, _tools = _drive([invented, invented, invented])

    assert decision is not None
    assert _stepped(decision).get("terminal_state") in {"no_finding", "blocked"}


def test_provider_empty_content_terminates_inside_the_stepped_lane() -> None:
    decision, _router, _tools = _drive([{"text": ""}, {"text": ""}])

    assert decision is not None
    assert _stepped(decision).get("terminal_state") in {"no_finding", "blocked"}


def test_no_evidence_terminates_inside_the_stepped_lane() -> None:
    decision, _router, _tools = _drive(
        [TRUNCATION_FINDING], context={"workspace_audit_evidence_collected": True}
    )

    assert decision is not None, "a turn with no evidence fell through to the single-shot lane"
    assert _stepped(decision).get("terminal_state") == "blocked"


def test_an_exhausted_candidate_budget_terminates_honestly() -> None:
    _seed_with_the_refuted_hypothesis("exhaust-1")
    decision, _router, _tools = _drive(
        [TEST_FILE, TRUNCATION_FINDING, TEST_FILE, EXTEND_FINDING, TEST_FILE, ADVANCE_FINDING],
        prompt=PROVE_FOLLOW_UP,
        tool_runner=_ToolRunner(runs=[(0, GREEN_RUN)] * 4),
        session_id="exhaust-1",
    )

    assert decision is not None
    assert _stepped(decision).get("terminal_state") in {"no_finding", "blocked"}
    assert "highest-risk bug" not in _report(decision).lower()


# --------------------------------------------------------------------------------------
# Rule 9 — provider failure labels are factual
# --------------------------------------------------------------------------------------


def test_a_model_that_answered_is_not_reported_as_unreachable() -> None:
    """The incident wording: the operator was told to pick 'a model this machine can reach' after
    the model had answered and merely failed the artifact contract."""
    decision, _router, _tools = _drive([{"text": ""}, {"text": ""}])
    report = _report(decision).lower()

    assert "can reach" not in report and "unreachable" not in report
    assert "empty" in report or "no content" in report or "artifact" in report


def test_the_failure_taxonomy_distinguishes_its_cases() -> None:
    from core.agent_runtime.provider_failure import ProviderFailure, classify_provider_failure

    cases = {
        # A transport failure is the ONLY thing allowed to be called unreachable.
        "unreachable": classify_provider_failure(error="ConnectionError: connection refused"),
        "provider_error": classify_provider_failure(error="rate_limit_exceeded (429)"),
        "empty_choices": classify_provider_failure(error="", text="", has_choices=False),
        "empty_content": classify_provider_failure(error="", text=""),
        "reasoning_only": classify_provider_failure(
            error="", text="<think>Let me analyze. But wait. Actually</think>"
        ),
        "output_budget_exhausted": classify_provider_failure(
            error="", text="", finish_reason="length"
        ),
    }
    for expected, failure in cases.items():
        assert isinstance(failure, ProviderFailure)
        assert failure.kind == expected, f"{expected} was labelled {failure.kind}"

    # The incident sentence: a model that ANSWERED must never be described as out of reach.
    for kind in ("empty_content", "reasoning_only", "output_budget_exhausted"):
        sentence = cases[kind].operator_sentence(model_label="nemotron:free").lower()
        assert "reached" in sentence and "could not be reached" not in sentence
        assert cases[kind].model_answered is True
    assert cases["unreachable"].model_answered is False


# --------------------------------------------------------------------------------------
# Gate G / rule 10 — usage totals carry completeness
# --------------------------------------------------------------------------------------


def test_a_missing_usage_record_makes_the_total_a_lower_bound() -> None:
    decision, _router, _tools = _drive(
        [
            {"text": TRUNCATION_FINDING, "usage": {"prompt_tokens": 900, "completion_tokens": 40}},
            {"text": TEST_FILE, "usage": {}},
            {"text": SYNTHESIS, "usage": {"prompt_tokens": 300, "completion_tokens": 60}},
        ],
        prompt=AUDIT_AND_PROVE_PROMPT,
        session_id="usage-1",
    )
    usage = dict(dict(getattr(decision, "details", None) or {}).get("token_usage") or {})

    assert usage.get("complete") is False
    assert usage.get("calls_missing_usage", 0) >= 1
    assert usage.get("lower_bound") is True


def test_full_usage_reports_exact_totals_that_equal_the_receipts() -> None:
    decision, _router, _tools = _drive(
        [
            {"text": TRUNCATION_FINDING, "usage": {"prompt_tokens": 900, "completion_tokens": 40}},
            {"text": TEST_FILE, "usage": {"prompt_tokens": 500, "completion_tokens": 700}},
            {"text": SYNTHESIS, "usage": {"prompt_tokens": 300, "completion_tokens": 60}},
        ],
        prompt=AUDIT_AND_PROVE_PROMPT,
        session_id="usage-2",
    )
    usage = dict(dict(getattr(decision, "details", None) or {}).get("token_usage") or {})

    assert usage.get("complete") is True
    assert usage.get("calls_missing_usage") == 0
    assert usage.get("input_tokens") == 1700
    assert usage.get("output_tokens") == 800


def test_the_operator_receipt_names_the_missing_call_count() -> None:
    decision, _router, _tools = _drive(
        [
            {"text": TRUNCATION_FINDING, "usage": {"prompt_tokens": 900, "completion_tokens": 40}},
            {"text": TEST_FILE, "usage": {}},
            {"text": SYNTHESIS, "usage": {}},
        ],
        prompt=AUDIT_AND_PROVE_PROMPT,
        session_id="usage-3",
    )
    report = _report(decision).lower()

    assert "lower bound" in report
    assert "2" in report, "the receipt must name how many calls reported no usage"


# --------------------------------------------------------------------------------------
# Rule 11 — a pinned model stays pinned across every phase
# --------------------------------------------------------------------------------------


def test_a_pinned_model_is_never_substituted_across_phases() -> None:
    _decision, router, _tools = _drive(
        [TRUNCATION_FINDING, TEST_FILE, SYNTHESIS],
        prompt=AUDIT_AND_PROVE_PROMPT,
        session_id="pin-1",
    )

    assert router.requests, "no model call was made"
    assert all(
        getattr(request, "reasoning_mode", "auto") == "disabled" for request in router.requests
    ), "audit artifact calls must carry the disabled reasoning policy"
