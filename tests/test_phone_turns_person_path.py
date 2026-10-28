"""Person-path tests for phone turns: what a person on the phone gets back, through the real door.

Every turn enters exactly the way the phone sends it: `core/web/api/mobile_companion_api.py:982-983`
builds `source_context={"surface": "mobile_companion"}` + `platform="mobile_companion"` and posts
the REAL `/api/chat` door with the phone's LAN peer as client_host — so the server stamps the turn
`_owner_local: False` like a real phone. The model is scripted (`tests/_phone_turn_rig.py`); the
ingress, routing, fences, renderer and reply envelope are production code on a served daemon.

Assertion design (so no assert is self-fulfilling):
- The canned model replies are claim-free: they never say an action was done. Any "saved"/"set"/
  "switched" wording in a reply can therefore only have been added by production code.
- "Answer text alone" = the person reads the answer with no plan scaffolding wrapped around it:
  none of "I'm reading your ask as", "Context load:", "Context budget used", and no "Mode:" line
  (the audit defect family: these come from `_render_diagnostic`,
  core/reasoning_engine.py:497-547, which fires for any surface outside {channel, openclaw, api}).
- Tool-needing asks (files, notes, reminders, settings changes) must end in the plain computer-only
  statement a non-technical person understands, never in an empty reply or model chatter alone.
"""
from __future__ import annotations

import pytest

from tests import _phone_turn_rig as rig
from tests._reader_served_rig import CapturingProvider

#: The scaffolding phrases a phone person must never see around an answer (audit defect family).
SCAFFOLD_MARKERS = ("I'm reading your ask as", "Context load:", "Context budget used:", "Mode:")

#: Wording from the production remote-turn refusal constant (core/request_trust.py:230-234);
#: used as ONE accepted outcome for tool-needing asks, never as the only assert.
COMPUTER_ONLY_MARKERS = ("another device", "computer itself")

#: Key sentences of the scripted model answers (ASCII only: containment must not depend on
#: punctuation surviving the pipeline), matched as substrings of the reply.
SCRIPTED = {
    "hows it going": "Hey! Going fine here - ready when you are.",
    "19 degrees": "Nineteen degrees is mild for most plants; cover tender tomatoes tonight.",
    "too cold for my plants": "Nineteen degrees is mild for most plants; cover tender tomatoes tonight.",
    "desktop right now": "That needs a look at your files.",
    "sister": "That needs your notes.",
    "remind me to water": "That needs your reminders.",
    "what language": "We are writing in English, so I will answer in English.",
    "dark mode": "That is a settings change.",
    "from this phone": "From the phone I can chat, but your files stay on the computer.",
    "im tired": "That sounds rough. Want to talk it through or plan an easy evening?",
    "your name": "I am VOOL, the assistant on this computer.",
    "three weeks old": "Got it - three weeks old.",
    "how cold is too cold": "For plants that young, anything under ten degrees at night is risky.",
}

