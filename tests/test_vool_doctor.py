"""VOOL Doctor, each check against a deliberately broken part: it names the fault, points at the repair,
and never reports a part it could not read as working.

The checks read the real owners (the folders through the real file system calls, the database through
sqlite); only the owner whose fault cannot be made on this machine is replaced (a folder macOS keeps
private, an updater that failed, a stored cloud failure).
"""
from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import vool_doctor
from core.vool_doctor import OFF, OK, PROBLEM, UNKNOWN


def _home(tmp_path: Path, monkeypatch, *names: str) -> Path:
    person = tmp_path / "person"
    for name in names:
        (person / name).mkdir(parents=True)
    person.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(person))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: person))
    return person


def test_a_folder_this_computer_keeps_private_is_a_problem_with_its_repair(tmp_path, monkeypatch):
    person = _home(tmp_path, monkeypatch, "Documents", "Desktop")
    real = os.listdir

    def listdir(path="."):
        if Path(path).name == "Desktop":
            raise PermissionError(1, "Operation not permitted", str(path))
        return real(path)

    monkeypatch.setattr(os, "listdir", listdir)
    row = vool_doctor.check_folders()
    assert (row["state"], row["reason"], row["repair"]) == (PROBLEM, "folders.private", "privacy_files"), row
    assert row["values"]["names"] == "Desktop"
    assert str(person) not in str(row), "the answer names the folder, never the person's path"


def test_a_folder_read_the_computer_holds_is_not_reported_as_working(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, "Documents")
    release = threading.Event()
    real = os.listdir

    def listdir(path="."):
        if Path(path).name == "Documents":
            release.wait(10)  # a privacy prompt nobody has answered yet
        return real(path)

    monkeypatch.setattr(os, "listdir", listdir)
    monkeypatch.setattr(vool_doctor, "_FOLDER_WAIT_S", 0.2)
    try:
        row = vool_doctor.check_folders()
    finally:
        release.set()
    assert (row["state"], row["reason"]) == (UNKNOWN, "folders.slow"), row


def test_readable_folders_are_ok_and_no_folders_is_off(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    assert vool_doctor.check_folders()["state"] == OFF
    (tmp_path / "person" / "Downloads").mkdir()
    row = vool_doctor.check_folders()
    assert (row["state"], row["values"]["names"]) == (OK, "Downloads"), row


def _storage_at(tmp_path: Path, monkeypatch, db: Path) -> None:
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    monkeypatch.setattr("core.runtime_paths.active_data_dir", lambda: data)
    monkeypatch.setattr("storage.db.active_default_db_path", lambda: str(db))


def test_a_damaged_database_is_a_problem_that_points_at_a_backup(tmp_path, monkeypatch):
    db = tmp_path / "vool.db"
    db.write_bytes(b"this was a database once" * 200)
    _storage_at(tmp_path, monkeypatch, db)
    row = vool_doctor.check_storage()
    assert (row["state"], row["reason"], row["repair"]) == (PROBLEM, "storage.damaged", "backup"), row


def test_a_sound_database_with_room_is_ok_and_a_full_disk_is_a_problem(tmp_path, monkeypatch):
    db = tmp_path / "vool.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (x)")
    conn.commit()
    conn.close()
    _storage_at(tmp_path, monkeypatch, db)
    assert vool_doctor.check_storage()["state"] == OK
    monkeypatch.setattr(vool_doctor.shutil, "disk_usage", lambda _p: SimpleNamespace(free=200 * 1024 * 1024))
    row = vool_doctor.check_storage()
    assert (row["state"], row["reason"], row["values"]["free_mb"]) == (PROBLEM, "storage.low_space", 200), row


def test_a_data_folder_that_cannot_be_written_is_a_problem(tmp_path, monkeypatch):
    _storage_at(tmp_path, monkeypatch, tmp_path / "missing.db")

    def refuse(*_a, **_k):
        raise PermissionError(13, "Read-only file system")

    monkeypatch.setattr(vool_doctor.tempfile, "NamedTemporaryFile", refuse)
    assert vool_doctor.check_storage()["reason"] == "storage.read_only"


def test_a_stopped_model_server_is_a_problem_unless_an_online_model_answers(monkeypatch):
    from core import local_model_presence as presence

    monkeypatch.setattr(presence, "local_model_presence",
                        lambda **_k: presence.LocalModelPresence(running=False, reason="no_local_endpoint_answered"))
    monkeypatch.setattr(vool_doctor, "_cloud_ready", lambda: False)
    row = vool_doctor.check_local_model()
    assert (row["state"], row["reason"], row["repair"]) == (PROBLEM, "local.not_running", "models")
    monkeypatch.setattr(vool_doctor, "_cloud_ready", lambda: True)
    assert vool_doctor.check_local_model()["state"] == OFF


def test_a_model_server_that_answers_but_cannot_list_its_models_is_unknown(monkeypatch):
    from core import local_model_presence as presence

    monkeypatch.setattr(presence, "local_model_presence",
                        lambda **_k: presence.LocalModelPresence(running=True, reason="running", endpoint="http://127.0.0.1:9"))
    assert vool_doctor.check_local_model()["state"] == UNKNOWN


