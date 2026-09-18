"""A deterministic lane may not answer a question about THIS CHAT out of a settings store.

Measured 2026-08-17 against the shipped app: one 50-turn conversation, one session, the whole
history sent on every turn. Facts were planted in turns 1-3 and asked back later. Three of the
failures had ``model_ran=False`` -- a lane took the turn and answered it without a model at all:

======  ===================================================  ==========================  =========================================
turn    question                                             lane                        what the user got
======  ===================================================  ==========================  =========================================
C06     "What is my project budget?"                         workspace_identity_fast_path a filesystem path to the .app bundle
C20     "What is my name and what city do I live in?"        user identity (settings)     "Add it in Settings under Your name"
C49     "What was the very first thing I asked you to        memory_command               "I don't have an active remembered
        remember?"                                                                        value matching that request in this chat"
======  ===================================================  ==========================  =========================================

The budget was stated in turn 2. The name was stated in turn 1, nineteen turns earlier. The
conversation was present and sufficient throughout: turn 48 asked for all six planted facts at once,
reached a model, and recited name, city, project and budget correctly out of the transcript.

So none of the three is a memory failure. Each is a ROUTING failure with the same shape: a lane
whose only source of truth is a persistent store claimed a turn whose answer was in the
conversation, and then reported its own store's silence as the user's.

Every assertion below is on WHICH LANE CLAIMS THE TURN -- never on prose a model produced. A lane
that declines has done its job; what the model then says about the transcript is the model's
business and is measured elsewhere.

The MUST-KEEP corpora were written before the fixes, from what each lane exists for, so that
"decline more" could not quietly become "decline everything".
"""
from __future__ import annotations

import pytest

# =================================================================================================
# Lane 1 -- the workspace identity fast path
# =================================================================================================
#
# It exists to answer "which folder is this chat bound to?" in one line, because the nearest thing
# that fired was the folder-overview reader and a one-line metadata question was being answered
# with a 46-line directory dump. See tests/test_workspace_identity_fast_path.py.
#
# It holds exactly one value: the resolved workspace path.
from core.agent_runtime.fast_paths_utility import (
    _WORKSPACE_IDENTITY_RE,
    _workspace_noun_is_the_subject,
    maybe_handle_workspace_identity_request,
)
from core.context_namespace import ensure_chat_namespace

# What the lane is FOR. Every one of these asks which workspace, and the folder path is the answer.
WORKSPACE_MUST_KEEP = (
    # verbatim from the driven matrix this lane was built against
    "what folder is in use for this workspace?",
    "do you see what folder our workspace is set on?",
    "tell me which folder this chat is bound to",
    "show me the workspace folder",
    "confirm the workspace path",
    "whats the current workspace",
    "am i in the right workspace?",
    "which project am i working in",
    "name the folder you are scoped to",
    "where am i",
    "what directory are we in",
    "whats my workspace",
    # the bare-noun forms: the workspace noun IS the question
    "what is my project?",
    "which project is this?",
    "what folder am i in?",
    "what is the current workspace?",
    "what workspace are we in?",
    "what is my project called?",
    "what is my project name?",
    "which project folder is this?",
    "what is the workspace path?",
    "what folder does this chat use?",
)

# The workspace noun is a MODIFIER here. The question is about the budget, the deadline, the team --
# subjects this lane has never held a value for and cannot get one for.
WORKSPACE_MUST_DECLINE = (
    "What is my project budget?",            # C06, measured
    "What is my project budget in euros?",
    "what is my project deadline?",
    "what is my project timeline?",
    "which project budget did i give you?",
    "what is my project manager called?",
    "what is my project team size?",
    "what is the project start date?",
    "what is my folder password?",
    "what is my workspace budget?",
)


class _Agent:
    def _fast_path_result(self, **kwargs):
        return {"reason": kwargs.get("reason"), "response": kwargs.get("response")}


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")
    return str(tmp_path)


def _workspace_lane(text: str, workspace: str):
    return maybe_handle_workspace_identity_request(
        _Agent(),
        text,
        session_id="ctxmem",
        source_surface="api",
        source_context={"workspace": workspace, "project_id": "p", "surface": "api"},
    )


@pytest.mark.parametrize("prompt", WORKSPACE_MUST_KEEP)
def test_the_workspace_lane_still_claims_the_questions_it_exists_for(prompt, workspace) -> None:
    result = _workspace_lane(prompt, workspace)

    assert result is not None, f"the folder question fell through to a model: {prompt!r}"
    assert result["reason"] == "workspace_identity_fast_path"
    assert workspace in result["response"]


