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
    status, sent = _settings({"action": "approve", "action_id": action["id"], "hash": action["hash"]})
    assert status == 200 and sent["ok"], sent
    [write] = fake_x.writes()
    assert write["method"] == "POST" and write["path"] == "/2/tweets"
    assert write["body"] == {"text": "agreed", "reply": {"in_reply_to_tweet_id": "555"}}
    assert write["auth"].startswith("OAuth ")  # signed as the user
    assert sent["action"]["result"]["url"] == "https://x.com/fixture_owner/status/999"
    again_status, again = _settings({"action": "approve", "action_id": action["id"]})
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
    status, sent = _settings({"action": "approve", "action_id": action["id"]})
    assert status == 200, sent
    [write] = fake_x.writes()
    assert (write["method"], write["path"], write["body"]) == (method, path, body)


def test_declined_and_foreign_deletes_send_nothing(fake_x):
    action = _propose({"kind": "post", "text": "hello"}).details["action"]
    assert _settings({"action": "decline", "action_id": action["id"]})[0] == 200
    status, out = _settings({"action": "approve", "action_id": action["id"]})
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
    status, _ = _settings({"action": "approve", "action_id": action["id"]}, client_host="10.0.0.5")
    assert status == 403
    status, _ = _settings({"action": "approve", "action_id": action["id"]}, headers={"Sec-Fetch-Site": "cross-site"})
    assert status == 403
    assert fake_x.writes() == []


def test_changed_hash_and_action_limit_refuse_before_sending(fake_x):
    from core import x_usage

    action = _propose({"kind": "post", "text": "hello"}).details["action"]
    status, out = _settings({"action": "approve", "action_id": action["id"], "hash": "0" * 64})
    assert status == 400 and out["state"] == "changed"
    status, out = _settings({"action": "set_limits", "monthly_actions": 0})
    assert status == 200 and out["monthly_actions"] == 0
    status, out = _settings({"action": "approve", "action_id": action["id"]})
    assert status == 400 and out["state"] == "usage_limit_reached"
    assert fake_x.writes() == []
    assert x_usage.snapshot()["actions"] == 0


def test_permission_refusal_explains_read_and_write(fake_x):
    fake_x.write_answer = (403, {"title": "Forbidden", "detail": "You are not permitted to perform this action."})
    action = _propose({"kind": "post", "text": "hello"}).details["action"]
    status, out = _settings({"action": "approve", "action_id": action["id"]})
    assert status == 400 and out["state"] == "plan_refused"
    assert "Read and write" in out["message"]
    assert out["action"]["state"] == "failed"


def test_lost_answer_is_unknown_and_never_resent(fake_x, monkeypatch):
    from core import x_api

    monkeypatch.setattr(x_api, "REQUEST_TIMEOUT_S", 0.5)
    fake_x.hang.set()
    action = _propose({"kind": "post", "text": "hello"}).details["action"]
    status, out = _settings({"action": "approve", "action_id": action["id"]})
    assert status == 400 and out["action"]["state"] == "unknown"
    assert "check X" in out["message"]
    status, out = _settings({"action": "approve", "action_id": action["id"]})
    assert status == 400 and out["state"] == "not_pending"
    assert len(fake_x.writes()) == 1


def test_expired_proposals_cannot_be_approved(fake_x, monkeypatch):
    from core import x_actions

    action = _propose({"kind": "post", "text": "hello"}).details["action"]
    real = x_actions.time.time
    monkeypatch.setattr(x_actions.time, "time", lambda: real() + x_actions.EXPIRY_S + 5)
    status, out = _settings({"action": "approve", "action_id": action["id"]})
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
