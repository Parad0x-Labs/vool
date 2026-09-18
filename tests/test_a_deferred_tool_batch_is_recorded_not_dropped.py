"""A provider that asks for several tools in one reply gets an account of what did not run.

Before this, `ModelResponse.tool_calls` was filled by every adapter and read by NOTHING:
`ModelExecutionDecision` had no such field, and the step loop consumed `structured_output` - a
single dict. A model that asked to read two files had the second request dropped with no record on
any lane.

**The extra calls are deliberately NOT executed.** The loop runs one call per step because every
guard it has is per-step: repeat detection, the step budget, the per-call permission gate, and
mutating-tool receipt idempotency keyed on `step_index`. Draining a queue through the loop's
pending slot would bypass all of them - that slot is the approval seam, it holds one payload, and
it is stored redacted - and would execute intent the model formed before it saw the first result.

What was actually harmful was the silence. A model cannot tell "my second call ran and returned
nothing" from "my second call never happened", and its next step is a guess either way. The loop
re-asks the model after every step with the accumulated results, so once it is TOLD, it can
re-request what it still needs with the first result in hand.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from core.agent_runtime.tool_result_history_surface import DeferredToolBatchSurfaceMixin
from core.memory_first_router import ModelExecutionDecision


@dataclass
class _Call:
    intent: str


def _decision(*intents: str) -> ModelExecutionDecision:
    return ModelExecutionDecision(
        source="provider_execution",
        task_hash="h",
        tool_calls=tuple(_Call(name) for name in intents),
    )


def test_the_decision_can_carry_the_batch_at_all() -> None:
    """The field whose absence was the whole defect."""

    assert _decision("workspace.read_file", "workspace.list_files").tool_calls != ()


def test_a_decision_defaults_to_no_batch() -> None:
    """Every other construction site must keep working untouched."""

    assert ModelExecutionDecision(source="memory_hit", task_hash="h").tool_calls == ()


@pytest.mark.parametrize(
    ("intents", "executed", "expected"),
    [
        (("workspace.read_file",), "workspace.read_file", []),
        (("workspace.read_file", "workspace.list_files"), "workspace.read_file", ["workspace.list_files"]),
        (
            ("workspace.read_file", "workspace.git_diff", "sandbox.run_command"),
            "workspace.read_file",
            ["workspace.git_diff", "sandbox.run_command"],
        ),
        ((), "workspace.read_file", []),
    ],
)
def test_the_unrun_members_are_named_in_provider_order(intents, executed, expected) -> None:
    members = DeferredToolBatchSurfaceMixin._unexecuted_batch_members(
        _decision(*intents), {"intent": executed}
    )

    assert members == expected


def test_the_same_tool_requested_twice_still_reports_the_second() -> None:
    """Only the FIRST occurrence is the one being executed.

    A provider that genuinely asked to read the same file twice has made two requests, and
    swallowing the second would be the original defect in miniature.
    """

    members = DeferredToolBatchSurfaceMixin._unexecuted_batch_members(
        _decision("workspace.read_file", "workspace.read_file"), {"intent": "workspace.read_file"}
    )

    assert members == ["workspace.read_file"]


def test_the_model_is_told_which_calls_did_not_run() -> None:
    """Logging it is not enough - the note has to reach the next model prompt."""

    context = DeferredToolBatchSurfaceMixin._note_deferred_batch_for_model(
        {"conversation_history": []}, deferred=["workspace.list_files", "sandbox.run_command"]
    )

    note = context["conversation_history"][-1]["content"]
    assert "NOT run" in note
    assert "workspace.list_files" in note and "sandbox.run_command" in note
    assert "3 tools" in note, "the count must include the one that did run"
    assert context["deferred_tool_calls"] == ["workspace.list_files", "sandbox.run_command"]


def test_nothing_is_said_when_there_was_no_batch() -> None:
    """The control. A note on every single-call turn would be noise the model has to parse."""

    context = DeferredToolBatchSurfaceMixin._note_deferred_batch_for_model(
        {"conversation_history": []}, deferred=[]
    )

    assert context["conversation_history"] == []
    assert "deferred_tool_calls" not in context


def test_the_note_does_not_destroy_existing_history() -> None:
    context = DeferredToolBatchSurfaceMixin._note_deferred_batch_for_model(
        {"conversation_history": [{"role": "user", "content": "original"}]},
        deferred=["workspace.list_files"],
    )

    assert context["conversation_history"][0]["content"] == "original"
    assert len(context["conversation_history"]) == 2


# --------------------------------------------------------------------------------------
# The router copy site. Constructing a decision by hand proves nothing about the wiring.
# --------------------------------------------------------------------------------------


def test_the_router_copies_the_provider_batch_onto_the_decision(monkeypatch) -> None:
    """`_decision_from_response` is the single funnel for every served provider response.

    The tests above build a `ModelExecutionDecision` directly, so they pass whether or not the
    router ever fills the field. This one drives the real copy: it is the difference between the
    field existing and the batch actually arriving.
    """


    from adapters.base_adapter import ModelResponse
    from core.cloud_provider_contract import CloudToolCall
    from core.memory_first_router import MemoryFirstRouter

    response = ModelResponse(
        output_text='{"intent": "workspace.read_file", "arguments": {"path": "a.txt"}}',
        confidence=0.7,
        output_mode="tool_intent",
        tool_calls=(
            CloudToolCall(call_id="1", intent="workspace.read_file", name="w", arguments={"path": "a.txt"}),
            CloudToolCall(call_id="2", intent="workspace.list_files", name="l", arguments={}),
        ),
    )
    class _Permissive:
        """Anything this test does not care about answers None.

        `_decision_from_response` touches trust scoring, licensing and retrieval confidence on its
        way to building the decision. Stubbing each one by hand would make this test about those
        fields; it is about exactly one - whether the provider's batch survives the copy.
        """

        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        def __getattr__(self, name):
            # Recursive so attribute CHAINS resolve, and falsey/zero-coercible so the numeric and
            # boolean reads along the way behave like "absent" rather than exploding.
            return _Permissive()

        def __bool__(self):
            return False

        def __float__(self):
            return 0.0

        def __str__(self):
            return ""

        def __iter__(self):
            return iter(())

        def __call__(self, *args, **kwargs):
            return _Permissive()

    manifest = _Permissive(
        provider_id="ollama-local:qwen3:8b", provider_name="ollama", model_name="qwen3:8b",
        metadata={}, runtime_config={},
    )

    # The decision path also records a cache candidate and emits telemetry. Neither is what this
    # test is about, and both choke on the stub, so they are silenced - the copy under test still
    # runs for real.
    import core.memory_first_router as mfr
    monkeypatch.setattr(mfr, "record_candidate_output", lambda *a, **k: None, raising=False)

    router = MemoryFirstRouter.__new__(MemoryFirstRouter)
    decision = MemoryFirstRouter._decision_from_response(
        router,
        manifest=manifest,
        adapter=_Permissive(),
        response=response,
        task_hash="h",
        task=_Permissive(task_id="t"),
        classification={"task_class": "debugging"},
        context_result=_Permissive(retrieval_confidence_score=0.5),
        task_kind="tool_intent",
        output_mode="tool_intent",
        provider_role="drone",
        ranked_manifests=[manifest],
        attempted=[],
        failover_used=False,
        source="provider_execution",
    )

    assert [call.intent for call in decision.tool_calls] == [
        "workspace.read_file",
        "workspace.list_files",
    ], "the router dropped the provider's batch before the loop could report it"
