"""v0.7 payload/data contracts, including malicious archive and restore failures."""
from __future__ import annotations

import os
import shutil
import sqlite3
import stat
import zipfile
from contextlib import nullcontext

import pytest

from core.updater.archive import extract_update_zip
from core.updater.service import UpdateCheckService, UpdateController, UserGesture
from core.updater.snapshot import restore_runtime, snapshot_runtime
from core.updater.status import UpdatePhase
from core.updater.trust import TrustedPublishers
from installer.build_update_payload import write_tree_zip

from .helpers import build_manifest, generate_publisher_keypair, manifest_bytes


def _entry(archive, name, body, mode=stat.S_IFREG | 0o644):
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = mode << 16
    archive.writestr(info, body)


def test_payload_keeps_relative_framework_and_executable_links(tmp_path):
    payload = tmp_path / "payload.zip"
    with zipfile.ZipFile(payload, "w") as archive:
        _entry(archive, "VOOL.app/Contents/MacOS/actual", b"executable", stat.S_IFREG | 0o755)
        _entry(archive, "VOOL.app/Contents/MacOS/VOOL", b"actual", stat.S_IFLNK | 0o777)
        _entry(archive, "VOOL.app/Contents/Frameworks/F/Versions/A/file", b"framework")
        _entry(archive, "VOOL.app/Contents/Frameworks/F/Versions/Current", b"A", stat.S_IFLNK | 0o777)
    bundle = extract_update_zip(payload, tmp_path / "stage")
    assert (bundle / "Contents/MacOS/VOOL").is_symlink()
    assert (bundle / "Contents/MacOS/VOOL").read_bytes() == b"executable"
    assert os.access(bundle / "Contents/MacOS/VOOL", os.X_OK)
    assert (bundle / "Contents/Frameworks/F/Versions/Current/file").read_bytes() == b"framework"


@pytest.mark.parametrize("kind", ["traversal", "absolute", "link_escape", "link_child", "duplicate", "case_collision", "device", "setuid"])
def test_payload_refuses_unsafe_names_before_writing(tmp_path, kind):
    payload = tmp_path / "payload.zip"
    with zipfile.ZipFile(payload, "w") as archive:
        if kind in ("traversal", "absolute"):
            _entry(archive, "../escaped" if kind == "traversal" else "/escaped", b"bad")
        elif kind == "link_escape":
            _entry(archive, "link", b"../escaped", stat.S_IFLNK | 0o777)
        elif kind == "link_child":
            _entry(archive, "link", b"inside", stat.S_IFLNK | 0o777)
            _entry(archive, "link/file", b"bad")
        elif kind in ("duplicate", "case_collision"):
            _entry(archive, "file", b"first")
            with pytest.warns(UserWarning) if kind == "duplicate" else nullcontext():
                _entry(archive, "file" if kind == "duplicate" else "FILE", b"second")
        else:
            _entry(archive, "file", b"bad", stat.S_IFCHR | 0o644 if kind == "device" else stat.S_IFREG | 0o4755)
    with pytest.raises(ValueError):
        extract_update_zip(payload, tmp_path / "stage")
    assert not (tmp_path / "escaped").exists()
    assert list((tmp_path / "stage").iterdir()) == []


def test_snapshot_captures_uncheckpointed_sqlite_and_home_config(tmp_path):
    home = tmp_path / "home"
    data = home / "data"
    data.mkdir(parents=True)
    config = home / "config"
    config.mkdir()
    (config / "profile.json").write_text('{"name":"seed"}')
    database = data / "vool_web0_v2.db"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE memory(value TEXT)")
    connection.execute("INSERT INTO memory VALUES ('retained')")
    connection.commit()
    snapshot = data / "update_v2" / "snapshot"
    snapshot_runtime(data, snapshot)
    connection.execute("DELETE FROM memory")
    connection.commit()
    connection.close()
    (config / "profile.json").write_text('{"name":"changed"}')
    restore_runtime(data, snapshot)
    with sqlite3.connect(database) as restored:
        assert restored.execute("SELECT value FROM memory").fetchall() == [("retained",)]
        assert restored.execute("PRAGMA quick_check").fetchone() == ("ok",)
    assert (config / "profile.json").read_text() == '{"name":"seed"}'
    assert (snapshot / "data/vool_web0_v2.db").exists()


