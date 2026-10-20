"""Real HTTP service, repository signer, native HTTP handler and Chromium, with disposable keys.

Only the paid review verdict and the installed release identity/origin are supplied by the test.
No upload, check, catalog signature, download, preview, install or runtime loader is simulated.
"""
import contextlib
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest
import uvicorn

from core.registry.codec import RegistryError, canonical, decode
from tests.registry.helpers import TARGET
from tests.registry.test_skill_codex_authority import ORIGIN, SKILL, creator, draft
from tests.registry.test_skill_codex_github_signer import run, team_steps, upload
from tests.registry.test_skill_codex_github_signer import world as world


@contextlib.contextmanager
def registry_http(app):
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "registry HTTP server did not start"
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        assert not thread.is_alive()


@contextlib.contextmanager
def native_http():
    from apps.vool_api_server import VoolAPIHandler
    from core.web.api.runtime import RuntimeServices

    server = ThreadingHTTPServer(("127.0.0.1", 0), VoolAPIHandler)
    server.vool_runtime = RuntimeServices(display_name="VOOL")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.fixture
def served(world, monkeypatch, tmp_path):
    from core import plugin_catalog, plugin_lifecycle
    from core.registry import client

    runtime = tmp_path / "runtime"
    monkeypatch.setenv("VOOL_HOME", str(runtime))
    monkeypatch.setenv("VOOL_PLUGIN_LIFECYCLE_PATH", str(runtime / "plugin_lifecycle.json"))
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(runtime / "addons"))
    monkeypatch.setenv("VOOL_BUNDLED_PLUGINS_DIR", str(tmp_path / "no-bundled"))
    monkeypatch.setenv("VOOL_NATIVE_SKILLS_DIR", str(tmp_path / "no-native"))
    anchor = tmp_path / "app-root.json"
    anchor.write_bytes(canonical(world.root))
    monkeypatch.setenv("VOOL_REGISTRY_ROOT_FILE", str(anchor))
    plugin_lifecycle.reset_for_tests()
    plugin_catalog.invalidate_storage_listing()
    app = world.http.app
    world.http.close()
    world.outbound, world.corrupt = [], False
    with registry_http(app) as address, httpx.Client(base_url=address, headers={"Host": "registry.test"},
                                                    trust_env=False, timeout=20) as http:
        world.http = http

        def transport(method, url, body, headers):
            path = urlsplit(url).path
            world.outbound.append((method, path))
            response = http.request(method, path, content=body or None, headers=headers)
            return response.status_code, response.content

        world.transport = transport

        def request(url, *, limit):
            parsed = urlsplit(url)
            assert parsed.netloc == "registry.test"
            world.outbound.append(("GET", parsed.path))
            response = http.get(parsed.path + ("?" + parsed.query if parsed.query else ""))
            if response.status_code != 200:
                raise RegistryError(response.json()["error"])
            raw = response.content
            if world.corrupt and parsed.path.endswith("/snapshot"):
                value = decode(raw)
                value["signed"]["sequence"] += 1
                raw = canonical(value)
            assert len(raw) <= limit
            return raw

        monkeypatch.setattr(client, "time", SimpleNamespace(time=lambda: world.clock.t))
        monkeypatch.setattr(client, "configured_client", lambda: client.Client(
            decode(anchor.read_bytes()), TARGET, origin=ORIGIN, request=request, now=world.clock.t))
        yield world
    world.trust.close()


def open_codex(page, address):
    page.goto(address + "/chat", wait_until="domcontentloaded")
    page.locator("#homeToggle").click()
    page.get_by_role("button", name="Skills & Plugins", exact=True).click()
    page.locator("#addonNav").get_by_role("button", name="Skill Codex", exact=True).click()


def browse(page):
    with page.expect_response(lambda response: response.url.endswith("/api/addons") and
                              response.request.method == "POST") as response:
        page.get_by_role("button", name="Browse Skill Codex", exact=True).click()
    return response.value


def post(page, value):
    return page.evaluate("""async value => {
        const r = await fetch('/api/addons', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});
        return {status:r.status,body:await r.json()};
    }""", value)


def guidance(plugin_id):
    from core.tool_offer_assembly import reset_skill_cache, skill_guidance_for

    reset_skill_cache()
    result = skill_guidance_for(user_text="/notes Turn these meeting notes into decisions", source_context={})
    assert any(row["plugin_id"] == plugin_id for row in result.skills)
    assert "List each decision" in result.text


