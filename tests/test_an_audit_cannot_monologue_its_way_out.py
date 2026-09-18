"""An audit turn must not be one open-ended model call, because that call is where a weak model
monologues instead of answering.

Measured 2026-08-01 on the daemon: `nemotron-3-ultra-550b-a55b:free`, asked for the highest-risk
bug in one Apache codec with the evidence already gathered, produced 8,320 tokens of "Let me
analyze... wait... actually" that never became an answer — and the SAME model produced a complete
audit under a harness that forces the task into artifact-per-step calls. Every output-side guard
shipped since catches the monologue after the spend and ships a degraded reply.

These tests pin the cause-side fix, `core/agent_runtime/stepped_audit.py`:

* step 1 must return a CHECKABLE finding — a citation whose line text matches the evidence, not
  merely an in-range number (the range-only check let `147-160` pass for code living at 132);
* step 2's proof verdict is `proof_holds` — a green run is a FAILED proof, an errored test proves
  nothing; a blocked write ships a step-1-only report with the reason, not a failed turn;
* step 3's prose is optional — a monologue there is dropped and the report still ships from the
  artifacts, so the model cannot monologue its way out of answering;
* the grounded turn actually TAKES this branch on an audit turn (behavioral, not a source grep —
  the previous "wiring test" for a guard was a string assertion and the guard was dead);
* every step call carries `workspace_audit_turn` metadata (the think-off timeout stamp) and a
  bounded `max_output_tokens`, and usage is SUMMED across the calls for the operator's receipt.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest import mock

from core.agent_runtime.stepped_audit import run_stepped_audit

# A subject shaped like the incident's codec: the buggy line sits deep enough that a guessed
# number and the real number cannot coincide by luck.
_CODEC_LINES = (
    ["#!/usr/bin/env python3", '"""A codec."""', "import struct", "", "class Codec:"]
    + [f"    def method_{i}(self):" if i % 2 else f"        return {i}" for i in range(6, 28)]
    + [
        "    def decompress(self, blob):",
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
# 1-indexed line of "            except Exception: break"
BUGGY_LINE_NO = _CODEC_LINES.index("            except Exception: break") + 1
BUGGY_LINE_TEXT = "            except Exception: break"

MANIFEST = SimpleNamespace(
    provider_id="openrouter-byok:nvidia/nemotron",
    model_name="nvidia/nemotron-3-ultra-550b-a55b:free",
)

GOOD_FINDING = json.dumps(
    {
        "title": "Silent truncation on decompress errors",
        "file": "api/apache/codec.py",
        "line_start": BUGGY_LINE_NO,
        "line_end": BUGGY_LINE_NO + 3,
        "cited_line_text": BUGGY_LINE_TEXT,
        "failure_scenario": "A corrupted byte makes the bare except break the loop and return a short buffer with no error raised.",
    }
)
BAD_FINDING = json.dumps(
    {
        "title": "Silent truncation on decompress errors",
        "file": "api/apache/codec.py",
        "line_start": BUGGY_LINE_NO,
        "line_end": BUGGY_LINE_NO + 3,
        # In range, wrong content — the exact fabrication the range-only verifier let through.
        "cited_line_text": "for pid, count in self.read_runs(blob):",
        "failure_scenario": "A corrupted byte truncates the output.",
    }
)
TEST_FILE = (
    "class TestBug(unittest.TestCase):\n"
    "    def test_truncation_is_silent(self):\n"
    "        self.assertEqual(codec.Codec().decompress(b'\\xff'), b'should have raised')\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    unittest.main()\n"
)
SYNTHESIS = (
    "The decompressor swallows parsing errors, so a corrupted archive comes back shorter with no "
    "exception raised. Any fix must raise on malformed input instead of returning a partial buffer."
)
FAILING_RUN = "test_truncation_is_silent ... FAIL\n\nRan 1 test in 0.002s\n\nFAILED (failures=1)"
GREEN_RUN = "test_truncation_is_silent ... ok\n\nRan 1 test in 0.002s\n\nOK"
ERRORED_RUN = "test_truncation_is_silent ... ERROR\n\nRan 1 test in 0.002s\n\nFAILED (errors=1)"


def _evidence_context(**overrides):
    context = {
        "workspace_audit_evidence_collected": True,
        "workspace_audit_evidence": {
            "all_paths": ("api/apache/codec.py", "README.md"),
            "inspected_paths": ("api/apache/codec.py",),
            "sources": {"api/apache/codec.py": CODEC},
            "workspace_root": "/tmp/audited-ws",
            "incomplete_files": (),
        },
        "requested_model": "nvidia/nemotron-3-ultra-550b-a55b:free",
    }
    context.update(overrides)
    return context


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
        if not self.replies:
            return (None, None, "script_exhausted")
        item = self.replies.pop(0)
        if isinstance(item, str) and item.startswith("ERROR:"):
            return (None, None, item[len("ERROR:"):])
        text = item["text"] if isinstance(item, dict) else str(item)
        usage = item.get("usage", {}) if isinstance(item, dict) else {"prompt_tokens": 10, "completion_tokens": 5}
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


class _ToolRunner:
    """`agent._execute_tool_intent` with a scripted sandbox verdict; records every intent."""

    def __init__(self, *, returncode=1, output=FAILING_RUN, write_ok=True):
        self.returncode = returncode
        self.output = output
        self.write_ok = write_ok
        self.calls = []

    def __call__(self, payload, **kwargs):
        intent = str(payload.get("intent") or "")
        self.calls.append((intent, dict(payload.get("arguments") or {})))
        if intent == "workspace.write_file" and not self.write_ok:
            return SimpleNamespace(
                ok=False, handled=True, response_text="", details={},
                status="approval_required", mode="tool_failed",
            )
        if intent == "sandbox.run_command":
            return SimpleNamespace(
                ok=self.returncode == 0, handled=True, response_text=self.output,
                details={"returncode": self.returncode}, status="executed", mode="tool_executed",
            )
        return SimpleNamespace(
            ok=True, handled=True, response_text="", details={}, status="executed", mode="tool_executed",
        )


def _agent(router, tool_runner):
    return SimpleNamespace(
        memory_router=router,
        _execute_tool_intent=tool_runner,
        hive_activity_tracker=None,
        public_hive_bridge=None,
    )


def _drive(replies, *, tool_runner=None, context=None):
    router = _ScriptedRouter(replies)
    tools = tool_runner if tool_runner is not None else _ToolRunner()
    decision = run_stepped_audit(
        _agent(router, tools),
        task=SimpleNamespace(task_id="task-stepped"),
        effective_input=(
            "Inspect this workspace and audit: api/apache/codec.py — find the single "
            "highest-risk real bug, and prove it with a failing test."
        ),
        source_context=context if context is not None else _evidence_context(),
        session_id="stepped-session",
    )
    return decision, router, tools


# --------------------------------------------------------------------------------------
# Step 1 — the finding must be checkable
# --------------------------------------------------------------------------------------


def test_a_checkable_finding_becomes_a_proven_stepped_report() -> None:
    decision, router, _tools = _drive([GOOD_FINDING, TEST_FILE, SYNTHESIS])
    assert decision is not None
    assert decision.used_model is True
    text = str(decision.output_text)
    assert f"api/apache/codec.py:{BUGGY_LINE_NO}-{BUGGY_LINE_NO + 3}" in text
    assert BUGGY_LINE_TEXT.strip() in text
    assert "reproduced by a failing test on the current code" in text
    assert SYNTHESIS.split(",")[0] in text
    receipts = decision.details["stepped_audit"]
    assert receipts["proof"]["proven"] is True
    assert receipts["proof"]["returncode"] == 1
    assert len(router.requests) == 3


def test_model_copied_line_text_is_replaced_with_the_canonical_source_line() -> None:
    """The model chooses a location; the runtime owns what that source line actually says.

    Rejecting an otherwise correct finding because a weak model changed whitespace or copied the
    wrong text is a transcription gate, not a truth gate. Canonicalization preserves the real line
    while proof/challenge judges the causal claim.
    """
    decision, router, _tools = _drive([BAD_FINDING, TEST_FILE, SYNTHESIS])
    assert decision is not None
    assert len(router.requests) == 3
    assert BUGGY_LINE_TEXT.strip() in str(decision.output_text)
    assert "for pid, count in self.read_runs(blob):" not in str(decision.output_text)


def test_three_uncheckable_findings_abandon_the_stepped_lane() -> None:
    """Rule 7: the lane terminates HONESTLY rather than returning None into the single-shot lane,
    which is the hallucination path this module exists to close."""
    impossible = json.loads(BAD_FINDING)
    impossible["line_start"] = len(_CODEC_LINES) + 50
    impossible["line_end"] = len(_CODEC_LINES) + 53
    impossible = json.dumps(impossible)
    decision, router, tools = _drive([impossible, impossible, impossible])
    assert decision is not None
    terminal = dict(decision.details["stepped_audit"])["terminal_state"]
    assert terminal in {"no_finding", "blocked"}
    assert "Highest-risk bug" not in str(decision.output_text)
    assert len(router.requests) == 3
    assert tools.calls == [], "no test may be written for a finding that failed its citation check"


def test_a_shape_failure_retries_as_plain_text_on_the_same_model() -> None:
    """Driven live 2026-08-01: the free cloud reasoning model completed a 91s call whose content
    was unusable under provider-native forced JSON. The second attempt must drop the JSON contract
    (instruction-only JSON), because the contract itself is a known failure input there."""
    decision, router, _tools = _drive(
        ["I think the bug is somewhere in the loop", GOOD_FINDING, TEST_FILE, SYNTHESIS]
    )
    assert decision is not None
    assert router.requests[0].output_mode == "json_object"
    assert router.requests[1].output_mode == "plain_text"


def test_json_wrapped_in_narration_still_parses_on_the_retry() -> None:
    """A reasoning model answering the plain-text retry wraps its JSON in prose; the balanced
    scan must extract it rather than fail the whole nomination."""
    wrapped = f"Sure — here is the finding you asked for:\n\n{GOOD_FINDING}\n\nLet me know if you need more."
    decision, router, _tools = _drive(["", wrapped, TEST_FILE, SYNTHESIS])
    assert decision is not None
    assert router.requests[1].output_mode == "plain_text"
    assert decision.details["stepped_audit"]["finding"]["line_start"] == BUGGY_LINE_NO


def test_a_gated_model_falls_through_to_the_next_manifest() -> None:
    """Driven live 2026-08-01: qwen3:14b was ranked first and failed model_load_gated_low_memory
    twice; the lane must walk on to the next ranked manifest instead of abandoning."""
    manifest_b = SimpleNamespace(provider_id="ollama-local:qwen3:8b", model_name="qwen3:8b")

    class _TwoManifestRouter(_ScriptedRouter):
        registry = SimpleNamespace()

        def _requested_model_manifest(self, _context):
            return None

        def _invoke_manifest(self, *, manifest, **kwargs):
            self.manifests.append(manifest)
            return super()._invoke_manifest(manifest=manifest, **kwargs)

    # ONE gated error, not two. `model_load_gated_low_memory` is a fact about the box, so the
    # manifest is abandoned on first sight rather than re-asked — measured live 2026-08-01, two
    # identical refusals spent the whole nomination budget and the model that could answer had no
    # attempts left.
    router = _TwoManifestRouter(
        ["ERROR:model_load_gated_low_memory", GOOD_FINDING, TEST_FILE, SYNTHESIS]
    )
    router.manifests = []
    context = _evidence_context()
    context.pop("requested_model")
    with mock.patch(
        "core.provider_routing.rank_provider_candidates", return_value=[MANIFEST, manifest_b]
    ), mock.patch(
        "core.local_ollama_inventory.is_text_generation_ollama_model", return_value=True
    ), mock.patch(
        "core.model_selection_policy.provider_cost_class", return_value="free_local"
    ):
        decision = run_stepped_audit(
            _agent(router, _ToolRunner()),
            task=SimpleNamespace(task_id="task-stepped"),
            effective_input="audit api/apache/codec.py and prove the bug with a failing test",
            source_context=context,
            session_id="stepped-session",
        )
    assert decision is not None, "a gated first manifest must not end the stepped lane"
    assert decision.model_name == "qwen3:8b"
    assert len(router.requests) == 4
    gated_attempts = [m for m in router.manifests if getattr(m, "model_name", "") == MANIFEST.model_name]
    assert len(gated_attempts) == 1, "the resource-gated model was asked again after being refused"


def test_a_citation_past_the_end_of_the_file_is_rejected() -> None:
    beyond = json.loads(GOOD_FINDING)
    beyond["line_start"] = len(_CODEC_LINES) + 40
    beyond["line_end"] = len(_CODEC_LINES) + 44
    decision, router, _tools = _drive([json.dumps(beyond), json.dumps(beyond)])
    assert decision is not None
    assert dict(decision.details["stepped_audit"])["terminal_state"] in {"no_finding", "blocked"}
    assert "does not exist" in str(router.requests[1].prompt)


# --------------------------------------------------------------------------------------
# Step 2 — the proof verdict is proof_holds, and blocked is not failed
# --------------------------------------------------------------------------------------


def test_a_green_proof_run_refutes_the_claim_and_never_ships_it_as_a_bug() -> None:
    """A green run is not merely a failed proof — it is a DISPROOF (§1A rule 4). The claim is
    recorded as refuted, the search moves on, and no wording in the report calls it a bug."""
    decision, _router, _tools = _drive(
        [GOOD_FINDING, TEST_FILE, SYNTHESIS],
        tool_runner=_ToolRunner(returncode=0, output=GREEN_RUN),
    )
    assert decision is not None
    stepped = dict(decision.details["stepped_audit"])
    assert stepped["terminal_state"] in {"refuted", "no_finding"}
    refuted = list(stepped["refuted"])
    assert refuted and refuted[0]["returncode"] == 0
    assert "PASSED (exit 0)" in refuted[0]["counterexample"]
    text = str(decision.output_text)
    assert "Highest-risk bug" not in text
    assert "disproved" in text.lower()


def test_an_errored_test_is_not_a_proof() -> None:
    decision, _router, _tools = _drive(
        [GOOD_FINDING, TEST_FILE, SYNTHESIS],
        tool_runner=_ToolRunner(returncode=1, output=ERRORED_RUN),
    )
    assert decision is not None
    proof = decision.details["stepped_audit"]["proof"]
    assert proof["proven"] is False
    assert proof["note"] == "errored"
    # 2026-08-06: CANDIDATE_UNPROVEN's chat report no longer inlines proof detail/reasoning
    # prose at all (the concise NOT-PROVEN contract) -- the fact this test exists to pin (an
    # errored run is never scored as proof) is fully checked above via the structured `proof` dict.
    assert "NOT PROVEN" in str(decision.output_text)


def test_a_blocked_write_ships_the_finding_with_the_reason() -> None:
    decision, _router, tools = _drive(
        [GOOD_FINDING, TEST_FILE, SYNTHESIS],
        tool_runner=_ToolRunner(write_ok=False),
    )
    assert decision is not None
    stepped = decision.details["stepped_audit"]
    proof = stepped["proof"]
    assert proof["blocked_reason"]
    # 2026-08-06: neither the blocked-reason prose nor the citation range reach chat for
    # CANDIDATE_UNPROVEN anymore -- checked against the structured record instead.
    finding = stepped.get("finding") or {}
    assert finding.get("file") == "api/apache/codec.py"
    assert finding.get("line_start") == BUGGY_LINE_NO
    assert "NOT PROVEN" in str(decision.output_text)
    assert not any(intent == "sandbox.run_command" for intent, _ in tools.calls)


def test_the_scratch_test_lands_under_generated_and_runs_named() -> None:
    _decision, _router, tools = _drive([GOOD_FINDING, TEST_FILE, SYNTHESIS])
    writes = [args for intent, args in tools.calls if intent == "workspace.write_file"]
    assert writes and writes[0]["path"].startswith("generated/")
    assert writes[0]["path"].endswith("test_codec_bug.py")
    # The composed loader preamble must be present so the test can import its subject at all.
    assert "importlib.util.spec_from_file_location" in writes[0]["content"]
    assert "/tmp/audited-ws" in writes[0]["content"]
    runs = [args for intent, args in tools.calls if intent == "sandbox.run_command"]
    assert runs and runs[0]["command"] == "python3 -m unittest -v test_codec_bug"
    assert runs[0]["cwd"] == writes[0]["path"].rsplit("/", 1)[0]


# --------------------------------------------------------------------------------------
# Step 3 — a monologue cannot become the answer, and its absence cannot cost the report
# --------------------------------------------------------------------------------------


def test_a_synthesis_that_asks_to_run_tools_is_dropped_and_the_report_still_ships() -> None:
    stalling = "Let me run the tests to verify this. I will now run the test suite and report back."
    decision, _router, _tools = _drive([GOOD_FINDING, TEST_FILE, stalling])
    assert decision is not None
    text = str(decision.output_text)
    assert "Let me run the tests" not in text
    assert "## Analysis" not in text
    assert "## Proof" in text, "the artifact-built report must ship even with no usable prose"


def test_an_errored_report_survives_the_chat_sanitizer() -> None:
    """Driven live 2026-08-01: an errored proof digest carries `ERROR:` headers, the report cites
    lines (so contains "line "), and it is multi-line — together that satisfied the chat
    sanitizer's traceback heuristic and the ENTIRE stepped report was replaced with "I couldn't
    resolve that cleanly." The rendered report must not look like a traceback to that gate."""
    from core.agent_runtime.response import looks_like_runtime_traceback

    traceback_output = (
        "test_x ... ERROR\nTraceback (most recent call last):\n"
        '  File "test_codec_bug.py", line 8, in <module>\n'
        "ModuleNotFoundError: No module named 'common_zstd'\n\n"
        "Ran 1 test in 0.000s\n\nFAILED (errors=1)\nERROR: test_x (__main__.TestBug)"
    )
    decision, _router, _tools = _drive(
        [GOOD_FINDING, TEST_FILE, SYNTHESIS],
        tool_runner=_ToolRunner(returncode=1, output=traceback_output),
    )
    assert decision is not None
    text = str(decision.output_text)
    # 2026-08-06: the errored proof's own output/reasoning prose (the traceback-shaped text this
    # test's sanitizer concern was originally about) no longer reaches chat at all for
    # CANDIDATE_UNPROVEN -- it stays in `details.stepped_audit.proof` for Activity. The sanitizer
    # guarantee this test exists for still matters (the concise report itself must never be
    # mistaken for a traceback), so that check stays; the specific old wording assertion is gone
    # because that wording no longer renders anywhere, by design, not by accident.
    assert "NOT PROVEN" in text
    assert not looks_like_runtime_traceback(text), (
        "the sanitizer would replace this whole report with a generic error"
    )


