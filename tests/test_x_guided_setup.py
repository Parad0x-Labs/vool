"""Guided X (Twitter) setup: the Bearer Token path, the four-key account path, Test and Remove.

X is a loopback fixture that answers the documented v2 shapes and checks the OAuth 1.0a header it is
sent; api.x.com is never contacted. What a real X app answers is checked separately on the owner's
machine.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

import pytest

from tests._credential_intelligence_support import isolated_home, vault_home

BEARER = "AAAAAAAAAAAAAAAAAAAAAFixtureBearer%2Bnot%2Freal%3DAbCdEf0123456789"
KEYS = {
    "api_key": "fixtureConsumerKey0123456",
    "api_secret": "fixtureConsumerSecret0123456789abcdefghijklmnop",
    "access_token": "1234567890-fixtureAccessToken0123456789abcdefg",
    "access_token_secret": "fixtureAccessSecret0123456789abcdefghijklmn",
}
PROFILE = {"data": {"id": "2244994945", "name": "Developers", "username": "XDevelopers"}}
ME = {"data": {"id": "1234567890", "name": "Fixture Owner", "username": "fixture_owner"}}


class FakeX:
    """Answers GET /2/users/by/username/<h> for the fixture bearer and GET /2/users/me for an OAuth 1.0a
    header carrying the fixture consumer key and access token. ``script`` overrides answers in order."""

    def __init__(self, script=None):
        self.script = list(script or [])
        self.requests: list[dict[str, str]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _answer(self, status, body):
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                auth = str(self.headers.get("Authorization") or "")
                outer.requests.append({"method": "GET", "path": self.path, "auth": auth})
                if outer.script:
                    return self._answer(*outer.script.pop(0))
                if self.path.startswith("/2/users/by/username/") and auth == f"Bearer {BEARER}":
                    return self._answer(200, PROFILE)
                if self.path == "/2/users/me" and auth.startswith("OAuth "):
                    fields = dict(part.strip().split("=", 1) for part in auth[6:].split(","))
                    values = {k: unquote(v.strip('"')) for k, v in fields.items()}
                    if (values.get("oauth_consumer_key") == KEYS["api_key"] and values.get("oauth_token") == KEYS["access_token"]
                            and values.get("oauth_signature_method") == "HMAC-SHA1" and values.get("oauth_signature")):
                        return self._answer(200, ME)
                return self._answer(401, {"title": "Unauthorized", "status": 401})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake_x(vault_home, monkeypatch):
    from core import x_setup

    with FakeX() as server:
        monkeypatch.setattr(x_setup, "API_ORIGIN", server.url)
        yield server


def _post(body: dict, *, client_host: str = "127.0.0.1", headers: dict | None = None):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    res = dispatch_post(path="/api/x/setup", body=body, headers={"Content-Type": "application/json", **(headers or {})},
                        runtime=RuntimeServices(display_name="t"), model_name="fixture", client_host=client_host,
                        workspace_root_provider=lambda: ".")
    text = res.body.decode() if isinstance(res.body, bytes) else str(res.body)
    for secret in (BEARER, *KEYS.values()):
        assert secret not in text, "an X key came back in a response"
    return res.status, json.loads(text)


def _saved() -> dict:
    from core.credential_store import get_credential

    raw = get_credential("x.api.default")
    return json.loads(raw) if raw else {}


def test_oauth1_signature_matches_the_published_x_example():
    """X's documented signing example (developer docs, "Creating a signature"): same inputs, same signature."""
    from core.x_setup import OAuth1Keys, oauth1_authorization

    keys = OAuth1Keys(api_key="xvz1evFS4wEEPTGEFPHBog", api_secret="kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw",
                      access_token="370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb",
                      access_token_secret="LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE")
    url = ("https://api.twitter.com/1.1/statuses/update.json?include_entities=true"
           "&status=Hello%20Ladies%20%2B%20Gentlemen%2C%20a%20signed%20OAuth%20request%21")
    header = oauth1_authorization("POST", url, keys, nonce="kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg", timestamp=1318622958)
    assert 'oauth_signature="hCtSmYh%2BiHYCEqBWrE7C7hYmtUk%3D"' in header
    assert repr(keys) == "<X OAuth1 keys: sealed>"


