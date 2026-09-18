"""Contact import doors: /api/contacts/import/* through the production API service, the command registry, the import owner
and the adapters that core.contacts.importers.adapter_for builds.

What executes: core.web.api.service dispatch -> command registry (contacts.import.*) -> core.web.api.contacts_api ->
core.contacts.importing -> AppleContactsAdapter / GoogleContactsAdapter. Two seams are FIXTURES: the macOS Contacts bridge
(core.contacts.importers.apple._load_binding returns the scripted framework) and the KAS transport
(core.kas.transport.build_transport returns scripted People API answers). The credential bindings are rows in this run's
isolated binding index, judged by the production gate. No permission prompt, provider account, credential or network.
"""
from __future__ import annotations

import json

import pytest

from core.contacts import importing, resolver, store
from core.contacts.importers import apple
from tests.contacts import protected_changes
from tests.contacts.test_contacts_imports import PRIVATE_KEY_SHAPED, _apple_people, _framework, _google_page, _Transport
from tests.contacts.test_contacts_surface import _get, _post

SCOPE_INSUFFICIENT = (403, {"error": {"code": 403, "status": "PERMISSION_DENIED", "details": [
    {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}]}}, {})


@pytest.fixture(autouse=True)
def _empty_contacts():
    protected_changes.reset()
    yield
    protected_changes.reset()


@pytest.fixture
def binding_index():
    """A Google Contacts connection (verified), a Gemini-style key stored under 'google' (verified, another service) and a
    Microsoft Outlook contacts connection that is no longer verified."""
    from core.credential_intelligence import binding

    path = binding.index_path()
    before = path.read_text(encoding="utf-8") if path.exists() else None
    base = {"capability_family": "contacts", "created_at": "2026-09-15T09:00:00+00:00", "slot": "fixture-slot", "last_verified_at": "2026-09-15T10:00:00+00:00"}
    binding.save_index({
        "google_contacts": {**base, "binding_id": "cb_google_contacts", "provider_id": "google_contacts", "provider_label": "Google Contacts",
                            "account": "me@gmail.example.test", "status": "verified"},
        "google": {**base, "binding_id": "cb_google", "provider_id": "google", "provider_label": "Google AI Studio", "account": "",
                   "capability_family": "model", "status": "verified"},
        "microsoft_contacts": {**base, "binding_id": "cb_microsoft_contacts", "provider_id": "microsoft_contacts", "provider_label": "Microsoft Outlook",
                               "account": "studio@clayworks.example.test", "status": "unauthorized", "last_verified_at": None},
    })
    yield
    if before is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(before, encoding="utf-8")


