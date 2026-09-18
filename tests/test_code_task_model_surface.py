"""The model-facing surface of an open code task: one plane, visible vocabulary, actionable feedback.

Measured on the pinned base df49f096 (native runs c354e500/01ce8109 and the boundary probes):

- the offer seated top-level ``workspace.run_tests`` beside the task's control plane, and the
  model door EXECUTED it: the model saw the real failing output while the task journal stayed at
  ``reproduce`` with no steps -- honest work, silently disconnected from the task;
- a top-level ``sandbox.run_command`` was refused with "mutations must be proposed through
  ``code.task.propose``" -- a dead end, because ``code.task.propose`` refuses command intents as
  ``not_a_mutation``;
- the ``code.task.step`` wire schema carried a free-form ``intent`` string with three examples,
  so models guessed (``code.task.step`` nested inside itself, catalog calls) instead of choosing
  from the real vocabulary;
- the bounded completion feedback said "the next real tool action for their stage" without ever
  naming what that action is.

This pack pins the repaired surface and the controls that keep the repair honest: no task open ->
the ordinary doors behave exactly as before; reads stay open beside a task; the stage machine's
own laws (command stages, owner-read, proposal mutations) are unchanged.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

BUGGY = "exports.add = (left, right) => left - right;\n"
FIXED = "exports.add = (left, right) => left + right;\n"
TEST = (
    "const assert = require('assert');\n"
    "const { add } = require('./math.js');\n"
    "assert.strictEqual(add(11, 4), 15);\n"
)


def _git(root: Path, *args: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "fx",
        "GIT_AUTHOR_EMAIL": "fx@local",
        "GIT_COMMITTER_NAME": "fx",
        "GIT_COMMITTER_EMAIL": "fx@local",
    }
    out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30, env=env)
    assert out.returncode == 0, out.stderr


@pytest.fixture
def fixture_repo(tmp_path, monkeypatch):
    from core.mode_permission_policy import reset_mode_permission_state

    reset_mode_permission_state()
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    monkeypatch.setenv("VOOL_CODE_TASK_DIR", str(tmp_path / "code_tasks"))
    root = tmp_path / "repo"
    root.mkdir()
    (root / "math.js").write_text(BUGGY, encoding="utf-8")
    (root / "test.js").write_text(TEST, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "seed")
    yield root
    reset_mode_permission_state()


def _ctx(root: Path, session: str = "surface-session", **extra) -> dict:
    ctx = {"workspace": str(root), "workspace_root": str(root), "session_id": session,
           "runtime_session_id": session, "operating_mode": "auto"}
    ctx.update(extra)
    return ctx


def _door(intent: str, arguments: dict, ctx: dict):
    from core.runtime_execution_tools import execute_runtime_tool

    result = execute_runtime_tool(intent, arguments, source_context=ctx)
    assert result is not None, f"{intent} is not contracted at the production door"
    return result


def _journal(root: Path, task_id: str) -> dict:
    import json

    return json.loads(
        (Path(os.environ["VOOL_CODE_TASK_DIR"]) / f"{task_id}.json").read_text(encoding="utf-8")
    )


def _model_door(intent: str, arguments: dict, ctx: dict, session: str):
    from core.tool_intent_executor import execute_tool_intent
    from tests._toolchain_fixtures import executor_kwargs

    return execute_tool_intent(
        {"intent": intent, "arguments": arguments},
        **executor_kwargs(
            session,
            runtime_session_id=session,
            workspace=ctx["workspace"],
            workspace_root=ctx["workspace_root"],
            operating_mode="auto",
            surface="api",
        ),
    )


# ---------------------------------------------------------------------------
# The executor guard: evidence commands belong to the task plane
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("intent", ["workspace.run_tests", "sandbox.run_command"])
def test_top_level_evidence_commands_are_refused_with_the_step_path(fixture_repo, intent):
    """The measured silent-disconnect hole: a top-level test/shell call beside an open task
    executed for real while the journal recorded nothing. It must be refused BEFORE execution,
    with the refusal naming the action that is actually lawful for a command."""
    ctx = _ctx(fixture_repo)
    task_id = _door("code.task.open", {"objective": "Fix math.js"}, ctx).details["task_id"]
    out = _model_door(intent, {"command": "node test.js"}, ctx, "surface-session")
    assert out.ok is False and out.status == "code_task_control_required", (out.status, out.response_text)
    assert out.details.get("executed") is False
    text = str(out.response_text)
    assert "code.task.step" in text and intent in text, text
    # The propose dead-end is gone: a command is not a mutation and must not be sent there.
    assert "code.task.propose" not in text
    journal = _journal(fixture_repo, task_id)
    assert journal["stage"] == "reproduce" and not journal["steps"]
    assert journal["reproduced_failure"] is None


def test_top_level_mutation_refusal_still_names_the_proposal_path(fixture_repo):
    ctx = _ctx(fixture_repo)
    _door("code.task.open", {"objective": "Fix math.js"}, ctx)
    out = _model_door(
        "workspace.write_file", {"path": "scratch.txt", "content": "x"}, ctx, "surface-session"
    )
    assert out.ok is False and out.status == "code_task_control_required"
    assert "code.task.propose" in str(out.response_text)
    assert not (fixture_repo / "scratch.txt").exists()


def test_command_refusal_names_the_lawful_stages_when_stage_forbids_commands(fixture_repo):
    ctx = _ctx(fixture_repo)
    task_id = _door("code.task.open", {"objective": "Fix math.js"}, ctx).details["task_id"]
    _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "s1", "intent": "workspace.run_tests",
         "arguments": {"command": "node test.js"}},
        ctx,
    )
    _door("code.task.identify", {"task_id": task_id, "path": "math.js", "reason": "subtracts"}, ctx)
    _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "s2", "intent": "workspace.read_file", "arguments": {"path": "math.js"}},
        ctx,
    )
    proposal = _door(
        "code.task.propose",
        {"task_id": task_id, "intent": "workspace.write_file",
         "arguments": {"path": "math.js", "content": FIXED}, "rationale": "math.js subtracts; restore the sum."},
        ctx,
    )
    _door("code.task.approve", {"task_id": task_id, "proposal_id": proposal.details["proposal_id"]}, ctx)
    # mutate stage: commands are order disobedience here; the refusal must say WHERE they run.
    out = _model_door("workspace.run_tests", {"command": "node test.js"}, ctx, "surface-session")
    assert out.ok is False and out.status == "code_task_control_required"
    text = str(out.response_text)
    assert "mutate" in text and "reproduce" in text and "code.task.step" in text, text


# ---------------------------------------------------------------------------
# Controls: the guard holds only while a task owns the session
# ---------------------------------------------------------------------------


def test_without_an_open_task_the_ordinary_doors_are_unchanged(fixture_repo):
    ctx = _ctx(fixture_repo)
    out = _model_door("workspace.run_tests", {"command": "node test.js"}, ctx, "surface-session")
    assert out.status == "command_failed", out.status  # the real failing suite, really run
    assert _journal_dir_empty()


def test_reads_stay_open_beside_a_task(fixture_repo):
    ctx = _ctx(fixture_repo)
    _door("code.task.open", {"objective": "Fix math.js"}, ctx)
    out = _model_door("workspace.read_file", {"path": "math.js"}, ctx, "surface-session")
    assert out.ok is True, (out.status, out.response_text)
    assert "left - right" in str(out.response_text)


def _journal_dir_empty() -> bool:
    root = Path(os.environ["VOOL_CODE_TASK_DIR"])
    return not root.is_dir() or not any(root.glob("ct-*.json"))


# ---------------------------------------------------------------------------
# The wire schema: the inner vocabulary is an enum, not a guessing game
# ---------------------------------------------------------------------------


def test_step_wire_schema_carries_the_full_inner_enum():
    from core.cloud_tool_call_contract import build_cloud_tool_definitions, openai_tool_payload
    from core.runtime_execution_tools import runtime_execution_tool_specs

    spec = next(row for row in runtime_execution_tool_specs() if row["intent"] == "code.task.step")
    (definition,) = build_cloud_tool_definitions([spec])
    wire = openai_tool_payload(definition)
    intent_prop = wire["function"]["parameters"]["properties"]["intent"]
    enum = set(intent_prop.get("enum") or [])
    assert enum, "the inner vocabulary must be visible on the wire"
    # reads, evidence commands and mutations are all choosable
    assert {"workspace.read_file", "workspace.run_tests", "workspace.write_file",
            "workspace.git_diff", "sandbox.run_command"} <= enum
    # the nested class that native runs actually produced is not choosable
    assert not any(name.startswith("code.task.") for name in enum)
    assert "respond.direct" not in enum and "operator.list_tools" not in enum


def test_propose_wire_schema_carries_only_mutation_intents():
    from core.cloud_tool_call_contract import build_cloud_tool_definitions, openai_tool_payload
    from core.runtime_execution_tools import runtime_execution_tool_specs

    spec = next(row for row in runtime_execution_tool_specs() if row["intent"] == "code.task.propose")
    (definition,) = build_cloud_tool_definitions([spec])
    wire = openai_tool_payload(definition)
    enum = set(wire["function"]["parameters"]["properties"]["intent"].get("enum") or [])
    assert enum
    assert "workspace.write_file" in enum and "workspace.replace_in_file" in enum
    # proposing a command or a read is refused by the runtime; the schema must not offer it
    assert "workspace.run_tests" not in enum
    assert "sandbox.run_command" not in enum
    assert "workspace.read_file" not in enum


def test_the_enum_round_trips_through_native_parsing():
    from core.cloud_tool_call_contract import build_cloud_tool_definitions, parse_native_tool_calls
    from core.runtime_execution_tools import runtime_execution_tool_specs

    spec = next(row for row in runtime_execution_tool_specs() if row["intent"] == "code.task.step")
    (definition,) = build_cloud_tool_definitions([spec])
    args = {"task_id": "ct-x", "intent": "workspace.run_tests", "arguments": {"command": "node test.js"}}
    (call,) = parse_native_tool_calls(
        [{"function": {"name": definition.name, "arguments": args}}], definitions=(definition,)
    )
    assert call.arguments == args


# ---------------------------------------------------------------------------
# The offer: the seat stays resolvable; the DOOR carries the correction
# ---------------------------------------------------------------------------


def test_open_task_offer_keeps_validation_seats_parseable(fixture_repo):
    """The correction lives at the executor's door, not behind the offer. The native adapter
    validates a called function name against the OFFERED definitions: hiding the seat turns a
    model's top-level ``workspace.run_tests`` call into an unparseable transport error with no
    guidance, instead of a parseable call the task guard refuses WITH the lawful path. While a
    task is open the seat may therefore still resolve -- and the guard test above is what the
    model meets when it uses it."""
    from core.tool_offer_assembly import assemble_tool_offer

    ctx = _ctx(fixture_repo)
    prompt = "Fix math.js so the existing test.js passes. Run the existing tests."
    _door("code.task.open", {"objective": prompt}, ctx)
    offer = assemble_tool_offer(user_text=prompt, task_class="shell_guidance", source_context=dict(ctx))
    intents = set(offer.intents)
    assert {"code.task.step", "code.task.report", "code.task.cancel"} <= intents
    assert {"respond.direct", "operator.list_tools"} <= intents
    # a validation seat demanded by the words stays a resolvable name
    assert "workspace.run_tests" in intents
    # and the task plane stays the only coding MUTATION surface offered
    assert "workspace.write_file" not in intents


def test_offer_without_a_task_still_seats_validation_tools(fixture_repo):
    from core.tool_offer_assembly import assemble_tool_offer

    ctx = _ctx(fixture_repo)
    prompt = "Run the project's tests and tell me what fails."
    offer = assemble_tool_offer(user_text=prompt, task_class="shell_guidance", source_context=dict(ctx))
    assert "workspace.run_tests" in offer.intents, "the no-task offer must keep its ordinary shape"


# ---------------------------------------------------------------------------
# The ingress: an explicit repair demand is coding work, not a folder overview
# ---------------------------------------------------------------------------


def test_folder_overview_declines_an_explicit_repair_demand(tmp_path, monkeypatch):
    """The mission-worded original request names no file, so the base's folder-overview
    fast path read "this project" as a demonstrative and answered the repair demand with a
    directory listing in 0s -- the coding assistant never saw the turn. The overview must
    decline what the planner's own predicate calls a repair demand."""
    from core.agent_runtime.fast_paths_utility import maybe_handle_folder_overview_request

    agent = None  # the gate must decline before touching the agent
    demand = "Fix the bug in this project, run its tests, and explain what you changed."
    declined = maybe_handle_folder_overview_request(
        agent, demand, session_id="s", source_surface="api", source_context={"surface": "api"}
    )
    assert declined is None


