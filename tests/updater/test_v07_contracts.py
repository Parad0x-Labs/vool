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


@pytest.mark.parametrize("fault", ["install_failed", "health_check_failed", "rollback_failed"])
def test_background_checks_preserve_update_failure_until_manual_check(tmp_path, fault):
    from core.updater.status import UpdateFault

    private, public = generate_publisher_keypair()
    document, _ = build_manifest(private_hex=private, version="0.7.1", channel="beta")
    calls = []
    service = UpdateCheckService(manifest_url="fixture", trust=TrustedPublishers({"release-2026-01": public}),
                                 installed_version="0.7.0", channel="beta", platform="macos-arm64", data_dir=tmp_path,
                                 fetch=lambda _: (calls.append(True), manifest_bytes(document))[1])
    service.status.publish(UpdatePhase.ROLLED_BACK if fault == "health_check_failed" else UpdatePhase.FAILED,
                           previous_version="0.7.0", detail="injected update failure", fault=UpdateFault(fault))
    assert not service.check_once(preserve_failure=True).ok
    assert calls == [] and service.current_offer() is None
    assert service.status.load().fault_code == fault
    manual = service.check_once()
    if fault == "rollback_failed":
        assert not manual.ok and calls == []
        assert service.status.load().fault_code == fault
    else:
        assert manual.ok and service.status.load().phase is UpdatePhase.READY
        assert calls == [True]


def test_startup_recovery_that_can_stop_its_host_is_detached(tmp_path, monkeypatch):
    from core.updater import worker
    from core.updater.feed import FeedConfig
    from core.updater.runtime import UpdateSubsystem
    from core.updater.transaction import Step, UpdateJournal

    _, public = generate_publisher_keypair()
    subsystem = UpdateSubsystem(feed=FeedConfig(manifest_url="fixture", app_path=str(tmp_path / "VOOL.app")),
                                trust=TrustedPublishers({"test": public}), data_dir=tmp_path / "data", platform="macos-arm64")
    UpdateJournal(subsystem.paths.transaction_dir("interrupted")).append(Step.SWAPPED)
    calls = []
    monkeypatch.setattr(subsystem, "_worker_alive", lambda: False)
    monkeypatch.setattr(worker, "start_worker", lambda instance, offer, txid, **kwargs: calls.append((offer, txid, kwargs)))
    monkeypatch.setattr(subsystem, "_make_helper", lambda: pytest.fail("recovery must not stop its own host in process"))
    subsystem._recover_bounded()
    assert len(calls) == 1 and calls[0][0] is None
    assert calls[0][1].startswith("recovery-") and calls[0][2] == {"recovery": True}


@pytest.mark.parametrize("membership", ["vool-runtime.service", "another.service"])
def test_service_hosted_worker_uses_its_own_scope_without_changing_other_services(tmp_path, monkeypatch, membership):
    import subprocess

    from core.updater import worker

    cgroup = tmp_path / "cgroup"
    cgroup.write_text("0::/user.slice/app.slice/" + membership + "\n")
    monkeypatch.setattr(worker.shutil, "which", lambda _: "/bin/systemd-run")
    monkeypatch.setattr(worker.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 0, "--expand-environment=BOOL", ""))
    command = worker._systemd_scope_prefix("owned-transaction", platform="linux", cgroup_path=cgroup)
    if membership == "vool-runtime.service":
        assert command[:5] == ["/bin/systemd-run", "--user", "--scope", "--quiet", "--collect"]
        assert command[-2:] == ["--expand-environment=no", "--"]
        assert command[5].startswith("--unit=vool-update-")
    else:
        assert command == []


def test_service_hosted_worker_refuses_without_independent_scope_tool(tmp_path, monkeypatch):
    from core.updater import worker

    cgroup = tmp_path / "cgroup"
    cgroup.write_text("0::/app.slice/vool-runtime.service\n")
    monkeypatch.setattr(worker.shutil, "which", lambda _: None)
    with pytest.raises(ValueError, match="cannot leave its runtime service"):
        worker._systemd_scope_prefix("owned", platform="linux", cgroup_path=cgroup)


@pytest.fixture
def configured_update_checks(tmp_path, monkeypatch):
    import json

    from core import runtime_paths
    from core.updater import feed
    from core.updater.runtime import UpdateSubsystem

    home = tmp_path / "home"
    config = tmp_path / "feed.json"
    config.write_text(json.dumps({"manifest_url": "https://updates.example.invalid/manifest.json",
                                  "app_path": str(tmp_path / "VOOL.app"), "channel": "beta"}))
    monkeypatch.setattr(feed, "DEFAULT_CONFIG_PATH", config)
    monkeypatch.delenv("VOOL_UPDATE_MANIFEST_URL", raising=False)
    monkeypatch.delenv("VOOL_UPDATE_DISABLED", raising=False)
    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", home)
    private, public = generate_publisher_keypair()
    document, _ = build_manifest(private_hex=private, version="0.7.1", channel="beta")
    calls = []
    subsystem = UpdateSubsystem(feed=feed.load_feed_config(), trust=TrustedPublishers({"release-2026-01": public}),
                                data_dir=home / "data", platform="macos-arm64", installed_version="0.7.0",
                                fetch=lambda url: (calls.append(url), manifest_bytes(document))[1])
    try:
        yield subsystem, calls
    finally:
        if subsystem.service is not None:
            subsystem.service.stop()
            if subsystem.service._thread is not None:
                subsystem.service._thread.join(timeout=2)
                assert not subsystem.service._thread.is_alive()


