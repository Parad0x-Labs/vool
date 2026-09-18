"""Feed configuration: WHERE signed manifests come from and WHAT gets updated.

Shipped default `config/release/update_feed.json`:

    {
      "manifest_url": "",          # empty = no release feed configured (honest disable)
      "app_path": "",              # empty = installs refused (no target configured)
      "channel": "stable",
      "health_url": "http://127.0.0.1:11435/healthz",
      "helper_script": "",         # empty = in-process helper; else the external script
      "check_interval_seconds": 86400
    }

Environment overrides (release staging + sandbox journeys only):
    VOOL_UPDATE_MANIFEST_URL, VOOL_UPDATE_APP_PATH, VOOL_UPDATE_CHANNEL,
    VOOL_UPDATE_HEALTH_URL, VOOL_UPDATE_HELPER_SCRIPT, VOOL_UPDATE_DISABLED=1,
    VOOL_UPDATE_TRUSTED_KEYS_JSON (path to a pin file for tests/sandboxes).

Empty manifest_url or an unpinned trust set is a FIRST-CLASS state, not an error: the
subsystem boots, reports `unavailable` with the plain reason, and runs no background
thread. The UI shows "Updates are unavailable" — never silence.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("config/release/update_feed.json")
DEFAULT_HEALTH_URL = "http://127.0.0.1:11435/healthz"
DEFAULT_CHECK_INTERVAL = 24 * 60 * 60


@dataclass(frozen=True)
class FeedConfig:
    manifest_url: str = ""
    app_path: str = ""
    channel: str = "stable"
    health_url: str = DEFAULT_HEALTH_URL
    helper_script: str = ""
    check_interval_seconds: int = DEFAULT_CHECK_INTERVAL
    source: str = "defaults"

    @property
    def has_feed(self) -> bool:
        return bool(self.manifest_url.strip())

    @property
    def has_target(self) -> bool:
        return bool(self.app_path.strip())


def load_feed_config(
    *,
    config_path: Path | str | None = None,
    env: dict[str, str] | None = None,
) -> FeedConfig:
    """Defaults < shipped config file < environment. Never raises."""
    source = os.environ if env is None else dict(env)
    if str(source.get("VOOL_UPDATE_DISABLED") or "").strip() in ("1", "true", "yes"):
        return FeedConfig(source="disabled-by-env")

    data: dict = {}
    path = Path(config_path) if config_path is not None else DEFAULT_CONFIG_PATH
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    except (OSError, ValueError):
        data = {}

    def pick(key: str, env_name: str, default: str) -> str:
        override = str(source.get(env_name) or "").strip()
        if override:
            return override
        return str(data.get(key) or default).strip()

    interval_raw = data.get("check_interval_seconds")
    try:
        interval = int(interval_raw) if interval_raw else DEFAULT_CHECK_INTERVAL
    except (TypeError, ValueError):
        interval = DEFAULT_CHECK_INTERVAL

    came_from_env = bool(
        str(source.get("VOOL_UPDATE_MANIFEST_URL") or "").strip()
    )
    return FeedConfig(
        manifest_url=pick("manifest_url", "VOOL_UPDATE_MANIFEST_URL", ""),
        app_path=pick("app_path", "VOOL_UPDATE_APP_PATH", ""),
        channel=pick("channel", "VOOL_UPDATE_CHANNEL", "stable") or "stable",
        health_url=pick("health_url", "VOOL_UPDATE_HEALTH_URL", DEFAULT_HEALTH_URL),
        helper_script=pick("helper_script", "VOOL_UPDATE_HELPER_SCRIPT", ""),
        check_interval_seconds=max(60, interval),
        source="env" if came_from_env else ("config" if data else "defaults"),
    )


__all__ = ["FeedConfig", "load_feed_config"]
