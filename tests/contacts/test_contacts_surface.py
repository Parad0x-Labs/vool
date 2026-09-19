"""The owner's Contacts surface: the /api/contacts doors and the Home -> Contacts overlay.

What executes: the production API doors (core.web.api.service.dispatch_get / dispatch_post -> command registry ->
core.web.api.contacts_api -> core.contacts), the real chat page HTML and scripts under node, and the same page in headless
Chromium with /api/contacts* answered by those real doors in-process (other /api/* calls get fixture JSON). The browser
proof is browser-only: no daemon, native window, model or network.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse

import pytest

from core.contacts import store
from core.vool_chat_page import render_vool_chat_html
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get, dispatch_post
from tests.contacts import protected_changes

OWNER = store.ACTOR_OWNER
SOLANA_DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
EVM_A = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"


@pytest.fixture(autouse=True)
def _empty_contacts():
    protected_changes.reset()
    yield
    protected_changes.reset()


def _rt() -> RuntimeServices:
    return RuntimeServices(display_name="VOOL")


def _get(path: str, query: dict | None = None, *, client_host: str = "127.0.0.1"):
    response = dispatch_get(path=path, query=query or {}, runtime=_rt(), model_name="vool", client_host=client_host)
    return response.status, json.loads(response.body.decode("utf-8") or "{}")


def _post(path: str, body: dict, *, client_host: str = "127.0.0.1", headers: dict | None = None):
    response = dispatch_post(path=path, body=body, headers=headers if headers is not None else {"content-type": "application/json"},
                             runtime=_rt(), model_name="vool", workspace_root_provider=lambda: "/tmp", client_host=client_host)
    return response.status, json.loads(response.body.decode("utf-8") or "{}")


def _sol_key() -> str:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from core.vool_wallet import b58encode

    return b58encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


# --- the doors -----------------------------------------------------------------------------------------------------

def test_original_owner_adds_reads_edits_and_deletes_through_the_owner_doors() -> None:
    status, created = _post("/api/contacts/create", {"display_name": "Alex Chen", "aliases": ["AC"], "endpoints": [
        {"kind": "email", "value": "alex.chen@example.test", "label": "work"}, {"kind": "wallet", "value": EVM_A, "network": "base-sepolia"}]})
    assert status == 200 and created["ok"], created
    contact = created["contact"]
    status, listing = _get("/api/contacts", {"q": ["AC"]})
    assert status == 200 and [c["contact_id"] for c in listing["contacts"]] == [contact["contact_id"]]
    status, detail = _get("/api/contacts/detail", {"id": [contact["contact_id"]]})
    assert status == 200 and {e["value"] for e in detail["contact"]["endpoints"]} == {"alex.chen@example.test", EVM_A}
    assert [event["action"] for event in detail["events"]] == ["created"]
    email_id = next(e["endpoint_id"] for e in detail["contact"]["endpoints"] if e["kind"] == "email")
    status, stale = _post("/api/contacts/update", {"contact_id": contact["contact_id"], "expected_revision": contact["revision"] + 5, "notes": "x"})
    assert status == 409 and stale["reason"] == "revision_mismatch", stale
    status, updated = _post("/api/contacts/update", {
        "contact_id": contact["contact_id"], "expected_revision": contact["revision"],
        "change_endpoints": [{"endpoint_id": email_id, "value": "a.chen@example.test", "label": "work"}],
        "add_endpoints": [{"kind": "messaging", "value": "U02ABCDEF", "channel": "slack", "account": "Clayworks Studio"}]})
    assert status == 200 and updated["status"] == "pending_authentication" and updated["applied"] is False, updated
    assert {e["value"] for e in _get("/api/contacts/detail", {"id": [contact["contact_id"]]})[1]["contact"]["endpoints"]} == {"alex.chen@example.test", EVM_A}
    status, confirmed = protected_changes.confirm_through_the_door(_post, updated["operation"])
    assert status == 200 and {c["action"] for c in confirmed["result"]["updated"][0]["changes"]} == {"endpoint_changed", "endpoint_added"}, confirmed
    status, refused = _post("/api/contacts/delete", {"contact_id": contact["contact_id"]})
    assert status == 409 and refused["reason"] == "confirmation_required"
    status, proposed = _post("/api/contacts/delete", {"contact_id": contact["contact_id"], "confirm": True})
    assert status == 200 and proposed["status"] == "pending_authentication" and _get("/api/contacts/detail", {"id": [contact["contact_id"]]})[0] == 200, proposed
    status, deleted = protected_changes.confirm_through_the_door(_post, proposed["operation"])
    assert status == 200 and deleted["result"]["deleted"][0]["endpoints_erased"] == 3, deleted
    assert _get("/api/contacts/detail", {"id": [contact["contact_id"]]})[0] == 404


def test_novel_suggestions_show_before_and_after_and_apply_only_through_the_owner_door() -> None:
    from core.contacts import tools

    good, evil = _sol_key(), _sol_key()
    contact = store.create_contact(display_name="Zoë Ng", actor=OWNER, endpoints=[{"kind": "wallet", "value": good, "network": SOLANA_DEVNET, "label": "savings"}])
    endpoint_id = contact["endpoints"][0]["endpoint_id"]
    tools.run("contacts.update", {"contact_id": contact["contact_id"], "change_endpoints": [{"endpoint_id": endpoint_id, "value": evil}]}, None)
    tools.run("contacts.save", {"name": "Mara Okafor", "email": "mara@example.test"}, None)
    status, pending = _get("/api/contacts/suggestions")
    by_value = {s["value"]: s for s in pending["suggestions"]}
    assert status == 200 and set(by_value) == {evil, "mara@example.test"}
    assert (by_value[evil]["replaces"]["value"], by_value[evil]["contact_display_name"]) == (good, "Zoë Ng")
    assert (by_value["mara@example.test"]["contact_id"], by_value["mara@example.test"]["display_name"]) == ("", "Mara Okafor")
    status, proposed = _post("/api/contacts/suggestions/accept", {"suggestion_id": by_value[evil]["suggestion_id"]})
    assert status == 200 and proposed["status"] == "pending_authentication", proposed
    assert [e["value"] for e in store.get_contact(contact["contact_id"])["endpoints"]] == [good], "taking a replacement waits for the PIN"
    status, accepted = protected_changes.confirm_through_the_door(_post, proposed["operation"])
    assert status == 200 and accepted["status"] == "committed", accepted
    [endpoint] = store.get_contact(contact["contact_id"])["endpoints"]
    assert (endpoint["value"], endpoint["verification"]) == (evil, "user_confirmed")
    status, dismissed = _post("/api/contacts/suggestions/dismiss", {"suggestion_id": by_value["mara@example.test"]["suggestion_id"]})
    assert status == 200 and dismissed["state"] == "dismissed"
    assert _get("/api/contacts/suggestions")[1]["suggestions"] == []
    assert [c["display_name"] for c in store.active_contacts()] == ["Zoë Ng"]


def test_the_owner_doors_refuse_other_peers_types_origins_and_key_material() -> None:
    assert _get("/api/contacts", client_host="192.168.1.20")[0] == 403
    assert _post("/api/contacts/create", {"display_name": "Eve"}, client_host="192.168.1.20")[0] == 403
    assert _post("/api/contacts/create", {"display_name": "Eve"}, headers={"content-type": "text/plain"})[0] == 415
    assert _post("/api/contacts/create", {"display_name": "Eve"}, headers={"content-type": "application/json", "origin": "https://evil.example"})[0] == 403
    status, secret = _post("/api/contacts/create", {"display_name": "Backup", "notes": "abandon ability able about above absent absorb abstract absurd abuse access accident"})
    assert status == 400 and secret["reason"] == "secret_material_refused"
    assert store.active_contacts() == []


def test_the_model_principal_cannot_run_an_owner_edit() -> None:
    from core.command_registry.execute import ExecutionContext, execute_command

    envelope = execute_command("contacts.book.create", {"payload": {"display_name": "Eve", "endpoints": [{"kind": "email", "value": "eve@example.test"}]}},
                               context=ExecutionContext(projection="api", principal="model",
                                                        extra={"client_host": "127.0.0.1", "headers": {"content-type": "application/json"}}))
    assert not envelope.ok
    assert store.active_contacts() == []


def test_options_offer_every_kind_channel_and_declared_network() -> None:
    status, options = _get("/api/contacts/options")
    assert status == 200
    assert [k["id"] for k in options["kinds"]] == ["email", "phone", "messaging", "wallet"]
    slack = next(c for c in options["channels"] if c["id"] == "slack")
    assert slack["account_required"] is True
    assert {"solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1", "eip155:84532", "eip155:1"} <= {n["network"] for n in options["networks"]}


# --- the page ------------------------------------------------------------------------------------------------------

def test_home_carries_a_text_labelled_contacts_entry_and_no_new_header_icon() -> None:
    html = render_vool_chat_html(build_commit="contacts-static")
    home = html.split('id="homeOptions"', 1)[1].split('class="home-support"', 1)[0]
    assert 'id="contactsBtn"' in home and "Contacts</button>" in home
    header = html.split("<header", 1)[1].split("</header>", 1)[0]
    assert "contact" not in header.lower()
    assert "window.VoolContacts" in html


def test_the_page_boots_with_the_contacts_namespace_page_actions_and_palette_entries() -> None:
    from tests.chat_page_js_harness import DOM, run_node

    page = render_vool_chat_html(build_commit="contacts-node-boot")
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", page, re.DOTALL)
    program = (
        DOM + "\n;(function(){\n" + "\n;\n".join(scripts)
        + "\nout({ contacts: typeof window.VoolContacts, api: window.VoolContacts ? Object.keys(window.VoolContacts) : [],"
        + " actions: window.VoolPalette ? window.VoolPalette.actions() : null,"
        + " page: ['openContacts', 'insertComposerText'].filter(function (k) { return window.VoolPageActions && typeof window.VoolPageActions[k] === 'function'; }) });\n})();\n"
    )
    result = run_node(program)
    assert result["contacts"] == "object" and {"open", "close", "choose", "chooseIntoComposer"} <= set(result["api"])
    assert {"contacts", "insert-contact"} <= set(result["actions"])
    assert result["page"] == ["openContacts", "insertComposerText"]


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        executable = os.environ.get("VOOL_BROWSER_BINARY") or None
        launched = playwright.chromium.launch(headless=True, executable_path=executable)
        yield launched
        launched.close()


_FIXTURE = json.dumps({"ok": True, "sessions": [], "items": [], "plugins": [], "skills": [], "files": [], "mode": "manual", "state": "ready",
                       "notifications": [], "models": [], "projects": []})


def test_home_contacts_add_view_use_edit_and_delete_in_the_real_page(browser) -> None:
    from playwright.sync_api import expect

    sol = _sol_key()
    html = render_vool_chat_html(build_commit="contacts-browser")
    requests: list[tuple[str, str]] = []
    errors: list[str] = []
    context = browser.new_context(viewport={"width": 1280, "height": 860})
    context.add_init_script("window.open = () => null;")

    def route(r):
        url = urllib.parse.urlsplit(r.request.url)
        requests.append((r.request.method, url.path))
        if url.path == "/":
            return r.fulfill(status=200, content_type="text/html", body=html)
        if url.path.startswith("/api/contacts"):
            if r.request.method == "GET":
                response = dispatch_get(path=url.path, query=urllib.parse.parse_qs(url.query), runtime=_rt(), model_name="vool", client_host="127.0.0.1")
            else:
                response = dispatch_post(path=url.path, body=json.loads(r.request.post_data or "{}"), headers={"content-type": "application/json"},
                                         runtime=_rt(), model_name="vool", workspace_root_provider=lambda: "/tmp", client_host="127.0.0.1")
            return r.fulfill(status=response.status, content_type="application/json", body=response.body)
        return r.fulfill(status=200, content_type="application/json", body=_FIXTURE)

    context.route("**/*", route)
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("http://vool-contacts.test/", wait_until="load")
    page.locator("#input").fill("Please email")

    page.locator("#homeToggle").click()
    page.locator("#contactsBtn").click()
    expect(page.locator("#vcOverlay")).to_be_visible()
    expect(page.locator("#vcList")).to_contain_text("Nothing saved yet")

    page.locator("#vcAdd").click()
    page.locator("#vcName").fill("Alex Chen")
    page.locator("#vcValue1").fill("alex.chen@example.test")
    page.locator("#vcLabel1").fill("work")
    page.get_by_role("button", name="Add an entry").click()
    page.locator("#vcKind2").select_option("wallet")
    page.locator("#vcValue2").fill(sol)
    page.locator("#vcNetwork2").select_option(SOLANA_DEVNET)
    page.locator("#vcSave").click()

    detail = page.locator("#vcDetail")
    expect(detail.locator(".vc-title")).to_have_text("Alex Chen")
    wallet_card = detail.locator(".vc-endpoint", has_text=sol)
    expect(wallet_card).to_contain_text("Solana Devnet")
    expect(wallet_card.locator(".vc-value")).to_have_text(sol)
    expect(detail.locator(".vc-endpoint", has_text="alex.chen@example.test")).to_contain_text("Added by you")

    detail.locator(".vc-endpoint", has_text="alex.chen@example.test").get_by_role("button", name="Use in chat").click()
    expect(page.locator("#vcOverlay")).to_be_hidden()
    expect(page.locator("#input")).to_have_value("Please email Alex Chen (work)")

    page.locator("#homeToggle").click()
    page.locator("#contactsBtn").click()
    page.locator("#vcList .vc-row", has_text="Alex Chen").click()
    detail.get_by_role("button", name="Edit").click()
    page.locator("#vcForm .vc-ep-row", has_text="").first.locator("input").nth(1).fill("office")
    page.locator("#vcSave").click()
    review = page.locator("#vcReview")
    expect(review).to_contain_text("Change Alex Chen")
    expect(review).to_contain_text("It changes a saved contact.")
    expect(review.locator(".vc-diff", has_text="Change label")).to_contain_text("office")
    assert [e["label"] for e in store.active_contacts()[0]["endpoints"] if e["kind"] == "email"] == ["work"], "nothing changes before the PIN"
    review.get_by_role("button", name="Set up protection").click()
    with protected_changes.system_consent(True) as asked:
        page.locator("#vcNewSecretEnroll").fill(protected_changes.SYNTHETIC_PIN)
        page.locator("#vcNewSecretEnrollRepeat").fill(protected_changes.SYNTHETIC_PIN)
        page.locator("#vcProtectGoEnroll").click()
        expect(page.locator("#vcRecoveryCode")).to_contain_text("Recovery code")
    assert len(asked) == 1 and "saved contacts" in asked[0], "enrolling asked the operating system once"
    page.locator("#vcRecoveryCode").get_by_role("button", name="I saved it").click()
    expect(page.locator("#vcRecoveryCode")).to_have_count(0)
    page.locator("#vcPending .vc-row", has_text="Change Alex Chen").click()
    page.locator("#vcPin").fill("111222")
    page.locator("#vcConfirmChange").click()
    expect(page.locator("#vcConfirmForm [role='alert']")).to_contain_text("not right")
    expect(page.locator("#vcPin")).to_have_value("")
    assert [e["label"] for e in store.active_contacts()[0]["endpoints"] if e["kind"] == "email"] == ["work"], "a wrong PIN changes nothing"
    page.locator("#vcPin").fill(protected_changes.SYNTHETIC_PIN)
    page.locator("#vcConfirmChange").click()
    expect(detail.locator(".vc-endpoint", has_text="alex.chen@example.test")).to_contain_text("Email · office")

    detail.get_by_role("button", name="Delete contact").click()
    expect(detail.locator(".vc-confirm")).to_contain_text("Delete Alex Chen?")
    detail.locator(".vc-confirm").get_by_role("button", name="Review deletion").click()
    expect(page.locator("#vcReview")).to_contain_text("Delete Alex Chen")
    assert len(store.active_contacts()) == 1, "deleting waits for the PIN"
    page.locator("#vcPin").fill(protected_changes.SYNTHETIC_PIN)
    page.locator("#vcConfirmChange").click()
    expect(page.locator("#vcList")).to_contain_text("Nothing saved yet")

    assert store.active_contacts() == []
    assert not any(method == "POST" and path == "/api/chat" for method, path in requests), "the surface never sends a chat turn"
    assert [e for e in errors if "Contacts" in e or "vc" in e] == [], errors
    context.close()


def test_home_contacts_import_previews_applies_the_selection_and_confirms_an_entry_in_the_real_page(browser, monkeypatch) -> None:
    """Browser-only: the Import screen against the real doors in-process. The macOS Contacts bridge is the scripted fixture
    framework (core.contacts.importers.apple._load_binding); no permission prompt, address book or network is used."""
    from playwright.sync_api import expect

    from core.contacts import importing
    from core.contacts.importers import apple
    from tests.contacts.test_contacts_imports import PRIVATE_KEY_SHAPED, _apple_people, _framework

    framework = _framework(_apple_people(), status=3)
    monkeypatch.setattr(apple, "_load_binding", lambda: framework)
    store.create_contact(display_name="Alex Chen", actor=OWNER, endpoints=[{"kind": "email", "value": "alex.chen@example.test", "label": "work"}])
    protected_changes.enroll_synthetic_pin()
    html = render_vool_chat_html(build_commit="contacts-import-browser")
    requests: list[tuple[str, str]] = []
    errors: list[str] = []
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    context.add_init_script("window.open = () => null;")

    def route(r):
        url = urllib.parse.urlsplit(r.request.url)
        requests.append((r.request.method, url.path))
        if url.path == "/":
            return r.fulfill(status=200, content_type="text/html", body=html)
        if url.path.startswith("/api/contacts"):
            if r.request.method == "GET":
                response = dispatch_get(path=url.path, query=urllib.parse.parse_qs(url.query), runtime=_rt(), model_name="vool", client_host="127.0.0.1")
            else:
                response = dispatch_post(path=url.path, body=json.loads(r.request.post_data or "{}"), headers={"content-type": "application/json"},
                                         runtime=_rt(), model_name="vool", workspace_root_provider=lambda: "/tmp", client_host="127.0.0.1")
            return r.fulfill(status=response.status, content_type="application/json", body=response.body)
        return r.fulfill(status=200, content_type="application/json", body=_FIXTURE)

    context.route("**/*", route)
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("http://vool-contacts.test/", wait_until="load")
    page.locator("#homeToggle").click()
    page.locator("#contactsBtn").click()
    page.locator("#vcImport").click()
    expect(page.locator("#vcImportPanel")).to_contain_text("Import contacts")
    expect(page.locator("#vcImportProvider")).to_have_value("apple_contacts")
    expect(page.locator("#vcImportForm .vc-note").first).to_contain_text("Access allowed")

    page.locator("#vcImportAccount").fill("This Mac")
    page.locator("#vcImportConnect").click()
    expect(page.locator("#vcImportForm [role='alert']")).to_contain_text("nothing was connected")
    assert importing.list_sources() == [], "an unticked consent box connects nothing"
    page.locator("#vcImportConsent").check()
    page.locator("#vcImportConnect").click()
    source_card = page.locator("#vcImportSources .vc-card", has_text="This Mac")
    expect(source_card).to_contain_text("Connected")

    source_card.get_by_role("button", name="Preview").click()
    preview = page.locator("#vcImportPreview")
    expect(preview).to_contain_text("Read 6")
    expect(page.locator("#vcImportApply")).to_have_text("Import 4 new, add to 1 saved")
    expect(preview.locator(".vc-item", has_text="Zoë Ng")).to_contain_text("One messaging was not kept: secret material refused.")
    assert PRIVATE_KEY_SHAPED not in page.content()
    assert [c["display_name"] for c in store.active_contacts()] == ["Alex Chen"], "the preview saved nothing"
    preview.locator(".vc-item", has_text="Rivera").locator("select").select_option("skip")
    expect(page.locator("#vcImportApply")).to_have_text("Import 3 new, add to 1 saved")
    page.locator("#vcImportApply").click()
    expect(preview).to_contain_text("Imported 3 new and added entries to 0 saved")
    expect(preview).to_contain_text("Change Alex Chen waits for your PIN or password (1 item).")
    expect(page.locator("#vcList")).to_contain_text("Northfield Kiln Repair")
    expect(page.locator("#vcList")).not_to_contain_text("Rivera")
    assert [e["value"] for e in store.list_contacts("Alex Chen")[0]["endpoints"]] == ["alex.chen@example.test"], "adding to a saved contact waits for the PIN"
    preview.get_by_role("button", name="Review it").click()
    review = page.locator("#vcReview")
    expect(review).to_contain_text("Change Alex Chen")
    expect(review.locator(".vc-diff", has_text="Add email")).to_contain_text("alex@home.example.test")
    page.locator("#vcPin").fill(protected_changes.SYNTHETIC_PIN)
    page.locator("#vcConfirmChange").click()

    detail = page.locator("#vcDetail")
    home_card = detail.locator(".vc-endpoint", has_text="alex@home.example.test")
    expect(home_card).to_contain_text("Imported, not verified · from Apple Contacts (This Mac)")
    home_card.get_by_role("button", name="Confirm entry").click()
    expect(page.locator("#vcReview")).to_contain_text("Mark as confirmed")
    page.locator("#vcPin").fill(protected_changes.SYNTHETIC_PIN)
    page.locator("#vcConfirmChange").click()
    expect(detail.locator(".vc-endpoint", has_text="alex@home.example.test")).to_contain_text("Confirmed by you")
    expect(detail.locator(".vc-endpoint", has_text="alex@home.example.test").get_by_role("button", name="Confirm entry")).to_have_count(0)

    page.locator("#vcImport").click()
    page.locator("#vcImportSources .vc-card", has_text="This Mac").get_by_role("button", name="Remove source").click()
    page.locator("#vcImportRemoveKeep").click()
    expect(page.locator("#vcOverlay [role='status']")).to_contain_text("imported entries were kept")
    expect(page.locator("#vcImportSources")).to_contain_text("No source connected yet")

    assert sorted(c["display_name"] for c in store.active_contacts()) == ["Alex Chen", "Mara", "Northfield Kiln Repair", "Zoë Ng"]
    assert [s["status"] for s in importing.list_sources()] == ["removed"]
    assert len(framework.fetches) == 1, "the source was read once, for the preview"
    assert not any(method == "POST" and path == "/api/chat" for method, path in requests), "the surface never sends a chat turn"
    assert [e for e in errors if "Contacts" in e or "vc" in e or "import" in e.lower()] == [], errors
    context.close()