def test_folder_overview_still_answers_a_genuine_overview_ask(tmp_path):
    from types import SimpleNamespace

    from core.agent_runtime.fast_paths_utility import maybe_handle_folder_overview_request

    captured = {}

    def _fast_path_result(**kwargs):
        captured.update(kwargs)
        return {"response": "overview"}

    agent = SimpleNamespace(_fast_path_result=_fast_path_result)
    reply = maybe_handle_folder_overview_request(
        agent, "What is this project about?", session_id="s", source_surface="api",
        source_context={"surface": "api", "workspace": str(tmp_path)},
    )
    assert reply is not None, "an ordinary overview ask keeps its fast answer"
    assert "bug" not in str(captured.get("user_input", ""))


# ---------------------------------------------------------------------------
# The bounded completion feedback names the stage's lawful next actions
# ---------------------------------------------------------------------------


def test_completion_rows_carry_stage_actions():
    from core.code_assistant.task_runtime import enforce_code_task_completion

    verdict = enforce_code_task_completion(
        {"response": "done", "success": True, "details": {}},
        [{"tool_name": "code.task.open", "details": {"task_id": "ct-a", "stage": "reproduce",
                                                     "code_task_verdict": "unresolved"}}],
    )
    rows = verdict["details"]["unfinished_code_tasks"]
    assert rows and rows[0]["stage"] == "reproduce"
    actions = " ".join(rows[0].get("next") or [])
    assert "code.task.step" in actions and "workspace.run_tests" in actions, actions