def test_the_scratch_test_preamble_covers_every_ancestor_directory() -> None:
    """Driven live: the subject imported a sibling from its PARENT directory (api/), which was not
    on sys.path, so the proof errored on import instead of failing on the assertion."""
    _decision, _router, tools = _drive([GOOD_FINDING, TEST_FILE, SYNTHESIS])
    writes = [args for intent, args in tools.calls if intent == "workspace.write_file"]
    content = writes[0]["content"]
    assert "sys.path.insert(0, '/tmp/audited-ws')" in content
    assert "sys.path.insert(0, '/tmp/audited-ws/api')" in content
    assert "sys.path.insert(0, '/tmp/audited-ws/api/apache')" in content


def test_a_skipped_proof_retries_once_then_says_so_without_tripping_the_claim_verifier() -> None:
    """Driven live 2026-08-01: the free cloud lane rate-limited the prove call, and the frame's
    honest sentence "the model produced no test" matched the post-hoc claim verifier's
    no-tests-in-this-project pattern — the runtime's own wording was annotated as a contradicted
    model claim. The step retries once for the transient error, and the wording must not read as
    a claim about the project's tests."""
    from core.agent_runtime.audit_claim_verifier import _NO_TESTS_RE

    replies = [GOOD_FINDING, "ERROR:rate_limited", "ERROR:rate_limited", SYNTHESIS]
    decision, router, _tools = _drive(replies)
    assert decision is not None
    prove_requests = [r for r in router.requests if r.metadata.get("stepped_audit_step") == "prove"]
    assert len(prove_requests) == 2, "a transient provider error deserves one retry"
    text = str(decision.output_text)
    # 2026-08-06: the "did not run"/blocked-reason wording no longer reaches chat for
    # CANDIDATE_UNPROVEN -- checked against the structured proof record instead. The no-tests-claim
    # guard still matters for whatever DOES render (the concise contract), so it stays.
    proof = decision.details["stepped_audit"]["proof"]
    assert proof["attempted"] is False, "two rate-limited attempts produced no usable artifact"
    assert not proof.get("test_command"), "nothing should have run after two rate-limited attempts"
    assert proof.get("blocked_reason")
    assert not _NO_TESTS_RE.search(text), (
        "the frame wording reads as a no-tests claim and the verifier will contradict it"
    )


