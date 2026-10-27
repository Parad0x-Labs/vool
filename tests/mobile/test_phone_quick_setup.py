"""The three-choice phone setup: models (with a phone's own monthly cloud limit), files (none, chosen
folders, or everything but the system), payments; and a phone answering the approvals its own turns raised.
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import request_trust as rt
from core.mobile_companion import policy

_REMOTE = {rt.HTTP_INGRESS_KEY: True, rt.OWNER_LOCAL_KEY: False}


class _Store:
    def __init__(self, device):
        self.record = device

    def device(self, device_id):
        return dict(self.record) if self.record and device_id == self.record["device_id"] else None


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "home"
    for name in ("Documents", "Desktop", "Downloads", "Library/Keychains", "Projects"):
        (root / name).mkdir(parents=True)
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: root))
    return root.resolve()


@pytest.fixture
def phone(monkeypatch, home):
    device = {"device_id": "phone:abc", "status": "active", "kind": "phone", "sealed_protocol": True,
              "rules": {"computer_tools": True, "paid_services": False},
              "right_expiry": {"computer_tools": time.time() + 3600}}
    store = _Store(device)
    import core.mobile_companion.listener as listener
    import core.web.api.mobile_companion_api as api

    monkeypatch.setattr(api, "mobile_companion_service", lambda: SimpleNamespace(store=store))
    monkeypatch.setattr(listener, "load_settings", lambda: {"enabled": True})
    return device


def _turn(**extra):
    return {**_REMOTE, rt.REMOTE_DEVICE_KEY: "phone:abc", **extra}


# --- files: chosen folders ---------------------------------------------------------------------------

def test_chosen_folders_open_file_tools_only(phone, home):
    phone["file_scope"] = {"mode": "folders", "folders": [str(home / "Documents")]}
    for allowed in ("workspace.read_file", "machine.read_file", "machine.write_file", "pdf.extract_text", "file.deliver"):
        assert rt.remote_turn_tool_refusal(allowed, _turn()) == "", allowed
    for refused in ("sandbox.run_command", "operator.list_processes", "email.send", "machine.find_file",
                    "machine.move_path", "workspace.apply_unified_diff"):
        assert rt.remote_turn_tool_refusal(refused, _turn()) == rt.PHONE_FOLDERS_ONLY, refused
        assert str(home / "Documents") in rt.remote_turn_refusal_text(refused, _turn())
    # Money and owner-only tools keep their own refusals.
    assert rt.remote_turn_tool_refusal("pay.x402", _turn()) == "owner_local_required"


def test_folder_paths_are_checked_the_way_the_tools_resolve_them(phone, home, tmp_path):
    from core.runtime_execution_tools import phone_folder_refusal

    docs = home / "Documents"
    (docs / "a.txt").write_text("x")
    (docs / "out").symlink_to(home / "Desktop")
    phone["file_scope"] = {"mode": "folders", "folders": [str(docs)]}
    ok = lambda intent, args, **ctx: phone_folder_refusal(intent, args, source_context=_turn(**ctx)) == ""  # noqa: E731
    assert ok("machine.read_file", {"path": str(docs / "a.txt")})
    assert ok("machine.list_directory", {"path": "~/Documents"})
    assert not ok("machine.list_directory", {"path": "~/Desktop"})
    assert not ok("machine.list_directory", {"path": str(docs / "out")})  # a symlink out is out
    assert not ok("machine.list_directory", {})  # the default root is not a chosen folder
    assert not ok("pdf.extract_text", {"path": "a.pdf"})  # relative: refused
    assert ok("pdf.extract_text", {"path": str(docs / "a.pdf")})
    workspace = {"workspace_root": str(docs)}
    assert ok("workspace.read_file", {"path": "a.txt"}, **workspace)
    assert not ok("workspace.list_files", {"path": ".", "glob": "../*"}, **workspace)
    assert not ok("workspace.read_file", {"path": "a.txt"}, workspace_root=str(tmp_path))
    # No folders left: nothing on disk.
    phone["file_scope"] = {"mode": "folders", "folders": []}
    assert "no folders" in phone_folder_refusal("machine.read_file", {"path": str(docs / "a.txt")}, source_context=_turn())
    # A broken record never widens to the whole computer.
    phone["file_scope"] = {"mode": "everything"}
    assert rt.remote_turn_folders(_turn()) == ()


def test_everything_still_keeps_the_system_and_library_out(phone, home):
    from core.runtime_execution_tools import phone_folder_refusal

    phone["file_scope"] = {"mode": "all"}
    assert rt.remote_turn_tool_refusal("sandbox.run_command", _turn()) == ""
    assert phone_folder_refusal("machine.list_directory", {"path": "~/Documents"}, source_context=_turn()) == ""
    assert "Library" in phone_folder_refusal("pdf.extract_text", {"path": str(home / "Library/Keychains/x.pdf")},
                                             source_context=_turn())
    assert "Library" in phone_folder_refusal("pdf.extract_text", {"path": "/etc/x.pdf"}, source_context=_turn())
    # The owner's own session is never held to this.
    assert phone_folder_refusal("pdf.extract_text", {"path": "/etc/x.pdf"}, source_context={rt.OWNER_LOCAL_KEY: True}) == ""


def test_saving_folders_keeps_them_real_resolved_and_out_of_the_system(home):
    (home / "Documents" / "link").symlink_to(home / "Projects")
    scope = policy._file_scope({"mode": "folders", "folders": ["~/Documents", str(home / "Documents/link"), "~/Documents"]})
    assert scope == {"mode": "folders", "folders": [str(home / "Documents"), str(home / "Projects")]}
    assert policy._file_scope({"mode": "all"}) == {"mode": "all"}
    for bad in ({"mode": "folders", "folders": []}, {"mode": "folders", "folders": ["Documents"]},
                {"mode": "folders", "folders": ["~/Nope"]}, {"mode": "folders", "folders": ["~/Library"]},
                {"mode": "folders", "folders": ["/etc"]}, {"mode": "all", "folders": ["/"]}, {"mode": "some"}):
        with pytest.raises(ValueError):
            policy._file_scope(bad)


def test_monthly_cloud_limit_values():
    assert policy._cloud_monthly(None) is None
    assert policy._cloud_monthly(20) == 20.0
    for bad in (0, -1, 0.001, float("nan"), "20", True, 1e9):
        with pytest.raises(ValueError):
            policy._cloud_monthly(bad)


# --- models: the phone's own monthly cloud limit ----------------------------------------------------

@pytest.fixture
def ledger(tmp_path, monkeypatch):
    from storage.db import configure_default_db_path

    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "vool"))
    configure_default_db_path(tmp_path / "data" / "test.db")
    yield tmp_path / "data" / "test.db"
    configure_default_db_path(None)


def _limits(**kw):
    from core.model_spend_ledger import SpendLimits

    return SpendLimits(per_call_usd=5, per_task_usd=50, daily_usd=50, monthly_usd=50, **kw)


def test_a_phone_limit_caps_that_phone_only(ledger):
    from core import model_spend_ledger as ml

    phone = _limits(spender="phone:abc", spender_monthly_usd=3.0)
    ml.reserve_spend(model_call_id="c1", task_id="t1", subtask_id="s", model_id="m", maximum_usd=2.0, limits=phone)
    with pytest.raises(PermissionError, match="device_monthly_spend_cap_exceeded"):
        ml.reserve_spend(model_call_id="c2", task_id="t2", subtask_id="s", model_id="m", maximum_usd=2.0, limits=phone)
    # Another phone and the owner are not held to this phone's limit.
    ml.reserve_spend(model_call_id="c3", task_id="t3", subtask_id="s", model_id="m", maximum_usd=2.0,
                     limits=_limits(spender="phone:other", spender_monthly_usd=3.0))
    ml.reserve_spend(model_call_id="c4", task_id="t4", subtask_id="s", model_id="m", maximum_usd=2.0, limits=_limits())
    # Settling low frees room; a retry's late charge books to the same phone.
    ml.settle_spend("c1", actual_usd=0.5)
    ml.record_supplemental_charge("c1", invocation_id="i2", task_id="t1", model_id="m", actual_usd=0.5)
    assert ml.spender_month_total("phone:abc") == pytest.approx(1.0)
    ml.reserve_spend(model_call_id="c5", task_id="t5", subtask_id="s", model_id="m", maximum_usd=2.0, limits=phone)
    # A limit with no spender to book against fails closed.
    with pytest.raises(PermissionError):
        ml.reserve_spend(model_call_id="c6", task_id="t6", subtask_id="s", model_id="m", maximum_usd=0.1,
                         limits=_limits(spender_monthly_usd=3.0))


def test_an_old_ledger_gains_the_spender_column(ledger):
    ledger.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ledger)
    conn.execute("""CREATE TABLE model_spend_reservations (reservation_id TEXT PRIMARY KEY, model_call_id TEXT NOT NULL UNIQUE,
        task_id TEXT NOT NULL, subtask_id TEXT NOT NULL DEFAULT '', model_id TEXT NOT NULL, reserved_usd REAL NOT NULL,
        actual_usd REAL NOT NULL DEFAULT 0, status TEXT NOT NULL, billing_ambiguous INTEGER NOT NULL DEFAULT 0,
        created_ts REAL NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    conn.execute("INSERT INTO model_spend_reservations VALUES ('r','old','t','s','m',0,1.5,'settled',0,?, 'x','x')", (time.time(),))
    conn.commit()
    conn.close()
    from core import model_spend_ledger as ml

    ml.reserve_spend(model_call_id="new", task_id="t", subtask_id="s", model_id="m", maximum_usd=1.0,
                     limits=_limits(spender="phone:abc", spender_monthly_usd=2.0))
    assert ml.spender_month_total("phone:abc") == pytest.approx(1.0)


def test_a_phone_turn_reserves_against_its_own_limit(phone, monkeypatch):
    from core import paid_call_reservation as pcr

    phone["rules"]["paid_services"] = True
    phone["right_expiry"]["paid_services"] = time.time() + 3600
    phone["cloud_monthly_usd"] = 7.5
    seen = {}
    monkeypatch.setattr(pcr, "_authorize", lambda **kw: seen.update(kw) or None)
    monkeypatch.setattr(pcr, "daily_call_cap_available", lambda: True)
    import core.model_selection_policy as msp

    monkeypatch.setattr(msp, "provider_cost_class", lambda _m: "paid_cloud")
    monkeypatch.setattr(msp, "is_verified_free_cloud_manifest", lambda _m: False)
    manifest = SimpleNamespace(provider_id="openrouter", model_name="paid/model")
    pcr.reserve_owner_pick_paid_call(manifest=manifest, task=SimpleNamespace(task_id="t"), source_context=_turn())
    assert seen["spender"] == "phone:phone:abc" and seen["spender_monthly_usd"] == 7.5
    phone.pop("cloud_monthly_usd")
    pcr.reserve_owner_pick_paid_call(manifest=manifest, task=SimpleNamespace(task_id="t"), source_context=_turn())
    assert seen["spender"] == "phone:phone:abc" and seen["spender_monthly_usd"] is None
    pcr.reserve_owner_pick_paid_call(manifest=manifest, task=SimpleNamespace(task_id="t"),
                                     source_context={rt.OWNER_LOCAL_KEY: True})
    assert seen["spender"] == "" and seen["spender_monthly_usd"] is None


# --- approvals the phone's own turns raised ---------------------------------------------------------

@pytest.fixture
def approvals(monkeypatch, tmp_path):
    import core.mode_permission_policy as mpp

    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "vool"))
    monkeypatch.setattr(mpp, "_persist_pending_approvals_locked", lambda: None)
    monkeypatch.setattr(mpp, "_ensure_approvals_restored", lambda: None)
    monkeypatch.setattr(mpp, "_APPROVALS", {})

    def add(token, **fields):
        mpp._APPROVALS[token] = {"approval_id": token, "status": "pending", "intent": "workspace.write_file",
                                 "expires_at": time.time() + 600, "session_id": "s", "task_id": "t",
                                 "scope_options": ["once"], **fields}
    return add


