"""Read the Vool-skills-plugins catalog so the console can show installed Plugins & Skills.

Plugins give VOOL new powers; skills teach it how to use them. This reader walks the plugin monorepo
(marketplace + each plugin's manifest + its skills' SKILL.md frontmatter) into a structured catalog for
the console. Read-only and fail-soft: a missing repo yields an empty catalog with a reason, never a
raised exception. Override the repo location with VOOL_PLUGINS_DIR.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import threading
from pathlib import Path
from typing import Any

from core.runtime_paths import user_runtime_default

_DEFAULT_PLUGINS_DIR = Path.home() / "Desktop" / "Vool-skills-plugins"
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def plugins_root() -> Path | None:
    override = str(os.environ.get("VOOL_PLUGINS_DIR") or "").strip()
    root = Path(override) if override else _DEFAULT_PLUGINS_DIR
    return root if (root / "plugins").is_dir() else None


def _enabled_store_path() -> Path:
    home = Path(os.environ.get("VOOL_HOME") or os.environ.get("NULLA_HOME") or user_runtime_default())
    return home / "config" / "plugins_enabled.json"


def _disabled_ids() -> set[str]:
    """Plugins the owner turned off. Absent file / parse error -> nothing disabled (all on)."""
    try:
        data = json.loads(_enabled_store_path().read_text(encoding="utf-8"))
        return {str(x).strip() for x in (data.get("disabled") or []) if str(x).strip()}
    except Exception:
        return set()


def set_plugin_enabled(plugin_id: str, enabled: bool) -> bool:
    """Persist a plugin's enabled state (owner action). Returns True on a successful write."""
    pid = str(plugin_id or "").strip()
    if not pid:
        return False
    disabled = _disabled_ids()
    disabled.discard(pid) if enabled else disabled.add(pid)
    path = _enabled_store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"disabled": sorted(disabled)}), encoding="utf-8")
        return True
    except Exception:
        return False


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _skill_frontmatter(skill_md: Path) -> dict[str, str]:
    try:
        text = skill_md.read_text(encoding="utf-8")
    except Exception:
        return {}
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    values: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip()
    return values


def _plugin_entry(plugin_dir: Path) -> dict[str, Any] | None:
    manifest = _read_json(plugin_dir / ".codex-plugin" / "plugin.json")
    if not manifest:
        return None
    interface = manifest.get("interface") if isinstance(manifest.get("interface"), dict) else {}
    author = manifest.get("author") if isinstance(manifest.get("author"), dict) else {}
    # First-party = built by us (Parad0x / sls_0x), so the console can badge our own plugins.
    author_blob = f"{author.get('name', '')} {author.get('url', '')} {interface.get('developerName', '')}".lower()
    first_party = any(mark in author_blob for mark in ("parad0x", "sls_0x", "sls0x", "vool"))
    skills: list[dict[str, Any]] = []
    skills_dir = plugin_dir / "skills"
    if skills_dir.is_dir():
        for skill_dir in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
            md = skill_dir / "SKILL.md"
            if not md.is_file():
                continue
            front = _skill_frontmatter(md)
            skills.append({
                "name": front.get("name") or skill_dir.name,
                "description": front.get("description", ""),
            })
    return {
        "id": str(manifest.get("name") or plugin_dir.name),
        "name": str(interface.get("displayName") or manifest.get("name") or plugin_dir.name),
        "version": str(manifest.get("version") or "0.0.0"),
        "description": str(interface.get("shortDescription") or manifest.get("description") or ""),
        "category": str(interface.get("category") or "Uncategorized"),
        "first_party": bool(first_party),
        # Declared tools/permissions are part of the target schema; surface them when present so the
        # console already shows them for plugins that adopt the richer manifest.
        "tools": [str(t) for t in (manifest.get("tools") or []) if str(t).strip()],
        "permissions": [str(p) for p in (manifest.get("permissions") or []) if str(p).strip()],
        "skills": skills,
    }


