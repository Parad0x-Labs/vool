"""Contact imports: Apple Contacts, Google Contacts and Microsoft Outlook behind one read-only seam, previewed with
duplicates named, applied only for what the owner explicitly selected, and failing with a typed reason and recovery.

What executes: the production import owner (core.contacts.importing), the three production adapters, and the Contacts
store. The sources are FIXTURES: a scripted object with the macOS Contacts framework's call shapes for Apple, and a
scripted KAS transport answering People API and Microsoft Graph response shapes for Google and Microsoft. No macOS
permission prompt, provider account, credential or network is used; those are live gates recorded separately.
"""
from __future__ import annotations

import json
import types

import pytest

from core.contacts import authority, importing, resolver, store
from core.contacts.importers import adapter_for
from core.contacts.importers.apple import AppleContactsAdapter
from core.contacts.importers.google import GoogleContactsAdapter
from core.contacts.importers.microsoft import MicrosoftContactsAdapter
from tests.contacts import protected_changes

OWNER = store.ACTOR_OWNER
PRIVATE_KEY_SHAPED = "ab" * 32


@pytest.fixture(autouse=True)
def _empty_contacts():
    protected_changes.reset()
    yield
    protected_changes.reset()


# --- fixture sources -------------------------------------------------------------------------------------------------

class _Labeled:
    def __init__(self, label, value):
        self._label, self._value = label, value

    def label(self):
        return self._label

    def value(self):
        return self._value


class _Phone:
    def __init__(self, text):
        self._text = text

    def stringValue(self):
        return self._text


class _Messaging:
    def __init__(self, service, username):
        self._service, self._username = service, username

    def service(self):
        return self._service

    def username(self):
        return self._username


class _Person:
    def __init__(self, identifier, given, family="", organization="", emails=(), phones=(), messaging=()):
        self._identifier, self._given, self._family, self._organization = identifier, given, family, organization
        self._emails, self._phones, self._messaging = emails, phones, messaging

    def identifier(self):
        return self._identifier

    def givenName(self):
        return self._given

    def familyName(self):
        return self._family

    def organizationName(self):
        return self._organization

    def emailAddresses(self):
        return [_Labeled(label, value) for label, value in self._emails]

    def phoneNumbers(self):
        return [_Labeled(label, _Phone(value)) for label, value in self._phones]

    def instantMessageAddresses(self):
        return [_Labeled(label, _Messaging(service, username)) for label, service, username in self._messaging]


def _framework(people, *, status, grant=True):
    """The Contacts framework's call shapes, scripted: authorization status, the access prompt, the fetch request."""
    fetches: list = []

    class CNContactStore:
        status_value = status
        prompts = 0

        @classmethod
        def authorizationStatusForEntityType_(cls, _entity):
            return cls.status_value

        @classmethod
        def alloc(cls):
            return cls()

        def init(self):
            return self

        def requestAccessForEntityType_completionHandler_(self, _entity, handler):
            CNContactStore.prompts += 1
            CNContactStore.status_value = 3 if grant else 2
            handler(grant, None)

        def enumerateContactsWithFetchRequest_error_usingBlock_(self, request, _error, block):
            stop = [False]
            for person in people:
                block(person, stop)
                if stop[0]:
                    break
            return True, None

    class CNContactFetchRequest:
        @classmethod
        def alloc(cls):
            return cls()

        def initWithKeysToFetch_(self, keys):
            self.keys = list(keys)
            fetches.append(self)
            return self

    class CNLabeledValue:
        @staticmethod
        def localizedStringForLabel_(label):
            return {"_$!<Work>!$_": "work", "_$!<Home>!$_": "home", "_$!<Mobile>!$_": "mobile"}.get(label, label)

    return types.SimpleNamespace(
        CNContactStore=CNContactStore, CNContactFetchRequest=CNContactFetchRequest, CNLabeledValue=CNLabeledValue, fetches=fetches,
        CNContactIdentifierKey="identifier", CNContactGivenNameKey="givenName", CNContactFamilyNameKey="familyName",
        CNContactOrganizationNameKey="organizationName", CNContactEmailAddressesKey="emailAddresses", CNContactPhoneNumbersKey="phoneNumbers",
        CNContactInstantMessageAddressesKey="instantMessageAddresses",
    )