def _resolve(token, scope="once"):
    from core.web.api import mobile_companion_api as api

    service = SimpleNamespace(store=SimpleNamespace(append_receipt=lambda *a, **k: None))
    return api._action_approvals_resolve({"approval_id": token, "decision": "allow", "scope": scope},
                                         service=service, device={"device_id": "phone:abc", "name": "p"})


def test_a_phone_answers_only_its_own_tool_approvals(phone, approvals, monkeypatch):
    import core.runtime_task_events as events

    monkeypatch.setattr(events, "emit_runtime_event", lambda *a, **k: None)
    approvals("own", remote_device="phone:abc")
    approvals("desktop", remote_device="")
    approvals("other", remote_device="phone:other")
    approvals("money", remote_device="phone:abc", intent="pay.x402")
    approvals("spend_grant")  # register_external_approval-style: no device at all
    for token in ("desktop", "other", "money", "spend_grant"):
        assert _resolve(token)[0] == 403, token
    assert _resolve("own", scope="project")[0] == 400
    assert _resolve("own")[0] == 200
    approvals("later", remote_device="phone:abc")
    phone["rules"]["computer_tools"] = False
    assert _resolve("later")[0] == 403
    assert policy.refusal({**phone, "rules": {"computer_tools": True}}, "approvals.resolve") == ""
    assert policy.refusal({**phone, "rules": {"computer_tools": True}}, "wallet.approve")