def _native_library_entry() -> dict[str, Any] | None:
    """The first-party native skill library as one catalog entry, for the SAME projection.

    The toolbelt console reads this catalog; the library the runtime ships with must be visible
    in it exactly like an installed plugin, with per-skill version + enabled state. Fail-soft:
    a library that cannot be read yields no entry rather than an error page.
    """
    try:
        from core.native_skill_library import skill_inventory

        rows = skill_inventory()
    except Exception:
        return None
    if not rows:
        return None
    skills = [
        {
            "name": row["id"],
            "description": row.get("description") or "",
            "version": row.get("version") or "",
            "enabled": bool(row.get("enabled")),
            "available": bool(row.get("available")),
            "reason": row.get("reason") or "",
        }
        for row in rows
    ]
    return {
        "id": "native-library",
        "name": "Native Skill Library",
        "version": "1.0.0",
        "description": "The skill packages shipped with the runtime itself.",
        "category": "First-party",
        "first_party": True,
        "tools": [],
        "permissions": [],
        "skills": skills,
        "enabled": any(s["enabled"] for s in skills),
    }


# --- plugin STORAGE: the folder's accessibility as explicit runtime state ------------------------
#
# Measured on the packaged 352ce68b app, 2026-09-10 (validation-logs/owner-execution-repair-20260910/
# NATIVE_SWITCH_20260910.md): the API child's boot listed `~/Desktop/Vool-skills-plugins/plugins`
# on the main thread BEFORE it bound its port. The `opendir()` hung in the kernel, the native host
# never saw `/healthz`, and after 90 s it tore the child down -- no window, no explanation, and no
# way to recover short of relaunching. The same boot loaded both plugins in 0.1 s an hour later.
#
# Plugin storage is user-controlled, may live on a folder macOS gates behind a consent dialog, may be
# on a slow or absent volume, and may be unreadable. Core readiness must not depend on it, and its
# state must be a FACT the app reports rather than a hang the app disappears into. Three laws:
#
# * the folder is opened by a bounded PROBE SUBPROCESS, never by the serving process's own threads.
#   A directory open that does not return within its budget is killed (SIGKILL) and reported as
#   STALLED; the serving process keeps no thread parked in the kernel, so there is no worker that
#   cannot be cancelled;
# * every outcome is a named state -- accessible / missing / denied / stalled / failed -- carried on
#   the catalog, on /healthz and on the plugin panel with the folder and the reason, and NOTHING is
#   changed to get there: no plugin is disabled, no configuration is discarded, no file is moved;
# * recovery is a rescan (owner-local POST, the catalog read, the boot) that repeats the bounded
#   probe; when the folder answers, the packs load exactly as they would have at boot.

STORAGE_PENDING = "pending"
STORAGE_ACCESSIBLE = "accessible"
STORAGE_MISSING = "missing"
STORAGE_DENIED = "denied"
STORAGE_STALLED = "stalled"
STORAGE_FAILED = "failed"

#: Wall-clock bounds (CLAUDE.md 4b: a bound, not a spend guess). The boot probe of the two installed
#: packs measures ~0.1 s on this machine; a probe that needs seconds is not a slow folder, it is a
#: folder waiting on something else (a consent dialog, a dead volume), and the app serves without it.
BOOT_PROBE_BUDGET_S = 3.0
RESCAN_PROBE_BUDGET_S = 5.0
REQUEST_PROBE_BUDGET_S = 1.0
#: How old an ACCESSIBLE listing may be before a read repeats the probe. Zero: every listing read
#: re-probes (~15 ms measured, bounded, single-flight), so a pack written a moment ago is listed
#: at once -- the write-then-read contract the direct directory walk gave installers and tests.
LISTING_MAX_AGE_S = 0.0
#: How old a NOT-accessible result may be before a request path retries the folder: at most one
#: bounded probe per this interval is spent on a folder that is not answering.
PROBE_MAX_AGE_S = 30.0
#: How long to wait for a killed probe to actually exit before reporting it as lingering.
_KILL_WAIT_S = 2.0

#: The probe: stdlib only, run with `-I -S` so it starts in tens of milliseconds and ignores the
#: environment. It opens the folder (the call that hung), names every directory in it and which
#: of them carry a manifest (a skills-only directory is a plugin directory to the skill readers,
#: a pack to the loader only with its manifest). Any OSError is returned typed; the parent
#: classifies it.
_PROBE_SOURCE = (
    "import json, os, sys\n"
    "root = sys.argv[1]\n"
    "try:\n"
    "    dirs, packs = [], []\n"
    "    with os.scandir(root) as it:\n"
    "        for e in it:\n"
    "            try:\n"
    "                if not e.is_dir():\n"
    "                    continue\n"
    "                dirs.append(e.name)\n"
    "                if os.path.isfile(os.path.join(e.path, '.codex-plugin', 'plugin.json')):\n"
    "                    packs.append(e.name)\n"
    "            except OSError:\n"
    "                continue\n"
    "    print(json.dumps({'ok': True, 'entries': sorted(dirs), 'packs': sorted(packs)}))\n"
    "except OSError as exc:\n"
    "    print(json.dumps({'ok': False, 'errno': exc.errno, 'error': exc.strerror or str(exc)}))\n"
)

