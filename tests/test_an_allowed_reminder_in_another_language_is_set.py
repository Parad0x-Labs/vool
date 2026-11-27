"""A reminder the person allowed is set, whatever language it was asked in, over the real /api/chat door.

Real-app probe on the installed 3dfb2f34 (2026-11-22, Manual mode, local qwen3:8b): "Primink man poryt 9 val.
palaistyti gėles" reached the reminder tool through the turn's judge and the card was allowed. The resumed turn was
read again from its Lithuanian words alone, which seat no tool, so it was offered none: the approved call never ran,
and a plain answer said "Reminder scheduled" with an earlier reminder's id while nothing was set.

The resumed turn now runs the call the person allowed. The judge is scripted to pick the reminders once only (a
second judgement may differ), and the note and the day are built from parts at random.

Cut-list 29 (2026-11-27): a call whose time and note are the message's own, in a message that is no question, is the
person's order (core.operator.reminder_order) and is set with no card, as "remind me ..." is by the chat lane in Manual
mode. The card, its allow and the resume stay for a call the message does not plainly carry: a time the words never
give in numbers, or an order put as a question.
"""
from __future__ import annotations

import json
import random
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import pytest

from tests._blackbox_served_rig import ServedDaemon, run_in_home
from tests._served_sufficiency_provider import SEED
from tests.test_private_chat_served_workflow import MODEL, _certify, _Provider

REPO = Path(__file__).resolve().parents[1]
_RNG = random.Random(20261122)
NOTES = ("palaistyti gėles", "nupirkti duonos", "paskambinti mamai", "išnešti šiukšles", "sumokėti už butą")
DAYS = (("rytoj", 1), ("poryt", 2))


class _ReminderProvider(_Provider):
    """The judge picks Reminders on its first question only; the model calls the reminder tool when offered it."""

    def __init__(self, note: str, due_iso: str) -> None:
        super().__init__({MODEL: "Gerai."})
        self.note, self.due_iso = note, due_iso
        self.judged = 0
        self.offered_reminder_tool = 0

    def reply(self, model: str, body: dict[str, Any]) -> Any:
        text = json.dumps(body, ensure_ascii=False)
        if "vool_probe" in text or "probe." in text:  # the model's tool certification keeps its own choreography
            return super().reply(model, body)
        if "The assistant has the tools below" in text:
            self.judged += 1
            return '{"answer": [1]}' if self.judged == 1 else '{"answer": []}'
        tools = [str(((t or {}).get("function") or {}).get("name") or "") for t in (body.get("tools") or [])
                 if isinstance(t, dict)]
        reminder_tool = next((name for name in tools if "schedule_reminder" in name), "")
        if any(str((m or {}).get("role")) == "tool" for m in body.get("messages") or []):
            return "Priminimas nustatytas."
        if reminder_tool:
            self.offered_reminder_tool += 1
            arguments = {"note": self.note, "due_iso": self.due_iso}
            return {"content": "", "tool_calls": [
                {"id": "call-reminder-1", "type": "function", "function": {"name": reminder_tool, "arguments": arguments}}]}
        return super().reply(model, body)


