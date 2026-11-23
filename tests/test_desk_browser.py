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
    _quiet_test_engine(monkeypatch, home)
    item = desk_module.Desk()
    yield item
    item.stop()


def _quiet_test_engine(monkeypatch, home):
    """Test launches use the engine's dummy credential store and a downloads folder of
    their own. The home folder is never moved: on macOS the engine then cannot find the
    person's keychain and the system raises a "Keychain Not Found" window."""
    from core.vool_browser.engine import CREDENTIAL_ISOLATION_FLAGS

    monkeypatch.setattr(desk_module, "extra_engine_flags", CREDENTIAL_ISOLATION_FLAGS)
    folder = Path(home).parent / (Path(home).name + "-person") / "Downloads" / "VOOL Downloads"
    monkeypatch.setattr(desk_module, "downloads_dir", lambda: folder)


def start_page(browser) -> str:
    """The address of VOOL's start page in the lasting window."""
    from core.vool_browser import home as start

    address = start.address(browser._main)
    assert address.startswith("chrome-extension://") and address.endswith("/home.html"), address
    return address


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


def test_the_persons_tabs_carry_no_engine_start_up_notice_on_either_way_out(browser, site, monkeypatch):
    """The engine shows a start-up warning bar that names VOOL's own launch options.

    A bar takes room from the page, so it shows as a shorter page area. The person's tab
    must be exactly as tall as a tab made later, which never carries start-up notices.
    """
    from core import private_web

    base, hits = site

    async def page_areas():
        context = browser.context_for_agent()
        opened = [p for p in context.pages if not p.is_closed()]
        later = await context.new_page()
        heights = [await p.evaluate("window.innerHeight") for p in opened]
        expected = await later.evaluate("window.innerHeight")
        await later.close()
        return heights, expected

    browser.open(base + "/first")
    heights, expected = browser._call(page_areas())
    assert heights and all(height == expected for height in heights), (heights, expected)
    browser.stop()

    # Private web adds the option that keeps name lookups on the route: same result.
    monkeypatch.setattr(private_web, "current_route", lambda: private_web.RouteState(kind=private_web.ROUTE_TOR, socks_port=9))
    monkeypatch.setattr(private_web, "route_problem", lambda state=None: None)
    assert any(flag.startswith("--host-resolver-rules") for flag in desk_module.launch_flags(private=False))
    browser.open("")
    heights, expected = browser._call(page_areas())
    assert heights and all(height == expected for height in heights), (heights, expected)


def test_an_address_the_person_opens_never_lands_in_the_tab_vool_is_working_in(browser, site):
    base, hits = site
    browser.open(base + "/mine")
    vools = browser._call(browser.tab_for_agent())
    assert vools.url == "about:blank"
    browser.open(base + "/second")
    assert _wait(lambda: any(path == "/second" for path, _ in hits))
    assert vools.url == "about:blank"
    context = browser.context_for_agent()
    assert sorted(p.url.rsplit("/", 1)[-1] for p in context.pages if p is not vools) == ["mine", "second"]


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


