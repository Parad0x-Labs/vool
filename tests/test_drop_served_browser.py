"""Drop through the actual served doors, with native crypto and two isolated homes."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

import pytest

from core import runtime_paths
from tests.asgi_harness import asgi_request


@pytest.fixture
def homes(tmp_path, monkeypatch):
    from apps.vool_api_server import create_app
    from core.web.api.runtime import RuntimeServices

    previous = runtime_paths._VOOL_HOME_OVERRIDE
    monkeypatch.delenv("VOOL_DROP", raising=False)
    original = socket.socket.connect

    def local_only(sock, address):
        if sock.family in {socket.AF_INET, socket.AF_INET6} and address[0] not in {"127.0.0.1", "::1"}:
            raise AssertionError("Drop attempted an external connection")
        return original(sock, address)

    monkeypatch.setattr(socket.socket, "connect", local_only)
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(mode=0o700)
    b.mkdir(mode=0o700)
    runtime_paths.configure_runtime_home(a)
    app = create_app(RuntimeServices(display_name="VOOL"))
    try:
        yield app, a, b, tmp_path / "relay"
    finally:
        runtime_paths.configure_runtime_home(previous)


def call(app, home, action, **values):
    runtime_paths.configure_runtime_home(home)
    status, _, raw = asgi_request(app, method="POST", path="/api/drop",
                                  headers={"Host": "127.0.0.1", "Content-Type": "application/json"},
                                  body=json.dumps({"action": action, **values}).encode())
    return status, json.loads(raw)


def ok(app, home, action, **values):
    status, value = call(app, home, action, **values)
    assert status == 200 and value["ok"], value
    return value


def connect(homes, *, permitted=True):
    app, a, b, relay = homes
    for home in (a, b):
        ok(app, home, "settings", enabled=True, relay=str(relay), allow_digest=permitted)
    apin = ok(app, a, "identity")["fingerprint"]
    bpin = ok(app, b, "identity")["fingerprint"]
    invite = ok(app, a, "invite", fingerprint=bpin, confirmed=True)["link"]
    sid = ok(app, b, "join", fingerprint=apin, link=invite, confirmed=True)["sid"]
    assert ok(app, a, "poll")["outcomes"] == ["received"]
    return sid


def send(homes, sid, data=b"Selected local file", note="<script>throw Error('untrusted')</script>"):
    app, a, _, _ = homes
    runtime_paths.configure_runtime_home(a)
    status, _, raw = asgi_request(
        app, method="POST", path="/api/drop/send", body=data,
        headers={"Host": "127.0.0.1", "Content-Type": "application/octet-stream", "X-Drop-Session": sid,
                 "X-Drop-Name": quote("selected.txt"), "X-Drop-Note": quote(note)})
    return status, json.loads(raw)


def test_fresh_install_and_owner_origin_guards(homes):
    app, a, _, relay = homes
    state = ok(app, a, "status")
    assert state["enabled"] is False and state["allow_digest"] is False
    assert call(app, a, "identity")[1]["error"] == "DROP_DISABLED"
    assert not (a / "drop").exists() and not relay.exists()
    for headers in (
        {"Host": "127.0.0.1", "Content-Type": "text/plain"},
        {"Host": "127.0.0.1", "Content-Type": "application/json", "Origin": "http://127.0.0.1:9999"},
        {"Host": "127.0.0.1", "Content-Type": "application/json", "Sec-Fetch-Site": "same-site"},
    ):
        status, _, _ = asgi_request(app, method="POST", path="/api/drop", headers=headers,
                                   body=json.dumps({"action": "settings", "enabled": True,
                                                    "relay": str(relay), "allow_digest": True}).encode())
        assert status in {403, 415}
    assert ok(app, a, "status")["enabled"] is False
    assert call(app, a, ["identity"])[0] == 400


def test_settings_belong_only_to_the_receivers_home(homes):
    app, a, b, relay = homes
    ok(app, a, "settings", enabled=True, relay=str(relay), allow_digest=True)
    assert ok(app, b, "status")["enabled"] is False
    assert call(app, b, "identity")[1]["error"] == "DROP_DISABLED"
    ok(app, b, "settings", enabled=True, relay=str(relay), allow_digest=False)
    assert ok(app, a, "status")["allow_digest"] is True
    assert ok(app, b, "status")["allow_digest"] is False


def test_cli_accepts_a_valid_dash_prefixed_session_id(tmp_path):
    relay = tmp_path / "relay"
    env = dict(os.environ, VOOL_DROP="1", VOOL_HOME=str(tmp_path / "home"))
    command = [sys.executable, "-m", "core.drop", "--home", env["VOOL_HOME"], "--relay", str(relay)]
    created = subprocess.run([*command, "relay-init"], env=env, capture_output=True, timeout=40)
    assert created.returncode == 0, created.stderr
    result = subprocess.run([*command, "show", "-" + "A" * 21], env=env, capture_output=True, timeout=40)
    assert result.returncode == 2
    assert b'"refused": "UNKNOWN_DROP"' in result.stdout, result.stdout + result.stderr


def test_enable_prepares_a_fresh_private_profile_and_preserves_home(homes, tmp_path, monkeypatch):
    app, _, _, relay = homes
    fresh = tmp_path / "fresh-profile"
    assert not fresh.exists()
    ok(app, fresh, "settings", enabled=True, relay=str(relay), allow_digest=False)
    assert fresh.stat().st_mode & 0o777 == 0o700
    fingerprint = ok(app, fresh, "identity")["fingerprint"]
    marker = fresh / "kept.txt"
    marker.write_text("Existing profile data stays here.")
    fresh.chmod(0o755)
    ok(app, fresh, "settings", enabled=False, relay=str(relay), allow_digest=False)
    assert fresh.stat().st_mode & 0o777 == 0o755
    ok(app, fresh, "settings", enabled=True, relay=str(relay), allow_digest=False)
    assert fresh.stat().st_mode & 0o777 == 0o700
    assert marker.read_text() == "Existing profile data stays here."
    assert ok(app, fresh, "identity")["fingerprint"] == fingerprint
    # Simulate HOME inside the disposable fixture. Never point this test at a real profile.
    protected = tmp_path / "protected-home"
    protected.mkdir(mode=0o755)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: protected))
    status, result = call(app, protected, "settings", enabled=True, relay=str(relay), allow_digest=False)
    assert status == 409 and result["error"] == "UNSAFE_HOME"
    assert protected.stat().st_mode & 0o777 == 0o755


def test_environment_opt_in_without_relay_never_uses_server_directory(homes, tmp_path, monkeypatch):
    app, a, _, _ = homes
    working = tmp_path / "server-directory"
    working.mkdir(mode=0o700)
    monkeypatch.chdir(working)
    monkeypatch.setenv("VOOL_DROP", "1")
    status, result = call(app, a, "identity")
    assert status == 409 and result["error"] == "RELAY_REQUIRED"
    assert not (working / "invites").exists()
    assert not (a / "drop").exists()


def test_two_home_approval_result_and_delete(homes):
    app, a, b, _ = homes
    sid = connect(homes)
    assert send(homes, sid)[0] == 200
    ok(app, b, "poll")
    shown = ok(app, b, "show", sid=sid)
    assert shown["note"].startswith("<script>") and shown["executions"] == 0
    task = ok(app, a, "task", sid=sid)["task_id"]
    ok(app, b, "poll")
    assert call(app, b, "execute", sid=sid)[0] == 409
    ok(app, b, "approve", sid=sid, task_id=task)
    digest = ok(app, b, "execute", sid=sid)["result"]
    assert digest == hashlib.sha256(b"Selected local file").hexdigest()
    assert call(app, b, "execute", sid=sid)[0] == 409
    ok(app, a, "poll")
    assert ok(app, a, "show", sid=sid)["result"] == digest
    ok(app, b, "delete", sid=sid)
    assert call(app, b, "show", sid=sid)[0] == 409
    assert not (b / "drop" / sid).exists()


def test_decline_and_receiver_permission_are_durable(homes):
    app, a, b, relay = homes
    sid = connect(homes)
    assert send(homes, sid)[0] == 200
    ok(app, b, "poll")
    task = ok(app, a, "task", sid=sid)["task_id"]
    ok(app, b, "poll")
    ok(app, b, "decline", sid=sid, task_id=task)
    assert call(app, b, "approve", sid=sid, task_id=task)[0] == 409
    assert call(app, b, "execute", sid=sid)[0] == 409
    assert ok(app, b, "show", sid=sid)["executions"] == 0
    sid2 = connect(homes)
    assert send(homes, sid2)[0] == 200
    ok(app, b, "poll")
    task2 = ok(app, a, "task", sid=sid2)["task_id"]
    ok(app, b, "poll")
    ok(app, b, "approve", sid=sid2, task_id=task2)
    ok(app, b, "settings", enabled=True, relay=str(relay), allow_digest=False)
    assert call(app, b, "execute", sid=sid2)[1]["error"] == "RECIPIENT_PERMISSION_REFUSED"
    assert ok(app, b, "show", sid=sid2)["executions"] == 0


def test_unconfirmed_and_wrong_fingerprint(homes):
    app, a, b, relay = homes
    for home in (a, b):
        ok(app, home, "settings", enabled=True, relay=str(relay), allow_digest=False)
    apin = ok(app, a, "identity")["fingerprint"]
    bpin = ok(app, b, "identity")["fingerprint"]
    assert call(app, a, "invite", fingerprint=bpin, confirmed=False)[1]["error"] == "FINGERPRINT_CONFIRMATION_REQUIRED"
    link = ok(app, a, "invite", fingerprint=bpin, confirmed=True)["link"]
    assert call(app, b, "join", fingerprint=bpin, link=link, confirmed=True)[0] == 409
    assert ok(app, b, "list")["drops"] == []
    assert ok(app, b, "join", fingerprint=apin, link=link, confirmed=True)["sid"]


def _memory_listing(app, home):
    runtime_paths.configure_runtime_home(home)
    status, _, raw = asgi_request(app, method="GET", path="/api/memory/entries",
                                  headers={"Host": "127.0.0.1"}, query_string=b"limit=200")
    assert status == 200, raw
    return raw.decode()


def _remember(home, words):
    """An ordinary memory in an ordinary chat of this home, unrelated to any Drop."""
    from core.context_namespace import ensure_chat_namespace
    from core.drop.session import fresh_id
    from core.memory import entries

    runtime_paths.configure_runtime_home(home)
    chat = fresh_id()
    ensure_chat_namespace(chat, grant_current_receipts=False)
    assert entries.add_memory_fact(words, session_id=chat,
                                   access_policy=entries.resolve_memory_access_policy(chat_id=chat))


def test_junk_in_the_shared_folder_never_fails_a_delete_or_hides_memory(homes):
    import secrets

    app, a, b, relay = homes
    sid = connect(homes)
    assert send(homes, sid)[0] == 200
    ok(app, b, "poll")
    fact = "The boiler service is booked for Tuesday, reference " + secrets.token_hex(4)
    _remember(b, fact)
    assert fact in _memory_listing(app, b)
    apin = ok(app, a, "identity")["fingerprint"]
    bpin = ok(app, b, "identity")["fingerprint"]
    left = [relay / apin / "99999999999999999999-left-by-someone.wire", relay / bpin / "zz-left-by-someone.wire"]
    for path in left:
        path.write_bytes(b"not a Drop frame")
    assert ok(app, b, "delete", sid=sid)["state"] == "deleted"
    assert call(app, b, "show", sid=sid)[1]["error"] == "DROP_REVOKED"
    assert fact in _memory_listing(app, b)
    assert all(path.exists() for path in left)


@pytest.fixture
def pages(homes, monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    from core.drop.service import _HOME_LOCK
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get, dispatch_post, dispatch_upload, is_raw_upload_path
    from storage.db import active_default_db_path, configure_default_db_path
    from tests.served_browser import launch_chromium

    monkeypatch.setenv("VOOL_GATE", "1")
    _, a, b, relay = homes
    servers = []

    def serve(home):
        runtime = RuntimeServices(display_name="VOOL")

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                self.respond(False)

            def do_POST(self):
                self.respond(True)

            def respond(self, post):
                with _HOME_LOCK:
                    previous_home = runtime_paths._VOOL_HOME_OVERRIDE
                    previous_db = active_default_db_path()
                    runtime_paths.configure_runtime_home(home)
                    configure_default_db_path(home / "data" / "runtime.db")
                    try:
                        path = urlsplit(self.path)
                        arguments = {"path": path.path, "runtime": runtime, "headers": dict(self.headers),
                                     "client_host": "127.0.0.1"}
                        if not post:
                            response = dispatch_get(**arguments, query=parse_qs(path.query), model_name="fixture")
                        else:
                            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                            if is_raw_upload_path(path.path):
                                response = dispatch_upload(**arguments, raw_body=raw)
                            else:
                                response = dispatch_post(**arguments, body=json.loads(raw or b"{}"), model_name="fixture",
                                                         workspace_root_provider=lambda: str(home))
                        self.send_response(response.status)
                        self.send_header("Content-Type", response.content_type)
                        self.end_headers()
                        self.wfile.write(response.body or b"")
                    finally:
                        configure_default_db_path(previous_db)
                        runtime_paths.configure_runtime_home(previous_home)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        servers.append((server, worker))
        return f"http://127.0.0.1:{server.server_port}"

    base_a, base_b = serve(a), serve(b)
    manager, browser = launch_chromium()
    errors = []
    try:
        aa, bb = browser.new_page(), browser.new_page()
        for page, base in ((aa, base_a), (bb, base_b)):
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.goto(base + "/settings#backup/drop")
            page.locator(".vd-root").first.wait_for()
            page.locator(".vd-status").get_by_text("Drop is off.", exact=True).wait_for()
        yield aa, bb, relay
        assert not errors, errors
    finally:
        browser.close()
        manager.stop()
        for server, worker in servers:
            server.shutdown()
            server.server_close()
            worker.join(5)
            assert not worker.is_alive()


def configure(page, relay):
    root = page.locator(".vd-root").first
    root.locator(".vd-enable").check()
    root.locator(".vd-relay").fill(str(relay))
    root.locator(".vd-allow").check()
    root.get_by_role("button", name="Save", exact=True).click()
    page.wait_for_function("document.querySelector('.vd-fingerprint').value.length === 43")


def browser_pair(pages):
    a, b, relay = pages
    for page in (a, b):
        configure(page, relay)
    apin = a.locator(".vd-fingerprint").input_value()
    bpin = b.locator(".vd-fingerprint").input_value()
    a.locator(".vd-peer").fill(bpin)
    a.get_by_role("button", name="Create invite", exact=True).click()
    a.get_by_text("Check the fingerprint with the other person before connecting.", exact=True).wait_for()
    a.locator(".vd-confirm").check()
    a.get_by_role("button", name="Create invite", exact=True).click()
    a.wait_for_function("document.querySelector('.vd-link').value.startsWith('vool-drop://')")
    b.locator(".vd-link").fill(a.locator(".vd-link").input_value())
    b.locator(".vd-peer").fill(bpin)
    b.locator(".vd-confirm").check()
    b.get_by_role("button", name="Join", exact=True).click()
    b.get_by_text("Check the fingerprint with the other person before connecting.", exact=True).wait_for()
    b.locator(".vd-peer").fill(apin)
    assert not b.locator(".vd-confirm").is_checked()
    b.locator(".vd-confirm").check()
    b.get_by_role("button", name="Join", exact=True).click()
    b.locator(".vd-select option").wait_for(state="attached")
    a.get_by_role("button", name="Check for Drops", exact=True).click()
    a.locator(".vd-select option").wait_for(state="attached")
    return a.locator(".vd-select").input_value()


def browser_send(a, b):
    a.locator(".vd-draft").fill("<script>window.receivedExecuted=true</script> calculate unrelated secrets")
    a.locator(".vd-file").set_input_files({"name": "sample.txt", "mimeType": "text/plain", "buffer": b"A distinct browser sample"})
    a.get_by_role("button", name="Send", exact=True).click()
    a.get_by_text("Sent to the shared folder. The other person must check for Drops.", exact=True).wait_for()
    b.get_by_role("button", name="Check for Drops", exact=True).click()
    b.locator(".vd-note").get_by_text("calculate unrelated secrets", exact=False).wait_for()
    assert b.evaluate("window.receivedExecuted") is None


def test_browser_refused_send_keeps_the_drop_ready(pages):
    """A refused send (a file over 10 MiB) says why and leaves the chosen Drop and its Send button in place."""
    from playwright.sync_api import expect

    a, b, _ = pages
    sid = browser_pair(pages)
    a.locator(".vd-file").set_input_files(
        {"name": "too-big.bin", "mimeType": "application/octet-stream", "buffer": b"\0" * (10 * 1024 * 1024 + 1)})
    a.get_by_role("button", name="Send", exact=True).click()
    expect(a.locator(".vd-status")).to_have_text("Choose one file of at most 10 MiB.")
    expect(a.get_by_role("button", name="Send", exact=True)).to_be_visible()
    assert a.locator(".vd-select").input_value() == sid
    browser_send(a, b)


def test_browser_full_two_home_flow_and_delete(pages, homes, tmp_path):
    from playwright.sync_api import expect

    a, b, _ = pages
    sid = browser_pair(pages)
    browser_send(a, b)
    a.get_by_role("button", name="Request file check", exact=True).click()
    expect(a.locator(".vd-task-state")).to_have_text("File check requested")
    b.get_by_role("button", name="Check for Drops", exact=True).click()
    expect(b.locator(".vd-task-state")).to_have_text("Waiting for approval")
    assert not b.locator(".vd-result").inner_text()
    b.get_by_role("button", name="Approve and run file check", exact=True).click()
    digest = hashlib.sha256(b"A distinct browser sample").hexdigest()
    expect(b.locator(".vd-result")).to_have_text(digest)
    a.get_by_role("button", name="Check for Drops", exact=True).click()
    expect(a.locator(".vd-result")).to_have_text(digest)
    b.screenshot(path=str(tmp_path / "drop-screen.png"), full_page=True)
    with b.expect_response(lambda response: response.url.endswith("/api/drop")
                           and response.request.post_data_json.get("action") == "delete") as deleted:
        b.get_by_role("button", name="Delete", exact=True).click()
    assert deleted.value.json() == {"ok": True, "state": "deleted"}
    expect(b.get_by_role("button", name="Delete", exact=True)).to_be_enabled()
    expect(b.locator(".vd-note")).to_have_text("")
    expect(b.locator(".vd-result")).to_have_text("")
    assert not (homes[2] / "drop" / sid).exists()
    a.locator(".vd-enable").uncheck()
    a.get_by_role("button", name="Save", exact=True).click()
    expect(a.locator(".vd-panel")).to_be_hidden()
    response = a.request.post(a.url.split('/settings')[0] + "/api/drop", data={"action": "poll"})
    assert response.status == 409 and response.json()["error"] == "DROP_DISABLED"


@pytest.mark.parametrize("decision", ["delete", "disable"])
def test_browser_decisions_reject_late_content(pages, homes, decision):
    from playwright.sync_api import expect

    a, b, _ = pages
    sid = browser_pair(pages)
    browser_send(a, b)
    held = {}

    def delay_show(route):
        if route.request.post_data_json.get("action") == "show" and not held:
            held["route"] = route
            held["response"] = route.fetch()
        else:
            route.continue_()

    b.route("**/api/drop", delay_show)
    b.get_by_role("button", name="Check for Drops", exact=True).click()
    deadline = time.monotonic() + 30
    while "response" not in held and time.monotonic() < deadline:
        b.wait_for_timeout(50)
    assert "response" in held, "The real show response was not captured"
    assert held["response"].json()["note"].startswith("<script>")
    if decision == "delete":
        with b.expect_response(lambda response: response.url.endswith("/api/drop")
                               and response.request.post_data_json.get("action") == "delete") as deleted:
            b.get_by_role("button", name="Delete", exact=True).click()
        assert deleted.value.json() == {"ok": True, "state": "deleted"}
        expect(b.locator(".vd-select option")).to_have_count(0)
        assert not (homes[2] / "drop" / sid).exists()
    else:
        b.locator(".vd-enable").uncheck()
        b.get_by_role("button", name="Save", exact=True).click()
        expect(b.locator(".vd-panel")).to_be_hidden()
    with b.expect_response(lambda response: response.url.endswith("/api/drop")
                           and response.request.post_data_json.get("action") == "show") as late:
        held["route"].fulfill(response=held["response"])
    assert late.value.status == 200
    # Flush the response continuation and a paint, after the real deletion finished.
    b.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    expect(b.locator(".vd-note")).to_have_text("")
    expect(b.locator(".vd-file-info")).to_have_text("")


def test_browser_approval_keeps_the_original_drop_after_selection_changes(pages):
    from playwright.sync_api import expect

    a, b, _ = pages
    sid = browser_pair(pages)
    browser_send(a, b)
    def api(page, action, **values):
        endpoint = page.url.split("/settings")[0] + "/api/drop"
        response = page.request.post(endpoint, data={"action": action, **values})
        result = response.json()
        assert response.status == 200 and result["ok"], result
        return result

    api(a, "task", sid=sid)
    api(b, "poll")
    apin = a.locator(".vd-fingerprint").input_value()
    bpin = b.locator(".vd-fingerprint").input_value()
    link = api(a, "invite", fingerprint=bpin, confirmed=True)["link"]
    other = api(b, "join", link=link, fingerprint=apin, confirmed=True)["sid"]
    assert other != sid
    api(a, "poll")
    upload = a.request.post(a.url.split("/settings")[0] + "/api/drop/send",
                            headers={"Content-Type": "application/octet-stream",
                                     "X-Drop-Session": other, "X-Drop-Name": "other.txt"},
                            data=b"A different selected file")
    assert upload.status == 200 and upload.json()["ok"]
    api(b, "poll")
    other_task = api(a, "task", sid=other)["task_id"]
    api(b, "poll")
    api(b, "approve", sid=other, task_id=other_task)
    b.get_by_role("button", name="Check for Drops", exact=True).click()
    expect(b.locator(".vd-select option")).to_have_count(2)
    b.locator(".vd-select").select_option(sid)
    expect(b.locator(".vd-task-state")).to_have_text("Waiting for approval")
    held = {}

    def delay_approval(route):
        if route.request.post_data_json.get("action") == "approve" and not held:
            held["route"] = route
            held["response"] = route.fetch()
        else:
            route.continue_()

    b.route("**/api/drop", delay_approval)
    b.get_by_role("button", name="Approve and run file check", exact=True).click()
    deadline = time.monotonic() + 30
    while "response" not in held and time.monotonic() < deadline:
        b.wait_for_timeout(50)
    assert "response" in held
    assert held["response"].json() == {"ok": True, "state": "approved"}
    b.locator(".vd-select").select_option(other)
    expect(b.locator(".vd-task-state")).to_have_text("File check approved")
    with b.expect_response(lambda response: response.url.endswith("/api/drop")
                           and response.request.post_data_json.get("action") == "execute") as executed:
        held["route"].fulfill(response=held["response"])
    assert executed.value.request.post_data_json["sid"] == sid
    assert executed.value.json()["ok"] is True
    endpoint = b.url.split("/settings")[0] + "/api/drop"
    original = b.request.post(endpoint, data={"action": "show", "sid": sid}).json()
    untouched = b.request.post(endpoint, data={"action": "show", "sid": other}).json()
    assert original["executions"] == 1 and original["task_state"] == "complete"
    assert untouched["executions"] == 0 and untouched["task_state"] == "approved"
    expect(b.locator(".vd-task-state")).to_have_text("File check approved")


def test_browser_bad_frames_replay_decline_and_expiry(pages, homes):
    from playwright.sync_api import expect

    a, b, relay = pages
    sid = browser_pair(pages)
    bpin = b.locator(".vd-fingerprint").input_value()
    # Deliver native frames, plus forged and oversized siblings, through the real folder.
    a.locator(".vd-file").set_input_files({"name": "frame.txt", "mimeType": "text/plain", "buffer": b"frame sample"})
    a.get_by_role("button", name="Send", exact=True).click()
    a.get_by_text("Sent to the shared folder. The other person must check for Drops.", exact=True).wait_for()
    wire = next((relay / bpin).glob("*.wire")).read_bytes()
    from core.drop.session import b64, unb64

    frame = json.loads(wire)
    encrypted = bytearray(unb64(frame["box"]["body"], 0, 65536))
    encrypted[0] ^= 1
    frame["box"]["body"] = b64(encrypted)
    (relay / bpin / "000-forged.wire").write_text(json.dumps(frame, sort_keys=True, separators=(",", ":")))
    (relay / bpin / "001-oversized.wire").write_bytes(b"x" * 110001)
    b.get_by_role("button", name="Check for Drops", exact=True).click()
    expect(b.locator(".vd-file-info")).to_contain_text("frame.txt")
    assert not (relay / bpin / "000-forged.wire").exists()
    assert not (relay / bpin / "001-oversized.wire").exists()
    (relay / bpin / "replayed.wire").write_bytes(wire)
    b.get_by_role("button", name="Check for Drops", exact=True).click()
    a.get_by_role("button", name="Request file check", exact=True).click()
    expect(a.locator(".vd-task-state")).to_have_text("File check requested")
    b.get_by_role("button", name="Check for Drops", exact=True).click()
    expect(b.locator(".vd-task-state")).to_have_text("Waiting for approval")
    b.get_by_role("button", name="Decline", exact=True).click()
    expect(b.locator(".vd-task-state")).to_have_text("Declined")
    assert not b.locator(".vd-result").inner_text()
    # Shorten only the carrier's local lifetime; neither protocol nor cryptography is changed.
    db = sqlite3.connect(homes[2] / "drop" / "drops.db")
    db.execute("UPDATE drops SET expires=? WHERE sid=?", (int(time.time()*1000)+2000, sid))
    db.commit()
    db.close()
    b.get_by_role("button", name="Check for Drops", exact=True).click()
    expect(b.locator(".vd-select option")).to_have_count(0, timeout=15000)
    expect(b.locator(".vd-file-info")).to_have_text("")
    assert not (homes[2] / "drop" / sid).exists()
    from core.drop.service import DropHome

    # The sender still sees a live session. This is a NEW authenticated frame,
    # which the receiver must reject by its own expiry, rather than a known replay.
    with DropHome(homes[1], relay) as sender:
        with sender.scope():
            sender._send(sid, {"type": "hello"})
    late = list((relay / bpin).glob("*.wire"))
    assert late
    with b.expect_response(lambda response: response.url.endswith("/api/drop")
                           and response.request.post_data_json.get("action") == "poll") as received:
        b.get_by_role("button", name="Check for Drops", exact=True).click()
    assert "rejected" in received.value.json()["outcomes"]
    expect(b.get_by_role("button", name="Check for Drops", exact=True)).to_be_enabled()
    expect(b.locator(".vd-select option")).to_have_count(0)
    assert all(not path.exists() for path in late)
