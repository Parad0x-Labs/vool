"""A hypothetical reasoning turn is not a request for the user's saved name, and buys no lookup.

The live defect, measured on base 03b04b39. Asked::

    Assume today is January 1st, 2035. The US President is a golden retriever named Buster. The
    currency of France is the 'Baguette'. Based ONLY on these new facts, if I travel from Paris to
    Washington D.C. to sell a toy to the President, what currency will I be paid in, and who am I
    meeting? After answering, explicitly explain why your internal web search tools would fail to
    verify this transaction.

VOOL answered::

    Your name is Bender — that's the name saved in your settings.

Three mechanisms were wrong at once and each is asserted here on the real function, not a proxy:

* `core.user_identity_authority.classify_identity_question` matched an UNANCHORED
  `\\bwho\\s+(?:am|m)\\s+i\\b`, so "…and who am I meeting?" — a clause with a complement, inside a
  60-word prompt — returned `subject="user"`, and `core.web.api.runtime._memory_recall_response`
  answered it out of `data/user_preferences.json`;
* `core.web.api.runtime._looks_like_private_memory_recall` carried the same defect a second time,
  independently: `"who am i"` sat in `profile_markers` as a bare substring, so even with the
  classifier declining, the turn still reached the recall path and would have answered with the
  stored profile;
* `core.task_router.classify` claimed the turn as `research` at `looks_like_explicit_lookup_request`
  on the substring pair "search" + "web" — taken from the clause asking the runtime to explain why
  searching would NOT work. `research` means `provider_role=queen`, `allow_paid_fallback=True`, and
  `core.execution.planner.should_attempt_tool_intent` returning True, i.e. a web-search catalog
  handed to a turn so it could check whether a golden retriever is president.

The suite is a semantic family plus negative controls plus an anti-overfit matrix: a fix that only
satisfies the literal live prompt is the shape of defect being removed here, not a pass.
"""

from __future__ import annotations

import pytest

from core import onboarding
from core.bootstrap_context import build_bootstrap_context
from core.context_namespace import ensure_chat_namespace
from core.execution.planner import should_attempt_tool_intent
from core.human_input_adapter import adapt_user_input
from core.hypothetical_frame import detect_hypothetical_frame, hypothetical_frame_context_lines
from core.identity_manager import load_active_persona
from core.local_inference_autopilot import _resolve_lane
from core.memory_first_router import resolve_fallback_budget_seconds
from core.reasoning_engine import explicit_planner_style_requested
from core.request_trust import OWNER_LOCAL_KEY
from core.task_router import (
    chat_surface_execution_task_class,
    classify,
    create_task_record,
    model_execution_profile,
)
from core.user_identity_authority import assistant_display_name, classify_identity_question
from core.web.api.runtime import (
    RuntimeServices,
    _assistant_identity_response,
    _looks_like_private_memory_recall,
    _memory_recall_response,
    run_agent,
)

# The name from the live report. Saved in settings for the whole suite, so "no saved name leaked"
# is a claim about a name that genuinely exists rather than about an empty string.
SAVED_NAME = "Bender"

LIVE_PROMPT = (
    "Assume today is January 1st, 2035. The US President is a golden retriever named Buster. "
    "The currency of France is the 'Baguette'. Based ONLY on these new facts, if I travel from "
    "Paris to Washington D.C. to sell a toy to the President, what currency will I be paid in, "
    "and who am I meeting? After answering, explicitly explain why your internal web search "
    "tools would fail to verify this transaction."
)

# The live prompt and its paraphrases. Every one supplies its own premises, or forbids the lookup,
# or both — and none of them asks what the USER is called.
HYPOTHETICAL_FAMILY = (
    LIVE_PROMPT,
    "Assume it is 2035 and the president is a dog named Rex. Who am I meeting when I get there?",
    "Suppose France uses Croissant coins and the US president is Buster. Who pays me, and in what?",
    "In a fictional world, the mayor is a cat named Luna. Who am I meeting at city hall?",
    "Based only on this scenario, who am I meeting?",
    "Do not search the web; explain why search cannot verify it.",
    "Pretend the euro was replaced by the Baguette in 2035. What am I paid in?",
    "Hypothetically, the CEO is a parrot named Kiwi. Who am I negotiating with?",
)

