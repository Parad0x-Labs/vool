"""Eleven tools asked for in one reply should not cost eleven trips to the model.

The 2026-08-03 incident, third and last fix. The model emitted parallel tool batches, the loop ran
exactly ONE member per round and recorded the rest as deferred, and each round resent full context
at roughly 20k tokens. The model — never having received the files — asked again, the repeat guard
scored that as looping, and the turn ended on its own preamble.

Read-only members now run inline, in provider order, through the same `_execute_tool_intent` seam
as the head of the batch, so the permission gate and the turn's cancel signal fire per member
rather than once per round.

Measured through the real loop:

    4 reads in one batch -> 4 executed, ONE round (router called twice, not five times)
                            4 observations handed to the model, all four markers present

THE SAFETY PROPERTY, and it is the reason two of three rejected designs died. Inline execution is
gated on DECLARED CAPABILITY — `side_effect_class == "read_only"` — never on a name list. The
rejected `execute-the-batch` gated on `is_mutating_tool_intent`, a 15-name set that does not
contain `workspace.run_tests`, and `workspace.run_tests` passes the model's own `command` through
verbatim. Its red team measured a file overwritten with `DESTROYED`. A positive allow excludes that
by construction, and refuses an UNDECLARED intent for free — the case a blocklist can never cover.
"""
from __future__ import annotations

import pathlib
import tempfile
import uuid
from types import SimpleNamespace

import pytest

from apps.vool_agent import VoolAgent
from core.agent_runtime.research_tool_loop_facade import (
    _MAX_BATCH_MEMBERS_PER_ROUND,
    _batch_member_is_read_only,
)
from core.memory_first_router import ModelExecutionDecision

FILES = {
    "README.md": "MARKER_README",
    "Cargo.toml": "MARKER_CARGO",
    "a.py": "MARKER_A",
    "b.py": "MARKER_B",
    "c.py": "MARKER_C",
}


def _call(intent: str, **arguments):
    return SimpleNamespace(intent=intent, arguments=arguments, call_id=f"c-{intent}", name=intent)


# --------------------------------------------------------------------------------------
# The capability gate, alone. A name list is what got two designs rejected.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "intent",
    ["workspace.read_file", "workspace.list_files", "workspace.list_tree", "workspace.search_text"],
)
def test_a_declared_read_only_tool_may_run_inline(intent: str) -> None:
    assert _batch_member_is_read_only(intent)


@pytest.mark.parametrize(
    "intent",
    [
        # The one that overwrote a file with DESTROYED in the rejected design: it is
        # `validation_command`, not read_only, and it passes the model's `command` through.
        "workspace.run_tests",
        "workspace.run_lint",
        "workspace.write_file",
        "machine.write_file",
        "machine.ensure_directory",
        "orchestration.execute_envelope",
        "sandbox.run_command",
        # Undeclared today. A blocklist cannot cover this case; a positive allow does, for free.
        "workspace.apply_patch",
        # A tool that does not exist at all, which is what a compromised or confused reply looks
        # like. Safe by default is the whole point of gating on declaration.
        "made.up.tool",
        "",
    ],
)
def test_anything_not_declared_read_only_is_refused(intent: str) -> None:
    assert not _batch_member_is_read_only(intent)


# --------------------------------------------------------------------------------------
# Through the real loop.
# --------------------------------------------------------------------------------------


class _Router:
    def __init__(self, batch):
        self.batch = batch
        self.calls = 0
        self.last_context: dict = {}

    def resolve_tool_intent(self, **kwargs):
        self.calls += 1
        self.last_context = dict(kwargs.get("source_context") or {})
        if self.calls == 1:
            head = self.batch[0]
            return ModelExecutionDecision(
                source="scripted", task_hash="h", used_model=True, confidence=0.8,
                structured_output={"intent": head.intent, "arguments": dict(head.arguments)},
                tool_calls=tuple(self.batch), validation_state="validated", provider_id="p",
            )
        return ModelExecutionDecision(
            source="scripted", task_hash="h", used_model=True, confidence=0.8,
            structured_output={"intent": "respond.direct", "arguments": {"message": "Done."}},
            tool_calls=(_call("respond.direct", message="Done."),),
            validation_state="validated", provider_id="p",
        )


