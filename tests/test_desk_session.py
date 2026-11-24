"""VOOL's own steps in the person's VOOL browser window: a real engine, real local pages.

The question each check answers: can VOOL work on a site the way a person can (scripts,
sign-ins), while every rule about what it may do without the person still holds?
"""
from __future__ import annotations

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from core.vool_browser import desk as desk_module
from core.vool_browser import live
from tests.first_run_pact_rig import pact_rig as pact_rig
from tests.test_desk_browser import _quiet_test_engine, _wait

CHAT = "window-chat"

PAGE = b"""<title>Members</title>
<h1 id="who">checking</h1>
<div id="late"></div>
<a id="files" href="/file">Timetable file</a>
<form method="post" action="/send"><input name="note" aria-label="note">
<input name="secret" type="password" value="hunter2-on-page"><button type="submit">Send note</button></form>
<script>
  document.getElementById('who').textContent = document.cookie.includes('member=yes') ? 'Welcome back, member' : 'Please sign in';
  setTimeout(function () { document.getElementById('late').textContent = 'Late train: 23:58'; }, 150);
  fetch('http://localhost:' + location.port + '/inner-secret').catch(function () {});
</script>"""


SIGN_IN = b"""<title>Club sign-in</title><h1>Sign in to the club</h1>
<form method="post" action="/send"><input name="user" autocomplete="username" aria-label="member name">
<input name="pass" type="password" autocomplete="current-password" aria-label="member password">
<button type="submit">Sign in</button></form><p id="terms">Members only.</p>"""


@pytest.fixture
def world(pact_rig, request, monkeypatch):
    from core import policy_engine

    if not desk_module.engine_binary():
        pytest.skip("no Chromium-family engine on this machine")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("the engine's sandbox does not start for root")
    requests: list[tuple[str, str, str, bytes]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, body: bytes, kind: str = "text/html", extra: dict | None = None):
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path).path
            requests.append(("GET", self.headers.get("Host", "").split(":")[0], path, b""))
            if path == "/sign-in":
                return self._send(b"<h1>Signed in</h1>", extra={"Set-Cookie": "member=yes; Max-Age=3600; Path=/"})
            if path == "/file":
                return self._send(b"06:05 early", "application/octet-stream", {"Content-Disposition": 'attachment; filename="timetable.txt"'})
            if path == "/checked":
                # The standard widget marker; the stand-in "service" script draws the widget
                # the way the real ones do. Solving it (a click) reveals the content.
                return self._send(b"""<title>Checked</title><h1 id="state">One moment</h1>
<div class="cf-turnstile" data-sitekey="test-key" style="width:300px;height:65px;border:1px solid #888" id="widget">verify</div>
<script>document.getElementById('widget').addEventListener('click', function () {
  this.remove(); document.getElementById('state').textContent = 'Fares: 4.20 single'; });</script>""")
            if path == "/badge-only":
                return self._send(b"<title>Shop</title><h1>Open shop</h1><div class='g-recaptcha' data-sitekey='k' style='display:none'></div>")
            if path == "/opens-window":
                return self._send(b"<h1>Opener</h1><a target='_blank' href='/popup-page'>New window link</a><script>window.open('/popup-page')</script>")
            if path == "/club-sign-in":
                return self._send(SIGN_IN)
            self._send(PAGE if path == "/members" else b"<h1>Other</h1>")

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or "0"))
            requests.append(("POST", self.headers.get("Host", "").split(":")[0], urlsplit(self.path).path, body))
            self._send(b"<h1>Note received</h1>")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    _quiet_test_engine(monkeypatch, request.getfixturevalue("tmp_path"))
    policy_engine.set_operator_policy_values({"system.allow_web_fallback": True})
    status, data = pact_rig.post("/api/browser/settings", {"enabled": True})
    assert status == 200 and data["agent_in_window"] is True, data
    status, data = pact_rig.post("/api/chat/session", {"session_id": CHAT, "operation": "create"})
    assert status == 201, data
    live.stop_all()
    yield pact_rig, origin, requests
    live.stop_all()
    desk_module.stop_all()
    server.shutdown()


