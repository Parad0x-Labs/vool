"""Served proof: setup's "Try three things" ticks a try only when it really ran, and says why one did not.

One isolated daemon with a scripted model that, like a real one, picks the tool the person asked for
from the judge's numbered list and then calls it. The asks are sent over the real /api/chat door in
fresh words each run (English and Lithuanian, built from parts); the ticks are read from the real
/api/setup/state feed, which reads the ledger of what ran and the stores the tools write.

- a fresh install: no try is done, nothing claims otherwise;
- "find a file" in a home with no readable folder fails, and the step says why in the tool's words;
- after a real folder with the file exists, the same try is done;
- remember and a reminder, asked in two languages, tick their tries; the step is done only with all three.
"""
from __future__ import annotations

import json
import random
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from tests._blackbox_served_rig import ServedDaemon, run_in_home
from tests._served_sufficiency_provider import SEED
from tests.test_private_chat_served_workflow import MODEL, _certify, _Provider

REPO = Path(__file__).resolve().parents[1]
_RNG = random.Random()


class _AskProvider(_Provider):
    """Picks the judge's numbered tool whose description carries one of ``self.words``, then calls the
    offered tool whose name carries ``self.tool`` with ``self.arguments``."""

    def __init__(self) -> None:
        super().__init__({MODEL: "Okay."})
        self.words: tuple[str, ...] = ()
        self.tool = ""
        self.arguments: dict[str, Any] = {}

    def reply(self, model: str, body: dict[str, Any]) -> Any:
        text = json.dumps(body, ensure_ascii=False)
        if "vool_probe" in text or "probe." in text:
            return super().reply(model, body)
        said = "\n".join(str((m or {}).get("content") or "") for m in body.get("messages") or [])
        if '{"answer": "keep"}, {"answer": "recall"}' in said:
            # The memory reading of the same message: a keep when the person asked to remember something.
            return '{"answer": "keep"}' if self.tool == "remember" else '{"answer": "none"}'
        if "The assistant has the tools below" in said:
            tools_line = [line for line in said.splitlines() if line.startswith("Tools: ")][-1]
            for number, desc in re.findall(r'(\d+)\. "([^"]*)"', tools_line):
                if number != "0" and any(word in desc.lower() for word in self.words):
                    return '{"answer": [%s]}' % number
            return '{"answer": [0]}'
        if any(str((m or {}).get("role")) == "tool" for m in body.get("messages") or []):
            return "Done."
        # The local file loop asks for its tools as a JSON line ({"tool": "find", ...}) and hands the result back
        # under "[tool result]"; a model that follows it looks once, then answers.
        if '{"tool"' in said and self.tool == "find_file":
            if "[tool result]" in said:
                return '{"answer": "Done."}'
            return json.dumps({"tool": "find", "name": self.arguments.get("name", "")})
        names = [str(((t or {}).get("function") or {}).get("name") or "") for t in (body.get("tools") or []) if isinstance(t, dict)]
        name = next((n for n in names if self.tool and self.tool in n), "")
        if name:
            return {"content": "", "tool_calls": [{"id": "call-" + uuid.uuid4().hex[:6], "type": "function",
                                                   "function": {"name": name, "arguments": self.arguments}}]}
        return super().reply(model, body)


