"""Linux updates: installer-source checkouts (protected code swap) and the per-user package (version switch)."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from core.updater.macos import MacProcessHelper, _process_command
from core.updater.platforms import BundleVerification, SwapOutcome
from installer.self_update import backup_and_swap, compute_preserve_names, looks_like_vool_release, rollback_swap


class LinuxSourceInstaller:
    name = "linux-installed-source"

    def __init__(self, *, runtime_home: Path | None = None):
        if runtime_home is None:
            from core.runtime_paths import active_vool_home

            runtime_home = active_vool_home()
        self.runtime_home = Path(runtime_home)
        self.replace_full_code = False

    def verify_bundle(self, staged_bundle: Path, *, require_notarization=None) -> BundleVerification:
        if not looks_like_vool_release(staged_bundle):
            return BundleVerification(False, detail="payload is not an installer-source VOOL release")
        if not (staged_bundle / "installer" / "install_vool.sh").is_file():
            return BundleVerification(False, detail="payload has no Linux installer")
        return BundleVerification(True, detail="publisher-verified source payload")

    def atomic_swap(self, staged_bundle: Path, target_app: Path, *, txid: str) -> SwapOutcome:
        target = Path(target_app)
        prior = target / "update_backups" / f"signed-{txid}"
        try:
            receipt = json.loads((target / "install_receipt.json").read_text(encoding="utf-8"))
            if not isinstance(receipt, dict) or not receipt.get("runtime_home"):
                raise ValueError("invalid install receipt; update with git pull for a raw checkout")
            if Path(receipt["runtime_home"]).expanduser().resolve() != self.runtime_home.resolve():
                raise ValueError("install receipt and active runtime home disagree")
            if prior.exists():
                raise FileExistsError("code backup already exists for this transaction")
            preserve = compute_preserve_names(target, self.runtime_home)
            backup_and_swap(staged_bundle, target, preserve, prior, remove_absent=self.replace_full_code)
            return SwapOutcome(True, prior_path=prior)
        except Exception as exc:
            if prior.exists():
                try:
                    rollback_swap(target, prior)
                except Exception as restore:
                    return SwapOutcome(False, prior_path=prior, detail=f"code swap failed: {exc}; restore failed: {restore}")
            return SwapOutcome(False, prior_path=prior if prior.exists() else None, detail=f"code swap refused: {exc}")

    def restore_prior(self, target_app: Path, prior_path: Path, *, txid: str) -> SwapOutcome:
        try:
            if not (prior_path / "_replaced.json").is_file():
                raise ValueError("backup is not a Linux protected-code-swap journal")
            rollback_swap(target_app, prior_path)
            return SwapOutcome(True, prior_path=prior_path)
        except Exception as exc:
            return SwapOutcome(False, prior_path=prior_path, detail=str(exc))

    def prune_priors(self, target_app: Path, *, keep: int = 2) -> list[Path]:
        root = target_app / "update_backups"
        priors = sorted(root.glob("signed-*"), key=lambda p: p.stat().st_mtime, reverse=True)
        removed = []
        for prior in priors[max(1, keep):]:
            shutil.rmtree(prior)
            removed.append(prior)
        return removed


class LinuxProcessHelper(MacProcessHelper):
    """The installer-generated launcher owns env/venv/home; keep and execute it."""

    def __init__(self, *, app_path: Path, **kwargs):
        super().__init__(app_path=app_path, **kwargs)
        self.app_path = Path(app_path)
        self.lock_path = self.app_path / ".vool_updating.lock"

    def shutdown_app(self) -> None:
        # The installer's systemd/XDG launcher observes this before respawning.
        # It is protected by the SAME preserve contract as the environment.
        self.lock_path.write_text("signed update in progress\n", encoding="utf-8")
        super().shutdown_app()

    def relaunch(self, app_path: Path) -> bool:
        self.lock_path.unlink(missing_ok=True)
        self._launch_process = None  # a supervisor or systemd restart is not observed through an old handle
        if os.environ.get("VOOL_LAUNCHD_SUPERVISOR") == "1" or os.environ.get("VOOL_UPDATE_LAUNCHER_WAITING") == "1":
            return True  # the existing installer-owned supervisor resumes
        try:
            receipt = json.loads((self.app_path / "install_receipt.json").read_text())
        except (OSError, ValueError):
            receipt = {}
        service = str(receipt.get("launch_agent_path") or (receipt.get("launch_agent") or {}).get("macos") or "")
        if service.endswith("/vool-runtime.service") and shutil.which("systemctl"):
            current = subprocess.run(["systemctl", "--user", "show", "vool-runtime.service",
                                      "--property=WorkingDirectory", "--value"],
                                     capture_output=True, text=True, timeout=10, check=False)
            if current.returncode == 0 and Path(current.stdout.strip()).resolve() == self.app_path.resolve():
                restart = subprocess.run(["systemctl", "--user", "restart", "vool-runtime.service"],
                                         capture_output=True, text=True, timeout=30, check=False)
                return restart.returncode == 0
        self.relaunch_cmd = ["bash", str(Path(app_path) / "Start_VOOL.sh")]
        return super().relaunch(app_path)


def _bundle_installer_module():
    """The package installer that shipped with THIS running version (the trusted side of the check)."""
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "installer" / "bundle" / "linux" / "install_bundle.py"
    spec = importlib.util.spec_from_file_location("vool_linux_install_bundle", path)
    if spec is None or spec.loader is None:
        raise ValueError("the Linux package installer is missing from this version")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _package_version_dir(root: Path, candidate: Path) -> Path:
    """A package version folder inside this root's versions/, never a link or a stray path."""
    versions = (root / "versions").resolve()
    resolved = Path(candidate).resolve()
    if (resolved.parent != versions or Path(candidate).is_symlink() or not resolved.is_dir()
            or not re.fullmatch(r"[0-9a-f]{40}", resolved.name)
            or (resolved / "SOURCE_COMMIT").read_text(encoding="utf-8").strip() != resolved.name):
        raise ValueError(f"not an installed VOOL package version: {candidate}")
    return resolved


