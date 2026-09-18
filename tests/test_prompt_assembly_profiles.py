from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

import core.prompt_normalizer as prompt_normalizer
from core.cold_context_gate import ColdContextDecision
from core.context_namespace import ensure_chat_namespace
from core.human_input_adapter import HumanInputInterpretation, adapt_user_input
from core.identity_manager import load_active_persona
from core.persistent_memory import append_conversation_event
from core.prompt_assembly_report import ContextItem, PromptAssemblyReport
from core.prompt_normalizer import normalize_prompt
from core.runtime_continuity import (
    create_runtime_checkpoint,
    store_tool_receipt,
    update_runtime_checkpoint,
)
from core.task_router import create_task_record
from core.tiered_context_loader import TieredContextLoader, TieredContextResult


def _build_request(
    prompt: str,
    *,
    task_class: str,
    task_kind: str,
    output_mode: str,
    source_context: dict | None = None,
    context_result_override: TieredContextResult | None = None,
):
    persona = load_active_persona("default")
    task = create_task_record(prompt)
    interpretation = HumanInputInterpretation(
        raw_text=task.task_summary,
        normalized_text=task.task_summary,
        reconstructed_text=task.task_summary,
        intent_mode="request",
        topic_hints=[],
        reference_targets=[],
        understanding_confidence=0.84,
        quality_flags=[],
    )
    classification = {
        "task_class": task_class,
        "risk_flags": [],
        "confidence_hint": 0.84,
    }
    loader = TieredContextLoader()
    session_id = f"ctx-{task.task_id}"
    ensure_chat_namespace(
        session_id,
        grant_current_receipts=False,
    )
    context_result = context_result_override or loader.load(
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
        output_mode=output_mode,
        task_kind=task_kind,
        trace_id=task.task_id,
        surface="openclaw",
        source_context=source_context or {"surface": "openclaw", "platform": "openclaw"},
    )
    return request, context_result


def test_chat_prompt_makes_stipulated_facts_authoritative_across_followups() -> None:
    request, _context_result = _build_request(
        "Assume it is 2040. President Buster is selling me a watch in Washington, DC.",
        task_class="research",
        task_kind="summarization",
        output_mode="plain_text",
    )

    system = request.as_openai_messages()[0]["content"]
    assert "User-stipulated assumptions" in system
    assert "without searching" in system
    assert "replacing named people, roles" in system
    assert "Keep those stipulated facts active in follow-up answers" in system
    assert "not a current indexed fact" in system
    assert "do not blame Local Only" in system


def test_selected_model_and_bound_workspace_are_in_authoritative_prompt_context() -> None:
    request, _context_result = _build_request(
        "Which model are you using, and audit this folder?",
        task_class="debugging",
        task_kind="normalization_assist",
        output_mode="plain_text",
        source_context={
            "surface": "openclaw",
            "platform": "openclaw",
            "requested_model": "nvidia/nemotron-3-ultra-550b-a55b:free",
            "workspace": "/Users/example/private-project",
            "workspace_binding": "project",
            "project_id": "web0",
        },
    )

    system_prompt = request.system_prompt()
    assert "explicitly selected model `nvidia/nemotron-3-ultra-550b-a55b:free`" in system_prompt
    assert "This chat is bound to project `web0`" in system_prompt
    assert "do not search the whole machine" in system_prompt
    assert "/Users/example/private-project" not in system_prompt
    truth = dict(request.metadata.get("chat_truth_prompt") or {})
    assert truth["requested_model"] == "nvidia/nemotron-3-ultra-550b-a55b:free"
    assert truth["workspace_binding"] == "project"


def _session_id(label: str) -> str:
    return f"openclaw:{label}:{uuid.uuid4().hex}"


def _synthetic_context_result() -> TieredContextResult:
    return TieredContextResult(
        bootstrap_items=[
            ContextItem(
                item_id="bootstrap-persona",
                layer="bootstrap",
                source_type="persona",
                title="Agent identity",
                content="Persona: VOOL. Tone: direct.",
            ),
            ContextItem(
                item_id="bootstrap-safety",
                layer="bootstrap",
                source_type="policy",
                title="Safety mode",
                content="Execution default: advice_only.",
                metadata={"exclude_from_chat_minimal_system_prompt": True},
            ),
            ContextItem(
                item_id="bootstrap-conversation-safety",
                layer="bootstrap",
                source_type="conversation_policy",
                title="Conversation policy",
                content="Sensitive conversation is allowed when the user is asking for discussion only.",
            ),
            ContextItem(
                item_id="bootstrap-conversation-preferences",
                layer="bootstrap",
                source_type="user_preferences",
                title="Conversation preferences",
                content="humor=20/100; boundaries=user_defined; profanity=40/100",
            ),
            ContextItem(
                item_id="bootstrap-session-memory-policy",
                layer="bootstrap",
                source_type="session_policy",
                title="Session memory policy",
                content="Session sharing: PRIVATE VAULT.",
                metadata={"exclude_from_chat_minimal_system_prompt": True},
            ),
            ContextItem(
                item_id="bootstrap-owner-privacy-pact",
                layer="bootstrap",
                source_type="privacy_pact",
                title="Privacy pact",
                content="Privacy pact: local only",
                metadata={"exclude_from_chat_minimal_system_prompt": True},
            ),
            ContextItem(
                item_id="bootstrap-execution-preferences",
                layer="bootstrap",
                source_type="execution_preferences",
                title="Execution preferences",
                content="autonomy=hands_off; show_workflow=off",
                metadata={"exclude_from_chat_minimal_system_prompt": True},
            ),
            ContextItem(
                item_id="bootstrap-active-mission",
                layer="bootstrap",
                source_type="active_mission",
                title="Active mission",
                content="Stale mission: cap 0.05 SOL; domain alice.null; wallet prefix F6Fr2.",
            ),
        ],
        relevant_items=[
            ContextItem(
                item_id="memory-1",
                layer="relevant",
                source_type="runtime_memory",
                title="Persistent memory",
                content="The user prefers direct, useful answers.",
            ),
            ContextItem(
                item_id="doctrine-1",
                layer="relevant",
                source_type="operating_doctrine",
                title="OpenClaw tool doctrine",
                content="Never claim you searched the web without evidence.",
                metadata={"exclude_from_chat_minimal_system_prompt": True},
            ),
        ],
        cold_items=[],
        local_candidates=[],
        swarm_metadata=[],
        report=PromptAssemblyReport(
            task_id="task-1",
            trace_id="trace-1",
            total_context_budget=1000,
            bootstrap_budget=300,
            relevant_budget=500,
            cold_budget=200,
            retrieval_confidence="medium",
        ),
        retrieval_confidence_score=0.6,
        cold_decision=ColdContextDecision(False, "cold_context_not_justified"),
    )


