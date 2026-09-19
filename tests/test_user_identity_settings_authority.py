"""The user's saved name is answered from local settings, and is never the assistant's name.

Live defect this covers: with a name saved in Settings, "yo say my name!" was answered with the
ASSISTANT's name, "whats my name?" asked the user for a name they had already saved, and the
follow-up complaint escalated to a cloud model to answer a question whose answer is a field in a
local JSON file.

The suite is deliberately a family plus negative controls: a fix that only satisfies the exact
string "what's my name?" is the shape of defect being removed here, not a pass.
"""

from __future__ import annotations

import json

import pytest

import core.web.api.runtime as runtime_module
from core import user_preferences
from core.context_namespace import ensure_chat_namespace
from core.human_input_adapter import HumanInputInterpretation
from core.identity_manager import load_active_persona
from core.onboarding import get_agent_display_name
from core.prompt_normalizer import normalize_prompt
from core.request_trust import OWNER_LOCAL_KEY
from core.task_router import create_task_record
from core.tiered_context_loader import TieredContextLoader
from core.user_identity_authority import (
    SETTINGS_PROVENANCE,
    assistant_display_name,
    classify_identity_question,
    identity_context_lines,
    needs_prior_turns_to_classify,
    saved_user_name,
    user_identity_answer,
)
from core.vool_memory import VoolMemory
from core.web.api.runtime import RuntimeServices, _memory_recall_response, run_agent
from storage.dialogue_memory import record_dialogue_turn

# The exact family from the defect report, plus the sloppy variants a real user types.
CLEAN_USER_IDENTITY_PROMPTS = (
    "yo say my name!",
    "what's my name?",
    "what is my name?",
    "do you know my saved name?",
    "what name did I save in settings?",
    "call me by my name",
    "who am I in settings?",
    "you should know my name from settings",
    "i saved my name already",
    "say my profile name",
)
SLOPPY_USER_IDENTITY_PROMPTS = (
    "whats my nmae?",
    "say my nam pls",
    "u know my name right?",
    "what am i called in app?",
    "whats my name?",
)
USER_IDENTITY_PROMPTS = CLEAN_USER_IDENTITY_PROMPTS + SLOPPY_USER_IDENTITY_PROMPTS

ASSISTANT_IDENTITY_PROMPTS = (
    "what is your name?",
    "what is this app called?",
    "who are you?",
    "what are you called?",
)
# Messages that must never be read as a question about the user's stored name.
NON_IDENTITY_PROMPTS = (
    "rename yourself to Atlas",
    "my app is named Lumen",
    "remember my name is Rick",
    "my name is Rick",
    "call me Rick",
    "you can call me Rick",
    "set my name to Rick",
    "change my name in settings",
    "i need to change my name",
    "set your response style to terse",
    "the project codename is Orion",
    "i have a preference for dark mode in the ui",
    "what is the name of the capital of France?",
    "name three databases",
    "summarize the readme",
    "hey",
)

SAVED_NAME = "Alex"


@pytest.fixture()
def saved_settings_name():
    """Save a name in the Operator Profile (the one authority) for the duration of a test and
    clear it after.

    The pytest runtime home is shared across the session, so a leaked name would change what
    unrelated recall tests observe.
    """
    from tests.operator_profile_rig import set_owner_preferred_name

    set_owner_preferred_name(SAVED_NAME)
    try:
        yield SAVED_NAME
    finally:
        set_owner_preferred_name("")


@pytest.fixture()
def no_saved_settings_name():
    from tests.operator_profile_rig import set_owner_preferred_name

    set_owner_preferred_name("")
    try:
        yield
    finally:
        set_owner_preferred_name("")


@pytest.fixture()
def restored_agent_identity():
    """Undo an assistant rename. It persists to the session-wide runtime home, and leaking it
    changes the product name every later test reads."""
    from core import onboarding
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


# --- classification ---------------------------------------------------------------------------


@pytest.mark.parametrize("prompt", USER_IDENTITY_PROMPTS)
def test_every_phrasing_of_the_user_name_question_is_classified_as_the_users_identity(prompt) -> None:
    question = classify_identity_question(prompt)
    assert question.asks_user_identity, f"{prompt!r} classified as {question.subject!r} ({question.reason})"


@pytest.mark.parametrize("prompt", ASSISTANT_IDENTITY_PROMPTS)
def test_a_question_about_the_assistant_is_not_a_question_about_the_user(prompt) -> None:
    question = classify_identity_question(prompt)
    assert question.asks_assistant_identity
    assert not question.asks_user_identity