def test_fresh_feed_waits_for_a_user_check_and_the_census_uses_that_same_gate(configured_update_checks):
    from core.first_run_pact import egress_census
    from core.updater.feed import automatic_checks_enabled

    subsystem, calls = configured_update_checks
    subsystem.start()
    assert subsystem.configured and subsystem.service._thread is None
    assert not subsystem.service.check_once().ok
    assert calls == []
    door = next(row for row in egress_census()["doors"] if row["door"] == "updater_feed")
    assert door["closed"] and not automatic_checks_enabled()

    assert subsystem.trigger_check()["check_ok"]
    assert calls == [subsystem.feed.manifest_url]
    assert automatic_checks_enabled()
    census = egress_census()
    assert not next(row for row in census["doors"] if row["door"] == "updater_feed")["closed"]
    assert census["proof"] == "partial"
    assert "signed update checks may contact their configured feed" in census["residuals"]
    assert subsystem.service._thread.is_alive()


@pytest.mark.parametrize("choice", [None, "{", "[]", '{"enabled": false}', '{"enabled": "true"}',
                                   '{"enabled": true, "manifest_url": "https://other.example.invalid/feed"}'])
def test_absent_or_invalid_update_choice_cannot_open_the_feed(configured_update_checks, choice):
    from core.updater.feed import automatic_checks_enabled

    subsystem, calls = configured_update_checks
    if choice is not None:
        (subsystem.paths.root / "check_consent.json").write_text(choice)
    subsystem.start()
    assert not automatic_checks_enabled()
    assert subsystem.service._thread is None
    assert not subsystem.service.check_once().ok
    assert calls == []


def test_check_choice_survives_restart_but_cannot_enable_a_different_feed(configured_update_checks):
    import threading
    from dataclasses import replace

    from core.updater.feed import automatic_checks_enabled
    from core.updater.runtime import UpdateSubsystem

    subsystem, calls = configured_update_checks
    assert subsystem.trigger_check()["check_ok"]
    assert automatic_checks_enabled(feed=subsystem.feed, data_dir=subsystem.data_dir)
    assert not automatic_checks_enabled(feed=replace(subsystem.feed, manifest_url="https://other.example.invalid/feed"),
                                       data_dir=subsystem.data_dir)
    fetched = threading.Event()

    def fetch(url):
        fetched.set()
        return subsystem._custom_fetch(url)

    restarted = UpdateSubsystem(feed=subsystem.feed, trust=subsystem.trust, data_dir=subsystem.data_dir,
                                platform="macos-arm64", fetch=fetch)
    try:
        restarted.start()
        assert fetched.wait(timeout=2)
        restarted.service.stop()
        restarted.service._thread.join(timeout=2)
        assert not restarted.service._thread.is_alive()
        (subsystem.paths.root / "check_consent.json").unlink()
        assert not restarted.service.check_once().ok
        assert calls == [subsystem.feed.manifest_url] * 2
    finally:
        restarted.service.stop()
        if restarted.service._thread is not None:
            restarted.service._thread.join(timeout=2)


def test_failure_to_save_the_user_choice_sends_no_update_request(configured_update_checks, monkeypatch):
    from core.updater import runtime

    subsystem, calls = configured_update_checks

    def refuse(**kwargs):
        raise OSError("injected read-only consent store")

    monkeypatch.setattr(runtime, "enable_automatic_checks", refuse)
    subsystem.start()
    response = subsystem.trigger_check()
    assert not response["check_ok"] and "no check was sent" in response["detail"]
    assert calls == [] and subsystem.service._thread is None


def test_census_tracks_the_live_feed_when_configuration_changes(configured_update_checks, monkeypatch):
    from core import runtime_paths
    from core.first_run_pact import egress_census
    from core.updater import feed, runtime

    subsystem, calls = configured_update_checks
    assert subsystem.trigger_check()["check_ok"]
    monkeypatch.setattr(runtime, "_SUBSYSTEM", subsystem)
    feed.DEFAULT_CONFIG_PATH.write_text('{"manifest_url": "https://other.example.invalid/feed"}')
    subsystem.service.check_once()
    assert calls == [subsystem.feed.manifest_url] * 2
    assert not next(row for row in egress_census()["doors"] if row["door"] == "updater_feed")["closed"]
    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", subsystem.data_dir.parent / "another-home")
    assert next(row for row in egress_census()["doors"] if row["door"] == "updater_feed")["closed"]


@pytest.mark.parametrize("setting, enabled", [(False, False), ("true", False), (1, False), (True, True)])
def test_automatic_update_setting_requires_a_boolean_true(tmp_path, setting, enabled):
    import json

    from core.updater.feed import automatic_checks_enabled, load_feed_config

    config = tmp_path / "feed.json"
    config.write_text(json.dumps({"manifest_url": "https://updates.example.invalid/feed", "auto_check": setting}))
    assert automatic_checks_enabled(feed=load_feed_config(config_path=config, env={}), data_dir=tmp_path) is enabled


def test_operator_feed_override_is_explicit_and_the_disable_switch_still_wins(tmp_path):
    from core.updater.feed import automatic_checks_enabled, load_feed_config

    env = {"VOOL_UPDATE_MANIFEST_URL": "https://updates.example.invalid/feed"}
    assert automatic_checks_enabled(feed=load_feed_config(env=env), data_dir=tmp_path)
    env["VOOL_UPDATE_DISABLED"] = "1"
    assert not automatic_checks_enabled(feed=load_feed_config(env=env), data_dir=tmp_path)