@pytest.mark.parametrize(
    ("task_class", "task_kind", "prompt"),
    [
        ("unknown", "normalization_assist", "Do you think boredom is useful?"),
        ("business_advisory", "normalization_assist", "How should I position my B2B analytics product?"),
        ("relationship_advisory", "normalization_assist", "My partner and I keep having the same argument. What should I do?"),
        ("research", "summarization", "Latest Telegram Bot API updates?"),
        ("integration_orchestration", "normalization_assist", "Show me the open Hive tasks."),
    ],
)
def test_plain_text_chat_system_prompt_stays_minimal(
    task_class: str,
    task_kind: str,
    prompt: str,
) -> None:
    request, context_result = _build_request(
        prompt,
        task_class=task_class,
        task_kind=task_kind,
        output_mode="plain_text",
    )

    system_prompt = request.system_prompt().lower()
    chat_truth = dict(request.metadata.get("chat_truth_prompt") or {})
    context_messages = [message for message in request.messages if message.role == "context"]

    assert request.metadata["system_prompt_profile"] == "chat_minimal"
    assert chat_truth["system_prompt_profile"] == "chat_minimal"
    assert chat_truth["speech_safety_mode"] == "conversation_freer"
    assert chat_truth["tooling_guidance_enabled"] is False
    assert chat_truth["execution_safety_guidance_enabled"] is False
    assert chat_truth["context_delivery"] == "context_message"
    assert "be truthful about uncertainty, freshness, and capabilities" in system_prompt
    assert "handle sensitive, intimate, profane, or controversial discussion directly" in system_prompt
    assert "do not treat discussion-only prompts as permission to use tools" in system_prompt
    assert "do not ask for micro-confirmation" not in system_prompt
    assert "these action rules apply only when using tools" not in system_prompt
    assert "workspace file listing" not in system_prompt
    assert "sandboxed local command execution" not in system_prompt
    assert "email and inbox tooling are not guaranteed" not in system_prompt
    assert "never claim you searched the web" not in system_prompt
    assert "execution default:" not in system_prompt
    assert "openclaw tool doctrine" not in system_prompt
    assert "relevant context from your memory" not in system_prompt
    assert len(context_messages) == 1
    assert context_messages[0].content.startswith("Relevant context and evidence:\n")
    lowered_context = context_messages[0].content.lower()
    assert "session sharing:" not in lowered_context
    assert "privacy pact:" not in lowered_context
    assert "autonomy=" not in lowered_context
    assert context_result.assembled_context(prompt_profile="chat_minimal")[:200] in context_messages[0].content


def test_canonical_project_context_reaches_the_provider_before_generic_context() -> None:
    context_result = _synthetic_context_result()
    context_result.bootstrap_items.append(
        ContextItem(
            item_id="canonical-project-1",
            layer="bootstrap",
            source_type="canonical_document",
            title="VOOL (README.md)",
            content="VOOL is the local-first personal agent built by Parad0x Labs.",
            metadata={"source_class": "canonical"},
        )
    )
    request, _context_result = _build_request(
        "A teammate asks what VOOL is and who builds it. Answer in one short sentence.",
        task_class="unknown",
        task_kind="normalization_assist",
        output_mode="plain_text",
        source_context={
            "surface": "api",
            "platform": "api",
            "canonical_grounding_required": True,
        },
        context_result_override=context_result,
    )

    context_messages = [message for message in request.messages if message.role == "context"]

    assert len(context_messages) == 1
    assert "Authoritative canonical project context" in context_messages[0].content
    assert "VOOL is the local-first personal agent built by Parad0x Labs" in context_messages[0].content
    assert context_messages[0].metadata["canonical_grounding"] is True
    assert "Canonical project context in this request is authoritative" in request.system_prompt()