def test_missing_snapshot_is_a_restore_failure(tmp_path):
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    (home / "config").mkdir()
    (home / "config/profile.json").write_text("original")
    snapshot = tmp_path / "snapshot"
    snapshot_runtime(home / "data", snapshot)
    shutil.rmtree(snapshot / "config")
    with pytest.raises(FileNotFoundError):
        restore_runtime(home / "data", snapshot)
    assert (home / "config/profile.json").read_text() == "original"


@pytest.mark.parametrize("local_platform", ["macos-x64", "macos-arm64", "linux-x64"])
@pytest.mark.parametrize("published_platform", ["macos-x64", "macos-arm64", "linux-x64"])
def test_offer_and_install_require_exact_os_and_architecture(tmp_path, local_platform, published_platform):
    private, public = generate_publisher_keypair()
    document, _ = build_manifest(private_hex=private, version="0.7.1", channel="beta",
                                artifacts={published_platform: b"exact-platform"})
    service = UpdateCheckService(manifest_url="file-mock", trust=TrustedPublishers({"release-2026-01": public}),
                                 installed_version="0.7.0", channel="beta", platform=local_platform,
                                 data_dir=tmp_path / "data", fetch=lambda _: manifest_bytes(document))
    outcome = service.check_once()
    assert outcome.ok
    if local_platform == published_platform:
        assert service.current_offer().available
        assert outcome.decision.artifact.url.endswith(published_platform + ".zip")
        assert service.status.load().phase is UpdatePhase.READY
    else:
        assert not outcome.decision.should_install
        assert outcome.decision.artifact is None
        assert service.current_offer() is None
        assert service.status.load().phase is UpdatePhase.UNAVAILABLE
        assert "no download for " + local_platform in service.status.load().message
        calls = []
        controller = UpdateController(offer_loader=service.current_offer, flow_factory=lambda: calls.append(True))
        assert not controller.press_update(UserGesture(1.0)).ok
        assert calls == []


@pytest.mark.parametrize("failure", ["network", "bad_signature", "older", "equal", "other_platform", "unpinned"])
def test_refusal_clears_a_previous_offer_and_reports_why(tmp_path, failure):
    private, public = generate_publisher_keypair()
    document, _ = build_manifest(private_hex=private, version="0.7.1", channel="beta")
    service = UpdateCheckService(manifest_url="file-mock", trust=TrustedPublishers({"release-2026-01": public}),
                                 installed_version="0.7.0", channel="beta", platform="macos-arm64",
                                 data_dir=tmp_path / "data", fetch=lambda _: manifest_bytes(document))
    assert service.check_once().decision.should_install
    version = {"older": "0.6.0", "equal": "0.7.0"}.get(failure, "0.7.1")
    candidate, _ = build_manifest(private_hex=private, version=version, channel="beta", sequence=48,
                                  artifacts={"linux-x64" if failure == "other_platform" else "macos-arm64": b"new"})
    if failure == "network":
        def unavailable(_):
            raise OSError("network unavailable")
        service._fetch = unavailable
    else:
        if failure == "bad_signature":
            candidate["notes"] = "modified after signing"
        if failure == "unpinned":
            service.trust = TrustedPublishers({})
        service._fetch = lambda _: manifest_bytes(candidate)
    service.check_once()
    assert service.current_offer() is None
    status = service.status.load()
    assert status.phase is (UpdatePhase.UP_TO_DATE if failure == "equal" else
                            UpdatePhase.UNAVAILABLE if failure in ("older", "other_platform") else UpdatePhase.FAILED)
    assert status.message and "No update activity" not in status.message


def test_payload_builder_round_trip_preserves_links_and_modes(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "program").write_bytes(b"signed executable")
    (source / "program").chmod(0o755)
    (source / "launcher").symlink_to("program")
    payload = tmp_path / "payload.zip"
    write_tree_zip(source, payload, prefix="VOOL.app")
    extracted = extract_update_zip(payload, tmp_path / "extracted")
    assert (extracted / "launcher").is_symlink()
    assert (extracted / "launcher").read_bytes() == b"signed executable"
    assert os.access(extracted / "launcher", os.X_OK)


