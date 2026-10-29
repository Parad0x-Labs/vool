"""Web lookups off means no live lookup goes online, on every lane that answers one.

Measured on 1cbf433 (fresh-user test, 2026-10-29), with Settings > Privacy & Permissions > Web
lookups off, the fresh default:

    "whats the weather in London today"   -> "London: Sunny, 13 C ... Source: wttr.in"
    "what is the bitcoin price right now" -> "Bitcoin: USD 80,170.00 ... Source: CoinGecko"

while exchange rates and news on the same install refused. The typed live-data lane never asked the
switch, and the web-research door it fetched through did not ask it either. These tests drive the
real turn and watch the socket layer, so they hold whichever lane a later change routes a lookup to.
"""

from __future__ import annotations

import socket
import urllib.request

import pytest

from core.remote_fetch_policy import remote_fetch_attempt_count, remote_fetch_policy_scope
from tools.web import web_research as wr

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}

_LIVE_ASKS = (
    "whats the weather in London today",
    "what is the bitcoin price right now",
    "weather in Paris and Rome",
    "price of gold and bitcoin",
)


@pytest.fixture
def public_connects(monkeypatch):
    """Every attempt to open a connection to a non-loopback host, refused and recorded."""

    attempts: list[str] = []
    real = socket.create_connection

    def _guard(address, *args, **kwargs):
        host = str(address[0])
        if host in _LOOPBACK:
            return real(address, *args, **kwargs)
        attempts.append(host)
        raise OSError(f"test: refused connection to {host}")

    monkeypatch.setattr(socket, "create_connection", _guard)
    return attempts


@pytest.mark.parametrize("ask", _LIVE_ASKS)
def test_a_live_ask_with_web_lookups_off_never_goes_online(ask, public_connects) -> None:
    from tests.test_turn_attempt_chain import _SOURCE_CONTEXT, _Harness

    harness = _Harness(f"sess-web-off-{abs(hash(ask)) % 10_000_000}")
    try:
        result = harness.agent.run_once(
            ask, source_context=dict(_SOURCE_CONTEXT), session_id_override=harness.session_id
        )
    finally:
        harness.close()
    response = str(result.get("response") or "")

    assert public_connects == [], f"went online with Web lookups off: {public_connects}"
    # The person is told why nothing was looked up and where the switch is.
    assert "Web lookups are off" in response
    assert "Settings > Privacy & Permissions" in response
    for leaked in ("wttr.in", "CoinGecko", "USD "):
        assert leaked not in response


def test_the_lookup_door_refuses_when_web_lookups_are_off(public_connects) -> None:
    """The turn permits remote fetching, but the switch is off: the door refuses before a socket."""

    with remote_fetch_policy_scope({"allow_remote_fetch": True}):
        with pytest.raises(wr.RemoteFetchRefusedError, match=wr.WEB_LOOKUPS_OFF_REFUSAL):
            wr._open_remote(urllib.request.Request("https://wttr.in/london?format=j1"), timeout=5)
        with pytest.raises(wr.RemoteFetchRefusedError, match=wr.WEB_LOOKUPS_OFF_REFUSAL):
            wr.structured_weather_lookup("London", timeout_s=5.0)
        assert remote_fetch_attempt_count() == 0
    assert public_connects == []


def test_the_lookup_door_opens_once_web_lookups_are_on(enable_web, monkeypatch) -> None:
    """The gate is the switch, not a blanket block: switched on, the same fetch goes out."""

    opened: list[str] = []

    class _Response:
        status = 200
        headers: dict = {}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, *args):
            return b"{}"

    def _open(request, timeout=None, **kwargs):
        opened.append(request.full_url)
        return _Response()

    monkeypatch.setattr(urllib.request, "urlopen", _open)
    with remote_fetch_policy_scope({"allow_remote_fetch": True}):
        with wr._open_remote(urllib.request.Request("https://wttr.in/london?format=j1"), timeout=5):
            pass
        assert remote_fetch_attempt_count() == 1
    assert opened == ["https://wttr.in/london?format=j1"]


def test_local_only_names_local_only_not_the_web_switch(enable_web, monkeypatch, public_connects) -> None:
    """Web lookups on but Local Only in force: the refusal names the switch that actually held."""

    from core import policy_engine
    from tests.test_turn_attempt_chain import _SOURCE_CONTEXT, _Harness

    monkeypatch.setattr(policy_engine, "local_only_mode", lambda: True)
    harness = _Harness("sess-web-on-local-only")
    try:
        result = harness.agent.run_once(
            "whats the weather in London today",
            source_context=dict(_SOURCE_CONTEXT),
            session_id_override=harness.session_id,
        )
    finally:
        harness.close()
    response = str(result.get("response") or "")

    assert public_connects == []
    assert "Local Only is on" in response
    assert "wttr.in" not in response