def session(origin):
    from core.vool_browser.desk_session import DeskSession

    item = live.session(CHAT, create=True)
    assert isinstance(item, DeskSession)
    # Only a test constructor can allow its own loopback site; no request or tool field can.
    item.fixture_origin = origin
    return item


def agent(rig, item, op, args, approve=True):
    """VOOL proposes; the person answers on the owner's door; wait for the step to end."""
    proposed = item.propose(op, args)
    assert proposed["status"] == "approval_required" and proposed["proposal"]["actor"] == "agent"
    status, data = rig.post("/api/browser/decide", {"chat": CHAT, "id": proposed["proposal"]["id"], "approve": approve})
    assert status == 200, data
    deadline = time.monotonic() + 90
    while item.state == "running" and time.monotonic() < deadline:
        time.sleep(0.05)
    assert item.state != "running"
    return item.snapshot()


def node(snap, label):
    return next(n for n in snap["nodes"] if n["label"] == label)


def test_vool_reads_a_scripted_page_signed_in_as_the_person_and_nothing_runs_before_allow(world):
    rig, origin, requests = world
    # The person signs in, in their own tab of the everyday window.
    status, data = rig.post("/api/browser/window", {"action": "open", "url": origin + "/sign-in"})
    assert status == 200 and data["open"] is True
    assert _wait(lambda: any(path == "/sign-in" for _, _, path, _ in requests))
    item = session(origin)

    proposed = item.propose("navigate", {"url": origin + "/members"})
    time.sleep(0.5)
    assert not any(path == "/members" for _, _, path, _ in requests), "a proposal alone must send nothing"
    status, _ = rig.post("/api/browser/decide", {"chat": CHAT, "id": proposed["proposal"]["id"], "approve": False})
    assert status == 200 and not any(path == "/members" for _, _, path, _ in requests)

    snap = agent(rig, item, "navigate", {"url": origin + "/members"})
    assert snap["state"] == "ready", snap
    text = snap["result"]["page"]["text"]
    # Written by the page's own script from the person's sign-in.
    assert "Welcome back, member" in text
    assert snap["result"]["page"]["authority"] == "none"
    assert snap["frame"]
    # A password already on the page is never part of what VOOL is shown.
    assert "hunter2-on-page" not in repr(snap["nodes"]) and "hunter2-on-page" not in repr(item.model_nodes())
    # The page's script tried this computer by another name: refused before any network.
    assert not any(path == "/inner-secret" for _, _, path, _ in requests)
    assert item.page_request_refusals >= 1

    # Content a script adds after load is there on the next look.
    result = live.tool_intent("vool-browser.inspect", {}, {"session_id": CHAT, "surface": "api", "platform": "web_companion"})
    assert "Late train: 23:58" in result["result"]["page"]["text"] or _wait(
        lambda: "Late train" in agent(rig, item, "scroll", {"direction": "down"})["result"]["page"]["text"], 5)


