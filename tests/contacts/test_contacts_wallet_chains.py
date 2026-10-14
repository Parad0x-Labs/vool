"""A contact's wallets carry their chain: a Solana address takes Solana by itself, a 0x address waits for the owner to say
which chain the person uses, and one contact holds several wallets added with "+ Add another wallet".

What executes: the production /api/contacts doors (core.web.api.service -> command registry -> core.web.api.contacts_api
-> core.contacts), the production chat tool path (core.contacts.tools with a real runtime checkpoint as provenance), the
production resolver a payment proposal uses, the wallet's own environment and chain rows, and the real chat page in
headless Chromium with /api/contacts* answered by those doors in-process. No model, network or provider is involved.
"""
from __future__ import annotations

import json
import os
import urllib.parse

import pytest

from core.contacts import resolver, store, tools
from core.vool_chat_page import render_vool_chat_html
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get, dispatch_post
from tests.contacts import protected_changes

OWNER = store.ACTOR_OWNER
SOLANA_MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
SOLANA_DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
BASE = "eip155:8453"
ETHEREUM = "eip155:1"
# EIP-55 reference vectors: valid checksummed addresses
EVM_A = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
EVM_B = "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359"
# a Bitcoin address: base58 and Solana-length, but it does not decode to 32 bytes
NOT_SOLANA = "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"


@pytest.fixture(autouse=True)
def _empty_contacts(monkeypatch):
    monkeypatch.delenv("VOOL_WALLET_NETWORK_ENVIRONMENT", raising=False)
    protected_changes.reset()
    yield
    protected_changes.reset()


def _rt() -> RuntimeServices:
    return RuntimeServices(display_name="VOOL")


def _get(path: str, query: dict | None = None):
    response = dispatch_get(path=path, query=query or {}, runtime=_rt(), model_name="vool", client_host="127.0.0.1")
    return response.status, json.loads(response.body.decode("utf-8") or "{}")


def _post(path: str, body: dict):
    response = dispatch_post(path=path, body=body, headers={"content-type": "application/json"},
                             runtime=_rt(), model_name="vool", workspace_root_provider=lambda: "/tmp", client_host="127.0.0.1")
    return response.status, json.loads(response.body.decode("utf-8") or "{}")


def _sol_key() -> str:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from core.vool_wallet import b58encode

    return b58encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


def _turn(text: str, session: str = "openclaw:contact-wallets") -> dict:
    from core.agent_runtime.request_authority import REQUEST_PROVENANCE_KEY, request_provenance_for_visible_user_text
    from core.runtime_continuity import create_runtime_checkpoint

    checkpoint = create_runtime_checkpoint(session_id=session, request_text=text,
                                           source_context={REQUEST_PROVENANCE_KEY: request_provenance_for_visible_user_text(text, session_id=session)})
    return {"runtime_checkpoint_id": checkpoint["checkpoint_id"], "runtime_session_id": session, "session_id": session}


def _wallets(contact_id: str) -> list[tuple[str, str]]:
    contact = store.get_contact(contact_id)
    assert contact is not None
    return [(e["value"], e["chain_network"]) for e in contact["endpoints"] if e["kind"] == "wallet"]


# --- the doors -----------------------------------------------------------------------------------------------------

def test_a_solana_address_with_no_chain_is_saved_on_solana() -> None:
    sol = _sol_key()
    status, body = _post("/api/contacts/create", {"display_name": "Rita Vale", "endpoints": [{"kind": "wallet", "value": sol}]})
    assert status == 200 and body["status"] == "committed", body
    assert _wallets(body["contact"]["contact_id"]) == [(sol, SOLANA_MAINNET)]


def test_on_test_networks_a_solana_address_takes_solana_devnet(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "testnet")
    sol = _sol_key()
    status, body = _post("/api/contacts/create", {"display_name": "Rita Vale", "endpoints": [{"kind": "wallet", "value": sol}]})
    assert status == 200, body
    assert _wallets(body["contact"]["contact_id"]) == [(sol, SOLANA_DEVNET)]