@pytest.mark.parametrize("prompt", WORKSPACE_MUST_DECLINE)
def test_the_workspace_lane_declines_a_question_about_something_else(prompt, workspace) -> None:
    """The measured failure: a BUDGET question answered with a path, in 0s, with no model run."""

    assert _workspace_lane(prompt, workspace) is None, (
        f"the workspace lane claimed {prompt!r} and can only answer it with a folder path"
    )


def test_the_regex_alone_does_not_separate_them_the_tail_check_does() -> None:
    """Names the mechanism, so a later edit cannot delete the guard and stay green by accident."""

    claimed = "What is my project budget?"
    kept = "What is my project?"

    assert _WORKSPACE_IDENTITY_RE.search(claimed), "the regex matched before the fix and still does"
    assert _WORKSPACE_IDENTITY_RE.search(kept)

    assert _workspace_noun_is_the_subject(kept, _WORKSPACE_IDENTITY_RE.search(kept)) is True
    assert _workspace_noun_is_the_subject(claimed, _WORKSPACE_IDENTITY_RE.search(claimed)) is False


def test_SABOTAGE_reverting_the_workspace_tail_check_answers_the_budget_with_a_path(
    monkeypatch,
    workspace,
) -> None:
    """Revert the fix: `_workspace_noun_is_the_subject` always True, which is the pre-fix code.

    The case that dies is
    `test_the_workspace_lane_declines_a_question_about_something_else[What is my project budget?]`
    -- C06 exactly. The must-keep corpus above is unaffected by the revert, which is the point: the
    guard only removes claims, so a green must-keep run is not evidence the guard is present.
    """
    monkeypatch.setattr(
        "core.agent_runtime.fast_paths_utility._workspace_noun_is_the_subject",
        lambda text, match: True,
    )

    result = _workspace_lane("What is my project budget?", workspace)

    assert result is not None, "sabotage did not restore the defect"
    assert result["reason"] == "workspace_identity_fast_path"
    assert workspace in result["response"], (
        "pre-fix behaviour: a question about a budget is answered with the folder path"
    )


# =================================================================================================
# Lane 2 -- the user identity lane, answering from settings
# =================================================================================================
#
# It exists so that "what's my name?" is answered from the name the user saved in Settings instead
# of costing a 14B local model and a cloud escalation to produce a string sitting in a local file,
# and so a typo'd or unusual phrasing does not get asked for a name the app already holds.
#
# It reads settings and durable memory. It does not read the conversation.

from core.request_trust import OWNER_LOCAL_KEY
from core.web.api.runtime import (
    RuntimeServices,
    _chat_already_holds_a_name,
    _memory_recall_response,
)
from storage.dialogue_memory import record_dialogue_turn

SAVED_NAME = "Alex"

# What the lane is FOR: a saved name, asked for in the wordings people actually type.
IDENTITY_MUST_KEEP = (
    "what is my name",
    "what's my name?",
    "whats my name",
    "who am i",
    "what do you call me?",
    "say my name",
)


@pytest.fixture()
def saved_settings_name():
    """A saved name in the Operator Profile (the one authority) for the duration of a test."""
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


def _identity_context(chat_id: str) -> dict:
    ensure_chat_namespace(chat_id, grant_current_receipts=False)
    return {
        "surface": "openclaw",
        "platform": "openclaw",
        "chat_id": chat_id,
        "runtime_session_id": chat_id,
        OWNER_LOCAL_KEY: True,
    }


def _say(chat_id: str, text: str) -> None:
    record_dialogue_turn(
        chat_id,
        raw_input=text,
        normalized_input=text,
        reconstructed_input=text,
        speaker_role="user",
        topic_hints=[],
        reference_targets=[],
        understanding_confidence=1.0,
        quality_flags=[],
    )


@pytest.mark.parametrize("prompt", IDENTITY_MUST_KEEP)
def test_the_identity_lane_still_answers_from_a_saved_name(
    saved_settings_name,
    tmp_path,
    prompt,
) -> None:
    chat_id = f"ctxmem-identity-saved-{abs(hash(prompt))}"
    result = _memory_recall_response(
        RuntimeServices(runtime_home=str(tmp_path)),
        user_text=prompt,
        source_context=_identity_context(chat_id),
    )

    assert result is not None, f"a saved name was not answered locally: {prompt!r}"
    assert result["identity_subject"] == "user"
    assert result["identity_name"] == SAVED_NAME


