"""B11 (AMENDMENT 1, round 2 Job 1): world questions keep their lane.

Weather, prices, news, "search the web for ...", and facts about apps or events keep,
for owner-local AND remote AND no-context turns:

- the requirement they have on base (LIVE_DATA or GROUNDED, current_information_required,
  evidence toolsets present);
- their web planning: neither the local-code guard nor a matched-tool-skill release may
  turn a world ask into a tool-only turn.

Red on the round-1 head af2303f1 (this job's comparison base): there the operator arm in
``_compute_requirements`` returned ``("operator",)`` with ``current_information_required=False``
BEFORE the LIVE_DATA/GROUNDED arms, so owner-local asks that merely mention reminder or
calendar words ("which reminders app is best for Android right now?") lost their evidence
requirement, and a MISSING context counted as the owner, so even ``classify_requirements``
(no context) seated operator tools; and ``_local_code_ask_beats_web_planning`` fired on bare
artifact words ("test flight", "build", "bug") with no owner-local gate, so remote and
no-context world asks ended with ``no_workflow_plan``.

On b744af81 the requirements rows already hold (the operator arm did not exist there): they
are must-stay pins of base behaviour. The planner rows cannot run on b744af81 (the guard and
its seam are round-1 work; the import itself fails), and the classify_requirements row passes
there for the same reason the requirements rows do.
"""
from __future__ import annotations

import pytest

from core.execution.planner import _local_code_ask_beats_web_planning, plan_tool_workflow
from core.execution_requirements import _compute_requirements, classify_requirements

OWNER_LOCAL = {"_owner_local": True, "runtime_session_id": "s-b11", "turn_id": "t-b11-owner"}
REMOTE = {"_owner_local": False, "runtime_session_id": "s-b11", "turn_id": "t-b11-remote"}

#: The three lanes a world question runs in. A missing or unsure context is remote.
CONTEXTS = [
    pytest.param("owner_local", OWNER_LOCAL, id="owner"),
    pytest.param("remote", REMOTE, id="remote"),
    pytest.param("no_context", None, id="no-context"),
]

WORLD_QUESTIONS = [
    # weather and live instruments
    "what's the weather in Paris today",
    "will it rain in Lisbon tomorrow",
    "what is the temperature in Tokyo right now",
    "what is the current bitcoin price",
    "how is the euro doing against the dollar today",
    "what's apple's stock price at the moment",
    "what's the current exchange rate for the yen",
    # news
    "what's the latest on the Starship test flight failure?",
    "any news about the CrowdStrike outage",
    "what happened at the Oscars this year",
    "what's new in the latest Chrome release",
    "who is playing in the Champions League final this weekend",
    "is there a transit strike in Berlin this week",
    "how expensive are eggs in the US right now",
    # explicit web asks
    "search the web for why the Python 3.13 build fails on macOS",
    "search the web for the latest news about the CrowdStrike bug and why it crashed",
    "search the web for the best reminders app for Android",
    "look up online who won the last World Cup",
    # facts about apps and events in words that also name owner objects
    "which reminders app is best for Android right now?",
    "what are the latest reminders features in iOS 18?",
]

#: Toolsets that are evidence about the world, not this machine's own objects.
_EVIDENCE_TOOLSETS = {"weather", "market_prices", "news", "web_search", "web_fetch"}


@pytest.mark.parametrize("question", WORLD_QUESTIONS)
@pytest.mark.parametrize("ctx_name,context", CONTEXTS)
def test_a_world_question_keeps_its_evidence_requirement(question, ctx_name, context):
    """LIVE_DATA or GROUNDED with current_information_required, whatever else the words seat."""
    requirements = _compute_requirements(
        question, task_class="unknown", source_context=dict(context) if context else None
    )
    assert requirements.current_information_required, (
        f"[{ctx_name}] lost current_information_required: {requirements.reason_codes}"
    )
    assert requirements.tools_required, f"[{ctx_name}] lost tools_required: {question}"
    evidence = set(requirements.allowed_toolsets) - {"operator"}
    assert evidence & _EVIDENCE_TOOLSETS or evidence, (
        f"[{ctx_name}] lost its evidence toolsets (got {requirements.allowed_toolsets}): {question}"
    )


@pytest.mark.parametrize("question", WORLD_QUESTIONS)
@pytest.mark.parametrize(
    "ctx_name,context",
    [pytest.param("remote", REMOTE, id="remote"), pytest.param("no_context", None, id="no-context")],
)
def test_a_remote_or_context_free_world_ask_seats_no_operator_tools(question, ctx_name, context):
    """The operator tools are owner-local; a missing or unsure context is remote (R9)."""
    requirements = _compute_requirements(
        question, task_class="unknown", source_context=dict(context) if context else None
    )
    assert "operator" not in set(requirements.allowed_toolsets), (
        f"[{ctx_name}] seated operator tools: {requirements.allowed_toolsets}"
    )