def test_the_owners_stored_test_network_choice_is_read_while_the_contact_is_saved() -> None:
    # the choice made in Settings -> Crypto, not the process override: read inside the Contacts save itself
    from core.wallet import environment

    environment.set_active_environment("testnet")
    try:
        sol = _sol_key()
        status, body = _post("/api/contacts/create", {"display_name": "Rita Vale", "endpoints": [{"kind": "wallet", "value": sol}]})
        assert status == 200, body
        assert _wallets(body["contact"]["contact_id"]) == [(sol, SOLANA_DEVNET)]
        assert _get("/api/contacts/options")[1]["active_environment"] == "testnet"
    finally:
        environment.set_active_environment("mainnet")


def test_a_0x_address_with_no_chain_is_refused_and_names_the_chains_to_choose() -> None:
    status, body = _post("/api/contacts/create", {"display_name": "Omar Diaz", "endpoints": [{"kind": "wallet", "value": EVM_A}]})
    assert status >= 400 and body.get("ok") is False, body
    text = json.dumps(body)
    assert "wallet_network_required" in text
    message = body.get("message") or body.get("error") or ""
    assert "several chains" in message and "Ethereum" in message and "Base" in message, message
    assert "Solana" not in message and "Sepolia" not in message, "only the active environment's EVM chains are offered"
    assert store.active_contacts() == [], "nothing is saved without a chain"


def test_an_address_of_neither_shape_is_refused_without_guessing_a_chain() -> None:
    for value in (NOT_SOLANA, "0x1234", "hello"):
        status, body = _post("/api/contacts/create", {"display_name": "Omar Diaz", "endpoints": [{"kind": "wallet", "value": value}]})
        assert status >= 400 and "wallet_address_invalid" in json.dumps(body), (value, body)
    assert store.active_contacts() == []


def test_a_chosen_chain_is_kept_and_a_solana_address_on_an_evm_chain_is_refused() -> None:
    status, body = _post("/api/contacts/create", {"display_name": "Omar Diaz", "endpoints": [
        {"kind": "wallet", "value": EVM_A, "network": BASE}, {"kind": "wallet", "value": EVM_A, "network": ETHEREUM}]})
    assert status == 200, body
    assert sorted(_wallets(body["contact"]["contact_id"])) == sorted([(EVM_A, BASE), (EVM_A, ETHEREUM)])
    status, body = _post("/api/contacts/create", {"display_name": "Lee Park", "endpoints": [{"kind": "wallet", "value": _sol_key(), "network": BASE}]})
    assert status >= 400 and "wallet_address_invalid" in json.dumps(body)


def test_a_blank_chain_on_an_edit_keeps_the_saved_chain(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "testnet")
    sol = _sol_key()
    contact = store.create_contact(display_name="Rita Vale", actor=OWNER, endpoints=[{"kind": "wallet", "value": sol}])
    assert _wallets(contact["contact_id"]) == [(sol, SOLANA_DEVNET)]
    monkeypatch.delenv("VOOL_WALLET_NETWORK_ENVIRONMENT")
    endpoint_id = store.get_contact(contact["contact_id"])["endpoints"][0]["endpoint_id"]
    status, body = _post("/api/contacts/update", {"contact_id": contact["contact_id"], "expected_revision": contact["revision"],
                                                  "change_endpoints": [{"endpoint_id": endpoint_id, "label": "main", "network": ""}]})
    assert status == 200, body
    changes = json.dumps(body["operation"])
    assert SOLANA_MAINNET not in changes, "a label edit never moves the wallet to another chain"
    assert "endpoint_relabelled" in changes or "Change label" in changes, changes

    chat = tools.run("contacts.update", {"contact_id": contact["contact_id"], "change_endpoints": [{"endpoint_id": endpoint_id, "label": "main", "network": None}]},
                     _turn("label Rita Vale's wallet main"))
    assert SOLANA_MAINNET not in json.dumps(chat), chat