def test_the_audits_own_reads_satisfy_the_inspection_honesty_gate() -> None:
    """Driven live 2026-08-01: the inspection gate was dormant on audit turns until the prove
    step's write/run receipts woke it — and it then rewrote the whole report to "I did not
    actually open <file>" because the audited file had no read record, despite run_workspace_audit
    having read it verbatim this very turn. The stepped lane must register those reads."""
    from unittest import mock as _mock

    from core import execution_records

    execution_records.clear("stepped-session")
    with _mock.patch("core.runtime_flags.flag_enabled", return_value=True):
        decision, _router, _tools = _drive([GOOD_FINDING, TEST_FILE, SYNTHESIS])
    assert decision is not None
    verdict = execution_records.verify_claim("stepped-session", target="api/apache/codec.py")
    assert verdict["supported"] is True, (
        "without a read record for the audited file, the honesty gate rewrites the whole report"
    )
    execution_records.clear("stepped-session")


def test_a_quoted_code_expression_is_not_a_claimed_file() -> None:
    """Driven live 2026-08-01: the inspection gate parsed `blob.startswith` — quoted CODE in the
    report — as a claimed file `blob.starts`, found no record for that "file", and replaced the
    ENTIRE report. A dotted expression with a non-file extension is ambiguity, and this gate's
    contract is to fail open on ambiguity."""
    from core.agent_runtime.action_honesty_validator import _claimed_inspection_targets

    report = (
        "I inspected `api/apache/codec.py` and the loop at line 30. The guard `blob.startswith` "
        "and the accessor `payload.get` never validate `self.state` before use."
    )
    targets = _claimed_inspection_targets(report, "audit api/apache/codec.py")
    assert targets == ["api/apache/codec.py"], targets