@pytest.mark.parametrize(
    ("stage", "must_name"),
    [
        ("reproduce", "workspace.run_tests"),
        ("identify", "workspace.read_file"),
        ("propose", "code.task.propose"),
        ("approve", "code.task.approve"),
        ("mutate", "code.task.step"),
        ("narrow_test", "workspace.run_tests"),
        ("cumulative", "workspace.run_tests"),
        ("inspect_diff", "workspace.git_diff"),
        ("report", "code.task.report"),
    ],
)
def test_every_stage_has_an_actionable_next_action(stage, must_name):
    from core.code_assistant.task_runtime import stage_next_actions

    actions = " ".join(stage_next_actions(stage))
    assert must_name in actions, (stage, actions)


def test_feedback_block_renders_the_actions_for_the_model():
    from core.prompt_normalizer import _runtime_tool_observation_message

    msg = _runtime_tool_observation_message(
        {
            "runtime_tool_observations": [{"intent": "code.task.open", "ok": True, "task_id": "ct-proof"}],
            "code_task_completion_feedback": [
                {"task_id": "ct-proof", "stage": "reproduce",
                 "next": ["run the failing suite through `code.task.step` with intent `workspace.run_tests`"]}
            ],
        }
    )
    text = str(msg.content)
    assert "ct-proof at stage reproduce" in text
    assert "workspace.run_tests" in text
    assert "unfinished" in text.lower()


