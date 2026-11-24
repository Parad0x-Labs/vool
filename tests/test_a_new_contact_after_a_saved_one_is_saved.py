"""A new contact asked right after another one was saved is saved, through the served tool loop.

Mac gate on 608294d38 (2026-11-24, installed app, qwen3:8b): after "Um please store Pavel Lindqvist's number:
+1 555 0182" was approved and saved, "btw new cnotact pavel duarte, +44 20 7946 0182?" ended with "Internal tool result
did not match the requested operation. The task was stopped safely." Two causes, both general: the local model sent
the earlier save again (the earlier name, which shares "pavel" with this message, and the earlier number, which the
earlier-message check never compared because a number has no words); and a save that changes nothing came back from
the executor named contacts.search, which the tool loop reads as the result of another call than the one it sent.

Served end to end (a real daemon, a scripted local model that copies the earlier call until it is told). Names,
numbers and wordings are drawn at random, in several languages.
"""
from __future__ import annotations

import json
import random
import re
import uuid
from typing import Any

import pytest

from tests._blackbox_served_rig import ServedDaemon, run_in_home
from tests._served_sufficiency_provider import SEED
from tests.test_action_receipts_served import REPO, _allow, _approvals
from tests.test_private_chat_served_workflow import MODEL, _certify, _Provider

FIRST_NAMES = ("Pavel", "Ieva", "Jonas", "Marta", "Tomas", "Greta")
SURNAMES = ("Lindqvist", "Duarte", "Kowalski", "Petrauskas", "Moreau", "Varga", "Okafor")
FIRST_ASKS = (
    "Um please store {name}'s number: {phone}",
    "save {name} to my contacts, phone {phone}",
    "išsaugok kontaktą {name}, telefonas {phone}",
    "speichere bitte {name} mit der Nummer {phone}",
)
NEXT_ASKS = (
    "btw new cnotact {name}, {phone}?",
    "and add {name} too: {phone}",
    "dar vienas kontaktas: {name} {phone}",
    "noch ein Kontakt bitte, {name}, Telefon {phone}",
    "guarda también a {name}, {phone}",
)


def _phone(rng: random.Random) -> str:
    return rng.choice((
        "+1 555 {a:04d}", "+44 20 7946 {a:04d}", "+370 6{b:02d} {a:04d}5", "+49 30 {b:02d}{a:04d}",
    )).format(a=rng.randint(0, 9999), b=rng.randint(10, 99))


def _contacts_tool_number(prompt: str) -> int:
    """The number the judge's question gives the Contacts choice (the last list in the prompt is the real one)."""
    line = [row for row in prompt.splitlines() if row.startswith("Tools:")][-1]
    found = re.search(r'(\d+)\. "Contacts:', line)
    return int(found.group(1)) if found else 0


class _CopyingModel(_Provider):
    """A local model that sends the first turn's save, and on the second turn sends that same save again until it is
    told the call is the earlier message's (``stubborn``: it sends the copy once more after being told)."""

    def __init__(self, first: dict[str, Any], meant: dict[str, Any], *, stubborn: bool) -> None:
        super().__init__({MODEL: "Okay."})
        self.first, self.meant, self.stubborn = first, meant, stubborn
        self.turn = 1
        self.copies_sent = 0

    def reply(self, model: str, body: dict[str, Any]) -> Any:
        text = json.dumps(body, ensure_ascii=False)
        if "vool_probe" in text or "probe." in text:
            return super().reply(model, body)
        if "The assistant has the tools below" in text:
            prompt = " ".join(str((m or {}).get("content") or "") for m in body.get("messages") or [])
            return json.dumps({"answer": [_contacts_tool_number(prompt)]})
        tools = [str(((t or {}).get("function") or {}).get("name") or "") for t in (body.get("tools") or [])
                 if isinstance(t, dict)]
        save_tool = next((name for name in tools if name.endswith("save") and "contact" in name), "")
        messages = [m for m in body.get("messages") or [] if isinstance(m, dict)]
        last = messages[-1] if messages else {}
        if not save_tool:
            return super().reply(model, body)
        if str(last.get("role")) == "tool":
            content = str(last.get("content") or "")
            told = "is from an earlier message" in content or "Nothing was saved" in content
            if not told:
                return "Done."
        if self.turn == 1:
            arguments = self.first
        elif self.copies_sent == 0 or (self.stubborn and self.copies_sent == 1):
            self.copies_sent += 1
            arguments = self.first
        else:
            arguments = self.meant
        return {"content": "", "tool_calls": [{"id": "call-c-" + uuid.uuid4().hex[:6], "type": "function",
                                               "function": {"name": save_tool, "arguments": dict(arguments)}}]}


