"""macOS bundle installer: codesign/notarization verification + atomic swap + priors.

Bundle verification (defense in depth beyond Ed25519):
  1. `codesign --verify --deep --strict` must pass — the bundle's signature is intact;
  2. when a pinned `expected_authority` is configured, the signing certificate chain
     must show it (`codesign -dv`);
  3. `spctl --assess --type execute` (Gatekeeper) must accept the bundle when
     notarization is required — the production default. The ONLY way around it is a
     per-transaction explicit, journaled `notarization_waiver_reason` (dev sandboxes);
     a dedicated test pins that the default refuses ad-hoc/un-notarized bundles.

Atomic swap = renames inside the app's parent directory (same volume ⇒ atomic):
  target → .<name>.prior-<txid>   (keep, for rollback and "run previous version")
  staged → target                 (undo the first rename if this one fails)
Priors are pruned to a retention count only AFTER a transaction finalizes — during a
transaction the previous version is never deleted, so a failed update always leaves it
runnable. The command runner is injectable; tests use fakes plus one real ad-hoc-signed
bundle exercise.
"""
from __future__ import annotations

import contextlib
import logging
import os
import shlex
import shutil
import signal
import subprocess
from collections.abc import Callable
from pathlib import Path

from core.updater.platforms import BundleVerification, SwapOutcome

logger = logging.getLogger("vool.updater.macos")

CommandRunner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


