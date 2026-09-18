"""X trending: creds gate, read-only fetch, bare-token config, disabled-by-default, fail-closed."""
from __future__ import annotations

import json

import pytest

from core import credential_store, runtime_paths, x_tools
from core.runtime_execution_tools import _x_trending, execute_runtime_tool


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


def _store(account: str = "default", blob=None) -> None:
    if blob is None:
        blob = {"bearer_token": "t"}
    credential_store.store_credential(
        f"x.api.{account}",
        json.dumps(blob) if isinstance(blob, dict) else str(blob),
        label="test x api",
    )


def _fake_urlopen(monkeypatch, payload: dict, capture: dict | None = None) -> None:
    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def read(self, *_a):
            return json.dumps(payload).encode("utf-8")

    def fake(req, timeout=None, context=None):
        if capture is not None:
            capture["url"] = req.full_url
            capture["auth"] = req.headers.get("Authorization")
        return FakeResp()

    monkeypatch.setattr(x_tools.urllib.request, "urlopen", fake)


def test_no_token_configured() -> None:
    r = x_tools.fetch_trending()
    assert not r.ok and r.status == "needs_credentials"


def test_bare_token_string_is_accepted(monkeypatch) -> None:
    _store(blob="bare-token-123")
    cap: dict = {}
    _fake_urlopen(monkeypatch, {"data": []}, cap)
    r = x_tools.fetch_trending()
    assert r.ok and r.status == "executed"
    assert cap["auth"] == "Bearer bare-token-123"


def test_fetch_ok_parses_and_limits(monkeypatch) -> None:
    _store()
    payload = {"data": [{"trend_name": "#alpha", "tweet_count": 10}, {"name": "#beta", "count": 5}, {"trend_name": "#gamma"}]}
    _fake_urlopen(monkeypatch, payload)
    r = x_tools.fetch_trending(limit=2)
    assert r.ok and len(r.trends) == 2
    assert r.trends[0] == {"name": "#alpha", "count": 10}
    assert r.trends[1] == {"name": "#beta", "count": 5}


def test_woeid_is_substituted(monkeypatch) -> None:
    _store(blob={"bearer_token": "t", "trends_endpoint": "https://api.example.com/trends/{woeid}"})
    cap: dict = {}
    _fake_urlopen(monkeypatch, {"data": []}, cap)
    x_tools.fetch_trending(woeid=2486982)
    assert cap["url"] == "https://api.example.com/trends/2486982"


def test_fetch_failure_fails_closed(monkeypatch) -> None:
    _store()

    def boom(_req, timeout=None, context=None):
        raise OSError("rate limited")

    monkeypatch.setattr(x_tools.urllib.request, "urlopen", boom)
    r = x_tools.fetch_trending()
    assert not r.ok and r.status == "fetch_failed"


def test_handler_delegates(monkeypatch) -> None:
    _store()
    _fake_urlopen(monkeypatch, {"data": [{"trend_name": "#x"}]})
    res = _x_trending({"woeid": 1, "limit": 5})
    assert res.ok and res.status == "executed"
    assert res.details["trends"] == [{"name": "#x", "count": None}]


def test_disabled_by_default() -> None:
    r = execute_runtime_tool("x.trending", {})
    assert r is not None and r.handled and not r.ok and r.status == "disabled"


def test_non_iterable_data_does_not_raise(monkeypatch) -> None:
    _store()
    _fake_urlopen(monkeypatch, {"data": 5})  # malformed: "data" is not a list
    r = x_tools.fetch_trending()
    assert r.ok and r.status == "executed" and r.trends == []


def test_non_numeric_woeid_fails_closed(monkeypatch) -> None:
    _store()
    _fake_urlopen(monkeypatch, {"data": []})
    r = x_tools.fetch_trending(woeid="not-a-number")
    assert not r.ok and r.status == "invalid_arguments"
