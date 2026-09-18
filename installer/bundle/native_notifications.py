"""The window host's side of macOS notifications: it runs the notification helper while the host runs and relays between
the helper and the runtime.

The runtime owns the outbox and every state (core/operator/native_notifications.py); the Swift helper owns the calls
into the macOS notification center (core/notifications_macos.py). This module only moves JSON lines: helper events are
posted to ``/api/notifications/native/report`` and the outbox from ``/api/notifications/native/outbox`` is handed to
the helper. Nothing here starts at login or keeps running after the host exits: closing the helper's stdin ends it,
and a host that dies closes the pipe the same way.
"""
from __future__ import annotations

import contextlib
import json
import queue
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

STEP_SECONDS = 5.0
LIST_EVERY_SECONDS = 60.0
REQUEST_TIMEOUT_SECONDS = 5.0
BACKLOG_CAP = 500


class HelperProcess:
    """The helper as a child process speaking one JSON object per line on stdin and stdout."""

    def __init__(self, argv: list[str], *, env: dict[str, str] | None = None) -> None:
        self._argv = [str(part) for part in argv]
        self._env = env
        self._process: subprocess.Popen | None = None
        self._events: queue.Queue = queue.Queue()
        self._reader: threading.Thread | None = None
        self._write_lock = threading.Lock()

    def start(self) -> None:
        self._process = subprocess.Popen(self._argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=self._env)
        self._reader = threading.Thread(target=self._read, name="vool-notify-reader", daemon=True)
        self._reader.start()

    def _read(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for raw in process.stdout:
            try:
                event = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            if isinstance(event, dict):
                self._events.put(event)

    def send(self, message: dict[str, Any]) -> bool:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            return False
        data = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        with self._write_lock:
            try:
                process.stdin.write(data)
                process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                return False
        return True

    def drain(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                return events

    def alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def stop(self, timeout: float = 5.0) -> int | None:
        process = self._process
        if process is None:
            return None
        with contextlib.suppress(Exception):
            if process.stdin is not None:
                process.stdin.close()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if self._reader is not None:
            self._reader.join(timeout=2)
        return process.returncode


class NotificationBridgePump:
    """One relay cycle at a time: helper events to the runtime, then the runtime's outbox to the helper."""

    def __init__(self, *, helper: Any, fetch_outbox: Callable[[], dict[str, Any]], post_report: Callable[[list[dict[str, Any]]], dict[str, Any]],
                 on_open: Callable[[dict[str, Any]], None] | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self._helper = helper
        self._fetch_outbox = fetch_outbox
        self._post_report = post_report
        self._on_open = on_open
        self._clock = clock
        self._backlog: list[dict[str, Any]] = []
        self._listed_at: float | None = None

    def step(self) -> dict[str, Any]:
        result: dict[str, Any] = {"reported": 0, "handed": 0, "error": ""}
        events = self._backlog + list(self._helper.drain())
        self._backlog = []
        if events:
            try:
                reply = self._post_report(events) or {}
            except Exception as exc:
                self._backlog = events[-BACKLOG_CAP:]  # kept for the next cycle; the runtime ignores repeats
                result["error"] = f"report: {type(exc).__name__}"
            else:
                result["reported"] = len(events)
                for entry in reply.get("opened") or []:
                    if self._on_open is not None and isinstance(entry, dict):
                        with contextlib.suppress(Exception):
                            self._on_open(entry)
        try:
            outbox = self._fetch_outbox() or {}
        except Exception as exc:
            result["error"] = result["error"] or f"outbox: {type(exc).__name__}"
            return result
        requests = [request for request in outbox.get("requests") or [] if isinstance(request, dict)]
        authorize, settings = bool(outbox.get("want_authorization")), bool(outbox.get("want_settings"))
        now = self._clock()
        list_due = self._listed_at is None or now - self._listed_at >= LIST_EVERY_SECONDS
        if requests or authorize or settings or list_due:
            # send() answers False when the helper's pipe is closed; only then was nothing handed.
            sent = self._helper.send({"cmd": "apply", "requests": requests, "authorize": authorize, "settings": settings, "list": list_due})
            if sent is not False:
                result["handed"] = len(requests)
                if list_due:
                    self._listed_at = now
        return result


def _post_json(origin: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    """One loopback call to this host's own runtime."""
    request = urllib.request.Request(origin.rstrip("/") + path, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8") or "{}")
    return payload if isinstance(payload, dict) else {}


class HostNotificationBridge:
    """The helper and the pump on one background thread, for the window host's lifetime."""

    def __init__(self, *, api_origin: str, resolve_helper: Callable[[], Path | None], on_open: Callable[[dict[str, Any]], None] | None = None,
                 log: Callable[[str], None] | None = None) -> None:
        self._origin = str(api_origin).rstrip("/")
        self._resolve_helper = resolve_helper
        self._on_open = on_open
        self._log = log or (lambda _message: None)
        self._bridge_id = "host-" + uuid.uuid4().hex[:12]
        self._stop = threading.Event()
        self._helper: HelperProcess | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="vool-notify-bridge", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        from core.notifications_macos import PROTOCOL_VERSION, helper_executable

        try:
            helper_app = self._resolve_helper()
        except Exception as exc:
            self._log(f"macOS notifications unavailable: the helper could not be prepared ({exc})")
            return
        if helper_app is None or self._stop.is_set():
            return
        self._helper = HelperProcess([str(helper_executable(helper_app)), "serve"])
        try:
            self._helper.start()
        except OSError as exc:
            self._log(f"macOS notifications unavailable: the helper did not start ({exc})")
            return
        self._log(f"notification helper running from {helper_app}")
        pump = NotificationBridgePump(
            helper=self._helper,
            fetch_outbox=lambda: _post_json(self._origin, "/api/notifications/native/outbox",
                                            {"bridge_id": self._bridge_id, "helper_version": PROTOCOL_VERSION}),
            post_report=lambda events: _post_json(self._origin, "/api/notifications/native/report",
                                                  {"bridge_id": self._bridge_id, "events": events}),
            on_open=self._on_open,
        )
        last_error = ""
        while not self._stop.is_set():
            if not self._helper.alive():
                self._log("the notification helper exited; macOS notifications resume when VOOL starts again")
                return
            error = str(pump.step().get("error") or "")
            if error and error != last_error:
                self._log(f"notification bridge: {error}")
            last_error = error
            self._stop.wait(STEP_SECONDS)

    def stop(self) -> None:
        self._stop.set()
        if self._helper is not None:
            self._helper.stop(timeout=3)
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)


def start_for_host(*, api_origin: str, on_open: Callable[[dict[str, Any]], None] | None = None,
                   log: Callable[[str], None] | None = None) -> HostNotificationBridge | None:
    """Start the bridge on macOS: the helper shipped in this bundle, or one compiled for a source checkout (in the
    background, so the window never waits for the compiler). Returns None where macOS notifications cannot run."""
    if sys.platform != "darwin":
        return None
    from core import notifications_macos

    def resolve() -> Path | None:
        shipped = notifications_macos.shipped_helper_app()
        if shipped is not None:
            return shipped
        if not notifications_macos.toolchain_available():
            (log or (lambda _message: None))("this build has no notification helper and this Mac has no Swift toolchain to build one")
            return None
        return notifications_macos.cached_helper_app()

    bridge = HostNotificationBridge(api_origin=api_origin, resolve_helper=resolve, on_open=on_open, log=log)
    bridge.start()
    return bridge


__all__ = ["HelperProcess", "HostNotificationBridge", "NotificationBridgePump", "start_for_host"]