def test_the_prove_steps_work_leaves_receipts_the_honesty_gate_accepts() -> None:
    """Driven live 2026-08-01 (drive 6): the durable receipt store only writes under a runtime
    checkpoint, which this lane does not create, so the completion-claim gate judged "Test written
    / the test ran" against ZERO receipts and replaced the whole report with "I did not create
    those files". The lane must leave receipts on the SAME dict the guards read."""
    from core.agent_runtime.action_honesty_validator import _receipt_shows_execution

    context = _evidence_context()
    decision, _router, _tools = _drive([GOOD_FINDING, TEST_FILE, SYNTHESIS], context=context)
    assert decision is not None
    receipts = context.get("tool_receipts") or []
    tools_receipted = {r["tool_name"] for r in receipts}
    assert "workspace.write_file" in tools_receipted
    assert "sandbox.run_command" in tools_receipted
    assert all(_receipt_shows_execution(r) for r in receipts)


def test_the_receipts_are_also_durable_for_the_api_layers_second_honesty_pass() -> None:
    """Driven live 2026-08-01 (drive 7): the first honesty pass (inside run_once) accepted the
    report, and the API layer's SECOND pass — on a context dict that never sees the inner copy —
    rewrote it anyway. The session-scoped receipt store is the channel both passes read."""
    from unittest import mock as _mock

    stored = []
    with _mock.patch(
        "core.runtime_continuity.store_tool_receipt",
        side_effect=lambda **kw: stored.append(kw),
    ):
        decision, _router, _tools = _drive([GOOD_FINDING, TEST_FILE, SYNTHESIS])
    assert decision is not None
    tools_stored = {kw["tool_name"] for kw in stored}
    assert "workspace.write_file" in tools_stored
    assert "sandbox.run_command" in tools_stored
    assert all(kw["session_id"] == "stepped-session" for kw in stored)
    assert all(kw["execution"].get("executed") is True for kw in stored)