def _saved(home) -> list[dict[str, Any]]:
    return json.loads(run_in_home(home, (
        "import json\n"
        "from core.contacts import store\n"
        "print(json.dumps([{'name': c['display_name'], 'values': [e['value'] for e in c['endpoints']]}"
        " for c in store.active_contacts()]))\n"
    )).strip().splitlines()[-1])


def _turn(daemon: ServedDaemon, text: str, session: str, turn_id: str, **extra: Any) -> tuple[list[dict], str]:
    """One chat turn as the desktop page sends it: its task events and the reply it committed."""
    from urllib.request import Request, urlopen

    body = {"messages": [{"role": "user", "content": text}], "stream": True, "stream_task_events": True,
            "session_id": session, "turn_id": turn_id, "model": MODEL, "model_selection": "sticky", "mode": "manual",
            "surface": "desktop", **extra}
    request = Request(f"{daemon.base_url}/api/chat", data=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
    events, said = [], []
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
            if isinstance(commit, dict) and commit.get("canonical_content"):
                said.append(str(commit["canonical_content"]))
    return events, " ".join(said)


@pytest.mark.parametrize("stubborn", [False, True])
def test_a_new_contact_after_a_saved_one_is_saved_or_answered_never_stopped(stubborn, tmp_path) -> None:
    rng = random.Random()
    first_name = rng.choice(FIRST_NAMES)
    earlier_name = f"{first_name} {rng.choice(SURNAMES)}"
    new_name = f"{first_name} {rng.choice([s for s in SURNAMES if s not in earlier_name])}"
    earlier_phone, new_phone = _phone(rng), _phone(rng)
    first_ask = rng.choice(FIRST_ASKS).format(name=earlier_name, phone=earlier_phone)
    next_ask = rng.choice(NEXT_ASKS).format(name=new_name.lower() if rng.random() < 0.5 else new_name, phone=new_phone)
    first = {"name": earlier_name, "phone": earlier_phone}
    meant = {"name": new_name, "phone": new_phone}

    home = tmp_path / "home"
    provider = _CopyingModel(first, meant, stubborn=stubborn)
    with provider:
        run_in_home(home, SEED.format(root=REPO, base_url=provider.base_url, models=[(MODEL, {})]))
        env = {"VOOL_MODEL_LOAD_FLOOR_GB": "0", "OLLAMA_HOST": provider.base_url, "VOOL_OLLAMA_URL": provider.base_url,
               "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat"}
        with ServedDaemon(home, env_extra=env) as daemon:
            assert _certify(daemon).get("state") == "verified"
            session = "openclaw:" + uuid.uuid4().hex[:20]
            seen: list[dict] = []
            replies: list[str] = []
            for turn, said in ((1, first_ask), (2, next_ask)):
                provider.turn = turn
                turn_id = uuid.uuid4().hex
                events, reply = _turn(daemon, said, session, turn_id)
                seen += events
                replies.append(reply)
                approvals = _approvals(events)
                if approvals:
                    _allow(daemon, str(approvals[0]["approval_id"]), session, turn_id)
                    resumed, reply = _turn(daemon, said, session, turn_id,
                                           approval_token=str(approvals[0]["approval_id"]))
                    seen += resumed
                    replies.append(reply)
            saved = _saved(home)

    names = sorted(row["name"] for row in saved)
    reply = " | ".join(replies)
    assert all(replies), replies
    assert "did not match the requested operation" not in reply, reply[-2000:]
    assert "stale_result_rejected" not in json.dumps(seen), "the tool loop stopped the turn on a result mismatch"
    assert names == sorted([earlier_name, new_name]), (names, next_ask, reply[-1500:])