def _drive(batch):
    workspace = pathlib.Path(tempfile.mkdtemp())
    for name, marker in FILES.items():
        (workspace / name).write_text(marker + "\n", encoding="utf-8")

    router = _Router(batch)
    agent = VoolAgent.__new__(VoolAgent)
    agent.memory_router = router
    agent._should_keep_ai_first_chat_lane = lambda **_kw: False
    agent._should_run_builder_controller = lambda **_kw: False
    agent._plan_tool_workflow = lambda **_kw: SimpleNamespace(
        handled=False, stop_after=False, next_payload=None, reason=""
    )
    agent.hive_activity_tracker = None
    agent.public_hive_bridge = None

    session = f"openclaw:{uuid.uuid4().hex[:20]}"
    result = agent._maybe_execute_model_tool_intent(
        task=SimpleNamespace(task_id="t1"),
        effective_input="check the project and tell me what is broken",
        classification={"task_class": "debugging"},
        interpretation=SimpleNamespace(),
        context_result=SimpleNamespace(),
        persona=SimpleNamespace(),
        session_id=session,
        source_context={
            "session_id": session, "runtime_session_id": session,
            "surface": "api", "workspace": str(workspace), "workspace_root": str(workspace),
            "workspace_binding": "project", "project_id": workspace.name,
        },
        surface="api",
    )
    return result or {}, router, workspace


READ_BATCH = [
    _call("workspace.read_file", path="README.md"),
    _call("workspace.read_file", path="Cargo.toml"),
    _call("workspace.read_file", path="a.py"),
    _call("workspace.read_file", path="b.py"),
]


def test_every_read_in_the_batch_executes() -> None:
    result, _router, _ws = _drive(READ_BATCH)

    assert (result.get("details") or {}).get("tool_steps") == ["workspace.read_file"] * 4


def test_the_batch_costs_one_model_round() -> None:
    """The token claim, measured. One member per round meant ~20k tokens per file."""

    _result, router, _ws = _drive(READ_BATCH)

    assert router.calls == 2, "each batch member is still costing its own trip to the model"


def test_every_result_reaches_the_model() -> None:
    """The assertion that catches the defect everything else can pass over.

    A batch can execute perfectly and still deliver nothing — that was the state of this runtime
    before `_runtime_tool_observation_message` was fixed, and it is why the incident looked like a
    looping model. Executed-count and delivered-count have to match.
    """

    import json

    _result, router, _ws = _drive(READ_BATCH)

    observations = router.last_context.get("runtime_tool_observations") or []
    blob = json.dumps(observations, default=str)

    assert len(observations) == 4
    for marker in ("MARKER_README", "MARKER_CARGO", "MARKER_A", "MARKER_B"):
        assert marker in blob, f"{marker} executed but never reached the model"


# --------------------------------------------------------------------------------------
# Safety. This is the section that decides whether the change may ship at all.
# --------------------------------------------------------------------------------------


def test_a_shell_running_member_does_not_run_and_stops_the_walk() -> None:
    """The exact attack that disqualified `execute-the-batch`.

    `workspace.run_tests` returns the model's own `command` verbatim, so a build that lets it run
    as a batch member runs arbitrary local shell blind. Its red team measured a file overwritten
    with `DESTROYED`.
    """

    batch = [
        _call("workspace.read_file", path="README.md"),
        _call("workspace.read_file", path="Cargo.toml"),
        _call("workspace.run_tests", command="sh -c 'echo DESTROYED > a.py'"),
        _call("workspace.read_file", path="b.py"),
    ]

    result, _router, workspace = _drive(batch)

    assert "DESTROYED" not in (workspace / "a.py").read_text()
    assert (workspace / "a.py").read_text().strip() == "MARKER_A"
    assert (result.get("details") or {}).get("tool_steps") == ["workspace.read_file"] * 2


