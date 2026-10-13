"""Registry packages use the existing native lifecycle, permissions and skill loader."""
from __future__ import annotations

import dataclasses
import os
import re
import sqlite3
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from core.plugin_package import capture_package, execution_directory
from core.registry.artifact import inspect, materialize
from core.registry.codec import RegistryError, canonical, decode, require
from core.registry.trust import verify

_INSTALL_LOCK = threading.RLock()
STATE_NAME = "registry_trust.sqlite"


def home() -> Path:
    from core.runtime_paths import user_runtime_default

    return Path(os.environ.get("VOOL_HOME") or os.environ.get("NULLA_HOME") or user_runtime_default())


def _receipt_path(plugin_id: str) -> Path:
    from core.registry.codec import IDENTITY

    require(isinstance(plugin_id, str) and bool(IDENTITY.fullmatch(plugin_id)), "invalid registry plugin identity")
    return home() / "config" / "registry_receipts" / (plugin_id + ".json")


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".registry-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def runtime_allowed(record) -> bool:
    """Offline installs keep working; known revocations and removed signing keys block use."""
    if not str(record.source).startswith("registry:"):
        return True
    try:
        receipt = decode(_receipt_path(record.plugin_id).read_bytes(), 65536)
        state = home() / "config" / STATE_NAME
        db = sqlite3.connect(state.as_uri() + "?mode=ro", uri=True)
        try:
            row = db.execute("SELECT root FROM trust WHERE id=1").fetchone()
            require(row, "registry trust state missing")
            root = decode(row[0])["signed"]
            entry = verify(receipt["admission"], root, "admission")
            revoked = db.execute("SELECT 1 FROM revoked WHERE digest=?", (entry["artifact_sha256"],)).fetchone()
        finally:
            db.close()
        return not revoked and record.source == "registry:" + entry["metadata"]["id"] and \
            record.verified_digest == entry["native_digest"] and record.verified_dependencies == entry["dependencies"]
    except (RegistryError, OSError, sqlite3.Error, KeyError, TypeError):
        return False


@contextmanager
def _filesystem_lock(root: Path):
    import fcntl

    scratch = root / ".registry-scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    with (scratch / "install.lock").open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield scratch
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _journal_path(plugin_id: str) -> Path:
    return _receipt_path(plugin_id).parent.parent / "registry_transactions" / (plugin_id + ".json")


def recover(plugin_id: str, root: Path) -> None:
    """Roll back an interrupted owned transaction; refuse unexpected or edited files."""
    from core import plugin_catalog, plugin_lifecycle

    journal_path = _journal_path(plugin_id)
    if not journal_path.exists():
        return
    journal = decode(journal_path.read_bytes(), 65536)
    require(journal.get("schema") == "vool.registry.transaction.v1" and journal.get("plugin_id") == plugin_id and
            journal.get("root") == str(root.resolve()) and
            bool(re.fullmatch(r"invocation-[A-Za-z0-9_-]+", str(journal.get("owned")))), "transaction recovery identity mismatch")
    owned = root / ".registry-scratch" / journal["owned"]
    require(owned.is_dir() and not owned.is_symlink(), "owned transaction scratch is missing")
    require({path.name for path in owned.iterdir()} <= {"package", "previous", "failed"}, "unexpected data in recovery scratch")
    destination, backup = root / "plugins" / plugin_id, owned / "previous"
    previous = plugin_lifecycle.PluginRecord(**journal["previous"]) if journal["previous"] else None
    if previous:
        require(previous.plugin_id == plugin_id and Path(previous.root).resolve() == destination.resolve(),
                "previous installation identity mismatch")
    current = plugin_lifecycle.record_for(plugin_id)
    require(current is None or current.source == journal["source"], "a different installation owns this identity")
    if destination.exists():
        actual = capture_package(destination).digest
        if actual == journal["new_digest"]:
            require(not (owned / "failed").exists(), "recovery would overwrite unexpected scratch")
            destination.rename(owned / "failed")
        else:
            require(previous and actual == previous.verified_digest and not backup.exists(),
                    "installed files changed; recovery will not overwrite them")
    if backup.exists():
        require(previous and capture_package(backup).digest == previous.verified_digest, "previous package changed during recovery")
        backup.rename(destination)
    if previous:
        require(destination.exists(), "previous package is missing")
        plugin_lifecycle._save(previous)
    else:
        current = plugin_lifecycle.record_for(plugin_id)
        if current:
            require(current.source == journal["source"], "a different installation owns this identity")
            plugin_lifecycle.uninstall(plugin_id)
    receipt = _receipt_path(plugin_id)
    if journal["receipt"]:
        _write(receipt, journal["receipt"])
    elif receipt.exists():
        receipt.unlink()
    require(plugin_catalog.set_plugin_enabled(plugin_id, not journal["was_disabled"]), "enabled state recovery failed")
    journal_path.unlink()
    # Only the invocation named by our durable journal is removed.
    import shutil

    for directory, folders, _ in os.walk(owned, followlinks=False):
        Path(directory).chmod(0o700)
        for name in folders:
            child = Path(directory) / name
            if not child.is_symlink():
                child.chmod(0o700)
    shutil.rmtree(owned)
    plugin_catalog.invalidate_storage_listing()