def test_the_window_starts_on_a_start_page_with_bookmarks_and_a_home_button_that_last(browser, site):
    """What a person expects of any browser: it opens on a start page, not an empty tab;
    the bookmarks bar and the home button are there; a bookmark and a home page of their
    own choosing are still there after the browser was closed."""
    base, hits = site
    shown = ("bookmark_bar.show_on_all_tabs", "browser.show_home_button", "homepage", "homepage_is_newtabpage")
    read = """(keys) => new Promise((done) => chrome.settingsPrivate.getAllPrefs((all) =>
        done(Object.fromEntries(all.filter((p) => keys.includes(p.key)).map((p) => [p.key, p.value])))))"""

    async def look(change: bool):
        context = browser.context_for_agent()
        first = [p.url for p in context.pages if not p.is_closed()]
        page = await context.new_page()
        try:
            await page.goto("chrome://bookmarks/")
            if change:
                await page.evaluate(
                    "(url) => new Promise((done) => chrome.bookmarks.create({parentId: '1', title: 'Timetable', url}, done))",
                    base + "/timetable",
                )
            kept = await page.evaluate("() => new Promise((done) => chrome.bookmarks.search('Timetable', (found) => done(found.map((b) => b.url))))")
            await page.goto("chrome://settings/appearance")
            if change:
                await page.evaluate(
                    """(url) => new Promise((done) => chrome.settingsPrivate.setPref('homepage', url, '', () =>
                        chrome.settingsPrivate.setPref('homepage_is_newtabpage', false, '', done)))""",
                    base + "/home",
                )
            return first, kept, await page.evaluate(read, list(shown))
        finally:
            await page.close()

    browser.open("")
    first, kept, settings = browser._call(look(change=False))
    # VOOL's own start page, which lives on this computer: nothing was fetched for it.
    assert first == [start_page(browser)], first
    assert kept == [] and hits == []
    assert settings == {
        "bookmark_bar.show_on_all_tabs": True, "browser.show_home_button": True,
        "homepage": "", "homepage_is_newtabpage": True,
    }
    # An address opened next takes the unused start tab instead of leaving it behind.
    browser.open(base + "/first")
    assert _wait(lambda: any(path == "/first" for path, _ in hits))
    assert [p.url for p in browser.context_for_agent().pages] == [base + "/first"]

    browser._call(look(change=True))
    browser.stop()
    browser.open("")
    first, kept, settings = browser._call(look(change=False))
    assert first == [start_page(browser)], first
    assert kept == [base + "/timetable"]
    assert (settings["homepage"], settings["homepage_is_newtabpage"]) == (base + "/home", False)
    assert settings["bookmark_bar.show_on_all_tabs"] is True and settings["browser.show_home_button"] is True


def choose_on_start_up(browser, choice: int, pages: tuple[str, ...] = ()) -> None:
    """The person's own choice under "On start-up" in the browser's settings: 1 continues
    where they left off, 4 opens pages of their choosing, 5 opens the new tab page."""

    async def choose():
        page = await browser.context_for_agent().new_page()
        try:
            await page.goto("chrome://settings/onStartup")
            await page.evaluate(
                """([choice, pages]) => new Promise((done) => chrome.settingsPrivate.setPref('session.startup_urls', pages, '', () =>
                    chrome.settingsPrivate.setPref('session.restore_on_startup', choice, '', done)))""",
                [choice, list(pages)],
            )
        finally:
            await page.close()

    browser._call(choose())


def open_tabs(browser) -> list[str]:
    return sorted(p.url for p in browser.context_for_agent().pages if not p.is_closed())


def test_the_window_reopens_the_persons_tabs_only_when_they_chose_to_continue(browser, site):
    base, hits = site

    def asked():
        return sorted({path for path, _ in hits if path != "/favicon.ico"})

    # Left as the browser comes, a new start shows the start page and asks no site for anything.
    browser.open(base + "/one")
    browser.open(base + "/two")
    assert _wait(lambda: asked() == ["/one", "/two"])
    browser.stop()
    hits.clear()
    browser.open("")
    assert open_tabs(browser) == [start_page(browser)]
    assert asked() == []

    # The person chooses "Continue where you left off", opens two pages, and VOOL works in a tab.
    choose_on_start_up(browser, 1)
    browser.open(base + "/three")
    browser.open(base + "/four")
    vools = browser._call(browser.tab_for_agent())
    browser._call(vools.goto(base + "/vools-own-work"))
    assert _wait(lambda: asked() == ["/four", "/three", "/vools-own-work"])
    # VOOL closes the browser, as when the app quits or the way out changes.
    browser.stop()

    # The next start brings back their two tabs and nothing else: not the tab VOOL worked
    # in, no empty tab beside them, and no start-up notice on any of them.
    browser.open("")
    assert _wait(lambda: open_tabs(browser) == [base + "/four", base + "/three"]), open_tabs(browser)

    async def page_areas():
        context = browser.context_for_agent()
        theirs = [p for p in context.pages if not p.is_closed()]
        later = await context.new_page()
        areas = [await p.evaluate("[window.innerHeight, document.getElementById('h').textContent]") for p in theirs]
        expected = await later.evaluate("window.innerHeight")
        await later.close()
        return areas, expected

    areas, expected = browser._call(page_areas())
    assert areas == [[expected, "scripts-on"], [expected, "scripts-on"]], (areas, expected)
    # An address asked for next opens beside them, and all three are there after one more restart.
    browser.open(base + "/five")
    assert _wait(lambda: open_tabs(browser) == [base + "/five", base + "/four", base + "/three"]), open_tabs(browser)
    browser.stop()
    browser.open("")
    assert _wait(lambda: open_tabs(browser) == [base + "/five", base + "/four", base + "/three"]), open_tabs(browser)

    # They go back to starting on the new tab page: the next start reopens nothing.
    choose_on_start_up(browser, 5)
    browser.stop()
    hits.clear()
    browser.open("")
    assert open_tabs(browser) == [start_page(browser)]
    assert asked() == []