@pytest.mark.parametrize("prompt", NON_IDENTITY_PROMPTS)
def test_a_naming_statement_is_never_read_as_a_recall_question(prompt) -> None:
    question = classify_identity_question(prompt)
    assert not question.asks_user_identity, f"{prompt!r} wrongly claimed as user identity ({question.reason})"


def test_a_bare_pronoun_complaint_resolves_only_against_a_prior_identity_question() -> None:
    complaint = "i saved it already in settigns?!?! u dont see?!"

    # Standalone the subject is genuinely unknown -- it could be a key, a path, a model tag.
    assert not classify_identity_question(complaint).asks_user_identity
    assert (
        not classify_identity_question(
            complaint,
            recent_user_texts=("where do i paste my openai key?",),
        ).asks_user_identity
    )
    assert classify_identity_question(
        complaint,
        recent_user_texts=("whats my name?",),
    ).asks_user_identity


def test_only_a_pronoun_follow_up_needs_the_conversation_history() -> None:
    assert needs_prior_turns_to_classify("i saved it already in settigns?!?! u dont see?!")
    for standalone in USER_IDENTITY_PROMPTS + ASSISTANT_IDENTITY_PROMPTS + NON_IDENTITY_PROMPTS:
        assert not needs_prior_turns_to_classify(standalone), standalone


def test_an_ordinary_turn_never_reads_the_conversation_history_to_classify(
    saved_settings_name,
    tmp_path,
    monkeypatch,
) -> None:
    """The history read is a DB round trip on the hot path of every chat turn if it is unguarded."""
    reads: list[str] = []
    real = runtime_module._recent_user_texts
    monkeypatch.setattr(
        runtime_module,
        "_recent_user_texts",
        lambda chat_id, **kwargs: (reads.append(chat_id), real(chat_id, **kwargs))[1],
    )
    runtime = RuntimeServices(runtime_home=str(tmp_path))

    _memory_recall_response(
        runtime,
        user_text="what's the weather in Berlin?",
        source_context=_identity_context("identity-hot-path"),
    )
    assert reads == []

    _memory_recall_response(
        runtime,
        user_text="i saved it already in settigns?!?! u dont see?!",
        source_context=_identity_context("identity-hot-path"),
    )
    assert reads == ["identity-hot-path"]


def test_a_name_typo_is_tolerated_without_swallowing_ordinary_words() -> None:
    # Transpositions and deletions of "name" are typing slips; substitutions produce real English
    # words and must not trigger the identity lane.
    assert classify_identity_question("whats my naem").asks_user_identity
    assert classify_identity_question("say my nam").asks_user_identity
    for decoy in ("my same old question", "my game is chess", "the frame is my design", "my lame excuse"):
        assert not classify_identity_question(decoy).asks_user_identity, decoy


# --- stored value and answer ------------------------------------------------------------------


def test_the_saved_name_source_is_the_settings_preferences_file(saved_settings_name) -> None:
    saved = saved_user_name()

    assert saved.name == SAVED_NAME
    assert saved.known
    assert saved.provenance == SETTINGS_PROVENANCE

    # The JSON preferences file is no longer where the name lives: the Operator Profile is.
    path = user_preferences._prefs_path()
    stored = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    assert stored.get("user_address", "") == ""
    from core.operator_profile import OWNER_PRINCIPAL, resolve

    assert resolve(OWNER_PRINCIPAL, "preferred_name").value_text == SAVED_NAME


def test_the_answer_carries_the_saved_name_and_its_settings_provenance(saved_settings_name) -> None:
    text, name, provenance = user_identity_answer()

    assert name == SAVED_NAME
    assert provenance == SETTINGS_PROVENANCE
    assert SAVED_NAME in text
    assert assistant_display_name().lower() not in text.lower()


def test_settings_outrank_a_name_recovered_from_memory(saved_settings_name) -> None:
    _text, name, provenance = user_identity_answer(
        fallback_name="Rick",
        fallback_provenance="memory.user_profile.Name",
    )

    assert name == SAVED_NAME
    assert provenance == SETTINGS_PROVENANCE


def test_with_nothing_saved_the_answer_says_so_instead_of_naming_the_assistant(no_saved_settings_name) -> None:
    text, name, provenance = user_identity_answer()

    assert name == ""
    assert provenance == ""
    assert "remember" in text.lower() and "settings" in text.lower()
    assert assistant_display_name().lower() not in text.lower()


