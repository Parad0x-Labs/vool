"""Normal daemon + browser + real TLS fixture and confined MCP.

The external compatible service is synthetic and derives answers from supplied
text or actual tool results. This is transport/lifecycle proof, not live model
competence, live vendor availability or payment acceptance.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

from tests.test_plugin_services_v1 import https_service

ROOT = Path(__file__).resolve().parents[1]


class PluginDaemon:
    def __init__(self, root, service, *, upgrade=False):
        self.root, self.service = root, service
        self.upgrade = upgrade
        self.home = root / "home"
        self.home.mkdir(parents=True)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.log = root / "daemon.log"
        self.routes = root / "native-egress.jsonl"
        self.child = None

    def start(self):
        env = {name: os.environ[name] for name in ("HOME", "USER", "LANG", "TZ", "TMPDIR", "VOOL_PLUGIN_DOCKER_SOCKET") if name in os.environ}
        env.update(PATH=os.environ.get("PATH", "/usr/bin:/bin"), VOOL_HOME=str(self.home),
                   VOOL_PLUGINS_DIR=str(self.home / "addons"), VOOL_PLUGIN_SCRATCH_ROOT=str(self.home / "plugin-scratch"),
                   VOOL_PLUGIN_LIFECYCLE_PATH=str(self.home / "data/plugin_lifecycle.json"),
                   VOOL_CREDENTIAL_STORE="vault", VOOL_KEY_STORAGE_MODE="file", VOOL_KEY_PASSPHRASE=secrets.token_urlsafe(24),
                   VOOL_INSTALL_PROFILE="full-orchestrated", VOOL_LOCAL_MODELS_ENABLED="0", VOOL_DISABLE_MESH_DAEMON="1",
                   VOOL_DISABLE_STUN="1", VOOL_DISABLE_COMPUTE_MODE="1", VOOL_SKIP_PROVIDER_PREWARM="1",
                   VOOL_REGISTER_INSTALLED_OLLAMA_MODELS="0")
        with self.log.open("w") as output:
            self.child = subprocess.Popen([sys.executable, "-B", "-m", "tests.plugin_served_fixture", "--port", str(self.port),
                                           "--bind", "127.0.0.1"], cwd=ROOT, env=env, stdin=subprocess.PIPE,
                                          stdout=output, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        self.child.stdin.write(json.dumps({"ca": str(self.service["ca"]), "service_port": self.service["port"],
                                           "routes": str(self.routes), "upgrade": self.upgrade,
                                           "legacy_access": self.service["access"],
                                           "unselected_access": secrets.token_urlsafe(32)}) + "\n")
        self.child.stdin.flush()
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if self.child.poll() is not None:
                raise AssertionError(self.log.read_text(errors="replace")[-6000:])
            try:
                if self.get("/healthz")[0] == 200:
                    return self
            except (OSError, URLError):
                pass
            time.sleep(0.2)
        raise AssertionError("The normal daemon did not become ready: " + self.log.read_text(errors="replace")[-3000:])

    def get(self, path):
        with urlopen(self.origin + path, timeout=5) as response:
            return response.status, json.load(response)

    def post(self, path, body):
        request = Request(self.origin + path, data=json.dumps(body).encode(), method="POST",
                          headers={"Content-Type": "application/json", "Origin": self.origin})
        try:
            response = urlopen(request, timeout=30)
        except HTTPError as response:
            return response.code, json.load(response)
        with response:
            return response.status, json.load(response)

    def stop(self):
        if self.child is not None:
            if self.child.poll() is None:
                os.killpg(self.child.pid, signal.SIGTERM)
                try:
                    self.child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(self.child.pid, signal.SIGKILL)
                    self.child.wait(timeout=5)
            self.child.stdin.close()


def plugins_page(page, daemon):
    page.goto(daemon.origin + "/chat", wait_until="domcontentloaded")
    page.get_by_text("Home", exact=True).click()
    page.locator("#pluginsBtn").click()
    page.locator("#addonNav").get_by_role("button", name="My add-ons", exact=True).click()
    page.get_by_role("button", name="Plugins", exact=True).click()
    page.get_by_role("heading", name="Install from a folder", exact=True).wait_for()


def install_folder(page, folder, identity):
    page.locator("#pluginLocalFolder").fill(str(folder))
    page.get_by_role("button", name="Review folder", exact=True).click()
    page.get_by_role("dialog", name="Review plugin package").wait_for()
    assert page.get_by_text("Not reviewed by the VOOL store", exact=True).is_visible()
    page.get_by_role("button", name="Install and keep disabled", exact=True).click()
    card = page.locator(f'[data-plugin-id="{identity}"]')
    card.wait_for()
    assert card.get_by_text("Disabled", exact=True).is_visible()
    return card


def accept_access(page, card):
    card.get_by_role("button", name="Review access and enable", exact=True).click()
    page.get_by_role("dialog", name="Review plugin access").wait_for()
    page.get_by_role("button", name="Accept and enable", exact=True).click()
    card.get_by_text("Enabled", exact=True).wait_for()


def send_chat(page, text, expected, *, artifact=None):
    page.locator("#input").fill(text)
    with page.expect_response(lambda response: response.url.endswith("/api/chat")) as exchange:
        page.locator("#send").click()
    body = exchange.value.body()
    if artifact is not None:
        artifact.write_bytes(body)
    page.get_by_text(expected, exact=False).last.wait_for(timeout=10000)
    assert "application/x-ndjson" in exchange.value.headers.get("content-type", "")
    assert exchange.value.headers.get("transfer-encoding") == "chunked"
    return body


def select_provider(page):
    page.locator("#modelBtn").click()
    page.get_by_label("Model provider", exact=True).select_option("plugin-vool-plugin-usepod")
    page.locator('.cloud-dyn[data-model="plugin-vool-plugin-usepod:fixture-model"]').click()
    page.get_by_role("button", name="Accept price and select", exact=True).click()
    page.locator("#modelLbl").filter(has_text="Fixture model").wait_for(timeout=10000)


@pytest.mark.parametrize("https_service", [{"tool_dispatch": True}], indirect=True)
@pytest.mark.parametrize("digest_shape", ["arbitrary", "base58_collision"])
def test_native_provider_and_confined_template_in_normal_served_chat(tmp_path, https_service, digest_shape):
    from tests.served_browser import launch_chromium

    daemon = PluginDaemon(tmp_path / "served", https_service).start()
    manager, browser = launch_chromium()
    page = browser.new_page()
    page.set_default_timeout(10000)
    try:
        plugins_page(page, daemon)
        assert page.get_by_text("No plugins installed.", exact=True).is_visible()
        assert daemon.get("/api/addons")[1]["plugins"] == []
        assert not daemon.routes.exists() and not https_service["log"].exists()
        (tmp_path / "fresh-checkpoint.json").write_text(json.dumps({"plugins": [], "native_requests": 0, "service_requests": 0}))
        page.screenshot(path=str(tmp_path / "fresh-plugins.png"), full_page=True)

        card = install_folder(page, ROOT / "examples/plugins/vool-plugin-usepod", "vool-plugin-usepod")
        card.get_by_label("Your UsePod service token", exact=True).fill(https_service["access"])
        card.get_by_role("button", name="Save key", exact=True).click()
        assert not daemon.routes.exists()
        accept_access(page, card)
        card.get_by_role("button", name="Refresh model list", exact=True).click()
        card.get_by_text("1 models available", exact=True).wait_for()
        page.goto(daemon.origin + "/chat", wait_until="domcontentloaded")
        select_provider(page)
        message = "Transport " + secrets.token_hex(12)
        (tmp_path / "provider-chat.ndjson").write_bytes(send_chat(page, message, message[::-1]))
        page.screenshot(path=str(tmp_path / "provider-chat.png"), full_page=True)

        plugins_page(page, daemon)
        card = install_folder(page, ROOT / "examples/plugins/template", "vool-plugin-template")
        accept_access(page, card)
        page.goto(daemon.origin + "/chat", wait_until="domcontentloaded")
        supplied = "Fresh text " + secrets.token_hex(18)
        if digest_shape == "base58_collision":
            # Exercise the real redaction collision with fresh input and a computed answer.
            seed = supplied
            supplied = next(
                text for index in range(4096)
                if "0" not in hashlib.sha256((text := f"{seed}:{index}").encode()).hexdigest()
            )
        prompt = "Use the vool-plugin-template.sha256 tool to compute the text digest.\n" + json.dumps({"tool": "vool-plugin-template.sha256", "text": supplied})
        digest = hashlib.sha256(supplied.encode()).digest().hex(":")
        assert bytes.fromhex(digest.replace(":", "")) == hashlib.sha256(supplied.encode()).digest()
        send_chat(page, prompt, digest, artifact=tmp_path / "tool-chat.ndjson")
        page.screenshot(path=str(tmp_path / "confined-tool-chat.png"), full_page=True)
        records = [json.loads(line) for line in https_service["log"].read_text().splitlines()]
        assert any(row.get("used_tool_result") for row in records)
        assert any(row.get("path_auth") and not row.get("authorization_header") for row in records)
        assert https_service["access"] not in page.locator("body").inner_text()
        assert https_service["access"] not in daemon.log.read_text(errors="replace")

        plugins_page(page, daemon)
        card = page.locator('[data-plugin-id="vool-plugin-template"]')
        card.get_by_role("button", name="Disable", exact=True).click()
        card.get_by_text("Disabled", exact=True).wait_for()
        page.goto(daemon.origin + "/chat", wait_until="domcontentloaded")
        (tmp_path / "disabled-chat.ndjson").write_bytes(send_chat(page, prompt, "Unavailable: vool-plugin-template.sha256"))
        plugins_page(page, daemon)
        card = page.locator('[data-plugin-id="vool-plugin-template"]')
        card.get_by_role("button", name="Uninstall", exact=True).click()
        review = page.get_by_role("dialog", name="Uninstall plugin")
        assert review.get_by_role("checkbox", name="Also remove saved keys").is_checked()
        review.get_by_role("button", name="Uninstall", exact=True).click()
        review.wait_for(state="detached")
        assert not page.locator('[data-plugin-id="vool-plugin-template"]').count()
        assert not (daemon.home / "addons/plugins/vool-plugin-template").exists()
        assert not (daemon.home / "plugin-scratch/vool-plugin-template").exists()
    finally:
        page.screenshot(path=str(tmp_path / "final-page.png"), full_page=True)
        browser.close()
        manager.stop()
        daemon.stop()


def test_upgrade_notice_decline_and_explicit_saved_key_reuse(tmp_path, https_service):
    from tests.served_browser import launch_chromium

    daemon = PluginDaemon(tmp_path / "upgrade", https_service, upgrade=True).start()
    manager, browser = launch_chromium()
    page = browser.new_page()
    page.set_default_timeout(10000)
    try:
        plugins_page(page, daemon)
        page.get_by_text("UsePod now comes as a plugin. Install it to keep using it; your saved key will be reused.", exact=True).wait_for()
        page.get_by_text("Eyebrow now comes as a plugin. Install it to keep using it; your saved key will be reused.", exact=True).wait_for()
        assert page.get_by_text("No plugins installed.", exact=True).is_visible()
        assert not daemon.routes.exists() and not https_service["log"].exists()
        page.screenshot(path=str(tmp_path / "upgrade-notices.png"), full_page=True)
        page.goto(daemon.origin + "/chat", wait_until="domcontentloaded")
        send_chat(page, "Hello " + secrets.token_hex(8), "cloud models only answer once you choose one")
        assert not daemon.routes.exists() and not https_service["log"].exists()
        saved = json.loads((daemon.home / "data/cloud_escalation.json").read_text())
        assert saved["policy"]["provider"] == "" and saved["policy"]["mode"] == "off"
        assert saved["usage"]["count"] == 3
        plugins_page(page, daemon)
        card = install_folder(page, ROOT / "examples/plugins/vool-plugin-usepod", "vool-plugin-usepod")
        assert not card.get_by_text("Key saved.", exact=True).count()
        card.get_by_role("button", name="Review access and enable", exact=True).click()
        page.get_by_role("button", name="Keep disabled", exact=True).click()
        assert not daemon.routes.exists()
        accept_access(page, card)
        card.get_by_text("Key saved.", exact=True).wait_for()
        assert not daemon.routes.exists()
        card.get_by_role("button", name="Refresh model list", exact=True).click()
        card.get_by_text("1 models available", exact=True).wait_for()
        page.goto(daemon.origin + "/chat", wait_until="domcontentloaded")
        select_provider(page)
        message = "Transport " + secrets.token_hex(12)
        (tmp_path / "upgrade-chat.ndjson").write_bytes(send_chat(page, message, message[::-1]))
        assert https_service["access"] not in daemon.log.read_text(errors="replace")
        page.screenshot(path=str(tmp_path / "upgrade-chat.png"), full_page=True)
    finally:
        page.screenshot(path=str(tmp_path / "upgrade-final.png"), full_page=True)
        browser.close()
        manager.stop()
        daemon.stop()


def github_skill_review(page):
    page.locator("#addonNav").get_by_role("button", name="Browse", exact=True).click()
    page.locator("#pluginsSearch").fill("obra/superpowers")
    with page.expect_response(lambda response: response.request.method == "POST" and
                              response.request.post_data_json.get("action") == "github_repository") as lookup:
        page.get_by_role("button", name="Search GitHub", exact=True).click()
    result = lookup.value.json()
    assert lookup.value.status == 200 and result.get("ok"), result
    assert any(row.get("skill_path") == "skills/verification-before-completion/SKILL.md" for row in result["entries"]), result
    card = page.locator(".addon-card").filter(has_text="skills/verification-before-completion/SKILL.md")
    card.get_by_role("button", name="View add-on", exact=True).click(timeout=60000)
    with page.expect_response(lambda response: response.request.method == "POST" and
                              response.request.post_data_json.get("action") == "github_inspect") as inspected:
        page.get_by_role("button", name="Check compatibility", exact=True).click()
    entry = inspected.value.json()["entry"]
    assert entry["import_ready"] and len(entry["ref"]) == 40
    page.get_by_role("button", name="Download and scan", exact=True).click(timeout=60000)
    page.get_by_role("button", name="Run one scan", exact=True).click()
    return entry


def test_scanner_key_and_real_github_install_flow(tmp_path, https_service):
    from tests.served_browser import launch_chromium

    daemon = PluginDaemon(tmp_path / "scanner", https_service).start()
    manager, browser = launch_chromium()
    page = browser.new_page()
    page.set_default_timeout(10000)
    try:
        plugins_page(page, daemon)
        card = install_folder(page, ROOT / "examples/plugins/vool-plugin-eyebrow", "vool-plugin-eyebrow")
        accept_access(page, card)
        card.get_by_role("button", name="Test connection", exact=True).click()
        page.get_by_text("Connect your service key in Add-ons before using this plugin.", exact=True).wait_for()
        assert not daemon.routes.exists()
        card.get_by_label("Your Eyebrow service key", exact=True).fill("wrong-" + secrets.token_hex(16))
        card.get_by_role("button", name="Save key", exact=True).click()
        entry = github_skill_review(page)
        page.get_by_text("The service rejected this key or access. Check your key and account in Add-ons.", exact=True).wait_for(timeout=30000)
        assert not page.get_by_role("button", name="Accept and enable", exact=True).count()
        inventory = daemon.get("/api/addons")[1]["entries"]
        scanned = [row for row in inventory if row["id"] == entry["id"]]
        assert len(scanned) == 1 and not scanned[0].get("installed", False)
        page.screenshot(path=str(tmp_path / "scanner-bad-key.png"), full_page=True)
        plugins_page(page, daemon)
        card = page.locator('[data-plugin-id="vool-plugin-eyebrow"]')
        card.get_by_label("Your Eyebrow service key", exact=True).fill(https_service["access"])
        card.get_by_role("button", name="Save key", exact=True).click()
        card.get_by_role("button", name="Test connection", exact=True).click()
        page.get_by_text("Connection checked. The service returned a response.", exact=True).wait_for()
        github_skill_review(page)
        page.get_by_role("button", name="Accept and enable", exact=True).click(timeout=30000)
        page.get_by_text("Scan reviewed", exact=True).wait_for()
        inventory = daemon.get("/api/addons")[1]["entries"]
        installed = next(row for row in inventory if row["id"] == entry["id"])
        assert installed["installed"] and installed["enabled"] and installed["scan"] == "checked"
        records = [json.loads(line) for line in https_service["log"].read_text().splitlines()]
        assert any(row.get("path") == "/v1/scan" and not row["authorized"] for row in records)
        assert any(row.get("path") == "/v1/scan" and row["authorized"] for row in records)
        assert https_service["access"] not in page.locator("body").inner_text() + daemon.log.read_text(errors="replace")
        page.screenshot(path=str(tmp_path / "scanner-installed.png"), full_page=True)
    finally:
        (tmp_path / "scanner-final.txt").write_text(page.locator("body").inner_text())
        page.screenshot(path=str(tmp_path / "scanner-final.png"), full_page=True)
        browser.close()
        manager.stop()
        daemon.stop()


def test_template_exact_mcp_configuration_handshake(tmp_path):
    folder = ROOT / "examples/plugins/template"
    spec = json.loads((folder / ".mcp.json").read_text())["mcpServers"]["text-digest"]
    text = "Unicode λ " + secrets.token_hex(18)
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "compatibility-check", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "sha256_text", "arguments": {"text": text}}},
    ]
    incoming = "".join(json.dumps(row) + "\n" for row in requests)
    result = subprocess.run([str(folder / spec["command"]), *spec["args"]], cwd=folder,
                            input=incoming, capture_output=True, text=True, timeout=10,
                            env={"PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin", **spec["env"]})
    (tmp_path / "mcp-client.jsonl").write_text(incoming)
    (tmp_path / "mcp-server.jsonl").write_text(result.stdout)
    assert result.returncode == 0, result.stderr
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    assert [row["id"] for row in replies] == [1, 2, 3]
    assert replies[0]["result"]["protocolVersion"] == "2025-06-18"
    assert replies[1]["result"]["tools"][0]["name"] == "sha256_text"
    value = json.loads(replies[2]["result"]["content"][0]["text"])
    assert value == {"sha256": hashlib.sha256(text.encode()).digest().hex(":"), "bytes": len(text.encode())}
    assert bytes.fromhex(value["sha256"].replace(":", "")) == hashlib.sha256(text.encode()).digest()
