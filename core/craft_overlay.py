"""Optional per-machine craft overlay.

A local, git-ignored ``data/craft_overlay.json`` can override the shipped writing-craft and
visual-playbook directives on that machine - e.g. after VOOL re-authors her craft with a stronger
local model on that box (see core.craft_upgrade). No file => shipped defaults, unchanged: the repo
ships the baseline, each machine may upgrade locally.

Shape:
    {
      "writing_craft":    {genre_key: {"craft_directive": "...", "register": "...", ...}},
      "visual_playbooks": {genre_key: {"camera_language": "...", "negative_prompt": "...", ...}}
    }
Only known string fields are honored (see the *_OVERLAY_FIELDS sets in the consuming modules).
"""

from __future__ import annotations

import json
from functools import lru_cache

from core.runtime_paths import data_path

OVERLAY_FILE = "craft_overlay.json"


@lru_cache(maxsize=1)
def _load() -> dict:
    try:
        path = data_path(OVERLAY_FILE)
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def overlay_for(section: str, key: str) -> dict:
    """Override fields for one genre in a section, or {} when there is no overlay for it."""
    if not key:
        return {}
    section_map = _load().get(section)
    if not isinstance(section_map, dict):
        return {}
    entry = section_map.get(key)
    return {str(k): v for k, v in entry.items()} if isinstance(entry, dict) else {}


def overlay_present() -> bool:
    return bool(_load())


def reload_overlay() -> None:
    """Drop the cached overlay after the file is (re)written (see core.craft_upgrade)."""
    _load.cache_clear()
