"""The bounded walk must bound EVERYTHING it touches: enumeration, reads, and its own verdicts.

What this file pins
-------------------
Three falsifiable holes remained in the exact-count traversal after the first bounded-walk pass,
each written RED-first:

1. EAGER DIRECTORY MATERIALIZATION. `list(os.scandir(dir))` consumed every entry of a directory
   before the entry budget was consulted — one directory with millions of entries consumed
   unbounded memory before the 250k budget could fire. The walk must STREAM entries and enforce
   the entry and time budgets while iterating.
2. READS THAT IGNORE THE REMAINING BUDGETS. `_count_file_lines` checked the size once and then
   read to EOF: a file growing while read blew through the per-file ceiling, the total-byte
   budget, and the deadline, and the outer loop could not intervene until the read returned. The
   reader must take the remaining total-byte budget and the deadline, check them INSIDE the chunk
   loop, and return a typed budget outcome — never reading materially past the bound.
3. SWALLOWED POST-READ RACES. A failed post-read stat was silently ignored and an in-place
   mutation during the read was never looked for — yet the result claimed no entry changed during
   the walk. The reader must verify identity, size and mtime after reading (fstat on the open fd
   and lstat after close); any unverifiable or changed post-read state is a race, the entry is
   reported, `complete` is False, and no exact wording ships.

Byte accounting belongs to what was actually READ (`bytes_read`), not to a later stat that may
itself fail or disagree. Tie-breaking, no-follow protection, typed partials, the restored 120s
global gauntlet timeout and the measured 240s g1_continuation group-local budget are all out of
this file's scope and stay as they are.
"""
from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import core.folder_overview as folder_overview
from core.folder_overview import _count_file_lines, build_exact_file_facts, measure_workspace_files


def _write(path: Path, lines: int) -> None:
    path.write_text("\n".join(f"l{n}" for n in range(lines)) + "\n", encoding="utf-8")


# ------------------------------------------------------------- 1. lazy enumeration (no list())


def test_a_directory_larger_than_the_entry_budget_is_never_materialized(tmp_path: Path, monkeypatch):
    """A virtual directory of 50,000 entries behind a LAZY scandir; budget is 50.

    The fake counts every entry the walk CONSUMES and explodes past max_entries + a small
    lookahead. Materializing the directory consumes all 50,000 and explodes; a streaming walk
    stops at the budget having consumed only ~51.
    """
    consumed = {"count": 0}
    limit = 50
    real_scandir = os.scandir

    def fake_scandir(path):
        try:
            real_entries = list(real_scandir(path))
        except OSError:
            real_entries = []

        @contextlib.contextmanager
        def cm():
            def stream():
                for index in range(50_000):
                    consumed["count"] += 1
                    if consumed["count"] > limit + 8:
                        raise AssertionError(
                            f"walk consumed {consumed['count']} entries with a budget of {limit}"
                        )
                    if index < len(real_entries):
                        yield real_entries[index]
                    else:
                        virtual = SimpleNamespace(
                            name=f"virtual{index:05d}.py",
                            path=str(tmp_path / f"virtual{index:05d}.py"),
                        )
                        virtual.is_dir = lambda follow_symlinks=False: False
                        virtual.is_file = lambda follow_symlinks=False: True
                        yield virtual

            yield stream()

        return cm()

    monkeypatch.setattr(folder_overview.os, "scandir", fake_scandir)

    measurement = measure_workspace_files(str(tmp_path), "py", max_entries=limit)

    assert measurement is not None, "the walk must survive and terminate, not explode"
    assert consumed["count"] <= limit + 8, (
        f"the walk materialized {consumed['count']} entries for a {limit}-entry budget"
    )
    assert "entry budget" in measurement.termination_reason, measurement.termination_reason
    assert measurement.complete is False


# ------------------------------------------------------- 2. reads respect the remaining budget


def test_the_reader_stops_at_the_remaining_total_byte_budget(tmp_path: Path):
    big = tmp_path / "big.py"
    big.write_text(("x" * 79 + "\n") * 1_300, encoding="utf-8")  # ~103 KB, no NUL bytes

    read = _count_file_lines(str(big), remaining_bytes=64)

    assert read.outcome == "total-byte-budget", read.outcome
    assert read.bytes_read <= 64, read.bytes_read


def test_a_file_growing_while_read_cannot_outread_the_total_byte_budget(
    tmp_path: Path, monkeypatch
):
    """A file whose read never reaches EOF (endless chunks) must be cut off by the budget.

    The fake read produces unlimited fresh bytes; a watchdog trips it if the reader consumes more
    than a few chunks past a 4 KiB budget, so this test can never hang.
    """
    real_read = os.read
    (tmp_path / "endless.py").write_text("x = 1\n", encoding="utf-8")
    state = {"calls": 0}

    class EndlessData(Exception):
        pass

    def endless_read(fd, size):
        state["calls"] += 1
        if state["calls"] > 4_096:
            raise EndlessData("reader consumed 4096 chunks past its byte budget")
        return b"x" * min(size, 65_536)

    monkeypatch.setattr(folder_overview.os, "read", endless_read)

    measurement = measure_workspace_files(str(tmp_path), "py", max_total_bytes=4_096)

    assert state["calls"] <= 64, f"the reader kept reading past the budget ({state['calls']} chunks)"
    assert measurement.complete is False
    assert "byte budget" in measurement.termination_reason, measurement.termination_reason
    reply = build_exact_file_facts(str(tmp_path), "py", max_total_bytes=4_096)
    assert reply is not None
    assert "counted, not estimated" not in reply.lower(), reply


