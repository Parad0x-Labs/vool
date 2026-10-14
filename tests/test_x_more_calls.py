"""The rest of the X API from normal chat: account search, Spaces, DMs, mutes/blocks, trends, X's own
usage count, and the newer approval-gated actions (hide replies, mute, DM, lists).

X is a loopback fixture answering the documented v2 shapes; api.x.com is never contacted. Every
read goes through the model's door; every action is only proposed there and sent by the owner's
approval in Settings (``POST /api/x/activity``).
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from tests._credential_intelligence_support import isolated_home, vault_home
from tests.test_x_api_reads import _connect

ME = "1234567890"
BOB = {"id": "33", "username": "bob_trades", "name": "Bob", "public_metrics": {"followers_count": 5000}}
MY_POST = {"id": "556", "author_id": ME, "text": "my take", "created_at": "2026-10-12T07:00:00.000Z"}
REPLY = {"id": "557", "author_id": "33", "text": "spam reply", "created_at": "2026-10-12T08:00:00.000Z"}


class FakeX:
    def __init__(self):
        self.requests: list[dict] = []
        self.refuse: dict[str, int] = {}
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

            def _serve(self, method):
                parts = urlsplit(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                query = {k: v[0] for k, v in parse_qs(parts.query).items()}
                outer.requests.append({"method": method, "path": parts.path, "query": query, "body": body,
                                       "auth": str(self.headers.get("Authorization") or "")})
                for prefix, status in outer.refuse.items():
                    if parts.path.startswith(prefix):
                        return self._answer(status, {"title": "Forbidden", "detail": "Your client app is not configured with the appropriate oauth1 app permissions for this endpoint."})
                p = parts.path
                if method != "GET":
                    return self._answer(200, {"data": {"id": "77", "done": True}})
                routes = {
                    "/2/users/me": {"data": {"id": ME, "username": "fixture_owner", "name": "Owner"}},
                    "/2/users/by/username/bob_trades": {"data": BOB},
                    "/2/users/by": {"data": [BOB]},
                    "/2/users/search": {"data": [BOB], "meta": {"next_token": "n2"}},
                    "/2/spaces/search": {"data": [{"id": "1OdKrBnaEXYKX", "title": "Solana memes", "state": "live",
                                                   "creator_id": "33", "participant_count": 120}],
                                         "includes": {"users": [BOB]}},
                    "/2/dm_events": {"data": [{"id": "d1", "event_type": "MessageCreate", "text": "gm", "sender_id": "33",
                                               "created_at": "2026-10-13T07:00:00.000Z", "dm_conversation_id": "33-1234567890"}],
                                     "includes": {"users": [BOB]}},
                    f"/2/users/{ME}/muting": {"data": [BOB]},
                    "/2/users/personalized_trends": {"data": [{"trend_name": "#solana", "category": "Crypto", "post_count": 9000}]},
                    "/2/usage/tweets": {"data": {"project_usage": "812", "project_cap": "10000", "cap_reset_day": 5}},
                    "/2/lists/9001": {"data": {"id": "9001", "name": "alpha", "owner_id": ME}},
                    "/2/lists/9002": {"data": {"id": "9002", "name": "theirs", "owner_id": "33"}},
                }
                if p == "/2/tweets":
                    post = REPLY if "557" in query.get("ids", "") else MY_POST
                    return self._answer(200, {"data": [post], "includes": {"users": [BOB]}})
                if p in routes:
                    return self._answer(200, routes[p])
                return self._answer(404, {"title": "Not Found Error"})

            def do_GET(self):
                self._serve("GET")

            def do_POST(self):
                self._serve("POST")

            def do_PUT(self):
                self._serve("PUT")

            def do_DELETE(self):
                self._serve("DELETE")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def writes(self):
        return [r for r in self.requests if r["method"] != "GET"]

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def x(vault_home, monkeypatch):
    from core import x_api

    with FakeX() as server:
        monkeypatch.setattr(x_api, "API_ORIGIN", server.url)
        _connect()
        yield server


def _run(intent, args):
    from core.runtime_execution_tools import execute_runtime_tool

    return execute_runtime_tool(intent, args, source_context={"session_id": "chat-1"})


def test_account_search_spaces_dms_and_account_reads(x):
    found = _run("x.user", {"search": "memecoin traders"})
    assert found.ok and found.details["users"][0]["username"] == "bob_trades"
    spaces = _run("x.spaces", {"query": "solana", "state": "live"})
    assert spaces.ok and spaces.details["spaces"][0]["creator"] == "bob_trades"
    assert spaces.details["spaces"][0]["url"] == "https://x.com/i/spaces/1OdKrBnaEXYKX"
    dms = _run("x.dms", {})
    assert dms.ok and dms.details["messages"] == [{"id": "d1", "at": "2026-10-13T07:00:00.000Z", "from": "bob_trades",
                                                    "text": "gm", "conversation": "33-1234567890"}]
    assert next(r for r in x.requests if r["path"] == "/2/dm_events")["auth"].startswith("OAuth ")  # the user's own keys
    muted = _run("x.account", {"kind": "muted"})
    assert muted.ok and muted.details["users"][0]["username"] == "bob_trades"
    trends = _run("x.account", {"kind": "trends"})
    assert trends.ok and trends.details["trends"][0]["trend_name"] == "#solana"
    usage = _run("x.account", {"kind": "x_usage"})
    assert usage.ok and usage.details["x_usage"]["project_cap"] == "10000"


def test_everything_read_is_counted_against_the_users_limit(x):
    from core import x_usage

    before = x_usage.snapshot()["reads"]
    _run("x.spaces", {"query": "solana"})
    _run("x.account", {"kind": "trends"})
    # one Space and its host, one trend
    assert x_usage.snapshot()["reads"] - before == 3


def test_a_call_the_plan_does_not_include_is_said_plainly(x):
    x.refuse["/2/dm_events"] = 403
    out = _run("x.dms", {})
    assert not out.ok and out.status == "plan_refused"
    assert "Read and write and Direct message" in out.response_text
    x.refuse["/2/tweets/search/all"] = 403
    archive = _run("x.search", {"query": "solana", "archive": True})
    assert not archive.ok and "Full-archive search is not included in your X plan" in archive.response_text
    assert "Recent search, the last 7 days, still works" in archive.response_text


def _settings(body):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    res = dispatch_post(path="/api/x/activity", body=body, headers={"Content-Type": "application/json"},
                        runtime=RuntimeServices(display_name="t"), model_name="fixture", client_host="127.0.0.1",
                        workspace_root_provider=lambda: ".")
    return res.status, json.loads(res.body.decode() if isinstance(res.body, bytes) else res.body)


def _approve(action_id):
    _, overview = _settings({"action": "overview"})
    shown = next(a for a in overview["pending"] if a["id"] == action_id)
    return shown, _settings({"action": "approve", "action_id": action_id, "hash": shown["hash"]})


@pytest.mark.parametrize("args,method,path,body,summary", [
    ({"kind": "hide_reply", "post": "557"}, "PUT", "/2/tweets/557/hidden", {"hidden": True}, "Hide a reply by @bob_trades"),
    ({"kind": "mute", "username": "bob_trades"}, "POST", f"/2/users/{ME}/muting", {"target_user_id": "33"}, "Mute @bob_trades"),
    ({"kind": "unmute", "username": "@bob_trades"}, "DELETE", f"/2/users/{ME}/muting/33", None, "Unmute @bob_trades"),
    ({"kind": "dm", "username": "bob_trades", "text": "gm, saw your call"}, "POST",
     "/2/dm_conversations/with/33/messages", {"text": "gm, saw your call"}, "Send a direct message to @bob_trades"),
    ({"kind": "list_create", "name": "alpha callers", "private": True}, "POST", "/2/lists",
     {"name": "alpha callers", "description": "", "private": True}, '"alpha callers" (private)'),
    ({"kind": "list_create", "name": "open book", "description": "who I read"}, "POST", "/2/lists",
     {"name": "open book", "description": "who I read", "private": False}, '"open book" (public)'),
    ({"kind": "list_add", "list_id": "9001", "username": "bob_trades"}, "POST", "/2/lists/9001/members",
     {"user_id": "33"}, "Add to your list: alpha @bob_trades"),
])
def test_new_actions_wait_for_approval_then_send_exactly_once(x, args, method, path, body, summary):
    proposed = _run("x.action.propose", args)
    assert proposed.ok and proposed.status == "awaiting_approval", proposed.response_text
    assert x.writes() == []
    shown, (status, sent) = _approve(proposed.details["action"]["id"])
    assert summary in shown["summary"]
    assert status == 200, sent
    [write] = x.writes()
    assert (write["method"], write["path"], write["body"]) == (method, path, body)


def test_someone_elses_list_and_bad_text_are_refused_before_approval(x):
    theirs = _run("x.action.propose", {"kind": "list_add", "list_id": "9002", "username": "bob_trades"})
    assert not theirs.ok and theirs.status == "not_yours"
    hidden = _run("x.action.propose", {"kind": "dm", "username": "bob_trades", "text": "hi‮there"})
    assert not hidden.ok and hidden.status == "invalid_arguments"
    long_name = _run("x.action.propose", {"kind": "list_create", "name": "x" * 26})
    assert not long_name.ok
    assert x.writes() == []


def test_a_dm_refused_for_permissions_names_the_dm_permission(x):
    x.refuse["/2/dm_conversations"] = 403
    proposed = _run("x.action.propose", {"kind": "dm", "username": "bob_trades", "text": "gm"})
    _, (status, out) = _approve(proposed.details["action"]["id"])
    assert status == 400 and "Read and write and Direct message" in out["message"]
    assert out["action"]["state"] == "failed"


def test_a_refused_send_gives_back_the_action_and_the_days_post(x):
    """X refused it (4xx), so it did nothing: it must not use up the user's action limit or post cap."""
    from core import usage_quota, x_usage

    x.refuse["/2/tweets"] = 403
    before = x_usage.snapshot()["actions"]
    proposed = _run("x.action.propose", {"kind": "post", "text": "gm"})
    _, (status, out) = _approve(proposed.details["action"]["id"])
    assert status == 400 and out["action"]["state"] == "failed"
    assert x_usage.snapshot()["actions"] == before
    assert usage_quota.usage_today("x.post") == 0


def test_a_new_lists_card_shows_its_description(x):
    proposed = _run("x.action.propose", {"kind": "list_create", "name": "open book", "description": "who I read"})
    shown, _ = _approve(proposed.details["action"]["id"])
    assert shown["text"] == "who I read" and "(public)" in shown["summary"]


def test_reads_with_no_stated_size_hold_room_for_a_full_answer(x):
    """Trends state no page size; with 10 reads left the call is held back rather than overrun."""
    from core import x_usage

    used = x_usage.snapshot()["reads"]
    x_usage.set_limits(monthly_reads=used + 10)
    out = _run("x.account", {"kind": "trends"})
    assert not out.ok and out.status == "usage_limit_reached"
    assert all(r["path"] != "/2/users/personalized_trends" for r in x.requests)


@pytest.mark.parametrize("text,intent", [
    ("any live spaces on x about solana?", "x.spaces"),
    ("read my x dms", "x.dms"),
    ("who have i muted", "x.account"),
    ("mute @bob_trades", "x.action.propose"),
    ("find twitter accounts about memecoins", "x.user"),
])
def test_new_calls_are_seated_from_normal_chat(text, intent):
    from core.tool_demand_signals import resolve_demand_signals

    assert intent in resolve_demand_signals(text).explicit_intents