class MacOSBundleInstaller:
    name = "macos-bundle"

    def __init__(
        self,
        *,
        run: CommandRunner | None = None,
        expected_authority: str = "",
        default_require_notarization: bool = True,
    ):
        self._run = run or _run
        self.expected_authority = str(expected_authority or "")
        self.default_require_notarization = bool(default_require_notarization)

    # -- verification ------------------------------------------------------- #

    def verify_bundle(
        self, staged_bundle: Path, *, require_notarization: bool | None = None
    ) -> BundleVerification:
        bundle = Path(staged_bundle)
        if not bundle.is_dir():
            return BundleVerification(False, detail=f"no such bundle: {bundle}")

        codesign = self._run(["codesign", "--verify", "--deep", "--strict", str(bundle)])
        if codesign.returncode != 0:
            return BundleVerification(
                False,
                codesigned=False,
                detail=f"codesign rejected the bundle: {(codesign.stderr or codesign.stdout).strip()}",
            )

        if self.expected_authority:
            info = self._run(["codesign", "-dv", str(bundle)])
            chain = (info.stdout or "") + (info.stderr or "")
            if self.expected_authority not in chain:
                return BundleVerification(
                    False,
                    codesigned=True,
                    notarized=False,
                    detail=f"signed by an unexpected authority (wanted {self.expected_authority!r})",
                )

        need_notarization = self.default_require_notarization if require_notarization is None else require_notarization
        if need_notarization:
            spctl = self._run(["spctl", "--assess", "--type", "execute", "-vv", str(bundle)])
            if spctl.returncode != 0:
                return BundleVerification(
                    False,
                    codesigned=True,
                    notarized=False,
                    detail=f"Gatekeeper did not accept the bundle: {(spctl.stderr or spctl.stdout).strip()}",
                )
            return BundleVerification(True, codesigned=True, notarized=True)

        return BundleVerification(True, codesigned=True, notarized=False, detail="notarization not required")

    # -- swap --------------------------------------------------------------- #

    @staticmethod
    def _prior_path(target_app: Path, txid: str) -> Path:
        return target_app.parent / f".{target_app.name}.prior-{txid}"

    def atomic_swap(self, staged_bundle: Path, target_app: Path, *, txid: str) -> SwapOutcome:
        staged = Path(staged_bundle)
        target = Path(target_app)
        if not staged.exists():
            return SwapOutcome(False, detail=f"staged bundle missing: {staged}")
        if not target.exists():
            return SwapOutcome(False, detail=f"target app missing: {target}")
        try:
            if staged.stat().st_dev != target.stat().st_dev:
                return SwapOutcome(
                    False,
                    detail="staged bundle is on a different volume than the app; "
                    "an atomic swap is impossible (refusing to fall back to copy)",
                )
        except OSError as exc:
            return SwapOutcome(False, detail=f"cannot stat swap endpoints: {exc}")

        prior = self._prior_path(target, str(txid))
        if prior.exists():
            return SwapOutcome(False, detail=f"prior already exists for this transaction: {prior}")

        try:
            prior.mkdir(parents=False)  # reserve the name atomically
            prior.rmdir()
            os.rename(target, prior)
        except OSError as exc:
            return SwapOutcome(False, detail=f"could not move the current app aside: {exc}")
        try:
            os.rename(staged, target)
        except OSError as exc:
            # Undo: the previous version must be runnable the instant the swap fails.
            try:
                os.rename(prior, target)
            except OSError as undo_exc:
                logger.error("swap undo ALSO failed: %s (prior kept at %s)", undo_exc, prior)
            return SwapOutcome(False, detail=f"could not move the new app into place: {exc}")
        return SwapOutcome(True, prior_path=prior)

    def restore_prior(self, target_app: Path, prior_path: Path, *, txid: str) -> SwapOutcome:
        target = Path(target_app)
        prior = Path(prior_path)
        if not prior.exists():
            return SwapOutcome(False, detail=f"prior bundle missing: {prior}")
        displaced = self._prior_path(target, f"{txid}-displaced")
        if target.exists():
            os.rename(target, displaced)
        try:
            os.rename(prior, target)
        except OSError as exc:
            if displaced.exists():
                os.rename(displaced, target)  # never leave the slot empty
            return SwapOutcome(False, detail=f"could not restore the previous app: {exc}")
        if displaced.exists():
            shutil.rmtree(displaced, ignore_errors=True)
        return SwapOutcome(True, prior_path=prior)

    def prune_priors(self, target_app: Path, *, keep: int = 2) -> list[Path]:
        target = Path(target_app)
        priors = sorted(
            (p for p in target.parent.glob(f".{target.name}.prior-*") if p.is_dir()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        removed: list[Path] = []
        for stale in priors[max(0, int(keep)):]:
            shutil.rmtree(stale, ignore_errors=True)
            removed.append(stale)
        return removed


__all__ = ["ExternalScriptHelper", "MacOSBundleInstaller", "MacProcessHelper"]


def _pid_has_exited(pid: int) -> bool:
    """A pid is gone when the kernel no longer lists it OR it is a zombie (state Z):
    the parent may not have reaped it yet, but it is not running. Mirrors the bash
    helper's own wait logic so both sides agree."""
    if pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return True
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True, check=False
        )
        return out.returncode != 0 or out.stdout.strip().startswith("Z")
    except Exception:
        return False


class MacProcessHelper:
    """The production ExternalHelper for the macOS wrapper: stop the app via its
    pidfile (never killing an unrelated pid that reused the number), then relaunch
    detached via `open -n` (or an explicit command). Injectable seams stay open for
    tests; nothing here ever touches the installed app unless pointed at it."""

    def __init__(
        self,
        *,
        pid_file: Path | None = None,
        relaunch_cmd: list[str] | None = None,
        poll_interval: float = 0.2,
    ):
        self.pid_file = Path(pid_file) if pid_file else None
        self.relaunch_cmd = list(relaunch_cmd) if relaunch_cmd else []
        self.poll_interval = poll_interval

    def _read_pid(self) -> int:
        if self.pid_file is None or not self.pid_file.exists():
            return 0
        try:
            return int(self.pid_file.read_text(encoding="utf-8").strip() or "0")
        except (OSError, ValueError):
            return 0

    def shutdown_app(self) -> None:
        import os
        import signal

        pid = self._read_pid()
        if pid <= 0 or pid == os.getpid():
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError) as exc:
            logger.warning("helper shutdown could not signal pid %s: %s", pid, exc)

    def wait_until_stopped(self, *, timeout: float) -> bool:
        import time

        deadline = time.monotonic() + max(0.0, float(timeout))
        while time.monotonic() < deadline:
            if _pid_has_exited(self._read_pid()):
                return True
            time.sleep(self.poll_interval)
        return _pid_has_exited(self._read_pid())

    def relaunch(self, app_path: Path) -> bool:
        command = self.relaunch_cmd or ["open", "-n", str(app_path)]
        try:
            subprocess.Popen(command, start_new_session=True, close_fds=True)
            return True
        except OSError as exc:
            logger.error("helper relaunch failed: %s", exc)
            return False