def test_the_gate_stamps_which_phone_raised_an_approval():
    import core.mode_permission_policy as mpp

    assert mpp._raising_device(_turn()) == "phone:abc"
    assert mpp._raising_device({rt.OWNER_LOCAL_KEY: True, rt.REMOTE_DEVICE_KEY: "phone:abc"}) == ""
    assert mpp._raising_device({}) == ""


# --- the screens -----------------------------------------------------------------------------------

def test_pairing_hands_over_to_setup_and_settings_offers_three_choices():
    import core.vool_settings_page as page
    import core.web.api.mobile_companion_api as api

    assert "/api/mobile/pairing/status?pairing_id=" in api._PAIRING_PAGE_TEMPLATE
    assert "/settings?phone_setup=" in api._PAIRING_PAGE_TEMPLATE
    source = Path(page.__file__).read_text(encoding="utf-8")
    for key in ("phone.setup.models", "phone.setup.files", "phone.setup.payments", "phone.setup.done",
                "phone.setup.advanced", "prepare_folders"):
        assert key in source, key


def test_prepare_looks_into_each_folder_once(home):
    from core.web.api.mobile_companion_api import prepare_phone_folders

    device = {"rules": {"computer_tools": True}, "file_scope": {"mode": "folders", "folders": [str(home / "Projects"), str(home / "gone")]}}
    assert prepare_phone_folders(device) == [{"folder": str(home / "Projects"), "state": "ready"},
                                             {"folder": str(home / "gone"), "state": "missing"}]
    assert prepare_phone_folders({"rules": {}}) == []
    if os.geteuid() != 0:
        locked = home / "Locked"
        locked.mkdir(mode=0)
        device["file_scope"]["folders"] = [str(locked)]
        assert prepare_phone_folders(device)[0]["state"] == "blocked"