def _current_version_dir(root: Path) -> Path:
    current = root / "current"
    if not current.is_symlink():
        raise ValueError("the package has no current version link")
    return _package_version_dir(root, root / os.readlink(current))


class LinuxBundleInstaller:
    """Per-user Linux package updates: the signed payload is a whole package, installed beside the running one.

    The package's own install.sh (its stable updater hook) copies it into ``versions/<commit>`` and switches the
    ``current`` link in one rename. The previous version folder is never touched, so it is the backup: restoring
    it is the same one-rename switch back.
    """

    name = "linux-user-package"

    def __init__(self, *, runtime_home: Path | None = None):
        self.runtime_home = runtime_home
        self.replace_full_code = True

    def verify_bundle(self, staged_bundle: Path, *, require_notarization=None) -> BundleVerification:
        try:
            staged = Path(staged_bundle)
            if not (staged / "install.sh").is_file() or (staged / "install.sh").is_symlink():
                raise ValueError("payload has no package installer")
            commit = _bundle_installer_module().verify_package(staged)
        except Exception as exc:
            return BundleVerification(False, detail=f"payload is not a complete VOOL Linux package: {exc}")
        return BundleVerification(True, detail=f"publisher-verified Linux package {commit[:12]}")

    def atomic_swap(self, staged_bundle: Path, target_app: Path, *, txid: str) -> SwapOutcome:
        root = Path(target_app)
        staged = Path(staged_bundle)
        try:
            prior = _current_version_dir(root)
            home = root.parent.parent.parent
            if home / ".local" / "share" / "vool" != root:
                raise ValueError("the package is not at its per-user location")
            commit = (staged / "SOURCE_COMMIT").read_text(encoding="utf-8").strip()
            if commit == prior.name:
                raise ValueError("this package version is already the current one")
        except Exception as exc:
            return SwapOutcome(False, detail=f"package switch refused: {exc}")
        env = {key: value for key, value in os.environ.items()
               if key not in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONINSPECT")}
        env["HOME"] = str(home)
        try:
            result = subprocess.run(["bash", str(staged / "install.sh")], env=env, cwd=str(staged),
                                    capture_output=True, text=True, timeout=900, check=False)
            switched = _current_version_dir(root)
        except Exception as exc:
            result, switched = None, None
            detail = f"package install did not finish: {exc}"
        else:
            detail = (result.stderr or result.stdout or "").strip()[-400:]
        if result is not None and result.returncode == 0 and switched is not None and switched.name == commit:
            shutil.rmtree(staged, ignore_errors=True)
            return SwapOutcome(True, prior_path=prior)
        # The installer restores its own links on failure; make sure the previous version is current.
        restored = self.restore_prior(root, prior, txid=txid)
        if not restored.ok:
            return SwapOutcome(False, prior_path=prior, detail=f"package install failed: {detail}; restore failed: {restored.detail}")
        return SwapOutcome(False, prior_path=prior, detail=f"package install failed: {detail or 'no detail'}")

    def restore_prior(self, target_app: Path, prior_path: Path, *, txid: str) -> SwapOutcome:
        root = Path(target_app)
        lock = root / ".install-lock"
        try:
            prior = _package_version_dir(root, prior_path)
            lock.mkdir()
        except Exception as exc:
            return SwapOutcome(False, prior_path=Path(prior_path), detail=f"previous version cannot be restored: {exc}")
        try:
            link = root / f".restore-current-{os.getpid()}"
            link.unlink(missing_ok=True)
            link.symlink_to(Path("versions") / prior.name)
            os.replace(link, root / "current")
            return SwapOutcome(True, prior_path=prior)
        except Exception as exc:
            return SwapOutcome(False, prior_path=prior, detail=f"previous version could not be made current: {exc}")
        finally:
            lock.rmdir()

    def prune_priors(self, target_app: Path, *, keep: int = 2) -> list[Path]:
        root = Path(target_app)
        current = _current_version_dir(root)
        priors = []
        for path in (root / "versions").iterdir():
            try:
                version = _package_version_dir(root, path)
            except Exception:
                continue  # never remove anything the package installer did not lay down
            if version != current:
                priors.append(version)
        priors.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        removed = []
        for prior in priors[max(1, keep):]:
            shutil.rmtree(prior)
            removed.append(prior)
        return removed


class LinuxBundleHelper(MacProcessHelper):
    """Stops the package's API server and starts the switched package again, headless, on the same port."""

    def __init__(self, *, app_path: Path, pid_file: Path, **kwargs):
        root = Path(app_path)
        super().__init__(app_path=root, pid_file=pid_file,
                         relaunch_cmd=[str(root / "current" / "vool"), "--headless"], **kwargs)

    def _read_pid(self) -> int:
        if self.pid_file is None or not self.pid_file.exists():
            return 0
        try:
            pid = int(self.pid_file.read_text(encoding="utf-8").strip() or "0")
        except (OSError, ValueError):
            return 0
        if pid <= 1:
            return 0
        command = _process_command(pid)
        if command is None or "-m apps.vool_api_server" not in command:
            return 0
        try:
            cwd = Path(f"/proc/{pid}/cwd").resolve()
        except OSError:
            return 0
        versions = (Path(self.app_path) / "versions").resolve()
        return pid if cwd.parent.parent == versions and cwd.name == "app" else 0
