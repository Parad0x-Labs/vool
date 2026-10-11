"""Installer-source Linux updates using the existing protected code-swap helpers."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from core.updater.macos import MacProcessHelper
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
