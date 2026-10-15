"""X reads with the user's keys: search by token, token callers, accounts, timelines, usage limit.

X is a loopback fixture answering the documented v2 shapes; api.x.com is never contacted. Every
test goes through the same door the model uses (``execute_runtime_tool``), so the contract gate,
dispatch, the outbound door and the usage meter all run. What a real X app answers is checked
separately with the owner's keys.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from tests._credential_intelligence_support import isolated_home, vault_home

BEARER = "AAAAAAAAAAAAAAAAAAAAAFixtureBearerNotReal0123456789"
KEYS = {
    "api_key": "fixtureConsumerKey0123456",
    "api_secret": "fixtureConsumerSecret0123456789abcdefghijklmnop",
    "access_token": "1234567890-fixtureAccessToken0123456789abcdefg",
    "access_token_secret": "fixtureAccessSecret0123456789abcdefghijklmn",
}
SOL = "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr"
EVM = "0x6982508145454Ce325dDbE47a25d4ec3d2311933"

USERS = {
    "11": {"id": "11", "username": "early_ape", "name": "Early", "created_at": "2025-01-01T00:00:00.000Z",
           "public_metrics": {"followers_count": 1200, "following_count": 300, "tweet_count": 900}},
    "22": {"id": "22", "username": "late_whale", "name": "Late", "created_at": "2019-05-01T00:00:00.000Z",
           "public_metrics": {"followers_count": 98000, "following_count": 50, "tweet_count": 15000}},
}


def _post(pid, author, text, at, likes=0, cashtags=()):
    return {"id": pid, "author_id": author, "text": text, "created_at": at,
            "public_metrics": {"like_count": likes, "retweet_count": 1, "reply_count": 0, "quote_count": 0},
            "entities": {"cashtags": [{"tag": t} for t in cashtags]}}


SEARCH = {
    "data": [
        _post("903", "22", f"aping {SOL} now", "2026-10-13T10:00:00.000Z", likes=500),
        _post("902", "11", f"second look {SOL}", "2026-10-13T09:00:00.000Z", likes=5),
        _post("901", "11", f"called it first: {SOL}", "2026-10-13T08:00:00.000Z", likes=40),
    ],
    "includes": {"users": [USERS["11"], USERS["22"]]},
    "meta": {"result_count": 3, "next_token": "nxt"},
}
TIMELINE_11 = {
    "data": [
        _post("801", "11", "$WIF to the moon", "2026-10-01T08:00:00.000Z", cashtags=("WIF",)),
        _post("802", "11", f"ca {SOL}", "2026-10-13T08:00:00.000Z"),
        _post("803", "11", f"new one {EVM}", "2026-10-05T08:00:00.000Z"),
    ],
    "includes": {"users": [USERS["11"]]},
    "meta": {"result_count": 3},
}


class FakeX:
    """Routes by path; ``script`` overrides answers in order; records every request."""

    def __init__(self, script=None):
        self.script = list(script or [])
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _answer(self, status, body):
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                parts = urlsplit(self.path)
                query = {k: v[0] for k, v in parse_qs(parts.query).items()}
                auth = str(self.headers.get("Authorization") or "")
                outer.requests.append({"method": "GET", "path": parts.path, "query": query, "auth": auth})
                if outer.script:
                    return self._answer(*outer.script.pop(0))
                if parts.path == "/2/tweets/search/recent":
                    return self._answer(200, SEARCH)
                if parts.path == "/2/users/11/tweets":
                    return self._answer(200, TIMELINE_11)
                if parts.path == "/2/users/by/username/early_ape":
                    return self._answer(200, {"data": USERS["11"]})
                if parts.path == "/2/users/me":
                    return self._answer(200, {"data": {"id": "1234567890", "username": "fixture_owner", "name": "Owner"}})
                if parts.path.startswith("/2/users/1234567890/timelines/reverse_chronological"):
                    return self._answer(200, {"data": [_post("700", "22", "gm", "2026-10-13T07:00:00.000Z")],
                                              "includes": {"users": [USERS["22"]]}})
                if parts.path == "/2/users/by":
                    names = query.get("usernames", "").split(",")
                    found = [u for u in USERS.values() if u["username"] in names]
                    return self._answer(200, {"data": found})
                if parts.path == "/2/tweets/counts/recent":
                    return self._answer(200, {"data": [{"start": "a", "end": "b", "tweet_count": 7}],
                                              "meta": {"total_tweet_count": 7}})
                return self._answer(404, {"title": "Not Found Error", "detail": "no route"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def _connect(bearer=True, account=True):
    from core.credential_store import store_credential

    blob = {}
    if bearer:
        blob["bearer_token"] = BEARER
    if account:
        blob.update(KEYS)
        blob["account"] = {"username": "fixture_owner", "id": "1234567890"}
    store_credential("x.api.default", json.dumps(blob), label="X (Twitter)")


@pytest.fixture
def fake_x(vault_home, monkeypatch):
    from core import x_api

    with FakeX() as server:
        monkeypatch.setattr(x_api, "API_ORIGIN", server.url)
        yield server


def _run(intent, args):
    from core.runtime_execution_tools import execute_runtime_tool

    return execute_runtime_tool(intent, args)


def test_tools_are_off_until_x_is_connected(fake_x):
    from core.runtime_execution_tools import runtime_execution_tool_specs

    assert not {s["intent"] for s in runtime_execution_tool_specs()} & {"x.search", "x.token_callers"}
    result = _run("x.search", {"token": SOL})
    assert result is not None and not result.ok
    assert fake_x.requests == []
    _connect()
    offered = {s["intent"] for s in runtime_execution_tool_specs()}
    assert {"x.search", "x.token_callers", "x.user", "x.user_posts", "x.post", "x.follows", "x.lists",
            "x.usage", "x.action.propose", "x.action.status"} <= offered
    # Trending reads outside the usage meter, so connecting X never switches it on by itself.
    assert "x.trending" not in offered


def test_search_by_solana_address_end_to_end(fake_x):
    _connect()
    result = _run("x.search", {"token": SOL, "max_results": 20})
    assert result.ok and result.status == "executed", result.response_text
    sent = fake_x.requests[-1]
    assert sent["path"] == "/2/tweets/search/recent"
    assert sent["query"]["query"] == f'({SOL} OR url:"{SOL}") -is:retweet'
    assert sent["query"]["max_results"] == "20"
    assert sent["auth"] == f"Bearer {BEARER}"  # public read uses the Bearer Token
    posts = result.details["posts"]
    assert [p["id"] for p in posts] == ["903", "902", "901"]
    assert posts[2]["author"] == "early_ape" and posts[2]["url"] == "https://x.com/early_ape/status/901"
    assert posts[0]["tokens"]["addresses"] == [SOL]
    assert {a["username"] for a in result.details["authors"]} == {"early_ape", "late_whale"}
    assert result.details["observation"]["posts"] == posts  # the model sees the data, not just a sentence


@pytest.mark.parametrize("token,query", [
    (EVM, f'({EVM} OR url:"{EVM}") -is:retweet'),
    ("$pepe", "$PEPE -is:retweet"),
    ("dogwifhat", '"dogwifhat" -is:retweet'),
])
def test_token_kinds_build_the_right_query(fake_x, token, query):
    _connect()
    assert _run("x.search", {"token": token}).ok
    assert fake_x.requests[-1]["query"]["query"] == query


def test_cashtag_operator_refused_falls_back_to_the_word(fake_x):
    _connect()
    fake_x.script = [(400, {"errors": [{"message": "There were errors processing your request: Reference to invalid operator 'cashtag'"}]})]
    result = _run("x.search", {"token": "$WIF"})
    assert result.ok
    assert [r["query"]["query"] for r in fake_x.requests] == ["$WIF -is:retweet", '"WIF" -is:retweet']


def test_token_callers_ranks_first_caller_and_reads_their_other_calls(fake_x):
    _connect()
    result = _run("x.token_callers", {"token": SOL, "history_for": 1, "history_posts": 10})
    assert result.ok, result.response_text
    callers = result.details["callers"]
    assert [c["username"] for c in callers] == ["early_ape", "late_whale"]
    first = callers[0]
    assert first["first_call_at"] == "2026-10-13T08:00:00.000Z" and first["call_posts"] == 2
    assert first["engagement"]["likes"] == 45 and first["followers"] == 1200
    assert first["first_call"]["url"] == "https://x.com/early_ape/status/901"
    # Their other calls: the cashtag and the EVM address, never the token being checked.
    assert [c["token"] for c in first["other_calls"]] == ["$WIF", EVM.lower()]
    assert "other_calls" not in callers[1]  # history_for=1 reads only the first caller
    assert [r["path"] for r in fake_x.requests] == ["/2/tweets/search/recent", "/2/users/11/tweets"]
    assert "early_ape" in result.response_text


def test_user_lookup_and_home_timeline_needs_account_keys(fake_x):
    _connect(account=False)
    looked = _run("x.user", {"usernames": "@early_ape, nobody_here"})
    assert looked.ok and looked.details["users"][0]["followers"] == 1200
    assert looked.details["not_found"] == ["nobody_here"]
    home = _run("x.user_posts", {"kind": "home"})
    assert not home.ok and home.status == "needs_account"
    assert all(r["path"] != "/2/users/me" for r in fake_x.requests)


def test_home_timeline_is_signed_as_the_user(fake_x):
    _connect()
    home = _run("x.user_posts", {"kind": "home", "max_results": 10})
    assert home.ok, home.response_text
    assert all(r["auth"].startswith("OAuth ") for r in fake_x.requests)
    assert home.details["posts"][0]["author"] == "late_whale"


def test_usage_is_counted_and_the_limit_stops_reads_before_sending(fake_x):
    from core import x_usage

    _connect()
    assert _run("x.search", {"token": SOL}).ok
    snap = x_usage.snapshot()
    assert snap["requests"] == 1 and snap["posts_read"] == 3 and snap["users_read"] == 2
    x_usage.set_limits(monthly_reads=5)
    sent_before = len(fake_x.requests)
    refused = _run("x.search", {"token": SOL})
    assert not refused.ok and refused.status == "usage_limit_reached"
    assert "Nothing was sent to X" in refused.response_text
    assert len(fake_x.requests) == sent_before  # refused BEFORE the request
    usage = _run("x.usage", {})
    assert usage.ok and usage.details["usage"]["reads_left"] == 0


def test_page_size_is_clamped_to_what_is_left(fake_x):
    from core import x_usage

    _connect()
    x_usage.set_limits(monthly_reads=40)
    assert _run("x.search", {"token": SOL, "max_results": 100}).ok
    assert fake_x.requests[-1]["query"]["max_results"] == "20"  # half of 40: authors ride along


def test_lookups_by_list_reserve_their_worst_case_before_sending(fake_x):
    from core import x_usage

    _connect()
    x_usage.set_limits(monthly_reads=10)
    sent_before = len(fake_x.requests)
    # 6 accounts with their pinned posts could carry 12 reads: more than the 10 left.
    many = _run("x.user", {"usernames": "a1,a2,a3,a4,a5,a6"})
    assert not many.ok and many.status == "usage_limit_reached" and "Nothing was sent to X" in many.response_text
    assert len(fake_x.requests) == sent_before
    assert x_usage.snapshot()["reads"] == 0  # a refused reservation holds nothing


def test_unreadable_meter_pauses_reads(fake_x):
    from core.runtime_paths import data_path

    _connect()
    data_path("x_api_usage.json").parent.mkdir(parents=True, exist_ok=True)
    data_path("x_api_usage.json").write_text("{not json", encoding="utf-8")
    result = _run("x.search", {"token": SOL})
    assert not result.ok and result.status == "usage_limit_reached"
    assert fake_x.requests == []


@pytest.mark.parametrize("status,body,expected", [
    (401, {"title": "Unauthorized"}, "unauthorized"),
    (429, {"title": "Too Many Requests"}, "rate_limited"),
    (402, {"title": "Payment Required"}, "exhausted"),
    (503, {"title": "Service Unavailable"}, "provider_unavailable"),
])
def test_x_errors_are_plain_structured_results(fake_x, status, body, expected):
    _connect()
    fake_x.script = [(status, body)]
    result = _run("x.search", {"token": SOL})
    assert not result.ok and result.status == expected
    assert BEARER not in result.response_text


def test_malformed_answer_fails_closed(fake_x):
    _connect()
    fake_x.script = [(200, ["not", "an", "object"])]
    result = _run("x.search", {"token": SOL})
    assert not result.ok and result.status == "unexpected_schema"


def test_bad_inputs_send_nothing(fake_x):
    _connect()
    assert _run("x.search", {"token": 'evil" OR from:x'}).status == "invalid_arguments"
    assert _run("x.search", {}).status == "invalid_arguments"
    assert _run("x.user_posts", {"username": "not a handle!!"}).status == "invalid_arguments"
    assert _run("x.search", {"token": SOL, "start_time": "yesterday-ish"}).status == "invalid_arguments"
    assert fake_x.requests == []


def test_counts_mode(fake_x):
    _connect()
    result = _run("x.search", {"token": "$BONK", "mode": "counts"})
    assert result.ok and result.details["total"] == 7
    assert fake_x.requests[-1]["path"] == "/2/tweets/counts/recent"


def test_profile_links_name_the_account_only_on_x_hosts(fake_x):
    _connect()
    for link in ("x.com/early_ape", "https://twitter.com/early_ape/status/1234567890123456789",
                 "https://www.x.com/early_ape/?s=20"):
        fake_x.requests.clear()
        posts = _run("x.user_posts", {"username": link})
        assert posts.ok, (link, posts.status)
        assert fake_x.requests[0]["path"] == "/2/users/by/username/early_ape", link
    fake_x.requests.clear()
    for link in ("https://evil.example/early_ape", "https://evil.example/x.com/early_ape",
                 "https://x.com.evil.example/early_ape", "ftp://x.com/early_ape"):
        assert _run("x.user_posts", {"username": link}).status == "invalid_arguments", link
    assert fake_x.requests == []
