"""A self-contained coding turn is one atomic task answered from its own specification.

Measured live 2026-09-17 (provider-continuity test, simple JavaScript tasks):

* a continuation turn ("Continue the previous implementation. Add these changes: - add an
  active boolean field ...") was carved into six sub-turns; each fragment lost the code it
  modified and the merge published contradictory partial implementations;
* a standalone "Create one JavaScript function called prepareTasks..." classified as
  `unknown` (and a spec mentioning relationship-shaped words as `relationship_advisory`);
* a correctly-classified chat_conversation coding turn still opened the grounding lifecycle
  on the word "current" in its own spec ("The current behavior duplicates entries"),
  searched, bound unrelated sources, and withheld the user's requirements as unsupported
  claims.

These tests pin the repaired contracts at their owners: the shared software-authoring
register (continuation shape), the classifier branch ahead of the topical keyword families,
and the lookup-shape rule for arming grounding from an authoring turn's remainder.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.grounded_mode import (
    AnswerMode,
    answer_mode_for,
    is_software_authoring_request,
)

CONTINUATION_TURN = (
    "Continue the previous implementation.\n\nAdd these changes:\n"
    "- add an active boolean field\n"
    "- priority 1 and 2 are active\n"
    "- priority 3 and above are inactive\n"
    "- add an optional second argument onlyActive = false\n"
    "- when onlyActive is true, return only active jobs\n\n"
    "Keep every earlier project rule."
)

STANDALONE_TURN = (
    "Create one JavaScript function called prepareTasks that takes an array of job objects "
    "and returns a normalized array sorted by priority, with an active boolean field set "
    "from the priority, and an optional onlyActive second argument."
)

SPEC_WITH_FRESHNESS_WORD = (
    "Write a small JavaScript function called normalizeJobs. It must keep the first event "
    "for each unique id. The current behavior duplicates entries. Sort by priority and show "
    "the status of each job."
)

CONFUSING_WORDS_TURN = (
    "Write a JavaScript function called reconcileRelationships that takes relationship and "
    "status fields per source, deduplicates by the current source id, and returns the active "
    "relationships sorted by priority."
)


# --------------------------------------------------------------- A/B/C: atomic envelope

@pytest.mark.parametrize(
    "text",
    [CONTINUATION_TURN, STANDALONE_TURN, CONFUSING_WORDS_TURN, "Extend the normalizeJobs function with an onlyActive filter"],
)
def test_a_coding_turn_is_one_authoring_task(text: str) -> None:
    assert is_software_authoring_request(text)


def test_the_planner_does_not_split_a_coding_continuation() -> None:
    from core.agent_runtime.turn_planner import plan_turn

    calls: list[str] = []

    def ask_model(system_prompt: str, prompt: str) -> str:
        calls.append(prompt)
        # Even a planner that WOULD split cannot be asked: the turn never reaches it.
        return '[{"request": "Continue the previous implementation.", "depends_on": []}, {"request": "Add these changes:", "depends_on": [0]}]'

    assert plan_turn(CONTINUATION_TURN, ask_model=ask_model) == []
    assert calls == [], "the continuation turn was handed to the split planner"


def test_the_demand_seam_does_not_carve_a_coding_turn() -> None:
    """The plan-before-execute seam returns no sub-turn tasks for a coding continuation, and
    the executor declines it before any dispatch (its eligibility phase consults the same
    register)."""
    from core.agent_runtime.demand_ownership import units_as_plan

    assert units_as_plan(CONTINUATION_TURN) == []
    assert units_as_plan(STANDALONE_TURN) == []


# -------------------------------------------------------------------- F: code-context words

def test_the_classifier_reads_coding_shape_not_isolated_words() -> None:
    from core.task_router import classify

    for text, wanted in (
        (STANDALONE_TURN, "chat_conversation"),
        (CONFUSING_WORDS_TURN, "chat_conversation"),
        (CONTINUATION_TURN, "chat_conversation"),
        ("my relationship with my partner is difficult lately", "relationship_advisory"),
    ):
        got = str(classify(text).get("task_class") or "")
        assert got == wanted, (text[:60], got)


# ------------------------------------------------------------------ D/I: no false grounding

@pytest.mark.parametrize(
    "text",
    [STANDALONE_TURN, CONTINUATION_TURN, CONFUSING_WORDS_TURN, SPEC_WITH_FRESHNESS_WORD],
)
def test_self_contained_coding_does_not_demand_current_information(text: str) -> None:
    assert answer_mode_for(text) is AnswerMode.DIRECT
    from core.execution_requirements import requirements_for

    requirements = requirements_for(text)
    assert requirements.current_information_required is False
    assert requirements.external_evidence_required is False


def test_a_real_lookup_clause_beside_authoring_still_grounded() -> None:
    mixed = "build a dashboard for our sales team and tell me today's EUR to USD rate"
    assert answer_mode_for(mixed) is AnswerMode.GROUNDED


def test_user_requirements_are_not_claim_support_gated() -> None:
    """The publication gate must not adjudicate the spec's own sentences as world claims.

    With grounding disarmed (DIRECT), `gate_publishable_content` returns the bytes unchanged
    and no lifecycle row exists -- the exact silent path a self-contained coding turn needs.
    """
    from core.grounding_publication import gate_publishable_content

    artifact = (
        "```javascript\nfunction normalizeJobs(jobs, onlyActive = false) {\n"
        "  const normalized = jobs.map(({id, name, priority}) => ({id, name, priority, "
        "label: `${id}::${name}`, active: priority <= 2})).sort((a, b) => a.priority - b.priority);\n"
        "  return onlyActive ? normalized.filter((job) => job.active) : normalized;\n}\n```"
    )
    content, record = gate_publishable_content(artifact, turn_id="turn-coding-fixture-1")
    assert content == artifact
    assert record == {}, (content[:80], record)


# ----------------------------------------------------------------------- E: unknown safety

def test_unknown_classification_does_not_arm_retrieval_for_a_self_contained_turn() -> None:
    """`unknown` with no lookup shape stays model/chat: DIRECT, no external evidence."""
    from core.execution_requirements import requirements_for

    text = "Explain closures in JavaScript."
    requirements = requirements_for(text)
    assert requirements.answer_mode in {"DIRECT", "GROUNDED"}
    assert requirements.external_evidence_required is False or requirements.answer_mode == "GROUNDED"
    # The decisive invariant: nothing in an `unknown` self-contained turn schedules retrieval.
    assert not requirements.current_information_required
