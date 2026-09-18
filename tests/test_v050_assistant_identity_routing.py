"""An assistant/product identity question is answered locally, and never bought a research lane.

Live defect this covers: "what is your name?" was classified `research`, activated qwen3:14b and
took nearly three minutes to answer "VOOL" -- a string sitting in a local identity file the whole
time. The neighbouring turns were already correct ("hi" hit the smalltalk fast path with no model,
"what's my name?" and "yo say my name" were answered from saved settings), so the defect is
specifically the assistant's own name.

Three separate mechanisms had to be wrong at once, and each is asserted here on the real function
rather than a proxy:

* `core.task_router.classify` had no branch for the shape, so "what is your name?" fell to
  `_bare_lookup_marker_claims_this_turn` on the bare substring "what is" and became `research` --
  which `model_execution_profile` maps to `provider_role=queen` with `allow_paid_fallback=True`.
  The contraction "what's your name?" missed even that and landed `unknown`, and `unknown` is what
  sends `classify` to `_classify_via_model`: a model call to decide how to route the question;
* `IdentityQuestion.asks_assistant_identity` had no production consumer at all. Both runtime
  callers branch on `asks_user_identity`, so a correctly classified assistant question fell through
  to a full `run_once`;
* `_owner_of_name_word` resolved the possessor by priority-ordered set membership over a
  three-token window with self words checked first, so "tell me your name" -- where `me` is the
  verb's indirect object and `your` is the actual possessor -- was answered with the USER's name.

The suite is a semantic family plus negative controls plus a rename drive: a fix that only
satisfies the literal string "what is your name?" is the shape of defect being removed here, not a
pass.
"""

from __future__ import annotations

import pytest

import core.task_router as task_router_module
from core import onboarding, user_preferences
from core.context_namespace import ensure_chat_namespace
from core.local_inference_autopilot import _resolve_lane
from core.memory_first_router import resolve_fallback_budget_seconds
from core.reasoning_engine import explicit_planner_style_requested
from core.request_trust import OWNER_LOCAL_KEY
from core.task_router import classify, model_execution_profile
from core.user_identity_authority import (
    assistant_display_name,
    assistant_identity_answer,
    classify_identity_question,
    saved_user_name,
)
from core.web.api.runtime import RuntimeServices, _assistant_identity_response, run_agent

# The family from the defect report, plus the sloppy spellings a real user types. Every one of
# these asks what THIS assistant or app is called.
ASSISTANT_IDENTITY_FAMILY = (
    "what is your name?",
    "what's your name?",
    "who are you?",
    "what should I call you?",
    "are you VOOL?",
    "what is this assistant called?",
    "what is the app called?",
    "say your name",
    "tell me ur name",
    "yo bot name?",
    "what are you called?",
    "tell me your name",
    "wuts ur name",
    "what is this app called?",
)

# Must never be read as a question about the assistant's name.
NEGATIVE_CONTROLS = (
    # the user's own name -- the other identity, answered from saved settings
    "what's my name?",
    "say my name",
    "yo say my name!",
    "u know my name right?",
    # writes -- these belong to the preference path, never to a recall answer
    "call me Me Lord",
    "rename yourself to X",
    "set my name to X",
    # genuine lookups about the product as a subject in the world
    "what is VOOL?",
    "research VOOL company",
    # words from the family used for something else entirely
    "i like your name",
    "are you sure?",
    "what is the tool for reading files",
    "what is the name of the capital of France?",
    "name three databases",
    "hey",
)

# The user-identity side of the separation. These must stay `user` after the possessor fix.
USER_IDENTITY_CONTROLS = (
    "what's my name?",
    "say my name",
    "yo say my name!",
    "whats my nmae?",
    "say my nam pls",
    "u know my name right?",
    "say my profile name",
)

SAVED_NAME = "Alex"


