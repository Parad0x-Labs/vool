"""One model round renders ONE offer as its prompt catalog and as its native tools (revision 6, Gate B).

core.memory_first_router.MemoryFirstRouter._build_round_request -- what _execute_provider_task calls for every
model round -- materializes the round's offer before the prompt is built, and hands that same object to the
prompt catalog (normalize_prompt(tool_offer=...)) and to the native tool definitions (request.tools). This
builds a real tool_intent round's request in a turn whose inputs a catalog built on its own would not share:
the session's previous family, inherited by a follow-up-shaped message, and a same-turn expansion.
"""
from __future__ import annotations

import re
import uuid
from types import SimpleNamespace
from unittest import mock

import pytest

CATALOG_LINE = re.compile(r"(?:^|\s)- ([a-z0-9_]+(?:\.[a-z0-9_]+)+)\(")


@pytest.fixture(autouse=True)
def _clean_offer_state():
    from core import policy_engine
    from core.tool_offer_state import reset_offer_state

    previous = policy_engine._POLICY_CACHE
    reset_offer_state()
    try:
        yield
    finally:
        reset_offer_state()
        policy_engine._POLICY_CACHE = previous


@pytest.mark.parametrize(
    ("text", "inherited", "expanded"),
    [("and the rest?", "workspace", "filesystem"), ("so what else is there?", "filesystem", "pdf")],
    ids=["original-families", "novel-families"],
)
def test_a_tool_intent_round_renders_one_offer_for_its_catalog_and_its_native_tools(
    text: str, inherited: str, expanded: str
) -> None:
    from core import prompt_normalizer
    from core.human_input_adapter import HumanInputInterpretation
    from core.identity_manager import load_active_persona
    from core.memory_first_router import MemoryFirstRouter
    from core.task_router import classify, create_task_record
    from core.tool_offer_state import (
        begin_turn_navigation,
        end_turn_navigation,
        note_family_offer,
        record_family_expansion,
    )

    session = "openclaw:round-offer-" + uuid.uuid4().hex
    note_family_offer(session, (inherited,))  # the previous turn's family; a follow-up-shaped message inherits it
    task = create_task_record(text)
    interpretation = HumanInputInterpretation(
        raw_text=text, normalized_text=text, reconstructed_text=text, intent_mode="question",
        topic_hints=[], reference_targets=[], understanding_confidence=0.9, quality_flags=[],
    )
    classification = classify(text, context=interpretation.as_context())
    context_result = SimpleNamespace(
        assembled_context=lambda: "", context_snippets=lambda: [], local_candidates=[], swarm_metadata=[],
        retrieval_confidence_score=0.0,
        report=SimpleNamespace(retrieval_confidence=0.0, total_tokens_used=lambda: 0,
                               to_dict=lambda: {"external_evidence_attachments": []}),
    )
    source_context = {
        "surface": "openclaw", "platform": "openclaw", "runtime_session_id": session, "session_id": session,
        "turn_id": "turn-" + uuid.uuid4().hex,
    }
    scope = begin_turn_navigation(source_context)
    assert record_family_expansion(source_context, expanded) == (expanded,)
    try:
        with mock.patch(
            "core.memory_first_router.normalize_prompt", wraps=prompt_normalizer.normalize_prompt
        ) as prompt_builder:
            request = MemoryFirstRouter()._build_round_request(
                task=task, classification=classification, interpretation=interpretation,
                context_result=context_result, persona=load_active_persona("default"), output_mode="tool_intent",
                task_kind="tool_intent", surface="openclaw", source_context=source_context,
            )
    finally:
        end_turn_navigation(source_context, scope)

    offers = [call.kwargs.get("tool_offer") for call in prompt_builder.call_args_list]
    assert offers and offers[0] is not None, "the prompt catalog was not handed the round's offer"
    offer = offers[0]
    assert offer.expanded_families == (expanded,)
    native = [tool.intent for tool in request.tools]
    assert native == list(offer.intents)
    assert request.metadata.get("tool_offer_fingerprint") == offer.fingerprint
    rendered = " ".join([str(request.system_prompt or "")]
                        + [str(message.get("content") or "") for message in request.messages])
    assert set(CATALOG_LINE.findall(rendered)) == set(offer.intents)
    assert request.tool_choice == "required" and request.tools_required
