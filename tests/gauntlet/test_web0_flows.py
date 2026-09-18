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


def _post(content: str):
    return dispatch_post(
        path="/api/chat",
        body={"messages": [{"role": "user", "content": content}]},
        headers={},
        runtime=RuntimeServices(display_name="VOOL"),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        run_agent_provider=_model_must_not_run,
        resolve_null_domain_provider=lambda name: None,
    )


# ---------------------------------------------------------------------------
# HTTP-joined: deterministic .null safety knowledge never reaches the model
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("question", ["how much does it cost to buy a .null name?"])
def test_web0_knowledge_is_grounded_without_invoking_the_model(question):
    resp = _post(question)
    assert resp.status == 200
    body = json.loads(resp.body)
    text = json.dumps(body).lower()
    # a real, grounded answer came back (not an empty/error shell)
    assert "web0" in text or "null" in text


def test_web0_cost_answer_is_honest_about_price_over_http():
    resp = _post("is registering a .null name free?")
    body = json.loads(resp.body)
    text = json.dumps(body).lower()
    # never claims it is free; names the real cost basis
    assert "free" not in text or "not free" in text or "isn't free" in text
    assert "sol" in text


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


def test_runtime_run_agent_null_registration_grounding_does_not_invoke_model(tmp_path):
    class _ModelMustNotRun:
        def run_once(self, *_args, **_kwargs):
            raise AssertionError(".null registration grounding should run before the model")

    runtime = RuntimeServices(agent=_ModelMustNotRun(), runtime_home=str(tmp_path))
    result = run_agent(
        runtime,
        "How do I register a .null name? Do not spend anything.",
        session_id="null-register-runtime-grounding",
        source_context={"surface": "api", "platform": "api", "allow_remote_fetch": False},
        workspace_root_provider=lambda: str(tmp_path),
    )

    text = result["response"].lower()
    assert result["model_calls"] == 0
    assert result["web_calls"] == 0
    assert ".null" in text
    assert "approval" in text or "approve" in text or "windows hello" in text
    assert "automatically spend" not in text


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
