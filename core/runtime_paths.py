from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
import tempfile
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
_VOOL_HOME_ENV_AT_OVERRIDE = ""
_CONFIGURED_HOME_OVERRIDE: Path | None = None
_RUNTIME_HOME_GENERATION = 0
_RUNTIME_HOME_ENV_LAST_SEEN: str | None = None


def runtime_home_generation() -> int:
    """Monotonic count of in-process runtime-home authority changes.

    Hot caches that must never outlive a home switch (the A8 digest-key cache)
    compare this number instead of re-resolving filesystem paths on every call.
    Explicit environment changes also advance it, so a bootstrap binding and a
    digest-key cache cannot silently survive a later profile selection.
    """
    global _RUNTIME_HOME_GENERATION, _RUNTIME_HOME_ENV_LAST_SEEN
    selected = _runtime_env_home(os.environ)
    if _RUNTIME_HOME_ENV_LAST_SEEN is not None and selected != _RUNTIME_HOME_ENV_LAST_SEEN:
        _RUNTIME_HOME_GENERATION += 1
    _RUNTIME_HOME_ENV_LAST_SEEN = selected
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
    global _VOOL_HOME_OVERRIDE, _VOOL_HOME_ENV_AT_OVERRIDE, _RUNTIME_HOME_GENERATION, _RUNTIME_HOME_ENV_LAST_SEEN, _CONFIGURED_HOME_OVERRIDE
    _RUNTIME_HOME_GENERATION += 1
    _VOOL_HOME_OVERRIDE = None if path is None else Path(path).expanduser().resolve()
    _CONFIGURED_HOME_OVERRIDE = _VOOL_HOME_OVERRIDE
    _VOOL_HOME_ENV_AT_OVERRIDE = _runtime_env_home(os.environ)
    _RUNTIME_HOME_ENV_LAST_SEEN = _VOOL_HOME_ENV_AT_OVERRIDE


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
        # An in-process override owns a deliberate home switch (session import, an
        # embedder). A later explicit environment change selects a new profile and
        # must not be silently pinned to a home captured by an earlier bootstrap.
        if _runtime_override_is_active(os.environ):
            return _VOOL_HOME_OVERRIDE.resolve()
        env_map = os.environ
        env_home = _runtime_env_home(env_map)
        if env_home:
            return Path(env_home).expanduser().resolve()
        installed_home = discover_installed_runtime_home()
        if installed_home is not None:
            return installed_home
        return VOOL_HOME.resolve()

    env_map = env
    explicit_env_home = "VOOL_HOME" in env_map or "NULLA_HOME" in env_map
    env_home = _runtime_env_home(env_map)
    if env_home:
        return Path(env_home).expanduser().resolve()
    if _VOOL_HOME_OVERRIDE is not None and not explicit_env_home:
        return _VOOL_HOME_OVERRIDE.resolve()
    installed_home = discover_installed_runtime_home()
    if installed_home is not None:
        return installed_home
    return VOOL_HOME.resolve()


def _runtime_env_home(env: Mapping[str, str]) -> str:
    return str(env.get("VOOL_HOME") or "").strip() or str(env.get("NULLA_HOME") or "").strip()


def _runtime_override_is_active(env: Mapping[str, str]) -> bool:
    # Explicit in-process assignments are a supported embedding seam. Only an
    # override bound through configure_runtime_home can expire after a later
    # environment selection; a newly assigned override has its own authority.
    return _VOOL_HOME_OVERRIDE is not None and (
        _VOOL_HOME_OVERRIDE is not _CONFIGURED_HOME_OVERRIDE
        or _runtime_env_home(env) == _VOOL_HOME_ENV_AT_OVERRIDE
    )


def active_data_dir() -> Path:
    return (active_vool_home() / "data").resolve()


def active_config_home_dir() -> Path:
    return (active_vool_home() / "config").resolve()


def runtime_home_is_isolated(env: Mapping[str, str] | None = None) -> bool:
    """Whether the active profile is separate from a conventional installed home.

    Existing installations keep their external Desktop packs and native compatibility
    locks. A custom profile must never inherit those resources from the login home.
    This includes in-process overrides and install receipts, not only environment vars.
    """
    env_map = os.environ if env is None else env
    user_home = Path.home()
    support = user_home / "Library" / "Application Support"
    defaults = {
        VOOL_HOME.resolve(),
        (PROJECT_ROOT / ".vool_local").resolve(),
        (PROJECT_ROOT / ".nulla_local").resolve(),
        (user_home / ".vool_runtime").resolve(),
        (user_home / ".nulla_runtime").resolve(),
        (support / "VOOL" / "runtime").resolve(),
        (support / "NULLA" / "runtime").resolve(),
    }
    if sys.platform == "win32":
        local = Path(str(env_map.get("LOCALAPPDATA") or user_home)).expanduser()
        defaults.update({(local / "VOOL").resolve(), (local / "NULLA").resolve()})
    return active_vool_home(env).resolve() not in defaults