@pytest.fixture()
def saved_settings_name():
    """A saved user name (in the Operator Profile, the one authority), cleared after. The identity
    answers must stay separable while one exists."""
    from tests.operator_profile_rig import set_owner_preferred_name

    set_owner_preferred_name(SAVED_NAME)
    try:
        yield SAVED_NAME
    finally:
        set_owner_preferred_name("")


@pytest.fixture()
def restored_agent_identity():
    """Undo an assistant rename. It persists to the session-wide runtime home, and leaking it
    changes the product name every later test reads."""
    from core.identity_manager import load_active_persona, update_local_persona

    path = onboarding._identity_path()
    original = path.read_text(encoding="utf-8") if path.exists() else None
    original_persona = load_active_persona("default").display_name
    try:
        yield
    finally:
        if original is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(original, encoding="utf-8")
        update_local_persona("default", display_name=original_persona)


class _RecordingAgent:
    """Stands in for the real agent so a model call is observable as a fact, not an inference."""

    class ResponseClass:
        GENERIC_CONVERSATION = "generic_conversation"

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.calls: list[str] = []

    def run_once(self, user_text, *, session_id_override=None, source_context=None):
        self.calls.append(user_text)
        return {"response": "model answer", "confidence": 0.4, "model_calls": 1}

    def _sanitize_user_chat_text(self, text: str, *, response_class):
        return text


def _route(text: str) -> dict[str, object]:
    """The real decisions, taken through the real functions rather than a mirror of them.

    Same shape as `tests/test_v050_trivial_tasks_stay_trivial.py` uses, because these are the three
    decisions that actually buy weight: `task_class` -> `model_execution_profile` (where `research`
    means `provider_role=queen` and a paid arm), the autopilot LANE (where `deep` is the heavyweight
    tier) and `resolve_fallback_budget_seconds` (where `None` is an unbounded fallback loop).
    """
    classification = classify(text, {"chat_surface": True})
    profile = model_execution_profile(
        classification["task_class"],
        chat_surface=True,
        planner_style_requested=explicit_planner_style_requested(text),
    )
    lane = _resolve_lane(
        user_text=text,
        task_kind=str(profile["task_kind"]),
        output_mode=str(profile["output_mode"]),
        source_context={},
        local_available=True,
        has_tiny_lane=True,
        has_deep_lane=True,
    )
    return {
        "task_class": classification["task_class"],
        "profile": profile,
        "lane": lane,
        "budget": resolve_fallback_budget_seconds(
            lane,
            forced_cpu=False,
            no_usable_gpu=False,
            output_mode=str(profile["output_mode"]),
        ),
    }


# --- classification and identity separation -----------------------------------------------------


@pytest.mark.parametrize("prompt", ASSISTANT_IDENTITY_FAMILY)
def test_every_phrasing_of_the_assistant_name_question_is_classified_as_the_assistant(prompt) -> None:
    question = classify_identity_question(prompt)

    assert question.asks_assistant_identity, f"{prompt!r} classified as {question.subject!r} ({question.reason})"
    assert not question.asks_user_identity


@pytest.mark.parametrize("prompt", NEGATIVE_CONTROLS)
def test_a_negative_control_is_never_claimed_as_an_assistant_identity_question(prompt) -> None:
    question = classify_identity_question(prompt)

    assert not question.asks_assistant_identity, f"{prompt!r} wrongly claimed as the assistant ({question.reason})"


@pytest.mark.parametrize("prompt", USER_IDENTITY_CONTROLS)
def test_the_users_own_name_question_survives_the_assistant_lane(prompt) -> None:
    """The fix must separate the two identities, not move the boundary onto the user's side."""
    question = classify_identity_question(prompt)

    assert question.asks_user_identity, f"{prompt!r} lost its user subject ({question.reason})"


