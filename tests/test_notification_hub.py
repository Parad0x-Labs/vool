"""Unified inbox laws through real stores and served owner-local routes."""
from datetime import datetime, timezone

import pytest

from tests.pa_beta_gate._pc_calendar_rig import api_get, api_post, prepare_home


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_MODEL_RADAR_POLLER", "0")
    return prepare_home(tmp_path, monkeypatch)


def offer(price=1, *, baseline=True):
    from core.model_radar import ModelObservation, PriceComponents
    from core.model_radar_service import observe_feed
    stamp = datetime.now(timezone.utc).isoformat()
    def row(p):
        return ModelObservation(provider_id="openrouter", model_id="vendor/alpha", display_name="Alpha",
                                prices=PriceComponents(p, p * 4, None), offer_kind="permanent",
                                evidence_fetched_at=stamp, source_feed="test-feed")
    if baseline:
        observe_feed("openrouter", (row(price * 2),), now=stamp)
    observe_feed("openrouter", (row(price),), now=stamp)
    from storage.model_radar import list_findings
    return list_findings()[0]


def event(f, seq=1):
    return {"seq": seq, "type": "price_decreased", "model": f.model_id,
            "before": {"prompt_usd_per_m": f.before["input_usd_per_m"], "completion_usd_per_m": f.before["output_usd_per_m"]},
            "after": {"prompt_usd_per_m": f.after["input_usd_per_m"], "completion_usd_per_m": f.after["output_usd_per_m"]}}


def inbox():
    status, result = api_get("/api/notifications")
    assert status == 200 and result["ok"], result
    return result


def test_same_catalog_and_offer_one_alert_dismiss_keeps_discovery(home, monkeypatch):
    f = offer()
    monkeypatch.setattr("core.model_market_feed.read_events", lambda **kw: [event(f)])
    result = inbox()
    assert len(result["items"]) == result["unread"] == 1
    item = result["items"][0]
    assert item["payload"]["finding"]["fingerprint"] == f.fingerprint
    status, result = api_post("/api/notifications/action", {"notification_id": item["notification_id"], "action": "dismiss"})
    assert status == 200 and result["ok"]
    assert inbox()["items"] == []
    from storage.model_radar import list_findings
    assert len(list_findings()) == 1
    from core.model_radar_service import get_preferences
    assert get_preferences().enabled


def test_read_survives_reopen_and_repeated_sync_new_offer_stays_unread(home):
    offer()
    first = inbox()["items"][0]
    api_post("/api/notifications/action", {"notification_id": first["notification_id"], "action": "read"})
    assert inbox()["unread"] == 0
    offer(.25, baseline=False)
    reopened = inbox()
    assert len(reopened["items"]) == 2 and reopened["unread"] == 1
    assert next(x for x in reopened["items"] if x["notification_id"] == first["notification_id"])["read_at"]


def test_market_baseline_once_and_legacy_import_never_future_read(home, monkeypatch):
    events = [{"seq": 1, "type": "model_delisted", "model": "vendor/old"}]
    monkeypatch.setattr("core.model_market_feed.read_events", lambda **kw: events[:])
    assert inbox()["unread"] == 0
    events.append({"seq": 2, "type": "model_delisted", "model": "vendor/new"})
    assert inbox()["unread"] == 1
    status, result = api_post("/api/notifications/migrate", {"read_seq": 1, "dismissed": [1]})
    assert status == 200 and result["ok"]
    assert len(inbox()["items"]) == inbox()["unread"] == 1
    status, result = api_post("/api/notifications/migrate", {"read_seq": True, "dismissed": []})
    assert status == 400 and not result["ok"]
    assert api_post("/api/notifications/migrate", {}, host="203.0.113.4")[0] == 403


def test_runtime_event_records_once_without_browser_and_reminders_untouched(home):
    from core.operator import notification_center
    from core.runtime_task_events import emit_runtime_event
    ctx = {"session_id": "chat-a", "cancel_turn_id": "turn-a"}
    emit_runtime_event(ctx, event_type="task_failed", message="Provider refused")
    emit_runtime_event(ctx, event_type="task_failed", message="Provider refused")
    reminder = notification_center.record_item(dedupe_key="reminder-fixture", source_kind="reminder", title="Meet Alex")
    result = inbox()
    assert len(result["items"]) == result["unread"] == 2
    failure = next(x for x in result["items"] if x["source_kind"] == "background_run")
    assert failure["session_id"] == "chat-a" and failure["payload"]["section"] == "needs"
    api_post("/api/notifications/action", {"notification_id": failure["notification_id"], "action": "dismiss"})
    assert inbox()["items"][0]["notification_id"] == reminder["notification_id"]
    assert notification_center.load_item(failure["notification_id"])["dismissed_at"]


def test_discovery_view_marks_inbox_read_not_dismissed(home):
    f = offer()
    item = inbox()["items"][0]
    status, result = api_post("/api/model-radar/viewed", {"fingerprints": [f.fingerprint]})
    assert status == 200 and result["ok"]
    assert inbox()["unread"] == 0
    assert inbox()["items"][0]["notification_id"] == item["notification_id"]


def test_migration_keeps_unread_legacy_arrivals_and_does_not_repeat(home, monkeypatch):
    events = [{"seq": n, "type": "model_delisted", "model": f"vendor/model-{n}"} for n in (1, 2)]
    monkeypatch.setattr("core.model_market_feed.read_events", lambda **kw: events[:])
    assert inbox()["unread"] == 0  # initial baseline before browser migration arrives
    assert api_post("/api/notifications/migrate", {"read_seq": 1, "dismissed": []})[0] == 200
    assert inbox()["unread"] == 1
    api_post("/api/notifications/migrate", {"read_seq": 200, "dismissed": []})
    assert inbox()["unread"] == 1  # another stale window cannot reset canonical state