def test_the_reader_stops_at_a_passed_deadline(tmp_path: Path):
    big = tmp_path / "big.py"
    big.write_text(("x" * 79 + "\n") * 1_300, encoding="utf-8")

    read = _count_file_lines(str(big), deadline=time.monotonic() - 1)

    assert read.outcome == "time-budget", read.outcome


def test_a_file_growing_while_read_cannot_outread_the_deadline(tmp_path: Path, monkeypatch):
    """With an endless read and a deadline already due, the reader must stop inside the read."""
    real_read = os.read
    (tmp_path / "endless.py").write_text("x = 1\n", encoding="utf-8")
    state = {"calls": 0}

    class EndlessData(Exception):
        pass

    def endless_read(fd, size):
        state["calls"] += 1
        if state["calls"] > 4_096:
            raise EndlessData("reader consumed 4096 chunks past its deadline")
        return b"x" * min(size, 65_536)

    monkeypatch.setattr(folder_overview.os, "read", endless_read)

    measurement = measure_workspace_files(str(tmp_path), "py", max_seconds=0.0)

    assert state["calls"] <= 64, f"the reader kept reading past its deadline ({state['calls']} chunks)"
    assert measurement.complete is False
    assert "time budget" in measurement.termination_reason, measurement.termination_reason


def test_a_growing_file_is_cut_off_at_the_per_file_ceiling_too(tmp_path: Path, monkeypatch):
    """The 64 MiB per-file ceiling must hold inside the chunk loop, not only at lstat time.

    The fake read NEVER reaches EOF, so a reader without an in-loop ceiling check would read
    forever — the trip counter bounds this test while proving the reader did not stop itself.
    """
    real_read = os.read
    (tmp_path / "endless.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(folder_overview, "_MEASUREMENT_MAX_FILE_BYTES", 1_024)
    state = {"calls": 0}

    class EndlessData(Exception):
        pass

    def endless_read(fd, size):
        state["calls"] += 1
        if state["calls"] > 4_096:
            raise EndlessData("reader consumed 4096 chunks past the per-file ceiling")
        return b"x" * min(size, 65_536)

    monkeypatch.setattr(folder_overview.os, "read", endless_read)

    measurement = measure_workspace_files(str(tmp_path), "py")

    assert state["calls"] <= 64, f"the reader never enforced the ceiling ({state['calls']} chunks)"
    assert "endless.py" not in (measurement.largest_relpath or ""), measurement
    assert measurement.skipped.get("too-large", 0) == 1, measurement.skipped


def test_the_per_file_ceiling_bounds_every_requested_and_accepted_byte(
    tmp_path: Path, monkeypatch
):
    """The ceiling is a BYTE bound, not a call-count bound.

    With the ceiling at 1,024, the reader must never REQUEST more than ceiling+1 bytes in one
    call and never ACCEPT more than ceiling+1 bytes in total — the +1 being the single probe
    byte that distinguishes "exactly at ceiling" from "over ceiling". A reader that clamps only
    against the remaining total budget requests a full megabyte first and accepts the whole read
    before noticing it is over. The file is small at lstat time (so the walk reaches the read at
    all) and yields beyond-ceiling bytes from the read itself — a file growing while read.
    """
    small = tmp_path / "small.py"
    small.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(folder_overview, "_MEASUREMENT_MAX_FILE_BYTES", 1_024)
    requested: list[int] = []
    returned: list[int] = []

    def instrumented_read(fd, size):
        requested.append(size)
        # Far more than the 1,024-byte ceiling: a file growing while read. Fabricated bytes, so
        # the read never EOFs inside the growth.
        chunk = b"x" * min(size, 2_048)
        returned.append(len(chunk))
        return chunk

    monkeypatch.setattr(folder_overview.os, "read", instrumented_read)

    read = _count_file_lines(str(small))

    assert read.outcome == "too-large", read.outcome
    assert max(requested) <= 1_024 + 1, (
        f"the reader requested {max(requested)} bytes against a 1,024-byte ceiling: {requested}"
    )
    assert read.bytes_read <= 1_024 + 1, (
        f"the reader accepted {read.bytes_read} bytes against a 1,024-byte ceiling"
    )
    assert sum(returned) <= 1_024 + 1, (
        f"the reader accepted {sum(returned)} bytes across chunks against a 1,024-byte ceiling"
    )


def test_a_file_exactly_at_the_per_file_ceiling_still_measures(tmp_path: Path, monkeypatch):
    """Exactly at ceiling + EOF is measurable; the +1 probe is what tells the difference."""
    exact = tmp_path / "exact.py"
    exact.write_text(("x" * 7 + "\n") * 128, encoding="utf-8")  # exactly 1,024 bytes
    monkeypatch.setattr(folder_overview, "_MEASUREMENT_MAX_FILE_BYTES", 1_024)
    requested: list[int] = []
    returned: list[int] = []
    real_read = os.read

    def instrumented_read(fd, size):
        requested.append(size)
        chunk = real_read(fd, size)
        returned.append(len(chunk))
        return chunk

    monkeypatch.setattr(folder_overview.os, "read", instrumented_read)

    read = _count_file_lines(str(exact))

    assert read.outcome == "measured", read.outcome
    assert read.lines == 128, read.lines
    assert read.bytes_read == 1_024, read.bytes_read
    assert max(requested) <= 1_024 + 1, requested
    assert sum(returned) <= 1_024 + 1, returned


# ------------------------------------------------------------- 3. post-read races are verdicts


def test_a_failed_post_read_stat_makes_the_result_incomplete(tmp_path: Path, monkeypatch):
    """Open and read succeed, the post-read lstat fails: that entry is unverifiable."""
    _write(tmp_path / "a.py", 5)
    _write(tmp_path / "b.py", 2)
    real_lstat = os.lstat
    seen: dict[str, int] = {}

    def lstat_fail_after_first(path, *args, **kwargs):
        key = str(path)
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            raise FileNotFoundError(key)
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(folder_overview.os, "lstat", lstat_fail_after_first)

    measurement = measure_workspace_files(str(tmp_path), "py")

    assert measurement.complete is False, "an unverifiable entry cannot ship as exact"
    assert measurement.termination_reason, measurement
    assert any("a.py" in entry or "b.py" in entry for entry in measurement.raced_entries), (
        measurement
    )
    reply = build_exact_file_facts(str(tmp_path), "py")
    assert reply is not None
    assert "counted, not estimated" not in reply.lower(), reply


def test_a_file_mutated_in_place_during_the_read_is_a_race(tmp_path: Path, monkeypatch):
    """The same inode being modified while its bytes are counted is not a measurable state.

    The fake touches the file (mtime changes, size does not) on EVERY read, so every walk over
    this workspace — including the second walk that renders the reply — reads a moving target and
    must be verdict-raced, never rendered as exact.
    """
    victim = tmp_path / "victim.py"
    _write(victim, 3)
    real_read = os.read

    def touch_during_read(fd, size):
        chunk = real_read(fd, size)
        victim.touch()
        return chunk

    monkeypatch.setattr(folder_overview.os, "read", touch_during_read)

    measurement = measure_workspace_files(str(tmp_path), "py")

    assert measurement.complete is False, "a file mutated mid-read cannot ship as exact"
    assert any("victim" in entry for entry in measurement.raced_entries), measurement
    reply = build_exact_file_facts(str(tmp_path), "py")
    assert "counted, not estimated" not in reply.lower(), reply


def test_an_entry_that_changes_identity_after_reading_is_reported_as_raced(
    tmp_path: Path, monkeypatch
):
    """dev/ino changed between the pre-open lstat and the post-read lstat: raced."""
    _write(tmp_path / "swapped.py", 4)
    real_lstat = os.lstat
    seen: dict[str, int] = {}

    def lstat_swap_after_first(path, *args, **kwargs):
        key = str(path)
        seen[key] = seen.get(key, 0) + 1
        before = real_lstat(path, *args, **kwargs)
        if seen[key] > 1:
            return SimpleNamespace(
                st_dev=before.st_dev,
                st_ino=before.st_ino + 1,
                st_size=before.st_size,
                st_mtime_ns=before.st_mtime_ns,
            )
        return before

    monkeypatch.setattr(folder_overview.os, "lstat", lstat_swap_after_first)

    measurement = measure_workspace_files(str(tmp_path), "py")

    assert measurement.complete is False
    assert any("swapped" in entry for entry in measurement.raced_entries), measurement
    assert "swapped" in measurement.termination_reason, measurement.termination_reason
    reply = build_exact_file_facts(str(tmp_path), "py")
    assert "counted, not estimated" not in reply.lower(), reply


# -------------------------------------------------------------------- byte accounting integrity


def test_byte_accounting_uses_bytes_actually_read(tmp_path: Path, monkeypatch):
    """The total-byte budget must fire from READ bytes even when no stat ever agrees with them.

    The real file on disk is a few bytes; the reader is fed endless chunks. If accounting came
    from a stat instead of from bytes_read, the budget could never fire and the fake would trip.
    """
    real_read = os.read
    (tmp_path / "endless.py").write_text("x = 1\n", encoding="utf-8")
    state = {"calls": 0, "bytes": 0}

    class EndlessData(Exception):
        pass

    def endless_read(fd, size):
        state["calls"] += 1
        state["bytes"] += size
        if state["calls"] > 4_096:
            raise EndlessData("reader consumed 4096 chunks past its byte budget")
        return b"x" * size

    monkeypatch.setattr(folder_overview.os, "read", endless_read)

    measurement = measure_workspace_files(str(tmp_path), "py", max_total_bytes=4_096)

    assert measurement.complete is False
    assert "byte budget" in measurement.termination_reason, measurement.termination_reason