def test_nothing_behind_the_stop_runs_out_of_order() -> None:
    """`b.py` sits AFTER the refused member and must not be pulled forward.

    Reordering would run tools in an order the model never asked for, and would run a read whose
    whole purpose might have been to observe the refused write's effect.
    """

    import json

    batch = [
        _call("workspace.read_file", path="README.md"),
        _call("workspace.write_file", path="a.py", content="clobbered"),
        _call("workspace.read_file", path="b.py"),
    ]

    _result, router, workspace = _drive(batch)

    blob = json.dumps(router.last_context.get("runtime_tool_observations") or [], default=str)
    assert "MARKER_B" not in blob
    assert (workspace / "a.py").read_text().strip() == "MARKER_A"


def test_the_deferred_members_are_reported_with_their_arguments() -> None:
    """"`workspace.read_file` did not run" is unactionable when three of them were requested."""

    batch = [
        _call("workspace.read_file", path="README.md"),
        _call("workspace.write_file", path="a.py", content="x"),
        _call("workspace.read_file", path="c.py"),
    ]

    _result, _router, _ws = _drive(batch)
    # The event payload is what the operator and the next prompt see; the step record carries the
    # same list. Asserting the names alone would pass with the paths thrown away.
    from core.agent_runtime.research_tool_loop_facade import _describe_batch_member

    assert _describe_batch_member({"intent": "workspace.read_file", "arguments": {"path": "c.py"}}) == (
        "workspace.read_file(c.py)"
    )


def test_a_batch_is_capped_per_round() -> None:
    """An uncapped build let a red team execute 200 tools in a single model round.

    The bound is written as a LITERAL, not as `_MAX_BATCH_MEMBERS_PER_ROUND`. The first version of
    this test imported the constant and compared against it, so raising the constant to 10,000
    raised the assertion with it and the sabotage pass went green — a test that cannot fail is not
    a test. The constant's own value is pinned separately below.
    """

    batch = [_call("workspace.read_file", path="README.md")] + [
        _call("workspace.read_file", path="Cargo.toml") for _ in range(30)
    ]

    result, _router, _ws = _drive(batch)
    steps = (result.get("details") or {}).get("tool_steps") or []

    assert len(steps) <= 8, f"31 tools were requested and {len(steps)} ran in one round"
    assert len(steps) < 31


def test_the_cap_constant_is_what_the_tests_assume() -> None:
    """Pinned on its own so the literal above and the code cannot drift apart silently.

    Which limit it serves (CLAUDE.md 4b): wall clock and provider rate limit, not tokens — every
    member is a local tool call costing no model output. Overflow defers and is reported, so being
    wrong costs one round-trip.
    """

    assert _MAX_BATCH_MEMBERS_PER_ROUND == 8


def test_a_head_awaiting_approval_does_not_release_its_batch() -> None:
    """Found by driving this change, not by reading it.

    A batch headed by `workspace.write_file` in manual mode pauses for the operator (mode
    `tool_preview`). Before this guard the read BEHIND it ran anyway - work happening while the
    operator was still being asked, and out of the order the model wrote them. Only a head that
    actually executed earns the rest of its batch.
    """

    batch = [
        _call("workspace.write_file", path="a.py", content="clobbered"),
        _call("workspace.read_file", path="b.py"),
    ]

    workspace = pathlib.Path(tempfile.mkdtemp())
    for name, marker in FILES.items():
        (workspace / name).write_text(marker + "\n", encoding="utf-8")
    router = _Router(batch)
    agent = VoolAgent.__new__(VoolAgent)
    agent.memory_router = router
    agent._should_keep_ai_first_chat_lane = lambda **_kw: False
    agent._should_run_builder_controller = lambda **_kw: False
    agent._plan_tool_workflow = lambda **_kw: SimpleNamespace(
        handled=False, stop_after=False, next_payload=None, reason=""
    )
    agent.hive_activity_tracker = None
    agent.public_hive_bridge = None
    result = agent._maybe_execute_model_tool_intent(
        task=SimpleNamespace(task_id="t1"),
        effective_input="update a.py and check b.py",
        classification={"task_class": "debugging"},
        interpretation=SimpleNamespace(),
        context_result=SimpleNamespace(),
        persona=SimpleNamespace(),
        session_id=f"openclaw:{uuid.uuid4().hex[:20]}",
        source_context={
            "surface": "api", "workspace": str(workspace), "workspace_root": str(workspace),
            "workspace_binding": "project", "project_id": workspace.name,
            "operating_mode": "manual",
        },
        surface="api",
    ) or {}

    assert result.get("mode") == "tool_preview", "the write no longer pauses for the operator"
    assert (result.get("details") or {}).get("tool_steps") == ["workspace.write_file"], (
        "a batch member ran while the operator was still being asked about the head"
    )
    assert (workspace / "a.py").read_text().strip() == "MARKER_A"