def test_the_settings_guidance_survives_in_the_chat_it_was_written_for(
    no_saved_settings_name,
    tmp_path,
) -> None:
    """Nothing stored AND nothing said: pointing at Settings is the right answer, and stays."""

    chat_id = "ctxmem-identity-never-said"
    context = _identity_context(chat_id)
    _say(chat_id, "how do i list files in a folder?")
    _say(chat_id, "and how do i delete one?")

    result = _memory_recall_response(
        RuntimeServices(runtime_home=str(tmp_path)),
        user_text="what is my name?",
        source_context=context,
    )

    assert result is not None, "an empty store with an empty transcript still has guidance to give"
    assert result["identity_subject"] == "user"
    assert result["identity_name"] == ""


def test_the_identity_lane_declines_when_the_user_gave_their_name_in_this_chat(
    no_saved_settings_name,
    tmp_path,
) -> None:
    """C20, reconstructed: the name was given in turn 1 and asked for again nineteen turns later."""

    chat_id = "ctxmem-identity-said-in-chat"
    context = _identity_context(chat_id)
    _say(chat_id, "My name is Alex and I live in Berlin.")
    for filler in range(18):
        _say(chat_id, f"filler question {filler} about an unrelated topic")

    result = _memory_recall_response(
        RuntimeServices(runtime_home=str(tmp_path)),
        user_text="What is my name and what city do I live in?",
        source_context=context,
    )

    assert result is None, (
        "the lane answered 'I don't have a name saved for you' about a name the user "
        "stated in this chat, with model_calls=0"
    )


def test_the_lookback_reaches_further_than_the_pronoun_window(
    no_saved_settings_name,
    tmp_path,
) -> None:
    """The measured gap was 19 turns; the pre-existing history read stopped at 4."""

    chat_id = "ctxmem-identity-far-back"
    context = _identity_context(chat_id)
    _say(chat_id, "my name is Alex")
    for filler in range(40):
        _say(chat_id, f"unrelated turn {filler}")

    assert _chat_already_holds_a_name(chat_id) is True
    assert (
        _memory_recall_response(
            RuntimeServices(runtime_home=str(tmp_path)),
            user_text="what is my name?",
            source_context=context,
        )
        is None
    )


def test_SABOTAGE_reverting_the_transcript_check_tells_the_user_to_open_settings(
    monkeypatch,
    no_saved_settings_name,
    tmp_path,
) -> None:
    """Revert the fix: `_chat_already_holds_a_name` always False, which is the pre-fix code.

    The cases that die are
    `test_the_identity_lane_declines_when_the_user_gave_their_name_in_this_chat` (C20 itself) and
    `test_the_lookback_reaches_further_than_the_pronoun_window`.
    `test_the_settings_guidance_survives_in_the_chat_it_was_written_for` stays green under the
    revert -- it is the must-keep control and is deliberately blind to this guard.
    """
    monkeypatch.setattr("core.web.api.runtime._chat_already_holds_a_name", lambda chat_id: False)

    chat_id = "ctxmem-identity-sabotage"
    context = _identity_context(chat_id)
    _say(chat_id, "My name is Alex and I live in Berlin.")

    result = _memory_recall_response(
        RuntimeServices(runtime_home=str(tmp_path)),
        user_text="What is my name and what city do I live in?",
        source_context=context,
    )

    assert result is not None, "sabotage did not restore the defect"
    assert result["identity_subject"] == "user"
    assert result["identity_name"] == "", (
        "pre-fix behaviour: the lane claims the turn and reports no name it can see"
    )


# =================================================================================================
# Lane 3 -- the memory_command recall lane
# =================================================================================================
#
# It exists so that a typed memory slot -- "remember this exact identifier: INSTALL-MEM-A8AA-9921"
# and later "what is the current exact identifier?" -- is answered from the store deterministically,
# including reporting an absence after the user has explicitly forgotten a value, so that chat
# history cannot resurrect it. See tests/test_persistent_memory_and_preferences.py.
#
# It reads the durable memory store. It does not read the conversation.

from core.persistent_memory import (
    _EMPTY_RECALL_ANSWERS,
    _asks_about_transcript_order,
    ensure_memory_files,
    maybe_handle_memory_command,
)