# ---------------------------------------------------------------------------
# The owner-read gate points at the recovery instead of contradicting the model
# ---------------------------------------------------------------------------


def test_owner_not_read_names_the_step_read(fixture_repo):
    ctx = _ctx(fixture_repo)
    task_id = _door("code.task.open", {"objective": "Fix math.js"}, ctx).details["task_id"]
    _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "s1", "intent": "workspace.run_tests",
         "arguments": {"command": "node test.js"}},
        ctx,
    )
    _door("code.task.identify", {"task_id": task_id, "path": "math.js", "reason": "subtracts"}, ctx)
    # the model read the owner TOP-LEVEL: that read does not count for the task plane,
    # and the refusal must say exactly how to make it count.
    _model_door("workspace.read_file", {"path": "math.js"}, ctx, "surface-session")
    proposal = _door(
        "code.task.propose",
        {"task_id": task_id, "intent": "workspace.write_file",
         "arguments": {"path": "math.js", "content": FIXED}, "rationale": "math.js subtracts; restore the sum."},
        ctx,
    )
    assert proposal.ok is False and proposal.status == "owner_not_read"
    text = str(proposal.response_text)
    assert "code.task.step" in text and "workspace.read_file" in text, text
    # and following that instruction lifts the gate
    _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "s2", "intent": "workspace.read_file", "arguments": {"path": "math.js"}},
        ctx,
    )
    retry = _door(
        "code.task.propose",
        {"task_id": task_id, "intent": "workspace.write_file",
         "arguments": {"path": "math.js", "content": FIXED}, "rationale": "math.js subtracts; restore the sum."},
        ctx,
    )
    assert retry.ok is True, (retry.status, retry.response_text)


