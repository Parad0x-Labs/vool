"""A chat turn from another device cannot spend the owner's keys, money or network position.

Served: the turn enters through the real ``/api/chat`` door from a LAN peer, and the real
``run_agent`` stamps it. The agent stands in for the model's tool choice only: it hands each tool
call to the real tool-intent executor with the context the server gave the turn. Every outbound
request VOOL makes goes through one door (``core.remote_fetch_policy``); here that door delivers to a
fake search/provider transport on 127.0.0.1, so "did the provider see the request" is a counted fact.
"""
from __future__ import annotations

import http.server
import json
import threading
import urllib.request

import pytest

from core.request_trust import OWNER_LOCAL_KEY
from core.runtime_paths import configure_runtime_home
from core.web.api.runtime import RuntimeServices

LAN_PEER = "192.168.1.55"
OWNER_PEER = "127.0.0.1"
SPENDING_CALLS = (
    ("web.search", {"query": "price of silver today"}),
    ("web.research", {"query": "price of silver today"}),
    ("token.research", {"query": "BONK", "token": "BONK"}),
    ("web.chain_lookup", {"query": "BONK"}),
    ("pay.x402", {"url": "https://api.example.test/paid", "max_amount": "0.01"}),
    ("sell.quote", {"target": "null://service/path"}),
    ("marketplace.search_listings", {"query": "gpu"}),
    ("browser.render", {"url": "https://example.test/"}),
)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    from core import runtime_paths

    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "vool"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    previous = runtime_paths._VOOL_HOME_OVERRIDE
    configure_runtime_home(tmp_path / "vool")
    try:
        yield
    finally:
        configure_runtime_home(previous)