def test_migration_snapshot_restore_failure_is_not_claimed_as_success(tmp_path, monkeypatch):
    from core.updater import migrations
    catalog = migrations.MigrationCatalog()
    home = tmp_path / "home"
    home.mkdir()
    (home / "profile").write_text("original")
    def failing_step(context):
        (context.user_home / "profile").write_text("changed")
        raise OSError("migration failed")
    def failing_restore(*args):
        raise OSError("restore denied")
    catalog.register(migrations.MigrationStep("test", "0.7.0", "0.7.1", ("profile",), failing_step))
    monkeypatch.setattr(migrations, "restore_tree", failing_restore)
    result = migrations.run_migrations(catalog, from_version="0.7.0", to_version="0.7.1",
                                       user_home=home, snapshot_root=tmp_path / "snapshot")
    assert result.reason is migrations.MigrationReason.RESTORE_FAILED
    assert "recovery is incomplete" in result.plain_message
    assert "restore denied" in result.detail
    assert (tmp_path / "snapshot/step-00-test/profile").read_text() == "original"


@pytest.mark.parametrize("destination", [
    "https://release-assets.githubusercontent.com/asset?signature=fixture",
    "https://evil.invalid/asset", "http://release-assets.githubusercontent.com/asset",
    "https://github.com/another/repo/releases/asset", "https://release-assets.githubusercontent.com:444/asset",
])
def test_github_redirects_reenter_the_policy_door_and_stay_closed(monkeypatch, destination):
    from urllib.error import HTTPError

    from core import remote_fetch_policy
    from core.updater.download import PolicyRefusalError, _open_release_url

    initial = "https://github.com/Parad0x-Labs/vool/releases/latest/download/manifest.json"
    calls = []
    response = object()

    def door(url, **kwargs):
        calls.append((url, kwargs))
        if len(calls) == 1:
            raise HTTPError(url, 302, "redirect", {"Location": destination}, None)
        return response

    monkeypatch.setattr(remote_fetch_policy, "open_remote_url", door)
    if destination.startswith("https://release-assets.githubusercontent.com/asset?"):
        assert _open_release_url(initial, headers={}, timeout=3) is response
        assert [url for url, _ in calls] == [initial, destination]
    else:
        with pytest.raises(PolicyRefusalError):
            _open_release_url(initial, headers={}, timeout=3)
        assert [url for url, _ in calls] == [initial]
    assert all(options["redirect_policy"] == "refuse" for _, options in calls)


def test_partial_linux_copy_failure_restores_old_and_removes_new_entries(tmp_path, monkeypatch):
    from installer import self_update

    target, staged, backup = (tmp_path / name for name in ("target", "staged", "backup"))
    target.mkdir()
    staged.mkdir()
    (target / "a-old").write_text("old")
    (staged / "a-old").write_text("replacement")
    (staged / "z-new").write_text("new")
    original = self_update._copy_into

    def failing_copy(source, destination):
        original(source, destination)
        if source.name == "z-new":
            raise OSError("injected partial copy failure")

    monkeypatch.setattr(self_update, "_copy_into", failing_copy)
    with pytest.raises(OSError, match="partial copy"):
        self_update.backup_and_swap(staged, target, set(), backup)
    self_update.rollback_swap(target, backup)
    assert (target / "a-old").read_text() == "old"
    assert not (target / "z-new").exists()
    self_update.rollback_swap(target, backup)
    assert (target / "a-old").read_text() == "old"


def test_worker_pruning_keeps_active_and_incomplete_jobs(tmp_path, monkeypatch):
    import json

    from core.updater import worker
    from core.updater.state import UpdaterPaths
    from core.updater.transaction import Step, UpdateJournal

    paths = UpdaterPaths.for_data_dir(tmp_path)
    for name, pid, terminal in (("finished", 11, True), ("active", 12, True), ("incomplete", 13, False)):
        job = paths.root / "workers" / name
        (job / "code").mkdir(parents=True)
        (job / "python").mkdir()
        (job / "worker.log").write_text("retained evidence")
        (job / "process.json").write_text(json.dumps({"pid": pid}))
        UpdateJournal(paths.transaction_dir(name)).append(Step.FINALIZED if terminal else Step.SWAPPED)
    monkeypatch.setattr(worker, "_pid_has_exited", lambda pid: pid != 12)
    worker.prune_finished_workers(tmp_path)
    for name in ("active", "incomplete"):
        assert (paths.root / "workers" / name / "code").exists()
    assert not (paths.root / "workers/finished/code").exists()
    assert not (paths.root / "workers/finished/python").exists()
    assert (paths.root / "workers/finished/worker.log").read_text() == "retained evidence"