def test_a_sign_in_the_person_saved_is_filled_in_by_the_browser_and_never_shown_to_vool(world):
    rig, origin, requests = world
    kept = "kept-by-the-browser-77"
    status, data = rig.post("/api/browser/window", {"action": "open", "url": origin + "/other"})
    assert status == 200 and data["open"] is True
    main = desk_module.desk()

    # The person saves a sign-in for the site in the browser's own password manager.
    async def save():
        page = await main.context_for_agent().new_page()
        try:
            await page.goto("chrome://password-manager/passwords")
            await page.evaluate(
                """([url, password]) => chrome.passwordsPrivate.addPassword(
                    {url, username: 'dana', password, note: '', useAccountStore: false})""",
                [origin + "/club-sign-in", kept],
            )
        finally:
            await page.close()

    main._call(save())
    # It outlives the browser being closed, and is filled in again in any tab on that site:
    # in VOOL's tab too, where VOOL goes to the sign-in page.
    main.stop()
    item = session(origin)
    snap = agent(rig, item, "navigate", {"url": origin + "/club-sign-in"})
    assert snap["state"] == "ready", snap
    read = "(name) => document.querySelector('[name=' + name + ']').value"
    assert _wait(lambda: main._call(item._page.evaluate(read, "pass")) == kept), "the browser did not fill the saved sign-in"
    assert main._call(item._page.evaluate(read, "user")) == "dana"

    # VOOL looks at the page again, then works on it: a step of its own on the page.
    looks = [live.tool_intent("vool-browser.inspect", {}, {"session_id": CHAT, "surface": "api", "platform": "web_companion"})]
    looks.append(agent(rig, item, "scroll", {"direction": "down"}))
    looks.append(item.snapshot())
    shown = repr(looks) + repr(item.model_nodes()) + repr(item.nodes)
    # The name is on the page for VOOL to see; the password is nowhere in what VOOL is given.
    assert "dana" in shown and "member password" in shown
    assert kept not in shown
    # VOOL may not open the browser's password manager either.
    with pytest.raises(live.BrowserFault):
        item.propose("navigate", {"url": "chrome://password-manager/passwords"})
    assert not any(method == "POST" for method, *_ in requests)


def test_a_form_vool_sends_is_reviewed_and_sent_once_exactly_as_shown(world):
    rig, origin, requests = world
    item = session(origin)
    snap = agent(rig, item, "navigate", {"url": origin + "/members"})
    snap = agent(rig, item, "type", {"selector": node(snap, "note")["selector"], "text": "Platform 4 at noon"})
    assert not any(method == "POST" for method, *_ in requests)
    proposed = item.propose("click", {"selector": node(snap, "Send note")["selector"]})
    pending = item.snapshot()["pending"]
    assert pending["submission"], "a sending step must show what it will send"
    assert not any(method == "POST" for method, *_ in requests)
    status, _ = rig.post("/api/browser/decide", {"chat": CHAT, "id": proposed["proposal"]["id"], "approve": True})
    assert status == 200
    assert _wait(lambda: item.state != "running", 30)
    posts = [r for r in requests if r[0] == "POST"]
    assert len(posts) == 1 and b"Platform+4+at+noon" in posts[0][3], posts
    assert "Note received" in item.snapshot()["result"]["page"]["text"]


def test_a_click_cannot_save_a_file_or_open_a_window_and_local_addresses_are_refused(world):
    rig, origin, requests = world
    item = session(origin)
    snap = agent(rig, item, "navigate", {"url": origin + "/members"})
    agent(rig, item, "click", {"selector": node(snap, "Timetable file")["selector"]})
    time.sleep(1.0)
    assert not (desk_module.downloads_dir() / "timetable.txt").exists()

    snap = agent(rig, item, "navigate", {"url": origin + "/opens-window"})
    context = desk_module.desk().context_for_agent()
    before = len(context.pages)
    # A link made to open another window, clicked with the person's Allow: the page it
    # asks for opens in VOOL's own tab, read like any other, and no second window or
    # tab is left behind.
    snap = agent(rig, item, "click", {"selector": node(snap, "New window link")["selector"]})
    assert snap["state"] == "ready" and snap["url"].endswith("/popup-page"), (snap["state"], snap["url"])
    assert "Other" in snap["result"]["page"]["text"]
    assert len(context.pages) == before and item._page.url.endswith("/popup-page")
    # A page that opens a window by script, with nobody clicking: that window does not stay.
    agent(rig, item, "navigate", {"url": origin + "/opens-window?again"})
    time.sleep(0.5)
    assert len(context.pages) == before

    for target in ("http://localhost:9/x", "http://192.168.1.1/", "http://169.254.169.254/latest/meta-data/"):
        with pytest.raises(live.BrowserFault, match="destination_refused"):
            item.propose("navigate", {"url": target})


