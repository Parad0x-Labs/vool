"""Memory questions stay out of the runtime-state lanes.

Each MEMORY case is a synthetic sentence with the grammatical shape of a recall question that a
runtime lane used to claim (machine-fact refusal, workspace identity, clock, creative director,
focused answer follow-up). Each RUNTIME case is a control the lane must keep claiming. The rules
under test are grammatical: a display property word needs a display or host noun, a report of what
was said is not a machine fact, "time" is the clock only as the head of its noun phrase, workspace
identity declines an activity clause, a recall frame is not a creative brief, a locative "there" is
not a pointer at an answer, and a cross-session reference with no assistant turn keeps memory.
"""
from types import SimpleNamespace

import pytest

import core.agent_runtime.fast_paths_utility as fpu
from core.creative_director import detect_creative_brief
from core.execution.constants import local_fact_capability_required

SC = {"surface": "openclaw", "platform": "api", "chat_id": "t"}


class _Agent:
    def _fast_path_result(self, **k):
        return {"claimed_by": k.get("reason")}


@pytest.fixture(autouse=True)
def _resolvable_workspace_identity(monkeypatch):
    import core.runtime_execution_tools as ret

    class _OK:
        ok = True
        response_text = "workspace: fixture"
    monkeypatch.setattr(ret, "execute_runtime_tool", lambda *a, **k: _OK())


def _identity(text):
    return fpu.maybe_handle_workspace_identity_request(_Agent(), text, session_id="t", source_surface="openclaw", source_context=SC)


def _clock(text):
    return fpu.date_time_fast_path(None, text, source_surface="openclaw", session_id="t", source_context=SC)


MEMORY_LOCAL_FACT = ["What size is my new TV?", "What size was the rug my aunt picked for her hallway?", "How much RAM did I say my old laptop had?", "What monitor did you recommend for my sister?", "Do you remember how much storage I said my old tablet had?"]
RUNTIME_LOCAL_FACT = ["What size is the monitor on this machine?", "what resolution is my screen?", "how much ram does this machine have?", "what are my specs?", "how much free space is on my drive?", "You said my disk was nearly full; how much free space is left now?", "what's my refresh rate?"]
MEMORY_IDENTITY = ["Which project did we wrap up sooner, the garden shed or the treehouse?", "What project is Leo working on at his woodshop these days?", "Which project did my brother finish last?"]
RUNTIME_IDENTITY = ["Which folder is this chat bound to?", "what project is this?", "which workspace am I in?", "what is my project name?"]
MEMORY_CLOCK = ["What time blocking methods do my cousins rely on?", "What time management app did I start using?"]
RUNTIME_CLOCK = ["what time is it?", "What time is it in Tokyo right now?", "what's the time now?", "what time zone am I in?"]
MEMORY_CREATIVE = ["I wanted to circle back to an older session. Could you remind me which glacier documentary video you suggested?", "In the screenplay script you wrote for my film class, what coat did the detective wear?", "I wanted to confirm which tool you mentioned for processing satellite images."]
RUNTIME_CREATIVE = ["Write me a cinematic video prompt about a lighthouse at dusk.", "I want a video of a dragon flying over a castle", "Expand the video prompt you wrote with more detail", "image: a red fox in fresh snow", "Generate an image prompt for a cyberpunk street at night", "Write me a new video prompt in the style of the one you made last week"]


@pytest.mark.parametrize("text", MEMORY_LOCAL_FACT)
def test_memory_shape_is_not_a_machine_fact(text):
    assert local_fact_capability_required(text) is None


@pytest.mark.parametrize("text", RUNTIME_LOCAL_FACT)
def test_machine_fact_still_requires_a_tool(text):
    assert local_fact_capability_required(text) is not None


@pytest.mark.parametrize("text", MEMORY_IDENTITY)
def test_memory_shape_is_not_workspace_identity(text):
    assert _identity(text) is None


@pytest.mark.parametrize("text", RUNTIME_IDENTITY)
def test_workspace_identity_still_answers(text):
    assert (_identity(text) or {}).get("claimed_by") == "workspace_identity_fast_path"


@pytest.mark.parametrize("text", MEMORY_CLOCK)
def test_time_as_modifier_is_not_the_clock(text):
    assert _clock(text) is None


