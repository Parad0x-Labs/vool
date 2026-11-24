"""A reply that says back what a read of its own source returned is not a change it claims.

Mac gate on 561e3e9d0 (2026-11-23, installed app, qwen3:8b): "got any reminders lined up for me?" ran the reminder list,
and the model answered 'I see you have a reminder scheduled: "return the library books" is due on 26 Nov 2026 at
17:20:00 EET.' "reminder scheduled" reads as a done form, only a read had run, and the person was told "No reminder or
timer was set, changed or cancelled" in place of the right answer.

What the read returned is the evidence: a sentence that names what it showed, with no number or weekday it never
showed, says it back. VOOL speaking of itself ("I've moved it"), a time or day the read never showed, and a read of
another source still leave the sentence a claim, corrected as before. Notes, times and wordings are drawn at random;
one case runs the served daemon end to end with a stub model.
"""
from __future__ import annotations

import json
import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from tests.test_tool_cards_use import SESSION, fresh_turns

NOTES = (
    "return the library books", "pick up the dry cleaning", "renew the parking permit", "water the tomato plants",
    "call the plumber about the boiler", "bring the passport to the bank",
)
ASKS = (
    "got any reminders lined up for me?", "what reminders do i have", "anything i asked you to nudge me about?",
    "show my reminders pls", "do I have something to be reminded of this week?",
)
SAID_BACK = (
    'I see you have a reminder scheduled: "{note}" is due on {wall}.',
    "Your reminder to {note} is scheduled for {wall}.",
    'You have one reminder set: "{note}", due {wall}.',
    "There is a reminder scheduled for {wall}: {note}.",
)


def _wall(rng: random.Random) -> tuple[datetime, str]:
    due = (datetime.now(timezone.utc) + timedelta(days=rng.randint(1, 20))).replace(
        hour=rng.randint(7, 21), minute=rng.choice((0, 15, 20, 30, 45)), second=0, microsecond=0
    )
    return due, due.strftime("%d %b %Y at %H:%M:%S UTC")


def _list_read(note: str, wall: str, rid: str) -> str:
    """What the reminder list returns (`core.operator.handlers.handle_list_reminders`)."""
    return f"Pending reminders:\n- {note} — due {wall} (id {rid})"


def _gate_after(request_text: str, reply: str, request_id: str, *, tool: str, returned: str, asked: Any = None):
    """The gate on a turn whose ledger recorded ``tool`` returning ``returned``, as the tool executor records it."""
    from core.action_claim_gate import gate_action_claims
    from core.grounding_lifecycle import record_model_authorship, register_required
    from core.semantic.semantic_admissions import bound_request_context
    from core.turn_model_call_ledger import begin_turn, record_tool_execution, record_tool_result

    with bound_request_context(request_id):
        context = {"session_id": SESSION, "request_id": request_id, "turn_id": request_id}
        register_required(context, request_text=request_text, reason_codes=("test",))
        record_model_authorship(context)
        begin_turn(context)
        record_tool_execution(context, tool)
        record_tool_result(context, tool, returned, asked or {})
        return gate_action_claims(reply, turn_id=request_id, source_context=context, connected=lambda _n: False)


@pytest.mark.parametrize("seed", range(10))
def test_a_reply_saying_back_the_listed_reminder_is_kept(seed, fresh_turns) -> None:
    rng = random.Random(f"said-back-{seed}")
    note, (_due, wall) = rng.choice(NOTES), _wall(rng)
    reply = rng.choice(SAID_BACK).format(note=note, wall=wall)
    if rng.random() < 0.5:
        reply += " Let me know if you'd like to change it."

    published, record = _gate_after(rng.choice(ASKS), reply, f"req:said-back:{seed}", tool="operator.list_reminders",
                                    returned=_list_read(note, wall, f"{rng.getrandbits(32):08x}"))

    assert published == reply, (reply, record)
    assert "No reminder" not in published


@pytest.mark.parametrize("seed", range(6))
def test_a_time_or_day_the_read_never_showed_is_still_a_change_claim(seed, fresh_turns) -> None:
    rng = random.Random(f"said-new-{seed}")
    note, (due, wall) = rng.choice(NOTES), _wall(rng)
    other = due + timedelta(days=rng.randint(1, 5), hours=rng.choice((1, 2, 3)))
    told = rng.choice((
        f"Your reminder to {note} is now scheduled for {other.strftime('%d %b %Y at %H:%M')}.",
        f"Your reminder to {note} is set for {other.strftime('%A')}.",
        f'The reminder "{note}" has been moved to {other.strftime("%H:%M")} on {other.strftime("%A")}.',
    ))

    published, record = _gate_after(f"move my {note.split()[-1]} reminder", told, f"req:said-new:{seed}",
                                    tool="operator.list_reminders", returned=_list_read(note, wall, "3bbb4fbf"))

    assert record.get("publication") == "action_claims_corrected", (told, record)
    assert told not in published