@pytest.mark.parametrize(("stored", "state", "reason"), [
    ({"state": "no_key"}, OFF, "cloud.none"),
    ({"state": "failed", "label": "Example", "detail": "401"}, PROBLEM, "cloud.failed"),
    ({"state": "untested", "label": "Example"}, UNKNOWN, "cloud.untested"),
    ({"state": "ok", "label": "Example"}, OK, "cloud.ok"),
])
def test_the_online_model_is_read_from_its_stored_state_never_probed(monkeypatch, stored, state, reason):
    calls = []

    def status(*, provider=None, probe=False):
        calls.append(probe)
        return stored

    monkeypatch.setattr("core.cloud_connection_state.connection_status", status)
    row = vool_doctor.check_cloud()
    assert (row["state"], row["reason"], row["repair"]) == (state, reason, "keys")
    assert calls == [False], "the Doctor never goes online on its own"


@pytest.mark.parametrize(("payload", "state", "reason"), [
    ({"configured": True, "phase": "failed", "installed_version": "0.8.0", "online_access": True}, PROBLEM, "updates.failed"),
    ({"configured": True, "phase": "ready_to_restart", "restart_pending": True, "target_version": "0.8.1"}, PROBLEM, "updates.restart"),
    ({"configured": True, "phase": "up_to_date", "installed_version": "0.8.0", "online_access": True}, OK, "updates.ok"),
    ({"configured": True, "phase": "idle", "installed_version": "0.8.0", "online_access": False}, OFF, "updates.offline"),
    ({"configured": True, "phase": "idle", "installed_version": "0.8.0", "online_access": True}, UNKNOWN, "updates.not_checked"),
    ({"configured": False, "installed_version": "0.8.0"}, UNKNOWN, "updates.unavailable"),
])
def test_updates_are_read_from_the_updaters_own_status(monkeypatch, payload, state, reason):
    subsystem = SimpleNamespace(status_payload=lambda: payload)
    monkeypatch.setattr("core.updater.runtime.get_update_subsystem", lambda: subsystem)
    row = vool_doctor.check_updates()
    assert (row["state"], row["reason"]) == (state, reason), row


def test_never_all_green_while_a_part_is_broken_unread_or_its_check_crashed(monkeypatch):
    def broken():
        raise RuntimeError("boom")

    rows = {
        "ok": lambda: vool_doctor._check("a", OK, "x"),
        "off": lambda: vool_doctor._check("b", OFF, "x"),
        "unknown": lambda: vool_doctor._check("c", UNKNOWN, "x"),
        "problem": lambda: vool_doctor._check("d", PROBLEM, "x"),
    }
    monkeypatch.setattr(vool_doctor, "CHECKS", (("a", rows["ok"]), ("b", rows["off"])))
    assert vool_doctor.run_checks()["summary"] == OK
    monkeypatch.setattr(vool_doctor, "CHECKS", (("a", rows["ok"]), ("c", rows["unknown"])))
    assert vool_doctor.run_checks()["summary"] == UNKNOWN
    monkeypatch.setattr(vool_doctor, "CHECKS", (("a", rows["ok"]), ("c", rows["unknown"]), ("d", rows["problem"])))
    assert vool_doctor.run_checks()["summary"] == PROBLEM
    monkeypatch.setattr(vool_doctor, "CHECKS", (("a", rows["ok"]), ("e", broken)))
    report = vool_doctor.run_checks()
    assert report["summary"] == UNKNOWN
    crashed = report["checks"][1]
    assert (crashed["id"], crashed["state"], crashed["reason"], crashed["values"]["fault"]) == ("e", UNKNOWN, "check_failed", "RuntimeError")


def test_every_reason_and_repair_the_doctor_can_give_has_words_in_every_language():
    import json
    import re

    source = Path(vool_doctor.__file__).read_text(encoding="utf-8")
    reasons = set(re.findall(r'_check\([^,]+,\s*\w+,\s*"([a-z_.]+)"', source)) | {"check_failed"}
    repairs = set(re.findall(r'repair="([a-z_]+)"', source))
    # Opening the privacy settings is worded per computer: System Settings on a Mac, the folder's own
    # permissions elsewhere.
    repairs = (repairs - {"privacy_files"}) | {"privacy_mac", "privacy_other"}
    assert len(reasons) > 20 and repairs
    catalogs = Path(vool_doctor.__file__).resolve().parent / "i18n" / "catalogs"
    for path in sorted(catalogs.glob("*.json")):
        keys = json.loads(path.read_text(encoding="utf-8"))
        keys = keys.get("messages", keys) if isinstance(keys, dict) else keys
        missing = [r for r in reasons if f"doctor.said.{r}" not in keys] + [r for r in repairs if f"doctor.repair.{r}" not in keys]
        assert not missing, (path.name, missing)
