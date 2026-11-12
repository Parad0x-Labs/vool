"""Failed-journey abort: the studio fails visibly or it does not fail at all.

A demo pipeline whose step can silently degrade into a hand-authored success
screen is exactly the staged-video fraud this lane exists to prevent. When any
journey step fails — or an expected public event never arrives — the run must
abort loudly: a typed exception, an abort report on disk, and no deliverable
manifest claiming success.

These tests exercise the orchestration core (``execute_capture``) against a
loopback fake daemon speaking the real public-event shapes and a scripted
driver implementing the same protocol as the Playwright driver. The real
product capture is proven separately (gated e2e); this suite pins the
pipeline's failure semantics.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from ops.demo_studio.journeys import JOURNEYS, StepSpec
from ops.demo_studio.runner import (
    JourneyAborted,
    StepOutcome,
    execute_capture,
)


class _EventsDaemon:
    """Loopback server answering /api/runtime/events with a controlled trace."""

    def __init__(self, events: list[dict[str, Any]]):
        self.events = events
        self.lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.port = self._server.server_address[1]

    def _handler(self):
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if not self.path.startswith("/api/runtime/events"):
                    self.send_response(404)
                    self.end_headers()
                    return
                with outer.lock:
                    rows = list(outer.events)
                body = json.dumps(
                    {"session_id": "demo-sess", "events": rows, "next_after": len(rows)}
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a) -> None:
                return

        return H

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class _ScriptedDriver:
    """Driver double implementing the JourneyDriver protocol.

    Steps succeed unless ``fail_step`` names one. Event collection goes over
    the real HTTP seam (the fake daemon above), so the studio's event-timeline
    code runs for real.
    """

    def __init__(self, wire, fail_step: str | None = None):
        self.wire = wire
        self.fail_step = fail_step
        self.executed: list[str] = []
        self.session_id = "demo-sess"
        self.closed = False

    def session(self) -> str:
        return self.session_id

    def drain_events(self) -> list[dict[str, Any]]:
        payload = self.wire.get_json(f"/api/runtime/events?session={self.session_id}")
        return list((payload or {}).get("events") or [])

    def observe_box(self, selector: str) -> dict[str, int] | None:
        return {"x": 10, "y": 10, "width": 400, "height": 300}

    def execute_step(self, step: StepSpec) -> StepOutcome:
        self.executed.append(step.id)
        if step.id == self.fail_step:
            return StepOutcome(ok=False, step_id=step.id, reason="scripted failure of " + step.id)
        return StepOutcome(ok=True, step_id=step.id, reason="")

    def finish_video(self) -> Path | None:
        return None

    def shutdown(self) -> None:
        self.closed = True


class _NoBootWire:
    def __init__(self, daemon: _EventsDaemon):
        self.base = f"http://127.0.0.1:{daemon.port}"

    def get_json(self, path: str) -> Any:
        import urllib.request

        with urllib.request.urlopen(self.base + path, timeout=5) as resp:
            return json.loads(resp.read())


def _run(spec_id: str, tmp_path: Path, daemon: _EventsDaemon, driver: _ScriptedDriver):
    from ops.demo_studio.redact import RedactionEngine

    engine = RedactionEngine(workspace_root="/tmp/ws", runtime_home="/tmp/home")
    return execute_capture(
        spec=JOURNEYS[spec_id],
        run_dir=tmp_path,
        wire=_NoBootWire(daemon),
        driver=driver,
        engine=engine,
        boot_report=None,
        model_lane="scripted_stub",
    )


def test_step_failure_aborts_with_report_and_no_success_manifest(tmp_path: Path) -> None:
    daemon = _EventsDaemon(
        [
            {"seq": 1, "event_type": "task_received", "message": "turn received", "created_at": 100.0},
            {"seq": 2, "event_type": "task_completed", "message": "done", "created_at": 105.0},
        ]
    )
    try:
        driver = _ScriptedDriver(_NoBootWire(daemon), fail_step="send")
        spec = JOURNEYS["local_chat"]
        step_ids = [s.id for s in spec.steps]
        driver.fail_step = step_ids[min(1, len(step_ids) - 1)]
        with pytest.raises(JourneyAborted) as excinfo:
            _run("local_chat", tmp_path, daemon, driver)
        assert excinfo.value.step_id == driver.fail_step
        report_path = tmp_path / "abort-report.json"
        assert report_path.is_file(), "an abort must leave its report on disk"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["journey"] == "local_chat"
        assert report["failed_step"] == driver.fail_step
        assert report["verdict"] == "aborted"
        # No deliverable manifest may exist claiming success.
        assert not list(tmp_path.glob("evidence.manifest.json"))
        assert driver.closed, "an aborted run must still tear its driver down"
    finally:
        daemon.close()


def test_missing_expected_event_aborts_the_journey(tmp_path: Path) -> None:
    # The journey names the public events that prove it happened; a trace
    # that never carries them is a failed journey even when every click
    # "worked" — that is the anti-fake-success gate.
    daemon = _EventsDaemon(
        [{"seq": 1, "event_type": "task_received", "message": "hi", "created_at": 1.0}]
    )
    try:
        driver = _ScriptedDriver(_NoBootWire(daemon))
        with pytest.raises(JourneyAborted) as excinfo:
            _run("local_chat", tmp_path, daemon, driver)
        report = json.loads((tmp_path / "abort-report.json").read_text(encoding="utf-8"))
        assert "task_completed" in str(excinfo.value) or any(
            "task_completed" in str(m) for m in report["missing_expected_events"]
        ), "the abort must name the expected event that never arrived"
    finally:
        daemon.close()


def test_abort_report_is_redacted(tmp_path: Path) -> None:
    daemon = _EventsDaemon(
        [
            {
                "seq": 1,
                "event_type": "tool_executed",
                "message": "ran against /Users/someuser/.vool-isolated/secret.db",
                "created_at": 1.0,
            }
        ]
    )
    try:
        driver = _ScriptedDriver(_NoBootWire(daemon), fail_step="send")
        spec = JOURNEYS["local_chat"]
        driver.fail_step = spec.steps[min(1, len(spec.steps) - 1)].id
        with pytest.raises(JourneyAborted):
            _run("local_chat", tmp_path, daemon, driver)
        raw = (tmp_path / "abort-report.json").read_text(encoding="utf-8")
        assert "/Users/someuser" not in raw, "abort reports carry no private paths"
    finally:
        daemon.close()


def test_successful_pipeline_writes_valid_jsonl_and_clean_manifest(tmp_path: Path) -> None:
    # The success path with no video (hermetic driver): the public event
    # mirror must be valid single-line JSONL, the manifest must carry only
    # the artifacts that really exist, and verification must be clean.
    daemon = _EventsDaemon(
        [
            {"seq": 1, "event_type": "task_received", "message": "hi", "created_at": 1.0},
            {"seq": 2, "event_type": "task_completed", "message": "done", "created_at": 5.0},
        ]
    )
    try:
        driver = _ScriptedDriver(_NoBootWire(daemon))
        outcome = _run("local_chat", tmp_path, daemon, driver)
        assert outcome.verdict == "pass"
        rows = [
            json.loads(line)
            for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert [r["event_type"] for r in rows] == ["task_received", "task_completed"]
        assert all("\n" not in line for line in (tmp_path / "events.jsonl").read_text().splitlines())
        manifest = json.loads((tmp_path / "evidence.manifest.json").read_text(encoding="utf-8"))
        assert manifest["verification"]["violations"] == []
        assert manifest["artifacts"] == {}, "no video was produced; none may be claimed"
        assert manifest["honesty"]["captured"] is True
    finally:
        daemon.close()