def test_bearer_token_is_checked_with_one_public_read_then_saved(fake_x):
    from core import x_tools

    status, payload = _post({"action": "save_bearer", "bearer_token": BEARER})
    assert status == 200 and payload["ok"], payload
    assert [r["path"] for r in fake_x.requests] == ["/2/users/by/username/XDevelopers"]
    assert fake_x.requests[0]["method"] == "GET"
    assert _saved() == {"bearer_token": BEARER}
    assert x_tools._config("default")["bearer_token"] == BEARER  # the existing X reader sees it


@pytest.mark.parametrize(("answer", "state"), [
    ((401, {"title": "Unauthorized"}), "unauthorized"),
    ((403, {"title": "Forbidden", "reason": "client-not-enrolled"}), "plan_refused"),
    ((402, {"title": "CreditsDepleted"}), "exhausted"),
    ((429, {"title": "Too Many Requests"}), "rate_limited"),
    ((503, {"title": "Service Unavailable"}), "provider_unavailable"),
    ((200, b"<html>login</html>"), "malformed_response"),
    ((200, {"errors": [{"message": "no"}]}), "unexpected_schema"),
])
def test_a_bearer_token_x_does_not_accept_is_never_saved(fake_x, answer, state):
    fake_x.script = [answer]
    status, payload = _post({"action": "save_bearer", "bearer_token": BEARER})
    assert status == 400 and payload["state"] == state, payload
    assert "Nothing was saved" in payload["message"]
    assert _saved() == {}


@pytest.mark.parametrize("token", ["", "   ", "two words", "line\nbreak", "x" * 5000, "ünïcode-token"])
def test_a_malformed_paste_is_refused_before_anything_is_sent(fake_x, token):
    status, payload = _post({"action": "save_bearer", "bearer_token": token})
    assert status == 400 and payload["state"] in {"missing", "invalid"}, payload
    assert fake_x.requests == [] and _saved() == {}


def test_four_keys_show_the_account_first_and_save_only_on_confirm(fake_x):
    status, payload = _post({"action": "keys_check", "credentials": KEYS})
    assert status == 200 and payload["account"] == "fixture_owner", payload
    assert [r["path"] for r in fake_x.requests] == ["/2/users/me"]
    assert fake_x.requests[0]["auth"].startswith("OAuth ")
    assert _saved() == {}, "the check alone must not save anything"
    status, done = _post({"action": "keys_save", "session_id": payload["session_id"]})
    assert status == 200 and done["account"] == "fixture_owner", done
    saved = _saved()
    assert {k: saved[k] for k in KEYS} == KEYS and saved["account"] == {"username": "fixture_owner", "id": "1234567890"}
    assert len(fake_x.requests) == 1, "confirming re-uses the check; it does not ask X again"
    status, again = _post({"action": "keys_save", "session_id": payload["session_id"]})
    assert status == 400 and again["state"] == "expired", "a confirm cannot be replayed"


def test_the_two_paths_keep_each_other(fake_x):
    _post({"action": "save_bearer", "bearer_token": BEARER})
    _, check = _post({"action": "keys_check", "credentials": KEYS})
    _post({"action": "keys_save", "session_id": check["session_id"]})
    saved = _saved()
    assert saved["bearer_token"] == BEARER and saved["access_token"] == KEYS["access_token"]
    status, payload = _post({"action": "status"})
    assert payload == {"ok": True, "public_reads": True, "account": "fixture_owner"}


@pytest.mark.parametrize("change", [
    {"api_key": "someoneElsesConsumerKey"},  # X rejects the signature
    {"access_token": ""},
    {"access_token_secret": "has a space"},
])
def test_wrong_or_incomplete_keys_save_nothing(fake_x, change):
    status, payload = _post({"action": "keys_check", "credentials": {**KEYS, **change}})
    assert status == 400 and payload["state"] in {"unauthorized", "missing", "invalid"}, payload
    assert "session_id" not in payload and _saved() == {}


def test_an_account_answer_without_a_valid_handle_is_refused(fake_x):
    fake_x.script = [(200, {"data": {"id": "12", "username": "https://evil.example"}})]
    status, payload = _post({"action": "keys_check", "credentials": KEYS})
    assert status == 400 and payload["state"] == "unexpected_schema" and _saved() == {}


