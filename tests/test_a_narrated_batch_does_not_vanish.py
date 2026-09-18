"""A model that narrates while it requests files must still get the files read.

The 2026-08-03 incident, reproduced here in full. The operator asked "can you check whole workspace
project? tell me your insights what is wrong or broken and what is good also what improvements you
would suggest" and received, as the complete answer:

    "I'll do a proper audit. Let me read the key files first."

A provider emitting native parallel tool calls can put `respond.direct` at position 1 and the real
work behind it — that is what "narrate, then act" looks like on the wire. `structured_output` is
call #1 re-parsed, so the loop read the narration, took the direct-response exit, and returned
before the remaining members were ever looked at. No execution, no step record, and no
`tool_batch_deferred` either, because that accounting lives *after* the loop. The batch vanished
with no receipt anywhere and the preamble shipped with `success: True`.

Measured through the real loop, sabotaging only the promotion:

    without the fix     router calls: 1   tool_steps: []                        status: direct_response
                        response: "I'll do a proper audit. Let me read the key files first."
    with the fix        router calls: 2   tool_steps: ['workspace.read_file']   status: direct_response_after_tools
                        response: "File `README.md`: 1: MARKER_README"

This is the mandate's section 4 in one line: stopping is decided by task completion, never by the
mere presence of assistant text.

NOT fixed here, deliberately: only the FIRST executable member runs. The rest are still deferred
and reported, which is the standing batch-execution work. This change stops the batch from
vanishing; it does not yet make it run.
"""
from __future__ import annotations

import pathlib
import tempfile
import uuid
from types import SimpleNamespace

import pytest

from apps.vool_agent import VoolAgent
from core.agent_runtime.research_tool_loop_facade import _first_executable_batch_member
from core.memory_first_router import ModelExecutionDecision

NARRATION = "I'll do a proper audit. Let me read the key files first."


def _call(intent: str, **arguments):
    return SimpleNamespace(intent=intent, arguments=arguments, call_id=f"c-{intent}", name=intent)


# --------------------------------------------------------------------------------------
# The picker, at its edges. An over-eager promotion would break every ordinary closing reply.
# --------------------------------------------------------------------------------------


def test_the_incident_shape_promotes_the_first_real_tool() -> None:
    decision = SimpleNamespace(
        tool_calls=(
            _call("respond.direct", message=NARRATION),
            _call("workspace.read_file", path="README.md"),
            _call("workspace.read_file", path="Cargo.toml"),
        )
    )

    promoted = _first_executable_batch_member(decision)

    assert promoted == {"intent": "workspace.read_file", "arguments": {"path": "README.md"}}


@pytest.mark.parametrize(
    ("label", "calls"),
    [
        ("a plain closing reply", [_call("respond.direct", message="Here is the answer.")]),
        ("no batch at all", []),
        (
            "only non-executing members",
            [_call("respond.direct", message="done"), _call("none"), _call("no_tool")],
        ),
    ],
)
def test_an_ordinary_reply_is_not_promoted(label: str, calls: list) -> None:
    """The control that matters most.

    Every turn that ends normally ends through this branch. Promoting anything here would turn
    each closing message into another tool step and the turn would never finish.
    """

    assert _first_executable_batch_member(SimpleNamespace(tool_calls=tuple(calls))) is None, label


# --------------------------------------------------------------------------------------
# The wiring, through the real loop. The picker tests above pass whether or not it is called.
# --------------------------------------------------------------------------------------


