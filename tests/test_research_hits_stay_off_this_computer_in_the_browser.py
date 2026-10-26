"""Research never hands a search hit to the browser.

The browser resolves names and follows redirects on its own, so a hit whose checked fetch came back
short (a page the browser would once have rendered) is not rendered, whatever its name answers.
"""
from __future__ import annotations

import socket

from core.effect_gateway import named_background_effect_scope
from tools.web import web_research as wr


def _research_with_a_short_page(address: str, monkeypatch) -> list[str]:
    rendered: list[str] = []
    hit = wr.WebHit(title="Silver price", url="https://silver-prices.example/today", snippet="silver price today",
                    engine="duckduckgo_html")
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *_a, **_kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))])
    monkeypatch.setattr(wr, "_prefer_specialized_live_research", lambda _q: False)
    monkeypatch.setattr(wr, "_provider_order", lambda: ["duckduckgo_html"])
    monkeypatch.setattr(wr, "_engine_skip_reason", lambda _p: "")
    monkeypatch.setattr(wr, "_duckduckgo_html_hits", lambda *_a, **_kw: [hit])
    monkeypatch.setattr(wr, "_accept_search_hits", lambda *_a, **_kw: (True, ""))
    monkeypatch.setattr(wr, "_should_try_browser", lambda: True)
    monkeypatch.setattr(wr, "_needs_browser", lambda *_a: True)
    monkeypatch.setattr(wr, "http_fetch_text", lambda url, **_kw: {"status": "ok", "text": "Loading...", "html": "",
                                                                   "final_url": url})
    monkeypatch.setattr(wr, "browser_render", lambda url, **_kw: rendered.append(url) or {"status": "fetch_error"})
    with named_background_effect_scope("test.research"):
        wr.web_research("silver price today", max_pages=1, total_budget_s=30)
    return rendered


def test_a_hit_that_now_names_this_computer_is_not_rendered(monkeypatch):
    assert _research_with_a_short_page("127.0.0.1", monkeypatch) == []


def test_a_public_hit_is_not_rendered_either(monkeypatch):
    assert _research_with_a_short_page("93.184.216.34", monkeypatch) == []