def test_an_empty_synthesis_still_ships_the_artifacts() -> None:
    decision, _router, _tools = _drive([GOOD_FINDING, TEST_FILE, ""])
    assert decision is not None
    assert "## Proof" in str(decision.output_text)
    assert decision.details["stepped_audit"]["synthesis_used"] is False


# --------------------------------------------------------------------------------------
# Every call is bounded, stamped, and accounted
# --------------------------------------------------------------------------------------


def test_every_step_call_carries_the_audit_stamp_and_a_bounded_output() -> None:
    _decision, router, _tools = _drive([GOOD_FINDING, TEST_FILE, SYNTHESIS])
    assert len(router.requests) == 3
    for request in router.requests:
        assert request.metadata.get("workspace_audit_turn") is True, (
            "without the stamp a thinking-capable local model burns its whole read timeout"
        )
        assert 0 < int(request.max_output_tokens) <= 3000
        assert not request.messages, "messages must stay empty or prompt_budget rejects the call"
    assert router.requests[0].output_mode == "json_object"


def test_usage_is_summed_across_the_steps_for_the_receipt() -> None:
    decision, _router, _tools = _drive(
        [
            {"text": GOOD_FINDING, "usage": {"prompt_tokens": 100, "completion_tokens": 20}},
            {"text": TEST_FILE, "usage": {"prompt_tokens": 200, "completion_tokens": 30}},
            {"text": SYNTHESIS, "usage": {"prompt_tokens": 50, "completion_tokens": 10}},
        ]
    )
    assert decision is not None
    usage = decision.details["token_usage"]
    assert usage["input_tokens"] == 350, "the receipt must describe the turn, not the last call"
    assert usage["output_tokens"] == 60
    assert usage["source"] == "provider_reported"


