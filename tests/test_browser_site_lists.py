"""Lists of harmful sites: taken only when signed and whole, and enforced in every browser window.

A real release is made here the way the public lists are published: an index with a serial,
sizes and SHA-256 of every file, signed with an Ed25519 key. It is served by a local web
server standing in for the published branch, fetched through VOOL's one outbound door, and
enforced by the real engine in the real window. The release key is made for the test; the
product ships its own.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core.i18n import catalog_for
from core.vool_browser import desk as desk_module
from core.vool_browser import guard, live, site_lists
from tests.test_desk_browser import _quiet_test_engine, _wait
from tests.test_desk_browser import browser as browser
from tests.test_desk_browser import home as home
from tests.test_desk_browser import socks as socks
from tests.test_desk_session import CHAT, agent, node, session
from tests.test_desk_session import pact_rig as pact_rig
from tests.test_desk_session import world as world

LISTED = "other-site.test"
ALSO = "plain-site.test"


class Release:
    """One publisher: a signing key, and the files of the release now on offer."""

    def __init__(self) -> None:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        self.key = Ed25519PrivateKey.generate()
        self.public = base64.b64encode(self.key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
        self.files: dict[str, bytes] = {}
        self.asked: list[str] = []

    def publish(self, serial: int, hosts: list[str], *, key=None, extra: dict[str, bytes] | None = None) -> None:
        listing = "# hosts\n" + "".join(host + "\n" for host in hosts)
        files = {"lists/vool-block.txt": listing.encode(),
                 "lists/sources.json": b'{"schema": "vool.browser.sources.v1", "sources": []}\n',
                 "plugins/index.json": b'{"schema": "vool.browser.plugins.v1", "plugins": []}\n', **(extra or {})}
        index = (json.dumps({"schema": "vool.browser.manifest.v1", "serial": serial, "generated": "2026-11-23T00:00:00Z",
                             "files": [{"path": path, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                                       for path, raw in sorted(files.items())]}, indent=2, sort_keys=True) + "\n").encode()
        self.files = {**files, "manifest/manifest.json": index,
                      "manifest/manifest.json.sig": base64.b64encode((key or self.key).sign(index)) + b"\n"}


@pytest.fixture
def release(home, monkeypatch):
    item = Release()

    class Published(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            path = self.path.lstrip("/")
            item.asked.append(path)
            body = item.files.get(path)
            self.send_response(200 if body is not None else 404)
            self.send_header("Content-Length", str(len(body or b"")))
            self.end_headers()
            self.wfile.write(body or b"")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Published)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    item.port = server.server_port
    monkeypatch.setattr(site_lists, "SOURCE", f"http://127.0.0.1:{server.server_port}/")
    monkeypatch.setattr(site_lists, "RELEASE_KEYS", (item.public,))
    monkeypatch.setattr(site_lists, "_cache", None)
    monkeypatch.setattr(live, "available", lambda: True)
    yield item
    server.shutdown()


def restarted(monkeypatch) -> None:
    """VOOL was closed and opened again: what is held is read from disk afresh."""
    monkeypatch.setattr(site_lists, "_cache", None)


# -- taking a release ---------------------------------------------------------------------


def test_with_no_release_key_nothing_is_asked_and_nothing_is_listed(release, monkeypatch):
    release.publish(1, [LISTED])
    monkeypatch.setattr(site_lists, "RELEASE_KEYS", ())
    assert site_lists.refresh(force=True) == "no_key"
    assert release.asked == [] and not site_lists.listed(LISTED)
    assert site_lists.status() == {"on": False, "sites": 0, "serial": 0, "checked": None}


def test_nothing_is_asked_while_the_browser_is_off(release, monkeypatch):
    release.publish(1, [LISTED])
    monkeypatch.setattr(live, "available", lambda: False)
    assert site_lists.refresh(force=True) == "off"
    assert release.asked == [] and not site_lists.listed(LISTED)


def test_a_signed_release_is_taken_whole_and_asked_for_once_a_day(release, monkeypatch):
    release.publish(3, [LISTED, "bad.example.com"])
    assert site_lists.refresh() == "updated"
    assert sorted(release.asked) == ["lists/sources.json", "lists/vool-block.txt", "manifest/manifest.json",
                                     "manifest/manifest.json.sig", "plugins/index.json"]
    # A listed host is listed together with every name under it, and nothing else is.
    assert site_lists.listed(LISTED) and site_lists.listed("deep.Under." + LISTED + ".")
    assert site_lists.listed_url(f"https://login.{LISTED}:8443/a?b#c")
    assert not site_lists.listed("another-" + LISTED) and not site_lists.listed("test") and not site_lists.listed("example.com")
    state = site_lists.status()
    assert state["on"] and state["sites"] == 2 and state["serial"] == 3 and state["checked"] > time.time() - 60
    # The same day: nothing more is asked.
    release.asked.clear()
    assert site_lists.refresh() == "not_due" and release.asked == []
    # A day later with nothing new: two small files, no list download.
    monkeypatch.setattr(site_lists, "due", lambda now=None: True)
    assert site_lists.refresh() == "unchanged"
    assert sorted(release.asked) == ["manifest/manifest.json", "manifest/manifest.json.sig"]
    # What is held survives a restart.
    restarted(monkeypatch)
    assert site_lists.listed(LISTED) and site_lists.held().serial == 3


def _signed_by_someone_else(release):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    release.publish(9, ["evil-update.example.com"], key=Ed25519PrivateKey.generate())


def _index_changed_after_signing(release):
    release.publish(9, ["evil-update.example.com"])
    release.files["manifest/manifest.json"] = release.files["manifest/manifest.json"].replace(b'"serial": 9', b'"serial": 8')


def _list_changed_after_signing(release):
    release.publish(9, ["evil-update.example.com"])
    # The same length, so only the SHA-256 can tell.
    release.files["lists/vool-block.txt"] = release.files["lists/vool-block.txt"].replace(b"evil-update", b"else-update")


def _list_longer_than_stated(release):
    release.publish(9, ["evil-update.example.com"])
    release.files["lists/vool-block.txt"] += b"more.example.com\n"


def _an_older_release_replayed(release):
    release.publish(4, ["evil-update.example.com"])


def _same_serial_other_contents(release):
    release.publish(5, ["evil-update.example.com"])


def _a_file_outside_the_lists(release):
    release.publish(9, ["evil-update.example.com"], extra={"lists/../../outside.txt": b"x\n"})


def _no_signature(release):
    release.publish(9, ["evil-update.example.com"])
    del release.files["manifest/manifest.json.sig"]


@pytest.mark.parametrize("spoil", [
    _signed_by_someone_else, _index_changed_after_signing, _list_changed_after_signing, _list_longer_than_stated,
    _an_older_release_replayed, _same_serial_other_contents, _a_file_outside_the_lists, _no_signature,
], ids=lambda spoil: spoil.__name__.strip("_"))
def test_a_release_that_does_not_check_out_changes_nothing(release, monkeypatch, spoil):
    release.publish(5, [LISTED])
    assert site_lists.refresh(force=True) == "updated"
    spoil(release)
    assert site_lists.refresh(force=True) in {"refused", "unreachable"}
    for _ in range(2):
        assert site_lists.held().serial == 5 and site_lists.hosts() == frozenset({LISTED})
        assert not site_lists.listed("evil-update.example.com") and not site_lists.listed("else-update.example.com")
        restarted(monkeypatch)
    assert not (site_lists._dir().parent.parent / "outside.txt").exists()
    # The refusal counts as today's check: the source is not asked again until tomorrow.
    assert site_lists.refresh() == "not_due"


def test_nothing_is_read_from_a_release_before_its_signature_holds(release):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    release.publish(1, [LISTED], key=Ed25519PrivateKey.generate())
    assert site_lists.refresh(force=True) == "refused"
    assert sorted(release.asked) == ["manifest/manifest.json", "manifest/manifest.json.sig"]
    assert not site_lists._held_dir().exists()


def test_no_release_can_block_the_hosts_vool_never_blocks(release):
    release.publish(1, [LISTED, "github.com", "raw.githubusercontent.com", "sub.google.com", "org", "wikipedia.org",
                        "UPPER.example.com", "http://scheme.example.com", "198.51.100.7", "spa ce.example.com", "a..b.com"])
    assert site_lists.refresh(force=True) == "updated"
    assert site_lists.hosts() == frozenset({LISTED})
    for kept in ("github.com", "api.github.com", "raw.githubusercontent.com", "google.com", "mail.google.com", "en.wikipedia.org"):
        assert not site_lists.listed(kept), kept


def test_lists_changed_on_disk_while_vool_was_closed_are_not_used(release, monkeypatch):
    release.publish(1, [LISTED])
    assert site_lists.refresh(force=True) == "updated"
    held = site_lists._held_dir() / "lists" / "vool-block.txt"
    held.write_bytes(held.read_bytes() + b"wikipedia-mirror.example.com\n")
    restarted(monkeypatch)
    assert site_lists.hosts() == frozenset() and not site_lists.listed("wikipedia-mirror.example.com")
    # The next check brings the signed release back.
    assert site_lists.refresh(force=True) == "updated"
    assert site_lists.hosts() == frozenset({LISTED})


def test_a_release_signed_by_a_key_this_vool_no_longer_trusts_stops_being_used(release, monkeypatch):
    release.publish(1, [LISTED])
    assert site_lists.refresh(force=True) == "updated" and site_lists.listed(LISTED)
    monkeypatch.setattr(site_lists, "RELEASE_KEYS", (Release().public,))
    assert not site_lists.listed(LISTED) and site_lists.held().serial == 0


def test_the_update_uses_private_web_and_does_not_go_around_it(release, socks, monkeypatch):
    from core import private_web

    try:
        if socket.gethostbyname(ALSO) != "127.0.0.1":
            raise OSError
    except OSError:
        pytest.skip("needs a non-local name that resolves to this machine")
    release.publish(1, [LISTED])
    # The lists are published under a public name, as in the product.
    monkeypatch.setattr(site_lists, "SOURCE", f"http://{ALSO}:{release.port}/")
    socks_port, through = socks
    # A route that is down: nothing leaves, and the source is asked again within the hour.
    monkeypatch.setattr(private_web, "current_route", lambda: private_web.RouteState(kind=private_web.ROUTE_TOR, socks_port=0))
    assert site_lists.refresh(force=True) == "unreachable"
    assert release.asked == [] and through == [] and not site_lists.listed(LISTED)
    now = time.time()
    assert site_lists.due(now + site_lists.RETRY_UNREACHABLE_SECONDS + 5) and not site_lists.due(now + 60)
    # A route that is up carries the whole update, asked for by name at the far end.
    monkeypatch.setattr(private_web, "current_route", lambda: private_web.RouteState(kind=private_web.ROUTE_TOR, socks_port=socks_port))
    assert site_lists.refresh(force=True) == "updated"
    assert len(release.asked) == 5 and through == [(3, ALSO)] * 5
    assert site_lists.listed(LISTED)


# -- the engine enforces them -------------------------------------------------------------


@pytest.fixture
def sites():
    """One web server answering for every name: it records which name each request was for."""
    hits: list[tuple[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            host = self.headers.get("Host", "").split(":")[0]
            hits.append((host, self.path))
            page = self.path.startswith("/page")
            body = (f"<title>{host}</title><h1 id=h>{host}</h1>"
                    f"<img src='http://{LISTED}:{self.server.server_port}/part.png'>"
                    f"<a id=go href='http://{LISTED}:{self.server.server_port}/page-linked'>next</a>").encode() if page else b"x"
            self.send_response(200)
            self.send_header("Content-Type", "text/html" if page else "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    for name in (LISTED, ALSO):
        try:
            if socket.gethostbyname(name) != "127.0.0.1":
                raise OSError
        except OSError:
            pytest.skip("needs non-local names that resolve to this machine")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_port, hits
    server.shutdown()


def plain_pages_allowed() -> None:
    """The person switched the plain-page warning off in their browser, so a test site without
    encryption opens. Their own choice is kept at every start."""
    profile = desk_module.profile_dir()
    assert asyncio.run(desk_module.seed_profile(profile))
    target = profile / "Default" / "Preferences"
    chosen = json.loads(target.read_text())
    chosen["https_only_mode_enabled"] = False
    target.write_text(json.dumps(chosen))


def tabs(browser, window=None):
    context = (window or browser._main).context
    return [page for page in context.pages if not page.is_closed()]


def tab_at(browser, text, window=None):
    return next((page for page in tabs(browser, window) if text in page.url), None)


def test_a_listed_site_is_stopped_with_a_warning_and_the_person_can_open_it_with_one_press(browser, release, sites):
    port, hits = sites
    release.publish(1, [LISTED])
    assert site_lists.refresh(force=True) == "updated"
    plain_pages_allowed()
    words = catalog_for("en")

    browser.open(f"http://{LISTED}:{port}/page")
    assert _wait(lambda: tab_at(browser, "/blocked.html") is not None)
    warning = tab_at(browser, "/blocked.html")
    assert warning.url.startswith("chrome-extension://") and warning.url.endswith(f"#http://{LISTED}:{port}/page")
    state = browser.status()
    assert state["protected"] is True and state["site_lists"]["sites"] == 1

    async def read():
        # The page writes its words itself once it has loaded.
        await warning.wait_for_function("() => document.title !== '' && document.body.innerText.includes(document.title)")
        return await warning.inner_text("body"), await warning.title()

    text, title = browser._call(read())
    assert title == words.text("live.guard_title")
    for key in ("live.guard_title", "live.guard_text", "live.guard_back", "live.guard_open", "live.guard_open_note"):
        assert words.text(key) in text, key
    assert LISTED in text

    # A page elsewhere that pulls a part from the listed site, or links to it: the part is never asked for.
    browser.open(f"http://127.0.0.1:{port}/page")
    assert _wait(lambda: ("127.0.0.1", "/page") in hits)
    time.sleep(1.0)
    assert not [hit for hit in hits if hit[0] == LISTED], hits

    # One press on the warning opens the site, for this person, until the browser is closed.
    async def press():
        await warning.click("#open")
        await warning.wait_for_url(f"http://{LISTED}:{port}/page", timeout=10000)
        return await warning.inner_text("#h")

    assert browser._call(press()) == LISTED
    assert (LISTED, "/page") in hits
    # Other listed sites, had there been any, stay stopped; so does this one after a restart.
    browser.stop()
    hits.clear()
    browser.open(f"http://{LISTED}:{port}/page")
    assert _wait(lambda: tab_at(browser, "/blocked.html") is not None)
    assert not [hit for hit in hits if hit[0] == LISTED]


def test_a_tab_reopened_at_the_start_is_held_to_todays_lists(browser, release, sites):
    """The person continues where they left off, and one of yesterday's tabs is on a site
    that was added to the lists overnight. The engine reopens tabs in its first window,
    which VOOL makes only once today's lists are in force: that tab shows the warning and
    the site is never asked."""
    from tests.test_desk_browser import choose_on_start_up

    port, hits = sites
    release.publish(1, ["some-other-site.example.com"])
    assert site_lists.refresh(force=True) == "updated"
    plain_pages_allowed()
    browser.open(f"http://{ALSO}:{port}/page-kept")
    browser.open(f"http://{LISTED}:{port}/page-yesterday")
    assert _wait(lambda: (LISTED, "/page-yesterday") in hits)
    choose_on_start_up(browser, 1)
    browser.stop()

    release.publish(2, [LISTED])
    assert site_lists.refresh(force=True) == "updated"
    hits.clear()
    browser.open("")
    assert _wait(lambda: tab_at(browser, "/blocked.html") is not None), [page.url for page in tabs(browser)]
    assert sorted(page.url.split("#")[-1] for page in tabs(browser)) == sorted(
        [f"http://{ALSO}:{port}/page-kept", f"http://{LISTED}:{port}/page-yesterday"]
    )
    assert not [page.url for page in tabs(browser) if page.url.startswith(f"http://{LISTED}")]
    # Today's lists were in force before the engine had a window to reopen a tab in. Once
    # the other tab is back (the engine shows it from its own copy and asks its site only
    # for the icon), the listed site has been asked for nothing at all.
    assert _wait(lambda: any(hit[0] == ALSO for hit in hits)), hits
    assert [hit for hit in hits if hit[0] == LISTED] == []


def test_a_site_the_person_chose_to_open_yesterday_is_stopped_again_in_its_reopened_tab(browser, release, sites):
    """"Open it anyway" lasts until the browser is closed: the tab the engine reopens at the
    next start shows the warning again, and the site is asked for nothing: the lists are in
    force before the engine has a window to reopen the tab in."""
    from tests.test_desk_browser import choose_on_start_up

    port, hits = sites
    release.publish(1, [LISTED])
    assert site_lists.refresh(force=True) == "updated"
    plain_pages_allowed()
    browser.open(f"http://{LISTED}:{port}/page-chosen")
    assert _wait(lambda: tab_at(browser, "/blocked.html") is not None)
    warning = tab_at(browser, "/blocked.html")

    async def press():
        await warning.click("#open")
        await warning.wait_for_url(f"http://{LISTED}:{port}/page-chosen", timeout=10000)

    browser._call(press())
    choose_on_start_up(browser, 1)
    browser.stop()

    hits.clear()
    browser.open("")
    assert _wait(lambda: tab_at(browser, "/blocked.html") is not None), [page.url for page in tabs(browser)]
    assert [page.url.split("#")[-1] for page in tabs(browser)] == [f"http://{LISTED}:{port}/page-chosen"]
    assert [hit for hit in hits if hit[0] == LISTED] == []


def test_a_list_as_long_as_the_public_ones_is_enforced_from_the_first_page(browser, release, sites):
    port, hits = sites
    # As many entries as the public lists hold.
    many = [hashlib.sha256(str(number).encode()).hexdigest()[:16] + ".bad.example.com" for number in range(120_000)]
    release.publish(1, [LISTED, *many])
    assert site_lists.refresh(force=True) == "updated" and len(site_lists.hosts()) == 120_001
    plain_pages_allowed()
    started = time.monotonic()
    browser.open(f"http://{LISTED}:{port}/page")
    took = time.monotonic() - started
    assert _wait(lambda: tab_at(browser, "/blocked.html") is not None)
    time.sleep(1.0)
    assert not [hit for hit in hits if hit[0] == LISTED], hits
    assert browser.status()["protected"] is True and browser._main.guard_sites == 120_001
    assert took < 20, took


def test_a_private_window_is_protected_too(browser, release, sites):
    port, hits = sites
    release.publish(1, [LISTED])
    assert site_lists.refresh(force=True) == "updated"
    browser.open(f"http://{LISTED}:{port}/page", private=True)
    window = browser._private[0]
    assert _wait(lambda: tab_at(browser, "/blocked.html", window) is not None)
    browser.open(f"http://127.0.0.1:{port}/page", private=True)
    assert _wait(lambda: ("127.0.0.1", "/page") in hits)
    time.sleep(1.0)
    assert not [hit for hit in hits if hit[0] == LISTED], hits
    assert window.guard_id and window.extensions == {}


def test_a_newer_release_reaches_a_window_that_is_already_open(browser, release, sites):
    port, hits = sites
    release.publish(1, [LISTED])
    assert site_lists.refresh(force=True) == "updated"
    plain_pages_allowed()
    browser.open(f"http://{ALSO}:{port}/page")
    assert _wait(lambda: (ALSO, "/page") in hits)
    first = browser._main.guard_folder

    release.publish(2, [LISTED, ALSO])
    assert site_lists.refresh(force=True) == "updated"
    browser.lists_changed()
    assert browser._main.guard_folder not in ("", first) and browser._main.guard_sites == 2
    hits.clear()
    browser.open(f"http://{ALSO}:{port}/page-again")
    assert _wait(lambda: len([page for page in tabs(browser) if "/blocked.html" in page.url]) == 1)
    assert not [hit for hit in hits if hit[0] == ALSO], hits
    # The earlier protection's folder goes with the next start; only the one in force is kept.
    second = browser._main.guard_folder
    browser.stop()
    browser.open(f"http://127.0.0.1:{port}/page")
    assert browser._main.guard_folder == second
    assert [path.name for path in guard._root().iterdir()] == [second]


def test_a_window_left_open_takes_the_next_days_release_by_itself(browser, release, sites, monkeypatch):
    port, hits = sites
    release.publish(1, [LISTED])
    monkeypatch.setattr(desk_module, "LISTS_LOOK_SECONDS", 0.5)
    plain_pages_allowed()
    # Opening the window asks for the lists; nothing here asks for them by hand.
    browser.open(f"http://{ALSO}:{port}/page")
    assert _wait(lambda: browser._main is not None and browser._main.guard_sites == 1, 20)
    assert (ALSO, "/page") in hits and site_lists.held().serial == 1
    # The next day a newer release is out. The open window takes it.
    release.publish(2, [LISTED, ALSO])
    state = site_lists._state()
    site_lists._write_state(checked=state["checked"] - site_lists.CHECK_EVERY_SECONDS - 1)
    assert _wait(lambda: browser._main.guard_sites == 2, 20)
    hits.clear()
    browser.open(f"http://{ALSO}:{port}/page-again")
    assert _wait(lambda: tab_at(browser, "/blocked.html") is not None)
    assert not [hit for hit in hits if hit[0] == ALSO], hits


def test_with_no_lists_the_browser_opens_without_the_protection_and_says_so(browser, release, sites):
    port, hits = sites
    browser.open(f"http://127.0.0.1:{port}/page")
    assert _wait(lambda: ("127.0.0.1", "/page") in hits)
    state = browser.status()
    assert state["open"] is True and state["protected"] is False and state["site_lists"]["on"] is False
    assert not guard._root().exists() or list(guard._root().iterdir()) == []


def test_the_protection_asks_the_engine_for_no_page_content_and_carries_no_page_script(home):
    folder = guard.build(frozenset({LISTED, "bad.example.com"}), "en")
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["permissions"] == ["declarativeNetRequest", "declarativeNetRequestFeedback"]
    assert "content_scripts" not in manifest and "externally_connectable" not in manifest and "update_url" not in manifest
    assert manifest["incognito"] == "split"
    rules = json.loads((folder / "rules.json").read_text())
    assert [rule["condition"]["requestDomains"] for rule in rules] == [["bad.example.com", LISTED]] * 2
    assert {path.name for path in folder.iterdir()} == {"manifest.json", "rules.json", "guard.js", "blocked.html", "blocked.css", "blocked.js"}
    for name in ("guard.js", "blocked.js"):
        source = (folder / name).read_text()
        assert "fetch(chrome.runtime.getURL('rules.json'))" in source or "fetch(" not in source, name
        assert "XMLHttpRequest" not in source and "scripting" not in source
    # Nothing to block: no folder, and an earlier one is removed.
    assert guard.build(frozenset(), "en") is None and list(guard._root().iterdir()) == []


def test_the_warning_speaks_every_language_vool_has(home):
    from core.i18n.catalog import CATALOGS_DIR

    english = catalog_for("en")
    for path in sorted(CATALOGS_DIR.glob("*.json")):
        tag = path.stem
        own = catalog_for(tag)
        guard.remember_language(tag)
        assert guard.language() == tag
        folder = guard.build(frozenset({LISTED}))
        page = (folder / "blocked.js").read_text(encoding="utf-8")
        for key in ("guard_name", "guard_title", "guard_text", "guard_back", "guard_open", "guard_open_note", "harmful_site"):
            text = own.text("live." + key)
            assert text and (tag == "en" or text != english.text("live." + key)), (tag, key)
            if key != "harmful_site":
                assert json.dumps(text, ensure_ascii=False) in page, (tag, key)
        assert json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["name"] == own.text("live.guard_name")
        assert ('"rtl"' in page) == (tag in {"ar", "he"}), tag
    guard.remember_language("../../etc")
    assert guard.language() == "zh-Hans"


# -- VOOL itself ---------------------------------------------------------------------------


def test_vools_own_steps_never_open_a_listed_site_even_one_the_person_chose_to_open(world, release, sites):
    rig, origin, _ = world
    port, hits = sites
    release.publish(1, [LISTED])
    assert site_lists.refresh(force=True) == "updated"
    plain_pages_allowed()

    # The person opens the listed site anyway, in their own tab of the window.
    status, data = rig.post("/api/browser/window", {"action": "open", "url": f"http://{LISTED}:{port}/page"})
    assert status == 200 and data["open"] is True and data["protected"] is True
    desk = desk_module.desk()
    assert _wait(lambda: tab_at(desk, "/blocked.html") is not None)
    warning = tab_at(desk, "/blocked.html")

    async def press():
        await warning.click("#open")
        await warning.wait_for_url(f"http://{LISTED}:{port}/page", timeout=10000)

    desk._call(press())
    # Everything the person's own tab asks the site for arrives: the page, its picture, its icon.
    assert _wait(lambda: {(LISTED, "/page"), (LISTED, "/part.png"), (LISTED, "/favicon.ico")} <= set(hits)), hits
    hits.clear()

    # VOOL is not offered the site at all: there is no card to allow.
    item = session(origin)
    with pytest.raises(live.BrowserFault, match="harmful_site"):
        item.propose("navigate", {"url": f"http://{LISTED}:{port}/page"})

    # A page elsewhere that pulls a part from the listed site and links to it.
    item.fixture_origin = f"http://127.0.0.1:{port}"
    snap = agent(rig, item, "navigate", {"url": f"http://127.0.0.1:{port}/page"})
    assert snap["state"] == "ready", snap["state"]
    assert item.page_request_refusals >= 1
    snap = agent(rig, item, "click", {"selector": node(snap, "next")["selector"]})
    assert snap["state"] == "harmful_site", snap["state"]
    time.sleep(0.5)
    assert not [hit for hit in hits if hit[0] == LISTED], hits
    # The pane has the plain sentence for it, in the person's language.
    from core.vool_browser.live_page import render

    assert json.dumps(catalog_for("lt").text("live.harmful_site"), ensure_ascii=False).strip('"') in render(CHAT, "lt")


def test_vools_hidden_lookup_browser_and_page_reads_do_not_open_a_listed_site(release, sites):
    from tools.browser import browser_render

    port, hits = sites
    release.publish(1, [LISTED])
    assert site_lists.refresh(force=True) == "updated"
    url = f"http://www.{LISTED}:{port}/page"
    assert browser_render.chrome_render(url) == {"status": "harmful_site", "final_url": url}
    assert hits == []
    # Reading a page for the person, the plain way.
    from core.runtime_execution_tools import _web_fetch

    read = _web_fetch({"url": url}, source_context={"allow_remote_fetch": True})
    assert read.ok is False and read.status == "harmful_site" and url in read.response_text
    assert hits == []


def test_the_chat_says_why_a_listed_site_was_not_opened():
    from core.agent_runtime import action_honesty_validator as validator

    said = validator._unanswered_browser_response({"op": "navigate", "url": "https://bad.example.com/", "status": "harmful_site"})
    assert "https://bad.example.com/" in said and "did not open it on this turn" in said
    assert said != validator._UNANSWERED_BROWSER_FALLBACK.format(url="https://bad.example.com/")
