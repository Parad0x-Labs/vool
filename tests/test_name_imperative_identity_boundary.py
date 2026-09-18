"""The verb ``name`` must not become an assistant-name lookup.

The frozen Set 2 FX prompt ends one question, then says ``Explicitly name the currencies``.
Identity routing used a punctuation-blind proximity window and treated ``it`` from ``buy it?`` as
the possessor of ``name``. These tests pin the grammatical boundary: a name noun needs a real
possessive/compound relation; a transitive name verb owns its object, not an identity fast path.
"""

from __future__ import annotations

import pytest

from core.agent_runtime.fast_paths_currency import currency_fast_path
from core.context_namespace import ensure_chat_namespace
from core.request_trust import OWNER_LOCAL_KEY
from core.user_identity_authority import classify_identity_question
from core.web.api.runtime import RuntimeServices, _assistant_identity_response, run_agent
from tests.live.runtime_model_gauntlet import load_cases

FROZEN_SET2_07 = load_cases((2,))[6].prompt

NAME_VERB_CONTROLS = (
    "Explicitly name the currencies.",
    "Name both currencies.",
    "Can you name three databases?",
    "Please name every country in Europe.",
    "Name your favorite currency and explain why.",
    "I can name the tool, but explain what it does.",
    "Do you have enough to buy it? Name the currencies and show the remainder.",
)

ASSISTANT_NAME_NOUN_CONTROLS = (
    "What is your name?",
    "What is your full name?",
    "What is your current name?",
    "What name should I call you?",
    "Which name do you use?",
    "Tell me your name.",
    "Yo bot name?",
    "What is the application name?",
    "What is the name of this app?",
    "Tell me the name of your assistant.",
)

USER_NAME_NOUN_CONTROLS = (
    "What is my name?",
    "What is my full name?",
    "Say my profile name.",
)


def test_frozen_set2_07_is_currency_work_not_assistant_identity() -> None:
    identity = classify_identity_question(FROZEN_SET2_07)
    currency = currency_fast_path(FROZEN_SET2_07)

    assert not identity.subject
    assert identity.reason == "name_verb_with_object"
    assert _assistant_identity_response(FROZEN_SET2_07) is None
    assert currency is not None
    assert currency["kind"] == "travel_spend"
    assert "Indonesian rupiah (IDR)" in currency["response"]
    assert "Japanese yen (JPY)" in currency["response"]
    assert "1,000 JPY short" in currency["response"]


def test_web_runtime_frontdoor_reaches_currency_lane_for_frozen_set2_07(tmp_path, monkeypatch) -> None:
    from apps.vool_agent import VoolAgent

    session_id = "name-verb-frozen-set2-07"
    agent = VoolAgent(backend_name="test-backend", device="name-verb-test", persona_id="default")
    runtime = RuntimeServices(agent=agent, runtime_home=str(tmp_path))
    monkeypatch.setattr("core.web.api.runtime.schedule_memory_extraction", lambda *_args, **_kwargs: None)
    ensure_chat_namespace(session_id, grant_current_receipts=False)

    result = run_agent(
        runtime,
        FROZEN_SET2_07,
        session_id=session_id,
        source_context={
            "surface": "api",
            "platform": "api",
            "allow_remote_fetch": False,
            "workspace": str(tmp_path),
            "workspace_root": str(tmp_path),
            OWNER_LOCAL_KEY: True,
        },
        workspace_root_provider=lambda: str(tmp_path),
    )

    assert result["route_reason"] == "currency_travel_spend_fast_path"
    assert "1,000 JPY short" in result["response"]
    assert result.get("model_calls", 0) == 0
    assert result.get("web_calls", 0) == 0


@pytest.mark.parametrize("prompt", NAME_VERB_CONTROLS)
def test_transitive_name_verbs_never_claim_an_assistant_identity(prompt: str) -> None:
    question = classify_identity_question(prompt)

    assert not question.asks_assistant_identity, (prompt, question)
    assert _assistant_identity_response(prompt) is None


@pytest.mark.parametrize("prompt", ASSISTANT_NAME_NOUN_CONTROLS)
def test_grammatical_assistant_name_nouns_keep_the_identity_lane(prompt: str) -> None:
    question = classify_identity_question(prompt)

    assert question.asks_assistant_identity, (prompt, question)
    assert _assistant_identity_response(prompt) is not None


@pytest.mark.parametrize("prompt", USER_NAME_NOUN_CONTROLS)
def test_grammatical_user_name_nouns_keep_the_user_identity_lane(prompt: str) -> None:
    question = classify_identity_question(prompt)

    assert question.asks_user_identity, (prompt, question)
    assert not question.asks_assistant_identity
