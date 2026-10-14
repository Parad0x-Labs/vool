"""The Watching card in Settings: the owner-local routes it calls, and the card itself.

Routes are served through the same dispatcher the app uses (``dispatch_get`` / ``dispatch_post``);
owner-local exactly like the notification preference routes beside them.
"""
from __future__ import annotations

import pytest

from tests.pa_beta_gate._pc_calendar_rig import api_get, api_post, prepare_home

HELIUS_KEY = "1b2c3d4e-5f60-4a7b-8c9d-0e1f2a3b4c5d"
SOL = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_MODEL_RADAR_POLLER", "0")
    home = prepare_home(tmp_path, monkeypatch)
    from core import market_watch
    from storage.db import get_connection

    market_watch._ensure_tables()
    conn = get_connection()
    try:
        conn.execute("PRAGMA secure_delete=ON")
        for table in ("market_watches", "market_watch_events"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()
    return home


def _add_wallet(label="Team wallet"):
    from core.credential_store import store_credential
    from core.market_watch import add_watch

    store_credential("chain.data.helius", HELIUS_KEY, label="fixture")
    return add_watch({"kind": "wallet", "chain": "solana", "subject": SOL, "label": label}, now_fn=lambda: "2026-10-14T12:00:00+00:00")["watch"]


def _add_price(rule):
    from core.market_watch import add_watch

    return add_watch({"kind": "price", "chain": "solana", "subject": USDC_MINT, "label": "USDC", "rule": rule},
                     source_context=None, now_fn=lambda: "2026-10-14T12:00:00+00:00")


# --------------------------------------------------------------------------- the list route


def test_the_list_route_shows_each_watch_with_its_rule_in_words(home):
    _add_wallet()
    status, result = api_get("/api/market/watches")
    assert status == 200 and result["ok"], result
    [row] = result["watches"]
    assert row["label"] == "Team wallet" and row["chain"] == "Solana"
    assert row["kind"] == "wallet" and row["rule_words"] == "new transactions"
    assert row["state"] == "active" and row["last_checked"] == ""


def test_the_list_route_is_owner_local(home):
    _add_wallet()
    status, result = api_get("/api/market/watches", host="192.168.1.9")
    assert status == 403 and result["error"] == "owner_local_required"


def test_the_empty_list(home):
    status, result = api_get("/api/market/watches")
    assert status == 200 and result["watches"] == []


# --------------------------------------------------------------------------- the action routes


def test_pause_and_resume_through_the_routes(home):
    watch = _add_wallet()
    status, result = api_post("/api/market/watches/pause", {"id": watch["id"]})
    assert status == 200 and result["watch"]["state"] == "paused"
    status, result = api_post("/api/market/watches/resume", {"subject": SOL})
    assert status == 200 and result["watch"]["state"] == "active"


def test_remove_through_the_route_deletes_the_rows(home):
    watch = _add_wallet()
    status, result = api_post("/api/market/watches/remove", {"id": watch["id"]})
    assert status == 200 and result["removed"] == watch["id"]
    from storage.db import get_connection

    conn = get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM market_watches").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM market_watch_events").fetchone()[0] == 0
    finally:
        conn.close()


def test_an_unknown_watch_is_a_404_and_the_actions_are_owner_local(home):
    status, result = api_post("/api/market/watches/pause", {"id": "mw-nothere"})
    assert status == 404 and result["reason"] == "unknown_watch"
    _add_wallet()
    status, _ = api_post("/api/market/watches/remove", {"subject": SOL}, host="10.1.2.3")
    assert status == 403


def test_resume_without_the_requirement_names_the_next_step(home):
    from core.credential_store import delete_credential
    from core.market_watch import set_state

    watch = _add_wallet()
    set_state({"id": watch["id"]}, "paused")
    assert delete_credential("chain.data.helius")
    status, result = api_post("/api/market/watches/resume", {"id": watch["id"]})
    assert status == 400 and result["reason"] == "requirement_missing"
    assert "Settings" in result["detail"]


# --------------------------------------------------------------------------- the card itself


def test_the_settings_fragment_carries_the_watching_card():
    from core.notification_settings_fragment import render_notification_settings_fragment

    page = render_notification_settings_fragment()
    assert "/api/market/watches" in page
    assert "vmw.empty" in page and "vmw.btn.pause" in page and "vmw.btn.resume" in page and "vmw.btn.remove" in page
    assert "Nothing watched yet. Ask in any chat, for example: watch this wallet." in page


def test_the_card_texts_are_translated_in_every_catalog():
    import json
    from pathlib import Path

    catalogs = Path("core/i18n/catalogs")
    en = json.loads((catalogs / "en.json").read_text(encoding="utf-8"))["messages"]
    keys = [k for k in en if k.startswith("vmw.")]
    assert len(keys) >= 20 and en["vmw.empty"].startswith("Nothing watched yet")
    for path in sorted(catalogs.glob("*.json")):
        if path.name == "en.json":
            continue
        messages = json.loads(path.read_text(encoding="utf-8"))["messages"]
        for key in keys:
            assert key in messages, f"{path.name} lost {key}"
            assert messages[key] != en[key], f"{path.name} copied English for {key}"
