"""Permission setup uses server-owned eligibility and stays bound to its original chat."""
from __future__ import annotations

import json

import pytest

from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_post


def mode(body, host="127.0.0.1"):
    response = dispatch_post(path="/api/mode", body=body, headers={},
        runtime=RuntimeServices(display_name="Permission fixture"), model_name="fixture",
        client_host=host, workspace_root_provider=lambda: "/not-a-chat-workspace")
    return response.status, json.loads(response.body)


def bind(session, root):
    from core.context_namespace import ensure_chat_namespace
    from core.project_store import create_project
    ok, project = create_project(session, str(root))
    assert ok, project
    ensure_chat_namespace(session, project_id=project["id"], grant_confirmed_profile=True)
    return project["id"]


def test_readiness_distinguishes_bound_and_general_chats_without_granting(tmp_path):
    from core.mode_permission_policy import _ACTIVE_MODES, _BYPASS_GRANTS
    root = tmp_path / "workspace"
    root.mkdir()
    bind("bound-chat", root)
    before = dict(_BYPASS_GRANTS)
    status, ready = mode({"op":"bypass_options", "session_id":"bound-chat"})
    assert status == 200
    assert ready["until_off_available"] is True
    assert ready["workspace_root"] == str(root.resolve())
    status, general = mode({"op":"bypass_options", "session_id":"general-chat", "workspace_root":str(root)})
    assert status == 200
    assert general["until_off_available"] is False
    assert general["workspace_reason"] == "unbound"
    assert general["workspace_root"] == ""
    assert dict(_BYPASS_GRANTS) == before
    assert "bound-chat" not in _ACTIVE_MODES


def test_missing_workspace_refuses_before_minting_with_same_actionable_reason():
    from core.mode_permission_policy import _PENDING_BYPASS_CONFIRMATIONS
    before = dict(_PENDING_BYPASS_CONFIRMATIONS)
    status, options = mode({"op":"bypass_options", "session_id":"general-chat"})
    assert status == 200
    status, refusal = mode({"op":"request_bypass_confirmation", "session_id":"general-chat",
                           "scope":"session", "until_off":True})
    assert status == 409
    assert refusal["error"] == options["message"]
    assert refusal["reason"] == "bypass_workspace_required"
    assert dict(_PENDING_BYPASS_CONFIRMATIONS) == before


@pytest.mark.parametrize("host", ["192.0.2.8", "example.com"])
def test_readiness_is_local_only(host):
    status, _ = mode({"op":"bypass_options", "session_id":"general-chat"}, host)
    assert status == 403


def test_recovered_grant_is_validated_against_current_folder_and_revocation(tmp_path):
    from core.context_namespace import set_chat_namespace_project
    from core.mode_permission_policy import (
        activate_bypass_grant,
        request_bypass_confirmation,
        revoke_bypass_grant,
    )
    root = tmp_path / "workspace"
    root.mkdir()
    project = bind("recover-chat", root)
    params = dict(session_id="recover-chat", scope="session", until_off=True,
                  project_id=project, workspace_root=str(root))
    grant = activate_bypass_grant(**params, confirmation_id=request_bypass_confirmation(**params))
    assert mode({"op":"bypass_options", "session_id":"recover-chat"})[1]["grant"]["token"] == grant["token"]
    assert mode({"op":"bypass_options", "session_id":"foreign-chat"})[1]["grant"] is None
    other = tmp_path / "other"
    other.mkdir()
    from core.project_store import create_project
    ok, replacement = create_project("other", str(other))
    assert ok
    set_chat_namespace_project("recover-chat", replacement["id"])
    assert mode({"op":"bypass_options", "session_id":"recover-chat"})[1]["grant"] is None
    set_chat_namespace_project("recover-chat", project)
    revoke_bypass_grant(grant["token"])
    assert mode({"op":"bypass_options", "session_id":"recover-chat"})[1]["grant"] is None


