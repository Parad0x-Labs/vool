"""Served: everyday remember asks, updates and recall through the routing a real local model takes.

Measured on the Mac (cbc915ec and 617983f9, fresh VOOL home, the owner's real home folder, qwen3:8b): with the
scripted model of tests/test_everyday_remember_v08.py every turn passed, but on the real model
  - "small update: the boat dates changed ...", "and the planning call went back to 10:00" and "keep in mind to
    renew the parking permit" were taken by the clean loop, because their words name the owner's files, and
    answered from a file search; nothing was saved or replaced;
  - "who is my team lead?", "what's the booking code for the boat?" and "remind me what my team lead is called"
    were answered from the owner's files while the facts sat in memory;
  - "what day does Anselm water the plants?" was asked back "Anselm of Canterbury or the actor?".
This rig gives the daemon what the Mac had: an owner home whose file names and documents share those words, a
planner that splits "A and B", an entity check that calls Anselm ambiguous, and a clean loop model that searches
and then answers. The VOOL model answers only from the memory it is shown. All names, places and dates are invented.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from tests.test_private_chat_served_workflow import _Provider

MODEL = "stub-private:4b"
FILE_LOOP = "FILE-LOOP: I looked in your files and found files about that."


class _RealRoutingProvider(_Provider):
    def reply(self, model: str, body: dict[str, Any]) -> Any:
        messages = body.get("messages") or []
        system = "\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")
        last = next((str(m.get("content") or "") for m in reversed(messages) if m.get("role") == "user"), "")
        if "Tools (reply with ONE JSON object per step)" in system:  # the clean loop's own model
            if not any(m.get("role") == "assistant" for m in messages):
                words = re.findall(r"[a-z]{4,}", last.lower())[:2]
                return json.dumps({"tool": "find", "query": " ".join(words) or "notes"})
            return json.dumps({"answer": FILE_LOOP})
        if "You split a user's message into the separate requests" in system:
            parts = [part.strip() for part in re.split(r"\s+and\s+", last) if part.strip()]
            return json.dumps({"requests": [{"request": part, "depends_on": []} for part in parts]})
        if "well-known referents" in json.dumps(body):
            if "Anselm" in last:
                return json.dumps({"ambiguous": True, "referents": ["Anselm of Canterbury", "Anselm Kiefer"],
                                   "clarification": "Which Anselm do you mean?"})
            return '{"ambiguous": false, "referents": [], "clarification": ""}'
        if "You are VOOL" in system[:200]:
            shown = "\n".join(str(m.get("content") or "") for m in messages[:-1])  # memory rides the context turn
            facts = re.findall(r"Persistent memory fact: ([^\n]+)", shown)
            asked = set(re.findall(r"[a-z]{4,}", last.lower())) - {"what", "remind", "called", "does", "with"}
            facts = [fact for fact in facts if asked & set(re.findall(r"[a-z]{4,}", fact.lower()))]
            return ("From memory: " + "; ".join(facts)) if facts else "NO-MEMORY-SHOWN"
        return super().reply(model, body)


def _owner_home(root: Path) -> Path:
    documents = root / "Documents"
    documents.mkdir(parents=True)
    (documents / "boat booking update.md").write_text("Boat booking notes. Lake Orrin Boat Club. The booking code is on page 2.\n")
    (documents / "parking permit renewal.md").write_text("Parking Permit renewal checklist for the council.\n")
    (documents / "team lead handbook.md").write_text("Team Lead duties: the Lead runs the planning call.\n")
    (documents / "planning call notes.md").write_text("Planning Call agenda and notes.\n")
    (documents / "Anselm reading list.md").write_text("Anselm of Canterbury, Proslogion. Anselm Kiefer catalogue.\n")
    (documents / "plants watering.md").write_text("Water the plants: ferns weekly.\n")
    return root


def _stream(base_url: str, text: str, session: str) -> str:
    body = {"messages": [{"role": "user", "content": text}], "stream": True, "session_id": session, "model": MODEL}
    request = Request(f"{base_url}/api/chat", data=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
    answer, commit = "", {}
    with urlopen(request, timeout=600) as response:
        for raw in response:
            line = raw.decode("utf-8").strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(frame.get("vool_response_commit"), dict):
                commit = frame["vool_response_commit"]
            answer += str((frame.get("message") or {}).get("content") or "")
    return str(commit.get("canonical_content") or answer)


CHATS = [
    ["hey, couple of things to remember for me pls. my neighbour Anselm waters the plants on fridays",
     "we booked the boat on lake Orrin for 4-7 june, booking code BR-5527",
     "also i always prefer aisle seats on trains"],
    ["work stuff to keep in mind: our planning call moved to 10:15 every monday",
     "my team lead is Rosalind and the audit is on january 20"],
    ["small update: the boat dates changed, its now 11-14 june, same booking code",
     "and the planning call went back to 10:00"],
    ["keep in mind that I can't eat shellfish"],
    ["keep in mind to renew the parking permit"],
]
RECALL = {
    "remind me what my team lead is called": "Rosalind",
    "who is my team lead?": "Rosalind",
    "what's the booking code for the boat?": "BR-5527",
    "what day does Anselm water the plants?": "fridays",
}


def test_remember_update_and_recall_stay_with_memory_on_the_real_routing(tmp_path: Path):
    from tests._blackbox_served_rig import ServedDaemon, run_in_home
    from tests._served_sufficiency_provider import SEED
    from tests.test_private_chat_served_workflow import _certify

    repo = Path(__file__).resolve().parents[1]
    home = tmp_path / "vool-home"
    owner = _owner_home(tmp_path / "owner")
    remembered: list[tuple[str, str]] = []
    recalled: dict[str, str] = {}
    with _RealRoutingProvider({MODEL: "Sure, noted."}) as provider:
        run_in_home(home, SEED.format(root=repo, base_url=provider.base_url, models=[(MODEL, {})]))
        with ServedDaemon(home, env_extra={"VOOL_MODEL_LOAD_FLOOR_GB": "0", "HOME": str(owner)}) as daemon:
            assert _certify(daemon).get("state") == "verified"
            for n, turns in enumerate(CHATS):
                for text in turns:
                    remembered.append((text, _stream(daemon.base_url, text, f"real-routing-{n}")))
            for n, text in enumerate(RECALL):
                recalled[text] = _stream(daemon.base_url, text, f"real-routing-recall-{n}")
            with urlopen(f"{daemon.base_url}/api/memory/entries?limit=200", timeout=60) as response:
                facts = [" ".join(str(row.get("fact") or "").split())
                         for row in json.load(response).get("entries") or []]
    routes = [json.loads(line).get("msg", "") for line in (home / "data" / "logs" / "vool_api.log").read_text().splitlines()
              if "clean loop route" in line]
    assert routes and "clean loop route: admitted" not in routes, routes  # the owner's files are never searched
    for text, reply in remembered:
        assert reply.startswith(("Locked in.", "Updated.")), (text, reply)
        assert "Nothing was saved" not in reply and FILE_LOOP not in reply, (text, reply)
    assert "my team lead is Rosalind and the audit is on january 20" in facts
    assert "renew the parking permit" in facts
    assert any(fact.startswith("the boat dates changed, its now 11-14 june, same booking code") for fact in facts)
    assert any(fact.startswith("the planning call went back to 10:00") for fact in facts)
    assert "we booked the boat on lake Orrin for 4-7 june, booking code BR-5527" not in facts
    assert "our planning call moved to 10:15 every monday" not in facts
    for text, value in RECALL.items():
        reply = recalled[text]
        assert value.lower() in reply.lower(), (text, reply)
        assert "file" not in reply.lower() and "Which Anselm" not in reply, (text, reply)
