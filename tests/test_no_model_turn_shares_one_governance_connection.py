"""SERVED: a no-model turn checks its runtime records under one governance connection per write.

Every runtime event and checkpoint update gates each string it carries against payload
availability (WITHHELD / ERASED bytes must never be written back). Each of those checks opened
its own SQLite connection, so one ordinary chat turn with no model set up opened about a thousand
governance connections and spent seconds doing it (measured on this daemon: 990 for the turn
below, 1564 for a weather question; 16 to 43 once each write shares one read scope).

The daemon is the real ``apps.vool_api_server`` in its own process and home, with no model
configured. The only addition is in-process instrumentation, installed before the daemon starts:
``sqlite3.connect`` is wrapped to log each connection opened by the availability check, and a
labelled FAULT INJECTION makes that check's connection fail while a trigger file exists. Nothing
in the turn is substituted.

Pinned:
* the availability check opens fewer than ``AVAILABILITY_CONNECTION_BOUND`` connections for one
  no-model turn (before the fix: 990);
* an availability outage still fails closed: nothing the turn carried is written into its runtime
  events while the store cannot be read.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.request import urlopen

import pytest

from tests.usepod.test_usepod_served_flow import LAUNCHER, UsePodServedDaemon, _answer_text, _session

REPO_ROOT = Path(__file__).resolve().parents[1]
# Measured with one read scope per write: 16 (greeting), 26 (this turn), 43 (weather). Before: 343,
# 990, 1564. The bound leaves room for a few more events per turn and still catches the regression.
AVAILABILITY_CONNECTION_BOUND = 150
PROMPT = "explain how photosynthesis works in two sentences"

_BOOTSTRAP = r"""
import os, runpy, sqlite3, sys, threading, traceback

_log = os.environ["GOVERNANCE_PROBE_LOG"]
_trigger = os.environ["GOVERNANCE_OUTAGE_TRIGGER"]
_lock = threading.Lock()
_connect = sqlite3.connect


def _from_availability_check():
    return any(frame.name == "_availability_connection" and frame.filename.endswith("finalization.py")
               for frame in traceback.extract_stack(limit=12))


def connect(*args, **kwargs):
    if _from_availability_check():
        with _lock, open(_log, "a", encoding="utf-8") as handle:
            handle.write("availability\n")
        if os.path.exists(_trigger):
            # FAULT INJECTION (test only): the governance store cannot be read.
            raise sqlite3.OperationalError("injected governance outage")
    return _connect(*args, **kwargs)


sqlite3.connect = connect
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
"""


class InstrumentedDaemon(UsePodServedDaemon):
    """The served daemon, no model configured, with the connection log and outage trigger."""

    def __init__(self, home: Path) -> None:
        super().__init__(home)
        self.probe_log = home.parent / "availability_connections.log"
        self.outage_trigger = home.parent / "governance_outage"

    def env(self) -> dict[str, str]:
        env = super().env()
        env["GOVERNANCE_PROBE_LOG"] = str(self.probe_log)
        env["GOVERNANCE_OUTAGE_TRIGGER"] = str(self.outage_trigger)
        return env

    def start(self, timeout: float = 240.0) -> InstrumentedDaemon:
        self.home.mkdir(parents=True, exist_ok=True)
        handle = self.log_path.open("wb")
        self.process = subprocess.Popen(
            [sys.executable, "-B", "-c", _BOOTSTRAP, str(LAUNCHER), "--port", str(self.port), "--bind", "127.0.0.1"],
            cwd=str(REPO_ROOT),
            env=self.env(),
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"daemon exited {self.process.returncode}\n{self.log_tail()}")
            try:
                with urlopen(f"{self.base_url}/healthz", timeout=3) as response:
                    if response.status == 200:
                        return self
            except OSError:
                time.sleep(1.0)
        raise TimeoutError(f"daemon never became healthy\n{self.log_tail()}")

    def availability_connections(self) -> int:
        if not self.probe_log.exists():
            return 0
        return len(self.probe_log.read_text(encoding="utf-8").splitlines())

    def turn(self, text: str, session_id: str) -> tuple[int, object]:
        payload = {"messages": [{"role": "user", "content": text}], "stream": False, "session_id": session_id, "mode": "auto"}
        return self.call("POST", "/api/chat", payload, timeout=300.0)


@pytest.fixture(scope="module")
def daemon(tmp_path_factory):
    rig = InstrumentedDaemon(tmp_path_factory.mktemp("governance-scope") / "home")
    try:
        rig.start()
        # Warm the daemon (first-turn schema work is not what this file measures).
        rig.turn("hello", _session(f"governance-warm-{uuid.uuid4()}"))
        yield rig
    finally:
        rig.stop()


def test_a_no_model_turn_stays_under_the_governance_connection_bound(daemon) -> None:
    session = _session(f"governance-count-{uuid.uuid4()}")
    before = daemon.availability_connections()
    status, answer = daemon.turn(PROMPT, session)
    opened = daemon.availability_connections() - before
    events = daemon.events(session)
    assert status == 200, answer
    assert _answer_text(answer).strip(), answer
    assert events, "the turn recorded no runtime events, so nothing was measured"
    assert opened > 0, "the instrumentation saw no availability check; the measurement is not running"
    assert opened < AVAILABILITY_CONNECTION_BOUND, (
        f"{opened} availability connections for one no-model turn ({len(events)} events)"
    )


def test_an_availability_outage_still_keeps_the_turn_out_of_its_runtime_events(daemon) -> None:
    marker = f"zephyr-{uuid.uuid4().hex[:10]}"
    session = _session(f"governance-outage-{uuid.uuid4()}")
    daemon.outage_trigger.write_text("1", encoding="utf-8")
    try:
        before = daemon.availability_connections()
        status, _answer = daemon.turn(f"explain the word {marker} in two sentences", session)
        attempted = daemon.availability_connections() - before
    finally:
        daemon.outage_trigger.unlink()
    assert attempted > 0, "no availability check ran during the outage; the fault was never exercised"
    assert status in (200, 500), _answer
    events = daemon.events(session)
    leaked = [event for event in events if marker in json.dumps(event, default=str)]
    assert not leaked, f"{len(leaked)} runtime events were written with the turn's text during the outage"
    # After the outage the same daemon writes events again: the refusal was the outage, not a broken writer.
    after_session = _session(f"governance-after-{uuid.uuid4()}")
    status, answer = daemon.turn(f"explain the word {marker} in two sentences", after_session)
    assert status == 200, answer
    assert any(marker in json.dumps(event, default=str) for event in daemon.events(after_session))
