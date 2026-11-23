"""Served proof: Today in VOOL on a running VOOL, in the browser, across a change and a restart.

The stores are written the way VOOL writes them (the reminder store, the to-do list, a goal), from a
separate process in the same home while the daemon runs, so nothing reaches the page except through
/api/today. The page is the real chat page in a real browser.

- a new chat shows the reminder, the to-do and the unfinished goal;
- a to-do added while the page is open appears when the window comes back, and a ticked one goes;
- after a restart the same items are there, and the goal that was running reads as stopped part way;
- in Lithuanian the box speaks Lithuanian.
"""
from __future__ import annotations

import os as _os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests._blackbox_served_rig import ServedDaemon, run_in_home
from tests._served_sufficiency_provider import SEED
from tests.test_private_chat_served_workflow import MODEL, _Provider

REPO = Path(__file__).resolve().parents[1]

WRITE = '''
import sys, time, uuid
sys.path.insert(0, "{root}")
from storage.db import get_connection
from storage.migrations import run_migrations
run_migrations()
from core import pa_tasks, goal_workflow
from core.operator.reminders import schedule_reminder
{body}
'''


def _write(home: Path, body: str) -> None:
    run_in_home(home, WRITE.format(root=REPO, body=body))


def _box(daemon: ServedDaemon, *, lang: str = "en", then=None) -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox"], **({"executable_path": _os.environ["VOOL_TEST_CHROMIUM_EXECUTABLE"]} if _os.environ.get("VOOL_TEST_CHROMIUM_EXECUTABLE") else {}))
        try:
            page = browser.new_page()
            page.goto(f"{daemon.base_url}/chat?ui_locale={lang}", wait_until="domcontentloaded")
            page.wait_for_selector("[data-testid=today]:not([hidden])", timeout=30000)
            text = page.inner_text("[data-testid=today]")
            if then is not None:
                then()
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                page.wait_for_function("(old) => { const b = document.querySelector('[data-testid=today]'); return b && b.innerText !== old; }",
                                       arg=text, timeout=15000)
                text = page.inner_text("[data-testid=today]")
            return text
        finally:
            browser.close()


def test_today_shows_what_the_stores_hold_after_a_change_and_a_restart(tmp_path):
    home = tmp_path / "home"
    provider = _Provider({MODEL: "Okay."})
    due = (datetime.now(timezone.utc) + timedelta(hours=2)).replace(second=0, microsecond=0)
    note = "water the " + uuid.uuid4().hex[:4] + " plants"
    goal = "sort the " + uuid.uuid4().hex[:4] + " photos"
    with provider:
        run_in_home(home, SEED.format(root=REPO, base_url=provider.base_url, models=[(MODEL, {})]))
        _write(home, f'''
schedule_reminder(session_id="chat-a", task_id=uuid.uuid4().hex, note={note!r}, due_at_utc={due.isoformat()!r},
                  tz_name="UTC", get_connection_fn=get_connection)
pa_tasks.create_task(title="buy oat milk")
goal_workflow.save_goal({{"goal_id": goal_workflow.new_goal_id(), "session_id": "chat-b", "objective": {goal!r},
    "state": "running", "plan": {{"steps": [{{"id": "1", "state": "completed"}}, {{"id": "2", "state": "pending"}}]}},
    "created_at": time.time(), "updated_at": time.time(), "started_at": time.time()}})
''')
        env = {"VOOL_MODEL_LOAD_FLOOR_GB": "0", "OLLAMA_HOST": provider.base_url, "VOOL_OLLAMA_URL": provider.base_url}
        with ServedDaemon(home, env_extra=env) as daemon:
            first = _box(daemon, then=lambda: _write(home, '''
pa_tasks.create_task(title="post the parcel")
[pa_tasks.complete_task(t["task_id"]) for t in pa_tasks.list_tasks() if t["title"] == "buy oat milk"]
'''))
            assert note in first and "post the parcel" in first and "buy oat milk" not in first, first
            assert goal in first and "1 of 2 steps" in first, first
            # The goal's worker is not this process's: it is not shown as working.
            assert "Stopped part way" in first, first

        with ServedDaemon(home, env_extra=env) as daemon:
            again = _box(daemon)
            assert note in again and "post the parcel" in again and goal in again, again
            assert "Stopped part way" in again and "Working" not in again, again
            lithuanian = _box(daemon, lang="lt")
            assert "Šiandien" in lithuanian and "Priminimai" in lithuanian and note in lithuanian, lithuanian
