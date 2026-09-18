"""PA Contacts owner: endpoint identity, the store's law, the resolver's rules and the tool path's provenance.

What executes: the production Contacts store on the suite's migrated SQLite database (storage.migrations version 7), the
wallet owner's own chain validators, the runtime checkpoint store and its request provenance authority, and the
production tool contracts and permission policy. No model, network or provider is involved.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

from core.contacts import authority, resolver, store, tools
from core.contacts.endpoints import EndpointError, normalize_endpoint, secret_material_reason
from tests.contacts import protected_changes

ROOT = Path(__file__).resolve().parents[2]
SOLANA_DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
BASE_SEPOLIA = "eip155:84532"
ETH_SEPOLIA = "eip155:11155111"
OWNER = store.ACTOR_OWNER
# EIP-55 reference vectors: valid checksummed addresses
EVM_A = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
EVM_B = "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359"


@pytest.fixture(autouse=True)
def _empty_contacts():
    protected_changes.reset()
    yield
    protected_changes.reset()


def _sol_key() -> str:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from core.vool_wallet import b58encode

    return b58encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


def _turn(text: str, session: str = "openclaw:contacts-owner") -> dict:
    """A turn exactly as the runtime front door records it: a checkpoint holding the visible user text and its stamp."""
    from core.agent_runtime.request_authority import REQUEST_PROVENANCE_KEY, request_provenance_for_visible_user_text
    from core.runtime_continuity import create_runtime_checkpoint

    checkpoint = create_runtime_checkpoint(session_id=session, request_text=text,
                                           source_context={REQUEST_PROVENANCE_KEY: request_provenance_for_visible_user_text(text, session_id=session)})
    return {"runtime_checkpoint_id": checkpoint["checkpoint_id"], "runtime_session_id": session, "session_id": session}


# --- identity ------------------------------------------------------------------------------------------------------

def test_solana_case_is_identity_and_evm_case_is_only_a_checksum() -> None:
    sol = _sol_key()
    saved = normalize_endpoint({"kind": "wallet", "value": sol, "network": SOLANA_DEVNET})
    assert saved.canonical == sol and saved.chain_family == "svm"
    try:
        flipped = normalize_endpoint({"kind": "wallet", "value": sol.swapcase(), "network": SOLANA_DEVNET})
        assert flipped.canonical != saved.canonical
    except EndpointError as exc:
        assert exc.reason == "wallet_address_invalid"
    lower = normalize_endpoint({"kind": "wallet", "value": EVM_A.lower(), "network": BASE_SEPOLIA})
    checksummed = normalize_endpoint({"kind": "wallet", "value": EVM_A, "network": "base-sepolia"})
    assert lower.canonical == checksummed.canonical == EVM_A.lower()
    with pytest.raises(EndpointError) as bad:
        normalize_endpoint({"kind": "wallet", "value": "0x5AAeb6053F3E94C9b9A09f33669435E7Ef1BeAed", "network": BASE_SEPOLIA})
    assert bad.value.reason == "wallet_address_checksum"
    with pytest.raises(EndpointError) as wrong_family:
        normalize_endpoint({"kind": "wallet", "value": sol, "network": BASE_SEPOLIA})
    assert wrong_family.value.reason == "wallet_address_invalid"


def test_the_same_evm_address_on_two_networks_is_two_destinations() -> None:
    contact = store.create_contact(display_name="Zoë Ng", actor=OWNER, endpoints=[
        {"kind": "wallet", "value": EVM_B, "network": BASE_SEPOLIA}, {"kind": "wallet", "value": EVM_B, "network": ETH_SEPOLIA}])
    assert len(contact["endpoints"]) == 2 and len({e["fingerprint"] for e in contact["endpoints"]}) == 2
    base = resolver.resolve("Zoë Ng", kind="wallet", network="base-sepolia")
    assert base.status == "resolved" and base.snapshot["chain_network"] == BASE_SEPOLIA
    both = resolver.resolve("Zoë Ng", kind="wallet")
    assert both.status == "ambiguous_endpoint" and {c["chain_network"] for c in both.choices} == {BASE_SEPOLIA, ETH_SEPOLIA}
    assert resolver.resolve("Zoë Ng", kind="wallet", environment="mainnet").status == "no_endpoint"
    assert resolver.resolve("Zoë Ng", kind="wallet", family="svm").status == "no_endpoint"


# --- resolution ----------------------------------------------------------------------------------------------------

def test_original_two_alexes_ask_and_novel_service_alias_resolves_by_label() -> None:
    chen = store.create_contact(display_name="Alex Chen", actor=OWNER, endpoints=[
        {"kind": "email", "value": "alex.chen@example.test", "label": "work"}, {"kind": "email", "value": "alex@home.example.test", "label": "personal"}])
    rivera = store.create_contact(display_name="Alex Rivera", actor=OWNER, endpoints=[{"kind": "email", "value": "rivera@example.test"}])
    ask = resolver.resolve("Alex", kind="email")
    assert ask.status == "ambiguous_contact" and ask.snapshot is None and "Which one" in ask.message
    assert {c["contact_id"] for c in ask.choices} == {chen["contact_id"], rivera["contact_id"]}
    two = resolver.resolve("Alex Chen", kind="email")
    assert two.status == "ambiguous_endpoint" and len(two.choices) == 2
    work = resolver.resolve("Alex Chen (work)", kind="email")
    assert work.status == "resolved" and work.snapshot["value"] == "alex.chen@example.test"
    chosen = resolver.resolve(two.choices[1]["choose_with"], kind="email")
    assert chosen.status == "resolved" and chosen.snapshot["endpoint_id"] == two.choices[1]["endpoint_id"]

    store.create_contact(display_name="Northfield Kiln Repair", kind="service", actor=OWNER, aliases=["kiln shop"], endpoints=[
        {"kind": "email", "value": "estimates@northfield-kiln.example.test", "label": "estimates"},
        {"kind": "email", "value": "billing@northfield-kiln.example.test", "label": "billing"}])
    billing = resolver.resolve("kiln shop (billing)", kind="email")
    assert billing.status == "resolved" and billing.snapshot["matched_by"] == "alias"
    assert billing.snapshot["value"] == "billing@northfield-kiln.example.test"
    assert resolver.resolve("kiln shop", kind="email").status == "ambiguous_endpoint"


def test_accents_and_near_spellings_are_suggestions_never_a_choice() -> None:
    store.create_contact(display_name="Zoë Ng", actor=OWNER, endpoints=[{"kind": "messaging", "value": "@zoe_ng_42", "channel": "telegram"}])
    folded = resolver.resolve("Zoe", kind="messaging")
    assert folded.status == "not_found" and folded.snapshot is None
    assert [s["display_name"] for s in folded.suggestions] == ["Zoë Ng"]
    assert resolver.resolve("Zoë Nq", kind="messaging").snapshot is None
    exact = resolver.resolve("zoë", kind="messaging")
    assert exact.status == "resolved" and exact.snapshot["channel"] == "telegram"


def test_unicode_names_normalise_and_round_trip() -> None:
    decomposed = unicodedata.normalize("NFD", "José Álvarez")
    store.create_contact(display_name=decomposed, actor=OWNER, endpoints=[{"kind": "phone", "value": "+34 600 123 456"}])
    assert resolver.resolve("José Álvarez", kind="phone").snapshot["canonical"] == "+34600123456"
    store.create_contact(display_name="李小龙", actor=OWNER, endpoints=[{"kind": "email", "value": "lee@example.test"}])
    assert resolver.resolve("李小龙", kind="email").snapshot["value"] == "lee@example.test"
    lukasz = store.create_contact(display_name="Łukasz Żółć", actor=OWNER, endpoints=[{"kind": "email", "value": "lukasz@example.test"}])
    assert store.get_contact(lukasz["contact_id"])["display_name"] == "Łukasz Żółć"


def test_an_address_that_only_looks_like_a_saved_one_is_flagged() -> None:
    store.create_contact(display_name="Alex Chen", actor=OWNER, endpoints=[{"kind": "wallet", "value": EVM_A, "network": BASE_SEPOLIA}])
    body = EVM_A.lower()[2:]
    lookalike = "0x" + body[:4] + "1" * 32 + body[-4:]
    flagged = resolver.resolve(lookalike, kind="wallet")
    assert flagged.status == "explicit" and flagged.snapshot["lookalikes"][0]["contact_display_name"] == "Alex Chen"
    assert "looks like" in flagged.snapshot["warning"] and "not a saved address" in flagged.snapshot["warning"]
    exact = resolver.resolve(EVM_A.lower(), kind="wallet")
    assert "lookalikes" not in exact.snapshot and exact.snapshot["saved_matches"][0]["contact_display_name"] == "Alex Chen"


# --- change detection, deletion, persistence -------------------------------------------------------------------------

def test_a_label_change_keeps_the_binding_and_a_destination_change_breaks_it() -> None:
    contact = store.create_contact(display_name="Alex Chen", actor=OWNER, endpoints=[{"kind": "email", "value": "alex.chen@example.test", "label": "work"}])
    snapshot = resolver.resolve("Alex Chen", kind="email").snapshot
    endpoint_id = snapshot["endpoint_id"]
    protected_changes.update_contact(contact["contact_id"], change_endpoints=[{"endpoint_id": endpoint_id, "label": "office"}])
    assert resolver.verify_snapshot(snapshot).status == "current"
    protected_changes.update_contact(contact["contact_id"], change_endpoints=[{"endpoint_id": endpoint_id, "value": "a.chen@example.test"}])
    changed = resolver.verify_snapshot(snapshot)
    assert changed.status == "endpoint_changed" and not changed.ok and "a.chen@example.test" in changed.message
    protected_changes.update_contact(contact["contact_id"], remove_endpoint_ids=[endpoint_id])
    assert resolver.verify_snapshot(snapshot).status == "endpoint_removed"


def test_no_actor_value_changes_removes_deletes_or_accepts_through_the_store() -> None:
    contact = store.create_contact(display_name="Alex Chen", actor=OWNER, endpoints=[{"kind": "email", "value": "alex.chen@example.test"}])
    endpoint_id = contact["endpoints"][0]["endpoint_id"]
    pending = store.add_suggestion(endpoint={"kind": "email", "value": "evil@example.test"}, origin="email_message", actor=store.ACTOR_MODEL,
                                   contact_id=contact["contact_id"], replaces_endpoint_id=endpoint_id)
    for actor in (store.ACTOR_MODEL, store.ACTOR_MODEL_APPROVED, store.ACTOR_USER_TEXT, OWNER):
        for kwargs in ({"change_endpoints": [{"endpoint_id": endpoint_id, "value": "evil@example.test"}]}, {"remove_endpoint_ids": [endpoint_id]},
                       {"display_name": "Alex Chen (old)"}):
            with pytest.raises(store.ContactsError) as refused:
                store.update_contact(contact["contact_id"], actor=actor, **kwargs)
            assert (refused.value.reason, refused.value.status) == ("authentication_required", 403), actor
        with pytest.raises(store.ContactsError):
            store.delete_contact(contact["contact_id"], actor=actor)
        with pytest.raises(store.ContactsError):
            store.accept_suggestion(pending["suggestion"]["suggestion_id"], actor=actor)
    assert store.get_contact(contact["contact_id"])["endpoints"][0]["value"] == "alex.chen@example.test"
    assert authority.list_operations() == [], "a refused direct write records nothing"


def test_delete_leaves_a_tombstone_without_names_values_or_journal_details() -> None:
    contact = store.create_contact(display_name="Alex Chen", actor=OWNER, aliases=["AC"], notes="met at the kiln fair", endpoints=[
        {"kind": "email", "value": "alex.chen@example.test"}, {"kind": "wallet", "value": EVM_A, "network": BASE_SEPOLIA}])
    protected_changes.update_contact(contact["contact_id"], add_endpoints=[{"kind": "phone", "value": "+44 20 7946 0000"}])
    snapshot = resolver.resolve("AC", kind="email").snapshot
    outcome = protected_changes.delete_contact(contact["contact_id"])
    assert outcome["endpoints_erased"] == 3
    tomb = store.get_contact(contact["contact_id"], include_removed_endpoints=True)
    assert (tomb["state"], tomb["display_name"], tomb["aliases"], tomb["notes"]) == ("deleted", "", [], "")
    assert all(e["value"] == "" and e["canonical"] == "" and e["fingerprint"] == "" for e in tomb["endpoints"])
    journal = json.dumps(store.list_events(contact["contact_id"]))
    for secret_of_the_deleted in ("alex.chen@example.test", "kiln fair", EVM_A.lower(), "7946"):
        assert secret_of_the_deleted not in journal.lower()
    with store._reader() as conn:
        kept = conn.execute("SELECT key_hash FROM contact_identity_tombstones WHERE contact_id = ?", (contact["contact_id"],)).fetchall()
        records = "\n".join(row["payload_json"] + row["result_json"] for row in conn.execute("SELECT payload_json, result_json FROM contact_operations")).lower()
    assert kept and all(len(row["key_hash"]) == 64 for row in kept), "a deleted name is remembered only as keyed hashes"
    assert '"alex.chen@example.test"' not in json.dumps(records) and records.count('"operation_id"') >= 0
    for secret_of_the_deleted in ("alex.chen@example.test", "kiln fair", EVM_A.lower(), "7946", '"alex chen"', '"ac"'):
        assert secret_of_the_deleted not in records, secret_of_the_deleted
    assert resolver.verify_snapshot(snapshot).status == "contact_deleted"
    assert resolver.resolve("Alex Chen", kind="email").status == "not_found"


def test_contacts_survive_a_process_restart() -> None:
    sol = _sol_key()
    store.create_contact(display_name="Zoë Ng", actor=OWNER, endpoints=[{"kind": "wallet", "value": sol, "network": SOLANA_DEVNET, "label": "savings"}])
    from storage.db import active_default_db_path

    script = (
        "import json, sys\n"
        "from storage.db import configure_default_db_path\n"
        "configure_default_db_path(sys.argv[1])\n"
        "from core.contacts import resolver\n"
        "r = resolver.resolve('Zoë Ng (savings)', kind='wallet')\n"
        "print(json.dumps({'status': r.status, 'value': (r.snapshot or {}).get('value'), 'network': (r.snapshot or {}).get('chain_network')}))\n"
    )
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    child = subprocess.run([sys.executable, "-B", "-c", script, active_default_db_path()], capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=180)
    assert child.returncode == 0, child.stderr[-2000:]
    assert json.loads(child.stdout.strip().splitlines()[-1]) == {"status": "resolved", "value": sol, "network": SOLANA_DEVNET}


# --- refusals ------------------------------------------------------------------------------------------------------

def test_key_material_is_refused_in_every_field_and_nothing_is_saved() -> None:
    phrase = "abandon ability able about above absent absorb abstract absurd abuse access accident"
    cases = [
        {"display_name": "Wallet backup", "notes": phrase},
        {"display_name": "Alex", "aliases": ["pin: 482913"]},
        {"display_name": "Alex", "endpoints": [{"kind": "email", "value": "alex@example.test", "label": "password: hunter22"}]},
        {"display_name": "0x" + "ab" * 32},
        {"display_name": "Vault", "endpoints": [{"kind": "messaging", "channel": "other", "account": "backup", "value": "[" + ",".join(["7"] * 64) + "]"}]},
    ]
    for case in cases:
        with pytest.raises(store.ContactsError) as refused:
            store.create_contact(actor=OWNER, **case)
        assert refused.value.reason == "secret_material_refused", case
    assert store.active_contacts() == []
    assert secret_material_reason("my sol wallet is 7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU") == ""


def test_a_messaging_identity_names_its_channel_and_account_and_claims_no_delivery() -> None:
    with pytest.raises(store.ContactsError) as missing:
        store.create_contact(display_name="Mara", actor=OWNER, endpoints=[{"kind": "messaging", "value": "U02ABCDEF", "channel": "slack"}])
    assert missing.value.reason == "messaging_account_required"
    store.create_contact(display_name="Mara", actor=OWNER, endpoints=[
        {"kind": "messaging", "value": "U02ABCDEF", "channel": "slack", "account": "Clayworks Studio"},
        {"kind": "messaging", "value": "@mara_pots", "channel": "telegram"}])
    slack = tools.run("contacts.resolve", {"name": "Mara", "kind": "messaging", "channel": "slack"}, None)
    assert slack["ok"] and slack["observation"]["destination"]["provider_account"] == "Clayworks Studio"
    assert slack["observation"]["delivery_available"] is False and "no Slack transport" in slack["response_text"]
    ask = tools.run("contacts.resolve", {"name": "Mara", "kind": "messaging"}, None)
    assert not ask["ok"] and ask["status"] == "ambiguous_endpoint" and len(ask["observation"]["choices"]) == 2


# --- the tool path -------------------------------------------------------------------------------------------------

def test_original_save_applies_what_the_user_typed_and_holds_the_rest_as_suggestions() -> None:
    sol = _sol_key()
    context = _turn("Save Alex Chen as a contact: work email alex.chen@example.test")
    saved = tools.run("contacts.save", {"name": "Alex Chen", "email": "alex.chen@example.test", "label": "work", "wallet_address": sol, "network": "solana-devnet"}, context)
    assert saved["ok"] and saved["status"] == "saved_with_suggestions"
    contact = store.list_contacts("Alex Chen")[0]
    assert [e["value"] for e in contact["endpoints"]] == ["alex.chen@example.test"] and contact["endpoints"][0]["verification"] == "user_entered"
    pending = store.list_suggestions()
    assert [(s["value"], s["contact_id"]) for s in pending] == [(sol, contact["contact_id"])]
    assert resolver.resolve("Alex Chen", kind="wallet").status == "no_endpoint"
    assert "not in your message" in saved["response_text"]


def test_novel_save_matches_formatted_numbers_and_handles_the_user_typed() -> None:
    context = _turn("add Zoë Ng to my contacts, mobile +370 612 34567 and telegram @zoe_ng_42")
    saved = tools.run("contacts.save", {"name": "Zoë Ng", "phone": "+37061234567", "messaging_identity": "zoe_ng_42", "channel": "telegram"}, context)
    assert saved["status"] == "saved", saved
    assert sorted(e["kind"] for e in store.list_contacts("Zoë")[0]["endpoints"]) == ["messaging", "phone"]
    assert store.list_suggestions() == []


def test_malicious_replacement_from_retrieved_content_waits_for_the_owner() -> None:
    good, evil = _sol_key(), _sol_key()
    contact = store.create_contact(display_name="Alex Chen", actor=OWNER, endpoints=[{"kind": "wallet", "value": good, "network": SOLANA_DEVNET}])
    endpoint_id = contact["endpoints"][0]["endpoint_id"]
    snapshot = resolver.resolve("Alex Chen", kind="wallet").snapshot
    context = _turn("Alex emailed me his new wallet, update it from his last email")
    staged = tools.run("contacts.update", {"contact_id": contact["contact_id"], "change_endpoints": [{"endpoint_id": endpoint_id, "value": evil}]}, context)
    assert staged["status"] == "saved_as_suggestion"
    assert resolver.resolve("Alex Chen", kind="wallet").snapshot["value"] == good
    assert resolver.verify_snapshot(snapshot).status == "current"
    pending = store.list_suggestions()
    assert (pending[0]["replaces_endpoint_id"], pending[0]["value"]) == (endpoint_id, evil)
    with pytest.raises(store.ContactsError):
        store.accept_suggestion(pending[0]["suggestion_id"], actor=OWNER)
    taken = authority.propose({"operation": "accept_suggestion", "suggestion_id": pending[0]["suggestion_id"]}, source=authority.SOURCE_OWNER_UI, requested_by="owner")
    assert taken["status"] == "pending_authentication" and resolver.verify_snapshot(snapshot).status == "current", "taking a replacement waits for the PIN"
    protected_changes.confirm(taken["operation"])
    assert resolver.verify_snapshot(snapshot).status == "endpoint_changed"

    typed = _sol_key()
    own_words = _turn(f"Alex's wallet changed, it is now {typed}")
    applied = tools.run("contacts.update", {"contact_id": contact["contact_id"], "change_endpoints": [{"endpoint_id": endpoint_id, "value": typed}]}, own_words)
    assert applied["status"] == "pending_confirmation" and "Nothing is changed yet" in applied["response_text"], applied
    assert resolver.resolve("Alex Chen", kind="wallet").snapshot["value"] == evil, "the user's own words still wait for the PIN"
    protected_changes.confirm(applied["details"]["operation"])
    assert resolver.resolve("Alex Chen", kind="wallet").snapshot["value"] == typed


def test_provenance_is_bound_to_the_chat_and_absent_without_a_checkpoint() -> None:
    context = _turn("Save Mara, email mara@example.test", session="openclaw:chat-a")
    assert tools.proven_user_text(context).startswith("Save Mara")
    other_chat = {**context, "runtime_session_id": "openclaw:chat-b", "session_id": "openclaw:chat-b"}
    assert tools.proven_user_text(other_chat) == ""
    assert tools.run("contacts.save", {"name": "Mara", "email": "mara@example.test"}, other_chat)["status"] == "saved_as_suggestion"
    assert tools.run("contacts.save", {"name": "Mara", "email": "mara@example.test"}, None)["status"] == "saved_as_suggestion"
    assert store.active_contacts() == []
    assert len(store.list_suggestions()) == 1


def test_delete_through_the_tool_names_one_target_and_is_a_prompted_action() -> None:
    first = store.create_contact(display_name="Alex", actor=OWNER, endpoints=[{"kind": "email", "value": "a1@example.test"}])
    with pytest.raises(store.ContactsError) as same_name:
        store.create_contact(display_name="Alex", actor=OWNER, endpoints=[{"kind": "email", "value": "a2@example.test"}])
    assert same_name.value.reason == "protected_review_required" and len(store.active_contacts()) == 1, "a second Alex waits for the PIN"
    created = protected_changes.confirm(authority.get_operation(same_name.value.details["operation_id"]))
    second = store.get_contact(created["result"]["created"][0]["contact_id"])
    ask = tools.run("contacts.delete", {"target": "Alex"}, None)
    assert ask["status"] == "contact_ambiguous" and len(store.active_contacts()) == 2
    done = tools.run("contacts.delete", {"target": second["contact_id"]}, None)
    assert done["status"] == "pending_confirmation" and len(store.active_contacts()) == 2, done
    protected_changes.confirm(done["details"]["operation"])
    assert [c["contact_id"] for c in store.active_contacts()] == [first["contact_id"]]

    from core.mode_permission_policy import MODE_PERMISSION_MATRIX, OperatingMode, PermissionAction, actions_for_tool

    assert actions_for_tool("contacts.delete", {"target": "Alex"}) == (PermissionAction.CHANGE_SETTINGS,)
    assert actions_for_tool("contacts.search", {"query": "Alex"}) == (PermissionAction.READ_FILES,)
    assert actions_for_tool("contacts.save", {"name": "Alex"}) == (PermissionAction.CREATE_FILES,)
    effect = MODE_PERMISSION_MATRIX[OperatingMode.AUTO][PermissionAction.CHANGE_SETTINGS]
    assert str(getattr(effect, "value", effect)) == "require_approval"
    save_effect = MODE_PERMISSION_MATRIX[OperatingMode.PLAN][PermissionAction.CREATE_FILES]
    assert str(getattr(save_effect, "value", save_effect)) == "deny"


def test_the_contacts_family_is_registered_and_offerable() -> None:
    from core.capability_graph import assert_representatives_resolve
    from core.runtime_tool_contracts import runtime_tool_contract_map

    contracts = runtime_tool_contract_map()
    for intent in tools.INTENTS:
        assert contracts[intent].supported and contracts[intent].tool_surface == "contacts", intent
    assert_representatives_resolve()
