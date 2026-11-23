"""Served proof: a consequential action shows what it did under the answer, read from its result and checked.

One isolated daemon, Manual mode, a scripted model that picks the reminder tool when the turn's judge offers it.
Driven over the real /api/chat door and the real approval door; the receipt is read back from the real proof
route. Notes, days and hours are built from parts at random, never a fixed sentence.

- before the person allows it: the reminder is "waiting", nothing is set;
- after they allow it: "checked" -- the reminder row is in the store with the due instant the receipt names;
- a time already gone: "failed", with the tool's own reason, and nothing is stored.
"""
from __future__ import annotations

import json
import random
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from tests._blackbox_served_rig import ServedDaemon, run_in_home
from tests._served_sufficiency_provider import SEED
from tests.test_private_chat_served_workflow import MODEL, _certify, _Provider

REPO = Path(__file__).resolve().parents[1]
_RNG = random.Random()
NOTES = ("repot the fern", "renew the bike lock code", "return the library atlas", "book the chimney sweep",
         "defrost the freezer", "send the plumber invoice")
OPENERS = ("remind me to {n} {d} at {h}", "can you set a reminder: {n}, {d} {h}", "{d} at {h} nudge me to {n} pls",
           "pls remind me {d} {h} to {n}")


class _ReminderProvider(_Provider):
    def __init__(self) -> None:
        super().__init__({MODEL: "Okay."})
        self.arguments: dict[str, Any] = {}

    def reply(self, model: str, body: dict[str, Any]) -> Any:
        text = json.dumps(body, ensure_ascii=False)
        if "vool_probe" in text or "probe." in text:
            return super().reply(model, body)
        if "The assistant has the tools below" in text:
            return '{"answer": [1]}'
        tools = [str(((t or {}).get("function") or {}).get("name") or "") for t in (body.get("tools") or [])
                 if isinstance(t, dict)]
        reminder_tool = next((name for name in tools if "schedule_reminder" in name), "")
        if any(str((m or {}).get("role")) == "tool" for m in body.get("messages") or []):
            return "All set."
        if reminder_tool and self.arguments:
            return {"content": "", "tool_calls": [{"id": "call-r-" + uuid.uuid4().hex[:6], "type": "function",
                                                   "function": {"name": reminder_tool, "arguments": self.arguments}}]}
        return super().reply(model, body)