# What the lane is FOR: inventory and typed-slot questions, including the empty answers, which are
# the whole reason the lane can speak about an absence at all.
MEMORY_MUST_KEEP_INVENTORY = (
    "what do you remember",
    "/memory",
    "show memory",
)
MEMORY_MUST_KEEP_EMPTY_IS_THE_ANSWER = (
    "What is the current exact identifier?",
    "What exact identifier is currently remembered in this chat?",
    "What is my current exact installer verification identifier?",
    "This is a completely fresh conversation. What identifiers have I asked you to remember in this chat?",
    "What facts are still active in this chat?",
    "Do you have the marker from another chat?",
)

# Questions about the CONVERSATION that this lane DOES claim. Every entry was checked against
# `_is_memory_recall_question` first: a phrasing the lane never wanted is not evidence of anything,
# and a must-decline corpus full of them passes without the fix being present at all.
MEMORY_MUST_DECLINE = (
    "What was the very first thing I asked you to remember?",   # C49, measured
    "What was the first identifier I asked you to remember?",
    "What did I save about my cat?",
    "What have I asked you to remember about my cat?",
    "Do you remember my cat name?",
    "What note did I leave about my colour?",
    "What is stored about my cat?",
)

# C10 and C21 from the same measurement. Both reached a model (`model_ran=True`), so neither is a
# lane defect -- they are defect B, below. They are pinned here because the fixes above must not
# make any lane start claiming them on the way past.
NO_LANE_MAY_CLAIM = (
    "What is my cat called?",
    "What is my favourite colour?",
    "What is my project budget?",
    "Which city did I say I live in?",
)


def _memory_lane(text: str, chat_id: str) -> tuple[bool, str]:
    return maybe_handle_memory_command(text, session_id=chat_id)


@pytest.fixture()
def stocked_chat():
    """A chat whose memory store is NOT empty -- the condition the old guard could not see past."""

    chat_id = "ctxmem-memory-stocked"
    ensure_chat_namespace(chat_id)
    ensure_memory_files()
    claimed, _ = _memory_lane(
        "Remember this exact identifier for this chat: INSTALL-MEM-C7B2-4410.",
        chat_id,
    )
    assert claimed is True, "fixture precondition: the store has to hold a row"
    return chat_id


@pytest.mark.parametrize("prompt", MEMORY_MUST_KEEP_INVENTORY)
def test_the_memory_lane_still_claims_an_inventory_request(prompt, stocked_chat) -> None:
    claimed, response = _memory_lane(prompt, stocked_chat)

    assert claimed is True, f"an inventory request fell through to a model: {prompt!r}"
    assert response.strip()


@pytest.mark.parametrize("prompt", MEMORY_MUST_KEEP_EMPTY_IS_THE_ANSWER)
def test_the_memory_lane_still_reports_an_absence_when_asked_for_an_inventory(prompt) -> None:
    """After an explicit forget, "there is no such value" IS the answer, and must survive."""

    chat_id = f"ctxmem-memory-absent-{abs(hash(prompt))}"
    ensure_chat_namespace(chat_id)
    ensure_memory_files()

    claimed, _response = _memory_lane(prompt, chat_id)

    assert claimed is True, f"the lane stopped answering a slot question it owns: {prompt!r}"


def test_the_forget_then_ask_sequence_still_ends_in_an_absence(stocked_chat) -> None:
    """The exact must-keep sequence from the pre-existing suite, driven end to end."""

    claimed, response = _memory_lane(
        "Forget the exact identifier I asked you to remember in this chat.",
        stocked_chat,
    )
    assert claimed is True
    assert "Removed" in response

    claimed, response = _memory_lane("What is the current exact identifier?", stocked_chat)

    assert claimed is True, "an explicitly forgotten slot must not fall back to chat history"
    assert response in _EMPTY_RECALL_ANSWERS
    assert "INSTALL-MEM-C7B2" not in response


@pytest.mark.parametrize("prompt", MEMORY_MUST_DECLINE)
def test_the_memory_lane_declines_a_question_about_the_conversation(prompt, stocked_chat) -> None:
    """A NON-EMPTY store is the case the old guard let through, so the fixture stocks one."""

    claimed, response = _memory_lane(prompt, stocked_chat)

    assert claimed is False, (
        f"the lane claimed {prompt!r} and answered {response!r} with no model run, "
        "about something that was said in the conversation and never stored"
    )


@pytest.mark.parametrize("prompt", NO_LANE_MAY_CLAIM)
def test_no_deterministic_lane_claims_a_plain_recall_question(prompt, stocked_chat, workspace) -> None:
    """These already reached a model in the measurement. They must keep reaching one."""

    claimed, _response = _memory_lane(prompt, stocked_chat)
    assert claimed is False, f"the memory lane started claiming {prompt!r}"
    assert _workspace_lane(prompt, workspace) is None, f"the workspace lane claimed {prompt!r}"