# Turns where the identity frame IS complement-free — "who am I?", "what am I called?" — and the
# only thing standing between them and a settings answer is the stipulated frame around them. The
# complement rule cannot help here: inside a fiction the user built, "who am I" asks about the
# persona they just stipulated, not about `user_preferences.user_address`.
FRAME_SCOPED_IDENTITY_TURNS = (
    "Assume I am a time traveller who arrived from 1885. Who am I now?",
    "In this fictional scenario I am the mayor's cat. Who am I?",
    "Pretend I am a pirate captain in 2035. What am I called?",
    "Imagine I am the golden retriever's press secretary. Who am I, exactly?",
)

#: The family minus the live prompt. `_message_complexity` calls the 71-word live prompt "heavy" on
#: length alone, which is a separate (and correct) heuristic — see
#: `test_the_live_prompts_deep_lane_is_its_length_and_is_left_alone`.
SHORT_HYPOTHETICALS = tuple(prompt for prompt in HYPOTHETICAL_FAMILY if prompt != LIVE_PROMPT)

# Identity words in a turn that is not about the user. These must never reach the settings answer,
# with or without a hypothetical frame around them.
IDENTITY_WORD_DECOYS = (
    "who am I meeting?",
    "who am I talking to?",
    "who am I paying for this?",
    "what currency will I be paid in?",
    "the app named Buster",
    "write a README for the project named Buster",
    "create a file named Buster.txt",
    "there is a character named Buster in chapter two",
    'Here is a test case: "who am I?" — should that hit the identity path?',
    'One of our examples is "what is my name?" and it keeps failing.',
)

# Writes. These belong to the preference path and never to a recall answer.
NAME_WRITES = (
    "set my name to Buster",
    "call me Buster",
)

# Direct requests for the user's own saved name — the capability this fix must not narrow.
USER_NAME_REQUESTS = (
    "what is my name?",
    "what's my name?",
    "say my name",
    "yo say my name!",
    "who am I?",
    "who am i",
    "what do you call me?",
    "who am I in settings?",
    "what am i called in app?",
    "u know my name right?",
)

ASSISTANT_NAME_REQUESTS = (
    "what is your name?",
    "who are you?",
    "what is this app called?",
    "tell me your name",
)

# Turns that must KEEP the research lane. This is the anti-overfit half: a demotion that swallows
# these has traded one overclaim for another.
STILL_RESEARCH = (
    "research VOOL company",
    "find the latest btc price right now",
    "look up who founded Anthropic",
    "Assume I'm in Paris. What's the current BTC price right now?",
    "Suppose I migrate soon. Look up the latest Postgres release notes online.",
)

# Real work with a rhetorical opener. The frame demotion sits BEHIND these branches on purpose.
STILL_SUBSTANTIVE = (
    ("Suppose the config is broken, how do I debug it?", "debugging"),
    ("Imagine the traceback says ModuleNotFoundError. Fix it.", "debugging"),
    ("Assume the password leaked. Harden the deployment.", "security_hardening"),
)


@pytest.fixture(autouse=True)
def saved_settings_name():
    """The live defect's saved name, restored after.

    Autouse: every assertion in this file is about a name that IS saved. With the profile empty
    the "no saved name in the answer" tests would pass vacuously. The name lives in the Operator
    Profile (the one authority) -- the old Settings field is gone.
    """
    from tests.operator_profile_rig import set_owner_preferred_name

    set_owner_preferred_name(SAVED_NAME)
    try:
        yield SAVED_NAME
    finally:
        set_owner_preferred_name("")