def _turn(daemon: ServedDaemon, text: str, session: str, turn_id: str, **extra: Any) -> tuple[list[dict], str]:
    body = {"messages": [{"role": "user", "content": text}], "stream": True, "stream_task_events": True,
            "session_id": session, "turn_id": turn_id, "model": MODEL, "model_selection": "sticky", "mode": "manual",
            "surface": "desktop", **extra}
    request = Request(f"{daemon.base_url}/api/chat", data=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
    events, request_id = [], ""
    with urlopen(request, timeout=600) as response:
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            try:
                frame = json.loads(line) if line else None
            except json.JSONDecodeError:
                continue
            if not isinstance(frame, dict):
                continue
            if frame.get("vool_event"):
                events.append(frame["vool_event"])
            commit = frame.get("vool_response_commit")
            if isinstance(commit, dict) and commit.get("request_id"):
                request_id = str(commit["request_id"])
    return events, request_id


def _receipts(daemon: ServedDaemon, session: str, request_id: str) -> list[dict[str, Any]]:
    assert request_id, "the turn committed no request id"
    with urlopen(f"{daemon.base_url}/api/chat/proof?{urlencode({'session': session, 'request_id': request_id})}",
                 timeout=60) as r:
        return json.loads(r.read().decode())["compact"]["action_receipts"]


def _allow(daemon: ServedDaemon, approval_id: str, session: str, turn_id: str) -> None:
    body = {"op": "resolve_approval", "approval_id": approval_id, "decision": "allow", "scope": "once",
            "session_id": session, "turn_id": turn_id}
    request = Request(f"{daemon.base_url}/api/mode", data=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=60) as response:
        assert response.status == 200


def _stored(home: Path, session: str) -> list[dict[str, Any]]:
    return json.loads(run_in_home(home, (
        "import json\n"
        "from core.operator.reminders import list_reminders\n"
        "from storage.db import get_connection\n"
        f"print(json.dumps(list_reminders(session_id={session!r}, get_connection_fn=get_connection)))\n"
    )).strip().splitlines()[-1])


def _approvals(events: list[dict]) -> list[dict]:
    return [e.get("approval") or {} for e in events if e.get("type") == "permission.required" and e.get("approval")]


def test_reminders_show_what_was_set_waiting_checked_or_why_not(tmp_path):
    note = _RNG.choice(NOTES)
    days = _RNG.randint(2, 4)
    hour = _RNG.randint(8, 11)
    due = (datetime.now() + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)
    text = _RNG.choice(OPENERS).format(n=note, d="on " + due.strftime("%A"), h=f"{hour}am")
    home = tmp_path / "home"
    provider = _ReminderProvider()
    with provider:
        run_in_home(home, SEED.format(root=REPO, base_url=provider.base_url, models=[(MODEL, {})]))
        env = {"VOOL_MODEL_LOAD_FLOOR_GB": "0", "OLLAMA_HOST": provider.base_url, "VOOL_OLLAMA_URL": provider.base_url,
               "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat"}
        with ServedDaemon(home, env_extra=env) as daemon:
            assert _certify(daemon).get("state") == "verified"
            session = "openclaw:" + uuid.uuid4().hex[:20]

            # 1 and 2. Whichever lane takes the ask (the front door, or the model's tool call held for the
            # person's OK), the last receipt is "checked" against the stored row; a held call is "waiting" first.
            lt_note = _RNG.choice(("palaistyti gėles", "nupirkti duonos", "paskambinti mamai"))
            for ask, wanted in ((text, note), (f"Primink man poryt {hour} val. {lt_note}", lt_note)):
                provider.arguments = {"note": wanted, "due_iso": due.strftime("%Y-%m-%dT%H:%M")}
                before = len(_stored(home, session))
                turn_id = uuid.uuid4().hex
                asked, request_id = _turn(daemon, ask, session, turn_id)
                approvals = _approvals(asked)
                if approvals:
                    assert "schedule_reminder" in str(approvals[0].get("intent")), approvals
                    assert [r["state"] for r in _receipts(daemon, session, request_id)] == ["waiting"]
                    assert len(_stored(home, session)) == before, "nothing may be set before the OK"
                    _allow(daemon, str(approvals[0]["approval_id"]), session, turn_id)
                    _, request_id = _turn(daemon, ask, session, turn_id, approval_token=str(approvals[0]["approval_id"]))
                rows = [row for row in _stored(home, session) if row["status"] == "scheduled"]
                assert len(rows) == before + 1, (ask, bool(approvals), [(r["note"], r["task_id"][:6], r["created_at"]) for r in rows])
                row = max(rows, key=lambda r: r["created_at"])
                last = _receipts(daemon, session, request_id)[-1]
                assert last["state"] == "checked", (ask, last)
                assert last["due_utc"] == row["due_at_utc"] and last["note"] == row["note"], (last, row)
                assert last["action_key"] == "approval.action.operator.schedule_reminder"

            # 3. A time already gone: the tool's own reason, nothing stored, no tool id in the words.
            before = len(_stored(home, session))
            _, past_request = _turn(daemon, f"remind me yesterday at {hour}am to feed the koi", session, uuid.uuid4().hex)
            failed = _receipts(daemon, session, past_request)
            assert [r["state"] for r in failed] == ["failed"] and failed[0]["reason"], failed
            assert "`" not in failed[0]["reason"] and "operator." not in failed[0]["reason"], failed
            assert len(_stored(home, session)) == before