# ---------------------------------------------------------------------------
# Failed-verification recovery: the same durable task reopens reviewed repair
# ---------------------------------------------------------------------------


def _drive_to_failed_narrow(root: Path, *, wrong: str, check: str):
    """open -> red -> identify -> read -> propose(wrong) -> approve -> mutate -> failing check.

    Returns (ctx, task_id, original_bytes_hash)."""
    import hashlib as _hl

    (root / "math.js").write_text("exports.add = (a, b) => a - b;\n", encoding="utf-8")
    (root / "check.js").write_text(check, encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "widen")
    ctx = _ctx(root, session="recovery-unit")
    task_id = _door("code.task.open", {"objective": "repair"}, ctx).details["task_id"]
    red = _door("code.task.step", {"task_id": task_id, "step_id": "red", "intent": "workspace.run_tests",
                                   "arguments": {"command": "node check.js"}}, ctx)
    assert red.details["executed"] and red.details["tool_result"]["success"] is False
    _door("code.task.identify", {"task_id": task_id, "path": "math.js", "reason": "subtracts"}, ctx)
    _door("code.task.step", {"task_id": task_id, "step_id": "read", "intent": "workspace.read_file",
                             "arguments": {"path": "math.js"}}, ctx)
    original = (root / "math.js").read_bytes()
    args = {"path": "math.js", "content": wrong,
            "expected_hash": _hl.sha256(original).hexdigest()}
    _door("code.task.propose", {"task_id": task_id, "proposal_id": "p1", "intent": "workspace.write_file",
                                "arguments": args, "rationale": "Owner math.js: wrong operator"}, ctx)
    _door("code.task.approve", {"task_id": task_id, "proposal_id": "p1"}, ctx)
    mutation = _door("code.task.step", {"task_id": task_id, "step_id": "mut1", "intent": "workspace.write_file",
                                        "arguments": args}, ctx)
    assert mutation.ok, mutation.response_text
    failed = _door("code.task.step", {"task_id": task_id, "step_id": "ver1", "intent": "workspace.run_tests",
                                      "arguments": {"command": "node check.js"}}, ctx)
    assert failed.details["executed"] and failed.details["tool_result"]["success"] is False
    assert failed.details["stage"] == "narrow_test"
    assert failed.details.get("verification_failed") is True
    return ctx, task_id, _hl.sha256(original).hexdigest()


CHECK = "const {add}=require('./math.js');require('assert').strictEqual(add(7,4),11);\n"


def test_failed_verification_preserves_evidence_and_reopens_review(fixture_repo):
    import hashlib as _hl

    ctx, task_id, original_hash = _drive_to_failed_narrow(fixture_repo, wrong="exports.add = (a, b) => a * b;\n",
                                                          check=CHECK)
    # Recovery: a NEW diagnosis and proposal are lawful in the SAME task, and remain
    # review-only until freshly approved.
    re_identified = _door("code.task.identify", {"task_id": task_id, "path": "math.js",
                                                 "reason": "first fix wrong; assertions require addition"}, ctx)
    assert re_identified.ok, re_identified.response_text
    _door("code.task.step", {"task_id": task_id, "step_id": "reread", "intent": "workspace.read_file",
                             "arguments": {"path": "math.js"}}, ctx)
    fixed = "exports.add = (a, b) => a + b;\n"
    revised = _door("code.task.propose", {
        "task_id": task_id, "proposal_id": "p2", "intent": "workspace.write_file",
        "arguments": {"path": "math.js", "content": fixed,
                      "expected_hash": _hl.sha256("exports.add = (a, b) => a * b;\n".encode()).hexdigest()},
        "rationale": "Owner math.js: the first operator remains wrong; restore addition"}, ctx)
    assert revised.ok, revised.response_text
    assert (fixture_repo / "math.js").read_text() == "exports.add = (a, b) => a * b;\n"
    # The failed evidence and BOTH diagnoses are preserved in the journal.
    journal = _journal(fixture_repo, task_id)
    assert journal["narrow"]["success"] is False
    assert [v["success"] for v in journal["verifications"]] == [False]
    assert len(journal["diagnoses"]) == 1 and journal["defect"]["reason"].startswith("first fix wrong")


