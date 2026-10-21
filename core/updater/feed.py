"""Feed configuration: WHERE signed manifests come from and WHAT gets updated.

Configuration format (`config/release/update_feed.json`):

    {
      "manifest_url": "",          # empty = no release feed configured (honest disable)
      "app_path": "",              # empty = installs refused (no target configured)
      "channel": "stable",
      "health_url": "http://127.0.0.1:11435/healthz",
      "helper_script": "",         # empty = in-process helper; else the external script
       "auto_check": false,         # opt in explicitly, or press Check for updates
      "check_interval_seconds": 86400
    }

Environment overrides (release staging + sandbox journeys only):
    VOOL_UPDATE_MANIFEST_URL, VOOL_UPDATE_APP_PATH, VOOL_UPDATE_CHANNEL,
    VOOL_UPDATE_HEALTH_URL, VOOL_UPDATE_HELPER_SCRIPT, VOOL_UPDATE_DISABLED=1,
    VOOL_UPDATE_TRUSTED_KEYS_JSON (path to a pin file for tests/sandboxes).

Empty manifest_url or an unpinned trust set is a FIRST-CLASS state, not an error: the
subsystem boots, reports `unavailable` with the plain reason, and runs no background
thread. The UI shows "Updates are unavailable" — never silence.
With a configured feed, a fresh home waits for an explicit Check action or an
operator's auto_check setting/feed override before contacting it. Above all of these sits the
outbound switch ("Go online on its own", network.outbound_enabled): while it is off no check goes
online, whatever the feed, the setting or an earlier Check press says.
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
    allow_publisher_verified_adhoc: bool = False
    auto_check: bool = False

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
        allow_publisher_verified_adhoc=data.get("allow_publisher_verified_adhoc") is True,
        auto_check=data.get("auto_check") is True,
    )


ONLINE_ACCESS_OFF = "online_access_off"
ONLINE_ACCESS_OFF_DETAIL = (
    "Online access is off, so VOOL did not check for updates. "
    "Turn on \u201cGo online on its own\u201d in Settings, Privacy & Permissions."
)

ONLINE_ACCESS_OFF_INSTALL_DETAIL = (
    "Online access is off, so VOOL did not download the update. "
    "Turn on \u201cGo online on its own\u201d in Settings, Privacy & Permissions."
)


def online_access_allowed(feed: FeedConfig | None = None) -> bool:
    """The master switch (network.outbound_enabled, vetoed by local-only mode) for every update check.

    Off, no update check goes online: not the background schedule, not a check the user pressed. The answer
    comes from the one policy gate the radar, presence and model downloads use; an unreadable setting is off.
    A feed whose address names this computer literally (a sandbox release server on loopback) is not online.
    """
    if feed is not None:
        from core.provider_http import is_loopback_url

        if is_loopback_url(feed.manifest_url):
            return True
    try:
        from core import policy_engine

        return policy_engine.automatic_outbound_enabled() is True
    except Exception:
        return False


def automatic_checks_enabled(*, feed: FeedConfig | None = None, data_dir: Path | None = None) -> bool:
    """The shipped feed alone never grants permission to contact it, and nothing does while the
    outbound switch is off."""
    config = feed or load_feed_config()
    if not online_access_allowed(config):
        return False
    if not config.has_feed or config.source == "disabled-by-env":
        return False
    # An operator-supplied feed override or auto_check setting is an explicit choice.
    if config.auto_check or config.source == "env":
        return True
    if data_dir is None:
        from core.runtime_paths import active_data_dir

        data_dir = active_data_dir()
    from core.updater.state import _read_json

    choice = _read_json(Path(data_dir) / "update_v2" / "check_consent.json")
    return (isinstance(choice, dict) and choice.get("enabled") is True
            and choice.get("manifest_url") == config.manifest_url)


def enable_automatic_checks(*, feed: FeedConfig, data_dir: Path) -> None:
    """Persist the explicit Check action for this home and this exact feed."""
    from core.updater.state import _atomic_write_json

    _atomic_write_json(Path(data_dir) / "update_v2" / "check_consent.json",
                       {"enabled": True, "manifest_url": feed.manifest_url})


__all__ = [
    "ONLINE_ACCESS_OFF",
    "ONLINE_ACCESS_OFF_DETAIL",
    "ONLINE_ACCESS_OFF_INSTALL_DETAIL",
    "FeedConfig",
    "automatic_checks_enabled",
    "enable_automatic_checks",
    "load_feed_config",
    "online_access_allowed",
]