def test_the_possessor_next_to_the_name_word_decides_whose_name_it_is() -> None:
    """"tell me your name" and "say me my name" put a self word and an assistant word in the same
    window. The possessor is the pronoun against the name word; the other is the verb's object."""
    assert classify_identity_question("tell me your name").asks_assistant_identity
    assert classify_identity_question("say me your name").asks_assistant_identity
    assert classify_identity_question("can you tell me your name").asks_assistant_identity

    assert classify_identity_question("tell me my name").asks_user_identity
    assert classify_identity_question("do you know my name").asks_user_identity


def test_a_name_word_without_recall_intent_is_not_a_question() -> None:
    """A compliment carries the same words as the question and must not be answered as one."""
    for statement in ("i like your name", "your name suits you", "my name rocks"):
        assert not classify_identity_question(statement).subject, statement


def test_a_yes_no_question_only_claims_the_assistant_when_it_names_one() -> None:
    assert classify_identity_question(f"are you {assistant_display_name()}?").asks_assistant_identity
    assert classify_identity_question("are you a bot?").asks_assistant_identity
    for decoy in ("are you sure?", "are you busy", "are you done", "are you there"):
        assert not classify_identity_question(decoy).asks_assistant_identity, decoy


# --- routing ------------------------------------------------------------------------------------


@pytest.mark.parametrize("prompt", ASSISTANT_IDENTITY_FAMILY)
def test_an_assistant_identity_turn_is_not_web_research(prompt) -> None:
    assert _route(prompt)["task_class"] != "research"


@pytest.mark.parametrize("prompt", ASSISTANT_IDENTITY_FAMILY)
def test_an_assistant_identity_turn_does_not_buy_the_queen_role_or_a_paid_arm(prompt) -> None:
    profile = _route(prompt)["profile"]

    assert profile["provider_role"] == "auto"
    assert profile["allow_paid_fallback"] is False


@pytest.mark.parametrize("prompt", ASSISTANT_IDENTITY_FAMILY)
def test_an_assistant_identity_turn_does_not_reach_the_deep_lane(prompt) -> None:
    """`deep` is the heavyweight model tier -- the one qwen3:14b is served from."""
    assert _route(prompt)["lane"] != "deep"


@pytest.mark.parametrize("prompt", ASSISTANT_IDENTITY_FAMILY)
def test_an_assistant_identity_turn_keeps_a_bounded_fallback_budget(prompt) -> None:
    """`None` is unbounded -- the sequential loop may then spend minutes across candidates."""
    budget = _route(prompt)["budget"]

    assert budget is not None, f"{prompt!r} bought an unbounded provider-fallback loop"
    assert float(budget) <= 180.0


@pytest.mark.parametrize("prompt", ASSISTANT_IDENTITY_FAMILY)
def test_routing_an_assistant_identity_turn_never_wakes_the_model_classifier(prompt, monkeypatch) -> None:
    """`unknown` is what sends `classify` to `_classify_via_model`, so a turn this cheap must be
    decided deterministically -- including the contraction that used to miss every branch."""

    def _explode(*_args, **_kwargs):
        raise AssertionError(f"{prompt!r} spent a model call deciding how to route itself")

    monkeypatch.setattr(task_router_module, "_classify_via_model", _explode)

    assert classify(prompt, {})["task_class"] == "chat_conversation"


@pytest.mark.parametrize(
    "prompt",
    (
        "research VOOL company",
        "find the latest btc price right now",
        "look up who founded Anthropic",
    ),
)
def test_a_real_lookup_still_routes_to_research(prompt) -> None:
    """The demotion must not swallow the lane it sits in front of."""
    assert _route(prompt)["task_class"] == "research"


# --- runtime turn -------------------------------------------------------------------------------


@pytest.mark.parametrize("prompt", ASSISTANT_IDENTITY_FAMILY)
def test_the_runtime_answers_the_whole_family_from_the_local_identity_registry(prompt) -> None:
    result = _assistant_identity_response(prompt)

    assert result is not None, f"{prompt!r} was not answered locally"
    assert assistant_display_name() in result["response"]
    assert result["identity_subject"] == "assistant"
    assert result["identity_name"] == assistant_display_name()
    assert result["source"] == "local_agent_identity"
    assert result["model_calls"] == 0
    assert result["web_calls"] == 0


