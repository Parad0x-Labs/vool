"""Web lookups off means no live lookup goes online, on every lane that answers one.

Measured on 1cbf433 (fresh-user test, 2026-10-29), with Settings > Privacy & Permissions > Web
lookups off, the fresh default:

    "whats the weather in London today"   -> "London: Sunny, 13 C ... Source: wttr.in"
    "what is the bitcoin price right now" -> "Bitcoin: USD 80,170.00 ... Source: CoinGecko"

while exchange rates and news on the same install refused. The typed live-data lane never asked the
switch, and the web-research door it fetched through did not ask it either. These tests drive the
real turn and watch every way out -- the door, `urlopen`, openers, DNS and sockets -- so they hold
whichever lane a later change routes a lookup to.
"""

from __future__ import annotations

import socket
import urllib.request

import pytest

from core.effect_gateway import current_effect_ledger
from core.remote_fetch_policy import remote_fetch_attempt_count, remote_fetch_policy_scope
from tools.web import web_research as wr

_LOOPBACK = {"127.0.0.1", "::1", "localhost", ""}

_LIVE_ASKS = (
    "whats the weather in London today",
    "what is the bitcoin price right now",
    "weather in Paris and Rome",
    "price of gold and bitcoin",
)


@pytest.fixture
def went_online(monkeypatch):
    """Every attempt to reach a non-loopback host, by any of the routes out, refused and recorded.

    conftest's own `urlopen` block answers first for most fetches, so a socket guard alone sees
    nothing (the first cut of this test passed on 1cbf433 while wttr.in and CoinGecko were asked
    for). The door is watched at `urlopen` and `OpenerDirector.open`, and DNS and sockets below it.
    """
    from urllib.parse import urlsplit

    attempts: list[str] = []

    def _host(target) -> str:
        url = getattr(target, "full_url", target)
        return str(urlsplit(str(url or "")).hostname or "").lower()

    def _refuse(where: str, host: str):
        attempts.append(f"{where}:{host}")
        raise OSError(f"test: refused {where} to {host}")

    real_urlopen = urllib.request.urlopen
    real_opener_open = urllib.request.OpenerDirector.open
    real_connect = socket.create_connection
    real_getaddrinfo = socket.getaddrinfo

    def _urlopen(request, *args, **kwargs):
        host = _host(request)
        if host in _LOOPBACK:
            return real_urlopen(request, *args, **kwargs)
        _refuse("urlopen", host)

    def _opener_open(self, request, *args, **kwargs):
        host = _host(request)
        if host in _LOOPBACK:
            return real_opener_open(self, request, *args, **kwargs)
        _refuse("opener", host)

    def _connect(address, *args, **kwargs):
        host = str(address[0])
        if host in _LOOPBACK:
            return real_connect(address, *args, **kwargs)
        _refuse("socket", host)

    def _getaddrinfo(host, *args, **kwargs):
        if str(host or "").lower() in _LOOPBACK:
            return real_getaddrinfo(host, *args, **kwargs)
        _refuse("dns", str(host))

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", _opener_open)
    monkeypatch.setattr(socket, "create_connection", _connect)
    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo)
    return attempts


def _ask(text: str, *, session: str, source_context: dict | None = None) -> str:
    from tests.test_turn_attempt_chain import _SOURCE_CONTEXT, _Harness

    harness = _Harness(session)
    try:
        result = harness.agent.run_once(
            text, source_context={**_SOURCE_CONTEXT, **(source_context or {})}, session_id_override=harness.session_id
        )
    finally:
        harness.close()
    return str(result.get("response") or "")


def _denials() -> list[str]:
    return [str(e.get("decided_by") or "") for e in current_effect_ledger().entries() if e.get("lifecycle") == "denied"]


@pytest.mark.parametrize("ask", _LIVE_ASKS)
def test_a_live_ask_with_web_lookups_off_never_goes_online(ask, went_online) -> None:
    response = _ask(ask, session=f"sess-web-off-{abs(hash(ask)) % 10_000_000}")

    assert went_online == [], f"went online with Web lookups off: {went_online}"
    # The person is told why nothing was looked up and where the switch is.
    assert "Web lookups are off" in response
    assert "Settings > Privacy & Permissions" in response
    for leaked in ("wttr.in", "CoinGecko", "USD "):
        assert leaked not in response


def test_the_lookup_door_refuses_and_records_when_web_lookups_are_off(went_online) -> None:
    """The turn permits remote fetching, but the switch is off: the door refuses before a socket
    and leaves a denial on the turn's ledger."""

    with remote_fetch_policy_scope({"allow_remote_fetch": True}):
        with pytest.raises(wr.RemoteFetchRefusedError, match=wr.WEB_LOOKUPS_OFF_REFUSAL):
            wr._open_remote(urllib.request.Request("https://wttr.in/london?format=j1"), timeout=5)
        with pytest.raises(wr.RemoteFetchRefusedError, match=wr.WEB_LOOKUPS_OFF_REFUSAL):
            wr.structured_weather_lookup("London", timeout_s=5.0)
        assert remote_fetch_attempt_count() == 0
        assert _denials() and set(_denials()) == {"remote_fetch_policy.web_lookups_off"}
    assert went_online == []