_STORAGE_LOCK = threading.RLock()
_PROBE_IN_FLIGHT = threading.Lock()
_STORAGE: dict[str, Any] = {
    "state": STORAGE_PENDING,
    "root": "",
    "plugins_dir": "",
    "detail": "",
    "reason": "",
    "entries": (),
    "packs": (),
    "loaded": (),
    "errors": (),
    "attempts": 0,
    "last_attempt_at": "",
    "completed_at": "",
    "elapsed_ms": None,
    "budget_s": None,
    "in_flight": False,
    "probe_pid": None,
    "probe_lingering": False,
    "last_reason": "",
    "registered_root": "",
    "registered_entries": (),
}
#: Set once this process has LOADED packs (boot, rescan): later probes by any reader load too.
_REGISTRATION_WANTED = False


def _utcnow() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _plugin_probe_command(plugins_dir: Path) -> list[str]:
    """The probe's argv. A seam so a test can stand a stalling probe in for the real one."""
    import sys

    return [sys.executable, "-I", "-S", "-c", _PROBE_SOURCE, str(plugins_dir)]


def _classify_probe_error(errno_value: Any, message: str) -> tuple[str, str]:
    import errno as _errno

    code = int(errno_value) if isinstance(errno_value, int) else -1
    if code in (_errno.ENOENT, _errno.ENOTDIR):
        return STORAGE_MISSING, message
    if code in (_errno.EACCES, _errno.EPERM):
        return STORAGE_DENIED, message
    return STORAGE_FAILED, message


def probe_plugin_storage(plugins_dir: Path, *, budget_s: float) -> dict[str, Any]:
    """Open the plugin folder in a bounded, killable subprocess and report what happened.

    Returns {state, entries, packs, detail, elapsed_ms, probe_pid, lingering}: `entries` is every
    directory in the folder, `packs` those carrying a manifest. The serving process never
    calls into the folder itself here: if the open blocks, the probe is the thing that blocks, and
    after `budget_s` it is killed. A probe that will not die inside `_KILL_WAIT_S` (a kernel wait the
    signal cannot reach yet) is reported as lingering by pid; it holds nothing of ours.
    """
    import subprocess
    import time

    started = time.monotonic()
    command = _plugin_probe_command(Path(plugins_dir))
    try:
        proc = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            stdin=subprocess.DEVNULL,
        )
    except OSError as exc:
        return {
            "state": STORAGE_FAILED, "entries": (), "detail": f"probe could not start: {exc}",
            "elapsed_ms": int((time.monotonic() - started) * 1000), "probe_pid": None, "lingering": False,
        }
    try:
        out, err = proc.communicate(timeout=max(0.05, float(budget_s)))
    except subprocess.TimeoutExpired:
        lingering = False
        with contextlib.suppress(OSError):
            proc.kill()
        try:
            proc.wait(timeout=_KILL_WAIT_S)
        except subprocess.TimeoutExpired:
            lingering = True
        return {
            "state": STORAGE_STALLED, "entries": (),
            "detail": f"the folder did not answer within {float(budget_s):.1f} s",
            "elapsed_ms": int((time.monotonic() - started) * 1000), "probe_pid": proc.pid,
            "lingering": lingering,
        }
    elapsed_ms = int((time.monotonic() - started) * 1000)
    try:
        payload = json.loads(str(out or "").strip().splitlines()[-1]) if str(out or "").strip() else {}
    except (ValueError, IndexError):
        payload = {}
    if not isinstance(payload, dict) or "ok" not in payload:
        detail = (str(err or "").strip() or f"probe exited {proc.returncode} without a report")[:300]
        return {"state": STORAGE_FAILED, "entries": (), "detail": detail, "elapsed_ms": elapsed_ms,
                "probe_pid": proc.pid, "lingering": False}
    if payload.get("ok"):
        entries = tuple(str(name) for name in (payload.get("entries") or []) if str(name).strip())
        packs = tuple(str(name) for name in (payload.get("packs") or []) if str(name).strip())
        return {"state": STORAGE_ACCESSIBLE, "entries": entries, "packs": packs, "detail": "",
                "elapsed_ms": elapsed_ms, "probe_pid": proc.pid, "lingering": False}
    state, detail = _classify_probe_error(payload.get("errno"), str(payload.get("error") or ""))
    return {"state": state, "entries": (), "detail": detail, "elapsed_ms": elapsed_ms,
            "probe_pid": proc.pid, "lingering": False}