def runtime_auxiliary_path(*parts: str, legacy: str | Path | None = None) -> Path:
    """Resolve product-owned auxiliary state without leaking a custom profile.

    ``legacy`` preserves an existing default-install location; explicit custom homes
    always own the corresponding state beneath their own root.
    """
    if legacy is not None and not runtime_home_is_isolated():
        return Path(legacy).expanduser().resolve()
    return active_vool_home().joinpath(*parts).resolve()


def native_runtime_home(env: Mapping[str, str] | None = None) -> Path:
    """Native launch home, resolved before the launcher opens logs or starts a child."""
    env_map = os.environ if env is None else env
    if env is None and _runtime_override_is_active(env_map):
        return _VOOL_HOME_OVERRIDE.resolve()
    explicit = _runtime_env_home(env_map)
    if explicit:
        return Path(explicit).expanduser().resolve()
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
        canonical, legacy = base / "VOOL" / "runtime", base / "NULLA" / "runtime"
        return (legacy if not canonical.exists() and legacy.exists() else canonical).resolve()
    if sys.platform == "win32":
        base = Path(str(env_map.get("LOCALAPPDATA") or Path.home())).expanduser()
        canonical, legacy = base / "VOOL", base / "NULLA"
        return (legacy if not canonical.exists() and legacy.exists() else canonical).resolve()
    return user_runtime_default().resolve()


def native_lock_base(env: Mapping[str, str] | None = None) -> Path:
    """Keep default current/legacy singleton names; isolate custom profile locks."""
    if runtime_home_is_isolated(env):
        return (active_vool_home(env) / "run" / "native").resolve()
    env_map = os.environ if env is None else env
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    if sys.platform == "win32":
        return Path(str(env_map.get("LOCALAPPDATA") or Path.home())).expanduser()
    return Path(str(env_map.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")).expanduser()


def native_state_dir(env: Mapping[str, str] | None = None) -> Path:
    if runtime_home_is_isolated(env):
        return (active_vool_home(env) / "logs").resolve()
    return native_lock_base(env) / "VOOL"


def runtime_temp_dir() -> Path:
    path = active_vool_home() / "tmp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def runtime_home_identity(env: Mapping[str, str] | None = None) -> str:
    """Non-secret profile identity for local health and native ownership checks.

    Hash the canonical selected root rather than exposing a personal filesystem
    path in the unauthenticated health stamp. Identity is not authorization to
    signal a process; native hosts may stop only children they actually created.
    """
    canonical = os.path.normcase(str(active_vool_home(env)))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def runtime_child_scratch_root(kind: str) -> Path:
    """Profile-scoped child scratch outside the sandbox's read-denied key home.

    TMPDIR remains the caller's authority for its placement. Naming by profile
    prevents an isolated install from inheriting another profile's child files.
    """
    identity = hashlib.sha256(str(active_vool_home()).encode("utf-8")).hexdigest()[:24]
    return Path(tempfile.gettempdir()) / f"vool-{kind}-scratch" / identity


def active_workspace_dir() -> Path:
    override = str(vool_env("WORKSPACE_ROOT") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    # Writable-state law (2026-09-02): a packaged/bundle runtime identifies its SOURCE root via
    # VOOL_PROJECT_ROOT (the app launchers export it). Runtime-generated state must never
    # materialize inside that tree — the workspace resolves beneath the active vool home like
    # every other writable state, so a read-only .app bundle stays byte-identical while running.
    if str(vool_env("PROJECT_ROOT") or "").strip() or runtime_home_is_isolated():
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
        working_directory = Path.cwd().resolve()
        # An isolated runtime launched from its source tree owns generated state
        # beneath its profile. A dev invocation in a selected working directory
        # keeps that directory as the user's project workspace.
        if working_directory == PROJECT_ROOT and runtime_home_is_isolated():
            return (active_vool_home() / "workspace").resolve()
        return working_directory
    except FileNotFoundError:
        return PROJECT_ROOT.resolve()


def ensure_runtime_dirs() -> None:
    # Best-effort outside the writable home: a packaged source root (VOOL_PROJECT_ROOT → the
    # .app bundle) may be read-only, and dirs like PROJECT_ROOT/docs simply don't exist there.
    # Creating them must never break boot; writes to the active home remain load-bearing and
    # fail loudly on their own if that home is somehow unusable.
    for path in (active_vool_home(), active_data_dir(), active_config_home_dir(), active_workspace_dir()):
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
