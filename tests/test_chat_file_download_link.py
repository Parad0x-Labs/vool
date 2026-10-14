"""A file the assistant built shows as a download link, and only that relative link ever does.

Seen on a v0.7.1 film build: "give me that as a spreadsheet" built and stored the file, but the chat
showed the raw reply 'Here is **weekly_grocery_budget.xlsx** (3.8 KB): [Download ...](/api/chat/
deliverable?id=dlv_...&session=...)'. The renderer made anchors only from http(s) links, and the
runtime writes the download link relative to the page.

Driven in a real browser against the shipped page: the reply renders a link, a click in the desktop
app goes to its native save sheet with the chat and file id, a browser click downloads, and every
other relative, protocol-relative, javascript: or malformed target stays plain text.
"""

from __future__ import annotations

import pytest

from core.vool_chat_page import render_vool_chat_html
from tests import served_browser

HTML = render_vool_chat_html()
FILE_ID = "dlv_" + "3c" * 16
CHAT = "openclaw:" + "a1" * 10
LINK = f"/api/chat/deliverable?id={FILE_ID}&session={CHAT}"
REPLY = f"Here is **weekly_grocery_budget.xlsx** (3.8 KB): [Download weekly_grocery_budget.xlsx]({LINK})"


def _route(route):
    request = route.request
    if request.resource_type == "document" and "/api/chat/deliverable" not in request.url:
        route.fulfill(status=200, content_type="text/html", body=HTML)
        return
    if "/api/chat/deliverable" in request.url:
        route.fulfill(
            status=200,
            headers={"Content-Disposition": 'attachment; filename="weekly_grocery_budget.xlsx"'},
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            body=b"PK\x03\x04 spreadsheet bytes",
        )
        return
    route.fulfill(status=200, content_type="application/json", body="{}")


@pytest.fixture(scope="module")
def page():
    ctx, browser = served_browser.launch_chromium()
    try:
        page = browser.new_page(accept_downloads=True)
        page.route("**/*", _route)
        page.goto("http://vool.test/chat")
        page.wait_for_timeout(220)
        yield page
    finally:
        browser.close()
        ctx.stop()


def _render(page, text: str) -> dict:
    return page.evaluate(
        """(text) => {
            logEl.innerHTML = '';
            addMsg('assistant', text);
            const body = logEl.querySelector('.msg:last-child') || logEl;
            const a = body.querySelector('a.chat-deliverable');
            return {
              text: body.textContent,
              anchors: Array.from(body.querySelectorAll('a')).map((x) => x.getAttribute('href')),
              link: a && {href: a.href, label: a.textContent,
                          id: a.dataset.deliverableId, session: a.dataset.deliverableSession},
            };
        }""",
        text,
    )


def test_the_file_reply_shows_a_download_link_not_raw_markdown(page) -> None:
    shown = _render(page, REPLY)
    assert shown["link"] == {
        "href": "http://vool.test" + LINK,
        "label": "Download weekly_grocery_budget.xlsx",
        "id": FILE_ID,
        "session": CHAT,
    }
    assert "](/api/chat/deliverable" not in shown["text"]
    assert "weekly_grocery_budget.xlsx" in shown["text"]


def test_in_the_desktop_app_a_click_opens_the_native_save_with_the_chat_and_file(page) -> None:
    _render(page, REPLY)
    calls = page.evaluate(
        """async () => {
            const calls = [];
            window.pywebview = {api: {save_deliverable: (session, id) => { calls.push([session, id]); return Promise.resolve({ok: true}); }}};
            const before = location.href;
            logEl.querySelector('a.chat-deliverable').click();
            await new Promise((r) => setTimeout(r, 50));
            delete window.pywebview;
            return {calls, stayed: location.href === before};
        }"""
    )
    assert calls == {"calls": [[CHAT, FILE_ID]], "stayed": True}


def test_a_failed_native_save_is_said_on_the_link(page) -> None:
    _render(page, REPLY)
    title = page.evaluate(
        """async () => {
            window.pywebview = {api: {save_deliverable: () => Promise.resolve({ok: false, message: 'That file is no longer available.'})}};
            const a = logEl.querySelector('a.chat-deliverable');
            a.click();
            await new Promise((r) => setTimeout(r, 50));
            delete window.pywebview;
            return a.title;
        }"""
    )
    assert title == "That file is no longer available."


def test_in_a_browser_a_click_downloads_the_file(page) -> None:
    _render(page, REPLY)
    with page.expect_download() as caught:
        page.click("a.chat-deliverable")
    download = caught.value
    assert download.suggested_filename == "weekly_grocery_budget.xlsx"
    assert download.url == "http://vool.test" + LINK


@pytest.mark.parametrize(
    "target",
    [
        "/api/files/raw?path=/etc/passwd",                                     # another relative door
        "/settings",                                                           # any other page path
        "javascript:alert(1)",                                                 # script
        "//evil.example/api/chat/deliverable?id=" + FILE_ID + "&session=" + CHAT,  # another origin
        "/api/chat/deliverable/../files/raw?id=" + FILE_ID + "&session=" + CHAT,   # path that resolves elsewhere
        "/api/chat/deliverable?id=dlv_notanid&session=" + CHAT,               # malformed id
        "/api/chat/deliverable?id=" + FILE_ID,                                 # no chat named
        "/api/chat/deliverable?id=" + FILE_ID + "&session=" + CHAT + '"onmouseover="alert(1)',  # attribute break-out
    ],
)
def test_no_other_relative_or_script_target_becomes_a_link(page, target) -> None:
    shown = _render(page, f"See [this file]({target}) now.")
    assert shown["link"] is None
    assert shown["anchors"] == [], shown["anchors"]
    assert "[this file](" in shown["text"]