@pytest.fixture()
def provider_transport(monkeypatch):
    """A fake search/chain-data/payment provider on 127.0.0.1. The outbound door delivers every
    request VOOL makes to it (recording the URL VOOL meant to reach), so its hit count is the number
    of provider requests the turn caused. Web access is on, as the owner sets it in Settings."""
    from core import policy_engine, remote_fetch_policy

    monkeypatch.setattr(policy_engine, "allow_web_fallback", lambda: True)
    hits: list[str] = []

    class _Provider(http.server.BaseHTTPRequestHandler):
        def _answer(self):
            body = json.dumps({"results": [{"title": "Silver", "url": "https://example.test/silver",
                                            "content": "provider answer 6613"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._answer()

        def do_POST(self):
            self._answer()

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _deliver_to_fake(request, *, timeout, **_kwargs):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        hits.append(url)
        return direct.open(f"http://127.0.0.1:{port}/fake", timeout=timeout)

    monkeypatch.setattr(remote_fetch_policy, "_open_enforced", _deliver_to_fake)
    try:
        yield hits
    finally:
        server.shutdown()
        server.server_close()


def _served_turn(peer: str, calls) -> tuple[int, list]:
    """Send one chat turn through the real /api/chat door from ``peer``; the agent runs ``calls``
    through the real tool-intent executor with the context the server stamped for this turn."""
    from core.tool_intent_executor import execute_tool_intent
    from core.web.api.service import dispatch_post
    from storage.migrations import run_migrations

    run_migrations()
    executions: list = []

    class _ModelChoosesTools:
        def run_once(self, user_text, *, session_id_override=None, source_context=None):
            for intent, arguments in calls:
                execution = execute_tool_intent(
                    {"intent": intent, "arguments": dict(arguments)},
                    task_id="remote-fence", session_id="openclaw:remote-fence",
                    source_context=dict(source_context or {}), hive_activity_tracker=None,
                )
                executions.append((intent, dict(source_context or {}), execution))
            return {"response": "done", "confidence": 1.0}

    services = RuntimeServices(display_name="VOOL")
    services.agent = _ModelChoosesTools()
    response = dispatch_post(
        path="/api/chat",
        body={"messages": [{"role": "user", "content": "what is the price of silver?"}], "stream": False},
        headers={"content-type": "application/json", "Host": "127.0.0.1:11435"},
        runtime=services, model_name="vool", workspace_root_provider=lambda: "",
        client_host=peer,
    )
    return response.status, executions


def test_a_turn_from_another_device_spends_no_key_and_no_money(provider_transport):
    status, executions = _served_turn(LAN_PEER, SPENDING_CALLS)
    assert status == 200
    assert [intent for intent, _, _ in executions] == [intent for intent, _ in SPENDING_CALLS], executions
    for intent, context, execution in executions:
        assert context[OWNER_LOCAL_KEY] is False, (intent, context)
        assert execution.ok is False, (intent, execution.status, execution.response_text)
        assert execution.status == "owner_local_required", (intent, execution.status, execution.response_text)
        assert execution.details.get("executed") is False, (intent, execution.details)
    assert provider_transport == [], "a provider saw a request from another device's turn"


def test_the_owner_turn_still_reaches_the_search_provider(provider_transport):
    # the same served path from the owner's own computer: the fake provider is reachable, so the
    # zero above is the fence, not a transport that cannot be hit
    status, executions = _served_turn(OWNER_PEER, (("web.search", {"query": "price of silver today"}),))
    assert status == 200
    [(intent, context, execution)] = executions
    assert context[OWNER_LOCAL_KEY] is True
    assert execution.status != "owner_local_required", (execution.status, execution.response_text)
    assert provider_transport, (execution.status, execution.response_text)


def test_a_turn_from_another_device_still_gets_a_plain_reply_and_a_public_page(provider_transport):
    status, executions = _served_turn(LAN_PEER, (("respond.direct", {}), ("web.fetch", {"url": "https://example.test/"})))
    assert status == 200
    (_, _, reply), (_, _, fetch) = executions
    assert reply.status == "direct_response"
    assert fetch.status != "owner_local_required", (fetch.status, fetch.response_text)


# ---- review fixes: no route from another device's turn into this computer or its network ----------

SERVER_TURN_REMOTE = {"surface": "api", "_http_ingress": True, OWNER_LOCAL_KEY: False, "fetch_text_references": True}
SERVER_TURN_OWNER = {"surface": "api", "_http_ingress": True, OWNER_LOCAL_KEY: True, "fetch_text_references": True}


@pytest.fixture()
def local_service(monkeypatch):
    """A real HTTP service on this computer (a model server, a router page), recording every request.
    Web access and the browser fallback are on, as the owner can set them in Settings."""
    from core import policy_engine

    monkeypatch.setattr(policy_engine, "allow_web_fallback", lambda: True)
    monkeypatch.setattr(policy_engine, "allow_browser_fallback", lambda: True)
    hits: list[str] = []

    class _Service(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            body = ("<html><body><p>" + "local model server private data 4472 " * 40 + "</p></body></html>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Service)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1], hits
    finally:
        server.shutdown()
        server.server_close()


def _ingest(text: str, context: dict, monkeypatch) -> tuple[list, list]:
    from core import media_ingestion
    from storage.migrations import run_migrations

    run_migrations()
    rendered: list[str] = []
    monkeypatch.setattr(media_ingestion, "browser_render",
                        lambda url, **_kw: rendered.append(url) or {"status": "fetch_error", "final_url": url})
    items = media_ingestion.ingest_media_evidence(task_id="t-media", trace_id="t-media", user_input=text,
                                                  source_context=dict(context))
    return items, rendered


def test_a_link_from_another_device_cannot_read_a_service_on_this_computer(local_service, monkeypatch):
    port, hits = local_service
    items, rendered = _ingest(f"summarize http://127.0.0.1:{port}/api/tags", SERVER_TURN_REMOTE, monkeypatch)
    assert hits == [], "another device's link reached a service on this computer"
    assert rendered == [], "another device's link was handed to a browser on this computer"
    assert "4472" not in json.dumps(items)
    [item] = items
    assert item["metadata"]["fetch_status"] == "private_target_refused", item["metadata"]


def test_the_owner_still_summarizes_a_page_on_their_own_computer(local_service, monkeypatch):
    port, hits = local_service
    items, _ = _ingest(f"summarize http://127.0.0.1:{port}/notes", SERVER_TURN_OWNER, monkeypatch)
    assert hits == ["/notes"]
    assert "4472" in json.dumps(items)


def test_a_turn_from_another_device_cannot_start_a_browser_on_this_computer(local_service, monkeypatch):
    import sys
    import types

    port, hits = local_service
    launches: list[str] = []

    class _Engine:
        def launch(self, **_kw):
            launches.append("launch")
            raise RuntimeError("no browser in tests")

    class _Playwright:
        chromium = firefox = webkit = _Engine()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    fake = types.ModuleType("playwright.sync_api")
    fake.sync_playwright = lambda: _Playwright()
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake)
    monkeypatch.setenv("PLAYWRIGHT_ENABLED", "1")
    status, executions = _served_turn(LAN_PEER, (("browser.render", {"url": f"http://127.0.0.1:{port}/"}),))
    assert status == 200
    [(_, _, execution)] = executions
    assert execution.status == "owner_local_required", (execution.status, execution.response_text)
    assert launches == [] and hits == []


def test_a_poisoned_search_hit_cannot_steer_research_into_this_computer(local_service, monkeypatch):
    from core.effect_gateway import named_background_effect_scope
    from tools.web import web_research as wr

    port, hits = local_service
    rendered: list[str] = []
    poisoned = wr.WebHit(title="Silver price", url=f"http://127.0.0.1:{port}/admin", snippet="silver price today",
                         engine="duckduckgo_html")
    monkeypatch.setattr(wr, "_prefer_specialized_live_research", lambda _q: False)
    monkeypatch.setattr(wr, "_provider_order", lambda: ["duckduckgo_html"])
    monkeypatch.setattr(wr, "_engine_skip_reason", lambda _p: "")
    monkeypatch.setattr(wr, "_duckduckgo_html_hits", lambda *_a, **_kw: [poisoned])
    monkeypatch.setattr(wr, "_accept_search_hits", lambda *_a, **_kw: (True, ""))
    monkeypatch.setattr(wr, "_should_try_browser", lambda: True)
    monkeypatch.setattr(wr, "browser_render", lambda url, **_kw: rendered.append(url) or {"status": "fetch_error"})
    with named_background_effect_scope("test.research"):
        result = wr.web_research("silver price today", max_pages=1, total_budget_s=30)
    assert hits == [], "a search hit reached a service on this computer"
    assert rendered == [], "a refused search hit was handed to the browser"
    assert "4472" not in json.dumps([page.text for page in result.pages])


def _owner_hit_fetch(url: str, monkeypatch, deliver) -> tuple[dict, list]:
    """The owner's research reading one search hit, with the outbound door handing the request to
    ``deliver`` (the network the door would have reached)."""
    from core import remote_fetch_policy
    from core.effect_gateway import named_background_effect_scope
    from tools.web.http_fetch import http_fetch_text

    sent: list[str] = []

    def _door(request, *, timeout, **_kwargs):
        sent.append(request.full_url)
        return deliver(request, timeout)

    monkeypatch.setattr(remote_fetch_policy, "_open_enforced", _door)
    with named_background_effect_scope("test.research"):
        return http_fetch_text(url, timeout_s=10, not_local=True), sent


def test_a_search_hit_whose_redirects_land_on_this_computer_is_not_read(local_service, monkeypatch):
    import socket

    port, hits = local_service
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    resolve = socket.getaddrinfo
    # The hit's name has a public DNS answer, so the checked, pinned door dispatches it.
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **kw: (
        [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        if host == "silver-prices.example" else resolve(host, *a, **kw)))
    # The hit names a public-looking site; the network answers with what its redirects landed on: a
    # service on this computer.
    fetched, sent = _owner_hit_fetch("https://silver-prices.example/today", monkeypatch,
                                     lambda _request, timeout: direct.open(f"http://127.0.0.1:{port}/admin", timeout=timeout))
    assert sent == ["https://silver-prices.example/today"]
    assert fetched["status"] == "private_target_refused"
    assert "4472" not in fetched["text"] + fetched["html"]


def test_a_search_hit_whose_name_resolves_to_this_computer_is_never_sent(monkeypatch, enable_web):
    _resolving_to("127.0.0.1", monkeypatch)
    fetched, sent = _owner_hit_fetch("http://router-admin.example/", monkeypatch,
                                     lambda *_a: pytest.fail("the door sent a request to this computer"))
    assert sent == [] and fetched["status"] == "private_target_refused"


def test_the_owners_research_does_not_read_a_hit_this_computer_cannot_resolve(monkeypatch, enable_web):
    """A hit's name that does not resolve here cannot be checked, so it is never handed to a proxy
    or the normal door to resolve elsewhere: it is refused before anything is sent."""
    import io
    import socket
    import urllib.response
    from email.message import Message

    def _no_dns(*_a, **_kw):
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    def _the_page(request, _timeout):
        headers = Message()
        headers["Content-Type"] = "text/html"
        body = b"<html><body><p>" + b"silver closed at 31.20 per ounce 6613 " * 20 + b"</p></body></html>"
        return urllib.response.addinfourl(io.BytesIO(body), headers, request.full_url, 200)

    monkeypatch.setattr(socket, "getaddrinfo", _no_dns)
    fetched, sent = _owner_hit_fetch("https://silver-prices.example/today", monkeypatch, _the_page)
    assert sent == []
    assert fetched["status"] == "private_target_refused"
    assert "6613" not in fetched["text"] + fetched["html"]


def _resolving_to(address: str, monkeypatch) -> None:
    import socket

    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_a, **_kw: [(family, socket.SOCK_STREAM, 6, "", (address, 80))])


@pytest.mark.parametrize("address", ["fec0::1", "fec0:1:2::3", "fd00::1", "fe80::1", "64:ff9b::a00:1", "64:ff9b::5db8:d70e",
                                     "2002:c0a8:101::1", "::c0a8:101", "192.88.99.1", "100.64.0.1", "198.18.0.1"])
def test_deprecated_and_local_ranges_are_not_public(address, monkeypatch):
    from core.remote_turn_fetch import RemoteTurnTargetRefused, resolve_public_target

    _resolving_to(address, monkeypatch)
    with pytest.raises(RemoteTurnTargetRefused):
        resolve_public_target("http://looks-public.example/")


@pytest.mark.parametrize("address", ["93.184.215.14", "2606:4700:4700::1111"])
def test_public_addresses_stay_public(address, monkeypatch):
    from core.remote_turn_fetch import resolve_public_target

    _resolving_to(address, monkeypatch)
    assert resolve_public_target("http://public.example/") == (address,)


# ---- bridge channels (Discord in process, Telegram through /api/chat) ---------------------------

def _channel_turn(text: str, calls=(), *, with_media: bool = False) -> tuple[list, list]:
    """One Discord-bridge turn through the canonical channel ingress (core.channel_gateway). The agent
    stands for the model only: it reads links the way turn_reasoning does for a chat surface
    (fetch_text_references on, core/agent_runtime/turn_reasoning.py) and runs ``calls`` through the
    real tool-intent executor, each with the context the gateway stamped."""
    from core import media_ingestion
    from core.channel_gateway import ChannelRequest, process_channel_request
    from core.tool_intent_executor import execute_tool_intent
    from storage.migrations import run_migrations

    run_migrations()
    executions: list = []
    media: list = []

    class _ModelChoosesTools:
        def run_once(self, user_text, *, session_id_override=None, source_context=None):
            context = dict(source_context or {})
            if with_media:
                media.extend(media_ingestion.ingest_media_evidence(
                    task_id="t-channel", trace_id="t-channel", user_input=user_text,
                    source_context={**context, "fetch_text_references": True}))
            for intent, arguments in calls:
                executions.append((intent, context, execute_tool_intent(
                    {"intent": intent, "arguments": dict(arguments)}, task_id="t-channel",
                    session_id=str(session_id_override), source_context=context, hive_activity_tracker=None)))
            return {"task_id": "t-channel", "response": "done", "confidence": 1.0,
                    "vool_response_commit": {"type": "response.commit", "canonical_content": "done"}}

    process_channel_request(_ModelChoosesTools(), ChannelRequest(platform="discord", user_id="4242",
                                                                 channel_id="77", text=text, surface="discord_bot"))
    return executions, media


def test_a_bridge_channel_link_cannot_read_a_service_on_this_computer(local_service, monkeypatch):
    from core import media_ingestion

    port, hits = local_service
    rendered: list[str] = []
    monkeypatch.setattr(media_ingestion, "browser_render", lambda url, **_kw: rendered.append(url) or {"status": "fetch_error"})
    _, media = _channel_turn(f"summarize http://127.0.0.1:{port}/api/tags", with_media=True)
    assert hits == [] and rendered == []
    assert "4472" not in json.dumps(media)


def test_a_bridge_channel_turn_cannot_search_on_the_owners_keys(provider_transport):
    executions, _ = _channel_turn("what is the price of silver?", (("web.search", {"query": "price of silver today"}),))
    [(_, context, execution)] = executions
    assert context[OWNER_LOCAL_KEY] is False
    assert execution.status == "owner_local_required", (execution.status, execution.response_text)
    assert provider_transport == []
