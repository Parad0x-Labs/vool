"""Gauntlet — category 5: Web0 flows (deterministic, wallet-risk-free).

Web0's register/quote/ecosystem/definition mechanics are already heavily covered
at the unit layer (tests/test_null_register_chat.py, test_web0_project_grounding.py,
test_web0_ecosystem_grounding.py, test_web0_ecosystem_definition.py,
test_web0_service_integration.py). This file adds the two things those don't:

  - the HTTP-JOINED proof that deterministic .null safety answers short-circuit
    before the model;
  - source-backed canonical retrieval for non-transactional project questions.

Deliberately avoids the register/execute path at the HTTP layer: dispatch_post binds
the REAL preview/execute/wallet there (a preview reaches live RPC), so only pure
knowledge questions — which never touch the wallet — are exercised end-to-end.
"""
from __future__ import annotations

import json

import pytest

from core.canonical_project_knowledge import retrieve_canonical_passages
from core.web.api.runtime import RuntimeServices, run_agent
from core.web.api.service import dispatch_post
from core.web0_project_grounding import web0_null_project_response

pytestmark = [pytest.mark.gauntlet]


def _model_must_not_run(*_args, **_kwargs):
    raise AssertionError("the model was invoked for a Web0 knowledge question that grounding should answer")


def _post(content: str, run_agent_provider=None):
    return dispatch_post(
        path="/api/chat",
        body={"messages": [{"role": "user", "content": content}]},
        headers={},
        runtime=RuntimeServices(display_name="VOOL"),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        run_agent_provider=run_agent_provider or _model_must_not_run,
        resolve_null_domain_provider=lambda name: None,
    )


# ---------------------------------------------------------------------------
# HTTP-joined: .null questions reach the ordinary lanes (owner instruction 2026-09-16)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    ["how much does it cost to buy a .null name?", "is registering a .null name free?"],
)
def test_web0_questions_reach_the_ordinary_lanes_not_a_transport_interceptor(question):
    """The scripted .null topic-answer interception at the transport layer was REMOVED by the
    owner's instruction (2026-09-16): its keyword evidence was weaker than its authority, and a
    provider-health checker asking about model CLAIMs and request COSTs was answered wholesale
    with registration-fee boilerplate. What must hold at this seam now is the INVERSE: nothing
    here answers a .null question on its own -- the turn always reaches the ordinary lanes,
    whose own grounding (canonical passages, the publication gate) governs the answer. The
    deterministic responder stays a library, proven by the unit tests below."""
    reached: list[str] = []

    def _recording_agent(_runtime, user_text, **_kwargs):
        reached.append(str(user_text))
        return {"response": "ordinary lane answer", "model_calls": 1, "route": "test:ordinary"}

    resp = _post(question, run_agent_provider=_recording_agent)
    assert resp.status == 200, resp.body
    assert reached == [question], "the transport intercepted a .null question before the ordinary lanes"


def test_web0_cost_honesty_is_carried_by_the_responder_not_the_transport():
    """The price honesty itself (never claims free, names the SOL cost basis, no fake
    on-chain claims) is the deterministic responder's own contract -- pinned directly,
    because the transport no longer answers .null questions on its own."""
    r = web0_null_project_response("is registering a .null name free?")
    assert r is not None
    low = r["response"].lower()
    assert "free" not in low or "not free" in low or "isn't free" in low
    assert "sol" in low


# ---------------------------------------------------------------------------
# Grounding honesty: no fake mainnet / no Web3 / DNS mischaracterisation
# ---------------------------------------------------------------------------

def test_web0_definition_uses_canonical_sources_instead_of_a_fixed_answer():
    assert web0_null_project_response("what is web0?") is None
    passages = retrieve_canonical_passages("what is web0?")
    assert passages
    assert all(passage.source_path for passage in passages)
    assert all(passage.content_hash for passage in passages)


def test_user_wording_does_not_select_a_canned_project_answer():
    query = "What is Web0 in VOOL context? Do not mention Web3."
    assert web0_null_project_response(query) is None
    assert retrieve_canonical_passages(query)


def test_web0_constraints_stay_in_the_prompt_not_a_fixed_answer():
    query = "Explain Web0 without mentioning Web3."
    passages = retrieve_canonical_passages(query)
    assert passages
    assert web0_null_project_response(query) is None


def test_runtime_run_agent_null_registration_reaches_the_agent(tmp_path):
    """The run_agent spine answers no .null question itself either (same 2026-09-16 removal):
    the registration question must reach the agent's ordinary lanes, where the canonical
    grounding requirement and the spend-gated tools govern it. The registration SAFETY copy
    (approval before any spend) is the responder's contract, pinned by the unit tests below."""
    reached: list[str] = []

    class _RecordingAgent:
        def run_once(self, _text, **_kwargs):
            reached.append(_text)
            return {"response": "ordinary lane answer", "model_calls": 1, "web_calls": 0}

    runtime = RuntimeServices(agent=_RecordingAgent(), runtime_home=str(tmp_path))
    run_agent(
        runtime,
        "How do I register a .null name? Do not spend anything.",
        session_id="null-register-runtime-grounding",
        source_context={"surface": "api", "platform": "api", "allow_remote_fetch": False},
        workspace_root_provider=lambda: str(tmp_path),
    )

    assert reached == ["How do I register a .null name? Do not spend anything."]
    r = web0_null_project_response("How do I register a .null name? Do not spend anything.")
    assert r is not None
    low = r["response"].lower()
    assert "approval" in low or "approve" in low or "windows hello" in low
    assert "automatically spend" not in low


def test_null_cost_answer_makes_no_fake_completed_registration_claim():
    r = web0_null_project_response("how much does it cost to buy a .null name?")
    assert r is not None
    low = r["response"].lower()
    # explains the cost path without pretending a purchase already happened on-chain
    assert "sol" in low
    for fabricated in ("registered on mainnet", "transaction confirmed", "you now own", "successfully registered"):
        assert fabricated not in low


def test_named_registration_refuses_to_auto_spend():
    r = web0_null_project_response("register test123.null for me please")
    assert r is not None
    low = r["response"].lower()
    # a chat request can never move SOL: the grounded answer says so and points to the CLI
    assert "will not sign, spend, or submit" in low
    assert "vool resolve test123.null" in low


def test_registration_word_alone_does_not_over_fire_grounding():
    # honesty in the other direction: don't hijack unrelated messages into web0 grounding
    assert web0_null_project_response("how do I register for the newsletter") is None
    assert web0_null_project_response("what is the capital of france?") is None