def test_cancel_and_expiry_drop_the_pending_keys(fake_x, monkeypatch):
    from core import x_setup

    _, first = _post({"action": "keys_check", "credentials": KEYS})
    assert _post({"action": "keys_cancel", "session_id": first["session_id"]})[0] == 200
    assert _post({"action": "keys_save", "session_id": first["session_id"]})[1]["state"] == "expired"
    _, second = _post({"action": "keys_check", "credentials": KEYS})
    monkeypatch.setattr(x_setup, "PENDING_TTL_S", -1.0)
    _, third = _post({"action": "keys_check", "credentials": KEYS})
    assert _post({"action": "keys_save", "session_id": third["session_id"]})[1]["state"] == "expired"
    assert _saved() == {}
    assert second["session_id"] != third["session_id"]


def test_unfinished_setups_are_capped(fake_x, monkeypatch):
    from core import x_setup

    monkeypatch.setattr(x_setup, "_pending", {})
    for _ in range(x_setup.MAX_PENDING):
        assert _post({"action": "keys_check", "credentials": KEYS})[0] == 200
    status, payload = _post({"action": "keys_check", "credentials": KEYS})
    assert status == 400 and payload["state"] == "busy"


def test_settings_test_rechecks_each_saved_part_and_remove_deletes(fake_x):
    from core import credential_store

    assert _post({"action": "test"})[1]["state"] == "no_key"
    _post({"action": "save_bearer", "bearer_token": BEARER})
    _, check = _post({"action": "keys_check", "credentials": KEYS})
    _post({"action": "keys_save", "session_id": check["session_id"]})
    before = len(fake_x.requests)
    status, payload = _post({"action": "test"})
    assert status == 200 and payload["state"] == "ok", payload
    assert [r["path"] for r in fake_x.requests[before:]] == ["/2/users/by/username/XDevelopers", "/2/users/me"]
    assert all(r["method"] == "GET" for r in fake_x.requests), "nothing but reads ever reaches X"
    fake_x.script = [(200, PROFILE), (401, {"title": "Unauthorized"})]
    _, payload = _post({"action": "test"})
    assert payload["state"] == "unauthorized" and payload["part"] == "account", payload
    status, payload = _post({"action": "remove"})
    assert status == 200 and not credential_store.has_credential("x.api.default")
    assert _post({"action": "test"})[1]["state"] == "no_key"


def test_local_only_stops_the_check_before_it_leaves_the_machine(vault_home, monkeypatch):
    from core import policy_engine

    monkeypatch.setattr(policy_engine, "local_only_mode", lambda: True)
    status, payload = _post({"action": "save_bearer", "bearer_token": BEARER})  # real api.x.com: refused before I/O
    assert status == 400 and payload["state"] == "refused", payload
    assert _saved() == {}


def test_x_unreachable_or_slow_saves_nothing(vault_home, monkeypatch):
    from core import x_setup

    with FakeX() as server:
        dead = server.url
    monkeypatch.setattr(x_setup, "API_ORIGIN", dead)  # nothing listens here any more
    status, payload = _post({"action": "save_bearer", "bearer_token": BEARER})
    assert status == 400 and payload["state"] == "unreachable", payload

    class Slow(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            threading.Event().wait(2.0)

    slow = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=slow.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(x_setup, "API_ORIGIN", f"http://127.0.0.1:{slow.server_port}")
        monkeypatch.setattr(x_setup, "REQUEST_TIMEOUT_S", 0.5)
        status, payload = _post({"action": "save_bearer", "bearer_token": BEARER})
        assert status == 400 and payload["state"] == "timeout", payload
    finally:
        slow.shutdown(); slow.server_close()
    assert _saved() == {}


@pytest.mark.parametrize(("body", "kw", "code"), [
    ({"action": "status"}, {"client_host": "192.168.1.20"}, 403),
    ({"action": "status"}, {"headers": {"Sec-Fetch-Site": "cross-site"}}, 403),
    ({"action": "status"}, {"headers": {"Origin": "https://evil.example"}}, 403),
    ({"action": "status", "post": "hello"}, {}, 400),
    ({"action": "post_tweet"}, {}, 400),
    ({"action": "save_bearer", "bearer_token": BEARER, "session_id": "x"}, {}, 400),
])
def test_the_route_is_owner_local_and_closed(fake_x, body, kw, code):
    status, _payload = _post(body, **kw)
    assert status == code
    assert fake_x.requests == [] and _saved() == {}