def test_an_unreadable_wallet_environment_detects_nothing(monkeypatch) -> None:
    from core.wallet import environment

    def broken():
        raise RuntimeError("wallet store unreadable")

    monkeypatch.setattr(environment, "peek_environment", broken)
    status, body = _post("/api/contacts/create", {"display_name": "Rita Vale", "endpoints": [{"kind": "wallet", "value": _sol_key()}]})
    assert status >= 400 and "wallet_network_required" in json.dumps(body), body
    assert store.active_contacts() == [], "no chain is guessed when the environment cannot be read"
    assert _get("/api/contacts/options")[1]["active_environment"] == ""


def test_options_name_the_active_environment(monkeypatch) -> None:
    assert _get("/api/contacts/options")[1]["active_environment"] == "mainnet"
    monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "testnet")
    assert _get("/api/contacts/options")[1]["active_environment"] == "testnet"


# --- several wallets, and paying a contact -------------------------------------------------------------------------

def test_several_wallets_on_one_chain_ask_which_and_each_chain_answers_only_for_itself() -> None:
    sol = _sol_key()
    contact = store.create_contact(display_name="Nia Brook", actor=OWNER, endpoints=[
        {"kind": "wallet", "value": EVM_A, "network": BASE, "label": "main"},
        {"kind": "wallet", "value": EVM_B, "network": BASE, "label": "savings"},
        {"kind": "wallet", "value": EVM_A, "network": ETHEREUM},
        {"kind": "wallet", "value": sol},
    ])
    on_base = resolver.resolve("Nia Brook", kind="wallet", network=BASE)
    assert on_base.status == "ambiguous_endpoint" and on_base.snapshot is None, "two Base wallets: nothing is guessed"
    assert sorted((c["value"], c["chain_network"]) for c in on_base.choices) == sorted([(EVM_A, BASE), (EVM_B, BASE)])
    picked = resolver.resolve("Nia Brook (savings)", kind="wallet", network=BASE)
    assert picked.status == "resolved" and picked.snapshot["value"] == EVM_B
    eth = resolver.resolve("Nia Brook", kind="wallet", network=ETHEREUM)
    assert eth.status == "resolved" and eth.snapshot["value"] == EVM_A and eth.snapshot["chain_network"] == ETHEREUM
    solana = resolver.resolve("Nia Brook", kind="wallet", network=SOLANA_MAINNET)
    assert solana.status == "resolved" and solana.snapshot["value"] == sol
    assert resolver.resolve("Nia Brook", kind="wallet", network="eip155:42161").status == "no_endpoint", "no Arbitrum wallet saved"
    assert len(_wallets(contact["contact_id"])) == 4


# --- the chat tool path --------------------------------------------------------------------------------------------

def test_chat_save_puts_a_typed_solana_address_on_solana_and_asks_the_chain_for_a_0x_address() -> None:
    sol = _sol_key()
    saved = tools.run("contacts.save", {"name": "Ivo Lind", "wallet_address": sol}, _turn(f"save Ivo Lind, his wallet is {sol}"))
    assert saved["ok"] and saved["status"] == "saved", saved
    assert _wallets(store.list_contacts("Ivo Lind")[0]["contact_id"]) == [(sol, SOLANA_MAINNET)]

    refused = tools.run("contacts.save", {"name": "Ada Moss", "wallet_address": EVM_A}, _turn(f"save Ada Moss, wallet {EVM_A}"))
    assert not refused["ok"] and refused["status"] == "wallet_network_required", refused
    assert "Choose the one this person uses" in refused["response_text"] and "Nothing was saved." in refused["response_text"]
    assert store.list_contacts("Ada Moss") == []

    chosen = tools.run("contacts.save", {"name": "Ada Moss", "wallet_address": EVM_A, "network": "base"}, _turn(f"save Ada Moss, wallet {EVM_A} on Base"))
    assert chosen["ok"], chosen
    assert _wallets(store.list_contacts("Ada Moss")[0]["contact_id"]) == [(EVM_A, BASE)]


