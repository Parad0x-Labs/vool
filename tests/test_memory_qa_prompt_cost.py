"""A memory-QA turn carries only the bootstrap that bears on answering from memory.

A memory-QA turn is a first-person recall question answered by the minimal chat profile with a
retrieved memory capsule in its transcript. On such a turn four bootstrap items are not about the
question: the user/assistant name anchors (unless the turn asks about a name), the session vault
policy, the tool doctrine the minimal profile already withholds, and a task summary that repeats
the user message verbatim. The scope sentence "do not restate ... from earlier turns" is narrowed
to earlier chat turns so it does not read as a ban on the retrieved memory itself.

A single-clause recall question needs no clause-decomposition call; a message that may hold
several requests still reaches the planner.

Every sentence below is synthetic.
"""
from __future__ import annotations

import pytest

import core.prompt_normalizer as prompt_normalizer
from core.human_input_adapter import HumanInputInterpretation
from core.identity_manager import load_active_persona
from core.prompt_normalizer import normalize_prompt
from core.task_router import create_task_record
from core.tiered_context_loader import TieredContextLoader
from core.context_namespace import ensure_chat_namespace

_CAPSULE = (
    "<retrieved_context>\n"
    "Distilled local facts. Answer from these exact facts only.\n"
    "- user said: I repainted the garden fence on Saturday. (stated: 2024-05-04)\n"
    "- user said: I hosted my book club on Wednesday. (stated: 2024-05-08)\n"
    "</retrieved_context>"
)

_RECALL_QUESTION = (
    "How many days passed between the day I repainted my garden fence and the day I hosted my book club?"
)

_UNSCOPED_SENTENCE = "Answer only what was asked; do not restate unrelated facts or identifiers from earlier turns."


def _fake_transcript_with_capsule(**_kwargs):
    return [{"role": "system", "content": _CAPSULE}], "canonical_runtime_transcript"


def _fake_transcript_without_capsule(**_kwargs):
    return [], "none"


def _build(prompt: str, *, task_class: str = "chat_conversation"):
    persona = load_active_persona("default")
    task = create_task_record(prompt)
    interpretation = HumanInputInterpretation(
        raw_text=prompt,
        normalized_text=prompt,
        reconstructed_text=prompt,
        intent_mode="request",
        topic_hints=[],
        reference_targets=[],
        understanding_confidence=0.84,
        quality_flags=[],
    )
    classification = {"task_class": task_class, "risk_flags": [], "confidence_hint": 0.84}
    session_id = f"ctx-{task.task_id}"
    ensure_chat_namespace(session_id, grant_current_receipts=False)
    context_result = TieredContextLoader().load(
        task=task,
        classification=classification,
        interpretation=interpretation,
        persona=persona,
        session_id=session_id,
        total_context_budget=5000,
    )
    request = normalize_prompt(
        task=task,
        classification=classification,
        interpretation=interpretation,
        context_result=context_result,
        persona=persona,
        output_mode="plain_text",
        task_kind="normalization_assist",
        trace_id=task.task_id,
        surface="openclaw",
        source_context={
            "surface": "openclaw",
            "platform": "openclaw",
            "runtime_session_id": session_id,
        },
    )
    return request, context_result


def _bootstrap_message(request) -> str:
    messages = [message for message in request.messages if message.role == "context"]
    assert len(messages) == 1
    return messages[0].content


def _full_capsule_bootstrap(context_result) -> str:
    return context_result.assembled_context(prompt_profile="chat_capsule")


#: First-person recall questions: the original shape, clean paraphrases, and sloppy typed variants.
_MEMORY_QA_FAMILY = [
    _RECALL_QUESTION,
    "What type of bread did I bake for my neighbours last month?",
    "When did I last get my boiler serviced?",
    "Which museum did I visit first, the maritime one or the toy museum?",
    "How much did I pay for my new running shoes?",
    "Where did we stay on our trip to the lakes?",
    "how many books did i finish in march?",
    "wat did i name my goldfish?",
    "how many days betwen the day i fixed my sink and the day i saw the dentist?",
    "WHO DID I GO TO THE CONCERT WITH?",
    "what was my score in the pub quiz ?",
]


@pytest.mark.parametrize("prompt", _MEMORY_QA_FAMILY)
def test_memory_qa_turn_drops_bootstrap_that_does_not_bear_on_the_question(monkeypatch, prompt: str) -> None:
    monkeypatch.setattr(prompt_normalizer, "canonical_runtime_transcript", _fake_transcript_with_capsule)
    request, context_result = _build(prompt)

    assert request.metadata["system_prompt_profile"] == "chat_minimal"
    full = _full_capsule_bootstrap(context_result)
    # The parent assembly carried every one of these on this turn.
    assert "The USER's name" in full
    assert "Session sharing" in full
    assert "OpenClaw tool doctrine" in full
    assert "Summary:" in full

    delivered = _bootstrap_message(request)
    assert "The USER's name" not in delivered
    assert "YOUR own name" not in delivered
    assert "Session sharing" not in delivered
    assert "OpenClaw tool doctrine" not in delivered
    assert "Summary:" not in delivered
    # What stays: identity, safety, conversation policy, the task class itself.
    assert "Agent identity" in delivered
    assert "Execution default" in delivered
    assert "Conversation policy" in delivered
    assert "Task class: chat_conversation" in delivered
    assert len(delivered) < len(full)


def test_memory_qa_scope_sentence_does_not_cover_retrieved_memory(monkeypatch) -> None:
    monkeypatch.setattr(prompt_normalizer, "canonical_runtime_transcript", _fake_transcript_with_capsule)
    request, _ = _build(_RECALL_QUESTION)

    system = request.system_prompt()
    assert _UNSCOPED_SENTENCE not in system
    assert "from earlier chat turns" in system
    assert "retrieved memory is evidence" in system


