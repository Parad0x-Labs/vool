"""The ONE bounded unattended preflight (C15, 2026-09-03 operator addendum).

Every VOOL-owned launcher, test harness, updater and node boot calls
:func:`preflight` FIRST — before any module in the process can reach an OS
credential API (the macOS ``security`` CLI, the ``keyring`` backend, a
signed-in browser profile). The preflight is bounded: it spawns nothing but
one short-lived ``ps`` per ancestor, never touches a keychain, and degrades
to a typed record when it cannot complete.

What it decides, from process/argument provenance (this process's ancestry
chain, not vibes):

* **VOOL-owned?** The ancestry chain must trace to a repo/launcher marker
  (repo path, ``python -m apps/ops/installer``, pytest, shard runner, bundle
  supervisor). An unrelated application that happens to import this library
  is recorded and left completely alone — no environment is rewritten.
* **Scratch / test mode?** pytest markers, the shard gate env, an explicit
  ``VOOL_TEST_MODE=1`` — or an unattended boot (``VOOL_UNATTENDED=1``/CI)
  against a home with no prior credential state. In this mode the preflight
  pins, BEFORE any credential call: file-backed AES vault credential storage,
  file-backed signer identity (the synthetic node identity), and no Keychain
  grant — even if the operator's shell exported one. It also selects the
  bundled Chromium (Playwright cache) and a fresh isolated profile root for
  browser automation, so a headless render can never open the signed-in
  browser or its "Chrome Safe Storage" Keychain item.
* **Established unattended boot** (launchd daemon, updater against the
  operator's real home): recorded, not rewritten. Saved credentials stay
  readable through the normal non-interactive path; the grant-gating in
  ``core.keychain_policy`` already prevents any interactive write there.
* **Interactive run**: recorded only. Explicit secure storage stays exactly
  what the user initiates, and it goes through
  :func:`explicit_secure_storage` — ONE bounded call, typed outcome, recovery
  instructions, never a retry loop and never an auto-clicked dialog.

The report of every preflight (including the full ancestry of the process at
the moment of the decision) is written under the active home's
``data/preflight/`` so each synthetic attempt can be attributed afterwards.
"""
from __future__ import annotations

import contextlib
import glob
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

#: Unattended product boot marker (launchd/updater/CI-style runs).
UNATTENDED_ENV = "VOOL_UNATTENDED"
#: Manual test-process marker (the shard runner and pytest are detected too).
TEST_MODE_ENV = "VOOL_TEST_MODE"
#: Exported by ops/pytest_shards.py to full-scope shard children.
SHARD_GATE_ENV = "VOOL_GATE"

#: Set (to the profile ROOT; the render seam creates a fresh subdirectory per
#: launch) once the preflight has selected browser isolation.
BROWSER_PROFILE_ROOT_ENV = "VOOL_BROWSER_PROFILE_DIR"

#: Wall-clock bound for the whole preflight. Everything it does is local
#: filesystem/env/ps work; if the machine is so loaded that this cannot finish,
#: the decision degrades to "recorded, not enforced" instead of delaying a boot.
PREFLIGHT_BUDGET_S = 5.0

_PROVENANCE_DEPTH = 16


# --------------------------------------------------------------------------------------- provenance


@dataclass(frozen=True)
class ProvenanceEntry:
    """One link of this process's ancestry chain (self first)."""

    pid: int
    ppid: int
    argv: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {"pid": self.pid, "ppid": self.ppid, "argv": list(self.argv)}


def provenance_chain(*, max_depth: int = _PROVENANCE_DEPTH) -> list[ProvenanceEntry]:
    """Walk PID -> PPID to the top, recording each ancestor's argv.

    Best-effort and bounded: an ancestor that exits mid-walk or an ``ps``
    failure simply ends the chain. ``ps`` reads another same-user process's
    command line; nothing is written outside the returned list.
    """
    chain: list[ProvenanceEntry] = []
    pid = os.getpid()
    ppid = os.getppid()
    for _ in range(max_depth):
        argv = _ps_command(pid) or ()
        chain.append(ProvenanceEntry(pid=pid, ppid=ppid, argv=argv))
        if ppid <= 1:
            break
        next_pid = ppid
        next_ppid = _ps_ppid(next_pid)
        if next_pid <= 1:
            break
        pid, ppid = next_pid, next_ppid
    return chain