def test_a_bare_chain_name_names_that_chain_in_the_active_environment(monkeypatch) -> None:
    from core.contacts.endpoints import EndpointError, normalize_wallet

    assert normalize_wallet(EVM_A, network="Ethereum").chain_network == ETHEREUM
    assert normalize_wallet(EVM_A, network="Arbitrum One").chain_network == "eip155:42161"
    monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "testnet")
    assert normalize_wallet(EVM_A, network="ethereum").chain_network == "eip155:11155111"
    assert normalize_wallet(EVM_A, network="Base Sepolia").chain_network == "eip155:84532"
    with pytest.raises(EndpointError) as unknown:
        normalize_wallet(EVM_A, network="polygon")
    assert unknown.value.reason == "wallet_network_unknown"


# --- the page ------------------------------------------------------------------------------------------------------

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


def _open_page(browser, errors: list[str]):
    html = render_vool_chat_html(build_commit="contact-wallets-browser")
    context = browser.new_context(viewport={"width": 1280, "height": 860})
    context.add_init_script("window.open = () => null;")

    def route(r):
        url = urllib.parse.urlsplit(r.request.url)
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
    page.goto("http://vool-contact-wallets.test/", wait_until="load")
    page.locator("#homeToggle").click()
    page.locator("#contactsBtn").click()
    return context, page


def _chain_options(page, row: int) -> list[str]:
    return page.locator(f"#vcNetwork{row} option").evaluate_all("(os) => os.map((o) => o.textContent)")


def test_the_editor_sets_solana_itself_asks_the_chain_for_0x_and_adds_another_wallet(browser) -> None:
    from playwright.sync_api import expect

    sol = _sol_key()
    errors: list[str] = []
    context, page = _open_page(browser, errors)
    try:
        page.locator("#vcAdd").click()
        page.locator("#vcName").fill("Kai Lund")
        add_wallet = page.locator("#vcAddWallet")
        expect(add_wallet).to_have_text("+ Add a wallet")
        add_wallet.click()
        expect(add_wallet).to_have_text("+ Add another wallet")
        rows = page.locator("#vcForm .vc-ep-row")
        expect(rows).to_have_count(2)
        wallet_row = rows.nth(1)
        kind_id = wallet_row.locator("select").first.get_attribute("id")
        n = int(kind_id.removeprefix("vcKind"))
        expect(page.locator(f"#vcKind{n}")).to_have_value("wallet")
        expect(page.locator(f"#vcValue{n}")).to_be_focused()

        page.locator(f"#vcValue{n}").fill(sol)
        expect(page.locator(f"#vcNetwork{n}")).to_have_value(SOLANA_MAINNET)
        expect(page.locator(f"#vcChainNote{n}")).to_have_text("A Solana address. The chain is set for you.")
        assert set(_chain_options(page, n)) == {"Solana", "Solana Devnet (test network)"}, "a Solana address offers only Solana"

        add_wallet.click()
        m = n + 1
        page.locator(f"#vcValue{m}").fill(EVM_A)
        expect(page.locator(f"#vcNetwork{m}")).to_have_value("")
        expect(page.locator(f"#vcChainNote{m}")).to_have_text("A 0x address works on several chains. Choose the one this person uses.")
        offered = _chain_options(page, m)
        assert offered[0] == "Choose the chain" and not any("Solana" in o for o in offered), offered
        assert offered[1:6] == ["Robinhood Chain", "Base", "Ethereum", "BNB Smart Chain", "Arbitrum One"], "this environment's chains come first"

        page.locator(f"#vcValue{m}").fill(NOT_SOLANA)
        expect(page.locator(f"#vcChainNote{m}")).to_have_text("")
        page.locator(f"#vcValue{m}").fill(EVM_A)
        page.locator("#vcSave").click()
        expect(page.locator("#vcForm .vc-status.vc-bad")).to_contain_text("Choose the one this person uses")
        assert store.active_contacts() == [], "a 0x wallet with no chain saves nothing"

        page.locator(f"#vcNetwork{m}").select_option(BASE)
        page.locator("#vcSave").click()
        detail = page.locator("#vcDetail")
        expect(detail.locator(".vc-title")).to_have_text("Kai Lund")
        expect(detail.locator(".vc-endpoint", has_text=sol)).to_contain_text("Solana")
        expect(detail.locator(".vc-endpoint", has_text=EVM_A)).to_contain_text("Base")
        saved = store.active_contacts()[0]
        assert sorted(_wallets(saved["contact_id"])) == sorted([(sol, SOLANA_MAINNET), (EVM_A, BASE)])
        assert not errors, errors
    finally:
        context.close()