def test_the_window_starts_on_the_pages_the_person_chose(browser, site):
    base, hits = site
    browser.open(base + "/somewhere")
    choose_on_start_up(browser, 4, (base + "/mail", base + "/news"))
    browser.stop()
    hits.clear()
    browser.open("")
    assert _wait(lambda: {"/mail", "/news"} <= {path for path, _ in hits})
    assert open_tabs(browser) == [base + "/mail", base + "/news"]
    assert not any(path == "/somewhere" for path, _ in hits)


def test_the_engine_keeps_saved_sign_ins_out_of_plain_stores_and_opens_no_control_port(browser, site):
    base, _ = site
    browser.open(base + "/")
    # What the product passes, before the test-only dummy credential store is added.
    product = desk_module.launch_flags(private=False)
    assert not set(product) & set(desk_module.NEVER_PASSED)
    assert desk_module.extra_engine_flags and "extra_engine_flags: tuple[str, ...] = ()" in Path(desk_module.__file__).read_text()
    argv = _engine_argv(desk_module.profile_dir())
    if not Path("/proc/self/cmdline").exists():
        pytest.skip("no process table to read on this platform")
    # The running engine was started on VOOL's own profile and no other.
    assert argv, "no engine process is running on the VOOL profile"
    assert [a for a in argv if a.startswith("--user-data-dir=")] == [f"--user-data-dir={desk_module.profile_dir()}"]
    test_only = set(desk_module.extra_engine_flags)
    assert not (set(argv) - test_only) & set(desk_module.NEVER_PASSED)
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


# -- safe defaults ---------------------------------------------------------------


def test_starting_preferences_are_written_once_and_never_over_the_persons_own(home):
    import json

    profile = desk_module.profile_dir()
    assert desk_module.seed_preferences(profile) is True
    target = profile / "Default" / "Preferences"
    written = json.loads(target.read_text())
    assert written["https_only_mode_enabled"] is True
    assert written["profile"]["cookie_controls_mode"] == 1
    assert written["safebrowsing"]["enabled"] is False and written["search"]["suggest_enabled"] is False
    # The starting search is an entry of the person's own, with no new-tab or suggestion address.
    search = written["default_search_provider_data"]["template_url_data"]
    assert (search["prepopulate_id"], search["new_tab_url"], search["suggestions_url"]) == (0, "", "")
    assert written["default_search_provider"]["guid"] == search["synced_guid"]
    assert written["download"]["default_directory"] == str(desk_module.downloads_dir())
    # The person switched a protection off in the browser: the next start keeps their choice.
    written["https_only_mode_enabled"] = False
    target.write_text(json.dumps(written))
    assert desk_module.seed_preferences(profile) is False
    assert json.loads(target.read_text())["https_only_mode_enabled"] is False


def test_downloads_folder_is_the_persons_own_and_outside_vool_data(home):
    folder = desk_module.downloads_dir().resolve()
    assert folder == (Path.home() / "Downloads" / "VOOL Downloads").resolve()
    assert not folder.is_relative_to(desk_module.active_data_dir().resolve())
    assert not desk_module.active_data_dir().resolve().is_relative_to(folder)