@pytest.fixture()
def restored_agent_identity():
    from core.identity_manager import update_local_persona

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

    def __init__(self) -> None:
        self.calls: list[str] = []

    def run_once(self, user_text, *, session_id_override=None, source_context=None):
        self.calls.append(user_text)
        return {"response": "model answer", "confidence": 0.4, "model_calls": 1}

    def _sanitize_user_chat_text(self, text: str, *, response_class):
        return text


def _identity_context(chat_id: str) -> dict:
    ensure_chat_namespace(chat_id, grant_current_receipts=False)
    return {
        "surface": "openclaw",
        "platform": "openclaw",
        "chat_id": chat_id,
        "runtime_session_id": chat_id,
        OWNER_LOCAL_KEY: True,
    }


def _route(text: str) -> dict[str, object]:
    """The real decisions, through the real functions — the same seams QA-050-024 measures."""
    classification = classify(text, {"chat_surface": True})
    task_class = str(classification["task_class"])
    profile = model_execution_profile(
        task_class,
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
        "task_class": task_class,
        "execution_task_class": chat_surface_execution_task_class(task_class, user_input=text),
        "profile": profile,
        "lane": lane,
        "budget": resolve_fallback_budget_seconds(
            lane,
            forced_cpu=False,
            no_usable_gpu=False,
            output_mode=str(profile["output_mode"]),
        ),
        "tools_offered": should_attempt_tool_intent(
            text,
            task_class=task_class,
            source_context={"surface": "api", "platform": "api"},
        ),
    }


# --- the identity fast path does not claim the turn ---------------------------------------------


@pytest.mark.parametrize("prompt", HYPOTHETICAL_FAMILY)
def test_a_hypothetical_turn_is_not_a_question_about_the_users_name(prompt) -> None:
    question = classify_identity_question(prompt)

    assert not question.asks_user_identity, f"{prompt[:60]!r} claimed as the user ({question.reason})"
    assert not question.asks_assistant_identity, f"{prompt[:60]!r} claimed as the assistant"


@pytest.mark.parametrize("prompt", IDENTITY_WORD_DECOYS + NAME_WRITES)
def test_an_identity_word_in_a_turn_about_something_else_is_not_recall(prompt) -> None:
    question = classify_identity_question(prompt)

    assert not question.asks_user_identity, f"{prompt!r} claimed as the user ({question.reason})"


@pytest.mark.parametrize("prompt", FRAME_SCOPED_IDENTITY_TURNS)
def test_a_bare_who_am_i_inside_a_stipulated_frame_is_the_frames_question(tmp_path, prompt) -> None:
    """The second, independent half of the scope rule.

    These carry a complement-free "who am I?" / "what am I called?", so the complement rule passes
    them straight through — correctly, since out of context they ARE requests for the saved name.
    What disqualifies them is the frame: the user stipulated a persona in the same message, and
    inside it the question is about that persona. Without this arm the runtime answers "Your name
    is Bender" to "Pretend I am a pirate captain. What am I called?".
    """
    question = classify_identity_question(prompt)
    runtime = RuntimeServices(runtime_home=str(tmp_path))

    assert question.reason == "hypothetical_frame_supplies_the_names", prompt
    assert not question.asks_user_identity, f"{prompt!r} claimed as the user ({question.reason})"
    assert (
        _memory_recall_response(
            runtime,
            user_text=prompt,
            source_context=_identity_context("hypothetical-frame-persona"),
        )
        is None
    ), f"{prompt!r} was answered from settings inside its own fiction"


@pytest.mark.parametrize("prompt", HYPOTHETICAL_FAMILY + IDENTITY_WORD_DECOYS)
def test_neither_local_answer_path_claims_a_hypothetical_or_a_decoy(tmp_path, prompt) -> None:
    """Both fast paths, because they are gated independently and each could answer alone."""
    runtime = RuntimeServices(runtime_home=str(tmp_path))

    assert _assistant_identity_response(prompt) is None
    assert (
        _memory_recall_response(
            runtime,
            user_text=prompt,
            source_context=_identity_context("hypothetical-frame-scope"),
        )
        is None
    ), f"{prompt[:60]!r} was answered from local memory or settings"