@pytest.fixture
def served(tmp_path):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlsplit

    from core.web.api.service import dispatch_get
    from tests.served_browser import launch_chromium
    root = tmp_path / "workspace"
    root.mkdir()
    from core.project_store import create_project
    ok, project = create_project("Research folder", str(root))
    assert ok
    import uuid
    a, b = "openclaw:" + uuid.uuid4().hex[:20], "openclaw:" + uuid.uuid4().hex[:20]
    for sid in (a, b):
        response = dispatch_post(path="/api/chat/session", body={"session_id":sid, "operation":"create"},
            headers={}, runtime=RuntimeServices(display_name="Fixture"), model_name="fixture",
            client_host="127.0.0.1", workspace_root_provider=lambda: str(root))
        assert response.status == 201
    from core.persistent_memory import set_session_meta
    set_session_meta(a, title="Research chat")
    set_session_meta(b, title="Other chat")
    runtime = RuntimeServices(display_name="Permission fixture")
    calls, errors = [], []
    mint_seen, release_mint = threading.Event(), threading.Event()
    release_mint.set()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def send(self, response):
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.end_headers()
            self.wfile.write(response.body or b"")
        def do_GET(self):
            u = urlsplit(self.path)
            self.send(dispatch_get(path=u.path, query=parse_qs(u.query), runtime=runtime,
                                   model_name="fixture", client_host="127.0.0.1"))
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
            calls.append(body)
            response = dispatch_post(path=urlsplit(self.path).path, body=body, headers=dict(self.headers),
                runtime=runtime, model_name="fixture", client_host="127.0.0.1",
                workspace_root_provider=lambda: str(root))
            if body.get("op") == "request_bypass_confirmation":
                mint_seen.set()
                release_mint.wait(15)
            self.send(response)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    manager, browser = launch_chromium()
    page = browser.new_page(viewport={"width":1280, "height":850})
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.add_init_script("localStorage.setItem('vool.sessionId', " + json.dumps(a) + ");")
    page.goto(f"http://127.0.0.1:{server.server_port}/chat")
    page.wait_for_function("document.getElementById('modeBtn') && typeof bypassDialogContext !== 'undefined'")
    try:
        yield dict(page=page, a=a, b=b, project=project["id"], root=root,
                   calls=calls, errors=errors, mint_seen=mint_seen, release_mint=release_mint)
        assert not errors, errors
    finally:
        release_mint.set()
        browser.close(); manager.stop(); server.shutdown(); server.server_close()


def open_dialog(page):
    page.locator("#modeBtn").click()
    page.locator('[data-mode="bypass_permissions"]').click()
    page.wait_for_function("bypassDialogContext && bypassDialogContext.ready")


def link_folder(page, project):
    page.locator("#bypassProject").select_option(project)
    page.locator("#bypassLink").click()
    page.wait_for_function("bypassDialogContext && bypassDialogContext.untilOffAvailable")


def test_served_folder_link_until_off_switch_reload_and_one_click_revoke(served):
    from core.mode_permission_policy import current_chat_bypass_grant
    page = served["page"]
    open_dialog(page)
    assert page.locator('#bypassDuration option[value="until_off"]').is_disabled()
    assert "Link this chat" in page.locator("#bypassReadiness").inner_text()
    link_folder(page, served["project"])
    assert current_chat_bypass_grant(served["a"]) is None  # linking is not approval
    page.locator("#bypassDuration").select_option("until_off")
    page.locator("#bypassConfirm").click()
    page.wait_for_function("view.mode === 'bypass_permissions' && view.bypassGrant.until_off")
    grant = current_chat_bypass_grant(served["a"])
    assert grant and grant["expires_at"] is None
    assert page.locator("#bypassBanner").is_visible()
    geometry = page.evaluate("""() => {
      const badge = document.getElementById('bypassBanner').getBoundingClientRect();
      const row = document.querySelector('.control-bar').getBoundingClientRect();
      return {height:badge.height, sameRow:badge.top >= row.top && badge.bottom <= row.bottom};
    }""")
    assert geometry["height"] <= 28 and geometry["sameRow"]
    page.evaluate("id => openSession(id)", served["b"])
    assert page.locator("#modeLbl").inner_text() == "Manual"
    assert page.locator("#bypassBanner").is_hidden()
    page.evaluate("id => openSession(id)", served["a"])
    page.wait_for_function("view.mode === 'bypass_permissions'")
    page.reload()
    page.wait_for_function("view.mode === 'bypass_permissions' && view.bypassGrant.until_off")
    import os
    from pathlib import Path
    evidence = os.environ.get("VOOL_BYPASS_EVIDENCE_DIR")
    if evidence:
        page.screenshot(path=str(Path(evidence) / "compact-status.png"))
    page.locator("#bypassRevoke").click()
    page.wait_for_function("view.mode === 'manual' && !view.bypassGrant")
    page.reload()
    page.wait_for_function("typeof bypassDialogContext !== 'undefined'")
    assert current_chat_bypass_grant(served["a"]) is None
    assert page.locator("#bypassBanner").is_hidden()