# --- prompt context ---------------------------------------------------------------------------


def test_prompt_context_states_both_names_and_which_is_whose(saved_settings_name) -> None:
    lines = identity_context_lines()
    blob = " ".join(lines)
    agent_name = get_agent_display_name()

    assert f'The USER\'s name is "{SAVED_NAME}"' in blob
    assert f'YOUR own name is "{agent_name}"' in blob
    # The separation has to be stated, not left to be inferred from two adjacent sentences.
    assert "It is NOT your name" in lines[0]
    assert "never the user's" in lines[1]
    assert "profile" in blob.lower()


def test_prompt_context_carries_only_the_identity_fields_not_the_rest_of_settings(saved_settings_name) -> None:
    prefs = user_preferences.load_preferences()
    prefs.email_signature = "Sent from my private vault"
    prefs.style_notes = "prefers terse bullet answers"
    prefs.daily_token_budget = 4242
    user_preferences.save_preferences(prefs)

    blob = " ".join(identity_context_lines())

    assert "private vault" not in blob
    assert "terse bullet" not in blob
    assert "4242" not in blob


def test_with_no_saved_name_the_prompt_forbids_answering_with_the_assistant_name(no_saved_settings_name) -> None:
    blob = " ".join(identity_context_lines())

    assert "The USER's name is not known" in blob
    assert "your own name is NOT theirs" in blob
    assert "Do NOT invent" in blob
    assert "never answer your own name" in blob


@pytest.mark.parametrize("prompt", ("yo say my name!", "whats my nmae?", "what name did I save in settings?"))
def test_the_assembled_chat_prompt_carries_the_saved_name(saved_settings_name, prompt) -> None:
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
    classification = {"task_class": "conversation", "risk_flags": [], "confidence_hint": 0.84}
    session_id = f"identity-prompt-{task.task_id}"
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
        task_kind="chat",
        trace_id=task.task_id,
        surface="openclaw",
        source_context={
            "surface": "openclaw",
            "platform": "openclaw",
            "chat_id": session_id,
            "runtime_session_id": session_id,
        },
    )

    blob = "\n".join(message.content for message in request.messages)
    assert f'The USER\'s name is "{SAVED_NAME}"' in blob
    # The budgeter trims item content from the end, so the separation must ride in the first line.
    assert "It is NOT your name" in blob


# --- runtime turn -----------------------------------------------------------------------------


def _identity_context(chat_id: str) -> dict:
    ensure_chat_namespace(chat_id, grant_current_receipts=False)
    return {
        "surface": "openclaw",
        "platform": "openclaw",
        "chat_id": chat_id,
        "runtime_session_id": chat_id,
        OWNER_LOCAL_KEY: True,
    }


@pytest.mark.parametrize("prompt", USER_IDENTITY_PROMPTS)
def test_the_runtime_answers_the_whole_prompt_family_from_settings(saved_settings_name, tmp_path, prompt) -> None:
    chat_id = f"identity-runtime-{abs(hash(prompt))}"
    runtime = RuntimeServices(runtime_home=str(tmp_path))

    result = _memory_recall_response(
        runtime,
        user_text=prompt,
        source_context=_identity_context(chat_id),
    )

    assert result is not None, f"{prompt!r} was not answered locally"
    assert SAVED_NAME in result["response"]
    assert result["identity_subject"] == "user"
    assert result["identity_name"] == SAVED_NAME
    assert result["identity_provenance"] == SETTINGS_PROVENANCE
    assert result["source"] == "local_user_settings"


@pytest.mark.parametrize("prompt", ASSISTANT_IDENTITY_PROMPTS)
def test_the_runtime_never_answers_an_assistant_question_with_the_users_name(
    saved_settings_name,
    tmp_path,
    prompt,
) -> None:
    chat_id = f"identity-assistant-{abs(hash(prompt))}"
    runtime = RuntimeServices(runtime_home=str(tmp_path))

    result = _memory_recall_response(
        runtime,
        user_text=prompt,
        source_context=_identity_context(chat_id),
    )

    if result is not None:
        assert result.get("identity_subject") != "user"
        assert SAVED_NAME not in result["response"]


def test_the_settings_answer_survives_an_unavailable_memory_subsystem(
    saved_settings_name,
    tmp_path,
    monkeypatch,
) -> None:
    # The saved name lives in settings; a memory failure must not turn it into "I don't know".
    def _explode(*_args, **_kwargs):
        raise RuntimeError("memory store unavailable")

    monkeypatch.setattr("core.vool_memory.VoolMemory", _explode)
    runtime = RuntimeServices(runtime_home=str(tmp_path))

    result = _memory_recall_response(
        runtime,
        user_text="whats my name?",
        source_context=_identity_context("identity-memory-down"),
    )

    assert result is not None
    assert result["identity_name"] == SAVED_NAME
    assert result["source"] == "local_user_settings"


