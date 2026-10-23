"""Notification policy on the actual sealed listener and canonical inbox."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

import pytest

from tests.mobile.test_phone_listener_served import desktop as desktop
from tests.mobile.test_phone_listener_served import http
from tests.mobile.test_sealed_transport import action, opened, pair


def call(phone, key, shared, claim, verb, params=None):
    wire = action(key, shared, claim, verb, params)
    status, response = http(phone, "/api/mobile/companion", wire)
    return status, opened(response, shared, wire)


def ready(desktop):
    phone, key, shared, claim = pair(desktop)
    status, changed = http(desktop[0], "/api/mobile/settings", {"operation": "device_rules", "device_id": claim["device_id"],
        "rules": {"alerts": True, "history": False}})
    assert status == 200, changed
    assert call(phone, key, shared, claim, "notifications.preferences.get")[0] == 200
    return phone, key, shared, claim


def record(kind, title="Private task details"):
    from core.operator import notification_center as center

    source = kind if kind in center.SOURCE_KINDS else "background_run"
    return center.record_item(dedupe_key=secrets.token_hex(16), source_kind=source,
                              title=title, payload={"event_type": kind})


@pytest.mark.parametrize("kind", ["task_completed", "task_failed", "task_pending_approval", "task_cancelled",
                                  "calendar_alert", "calendar_catch_up", "reminder", "model_offer", "model_market", "market_watch", "desktop_inbox"])
def test_each_existing_type_switch_filters_the_wire_and_badge(desktop, kind):
    phone, key, shared, claim = ready(desktop)
    change = {"preset": "off", "types": {kind: {"enabled": True}}}
    assert call(phone, key, shared, claim, "notifications.preferences.set", change)[0] == 200
    record(kind)
    other = "task_failed" if kind != "task_failed" else "task_completed"
    record(other)
    status, response = call(phone, key, shared, claim, "notifications.poll", {"after": 0})
    assert status == 200
    result = response["result"]
    assert [event["event_type"] for event in result["events"]] == [kind]
    assert result["badge"] == 1
    assert "Private task details" not in str(result)
    assert call(phone, key, shared, claim, "notifications.preferences.set", {"types": {kind: {"enabled": False}}})[0] == 200
    status, response = call(phone, key, shared, claim, "notifications.feed")
    assert status == 200 and response["result"]["badge"] == 0 and response["result"]["events"] == []


@pytest.mark.parametrize("preset,expected", [("everything", ["task_completed", "task_failed", "reminder"]),
                                             ("important", ["task_failed"]), ("off", [])])
def test_presets_on_the_served_path(desktop, preset, expected):
    phone, key, shared, claim = ready(desktop)
    assert call(phone, key, shared, claim, "notifications.preferences.set", {"preset": preset})[0] == 200
    for kind in ["task_completed", "task_failed", "reminder"]:
        record(kind)
    status, response = call(phone, key, shared, claim, "notifications.poll", {"after": 0})
    assert status == 200
    assert [event["event_type"] for event in response["result"]["events"]] == expected
    assert response["result"]["badge"] == len(expected)


def test_quiet_hours_hold_release_and_survive_a_service_restart(desktop, monkeypatch):
    from core.mobile_companion import notifications
    from core.web.api.mobile_companion_api import reset_mobile_companion_service

    phone, key, shared, claim = ready(desktop)
    monkeypatch.setattr(notifications, "_now", lambda: datetime(2026, 10, 20, 23, 30, tzinfo=timezone.utc))
    settings = {"preset": "everything", "quiet": {"enabled": True, "start": "22:00", "end": "07:00", "approvals_through": True}}
    assert call(phone, key, shared, claim, "notifications.preferences.set", settings)[0] == 200
    record("task_completed")
    record("task_pending_approval")
    _status, first = call(phone, key, shared, claim, "notifications.poll", {"after": 0})
    assert [event["event_type"] for event in first["result"]["events"]] == ["task_pending_approval"]
    assert first["result"]["badge"] == 1
    reset_mobile_companion_service()
    monkeypatch.setattr(notifications, "_now", lambda: datetime(2026, 10, 21, 7, 1, tzinfo=timezone.utc))
    _status, released = call(phone, key, shared, claim, "notifications.poll", {"after": first["result"]["next_after"]})
    assert [event["event_type"] for event in released["result"]["events"]] == ["task_completed"]
    _status, ack = call(phone, key, shared, claim, "notifications.poll", {"after": released["result"]["next_after"]})
    assert ack["result"]["events"] == []
    _status, reconnect = call(phone, key, shared, claim, "notifications.poll", {"after": 0})
    assert reconnect["result"]["events"] == []


def test_inbox_burst_is_ascending_without_skips_and_read_clears_badge(desktop):
    phone, key, shared, claim = ready(desktop)
    call(phone, key, shared, claim, "notifications.preferences.set", {"preset": "everything"})
    source = [record("reminder", title="Reminder " + str(i))["notification_id"] for i in range(205)]
    cursor, received = 0, []
    for _ in range(8):
        status, response = call(phone, key, shared, claim, "notifications.poll", {"after": cursor, "limit": 37})
        assert status == 200
        value = response["result"]
        received.extend(value["events"])
        cursor = value["next_after"]
        if not value["events"]:
            break
    assert [event["source_id"] for event in received] == source
    assert [event["seq"] for event in received] == list(range(1, 206))
    assert call(phone, key, shared, claim, "notifications.read")[1]["result"]["badge"] == 0


def test_desktop_gate_wins_and_detail_choice_cannot_widen_history_rights(desktop):
    from core.web.api.mobile_companion_api import mobile_companion_service

    phone, key, shared, claim = ready(desktop)
    settings = {"preset": "everything", "types": {"reminder": {"details": True}}}
    call(phone, key, shared, claim, "notifications.preferences.set", settings)
    record("reminder")
    _status, result = call(phone, key, shared, claim, "notifications.poll", {"after": 0})
    assert "detail" not in result["result"]["events"][0]
    service = mobile_companion_service()
    service.store.mark_device(claim["device_id"], rules={"alerts": False, "history": True})
    record("task_failed")
    _status, result = call(phone, key, shared, claim, "notifications.poll", {"after": 0})
    assert result["result"]["events"] == [] and result["result"]["badge"] == 0
    service.store.mark_device(claim["device_id"], rules={"alerts": True, "history": True})
    record("reminder", title="Owner-permitted reminder details")
    _status, result = call(phone, key, shared, claim, "notifications.poll", {"after": 0})
    assert result["result"]["events"][0]["detail"] == "Owner-permitted reminder details"


def test_preference_writes_reject_bad_types_without_changing_saved_choices(desktop):
    phone, key, shared, claim = ready(desktop)
    before = call(phone, key, shared, claim, "notifications.preferences.get")[1]["result"]["preferences"]
    for value in [{"preset": "unknown"}, {"types": {"made_up": {"enabled": True}}},
                  {"types": {"reminder": {"enabled": "yes"}}}, {"quiet": {"offset_minutes": 10000}}]:
        status, response = call(phone, key, shared, claim, "notifications.preferences.set", value)
        assert status == 400 and response["code"] == "invalid_notification_preferences"
    assert call(phone, key, shared, claim, "notifications.preferences.get")[1]["result"]["preferences"] == before


def test_shared_phone_poller_persists_its_cursor_before_presenting_and_does_not_repeat(desktop):
    import json
    import subprocess
    from pathlib import Path

    from nacl.signing import VerifyKey

    phone, key, _shared, claim = ready(desktop)
    call(phone, key, _shared, claim, "notifications.preferences.set", {"preset": "everything"})
    record("task_failed")
    record("task_pending_approval")
    credentials = {"baseUrl": phone, "deviceId": claim["device_id"], "grantId": claim["grant_id"],
                   "grantSecret": claim["grant_secret"], "deviceSeedHex": key.encode().hex(),
                   "desktopFingerprint": claim["desktop_fingerprint"], "pairedAt": "2026-10-20T00:00:00Z",
                   "protocol": "vool.mobile.sealed.v2",
                   "desktopEncryptionKey": VerifyKey(bytes.fromhex(claim["desktop_public_key"])).to_curve25519_public_key().encode().hex()}
    program = """
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {SealedMobileCompanionClient} from './src/protocol/sealed.ts';
import {NotificationPoller} from './src/protocol/notifications.ts';
const client=new SealedMobileCompanionClient(JSON.parse(fs.readFileSync(0,'utf8')));
let cursor=0;const seen=[];const badges=[];
const cursorStore={load:async()=>cursor,save:async(value)=>{cursor=value;}};
const sink={onEvent:async(event)=>{assert.equal(cursor,event.seq);seen.push(event);}};
const options={cursorStore,onBadge:(value)=>badges.push(value)};
const first=new NotificationPoller(client,sink,async()=>{},0,options);
assert.equal(await first.runOnce(),2);
const second=new NotificationPoller(client,sink,async()=>{},0,options);
assert.equal(await second.runOnce(),0);
assert.equal(seen.length,2);
assert.deepEqual(seen.map(e=>e.target.screen),['chats','approvals']);
assert.ok(seen.every(e=>!('detail' in e)));
assert.deepEqual(badges,[2,2]);
await client.markNotificationsRead();
await second.runOnce();assert.equal(badges.at(-1),0);
console.log('shared alert poller: durable-cursor contract, coarse messages, targets, no reconnect repeat and badge pass');
"""
    result = subprocess.run(["node", "--input-type=module", "-e", program],
                            cwd=Path(__file__).resolve().parents[2] / "mobile",
                            input=json.dumps(credentials), capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_first_poll_cannot_skip_an_item_created_after_pairing(desktop):
    phone,key,shared,claim=pair(desktop)
    assert http(desktop[0],'/api/mobile/settings',{'operation':'device_rules','device_id':claim['device_id'],'rules':{'alerts':True}})[0]==200
    record('task_failed')
    status,response=call(phone,key,shared,claim,'notifications.poll',{'after':0})
    assert status==200
    assert [event['event_type'] for event in response['result']['events']]==['task_failed']


def test_durable_record_keeps_approvals_when_noise_is_off_and_pages_old_read_items(desktop):
    from core.web.api.mobile_companion_api import reset_mobile_companion_service
    phone,key,shared,claim=ready(desktop)
    call(phone,key,shared,claim,'notifications.preferences.set',{'preset':'off'})
    source=[record('task_pending_approval')['notification_id'] for _ in range(225)]
    cursor=0
    for _ in range(3):
        status,response=call(phone,key,shared,claim,'notifications.poll',{'after':cursor})
        assert status==200 and not response['result']['events'] and response['result']['badge']==0
        cursor=response['result']['next_after']
    reset_mobile_companion_service()
    status,response=call(phone,key,shared,claim,'notifications.feed',{'records':True})
    assert status==200
    newest=response['result'];assert len(newest['events'])==200 and newest['next_before']>0
    status,response=call(phone,key,shared,claim,'notifications.feed',{'records':True,'before':newest['next_before']})
    older=response['result'];assert status==200 and len(older['events'])==25 and older['next_before']==0
    assert [e['source_id'] for e in older['events']+newest['events']]==source
    assert all('detail' not in e for e in older['events']+newest['events'])
    assert call(phone,key,shared,claim,'notifications.feed',{'before':-1})[0]==400


def test_expired_alert_and_history_rights_do_not_leak_delivery_or_archive(desktop):
    import time

    from core.web.api.mobile_companion_api import mobile_companion_service
    phone,key,shared,claim=ready(desktop)
    record('task_failed')
    service=mobile_companion_service()
    service.store.mark_device(claim['device_id'],right_expiry={'alerts':time.time()-1,'history':time.time()-1})
    status,response=call(phone,key,shared,claim,'notifications.poll',{'after':0})
    assert status==200 and response['result']['events']==[] and response['result']['badge']==0
    assert not call(phone,key,shared,claim,'notifications.preferences.get')[1]['result']['alerts_allowed']
    assert call(phone,key,shared,claim,'notifications.feed',{'records':True})[1]['result']['events']==[]
