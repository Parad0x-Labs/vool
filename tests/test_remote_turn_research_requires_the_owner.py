"""A remote chat cannot enter the owner's pre-model research or search backends."""
from __future__ import annotations

import copy
import json
import uuid

import pytest

from core import policy_engine
from core.channel_gateway import ChannelRequest, build_source_context
from core.remote_fetch_policy import remote_fetch_turn_scope
from retrieval.web_adapter import WebAdapter
from tests._owner_network_rig import CapturingProvider, NetworkDaemon, SearchTransport, model_choice
from tests._reader_served_rig import canonical_session
from tools.web import web_research as research

QUESTION = "Search the web for the latest news on geothermal research projects."
REMOTE_API = {"surface": "api", "_http_ingress": True, "_owner_local": False, "allow_remote_fetch": True}
REMOTE_CHANNEL = build_source_context(ChannelRequest(platform="discord", user_id="fixture-reader",
                                                     text=QUESTION, allow_remote_fetch=True))
OWNER = {**REMOTE_API, "_owner_local": True}


@pytest.fixture(scope="module")
def research_daemon(tmp_path_factory):
    with SearchTransport() as search, CapturingProvider(reply_fn=model_choice) as model:
        daemon = NetworkDaemon(tmp_path_factory.mktemp("owner-research-daemon") / "home", search=search, provider=model)
        try:
            daemon.start()
            certification = daemon.certify(timeout=60)
            assert certification.get("state") == "verified", certification
            yield daemon, search, model
        finally:
            daemon.stop()


def test_the_owners_loopback_turn_still_researches(research_daemon):
    daemon, search, _model = research_daemon
    search.reset()
    result = daemon.turn(QUESTION, session=canonical_session("owner-research-" + uuid.uuid4().hex))
    assert search.searches, (result, daemon.log_tail())
    # The fast-news lane legitimately answers from search snippets. Its actual
    # transport result, including this run's unique title, must reach the reply.
    assert search.title in json.dumps(result), result


@pytest.mark.parametrize("channel", [False, True], ids=["remote-api", "channel-gateway-http-shim"])
def test_a_remote_turn_does_no_pre_model_research(research_daemon, channel):
    daemon, search, _model = research_daemon
    search.reset()
    result = daemon.turn(QUESTION, session=canonical_session("remote-research-" + uuid.uuid4().hex),
                         remote=not channel, channel=channel)
    assert search.searches == [], ("remote pre-model search reached the transport", search.searches, result)
    assert search.pages == [], ("remote research fetched an article", search.pages, result)


def test_a_remote_turn_can_still_receive_a_plain_reply(research_daemon):
    daemon, search, _model = research_daemon
    search.reset()
    result = daemon.turn("Write one short whimsical sentence about a paper kite.",
                         session=canonical_session("remote-plain-" + uuid.uuid4().hex), remote=True)
    assert isinstance(result, dict) and result, result
    assert search.searches == [] and search.pages == []


@pytest.fixture
def search_policy(monkeypatch):
    with SearchTransport() as search:
        policy = copy.deepcopy(policy_engine.load())
        policy["system"].update(local_only_mode=False, allow_web_fallback=True)
        policy.setdefault("network", {})["outbound_enabled"] = True
        policy["web"].update(allowed_engines=["searxng"], provider_order=["searxng"],
                             searxng_url=search.base_url, allow_browser_fallback=False)
        monkeypatch.setattr(policy_engine, "_POLICY_CACHE", policy)
        monkeypatch.setenv("SEARXNG_URL", search.base_url)
        monkeypatch.setenv("WEB_SEARCH_PROVIDER_ORDER", "searxng")
        monkeypatch.setenv("ALLOW_BROWSER_FALLBACK", "0")
        yield search


@pytest.mark.parametrize("context", [REMOTE_API, REMOTE_CHANNEL], ids=["api", "channel"])
def test_the_adapter_defends_the_same_remote_turn_scope(search_policy, context):
    with remote_fetch_turn_scope(context):
        notes = WebAdapter.search_query(QUESTION, task_id="remote-adapter-fixture", total_budget_s=2)
    assert search_policy.searches == [], (search_policy.searches, notes)
    assert notes == []


@pytest.mark.parametrize("context", [REMOTE_API, REMOTE_CHANNEL], ids=["api", "channel"])
def test_a_remote_scope_offers_no_engines_or_browser(search_policy, monkeypatch, context):
    monkeypatch.setenv("ALLOW_BROWSER_FALLBACK", "1")
    with remote_fetch_turn_scope(context):
        assert research._provider_order() == []
        assert research._should_try_browser() is False
        result = research.web_research(QUESTION, total_budget_s=2)
    assert result.hits == [] and result.pages == []
    assert "owner_local_required" in result.notes
    assert search_policy.searches == []


def test_the_owner_scope_keeps_its_search_and_browser_choices(search_policy, monkeypatch):
    monkeypatch.setenv("ALLOW_BROWSER_FALLBACK", "1")
    with remote_fetch_turn_scope(OWNER):
        assert "searxng" in research._provider_order()
        assert research._should_try_browser() is True