def test_late_qualification_enriches_same_item_without_resetting_dismissal(home, monkeypatch):
    from core.operator.notification_hub import finding_key
    f = offer()
    from storage import model_radar
    rows = model_radar.list_findings()
    monkeypatch.setattr(model_radar, "list_findings", lambda **kw: [])
    monkeypatch.setattr("core.model_market_feed.read_events", lambda **kw: [event(f)])
    item = inbox()["items"][0]
    api_post("/api/notifications/action", {"notification_id": item["notification_id"], "action": "dismiss"})
    monkeypatch.setattr(model_radar, "list_findings", lambda **kw: rows)
    assert inbox()["items"] == []
    from core.operator import notification_center
    enriched = notification_center.load_item(item["notification_id"])
    assert enriched["source_kind"] == "model_offer" and enriched["dismissed_at"]
    assert enriched["payload"]["finding"]["fingerprint"] == f.fingerprint
    assert finding_key(f)


def test_expiry_change_and_cache_only_offer_are_not_conflated(home):
    from dataclasses import replace

    from core.operator.notification_hub import finding_key, market_key
    f = offer()
    assert finding_key(f) == market_key(event(f))
    assert finding_key(replace(f, expires_at="2026-12-01T00:00:00Z")) != finding_key(f)
    cache = replace(f, before={"input_usd_per_m": 1, "output_usd_per_m": 4, "cache_usd_per_m": 2},
                    after={"input_usd_per_m": 1, "output_usd_per_m": 4, "cache_usd_per_m": 1})
    assert finding_key(cache) != market_key(event(cache))


def test_exact_deep_link_and_bulk_read_cannot_acknowledge_new_arrivals(home):
    from core.operator import notification_center
    first = notification_center.record_item(dedupe_key="first", source_kind="reminder", title="First")
    newer = notification_center.record_item(dedupe_key="next", source_kind="reminder", title="Next")
    status, result = api_post("/api/notifications/read", {"notification_ids": [first["notification_id"]]})
    assert status == 200 and result["read"] == 1
    assert notification_center.load_item(newer["notification_id"])["read_at"] is None
    status, result = api_get("/api/notifications", {"notification_id": [first["notification_id"]]})
    assert status == 200 and result["items"][0]["notification_id"] == first["notification_id"]
    assert api_get("/api/notifications", {"notification_id": ["missing"]})[0] == 404
    assert api_post("/api/notifications/read", {"notification_ids": "all"})[0] == 400


def test_distinct_approval_requests_in_same_turn_do_not_merge(home):
    from core.runtime_task_events import emit_runtime_event
    context = {"session_id": "chat-a", "cancel_turn_id": "turn-a"}
    for request in ("approve-a", "approve-a", "approve-b"):
        emit_runtime_event(context, event_type="task_pending_approval", message="Needs approval",
                           details={"approval_request": {"approval_id": request}})
    assert len(inbox()["items"]) == 2


def test_hide_offer_removes_discovery_and_its_alert_but_not_other_alerts(home):
    f = offer()
    from core.operator import notification_center
    reminder = notification_center.record_item(dedupe_key="keep", source_kind="reminder", title="Keep this")
    assert len(inbox()["items"]) == 2
    status, result = api_post("/api/model-radar/dismiss", {"fingerprint": f.fingerprint})
    assert status == 200 and result["ok"]
    assert [x["notification_id"] for x in inbox()["items"]] == [reminder["notification_id"]]
    from core.model_radar_service import list_findings
    assert list_findings() == []


def test_legacy_unread_between_read_and_dismissed(home, monkeypatch):
    events = [{"seq": i, "type": "model_delisted", "model": f"vendor/model-{i}"} for i in (1, 2, 3)]
    monkeypatch.setattr("core.model_market_feed.read_events", lambda **kw: events)
    assert inbox()["unread"] == 0
    status, result = api_post("/api/notifications/migrate", {"read_seq": 1, "dismissed": [3]})
    assert status == 200 and result["ok"], result
    items = inbox()["items"]
    assert {x["payload"]["market_event"]["seq"] for x in items} == {1, 2}
    assert [x["payload"]["market_event"]["seq"] for x in items if not x["read_at"]] == [2]
    assert api_post("/api/notifications/migrate", {"read_seq": 3, "dismissed": []})[1]["already_imported"]
    assert inbox()["unread"] == 1




def test_legacy_import_rolls_back_items_and_marker_on_write_failure(home, monkeypatch):
    from core.operator import notification_center as centre
    events = [{"seq": i, "type": "model_delisted", "model": f"vendor/rollback-{i}"} for i in (1, 2, 3)]
    monkeypatch.setattr("core.model_market_feed.read_events", lambda **kw: events)
    before = inbox()["items"]
    original = centre._write_read_action
    def fail_dismiss(conn, notification_id, action, now):
        original(conn, notification_id, action, now)
        if action == "dismiss":
            raise RuntimeError("injected failed migration write")
    with monkeypatch.context() as patch:
        patch.setattr(centre, "_write_read_action", fail_dismiss)
        with pytest.raises(RuntimeError, match="injected failed migration write"):
            api_post("/api/notifications/migrate", {"read_seq": 1, "dismissed": [3]})
    assert inbox()["items"] == before
    conn = centre._default_connection()
    try:
        assert not conn.execute("SELECT 1 FROM notification_preferences WHERE pref_key = 'legacy_model_inbox_imported'").fetchone()
    finally:
        conn.close()
    assert api_post("/api/notifications/migrate", {"read_seq": 1, "dismissed": [3]})[1]["ok"]
    assert inbox()["unread"] == 1
