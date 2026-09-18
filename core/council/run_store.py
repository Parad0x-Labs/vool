"""Council run persistence: the state file the UI polls and the evidence ledger.

Every important council decision leaves evidence — not "reviewer #3 thought this looked
safe" but the exact run, round, seat, model, report hash, verdict, counterexample, and
authority transition, in an append-only JSONL next to a small polled state JSON.

Two files per run under ``data/council/``:
  * ``<run_id>.json``         — current run state (atomic temp+rename writes);
  * ``<run_id>.events.jsonl`` — append-only evidence ledger, never rewritten.

The state file is a VIEW; the ledger is the record. A missing or corrupt state file
reads as "no such run" — it is never reconstructed by guessing, and the ledger stays
on disk for the post-mortem either way.

Every appended row carries a gapless monotonic ``seq`` starting at 1, allocated HERE so
a caller cannot mint its own and collide. Allocation reads the ledger's current high
water mark on first use per run and is serialized by a per-run lock, so two
``CouncilRunStore`` instances over the same run — the orchestrator writing and the API
reading — cannot restart the sequence or interleave two rows onto one number.

Ledgers written before ``seq`` existed keep their bytes: nothing is backfilled and
nothing is rewritten. They are READ in documented file order (line 1 is ordinal 1), and
:meth:`CouncilRunStore.read_events_ordered` labels every row's ordinal as ``stored`` or
``file_order`` so a reader is never left guessing which it got. Because allocation
starts above the legacy line count, a run that spans the upgrade has one continuous
ordering with no collision at the seam.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from core.runtime_paths import data_path

_DIR_NAME = "council"

#: One lock per run id, guarding read-high-water-mark + append as a single step. Held
#: only across the append itself; `_LOCK_REGISTRY_LOCK` guards handing the locks out.
_SEQ_LOCKS: dict[str, threading.Lock] = {}
_LOCK_REGISTRY_LOCK = threading.Lock()


def _seq_lock(run_id: str) -> threading.Lock:
    with _LOCK_REGISTRY_LOCK:
        lock = _SEQ_LOCKS.get(run_id)
        if lock is None:
            lock = threading.Lock()
            _SEQ_LOCKS[run_id] = lock
        return lock


def _runs_dir() -> Path:
    out = Path(data_path(_DIR_NAME))
    out.mkdir(parents=True, exist_ok=True)
    return out


def _safe_run_id(run_id: str) -> str:
    cleaned = "".join(ch for ch in str(run_id or "") if ch.isalnum() or ch in "-_")
    if not cleaned:
        raise ValueError("council run id must be non-empty and filesystem-safe")
    return cleaned[:80]


class CouncilRunStore:
    """Per-run state + ledger IO. One instance per run id; safe to re-instantiate."""

    def __init__(self, run_id: str) -> None:
        self.run_id = _safe_run_id(run_id)

    # ------------------------------------------------------------------ state
    def _state_path(self) -> Path:
        return _runs_dir() / f"{self.run_id}.json"

    def write_state(self, state: dict[str, Any]) -> None:
        payload = dict(state)
        payload["run_id"] = self.run_id
        payload["updated_at"] = time.time()
        target = self._state_path()
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".council-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=1)
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                with __import__("contextlib").suppress(OSError):
                    os.unlink(tmp)

    def read_state(self) -> dict[str, Any] | None:
        try:
            raw = self._state_path().read_text(encoding="utf-8")
            loaded = json.loads(raw)
            return loaded if isinstance(loaded, dict) else None
        except (OSError, ValueError):
            return None

    # ----------------------------------------------------------------- ledger
    def _ledger_path(self) -> Path:
        return _runs_dir() / f"{self.run_id}.events.jsonl"

    def _high_water_mark(self) -> int:
        """The greatest ordinal the ledger already holds: the highest stored ``seq``, or
        the line count when the file predates sequencing. Never fabricated INTO the file
        — this is a read used to pick the NEXT number."""
        try:
            lines = self._ledger_path().read_text(encoding="utf-8").splitlines()
        except OSError:
            return 0
        highest = 0
        ordinal = 0
        for line in lines:
            if not line.strip():
                continue
            ordinal += 1
            try:
                loaded = json.loads(line)
            except ValueError:
                continue  # a torn tail line still occupies its position in file order
            stored = loaded.get("seq") if isinstance(loaded, dict) else None
            if isinstance(stored, int) and not isinstance(stored, bool):
                highest = max(highest, stored)
        return max(highest, ordinal)

    def append_event(self, event_type: str, **fields: Any) -> dict[str, Any]:
        row: dict[str, Any] = {
            "ts": time.time(),
            "run_id": self.run_id,
            "type": str(event_type),
        }
        row.update(fields)
        # `seq` is allocated last and is not overridable by a caller field: the ledger's
        # order is the store's to state, not its writers'.
        with _seq_lock(self.run_id):
            row["seq"] = self._high_water_mark() + 1
            line = json.dumps(row, ensure_ascii=False, default=str)
            with open(self._ledger_path(), "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        return row

    def read_events(self) -> list[dict[str, Any]]:
        try:
            lines = self._ledger_path().read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: list[dict[str, Any]] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                loaded = json.loads(line)
            except ValueError:
                continue  # a torn tail line is skipped, never invented
            if isinstance(loaded, dict):
                out.append(loaded)
        return out

    def read_events_ordered(self, *, after: int = 0) -> list[dict[str, Any]]:
        """Every event in ledger order, each carrying an ordinal and where it came from.

        ``seq_source`` is ``"stored"`` when the row was written with its own sequence and
        ``"file_order"`` when the ordinal was derived from the row's position in a ledger
        written before sequencing existed. The derived ordinal is a VIEW: the file is not
        touched, so a legacy ledger stays byte-identical after any number of reads.

        ``after`` filters to ordinals strictly greater than the cursor. A cursor past the
        end returns nothing — an empty page, never a reset to the start.
        """
        cursor = max(0, int(after or 0))
        out: list[dict[str, Any]] = []
        for position, row in enumerate(self.read_events(), start=1):
            stored = row.get("seq")
            if isinstance(stored, int) and not isinstance(stored, bool):
                seq, source = stored, "stored"
            else:
                seq, source = position, "file_order"
            if seq <= cursor:
                continue
            out.append({**row, "seq": seq, "seq_source": source})
        out.sort(key=lambda row: row["seq"])
        return out

    def read_event(self, seq: int) -> dict[str, Any] | None:
        """The ONE event at ``seq``, or None. Backs `text_ref` resolution.

        Ambiguity is refused rather than resolved by preference: if two rows somehow
        claim the same ordinal, this returns None instead of picking one, because a
        reference that silently resolves to the wrong report is worse than one that
        plainly does not resolve.
        """
        matches = [row for row in self.read_events_ordered() if row["seq"] == int(seq)]
        return matches[0] if len(matches) == 1 else None

    def count_events_after(self, after: int) -> int:
        """How many events remain past ``after``. The exact number, so a pager can say
        whether more exist instead of inferring it from a full page."""
        return len(self.read_events_ordered(after=after))


def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    """Newest-first run states for the runs listing surface."""
    rows: list[dict[str, Any]] = []
    try:
        paths = [p for p in _runs_dir().glob("*.json")]
    except OSError:
        return []
    for path in paths:
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(loaded, dict):
            rows.append(loaded)
    rows.sort(key=lambda row: float(row.get("updated_at") or 0.0), reverse=True)
    return rows[: max(1, int(limit))]
