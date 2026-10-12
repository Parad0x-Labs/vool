from __future__ import annotations

from pathlib import Path

import pytest

from core.context_namespace import ensure_chat_namespace, set_chat_namespace_state
from core.context_retrieval import SEMANTIC_MEMORY_AGENT_ID
from core.context_scope import ContextAccessPolicy
from core.human_input_adapter import HumanInputInterpretation
from core.identity_manager import load_active_persona
from core.internal_message_schema import is_turn_directives_message
from core.memory.revocation import initialize_memory_home
from core.memory_first_router import MemoryFirstRouter
from core.memory_recall_intent import revoked_memory_recall_clauses
from core.persistent_memory import maybe_handle_memory_command, search_relevant_memory
from core.prompt_budget import fit_messages_to_context_window
from core.runtime_paths import active_vool_home, configure_runtime_home
from core.task_router import create_task_record
from core.vool_memory import VoolMemory
from core.web.api.runtime import RuntimeServices, _memory_recall_response
from tests.test_prompt_assembly_profiles import _synthetic_context_result


@pytest.fixture(autouse=True)
def isolated_memory_home(tmp_path, monkeypatch):
    home = tmp_path / "memory-home"
    monkeypatch.setenv("VOOL_HOME", str(home))
    configure_runtime_home(home)
    initialize_memory_home(home)
    yield home
    configure_runtime_home(None)


@pytest.mark.parametrize(
    "question",
    ["Give a concise explanation of how a lighthouse works.", "Explain photosynthesis in exactly 2 sentences."],
)
def test_real_request_builder_delivers_split_protected_guidance(question):
    task = create_task_record(question)
    interpretation = HumanInputInterpretation(
        raw_text=question, normalized_text=question, reconstructed_text=question,
        intent_mode="request", topic_hints=[], reference_targets=[],
        understanding_confidence=0.95, quality_flags=[],
    )
    request = MemoryFirstRouter()._build_request(
        task=task, classification={"task_class": "unknown", "confidence_hint": 0.95, "risk_flags": []},
        interpretation=interpretation, context_result=_synthetic_context_result(),
        persona=load_active_persona("default"), output_mode="plain_text",
        task_kind="normalization_assist", surface="openclaw",
        source_context={"surface": "openclaw", "platform": "api"},
    )
    primary = request.messages[0]["content"]
    assert request.system_prompt.startswith(primary)
    assert request.system_prompt != primary
    additions = request.system_prompt[len(primary):].strip()
    assert any(is_turn_directives_message(message) and additions in message["content"]
               for message in request.messages)
    result = fit_messages_to_context_window(
        request.messages, num_ctx=24576, output_reserve_tokens=476,
        protected_system_prompt=request.system_prompt,
    )
    assert result.messages == request.messages
    assert result.telemetry["system_prompt_preserved"] is True
    assert result.telemetry["status"] == "fit"


def _context(chat="erased-chat", **extra):
    return {
        "surface": "openclaw", "platform": "api", "_owner_local": True,
        "chat_id": chat, "runtime_home": str(active_vool_home()), **extra,
    }


def _recall(question, chat="erased-chat", **extra):
    return _memory_recall_response(
        RuntimeServices(runtime_home=str(active_vool_home())),
        user_text=question, source_context=_context(chat, **extra),
    )


def _remember_and_forget(fact, subject, chat="erased-chat"):
    ensure_chat_namespace(chat, grant_confirmed_profile=True)
    handled, answer = maybe_handle_memory_command(
        f"Remember that {fact}", session_id=chat, source_context=_context(chat),
    )
    assert handled and "Locked in" in answer
    policy = ContextAccessPolicy.for_request(session_id=chat, source_context=_context(chat))
    hits = search_relevant_memory(subject, access_policy=policy, limit=10)
    assert any(fact.rstrip(".") in hit["text"] for hit in hits)
    handled, answer = maybe_handle_memory_command(
        f"Forget {subject}.", session_id=chat, source_context=_context(chat),
    )
    assert handled and answer.startswith("Forget applied"), answer
    assert not search_relevant_memory(subject, access_policy=policy, limit=10)


@pytest.mark.parametrize(
    ("fact", "subject", "questions"),
    [
        ("Maya's birthday is March 14.", "Maya's birthday",
         ("When is Maya's birthday?", "What do you know about Maya's birthday?")),
        ("The sailboat locker code is QRS-6725.", "the sailboat locker code",
         ("What is the sailboat locker code?", "Do you remember the sailboat locker code?")),
    ],
)
def test_completed_scoped_forget_answers_no_record_from_real_memory_authority(fact, subject, questions):
    for question in questions:
        assert _recall(question) is None
    _remember_and_forget(fact, subject)
    for question in questions:
        result = _recall(question)
        assert result is not None
        assert "no record" in result["response"].lower()
        assert "which" not in result["response"].lower()
        assert "March 14" not in result["response"]
        assert "QRS-6725" not in result["response"]
        assert result["route_reason"] == "completed_scoped_erasure"
        assert result["model_calls"] == result["web_calls"] == 0


