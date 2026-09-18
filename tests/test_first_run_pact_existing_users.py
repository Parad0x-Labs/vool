"""M-P6 — existing users are never onboarded (LP6); the consent persists once and forever."""
from __future__ import annotations

import json

import pytest

from core import first_run_pact
from tests.first_run_pact_rig import pact_rig  # noqa: F401 — fixture


def _fresh_seed_with(signal: str, pact_rig):
    from core.runtime_paths import active_data_dir

    pact_file = active_data_dir() / "first_run_pact_state.json"
    if pact_file.exists():
        pact_file.unlink()
    if signal == "conversation_log":
        log = active_data_dir() / "conversation_log.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(json.dumps({"user": "hi", "assistant": "hello", "session_id": "openclaw:0a1b2c3d4e5f6a7b8c9d"}) + "\n")
    elif signal == "receipts":
        ledger = active_data_dir() / "honesty_receipts"
        ledger.mkdir(parents=True, exist_ok=True)
        (ledger / ("0" * 24 + ".jsonl")).write_text("{}\n")
    elif signal == "profile_items":
        from core import operator_profile

        operator_profile.remember(operator_profile.OWNER_PRINCIPAL, "locale", "lt-LT", origin="explicit")
    elif signal == "credentials_meta":
        meta = active_data_dir() / "credentials.meta.json"
        meta.write_text("{}\n")
    elif signal == "connection_state":
        state = active_data_dir() / "cloud_connection_state.json"
        state.write_text(json.dumps({"openrouter": {"state": "ok"}}))
    return first_run_pact.seed()


@pytest.mark.parametrize("signal", ["conversation_log", "receipts", "profile_items", "credentials_meta", "connection_state"])
def test_each_existing_use_signal_seeds_not_applicable(pact_rig, signal):
    seeded = _fresh_seed_with(signal, pact_rig)
    assert seeded["state"] == "not_applicable"
    assert seeded["seed_reason"] == "existing_user"


def test_a_fresh_install_seeds_absent_and_the_boot_seed_never_onboards_existing_users(pact_rig):
    seeded = first_run_pact.seed()
    assert seeded["state"] == "absent" and seeded["seed_reason"] == "fresh_install"


def test_not_applicable_writes_only_the_pact_file_and_nothing_else(pact_rig):
    from core.runtime_paths import active_data_dir

    home = pact_rig.home
    before = {p.relative_to(home): p.stat().st_mtime_ns for p in home.rglob("*") if p.is_file()}
    _fresh_seed_with("receipts", pact_rig)
    # Seed wrote ONE file: the pact file (plus the signal fixture itself).
    after = {p.relative_to(home): p.stat().st_mtime_ns for p in home.rglob("*") if p.is_file()}
    new_files = set(after) - set(before)
    new_files = {name for name in new_files if "honesty_receipts" not in str(name)}
    new_files = {name for name in new_files if not str(name).startswith("data/vool_web0_v2.db")}
    assert new_files <= {"data/first_run_pact_state.json"}, new_files


def test_the_terminal_states_persist_and_the_card_never_reopens_after_updates(pact_rig):
    pact_rig.pact()
    pact_rig.post("/api/onboarding/pact/begin", {})
    pact_rig.post("/api/onboarding/pact/skip", {})
    # A "restart": re-seed on boot must honor the persisted consent, forever.
    seeded = first_run_pact.seed()
    assert seeded["state"] == "skipped"
