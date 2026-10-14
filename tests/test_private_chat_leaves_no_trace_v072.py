"""v0.7.2: a chat that was private from its first message leaves no trace that it existed.

THE MEASURED DEFECT (delivery/private-first-chat-sidebar-20261014 @ eec4b283, served daemon)
-------------------------------------------------------------------------------------------
That branch keeps a private-first chat out of the sidebar and out of the namespace tables, and no
word of it reached any store. But after private chat ended, a full scan of the home still found
the chat's id in the routing decision log and its shadow store (with an unsalted digest of every
private message), the per-chat receipt ledgers, the routing provenance log, ``vool_api.log``, and
eleven run tables: the runtime session, its events and attempts, a "Chat completed" notification,
the task row, execution facts, the hive watch row, semantic admissions, the provenance and provider
manifests, the context generation counter. "pin this chat" put it in ``chat_pins.json`` and the
audit log; "rename this chat to ..." put the private words in the sidebar. A private-first chat
opened inside a project kept its namespace row and its session-meta entry.

THE CONTRACT UNDER TEST
-----------------------
* Over the real ``/api/chat`` door, once a private-first chat's turn has ended, no file under the
  home (raw bytes, WAL and backups included) and no database cell names the chat, by id or by the
  digest receipt files are named with. While it is still private, only its switch row does.
* Stores that must never learn of it do not, even mid-turn (polled while the turns run).
* Pin, rename and move during a private turn are refused plainly; a saved chat that goes private
  keeps its title.
* A chat opened in a project that turns private gives up its row, and its first saved turn brings
  it back in the same project.
* An ordinary chat is recorded exactly as before (the scan finds it everywhere it did).
* After a restart, records a cut-off private turn left behind are erased; the switch is kept.
* Money is never erased: a private chat with a spend record keeps the task row the spend is traced to.
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from pathlib import Path

from tests._blackbox_served_rig import ServedDaemon, run_in_home
from tests._served_sufficiency_provider import SEED
from tests.test_private_first_chat_leaves_no_sidebar_entry import MODEL, REPO, SECRET, _get, _post, _Provider, _stream

PLAIN, FIRST, PROJECT, STILL, LATER = ("a1" * 10, "b2" * 10, "c3" * 10, "d4" * 10, "e5" * 10)
#: Stores that never hold a private chat's id, not even while its turn runs.
NEVER = ("routing_decisions.jsonl", "routing_authority_v2_shadow", "turn_routing_events.jsonl", "chat_pins.json",
         "chat_session_meta.json", "vool_api.log", "notification_items", "audit_log", "event_log_v2")


def _needles(key: str) -> tuple[bytes, ...]:
    """The chat's id, and the digest prefix per-chat files and folders are named with."""
    return key.encode(), hashlib.sha256(f"openclaw:{key}".encode()).hexdigest()[:16].encode()


def _traces(home: Path, key: str) -> list[str]:
    """Every file under the home whose bytes name the chat, and every database cell that does."""
    needles = _needles(key)
    found = []
    for path in sorted(home.rglob("*")):
        if not path.is_file():
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if any(n in raw for n in needles) or any(n.decode() in part for n in needles for part in path.parts):
            found.append(str(path.relative_to(home)))
    for path in home.rglob("*.db"):
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
                for column in [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]:
                    try:
                        hit = conn.execute(f'SELECT 1 FROM "{table}" WHERE instr(CAST("{column}" AS TEXT), ?) > 0 LIMIT 1',
                                           (key,)).fetchone()
                    except sqlite3.DatabaseError:
                        continue
                    if hit:
                        found.append(f"{path.name}:{table}.{column}")
        except sqlite3.DatabaseError:
            continue
        finally:
            conn.close()
    return found


def _sidebar(daemon) -> dict[str, dict]:
    return {str(row.get("session_id")): row for row in _get(daemon, "/api/chat/sessions").get("sessions") or []}