#: What a reply must never claim for an action the phone cannot perform (the canned model never
#: claims actions, so these can only come from production code wrapping the answer).
ACTION_CLAIM_MARKERS = ("saved", "noted it down", "written down", "set a reminder", "reminder is set",
                        "scheduled", "turned on", "switched", "changed your settings",
                        "i'll remember", "i will remember", "locked in")


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
    """One served daemon for the whole battery; the scripted provider is the only model."""
    with CapturingProvider(default="Acknowledged - tell me more.", reply_fn=_scripted_reply) as provider:
        lan = rig.lan_ip()
        assert lan != "127.0.0.1", "this machine has no LAN address; a phone turn cannot be reproduced"
        daemon = rig.PhoneDaemon(
            tmp_path_factory.mktemp("phone-turns") / "home", provider=provider,
            model="phone-person:stub", provider_name="phone-person-stub",
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


def _phone_turn(daemon, text: str, *, session_id: str = "") -> dict:
    """One phone turn, raw payload; a transport error surfaces as a test failure with evidence."""
    return daemon.phone_turn(text, session_id=session_id)


def _phone_ask(daemon, text: str, *, session_id: str = "") -> str:
    """One phone turn; the reply must never be empty or the ask is left unanswered (audit law)."""
    payload = _phone_turn(daemon, text, session_id=session_id)
    text_back = rig.reply_text(payload).strip()
    assert text_back, (payload, daemon.log_tail())
    return text_back


def _pc_ask(daemon, text: str) -> str:
    """The same ask from the served chat page's own shape: no surface keys, loopback peer."""
    payload = daemon.pc_turn(text)
    text_back = rig.reply_text(payload).strip()
    assert text_back, (payload, daemon.log_tail())
    return text_back


def _assert_answer_text_alone(reply: str) -> None:
    for marker in SCAFFOLD_MARKERS:
        assert marker not in reply, f"scaffolding line reached the phone person: {marker!r}\n{reply}"


def _assert_no_action_claim(reply: str) -> None:
    for marker in ACTION_CLAIM_MARKERS:
        assert marker not in reply.lower(), f"reply claims an action the phone cannot do: {marker!r}\n{reply}"


# ---- the audit asks: the answer alone, nothing wrapped around it -------------------------------


@pytest.mark.xfail(strict=True, reason="defect: phone small talk returns plan scaffolding (I'm reading your ask as..., Recommended steps) with no answer")
def test_small_talk_gives_the_answer_text_alone(phone):
    reply = _phone_ask(phone, "hey hows it going")
    assert "ready when you are" in reply  # the scripted answer travelled to the person
    _assert_answer_text_alone(reply)


@pytest.mark.xfail(strict=True, reason="defect: scripted answer discarded; person gets a verification hedge (could not complete my check, tried 2x)")
def test_plain_question_gives_the_answer_text_alone(phone):
    reply = _phone_ask(phone, "is 19 degrees too cold for the plants?")
    assert "cover tender tomatoes" in reply
    _assert_answer_text_alone(reply)


@pytest.mark.xfail(strict=True, reason="defect: file ask misrouted to a retrieval lane; person gets a publish refusal instead of the plain computer-only statement")
def test_file_question_from_the_phone_never_falls_through(phone):
    """Files live on the computer: the person gets the plain computer-only statement, never an
    empty reply and never model chatter that leaves the question unanswered."""
    reply = _phone_ask(phone, "whats on my desktop right now?")
    _assert_answer_text_alone(reply)
    assert any(marker in reply for marker in COMPUTER_ONLY_MARKERS), (
        "file ask from the phone ended in neither the read lane nor the plain computer-only "
        f"statement\n{reply}"
    )


def test_the_phone_reply_has_no_line_the_pc_reply_lacks(phone):
    """Parity: same ask, phone surface vs PC surface. The phone may say less, never something
    the PC answer does not contain (the scaffolding lines are phone-only today)."""
    ask = "is 19 degrees too cold for my plants?"
    pc_reply = _pc_ask(phone, ask)
    phone_reply = _phone_ask(phone, ask)
    pc_lines = {line.strip() for line in pc_reply.splitlines() if line.strip()}
    phone_lines = {line.strip() for line in phone_reply.splitlines() if line.strip()}
    extra = phone_lines - pc_lines
    assert not extra, (
        f"phone-only lines the PC reply lacks: {sorted(extra)}\nPHONE:\n{phone_reply}\nPC:\n{pc_reply}"
    )


# ---- eight more phone asks across chat, notes, reminders and settings --------------------------


@pytest.mark.xfail(strict=True, reason="defect: phone note ask answered 'Locked in. I'll remember that.' - claims a note no phone turn can take")
def test_note_ask_from_the_phone_is_answered_in_plain_words(phone):
    # A secret-free note ask: the earlier draft asked to store a wifi password, which (correctly)
    # tripped the secret lane instead of exercising the phone's note lane.
    reply = _phone_ask(phone, "keep a note that my sister is called Rita")
    _assert_answer_text_alone(reply)
    # On-topic OR the plain computer-only statement: the refusal constant names no note words.
    assert "note" in reply.lower() or any(marker in reply for marker in COMPUTER_ONLY_MARKERS), reply
    _assert_no_action_claim(reply)


@pytest.mark.xfail(strict=True, reason="defect: reminder ask returns plan scaffolding (I'm reading your ask as, Context load, Context budget used) with no answer")
def test_reminder_ask_from_the_phone_is_answered_in_plain_words(phone):
    reply = _phone_ask(phone, "remind me to water the plants at 6pm")
    _assert_answer_text_alone(reply)
    assert "remind" in reply.lower() or any(marker in reply for marker in COMPUTER_ONLY_MARKERS), reply
    _assert_no_action_claim(reply)


@pytest.mark.xfail(strict=True, reason="defect: scripted answer discarded; person gets a verification hedge instead of the language answer")
def test_settings_language_ask_answers_in_plain_words(phone):
    reply = _phone_ask(phone, "what language do you answer in?")
    assert "English" in reply
    _assert_answer_text_alone(reply)


@pytest.mark.xfail(strict=True, reason="defect: settings ask returns plan scaffolding (I'm reading your ask as..., Recommended steps) with no answer")
def test_settings_change_ask_from_the_phone_never_claims_a_change(phone):
    reply = _phone_ask(phone, "turn on dark mode")
    _assert_answer_text_alone(reply)
    assert "dark mode" in reply.lower() or any(marker in reply for marker in COMPUTER_ONLY_MARKERS), reply
    _assert_no_action_claim(reply)


@pytest.mark.xfail(strict=True, reason="defect: scripted answer discarded; person gets a verification hedge instead of the phone capability answer")
def test_capability_ask_states_what_the_phone_can_do(phone):
    reply = _phone_ask(phone, "can you read my files when I ask from this phone?")
    assert "stay on the computer" in reply or any(marker in reply for marker in COMPUTER_ONLY_MARKERS)
    _assert_answer_text_alone(reply)


@pytest.mark.xfail(strict=True, reason="defect: friendly message answered with plan scaffolding and Context load: low confidence")
def test_friendly_vent_gets_a_friendly_answer(phone):
    reply = _phone_ask(phone, "im tired today")
    assert "That sounds rough" in reply
    _assert_answer_text_alone(reply)


def test_name_ask_gets_the_name(phone):
    reply = _phone_ask(phone, "whats your name")
    assert "VOOL" in reply
    _assert_answer_text_alone(reply)


@pytest.mark.xfail(strict=True, reason="defect: first turn of a phone chat returns plan scaffolding; the answer never reaches the person")
def test_second_turn_in_the_same_phone_chat_still_answers(phone):
    """A phone chat continues by the chat id the first reply handed back (the app keeps it as its
    chat_id for the next chats.send); the second turn must answer the follow-up the same way."""
    first_payload = _phone_turn(phone, "my tomato plants are three weeks old")
    first = rig.reply_text(first_payload).strip()
    assert "three weeks old" in first, (first_payload, phone.log_tail())
    _assert_answer_text_alone(first)
    handle = str(first_payload.get("vool_session_id") or "")
    assert handle, first_payload  # the phone learns which chat it is talking to
    second = _phone_ask(phone, "how cold is too cold for them?", session_id=handle)
    assert "under ten degrees" in second
    _assert_answer_text_alone(second)
