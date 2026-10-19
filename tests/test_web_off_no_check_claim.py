"""With web lookups off, a reply never claims a web check that could not run.

Review finding on 3422a2e1 (2026-10-18): with web lookups off and no model available, a
"search the web" turn answered "I checked the live web lane for ..., but I could not ground a
current answer confidently." No lookup ran (web_calls=0); the live-info preflight had refused,
the front door set the refusal aside so other lanes could serve the demand, and the degraded
chat fallback then rendered the lookup-failure wording. The phrasings below are written for
this file except the review's own input, which is kept as its regression twin.
"""
from __future__ import annotations

import pytest

from apps.vool_agent import VoolAgent
from core import chat_attachments as ca
from core import policy_engine, runtime_paths
from core.agent_runtime.fast_live_info_mode_failure import live_info_failure_text

SESSION = "openclaw:5234567890abcdef5678"

REVIEW_INPUT = "Search the web for the current official opening times of the Helsinki maritime museum."
FRESH_INPUT = "Search online for the current entry fee at the Bergen aquarium."


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    runtime_paths.configure_runtime_home(tmp_path / "home")
    yield
    runtime_paths.configure_runtime_home(None)


@pytest.fixture
def web_off(monkeypatch):
    monkeypatch.setenv("VOOL_DISABLE_WEB", "1")
    policy_engine.load(force_reload=True)
    assert policy_engine.allow_web_fallback() is False
    yield
    monkeypatch.undo()
    policy_engine.load(force_reload=True)


def _enter(text, context=None):
    agent = VoolAgent(backend_name="test-backend", device="web-off-claim")
    context = {"surface": "api", "_owner_local": True, "runtime_session_id": SESSION, "operating_mode": "auto", **(context or {})}
    return agent.run_once(text, session_id_override=SESSION, source_context=context)


def _document_context():
    # A retained chat document that has nothing to do with the question.
    doc = ca.stage_document(session_id=SESSION, data=("The kiln rota starts with crew K4 on Mondays.\n" * 90).encode())
    ca.bind_to_turn(session_id=SESSION, turn_id="source", attachment_ids=[doc["id"]])
    ca.release_turn(session_id=SESSION, turn_id="source", outcomes=None)
    return {"runtime_session_id": SESSION}


@pytest.mark.parametrize("carried", [False, True], ids=["no-document", "retained-document"])
@pytest.mark.parametrize("text", [REVIEW_INPUT, FRESH_INPUT], ids=["review-input", "fresh-input"])
def test_web_off_turn_says_web_is_off_and_claims_no_check(web_off, text, carried):
    result = _enter(text, _document_context() if carried else {})
    response = str(result.get("response") or "")
    assert policy_engine.allow_web_fallback() is False
    assert "web lookups are off" in response.lower(), response
    assert "did not search the web" in response, response
    assert "checked the live web" not in response.lower(), response
    assert "tried the live web" not in response.lower(), response


@pytest.mark.parametrize("mode", ["fresh_lookup", "weather", "news"])
def test_web_off_renderer_never_reports_a_lookup(web_off, mode):
    text = live_info_failure_text(query="tide table for the harbour", mode=mode)
    assert text.startswith("Web lookups are off"), text
    assert "tide table" not in text


@pytest.mark.parametrize(
    ("mode", "opening"),
    [("fresh_lookup", "I checked the live web lane"), ("weather", "I tried the live web lane"), ("news", "I tried the live web lane")],
)
def test_web_on_renderer_keeps_the_lookup_failure_wording(monkeypatch, mode, opening):
    # With web lookups on, the lane really searched before reaching this text.
    monkeypatch.delenv("VOOL_DISABLE_WEB", raising=False)
    monkeypatch.setenv("VOOL_ENABLE_WEB", "1")
    policy_engine.load(force_reload=True)
    assert policy_engine.allow_web_fallback() is True
    try:
        text = live_info_failure_text(query="tide table for the harbour", mode=mode)
    finally:
        monkeypatch.undo()
        policy_engine.load(force_reload=True)
    assert text.startswith(opening), text
    assert '"tide table for the harbour"' in text