def test_original_apple_import_through_the_owner_doors_applies_only_the_selection_and_confirms_an_entry(monkeypatch) -> None:
    framework = _framework(_apple_people(), status=3)
    monkeypatch.setattr(apple, "_load_binding", lambda: framework)
    status, created = _post("/api/contacts/create", {"display_name": "Alex Chen", "endpoints": [{"kind": "email", "value": "alex.chen@example.test", "label": "work"}]})
    assert status == 200, created
    alex_id = created["contact"]["contact_id"]

    status, sources = _get("/api/contacts/import/sources")
    assert status == 200 and [p["provider"] for p in sources["providers"]] == ["apple_contacts", "google", "microsoft"]
    assert next(p for p in sources["providers"] if p["provider"] == "apple_contacts")["authorization"]["state"] == "authorized"
    assert sources["sources"] == []

    status, refused = _post("/api/contacts/import/connect", {"provider": "apple_contacts", "consent": False, "account_label": "This Mac"})
    assert status == 409 and refused["reason"] == "consent_required" and importing.list_sources() == []
    status, connected = _post("/api/contacts/import/connect", {"provider": "apple_contacts", "consent": True, "account_label": "This Mac"})
    assert status == 200 and connected["source"]["status"] == "connected", connected
    source_id = connected["source"]["source_id"]
    assert framework.fetches == [], "connecting reads nothing"

    status, previewed = _post("/api/contacts/import/preview", {"source_id": source_id})
    assert status == 200, previewed
    run = previewed["run"]
    assert run["state"] == "previewed" and run["read"] == 6 and len(framework.fetches) == 1
    assert [c["display_name"] for c in _get("/api/contacts")[1]["contacts"]] == ["Alex Chen"], "a preview saves nothing"
    assert PRIVATE_KEY_SHAPED not in json.dumps(previewed)
    items = {item["source_ref"]: item for item in run["items"]}
    assert items["A1"]["suggested_action"] == f"merge:{alex_id}" and items["A5"]["category"] == "already_saved"

    selections = {items["A1"]["item_id"]: f"merge:{alex_id}", items["A4"]["item_id"]: "create", items["A6"]["item_id"]: "skip"}
    status, applied = _post("/api/contacts/import/apply", {"run_id": run["run_id"], "selections": selections})
    assert status == 200 and len(applied["applied"]["created"]) == 1 and applied["applied"]["merged"] == [], applied
    [waiting] = applied["applied"]["pending"]
    status, review = _get("/api/contacts/operations/detail", {"id": [waiting["operation_id"]]})
    assert status == 200 and review["operation"]["title"] == "Change Alex Chen", review
    assert review["operation"]["steps"][0]["changes"][0]["after"]["value"] == "alex@home.example.test"
    status, merged = protected_changes.confirm_through_the_door(_post, review["operation"])
    assert status == 200 and merged["status"] == "committed", merged
    assert sorted(c["display_name"] for c in _get("/api/contacts")[1]["contacts"]) == ["Alex Chen", "Northfield Kiln Repair"], "only the selection"
    status, again = _post("/api/contacts/import/apply", {"run_id": run["run_id"], "selections": selections})
    assert status == 409 and again["reason"] == "run_not_applicable"

    detail = _get("/api/contacts/detail", {"id": [alex_id]})[1]["contact"]
    home = next(e for e in detail["endpoints"] if e["value"] == "alex@home.example.test")
    assert home["verification"] == "imported_unverified" and home["source"]["provider"] == "apple_contacts"
    reviewed = resolver.resolve("Alex Chen", kind="email", label="home").snapshot
    with pytest.raises(store.ContactsError) as not_owner:
        store.update_contact(alex_id, actor=store.ACTOR_MODEL, confirm_endpoint_ids=[home["endpoint_id"]])
    assert not_owner.value.reason == "authentication_required"
    status, proposed = _post("/api/contacts/update", {"contact_id": alex_id, "expected_revision": detail["revision"], "confirm_endpoint_ids": [home["endpoint_id"]]})
    assert status == 200 and proposed["status"] == "pending_authentication" and proposed["applied"] is False, proposed
    assert resolver.resolve("Alex Chen", kind="email", label="home").snapshot["verification"] == "imported_unverified", "confirming an entry waits for the PIN"
    status, confirmed = protected_changes.confirm_through_the_door(_post, proposed["operation"])
    assert status == 200 and [c["action"] for c in confirmed["result"]["updated"][0]["changes"]] == ["endpoint_confirmed"], confirmed
    assert resolver.resolve("Alex Chen", kind="email", label="home").snapshot["verification"] == "user_confirmed"
    assert resolver.verify_snapshot(reviewed).ok, "confirming an entry is not a destination change; what was reviewed stays current"

    status, removed = _post("/api/contacts/import/remove", {"source_id": source_id, "erase_imported": True})
    erase = removed["removed"]["erase_pending"]
    assert status == 200 and erase["endpoints_to_erase"] == 1 and len(erase["contacts_to_delete"]) == 1, removed
    assert sorted(c["display_name"] for c in store.active_contacts()) == ["Alex Chen", "Northfield Kiln Repair"], "erasing waits for the PIN"
    status, review = _get("/api/contacts/operations/detail", {"id": [erase["operation_id"]]})
    status, erased = protected_changes.confirm_through_the_door(_post, review["operation"])
    assert status == 200 and erased["status"] == "committed", erased
    kept = {e["value"]: e["verification"] for e in _get("/api/contacts/detail", {"id": [alex_id]})[1]["contact"]["endpoints"]}
    assert kept == {"alex.chen@example.test": "user_entered", "alex@home.example.test": "user_confirmed"}
    assert [c["display_name"] for c in store.active_contacts()] == ["Alex Chen"]
    status, gone = _post("/api/contacts/import/preview", {"source_id": source_id})
    assert status == 410 and gone["reason"] == "source_removed"
    assert len(framework.fetches) == 1, "the source was read once and never written"