def test_a_memory_stored_name_still_answers_when_settings_are_empty(
    no_saved_settings_name,
    tmp_path,
) -> None:
    """The Operator Profile is the ONE name authority: a harvested memory-block "Name:" line is
    no longer a fallback for it (that block leaked a chat-only, unconfirmed name into every
    chat's recall). With the profile empty the runtime says so rather than answer from the block."""
    chat_id = "identity-memory-fallback"
    memory = VoolMemory(runtime_home=tmp_path)
    memory.block_write("user_profile", "Name: Loop")
    memory.close()
    runtime = RuntimeServices(runtime_home=str(tmp_path))

    result = _memory_recall_response(
        runtime,
        user_text="say my nam pls",
        source_context=_identity_context(chat_id),
    )

    assert result is not None
    assert result["identity_name"] == ""
    assert "Loop" not in str(result["response"])


def test_with_nothing_stored_the_runtime_says_no_name_is_saved_rather_than_guessing(
    no_saved_settings_name,
    tmp_path,
) -> None:
    runtime = RuntimeServices(runtime_home=str(tmp_path))

    result = _memory_recall_response(
        runtime,
        user_text="yo say my name!",
        source_context=_identity_context("identity-empty"),
    )

    assert result is not None
    assert result["identity_name"] == ""
    assert "settings" in result["response"].lower()
    assert get_agent_display_name().lower() not in result["response"].lower()


def test_the_pronoun_follow_up_is_answered_from_the_recorded_prior_turn(saved_settings_name, tmp_path) -> None:
    chat_id = "identity-followup"
    context = _identity_context(chat_id)
    record_dialogue_turn(
        chat_id,
        raw_input="whats my name?",
        normalized_input="whats my name?",
        reconstructed_input="whats my name?",
        speaker_role="user",
        topic_hints=[],
        reference_targets=[],
        understanding_confidence=0.8,
        quality_flags=[],
    )
    runtime = RuntimeServices(runtime_home=str(tmp_path))

    result = _memory_recall_response(
        runtime,
        user_text="i saved it already in settigns?!?! u dont see?!",
        source_context=context,
    )

    assert result is not None
    assert result["identity_intent"] == "anaphoric_saved_name_followup"
    assert SAVED_NAME in result["response"]


@pytest.mark.parametrize("prompt", ("yo say my name!", "whats my name?", "what am i called in app?"))
def test_the_identity_turn_reaches_no_model_at_all(saved_settings_name, tmp_path, monkeypatch, prompt) -> None:
    """No model call means no local generation and no cloud escalation for a settings lookup."""
    chat_id = f"identity-no-model-{abs(hash(prompt))}"
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

    assert agent.calls == [], "the settings lookup woke a model"
    assert SAVED_NAME in result["response"]
    assert result.get("model_calls", 0) == 0


def test_a_rename_command_never_touches_the_users_saved_name(
    saved_settings_name,
    restored_agent_identity,
) -> None:
    handled, reply = user_preferences.maybe_handle_preference_command("rename yourself to Atlas")

    assert handled
    assert "Atlas" in reply
    assert saved_user_name().name == SAVED_NAME, "renaming the assistant overwrote the user's name"


def test_setting_the_user_name_goes_through_the_operator_profile_lane(no_saved_settings_name) -> None:
    """"call me X" is Operator Profile business now: the preference-command surface no longer
    writes a name silently, and the profile lane's explicit form is what lands it."""
    assert not saved_user_name().known

    handled, _reply = user_preferences.maybe_handle_preference_command("call me Rick")
    assert handled is False
    assert not saved_user_name().known

    from core.operator_profile import OWNER_PRINCIPAL, remember
    from core.operator_profile_interpretation import interpret_profile_turn

    proposal = interpret_profile_turn("remember to call me Rick")[0]
    assert proposal.strength == "explicit" and proposal.category == "preferred_name"
    change = remember(OWNER_PRINCIPAL, proposal.category, proposal.value, origin="explicit")
    assert change.kind == "saved" and "Rick" in change.report
    assert saved_user_name().name == "Rick"
    assert saved_user_name().provenance == SETTINGS_PROVENANCE