def test_a_pinned_model_that_resolves_nowhere_is_refused_by_name() -> None:
    router = _ScriptedRouter([], manifest=None)
    decision = run_stepped_audit(
        _agent(router, _ToolRunner()),
        task=SimpleNamespace(task_id="task-stepped"),
        effective_input="audit api/apache/codec.py",
        source_context=_evidence_context(),
        session_id="stepped-session",
    )
    assert decision is not None
    assert decision.used_model is False
    assert "nemotron" in str(decision.output_text)
    assert "different model" in str(decision.output_text)


def test_no_evidence_terminates_inside_the_stepped_lane() -> None:
    """Rule 7 again: no evidence is an ANSWER ("nothing to audit"), not a fall-through."""
    decision, router, _tools = _drive(
        [GOOD_FINDING], context={"workspace_audit_evidence_collected": True}
    )
    assert decision is not None
    assert dict(decision.details["stepped_audit"])["terminal_state"] == "blocked"
    assert router.requests == [], "no model call may be made with no evidence"


# --------------------------------------------------------------------------------------
# The front door recognises "audit <file>" (the phrasing that fell to the file-print path)
# --------------------------------------------------------------------------------------


def test_audit_of_a_named_file_reaches_the_audit_lane() -> None:
    """Driven live 2026-08-01: 'lets audit api/apache/liquefy_apache_repetition_v1.py - whats the
    single worst real bug in it' was claimed by the direct-read fast path and answered by PRINTING
    THE FILE, because every branch of the audit gate required a project NOUN after the verb."""
    from core.agent_runtime.workspace_audit import looks_like_code_audit_request

    for phrasing in [
        "lets audit api/apache/liquefy_apache_repetition_v1.py - whats the single worst real bug in it",
        "audit api/apache/liquefy_apache_repetition_v1.py",
        "please audit liquefy_apache_repetition_v1.py for silent data loss",
        "can you audit ./src/app.js",
        "auditing config.yaml today, anything scary in it?",
    ]:
        assert looks_like_code_audit_request(phrasing), phrasing