def test_confirm_in_flight_never_activates_the_chat_switched_to(served):
    from core.mode_permission_policy import current_chat_bypass_grant
    page = served["page"]
    open_dialog(page)
    link_folder(page, served["project"])
    page.locator("#bypassDuration").select_option("until_off")
    served["release_mint"].clear()
    page.locator("#bypassConfirm").click()
    assert served["mint_seen"].wait(5)
    page.evaluate("id => openSession(id)", served["b"])
    assert page.locator("#bypassOverlay").is_hidden()
    served["release_mint"].set()
    page.wait_for_function("id => chatState(id).mode === 'bypass_permissions'", arg=served["a"])
    mutations = [r for r in served["calls"] if r.get("op") in {"request_bypass_confirmation", "activate_bypass", "set"}]
    assert {r["session_id"] for r in mutations} == {served["a"]}
    assert current_chat_bypass_grant(served["b"]) is None
    assert page.locator("#modeLbl").inner_text() == "Manual"
    assert page.locator("#bypassBanner").is_hidden()


def test_timed_general_chat_custom_duration_and_expiry_remain_real(served, monkeypatch):
    from core import mode_permission_policy as policy
    from core.mode_permission_policy import validate_bypass_grant
    page = served["page"]
    open_dialog(page)
    page.locator("#bypassDuration").select_option("custom")
    page.locator("#bypassCustomMinutes").fill("1441")
    page.locator("#bypassConfirm").click()
    assert not any(r.get("op") == "activate_bypass" for r in served["calls"])
    page.locator("#bypassCustomMinutes").fill("90")
    page.locator("#bypassConfirm").click()
    page.wait_for_function("view.mode === 'bypass_permissions' && view.bypassGrant")
    grant = page.evaluate("view.bypassGrant")
    assert not grant["until_off"]
    assert 5390 <= grant["expires_at"] - policy.time.time() <= 5400
    assert validate_bypass_grant(grant["token"], session_id=served["a"])
    clock = grant["expires_at"] + 1
    monkeypatch.setattr(policy.time, "time", lambda:clock)
    assert validate_bypass_grant(grant["token"], session_id=served["a"]) is None
    page.reload()
    page.wait_for_function("typeof bypassDialogContext !== 'undefined'")
    assert page.locator("#bypassBanner").is_hidden()



def test_protected_folder_does_not_offer_persistent_bypass(tmp_path):
    from pathlib import Path
    bind("protected-chat", Path.home())
    status, options = mode({"op":"bypass_options", "session_id":"protected-chat"})
    assert status == 200
    assert options["until_off_available"] is False
    assert options["workspace_reason"] == "protected_workspace"
    status, refusal = mode({"op":"request_bypass_confirmation", "session_id":"protected-chat",
                           "scope":"session", "until_off":True})
    assert status == 409 and refusal["error"] == options["message"]


def test_new_folder_link_uses_existing_picker_flow_and_still_needs_confirmation(served, tmp_path):
    from core.mode_permission_policy import current_chat_bypass_grant
    folder = tmp_path / "new-research-folder"
    folder.mkdir()
    page = served["page"]
    open_dialog(page)
    page.locator("#bypassProject").select_option("__new")
    page.locator("#bypassLink").click()
    page.locator("#textPromptValue").fill(str(folder))
    page.locator("#textPromptDialog button[type=submit]").click()
    page.locator("#textPromptValue").fill("New research folder")
    page.locator("#textPromptDialog button[type=submit]").click()
    page.wait_for_function("bypassDialogContext && bypassDialogContext.untilOffAvailable")
    assert str(folder) in page.locator("#bypassReadiness").inner_text()
    assert current_chat_bypass_grant(served["a"]) is None
    page.locator("#bypassDuration").select_option("until_off")
    assert page.locator("#bypassScope").is_disabled()
    page.locator("#bypassDuration").select_option("7200")
    assert page.locator("#bypassScope").is_enabled()


def test_folder_change_after_preview_refuses_inline_without_granting(served, tmp_path):
    from core.context_namespace import set_chat_namespace_project
    from core.mode_permission_policy import current_chat_bypass_grant
    from core.project_store import create_project
    page = served["page"]
    open_dialog(page)
    link_folder(page, served["project"])
    page.locator("#bypassDuration").select_option("until_off")
    other = tmp_path / "replacement"
    other.mkdir()
    ok, project = create_project("Replacement", str(other))
    assert ok
    set_chat_namespace_project(served["a"], project["id"])
    page.locator("#bypassConfirm").click()
    page.locator("#bypassError").wait_for(state="visible")
    assert "does not match" in page.locator("#bypassError").inner_text()
    assert page.locator("#bypassOverlay").is_visible()
    assert current_chat_bypass_grant(served["a"]) is None
    assert page.locator("#modeLbl").inner_text() == "Manual"
