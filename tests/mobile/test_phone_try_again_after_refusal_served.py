"""A paired phone is refused for a right it lacks, the owner turns the right on, "try again" reruns the ask.

Measured on the phone (2026-11-12): "what pics are in my downloads" from a phone without "Use tools on
my computer" was answered with where to turn the right on; after the owner turned it on, "Try again, I
fixed permissions" was answered "Let me know which photos you need". The refusal carried nothing but its
sentence, so the chat had no refused request to run again. It now seals as refused, and the same chat's
"try again" lists the pictures.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.served

PICTURES = ["trip-01.jpg", "trip-02.jpg", "receipt.png"]


def _sealed(refusal_kind: str):
    from core import conversation_state
    from core.request_trust import REMOTE_GRANT_REFUSAL_TEXT, seal_remote_grant_refusal

    result = seal_remote_grant_refusal({"response": REMOTE_GRANT_REFUSAL_TEXT[refusal_kind], "success": True}, refusal_kind)
    return conversation_state.outcome_of(result)


@pytest.mark.parametrize("right", ["computer_tools", "paid_services"])
def test_the_refusal_reads_as_refused_and_can_be_run_again(right):
    from core import conversation_state

    outcome, reason, actionable = _sealed(right)
    # The record names the missing right, so "try again" can check live whether it is on now.
    assert (outcome, reason, actionable) == (conversation_state.REFUSED, f"missing_right:{right}", True)


def test_a_result_that_is_not_a_phone_refusal_is_left_alone():
    from core.request_trust import seal_remote_grant_refusal

    plain = {"response": "hello", "success": True}
    assert seal_remote_grant_refusal(dict(plain), "not_a_right") == plain
    assert seal_remote_grant_refusal(None, "computer_tools") is None


def test_served_phone_is_refused_then_try_again_lists_the_pictures(tmp_path):
    from core.request_trust import REMOTE_GRANT_COMPUTER, REMOTE_GRANT_REFUSAL_TEXT
    from tests._blackbox_served_rig import REPO_ROOT, ServedDaemon, run_in_home
    from tests._served_sufficiency_provider import MODEL_A, SEED
    from tests.mobile.test_phone_model_injection import InjectionProvider, call, http
    from tests.mobile.test_sealed_transport import pair
    from tests.test_pb01_served_sufficiency_served import _certify

    owner_home = tmp_path / "owner"
    downloads = owner_home / "Downloads"
    downloads.mkdir(parents=True)
    (owner_home / "Desktop").mkdir()
    (owner_home / "Documents").mkdir()
    now = time.time()
    for age, name in enumerate(PICTURES):
        (downloads / name).write_bytes(b"\x89PNG" if name.endswith(".png") else b"\xff\xd8\xff")
        os.utime(downloads / name, (now - 60 * age, now - 60 * age))
    (downloads / "notes.txt").write_text("x", encoding="utf-8")

    provider = InjectionProvider()
    daemon = ServedDaemon(tmp_path / "home", env_extra={
        "VOOL_TEST_MODE": "1", "VOOL_UNATTENDED": "1", "VOOL_ALWAYS_ON_CATALOG": "1",
        "VOOL_MODEL_LOAD_FLOOR_GB": "0", "HOME": str(owner_home),
        "VOOL_FORCE_OLLAMA_MODEL": MODEL_A, "VOOL_OLLAMA_MODEL": MODEL_A,
        "OLLAMA_HOST": provider.base_url, "VOOL_OLLAMA_URL": provider.base_url,
        "VOOL_OLLAMA_CHAT_URL": provider.base_url + "/api/chat",
    })
    provider.__enter__()
    try:
        daemon.start(timeout=60)
        seed = SEED.replace('"structured_json"]', '"structured_json", "tool_intent", "code_basic", "code_complex"]')
        run_in_home(daemon.home, seed.format(root=REPO_ROOT, base_url=provider.base_url, models=[(MODEL_A, {})]))
        assert _certify(daemon, MODEL_A).get("state") == "verified"
        base = daemon.base_url
        status, config = http(base, "/api/mobile/settings", {"enabled": True, "address": "127.0.0.1"})
        assert status == 200, config
        desktop = (base, SimpleNamespace(phone_connection=config), daemon.home)
        phone = pair(desktop, rights={"history": True})
        status, prepared = call(*phone, "chats.prepare")
        assert status == 200, prepared
        chat_id = prepared["result"]["chat_id"]

        def say(text: str) -> str:
            status, result = call(*phone, "chats.send", {"chat_id": chat_id, "message": text,
                                                         "idempotency_key": secrets.token_hex(16)})
            assert status == 200, result
            return str(result["result"]["reply"])

        refused = say("what pics are in my downloads folder? give me a list")
        assert refused == REMOTE_GRANT_REFUSAL_TEXT[REMOTE_GRANT_COMPUTER], refused
        assert not any(name in refused for name in PICTURES)

        status, changed = http(base, "/api/mobile/settings", {"operation": "device_rules", "device_id": phone[3]["device_id"],
                                                             "rules": {"computer_tools": True}})
        assert status == 200, changed

        again = say("Try again, I fixed permissions")
        assert all(name in again for name in PICTURES), again
        assert "notes.txt" not in again, again

        # New wording, same refused-then-granted shape, in a fresh chat of the same phone.
        status, changed = http(base, "/api/mobile/settings", {"operation": "device_rules", "device_id": phone[3]["device_id"],
                                                             "rules": {"computer_tools": False}})
        assert status == 200, changed
        status, prepared = call(*phone, "chats.prepare")
        chat_id = prepared["result"]["chat_id"]
        assert say("show me the photos in Downloads") == REMOTE_GRANT_REFUSAL_TEXT[REMOTE_GRANT_COMPUTER]
        status, changed = http(base, "/api/mobile/settings", {"operation": "device_rules", "device_id": phone[3]["device_id"],
                                                             "rules": {"computer_tools": True}})
        assert status == 200, changed
        once_more = say("ok it is on now, do it again")
        assert all(name in once_more for name in PICTURES), once_more
    finally:
        (tmp_path / "provider-calls.json").write_text(json.dumps(provider.calls, indent=2))
        daemon.stop()
        provider.__exit__(None, None, None)