def test_an_ordinal_question_is_declined_before_the_store_is_even_consulted() -> None:
    assert _asks_about_transcript_order("What was the very first thing I asked you to remember?")
    assert _asks_about_transcript_order("what did i say at the beginning of this chat")
    assert not _asks_about_transcript_order("What is the current exact identifier?")
    assert not _asks_about_transcript_order("what do you remember")


def test_SABOTAGE_reverting_the_empty_answer_guard_asserts_the_chat_never_said_it(
    monkeypatch,
    stocked_chat,
) -> None:
    """Revert half the fix: the lane no longer recognises its own empty answers.

    That is the pre-fix condition for a NON-EMPTY store -- the old guard declined only when the
    store held no row at all, which is why stocking one row was enough to bring the defect back.

    The cases that die are
    `test_the_memory_lane_declines_a_question_about_the_conversation[What did I save about my cat?]`
    and the `[What have I asked you to remember about my cat?]`, `[Do you remember my cat name?]`,
    `[What note did I leave about my colour?]` and `[What is stored about my cat?]`
    parametrisations alongside it.

    C49 itself does NOT die under this arm -- the ordinal guard holds it, and that guard is
    sabotaged separately below. Two arms because the two guards overlap on C49: reverting either
    one alone leaves it declined, which is exactly how a single-arm sabotage would have reported a
    load-bearing fix that was not.
    """
    monkeypatch.setattr("core.persistent_memory._EMPTY_RECALL_ANSWERS", frozenset())

    claimed, response = _memory_lane("What did I save about my cat?", stocked_chat)

    assert claimed is True, "sabotage did not restore the defect"
    assert "don't have an active remembered value" in response, (
        "pre-fix behaviour: the store's silence is reported as the chat's"
    )


def test_SABOTAGE_reverting_the_ordinal_guard_answers_first_from_a_relevance_ranked_row(
    monkeypatch,
    stocked_chat,
) -> None:
    """Revert the other half: ordinal questions go back to the store.

    The case that dies is
    `test_the_memory_lane_declines_a_question_about_the_conversation[What was the first identifier
    I asked you to remember?]`.

    That phrasing rather than C49's, deliberately: the stocked identifier row MATCHES it, so the
    lane returns a confident value and the empty-answer guard never sees the turn. It isolates the
    ordinal guard, and it shows the sharper half of the defect -- the lane's lookup is
    relevance-ranked and carries no ordering, so "the first identifier" is answered with whichever
    row scored best, presented as fact.
    """
    monkeypatch.setattr("core.persistent_memory._asks_about_transcript_order", lambda text: False)

    claimed, response = _memory_lane(
        "What was the first identifier I asked you to remember?",
        stocked_chat,
    )

    assert claimed is True, "sabotage did not restore the defect"
    assert "INSTALL-MEM-C7B2-4410" in response, (
        "pre-fix behaviour: an ordinal question answered from an unordered lookup"
    )


# =================================================================================================
# Defect B -- what the model is actually given, and what it is not
# =================================================================================================
#
# NOT FIXED. These tests pin the measured behaviour so the root cause is reproducible and so a later
# change to context-window policy is a deliberate, visible act rather than a silent one. See the
# report for the proposal.
#
# The measurement: at turn 50 the model recalled turn-1 and turn-2 facts (name, city, project,
# budget) perfectly and reported turn-3 facts (cat, colour) as "not mentioned". Newer facts lost
# while older survived rules out distance decay. What is asserted here is where the boundary
# actually sits and what carries anything across it.

from core import conversation_summarizer as conversation_summarizer_module
from core.bootstrap_context import _compress_history
from core.context_history_authority import (
    EXPANDED_HISTORY_MESSAGES,
    HISTORY_MAX_CHARS,
    enforce_history_budget,
)

PLANTED_TURNS = {
    1: ("My name is Alex and I live in Berlin.", "Noted, Alex."),
    2: ("My project is called VOOL and my project budget is 4200 euros.", "Got it."),
    3: ("My favourite colour is teal and my cat is called Mira.", "Understood."),
}
PLANTED_FACTS = ("Alex", "Berlin", "VOOL", "4200", "teal", "Mira")


