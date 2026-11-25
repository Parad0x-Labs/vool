"""A call that changes something is filled from this message, not an earlier one.

Mac gate on 91e898675 (2026-11-23, installed app, qwen3:8b): after "remind me on wednesday at 7:15pm to call grandma",
the Lithuanian "Priminkit man ketvirtadieni 9:30 isnest siuksles" was sent as operator.schedule_reminder with the
previous turn's arguments word for word, and the next ask, to cancel the rubbish reminder, went out as contacts.update
{"target": "call grandma", ...}. A value of two or more words that an earlier turn's call carried, which this message
never names and this turn never read, is handed back to the model once. Notes, languages and wordings are drawn at
random; no model and no network.
"""
from __future__ import annotations

import random

import pytest

EARLIER = (
    ("call grandma", "remind me on wednesday at 7:15pm to call grandma"),
    ("water the tomato plants", "nudge me friday morning to water the tomato plants"),
    ("renew the parking permit", "set a reminder for the 3rd: renew the parking permit"),
    ("bring the passport to the bank", "can you remind me tomorrow to bring the passport to the bank"),
)
OTHER_ASKS = (
    "Priminkit man ketvirtadieni 9:30 isnest siuksles",
    "Erinnere mich am Freitag um acht an den Zahnarzt",
    "recuérdame el sábado a las diez llamar al fontanero",
    "rappelle-moi jeudi de sortir les poubelles",
    "remind me at noon to feed the cat",
)


@pytest.fixture(autouse=True)
def _fresh_ledger():
    from core.turn_model_call_ledger import reset_for_tests

    reset_for_tests()
    yield
    reset_for_tests()


def _turn(session: str, request: str) -> dict:
    return {"session_id": session, "request_id": request}


def _earlier_call(rng: random.Random, session: str) -> tuple[str, str]:
    from core.turn_model_call_ledger import record_session_call

    note, said = rng.choice(EARLIER)
    record_session_call(_turn(session, "req-earlier"), "operator.schedule_reminder",
                        {"due_iso": "2026-11-25T19:15", "note": note, "text": said})
    return note, said


def _guard(intent, arguments, question, context, steps=None, **kw):
    from core.agent_runtime.research_tool_loop_facade import _change_call_carries_an_earlier_message

    return _change_call_carries_an_earlier_message(intent, {"intent": intent, "arguments": arguments}, question,
                                                   steps or [], context, **kw)


@pytest.mark.parametrize("seed", range(8))
def test_the_previous_turns_words_in_a_change_call_are_handed_back(seed) -> None:
    rng = random.Random(f"earlier-replay-{seed}")
    session = f"chat-replay-{seed}"
    note, said = _earlier_call(rng, session)
    ask = rng.choice(OTHER_ASKS)
    intent, arguments = rng.choice((
        ("operator.schedule_reminder", {"due_iso": "2026-11-26T09:30", "note": note, "text": said}),
        ("operator.schedule_reminder", {"due_iso": "2026-11-26T09:30", "note": note}),
        ("contacts.update", {"contact_id": f"{rng.getrandbits(32):08x}", "target": note, "notes": "Cancelled reminder"}),
    ))

    refused = _guard(intent, arguments, ask, _turn(session, f"req-now-{seed}"))

    assert refused is not None and refused.ok is False and refused.status == "invalid_arguments", refused
    assert note in refused.response_text and ask in refused.response_text
    assert refused.details["executed"] is False


@pytest.mark.parametrize("seed", range(6))
def test_a_message_that_names_it_or_a_read_that_showed_it_is_let_through(seed) -> None:
    rng = random.Random(f"earlier-named-{seed}")
    session = f"chat-named-{seed}"
    note, _said = _earlier_call(rng, session)
    word = max(note.split(), key=len)
    case = seed % 3
    if case == 0:   # the message names it, in another form of the word
        ask, steps = f"move the {word[:-1] + 'ų'} one to friday", []
    elif case == 1:  # this turn's own read showed it
        ask, steps = rng.choice(OTHER_ASKS), [{"tool_name": "operator.list_reminders", "observation": f"Pending reminders:\n- {note} — due soon"}]
    else:           # it was already said once this turn: the resend runs
        ask, steps = rng.choice(OTHER_ASKS), [{"tool_name": "operator.schedule_reminder", "correction": True,
                                               "observation": f'"{note}" is from an earlier message, and this message does not ask about it.'}]

    assert _guard("operator.schedule_reminder", {"note": note, "due_iso": "2026-11-27T10:00"}, ask,
                  _turn(session, f"req-named-{seed}"), steps) is None


def test_an_id_from_an_earlier_turn_is_a_reference_not_a_copy() -> None:
    from core.turn_model_call_ledger import record_session_call

    record_session_call(_turn("chat-id", "req-1"), "operator.schedule_reminder", {"note": "call grandma", "id": "bfb7f9ab"})

    assert _guard("operator.cancel_reminder", {"reminder_id": "bfb7f9ab"}, "cancel it", _turn("chat-id", "req-2")) is None


