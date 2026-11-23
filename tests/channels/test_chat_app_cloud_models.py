"""A chat app answers with the owner's cloud models only once the owner switched that on for it, and every
paid call it makes is still reserved against the owner's spend caps (core.paid_call_reservation)."""
import time

import pytest

from core.request_trust import OWNER_LOCAL_KEY, REMOTE_DEVICE_KEY


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    from core.mobile_companion.policy import defaults
    from core.web.api import mobile_companion_api

    mobile_companion_api.reset_mobile_companion_service()
    service = mobile_companion_api.mobile_companion_service()

    def put(device_id, *, kind="bot", rules=None, expiry=None, bound=True):
        service.store.put_device({
            "device_id": device_id, "kind": kind, "platform": device_id.split(":")[-1], "name": device_id,
            "status": "active", "connection_id": "c", "sealed_protocol": 2,
            "rules": {**defaults(), "chat": True, **(rules or {})}, "right_expiry": expiry or {}, "payment_cap": {},
            "binding": {"state": "bound" if bound else "waiting", "channel_id": "444", "user_id": "111"},
        })

    yield put
    mobile_companion_api.reset_mobile_companion_service()


def turn(device_id):
    return {OWNER_LOCAL_KEY: False, REMOTE_DEVICE_KEY: device_id}


def test_cloud_models_open_only_with_the_owners_switch(store):
    from core.request_trust import turn_may_spend_owner_keys, turn_may_use_owner_models

    soon = time.time() + 86400
    store("bot:telegram")
    assert not turn_may_use_owner_models(turn("bot:telegram"))
    store("bot:telegram", rules={"cloud_models": True}, expiry={"cloud_models": soon})
    assert turn_may_use_owner_models(turn("bot:telegram"))
    # Not a key for anything else the owner pays for: lookups on the owner's keys stay the owner's.
    assert not turn_may_spend_owner_keys(turn("bot:telegram"))
    # Expired, or the app not bound to its owner: closed.
    store("bot:telegram", rules={"cloud_models": True}, expiry={"cloud_models": time.time() - 1})
    assert not turn_may_use_owner_models(turn("bot:telegram"))
    store("bot:discord", rules={"cloud_models": True}, expiry={"cloud_models": soon}, bound=False)
    assert not turn_may_use_owner_models(turn("bot:discord"))
    # The owner's own session always may.
    assert turn_may_use_owner_models({OWNER_LOCAL_KEY: True})


def test_a_chat_app_paid_call_without_the_switch_is_refused_at_the_reservation(store):
    from core.paid_call_reservation import reserve_owner_pick_paid_call

    store("bot:telegram")
    denial = {}
    manifest = type("M", (), {"model_name": "some/paid-model", "provider": "openrouter"})()
    assert reserve_owner_pick_paid_call(manifest=manifest, task=None, source_context=turn("bot:telegram"), denial=denial) is None
    assert denial["reason"] == "not_owner_local"


def test_the_switch_is_a_chat_app_right_only(store):
    """A phone never gets it; a chat app gets it for 30 days when switched on with no chosen end."""
    from core.mobile_companion import policy
    from core.mobile_companion.bot_runtime import BOT_RIGHTS
    from core.web.api.mobile_companion_api import mobile_companion_service

    service = mobile_companion_service()
    # Strong: it ends after 30 days unless renewed, like the other rights that reach the owner's things.
    assert "cloud_models" in BOT_RIGHTS and "cloud_models" in policy.STRONG
    store("phone:abc", kind="phone")
    from core.request_trust import turn_may_use_owner_models

    service.store.mark_device("phone:abc", rules={**policy.defaults(), "chat": True, "cloud_models": True},
                              right_expiry={"cloud_models": time.time() + 86400})
    assert not turn_may_use_owner_models(turn("phone:abc"))
