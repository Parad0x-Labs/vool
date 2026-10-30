"""The reply a phone person reads is an answer, not scaffolding — and the PC's reply is an
answer too, with no line the phone's lacks (the parity rule: the phone behaves like VOOL on
the PC).

Every phone turn goes through the SEALED companion door (tests/_phone_turn_rig.py pairs for
real and signs every turn), so the replies here are the production pipeline's own output. The
scripted provider is claim-free by construction: its canned answers state facts or feelings and
never claim an action was taken, so an action claim or a scaffolding line in a reply can only
come from production code wrapping the answer — which is exactly what these tests forbid.
Phrasings that trip the out-of-scope verification hedge (the "single well-known answer" check,
reported separately) are deliberately not used: these tests measure the reply's SHAPE, and the
hedge lane's fix belongs to its own report row.

The desk/folder asks at the end hold the narrowed retrieval fix on BOTH surfaces: a disk-state
ask with "right now" must never end in the canned retrieval refusal ("I can't publish an
answer to this: it needed current information ... no usable rows"), because the machine's own
storage is not fresh world data.
"""
from __future__ import annotations

import pytest

from tests import _phone_turn_rig as rig
from tests._reader_served_rig import CapturingProvider

#: Production scaffolding lines the diagnostic renderer emits; none may reach a person.
SCAFFOLD_MARKERS = (
    "I'm reading your ask as",
    "Context load:",
    "Context budget used",
    "Recommended steps:",
    "Why: v1 defaults",
    "Mode:",
    "Confidence:",
)

#: The canned retrieval refusal a desk/files ask used to end in before the fresh-data
#: misroute was fixed (core/grounding_publication.py publishes the lead).
RETRIEVAL_REFUSAL_LEAD = "I can't publish an answer to this: it needed current information"

#: Key phrases of the scripted answers, matched as substrings (ASCII, punctuation-free).
#: The date and language asks below are deliberately NOT here: production's own deterministic
#: lanes answer them on both surfaces (the clock lane names the real day), so no canned phrase
#: can ride them -- their tests assert the reply's shape instead.
SCRIPTED = {
    "what are you up to": "Morning! Just keeping this computer tidy and waiting for your next question.",
    "you around": "Yes, I am here for as long as you want to talk.",
    "walk route": "A twenty-minute loop around your block works well for a lunch break.",
    "movie evening": "Pick one film, dim the lights at eight, and keep popcorn ready by seven thirty.",
    "short sentences": "Understood - short sentences from here on.",
}

#: Asks served by production's deterministic lanes: the answer is an answer on both surfaces;
#: what these tests hold is its SHAPE (a plain, non-empty reply with no scaffolding line).
DETERMINISTIC_LANE_ASKS = (
    "which day of the week is it today?",
    "what month is it?",
    "answer in english for this chat",
)


def _scripted_reply(body: dict) -> str | None:
    """Answer from SCRIPTED by matching the ask; None falls back to the provider default."""
    messages = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
    user_texts = [str(m.get("content") or "") for m in messages if m.get("role") == "user"]
    last = (user_texts[-1] if user_texts else "").lower()
    for key, answer in SCRIPTED.items():
        if key in last:
            return answer
    return None


@pytest.fixture(scope="module")
def phone(tmp_path_factory):
    """One sealed-paired daemon for the whole battery; the scripted provider is the only model."""
    with CapturingProvider(default="Acknowledged - tell me more.", reply_fn=_scripted_reply) as provider:
        lan = rig.lan_ip()
        assert lan != "127.0.0.1", "this machine has no LAN address; a phone turn cannot be reproduced"
        daemon = rig.PhoneDaemon(
            tmp_path_factory.mktemp("phone-shape") / "home", provider=provider,
            model="phone-shape:stub", provider_name="phone-shape-stub",
            env_extra={"VOOL_RAW_OLLAMA_API_URL": provider.base_url,
                       "VOOL_ALLOWED_HOSTS": f"127.0.0.1,localhost,{lan}"},
        )
        try:
            daemon.start(timeout=240)
            certification = daemon.certify(timeout=240)
            state = (certification.get("result") or certification).get("state")
            assert state == "verified", (certification, daemon.log_tail())
            yield daemon
        finally:
            daemon.stop()