def test_publish_browse_inspect_install_restart_update_and_remove_on_served_stack(served, tmp_path):
    from playwright.sync_api import expect

    from core import plugin_catalog, plugin_lifecycle
    from tests.served_browser import launch_chromium

    _, token, csrf = creator(served, "101", "alice")
    first = draft(served, token, csrf, "alice/notes").json()
    assert first["state"] == "checking"
    assert run(served)["admitted"] == ["alice/notes 1.0.0"]
    before = len(served.outbound)
    manager, browser = launch_chromium()
    try:
        with native_http() as address:
            page = browser.new_page(viewport={"width": 1280, "height": 960})
            open_codex(page, address)
            assert len(served.outbound) == before  # opening a fresh app/page sends no registry traffic
            listed = browse(page)
            assert listed.ok
            page.locator("#pluginsSearch").fill("a-missing-skill")
            expect(page.locator("#pluginsBody .addon-card")).to_have_count(0)
            page.locator("#pluginsSearch").fill("meetings")
            expect(page.locator("#pluginsBody .addon-card")).to_have_count(1)
            page.locator("#pluginsSearch").fill("")
            card = page.locator("#pluginsBody .addon-card").filter(has_text="alice/notes")
            card.get_by_role("button", name="Download for inspection", exact=True).click()
            page.get_by_role("button", name="Install and enable", exact=True).wait_for()
            page.get_by_text("Exact files, permissions and source", exact=True).click()
            shown = page.locator("#pluginsBody details[open] pre").inner_text()
            assert first["sha256"] in shown and '"permissions": []' in shown and "SKILL.md" in shown
            assert not plugin_lifecycle.records()
            page.get_by_role("button", name="Install and enable", exact=True).click()
            page.wait_for_function("document.querySelector('#addonNav button[aria-pressed=true]')?.textContent === 'My add-ons'")
            record, = plugin_lifecycle.records()
            assert plugin_lifecycle.is_available(record.plugin_id)
            guidance(record.plugin_id)
            plugin_id = record.plugin_id
            page.close()

        # Restart the actual native HTTP server and reload persistent lifecycle/skill state.
        probe = subprocess.run([sys.executable, "-c", "from core import plugin_lifecycle; "
                                "from core.tool_offer_assembly import skill_guidance_for; "
                                "import sys; assert plugin_lifecycle.is_available(sys.argv[1]); "
                                "assert 'List each decision' in skill_guidance_for('/notes meeting notes').text", plugin_id],
                               capture_output=True, text=True, timeout=30)
        assert probe.returncode == 0, probe.stderr
        plugin_catalog.invalidate_storage_listing()
        with native_http() as address:
            page = browser.new_page(viewport={"width": 1280, "height": 960})
            open_codex(page, address)
            assert plugin_lifecycle.is_available(plugin_id)
            guidance(plugin_id)
            assert browse(page).ok
            second = upload(served, token, csrf, "alice/notes", version="1.1.0", text=SKILL + "\nKeep a separate list of questions.\n")
            assert run(served)["admitted"] == ["alice/notes 1.1.0"]
            listed = browse(page)
            assert listed.ok and [item["version"] for item in listed.json()["entries"]] == ["1.1.0"]
            assert page.locator("#pluginsBody .addon-card").filter(has_text="alice/notes").count() == 1
            prepared = post(page, {"action": "registry_prepare", "id": "alice/notes", "version": "1.1.0", "approved": True})
            assert prepared["status"] == 200
            stage = tmp_path / "runtime/registry_staging" / (second["sha256"] + ".json")
            original = stage.read_bytes()
            stage.write_bytes(original + b" ")
            install = {"action": "registry_install", "id": "alice/notes", "version": "1.1.0",
                       "review_id": second["sha256"], "accepted": True, "enable": True}
            assert post(page, install)["status"] == 400
            assert plugin_lifecycle.record_for(plugin_id).verified_digest == record.verified_digest
            stage.write_bytes(original)
            installed_file = Path(record.root) / "skills/notes/SKILL.md"
            old_text = installed_file.read_bytes()
            installed_file.chmod(0o644)
            installed_file.write_bytes(old_text + b"\nMy own note.\n")
            edited = post(page, install)
            assert edited["status"] == 400 and "edited" in edited["body"]["message"]
            assert installed_file.read_bytes().endswith(b"My own note.\n")
            installed_file.write_bytes(old_text)
            installed_file.chmod(0o444)
            assert post(page, install)["status"] == 200
            # An unchanged version is not a fresh update either.
            assert post(page, {"action": "registry_prepare", "id": "alice/notes", "version": "1.1.0", "approved": True})["status"] == 200
            reused = post(page, install)
            assert reused["status"] == 400 and "higher" in reused["body"]["message"]
            assert post(page, {"action": "registry_prepare", "id": "alice/notes", "version": "1.0.0", "approved": True})["status"] == 200
            downgrade = post(page, {**install, "version": "1.0.0", "review_id": first["sha256"]})
            assert downgrade["status"] == 400 and "higher" in downgrade["body"]["message"]
            team_steps(served, "removed alice/notes\n")
            assert run(served)["catalog"]
            removed = browse(page)
            assert removed.ok and removed.json()["entries"] == []
            expect(page.get_by_role("button", name="Download for inspection", exact=True)).to_have_count(0)
            assert not plugin_lifecycle.is_available(plugin_id)
            assert post(page, {"action": "registry_prepare", "id": "alice/notes", "version": "1.1.0", "approved": True})["status"] == 400
    finally:
        browser.close()
        manager.stop()


def test_signature_and_stale_catalog_fail_closed_over_real_http(served):
    from core.registry.client import configured_client

    _, token, csrf = creator(served, "101", "alice")
    draft(served, token, csrf, "alice/notes")
    run(served)
    with contextlib.closing(configured_client()) as client:
        client.refresh(approved=True, now=served.clock.t)
        served.corrupt = True
        with pytest.raises(RegistryError, match="signature"):
            client.refresh(approved=True, now=served.clock.t)
        served.corrupt = False
        served.clock.t += 7 * 3600
        with pytest.raises(RegistryError, match="freshness expired"):
            client.prepare("alice/notes", "1.0.0", approved=True, now=served.clock.t)
        assert not (Path(client.catalog.db.execute("PRAGMA database_list").fetchone()[2]).parent /
                    "registry_receipts").exists()