def test_review_paths_stay_closed(phone, home, monkeypatch):
    from core.runtime_execution_tools import phone_folder_refusal

    (home / "Library/Mail").mkdir(parents=True)
    refuse = lambda intent, args: phone_folder_refusal(intent, args, source_context=_turn()) != ""  # noqa: E731
    phone["file_scope"] = {"mode": "all"}
    assert refuse("pdf.extract_text", {"path": "Library/Mail/x.pdf"})  # relative: never resolved elsewhere
    phone["file_scope"] = {"mode": "folders", "folders": [str(home)]}
    assert refuse("pdf.extract_text", {"path": str(home / "Library/Mail/x.pdf")})
    assert not refuse("pdf.extract_text", {"path": str(home / "Projects/x.pdf")})
    # A device that cannot be read right now refuses rather than skipping the check.
    monkeypatch.setattr(rt, "remote_turn_device", lambda _sc: None)
    assert refuse("pdf.extract_text", {"path": str(home / "Projects/x.pdf")})


def test_a_phone_never_approves_settings_secrets_or_unknown_effects(phone, approvals, monkeypatch):
    import core.runtime_task_events as events

    monkeypatch.setattr(events, "emit_runtime_event", lambda *a, **k: None)
    for action in ("change_settings", "unknown_side_effect", "access_secrets", "change_provider_configuration"):
        approvals(action, remote_device="phone:abc", intent="sandbox.run_command", actions=("run_side_effecting_commands", action))
        assert _resolve(action)[0] == 403, action
    approvals("cmd", remote_device="phone:abc", intent="sandbox.run_command", actions=("run_side_effecting_commands",))
    assert _resolve("cmd")[0] == 200
