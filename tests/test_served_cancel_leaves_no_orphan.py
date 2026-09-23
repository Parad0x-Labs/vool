"""Cancelling a turn mid-retrieval ends the stream with `task.cancelled` and leaves no worker behind.

Scratch daemon, fixture transport whose search answers only after a delay so the turn is reliably
mid-retrieval when `/api/chat/cancel` lands. Assertions are environmental: the typed stream ends
with the cancel, no fixture fetch starts after the cancel was acknowledged, and a second cancel
reports `not_found` because the worker released its registration.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

import tests._reader_served_rig as rig

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "comparison_coverage"
SHIM = ROOT / "tests" / "fixture_transport"


def _slow_manifest(tmp_path: Path) -> Path:
    manifest = json.loads((FIX / "manifest_complete.json").read_text())
    for rule in manifest["rules"]:
        if rule.get("body_file"):
            rule["body_file"] = str((FIX / rule["body_file"]).resolve())
        if rule["host"] == "search.yahoo.com":
            rule["delay_s"] = 4.0
    target = tmp_path / "manifest_slow.json"
    target.write_text(json.dumps(manifest))
    return target


def test_cancel_mid_retrieval_ends_the_turn_and_stops_new_fetches(tmp_path):
    log = tmp_path / "transport.log"
    env_extra = {
        "PYTHONPATH": os.pathsep.join([str(SHIM), str(rig.REPO_ROOT), str(rig.READER_DEPS)]),
        "VOOL_FIXTURE_TRANSPORT_MANIFEST": str(_slow_manifest(tmp_path)),
        "VOOL_FIXTURE_TRANSPORT_LOG": str(log),
        "ALLOW_BROWSER_FALLBACK": "0", "WEB_SEARCH_PROVIDER_ORDER": "google_html",
    }
    with rig.CapturingProvider(default="A comparison.") as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider, env_extra=env_extra)
        daemon.register_provider()
        daemon.start()
        try:
            session = rig.canonical_session("cancel-" + uuid.uuid4().hex[:8])
            turn_id = uuid.uuid4().hex
            body = {"messages": [{"role": "user", "content": "Compare the VW Passat and the VW Golf in detail: production periods, sales, regions, engines and prices."}],
                    "stream": True, "stream_task_events": True, "session_id": session, "turn_id": turn_id,
                    "model": daemon.model, "model_selection": "sticky", "mode": "ask"}
            typed: list[str] = []
            answer: list[str] = []
            cancel_state: dict = {}

            def _stream():
                req = Request(f"{daemon.base_url}/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
                with urlopen(req, timeout=600) as resp:
                    for raw in resp:
                        line = raw.decode("utf-8", "replace").strip()
                        try:
                            obj = json.loads(line) if line else {}
                        except Exception:
                            continue
                        if obj.get("vool_event"):
                            typed.append(str(obj["vool_event"].get("type")))
                        msg = obj.get("message")
                        if isinstance(msg, dict) and msg.get("content"):
                            answer.append(str(msg["content"]))

            reader = threading.Thread(target=_stream, daemon=True)
            reader.start()
            deadline = time.time() + 60
            while time.time() < deadline and not (log.exists() and "search.yahoo.com" in log.read_text()):
                time.sleep(0.2)
            assert log.exists() and "search.yahoo.com" in log.read_text(), "the turn never reached retrieval"
            cancel_req = Request(f"{daemon.base_url}/api/chat/cancel", data=json.dumps({"session_id": session, "turn_id": turn_id}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(cancel_req, timeout=30) as r:
                cancel_state = json.loads(r.read().decode())
            cancelled_at = time.time()
            reader.join(timeout=180)
            assert not reader.is_alive(), "the stream never ended after the cancel"
            assert cancel_state.get("state") == "cancelled", cancel_state
            assert "task.cancelled" in typed, typed
            published = "".join(answer)
            assert "37 million" not in published and "1974" not in published, published[:300]
            assert "cancelled" in published.lower(), published[:300]
            late = [json.loads(l) for l in log.read_text().splitlines() if json.loads(l)["ts"] > cancelled_at + 6.0]
            assert not late, f"fetches started long after the cancel: {[(r['rule'], r['outcome']) for r in late]}"
            with urlopen(cancel_req, timeout=30) as r:
                again = json.loads(r.read().decode())
            assert again.get("state") == "not_found", again
        finally:
            daemon.stop()