def test_a_saved_single_wallet_opens_with_its_chain_and_another_wallet_waits_for_review(browser) -> None:
    from playwright.sync_api import expect

    contact = store.create_contact(display_name="Mo Reyes", actor=OWNER, endpoints=[{"kind": "wallet", "value": EVM_A, "network": ETHEREUM}])
    errors: list[str] = []
    context, page = _open_page(browser, errors)
    try:
        page.locator("#vcList .vc-row", has_text="Mo Reyes").click()
        page.locator("#vcDetail").get_by_role("button", name="Edit").click()
        first = page.locator("#vcForm .vc-ep-row").first.locator("select").nth(1)
        expect(first).to_have_value(ETHEREUM)
        expect(page.locator("#vcAddWallet")).to_have_text("+ Add another wallet")
        page.locator("#vcAddWallet").click()
        new_value = page.locator("#vcForm .vc-ep-row").nth(1).locator("input").first
        new_value.fill(EVM_A)
        page.locator("#vcForm .vc-ep-row").nth(1).locator("select").nth(1).select_option(BASE)
        page.locator("#vcSave").click()
        review = page.locator("#vcReview")
        expect(review).to_contain_text("It changes a saved contact.")
        expect(review.locator(".vc-diff", has_text="Add wallet address")).to_contain_text("Base")
        assert _wallets(contact["contact_id"]) == [(EVM_A, ETHEREUM)], "the saved contact is unchanged until the PIN"
        assert not errors, errors
    finally:
        context.close()


# --- "send Tomas 10 ETH": only wallets on a chain that can take the coin are choices ---------------------------------

BNB = "eip155:56"


def test_a_payment_asks_only_among_wallets_on_chains_that_carry_the_coin() -> None:
    from core.contacts.consumers import resolve_wallet_recipient

    sol = _sol_key()
    store.create_contact(display_name="Tomas Berg", actor=OWNER, endpoints=[
        {"kind": "wallet", "value": EVM_A, "network": BASE}, {"kind": "wallet", "value": EVM_A, "network": ETHEREUM},
        {"kind": "wallet", "value": EVM_B, "network": BNB}, {"kind": "wallet", "value": sol},
    ])
    eth = resolve_wallet_recipient("Tomas Berg", asset="ETH", environment="mainnet")
    assert not eth.ok and eth.status == "recipient_ambiguous", eth
    assert sorted(c["chain_network"] for c in eth.resolution["choices"]) == sorted([BASE, ETHEREUM]), "BNB Smart Chain cannot take ETH"
    assert "2 saved wallets that can take ETH" in eth.message and "Which one?" in eth.message

    bnb = resolve_wallet_recipient("Tomas Berg", asset="BNB", environment="mainnet")
    assert bnb.ok and bnb.snapshots[0]["value"] == EVM_B and bnb.snapshots[0]["chain_network"] == BNB
    solana = resolve_wallet_recipient("Tomas Berg", asset="SOL", environment="mainnet")
    assert solana.ok and solana.snapshots[0]["value"] == sol
    chosen = resolve_wallet_recipient("Tomas Berg", asset="ETH", network=ETHEREUM, environment="mainnet")
    assert chosen.ok and chosen.snapshots[0]["chain_network"] == ETHEREUM


def test_a_payment_in_a_coin_no_saved_wallet_can_take_names_where_the_wallet_is() -> None:
    from core.contacts.consumers import resolve_wallet_recipient

    store.create_contact(display_name="Tomas Berg", actor=OWNER, endpoints=[{"kind": "wallet", "value": EVM_B, "network": BNB}])
    eth = resolve_wallet_recipient("Tomas Berg", asset="ETH", environment="mainnet")
    assert not eth.ok and eth.status == "recipient_has_no_address", eth
    assert "BNB Smart Chain" in eth.message and "cannot send ETH" in eth.message