def test_reads_this_turn_and_approved_resumes_are_not_checked() -> None:
    rng = random.Random("earlier-other")
    note, said = _earlier_call(rng, "chat-other")

    assert _guard("operator.search_reminders", {"query": note}, "anything else?", _turn("chat-other", "req-2")) is None
    assert _guard("operator.schedule_reminder", {"note": note, "text": said}, "ok", _turn("chat-other", "req-earlier")) is None
    assert _guard("operator.schedule_reminder", {"note": note, "text": said}, "ok", _turn("chat-other", "req-3"),
                  resuming_approval=True) is None
    assert _guard("operator.schedule_reminder", {"note": note}, "ok", _turn("another-chat", "req-3")) is None


# ---- served, end to end -----------------------------------------------------------------------------------------

def _reminder_notes(home, session: str) -> list[str]:
    from tests._blackbox_served_rig import REPO_ROOT, run_in_home

    out = run_in_home(home, f'''
import sys, json
sys.path.insert(0, "{REPO_ROOT}")
from storage.db import get_connection
conn = get_connection()
rows = conn.execute("select note from reminder_requests where source_kind = 'reminder'").fetchall()
print("NOTES=" + json.dumps([r[0] for r in rows]))
''')
    line = [x for x in out.splitlines() if x.startswith("NOTES=")][-1]
    import json

    return json.loads(line[len("NOTES="):])


def _said_of(payload: dict) -> str:
    commit = payload.get("vool_response_commit") if isinstance(payload.get("vool_response_commit"), dict) else {}
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    return str(commit.get("canonical_content") or message.get("content") or payload.get("response") or "")


@pytest.mark.parametrize("again", [False, True])
def test_the_served_loop_hands_back_a_replayed_reminder_and_runs_the_asked_one(again, tmp_path) -> None:
    """``again``: the person does ask for the same thing again; the model sends it again after being told, and it runs."""
    import json
    from datetime import datetime, timedelta, timezone
    from typing import Any

    from core.entity_ambiguity import AMBIGUITY_SYSTEM_PROMPT
    from tests._blackbox_served_rig import REPO_ROOT, SEED_MANIFEST, ScriptedProvider, ServedDaemon, run_in_home

    rng = random.Random(f"served-earlier-{datetime.now(timezone.utc):%Y%m%d}")
    model, session = "qwen3-stub:2b", "served-earlier"
    note, said = rng.choice(EARLIER)
    asked_note, ask = rng.choice((
        ("isnesti siuksles", "Priminkit man ketvirtadieni 9:30 isnest siuksles"),
        ("den Zahnarzt anrufen", "Erinnere mich am Freitag um acht, den Zahnarzt anzurufen"),
        ("llamar al fontanero", "recuérdame el sábado a las diez llamar al fontanero"),
    ))
    if again:
        ask = rng.choice(("same again please", "noch einmal bitte", "dar karta", "otra vez, por favor"))
    # Due times stay ahead of the clock: a reminder in the past is refused, and the test ran into that on its due day.
    day = datetime.now() + timedelta(days=2)
    earlier_call = {"due_iso": f"{day:%Y-%m-%d}T19:15", "note": note, "text": said}
    asked_call = {"due_iso": f"{day + timedelta(days=1):%Y-%m-%d}T09:30", "note": asked_note, "text": ask}
    state = {"turn": 0}

    def call(arguments: dict) -> dict:
        return {"id": "call-1", "type": "function",
                "function": {"name": "operator__schedule_reminder", "arguments": arguments}}

    def reply_fn(body: dict[str, Any]) -> Any:
        messages = [m for m in body.get("messages") or [] if isinstance(m, dict)]
        prompt = " ".join(str(m.get("content") or "") for m in messages)
        if AMBIGUITY_SYSTEM_PROMPT in prompt:
            return json.dumps({"ambiguous": False, "referents": [], "clarification": ""})
        if "which of these tools does the message ask" in prompt:
            return json.dumps({"answer": [1]})   # the judge reads both asks as reminders, as on the Mac
        if not body.get("tools"):
            return "Done."
        last = messages[-1] if messages else {}
        if str(last.get("role") or "") == "tool" and "earlier message" not in str(last.get("content") or ""):
            return "Done."
        if state["turn"] == 1:
            return call(earlier_call)
        # Turn 2: the local model copies the previous turn's call until it is told.
        return call(asked_call) if "is from an earlier message" in prompt and not again else call(earlier_call)

    home = tmp_path / "home"
    provider = ScriptedProvider({model: "Done."}, reply_fn=reply_fn)
    daemon = ServedDaemon(home, env_extra={
        "OLLAMA_HOST": provider.base_url, "VOOL_OLLAMA_URL": provider.base_url,
        "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat", "HOME": str(tmp_path / "owner-home"),
    })
    provider.__enter__()
    try:
        try:
            daemon.start(timeout=240)
        except Exception as exc:  # pragma: no cover - environment, not the guard
            pytest.skip(f"served daemon could not boot here: {exc}")
        seed = SEED_MANIFEST + '''
from tests._authorship_certification import certify_for_authorship
certify_for_authorship(manifest({model!r}))
'''
        run_in_home(home, seed.format(root=REPO_ROOT, base_url=provider.base_url, registered=[model], model=model))
        state["turn"] = 1
        daemon.chat(said, session_id=session, model=model, mode="auto")
        first = _reminder_notes(home, session)
        state["turn"] = 2
        answered = daemon.chat(ask, session_id=session, model=model, mode="auto")
        second = _reminder_notes(home, session)
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)

    assert first == [note], first
    assert sorted(second) == sorted([note, note if again else asked_note]), (second, _said_of(answered))


