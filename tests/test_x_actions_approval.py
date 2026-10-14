"""Acting on X as the user: the model only proposes; the user's approval in the app sends it, once.

X is a loopback fixture; api.x.com is never contacted. Proposals go through the model's door
(``execute_runtime_tool``); approvals go through the owner-local Settings route
(``POST /api/x/activity``), the only path that sends.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from tests._credential_intelligence_support import isolated_home, vault_home
from tests.test_x_api_reads import BEARER, KEYS, _connect

THEIR_POST = {"id": "555", "author_id": "22", "text": "gm frens", "created_at": "2026-10-13T07:00:00.000Z"}
MY_POST = {"id": "556", "author_id": "1234567890", "text": "my old take", "created_at": "2026-10-12T07:00:00.000Z"}
USERS = [{"id": "22", "username": "late_whale", "name": "Late"},
         {"id": "1234567890", "username": "fixture_owner", "name": "Owner"}]


class FakeX:
    def __init__(self):
        self.requests: list[dict] = []
        self.write_answer: tuple[int, dict] | None = None
        self.hang = threading.Event()
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

            def _record(self, method):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"null") if length else None
                outer.requests.append({"method": method, "path": urlsplit(self.path).path, "body": body,
                                       "auth": str(self.headers.get("Authorization") or "")})

            def do_GET(self):
                self._record("GET")
                path = urlsplit(self.path).path
                if path == "/2/tweets":
                    wanted = urlsplit(self.path).query
                    post = MY_POST if "556" in wanted else THEIR_POST
                    return self._answer(200, {"data": [post], "includes": {"users": USERS}})
                if path == "/2/users/by":
                    return self._answer(200, {"data": [USERS[0]]})
                return self._answer(404, {"title": "Not Found Error"})

            def _write(self, method):
                self._record(method)
                if outer.hang.is_set():
                    threading.Event().wait(3)
                if outer.write_answer:
                    return self._answer(*outer.write_answer)
                path = urlsplit(self.path).path
                if path == "/2/tweets" and method == "POST":
                    return self._answer(201, {"data": {"id": "999", "text": outer.requests[-1]["body"]["text"]}})
                return self._answer(200, {"data": {"liked": True}})

            def do_POST(self):
                self._write("POST")

            def do_DELETE(self):
                self._write("DELETE")

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
def fake_x(vault_home, monkeypatch):
    from core import x_api

    with FakeX() as server:
        monkeypatch.setattr(x_api, "API_ORIGIN", server.url)
        _connect()
        yield server


def _propose(args):
    from core.runtime_execution_tools import execute_runtime_tool

    return execute_runtime_tool("x.action.propose", args)


def _settings(body, *, client_host="127.0.0.1", headers=None):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    res = dispatch_post(path="/api/x/activity", body=body,
                        headers={"Content-Type": "application/json", **(headers or {})},
                        runtime=RuntimeServices(display_name="t"), model_name="fixture", client_host=client_host,
                        workspace_root_provider=lambda: ".")
    text = res.body.decode() if isinstance(res.body, bytes) else str(res.body)
    for secret in (BEARER, *KEYS.values()):
        assert secret not in text
    return res.status, json.loads(text)


def _hash_shown(action_id):
    """The hash the Settings panel shows for a waiting action (the model never sees it)."""
    status, overview = _settings({"action": "overview"})
    assert status == 200
    return next(a["hash"] for a in overview["pending"] if a["id"] == action_id)


def _approve(action_id, **extra):
    return _settings({"action": "approve", "action_id": action_id, "hash": _hash_shown(action_id), **extra})


def test_a_proposal_sends_nothing_and_shows_what_it_touches(fake_x):
    proposed = _propose({"kind": "reply", "post": "https://x.com/late_whale/status/555", "text": "agreed"})
    assert proposed.ok and proposed.status == "awaiting_approval", proposed.response_text
    assert "Nothing has been sent to X" in proposed.response_text
    assert fake_x.writes() == []
    status, overview = _settings({"action": "overview"})
    assert status == 200
    [waiting] = overview["pending"]
    assert waiting["kind"] == "reply" and waiting["text"] == "agreed"
    assert waiting["target"]["author"] == "late_whale" and waiting["target"]["text"] == "gm frens"


def test_the_users_approval_sends_exactly_the_shown_action_once(fake_x):
    action = _propose({"kind": "reply", "post": "555", "text": "agreed"}).details["action"]
    assert "hash" not in action  # the model's view of the action carries no approval hash
    status, sent = _approve(action["id"])
    assert status == 200 and sent["ok"], sent
    [write] = fake_x.writes()
    assert write["method"] == "POST" and write["path"] == "/2/tweets"
    assert write["body"] == {"text": "agreed", "reply": {"in_reply_to_tweet_id": "555"}}
    assert write["auth"].startswith("OAuth ")  # signed as the user
    assert sent["action"]["result"]["url"] == "https://x.com/fixture_owner/status/999"
    again_status, again = _settings({"action": "approve", "action_id": action["id"], "hash": "x"})
    assert again_status == 400 and again["state"] == "not_pending"
    assert len(fake_x.writes()) == 1
    from core import x_usage

    assert x_usage.snapshot()["actions"] == 1


@pytest.mark.parametrize("args,method,path,body", [
    ({"kind": "like", "post": "555"}, "POST", "/2/users/1234567890/likes", {"tweet_id": "555"}),
    ({"kind": "repost", "post": "555"}, "POST", "/2/users/1234567890/retweets", {"tweet_id": "555"}),
    ({"kind": "unlike", "post": "555"}, "DELETE", "/2/users/1234567890/likes/555", None),
    ({"kind": "follow", "username": "late_whale"}, "POST", "/2/users/1234567890/following", {"target_user_id": "22"}),
    ({"kind": "unfollow", "username": "@late_whale"}, "DELETE", "/2/users/1234567890/following/22", None),
    ({"kind": "delete", "post": "556"}, "DELETE", "/2/tweets/556", None),
    ({"kind": "quote", "post": "555", "text": "this"}, "POST", "/2/tweets", {"text": "this", "quote_tweet_id": "555"}),
])
def test_each_kind_sends_its_documented_request(fake_x, args, method, path, body):
    action = _propose(args).details["action"]
    status, sent = _approve(action["id"])
    assert status == 200, sent
    [write] = fake_x.writes()
    assert (write["method"], write["path"], write["body"]) == (method, path, body)


def test_declined_and_foreign_deletes_send_nothing(fake_x):
    action = _propose({"kind": "post", "text": "hello"}).details["action"]
    shown = _hash_shown(action["id"])
    assert _settings({"action": "decline", "action_id": action["id"]})[0] == 200
    status, out = _settings({"action": "approve", "action_id": action["id"], "hash": shown})
    assert status == 400 and out["state"] == "not_pending"
    refused = _propose({"kind": "delete", "post": "555"})
    assert not refused.ok and refused.status == "not_yours"
    assert fake_x.writes() == []


def test_model_cannot_approve_and_route_is_owner_local(fake_x):
    from core.runtime_execution_tools import execute_runtime_tool

    action = _propose({"kind": "post", "text": "hello"}).details["action"]
    # No tool approves or sends: an "approve" argument on a proposal is refused as unknown.
    result = execute_runtime_tool("x.action.propose", {"kind": "post", "text": "hello", "approve": True})
    assert not result.ok and result.status == "invalid_arguments"
    assert execute_runtime_tool("x.action.approve", {"action_id": action["id"]}) is None or \
        not execute_runtime_tool("x.action.approve", {"action_id": action["id"]}).ok
    shown = _hash_shown(action["id"])
    status, _ = _settings({"action": "approve", "action_id": action["id"], "hash": shown}, client_host="10.0.0.5")
    assert status == 403
    status, _ = _settings({"action": "approve", "action_id": action["id"], "hash": shown},
                          headers={"Sec-Fetch-Site": "cross-site"})
    assert status == 403
    assert fake_x.writes() == []


def test_changed_hash_and_action_limit_refuse_before_sending(fake_x):
    from core import x_usage

    action = _propose({"kind": "post", "text": "hello"}).details["action"]
    status, out = _settings({"action": "approve", "action_id": action["id"], "hash": "0" * 64})
    assert status == 400 and out["state"] == "changed"
    status, out = _settings({"action": "approve", "action_id": action["id"]})  # no hash: not the user's card
    assert status == 400 and out["state"] == "changed"
    status, out = _settings({"action": "set_limits", "monthly_actions": 0})
    assert status == 200 and out["monthly_actions"] == 0
    status, out = _approve(action["id"])
    assert status == 400 and out["state"] == "usage_limit_reached"
    assert fake_x.writes() == []
    assert x_usage.snapshot()["actions"] == 0


def test_permission_refusal_explains_read_and_write(fake_x):
    fake_x.write_answer = (403, {"title": "Forbidden", "detail": "You are not permitted to perform this action."})
    action = _propose({"kind": "post", "text": "hello"}).details["action"]
    status, out = _approve(action["id"])
    assert status == 400 and out["state"] == "plan_refused"
    assert "Read and write" in out["message"]
    assert out["action"]["state"] == "failed"


def test_lost_answer_is_unknown_and_never_resent(fake_x, monkeypatch):
    from core import x_api

    monkeypatch.setattr(x_api, "REQUEST_TIMEOUT_S", 0.5)
    fake_x.hang.set()
    action = _propose({"kind": "post", "text": "hello"}).details["action"]
    status, out = _approve(action["id"])
    assert status == 400 and out["action"]["state"] == "unknown"
    assert "check X" in out["message"]
    status, out = _settings({"action": "approve", "action_id": action["id"], "hash": "x"})
    assert status == 400 and out["state"] == "not_pending"
    assert len(fake_x.writes()) == 1


def test_expired_proposals_cannot_be_approved(fake_x, monkeypatch):
    from core import x_actions

    action = _propose({"kind": "post", "text": "hello"}).details["action"]
    shown = _hash_shown(action["id"])
    real = x_actions.time.time
    monkeypatch.setattr(x_actions.time, "time", lambda: real() + x_actions.EXPIRY_S + 5)
    status, out = _settings({"action": "approve", "action_id": action["id"], "hash": shown})
    assert status == 400 and out["state"] == "not_pending"
    assert fake_x.writes() == []


def test_bearer_only_cannot_propose(vault_home, monkeypatch):
    from core.credential_store import store_credential

    store_credential("x.api.default", json.dumps({"bearer_token": BEARER}), label="X (Twitter)")
    out = _propose({"kind": "post", "text": "hello"})
    assert not out.ok and out.status == "needs_account"


def test_limits_can_be_set_and_bad_values_refused(fake_x):
    status, out = _settings({"action": "set_limits", "monthly_reads": 250})
    assert status == 200 and out["monthly_reads"] == 250
    status, out = _settings({"action": "set_limits", "monthly_reads": -1})
    assert status == 400 and out["state"] == "invalid"
    status, out = _settings({"action": "set_limits", "monthly_reads": ""})
    assert status == 200 and out["monthly_reads"] is None


def test_parallel_approvals_never_pass_the_action_limit(fake_x):
    from concurrent.futures import ThreadPoolExecutor

    from core import x_usage

    ids = [_propose({"kind": "post", "text": f"hello {n}"}).details["action"]["id"] for n in range(4)]
    hashes = {i: _hash_shown(i) for i in ids}
    assert _settings({"action": "set_limits", "monthly_actions": 2})[0] == 200
    with ThreadPoolExecutor(4) as pool:
        outs = list(pool.map(lambda i: _settings({"action": "approve", "action_id": i, "hash": hashes[i]}), ids))
    assert sum(1 for status, _ in outs if status == 200) == 2
    assert sorted(out.get("state") for status, out in outs if status != 200) == ["usage_limit_reached"] * 2
    assert len(fake_x.writes()) == 2 and x_usage.snapshot()["actions"] == 2


def test_an_x_server_error_after_sending_is_unclear_not_nothing_sent(fake_x):
    fake_x.write_answer = (503, {"title": "Service Unavailable"})
    action = _propose({"kind": "post", "text": "hello"}).details["action"]
    status, out = _approve(action["id"])
    assert status == 400 and out["action"]["state"] == "unknown"
    assert "Nothing was sent" not in out["message"] and "check X" in out["message"]


def test_local_only_refusal_gives_the_action_back(fake_x, monkeypatch):
    import core.remote_fetch_policy as policy
    from core import x_usage

    def refuse(*args, **kwargs):
        raise policy.RemoteFetchRefusedError("local only")

    action = _propose({"kind": "post", "text": "hello"}).details["action"]
    monkeypatch.setattr(policy, "open_remote_url", refuse)
    status, out = _approve(action["id"])
    assert status == 400 and out["state"] == "refused" and out["action"]["state"] == "pending"
    assert x_usage.snapshot()["actions"] == 0 and fake_x.writes() == []


@pytest.mark.parametrize("hidden", ["‮", "⁦", "​", "﻿"])
def test_text_that_reads_differently_on_x_is_refused(fake_x, hidden):
    out = _propose({"kind": "post", "text": f"send to {hidden}evil"})
    assert not out.ok and out.status == "invalid_arguments" and "invisible" in out.response_text
    assert _settings({"action": "overview"})[1]["pending"] == []


def test_unreadable_action_list_is_shown_not_hidden_as_empty(fake_x):
    from core.runtime_paths import data_path

    _propose({"kind": "post", "text": "hello"})
    data_path("x_actions.json").write_text("{broken", encoding="utf-8")
    status, overview = _settings({"action": "overview"})
    assert status == 200 and overview["store"] == "unreadable" and overview["pending"] == []
    assert "Nothing will be sent" in overview["store_message"]


def test_a_send_cut_off_midway_becomes_unclear_and_is_never_resent(fake_x, monkeypatch):
    from core import x_actions
    from core.runtime_paths import data_path

    action = _propose({"kind": "post", "text": "hello"}).details["action"]
    path = data_path("x_actions.json")
    state = json.loads(path.read_text(encoding="utf-8"))
    state["actions"][action["id"]].update(state="sending", updated_at=0)
    path.write_text(json.dumps(state), encoding="utf-8")
    status, overview = _settings({"action": "overview"})
    [row] = [a for a in overview["recent"] if a["id"] == action["id"]]
    assert row["state"] == "unknown" and "check X" in row["message"]
    assert _settings({"action": "approve", "action_id": action["id"], "hash": "x"})[1]["state"] == "not_pending"
    assert fake_x.writes() == [] and x_actions.SENDING_STALE_S > 0


def test_the_model_only_sees_actions_from_its_own_chat(fake_x):
    from core.runtime_execution_tools import execute_runtime_tool

    mine = execute_runtime_tool("x.action.propose", {"kind": "post", "text": "mine"},
                                source_context={"session_id": "chat-a"}).details["action"]
    execute_runtime_tool("x.action.propose", {"kind": "post", "text": "other"}, source_context={"session_id": "chat-b"})
    newest = execute_runtime_tool("x.action.status", {}, source_context={"session_id": "chat-a"})
    assert newest.ok and newest.details["action"]["id"] == mine["id"]
    other = execute_runtime_tool("x.action.status", {"action_id": mine["id"]}, source_context={"session_id": "chat-b"})
    assert not other.ok and other.status == "not_found"


def test_generic_command_dispatch_cannot_reach_x_approvals(fake_x):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    action = _propose({"kind": "post", "text": "hello"}).details["action"]
    shown = _hash_shown(action["id"])
    for step in ({"action": "overview"}, {"action": "approve", "action_id": action["id"], "hash": shown}):
        res = dispatch_post(path="/api/commands/dispatch", body={"command_id": "x.activity", "input": step},
                            headers={"Content-Type": "application/json"}, runtime=RuntimeServices(display_name="t"),
                            model_name="fixture", client_host="192.168.1.50", workspace_root_provider=lambda: ".")
        text = res.body.decode() if isinstance(res.body, bytes) else str(res.body)
        assert res.status != 200 and shown not in text
    assert fake_x.writes() == []


@pytest.mark.parametrize("hidden", ["\U000e0041", "⁠", "؜", "­"])
def test_other_invisible_format_characters_are_refused(fake_x, hidden):
    out = _propose({"kind": "post", "text": f"hi{hidden}there"})
    assert not out.ok and out.status == "invalid_arguments"


def test_the_approval_hash_cannot_be_computed_from_the_text(fake_x):
    import hashlib

    action = _propose({"kind": "post", "text": "hello"}).details["action"]
    guess = hashlib.sha256(json.dumps({"kind": "post", "text": "hello"}, sort_keys=True).encode()).hexdigest()
    status, out = _settings({"action": "approve", "action_id": action["id"], "hash": guess})
    assert status == 400 and out["state"] == "changed" and fake_x.writes() == []


def test_proposing_takes_the_network_and_connector_gates(fake_x):
    from core.runtime_tool_contracts import runtime_tool_contracts

    [propose] = [c for c in runtime_tool_contracts() if c.intent == "x.action.propose"]
    assert set(propose.permission_actions) >= {"use_network_access", "access_external_providers"}


def test_a_redirected_send_is_unclear_not_nothing_sent(fake_x):
    fake_x.write_answer = (302, {"title": "moved"})
    action = _propose({"kind": "post", "text": "hello"}).details["action"]
    status, out = _approve(action["id"])
    assert status == 400 and out["action"]["state"] == "unknown" and "check X" in out["message"]


def test_posts_over_xs_weighted_280_are_refused_before_approval(fake_x):
    from core.x_platform_policy import weighted_length

    fits = "a" * 256 + " https://example.com/" + "b" * 50  # the whole link counts as 23, not its length
    assert weighted_length(fits) == 280 and len(fits) > 280
    assert _propose({"kind": "post", "text": fits}).ok
    over = "中" * 141  # CJK counts 2 each: 282
    out = _propose({"kind": "reply", "post": "555", "text": over})
    assert not out.ok and out.status == "invalid_arguments" and "282 characters as X counts them" in out.response_text


def test_publishing_keeps_a_daily_cap_and_a_refused_send_gives_the_day_back(fake_x, monkeypatch):
    import core.remote_fetch_policy as policy
    from core import usage_quota

    monkeypatch.setattr(usage_quota, "capability_limit", lambda capability, tier: 1 if capability == "x.post" else None)
    first, second = (_propose({"kind": "post", "text": t}).details["action"]["id"] for t in ("one", "two"))
    like = _propose({"kind": "like", "post": "555"}).details["action"]["id"]
    real_open = policy.open_remote_url

    def refuse(*args, **kwargs):
        raise policy.RemoteFetchRefusedError("local only")

    monkeypatch.setattr(policy, "open_remote_url", refuse)
    assert _approve(first)[1]["state"] == "refused"
    assert usage_quota.usage_today("x.post") == 0  # never left the machine: the day's post is given back
    monkeypatch.setattr(policy, "open_remote_url", real_open)
    assert _approve(first)[0] == 200
    status, out = _approve(second)
    assert status == 400 and out["state"] == "daily_limit_reached" and "Nothing was sent" in out["message"]
    assert _approve(like)[0] == 200  # likes and follows are not posts
    assert [w["path"] for w in fake_x.writes()] == ["/2/tweets", "/2/users/1234567890/likes"]


def test_publish_guidance_points_at_approval_not_at_a_missing_capability():
    from core.x_platform_policy import unsupported_capability_guidance

    text = unsupported_capability_guidance("tweet it out now")
    assert "not a capability" not in text and "approve" in text and "only your approval sends it" in text