@pytest.mark.parametrize(
    ("output_mode", "task_kind"),
    [
        ("action_plan", "action_plan"),
        ("tool_intent", "tool_intent"),
    ],
)
def test_structured_chat_system_prompt_keeps_operational_doctrine(
    output_mode: str,
    task_kind: str,
) -> None:
    request, _context_result = _build_request(
        "Find the latest OpenClaw release notes and tell me the next safe step.",
        task_class="system_design",
        task_kind=task_kind,
        output_mode=output_mode,
    )

    system_prompt = request.system_prompt().lower()
    chat_truth = dict(request.metadata.get("chat_truth_prompt") or {})
    context_messages = [message for message in request.messages if message.role == "context"]

    assert request.metadata["system_prompt_profile"] == "chat_operational"
    assert chat_truth["system_prompt_profile"] == "chat_operational"
    assert chat_truth["speech_safety_mode"] == "conversation_freer"
    assert chat_truth["tooling_guidance_enabled"] is True
    assert chat_truth["execution_safety_guidance_enabled"] is True
    assert chat_truth["context_delivery"] == "context_message"
    assert "these action rules apply only when using tools or proposing real-world side effects" in system_prompt
    assert "never claim you searched the web" in system_prompt
    assert "email and inbox tooling are not guaranteed" in system_prompt
    assert "local web0 builder draft generation" in system_prompt
    assert "do not refuse or say you can only guide setup" in system_prompt
    assert "relevant context from your memory" not in system_prompt
    assert len(context_messages) == 1
    assert context_messages[0].content.startswith("Relevant context and evidence:\n")
    lowered_context = context_messages[0].content.lower()
    assert "session sharing:" in lowered_context
    assert any(
        marker in lowered_context
        for marker in (
            "execution default:",
            "session sharing:",
            "openclaw tool doctrine",
        )
    )