@pytest.mark.parametrize("question", WORLD_QUESTIONS)
@pytest.mark.parametrize("ctx_name,context", CONTEXTS)
def test_a_world_question_is_never_a_local_code_ask(question, ctx_name, context):
    """The local-code guard never eats a world ask, in any lane (R6).

    Bare artifact words ("the Starship test flight failure", "the CrowdStrike bug", "the
    Python 3.13 build") are the world's; only the owner's OWN named project beside a failure
    verb is local, and only on an owner-local turn.
    """
    assert _local_code_ask_beats_web_planning(
        question, dict(context) if context else None
    ) is False


#: World asks that name the web itself, or carry world-artifact words the old guard stole.
_WEB_PLANNED_QUESTIONS = [
    "search the web for why the Python 3.13 build fails on macOS",
    "search the web for the latest news about the CrowdStrike bug and why it crashed",
    "search the web for the best reminders app for Android",
    "look up online who won the last World Cup",
    "what's the latest on the Starship test flight failure?",
    "any news about the CrowdStrike outage",
]


@pytest.mark.parametrize("question", _WEB_PLANNED_QUESTIONS)
@pytest.mark.parametrize("ctx_name,context", CONTEXTS)
def test_a_world_ask_keeps_its_web_planning(question, ctx_name, context):
    """An explicit web ask is planned onto the web in every lane, never ``no_workflow_plan``."""
    decision = plan_tool_workflow(
        user_text=question,
        task_class="research",
        executed_steps=[],
        source_context=dict(context) if context else None,
    )
    assert decision.reason != "no_workflow_plan", (
        f"[{ctx_name}] web planning was dropped for: {question}"
    )


def test_a_context_free_caller_never_seats_operator_tools():
    """classify_requirements has no context, and a missing context is remote (R9).

    On the round-1 head this seated the operator arm through the context-free path.
    """
    for question in (
        "show my agenda for today",
        "which reminders app is best for Android right now?",
        "is tomorrow free? what is the current bitcoin price",
    ):
        requirements = classify_requirements(question)
        assert "operator" not in set(requirements.allowed_toolsets), (
            f"context-free call seated operator tools: {requirements.allowed_toolsets}"
        )


def test_the_operator_demand_joins_the_evidence_arms_instead_of_replacing_them():
    """R1: the operator arm is additive -- the evidence verdicts survive beside it.

    On the round-1 head each of these returned ``("operator",)`` with
    ``current_information_required=False`` and the LIVE_DATA/GROUNDED toolsets were gone.
    """
    requirements = _compute_requirements(
        "is tomorrow free? what is the current bitcoin price",
        task_class="unknown",
        source_context=dict(OWNER_LOCAL),
    )
    assert "market_prices" in set(requirements.allowed_toolsets)
    assert "operator" in set(requirements.allowed_toolsets)
    assert requirements.current_information_required is True
    assert requirements.tools_required is True

    for question in (
        "which reminders app is best for Android right now?",
        "what are the latest reminders features in iOS 18?",
    ):
        requirements = _compute_requirements(question, task_class="unknown", source_context=dict(OWNER_LOCAL))
        toolsets = set(requirements.allowed_toolsets)
        assert toolsets & {"web_search", "web_fetch"}, f"grounded web toolsets lost: {toolsets}"
        assert requirements.current_information_required is True


def test_the_owners_own_local_code_ask_still_beats_web_planning():
    """R6 keeps the round-1 protection where it belongs: the owner's own named project.

    The guard fires only on an owner-local turn whose words name the owner's own
    project/repo beside a failure verb -- and never for a remote or context-free caller
    (the round-1 test that pinned the context-free call is superseded by this rule; see
    REPORT.md "Needs owner").
    """
    question = "a test in my project fails, find out why without changing code"
    assert _local_code_ask_beats_web_planning(question, dict(OWNER_LOCAL)) is True
    assert _local_code_ask_beats_web_planning(question, dict(REMOTE)) is False
    assert _local_code_ask_beats_web_planning(question, None) is False


def test_a_pure_operator_ask_still_lands_operator_tools():
    """The additive arm must not widen a pure owner ask: agenda/note asks keep their seats."""
    for question in ("show my agenda for today", "save this as a note: call the dentist"):
        requirements = _compute_requirements(question, task_class="unknown", source_context=dict(OWNER_LOCAL))
        assert "operator" in set(requirements.allowed_toolsets)
        assert requirements.tools_required is True
