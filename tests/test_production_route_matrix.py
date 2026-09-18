"""Ten request classes, driven through the PRODUCTION route, asserted on the route they take.

Why this exists alongside `test_execution_requirements.py`
----------------------------------------------------------
That file proves the authority computes the right contract. It does not prove the shipped route
consults it. Those are different claims, and the gap between them is where this project has
repeatedly shipped a green suite over a broken runtime: 10,000+ tests were passing on 2026-08-06
while every research turn in the app answered with no retrieval at all.

Two rules follow from that, and they are the whole design of this file:

1. **Use the context production actually sends.** `run_agent` starts from the shared production
   source-context contract in `core/web/api/runtime.py`. An earlier
   draft of the sibling test used `surface="openclaw"`, which is tool-capable but is a context the
   daemon never sends -- a test can pass on a context no user can produce. A behavioral probe below
   drives the real runtime to the agent boundary so this matrix cannot pass on an unused fixture.

2. **Assert on the ROUTE, not on the prose.** Whether a good answer comes back depends on model
   quality and provider health, neither of which is a routing fact. Whether the turn was OFFERED
   retrieval is a routing fact, it is deterministic, and it is the thing that was broken.

Provider health is deliberately not exercised here. Four dead providers once read as "the model
lane refuses ordinary questions"; conflating the two costs hours every time.
"""

from __future__ import annotations

import pytest

from core.execution_requirements import requirements_for
from core.web.api.runtime import default_agent_source_context

# --------------------------------------------------------------------------------------------
# The production context contract, shared with `run_agent` rather than hand-copied.
# --------------------------------------------------------------------------------------------

PRODUCTION_CONTEXT = default_agent_source_context()


def test_the_production_context_is_what_we_think_it_is() -> None:
    """Pin the route-relevant defaults and the deliberate absence of an override."""
    assert PRODUCTION_CONTEXT.get("surface") == "channel"
    assert PRODUCTION_CONTEXT.get("platform") == "openclaw"
    assert "allow_remote_fetch" not in PRODUCTION_CONTEXT