def test_erasure_recall_respects_chat_owner_group_and_lifecycle_boundaries():
    _remember_and_forget("The harbor access code is ZX-8291.", "the harbor access code")
    question = "What is the harbor access code?"
    assert _recall(question, chat="foreign-chat") is None
    assert _recall(question, _owner_local=False) is None
    assert _recall(question, is_group=True) is None
    assert _recall("When was Isaac Newton born?") is None
    assert _recall("Print the harbor access code as a heading.") is None
    set_chat_namespace_state("erased-chat", "archived")
    assert _recall(question) is None


def test_pending_or_unreadable_erasure_never_claims_no_record():
    _remember_and_forget("The museum delivery date is April 22.", "the museum delivery date")
    question = "When is the museum delivery date?"
    assert _recall(question) is not None
    with VoolMemory(runtime_home=str(active_vool_home()), agent_id=SEMANTIC_MEMORY_AGENT_ID) as memory:
        operation = memory.begin_erasure("erased-chat", "the museum delivery date")
        assert _recall(question) is None
        memory.complete_erasure(operation)
    assert _recall(question) is not None
    authority = Path(active_vool_home()) / ".forget-authority" / "ledger.json"
    assert authority.is_file()
    authority.write_text("{malformed", encoding="utf-8")
    assert _recall(question) is None


def test_revoked_question_is_only_its_slice_and_does_not_claim_commands():
    tokens = ("the workshop cabinet code",)
    question = "What is the workshop cabinet code?"
    assert revoked_memory_recall_clauses(question, revoked_tokens=tokens) == (question,)
    mixed = f"{question} Also explain why tides rise."
    assert revoked_memory_recall_clauses(mixed, revoked_tokens=tokens) == (question,)
    assert revoked_memory_recall_clauses(
        'Write "What is the workshop cabinet code?" into a heading.', revoked_tokens=tokens
    ) == ()
    assert revoked_memory_recall_clauses(
        "What is the workshop cabinet code and convert 100 EUR to USD?", revoked_tokens=tokens
    ) == ()


@pytest.mark.parametrize(
    ("subject", "fact", "private_question", "public_questions"),
    [
        ("Maya", "Maya's birthday is March 14.", "When is Maya's birthday?",
         ("When was Maya Angelou born?", "When is Maya Angelou's birthday?", "What is Mayan civilization?")),
        ("Alex Rivera", "Alex Rivera's locker code is YTR-4526.", "What is Alex Rivera's locker code?",
         ("What is Alex Riverand's locker code?", "What is Alex Rivera Stone's locker code?")),
    ],
)
def test_whole_subject_forget_uses_complete_entity_identity(subject, fact, private_question, public_questions):
    _remember_and_forget(fact, subject)
    result = _recall(private_question)
    assert result is not None and "no record" in result["response"].lower()
    for question in public_questions:
        assert _recall(question) is None, question


def test_full_relation_forget_does_not_claim_a_different_relation_or_entity():
    _remember_and_forget("Maya's birthday is March 14.", "Maya's birthday")
    assert _recall("When is Maya's birthday?") is not None
    assert _recall("When is the birthday of Maya?") is not None
    assert _recall("When is Maya's birthday party?") is None
    assert _recall("What is Maya's birthday cake recipe?") is None
    assert _recall("When is Maya Angelou's birthday?") is None
    assert _recall("What is the meaning of Maya's birthday in astrology?") is None


@pytest.mark.parametrize("sibling", ["Also explain photosynthesis.", "Also convert 100 EUR to USD at 1.10."])
def test_api_erased_memory_claim_preserves_every_sibling_demand(sibling):
    """Pin the real API handoff; stopping at the agent is instrumentation, not a model reply."""
    from core.agent_runtime.answer_coverage import FAMILY_MEMORY_RECALL, demand_units, turn_slices
    from core.web.api.runtime import run_agent

    _remember_and_forget("Maya's birthday is March 14.", "Maya's birthday")
    question = "When is Maya's birthday?"
    message = f"{question} {sibling}"
    captured = []

    class AgentBoundaryReached(RuntimeError):
        pass

    class RecordAgentBoundary:
        def run_once(self, user_text, *, session_id_override, source_context):
            captured.append((user_text, source_context))
            raise AgentBoundaryReached

    with pytest.raises(AgentBoundaryReached):
        run_agent(
            RuntimeServices(agent=RecordAgentBoundary(), runtime_home=str(active_vool_home())),
            message, session_id="erased-chat", source_context=_context(),
            workspace_root_provider=lambda: str(active_vool_home() / "workspace"),
        )
    assert len(captured) == 1
    assert captured[0][0] == message
    context = captured[0][1]
    answers = context["turn_answer_coverage"]["answers"]
    answer = next(item for item in answers if item["family"] == FAMILY_MEMORY_RECALL)
    memory_slice = next(item.slice_id for item in turn_slices(message) if item.text == question)
    assert answer["consumed"] == [memory_slice]
    units = demand_units(message)
    assert len(units) == 2
    assert answer["consumed_units"] == [unit.unit_id for unit in units if unit.slice_id == memory_slice]
    assert len(answer["consumed_units"]) == 1
    observations = context["runtime_tool_observations"]
    observation = next(item for item in observations if item["intent"] == f"turn.slice_answer.{FAMILY_MEMORY_RECALL}")
    assert observation["slice_text"] == question
    assert "no record" in observation["response_preview"].lower()