def test_a_lone_call_still_behaves_exactly_as_before() -> None:
    """The control. Most replies carry one call and must not notice any of this."""

    result, router, _ws = _drive([_call("workspace.read_file", path="README.md")])

    assert (result.get("details") or {}).get("tool_steps") == ["workspace.read_file"]
    assert router.calls == 2


# --------------------------------------------------------------------------------------
# The budget counts ROUNDS now, because a round can append several steps.
# --------------------------------------------------------------------------------------


def test_the_budget_is_not_spent_by_one_batched_round() -> None:
    """Counting steps would spend a 5-round budget on one round of six reads, then report that
    "the step budget was exhausted" — a cause that is false on its face.

    All three red teams on the rejected designs found this independently.
    """

    result, router, _ws = _drive(READ_BATCH)

    assert router.calls == 2, "the loop stopped early despite having rounds left"
    assert "budget was exhausted" not in str(result.get("response") or "").lower()


def test_coding_task_read_batch_executes_through_the_real_door(monkeypatch):
    original = _Router.resolve_tool_intent

    def next_batch(self, **kwargs):
        observations = kwargs.get("source_context", {}).get("runtime_tool_observations", [])
        if self.calls == 1:
            task_id = next(o["task_id"] for o in observations if o.get("task_id"))
            self.batch = [
                SimpleNamespace(intent="code.task.step", name="code.task.step", call_id=f"read-{name}",
                    arguments={"task_id": task_id, "step_id": f"read-{name}",
                               "intent": "workspace.read_file", "arguments": {"path": name}})
                for name in ("README.md", "Cargo.toml", "a.py")
            ]
            self.calls = 0
            decision = original(self, **kwargs)
            self.calls = 2
            return decision
        return original(self, **kwargs)

    monkeypatch.setattr(_Router, "resolve_tool_intent", next_batch)
    result, router, workspace = _drive([_call("code.task.open", objective="Inspect the failing project")])
    assert "details" in result, (result, router.calls, router.last_context.get("runtime_tool_observations"))
    assert result["details"]["tool_steps"].count("code.task.step") == 3, result
    observations = router.last_context["runtime_tool_observations"]
    import json
    evidence = json.dumps(observations)
    for marker in ("MARKER_README", "MARKER_CARGO", "MARKER_A"):
        assert marker in evidence
    assert not result["success"], "Reads alone must not complete a repair."
    assert (workspace / "a.py").read_text() == "MARKER_A\n"


@pytest.mark.parametrize("inner", ["workspace.run_tests", "workspace.write_file", "sandbox.run_command", "code.task.step", "unknown.tool"])
def test_wrapping_a_command_or_mutation_does_not_make_it_a_batch_read(inner):
    assert not _batch_member_is_read_only("code.task.step", {"intent": inner, "arguments": {}})


def test_tool_catalog_report_does_not_create_a_phantom_approval():
    result, router, _workspace = _drive([
        _call("operator.list_tools"),
        _call("workspace.read_file", path="README.md"),
    ])
    assert result["details"]["tool_steps"] == ["operator.list_tools", "workspace.read_file"]
    assert result.get("status") != "pending_approval"
    assert router.calls == 2
    assert router.last_context["runtime_tool_observations"][0]["intent"] == "operator.list_tools"
