from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json_config(path: str | Path) -> tuple[Path, dict[str, Any]]:
    config_path = Path(path).expanduser().resolve()
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Config must be a JSON object: {config_path}")
    return config_path, raw


def resolve_optional_config_path(base_dir: str | Path, raw_value: object) -> str | None:
    value = str(raw_value or "").strip()
    if not value:
        return None
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = Path(base_dir).expanduser().resolve() / candidate
    return str(candidate.resolve())
