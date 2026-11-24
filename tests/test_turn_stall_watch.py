"""A chat turn that runs long names the frame it is waiting in (core.turn_stall_watch)."""

from __future__ import annotations

import logging
import threading
import time

from core.turn_stall_watch import watch_turn
from tests._person_turn_rig import person_env  # the fixture


def _waiting_in_a_named_place(release: threading.Event) -> None:
    release.wait(5)


def test_a_long_turn_logs_the_frame_it_waits_in(monkeypatch, caplog) -> None:
    monkeypatch.setenv("VOOL_TURN_STALL_DUMP_SECONDS", "0.2")
    monkeypatch.setenv("VOOL_TURN_STALL_DUMP_EVERY_SECONDS", "10")
    release = threading.Event()
    secret = "my pin is 4471"
    with caplog.at_level(logging.WARNING, logger="core.turn_stall_watch"):
        with watch_turn("chat turn"):
            deadline = time.monotonic() + 5
            while not caplog.records and time.monotonic() < deadline:
                threading.Timer(0.5, release.set).start()
                _waiting_in_a_named_place(release)
                release.clear()
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "chat turn still running after" in text
    assert "_waiting_in_a_named_place" in text and "test_turn_stall_watch.py" in text
    assert secret not in text, "frames only, never local values"


def test_a_turn_still_running_after_the_first_dumps_is_named_again_later(monkeypatch, caplog) -> None:
    """Intel smoke on 3727b4e72: the last dump came 80 s into a turn that ran past 15 minutes."""
    import core.turn_stall_watch as stall_watch

    monkeypatch.setenv("VOOL_TURN_STALL_DUMP_SECONDS", "0.1")
    monkeypatch.setenv("VOOL_TURN_STALL_DUMP_EVERY_SECONDS", "0.1")
    monkeypatch.setattr(stall_watch, "_MAX_DUMPS", 1)
    monkeypatch.setattr(stall_watch, "_LATE_EVERY_S", 0.2)
    monkeypatch.setattr(stall_watch, "_MAX_LATE_DUMPS", 2)
    with caplog.at_level(logging.WARNING, logger="core.turn_stall_watch"):
        with watch_turn("chat turn"):
            time.sleep(1.2)
    assert len(caplog.records) == 3, [record.getMessage()[:60] for record in caplog.records]


def test_a_quick_turn_logs_nothing(monkeypatch, caplog) -> None:
    monkeypatch.setenv("VOOL_TURN_STALL_DUMP_SECONDS", "0.3")
    with caplog.at_level(logging.WARNING, logger="core.turn_stall_watch"):
        with watch_turn("chat turn"):
            pass
        time.sleep(0.5)
    assert not caplog.records


def test_zero_turns_the_watch_off(monkeypatch, caplog) -> None:
    monkeypatch.setenv("VOOL_TURN_STALL_DUMP_SECONDS", "0")
    before = {t.name for t in threading.enumerate()}
    with caplog.at_level(logging.WARNING, logger="core.turn_stall_watch"):
        with watch_turn("chat turn"):
            assert "turn-stall-watch" not in {t.name for t in threading.enumerate()} - before
    assert not caplog.records


class _Kept(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def test_a_served_turn_that_stalls_names_its_frame(person_env, monkeypatch) -> None:
    from tests._person_turn_rig import make_agent, reply, turn

    monkeypatch.setenv("VOOL_TURN_STALL_DUMP_SECONDS", "0.3")
    monkeypatch.setenv("VOOL_TURN_STALL_DUMP_EVERY_SECONDS", "0.25")
    import core.turn_stall_watch as stall_watch

    # The turn may spend a while before the slow gate; keep dumping until it is seen there.
    monkeypatch.setattr(stall_watch, "_MAX_DUMPS", 200)
    agent = make_agent()
    real = type(agent)._maybe_answer_attempt_followup_turn
    called = []

    def slow_gate(self, *args, **kwargs):
        called.append(1)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not any("slow_gate" in line for line in kept.lines):
            time.sleep(0.1)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(type(agent), "_maybe_answer_attempt_followup_turn", slow_gate)
    log = logging.getLogger("core.turn_stall_watch")
    kept = _Kept()
    log.addHandler(kept)
    monkeypatch.setattr(log, "disabled", False)
    monkeypatch.setattr(log, "level", logging.WARNING)
    try:
        result = turn(agent, "Calculate 23 * 19", session="stall-watch")
    finally:
        log.removeHandler(kept)
    assert "437" in reply(result) and called
    text = "\n".join(kept.lines)
    assert "chat turn still running after" in text and "slow_gate" in text and "_run_once_inner" in text
