"""pa_beta_gate — source-backed canonical project grounding."""
from __future__ import annotations

import uuid

import pytest

from core.canonical_project_knowledge import (
    retrieve_canonical_passages,
)
from core.web0_project_grounding import web0_boot_context, web0_null_project_response

pytestmark = [pytest.mark.pa_beta]


@pytest.mark.parametrize("query", [
    "what is web0?",
    "explain .null names",
    "tell me about VOOL Local",
    "how does DNA x402 work?",
    "what is dark null?",
    "is web0 like arweave storage?",
])
def test_canonical_grounding_triggers_for_supported_stack_terms(query):
    context = web0_boot_context(query)
    assert context
    assert "[Canonical source:" in context
    assert "sha256=" in context


@pytest.mark.parametrize("query", [
    "what is the capital of france?",
    "help me write a python function",
    "summarize my notes",
])
def test_canonical_grounding_does_not_trigger_for_unrelated(query):
    assert web0_boot_context(query) == ""


def test_canonical_grounding_has_relative_provenance():
    passages = retrieve_canonical_passages("what is web0?")
    assert passages
    assert all(not passage.source_path.startswith(("/", "\\")) for passage in passages)
    assert all(len(passage.content_hash) == 64 for passage in passages)


def test_canonical_documents_are_injected_into_bootstrap_context():
    from core.bootstrap_context import build_bootstrap_context
    from core.human_input_adapter import adapt_user_input
    from core.identity_manager import load_active_persona
    from core.task_router import classify, create_task_record

    sid = f"openclaw:web0:{uuid.uuid4().hex}"
    q = "explain what web0 is in the VOOL context"
    interp = adapt_user_input(q, session_id=sid)
    task = create_task_record(q)
    items = build_bootstrap_context(
        persona=load_active_persona("default"), task=task, classification=classify(task.task_summary),
        interpretation=interp, session_id=sid,
    )
    canonical_items = [i for i in items if i.source_type == "canonical_document"]
    assert canonical_items, "Canonical documents not injected for a Web0 query"
    assert canonical_items[0].metadata["source_class"] == "canonical"
    assert canonical_items[0].metadata["content_hash"]


@pytest.mark.parametrize("query", [
    "what is web0?",
    "explain .null names",
    "tell me about VOOL Local",
    "how does DNA x402 work?",
    "what is dark null?",
    "is web0 like arweave storage?",
])
def test_project_definitions_are_not_canned(query):
    assert web0_null_project_response(query) is None


def test_user_wording_does_not_select_a_fixed_answer():
    query = "Explain Web0 without mentioning Web3."
    assert web0_null_project_response(query) is None
    assert retrieve_canonical_passages(query)