def test_a_private_first_chat_leaves_no_trace_once_its_turns_end(tmp_path):
    home, folder = tmp_path / "home", tmp_path / "garden"
    folder.mkdir()
    provider = _Provider({MODEL: "Sounds lovely."})
    seen: dict[str, set[str]] = {key: set() for key in (FIRST, PROJECT, STILL)}
    stop = threading.Event()

    def poll() -> None:
        while not stop.is_set():
            for key in seen:
                try:
                    seen[key].update(_traces(home, key))
                except Exception:
                    pass
            time.sleep(0.3)

    with provider:
        run_in_home(home, SEED.format(root=REPO, base_url=provider.base_url, models=[(MODEL, {})]))
        with ServedDaemon(home, env_extra={"VOOL_MODEL_LOAD_FLOOR_GB": "0"}) as daemon:
            assert _post(daemon, "/api/model-tool-certification/run", {"provider_name": "ollama-local", "model_name": MODEL,
                                                                        "timeout_seconds": 120}).get("state") == "verified"
            # Control: an ordinary chat is recorded where it always was.
            _stream(daemon, "My cat is called Biscuit.", "openclaw:" + PLAIN)
            plain = _traces(home, PLAIN)
            for store in ("routing_decisions.jsonl", "turn_routing_events.jsonl", "runtime_sessions.session_id",
                          "notification_items.session_id", "provider_invocation_manifests.chat_id"):
                assert any(store in hit for hit in plain), (store, plain)
            watcher = threading.Thread(target=poll, daemon=True)
            watcher.start()

            sid = "openclaw:" + FIRST
            answers = [_stream(daemon, text, sid)[0] for text in (
                "don't record this", f"The surprise party is at {SECRET} Hall.", "pin this chat",
                "rename this chat to Lamp plans", "move this chat to Garden", "turn recording back on")]
            for answer in answers[2:5]:
                assert "private chat is on" in answer, answers
            assert "Recording is back on" in answers[5], answers[5]

            project = _post(daemon, "/api/projects", {"name": "Garden", "root": str(folder)})["project"]
            psid = "openclaw:" + PROJECT
            _post(daemon, "/api/chat/session", {"session_id": psid, "project_id": project["id"]})
            assert psid in _sidebar(daemon), "control: a chat opened in a project is listed before its first message"
            for text in ("private chat", f"The gift is a {SECRET} lamp.", "end private chat"):
                _, commit = _stream(daemon, text, psid)
            assert psid not in _sidebar(daemon)

            for text in ("off the record", f"The boat is {SECRET}."):
                _stream(daemon, text, "openclaw:" + STILL)

            # A saved chat that goes private keeps its row and its title; the private turn renames nothing.
            for text in ("don't record this", "rename this chat to Lamp plans", "turn recording back on"):
                _stream(daemon, text, "openclaw:" + PLAIN)
            assert _sidebar(daemon)["openclaw:" + PLAIN]["title"] == "My cat is called Biscuit."
            time.sleep(2.0)
            stop.set()
            watcher.join(10)

            assert _traces(home, FIRST) == [], _traces(home, FIRST)
            assert _traces(home, PROJECT) == [], _traces(home, PROJECT)
            assert _traces(home, STILL) == ["data/vool_web0_v2.db", "vool_web0_v2.db:private_chat_sessions.session_id"]
            assert _traces(home, SECRET) == []
            for key, hits in seen.items():
                # The project chat had its meta entry before it went private (opened in a project);
                # the scan above shows it gone.
                never = [store for store in NEVER if not (key == PROJECT and store == "chat_session_meta.json")]
                assert not [hit for hit in hits if any(store in hit for store in never)], (key, sorted(hits))
            assert any("runtime_sessions" in hit for hit in seen[FIRST]), "the poll never saw a turn run"

            # Its first saved turn brings the project chat back, in its project.
            _stream(daemon, "My cat likes tuna.", psid)
            row = _sidebar(daemon).get(psid) or {}
            assert row.get("title") == "My cat likes tuna." and row.get("project_id") == project["id"], row

        # A private turn cut off by a stop leaves its run records (written here through their own
        # writers, as an older build or a crash leaves them): the next start erases them.
        run_in_home(home, (
            "from core.runtime_continuity import append_runtime_event\n"
            f"append_runtime_event(session_id='openclaw:{STILL}', event_type='task_started', message='private chat')\n"
            "from core.operator.notification_center import record_item\n"
            f"record_item(dedupe_key='runtime:openclaw:{STILL}:t1:task_completed:', source_kind='background_run',"
            f" title='Chat completed', body='', session_id='openclaw:{STILL}', payload={{}})\n"
        ))
        assert any("runtime_sessions" in hit for hit in _traces(home, STILL))
        with ServedDaemon(home, env_extra={"VOOL_MODEL_LOAD_FLOOR_GB": "0"}) as daemon:
            assert _get(daemon, f"/api/chat/history?session=openclaw:{STILL}").get("private_chat") is True
        assert _traces(home, STILL) == ["data/vool_web0_v2.db", "vool_web0_v2.db:private_chat_sessions.session_id"]


