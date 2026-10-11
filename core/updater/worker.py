"""Detached continuation of a prepared, signed transaction across app shutdown."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

from core.updater.macos import _pid_has_exited
from core.updater.state import UpdaterPaths, _atomic_write_json, _read_json


def prune_finished_workers(data_dir: Path) -> None:
    """Retain journals/logs and incomplete recoveries; release dead frozen runtimes."""
    from core.updater.transaction import UpdateJournal

    paths = UpdaterPaths.for_data_dir(data_dir)
    root = paths.root / "workers"
    for job in root.iterdir() if root.is_dir() else ():
        if job.is_symlink() or not job.is_dir():
            continue
        lease = _read_json(job / "process.json")
        if not isinstance(lease, dict) or int(lease.get("pid") or 0) <= 1:
            continue
        if not _pid_has_exited(int(lease["pid"])):
            continue
        if not UpdateJournal(paths.transaction_dir(job.name)).is_terminal():
            continue
        for name in ("code", "python"):
            directory = job / name
            if directory.is_dir() and not directory.is_symlink():
                shutil.rmtree(directory)


def worker_alive(data_dir: Path) -> bool:
    lease = _read_json(UpdaterPaths.for_data_dir(data_dir).root / "worker.json")
    if not isinstance(lease, dict):
        return False
    pid = int(lease.get("pid") or 0)
    marker = str(lease.get("plan") or "")
    if pid <= 1 or not marker or _pid_has_exited(pid):
        return False
    # A stale/reused pid is insufficient. It must still run this exact plan.
    command = subprocess.run(["ps", "-p", str(pid), "-o", "args="],
                             capture_output=True, text=True, check=False)
    return command.returncode == 0 and marker in command.stdout


def start_worker(subsystem, offer, txid: str) -> subprocess.Popen:
    """Freeze code (and a bundled interpreter) OUTSIDE the replacement target.

    No private publisher material or user profile is copied. Parent already owns
    the work gate, active-state receipt, DB/config snapshot and signed download.
    The child re-verifies both signatures before proceeding with that journal.
    """
    paths = subsystem.paths
    prune_finished_workers(subsystem.data_dir)
    root = paths.root / "workers" / txid
    root.mkdir(parents=True, exist_ok=False)
    os.chmod(root, 0o700)
    code = root / "code"
    source = Path(__file__).resolve().parents[2]
    for name in ("core", "installer", "storage", "network", "config"):
        shutil.copytree(source / name, code / name, symlinks=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    executable = Path(sys.executable).resolve()
    app = Path(subsystem.feed.app_path).resolve()
    if executable.is_relative_to(app):
        runtime = app / "Contents/Resources/python"
        if not executable.is_relative_to(runtime):
            raise ValueError("bundled updater interpreter is outside the shipped runtime")
        shutil.copytree(runtime, root / "python", symlinks=True)
        executable = root / "python" / executable.relative_to(runtime)
    feed = subsystem.feed
    if feed.helper_script:
        frozen_helper = root / "mac_update_helper.sh"
        shutil.copy2(feed.helper_script, frozen_helper)
        feed = replace(feed, helper_script=str(frozen_helper))
    plan = root / "plan.json"
    _atomic_write_json(plan, {
        "txid": txid, "feed": asdict(feed), "data_dir": str(subsystem.data_dir),
        "installed_version": subsystem.installed_version, "platform": subsystem.platform,
        "health_timeout": subsystem.health_timeout,
        "notarization_waiver_reason": subsystem.notarization_waiver_reason,
        "keys": {key: subsystem.trust.public_key_hex(key) for key in subsystem.trust.key_ids},
    })
    (root / "manifest.json").write_bytes(offer.raw)
    env = dict(os.environ, PYTHONPATH=str(code), PYTHONDONTWRITEBYTECODE="1",
               VOOL_HOME=str(subsystem.data_dir.resolve().parent))
    log = root / "worker.log"
    with log.open("ab") as output:
        process = subprocess.Popen([str(executable), "-m", "core.updater.worker", str(plan)],
                                   cwd=code, env=env, stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=output, start_new_session=True, close_fds=True)
    lease = {
        "pid": process.pid, "plan": str(plan), "txid": txid, "started_at": time.time(),
    }
    _atomic_write_json(root / "process.json", lease)
    _atomic_write_json(paths.root / "worker.json", lease)
    return process


def main() -> int:
    from core.updater.decision import decide_update
    from core.updater.feed import FeedConfig
    from core.updater.manifest import parse_and_verify_manifest
    from core.updater.runtime import UpdateSubsystem
    from core.updater.status import UpdateFault, UpdatePhase
    from core.updater.trust import TrustedPublishers

    plan = Path(sys.argv[1])
    params = json.loads(plan.read_text(encoding="utf-8"))
    subsystem = UpdateSubsystem(feed=FeedConfig(**params["feed"]),
                                trust=TrustedPublishers(params["keys"]),
                                data_dir=Path(params["data_dir"]),
                                installed_version=params["installed_version"], platform=params["platform"],
                                notarization_waiver_reason=params["notarization_waiver_reason"],
                                health_timeout=params["health_timeout"])
    try:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            lease = _read_json(subsystem.paths.root / "worker.json") or {}
            if lease.get("txid") == params["txid"] and lease.get("pid") == os.getpid():
                break
            time.sleep(.02)
        else:
            raise ValueError("the parent did not publish the detached transaction lease; nothing was installed")
        raw = plan.with_name("manifest.json").read_bytes()
        verification = parse_and_verify_manifest(raw, subsystem.trust)
        if not verification.ok or verification.manifest is None:
            raise ValueError("prepared manifest failed detached re-verification")
        manifest = verification.manifest
        decision = decide_update(manifest, installed_version=subsystem.installed_version,
                                 channel=subsystem.feed.channel, platform_key=subsystem.platform,
                                 high_water=subsystem.high_water.load(subsystem.feed.channel),
                                 installer_available=subsystem.make_flow().installer is not None)
        if not decision.should_install:
            raise ValueError(f"prepared update no longer applies: {decision.reason.value}")
        result = subsystem.make_flow().resume(params["txid"], raw, manifest, decision)
        print(json.dumps({"ok": result.ok, "step": result.terminal.value,
                          "detail": result.detail, "txid": result.txid}), flush=True)
        return 0 if result.ok else 1
    except Exception as exc:
        subsystem.status.publish(UpdatePhase.FAILED, detail=f"detached update failed: {exc}",
                                 fault=UpdateFault.INSTALL_FAILED)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