def _reason_for(state: str, plugins_dir: str, detail: str, budget_s: float | None) -> str:
    if state == STORAGE_ACCESSIBLE:
        return ""
    if state == STORAGE_MISSING:
        return "No plugins repo found (set VOOL_PLUGINS_DIR)."
    if state == STORAGE_DENIED:
        return (
            f"Access to the plugin folder was denied ({plugins_dir}: {detail}). Grant VOOL access to "
            "that folder (System Settings › Privacy & Security › Files and Folders), then Rescan. "
            "The app is running without plugins until then; nothing was disabled or changed."
        )
    if state == STORAGE_STALLED:
        shown = f"{float(budget_s):.0f} s" if budget_s else "its time budget"
        return (
            f"The plugin folder did not answer within {shown} ({plugins_dir}); the app is running "
            "without plugins. If macOS is asking whether VOOL may access this folder, answer it, "
            "then Rescan. Nothing was disabled or changed."
        )
    if state == STORAGE_FAILED:
        return (
            f"The plugin folder could not be read ({plugins_dir}: {detail}); the app is running "
            "without plugins. Rescan once the folder is readable. Nothing was disabled or changed."
        )
    return "Plugin storage has not been checked yet."


def _publish(**fields: Any) -> None:
    with _STORAGE_LOCK:
        _STORAGE.update(fields)


def storage_state() -> dict[str, Any]:
    """The plugin-storage state as the API, /healthz and the panel report it. Never raises."""
    with _STORAGE_LOCK:
        snapshot = dict(_STORAGE)
    snapshot["entries"] = list(snapshot.get("entries") or ())
    snapshot["packs"] = list(snapshot.get("packs") or ())
    snapshot["loaded"] = list(snapshot.get("loaded") or ())
    snapshot["errors"] = list(snapshot.get("errors") or ())
    snapshot.pop("registered_root", None)
    snapshot.pop("registered_entries", None)
    return snapshot