def test_money_a_private_chat_spent_keeps_the_record_it_is_traced_to(tmp_path, monkeypatch):
    """The eraser never removes a spend record, nor the task row a model spend reservation names."""
    from core import private_chat
    from core.model_spend_ledger import SpendLimits, reserve_spend
    from core.private_chat_traces import erase_if_unsaved
    from core.runtime_continuity import append_runtime_event
    from core.task_router import create_task_record

    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    from core.runtime_paths import configure_runtime_home

    configure_runtime_home(tmp_path / "home")
    from storage.migrations import run_migrations

    run_migrations()
    try:
        paid, free = "openclaw:" + "f6" * 10, "openclaw:" + "a7" * 10
        tasks = {}
        for chat in (paid, free):
            private_chat.set_private(chat, True)
            append_runtime_event(session_id=chat, event_type="task_started", message="private chat")
            tasks[chat] = create_task_record("private chat", session_id=chat).task_id
        limits = SpendLimits(per_call_usd=1.0, per_task_usd=1.0, daily_usd=5.0, monthly_usd=5.0)
        reserve_spend(model_call_id="call-1", task_id=tasks[paid], subtask_id="", model_id="paid-model",
                      maximum_usd=0.01, limits=limits)
        assert erase_if_unsaved(paid)["kept_for_money"] == ["local_tasks"]
        assert erase_if_unsaved(free)["kept_for_money"] == []
        from storage.db import get_connection

        conn = get_connection()
        try:
            def rows(table, column, value):
                return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (value,)).fetchone()[0]

            assert rows("local_tasks", "session_id", paid) == 1
            assert rows("model_spend_reservations", "task_id", tasks[paid]) == 1
            assert rows("local_tasks", "session_id", free) == 0
            assert rows("runtime_sessions", "session_id", paid) == rows("runtime_sessions", "session_id", free) == 0
        finally:
            conn.close()
    finally:
        configure_runtime_home(None)


def test_the_eraser_waits_for_a_queued_message_then_takes_every_run_table_and_the_wal(tmp_path, monkeypatch):
    """A queued send is a wait on the person: nothing is erased while it is pending. Once it is done,
    the queue row and the tool receipts go too, and a reader holding a WAL snapshot only delays the
    WAL being emptied until the next erase."""
    from core import private_chat
    from core.private_chat_traces import erase_if_unsaved
    from core.runtime_continuity import append_runtime_event, complete_queue_item, enqueue_message, store_tool_receipt
    from core.runtime_paths import configure_runtime_home
    from storage.db import active_default_db_path, get_connection
    from storage.migrations import run_migrations

    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    configure_runtime_home(tmp_path / "home")
    run_migrations()
    try:
        chat = "openclaw:" + "b8" * 10
        private_chat.set_private(chat, True)
        append_runtime_event(session_id=chat, event_type="task_started", message="private chat")
        queued = enqueue_message(session_id=chat, payload={"text": "next"})
        store_tool_receipt(receipt_key="r-1", session_id=chat, checkpoint_id="c-1", tool_name="clock",
                           idempotency_key="i-1", arguments={}, execution={"ok": True})
        assert erase_if_unsaved(chat)["skipped"] == "waiting"
        complete_queue_item(queued["queue_item_id"])
        reader = get_connection()
        try:
            reader.execute("BEGIN")
            reader.execute("SELECT COUNT(*) FROM runtime_sessions").fetchone()
            removed = erase_if_unsaved(chat)["removed"]
        finally:
            reader.close()
        assert {"message_queue", "runtime_tool_receipts", "runtime_sessions"} <= set(removed), removed
        private_chat.set_private(chat, False)
        erase_if_unsaved(chat)  # the next erase empties the WAL the reader held
        db = Path(active_default_db_path())
        for path in (db, db.with_name(db.name + "-wal")):
            if path.exists():
                assert ("b8" * 10).encode() not in path.read_bytes(), path
    finally:
        configure_runtime_home(None)