def _turn(daemon: ServedDaemon, text: str, session: str, turn_id: str, **extra: Any) -> list[dict[str, Any]]:
    body = {"messages": [{"role": "user", "content": text}], "stream": True, "stream_task_events": True,
            "session_id": session, "turn_id": turn_id, "model": MODEL, "model_selection": "sticky", "mode": "manual",
            "surface": "desktop", **extra}
    request = Request(f"{daemon.base_url}/api/chat", data=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
    events = []
    with urlopen(request, timeout=600) as response:
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            try:
                frame = json.loads(line) if line else None
            except json.JSONDecodeError:
                continue
            if isinstance(frame, dict) and frame.get("vool_event"):
                events.append(frame["vool_event"])
    return events


def _allow(daemon: ServedDaemon, approval_id: str, session: str, turn_id: str) -> None:
    body = {"op": "resolve_approval", "approval_id": approval_id, "decision": "allow", "scope": "once",
            "session_id": session, "turn_id": turn_id}
    request = Request(f"{daemon.base_url}/api/mode", data=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=60) as response:
        assert response.status == 200


def _rows(home, session: str) -> list[dict[str, Any]]:
    return json.loads(run_in_home(home, (
        "import json\n"
        "from core.operator.reminders import list_reminders\n"
        "from storage.db import get_connection\n"
        f"print(json.dumps(list_reminders(session_id={session!r}, get_connection_fn=get_connection)))\n"
    )).strip().splitlines()[-1])


def _served(tmp_path, provider):
    home = tmp_path / "home"
    run_in_home(home, SEED.format(root=REPO, base_url=provider.base_url, models=[(MODEL, {})]))
    env = {"VOOL_MODEL_LOAD_FLOOR_GB": "0", "OLLAMA_HOST": provider.base_url, "VOOL_OLLAMA_URL": provider.base_url,
           "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat"}
    return home, ServedDaemon(home, env_extra=env)


def test_a_lithuanian_reminder_order_is_set_with_no_card(tmp_path):
    note = _RNG.choice(NOTES)
    word, days = _RNG.choice(DAYS)
    hour = _RNG.randint(8, 11)
    due = (datetime.now() + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)
    text = f"Primink man {word} {hour} val. {note}"
    provider = _ReminderProvider(note, due.strftime("%Y-%m-%dT%H:%M"))
    with provider:
        home, served = _served(tmp_path, provider)
        with served as daemon:
            assert _certify(daemon).get("state") == "verified"
            session = "openclaw:" + uuid.uuid4().hex[:20]
            asked = _turn(daemon, text, session, uuid.uuid4().hex)
            assert not [event for event in asked if event.get("type") == "permission.required"], (
                provider.judged, [(e.get("type"), str(e.get("summary") or "")[:60]) for e in asked][-8:])
            rows = _rows(home, session)
    assert [row["note"] for row in rows if row["status"] == "scheduled"] == [note], (text, rows)
    assert provider.judged >= 1 and provider.offered_reminder_tool == 1


@pytest.mark.parametrize("shape", ["{Word} ryte {note}", "Gal gali {word} {hour} val. priminti {note}?"])
def test_an_allowed_lithuanian_reminder_is_set_when_the_turn_resumes(shape, tmp_path):
    """A call the message does not plainly carry (its 9:00 said only as "ryte", or the order asked as a question)
    still asks first, and the allowed call runs when the turn resumes."""
    note = _RNG.choice(NOTES)
    word, days = _RNG.choice(DAYS)
    hour = 9 if "ryte" in shape else _RNG.randint(8, 11)
    due = (datetime.now() + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)
    text = shape.format(Word=word.capitalize(), word=word, hour=hour, note=note)
    provider = _ReminderProvider(note, due.strftime("%Y-%m-%dT%H:%M"))
    with provider:
        home, served = _served(tmp_path, provider)
        with served as daemon:
            assert _certify(daemon).get("state") == "verified"
            session = "openclaw:" + uuid.uuid4().hex[:20]
            turn_id = uuid.uuid4().hex
            asked = _turn(daemon, text, session, turn_id)
            approvals = [event.get("approval") or {} for event in asked if event.get("type") == "permission.required"]
            assert approvals and "schedule_reminder" in str(approvals[0].get("intent")), (
                provider.judged, [(e.get("type"), str(e.get("summary") or "")[:60]) for e in asked][-8:])
            assert _rows(home, session) == []
            _allow(daemon, str(approvals[0]["approval_id"]), session, turn_id)
            _turn(daemon, text, session, turn_id, approval_token=str(approvals[0]["approval_id"]))
            rows = _rows(home, session)
    assert [row["note"] for row in rows if row["status"] == "scheduled"] == [note], (text, rows)
    assert provider.judged >= 1 and provider.offered_reminder_tool == 1
