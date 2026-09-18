"""Which UsePod lane the owner chose: the wire protocol spoken and how calls are paid.

Kept apart from the routing policy on purpose. Changing the dialect (OpenAI chat vs Anthropic
Messages) or the payment transport (prepaid token vs accountless x402) does not change what a model
costs or which routes may serve it, so it must not invalidate an approved price bound -- and a change
to the price bound must not silently flip the dialect.
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.usepod.descriptor import TransportMode, WireProtocol

_SCHEMA = "vool.usepod.lane.v1"


@dataclass(frozen=True)
class LanePreference:
    protocol: str = WireProtocol.OPENAI.value
    transport_mode: str = TransportMode.PREPAID.value

    def validated(self) -> LanePreference:
        return LanePreference(
            protocol=WireProtocol(str(self.protocol)).value,
            transport_mode=TransportMode(str(self.transport_mode)).value,
        )

    def to_dict(self) -> dict[str, Any]:
        return {"protocol": self.protocol, "transport_mode": self.transport_mode}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> LanePreference:
        payload = dict(data or {})
        unknown = sorted(set(payload) - {"protocol", "transport_mode"})
        if unknown:
            raise ValueError("unknown lane fields: " + ",".join(unknown))
        return cls(
            protocol=str(payload.get("protocol") or WireProtocol.OPENAI.value),
            transport_mode=str(payload.get("transport_mode") or TransportMode.PREPAID.value),
        ).validated()


def _path() -> Path:
    from core.runtime_paths import active_data_dir

    return (active_data_dir() / "usepod" / "lane.json").resolve()


def load_lane_preference() -> tuple[LanePreference, str]:
    """The stored preference and an error code ("" when clean). Unreadable -> defaults, said so."""
    path = _path()
    if not path.exists():
        return LanePreference(), ""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("schema") != _SCHEMA:
            raise ValueError("schema")
        return LanePreference.from_dict(dict(record.get("lane") or {})), ""
    except Exception:
        return LanePreference(), "lane_store_unreadable"


def save_lane_preference(preference: LanePreference) -> LanePreference:
    clean = preference.validated()
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"schema": _SCHEMA, "lane": clean.to_dict()}, handle, sort_keys=True)
        os.replace(temp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp)
    return clean


__all__ = ["LanePreference", "load_lane_preference", "save_lane_preference"]
