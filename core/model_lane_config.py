from __future__ import annotations

import json
import os
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from core.runtime_paths import active_data_dir

LogicalModelLane = Literal["LOCAL_FAST", "LOCAL_DAILY", "LOCAL_HEAVY"]

LOCAL_FAST: LogicalModelLane = "LOCAL_FAST"
LOCAL_DAILY: LogicalModelLane = "LOCAL_DAILY"
LOCAL_HEAVY: LogicalModelLane = "LOCAL_HEAVY"
LOGICAL_MODEL_LANES: tuple[LogicalModelLane, ...] = (LOCAL_FAST, LOCAL_DAILY, LOCAL_HEAVY)

_ENV_BY_LANE: dict[LogicalModelLane, str] = {
    LOCAL_FAST: "VOOL_LOCAL_FAST_MODEL",
    LOCAL_DAILY: "VOOL_LOCAL_DAILY_MODEL",
    LOCAL_HEAVY: "VOOL_LOCAL_HEAVY_MODEL",
}
_LEGACY_TO_LOGICAL = {
    "tiny": LOCAL_FAST,
    "fast": LOCAL_FAST,
    "local_fast": LOCAL_FAST,
    "daily": LOCAL_DAILY,
    "local_daily": LOCAL_DAILY,
    "deep": LOCAL_HEAVY,
    "heavy": LOCAL_HEAVY,
    "local_heavy": LOCAL_HEAVY,
}
_LOCK = threading.RLock()
_FILENAME = "model_lanes.json"


@dataclass(frozen=True)
class ModelLaneConfig:
    assignments: dict[LogicalModelLane, str]

    def model_for(self, lane: LogicalModelLane) -> str:
        return str(self.assignments.get(lane) or "").strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "vool.model_lanes.v1",
            "assignments": {lane: self.model_for(lane) for lane in LOGICAL_MODEL_LANES},
        }


def logical_lane(value: str) -> LogicalModelLane:
    clean = str(value or "").strip()
    if clean in LOGICAL_MODEL_LANES:
        return clean  # type: ignore[return-value]
    return _LEGACY_TO_LOGICAL.get(clean.lower(), LOCAL_DAILY)


def lane_config_path() -> Path:
    return (active_data_dir() / _FILENAME).resolve()


def load_model_lane_config(*, env: Mapping[str, str] | None = None) -> ModelLaneConfig:
    env_map = os.environ if env is None else env
    raw: dict[str, Any] = {}
    path = lane_config_path()
    if path.exists():
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            raw = parsed if isinstance(parsed, dict) else {}
        except Exception:
            raw = {}
    stored = raw.get("assignments") if isinstance(raw.get("assignments"), dict) else {}
    assignments: dict[LogicalModelLane, str] = {}
    for lane in LOGICAL_MODEL_LANES:
        env_value = str(env_map.get(_ENV_BY_LANE[lane]) or "").strip()
        assignments[lane] = env_value or str(stored.get(lane) or "").strip()
    return ModelLaneConfig(assignments=assignments)


def save_model_lane_config(config: ModelLaneConfig) -> ModelLaneConfig:
    normalized = ModelLaneConfig(
        assignments={lane: str(config.assignments.get(lane) or "").strip() for lane in LOGICAL_MODEL_LANES}
    )
    path = lane_config_path()
    temp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp.write_text(json.dumps(normalized.to_dict(), sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        os.replace(temp, path)
    return normalized


def assign_model_to_lane(lane: str, model_id: str) -> ModelLaneConfig:
    resolved_lane = logical_lane(lane)
    current = load_model_lane_config()
    assignments = dict(current.assignments)
    assignments[resolved_lane] = str(model_id or "").strip()
    return save_model_lane_config(ModelLaneConfig(assignments=assignments))


__all__ = [
    "LOCAL_DAILY",
    "LOCAL_FAST",
    "LOCAL_HEAVY",
    "LOGICAL_MODEL_LANES",
    "LogicalModelLane",
    "ModelLaneConfig",
    "assign_model_to_lane",
    "lane_config_path",
    "load_model_lane_config",
    "logical_lane",
    "save_model_lane_config",
]