def install(catalog, raw: bytes, *, package_id: str, version: str, target: dict,
            accepted: bool, enable: bool, now: int) -> dict:
    """Explicit owner consent. Public review cannot grant a runtime permission."""
    from core import plugin_catalog, plugin_lifecycle
    from core.registry.validation import manifest_authority, native_format_findings

    require(accepted is True and type(enable) is bool, "explicit local installation consent is required")
    entry = catalog.resolve(package_id, version, target=target, now=now)
    artifact = inspect(raw)
    authority = manifest_authority(artifact["manifest"])
    from core.registry.rules import analyze

    require(not analyze(artifact["files"])["conflicts"], "instructions cannot override VOOL native controls")
    require(artifact["sha256"] == entry["artifact_sha256"] and len(raw) == entry["artifact_length"] and
            artifact["native_digest"] == entry["native_digest"] and artifact["metadata"] == entry["metadata"] and
            artifact["manifest"]["name"] == entry["plugin_id"], "download differs from signed admission")
    plugin_id = entry["plugin_id"]
    from core.registry.artifact import native_identity

    require(plugin_id == native_identity(package_id), "native plugin identity is not bound to the package id")
    # Only the application-owned state path may authorize the runtime predicate.
    require(Path(catalog.db.execute("PRAGMA database_list").fetchone()[2]).resolve() ==
            (home() / "config" / STATE_NAME).resolve(), "client trust state belongs to another runtime")
    root = plugin_catalog.configured_plugins_root()
    destination = root / "plugins" / plugin_id
    with _INSTALL_LOCK, plugin_lifecycle._lock, _filesystem_lock(root) as scratch, execution_directory(scratch) as owned:
        snapshot = owned / "package"
        materialize(raw, snapshot)
        # This parser validates declarations and creates no subprocess. Candidate
        # contracts inform validation only; they are never registered or granted.
        findings = native_format_findings(snapshot, artifact)
        require(not findings, "native skill validation failed: " + "; ".join(
            item["path"] + ": " + item["reason"] for item in findings))
        recover(plugin_id, root)
        for dependency, pin in entry["dependencies"].items():
            child = plugin_lifecycle.record_for(dependency)
            require(child and plugin_lifecycle.is_available(dependency) and child.approval_digest == pin,
                    "approved dependency closure is not installed and available")
        previous = plugin_lifecycle.record_for(plugin_id)
        receipt_path = _receipt_path(plugin_id)
        old_receipt = decode(receipt_path.read_bytes()) if receipt_path.exists() else None
        if destination.exists():
            require(previous and previous.source == "registry:" + package_id and old_receipt,
                    "installation would overwrite an existing local package")
            require(capture_package(destination).digest == previous.verified_digest, "existing package was edited; preserve it before updating")
        else:
            require(previous is None or previous.stage == plugin_lifecycle.STAGE_UNINSTALLED,
                    "plugin identity already belongs to another installation")
        destination.parent.mkdir(parents=True, exist_ok=True)
        backup = owned / "previous"
        moved = False
        journal_path = _journal_path(plugin_id)
        _write(journal_path, {"schema": "vool.registry.transaction.v1", "plugin_id": plugin_id,
            "root": str(root.resolve()), "owned": owned.name, "source": "registry:" + package_id,
            "new_digest": entry["native_digest"], "previous": dataclasses.asdict(previous) if previous else None,
            "receipt": old_receipt, "was_disabled": plugin_id in plugin_catalog._disabled_ids()})
        try:
            if destination.exists():
                destination.rename(backup)
            snapshot.rename(destination)
            moved = True
            record = plugin_lifecycle.install(plugin_id, root=destination, source="registry:" + package_id)
            record = plugin_lifecycle.verify(plugin_id, expected_digest=entry["native_digest"])
            require(record.verified_dependencies == entry["dependencies"], "native dependency approvals differ from admission")
            envelope = next(envelope for envelope in catalog.current["entries"]
                            if envelope["signed"] == entry)
            _write(receipt_path, {"schema": "vool.registry.install.v1", "admission": envelope,
                                  "accepted_at": now, "root_version": catalog.root["version"]})
            if enable:
                record = plugin_lifecycle.enable(plugin_id)
            require(plugin_catalog.set_plugin_enabled(plugin_id, enable), "plugin enabled-state could not be stored")
            plugin_catalog.invalidate_storage_listing()
            journal_path.unlink()
            return {"status": "installed", "id": package_id, "version": version, "plugin_id": plugin_id,
                    "enabled": record.enabled, "native_digest": record.verified_digest,
                    **authority, "note": "Runtime permission checks still apply."}
        except Exception:
            if moved and destination.exists():
                destination.rename(owned / "failed")
            if backup.exists():
                backup.rename(destination)
            if previous is not None:
                plugin_lifecycle._save(previous)
            else:
                failed = plugin_lifecycle.record_for(plugin_id)
                if failed is not None:
                    plugin_lifecycle.uninstall(plugin_id)
            if old_receipt is not None:
                _write(receipt_path, old_receipt)
            elif receipt_path.exists():
                receipt_path.unlink()
            journal = decode(journal_path.read_bytes())
            plugin_catalog.set_plugin_enabled(plugin_id, not journal["was_disabled"])
            journal_path.unlink()
            plugin_catalog.invalidate_storage_listing()
            raise


def preview(catalog, raw: bytes, *, package_id: str, version: str, target: dict, now: int) -> dict:
    from core.registry.validation import manifest_authority

    entry = catalog.resolve(package_id, version, target=target, now=now)
    value = inspect(raw)
    from core.registry.rules import analyze, notices

    boundaries = analyze(value["files"])
    boundaries["native_control_overlaps"] = notices(
        set(entry["native_rule_review"]["overlaps"]) | {item["control"] for item in boundaries["native_control_overlaps"]})
    require(value["sha256"] == entry["artifact_sha256"] and len(raw) == entry["artifact_length"], "preview input differs from admission")
    return {"id": package_id, "version": version, "sha256": value["sha256"], "metadata": value["metadata"], **boundaries,
            **manifest_authority(value["manifest"]), "dependencies": entry["dependencies"],
            "instructions": {name: data.decode("utf-8") for name, data in value["files"].items() if name.endswith("/SKILL.md")},
            "files": [{key: val for key, val in item.items() if key != "content"} for item in value["entries"]],
            "reviewed": True, "installed": False, "enabled": False}
