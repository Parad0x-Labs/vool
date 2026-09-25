"""Cold browser → real daemon → certified scripted provider, with existing history.

Set VOOL_STARTUP_APP_ROOT to a built Contents/Resources/app to exercise the
packaged source and interpreter. No cloud calls or local model launches.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from urllib.request import urlopen

from core.agent_runtime import fast_paths_utility as greetings
from tests import _reader_served_rig as rig
from tests import served_browser


def _production_greeting_prefixes(phrase: str, monkeypatch) -> tuple[str, ...]:
    """Every opener the production selector can draw for ``phrase`` at any hour of the day.

    ``build_greeting_reply`` may weave the operator's address before the terminal mark, append a
    task tail or an emoji, so the reply is matched on the opener stripped of its final mark.
    The pools and the per-period slice come from the production selector, not a copied subset.
    """
    openers: set[str] = set()
    for period in ("morning", "afternoon", "evening", "night"):
        monkeypatch.setattr(greetings, "_time_of_day", lambda now=None, _p=period: _p)
        openers.update(greetings._openers_for(phrase))
    monkeypatch.undo()
    return tuple(sorted({opener.rstrip(".!?") for opener in openers if opener.rstrip(".!?")}))


def _seed_history(home):
    data = home / "data"
    rows = []
    with sqlite3.connect(data / "vool_web0_v2.db") as conn:
        for i in range(12):
            sid, cp = f"history-{i}", f"checkpoint-{i}"
            conn.execute("INSERT INTO runtime_checkpoints "
                         "(checkpoint_id,session_id,request_text,status,source_context_json,created_at,updated_at) "
                         "VALUES (?,?,?,'completed',?,'2026-09-04','2026-09-04')",
                         (cp, sid, "previous task", json.dumps({"archived_material": "z" * 2_000_000})))
            conn.execute("INSERT INTO runtime_sessions "
                         "(session_id,started_at,updated_at,status,last_checkpoint_id) "
                         "VALUES (?,'2026-09-04','2026-09-04','completed',?)", (sid, cp))
            conn.executemany("INSERT INTO runtime_session_events "
                             "(session_id,seq,event_type,message,details_json,created_at) "
                             "VALUES (?,?,'task_progress','historical progress',?,'2026-09-04')",
                             [(sid, n, json.dumps({"status": "completed", "stage": str(n)}))
                              for n in range(1, 121)])
            rows.extend({"session_id": sid, "user": "previous message", "assistant": "previous response",
                         "ts": "2026-09-04"} for _ in range(8))
    (data / "conversation_log.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_cold_page_with_history_delivers_hi_and_survives_reload(tmp_path, monkeypatch):
    opener_prefixes = _production_greeting_prefixes("hi", monkeypatch)
    artifact = os.environ.get("VOOL_STARTUP_APP_ROOT")
    if artifact:
        app = Path(artifact).resolve()
        monkeypatch.setattr(rig, "REPO_ROOT", app)
        monkeypatch.setattr(rig.sys, "executable", str(app.parent / "python/bin/python3"))
    with rig.CapturingProvider(default="Hello! How can I help you today?") as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider,
                                  env_extra={"VOOL_INSTALL_PROFILE": "hybrid-fallback"})
        try:
            daemon.start(timeout=60)
            _seed_history(daemon.home)
            certification = daemon.certify(timeout=30)
            assert certification.get("state") == "verified", certification
            timings = {}
            for route in ("/api/chat/attachments/limits", "/api/chat/sessions", "/api/runtime/sessions"):
                started = time.monotonic()
                with urlopen(daemon.base_url + route, timeout=3) as response:
                    payload = json.load(response)
                timings[route] = round(time.monotonic() - started, 3)
                assert payload
                assert timings[route] < 2, timings
            manager, browser = served_browser.launch_chromium()
            try:
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                sends = []
                page.on("request", lambda request: sends.append(request.post_data)
                        if request.url == daemon.base_url + "/api/chat" else None)
                # The browser uses only the isolated daemon and scripted provider.
                page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(daemon.base_url)
                           else route.abort())
                for generation in range(2):
                    if generation:
                        page.reload(wait_until="domcontentloaded")
                    else:
                        page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
                    page.wait_for_selector("#input", timeout=5000)
                    provider.reset()
                    page.locator("#input").fill("Hi")
                    started = time.monotonic()
                    page.locator("#send").click()
                    try:
                        page.wait_for_function("view.run && view.run.released && view.run.status === 'completed'",
                                               timeout=15000)
                    except Exception:
                        print("HI_DIAGNOSTIC", generation, page.evaluate("({text:inputEl.value, chat:displayedChat, run:view.run && {status:view.run.status,text:view.run.text,released:view.run.released}})"),
                              page.locator(".msg.assistant .msg-text").all_text_contents(), sends, errors, flush=True)
                        raise
                    greeting = page.locator(".msg.assistant .msg-text").last.inner_text()
                    body = greeting.split("\n\n", 1)[0].strip()
                    assert any(body.startswith(prefix) for prefix in opener_prefixes), greeting
                    assert "smalltalk_fast_path" in greeting, greeting
                    timings[f"hi_{generation}"] = round(time.monotonic() - started, 3)
                    assert timings[f"hi_{generation}"] < 15, timings
                    # Hi uses the production deterministic greeting. A separate
                    # turn proves the provider path rather than mislabelling it.
                    page.locator("#input").fill("Write one friendly sentence about a harbor lantern.")
                    started = time.monotonic()
                    page.locator("#send").click()
                    try:
                        page.wait_for_function("[...document.querySelectorAll('.msg.assistant .msg-text')].at(-1).textContent.includes('Hello!')",
                                               timeout=15000)
                    except Exception:
                        print("BROWSER_DIAGNOSTIC", page.locator(".msg.assistant .msg-text").all_text_contents(),
                              "PROVIDER_CALLS", len(provider.calls), "ERRORS", errors, flush=True)
                        raise
                    timings[f"provider_{generation}"] = round(time.monotonic() - started, 3)
                    assert provider.calls, "no provider call for the model-backed turn"
                    page.wait_for_function("view.run && view.run.released", timeout=5000)
                    print("GENERATION", generation, timings, flush=True)
                assert not errors, errors
                # A hung model-selection read must neither dispatch nor trap
                # the composer. Exercise the actual timeout and Stop button.
                # Keep the real queue-claim request pending long enough to exercise
                # the previous terminal run coexisting with a new composer action.
                page.evaluate("""() => {
                    const original = queueOp;
                    queueOp = async function(op, chatId, extra) {
                        if (op === 'claim') await new Promise(resolve => setTimeout(resolve, 750));
                        return original(op, chatId, extra);
                    };
                    pumpQueue(displayedChat);
                }""")
                held = []
                page.route("**/api/cloud/model?*", lambda route: held.append(route))
                for cancel in (False, True):
                    # A released run can still own a pending queue claim. Do not
                    # mistake that run's terminal status for this button press.
                    page.wait_for_function("!isChatBusy(displayedChat)", timeout=5000)
                    previous_turn = page.evaluate("view.run && view.run.turnId")
                    before = len(sends)
                    held_before = len(held)
                    page.locator("#input").fill("Hi")
                    started = time.monotonic()
                    page.locator("#send").click()
                    page.wait_for_function(
                        "previous => view.run && view.run.turnId !== previous && !view.run.released",
                        arg=previous_turn, timeout=5000,
                    )
                    if cancel:
                        page.locator(".tc-stop").last.click(timeout=5000)
                    page.wait_for_function(
                        "previous => view.run && view.run.turnId !== previous && view.run.released",
                        arg=previous_turn, timeout=7000,
                    )
                    assert len(held) > held_before, "model-selection fault was never exercised"
                    state = page.evaluate("({status:view.run.status,error:view.run.error})")
                    assert state["status"] == ("cancelled" if cancel else "failed"), state
                    if not cancel:
                        assert "did not respond" in state["error"], state
                    assert len(sends) == before, "unreadable model state dispatched a turn"
                    timings["cancel" if cancel else "timeout"] = round(time.monotonic() - started, 3)
                page.unroute("**/api/cloud/model?*")
                print("STARTUP_TIMINGS " + json.dumps(timings), flush=True)
            finally:
                browser.close()
                manager.stop()
        finally:
            daemon.stop()
        assert daemon.process is None or daemon.process.poll() is not None