@pytest.mark.parametrize("seed", range(4))
def test_vool_saying_it_did_the_change_after_a_read_is_still_corrected(seed, fresh_turns) -> None:
    rng = random.Random(f"said-i-{seed}")
    note, (_due, wall) = rng.choice(NOTES), _wall(rng)
    told = rng.choice(("I've scheduled a reminder to {note} on {wall}.", "Done, I set your reminder to {note} for {wall}."))
    told = told.format(note=note, wall=wall)

    published, record = _gate_after(f"remind me to {note}", told, f"req:said-i:{seed}",
                                    tool="operator.list_reminders", returned=_list_read(note, wall, "9c0ffee1"))

    assert record.get("publication") == "action_claims_corrected", (told, record)
    assert told not in published


def test_a_read_of_another_source_does_not_back_the_sentence(fresh_turns) -> None:
    rng = random.Random("said-other")
    note, (_due, wall) = rng.choice(NOTES), _wall(rng)
    told = f"Your reminder to {note} is scheduled for {wall}."
    published, record = _gate_after("what's on", told, "req:said-other", tool="operator.list_calendar_events",
                                    returned=_list_read(note, wall, "0a1b2c3d"))

    assert record.get("publication") == "action_claims_corrected", record
    assert told not in published


@pytest.mark.parametrize("seed", range(4))
def test_what_a_search_only_echoed_back_from_its_query_backs_nothing(seed, fresh_turns) -> None:
    rng = random.Random(f"said-echo-{seed}")
    note, (_due, wall) = rng.choice(NOTES), _wall(rng)
    query = f"{note} {wall}"
    told = f"Your reminder to {note} is scheduled for {wall}."

    published, record = _gate_after(f"find my reminder about {note}", told, f"req:said-echo:{seed}",
                                    tool="operator.search_reminders",
                                    returned=f'No pending reminders match "{query}".', asked={"query": query})

    assert record.get("publication") == "action_claims_corrected", (told, record)
    assert told not in published


# ---- served, end to end -----------------------------------------------------------------------------------------

def test_the_served_reminder_list_answer_reaches_the_person(tmp_path: Path) -> None:
    from core.entity_ambiguity import AMBIGUITY_SYSTEM_PROMPT
    from tests._blackbox_served_rig import REPO_ROOT, SEED_MANIFEST, ScriptedProvider, ServedDaemon, run_in_home

    rng = random.Random(f"served-said-back-{datetime.now(timezone.utc):%Y%m%d}")
    model = "qwen3-stub:2b"
    session = "served-said-back"
    note, (due, wall) = rng.choice(NOTES), _wall(rng)
    reply = rng.choice(SAID_BACK).format(note=note, wall=wall)
    call = {"id": "call-1", "type": "function", "function": {"name": "operator__list_reminders", "arguments": {}}}
    rounds: list[int] = []

    def reply_fn(body: dict[str, Any]) -> Any:
        prompt = " ".join(str(m.get("content") or "") for m in body.get("messages") or [] if isinstance(m, dict))
        if AMBIGUITY_SYSTEM_PROMPT in prompt:
            return json.dumps({"ambiguous": False, "referents": [], "clarification": ""})
        judged = re.search(r'(\d+)\. "Reminders:', prompt)
        if judged and "which of these tools does the message ask" in prompt:
            # The turn's tool judge reads every wording as the reminder ask it is, as the local model does. With
            # no judgement the words alone decide, and "do I have something to be reminded of this week?" was
            # seated as a web lookup with no reminder tool (CI on 412af1a79): that is the judge's job, not
            # this gate's, so the stub answers the judge.
            return json.dumps({"answer": [int(judged.group(1))]})
        offered = {str((tool.get("function") or {}).get("name") or "") for tool in body.get("tools") or []
                   if isinstance(tool, dict)}
        if "operator__list_reminders" in offered:
            # The call goes to the first request that offers the list, as a model would make it.
            rounds.append(1)
            if len(rounds) == 1:
                return call
        return reply

    home = tmp_path / "home"
    provider = ScriptedProvider({model: reply}, reply_fn=reply_fn)
    daemon = ServedDaemon(home, env_extra={
        "OLLAMA_HOST": provider.base_url, "VOOL_OLLAMA_URL": provider.base_url,
        "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat", "HOME": str(tmp_path / "owner-home"),
    })
    provider.__enter__()
    try:
        try:
            daemon.start(timeout=240)
        except Exception as exc:  # pragma: no cover - environment, not the gate
            pytest.skip(f"served daemon could not boot here: {exc}")
        seed = SEED_MANIFEST + f'''
from tests._authorship_certification import certify_for_authorship
certify_for_authorship(manifest({model!r}))
from core.operator.reminders import schedule_reminder
from storage.db import get_connection
schedule_reminder(session_id={session!r}, task_id="seed", note={note!r}, due_at_utc={due.isoformat()!r},
                  tz_name="UTC", get_connection_fn=get_connection)
'''
        run_in_home(home, seed.format(root=REPO_ROOT, base_url=provider.base_url, registered=[model], model=model))
        provider.reset()
        payload = daemon.chat(rng.choice(ASKS), session_id=session, model=model, mode="auto")
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)
    commit = payload.get("vool_response_commit") if isinstance(payload.get("vool_response_commit"), dict) else {}
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    text = str(commit.get("canonical_content") or message.get("content") or payload.get("response") or "")

    assert rounds, "the model was never offered a tool"
    assert "No reminder or timer was set" not in text, text
    assert note in text, text