def _register_listing(
    root: Path, plugins_dir: Path, entries: tuple[str, ...]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Load the packs a probe named: the boot's own `load_all`, over the probe's listing."""
    from core.plugin_tools import load_all

    manifests = tuple(plugins_dir / name / ".codex-plugin" / "plugin.json" for name in entries)
    loaded, load_errors = load_all(root, manifests=manifests)
    return tuple(item.plugin_id for item in loaded), tuple(str(item) for item in load_errors)


def _probe_and_publish(*, budget_s: float, register: bool, reason: str) -> dict[str, Any]:
    """One bounded probe of the configured folder, published as the storage state.

    Single-flight: a call while a probe is in flight returns the current state marked in flight
    and starts nothing, so a stalled folder can never accumulate probes. With `register`, packs
    the probe names are loaded through the same idempotent `load_all` the boot has always used.
    """
    root = plugins_root()
    now = _utcnow()
    if root is None:
        _publish(
            state=STORAGE_MISSING, root="", plugins_dir="", detail="no `plugins/` directory at the configured root",
            reason=_reason_for(STORAGE_MISSING, "", "", budget_s), entries=(), packs=(), loaded=(), errors=(),
            last_attempt_at=now, completed_at=now, elapsed_ms=0, budget_s=float(budget_s), last_reason=reason,
            in_flight=False, probe_pid=None, probe_lingering=False, registered_root="", registered_entries=(),
        )
        with _STORAGE_LOCK:
            _STORAGE["attempts"] = int(_STORAGE.get("attempts") or 0) + 1
        return storage_state()
    plugins_dir = Path(root) / "plugins"
    if not _PROBE_IN_FLIGHT.acquire(blocking=False):
        with _STORAGE_LOCK:
            _STORAGE["in_flight"] = True
        return storage_state()
    try:
        with _STORAGE_LOCK:
            previous = dict(_STORAGE)
            _STORAGE["attempts"] = int(_STORAGE.get("attempts") or 0) + 1
            _STORAGE["in_flight"] = True
            _STORAGE["last_attempt_at"] = now
            _STORAGE["last_reason"] = reason
            _STORAGE["root"] = str(root)
            _STORAGE["plugins_dir"] = str(plugins_dir)
            _STORAGE["budget_s"] = float(budget_s)
        result = probe_plugin_storage(plugins_dir, budget_s=budget_s)
        state = str(result["state"])
        entries = tuple(result.get("entries") or ())
        packs = tuple(result.get("packs") or ())
        loaded_ids: tuple[str, ...] = ()
        errors: tuple[str, ...] = ()
        registered_root = ""
        registered_entries: tuple[str, ...] = ()
        if state == STORAGE_ACCESSIBLE:
            if register:
                loaded_ids, errors = _register_listing(root, plugins_dir, packs)
                registered_root, registered_entries = str(root), packs
            elif previous.get("registered_root") == str(root):
                # A list-only refresh keeps what an earlier registration established.
                loaded_ids = tuple(previous.get("loaded") or ())
                errors = tuple(previous.get("errors") or ())
                registered_root = str(root)
                registered_entries = tuple(previous.get("registered_entries") or ())
        _publish(
            state=state, detail=str(result.get("detail") or ""),
            reason=_reason_for(state, str(plugins_dir), str(result.get("detail") or ""), budget_s),
            entries=entries, packs=packs, loaded=loaded_ids, errors=errors, completed_at=_utcnow(),
            elapsed_ms=result.get("elapsed_ms"), in_flight=False,
            probe_pid=result.get("probe_pid"), probe_lingering=bool(result.get("lingering")),
            registered_root=registered_root, registered_entries=registered_entries,
        )
        return storage_state()
    finally:
        with _STORAGE_LOCK:
            _STORAGE["in_flight"] = False
        _PROBE_IN_FLIGHT.release()


def discover_and_register(*, budget_s: float, reason: str = "") -> dict[str, Any]:
    """Probe the plugin folder within `budget_s` and, when it answers, load its packs.

    The boot's and the rescan's door. It also marks this process as one that LOADS packs, so a
    later probe by any reader (a catalog read, a tool offer) that finds a folder answering after
    a stalled or denied boot loads the packs exactly as the boot would have -- recovery without
    a relaunch. A process that never called this (a test, an in-process tool) only lists.
    """
    global _REGISTRATION_WANTED
    _REGISTRATION_WANTED = True
    return _probe_and_publish(budget_s=budget_s, register=True, reason=reason)


def ensure_discovered(
    *,
    max_age_s: float | None = None,
    budget_s: float = REQUEST_PROBE_BUDGET_S,
    register: bool | None = None,
) -> dict[str, Any]:
    """The current storage state, re-probed when stale, not yet taken, or taken for another root.

    An ACCESSIBLE listing ages out after `LISTING_MAX_AGE_S` (a pack installed while the app runs
    shows up on the next read); any other state is retried after `PROBE_MAX_AGE_S`, so a request
    path spends at most one bounded probe per that interval on a folder that is not answering.
    `register` defaults to whatever this process established (see `discover_and_register`).
    """
    from datetime import datetime, timezone

    want_register = _REGISTRATION_WANTED if register is None else bool(register)
    root = plugins_root()
    current_root = str(root) if root is not None else ""
    with _STORAGE_LOCK:
        snapshot = dict(_STORAGE)
    if snapshot.get("in_flight"):
        return storage_state()
    state = str(snapshot.get("state") or STORAGE_PENDING)
    stale = True
    if state != STORAGE_PENDING and str(snapshot.get("root") or "") == current_root:
        limit = float(max_age_s) if max_age_s is not None else (
            LISTING_MAX_AGE_S if state == STORAGE_ACCESSIBLE else PROBE_MAX_AGE_S
        )
        try:
            taken = datetime.fromisoformat(str(snapshot.get("last_attempt_at") or ""))
            stale = (datetime.now(timezone.utc) - taken).total_seconds() >= limit
        except ValueError:
            stale = True
    if stale:
        return _probe_and_publish(budget_s=budget_s, register=want_register, reason="ensure")
    if (
        want_register
        and state == STORAGE_ACCESSIBLE
        and root is not None
        and (
            str(snapshot.get("registered_root") or "") != current_root
            or tuple(snapshot.get("registered_entries") or ()) != tuple(snapshot.get("packs") or ())
        )
    ):
        # Listed by a probe-only reader, not yet loaded here: load from the listing, no re-probe.
        if _PROBE_IN_FLIGHT.acquire(blocking=False):
            try:
                packs = tuple(snapshot.get("packs") or ())
                loaded_ids, errors = _register_listing(root, Path(str(snapshot.get("plugins_dir"))), packs)
                _publish(loaded=loaded_ids, errors=errors, registered_root=current_root, registered_entries=packs)
            finally:
                _PROBE_IN_FLIGHT.release()
    return storage_state()


def invalidate_storage_listing() -> None:
    """A writer changed the plugin folder (a pack or skill installed): the next read re-probes."""
    with _STORAGE_LOCK:
        _STORAGE["last_attempt_at"] = ""


def discovered_plugin_dirs() -> tuple[Path, ...]:
    """EVERY plugin directory the last successful probe named -- no directory listing here.

    Request-path readers (tool offers, the census, the lifecycle snapshot) take this instead of
    walking the folder themselves, so a folder that stalls after boot cannot stall a turn: the
    probe is bounded, and an inaccessible folder simply yields no directories.
    """
    state = ensure_discovered()
    if state.get("state") != STORAGE_ACCESSIBLE:
        return ()
    plugins_dir = Path(str(state.get("plugins_dir") or ""))
    return tuple(plugins_dir / name for name in state.get("entries") or ())


def discovered_manifests() -> tuple[Path, ...]:
    """The manifests of the PACKS the last successful probe named (directories with a manifest)."""
    state = ensure_discovered()
    if state.get("state") != STORAGE_ACCESSIBLE:
        return ()
    plugins_dir = Path(str(state.get("plugins_dir") or ""))
    return tuple(plugins_dir / name / ".codex-plugin" / "plugin.json" for name in state.get("packs") or ())


def reset_storage_state() -> None:
    """Tests only: forget every probe so the next call starts from `pending`."""
    global _REGISTRATION_WANTED
    _REGISTRATION_WANTED = False
    with _STORAGE_LOCK:
        _STORAGE.update(
            state=STORAGE_PENDING, root="", plugins_dir="", detail="", reason="", entries=(), packs=(), loaded=(),
            errors=(), attempts=0, last_attempt_at="", completed_at="", elapsed_ms=None, budget_s=None,
            last_reason="", in_flight=False, probe_pid=None, probe_lingering=False, registered_root="",
            registered_entries=(),
        )


def read_plugin_catalog() -> dict[str, Any]:
    """Return {installed, root, plugins, plugin_count, skill_count, reason, storage}.

    `installed` is True only when the plugin folder is ACCESSIBLE; otherwise `reason` says which
    state it is in and what recovers it, and `storage` carries the typed state. The listing comes
    from the bounded probe, never from a directory walk in the serving process.
    """
    storage = ensure_discovered()
    root = plugins_root()
    disabled = _disabled_ids()
    plugins: list[dict[str, Any]] = []
    native = _native_library_entry()
    if native is not None:
        # The first-party library leads the catalog: it is the runtime's own capability set.
        plugins.append(native)
    if root is None or storage.get("state") != STORAGE_ACCESSIBLE:
        return {
            "installed": False,
            "root": "" if root is None else str(root),
            "plugins": plugins if native is not None else [],
            "plugin_count": len(plugins) if native is not None else 0,
            "skill_count": sum(len(p["skills"]) for p in plugins) if native is not None else 0,
            "reason": str(storage.get("reason") or _reason_for(STORAGE_MISSING, "", "", None)),
            "storage": storage,
        }
    for plugin_dir in discovered_plugin_dirs():
        entry = _plugin_entry(plugin_dir)
        if entry is not None:
            entry["enabled"] = entry["id"] not in disabled
            plugins.append(entry)
    return {
        "installed": True,
        "root": str(root),
        "plugins": plugins,
        "plugin_count": len(plugins),
        "skill_count": sum(len(p["skills"]) for p in plugins),
        "reason": "",
        "storage": storage,
    }


__all__ = [
    "BOOT_PROBE_BUDGET_S",
    "LISTING_MAX_AGE_S",
    "PROBE_MAX_AGE_S",
    "REQUEST_PROBE_BUDGET_S",
    "RESCAN_PROBE_BUDGET_S",
    "STORAGE_ACCESSIBLE",
    "STORAGE_DENIED",
    "STORAGE_FAILED",
    "STORAGE_MISSING",
    "STORAGE_PENDING",
    "STORAGE_STALLED",
    "discover_and_register",
    "discovered_manifests",
    "discovered_plugin_dirs",
    "ensure_discovered",
    "invalidate_storage_listing",
    "plugins_root",
    "probe_plugin_storage",
    "read_plugin_catalog",
    "reset_storage_state",
    "set_plugin_enabled",
    "storage_state",
]
