"""The served Settings page, driven in a real browser: bringing chats in from other AI apps.

The page is served with the real dispatchers (GET, POST and the raw upload door) over a loopback
HTTP server, against a temporary VOOL home and a temporary user home holding one other app's
store. Pinned: what the person sees and can do with keyboard and mouse, from choosing a source to
the result line; nothing is ticked until they choose; the chats land in the chat list; the panel
reads in German, Japanese and Arabic (right to left) and fits a phone-width window.
"""
from __future__ import annotations

import io
import json
import sqlite3
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from core import runtime_paths
from core.chat_import import flow
from core.chat_import import store as import_store

SECRET = "sk-ant-api03-" + "B" * 95


def _drop_import_tables() -> None:
    from storage.db import get_connection

    connection = get_connection()
    try:
        for table in (import_store.MESSAGES_TABLE, import_store.CHATS_TABLE):
            connection.execute(f"DROP TABLE IF EXISTS {table}")
        connection.commit()
    finally:
        connection.close()


def _export_zip() -> bytes:
    chats = [
        {"uuid": "c-soup", "name": "Soup recipes", "created_at": "2025-05-01T08:00:00Z", "updated_at": "2025-05-01T08:10:00Z",
         "chat_messages": [
             {"uuid": "1", "sender": "human", "text": f"a good soup? (key {SECRET})", "created_at": "2025-05-01T08:00:00Z"},
             {"uuid": "2", "sender": "assistant", "text": "Try a lentil soup.", "created_at": "2025-05-01T08:00:03Z"}]},
        {"uuid": "c-run", "name": "Running plan", "created_at": "2025-05-02T08:00:00Z", "updated_at": "2025-05-02T08:10:00Z",
         "chat_messages": [
             {"uuid": "3", "sender": "human", "text": "a 5k plan please", "created_at": "2025-05-02T08:00:00Z"},
             {"uuid": "4", "sender": "assistant", "text": "Three runs a week.", "created_at": "2025-05-02T08:00:04Z"}]},
    ]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("conversations.json", json.dumps(chats))
    return buffer.getvalue()


def _agent_store(user_home) -> None:
    db = user_home / ".hermes" / "state.db"
    db.parent.mkdir(parents=True)
    connection = sqlite3.connect(db)
    connection.executescript(
        """
        CREATE TABLE schema_version (version INTEGER); INSERT INTO schema_version VALUES (31);
        CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, parent_session_id TEXT, started_at REAL,
            ended_at REAL, title TEXT, hidden INTEGER DEFAULT 0);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, tool_calls TEXT,
            timestamp REAL, active INTEGER DEFAULT 1, compacted INTEGER DEFAULT 0, message_uid TEXT);
        INSERT INTO sessions VALUES ('s1', 'cli', NULL, 1746000000.0, 1746000100.0, 'Bike repair', 0);
        INSERT INTO messages (session_id, role, content, timestamp, message_uid)
            VALUES ('s1', 'user', 'my chain skips', 1746000001.0, 'a'), ('s1', 'assistant', 'Check the cassette.', 1746000002.0, 'b');
        """
    )
    connection.commit()
    connection.close()