@pytest.mark.parametrize(
    "prompt",
    [
        # Advice, not recall: keeps every bootstrap item.
        "Can you suggest a quick dinner I could cook tonight?",
        # A general-knowledge question: not about the speaker's own facts.
        "When was the first transatlantic telegraph cable laid?",
        # Advice about the speaker's own thing (a modal, not recall).
        "Should I repaint my fence this spring?",
        # Instructions over the speaker's own thing (present manner, not recall).
        "How do I reset my router?",
        # Near miss: reads like a duration recall but asks about the world.
        "How many days are there between the solstice and the equinox?",
    ],
)
def test_capsule_turn_that_is_not_memory_qa_keeps_the_full_bootstrap(monkeypatch, prompt: str) -> None:
    monkeypatch.setattr(prompt_normalizer, "canonical_runtime_transcript", _fake_transcript_with_capsule)
    request, context_result = _build(prompt)

    delivered = _bootstrap_message(request)
    assert "The USER's name" in delivered
    assert "Session sharing" in delivered
    assert f"Summary: {prompt}" in delivered
    assert _UNSCOPED_SENTENCE in request.system_prompt()


def test_name_question_keeps_the_name_anchor(monkeypatch) -> None:
    monkeypatch.setattr(prompt_normalizer, "canonical_runtime_transcript", _fake_transcript_with_capsule)
    request, _ = _build("What is my name?")

    delivered = _bootstrap_message(request)
    assert "The USER's name" in delivered
    assert "YOUR own name" in delivered


def test_recall_question_without_a_capsule_is_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(prompt_normalizer, "canonical_runtime_transcript", _fake_transcript_without_capsule)
    request, context_result = _build(_RECALL_QUESTION)

    delivered = _bootstrap_message(request)
    assert "The USER's name" in delivered
    assert f"Summary: {_RECALL_QUESTION}" in delivered
    assert _UNSCOPED_SENTENCE in request.system_prompt()


# ---------------------------------------------------------------- planner call on recall questions


def _recording_model(calls: list[tuple[str, str]]):
    def ask(system: str, prompt: str) -> str:
        calls.append((system, prompt))
        return '[{"request": "x", "operation": "missing_information", "depends_on": []}]'

    return ask


@pytest.mark.parametrize(
    "question",
    [
        _RECALL_QUESTION,
        "Which hobby did I pick up first, the pottery class or the climbing gym?",
        "How much did I spend on my bike repair and my new helmet?",
        "What was the name of my hotel in Lisbon and Porto?",
        "How long had I been running before my first half marathon and the trail race?",
        "how many days betwen the day i fixed my sink and the day i saw the dentist?",
        "what did i pay for my headphones and my charger?",
        "which did i start 1st, the pottery class or the climbing gym?",
        "How much did I spend on my bike repair and my new helmet ?",
        "when did i paint the kitchen, the hallway or the bathroom first?",
    ],
)
def test_single_clause_recall_question_spends_no_planner_call(question: str) -> None:
    from core.agent_runtime.turn_planner import plan_turn
    from core.conductor.planner import plan_conductor_turn

    conductor_calls: list[tuple[str, str]] = []
    assert plan_conductor_turn(question, ask_model=_recording_model(conductor_calls)) is None
    assert conductor_calls == []

    generic_calls: list[tuple[str, str]] = []
    assert plan_turn(question, ask_model=_recording_model(generic_calls)) == []
    assert generic_calls == []


@pytest.mark.parametrize(
    "message",
    [
        # A second interrogative after the joiner.
        "What did I name my cat and what is the weather in Oslo today?",
        # An inverted auxiliary opens a second clause.
        "What was my locker number, and will it rain tomorrow?",
        # An imperative after the joiner.
        "When did I renew my passport and remind me to book the dentist?",
        # Two sentences.
        "Where did I park my car? Convert twenty euros to dollars.",
        # A recall question beside an explicit workspace action.
        "What did I call my draft, and search the workspace files for it?",
        # "also" opening a second request.
        "What did I order at the bakery also book me a table there?",
    ],
)
def test_message_that_may_hold_several_requests_still_reaches_the_planner(message: str) -> None:
    from core.conductor.planner import plan_conductor_turn

    calls: list[tuple[str, str]] = []
    plan_conductor_turn(message, ask_model=_recording_model(calls))
    assert len(calls) == 1


def test_single_clause_recall_predicate_shapes() -> None:
    from core.plain_task_routing import single_clause_recall_question

    assert single_clause_recall_question(_RECALL_QUESTION)
    assert single_clause_recall_question("Which trip did I take first, the one to Porto or the one to Riga?")
    # Not recall.
    assert not single_clause_recall_question("How do I descale an espresso machine and a kettle?")
    assert not single_clause_recall_question("What is the capital of Peru and Chile?")
    assert not single_clause_recall_question("How many days are there between the solstice and the equinox?")
    # Not a question.
    assert not single_clause_recall_question("I repainted my fence and hosted my book club")
    # Several requests.
    assert not single_clause_recall_question("What did I name my cat and what is the weather in Oslo?")
    assert not single_clause_recall_question("What did I buy; what did I return?")
    assert not single_clause_recall_question("What did I buy, and did I return it?")
    assert not single_clause_recall_question("What did I buy and remind me to return it?")
    # A joiner followed by a bare word is not proven to join noun phrases: the planner decides.
    assert not single_clause_recall_question("How much did I spend on groceries and fuel last week?")