def test_a_file_named_audit_is_not_the_verb() -> None:
    from core.agent_runtime.workspace_audit import looks_like_code_audit_request

    for phrasing in [
        "the audit.py module needs no changes",
        "open api/apache/liquefy_apache_repetition_v1.py",
        "what does liquefy_primitives.py do?",
        "show me the first 50 lines of main.py",
    ]:
        assert not looks_like_code_audit_request(phrasing), phrasing


# --------------------------------------------------------------------------------------
# The grounded turn takes the branch (behavioral wiring, not a source grep)
# --------------------------------------------------------------------------------------


def _drive_grounded_turn(agent, *, source_context, resolve_decision):
    """The real `_execute_grounded_turn` with only network/database seams stubbed — the same
    harness `test_token_usage_is_measured_not_guessed` uses, pointed at the audit branch."""

    from core.identity_manager import load_active_persona

    asked = "run a full audit on this codebase"
    adaptive = SimpleNamespace(
        enabled=False, tool_gap_note="", admitted_uncertainty=False, notes=[],
        reason="not_needed", strategy="none", actions_taken=[],
        to_dict=lambda: {"enabled": False, "reason": "not_needed"},
    )
    task = SimpleNamespace(
        task_id="task-stepped-wiring", task_summary=asked, environment_os="darwin",
        environment_shell="zsh", environment_runtime="python", environment_version_hint="3.11",
    )
    classification = {"task_class": "workspace_audit"}
    from core.prompt_assembly_report import PromptAssemblyReport

    context_result = SimpleNamespace(
        local_candidates=[], swarm_metadata=[], retrieval_confidence_score=0.7,
        assembled_context=lambda: "", context_snippets=lambda: [],
        report=PromptAssemblyReport(
            task_id="task-stepped-wiring", trace_id="trace-stepped-wiring",
            total_context_budget=8192, bootstrap_budget=2594,
            relevant_budget=4096, cold_budget=1502,
        ),
    )
    agent.context_loader.load = mock.Mock(return_value=context_result)
    agent._should_frontload_curiosity = mock.Mock(return_value=False)
    agent._maybe_execute_model_tool_intent = mock.Mock(return_value=None)
    agent._model_routing_profile = mock.Mock(return_value=(classification, {"output_mode": ""}))
    agent._collect_adaptive_research = mock.Mock(return_value=adaptive)
    agent.memory_router.resolve = mock.Mock(return_value=resolve_decision)
    agent.media_pipeline.analyze = mock.Mock(
        return_value=SimpleNamespace(
            used_provider=False, provider_id="", candidate_id="", reason="no_media",
            evidence_items=[], analysis_text="",
        )
    )
    agent._collect_live_web_notes = mock.Mock(return_value=[])
    agent._web_note_plan_candidates = mock.Mock(return_value=[])
    agent._default_gate = mock.Mock(
        return_value=SimpleNamespace(mode="advice_only", requires_user_approval=False)
    )
    agent._maybe_publish_public_task = mock.Mock(return_value={})
    agent._store_local_shard = mock.Mock()
    agent.hive_activity_tracker.note_watched_topic = mock.Mock()
    agent._decorate_chat_response = mock.Mock(side_effect=lambda result, **_: str(result.text or ""))

    with mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
        "core.agent_runtime.agent.ingest_media_evidence", return_value=[]
    ), mock.patch("core.agent_runtime.agent.build_media_context_snippets", return_value=[]), mock.patch(
        "core.agent_runtime.agent.build_plan", return_value=SimpleNamespace(confidence=0.72, evidence_sources=[])
    ), mock.patch(
        "core.agent_runtime.agent.should_use_planner_renderer", return_value=False
    ), mock.patch(
        "core.agent_runtime.agent.explicit_planner_style_requested", return_value=False
    ), mock.patch(
        "core.agent_runtime.agent.feedback_engine.evaluate_outcome",
        return_value=SimpleNamespace(is_success=False, is_durable=False),
    ), mock.patch("core.agent_runtime.agent.feedback_engine.apply", return_value=None):
        result = agent._execute_grounded_turn(
            task=task,
            effective_input=asked,
            classification=classification,
            interpreted=__import__(
                "core.human_input_adapter", fromlist=["adapt_user_input"]
            ).adapt_user_input(asked, session_id="stepped-wiring-session"),
            persona=load_active_persona(agent.persona_id),
            session_id="stepped-wiring-session",
            source_context=source_context,
        )
    return str(result["response"])


