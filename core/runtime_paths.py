from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Mapping
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VOOL_HOME = (PROJECT_ROOT / ".vool_local").resolve()
# NULLA -> VOOL compatibility: a pre-rename checkout may already hold the project-local
# runtime ".nulla_local". It is reused as-is (never a second profile); fresh checkouts
# use the canonical ".vool_local". See docs/VOOL_IDENTITY_COMPATIBILITY_MAP.md.
if not VOOL_HOME.exists() and (PROJECT_ROOT / ".nulla_local").resolve().exists():
    VOOL_HOME = (PROJECT_ROOT / ".nulla_local").resolve()
DATA_DIR = (VOOL_HOME / "data").resolve()
CONFIG_HOME_DIR = (VOOL_HOME / "config").resolve()
DOCS_DIR = (PROJECT_ROOT / "docs").resolve()
PROJECT_CONFIG_DIR = (PROJECT_ROOT / "config").resolve()
WORKSPACE_DIR = (PROJECT_ROOT / "workspace").resolve()
_VOOL_HOME_OVERRIDE: Path | None = None
_RUNTIME_HOME_GENERATION = 0


def runtime_home_generation() -> int:
    """Monotonic count of in-process runtime-home authority changes.

    Hot caches that must never outlive a home switch (the A8 digest-key cache)
    compare this number instead of re-resolving the home's filesystem path on
    every call: ``configure_runtime_home`` is the one owner that can move the
    active home underneath a running process, so its count IS the home's
    identity between resolutions.
    """
    return _RUNTIME_HOME_GENERATION


def vool_env(name: str, default: str | None = None) -> str | None:
    """Read the canonical VOOL_* variable first, then the legacy NULLA_* spelling.

    The canonical name wins when both are set; the legacy spelling is honored unchanged
    so existing shells, launch agents and installer scripts keep working.
    """
    canonical = os.environ.get(f"VOOL_{name}")
    if canonical is not None and canonical.strip():
        return canonical
    legacy = os.environ.get(f"NULLA_{name}")
    if legacy is not None and legacy.strip():
        return legacy
    return default


def user_runtime_default() -> Path:
    """Default per-user runtime home: ``~/.vool_runtime``, or the pre-rename
    ``~/.nulla_runtime`` when only that directory exists (reuse, never a second profile)."""
    canonical = Path.home() / ".vool_runtime"
    legacy = Path.home() / ".nulla_runtime"
    if not canonical.exists() and legacy.exists():
        return legacy
    return canonical


def configure_runtime_home(path: str | Path | None) -> None:
    global _VOOL_HOME_OVERRIDE, _RUNTIME_HOME_GENERATION
    _RUNTIME_HOME_GENERATION += 1
    _VOOL_HOME_OVERRIDE = None if path is None else Path(path).expanduser().resolve()


def discover_installed_runtime_home(
    *,
    project_root: str | Path | None = None,
) -> Path | None:
    root = (Path(project_root).expanduser().resolve() if project_root is not None else PROJECT_ROOT.resolve())
    receipt_path = root / "install_receipt.json"
    try:
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    candidate = str(payload.get("runtime_home") or "").strip()
    if not candidate:
        return None
    try:
        return Path(candidate).expanduser().resolve()
    except Exception:
        return None


def active_vool_home(env: Mapping[str, str] | None = None) -> Path:
    if env is None:
        if _VOOL_HOME_OVERRIDE is not None:
            return _VOOL_HOME_OVERRIDE.resolve()
        env_map = os.environ
        env_home = str(env_map.get("VOOL_HOME") or env_map.get("NULLA_HOME") or "").strip()
        if env_home:
            return Path(env_home).expanduser().resolve()
        installed_home = discover_installed_runtime_home()
        if installed_home is not None:
            return installed_home
        return VOOL_HOME.resolve()

    env_map = env
    explicit_env_home = "VOOL_HOME" in env_map or "NULLA_HOME" in env_map
    env_home = str(env_map.get("VOOL_HOME") or env_map.get("NULLA_HOME") or "").strip()
    if env_home:
        return Path(env_home).expanduser().resolve()
    if _VOOL_HOME_OVERRIDE is not None and not explicit_env_home:
        return _VOOL_HOME_OVERRIDE.resolve()
    installed_home = discover_installed_runtime_home()
    if installed_home is not None:
        return installed_home
    return VOOL_HOME.resolve()