def test_operational_chat_uses_canonical_runtime_transcript(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_transcript(**kwargs):
        calls.append(dict(kwargs))
        return ([{"role": "system", "content": "<retrieved_context>secret code SABLE-2048</retrieved_context>"}], "client_conversation_history")

    monkeypatch.setattr(prompt_normalizer, "canonical_runtime_transcript", fake_transcript)
    messages, source = prompt_normalizer._history_messages_for_chat(
        {
            "runtime_session_id": "runtime-secret",
            "conversation_history": [{"role": "user", "content": "Earlier local context is available."}],
        },
        runtime_session_id="runtime-secret",
        current_user_text="Inspect the project files and tell me the secret code.",
        prompt_profile="chat_operational",
    )

    assert calls
    assert source == "client_conversation_history"
    assert messages[0].content == "<retrieved_context>secret code SABLE-2048</retrieved_context>"

    request, _context_result = _build_request(
        "Inspect the project files and tell me the secret code.",
        task_class="system_design",
        task_kind="tool_intent",
        output_mode="tool_intent",
    )
    capsule_index = next(i for i, message in enumerate(request.messages) if "<retrieved_context>" in message.content)
    context_index = next(i for i, message in enumerate(request.messages) if message.role == "context")
    user_index = max(i for i, message in enumerate(request.messages) if message.role == "user")
    context_text = request.messages[context_index].content
    assert context_index < capsule_index < user_index
    assert "persona: vool" in context_text.lower()
    assert "The user prefers direct, useful answers." not in context_text
    # Use a real file operation: production doctrine is relevance-gated by the request.
    # Operational turns deliberately carry the production tool doctrine. This used to assert its
    # accidental absence from the first 2,000 rendered characters, making the result depend on
    # unrelated bootstrap-item lengths rather than the profile contract.
    assert "OpenClaw tool doctrine" in context_text


def test_authoritative_correction_is_folded_into_primary_system_prompt(monkeypatch) -> None:
    correction = (
        "Latest explicit user corrections for this chat are authoritative. "
        "They supersede older conflicting statements: preference = a short notebook"
    )
    monkeypatch.setattr(
        prompt_normalizer,
        "canonical_runtime_transcript",
        lambda **_kwargs: (
            [
                {"role": "system", "content": correction},
                {
                    "role": "user",
                    "content": "I changed my mind: I prefer a short notebook.",
                },
            ],
            "structured_dialogue_memory",
        ),
    )

    request, _context_result = _build_request(
        "What is my current preference?",
        task_class="chat_conversation",
        task_kind="normalization_assist",
        output_mode="plain_text",
        source_context={
            "surface": "openclaw",
            "platform": "openclaw",
            "runtime_session_id": "openclaw:correction-authority",
        },
    )

    assert correction in request.system_prompt()
    assert [
        message
        for message in request.messages
        if message.role == "system" and message.content == correction
    ] == []


def test_response_shaping_followup_uses_only_the_immediate_exchange(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_transcript(**kwargs):
        calls.append(dict(kwargs))
        return [], "none"

    monkeypatch.setattr(prompt_normalizer, "canonical_runtime_transcript", fake_transcript)

    request, _context_result = _build_request(
        "Make that exactly one sentence.",
        task_class="chat_conversation",
        task_kind="normalization_assist",
        output_mode="plain_text",
        source_context={
            "surface": "openclaw",
            "platform": "openclaw",
            "runtime_session_id": "openclaw:response-shaping",
        },
    )

    assert calls[-1]["max_messages"] == 2
    assert not [message for message in request.messages if message.role == "context"]


def test_preference_state_update_does_not_attach_broad_context() -> None:
    task = SimpleNamespace(
        task_id="preference-state-update",
        task_summary="Actually, I prefer concise bullet lists for this chat. Please keep that preference.",
    )
    interpretation = HumanInputInterpretation(
        raw_text=task.task_summary,
        normalized_text=task.task_summary,
        reconstructed_text=task.task_summary,
        intent_mode="statement",
        topic_hints=["preference"],
        reference_targets=[],
        understanding_confidence=0.84,
        quality_flags=[],
        is_continuation=False,
        state_mutation="preference",
    )
    request = normalize_prompt(
        task=task,
        classification={"task_class": "chat_conversation", "risk_flags": []},
        interpretation=interpretation,
        context_result=_synthetic_context_result(),
        persona=SimpleNamespace(persona_id="default", display_name="VOOL", tone="direct"),
        output_mode="plain_text",
        task_kind="conversation",
        trace_id="preference-state-update",
        surface="openclaw",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    truth = dict(request.metadata["chat_truth_prompt"])
    assert truth["state_mutation"] == "preference"
    assert truth["preference_state_update"] is True
    assert truth["context_delivery"] == "none"
    assert not [message for message in request.messages if message.role == "context"]
    assert all("persona: vool" not in message.content.lower() for message in request.messages)


def test_polite_response_shaping_followup_uses_only_the_immediate_exchange(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    immediate_exchange = [
        {
            "role": "user",
            "content": "Change the subject: why can a familiar song feel comforting?",
        },
        {
            "role": "assistant",
            "content": "It can bring back a reassuring memory.",
        },
    ]

    def fake_transcript(**kwargs):
        calls.append(dict(kwargs))
        return immediate_exchange, "structured_dialogue_memory"

    monkeypatch.setattr(prompt_normalizer, "canonical_runtime_transcript", fake_transcript)

    request, _context_result = _build_request(
        "Can you say that in a more casual way?",
        task_class="chat_conversation",
        task_kind="normalization_assist",
        output_mode="plain_text",
        source_context={
            "surface": "openclaw",
            "platform": "openclaw",
            "runtime_session_id": "openclaw:polite-response-shaping",
        },
    )

    assert calls[-1]["max_messages"] == 2
    assert [message.content for message in request.messages if message.role == "assistant"] == [
        "It can bring back a reassuring memory."
    ]
    assert not [message for message in request.messages if message.role == "context"]


def test_response_shaping_followup_guides_model_to_immediate_assistant_answer(monkeypatch) -> None:
    immediate_exchange = [
        {
            "role": "user",
            "content": "What makes a conversation feel easy?",
        },
        {
            "role": "assistant",
            "content": "Shared interests and respectful listening make it feel easy.",
        },
    ]

    monkeypatch.setattr(
        prompt_normalizer,
        "canonical_runtime_transcript",
        lambda **_kwargs: (immediate_exchange, "structured_dialogue_memory"),
    )

    request, _context_result = _build_request(
        "Put that in exactly three words.",
        task_class="chat_conversation",
        task_kind="normalization_assist",
        output_mode="plain_text",
        source_context={
            "surface": "openclaw",
            "platform": "openclaw",
            "runtime_session_id": "openclaw:exact-followup-guidance",
        },
    )

    system = request.system_prompt()
    assert "response-shaping follow-up" in system
    assert "Shared interests and respectful listening make it feel easy." in system
    assert "<immediate_assistant_answer>" in system
    assert "Current date time" not in system


def test_assistant_reference_followup_uses_only_the_immediate_exchange(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_transcript(**kwargs):
        calls.append(dict(kwargs))
        return [], "none"

    monkeypatch.setattr(prompt_normalizer, "canonical_runtime_transcript", fake_transcript)

    request, _context_result = _build_request(
        "Can you explain that without making it sound formal?",
        task_class="chat_conversation",
        task_kind="normalization_assist",
        output_mode="plain_text",
        source_context={
            "surface": "openclaw",
            "platform": "openclaw",
            "runtime_session_id": "openclaw:assistant-reference",
        },
    )

    assert calls[-1]["max_messages"] == 2
    assert not [message for message in request.messages if message.role == "context"]


def test_independent_chat_turn_keeps_adjacency_while_continuation_can_expand_it(
    monkeypatch,
) -> None:
    history = [
        {"role": "user", "content": "Why are indexes useful?"},
        {"role": "assistant", "content": "Indexes speed lookup."},
    ]
    base = {
        "surface": "openclaw",
        "platform": "openclaw",
        "runtime_session_id": "openclaw:independent-history",
        "conversation_history": history,
    }
    monkeypatch.setattr(
        prompt_normalizer,
        "canonical_runtime_transcript",
        lambda **_kwargs: (history, "client_conversation_history"),
    )
    task = SimpleNamespace(task_id="independent-history", task_summary="Why do file names matter?")
    common = {
        "task": task,
        "classification": {"task_class": "chat_conversation"},
        "context_result": _synthetic_context_result(),
        "persona": SimpleNamespace(persona_id="default", display_name="VOOL", tone="direct"),
        "output_mode": "plain_text",
        "task_kind": "conversation",
        "trace_id": "independent-history",
        "surface": "openclaw",
    }

    independent = normalize_prompt(
        **common,
        interpretation=HumanInputInterpretation(
            raw_text=task.task_summary,
            normalized_text=task.task_summary,
            reconstructed_text=task.task_summary,
            intent_mode="question",
            topic_hints=["file names"],
            reference_targets=[],
            understanding_confidence=0.8,
            is_continuation=False,
        ),
        source_context=dict(base),
    )
    continued = normalize_prompt(
        **common,
        interpretation=HumanInputInterpretation(
            raw_text="How does it help?",
            normalized_text="How does it help?",
            reconstructed_text="How does it help?",
            intent_mode="question",
            topic_hints=[],
            reference_targets=["indexes"],
            understanding_confidence=0.8,
            is_continuation=True,
        ),
        source_context=dict(base),
    )

    assert independent.metadata["chat_truth_prompt"]["transcript_source"] == (
        "client_conversation_history"
    )
    assert independent.metadata["chat_truth_prompt"]["history_expansion"] == (
        "same_chat_adjacency_floor"
    )
    assert any("indexes" in message.content.lower() for message in independent.messages)
    assert continued.metadata["chat_truth_prompt"]["history_expansion"] == "bounded_broad"
    assert any("indexes" in message.content.lower() for message in continued.messages)


def test_tiered_context_loader_filters_heavy_doctrine_from_chat_minimal_profile() -> None:
    context_result = _synthetic_context_result()

    default_context = context_result.assembled_context().lower()
    minimal_context = context_result.assembled_context(prompt_profile="chat_minimal").lower()

    assert "persona: vool. tone: direct." in default_context
    assert "execution default: advice_only." in default_context
    assert "never claim you searched the web without evidence." in default_context

    assert "persona: vool. tone: direct." in minimal_context
    assert "sensitive conversation is allowed" in minimal_context
    assert "humor=20/100" in minimal_context
    assert "the user prefers direct, useful answers." in minimal_context
    assert "execution default: advice_only." not in minimal_context
    assert "session sharing: private vault." not in minimal_context
    assert "privacy pact: local only" not in minimal_context
    assert "autonomy=hands_off" not in minimal_context
    assert "never claim you searched the web without evidence." not in minimal_context


def test_tiered_context_loader_capsule_profile_excludes_competing_retrieval() -> None:
    context_result = _synthetic_context_result()
    capsule_context = context_result.assembled_context(prompt_profile="chat_capsule")

    assert "Persona: VOOL" in capsule_context
    assert "The user prefers direct, useful answers." not in capsule_context
    assert "OpenClaw tool doctrine" not in capsule_context
    assert "Stale mission" not in capsule_context


def test_prompt_normalizer_falls_back_cleanly_for_stub_context_without_profile_support() -> None:
    request = normalize_prompt(
        task=SimpleNamespace(task_id="task-1", task_summary="Do you think boredom is useful?"),
        classification={"task_class": "unknown", "risk_flags": [], "confidence_hint": 0.84},
        interpretation=SimpleNamespace(
            reconstructed_text="Do you think boredom is useful?",
            topic_hints=[],
            understanding_confidence=0.84,
        ),
        context_result=SimpleNamespace(
            assembled_context=lambda: "Bootstrap Context:\n- OpenClaw tool doctrine: should stay as-is for plain stubs.",
            report=SimpleNamespace(
                retrieval_confidence=0.0,
                to_dict=lambda: {"external_evidence_attachments": []},
            ),
        ),
        persona=SimpleNamespace(persona_id="default", display_name="VOOL", tone="direct"),
        output_mode="plain_text",
        task_kind="normalization_assist",
        trace_id="trace-1",
        surface="openclaw",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    context_messages = [message for message in request.messages if message.role == "context"]

    assert request.metadata["system_prompt_profile"] == "chat_minimal"
    assert "be truthful about uncertainty, freshness, and capabilities" in request.system_prompt().lower()
    assert "relevant context from your memory" not in request.system_prompt().lower()
    assert len(context_messages) == 1
    assert "openclaw tool doctrine: should stay as-is for plain stubs." in context_messages[0].content.lower()


@pytest.mark.parametrize(
    ("task_class", "task_kind", "prompt"),
    [
        ("unknown", "normalization_assist", "Do you think boredom is useful?"),
        ("business_advisory", "normalization_assist", "How should I position my B2B analytics product?"),
        ("research", "summarization", "Latest Telegram Bot API updates?"),
        ("integration_orchestration", "normalization_assist", "Show me the open Hive tasks."),
    ],
)
def test_provider_facing_messages_keep_context_as_separate_user_evidence_payload_for_chat(
    task_class: str,
    task_kind: str,
    prompt: str,
) -> None:
    request, context_result = _build_request(
        prompt,
        task_class=task_class,
        task_kind=task_kind,
        output_mode="plain_text",
    )

    provider_messages = request.as_openai_messages()
    provider_system = next(message for message in provider_messages if message["role"] == "system")
    provider_evidence = [message for message in provider_messages if "Relevant context and evidence:" in message["content"]]
    fake_assistant_evidence = [
        message
        for message in provider_messages
        if message["role"] == "assistant" and "Relevant context and evidence:" in message["content"]
    ]

    assert "relevant context from your memory" not in provider_system["content"].lower()
    assert len(provider_evidence) == 1
    assert provider_evidence[0]["role"] == "user"
    assert not fake_assistant_evidence
    assert context_result.assembled_context(prompt_profile="chat_minimal")[:200] in provider_evidence[0]["content"]


def test_openclaw_chat_does_not_inject_canned_null_domain_claims() -> None:
    request, context_result = _build_request(
        "can we buy a .null address?",
        task_class="chat_conversation",
        task_kind="normalization_assist",
        output_mode="plain_text",
    )

    provider_messages = request.as_openai_messages()
    provider_system = next(message for message in provider_messages if message["role"] == "system")
    provider_evidence = [message for message in provider_messages if "Relevant context and evidence:" in message["content"]]
    assembled = context_result.assembled_context(prompt_profile="chat_minimal")

    assert request.metadata["system_prompt_profile"] == "chat_minimal"
    assert "wallet-owned solana null_registrar" not in provider_system["content"].lower()
    assert len(provider_evidence) == 1
    assert provider_evidence[0]["role"] == "user"
    assert "wallet-owned solana null_registrar" not in provider_evidence[0]["content"].lower()
    assert "not an icann dns tld" not in provider_evidence[0]["content"].lower()
    assert "nxgqhepfpdcu935h1d4g34g59zybo1jr4tbczwhv8np" not in provider_evidence[0]["content"].lower()
    assert "`vool resolve <name>.null`" not in provider_evidence[0]["content"]
    assert "web0 .null project facts" not in assembled.lower()


def test_internal_message_schema_maps_context_to_user_role_for_provider_calls() -> None:
    provider_message = normalize_prompt(
        task=SimpleNamespace(task_id="task-1", task_summary="Do you think boredom is useful?"),
        classification={"task_class": "unknown", "risk_flags": [], "confidence_hint": 0.84},
        interpretation=SimpleNamespace(
            reconstructed_text="Do you think boredom is useful?",
            topic_hints=[],
            understanding_confidence=0.84,
        ),
        context_result=SimpleNamespace(
            assembled_context=lambda **_: "Relevant shard note: boredom can be a signal.",
            report=SimpleNamespace(
                retrieval_confidence=0.8,
                to_dict=lambda: {"external_evidence_attachments": []},
            ),
        ),
        persona=SimpleNamespace(persona_id="default", display_name="VOOL", tone="direct"),
        output_mode="plain_text",
        task_kind="normalization_assist",
        trace_id="trace-1",
        surface="openclaw",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    ).as_openai_messages()[1]

    assert provider_message["role"] == "user"
    assert "relevant context and evidence:" in provider_message["content"].lower()


def test_plain_text_chat_uses_persisted_transcript_when_client_history_is_empty() -> None:
    session_id = _session_id("canonical-transcript")
    persona = load_active_persona("default")

    adapt_user_input("Do you think boredom is useful?", session_id=session_id)
    append_conversation_event(
        session_id=session_id,
        user_input="Do you think boredom is useful?",
        assistant_output="Boredom can be useful when it exposes that your environment is too flat.",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )
    interpretation = adapt_user_input("What do you mean by that?", session_id=session_id)
    request = normalize_prompt(
        task=create_task_record("What do you mean by that?"),
        classification={"task_class": "chat_conversation", "risk_flags": [], "confidence_hint": 0.84},
        interpretation=interpretation,
        context_result=_synthetic_context_result(),
        persona=persona,
        output_mode="plain_text",
        task_kind="normalization_assist",
        trace_id="trace-transcript-1",
        surface="openclaw",
        source_context={
            "surface": "openclaw",
            "platform": "openclaw",
            "runtime_session_id": session_id,
            "conversation_history": [],
        },
    )

    assert request.metadata["chat_truth_prompt"]["transcript_source"] == "structured_dialogue_memory"
    assert request.metadata["chat_truth_prompt"]["history_messages"] == 2
    assert request.messages[1].role == "user"
    assert request.messages[1].content == "Do you think boredom is useful?"
    assert request.messages[2].role == "assistant"
    assert request.messages[2].content == "Boredom can be useful when it exposes that your environment is too flat."
    assert [message.content for message in request.messages].count("What do you mean by that?") == 1
    assert request.messages[-1].role == "user"
    assert request.messages[-1].content == "What do you mean by that?"


def test_plain_text_chat_keeps_explicit_prior_answer_reason_followup_history() -> None:
    session_id = _session_id("prior-answer-reason")
    persona = load_active_persona("default")

    adapt_user_input(
        "Why might someone keep a paper notebook even when they use a phone?",
        session_id=session_id,
    )
    append_conversation_event(
        session_id=session_id,
        user_input="Why might someone keep a paper notebook even when they use a phone?",
        assistant_output=(
            "It can reduce screen distractions and preserve a personal connection to writing."
        ),
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )
    interpretation = adapt_user_input(
        "What was the most human reason you gave there?",
        session_id=session_id,
    )
    request = normalize_prompt(
        task=create_task_record("What was the most human reason you gave there?"),
        classification={"task_class": "chat_conversation", "risk_flags": [], "confidence_hint": 0.84},
        interpretation=interpretation,
        context_result=_synthetic_context_result(),
        persona=persona,
        output_mode="plain_text",
        task_kind="normalization_assist",
        trace_id="trace-prior-answer-reason",
        surface="openclaw",
        source_context={
            "surface": "openclaw",
            "platform": "openclaw",
            "runtime_session_id": session_id,
            "conversation_history": [],
        },
    )

    assert interpretation.is_continuation is True
    assert request.metadata["chat_truth_prompt"]["history_messages"] == 2
    assert any(
        message.role == "assistant"
        and "personal connection to writing" in message.content
        for message in request.messages
    )
    assert "personal connection to writing" in request.system_prompt()


def test_plain_text_chat_prefers_persisted_transcript_over_thin_client_history() -> None:
    session_id = _session_id("persisted-over-client")
    persona = load_active_persona("default")

    adapt_user_input("How should I position my analytics product?", session_id=session_id)
    append_conversation_event(
        session_id=session_id,
        user_input="How should I position my analytics product?",
        assistant_output="Position it around the painful decision it makes faster, not around dashboards.",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )
    interpretation = adapt_user_input("Can you sharpen that?", session_id=session_id)
    request = normalize_prompt(
        task=create_task_record("Can you sharpen that?"),
        classification={"task_class": "business_advisory", "risk_flags": [], "confidence_hint": 0.84},
        interpretation=interpretation,
        context_result=_synthetic_context_result(),
        persona=persona,
        output_mode="plain_text",
        task_kind="normalization_assist",
        trace_id="trace-transcript-2",
        surface="openclaw",
        source_context={
            "surface": "openclaw",
            "platform": "openclaw",
            "runtime_session_id": session_id,
            "client_conversation_history": [
                {"role": "assistant", "content": "Thin client history that should lose."},
            ],
            "conversation_history": [
                {"role": "assistant", "content": "Thin client history that should lose."},
            ],
        },
    )

    message_contents = [message.content for message in request.messages]

    assert request.metadata["chat_truth_prompt"]["transcript_source"] == "structured_dialogue_memory"
    assert "Thin client history that should lose." not in message_contents
    assert "How should I position my analytics product?" in message_contents
    assert "Position it around the painful decision it makes faster, not around dashboards." in message_contents


def test_plain_text_chat_delivers_compacted_persisted_history_to_model(monkeypatch) -> None:
    session_id = _session_id("compacted-live-path")
    persona = load_active_persona("default")
    monkeypatch.setattr(
        "core.conversation_summarizer._call_ollama",
        lambda model, messages, timeout=60: "## Key Facts\n- launch port 8096 survives compaction",
    )
    for index in range(13):
        user_text = f"Conversation message {index}; keep launch port 8096 in context."
        adapt_user_input(user_text, session_id=session_id)
        append_conversation_event(
            session_id=session_id,
            user_input=user_text,
            assistant_output=f"Recorded conversation message {index} and launch port 8096.",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    current_user_text = "What launch port did we choose?"
    interpretation = adapt_user_input(current_user_text, session_id=session_id)
    request = normalize_prompt(
        task=create_task_record(current_user_text),
        classification={"task_class": "chat_conversation", "risk_flags": [], "confidence_hint": 0.84},
        interpretation=interpretation,
        context_result=_synthetic_context_result(),
        persona=persona,
        output_mode="plain_text",
        task_kind="normalization_assist",
        trace_id="trace-compacted-live-path",
        surface="openclaw",
        source_context={
            "surface": "openclaw",
            "platform": "openclaw",
            "runtime_session_id": session_id,
            "conversation_history": [],
        },
    )

    assert request.metadata["chat_truth_prompt"]["transcript_source"] == "structured_dialogue_memory"
    assert any("<context_summary>" in message.content for message in request.messages)
    assert "8096" in " ".join(message.content for message in request.messages)


def test_plain_text_chat_falls_back_to_client_history_when_persisted_transcript_is_missing() -> None:
    session_id = _session_id("client-history-fallback")
    ensure_chat_namespace(
        session_id,
        grant_current_receipts=False,
    )
    request = normalize_prompt(
        task=SimpleNamespace(task_id="task-1", task_summary="Can you continue that?"),
        classification={"task_class": "chat_conversation", "risk_flags": [], "confidence_hint": 0.84},
        interpretation=SimpleNamespace(
            reconstructed_text="Can you continue that?",
            topic_hints=[],
            understanding_confidence=0.84,
        ),
        context_result=_synthetic_context_result(),
        persona=SimpleNamespace(persona_id="default", display_name="VOOL", tone="direct"),
        output_mode="plain_text",
        task_kind="normalization_assist",
        trace_id="trace-transcript-3",
        surface="openclaw",
        source_context={
            "surface": "openclaw",
            "platform": "openclaw",
            "runtime_session_id": session_id,
            "client_conversation_history": [
                {"role": "user", "content": "Do you think boredom is useful?"},
                {"role": "assistant", "content": "Yes. It can expose that your environment is too flat."},
            ],
        },
    )

    assert request.metadata["chat_truth_prompt"]["transcript_source"] == "client_conversation_history"
    assert request.metadata["chat_truth_prompt"]["history_messages"] == 2
    assert request.messages[1].content == "Do you think boredom is useful?"
    assert request.messages[2].content == "Yes. It can expose that your environment is too flat."


def test_provider_facing_chat_context_uses_structured_tool_observation_instead_of_fake_tool_prose() -> None:
    session_id = _session_id("tool-observation")
    ensure_chat_namespace(session_id)
    receipt_id = f"tool-receipt-qwen-profile-{session_id}"
    persona = load_active_persona("default")
    prior = adapt_user_input("latest qwen release notes", session_id=session_id)
    checkpoint = create_runtime_checkpoint(
        session_id=session_id,
        request_text="latest qwen release notes",
        source_context={
            "runtime_session_id": session_id,
            "surface": "openclaw",
            "platform": "openclaw",
            "_canonical_user_turn_id": prior.turn_id,
        },
    )
    store_tool_receipt(
        receipt_key=receipt_id,
        session_id=session_id,
        checkpoint_id=checkpoint["checkpoint_id"],
        tool_name="web.search",
        idempotency_key=f"action:{receipt_id}",
        arguments={},
        execution={
            "action_record": {
                "receipt_id": receipt_id,
                "origin": {"chat_id": session_id, "project_id": ""},
                "result": {
                    "summary": "web.search: Found Qwen release notes."
                },
            }
        },
    )
    update_runtime_checkpoint(
        checkpoint["checkpoint_id"],
        state={
            "executed_steps": [
                {
                    "tool_name": "web.search",
                    "status": "executed",
                    "summary": "Found Qwen release notes.",
                }
            ],
            "last_tool_response": {
                "handled": True,
                "ok": True,
                "status": "executed",
                "response_text": 'Search results for "latest qwen release notes": ...',
                "tool_name": "web.search",
                "receipt": {
                    "receipt_id": receipt_id,
                    "safe_summary": "web.search: Found Qwen release notes.",
                },
                "details": {
                    "observation": {
                        "schema": "tool_observation_v1",
                        "intent": "web.search",
                        "tool_surface": "web",
                        "ok": True,
                        "status": "executed",
                        "query": "latest qwen release notes",
                        "results": [
                            {
                                "title": "Qwen release notes",
                                "url": "https://example.test/qwen",
                                "snippet": "Fresh update summary",
                            }
                        ],
                    }
                },
            },
        },
        status="completed",
    )
    append_conversation_event(
        session_id=session_id,
        user_input="latest qwen release notes",
        assistant_output="I found the latest Qwen release notes.",
        source_context={},
    )
    interpretation = adapt_user_input("Open that result.", session_id=session_id)
    task = create_task_record(interpretation.normalized_text)
    request = normalize_prompt(
        task=task,
        classification={"task_class": "chat_research", "risk_flags": [], "confidence_hint": 0.84},
        interpretation=interpretation,
        context_result=TieredContextLoader().load(
            task=task,
            classification={"task_class": "chat_research", "risk_flags": [], "confidence_hint": 0.84},
            interpretation=interpretation,
            persona=persona,
            session_id=session_id,
            total_context_budget=5000,
        ),
        persona=persona,
        output_mode="plain_text",
        task_kind="summarization",
        trace_id="trace-tool-observation",
        surface="openclaw",
        source_context={
            "surface": "openclaw",
            "platform": "openclaw",
            "runtime_session_id": session_id,
            "conversation_history": [],
        },
    )

    provider_messages = request.as_openai_messages()
    provider_evidence = [message for message in provider_messages if "Relevant context and evidence:" in message["content"]]

    assert len(provider_evidence) == 1
    assert provider_evidence[0]["role"] == "user"
    assert f'"receipt_id": "{receipt_id}"' in provider_evidence[0]["content"]
    assert '"safe_summary": "web.search: Found Qwen release notes."' in provider_evidence[0]["content"]
    assert '"query": "latest qwen release notes"' not in provider_evidence[0]["content"]
    assert '"results": [' not in provider_evidence[0]["content"]
    assert "Real tool result from" not in provider_evidence[0]["content"]


def test_same_turn_tool_observations_are_injected_independently_of_dialogue_history() -> None:
    message = prompt_normalizer._runtime_tool_observation_message(
        {
            "runtime_tool_observations": [
                {
                    "schema": "tool_observation_v1",
                    "intent": "workspace.list_tree",
                    "ok": True,
                    "status": "executed",
                    "response_preview": "README.md\ncore/router.py",
                }
            ]
        }
    )

    assert message is not None
    assert message.role == "context"
    assert "same turn" in message.content
    assert "workspace.list_tree" in message.content
    assert "do not repeat an identical tool call" in message.content.lower()


def test_workspace_audit_evidence_is_labeled_as_tool_input_not_model_answer() -> None:
    request, _context_result = _build_request(
        "Judge whether this implementation is solid and propose fixes without editing it.",
        task_class="debugging",
        task_kind="normalization_assist",
        output_mode="plain_text",
        source_context={
            "surface": "openclaw",
            "platform": "openclaw",
            "requested_model": "novel/zephyr-audit:free",
            "workspace": "/tmp/metamorphic-lantern-42",
            "workspace_binding": "project",
            "project_id": "lantern-42",
            "workspace_audit_evidence_collected": True,
            "runtime_tool_observations": [
                {
                    "schema": "tool_observation_v1",
                    "intent": "workspace.audit",
                    "final_answer": False,
                    "response_preview": "P1: unsafe deserialization at engine.py:73",
                }
            ],
        },
    )

    assert "output is evidence, not an answer" in request.system_prompt()
    messages = request.as_openai_messages()
    evidence = next(message for message in messages if "workspace.audit" in message["content"])
    assert evidence["role"] == "user"
    assert '"final_answer": false' in evidence["content"]
    assert "engine.py:73" in evidence["content"]


@pytest.mark.parametrize("prompt", [
    "Fix math.js so the existing test.js passes. Run the existing tests and explain the change briefly. Keep the tests unchanged.",
    "Repair the CSV parser, run its existing checks, and describe the change.",
])
def test_tool_turn_guidance_does_not_force_json_envelopes_inside_native_calls(prompt):
    from adapters.base_adapter import ModelRequest
    from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
    from core.cloud_tool_call_contract import build_cloud_tool_definitions, parse_native_tool_calls
    from core.runtime_execution_tools import runtime_execution_tool_specs

    internal, _context = _build_request(
        prompt, task_class="debugging", task_kind="tool_intent", output_mode="tool_intent",
        source_context={"surface": "api", "operating_mode": "manual"},
    )
    specs = [s for s in runtime_execution_tool_specs() if s["intent"] == "code.task.step"]
    definitions = build_cloud_tool_definitions(specs)
    adapter = OpenAICompatibleAdapter(SimpleNamespace(
        provider_id="fixture-native", model_name="fixture-model", metadata={},
        runtime_config={"base_url": "https://openrouter.ai/api/v1"},
    ))
    request = ModelRequest(
        task_kind="tool_intent", prompt=prompt, messages=internal.as_openai_messages(),
        output_mode="tool_intent", tools=definitions, tools_required=True,
    )
    wire = adapter._build_openai_payload(request, force_json=True, stream=False)
    system = "\n".join(m["content"] for m in wire["messages"] if m["role"] == "system")
    assert "native functions" in system.lower()
    assert "only when no native functions are supplied" in system.lower()
    assert 'Return valid JSON only in the form {"intent": string, "arguments": object}.' not in system
    assert "Choose exactly one intent name" not in system
    assert "response_format" not in wire
    assert wire["tools"][0]["function"]["name"] == "code__task__step"
    inner = {"task_id": "ct-protocol-fixture", "intent": "workspace.read_file", "arguments": {"path": "parser.py"}}
    call, = parse_native_tool_calls([{"function": {
        "name": "code__task__step", "arguments": inner,
    }}], definitions=definitions)
    assert call.arguments == inner