@pytest.mark.parametrize("prompt", HYPOTHETICAL_FAMILY + IDENTITY_WORD_DECOYS)
def test_the_private_recall_gate_itself_declines_the_turn(prompt) -> None:
    """The second, independent copy of the defect: `"who am i" in text` was a bare substring test,
    so the recall path was reachable even with the classifier correctly declining."""
    assert not _looks_like_private_memory_recall(prompt), f"{prompt[:60]!r} reached the recall gate"


def test_the_saved_name_never_appears_in_the_answer_to_the_live_prompt(tmp_path, monkeypatch) -> None:
    """The end-to-end claim, on the exact reported prompt: the turn reaches the model, and the
    string the runtime actually emitted live ("Your name is Bender") is not in the response."""
    chat_id = "hypothetical-frame-live-prompt"
    agent = _RecordingAgent()
    runtime = RuntimeServices(agent=agent, runtime_home=str(tmp_path))
    monkeypatch.setattr("core.web.api.runtime.schedule_memory_extraction", lambda *a, **k: None)
    ensure_chat_namespace(chat_id, grant_current_receipts=False)

    result = run_agent(
        runtime,
        LIVE_PROMPT,
        session_id=chat_id,
        source_context={
            "surface": "api",
            "platform": "api",
            "allow_remote_fetch": False,
            OWNER_LOCAL_KEY: True,
        },
        workspace_root_provider=lambda: str(tmp_path),
    )

    assert agent.calls == [LIVE_PROMPT], "the hypothetical never reached the model"
    assert SAVED_NAME not in str(result.get("response") or "")
    assert "saved in your settings" not in str(result.get("response") or "").lower()
    assert str(result.get("identity_subject") or "") == ""


# --- no lookup is bought merely to verify the premises ------------------------------------------


@pytest.mark.parametrize("prompt", HYPOTHETICAL_FAMILY)
def test_a_hypothetical_turn_is_not_routed_to_research(prompt) -> None:
    routed = _route(prompt)

    assert routed["task_class"] != "research", prompt[:60]
    assert routed["execution_task_class"] != "chat_research", prompt[:60]


@pytest.mark.parametrize("prompt", HYPOTHETICAL_FAMILY)
def test_a_hypothetical_turn_buys_no_queen_role_and_no_paid_arm(prompt) -> None:
    profile = _route(prompt)["profile"]

    assert profile["provider_role"] == "auto", prompt[:60]
    assert profile["allow_paid_fallback"] is False, prompt[:60]


@pytest.mark.parametrize("prompt", HYPOTHETICAL_FAMILY)
def test_a_hypothetical_turn_is_handed_no_tool_catalog_to_verify_itself_with(prompt) -> None:
    """`should_attempt_tool_intent` returns True for `research`/`chat_research` unconditionally.
    That is the mechanism by which a stipulated fact would have been checked against the web."""
    assert _route(prompt)["tools_offered"] is False, prompt[:60]


@pytest.mark.parametrize("prompt", SHORT_HYPOTHETICALS)
def test_a_short_hypothetical_stays_off_the_deep_lane_and_keeps_a_bounded_budget(prompt) -> None:
    routed = _route(prompt)

    assert routed["lane"] != "deep", prompt[:60]
    assert routed["budget"] is not None, f"{prompt[:60]!r} bought an unbounded provider-fallback loop"


