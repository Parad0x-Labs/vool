"""Detect whether a newer official VOOL release is available.

Read-only and side-effect-free by design (Increment 1 of the self-update feature):
this module decides *whether* an update exists and builds the changelog to show the
user. It never downloads, swaps, or restarts anything — that is the detached updater's
job (installer/self_update.py) and only after explicit consent.

Channel: tagged GitHub releases from the official repo. "Newer" is a strict version
bump (never a downgrade), and a release the user already declined is not re-offered
until a still-newer one appears. All network calls are best-effort and swallow errors,
and both the clock and the fetcher are injectable so the logic is unit-testable offline.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

DEFAULT_OWNER = "Parad0x-Labs"
DEFAULT_REPO = "vool-local"
CHECK_INTERVAL_SECONDS = 24 * 60 * 60  # once per 24h

_VERSION_RE = re.compile(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?")
_BULLET_PREFIX_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")


def parse_version(text: str) -> tuple[int, int, int]:
    """Best-effort semver-ish tuple from a tag/version string ("v0.5.0" -> (0, 5, 0))."""
    match = _VERSION_RE.search(str(text or ""))
    if not match:
        return (0, 0, 0)
    return (int(match.group(1) or 0), int(match.group(2) or 0), int(match.group(3) or 0))


def is_newer_version(candidate: str, installed: str) -> bool:
    """True only when candidate is a strictly higher version than installed."""
    return parse_version(candidate) > parse_version(installed)


def changelog_lines(body: str, limit: int = 8) -> list[str]:
    """Turn a release body into a short, clean bullet list of what changed."""
    lines: list[str] = []
    for raw in str(body or "").splitlines():
        clean = _BULLET_PREFIX_RE.sub("", raw).strip()
        if not clean:
            continue
        if clean.startswith("#"):  # skip markdown section headers
            continue
        lines.append(clean)
        if len(lines) >= max(1, limit):
            break
    return lines


@dataclass
class UpdateCheckState:
    """Durable check state (persisted in VOOL_HOME/data, so it survives updates)."""

    last_check_utc: float = 0.0
    last_offered_version: str = ""
    dismissed_version: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> UpdateCheckState:
        data = data or {}
        return cls(
            last_check_utc=float(data.get("last_check_utc") or 0.0),
            last_offered_version=str(data.get("last_offered_version") or ""),
            dismissed_version=str(data.get("dismissed_version") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_check_utc": self.last_check_utc,
            "last_offered_version": self.last_offered_version,
            "dismissed_version": self.dismissed_version,
        }


def should_check(state: UpdateCheckState, now: float, interval_seconds: int = CHECK_INTERVAL_SECONDS) -> bool:
    """True if at least `interval_seconds` have passed since the last network check."""
    return (now - float(state.last_check_utc)) >= interval_seconds


@dataclass
class UpdateAvailability:
    installed_version: str
    available: bool = False
    target_version: str = ""
    changelog: list[str] = field(default_factory=list)
    asset_url: str = ""
    sha256_url: str = ""
    release_url: str = ""
    dismissed: bool = False
    reason: str = ""


def _select_windows_assets(assets: list[dict[str, Any]]) -> tuple[str, str]:
    """Return (package_url, sha256_url) for the Windows package + its checksum sidecar."""
    package_url = ""
    sha256_url = ""
    for asset in assets or []:
        name = str(asset.get("name") or "").lower()
        url = str(asset.get("browser_download_url") or "")
        if not url:
            continue
        if name.endswith(".sha256"):
            sha256_url = url
        elif name.endswith(".zip") and ("windows" in name or "vool" in name):
            package_url = url
    return package_url, sha256_url


def evaluate_release(
    installed_version: str,
    release: dict[str, Any] | None,
    dismissed_version: str,
) -> UpdateAvailability:
    """Decide if `release` is an eligible, strictly-newer, not-yet-declined update."""
    result = UpdateAvailability(installed_version=str(installed_version or ""))
    if not release or release.get("draft") or release.get("prerelease"):
        result.reason = "no eligible published release"
        return result
    tag = str(release.get("tag_name") or "").strip()
    if not tag:
        result.reason = "release has no tag"
        return result
    if not is_newer_version(tag, installed_version):
        result.reason = "already up to date"
        return result
    if tag == str(dismissed_version or ""):
        result.dismissed = True
        result.reason = "user already declined this version"
        return result

    package_url, sha256_url = _select_windows_assets(list(release.get("assets") or []))
    result.available = True
    result.target_version = tag
    result.changelog = changelog_lines(release.get("body") or release.get("name") or "")
    result.asset_url = package_url
    result.sha256_url = sha256_url
    result.release_url = str(release.get("html_url") or "")
    result.reason = "update available"
    return result


def fetch_latest_release(
    owner: str = DEFAULT_OWNER,
    repo: str = DEFAULT_REPO,
    timeout: float = 6.0,
) -> dict[str, Any] | None:
    """GET /releases/latest from the official repo. Best-effort: returns None on any error."""
    # The OWNING ENTRY POINT of this background service's network work (R2b1,
    # amended): the door grants nothing, so the check authorizes itself here,
    # by name. Scope-local account — handed to no one at close.
    from core.effect_gateway import named_background_effect_scope

    with named_background_effect_scope("self_update.check"):
        return _fetch_latest_release_owned(
            owner=owner,
            repo=repo,
            timeout=timeout,
        )


def _fetch_latest_release_owned(
    owner: str = DEFAULT_OWNER,
    repo: str = DEFAULT_REPO,
    timeout: float = 6.0,
) -> dict[str, Any] | None:
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    try:
        import urllib.request

        from core.remote_fetch_policy import open_remote

        req = urllib.request.Request(
            url,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "vool-self-update"},
        )
        # Fixed official https URL to the GitHub API; not user-controlled. The
        # ONE outbound door: a vetoed turn refuses before any socket and reads,
        # like any other fetch failure, as "no release information available".
        with open_remote(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def check_for_update(
    *,
    installed_version: str,
    state: UpdateCheckState,
    now: float,
    release_fetcher: Callable[[], dict[str, Any] | None] = fetch_latest_release,
) -> tuple[UpdateAvailability, UpdateCheckState]:
    """Run the 24h-gated availability check; returns (availability, mutated state).

    The caller persists the returned state. Network errors degrade to "not available"
    without raising, so a check failure never disrupts a chat turn.
    """
    if not should_check(state, now):
        return (
            UpdateAvailability(installed_version=installed_version, reason="checked within the last 24h"),
            state,
        )
    state.last_check_utc = now
    release = release_fetcher()
    if not release:
        return (
            UpdateAvailability(installed_version=installed_version, reason="no release information available"),
            state,
        )
    availability = evaluate_release(installed_version, release, state.dismissed_version)
    if availability.available:
        state.last_offered_version = availability.target_version
    return availability, state