def test_search_and_page_reads_stay_offline_when_web_lookups_are_off(went_online) -> None:
    """Search (every engine, keyless and keyed) and page reads ask the switch at the shared door,
    not only the lanes that call them: none of them sends the query or resolves a host."""
    from tools.web.ddg_instant import ddg_instant_answer
    from tools.web.http_fetch import http_fetch_text
    from tools.web.searxng_client import SearXNGClient

    with remote_fetch_policy_scope({"allow_remote_fetch": True}):
        result = wr.web_research("python 3.14 release notes")
        assert result.hits == [] and wr.WEB_LOOKUPS_OFF_REFUSAL in result.notes
        with pytest.raises(wr.RemoteFetchRefusedError, match=wr.WEB_LOOKUPS_OFF_REFUSAL):
            ddg_instant_answer("python")
        with pytest.raises(wr.RemoteFetchRefusedError, match=wr.WEB_LOOKUPS_OFF_REFUSAL):
            SearXNGClient(base_url="https://searx.example.org").search("python")
        for not_local in (False, True):
            page = http_fetch_text("https://example.org/", not_local=not_local)
            assert not page.get("text"), page
    assert went_online == [], went_online


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


def test_a_fetch_that_is_not_a_lookup_is_not_held_to_the_switch(monkeypatch) -> None:
    """Update checks, model downloads and services the person set up are not lookups: the switch
    is not theirs to answer to."""
    from core.remote_fetch_policy import open_remote

    opened: list[str] = []

    class _Response:
        status = 200
        headers: dict = {}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda request, *a, **k: opened.append(request.full_url) or _Response())
    with remote_fetch_policy_scope({"allow_remote_fetch": True}):
        with open_remote(urllib.request.Request("https://updates.example.org/latest.json"), timeout=5):
            pass
    assert opened == ["https://updates.example.org/latest.json"]


def test_a_download_is_refused_before_its_address_is_even_resolved(went_online) -> None:
    response = _ask("download https://example.com/index.html into Downloads", session="sess-web-off-download")

    assert went_online == [], went_online
    assert "Web lookups are off" in response


def test_a_question_that_needs_no_reading_is_not_refused(went_online) -> None:
    """"Explain how the bitcoin price is determined" is classified as live data too, but asks for
    nothing to be looked up. With the switch off it is answered, not refused."""
    for ask in ("explain how the bitcoin price is determined", "why is the weather in London so often rainy"):
        response = _ask(ask, session=f"sess-web-off-general-{abs(hash(ask)) % 10_000_000}")
        assert "Web lookups are off" not in response, (ask, response)
    assert went_online == []


def test_local_only_for_this_chat_names_local_only_not_the_web_switch(enable_web, went_online) -> None:
    """Web lookups on, but Local Only chosen for the chat: the refusal names what actually held,
    and the Local Only record is kept on the ledger."""

    response = _ask("whats the weather in London today", session="sess-web-on-local-only", source_context={"local_only": True})

    assert went_online == []
    assert "Local Only is on" in response
    assert "VOOL Auto instead of VOOL Auto Local Only" in response
    assert "wttr.in" not in response
    with remote_fetch_policy_scope({"allow_remote_fetch": True, "local_only": True}):
        with pytest.raises(wr.RemoteFetchRefusedError):
            wr._open_remote(urllib.request.Request("https://wttr.in/london?format=j1"), timeout=5)
        assert _denials() == ["remote_fetch_policy.local_only_egress"]


def test_keep_everything_on_this_machine_offers_no_switch_that_cannot_be_used(monkeypatch, went_online) -> None:
    """"Keep everything on this machine" holds the whole computer offline and keeps Web lookups off;
    Settings will not turn the switch on while it is set, so the refusal must not send the person
    there, nor to a model choice that would not help."""
    from core import policy_engine

    monkeypatch.setattr(policy_engine, "local_only_mode", lambda: True)
    response = _ask("whats the weather in London today", session="sess-machine-local-only")

    assert went_online == []
    assert "keep everything on this machine" in response
    assert "turn on Web lookups" not in response and "VOOL Auto instead" not in response


def test_a_refused_lookup_in_a_table_reads_as_words_not_a_code() -> None:
    """The retry lane and planned tables print each part's failure reason; a refused lookup showed
    "RemoteFetchRefusedError: web_lookups_off"."""
    from core.agent_runtime.live_data_runner import _failure_reason
    from core.conductor.compose import reader_facing_reason

    reason = _failure_reason(wr.RemoteFetchRefusedError(wr.WEB_LOOKUPS_OFF_REFUSAL))
    assert "web_lookups_off" not in reason and "Web lookups are off" in reason
    assert "web_lookups_off" not in reader_facing_reason(f"RemoteFetchRefusedError: {wr.WEB_LOOKUPS_OFF_REFUSAL}")
