"""L3 outbound totality closure: the keyless scraper lanes go through THE one
outbound door (core.remote_fetch_policy.open_remote).

- tools/web/google_html.py `_fetch` (Yahoo/Brave HTML scraping, user query in
  the URL) must ask the door, not open its own socket.
- tools/web/ddg_instant.py must do the same.
- core/runtime_execution_tools.py `web.fetch` must honor the per-turn ContextVar
  veto BEFORE any I/O, surfacing refusal as its existing "disabled_by_policy"
  result shape.
"""
from __future__ import annotations

import json

import pytest


def _veto_scope():
    from core.remote_fetch_policy import remote_fetch_policy_scope

    return remote_fetch_policy_scope({"allow_remote_fetch": False})


def _forbid_urlopen(monkeypatch):
    """Any raw urlopen under an active veto is a test failure."""

    import urllib.request

    def _boom(*args, **kwargs):
        raise AssertionError("raw socket opened despite veto")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


class _DoorCapture:
    """Stands in for the door: records the URL it received, returns a fake 200."""

    def __init__(self):
        self.urls: list[str] = []

    def __call__(self, request, *, timeout):
        self.urls.append(str(getattr(request, "full_url", "")))

        class _Resp:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

            def read(self_inner, n=-1):
                return b"<html>ok</html>"

        return _Resp()


def test_google_html_fetch_goes_through_door(monkeypatch):
    import tools.web.google_html as gh

    capture = _DoorCapture()
    monkeypatch.setattr(gh, "open_remote", capture)
    hits = gh.google_html_search("sam altman news", max_results=3)
    assert hits == []  # fake body parses to nothing; that's fine
    assert len(capture.urls) >= 1
    assert any("sam+altman+news" in u for u in capture.urls), capture.urls


def test_ddg_instant_goes_through_door(monkeypatch):
    import tools.web.ddg_instant as di

    captured: dict[str, str] = {}

    def _door(request, *, timeout):
        captured["url"] = str(getattr(request, "full_url", ""))

        class _Resp:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

            def read(self_inner, n=-1):
                return json.dumps({"AbstractText": "an answer"}).encode()

        return _Resp()

    monkeypatch.setattr(di, "open_remote", _door)
    payload = di.ddg_instant_answer("who is ada lovelace")
    assert payload["AbstractText"] == "an answer"
    assert "ada+lovelace" in captured["url"], captured


@pytest.mark.parametrize("module_fn", ["google_html", "ddg_instant"])
def test_veto_refuses_scrapers_before_socket(monkeypatch, module_fn):
    from core.remote_fetch_policy import RemoteFetchRefusedError

    _forbid_urlopen(monkeypatch)
    with _veto_scope():
        if module_fn == "google_html":
            import tools.web.google_html as gh

            with pytest.raises(RemoteFetchRefusedError):
                # RemoteFetchRefusedError must propagate, not read as empty.
                gh.google_html_search("private query")
        else:
            import tools.web.ddg_instant as di

            with pytest.raises(RemoteFetchRefusedError):
                di.ddg_instant_answer("private query")


def test_web_fetch_tool_honors_veto_before_io(monkeypatch):
    import core.runtime_execution_tools as ret

    _forbid_urlopen(monkeypatch)
    with _veto_scope():
        result = ret._web_fetch(
            {"url": "https://example.invalid/page"},
            source_context={"allow_remote_fetch": True},
        )
    assert result.handled is True
    assert result.ok is False
    assert result.status == "disabled_by_policy"
    assert "disabled by policy" in result.response_text


def test_web_fetch_tool_success_still_through_door(monkeypatch):
    """The happy path keeps its byte-cap semantics and goes through the door."""
    import core.remote_fetch_policy as rfp
    import core.runtime_execution_tools as ret

    seen: dict[str, object] = {}

    def _door(request, *, timeout):
        seen["url"] = str(getattr(request, "full_url", ""))
        seen["reported"] = None
        real_note = rfp.note_remote_fetch_attempt
        real_note(seen["url"])

        class _Resp:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

            def geturl(self_inner):
                return seen["url"]

            headers = {"content-type": "text/html"}

            def read(self_inner, n=-1):
                return b"<html>hello page</html>"

        return _Resp()

    monkeypatch.setattr(ret.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("raw socket opened outside the door")))
    # Patch the name the tool binds inside the function body.
    import core.remote_fetch_policy as door_module

    original_open_remote = door_module.open_remote
    monkeypatch.setattr(door_module, "open_remote", _door)
    try:
        result = ret._web_fetch(
            {"url": "https://example.invalid/page"},
            source_context={"allow_remote_fetch": True},
        )
    finally:
        monkeypatch.setattr(door_module, "open_remote", original_open_remote)

    assert result.ok is True
    assert result.status == "ok"
    assert seen["url"] == "https://example.invalid/page"
    assert "hello page" in result.details["text"]