def active_data_dir() -> Path:
    return (active_vool_home() / "data").resolve()


def active_config_home_dir() -> Path:
    return (active_vool_home() / "config").resolve()


def active_workspace_dir() -> Path:
    override = str(vool_env("WORKSPACE_ROOT") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    # Writable-state law (2026-09-02): a packaged/bundle runtime identifies its SOURCE root via
    # VOOL_PROJECT_ROOT (the app launchers export it). Runtime-generated state must never
    # materialize inside that tree — the workspace resolves beneath the active vool home like
    # every other writable state, so a read-only .app bundle stays byte-identical while running.
    if str(vool_env("PROJECT_ROOT") or "").strip():
        return (active_vool_home() / "workspace").resolve()
    return WORKSPACE_DIR.resolve()


def resolve_workspace_root(explicit: str | Path | None = None) -> Path:
    candidate = str(explicit or "").strip()
    if candidate:
        return Path(candidate).expanduser().resolve()
    override = str(vool_env("WORKSPACE_ROOT") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    # THE SAME WRITABLE-STATE LAW active_workspace_dir() STATES ABOVE. This used to fall back
    # to VOOL_PROJECT_ROOT, which is exactly the packaged case -- so the bundle became its own
    # workspace root and the runtime wrote control state INSIDE the .app. Measured on a real
    # build: driving the packaged app for one chat turn left 67 files under
    # Contents/Resources/app/workspace/control (approvals, budgets, deadletters, leases,
    # metrics) that the pristine bundle in the .dmg does not contain.
    #
    # That breaks the very property the law two functions up promises -- "a read-only .app
    # bundle stays byte-identical while running" -- and it breaks three real things: a signed
    # bundle's seal, state survival across an app replacement, and any install where the .app
    # is not user-writable. The two resolvers now agree.
    if str(vool_env("PROJECT_ROOT") or "").strip():
        return (active_vool_home() / "workspace").resolve()
    try:
        return Path.cwd().resolve()
    except FileNotFoundError:
        return PROJECT_ROOT.resolve()


def ensure_runtime_dirs() -> None:
    # Best-effort outside the writable home: a packaged source root (VOOL_PROJECT_ROOT → the
    # .app bundle) may be read-only, and dirs like PROJECT_ROOT/docs simply don't exist there.
    # Creating them must never break boot; writes to the active home remain load-bearing and
    # fail loudly on their own if that home is somehow unusable.
    for path in (active_vool_home(), active_data_dir(), active_config_home_dir(), DOCS_DIR, active_workspace_dir()):
        with contextlib.suppress(OSError):
            path.mkdir(parents=True, exist_ok=True)


def data_path(*parts: str) -> Path:
    ensure_runtime_dirs()
    return active_data_dir().joinpath(*parts).resolve()


def config_path(*parts: str) -> Path:
    ensure_runtime_dirs()
    candidate = active_config_home_dir().joinpath(*parts)
    if candidate.exists():
        return candidate.resolve()
    return PROJECT_CONFIG_DIR.joinpath(*parts).resolve()


def project_path(*parts: str) -> Path:
    return PROJECT_ROOT.joinpath(*parts).resolve()


def docs_path(*parts: str) -> Path:
    ensure_runtime_dirs()
    return DOCS_DIR.joinpath(*parts).resolve()


def workspace_path(*parts: str) -> Path:
    ensure_runtime_dirs()
    return active_workspace_dir().joinpath(*parts).resolve()
