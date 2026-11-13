"""The raw file door shows pictures on the chat page and nothing else: the served journey, in Chromium.

``/api/files/raw`` exists so the chat page can draw a picture VOOL saved (a local or cloud render)
inline. It used to serve any file under the Files roots with the type its name claims, ``.html``
included, on VOOL's own origin and with no policy of its own. A page opened there runs as VOOL: it
can call every owner door the chat page can. And any other site could ask the browser for the door
(an ``<img>`` or a link to ``127.0.0.1``) to learn which files the person has.

Security review 2026-11-13. The door now serves only pictures and videos, never as a document that
can run anything (``sandbox``, ``nosniff``), and only to VOOL's own page (``Sec-Fetch-Site`` and
``Cross-Origin-Resource-Policy``). Drives the real daemon with a planted page in the Files root.
"""

from __future__ import annotations

import base64
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest

from tests._blackbox_served_rig import ServedDaemon, free_port, run_in_home

pytestmark = [pytest.mark.served]

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
PLANTED = (
    "<html><body><script>"
    "fetch('/api/cloud/status').then(r => r.text()).then(t => { document.title = 'RAN ' + t.length; })"
    ".catch(e => { document.title = 'RAN blocked'; });"
    "</script></body></html>"
)


class _OtherSite:
    """A page on another origin that asks the browser for the person's files."""

    def __init__(self, base_url: str, picture: str) -> None:
        self.port = free_port()
        page = (
            "<html><body><img id='probe' src='" + base_url + "/api/files/raw?path=" + quote(picture) + "'"
            " onload=\"document.title='LOADED'\" onerror=\"document.title='REFUSED'\"></body></html>"
        ).encode()

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a: Any) -> None:
                return

            def do_GET(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), _Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        # "localhost" is another site than 127.0.0.1 to the browser, as any web page is.
        return f"http://localhost:{self.port}/"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def served(tmp_path: Path):
    home = tmp_path / "home"
    daemon = ServedDaemon(home, env_extra={"VOOL_MODEL_LOAD_FLOOR_GB": "0"})
    try:
        daemon.start(timeout=240)
        out = run_in_home(home, (
            "import base64, pathlib\nfrom core import generated_files\n"
            "root = pathlib.Path(generated_files.files_roots()[0]); root.mkdir(parents=True, exist_ok=True)\n"
            f"(root / 'planted.html').write_text({PLANTED!r})\n"
            f"(root / 'render.png').write_bytes(base64.b64decode({base64.b64encode(PNG).decode()!r}))\n"
            "print(root)\n"
        )).strip()
        yield {"daemon": daemon, "root": Path(out)}
    finally:
        daemon.stop()


def test_the_raw_file_door_serves_pictures_only_to_vools_own_page(served: dict[str, Any]) -> None:
    daemon: ServedDaemon = served["daemon"]
    root: Path = served["root"]
    planted = str(root / "planted.html")
    picture = str(root / "render.png")

    from tests import served_browser

    manager, browser = served_browser.launch_chromium()
    other = _OtherSite(daemon.base_url, picture)
    try:
        page = browser.new_page()

        # A page planted in the Files root, opened on VOOL's origin, runs nothing.
        response = page.goto(f"{daemon.base_url}/api/files/raw?path={quote(planted)}", wait_until="load")
        page.wait_for_timeout(1500)
        assert not page.title().startswith("RAN"), (page.title(), response.status if response else None)
        assert response is not None and response.status in (403, 404, 415), response.status

        # A picture VOOL saved still loads, as a picture that runs nothing.
        response = page.goto(f"{daemon.base_url}/api/files/raw?path={quote(picture)}")
        assert response is not None and response.status == 200
        assert response.headers.get("content-type") == "image/png"
        assert response.headers.get("x-content-type-options") == "nosniff"
        assert "sandbox" in response.headers.get("content-security-policy", "")

        # VOOL's own page draws it.
        page.goto(f"{daemon.base_url}/chat", wait_until="domcontentloaded")
        drawn = page.evaluate(
            "src => new Promise(done => { const i = new Image(); i.onload = () => done(i.naturalWidth);"
            " i.onerror = () => done(-1); i.src = src; })",
            f"/api/files/raw?path={quote(picture)}",
        )
        assert drawn == 1, drawn

        # Another site cannot use the door to see the person's files.
        page.goto(other.url, wait_until="load")
        page.wait_for_function("() => document.title === 'LOADED' || document.title === 'REFUSED'", timeout=15000)
        assert page.title() == "REFUSED", page.title()
    finally:
        other.close()
        browser.close()
        manager.stop()
