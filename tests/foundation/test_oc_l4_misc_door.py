"""OC lane L4 outbound totality closure: the misc live sites go through THE door.

Covers `core.demo_source._bounded_get`, `core.web0_tools._http` (NULL portal env-overridable
away from localhost), and `core.kernel.repl._openrouter_chat`. Every test monkeypatches a
RAISER over ``urllib.request.urlopen`` so any remaining raw-socket path fails loudly, while
the door itself is faked to capture what would have gone on the wire.
"""
from __future__ import annotations

import json
import urllib.request

import pytest

import core.remote_fetch_policy as policy
from core.remote_fetch_policy import RemoteFetchRefusedError, remote_fetch_policy_scope


@pytest.fixture(autouse=True)
def _no_raw_socket(monkeypatch):
    """Any urlopen that is NOT the door's own call is a regression: blow up."""

    def _raiser(*args, **kwargs):  # pragma: no cover - reached only on regression
        raise AssertionError("raw urllib socket opened outside the outbound door")

    monkeypatch.setattr(urllib.request, "urlopen", _raiser)


class _BytesResponse:
    """Minimal HTTPResponse stand-in: bounded reads, like the real socket."""

    def __init__(self, body: bytes, status: int = 200) -> None:
        import io

        self._stream = io.BytesIO(body)
        self.status = status

    def read(self, n: int = -1) -> bytes:
        return self._stream.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ---------------------------------------------------------------- demo_source


def test_demo_source_bounded_get_goes_through_door_with_capped_bytes(monkeypatch):
    from core import demo_source

    captured: dict = {}
    big_body = b"x" * (demo_source._MAX_FETCH_BYTES + 500_000)  # 3.5MB > 3MB cap

    def fake_door(url, *, data=None, headers=None, method="", timeout=0.0, context=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        assert method in ("", "GET")
        return _BytesResponse(big_body)

    monkeypatch.setattr(policy, "open_remote_url", fake_door)
    text = demo_source._bounded_get(
        "https://api.github.com/repos/owner/repo/readme",
        {"Accept": "application/vnd.github.raw+json", "User-Agent": "vool-demo-planner"},
    )

    assert captured["url"] == "https://api.github.com/repos/owner/repo/readme"
    assert captured["headers"]["Accept"] == "application/vnd.github.raw+json"
    assert captured["timeout"] == 15.0
    decoded = text.encode("utf-8")
    assert len(decoded) == demo_source._MAX_FETCH_BYTES  # byte cap preserved through the door


def test_demo_source_brief_from_url_refusal_fails_closed_not_none(monkeypatch):
    from core import demo_source

    def refusing_door(url, **kwargs):
        raise RemoteFetchRefusedError("remote fetch is not permitted for this turn")

    monkeypatch.setattr(policy, "open_remote_url", refusing_door)
    with pytest.raises(RemoteFetchRefusedError):
        demo_source.brief_from_url("https://github.com/owner/repo")


# ---------------------------------------------------------------- web0_tools


def test_web0_tools_http_uses_door_against_nonloopback_portal(monkeypatch):
    import core.web0_tools as web0_tools

    portal = "http://null-portal.example.invalid"   # non-loopback, env-overridable target
    monkeypatch.setattr(web0_tools, "NULL_PORTAL_URL", portal)
    captured: dict = {}

    def fake_door(request, *, timeout=0.0, context=None):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["body"] = request.data
        return _BytesResponse(json.dumps({"templates": [{"id": "landing_page"}]}).encode())

    monkeypatch.setattr(web0_tools, "open_remote", fake_door)
    templates = web0_tools.web0_list_templates()

    assert templates[0]["id"] == "landing_page"
    assert captured["url"].startswith(portal + "/api/templates")

    response = web0_tools.web0_create_project(
        "landing_page", "example.null", "Demo",
        http=lambda m, u, **kw: web0_tools._http(m, u, **kw),
    )
    assert not response.get("error")


def test_web0_tools_veto_refuses_before_socket(monkeypatch):
    import core.web0_tools as web0_tools

    monkeypatch.setattr(web0_tools, "NULL_PORTAL_URL", "http://null-portal.example.invalid")

    def must_not_be_called(*args, **kwargs):  # pragma: no cover - veto must fire first
        raise AssertionError("socket reached despite turn-level veto")

    monkeypatch.setattr(urllib.request, "urlopen", must_not_be_called)
    with remote_fetch_policy_scope({"allow_remote_fetch": False}):
        result = web0_tools._http("GET", "http://null-portal.example.invalid/api/templates")

    assert result.get("error") is True
    assert result.get("refused") is True   # fail closed, named as a refusal — never silent success


# ---------------------------------------------------------------- kernel repl


def test_repl_openrouter_chat_routes_payload_through_door(monkeypatch):
    import core.kernel.repl as repl

    monkeypatch.setattr(
        "core.credential_store.get_credential", lambda name: "test-openrouter-key"
        if name == "llm.cloud.openrouter" else ""
    )
    captured: dict = {}

    def fake_door(request, *, timeout=0.0, context=None):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode())
        captured["auth"] = request.get_header("Authorization")
        return _BytesResponse(
            json.dumps({"choices": [{"message": {"content": '{"verdict": "ok"}'}}]}).encode()
        )

    monkeypatch.setattr(repl, "open_remote", fake_door)
    out = repl._openrouter_chat("system prompt", "user words")

    assert out == '{"verdict": "ok"}'
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["body"]["messages"][1]["content"] == "user words"
    assert captured["auth"] == "Bearer test-openrouter-key"


def test_repl_openrouter_veto_raises_before_socket(monkeypatch):
    import core.kernel.repl as repl

    monkeypatch.setattr(
        "core.credential_store.get_credential", lambda name: "k" if name == "llm.cloud.openrouter" else ""
    )

    def must_not_be_called(*args, **kwargs):  # pragma: no cover
        raise AssertionError("socket reached despite turn-level veto")

    monkeypatch.setattr(urllib.request, "urlopen", must_not_be_called)
    with remote_fetch_policy_scope({"allow_remote_fetch": False}):
        with pytest.raises(RemoteFetchRefusedError):
            repl._openrouter_chat("s", "u")