@pytest.fixture
def served(tmp_path, monkeypatch):
    from core.vool_settings_page import render_vool_settings_html
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get, dispatch_post, dispatch_upload, is_raw_upload_path

    home = tmp_path / "vool-home"
    home.mkdir()
    prior = runtime_paths._VOOL_HOME_OVERRIDE
    monkeypatch.setenv("VOOL_HOME", str(home))
    runtime_paths.configure_runtime_home(home)
    user_home = tmp_path / "user-home"
    user_home.mkdir()
    monkeypatch.setenv("HOME", str(user_home))
    for name in ("HERMES_HOME", "OPENCLAW_STATE_DIR", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(name, raising=False)
    _agent_store(user_home)
    _drop_import_tables()
    flow.reset_for_tests()
    runtime = RuntimeServices(display_name="chat import fixture")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, res):
            self.send_response(res.status)
            self.send_header("Content-Type", res.content_type)
            self.end_headers()
            self.wfile.write(res.body)

        def do_GET(self):
            u = urlsplit(self.path)
            if u.path == "/chat":
                from core.vool_chat_page import render_vool_chat_html

                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(render_vool_chat_html().encode())
                return
            if u.path == "/settings":
                locale = (parse_qs(u.query).get("ui_locale") or ["en"])[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(render_vool_settings_html(ui_locale=locale).encode())
                return
            self.send(dispatch_get(path=u.path, query=parse_qs(u.query), runtime=runtime, model_name="fixture",
                                   client_host="127.0.0.1", headers=dict(self.headers)))

        def do_POST(self):
            path = urlsplit(self.path).path
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if is_raw_upload_path(path):
                self.send(dispatch_upload(path=path, raw_body=raw, headers=dict(self.headers), runtime=runtime,
                                          client_host="127.0.0.1"))
                return
            self.send(dispatch_post(path=path, body=json.loads(raw or b"{}"), headers=dict(self.headers), runtime=runtime,
                                    model_name="fixture", client_host="127.0.0.1", workspace_root_provider=lambda: str(home)))

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", tmp_path
    finally:
        server.shutdown()
        server.server_close()
        flow.reset_for_tests()
        _drop_import_tables()
        runtime_paths.configure_runtime_home(prior)


def _panel(page):
    panel = page.locator(".vci-root").first
    panel.wait_for()
    return panel


def test_bring_chats_in_from_a_file_and_from_an_app(served):
    from core.persistent_memory import list_conversation_sessions
    from tests.served_browser import launch_chromium

    base, tmp_path = served
    export = tmp_path / "my-export.zip"
    export.write_bytes(_export_zip())
    errors: list[str] = []
    manager, browser = launch_chromium()
    try:
        page = browser.new_page(viewport={"width": 1120, "height": 900})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{base}/settings#backup/chat_import")
        panel = _panel(page)
        page.get_by_text("Bring your chats from other AI apps").first.wait_for()
        assert "Where are your chats?" in panel.inner_text()
        look = panel.get_by_role("button", name="Show my chats", exact=True)
        assert look.is_disabled()  # nothing is read until a source is chosen

        # The app found on this computer is described by what it is, never by name.
        agent = panel.get_by_role("radio", name="An AI agent on this computer")
        agent.wait_for()
        assert "Last used" in panel.inner_text()

        panel.get_by_role("radio", name="A data export file (.zip)").check()
        panel.get_by_label("Data export file", exact=True).set_input_files(str(export))
        look.click()
        panel.locator(".vci-chat").first.wait_for()
        assert panel.locator(".vci-status").inner_text() == "Found 2 chats."
        boxes = panel.locator(".vci-chat input[type=checkbox]")
        assert boxes.count() == 2
        assert [boxes.nth(i).is_checked() for i in range(2)] == [False, False]  # nothing ticked until chosen
        importer = panel.locator("button.vci-import")
        assert importer.is_disabled() and importer.inner_text() == "Bring in chats"
        assert SECRET not in panel.inner_text()

        # Keyboard: tick one chat with the space bar.
        boxes.nth(0).focus()
        page.keyboard.press("Space")
        assert importer.inner_text() == "Bring in 1 chat"
        panel.get_by_role("button", name="Select all", exact=True).click()
        assert importer.inner_text() == "Bring in 2 chats"
        importer.click()
        panel.locator(".vci-status.vci-good").wait_for(timeout=30000)
        result = panel.locator(".vci-status").inner_text()
        assert result == "Brought in 2 chats. 1 password or key was hidden.", result
        assert "does not learn from them" in panel.locator(".vci-where").inner_text()

        imported = {row["title"]: row for row in list_conversation_sessions(limit=50) if row.get("imported")}
        assert set(imported) == {"Soup recipes", "Running plan"}

        # The same file again: everything is already here and cannot be ticked.
        panel.get_by_label("Data export file", exact=True).set_input_files(str(export))
        look.click()
        panel.locator(".vci-chat.vci-here").nth(1).wait_for()
        assert panel.locator(".vci-status").inner_text() == "Found 2 chats. 2 are already here."
        assert all(boxes.nth(i).is_disabled() for i in range(2))
        panel.get_by_role("button", name="Cancel", exact=True).click()
        assert panel.locator(".vci-chat").count() == 0

        # The app on this computer.
        agent.check()
        look.click()
        panel.locator(".vci-chat").first.wait_for()
        assert "Bike repair" in panel.locator(".vci-chat").first.inner_text()
        panel.get_by_role("button", name="Select all", exact=True).click()
        panel.locator("button.vci-import").click()
        panel.locator(".vci-status.vci-good").wait_for(timeout=30000)
        assert panel.locator(".vci-status").inner_text() == "Brought in 1 chat."
        assert not errors, errors
    finally:
        browser.close()
        manager.stop()


@pytest.mark.parametrize(
    ("locale", "label", "choose", "direction"),
    [
        ("de", "Chats aus anderen KI-Apps mitbringen", "Wo sind deine Chats?", "ltr"),
        ("ja", "ほかのAIアプリのチャットを持ってくる", "チャットはどこにありますか？", "ltr"),
        ("ar", "أحضر محادثاتك من تطبيقات ذكاء اصطناعي أخرى", "أين محادثاتك؟", "rtl"),
    ],
)
def test_the_panel_reads_in_other_languages_and_fits_a_phone(served, locale, label, choose, direction):
    from tests.served_browser import launch_chromium

    base, tmp_path = served
    export = tmp_path / "export.zip"
    export.write_bytes(_export_zip())
    errors: list[str] = []
    manager, browser = launch_chromium()
    try:
        page = browser.new_page(viewport={"width": 360, "height": 780})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{base}/settings?ui_locale={locale}#backup/chat_import")
        panel = _panel(page)
        page.get_by_text(label).first.wait_for()
        assert choose in panel.inner_text()
        assert page.evaluate("document.documentElement.dir || 'ltr'") == direction
        panel.locator("input[type=radio][value=file]").check()
        panel.locator("input[type=file]").set_input_files(str(export))
        panel.locator("button.vci-look").click()
        panel.locator(".vci-chat").nth(1).wait_for()
        # Nothing in the panel is wider than a phone screen.
        overflow = page.evaluate(
            "() => { const r = document.querySelector('.vci-root'); const box = r.getBoundingClientRect();"
            " return [...r.querySelectorAll('*')].filter(n => { const b = n.getBoundingClientRect();"
            " return b.width > 0 && (b.right > box.right + 1 || b.left < box.left - 1); }).map(n => n.className).slice(0, 5); }"
        )
        assert overflow == [], overflow
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        text = panel.inner_text()
        assert "Found 2 chats" not in text and "Bring in" not in text  # translated, not English
        assert not errors, errors
    finally:
        browser.close()
        manager.stop()


def test_every_panel_string_is_in_every_catalog():
    import re

    from core.chat_import_settings_fragment import _JS
    from core.i18n.catalog import CATALOGS_DIR, MessageCatalog, clear_catalog_cache

    referenced = set(re.findall(r"(?:IT\(|\[)'(chatimport\.[a-z_.]+)'", _JS))
    assert len(referenced) > 40, f"parser rot: only {len(referenced)} keys found"
    referenced |= {"settings.row.chat_import.label", "settings.row.chat_import.help", "settings.pane.chat_import_missing"}
    clear_catalog_cache()
    for path in sorted(CATALOGS_DIR.glob("*.json")):
        catalog = MessageCatalog(path.stem)
        missing = sorted(key for key in referenced if key not in catalog.locale_keys)
        assert not missing, f"{path.stem}: {missing}"


def test_continuing_an_imported_chat_sends_only_what_was_said_in_vool(served):
    """The chat page reloads an imported chat through the real history door and builds the model
    request from it: the imported messages are shown, never sent."""
    from core.chat_import import importer
    from core.chat_import.model import ImportedChat, ImportedMessage
    from tests.served_browser import launch_chromium

    base, _tmp = served
    chat = ImportedChat(
        source_format="anthropic_export", source_chat_id="c-1", title="Old chat", created_at=1.0, updated_at=2.0,
        messages=(ImportedMessage(role="user", text="IMPORTED QUESTION", created_at=1.0, attachment_names=()),
                  ImportedMessage(role="assistant", text="IMPORTED ANSWER", created_at=2.0, attachment_names=())),
        skipped={},
    )
    outcome = importer.import_chats([chat])
    (session_id,) = outcome.session_ids
    manager, browser = launch_chromium()
    try:
        page = browser.new_page()
        page.goto(f"{base}/chat")
        page.wait_for_function("typeof canonicalMessagesForModel === 'function'")
        sent = page.evaluate(
            """async (sid) => {
                const r = await fetch('/api/chat/history?session=' + encodeURIComponent(sid));
                const j = await r.json();
                const history = j.messages.map(historyEntryFromServer);
                history.push({role: 'user', content: 'a new question'});
                return {shown: history.map(m => m.content), sent: canonicalMessagesForModel(history)};
            }""",
            session_id,
        )
        assert sent["shown"] == ["IMPORTED QUESTION", "IMPORTED ANSWER", "a new question"]
        assert sent["sent"] == [{"role": "user", "content": "a new question"}]
    finally:
        browser.close()
        manager.stop()