def test_the_grounded_turn_takes_the_stepped_branch_on_an_audit_turn(make_agent) -> None:
    from core.memory_first_router import ModelExecutionDecision

    agent = make_agent()
    sentinel = ModelExecutionDecision(
        source="model", task_hash="", provider_id="p", model_name="m", used_model=True,
        output_text="STEPPED-SENTINEL-ANSWER", validation_state="valid",
    )
    with mock.patch(
        "core.agent_runtime.stepped_audit.run_stepped_audit", return_value=sentinel
    ) as stepped:
        response = _drive_grounded_turn(
            agent,
            source_context={
                "surface": "openclaw", "platform": "openclaw",
                "workspace_audit_evidence_collected": True,
            },
            resolve_decision=None,
        )
    assert stepped.call_count == 1
    assert "STEPPED-SENTINEL-ANSWER" in response
    assert agent.memory_router.resolve.call_count == 0, (
        "the single open-ended call must not run when the stepped lane answered"
    )


def test_the_stepped_lane_falls_back_to_the_single_shot_when_it_cannot_run(make_agent) -> None:
    from core.memory_first_router import ModelExecutionDecision

    agent = make_agent()
    fallback = ModelExecutionDecision(
        source="provider", task_hash="h", provider_id="p", model_name="m", used_model=True,
        output_text="single-shot answer", validation_state="valid",
    )
    with mock.patch("core.agent_runtime.stepped_audit.run_stepped_audit", return_value=None):
        response = _drive_grounded_turn(
            agent,
            source_context={
                "surface": "openclaw", "platform": "openclaw",
                "workspace_audit_evidence_collected": True,
            },
            resolve_decision=fallback,
        )
    assert agent.memory_router.resolve.call_count == 1
    assert "single-shot answer" in response


def test_a_non_audit_turn_never_enters_the_stepped_lane(make_agent) -> None:
    from core.memory_first_router import ModelExecutionDecision

    agent = make_agent()
    fallback = ModelExecutionDecision(
        source="provider", task_hash="h", provider_id="p", model_name="m", used_model=True,
        output_text="ordinary answer", validation_state="valid",
    )
    with mock.patch(
        "core.agent_runtime.stepped_audit.run_stepped_audit", return_value=None
    ) as stepped:
        response = _drive_grounded_turn(
            agent,
            source_context={"surface": "openclaw", "platform": "openclaw"},
            resolve_decision=fallback,
        )
    assert stepped.call_count == 0
    assert "ordinary answer" in response