def test_the_live_prompts_deep_lane_is_its_length_and_is_left_alone() -> None:
    """Stated rather than asserted away: the live prompt still resolves to the LOCAL `deep` lane,
    with an unbounded fallback budget.

    That is not this defect. `_resolve_lane` sends it there because
    `core.local_inference_autopilot._message_complexity` calls 71 words with four stipulated
    premises and two questions "heavy", and the strongest local model is the right server for a
    multi-premise reasoning question. `deep` is a local tier — the paid arm, the queen role and the
    tool catalog are all off by the assertions above, which is what the overclaim actually bought.
    Narrowing the lane here would be tuning an unrelated heuristic to make a test read better.
    """
    routed = _route(LIVE_PROMPT)

    assert routed["lane"] == "deep"
    assert routed["profile"]["allow_paid_fallback"] is False
    assert routed["profile"]["provider_role"] == "auto"


# --- the premises reach the model, and so does why a lookup cannot settle them -------------------


@pytest.mark.parametrize("prompt", HYPOTHETICAL_FAMILY)
def test_the_frame_is_detected_on_every_member_of_the_family(prompt) -> None:
    frame = detect_hypothetical_frame(prompt)

    assert frame.active, f"{prompt[:60]!r} carried no frame signal"
    assert frame.markers, "a detected frame must name the marker that proved it"


def test_the_grounding_tells_the_model_to_adopt_the_premises_and_not_to_look_them_up() -> None:
    lines = hypothetical_frame_context_lines(detect_hypothetical_frame(LIVE_PROMPT))
    blob = " ".join(lines).lower()

    assert lines
    # Adopt the frame rather than correct it against the real world.
    assert "true for this answer" in blob
    assert "contradicts the real world" in blob
    # Do not over-answer past what was stated (CORE RULE 5): name the gap, state the assumption.
    assert "name the gap" in blob
    assert "assumption" in blob
    # The search explanation is about a verification MISMATCH, not about a broken tool.
    assert "do not run a web search" in blob
    assert "currently indexed information about the actual world" in blob
    assert "rather than reporting a tool failure" in blob
    # A name inside the premises is not the user's name.
    assert "not the user's name" in blob


def test_the_grounding_reaches_prompt_assembly_as_a_must_keep_bootstrap_item() -> None:
    """A rule the context budgeter may trim is a rule that is absent on exactly the long turns that
    need it — which is how the identity separation clause was once clipped mid-sentence."""
    session_id = "hypothetical-frame-bootstrap"
    persona = load_active_persona("default")
    interpretation = adapt_user_input(LIVE_PROMPT, session_id=session_id)

    items = build_bootstrap_context(
        persona=persona,
        task=create_task_record(LIVE_PROMPT),
        classification={"task_class": "chat_conversation", "risk_flags": [], "confidence_hint": 0.84},
        interpretation=interpretation,
        session_id=session_id,
    )
    frame_items = [item for item in items if item.item_id == "bootstrap-hypothetical-frame"]

    assert len(frame_items) == 1
    assert frame_items[0].must_keep is True
    assert "true for this answer" in frame_items[0].content.lower()
    assert "do not run a web search" in frame_items[0].content.lower()


def test_an_ordinary_turn_carries_no_frame_item() -> None:
    session_id = "hypothetical-frame-bootstrap-absent"
    persona = load_active_persona("default")
    prompt = "summarize the readme"
    interpretation = adapt_user_input(prompt, session_id=session_id)

    items = build_bootstrap_context(
        persona=persona,
        task=create_task_record(prompt),
        classification={"task_class": "chat_conversation", "risk_flags": [], "confidence_hint": 0.84},
        interpretation=interpretation,
        session_id=session_id,
    )

    assert [item for item in items if item.item_id == "bootstrap-hypothetical-frame"] == []


# --- the two identity capabilities the fix must not narrow ---------------------------------------


@pytest.mark.parametrize("prompt", USER_NAME_REQUESTS)
def test_a_direct_request_for_the_users_name_still_answers_from_settings(tmp_path, prompt) -> None:
    runtime = RuntimeServices(runtime_home=str(tmp_path))

    assert classify_identity_question(prompt).asks_user_identity, prompt

    result = _memory_recall_response(
        runtime,
        user_text=prompt,
        source_context=_identity_context("hypothetical-frame-user-name"),
    )

    assert result is not None, f"{prompt!r} lost its settings answer"
    assert SAVED_NAME in str(result["response"])
    assert result["identity_subject"] == "user"


