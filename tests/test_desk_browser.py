"""The everyday browser window: a real engine, a real window, real local pages.

Each check answers a question a person would ask of a browser they use every day:
does it stay off until switched on, does it remember me after a restart, does a
private window really leave nothing, and can anything else on the machine reach in.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from core.vool_browser import desk as desk_module
from core.vool_browser import live


@pytest.fixture
def site():
    hits: list[tuple[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            hits.append((self.path, self.headers.get("Cookie", "")))
            self.send_response(200)
            self.send_header("content-type", "text/html")
            self.send_header("Set-Cookie", "seen=yes; Max-Age=86400; Path=/")
            self.end_headers()
            self.wfile.write(
                b"<title>Timetable</title><h1 id=h>plain</h1>"
                b"<script>document.getElementById('h').textContent='scripts-on'</script>"
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", hits
    server.shutdown()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def browser(home, monkeypatch):
    if not desk_module.engine_binary():
        pytest.skip("no Chromium-family engine on this machine")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("the engine's sandbox does not start for root")
    if os.name == "posix" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")) and os.uname().sysname != "Darwin":
        pytest.skip("a window needs a display")
    monkeypatch.setattr(live, "available", lambda: True)
    item = desk_module.Desk()
    yield item
    item.stop()


def _wait(check, seconds=10.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.1)
    return False


def _engine_argv(profile: Path) -> list[str]:
    """Arguments of the running engine that owns *profile*, read from the process table."""
    found: list[str] = []
    for entry in Path("/proc").iterdir() if Path("/proc").is_dir() else ():
        if not entry.name.isdigit():
            continue
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        text = [a.decode("utf-8", "replace") for a in argv if a]
        if f"--user-data-dir={profile}" in text and not any(a.startswith("--type=") for a in text):
            found = text
    return found


# -- nothing happens while it is off ---------------------------------------------


def test_nothing_starts_and_nothing_is_sent_while_the_browser_is_off(home, site, monkeypatch):
    base, hits = site
    monkeypatch.setattr(live, "available", lambda: False)
    item = desk_module.Desk()
    for private in (False, True):
        with pytest.raises(desk_module.DeskFault, match="web_off"):
            item.open(base + "/", private=private)
    assert hits == []
    assert not desk_module.profile_dir().exists()
    assert item.status()["open"] is False


def test_private_web_that_is_down_refuses_the_launch(home, site, monkeypatch):
    from core import private_web

    base, hits = site
    monkeypatch.setattr(live, "available", lambda: True)
    monkeypatch.setattr(private_web, "current_route", lambda: private_web.RouteState(kind=private_web.ROUTE_TOR, socks_port=0))
    with pytest.raises(desk_module.DeskFault, match="private_web_down"):
        desk_module.Desk().open(base + "/")
    assert hits == [] and not desk_module.profile_dir().exists()


def test_a_working_private_web_route_is_the_engines_only_way_out(home, monkeypatch):
    from core import private_web

    monkeypatch.setattr(private_web, "current_route", lambda: private_web.RouteState(kind=private_web.ROUTE_TOR, socks_port=9150))
    monkeypatch.setattr(private_web, "route_problem", lambda state=None: None)
    flags = desk_module.launch_flags(private=False)
    assert f"--proxy-server=socks5://{private_web.SOCKS_HOST}:9150" in flags
    assert any(f.startswith("--host-resolver-rules=MAP * ~NOTFOUND") for f in flags)
    assert "--disable-quic" in flags


# -- addresses -------------------------------------------------------------------


@pytest.mark.parametrize("typed, opened", [
    ("example.org", "https://example.org"),
    ("  example.org/a b  ", "https://example.org/a b"),
    ("http://127.0.0.1:8000/x", "http://127.0.0.1:8000/x"),
    ("", ""),
])
def test_a_typed_address_becomes_one_the_engine_can_open(typed, opened):
    assert desk_module.normalise_address(typed) == opened


@pytest.mark.parametrize("typed", [
    "javascript:alert(1)", "file:///etc/passwd", "chrome://settings", "ftp://example.org/",
    "data:text/html,x", "https://example.org/\nHost: other", "x" * 5000,
])
def test_an_address_that_is_not_a_web_page_is_refused(typed):
    with pytest.raises(desk_module.DeskFault, match="invalid_address"):
        desk_module.normalise_address(typed)


# -- the real window -------------------------------------------------------------


def test_the_window_runs_page_scripts_and_remembers_the_person_after_a_restart(browser, site):
    base, hits = site
    assert browser.open(base + "/first")["open"] is True
    context = browser.context_for_agent()

    async def read():
        page = next(p for p in context.pages if "/first" in p.url)
        await page.wait_for_load_state()
        return await page.text_content("#h"), await page.evaluate("navigator.webdriver")

    assert browser._call(read()) == ("scripts-on", False)
    browser.stop()
    assert browser.status()["open"] is False

    hits.clear()
    browser.open(base + "/second")
    assert _wait(lambda: any(path == "/second" for path, _ in hits))
    assert next(cookie for path, cookie in hits if path == "/second") == "seen=yes"
    browser.stop()
    history = desk_module.profile_dir() / "Default" / "History"
    with sqlite3.connect(f"file:{history}?mode=ro", uri=True) as db:
        visited = [row[0] for row in db.execute("select url from urls")]
    assert base + "/first" in visited and base + "/second" in visited


def test_the_engine_keeps_saved_sign_ins_out_of_plain_stores_and_opens_no_control_port(browser, site):
    base, _ = site
    browser.open(base + "/")
    argv = _engine_argv(desk_module.profile_dir())
    if not argv:
        pytest.skip("no process table to read on this platform")
    assert "--password-store=basic" not in argv and "--use-mock-keychain" not in argv
    assert "--disable-extensions" not in argv and "--enable-automation" not in argv
    assert "--no-sandbox" not in argv
    assert not any(a.startswith("--remote-debugging-port") for a in argv)
    assert "--remote-debugging-pipe" in argv
    assert desk_module.profile_dir().resolve().is_relative_to(desk_module.active_data_dir().resolve())


def test_a_private_window_shares_nothing_and_leaves_nothing(browser, site):
    base, hits = site
    browser.open(base + "/everyday")
    assert _wait(lambda: any(path == "/everyday" for path, _ in hits))
    browser.open(base + "/private-one", private=True)
    browser.open(base + "/private-two", private=True)
    assert _wait(lambda: sum(path.startswith("/private-") for path, _ in hits) == 2)
    # No cookie from the everyday window, and none from the other private window.
    assert [cookie for path, cookie in hits if path.startswith("/private-")] == ["", ""]
    assert browser.status()["private_windows"] == 2
    # The agent is handed the everyday window only.
    assert not any("private" in page.url for page in browser.context_for_agent().pages)

    folders = sorted(desk_module._root().glob("private-*"))
    assert len(folders) == 2
    # The person closes one private window: its folder goes at once, not at quit.
    first = browser._private[0]

    async def close_every_tab():
        for page in list(first.context.pages):
            await page.close()

    browser._call(close_every_tab())
    assert _wait(lambda: not first.profile.exists())
    assert browser.status()["private_windows"] == 1

    state = browser.stop()
    assert state == {**state, "open": False, "private_windows": 0, "private_cleanup_failed": False}
    assert list(desk_module._root().glob("private-*")) == []
    history = desk_module.profile_dir() / "Default" / "History"
    with sqlite3.connect(f"file:{history}?mode=ro", uri=True) as db:
        visited = [row[0] for row in db.execute("select url from urls")]
    assert not any("private" in url for url in visited)


def test_a_private_folder_left_by_a_crashed_run_is_removed_on_the_next_look(browser):
    stale = desk_module._root() / "private-0123456789abcdef"
    (stale / "Default").mkdir(parents=True)
    (stale / "Default" / "Cookies").write_text("left behind")
    assert browser.status()["private_cleanup_failed"] is False
    assert not stale.exists()
