"""An operator's CoinGecko key raises price limits and never takes price answers down.

THE LAWS UNDER TEST
-------------------
* **Each plan's key goes to its own root, in its own documented header.** Demo: api.coingecko.com
  with ``x-cg-demo-api-key``; Pro: pro-api.coingecko.com with ``x-cg-pro-api-key``. Never in the URL.
* **No key, no change.** Without a stored key requests go to the keyless public root, as before.
* **A refused key degrades, it does not break.** A 401/403/400 on a keyed request is remembered for
  that exact key, and the same request is asked with the next stored key, then without one.
* **Saving goes through verification only.** The credentials door removes a market-data key but
  refuses to write one, so an unverified key can never be stored there.
"""
from __future__ import annotations

import io
import json
import urllib.error

import pytest

DEMO = "CG-demoTestKey000000000000"
PRO = "CG-proTestKey0000000000000"


@pytest.fixture
def store(monkeypatch):
    from core import coingecko_auth, credential_store

    slots: dict[str, str] = {}
    monkeypatch.setattr(credential_store, "get_credential", lambda name: slots.get(name))
    coingecko_auth.reset_refusals_for_tests()
    yield slots
    coingecko_auth.reset_refusals_for_tests()


def test_no_key_keeps_the_keyless_public_root(store):
    from core.coingecko_auth import request_for

    url, auth, plan = request_for("/simple/price?ids=bitcoin&vs_currencies=usd")
    assert url.startswith("https://api.coingecko.com/api/v3/simple/price")
    assert auth == {} and plan == "keyless"


def test_demo_and_pro_keys_ride_their_documented_header_and_root(store):
    from core.coingecko_auth import request_for

    store["market.data.coingecko"] = DEMO
    url, auth, plan = request_for("/ping")
    assert (url, auth, plan) == ("https://api.coingecko.com/api/v3/ping", {"x-cg-demo-api-key": DEMO}, "coingecko")
    store["market.data.coingecko_pro"] = PRO
    url, auth, plan = request_for("/ping")
    assert (url, auth, plan) == ("https://pro-api.coingecko.com/api/v3/ping", {"x-cg-pro-api-key": PRO}, "coingecko_pro")
    assert DEMO not in url and PRO not in url


def _answer(url_to_status: dict[str, int], seen: list[tuple[str, set[str]]]):
    def door(req, *, timeout):
        names = {k.lower() for k in list(req.headers) + list(req.unredirected_hdrs)}
        seen.append((req.full_url.split("?")[0], names))
        host = req.full_url.split("/")[2]
        status = url_to_status.get(host, 200)
        if status != 200:
            raise urllib.error.HTTPError(req.full_url, status, "refused", {}, io.BytesIO(b"{}"))
        return io.BytesIO(json.dumps({"bitcoin": {"usd": 1}}).encode())

    return door


def test_a_refused_key_falls_through_to_the_next_key_then_keyless(store, monkeypatch):
    import tools.web.web_research as wr
    from core import coingecko_auth

    store["market.data.coingecko_pro"] = PRO
    store["market.data.coingecko"] = DEMO
    seen: list[tuple[str, set[str]]] = []
    monkeypatch.setattr(wr, "_open_remote", _answer({"pro-api.coingecko.com": 401}, seen))
    assert wr._coingecko_get_json("/simple/price?ids=bitcoin", timeout=3, limit=1000) == {"bitcoin": {"usd": 1}}
    assert [host for host, _ in seen] == ["https://pro-api.coingecko.com/api/v3/simple/price",
                                          "https://api.coingecko.com/api/v3/simple/price"]
    assert "x-cg-demo-api-key" in seen[1][1] and "x-cg-pro-api-key" not in seen[1][1]
    assert coingecko_auth.refused_plans() == ("coingecko_pro",)

    seen.clear()
    monkeypatch.setattr(wr, "_open_remote", _answer({"api.coingecko.com": 401}, seen))
    with pytest.raises(urllib.error.HTTPError):
        # the keyless root itself refusing is CoinGecko's answer, not a key problem: surfaced as before
        wr._coingecko_get_json("/simple/price?ids=bitcoin", timeout=3, limit=1000)
    assert [names for _, names in seen][-1] == {"user-agent"}


def test_a_replaced_key_is_tried_again(store):
    from core import coingecko_auth

    store["market.data.coingecko"] = DEMO
    coingecko_auth.note_refused("coingecko", DEMO)
    assert coingecko_auth.request_for("/ping")[2] == "keyless"
    store["market.data.coingecko"] = DEMO + "x"
    assert coingecko_auth.request_for("/ping")[2] == "coingecko"


def test_registry_verifies_each_plan_on_its_own_root():
    from core.credential_intelligence.provider_registry import default_registry

    registry = default_registry()
    demo, pro = registry.get("market.coingecko"), registry.get("market.coingecko_pro")
    assert (demo.verify_endpoint, demo.auth_style, demo.auth_name) == (
        "https://api.coingecko.com/api/v3/ping", "header", "x-cg-demo-api-key")
    assert (pro.verify_endpoint, pro.auth_name) == ("https://pro-api.coingecko.com/api/v3/ping", "x-cg-pro-api-key")
    assert 400 in pro.invalid_statuses  # a key of the other plan is refused for this one


def test_credentials_door_removes_but_never_writes_a_market_key(monkeypatch):
    from types import SimpleNamespace

    from core.web.api import registry_authorities as ra

    runtime = SimpleNamespace(runtime_version_stamp={})
    deleted = []
    monkeypatch.setattr("core.credential_store.delete_credential", lambda name: deleted.append(name) or True)
    refused = ra.set_credentials_authority({"name": "market.data.coingecko", "value": DEMO}, {}, runtime)
    assert refused.status == 400
    removed = ra.set_credentials_authority({"name": "market.data.coingecko", "delete": True}, {}, runtime)
    assert removed.status == 200 and deleted == ["market.data.coingecko"]


@pytest.mark.parametrize("lane", ["price", "index"])
def test_a_refusal_of_the_old_key_never_blocks_the_key_saved_meanwhile(store, monkeypatch, lane):
    """Key A is sent; while CoinGecko answers, the operator saves key B; A comes back 401. Only A
    may be remembered as refused: the next request carries B (audit T-0047 P2)."""

    import tools.web.web_research as wr
    from core import coingecko_auth
    from tools.web import coin_index

    store["market.data.coingecko_pro"] = PRO
    replacement = PRO + "B"
    sent: list[str] = []

    def refuse_after_rotation(req, *args, **kwargs):
        sent.append(req.unredirected_hdrs.get("X-cg-pro-api-key", ""))
        if len(sent) == 1:
            store["market.data.coingecko_pro"] = replacement  # saved while the request is in flight
            raise urllib.error.HTTPError(req.full_url, 401, "refused", {}, io.BytesIO(b"{}"))
        return io.BytesIO(json.dumps({"bitcoin": {"usd": 1}} if lane == "price" else []).encode())

    if lane == "price":
        monkeypatch.setattr(wr, "_open_remote", refuse_after_rotation)
        wr._coingecko_get_json("/simple/price?ids=bitcoin", timeout=3, limit=1000)
    else:
        # The index asks through the same one outbound door (open_remote_url -> open_remote).
        monkeypatch.setattr("core.remote_fetch_policy.open_remote", refuse_after_rotation)
        coin_index._read_index_rows()
    assert sent[0] == PRO and sent[1] == replacement, sent
    assert coingecko_auth.request_for("/ping")[1] == {"x-cg-pro-api-key": replacement}
    assert coingecko_auth.refused_plans() == ()