def test_recovery_requires_a_fresh_approval_for_changed_bytes(fixture_repo):
    import hashlib as _hl

    ctx, task_id, _base = _drive_to_failed_narrow(fixture_repo, wrong="exports.add = (a, b) => a * b;\n",
                                                  check=CHECK)
    _door("code.task.identify", {"task_id": task_id, "path": "math.js", "reason": "wrong operator"}, ctx)
    fixed = "exports.add = (a, b) => a + b;\n"
    _door("code.task.propose", {
        "task_id": task_id, "proposal_id": "p2", "intent": "workspace.write_file",
        "arguments": {"path": "math.js", "content": fixed,
                      "expected_hash": _hl.sha256("exports.add = (a, b) => a * b;\n".encode()).hexdigest()},
        "rationale": "Owner math.js: restore addition"}, ctx)
    # The FIRST proposal's approval does not cover the revised bytes: executing p2's
    # arguments without approving p2 must be refused byte-for-byte.
    unapproved = _door("code.task.step", {"task_id": task_id, "step_id": "mut2", "intent": "workspace.write_file",
                                          "arguments": {"path": "math.js", "content": fixed}}, ctx)
    assert unapproved.ok is False and unapproved.status == "stage_violation", unapproved.status
    assert (fixture_repo / "math.js").read_text() == "exports.add = (a, b) => a * b;\n"
    approve2 = _door("code.task.approve", {"task_id": task_id, "proposal_id": "p2"}, ctx)
    assert approve2.ok
    landed = _door("code.task.step", {"task_id": task_id, "step_id": "mut2b", "intent": "workspace.write_file",
                                      "arguments": {"path": "math.js", "content": fixed}}, ctx)
    assert landed.ok and (fixture_repo / "math.js").read_text() == fixed


def test_recovery_survives_a_cold_restart_and_cancellation(fixture_repo):
    from core.code_assistant.task_runtime import code_task_runtime

    ctx, task_id, _base = _drive_to_failed_narrow(fixture_repo, wrong="exports.add = (a, b) => a * b;\n",
                                                  check=CHECK)
    # Cold restart: drop the in-memory task; the journal must still admit recovery.
    code_task_runtime().reset()
    reopened = _door("code.task.identify", {"task_id": task_id, "path": "math.js", "reason": "post-restart"}, ctx)
    assert reopened.ok, reopened.response_text
    # Cancellation after a failed verification stays terminal.
    _door("code.task.step", {"task_id": task_id, "step_id": "ver2", "intent": "workspace.run_tests",
                             "arguments": {"command": "node check.js"}}, ctx)
    code_task_runtime().reset()
    _door("code.task.cancel", {"task_id": task_id, "reason": "operator stopped"}, ctx)
    refused = _door("code.task.identify", {"task_id": task_id, "path": "math.js", "reason": "too late"}, ctx)
    assert refused.ok is False and refused.status == "cancelled"


def test_failed_verification_seats_the_review_door_and_names_recovery(fixture_repo):
    from core.tool_offer_assembly import assemble_tool_offer

    ctx, task_id, _base = _drive_to_failed_narrow(fixture_repo, wrong="exports.add = (a, b) => a * b;\n",
                                                  check=CHECK)
    offer = assemble_tool_offer(user_text="continue the repair", task_class="debugging", source_context=dict(ctx))
    assert "code.task.identify" in offer.intents, offer.intents
    rows = [{"task_id": task_id, "stage": "narrow_test", "verification_failed": True,
             "next": ["the focused suite still fails: re-diagnose with `code.task.identify`"]}]
    from core.prompt_normalizer import _runtime_tool_observation_message

    text = str(_runtime_tool_observation_message({
        "runtime_tool_observations": [{"intent": "code.task.step", "ok": True}],
        "code_task_completion_feedback": rows,
    }).content)
    assert "re-diagnose with `code.task.identify`" in text
    # Before any failure the same stage must NOT claim recovery guidance.
    passing = __import__("core.code_assistant.task_runtime", fromlist=["stage_next_actions"]).stage_next_actions(
        "narrow_test")
    assert all("re-diagnose" not in action for action in passing)
