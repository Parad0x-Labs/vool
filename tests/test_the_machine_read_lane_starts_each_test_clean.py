"""A machine read performed by one test must not decide how the next test routes.

The elliptical follow-up lane remembers the last grounded machine read per session, so that
"ok what about D?" re-runs the disk tool instead of letting a model invent a figure. That memory
is deliberately durable: it is held in an in-process cache AND written through to the
``machine_read_memory`` table so a follow-up survives a server restart between turns.

Under pytest, durable is the problem. ``machine_read_memory`` was added after
``reset_runtime_continuity_state()``'s table list and never registered on it, so the row outlived
every per-test reset for the whole session -- and ``_recall_machine_read`` reads the table on a
cache miss. Measured before the fix: a test that ran "disk space on C:" for session "s1" made a
LATER test's bare "ok what about D?" for that session re-run ``machine.disk_usage`` and return an
answer, which is exactly what
``test_machine_followup_routing.py::test_bare_drive_letter_without_a_prior_read_is_not_hijacked``
forbids. That test never caught it: it escapes by hand-picking a session id ("s3") no other test
uses. Isolation resting on unique-looking string literals is isolation that holds until someone
picks an obvious name.

Clearing one half is not a fix. ``reset_machine_followup_state()`` drops only the cache -- on
purpose, since ``test_machine_read_memory.py`` calls it to simulate a restart and then asserts the
table still answers -- so the recall path just re-seeds the cache from the table.

Two guards below, because the leak has two ways back in:
  1. the leak itself, driven end to end: the second test fails if either half stops being reset;
  2. the shape: a new persisted store in ``core/runtime_continuity.py`` that nobody registered on
     the reset list fails here, at the commit that adds it, instead of years later as a test that
     only fails in the full suite.
"""
from __future__ import annotations

import re
import types
from pathlib import Path

import pytest

import core.agent_runtime.fast_paths_machine as fp
from core import runtime_continuity

# --------------------------------------------------------------------------------------
# 1. the leak, driven end to end
# --------------------------------------------------------------------------------------

# One session id, shared by both tests below ON PURPOSE. Every other machine test avoids
# collisions by choosing an unused literal; this pair needs the collision, because a collision is
# what the leak needs and what a careless future test will eventually produce by accident.
LEAKY_SESSION = "machine-read-leak-guard"


class _FakeAgent:
    def _emit_runtime_event(self, source_context, *, event_type, message, **details) -> None:
        pass

    def _fast_path_result(self, **kwargs):
        return {"response": kwargs.get("response", ""), "task_id": "t"}


def _recording_tool(calls: list[tuple[str, dict]]):
    def _run(intent, arguments=None, *, source_context=None):
        calls.append((str(intent), dict(arguments or {})))
        return types.SimpleNamespace(
            ok=True, response_text=f"[{intent}]", status="executed", details={"observation": {}}
        )

    return _run


def _ask(text: str, calls: list[tuple[str, dict]]):
    return fp.maybe_handle_direct_machine_read_request(
        _FakeAgent(),
        text,
        session_id=LEAKY_SESSION,
        source_surface="api",
        source_context={"session_id": LEAKY_SESSION},
    )


def test_one_test_runs_a_real_disk_read_and_the_lane_remembers_it(monkeypatch) -> None:
    """The setup half. This is an ordinary passing test -- it is the NEXT one that carries the claim."""

    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(fp, "execute_authorized_runtime_tool", _recording_tool(calls))

    assert _ask("disk space on C:", calls) is not None
    assert calls[-1] == ("machine.disk_usage", {"drive": "C:\\"})
    # Remembered in both halves: the cache the next turn reads first, and the table it falls back
    # to. Neither is this test's business to clean up -- that is the point being guarded.
    assert fp._recall_machine_read(LEAKY_SESSION) == {"kind": "disk", "drive": "C:\\"}


def test_the_next_test_is_not_hijacked_by_the_read_the_previous_one_ran(monkeypatch) -> None:
    """The claim. A bare drive letter with no prior read in THIS test must reach no tool at all.

    Runs after the test above, in the same file, and shares its session id. If either half of the
    per-test reset goes away, the leaked "disk" memory turns this sentence into an elliptical
    follow-up and the fast path answers it from the disk tool.
    """

    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(fp, "execute_authorized_runtime_tool", _recording_tool(calls))

    # The memory is gone before the turn starts -- both halves.
    assert fp._recall_machine_read(LEAKY_SESSION) is None, (
        "the last machine read of a previous test is still readable; "
        "one test's disk read is about to answer for another one"
    )

    result = _ask("ok what about D?", calls)
    assert result is None, f"a bare drive letter was claimed on leaked state, running {calls}"
    assert calls == [], f"no tool should have run, ran {calls}"


# --------------------------------------------------------------------------------------
# 2. the shape -- so the next store cannot be added without a reset
# --------------------------------------------------------------------------------------

# A store that must deliberately outlive the per-test reset goes here, with the reason. Empty
# today: every persisted store in the module is per-session or per-turn state, and none of it
# should answer for a test that did not create it.
_DELIBERATELY_NOT_RESET: frozenset[str] = frozenset()

_WRITE_SITE_RE = re.compile(
    r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+([a-z_]+)|UPDATE\s+([a-z_]+)\s+SET",
    re.IGNORECASE,
)


def _tables_written_by_runtime_continuity() -> set[str]:
    source = Path(runtime_continuity.__file__).read_text(encoding="utf-8")
    written: set[str] = set()
    for insert_target, update_target in _WRITE_SITE_RE.findall(source):
        written.add(insert_target or update_target)
    return written


def test_every_persisted_store_in_the_continuity_module_is_reset_between_tests() -> None:
    """The reset list must cover every table the module writes, or state crosses tests.

    This is the check that was missing when ``machine_read_memory`` landed. It reads the module's
    own write sites rather than a hand-kept list, so the guard cannot drift away from the code:
    add a store, and this fails until it is registered on ``_RESET_TABLES`` or declared above.
    """

    written = _tables_written_by_runtime_continuity()
    assert written, "found no write sites at all -- this guard has stopped reading the module"
    assert "machine_read_memory" in written, (
        "the store whose leak this file exists for is no longer written here; "
        "re-point the guard at wherever the last-machine-read memory now lives"
    )

    unreset = written - set(runtime_continuity._RESET_TABLES) - _DELIBERATELY_NOT_RESET
    assert not unreset, (
        f"{sorted(unreset)} is written by core/runtime_continuity.py but never cleared by "
        "reset_runtime_continuity_state(), so one test's rows are readable by every test after "
        "it. Add it to _RESET_TABLES, or to _DELIBERATELY_NOT_RESET in this file with the reason."
    )


@pytest.mark.parametrize("table", sorted(runtime_continuity._RESET_TABLES))
def test_the_reset_list_names_no_table_the_module_stopped_writing(table: str) -> None:
    """The opposite drift: a renamed or dropped table leaves a reset that silently does nothing."""

    assert table in _tables_written_by_runtime_continuity(), (
        f"{table!r} is on the reset list but nothing in core/runtime_continuity.py writes it -- "
        "the DELETE is a no-op and the real store, if it moved, is no longer being cleared"
    )