@pytest.mark.parametrize("text", RUNTIME_CLOCK)
def test_clock_still_answers(text):
    assert _clock(text)


@pytest.mark.parametrize("text", MEMORY_CREATIVE)
def test_recall_of_earlier_media_is_not_a_creative_brief(text):
    assert detect_creative_brief(text) is None


@pytest.mark.parametrize("text", RUNTIME_CREATIVE)
def test_creative_brief_still_detected(text):
    assert detect_creative_brief(text) is not None


# --- prompt_normalizer focused-assistant-follow-up: a locative "there" is not a pointer at an answer.
import core.prompt_normalizer as pn

NOT_FOLLOWUP = ["I'm planning a trip to Lisbon soon. Any suggestions on what to do there?", "What should I see there next month?"]
FOLLOWUP = ["What did you say about that earlier?", "Which point of that answer matters most?", "make that shorter", "explain that"]


@pytest.mark.parametrize("text", NOT_FOLLOWUP)
def test_locative_there_is_not_an_answer_reference(text):
    assert not pn._ASSISTANT_REFERENCE_FOLLOWUP_RE.search(text) and not pn._RESPONSE_SHAPING_FOLLOWUP_RE.search(text)


@pytest.mark.parametrize("text", FOLLOWUP)
def test_answer_references_still_detected(text):
    assert pn._ASSISTANT_REFERENCE_FOLLOWUP_RE.search(text) or pn._RESPONSE_SHAPING_FOLLOWUP_RE.search(text)


_CAPSULE = "<retrieved_context>\n- assistant said: For the lentil soup, toast the cumin seeds before adding the onions.\n</retrieved_context>"


def _conversational_request(monkeypatch, text, transcript):
    def fake_transcript(**kwargs):
        return [dict(row) for row in transcript], "structured_dialogue_memory"

    monkeypatch.setattr(pn, "canonical_runtime_transcript", fake_transcript)
    context = {"chat_id": "lanes-fixture", "runtime_session_id": "lanes-fixture", "surface": "api", "platform": "api", "memory_prompt_enabled": True}
    result = SimpleNamespace(
        report=SimpleNamespace(to_dict=lambda: {}),
        bootstrap_items=[SimpleNamespace(source_type="runtime_memory")],
        assembled_context=lambda **kwargs: "Relevant historical context: soup notes from an earlier session.",
    )
    return pn._build_conversational_request(
        user_text=text,
        persona=SimpleNamespace(display_name="VOOL", tone="calm"),
        classification={"task_class": "research"},
        context_result=result,
        task_kind="conversation",
        output_mode="plain_text",
        trace_id="lanes-fixture",
        ambiguity=0.9,
        source_context=context,
        current_turn_id="lanes-turn",
    )


def test_cross_session_reference_with_no_assistant_turn_keeps_memory(monkeypatch):
    """A question about an answer from an EARLIER session, in a chat with no assistant turn, is
    recall: the reader gets the memory context and no instruction to call the answer unavailable."""
    text = "Last month you gave me a soup recipe. Which spice did you say to toast earlier?"
    request = _conversational_request(monkeypatch, text, [{"role": "system", "content": _CAPSULE}])
    joined = "\n".join(str(message.content or "") for message in request.messages)
    assert "referenced answer is unavailable" not in joined
    assert "response-shaping follow-up" not in joined
    assert "toast the cumin seeds" in joined


def test_follow_up_on_a_present_answer_still_transforms_that_answer(monkeypatch):
    """Control: with the preceding assistant answer in this chat, an answer reference stays a
    focused follow-up bound to that answer."""
    text = "Which point of that answer matters most?"
    transcript = [
        {"role": "user", "content": "How should I start the lentil soup?"},
        {"role": "assistant", "content": "Toast the cumin seeds, then soften the onions."},
    ]
    request = _conversational_request(monkeypatch, text, transcript)
    # ae264ad6 (2026-10-06): the per-turn additions travel in the turn-directives system message ("Context for this
    # turn: ..."), so the leading system message stays byte-stable; the immediate answer is read from the served
    # system messages together
    system = "\n".join(str(m.content or "") for m in request.messages if getattr(m, "role", None) == "system")
    assert "<immediate_assistant_answer>" in system
    assert "Toast the cumin seeds, then soften the onions." in system