@pytest.fixture
def files_site():
    hits: list[tuple[str, str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            hits.append((self.headers.get("Host", ""), self.path, self.headers.get("DNT", "")))
            self.send_response(200)
            if self.path == "/file":
                self.send_header("content-type", "application/octet-stream")
                self.send_header("Content-Disposition", 'attachment; filename="timetable.txt"')
                self.end_headers()
                self.wfile.write(b"06:05 early service")
                return
            self.send_header("content-type", "text/html")
            self.end_headers()
            self.wfile.write(b'<h1>Files</h1><a id="get" href="/file">Timetable</a>')

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_port, hits
    server.shutdown()


def test_a_download_is_saved_under_its_own_name_in_the_downloads_folder(browser, files_site):
    port, hits = files_site
    browser.open(f"http://127.0.0.1:{port}/list")
    context = browser.context_for_agent()

    async def click():
        page = next(p for p in context.pages if p.url.endswith("/list"))
        await page.click("#get")

    browser._call(click())
    saved = desk_module.downloads_dir() / "timetable.txt"
    assert _wait(saved.is_file), sorted(p.name for p in desk_module.downloads_dir().glob("*"))
    assert saved.read_bytes() == b"06:05 early service"
    # Nothing of the download sits inside VOOL's own data.
    assert not [p for p in desk_module.active_data_dir().rglob("timetable*")]
    assert {dnt for _, _, dnt in hits} == {"1"}


def test_a_plain_unencrypted_site_gets_nothing_until_the_person_chooses(browser, files_site):
    import socket

    port, hits = files_site
    name = "plain-site.test"
    try:
        if socket.gethostbyname(name) != "127.0.0.1":
            raise OSError
    except OSError:
        pytest.skip("needs a non-local name that resolves to this machine")
    browser.open(f"http://127.0.0.1:{port}/start")
    context = browser.context_for_agent()

    async def go():
        page = next(p for p in context.pages if p.url.endswith("/start"))
        await page.evaluate(f"location.href='http://{name}:{port}/list'")
        await page.wait_for_timeout(2500)

    browser._call(go())
    assert not [host for host, _, _ in hits if host.startswith(name)]


# -- Private web carries everything, or nothing goes ------------------------------


@pytest.fixture
def socks():
    """A real SOCKS5 relay on this machine that records what it was asked to reach."""
    import select
    import socket
    import socketserver
    import struct

    asked: list[tuple[int, str]] = []

    class Relay(socketserver.BaseRequestHandler):
        def handle(self):
            client = self.request
            try:
                _, count = client.recv(2)
                client.recv(count)
                client.sendall(b"\x05\x00")
                _, _, _, kind = client.recv(4)
                if kind == 3:
                    host = client.recv(client.recv(1)[0]).decode()
                elif kind == 1:
                    host = socket.inet_ntoa(client.recv(4))
                else:
                    return
                port = struct.unpack(">H", client.recv(2))[0]
                asked.append((kind, host))
                # The far end: every name leads to this machine's test site.
                upstream = socket.create_connection(("127.0.0.1", port), timeout=5)
            except (OSError, ValueError, IndexError):
                return
            client.sendall(b"\x05\x00\x00\x01" + bytes(6))
            with upstream:
                while True:
                    ready, _, _ = select.select([client, upstream], [], [], 10)
                    if not ready:
                        return
                    for sock in ready:
                        data = sock.recv(65536)
                        if not data:
                            return
                        (upstream if sock is client else client).sendall(data)

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    server = Server(("127.0.0.1", 0), Relay)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1], asked
    server.shutdown()


def test_switching_private_web_on_closes_the_direct_window_and_the_next_one_sends_only_through_the_route(
    browser, files_site, socks, monkeypatch
):
    from core import private_web

    port, hits = files_site
    socks_port, asked = socks
    browser.open(f"http://127.0.0.1:{port}/direct")
    browser.open(f"http://127.0.0.1:{port}/quiet", private=True)
    assert browser.status()["open"] is True and asked == []

    # The person switches Private web on. Both windows were started on the direct way out.
    monkeypatch.setattr(private_web, "current_route", lambda: private_web.RouteState(kind=private_web.ROUTE_TOR, socks_port=socks_port))
    monkeypatch.setattr(private_web, "route_problem", lambda state=None: None)
    desk_module._DESKS["test"] = browser
    try:
        desk_module.route_changed()
    finally:
        desk_module._DESKS.pop("test", None)
    state = browser.status()
    assert state["open"] is False and state["private_windows"] == 0
    assert list(desk_module._root().glob("private-*")) == []

    # A name this machine cannot resolve by itself: only the far end of the route knows it.
    hits.clear()
    browser.open(f"http://far-end-only.example:{port}/routed")
    assert _wait(lambda: (3, "far-end-only.example") in asked), asked
    # Every request of that window went through the route, each by name, never as an address
    # this machine looked up. Nothing reached the site around the route.
    assert all(kind == 3 for kind, _ in asked), asked
    assert hits == []
    # The engine's own background services stay silent: the page is all the route was asked for.
    assert {host for _, host in asked} == {"far-end-only.example"}, asked

    # The route goes down while the window is open: the next open refuses and the window is gone.
    monkeypatch.setattr(private_web, "route_problem", lambda state=None: "down")
    with pytest.raises(desk_module.DeskFault, match="private_web_down"):
        browser.open(f"http://127.0.0.1:{port}/after")
    assert browser.status()["open"] is False
    assert not any(path == "/after" for _, path, _ in hits)


def test_the_engine_asks_no_search_company_for_anything_at_start_or_for_a_new_tab(
    browser, files_site, socks, monkeypatch
):
    """Left with its built-in search, the engine asks that company's site for something at
    every start and builds each new tab from that company's servers.

    Every request of these windows goes through the recording route, by name, so what the
    route was asked for is the complete list of what the engine asked the network for.
    """
    from core import private_web

    port, _ = files_site
    socks_port, asked = socks
    monkeypatch.setattr(private_web, "current_route", lambda: private_web.RouteState(kind=private_web.ROUTE_TOR, socks_port=socks_port))
    monkeypatch.setattr(private_web, "route_problem", lambda state=None: None)

    def others():
        return {host for _, host in asked} - {"far-end-only.example"}

    browser.open(f"http://far-end-only.example:{port}/first")
    assert _wait(lambda: (3, "far-end-only.example") in asked), asked

    async def new_tab():
        page = await browser.context_for_agent().new_page()
        await page.goto("chrome://newtab/")
        return page.url

    opened = browser._call(new_tab())
    # A new tab is VOOL's own start page, which lives on this computer.
    assert opened == start_page(browser), opened
    assert not _wait(others, seconds=12), asked

    # A private window starts from a profile of its own: the same holds there.
    asked.clear()
    browser.open(f"http://far-end-only.example:{port}/quiet", private=True)
    assert _wait(lambda: (3, "far-end-only.example") in asked), asked
    assert not _wait(others, seconds=12), asked


def test_a_site_keeps_its_own_cookies_and_gets_none_when_another_site_embeds_it(browser):
    """Sign-ins last because the visited site keeps its cookies. A site embedded in another
    site's page (an advert, a like button, a tracker) gets none of its cookies there.

    The cookie is one that asks to be sent everywhere (SameSite=None; Secure), the kind
    trackers use; the engine honours Secure for this machine's own name without a certificate.
    """
    seen: list[tuple[str, str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            host = self.headers.get("Host", "").split(":")[0]
            seen.append((host, self.path, self.headers.get("Cookie", "")))
            self.send_response(200)
            if self.path == "/embedded.js":
                self.send_header("content-type", "text/javascript")
                self.send_header("Set-Cookie", "follow=you; Max-Age=86400; Path=/; SameSite=None; Secure")
                self.end_headers()
                return
            self.send_header("content-type", "text/html")
            if self.path == "/sign-in":
                self.send_header("Set-Cookie", "signed=in; Max-Age=86400; Path=/; SameSite=None; Secure")
            self.end_headers()
            port = self.server.server_port
            self.wfile.write(
                f"<title>page</title><script src='http://localhost:{port}/embedded.js'></script>".encode()
                if self.path.startswith("/news") else b"<title>page</title>"
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_port

    def cookies(host, path):
        return [cookie for h, p, cookie in seen if (h, p) == (host, path)]

    try:
        # The person signs in at a site and comes back to it: the site knows them.
        browser.open(f"http://localhost:{port}/sign-in")
        assert _wait(lambda: cookies("localhost", "/sign-in"))
        browser.open(f"http://localhost:{port}/account")
        assert _wait(lambda: cookies("localhost", "/account"))
        assert cookies("localhost", "/account") == ["signed=in"]
        # Another site's page embeds that site, twice. It is not told who the person is,
        # and the cookie it tries to leave on the first visit is not there on the second.
        browser.open(f"http://127.0.0.1:{port}/news-1")
        assert _wait(lambda: len(cookies("localhost", "/embedded.js")) == 1)
        browser.open(f"http://127.0.0.1:{port}/news-2")
        assert _wait(lambda: len(cookies("localhost", "/embedded.js")) == 2)
        assert cookies("localhost", "/embedded.js") == ["", ""], seen
        # Visited again by the person, the site still knows them, and only by its own cookie.
        browser.open(f"http://localhost:{port}/account-again")
        assert _wait(lambda: cookies("localhost", "/account-again"))
        assert cookies("localhost", "/account-again") == ["signed=in"]
    finally:
        server.shutdown()


# -- the owner's HTTP door, with the real switch ---------------------------------

from tests.first_run_pact_rig import pact_rig as pact_rig


def test_the_owner_door_opens_the_window_only_after_the_real_switch_is_on(pact_rig, site, request):
    from core import policy_engine

    if not desk_module.engine_binary():
        pytest.skip("no Chromium-family engine on this machine")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("the engine's sandbox does not start for root")
    base, hits = site
    request.addfinalizer(desk_module.stop_all)
    _quiet_test_engine(request.getfixturevalue("monkeypatch"), request.getfixturevalue("tmp_path"))

    # Fresh home: nothing was switched on, so the door refuses and nothing is sent.
    status, data = pact_rig.post("/api/browser/window", {"action": "open", "url": base + "/early"})
    assert status == 409 and data["error"] == "web_off", data
    assert hits == []
    status, data = pact_rig.get("/api/browser/window")
    assert status == 200 and data["open"] is False and data["available"] is False

    policy_engine.set_operator_policy_values({"system.allow_web_fallback": True})
    status, data = pact_rig.post("/api/browser/settings", {"enabled": True})
    assert status == 200, data

    for body in ({"action": "sideways"}, {"action": "open", "url": 7}, {"action": "open", "url": "file:///etc/passwd"}):
        status, data = pact_rig.post("/api/browser/window", body)
        assert status == 409 and data["error"] in ("invalid_input", "invalid_address"), (body, data)
    assert hits == []

    status, data = pact_rig.post("/api/browser/window", {"action": "open", "url": base + "/wanted"})
    assert status == 200 and data["open"] is True, data
    assert _wait(lambda: any(path == "/wanted" for path, _ in hits))
    status, data = pact_rig.post("/api/browser/window", {"action": "private", "url": base + "/quiet"})
    assert status == 200 and data["private_windows"] == 1, data

    # Switching the Browser off closes every window, private ones included.
    status, data = pact_rig.post("/api/browser/settings", {"enabled": False})
    assert status == 200, data
    status, data = pact_rig.get("/api/browser/window")
    assert data["open"] is False and data["private_windows"] == 0, data
    assert list(desk_module._root().glob("private-*")) == []


def test_the_pane_offers_both_windows_in_every_language():
    from core.i18n import catalog_for
    from core.i18n.locales import ui_catalog_tags
    from core.vool_browser.live_page import render

    english = {key: catalog_for("en").text("live." + key) for key in ("window", "private_window")}
    codes = ui_catalog_tags()
    assert len(codes) >= 17
    for code in codes:
        page = render("c1", code)
        assert 'id="fullWindow"' in page and 'id="privateWindow"' in page
        if code != "en":
            for key, source in english.items():
                assert catalog_for(code).text("live." + key) != source, (code, key)