def test_novel_google_import_names_only_its_own_verified_binding_and_recovers_after_a_missing_scope(monkeypatch, binding_index) -> None:
    from core.kas import transport as kas_transport

    transport = _Transport([SCOPE_INSUFFICIENT, _google_page([
        {"resourceName": "people/z1", "names": [{"displayName": "Zoë Ng"}], "emailAddresses": [{"value": "zoe.ng@fixture.test", "formattedType": "Work"}]},
        {"resourceName": "people/z2", "names": [{"displayName": "Łukasz Żółć"}], "phoneNumbers": [{"value": "600 123 456", "canonicalForm": "+48600123456"}]},
    ])])
    monkeypatch.setattr(kas_transport, "build_transport", lambda **_: transport)

    status, sources = _get("/api/contacts/import/sources")
    assert status == 200 and [b["binding_id"] for b in sources["bindings"]] == ["cb_google_contacts"], sources["bindings"]
    assert "fixture-slot" not in json.dumps(sources)
    google = next(p for p in sources["providers"] if p["provider"] == "google")
    assert google["needs_binding"] is True and google["binding_providers"] == ["google_contacts"]

    for extra, reason in (({}, "credential_binding_required"), ({"auth_binding": "cb_nobody"}, "unknown_credential_binding"),
                          ({"auth_binding": "cb_google"}, "credential_binding_wrong_provider")):
        status, refused = _post("/api/contacts/import/connect", {"provider": "google", "consent": True, **extra})
        assert status == 400 and refused["reason"] == reason, (extra, refused)
    status, refused = _post("/api/contacts/import/connect", {"provider": "microsoft", "consent": True, "auth_binding": "cb_microsoft_contacts"})
    assert status == 400 and refused["reason"] == "credential_binding_not_verified", refused
    assert importing.list_sources() == [] and transport.requests == [], "no refused connection reached a provider"

    status, connected = _post("/api/contacts/import/connect", {"provider": "google", "consent": True, "account_label": "me@gmail.example.test",
                                                               "auth_binding": "cb_google_contacts"})
    assert status == 200, connected
    source_id = connected["source"]["source_id"]
    status, failed = _post("/api/contacts/import/preview", {"source_id": source_id})
    assert status == 409 and failed["reason"] == "scope_missing" and "contacts read permission" in failed["message"], failed
    assert [(s["status"], s["recovery"]) for s in _get("/api/contacts/import/sources")[1]["sources"]] == [("attention", failed["message"])]
    status, not_applied = _post("/api/contacts/import/apply", {"run_id": failed["run"]["run_id"], "selections": {}})
    assert status == 409 and not_applied["reason"] == "run_not_applicable"
    assert store.active_contacts() == []

    status, previewed = _post("/api/contacts/import/preview", {"source_id": source_id})
    assert status == 200 and previewed["run"]["read"] == 2, previewed
    assert [r.auth for r in transport.requests] == ["cb_google_contacts", "cb_google_contacts"]
    assert all(r.url.startswith("https://people.googleapis.com/v1/people/me/connections?") for r in transport.requests)
    assert _get("/api/contacts/import/sources")[1]["sources"][0]["status"] == "connected"
    selections = {item["item_id"]: "create" for item in previewed["run"]["items"]}
    status, applied = _post("/api/contacts/import/apply", {"run_id": previewed["run"]["run_id"], "selections": selections})
    assert status == 200 and len(applied["applied"]["created"]) == 2, applied
    zoe = resolver.resolve("Zoë Ng", kind="email")
    assert (zoe.snapshot["value"], zoe.snapshot["verification"], zoe.snapshot["source"]["provider"]) == ("zoe.ng@fixture.test", "imported_unverified", "google")
    assert "imported, not verified" in resolver.describe_snapshot(zoe.snapshot)
    assert resolver.resolve("Łukasz Żółć", kind="phone").snapshot["canonical"] == "+48600123456"


def test_apple_without_the_os_bridge_fails_typed_with_its_recovery_and_imports_nothing(monkeypatch) -> None:
    monkeypatch.setattr(apple, "_load_binding", lambda: None)
    status, sources = _get("/api/contacts/import/sources")
    auth = next(p for p in sources["providers"] if p["provider"] == "apple_contacts")["authorization"]
    assert status == 200 and auth["state"] == "unavailable" and "macOS Contacts bridge" in auth["recovery"]
    status, connected = _post("/api/contacts/import/connect", {"provider": "apple_contacts", "consent": True})
    assert status == 200, connected
    status, failed = _post("/api/contacts/import/preview", {"source_id": connected["source"]["source_id"]})
    assert status == 409 and failed["reason"] == "os_binding_unavailable" and failed["message"] == auth["recovery"], failed
    assert [(s["status"], s["recovery"]) for s in _get("/api/contacts/import/sources")[1]["sources"]] == [("attention", auth["recovery"])]
    assert store.active_contacts() == []


def test_the_import_doors_refuse_other_peers_types_origins_and_the_model_principal() -> None:
    from core.command_registry.execute import ExecutionContext, execute_command

    body = {"provider": "apple_contacts", "consent": True}
    assert _get("/api/contacts/import/sources", client_host="192.168.1.20")[0] == 403
    assert _post("/api/contacts/import/connect", body, client_host="192.168.1.20")[0] == 403
    assert _post("/api/contacts/import/connect", body, headers={"content-type": "text/plain"})[0] == 415
    assert _post("/api/contacts/import/connect", body, headers={"content-type": "application/json", "origin": "https://evil.example"})[0] == 403
    envelope = execute_command("contacts.import.connect", {"payload": body},
                               context=ExecutionContext(projection="api", principal="model",
                                                        extra={"client_host": "127.0.0.1", "headers": {"content-type": "application/json"}}))
    assert not envelope.ok
    assert importing.list_sources() == []