def test_an_ask_another_lane_served_is_not_resent_as_this_ones_text(tmp_path) -> None:
    """Mac gate on f3771b6b7 (Manual mode): the English cat reminder was set by the reminder lane, so no model call of
    an earlier turn held its words; the German oil-change ask then went out with that English ask as its ``text`` and
    note, was approved, and the cat reminder was stored twice. The request as written can only be this message."""
    import uuid
    from datetime import datetime, timedelta

    from core.agent_runtime.research_tool_loop_facade import _EARLIER_MESSAGE_SAID
    from tests._blackbox_served_rig import ServedDaemon, run_in_home
    from tests._served_sufficiency_provider import SEED
    from tests.test_action_receipts_served import REPO, _allow, _approvals, _ReminderProvider, _stored, _turn
    from tests.test_private_chat_served_workflow import MODEL, _certify

    rng = random.Random()
    hour = rng.randint(17, 20)
    first_note, first_ask = rng.choice((
        ("feed the neighbour's cat", f"remind me thursday at {hour}:40 to feed the neighbour's cat"),
        ("water the balcony plants", f"remind me on thursday at {hour}:40 to water the balcony plants"),
    ))
    asked_note, ask = rng.choice((
        ("Ölwechsel", "Erinnere mich am Samstag um 10 Uhr an den Ölwechsel"),
        ("išnešti šiukšles", "Primink šeštadienį 10 val. išnešti šiukšles"),
        ("llamar al fontanero", "recuérdame el sábado a las diez llamar al fontanero"),
    ))
    due = (datetime.now() + timedelta(days=4)).replace(hour=10, minute=0, second=0, microsecond=0)
    copied = {"note": first_note, "due_iso": due.strftime("%Y-%m-%dT%H:%M"), "text": first_ask}
    meant = {"note": asked_note, "due_iso": due.strftime("%Y-%m-%dT%H:%M"), "text": ask}

    class _CopyingModel(_ReminderProvider):
        """A local model that copies the previous ask from the history until it is told."""

        def reply(self, model, body):
            told = any(_EARLIER_MESSAGE_SAID in str((m or {}).get("content") or "") for m in body.get("messages") or [])
            self.arguments = dict(meant if told else copied)
            last = (body.get("messages") or [{}])[-1]
            if told and str((last or {}).get("role")) == "tool" and _EARLIER_MESSAGE_SAID in str(last.get("content")):
                tools = [str(((t or {}).get("function") or {}).get("name") or "") for t in (body.get("tools") or [])]
                name = next((n for n in tools if "schedule_reminder" in n), "")
                if name:
                    return {"content": "", "tool_calls": [{"id": "call-r2", "type": "function",
                                                           "function": {"name": name, "arguments": self.arguments}}]}
            return super().reply(model, body)

    home = tmp_path / "home"
    provider = _CopyingModel()
    with provider:
        run_in_home(home, SEED.format(root=REPO, base_url=provider.base_url, models=[(MODEL, {})]))
        env = {"VOOL_MODEL_LOAD_FLOOR_GB": "0", "OLLAMA_HOST": provider.base_url, "VOOL_OLLAMA_URL": provider.base_url,
               "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat"}
        with ServedDaemon(home, env_extra=env) as daemon:
            assert _certify(daemon).get("state") == "verified"
            session = "openclaw:" + uuid.uuid4().hex[:20]
            for said in (first_ask, ask):
                turn_id = uuid.uuid4().hex
                events, _request_id = _turn(daemon, said, session, turn_id)
                approvals = _approvals(events)
                if approvals:
                    _allow(daemon, str(approvals[0]["approval_id"]), session, turn_id)
                    _turn(daemon, said, session, turn_id, approval_token=str(approvals[0]["approval_id"]))
            notes = sorted(row["note"] for row in _stored(home, session))
    assert notes == sorted([first_note, asked_note]), notes
