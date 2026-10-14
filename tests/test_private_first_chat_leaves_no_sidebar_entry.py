"""Served proof: a chat that starts private leaves no chat behind -- no namespace row, no sidebar entry.

A chat whose first message turns private chat on ("don't record this", "private chat", or the clause
inside a longer message) used to leave a "New chat" entry in the sidebar: the chat door created the
chat's namespace row before the turn ran, and the sessions list shows every namespace. That row named
the private chat's id in the database for good. Driven over the real /api/chat door on one isolated
daemon, the way the chat page sends turns. Authored wording only.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import pytest

from tests._blackbox_served_rig import ServedDaemon, run_in_home
from tests._served_sufficiency_provider import SEED, BodyAwareScriptedProvider

REPO = Path(__file__).resolve().parents[1]
MODEL = "stub-private:4b"
SECRET = "Quillmere"  # authored marker for private words


class _Provider(BodyAwareScriptedProvider):
    def reply(self, model: str, body: dict[str, Any]) -> Any:
        if "well-known referents" in json.dumps(body):
            return '{"ambiguous": false, "referents": [], "clarification": ""}'
        return super().reply(model, body)


def _post(daemon: ServedDaemon, path: str, payload: dict[str, Any], timeout: int = 400) -> dict[str, Any]:
    request = Request(f"{daemon.base_url}{path}", method="POST", data=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def _get(daemon: ServedDaemon, path: str) -> dict[str, Any]:
    with urlopen(f"{daemon.base_url}{path}", timeout=60) as response:
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


def _rows_for(daemon: ServedDaemon, key: str) -> list[dict[str, Any]]:
    """The sidebar rows (GET /api/chat/sessions) whose session id holds ``key``."""
    return [row for row in _get(daemon, "/api/chat/sessions").get("sessions") or []
            if key in str(row.get("session_id") or "")]


def _namespace_rows_naming(home: Path, key: str) -> list[str]:
    """Every row of a chat-namespace table (namespaces, their import grants) in any database under the
    home that names ``key`` in any column, as "file:table"."""
    hits = []
    for path in home.rglob("*.db"):
        conn = sqlite3.connect(path)
        try:
            tables = [name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
                      if "namespace" in name or "import_grant" in name]
            for table in tables:
                columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
                clause = " OR ".join(f'CAST("{c}" AS TEXT) LIKE ?' for c in columns)
                if clause and conn.execute(f'SELECT 1 FROM "{table}" WHERE {clause} LIMIT 1',
                                           [f"%{key}%"] * len(columns)).fetchone():
                    hits.append(f"{path.name}:{table}")
        finally:
            conn.close()
    return hits


def test_a_chat_that_starts_private_leaves_no_chat_until_recording_is_back_on(tmp_path):
    home = tmp_path / "home"
    provider = _Provider({MODEL: "Sounds lovely."})
    with provider:
        run_in_home(home, SEED.format(root=REPO, base_url=provider.base_url, models=[(MODEL, {})]))
        with ServedDaemon(home, env_extra={"VOOL_MODEL_LOAD_FLOOR_GB": "0"}) as daemon:
            certified = _post(daemon, "/api/model-tool-certification/run",
                              {"provider_name": "ollama-local", "model_name": MODEL, "timeout_seconds": 120})
            assert certified.get("state") == "verified", certified

            # Control: an ordinary first message makes a chat with its own title.
            plain = "a1" * 10  # the page mints "openclaw:" + 20 hex digits
            _stream(daemon, "My cat is called Biscuit.", "openclaw:" + plain)
            plain_rows = _rows_for(daemon, plain)
            assert [row["title"] for row in plain_rows] == ["My cat is called Biscuit."], plain_rows
            assert _namespace_rows_naming(home, plain), "control: an ordinary chat has its namespace row"

            # Three ways a chat can start private: the bare request, another bare wording, and the
            # request as a clause of a longer message whose rest is answered privately.
            starts = {
                "b2" * 10: ["don't record this", f"The surprise party is at {SECRET} Hall."],
                "c3" * 10: ["private chat"],
                "d4" * 10: [f"off the record, the gift is a {SECRET} lamp"],
            }
            for key, messages in starts.items():
                for text in messages:
                    _, commit = _stream(daemon, text, "openclaw:" + key)
                    assert commit.get("display_metadata", {}).get("private_chat") is True, (text, commit)
                assert _rows_for(daemon, key) == [], (key, _rows_for(daemon, key))
                assert _namespace_rows_naming(home, key) == [], key
                # A reload of the private chat still knows it is private, and shows none of it.
                history = _get(daemon, f"/api/chat/history?session=openclaw:{key}")
                assert history.get("private_chat") is True and history.get("messages") == [], history
            sidebar = _get(daemon, "/api/chat/sessions").get("sessions") or []
            assert "New chat" not in [row.get("title") for row in sidebar], sidebar
            assert _rows_for(daemon, plain), "control: the ordinary chat is still listed"

            # Ending private chat is itself sent while it is on: still nothing. A tab that has not heard
            # it ended sends one more unsaved message: still nothing.
            first = "b2" * 10
            back, _ = _stream(daemon, "turn recording back on", "openclaw:" + first)
            assert "Recording is back on" in back, back
            _, stale = _stream(daemon, f"From the other tab: the cake is from {SECRET} Bakery.",
                               "openclaw:" + first, private_chat=True)
            assert stale.get("display_metadata", {}).get("private_chat") is True, stale
            assert _rows_for(daemon, first) == [] and _namespace_rows_naming(home, first) == []

            # The first saved message makes the chat, titled by that message.
            _, saved = _stream(daemon, "My cat likes tuna.", "openclaw:" + first)
            assert "private_chat" not in saved.get("display_metadata", {}), saved
            rows = _rows_for(daemon, first)
            assert [row["title"] for row in rows] == ["My cat likes tuna."], rows
            assert _namespace_rows_naming(home, first), "the saved chat has its namespace row"
            history = json.dumps(_get(daemon, f"/api/chat/history?session=openclaw:{first}"))
            assert "tuna" in history and SECRET not in history and "private_chat" not in history

            # A chat that was saved and then went private keeps its row and its title.
            _stream(daemon, "don't record this", "openclaw:" + plain)
            _stream(daemon, f"The surprise party is at {SECRET} Hall.", "openclaw:" + plain)
            plain_rows = _rows_for(daemon, plain)
            assert [row["title"] for row in plain_rows] == ["My cat is called Biscuit."], plain_rows
            assert _get(daemon, f"/api/chat/history?session=openclaw:{plain}").get("private_chat") is True

        # The daemon is down: the chats still private left no namespace row behind.
        for key in ("c3" * 10, "d4" * 10):
            assert _namespace_rows_naming(home, key) == [], key


def test_a_private_chat_without_a_row_keeps_its_reads_and_gets_its_row_once_saved(tmp_path, monkeypatch):
    """The namespace owner's half: while a chat that started private stays private, every reader finds
    an active namespace with the grants a row would carry (the confirmed profile still reaches the
    private turn), yet no row is written. Deleting it forgets it. Saving again creates the row."""
    from core import context_namespace as ns
    from core import private_chat
    from storage import db

    path = str(tmp_path / "runtime.db")
    monkeypatch.setattr(ns, "get_connection", lambda: db.get_connection(path))
    monkeypatch.setattr(private_chat, "get_connection", lambda: db.get_connection(path))
    monkeypatch.setattr(ns, "_UNSAVED", {})
    chat = "openclaw:" + "e5" * 10

    def saved_rows() -> list[str]:
        conn = sqlite3.connect(path)
        try:
            return [table for table in ("context_namespaces", "context_import_grants")
                    if conn.execute(f"SELECT 1 FROM {table} WHERE chat_id = ?", (chat,)).fetchone()]
        finally:
            conn.close()

    private_chat.set_private(chat, True)
    made = ns.ensure_chat_namespace(chat, grant_confirmed_profile=True)
    assert made.chat_id == chat and made.lifecycle_state == "active"
    assert ns.load_chat_namespace(chat) == made
    scopes = {(g.scope, g.source_id) for g in ns.list_context_imports(chat)}
    assert scopes == {("user_profile", "profile:confirmed"), ("action_receipt", f"chat:{chat}")}
    assert ns.list_chat_namespaces(include_deleted=True) == ()
    assert saved_rows() == []

    # Deleting a chat nothing was saved of leaves no tombstone row either.
    assert ns.set_chat_namespace_state(chat, "deleted").lifecycle_state == "deleted"
    assert ns.load_chat_namespace(chat) is None and saved_rows() == []
    with pytest.raises(ValueError):
        ns.set_chat_namespace_state(chat, "archived")

    ns.ensure_chat_namespace(chat, grant_confirmed_profile=True)
    private_chat.set_private(chat, False)
    # No longer private and not yet saved: there is no chat.
    assert ns.load_chat_namespace(chat) is None
    saved = ns.ensure_chat_namespace(chat, grant_confirmed_profile=True)
    assert saved.lifecycle_state == "active"
    assert saved_rows() == ["context_namespaces", "context_import_grants"]
    assert {(g.scope, g.source_id) for g in ns.list_context_imports(chat)} == scopes
    assert [n.chat_id for n in ns.list_chat_namespaces()] == [chat]

    # A chat that already has its row keeps it when it goes private.
    private_chat.set_private(chat, True)
    assert ns.ensure_chat_namespace(chat) == ns.load_chat_namespace(chat)
    assert [n.chat_id for n in ns.list_chat_namespaces()] == [chat]


SIDEBAR = """(() => [...document.querySelectorAll('#sessions .session')].map(s => ({
  title: (s.querySelector('.s-title') || {}).textContent || '',
  private: s.classList.contains('session-private'),
  actions: !!s.querySelector('.s-actions'),
})))()"""


def test_the_page_shows_a_private_first_chat_as_private_only_until_reload(tmp_path):
    """Real Chromium on the shipped page: a chat that starts private shows one row, labelled Private and
    offering nothing that would save something about it; a reload drops it; the first saved message
    gives the chat its real row. A chat saved first keeps its title when it goes private."""
    import subprocess
    import sys

    import tests._reader_served_rig as rig
    from tests.served_browser import launch_chromium

    def reply(body):
        from core.entity_ambiguity import AMBIGUITY_SYSTEM_PROMPT

        if any(AMBIGUITY_SYSTEM_PROMPT in str(m.get("content", "")) for m in body.get("messages") or []):
            return json.dumps({"ambiguous": False, "referents": [], "clarification": ""})
        return "Sounds lovely."

    def send(page, text: str, answers: int) -> None:
        page.fill("#input", text)
        page.click("#send")
        wait_answers(page, answers)

    def wait_answers(page, count: int) -> None:
        page.wait_for_function(
            "n => document.querySelectorAll('#log .msg.assistant:not(.pending)').length >= n "
            "&& !document.querySelector('#send').textContent.includes('Queue')",
            arg=count, timeout=180_000)
        page.wait_for_timeout(400)

    def sidebar(page) -> list[dict[str, Any]]:
        page.evaluate("() => loadSessions()")
        page.wait_for_timeout(300)
        return page.evaluate(SIDEBAR)

    with rig.CapturingProvider(reply_fn=reply) as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider,
                                 env_extra={"VOOL_RAW_OLLAMA_API_URL": provider.base_url})
        daemon.register_provider()
        daemon.start()
        try:
            subprocess.run([sys.executable, "-c",
                "from storage.model_provider_manifest import list_provider_manifests; "
                "from tests._authorship_certification import certify_for_authorship; "
                "[certify_for_authorship(m) for m in list_provider_manifests() "
                "if m.provider_name == 'reader-stub' and m.model_name == 'reader-drive:stub']"
            ], cwd=rig.REPO_ROOT, env=daemon.env(), check=True, capture_output=True, timeout=30)
            manager, browser = launch_chromium()
            try:
                page = browser.new_page()
                errors: list[str] = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.route("**/api/tags", lambda route: route.fulfill(
                    content_type="application/json", body=json.dumps({"models": [{"name": daemon.model}]})))
                page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
                page.wait_for_selector("#input", timeout=30_000)
                page.click("#modelBtn")
                page.locator(f'.local-dyn[data-model="{daemon.model}"]').click()

                send(page, "don't record this", 1)
                page.wait_for_selector("#privateBanner:not([hidden])", timeout=10_000)
                send(page, f"The surprise party is at {SECRET} Hall.", 2)
                rows = sidebar(page)
                assert rows == [{"title": "Private", "private": True, "actions": False}], rows
                assert SECRET not in page.evaluate("() => document.getElementById('sessions').textContent")

                # Reload: the page reopens the chat, still private, and the sidebar has no row for it.
                page.reload(wait_until="domcontentloaded")
                page.wait_for_selector("#privateBanner:not([hidden])", timeout=30_000)
                page.wait_for_selector(".private-note", timeout=10_000)
                page.wait_for_selector("#sessions .side-empty", timeout=30_000)
                assert sidebar(page) == []

                # Recording back on, then a saved message: the chat gets its row, titled by that message.
                page.click("#privateEnd")
                wait_answers(page, 1)
                page.wait_for_selector("#privateBanner[hidden]", state="attached", timeout=10_000)
                assert [r["title"] for r in sidebar(page)] == ["Private"]
                send(page, "My cat likes tuna.", 2)
                rows = sidebar(page)
                assert rows == [{"title": "My cat likes tuna.", "private": False, "actions": True}], rows

                # The same chat going private again keeps its row and its title.
                send(page, "private chat", 3)
                send(page, f"The cake is from {SECRET} Bakery.", 4)
                rows = sidebar(page)
                assert rows == [{"title": "My cat likes tuna.", "private": False, "actions": True}], rows
                assert "New chat" not in page.evaluate("() => document.getElementById('sessions').textContent")
                assert errors == [], errors
            finally:
                browser.close()
                manager.stop()
        finally:
            daemon.stop()