def test_ending_the_session_closes_vools_tab_only_and_a_closed_tab_is_replaced(world):
    rig, origin, requests = world
    rig.post("/api/browser/window", {"action": "open", "url": origin + "/mine"})
    item = session(origin)
    agent(rig, item, "navigate", {"url": origin + "/members"})
    context = desk_module.desk().context_for_agent()
    assert sorted(urlsplit(p.url).path for p in context.pages) == ["/members", "/mine"]

    # The person closes VOOL's tab by hand: the next allowed step opens a fresh one.
    desk_module.desk()._call(item._page.close())
    snap = agent(rig, item, "navigate", {"url": origin + "/members"})
    assert snap["state"] == "ready" and snap["title"] == "Members"

    item.stop()
    assert item.state == "stopped"
    assert [urlsplit(p.url).path for p in context.pages] == ["/mine"]
    assert desk_module.desk().status()["open"] is True


def test_a_private_chat_and_the_switched_off_choice_keep_the_separate_view(world):
    from core.private_chat import set_private
    from core.vool_browser.desk_session import DeskSession

    rig, origin, requests = world
    status, data = rig.post("/api/chat/session", {"session_id": "quiet-chat", "operation": "create"})
    assert status == 201
    set_private("quiet-chat", True)
    quiet = live.session("quiet-chat", create=True)
    assert type(quiet) is live.LiveSession and not isinstance(quiet, DeskSession)
    quiet.stop()

    status, data = rig.post("/api/browser/settings", {"agent_in_window": False})
    assert status == 200 and data["agent_in_window"] is False and data["enabled"] is True, data
    assert type(live.session(CHAT, create=True)) is live.LiveSession
    status, data = rig.post("/api/browser/settings", {"agent_in_window": "yes"})
    assert status == 409
    # Switching the Browser off and on keeps the person's choice.
    rig.post("/api/browser/settings", {"enabled": False})
    status, data = rig.post("/api/browser/settings", {"enabled": True})
    assert data["agent_in_window"] is False


def test_a_human_check_is_handed_to_the_person_and_vool_reads_on_after_they_pass_it(world):
    rig, origin, requests = world
    item = session(origin)
    context_ = {"session_id": CHAT, "surface": "api", "platform": "web_companion"}
    snap = agent(rig, item, "navigate", {"url": origin + "/checked"})
    assert snap["state"] == "ready"
    check = snap["result"]["human_check"]
    assert check["required"] is True and "Do not try to pass" in check["note"]
    assert "4.20" not in snap["result"]["page"]["text"]
    # The widget is not something VOOL is offered to act on.
    assert not [n for n in item.model_nodes() if "verify" in str(n.get("label", "")).lower()]
    seen = live.tool_intent("vool-browser.inspect", {}, context_)
    assert seen["result"]["human_check"]["required"] is True

    # The person passes the check themselves, in the tab.
    desk_module.desk()._call(item._page.click("#widget"))
    seen = live.tool_intent("vool-browser.inspect", {}, context_)
    assert "Fares: 4.20 single" in seen["result"]["page"]["text"]
    assert "human_check" not in seen["result"]

    # A hidden marker (a site that only scores visits in the background) is not a check.
    snap = agent(rig, item, "navigate", {"url": origin + "/badge-only"})
    assert "human_check" not in snap["result"] and "Open shop" in snap["result"]["page"]["text"]


def test_vool_does_not_read_its_tab_after_the_person_took_it_to_another_site(world):
    rig, origin, requests = world
    item = session(origin)
    context_ = {"session_id": CHAT, "surface": "api", "platform": "web_companion"}
    agent(rig, item, "navigate", {"url": origin + "/members"})
    other = origin.replace("127.0.0.1", "localhost")
    desk_module.desk()._call(item._page.goto("about:blank"))
    seen = live.tool_intent("vool-browser.inspect", {}, context_)
    assert seen == {"ok": False, "status": "page_moved", "note": seen["note"]} and other not in repr(seen)


