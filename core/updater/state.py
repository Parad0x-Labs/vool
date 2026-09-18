"""Durable updater state under user data — never inside the replaced app bundle.

Layout (`<data>/update_v2/`):

    staging/        resumable partial downloads + verified staged artifacts
    transactions/   one directory per install attempt: journal.jsonl + receipts
    receipts/       success / failure / rollback receipts (also copied per-tx)
    snapshots/      pre-migration config/state snapshots used by rollback
    status.json     the user-visible status surface ("Update ready", progress…)
    high_water.json per-channel anti-replay marks (durable half of the replay defense)

All writes are tmp+rename atomic; all reads fail safe (corrupt ⇒ empty), because this
state must never crash the app that owns it.
"""
from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path
from typing import Any


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # The tmp name must be unique per write: the boot check thread and a press can
    # publish status CONCURRENTLY, and a shared ".tmp" name made one writer's
    # os.replace consume the other's file (FileNotFoundError, measured in test).
    tmp = path.with_suffix(f"{path.suffix}.{os.getpid()}.{time.time_ns()}.tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class UpdaterPaths:
    """All updater-owned paths for one data dir. Creating it twice is idempotent."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.staging = self.root / "staging"
        self.transactions = self.root / "transactions"
        self.receipts = self.root / "receipts"
        self.snapshots = self.root / "snapshots"
        self.status_file = self.root / "status.json"
        self.high_water_file = self.root / "high_water.json"
        for needed in (self.root, self.staging, self.transactions, self.receipts, self.snapshots):
            needed.mkdir(parents=True, exist_ok=True)

    @classmethod
    def for_data_dir(cls, data_dir: Path | str) -> UpdaterPaths:
        return cls(Path(data_dir) / "update_v2")

    def transaction_dir(self, txid: str) -> Path:
        tx_dir = self.transactions / str(txid)
        tx_dir.mkdir(parents=True, exist_ok=True)
        return tx_dir


class HighWaterStore:
    """Per-channel monotonic marks: `{channel: {sequence, version, manifest_sha256,
    recorded_at}}`. The decision layer compares manifest sequence against this; a mark
    only ever moves FORWARD (recording an older sequence is ignored), so tampering with
    the file to rewind replay protection fails closed by construction."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def _load_all(self) -> dict[str, Any]:
        data = _read_json(self.path)
        return data if isinstance(data, dict) else {}

    def load(self, channel: str) -> dict[str, Any] | None:
        entry = self._load_all().get(str(channel))
        return dict(entry) if isinstance(entry, dict) else None

    def record(self, channel: str, *, sequence: int, version: str, manifest_sha256: str, now: float) -> None:
        current = self.load(channel)
        if current is not None and int(current.get("sequence") or 0) >= int(sequence):
            return  # high-water never regresses
        data = self._load_all()
        data[str(channel)] = {
            "sequence": int(sequence),
            "version": str(version),
            "manifest_sha256": str(manifest_sha256),
            "recorded_at": float(now),
        }
        with contextlib.suppress(OSError):
            _atomic_write_json(self.path, data)


__all__ = ["HighWaterStore", "UpdaterPaths"]