def _phone_ask(daemon, text: str) -> str:
    payload = daemon.phone_turn(text)
    reply = rig.reply_text(payload).strip()
    assert reply, (payload, daemon.log_tail())
    return reply


def _pc_ask(daemon, text: str) -> str:
    payload = daemon.pc_turn(text)
    reply = rig.reply_text(payload).strip()
    assert reply, (payload, daemon.log_tail())
    return reply


def _assert_no_scaffolding(reply: str) -> None:
    for marker in SCAFFOLD_MARKERS:
        assert marker not in reply, f"scaffolding line reached a person: {marker!r}\n{reply}"


# ---- the shape rule: an answer, alone ----------------------------------------------------------


@pytest.mark.parametrize("ask,answer_phrase", sorted(SCRIPTED.items()))
def test_chat_ask_gets_the_answer_text_alone(phone, ask, answer_phrase):
    reply = _phone_ask(phone, ask)
    assert answer_phrase in reply
    _assert_no_scaffolding(reply)


@pytest.mark.parametrize("ask,answer_phrase", sorted(SCRIPTED.items()))
def test_the_same_ask_on_the_pc_is_an_answer_too(phone, ask, answer_phrase):
    reply = _pc_ask(phone, ask)
    assert answer_phrase in reply
    _assert_no_scaffolding(reply)


@pytest.mark.parametrize("ask,answer_phrase", sorted(SCRIPTED.items()))
def test_the_phone_reply_adds_no_line_the_pc_reply_lacks(phone, ask, answer_phrase):
    phone_reply = _phone_ask(phone, ask)
    pc_reply = _pc_ask(phone, ask)
    assert answer_phrase in phone_reply and answer_phrase in pc_reply
    phone_lines = {line.strip() for line in phone_reply.splitlines() if line.strip()}
    pc_lines = {line.strip() for line in pc_reply.splitlines() if line.strip()}
    assert phone_lines <= pc_lines, f"phone-only lines: {sorted(phone_lines - pc_lines)}"


@pytest.mark.parametrize("ask", DETERMINISTIC_LANE_ASKS)
def test_a_deterministic_lane_ask_is_answered_plainly_on_the_phone(phone, ask):
    reply = _phone_ask(phone, ask)
    _assert_no_scaffolding(reply)
    assert RETRIEVAL_REFUSAL_LEAD not in reply, reply


@pytest.mark.parametrize("ask", DETERMINISTIC_LANE_ASKS)
def test_a_deterministic_lane_ask_is_answered_plainly_on_the_pc_too(phone, ask):
    reply = _pc_ask(phone, ask)
    _assert_no_scaffolding(reply)
    assert RETRIEVAL_REFUSAL_LEAD not in reply, reply


# ---- the narrowed retrieval fix: a disk-state ask is not fresh world data ----------------------


@pytest.mark.parametrize("ask", ["whats on my desktop right now?", "show me the downloads folder now"])
def test_desk_ask_never_ends_in_the_retrieval_refusal_on_the_phone(phone, ask):
    reply = _phone_ask(phone, ask)
    assert RETRIEVAL_REFUSAL_LEAD not in reply, reply
    assert "no usable rows" not in reply, reply
    _assert_no_scaffolding(reply)


@pytest.mark.parametrize("ask", ["whats on my desktop right now?", "show me the downloads folder now"])
def test_desk_ask_never_ends_in_the_retrieval_refusal_on_the_pc(phone, ask):
    reply = _pc_ask(phone, ask)
    assert RETRIEVAL_REFUSAL_LEAD not in reply, reply
    assert "no usable rows" not in reply, reply
    _assert_no_scaffolding(reply)