def test_run_agent_reaches_the_agent_with_the_production_context(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Behavioral anti-vacuity: the shared contract must cross the real runtime boundary."""
    from core.web.api.runtime import RuntimeServices, run_agent

    captured: dict[str, object] = {}

    class _ContextReachedError(RuntimeError):
        pass

    class _Agent:
        def run_once(self, _text, *, session_id_override, source_context):  # type: ignore[no-untyped-def]
            del session_id_override
            captured.update(source_context)
            raise _ContextReachedError

    monkeypatch.setattr("core.web.api.runtime._memory_recall_response", lambda *a, **k: None)
    runtime = RuntimeServices(agent=_Agent(), runtime_home=str(tmp_path))

    with pytest.raises(_ContextReachedError):
        run_agent(
            runtime,
            "explain how a mutex differs from a semaphore",
            workspace_root_provider=lambda: str(tmp_path),
        )

    assert captured["surface"] == "channel"
    assert captured["platform"] == "openclaw"
    assert captured["workspace_root"] == str(tmp_path)
    assert "allow_remote_fetch" not in captured


# --------------------------------------------------------------------------------------------
# The matrix. `retrieval` = the turn must be offered a retrieving path.
#            `direct`    = the turn must NOT be pushed through tooling.
# --------------------------------------------------------------------------------------------

MATRIX = [
    # 1. stable knowledge -- must stay cheap, no browsing, no tool-catalog latency
    ("stable-knowledge", "direct", "why is the sky blue"),
    ("stable-knowledge-long", "direct", "explain how a mutex differs from a semaphore, with an example"),
    # 2. creative -- must never be pushed through retrieval
    ("creative", "direct", "write a haiku about winter"),
    # 3. about the assistant -- carries every currency marker, still not a research turn
    ("self-reference", "direct", "what is your current mood right now"),
    ("self-reference-status", "direct", "which model did I select for this turn?"),
    # 4. explicit evidence promise
    ("evidence-promise", "retrieval", "give me the exact figures with sources, do not guess"),
    ("evidence-promise-verify", "retrieval", "only tell me what you can verify, no speculation"),
    # 5. fact-check
    ("fact-check", "retrieval", "fact-check this claim and cite your sources"),
    # 6. currency
    ("currency", "retrieval", "what is the current market share, sources please"),
    # 7. breakdown granularity a general source cannot support
    ("breakdown", "retrieval", "which country delivered the most units, broken down per year"),
    # 8. the shape that was broken in production on 2026-08-06 -- a research brief a keyword
    #    classifier labels `config`
    ("ev-brief-shape", "retrieval",
     "what is the most common battery-capacity configuration sold last year? use authoritative "
     "sources and do not guess"),
    # 9. comparison research
    ("comparison", "retrieval",
     "compare the two by cumulative units built and give the single highest-production year, "
     "with sources"),
    # 10. audit
    ("audit", "retrieval", "audit this module and verify every claim"),
]


def _lane_probe_agent():
    from core.agent_runtime.fast_path_facade import FastPathFacadeMixin
    from core.agent_runtime.hive_topic_facade import HiveTopicFacadeMixin
    from core.agent_runtime.proceed_intent_support import ProceedIntentSupportMixin

    class _Agent(ProceedIntentSupportMixin, FastPathFacadeMixin, HiveTopicFacadeMixin):
        def _is_chat_truth_surface(self, _source_context: object) -> bool:
            return True

    return _Agent()


def _route_of(text: str) -> dict[str, object]:
    """Run the shipped decision chain over the production context and report the route."""
    from core.agent_runtime.runtime_checkpoint_lane_policy import should_keep_ai_first_chat_lane
    from core.execution.planner import should_attempt_tool_intent

    context = dict(PRODUCTION_CONTEXT)
    agent = _lane_probe_agent()
    live_mode = agent._live_info_mode(text, interpretation=None)
    kept_in_chat_lane = should_keep_ai_first_chat_lane(
        agent,
        user_input=text,
        classification={"task_class": "chat_research"},
        interpretation=None,
        source_context=context,
        checkpoint_state=None,
    )
    return {
        "requirements": requirements_for(text, source_context=context),
        "tool_gate_open": should_attempt_tool_intent(
            text, task_class="unknown", source_context=context
        ),
        "live_info_mode": live_mode,
        "kept_in_chat_lane": kept_in_chat_lane,
        # A turn can retrieve either through the tool loop or through the live-info fast path;
        # every live-info mode performs a real `WebAdapter` search.
        "can_retrieve": bool(live_mode) or (
            should_attempt_tool_intent(text, task_class="unknown", source_context=context)
            and not kept_in_chat_lane
        ),
    }


@pytest.mark.parametrize(
    ("case_id", "expected", "text"),
    [pytest.param(c, e, t, id=c) for c, e, t in MATRIX],
)
def test_production_route(case_id: str, expected: str, text: str) -> None:
    route = _route_of(text)
    requirements = route["requirements"]

    if expected == "retrieval":
        assert requirements.tools_required is True, (
            f"[{case_id}] the authority did not require tools for a request that demands evidence; "
            f"reasons={list(requirements.reason_codes)}"
        )
        assert route["can_retrieve"] is True, (
            f"[{case_id}] production route offers NO retrieving path: "
            f"tool_gate_open={route['tool_gate_open']} live_info_mode={route['live_info_mode']!r} "
            f"kept_in_chat_lane={route['kept_in_chat_lane']}"
        )
    else:
        assert requirements.tools_required is False, (
            f"[{case_id}] a direct request was made to require tools; "
            f"reasons={list(requirements.reason_codes)}"
        )
        assert route["can_retrieve"] is False, (
            f"[{case_id}] a direct request was still offered a retrieving route: {route}"
        )


def test_the_matrix_covers_both_verdicts() -> None:
    """A matrix that only asserts one direction passes trivially if everything routes one way."""
    verdicts = {expected for _, expected, _ in MATRIX}
    assert verdicts == {"direct", "retrieval"}
    assert sum(1 for _, e, _ in MATRIX if e == "direct") >= 4
    assert sum(1 for _, e, _ in MATRIX if e == "retrieval") >= 6


# --------------------------------------------------------------------------------------------
# The failure, when it happens, must be NAMED rather than look like an ordinary answer.
# --------------------------------------------------------------------------------------------


def test_a_toolless_evidence_turn_is_classified_not_silently_answered() -> None:
    """`REQUIRED_TOOLS_NOT_OFFERED` is the point of this state.

    Without it the same turn classifies `SUCCESS`: text came back, no stage reported an error, and
    the trace looks clean. That is exactly how the aviation and EV turns read on 2026-08-06 -- a
    fluent answer over zero retrieval -- and why the first diagnosis blamed the search layer, which
    had never run.
    """
    from core.turn_failure_stage import StageObservation, TerminalState, classify_turn, explain

    requirements = requirements_for(
        "give me the exact figures with sources, do not guess",
        source_context=dict(PRODUCTION_CONTEXT),
    )
    assert requirements.tools_required is True

    starved = StageObservation(
        tools_required=requirements.tools_required,
        tools_offered_count=0,
        provider_called=True,
        raw_content="The most common configuration is roughly 75 kWh.",
        text_before_postprocessing="The most common configuration is roughly 75 kWh.",
        final_text="The most common configuration is roughly 75 kWh.",
    )
    assert classify_turn(starved) is TerminalState.REQUIRED_TOOLS_NOT_OFFERED
    assert "routing" in explain(TerminalState.REQUIRED_TOOLS_NOT_OFFERED)

    # The control: identical turn, tools actually offered -> the state must NOT fire, or it would
    # relabel every healthy turn and be worthless.
    served = StageObservation(
        tools_required=True,
        tools_offered_count=6,
        retrieval_attempted=True,
        retrieval_result_count=4,
        retrieval_on_topic_count=4,
        provider_called=True,
        raw_content="ok",
        text_before_postprocessing="ok",
        final_text="ok",
    )
    assert classify_turn(served) is TerminalState.SUCCESS

    # And a turn with no requirement is untouched by the new field's defaults.
    assert classify_turn(
        StageObservation(provider_called=True, raw_content="x", text_before_postprocessing="x", final_text="x")
    ) is TerminalState.SUCCESS