@pytest.mark.parametrize("prompt", NEGATIVE_CONTROLS)
def test_the_local_assistant_answer_declines_every_negative_control(prompt) -> None:
    assert _assistant_identity_response(prompt) is None, f"{prompt!r} was answered as the assistant's name"


def test_the_assistant_answer_is_never_the_users_saved_name(saved_settings_name) -> None:
    """The two identities are separate values; answering one with the other is the original defect."""
    for prompt in ASSISTANT_IDENTITY_FAMILY:
        result = _assistant_identity_response(prompt)

        assert result is not None
        assert SAVED_NAME not in result["response"], f"{prompt!r} answered with the USER's name"


@pytest.mark.parametrize("prompt", ("what is your name?", "who are you?", "tell me ur name", "what is the app called?"))
def test_the_assistant_identity_turn_reaches_no_model_at_all(tmp_path, monkeypatch, prompt) -> None:
    """The end-to-end claim: the whole turn runs without waking a model or a cloud arm."""
    chat_id = f"assistant-identity-no-model-{abs(hash(prompt))}"
    agent = _RecordingAgent(chat_id)
    runtime = RuntimeServices(agent=agent, runtime_home=str(tmp_path))
    monkeypatch.setattr("core.web.api.runtime.schedule_memory_extraction", lambda *args, **kwargs: None)
    ensure_chat_namespace(chat_id, grant_current_receipts=False)

    result = run_agent(
        runtime,
        prompt,
        session_id=chat_id,
        source_context={
            "surface": "api",
            "platform": "api",
            "allow_remote_fetch": False,
            OWNER_LOCAL_KEY: True,
        },
        workspace_root_provider=lambda: str(tmp_path),
    )

    assert agent.calls == [], "the identity lookup woke a model"
    assert assistant_display_name() in result["response"]
    assert result.get("model_calls", 0) == 0
    assert result.get("web_calls", 0) == 0


def test_the_answer_follows_a_rename_instead_of_carrying_a_hard_coded_product_name(
    restored_agent_identity,
) -> None:
    """Anti-overfit: a literal "VOOL" in the answer path would pass every test above and still be
    wrong the moment the user renames the assistant. The name has to be READ, not written in."""
    onboarding.save_identity(agent_name="Atlas")

    assert assistant_display_name() == "Atlas"
    result = _assistant_identity_response("what is your name?")

    assert result is not None
    assert "Atlas" in result["response"]
    assert result["identity_name"] == "Atlas"
    assert "VOOL" not in result["response"]


def test_a_rename_request_is_not_answered_as_an_identity_question(
    saved_settings_name,
    restored_agent_identity,
) -> None:
    """A write must reach the preference path. Answering it from the identity registry would report
    the OLD name and silently drop the request."""
    assert _assistant_identity_response("rename yourself to Atlas") is None

    handled, reply = user_preferences.maybe_handle_preference_command("rename yourself to Atlas")

    assert handled
    assert "Atlas" in reply
    assert saved_user_name().name == SAVED_NAME, "renaming the assistant overwrote the user's name"


def test_the_product_question_is_answered_as_the_app_not_as_a_person() -> None:
    app_text, _name, _prov = assistant_identity_answer(classify_identity_question("what is the app called?"))
    self_text, _name, _prov = assistant_identity_answer(classify_identity_question("what is your name?"))

    assert "app" in app_text.lower()
    assert self_text.lower().startswith("my name is")


def test_the_assistant_identity_path_needs_no_chat_namespace_or_profile_grant() -> None:
    """The product name is not the user's private data. Gating it on the memory-recall
    preconditions -- a chat id, an active namespace, a profile grant -- is what pushed a group
    surface or a closed namespace back onto a model for a question this machine can already answer.
    """
    result = _assistant_identity_response("what is your name?")

    assert result is not None
    assert assistant_display_name() in result["response"]
