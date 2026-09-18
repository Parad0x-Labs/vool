"""OC L2: relay bridge outbound HTTP goes through the ONE door (`core.remote_fetch_policy`).

(a) A representative bridge push with the door stubbed proves the delivery-truth flow is unchanged;
(b) an active veto (`allow_remote_fetch: False` turn scope) surfaces as `RemoteFetchRefusedError`,
    which each bridge maps into its existing typed failure/retry marks -- and NO socket is opened.
"""
from __future__ import annotations

import json
import urllib.request
from urllib import error as urlerror

import pytest

from core import remote_fetch_policy
from relay.bridge_workers.discord_bridge import DiscordBridge
from relay.bridge_workers.telegram_bridge import TelegramBridge
from relay.bridge_workers.telegram_chat_bridge import TelegramChatBridge


class _FakeResponse:
    def __init__(self, payload) -> None:
        self._payload = json.dumps(payload).encode("utf-8")
        self.status = 200
        self.length = len(self._payload)

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


def _stub_door(monkeypatch, urls: list, responder):
    """Route every door call through a fake transport, capturing (method, url)."""

    def fake_open_remote_url(url, *, data=None, headers=None, method="", timeout=30.0):
        urls.append((str(method or ("POST" if data is not None else "GET")), str(url)))
        return _FakeResponse(responder(str(url), data))

    monkeypatch.setattr(remote_fetch_policy, "open_remote_url", fake_open_remote_url)


def _forbid_sockets(monkeypatch) -> None:
    """Any raw urlopen anywhere under the veto is a test failure."""

    def _no_socket(*args, **kwargs):
        raise AssertionError("raw urlopen opened a socket while the fetch veto was active")

    monkeypatch.setattr(urllib.request, "urlopen", _no_socket)


def _telegram_record() -> dict:
    return {
        "kind": "outbound_post",
        "platform": "telegram",
        "target": "default",
        "content": "Hello Telegram",
        "delivery_status": "pending",
        "delivery_attempts": 0,
    }


# ---- (a) delivery truth flow unchanged when the door carries the traffic -------------------


def test_telegram_bridge_push_flows_through_door_and_marks_delivered(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    bridge = TelegramBridge()
    snapshot = {"records": [_telegram_record()]}
    urls: list = []
    published: list = []

    def responder(url, data):
        if "/topics/" in url:
            return snapshot
        if "/publish/" in url:
            published.append(json.loads(data.decode("utf-8")))
            return {"ok": True}
        if "api.telegram.org/botT/sendMessage" in url:
            return {"ok": True, "result": []}
        raise AssertionError(f"unexpected door URL {url}")

    _stub_door(monkeypatch, urls, responder)

    bridge.fetch_mirror_and_push_to_telegram()

    # both legs went through the ONE door: platform send + truth republish
    assert any("api.telegram.org/botT/sendMessage" in u for _, u in urls)
    assert any(m == "POST" and "/publish/" in u for m, u in urls)
    truth = published[-1]["records"][0]
    assert truth["delivery_status"] == "delivered"
    assert truth["delivery_attempts"] == 1
    assert truth["last_error"] is None


def test_discord_webhook_failure_keeps_bounded_retry_style_through_door(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_MIRROR_URL", "http://mirror.test")
    bridge = DiscordBridge()
    record = dict(_telegram_record(), platform="discord", content="Hello Discord")
    snapshot = {"records": [record]}
    urls: list = []
    published: list = []

    def responder(url, data):
        if "/topics/" in url:
            return snapshot
        if "/publish/" in url:
            published.append(json.loads(data.decode("utf-8")))
            return {"ok": True}
        if url.startswith("https://discord"):
            raise urlerror.URLError("webhook down")
        raise AssertionError(f"unexpected door URL {url}")

    _stub_door(monkeypatch, urls, responder)
    bridge.webhook_url = "https://discord.test/webhook"

    bridge.fetch_mirror_and_push_to_discord()

    assert any(u.startswith("https://discord.test/webhook") for _, u in urls)
    truth = published[-1]["records"][0]
    assert truth["delivery_attempts"] == 1                      # one attempt spent ...
    assert truth["delivery_status"] == "pending"                # ... still below cap 8
    assert truth["last_error"] == "discord_delivery_failed"


# ---- (b) veto: refusal maps into the typed failure marks, no socket ------------------------


def test_veto_refusal_maps_to_typed_pending_mark_with_no_socket(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    bridge = TelegramBridge()
    record = _telegram_record()

    # Only the queue read is faked; the platform send runs the REAL door under veto.
    snapshot = {"records": [record]}
    monkeypatch.setattr(bridge, "_mirror_request", lambda path, method="GET", data=None: snapshot)
    _forbid_sockets(monkeypatch)

    with remote_fetch_policy.remote_fetch_policy_scope({"allow_remote_fetch": False}):
        assert bridge._tg_request("sendMessage", {"chat_id": "42", "text": "x"}) == {}
        bridge.fetch_mirror_and_push_to_telegram()

    assert record["delivery_attempts"] == 1
    assert record["delivery_status"] == "pending"                # below cap -> bounded retry
    assert record["last_error"] == "telegram_send_failed"


def test_veto_refusal_reaches_retry_cap_marks_failed_transport(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    bridge = TelegramBridge()
    record = dict(_telegram_record(), delivery_attempts=7, max_delivery_attempts=8)

    snapshot = {"records": [record]}
    monkeypatch.setattr(bridge, "_mirror_request", lambda path, method="GET", data=None: snapshot)
    _forbid_sockets(monkeypatch)

    with remote_fetch_policy.remote_fetch_policy_scope({"allow_remote_fetch": False}):
        bridge.fetch_mirror_and_push_to_telegram()

    assert record["delivery_status"] == "failed"
    assert record["last_error"] == "delivery_retry_cap_exceeded"


def test_veto_blocks_discord_and_chat_bridge_transports_without_socket(monkeypatch) -> None:
    _forbid_sockets(monkeypatch)
    with remote_fetch_policy.remote_fetch_policy_scope({"allow_remote_fetch": False}):
        discord = DiscordBridge()
        discord.bot_token = "D"
        assert discord._discord_webhook_post("hi") is False       # typed failure, not an exception
        assert discord._discord_api_request("/users/@me") == {}
        chat = TelegramChatBridge(token="T")
        assert chat._default_tg_call("getUpdates") == {}
        assert "unreachable" in chat._default_vool_call("hello", "42")


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__]))