class _Transport:
    """A scripted KAS transport: answers one queued (status, payload, headers) per request and records each request."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.requests: list = []

    def __call__(self, request):
        from core.kas.contract import KasResponse

        self.requests.append(request)
        status, payload, headers = self.answers.pop(0)
        return KasResponse(status=status, body=json.dumps(payload).encode(), headers=headers or {})


def _apple_people():
    return [
        _Person("A1", "Alex", "Chen", emails=[("_$!<Work>!$_", "alex.chen@example.test"), ("_$!<Home>!$_", "alex@home.example.test")]),
        _Person("A2", "Mara", "", emails=[("_$!<Work>!$_", "mara.okafor@example.test")], phones=[("_$!<Mobile>!$_", "+44 20 7946 0101")]),
        _Person("A3", "Zoë", "Ng", messaging=[("", "Telegram", "zoe_ng_42"), ("", "Vault", PRIVATE_KEY_SHAPED)]),
        _Person("A4", "", "", organization="Northfield Kiln Repair", emails=[("", "estimates@northfield-kiln.example.test")]),
        _Person("A5", "Alex", "Chen", emails=[("_$!<Work>!$_", "alex.chen@example.test")]),
        _Person("A6", "Rivera", "", emails=[("", "rivera@example.test")]),
    ]


# --- Apple Contacts --------------------------------------------------------------------------------------------------

def test_original_apple_import_previews_duplicates_saves_nothing_and_applies_only_the_selection() -> None:
    alex = store.create_contact(display_name="Alex Chen", actor=OWNER, endpoints=[{"kind": "email", "value": "alex.chen@example.test", "label": "work"}])
    store.create_contact(display_name="Mara", actor=OWNER, endpoints=[{"kind": "email", "value": "mara@other.example.test"}])
    store.create_contact(display_name="Rivera", actor=OWNER, endpoints=[{"kind": "email", "value": "rivera@example.test"}])
    framework = _framework(_apple_people(), status=0, grant=True)
    adapter = AppleContactsAdapter(binding=framework)
    with pytest.raises(store.ContactsError) as no_consent:
        importing.connect_source("apple_contacts", consent=False, adapter=adapter)
    assert no_consent.value.reason == "consent_required" and importing.list_sources() == []
    source = importing.connect_source("apple_contacts", consent=True, account_label="This Mac", adapter=adapter)
    before = store.active_contacts()

    run = importing.preview(source["source_id"], adapter=adapter)
    assert run["state"] == "previewed" and framework.CNContactStore.prompts == 1
    assert "note" not in json.dumps(framework.fetches[0].keys).lower()
    assert store.active_contacts() == before, "a preview saves nothing"
    items = {item["source_ref"]: item for item in run["items"]}
    assert items["A1"]["category"] == "adds_to_existing" and items["A1"]["suggested_action"] == f"merge:{alex['contact_id']}"
    assert [e["value"] for e in items["A1"]["entries"]] == ["alex@home.example.test"]
    assert items["A2"]["category"] == "same_name_different_entries"
    assert items["A3"]["rejected"] == [{"kind": "messaging", "label": "", "reason": "secret_material_refused"}]
    assert PRIVATE_KEY_SHAPED not in json.dumps(importing.get_run(run["run_id"])["preview"])
    assert items["A4"]["display_name"] == "Northfield Kiln Repair" and items["A4"]["category"] == "new"
    assert items["A5"]["category"] == "already_saved" and items["A5"]["suggested_action"] == "skip"
    assert items["A6"]["category"] == "already_saved"

    applied = importing.apply(run["run_id"], {items["A1"]["item_id"]: f"merge:{alex['contact_id']}", items["A4"]["item_id"]: "create",
                                              items["A2"]["item_id"]: "skip"})
    assert len(applied["created"]) == 1 and applied["merged"] == [] and applied["failed"] == [], applied
    [waiting] = applied["pending"]
    assert waiting["items"] == [items["A1"]["item_id"]] and waiting["title"] == "Change Alex Chen", waiting
    assert resolver.resolve("Alex Chen", kind="email", label="home").status == "no_endpoint", "an entry for a saved contact waits for the PIN"
    protected_changes.confirm(authority.get_operation(waiting["operation_id"]))
    names_now = sorted(c["display_name"] for c in store.active_contacts())
    assert names_now == ["Alex Chen", "Mara", "Northfield Kiln Repair", "Rivera"], "unselected people were not imported"
    kiln = resolver.resolve("Northfield Kiln Repair", kind="email")
    assert kiln.status == "resolved" and kiln.snapshot["verification"] == "imported_unverified"
    assert kiln.snapshot["source"]["provider"] == "apple_contacts" and "imported, not verified" in resolver.describe_snapshot(kiln.snapshot)
    home = resolver.resolve("Alex Chen", kind="email", label="home")
    assert home.snapshot["verification"] == "imported_unverified" and home.snapshot["value"] == "alex@home.example.test"
    assert resolver.resolve("Alex Chen", kind="email", label="work").snapshot["verification"] == "user_entered"
    with pytest.raises(store.ContactsError) as again:
        importing.apply(run["run_id"], {items["A4"]["item_id"]: "create"})
    assert again.value.reason == "run_not_applicable"


def test_apple_permission_denied_or_missing_bridge_fails_typed_and_imports_nothing() -> None:
    denied = AppleContactsAdapter(binding=_framework(_apple_people(), status=2))
    source = importing.connect_source("apple_contacts", consent=True, adapter=denied)
    run = importing.preview(source["source_id"], adapter=denied)
    assert run["state"] == "failed" and run["error"]["reason"] == "permission_denied" and "System Settings" in run["error"]["recovery"]
    assert importing.get_source(source["source_id"])["status"] == "attention"
    declined = AppleContactsAdapter(binding=_framework(_apple_people(), status=0, grant=False))
    assert importing.preview(source["source_id"], adapter=declined)["error"]["reason"] == "permission_denied"
    unbridged = AppleContactsAdapter(binding=None)
    if unbridged.authorization(None)["state"] == "unavailable":
        assert importing.preview(source["source_id"], adapter=unbridged)["error"]["reason"] == "os_binding_unavailable"
    assert store.active_contacts() == []


# --- Google Contacts -------------------------------------------------------------------------------------------------

def _google_page(people, token=""):
    payload = {"connections": people}
    if token:
        payload["nextPageToken"] = token
    return (200, payload, {})


def test_novel_google_import_reads_every_page_with_the_binding_and_names_providers_honestly() -> None:
    transport = _Transport([
        _google_page([{"resourceName": "people/c1", "names": [{"displayName": "Mara Okafor", "metadata": {"primary": True}}],
                       "emailAddresses": [{"value": "mara.okafor@example.test", "formattedType": "Work"}],
                       "imClients": [{"username": "mara_pots", "protocol": "telegram"}, {"username": "mara.o", "protocol": "skype", "formattedProtocol": "Skype"}]}],
                     token="page-2"),
        _google_page([{"resourceName": "people/c2", "names": [{"displayName": "Łukasz Żółć"}], "phoneNumbers": [{"value": "600 123 456", "canonicalForm": "+48600123456"}]}]),
    ])
    adapter = GoogleContactsAdapter(transport=transport)
    with pytest.raises(store.ContactsError) as unbound:
        importing.connect_source("google", consent=True, account_label="me@gmail.example.test", adapter=adapter)
    assert unbound.value.reason == "credential_binding_required"
    source = importing.connect_source("google", consent=True, account_label="me@gmail.example.test", auth_binding="binding-google-1", adapter=adapter)
    run = importing.preview(source["source_id"], adapter=adapter)
    assert run["state"] == "previewed" and run["read"] == 2
    assert [r.auth for r in transport.requests] == ["binding-google-1", "binding-google-1"]
    assert all(r.url.startswith("https://people.googleapis.com/v1/people/me/connections?") for r in transport.requests)
    assert "pageToken=page-2" in transport.requests[1].url and "authorization" not in json.dumps({k.lower(): 1 for k in transport.requests[0].headers})
    mara = next(item for item in run["items"] if item["source_ref"] == "people/c1")
    channels = {(e["channel"], e["provider_account"]) for e in mara["entries"] if e["kind"] == "messaging"}
    assert channels == {("telegram", ""), ("other", "Skype")}
    applied = importing.apply(run["run_id"], {item["item_id"]: "create" for item in run["items"]})
    assert len(applied["created"]) == 2
    lukasz = resolver.resolve("Łukasz Żółć", kind="phone")
    assert lukasz.snapshot["canonical"] == "+48600123456" and lukasz.snapshot["source"]["provider"] == "google"


@pytest.mark.parametrize("answers, reason", [
    ([(403, {"error": {"code": 403, "status": "PERMISSION_DENIED", "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}]}}, {})], "scope_missing"),
    ([(401, {"error": {"code": 401, "status": "UNAUTHENTICATED"}}, {})], "needs_reconnect"),
    ([(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}}, {"Retry-After": "30"})], "rate_limited"),
    ([_google_page([{"resourceName": "people/c1", "names": [{"displayName": "Mara"}], "emailAddresses": [{"value": "m@example.test"}]}], token="t2"),
      (503, {"error": {"code": 503}}, {})], "provider_unavailable"),
])
def test_google_failures_are_typed_and_a_partial_read_is_never_applied(answers, reason) -> None:
    adapter = GoogleContactsAdapter(transport=_Transport(answers))
    source = importing.connect_source("google", consent=True, auth_binding="binding-google-1", adapter=adapter)
    run = importing.preview(source["source_id"], adapter=adapter)
    assert run["state"] == "failed" and run["error"]["reason"] == reason and run["error"]["recovery"]
    if reason == "rate_limited":
        assert run["error"]["retry_after"] == 30
    if reason == "provider_unavailable":
        assert run["error"]["partial_read"] == 1
    with pytest.raises(store.ContactsError):
        importing.apply(run["run_id"], {})
    assert store.active_contacts() == []


# --- Microsoft Outlook -----------------------------------------------------------------------------------------------

def test_microsoft_import_follows_next_links_and_keeps_teams_identities_with_their_account() -> None:
    transport = _Transport([
        (200, {"value": [{"id": "AAMk-1", "displayName": "Northfield Kiln Repair", "emailAddresses": [{"name": "Estimates", "address": "estimates@northfield-kiln.example.test"}],
                          "businessPhones": ["+44 161 496 0000"]}],
               "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/contacts?$skip=1"}, {}),
        (200, {"value": [{"id": "AAMk-2", "givenName": "Priya", "surname": "Nair", "imAddresses": ["sip:priya.nair@clayworks.example.test"], "mobilePhone": "+44 7700 900123"}]}, {}),
    ])
    adapter = MicrosoftContactsAdapter(transport=transport)
    source = importing.connect_source("microsoft", consent=True, account_label="studio@clayworks.example.test", auth_binding="binding-graph-1", adapter=adapter)
    run = importing.preview(source["source_id"], adapter=adapter)
    assert run["state"] == "previewed" and run["read"] == 2
    assert transport.requests[0].headers.get("Prefer") == 'IdType="ImmutableId"' and transport.requests[1].url.endswith("$skip=1")
    priya = next(item for item in run["items"] if item["source_ref"] == "AAMk-2")
    assert priya["display_name"] == "Priya Nair"
    assert {(e["kind"], e["channel"], e["provider_account"]) for e in priya["entries"]} == {("messaging", "teams", "studio@clayworks.example.test"), ("phone", "", "")}
    denied = MicrosoftContactsAdapter(transport=_Transport([(403, {"error": {"code": "ErrorAccessDenied"}}, {})]))
    assert importing.preview(source["source_id"], adapter=denied)["error"]["reason"] == "scope_missing"


def test_removing_a_source_keeps_or_erases_only_what_is_still_as_imported() -> None:
    framework = _framework([_Person("B1", "Priya", "Nair", emails=[("", "priya@example.test")], phones=[("", "+44 7700 900123")]),
                            _Person("B2", "Tomás", "Silva", emails=[("", "tomas@example.test")])], status=3)
    adapter = AppleContactsAdapter(binding=framework)
    source = importing.connect_source("apple_contacts", consent=True, adapter=adapter)
    run = importing.preview(source["source_id"], adapter=adapter)
    importing.apply(run["run_id"], {item["item_id"]: "create" for item in run["items"]})
    priya = store.list_contacts("Priya")[0]
    phone = next(e for e in priya["endpoints"] if e["kind"] == "phone")
    protected_changes.update_contact(priya["contact_id"], change_endpoints=[{"endpoint_id": phone["endpoint_id"], "value": "+44 7700 900999"}])
    outcome = importing.remove_source(source["source_id"], erase_imported=True)
    erase = outcome["erase_pending"]
    assert outcome["state"] == "removed" and erase["endpoints_to_erase"] == 2 and len(erase["contacts_to_delete"]) == 1, outcome
    assert sorted(c["display_name"] for c in store.active_contacts()) == ["Priya Nair", "Tomás Silva"], "erasing saved entries waits for the PIN"
    protected_changes.confirm(authority.get_operation(erase["operation_id"]))
    assert [c["display_name"] for c in store.active_contacts()] == ["Priya Nair"], "the fully imported contact went; the edited one stayed"
    kept = store.list_contacts("Priya")[0]["endpoints"]
    assert [(e["kind"], e["value"], e["verification"]) for e in kept] == [("phone", "+44 7700 900999", "user_entered")]
    with pytest.raises(store.ContactsError) as removed:
        importing.preview(source["source_id"], adapter=adapter)
    assert removed.value.reason == "source_removed"


def test_the_registry_offers_the_three_providers_and_no_writing_adapter() -> None:
    for provider in ("apple_contacts", "google", "microsoft"):
        adapter = adapter_for(provider)
        assert not any(name.startswith(("write", "save", "update", "delete", "create")) for name in dir(adapter) if not name.startswith("_")), provider