class ExternalScriptHelper:
    """ExternalHelper implemented by the REAL bash helper script, out-of-process.

    This is the production handoff path: the script is spawned DETACHED (own session)
    BEFORE the target app is terminated, so it survives the app's death and performs
    wait-for-exit → atomic swap → relaunch on its own. The driving process (the daemon)
    is never the process being replaced when this helper targets a different app — and
    when the app IS the daemon's host, the daemon's own death cannot kill the swap
    either, because the script is not its child at the OS level (start_new_session).

    `performs_swap = True` tells the flow to delegate the swap instead of doing it
    in-process (the two would race otherwise).
    """

    performs_swap = True

    def __init__(
        self,
        *,
        script: Path,
        app_path: Path,
        target_pid: int | Callable[[], int],
        result_path: Path,
        relaunch_cmd: str = "",
        poll_interval: float = 0.2,
    ):
        self.script = Path(script)
        self.app_path = Path(app_path)
        self._target_pid = target_pid
        self.result_path = Path(result_path)
        self.relaunch_cmd = str(relaunch_cmd or "")
        self.poll_interval = poll_interval
        self._txid = ""
        self._staged = Path()
        self.process: subprocess.Popen | None = None

    def prepare(self, *, txid: str, staged_bundle: Path) -> None:
        """The flow hands over the transaction id + extracted bundle before shutdown."""
        self._txid = str(txid)
        self._staged = Path(staged_bundle)

    def _pid(self) -> int:
        value = self._target_pid() if callable(self._target_pid) else self._target_pid
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def shutdown_app(self) -> None:
        if not self.script.exists():
            raise FileNotFoundError(f"update helper script missing: {self.script}")
        command = [
            "bash",
            str(self.script),
            "--app",
            str(self.app_path),
            "--staged",
            str(self._staged),
            "--txid",
            str(self._txid),
            "--wait-seconds",
            "60",
            "--result",
            str(self.result_path),
        ]
        pid = self._pid()
        if pid > 0:
            command += ["--wait-pid", str(pid)]
        if self.relaunch_cmd:
            command += ["--relaunch-cmd", self.relaunch_cmd]
        self.result_path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            self.result_path.unlink(missing_ok=True)
        # Detached with its own session: NOT a child of the app being replaced.
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        if pid > 0 and pid != os.getpid():
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGTERM)

    def wait_until_stopped(self, *, timeout: float) -> bool:
        import time

        deadline = time.monotonic() + max(0.0, float(timeout))
        while time.monotonic() < deadline:
            if _pid_has_exited(self._pid()):
                return True
            time.sleep(self.poll_interval)
        return _pid_has_exited(self._pid())

    def stop_running_app(self, *, timeout: float = 10.0) -> bool:
        """Stop whoever CURRENTLY occupies the app seat (fresh pidfile read), without
        running the swap script. Rollback needs exactly this: shutdown_app() here
        would run the FULL external swap (it is the swap vehicle, not a stop), which
        during rollback re-swaps the just-restored prior for the rejected bundle —
        measured in the final-p1 journey."""
        pid = self._pid()
        if pid <= 0 or pid == os.getpid():
            return True
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.kill(pid, signal.SIGTERM)
        import time

        deadline = time.monotonic() + max(0.0, float(timeout))
        while time.monotonic() < deadline:
            if _pid_has_exited(pid):
                return True
            time.sleep(self.poll_interval)
        return _pid_has_exited(pid)

    def relaunch(self, app_path: Path) -> bool:
        # In the success path the SCRIPT relaunches (--relaunch-cmd) and the flow
        # never calls this. This is the ROLLBACK path's relauncher: bring the
        # previous version back up from this process (the daemon), detached.
        try:
            command = shlex.split(self.relaunch_cmd) if self.relaunch_cmd else ["open", "-n", str(app_path)]
            subprocess.Popen(command, start_new_session=True, close_fds=True)
            return True
        except OSError as exc:
            logger.error("external helper relaunch failed: %s", exc)
            return False

    def wait_for_result(self, *, timeout: float) -> tuple[bool, str]:
        """Poll the helper's result receipt (bounded)."""
        import json as _json
        import time

        deadline = time.monotonic() + max(0.0, float(timeout))
        while time.monotonic() < deadline:
            try:
                payload = _json.loads(self.result_path.read_text(encoding="utf-8"))
                return bool(payload.get("ok")), str(payload.get("detail") or "")
            except (OSError, ValueError):
                time.sleep(self.poll_interval)
        return False, "external helper did not report a result in time"