def _send(daemon: ServedDaemon, text: str, session: str, **extra: Any) -> list[dict]:
    body = {"messages": [{"role": "user", "content": text}], "stream": True, "stream_task_events": True,
            "session_id": session, "model": MODEL, "model_selection": "sticky",
            "mode": "manual", "surface": "desktop", **extra}
    body.setdefault("turn_id", uuid.uuid4().hex)
    events: list[dict] = []
    request = Request(f"{daemon.base_url}/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=600) as response:
        for raw in response:
            try:
                frame = json.loads(raw.decode("utf-8", "replace").strip() or "null")
            except json.JSONDecodeError:
                continue
            if isinstance(frame, dict) and frame.get("vool_event"):
                events.append(frame["vool_event"])
    return events


def _ask(daemon: ServedDaemon, text: str, session: str) -> None:
    """One ask as a person makes it: when VOOL stops for an OK, the person allows it once."""
    turn_id = uuid.uuid4().hex
    turn_events = _send(daemon, text, session, turn_id=turn_id)
    held = [e.get("approval") or {} for e in turn_events if e.get("type") == "permission.required" and e.get("approval")]
    if held:
        approval = held[0]
        body = {"op": "resolve_approval", "approval_id": approval["approval_id"], "decision": "allow", "scope": "once", "session_id": session, "turn_id": turn_id}
        request = Request(f"{daemon.base_url}/api/mode", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=60) as response:
            assert response.status == 200
        _send(daemon, text, session, turn_id=turn_id, approval_token=str(approval["approval_id"]))


def _state(daemon: ServedDaemon) -> dict[str, Any]:
    with urlopen(f"{daemon.base_url}/api/setup/state", timeout=60) as response:
        snap = json.loads(response.read().decode())
    return {"tries": {t["id"]: t for t in snap["first_tries"]["tries"]},
            "step": next(s for s in snap["steps"] if s["id"] == "tryit")}


def test_each_first_try_ticks_only_after_it_really_ran(tmp_path):
    home = tmp_path / "home"
    provider = _AskProvider()
    with provider:
        run_in_home(home, SEED.format(root=REPO, base_url=provider.base_url, models=[(MODEL, {})]))
        env = {"VOOL_MODEL_LOAD_FLOOR_GB": "0", "OLLAMA_HOST": provider.base_url, "VOOL_OLLAMA_URL": provider.base_url,
               "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat", "HOME": str(tmp_path / "person")}
        (tmp_path / "person").mkdir()
        with ServedDaemon(home, env_extra=env) as daemon:
            assert _certify(daemon).get("state") == "verified"
            session = "openclaw:" + uuid.uuid4().hex[:20]
            fresh = _state(daemon)
            assert not any(t["done"] or t["last_failure"] for t in fresh["tries"].values()), fresh
            assert fresh["step"]["done"] is False

            # Find a file in a home with no folder to look in: a real failure, in the tool's own words.
            stem = _RNG.choice(("budget", "packing", "garden", "recipes")) + "-" + uuid.uuid4().hex[:4]
            provider.words, provider.tool, provider.arguments = ("find a file",), "find_file", {"name": stem}
            _ask(daemon, _RNG.choice(("where is my {s} file?", "can u find {s} on my computer", "look for a file named {s}")).format(s=stem), session)
            failed = _state(daemon)["tries"]["find"]
            assert failed["done"] is False and failed["last_failure"], failed
            assert "machine." not in failed["last_failure"], failed

            # The person makes the folder and the file; the same try now works.
            docs = tmp_path / "person" / "Documents"
            docs.mkdir()
            (docs / f"{stem}.txt").write_text("x", encoding="utf-8")
            _ask(daemon, f"Rask failą {stem}", session)
            assert _state(daemon)["tries"]["find"]["done"] is True

            # Remember something, then a reminder, in fresh words.
            style = _RNG.choice(("short answers", "bullet points", "plain words"))
            provider.words, provider.tool = ("memory, keep",), "remember"
            provider.arguments = {"category": "response_style", "value": style, "scope": "global"}
            _ask(daemon, _RNG.choice(("remember I prefer {v}", "from now on use {v} please", "keep in mind: {v}")).format(v=style), session)
            assert _state(daemon)["tries"]["remember"]["done"] is True
            assert _state(daemon)["step"]["done"] is False, "two of three tries is not the step"

            due = (datetime.now() + timedelta(days=_RNG.randint(2, 4))).replace(hour=_RNG.randint(8, 11), minute=0, second=0, microsecond=0)
            note = _RNG.choice(("paskambinti mamai", "nupirkti duonos", "palaistyti gėles"))
            provider.words, provider.tool = ("remind",), "schedule_reminder"
            provider.arguments = {"note": note, "due_iso": due.strftime("%Y-%m-%dT%H:%M")}
            _ask(daemon, f"Primink man už {(due.date() - datetime.now().date()).days} dienų {due.hour} val. {note}", session)
            done = _state(daemon)
            assert done["tries"]["reminder"]["done"] is True, done
            assert done["step"]["done"] is True