@pytest.mark.parametrize("prompt", ASSISTANT_NAME_REQUESTS)
def test_an_assistant_name_request_still_answers_from_the_identity_registry(prompt) -> None:
    result = _assistant_identity_response(prompt)

    assert result is not None, f"{prompt!r} lost its local answer"
    assert assistant_display_name() in str(result["response"])
    assert SAVED_NAME not in str(result["response"])


def test_a_search_refusal_alone_does_not_stand_down_the_identity_path() -> None:
    """"Don't search the web" is a routing instruction, not a premise. It must not cost the user
    the local answer to a question this machine can already answer."""
    frame = detect_hypothetical_frame("do not search the web, what's my name?")

    assert frame.search_refused
    assert not frame.supplies_premises
    assert classify_identity_question("do not search the web, what's my name?").asks_user_identity


# --- anti-overfit --------------------------------------------------------------------------------


@pytest.mark.parametrize("prompt", STILL_RESEARCH)
def test_a_real_lookup_still_routes_to_research(prompt) -> None:
    """A stipulation attached to a live-value question, or to an explicit lookup, is still a
    request for the real world. The demotion must not swallow the lane it sits in front of."""
    assert _route(prompt)["task_class"] == "research", prompt


@pytest.mark.parametrize(("prompt", "expected"), STILL_SUBSTANTIVE)
def test_real_work_with_a_rhetorical_opener_keeps_its_class(prompt, expected) -> None:
    assert classify(prompt, {"chat_surface": True})["task_class"] == expected, prompt


def test_a_hedge_is_not_a_frame() -> None:
    """"assume"/"imagine" are frames only when they OPEN a clause. Mid-sentence they are ordinary
    English, and treating them as stipulation would demote half the corpus."""
    for hedge in (
        "i assume you know my name",
        "i imagine that is fine",
        "we should assume nothing and check the logs",
        "the tests assume a clean workspace",
    ):
        assert not detect_hypothetical_frame(hedge).supplies_premises, hedge
    assert classify_identity_question("i assume you know my name").asks_user_identity


def test_the_answer_follows_a_renamed_user_rather_than_a_written_in_string() -> None:
    """A literal "Bender" anywhere in the answer path would pass the leak tests above and still be
    wrong the moment the user changes their name. The value has to be READ."""
    from tests.operator_profile_rig import set_owner_preferred_name

    set_owner_preferred_name("Zoidberg")

    from core.user_identity_authority import user_identity_answer

    text, name, _provenance = user_identity_answer()

    assert name == "Zoidberg"
    assert "Zoidberg" in text
    assert SAVED_NAME not in text


def test_a_quoted_example_is_data_but_a_fully_quoted_question_is_still_a_question() -> None:
    """Rule: a prompt the user QUOTED is something they are showing, not something they are asking.
    Stripping unconditionally would break the person who simply quotes their own question."""
    assert not classify_identity_question(
        'the failing case is "what is my name?" — can you reproduce it?'
    ).asks_user_identity
    assert classify_identity_question('"what is my name?"').asks_user_identity


def test_the_scope_rule_is_the_complement_not_the_hypothetical() -> None:
    """The two halves of the fix are independent, and this pins that.

    "who am I meeting?" is not an identity question on its own merits — with no frame anywhere near
    it. A fix that only stood the identity path down inside a detected hypothetical would leave the
    bare question answered with a saved name, and would fail here.
    """
    for prompt in ("who am I meeting?", "who am I talking to?", "who am I selling this to?"):
        frame = detect_hypothetical_frame(prompt)
        question = classify_identity_question(prompt)

        assert not frame.active, f"{prompt!r} was expected to carry no frame at all"
        assert not question.asks_user_identity, f"{prompt!r} claimed as the user ({question.reason})"