def _fifty_turn_history(turns: int = 49) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for turn in range(1, turns + 1):
        if turn in PLANTED_TURNS:
            user, assistant = PLANTED_TURNS[turn]
        else:
            user = f"Filler question number {turn}: explain topic {turn} briefly."
            assistant = f"Answer for topic {turn}. " + f"Detail line for turn {turn}. " * 6
        items.append({"role": "user", "content": user})
        items.append({"role": "assistant", "content": assistant})
    return items


def _blob(messages: list[dict[str, str]]) -> str:
    return "\n".join(str(message.get("content") or "") for message in messages)


def _surviving_turn_numbers(messages: list[dict[str, str]]) -> list[int]:
    text = _blob(messages)
    return [turn for turn in range(1, 60) if f"Answer for topic {turn}." in text]


def test_verbatim_history_keeps_only_the_newest_exchanges_and_drops_the_oldest_first() -> None:
    """Not head-and-tail. The envelope drops the OLDEST unit first, so the head goes first."""

    assembled = enforce_history_budget(
        _fifty_turn_history(),
        max_messages=EXPANDED_HISTORY_MESSAGES,
        max_chars=HISTORY_MAX_CHARS,
    )

    assert len(assembled) <= EXPANDED_HISTORY_MESSAGES
    surviving = _surviving_turn_numbers(assembled)
    assert surviving, "some tail must survive"
    assert min(surviving) > 40, (
        f"only the newest exchanges survive verbatim; surviving turns were {surviving}"
    )
    for fact in PLANTED_FACTS:
        assert fact not in _blob(assembled), (
            f"{fact!r} was planted in turn 1-3 and cannot reach the model verbatim"
        )


def test_the_context_summary_is_the_only_carrier_for_anything_older() -> None:
    """With a summariser that keeps everything, every planted fact crosses. It is the sole route."""

    original_call = conversation_summarizer_module._call_ollama
    original_pick = conversation_summarizer_module._pick_model_uncached

    def _faithful(model, messages, timeout=60):
        text = " ".join(str(message.get("content") or "") for message in messages)
        return "Earlier the user stated: " + ", ".join(
            fact for fact in PLANTED_FACTS if fact in text
        )

    conversation_summarizer_module._call_ollama = _faithful
    conversation_summarizer_module._pick_model_uncached = lambda: "stub-summariser"
    conversation_summarizer_module.reset_summary_cache()
    try:
        assembled = _compress_history(
            _fifty_turn_history(),
            max_messages=EXPANDED_HISTORY_MESSAGES,
            max_chars=HISTORY_MAX_CHARS,
            session_id="ctxmem-defect-b",
        )
    finally:
        conversation_summarizer_module._call_ollama = original_call
        conversation_summarizer_module._pick_model_uncached = original_pick
        conversation_summarizer_module.reset_summary_cache()

    text = _blob(assembled)
    assert "<context_summary>" in text
    for fact in PLANTED_FACTS:
        assert fact in text, f"{fact!r} crossed only inside the summary"
    assert min(_surviving_turn_numbers(assembled)) > 40, (
        "and nothing older reached the model any other way"
    )


@pytest.mark.xfail(
    reason=(
        "DEFERRED defect B. The compacted stand-in for the whole dropped middle carries no "
        "retention priority, so when it is larger than the char budget enforce_history_budget "
        "removes it FIRST -- it is the oldest unit. Everything before the last four exchanges is "
        "then gone with no trace. core/prompt_budget.py already sheds <context_summary> LAST at "
        "the token layer; core/context_history_authority.py does not agree with it. Fixing this "
        "changes which turns survive on every chat turn on every model, so it is not being landed "
        "inside a routing fix."
    ),
    strict=True,
)
def test_the_summary_outranks_verbatim_history_in_the_envelope() -> None:
    original_pick = conversation_summarizer_module._pick_model_uncached
    conversation_summarizer_module._pick_model_uncached = lambda: ""  # force the shipped fallback
    conversation_summarizer_module.reset_summary_cache()
    try:
        assembled = _compress_history(
            _fifty_turn_history(),
            max_messages=EXPANDED_HISTORY_MESSAGES,
            max_chars=HISTORY_MAX_CHARS,
            session_id="ctxmem-defect-b-fallback",
        )
    finally:
        conversation_summarizer_module._pick_model_uncached = original_pick
        conversation_summarizer_module.reset_summary_cache()

    assert "<context_summary>" in _blob(assembled), (
        "the summary is the only record of turns 1-40 and must not be the first thing shed"
    )