class _Router:
    """Scripted at `resolve_tool_intent` — the provider seam, and nothing below it."""

    def __init__(self, first_batch):
        self.first_batch = first_batch
        self.calls = 0

    def resolve_tool_intent(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            head = self.first_batch[0]
            return ModelExecutionDecision(
                source="scripted", task_hash="h", used_model=True, confidence=0.8,
                structured_output={"intent": head.intent, "arguments": dict(head.arguments)},
                tool_calls=tuple(self.first_batch), validation_state="validated", provider_id="p",
            )
        return ModelExecutionDecision(
            source="scripted", task_hash="h", used_model=True, confidence=0.8,
            structured_output={"intent": "respond.direct", "arguments": {"message": "Done reading."}},
            tool_calls=(_call("respond.direct", message="Done reading."),),
            validation_state="validated", provider_id="p",
        )


def _drive(first_batch) -> tuple[dict, _Router]:
    workspace = pathlib.Path(tempfile.mkdtemp())
    (workspace / "README.md").write_text("MARKER_README\n", encoding="utf-8")
    (workspace / "Cargo.toml").write_text("MARKER_CARGO\n", encoding="utf-8")

    router = _Router(first_batch)
    agent = VoolAgent.__new__(VoolAgent)
    agent.memory_router = router
    # Three PRE-LOOP gates belonging to OTHER lanes. Each owns its own tests; leaving them live
    # makes this test about their predicates instead of about the batch.
    agent._should_keep_ai_first_chat_lane = lambda **_kw: False
    agent._should_run_builder_controller = lambda **_kw: False
    agent._plan_tool_workflow = lambda **_kw: SimpleNamespace(
        handled=False, stop_after=False, next_payload=None, reason=""
    )
    agent.hive_activity_tracker = None
    agent.public_hive_bridge = None

    result = agent._maybe_execute_model_tool_intent(
        task=SimpleNamespace(task_id="t1"),
        effective_input="check the whole project and tell me what is broken",
        classification={"task_class": "debugging"},
        interpretation=SimpleNamespace(),
        context_result=SimpleNamespace(),
        persona=SimpleNamespace(),
        session_id=f"openclaw:{uuid.uuid4().hex[:20]}",
        source_context={
            "surface": "api",
            "workspace": str(workspace),
            "workspace_root": str(workspace),
            "workspace_binding": "project",
            "project_id": workspace.name,
        },
        surface="api",
    )
    return result or {}, router


def test_the_turn_does_not_end_on_the_narration() -> None:
    """The measured incident. This exact string was the operator's entire answer."""

    result, _router = _drive(
        [
            _call("respond.direct", message=NARRATION),
            _call("workspace.read_file", path="README.md"),
            _call("workspace.read_file", path="Cargo.toml"),
        ]
    )

    assert NARRATION not in str(result.get("response") or ""), (
        "the model's opening preamble shipped as the finished answer"
    )


def test_a_tool_actually_ran() -> None:
    result, _router = _drive(
        [
            _call("respond.direct", message=NARRATION),
            _call("workspace.read_file", path="README.md"),
        ]
    )

    assert (result.get("details") or {}).get("tool_steps") == ["workspace.read_file"]
    assert result.get("status") == "direct_response_after_tools"


def test_the_file_content_reaches_the_answer() -> None:
    """Environmental, not prose: the marker only exists on disk."""

    result, _router = _drive(
        [
            _call("respond.direct", message=NARRATION),
            _call("workspace.read_file", path="README.md"),
        ]
    )

    assert "MARKER_README" in str(result.get("response") or "")


def test_the_model_is_asked_again_rather_than_closed_out() -> None:
    """One provider round-trip means the loop returned on the narration and never resumed."""

    _result, router = _drive(
        [
            _call("respond.direct", message=NARRATION),
            _call("workspace.read_file", path="README.md"),
        ]
    )

    assert router.calls == 2


def test_a_reply_with_no_tool_members_still_closes_the_turn() -> None:
    """The control, through the real loop.

    If this regressed, every normal turn would keep looping instead of answering — a far worse
    failure than the one being fixed.
    """

    result, router = _drive([_call("respond.direct", message="Here is your answer.")])

    assert "Here is your answer." in str(result.get("response") or "")
    assert router.calls == 1
    assert (result.get("details") or {}).get("tool_steps") == []