def test_vool_keeps_working_in_its_tab_while_the_person_looks_at_another_tab(world):
    rig, origin, requests = world
    item = session(origin)
    snap = agent(rig, item, "navigate", {"url": origin + "/members"})
    assert snap["state"] == "ready"
    # The person opens a page of their own: their tab is now the one in front.
    status, data = rig.post("/api/browser/window", {"action": "open", "url": origin + "/other"})
    assert status == 200 and data["open"] is True
    assert _wait(lambda: any(path == "/other" for _, _, path, _ in requests))
    time.sleep(1)
    first_frame = snap["frame"]

    snap = agent(rig, item, "type", {"selector": node(snap, "note")["selector"], "text": "Still here"})
    assert snap["state"] == "ready", snap
    snap = agent(rig, item, "navigate", {"url": origin + "/checked"})
    assert snap["state"] == "ready" and snap["title"] == "Checked", snap["result"].get("observation_failure_class")
    assert snap["frame"] and snap["frame"] != first_frame
    result = live.tool_intent("vool-browser.inspect", {}, {"session_id": CHAT, "surface": "api", "platform": "web_companion"})
    assert result["ok"] is True and "One moment" in result["result"]["page"]["text"]


def test_vools_tab_outlives_a_closed_pane_and_closes_when_nothing_has_used_it(world, monkeypatch):
    from core.vool_browser import desk_session

    rig, origin, requests = world
    item = session(origin)
    snap = agent(rig, item, "navigate", {"url": origin + "/members"})
    snap = agent(rig, item, "type", {"selector": node(snap, "note")["selector"], "text": "half-written"})
    tab = item._page
    # Nobody has the pane open. The separate view would end here; this tab is in the
    # person's own window and stays, with what VOOL had typed still in it.
    item._owner_deadline = 0.0
    time.sleep(6.5)
    assert item.state == "ready" and not tab.is_closed()
    snap = agent(rig, item, "scroll", {"direction": "down"})
    assert snap["state"] == "ready" and item._page is tab
    assert desk_module.desk()._call(tab.locator("input[name=note]").input_value()) == "half-written"

    # Left alone for long enough, VOOL's tab is closed; the person's window is not.
    rig.post("/api/browser/window", {"action": "open", "url": origin + "/mine"})
    monkeypatch.setattr(desk_session, "IDLE_SECONDS", 1)
    assert _wait(lambda: item.closed and tab.is_closed(), 15)
    assert desk_module.desk().status()["open"] is True


def test_the_pane_and_settings_say_what_is_true_for_the_window_in_every_language(world):
    from core.i18n import catalog_for
    from core.i18n.locales import ui_catalog_tags
    from core.vool_browser.live_page import render

    rig, origin, requests = world
    item = session(origin)
    agent(rig, item, "navigate", {"url": origin + "/checked"})
    status, state = rig.get("/api/browser/state", query={"chat": CHAT})
    assert status == 200 and state["in_window"] is True and state["result"]["human_check"]["required"] is True
    # The person asks for VOOL's tab: it is shown, through their own door only.
    status, data = rig.post("/api/browser/window", {"action": "show", "chat": CHAT})
    assert status == 200 and data["shown"] is True
    status, data = rig.post("/api/browser/window", {"action": "show", "chat": "no-such-chat"})
    assert status == 200 and data["shown"] is False

    keys = ("live.note_window", "live.human_check", "live.show_tab",
            "settings.row.live_browser.in_window", "settings.row.live_browser.in_window_help")
    english = catalog_for("en")
    # A page whose scripts run must not be described as one where they stay off.
    assert "scripts stay off" not in english.text("live.note_window")
    assert "scripts stay off" not in english.text("settings.row.live_browser.help")
    codes = ui_catalog_tags()
    assert len(codes) >= 17
    for code in codes:
        page = render(CHAT, code)
        assert 'id="humanCheck"' in page and 'id="showTab"' in page
        for key in keys:
            text = catalog_for(code).text(key)
            assert text and (code == "en" or text != english.text(key)), (code, key)
            if key.startswith("live."):
                assert text.replace("<", "\\u003c") in page or text in page, (code, key)
        if code != "en":
            assert catalog_for(code).text("settings.row.live_browser.help") != english.text("settings.row.live_browser.help")