def _ps_command(pid: int) -> tuple[str, ...] | None:
    try:
        out = subprocess.run(
            ["/bin/ps", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = (out.stdout or "").strip()
    return tuple(line.split()) if line else None


def _ps_ppid(pid: int) -> int:
    try:
        out = subprocess.run(
            ["/bin/ps", "-o", "ppid=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return 1
    try:
        return int((out.stdout or "1").strip() or 1)
    except ValueError:
        return 1


def classify_provenance(chain: list[ProvenanceEntry], *, repo_root: str | None = None) -> bool:
    """True when the ancestry chain itself marks this as a VOOL-owned launch.

    A launch is VOOL-owned when any ancestor argv references this repository
    path or one of its known launch surfaces (``python -m apps/ops/installer``,
    pytest, the shard runner, the bundle supervisor). Wired call sites are also
    trusted through :func:`_surface_is_vool_owned`, so this check is the
    ancestry RECORD and the fallback for surfaces without a wired name — an
    unrelated application's embed gets recorded honestly, never rewired.
    """
    root = repo_root or str(Path(__file__).resolve().parent.parent)
    for entry in chain:
        joined = " ".join(entry.argv)
        if not joined:
            continue
        if root and root in joined:
            return True
        for marker in (
            " -m apps.",
            " -m ops.",
            " -m installer.",
            "/apps/vool_api_server",
            "/apps/vool_daemon",
            "/apps/vool_cli",
            "/ops/pytest_shards.py",
            "/installer/bundle/bundle_supervisor.py",
            "pytest",
        ):
            if marker in joined:
                return True
    return False


#: Surfaces are named after the VOOL module that wires the preflight. Only
#: VOOL-owned code can name itself here, so the surface name is the ownership
#: declaration and the ancestry chain is the recorded evidence.
_VOOL_SURFACE_PREFIXES = ("apps.", "core.", "ops.", "installer.", "tests.")


def _surface_is_vool_owned(surface: str) -> bool:
    return str(surface).startswith(_VOOL_SURFACE_PREFIXES)


# --------------------------------------------------------------------------------------- mode


def unattended_reasons(env: dict[str, str] | None = None) -> list[str]:
    """Every marker that makes this run unattended, split by class."""
    env_map = os.environ if env is None else env

    def _on(name: str) -> bool:
        return str(env_map.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}

    reasons: list[str] = []
    if _on(TEST_MODE_ENV):
        reasons.append(f"{TEST_MODE_ENV}=1")
    if str(env_map.get(SHARD_GATE_ENV) or "").strip():
        reasons.append(f"{SHARD_GATE_ENV} set")
    if env_map.get("PYTEST_CURRENT_TEST") or env_map.get("PYTEST_VERSION") or env_map.get("PYTEST_XDIST_WORKER"):
        reasons.append("pytest session marker")
    if _on(UNATTENDED_ENV):
        reasons.append(f"{UNATTENDED_ENV}=1")
    if env_map.get("CI"):
        reasons.append("CI environment")
    return reasons


def _test_process(reasons: list[str]) -> bool:
    """True for the markers that mean THIS process is a test/scratch process."""
    return any(
        not r.startswith(f"{UNATTENDED_ENV}=") and r != "CI environment" for r in reasons
    )


def home_is_synthetic(env: dict[str, str] | None = None) -> bool:
    """True when the active VOOL_HOME holds NO prior credential state at all.

    A home with a Keychain prior-use marker, a credentials sidecar/vault or a
    node key record belongs to a real runtime (possibly the operator's own) —
    its storage policy is never rewritten by the preflight. A home without any
    of those is synthetic: the runtime that boots against it has no user data
    to protect, so unattended runs keep it that way (file-backed, no Keychain).
    """
    env_map = os.environ if env is None else env
    home = str(env_map.get("VOOL_HOME") or "").strip()
    if not home:
        return False  # no explicit isolation -> assume the real runtime home
    root = Path(home).expanduser()
    data = root / "data"
    for marker in (
        data / "credentials.keychain_migrated",
        data / "credentials.meta.json",
        data / "credentials.enc.json",
        data / "keys" / "node_signing_key.json",
        data / "keys" / "node_signing_key.keyring.json",
        data / "keys" / "node_signing_key.b64",
        data / "keys" / "key_storage.passphrase",
    ):
        if marker.exists():
            return False
    return True


def preflight_mode(env: dict[str, str] | None = None) -> str:
    """'scratch' | 'established-unattended' | 'interactive', without side effects."""
    env_map = os.environ if env is None else env
    reasons = unattended_reasons(env_map)
    if not reasons:
        return "interactive"
    if _test_process(reasons):
        return "scratch"
    # Unattended product boot (VOOL_UNATTENDED/CI): isolated only when the
    # home holds no user credential state to protect.
    return "scratch" if home_is_synthetic(env_map) else "established-unattended"


def is_unattended() -> bool:
    """Cheap predicate for seams that must behave differently mid-process."""
    return preflight_mode() != "interactive"


# -------------------------------------------------------------------------------- browser


def _playwright_cache_dir() -> str:
    override = str(os.getenv("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
    if override and override not in {"0", "1"}:
        return override
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Caches/ms-playwright")
    return os.path.expanduser("~/.cache/ms-playwright")


def select_bundled_chromium() -> str | None:
    """Newest bundled Chromium-family binary in the local Playwright cache.

    Deliberately NEVER a profile-bearing installed browser: the cache holds
    the "for testing"/headless-shell builds that do not own the operator's
    signed-in profile or its Keychain item. Reuses the locator globs the
    render engine already defines so the two can never drift apart.
    """
    from tools.browser.browser_render import _PLAYWRIGHT_CACHE_GLOBS

    cache_dir = _playwright_cache_dir()
    # Prefer the HEADLESS SHELL build first, measured 2026-09-03: the full "Chrome for
    # Testing" browser HANGS on a fresh --user-data-dir (first-run/Keychain init — the
    # exact dialog class this lane eliminates), while chrome-headless-shell renders a
    # file:// DOM in ~0.4s against the same fresh profile, offline, touching nothing.
    ordered = sorted(
        _PLAYWRIGHT_CACHE_GLOBS,
        key=lambda pattern: (0 if "headless_shell" in pattern or "headless-shell" in pattern else 1),
    )
    for pattern in ordered:
        try:
            matches = sorted(
                (
                    m
                    for m in glob.glob(os.path.join(cache_dir, pattern))
                    if os.path.isfile(m) and os.access(m, os.X_OK)
                ),
                reverse=True,
            )
        except OSError:
            continue
        if matches:
            return matches[0]
    return None


# -------------------------------------------------------------------------------- report


@dataclass
class PreflightReport:
    """What the preflight decided, and the provenance it decided it from."""

    surface: str
    mode: str
    vool_owned: bool
    reasons: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    credential_backend: str = ""
    signer_mode: str = ""
    browser_binary: str = ""
    browser_profile_root: str = ""
    budget_s: float = PREFLIGHT_BUDGET_S
    degraded: bool = False
    provenance: list[ProvenanceEntry] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "surface": self.surface,
            "mode": self.mode,
            "vool_owned": self.vool_owned,
            "reasons": self.reasons,
            "actions": self.actions,
            "credential_backend": self.credential_backend,
            "signer_mode": self.signer_mode,
            "browser_binary": self.browser_binary,
            "browser_profile_root": self.browser_profile_root,
            "budget_s": self.budget_s,
            "degraded": self.degraded,
            "pid": os.getpid(),
            "provenance": [entry.as_dict() for entry in self.provenance],
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True)


def _write_report(report: PreflightReport) -> None:
    """Best-effort durable record under the active home (one file per pid+surface)."""
    try:
        from core.runtime_paths import data_path

        directory = data_path("preflight")
        directory.mkdir(parents=True, exist_ok=True)
        safe_surface = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in report.surface)[:60]
        path = directory / f"{os.getpid()}-{safe_surface}.json"
        path.write_text(report.to_json(), encoding="utf-8")
        with contextlib.suppress(OSError):
            path.chmod(0o600)
    except Exception:
        pass  # the record must never break the boot it records


# -------------------------------------------------------------------------------- preflight

_REPORTED_SURFACES: set[tuple[str, str]] = set()


def preflight(surface: str) -> PreflightReport:
    """Run the ONE bounded preflight for a VOOL-owned launch surface.

    Idempotent per (surface, mode) within a process; the first call is the one
    that must happen before any OS credential API is reachable.
    """
    started = time.monotonic()
    mode = preflight_mode()
    chain: list[ProvenanceEntry] = []
    ancestry_owned = False
    try:
        chain = provenance_chain()
        ancestry_owned = classify_provenance(chain)
    except Exception:
        ancestry_owned = False  # wired surfaces remain trusted below
    # A wired call site is VOOL-owned by its surface name; the ancestry chain is the
    # recorded evidence. A foreign surface with no VOOL ancestry is only ever recorded.
    owned = _surface_is_vool_owned(surface) or ancestry_owned

    report = PreflightReport(
        surface=surface,
        mode="interactive" if not owned else mode,
        vool_owned=owned,
        reasons=unattended_reasons(),
        provenance=chain,
    )

    key = (surface, report.mode)
    if key in _REPORTED_SURFACES:
        return report

    if owned and report.mode == "scratch" and time.monotonic() - started < PREFLIGHT_BUDGET_S:
        _enforce_scratch_isolation(report)
    if owned and report.mode != "interactive" and time.monotonic() - started < PREFLIGHT_BUDGET_S:
        _select_isolated_browser(report)

    report.degraded = time.monotonic() - started >= PREFLIGHT_BUDGET_S
    _REPORTED_SURFACES.add(key)
    _write_report(report)
    return report


def _enforce_scratch_isolation(report: PreflightReport) -> None:
    """Pin non-interactive storage BEFORE any OS credential API can be reached.

    Order is the invariant the tests sabotage: these env writes happen before
    the first credential-store or signer call in the process, so a grant the
    operator's shell happened to export can never turn a scratch run into a
    Keychain dialog.
    """
    if os.environ.get("VOOL_CREDENTIAL_STORE") != "vault":
        os.environ["VOOL_CREDENTIAL_STORE"] = "vault"
        report.actions.append("pinned VOOL_CREDENTIAL_STORE=vault")
    preference = str(os.environ.get("VOOL_KEY_STORAGE_MODE") or "").strip().lower()
    if preference not in {"file", "ephemeral"}:
        os.environ["VOOL_KEY_STORAGE_MODE"] = "file"
        report.actions.append("pinned VOOL_KEY_STORAGE_MODE=file")
    if str(os.environ.get("VOOL_KEYCHAIN_ALLOWED") or "").strip():
        os.environ.pop("VOOL_KEYCHAIN_ALLOWED", None)
        report.actions.append("removed inherited VOOL_KEYCHAIN_ALLOWED grant")
    report.credential_backend = "vault"
    report.signer_mode = str(os.environ.get("VOOL_KEY_STORAGE_MODE"))


def _select_isolated_browser(report: PreflightReport) -> None:
    """Point browser automation at the bundled Chromium + a fresh profile root.

    Never the operator's signed-in browser, never a copy of their profile:
    the fresh subdirectory the render seam creates under this root starts
    empty every launch, so there are no cookies, no logins and no "Chrome
    Safe Storage" Keychain item to look for.
    """
    if not os.environ.get(BROWSER_PROFILE_ROOT_ENV):
        profile_root = Path(tempfile.gettempdir()) / f"vool-browser-profiles-{os.getuid()}"
        try:
            profile_root.mkdir(parents=True, exist_ok=True)
            os.environ[BROWSER_PROFILE_ROOT_ENV] = str(profile_root)
            report.actions.append(f"set {BROWSER_PROFILE_ROOT_ENV}={profile_root}")
        except OSError:
            pass
    report.browser_profile_root = os.environ.get(BROWSER_PROFILE_ROOT_ENV, "")

    override = str(os.environ.get("VOOL_BROWSER_BINARY") or "").strip()
    if override and os.path.isfile(override) and os.access(override, os.X_OK):
        report.browser_binary = override
        return
    try:
        bundled = select_bundled_chromium()
    except Exception:
        bundled = None
    if bundled:
        os.environ["VOOL_BROWSER_BINARY"] = bundled
        report.actions.append(f"selected bundled Chromium: {bundled}")
        report.browser_binary = bundled


# -------------------------------------------------------------------------------- explicit storage


class SecureStorageError(RuntimeError):
    """A typed failure of EXPLICIT, user-initiated secure storage.

    Exactly one bounded call was attempted; ``code`` classifies the outcome
    and ``recovery`` is the operator-facing instruction. No caller may retry
    inside the process — a pending dialog outlives any retry budget.
    """

    def __init__(self, code: str, message: str, recovery: str) -> None:
        super().__init__(message)
        self.code = code
        self.recovery = recovery


_RECOVERY = {
    "timeout_prompt_pending": (
        "A Keychain authorization prompt is pending or outlived the bound. Close any pending "
        "dialog, then retry the action once from Settings while the app runs normally. If the "
        "dialog repeats, unlock your login keychain in Keychain Access and make it the default."
    ),
    "locked": (
        "Your login keychain is locked. Open Keychain Access, unlock the login keychain "
        "(it may ask for your account password), then retry the action once."
    ),
    "missing_keychain": (
        "macOS could not find a keychain to store the item. In Keychain Access use "
        "File > Add Keychain to restore 'login' (never delete or reset the existing one), "
        "then retry the action once."
    ),
    "backend_error": (
        "The system credential backend refused the write. Check Keychain Access for an entry "
        "for this app, then retry the action once; if it still fails, keep the fallback "
        "storage (your key keeps working, sealed to this user profile)."
    ),
}


def classify_secure_storage_failure(exc: BaseException) -> tuple[str, str]:
    """Map one failed credential-backend call to its typed (code, recovery) pair."""
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    flat = text.replace("-", "").replace(" ", "").replace("_", "")
    if isinstance(exc, TimeoutError) or "timedout" in flat or "timeout" in flat:
        return "timeout_prompt_pending", _RECOVERY["timeout_prompt_pending"]
    if "locked" in text or "locked" in name or "errseclocked" in flat:
        return "locked", _RECOVERY["locked"]
    if "notfound" in flat or "nokeychain" in flat or "cannotbefound" in flat or "nosuchkeychain" in flat:
        return "missing_keychain", _RECOVERY["missing_keychain"]
    return "backend_error", _RECOVERY["backend_error"]


def explicit_secure_storage(fn, *, what: str):
    """Run ONE bounded, explicitly user-initiated secure-storage call.

    The ONLY door interactive code should use after the user asked for secure
    storage: one bounded attempt, a typed result, no retries, no dialogs
    dismissed on the user's behalf. Success returns fn()'s value; failure
    raises :class:`SecureStorageError` with a code and recovery instructions.
    """
    from core.bounded_keyring import bounded_keyring_call

    try:
        return bounded_keyring_call(fn, what=what)
    except TimeoutError as exc:
        code, recovery = classify_secure_storage_failure(exc)
        raise SecureStorageError(code, f"secure storage '{what}' did not complete: {exc}", recovery) from exc
    except Exception as exc:
        code, recovery = classify_secure_storage_failure(exc)
        raise SecureStorageError(code, f"secure storage '{what}' failed: {exc}", recovery) from exc


__all__ = [
    "BROWSER_PROFILE_ROOT_ENV",
    "PreflightReport",
    "ProvenanceEntry",
    "SecureStorageError",
    "classify_provenance",
    "classify_secure_storage_failure",
    "explicit_secure_storage",
    "home_is_synthetic",
    "is_unattended",
    "preflight",
    "preflight_mode",
    "provenance_chain",
    "select_bundled_chromium",
    "unattended_reasons",
]
