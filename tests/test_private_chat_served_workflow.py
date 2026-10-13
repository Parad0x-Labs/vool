"""Served proof: private chat said in plain words over /api/chat, on one isolated daemon.

The user says "don't record this", tells VOOL something, asks VOOL to remember it, then says "turn recording
back on". Checked from outside the runtime, the way a person would notice: the transcript the chat page reloads,
the commit the page draws its ghost from, what the model was sent, and every file under the daemon's home --
not one byte of the private words may be on disk. Authored wording only.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from tests._blackbox_served_rig import ServedDaemon, run_in_home
from tests._served_sufficiency_provider import SEED, BodyAwareScriptedProvider

REPO = Path(__file__).resolve().parents[1]
MODEL = "stub-private:4b"
SECRET = "Quillmere"  # authored marker for the private words; appears nowhere else in the repo


class _Provider(BodyAwareScriptedProvider):
    def reply(self, model: str, body: dict[str, Any]) -> Any:
        if "well-known referents" in json.dumps(body):
            return '{"ambiguous": false, "referents": [], "clarification": ""}'
        return super().reply(model, body)


def _certify(daemon: ServedDaemon) -> dict[str, Any]:
    request = Request(f"{daemon.base_url}/api/model-tool-certification/run", method="POST",
                      data=json.dumps({"provider_name": "ollama-local", "model_name": MODEL,
                                       "timeout_seconds": 120}).encode(),
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=400) as response:
        return json.loads(response.read().decode())


def _stream(daemon: ServedDaemon, text: str, session: str, **extra: Any) -> tuple[str, dict[str, Any]]:
    """One streamed turn, as the chat page sends it: the answer and the turn's response.commit."""
    body = {"messages": [{"role": "user", "content": text}], "stream": True, "session_id": session, "model": MODEL,
            **extra}
    request = Request(f"{daemon.base_url}/api/chat", data=json.dumps(body).encode(),
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
    return str(commit.get("canonical_content") or answer), commit


def _history(daemon: ServedDaemon, session: str) -> dict[str, Any]:
    with urlopen(f"{daemon.base_url}/api/chat/history?session={session}", timeout=60) as response:
        return json.loads(response.read().decode())


def _files_holding(home: Path, needle: str) -> list[str]:
    """Every file under the home whose bytes hold ``needle``; for a database, also which tables."""
    import sqlite3

    hits = []
    raw = needle.encode("utf-8")
    for path in home.rglob("*"):
        if not path.is_file():
            continue
        try:
            if raw not in path.read_bytes():
                continue
        except OSError:
            continue
        where = str(path.relative_to(home))
        if path.suffix == ".db":
            tables = []
            try:
                conn = sqlite3.connect(path)
                for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
                    columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
                    clause = " OR ".join(f'CAST("{c}" AS TEXT) LIKE ?' for c in columns)
                    if clause and conn.execute(f'SELECT 1 FROM "{table}" WHERE {clause} LIMIT 1',
                                               [f"%{needle}%"] * len(columns)).fetchone():
                        tables.append(table)
                conn.close()
            except sqlite3.Error:
                tables.append("<unreadable>")
            # Bytes found but no live row: a freed page that has not been reused yet.
            where += ":" + (",".join(tables) or "<free pages only>")
        hits.append(where)
    return hits


def test_private_chat_keeps_nothing_and_recording_comes_back(tmp_path):
    home = tmp_path / "home"
    provider = _Provider({MODEL: "Sounds lovely."})
    with provider:
        run_in_home(home, SEED.format(root=REPO, base_url=provider.base_url, models=[(MODEL, {})]))
        with ServedDaemon(home, env_extra={"VOOL_MODEL_LOAD_FLOOR_GB": "0"}) as daemon:
            assert _certify(daemon).get("state") == "verified"
            chat = "openclaw:" + "9c" * 10  # the page mints 20 hex digits

            # An ordinary turn is saved as before.
            _stream(daemon, "My cat is called Biscuit.", chat)
            assert "Biscuit" in json.dumps(_history(daemon, chat))

            switched, on_commit = _stream(daemon, "don't record this", chat)
            assert "Private chat is on" in switched, switched
            assert on_commit.get("display_metadata", {}).get("private_chat") is True
            assert on_commit["display_metadata"].get("private_chat_on") is True

            before = len(provider.calls)
            _, private_commit = _stream(daemon, f"The surprise party is at {SECRET} Hall on Friday.", chat)
            sent = "\n".join(f"{c.get('system', '')}\n{c.get('full_prompt', '')}" for c in provider.calls[before:])
            assert "PRIVATE CHAT IS ON" in sent, "the model was not told the chat is private"
            assert private_commit.get("display_metadata", {}).get("private_chat") is True

            refused, _ = _stream(daemon, f"remember that the party is at {SECRET} Hall", chat)
            assert "Private chat is on" in refused and "won't save" in refused, refused

            status, _ = _stream(daemon, "are you recording this?", chat)
            assert "private right now" in status, status

            history = _history(daemon, chat)
            assert history.get("private_chat") is True
            assert SECRET not in json.dumps(history)
            assert "don't record this" not in json.dumps(history)

            back, off_commit = _stream(daemon, "turn recording back on", chat)
            assert "Recording is back on" in back, back
            # The turn that ends private chat was sent while it was on: it is marked, and says it is off now.
            assert off_commit.get("display_metadata", {}).get("private_chat") is True
            assert off_commit["display_metadata"].get("private_chat_on") is False

            # A tab that had not heard private chat ended still shows it, and sends its next message
            # believing it won't be saved: it isn't, and the commit tells that tab saving is back.
            _, stale_commit = _stream(daemon, f"From the other tab: the cake is from {SECRET} Bakery.", chat,
                                      private_chat=True)
            assert stale_commit.get("display_metadata", {}).get("private_chat") is True
            assert stale_commit["display_metadata"].get("private_chat_on") is False

            _, after_commit = _stream(daemon, "My cat likes tuna.", chat)
            assert "private_chat" not in after_commit.get("display_metadata", {})
            history = _history(daemon, chat)
            assert "private_chat" not in history
            text = json.dumps(history)
            assert "Biscuit" in text and "tuna" in text and SECRET not in text

            # Asking not to be saved in words the switch doesn't read: nothing switches, and the model is
            # told the chat is still saved, so it cannot promise otherwise.
            before = len(provider.calls)
            _, unread_commit = _stream(daemon, "bitte nicht speichern: my cat is scared of thunder", chat)
            sent = "\n".join(f"{c.get('system', '')}\n{c.get('full_prompt', '')}" for c in provider.calls[before:])
            assert "PRIVATE CHAT IS NOT ON" in sent, "the model was not told the chat is still saved"
            assert "private_chat" not in unread_commit.get("display_metadata", {})

            # Asking about it later: nothing of the private turns reaches the model, so there is no record to use.
            before = len(provider.calls)
            recall, _ = _stream(daemon, "Where is the surprise party?", chat)
            sent = "\n".join(json.dumps(c) for c in provider.calls[before:])
            assert SECRET not in sent and SECRET not in recall, recall

        # The daemon is down: every store it wrote is closed. Not one of them holds the private words.
        assert _files_holding(home, SECRET) == []
        assert _files_holding(home, "Biscuit"), "control: ordinary turns must reach disk"
