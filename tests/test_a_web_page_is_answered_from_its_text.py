"""Asked about a web page, VOOL answers from what the page says, never with its markup.

Measured on 1cbf433 (fresh-user test, 2026-10-29), Web lookups on:

    "can you summarise this page for me https://www.gov.uk/browse/driving"
        -> "Fetched `...` — 20000 character(s). (Truncated.)" and then raw HTML
    "in two sentences, what does https://example.com say"
        -> "Fetched `https://example.com` — 576 character(s)." and nothing else
    a page that answered HTTP 403 -> "is behind a login"

`web.fetch` returned the page's bytes as its text, the URL lane printed them, and no lane asked a
model to answer the question that came with the address. These tests drive the real chat door with
only the model wire and the page's server faked.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import pytest

_PAGE = """<!doctype html><html lang="en"><head><title>Driving and transport - GOV.UK</title>
<style>.govuk-header{display:block}</style><script>window.GOVUK={analytics:true}</script></head>
<body><nav><a href="/">Home</a></nav><main><h1>Driving and transport</h1>
<p>Includes car tax, MOT and driving licences.</p>
<ul><li><a href="/vehicle-tax">Tax your vehicle</a></li><li><a href="/mot">Book an MOT test</a></li></ul>
</main><script>trackPage()</script></body></html>"""

_SUMMARY = "This GOV.UK page covers driving and transport: vehicle tax, MOT tests and driving licences."


class _Response(io.BytesIO):
    def __init__(self, body: bytes, url: str, content_type: str = "text/html; charset=utf-8") -> None:
        super().__init__(body)
        self.status = 200
        self._url = url
        self.headers = {"content-type": content_type}

    def geturl(self) -> str:
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def chat(monkeypatch, enable_web):
    from tests.gauntlet import harness

    harness.install(monkeypatch)
    harness.SCRIPT.reset()
    # Web lookups on, as in the measurement. The runtime the harness builds reloads the policy
    # from disk, so the switch is held at the one reader every lane asks.
    from core import policy_engine

    monkeypatch.setattr(policy_engine, "allow_web_fallback", lambda: True)
    opened: list[str] = []
    pages: dict[str, object] = {}

    def _urlopen(request, timeout=None, **kwargs):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        if url.startswith(("http://127.0.0.1", "http://localhost")):
            raise urllib.error.URLError("test: no local model server")
        opened.append(url)
        page = pages.get(url)
        if isinstance(page, Exception):
            raise page
        if page is None:
            raise urllib.error.URLError("test: no such page")
        return _Response(str(page).encode("utf-8"), url)

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)

    def say(text: str) -> str:
        from core.web.api.service import dispatch_post

        res = dispatch_post(
            path="/api/chat",
            body={"stream": False, "session_id": "web-page", "turn_id": f"t-{len(opened)}-{abs(hash(text))}",
                  "messages": [{"role": "user", "content": text}]},
            headers={},
            runtime=harness.runtime(),
            model_name="gauntlet",
            workspace_root_provider=lambda: str(harness.PROJECT_ROOT),
            client_host="127.0.0.1",
        )
        assert res.status == 200, res.body[:400]
        return str((json.loads(res.body.decode("utf-8")).get("message") or {}).get("content") or "")

    seen: list[str] = []

    def _record(request) -> bool:
        seen.append("\n".join(str(m.get("content") or "") for m in list(getattr(request, "messages", None) or [])
                               if isinstance(m, dict)) + "\n" + str(getattr(request, "prompt", "")))
        return False

    harness.SCRIPT.rules.insert(0, (_record, ""))
    say.opened = opened
    say.pages = pages
    say.script = harness.SCRIPT
    say.seen = seen
    return say


def _page_prompts(chat) -> list[str]:
    return [text for text in chat.seen if "page_text" in text]


def _when_the_page_is_read(chat, reply) -> None:
    chat.script.when(lambda request: "page_text" in str(getattr(request, "messages", "")), reply)


def test_a_summary_ask_is_answered_from_the_page_text(chat) -> None:
    url = "https://www.gov.uk/browse/driving"
    chat.pages[url] = _PAGE
    _when_the_page_is_read(chat, _SUMMARY)

    reply = chat(f"can you summarise this page for me {url}")

    assert chat.opened == [url]
    assert _SUMMARY in reply
    for markup in ("<html", "<script", "<style", "govuk-header", "trackPage", "</"):
        assert markup not in reply, markup
    prompts = _page_prompts(chat)
    assert prompts, "the answering model never saw the page"
    # The model reads the page's words, not its markup.
    assert "Includes car tax, MOT and driving licences." in prompts[-1]
    assert "Driving and transport - GOV.UK" in prompts[-1]
    assert "<script" not in prompts[-1] and "window.GOVUK" not in prompts[-1]


def test_a_question_about_the_page_gets_an_answer_not_a_character_count(chat) -> None:
    url = "https://example.com"
    chat.pages[url] = (
        "<html><head><title>Example Domain</title></head><body><h1>Example Domain</h1>"
        "<p>This domain is for use in illustrative examples in documents.</p></body></html>"
    )
    _when_the_page_is_read(chat, "It says the domain exists for examples in documents. Anyone may use it in examples without asking.")

    reply = chat(f"in two sentences, what does {url} say")

    assert "It says the domain exists for examples in documents. Anyone may use it in examples without asking." in reply
    assert "character(s)" not in reply


def test_without_a_model_answer_the_person_still_gets_the_readable_page(chat) -> None:
    url = "https://www.gov.uk/browse/driving"
    chat.pages[url] = _PAGE
    _when_the_page_is_read(chat, RuntimeError("test: the model is down"))

    reply = chat(f"can you summarise this page for me {url}")

    assert "Includes car tax, MOT and driving licences." in reply
    assert "could not write the answer" in reply
    for markup in ("<html", "<script", "govuk-header", "trackPage"):
        assert markup not in reply, markup


def test_asking_for_the_page_shows_its_text_without_markup(chat) -> None:
    url = "https://www.gov.uk/browse/driving"
    chat.pages[url] = _PAGE

    reply = chat(f"open {url} and show me the content")

    assert "Driving and transport - GOV.UK" in reply
    assert "Includes car tax, MOT and driving licences." in reply
    assert "- Tax your vehicle" in reply
    for markup in ("<html", "<script", "govuk-header", "trackPage", "character(s)"):
        assert markup not in reply, markup
    assert not _page_prompts(chat), "showing the page needs no model"


def test_a_403_is_a_refusal_not_a_login(chat) -> None:
    url = "https://www.example.org/blocked"
    chat.pages[url] = urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

    reply = chat(f"can you summarise this page for me {url}")

    assert "login" not in reply.lower()
    assert "403" in reply and "refused" in reply


def test_a_401_is_still_a_login(chat) -> None:
    url = "https://www.example.org/members"
    chat.pages[url] = urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)

    reply = chat(f"open {url} and show me the content")

    assert "behind a login" in reply


def test_a_json_answer_is_shown_as_sent() -> None:
    from core.runtime_execution_tools import _fetched_page_text

    text, title, readable = _fetched_page_text(
        b'{"slideshow": {"title": "<b>Sample</b>"}}', content_type="application/json", content_encoding=""
    )
    assert text == '{"slideshow": {"title": "<b>Sample</b>"}}'
    assert (title, readable) == ("", False)


def test_a_compressed_page_in_its_own_charset_is_read() -> None:
    import gzip

    from core.runtime_execution_tools import _fetched_page_text

    raw = gzip.compress("<html><body><p>Café crème</p></body></html>".encode("latin-1"))
    text, _title, readable = _fetched_page_text(
        raw, content_type="text/html; charset=ISO-8859-1", content_encoding="gzip"
    )
    assert readable and text == "Café crème"
