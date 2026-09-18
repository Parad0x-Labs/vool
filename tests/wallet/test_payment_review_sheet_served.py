"""Served proof of the compact payment review: on a normal-sized window the sheet shows the purpose, the amount, the
recipient, the network badge, the network-cost ceiling, the per-asset maximum debit and remaining balance, the PIN
and one primary action, with everything else under ONE collapsed ``View details`` disclosure. Opening and closing the
disclosure mutates nothing (the same quote stays open); ``Decide later`` and Escape dismiss without approving or
cancelling and leave no credential behind; the request stays pending; the full addresses are under the details.

SCRIPTED MODEL, SIMULATED CHAIN (the loopback Solana-dialect node answering as Devnet), real product: the daemon is
``apps.vool_api_server`` in its own process and home; a real Chromium drives the served chat page.

PREPARED, NOT RUN (execution pause, 2026-09-16): every assertion here is a stated expectation until the suite runs.
"""
from __future__ import annotations

import json
import uuid
from urllib.request import urlopen

import pytest

from tests import served_browser
from tests.wallet._rig import DEVNET_GENESIS, ScriptedRpc
from tests.wallet._rig_provider import PromptRoutedProvider, seed_daemon
from tests.wallet.test_crypto_pilot_transfer_served import _chat_proposal, _ready_pilot_wallet

pytestmark = [pytest.mark.safety, pytest.mark.served]


def _get(daemon, path: str) -> dict:
    with urlopen(daemon.base_url + path, timeout=30) as response:
        return json.load(response)


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    from tests._blackbox_served_rig import ServedDaemon

    home = tmp_path_factory.mktemp("review") / "home"
    chain = ScriptedRpc(genesis_hash=DEVNET_GENESIS)
    chain.real_signature = True
    provider = PromptRoutedProvider()
    chain.__enter__()
    provider.__enter__()
    daemon = ServedDaemon(home, env_extra={
        "VOOL_ALWAYS_ON_CATALOG": "1",
        "VOOL_WALLET_ENABLED": "1",
        "VOOL_WALLET_NETWORK_ENVIRONMENT": "testnet",
        "VOOL_WALLET_TESTNET_RPC_URL": chain.url,
        "VOOL_WALLET_X402_ALLOW_LOOPBACK": "1",
        "OLLAMA_HOST": provider.base_url,
        "VOOL_OLLAMA_URL": provider.base_url,
        "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat",
        "VOOL_OLLAMA_PS_URL": f"{provider.base_url}/api/ps",
        "VOOL_OLLAMA_TAGS_URL": f"{provider.base_url}/api/tags",
    })
    try:
        try:
            daemon.start(timeout=240)
        except Exception as exc:  # pragma: no cover - environment
            pytest.skip(f"served daemon could not boot here: {exc}")
        seed_daemon(home, provider.base_url)
        provider.reset()
        yield daemon, chain, provider
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)
        chain.__exit__(None, None, None)


def _sol_key() -> str:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from core.vool_wallet import b58encode

    return b58encode(Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))


@pytest.mark.timeout(600)
def test_the_sheet_is_a_compact_review_with_one_collapsed_details_disclosure_that_mutates_nothing(served):
    daemon, chain, provider = served
    wallet = _ready_pilot_wallet(daemon, "review payer")  # the scripted chain answers every balance read with test funds
    recipient = _sol_key()
    session = f"review-{uuid.uuid4().hex[:8]}"
    proposal_id = _chat_proposal(daemon, f"Please pay 1500000 lamports to {recipient} for the quarterly report", session=session)
    manager, browser = served_browser.launch_chromium()
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(daemon.base_url) else route.abort())
        page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
        page.wait_for_selector("#input", timeout=20_000)
        review = page.locator(f'.vw-card[data-proposal="{proposal_id}"] .vw-review')
        review.wait_for(timeout=30_000)
        with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/quote") as minted:
            review.click()
        quote = minted.value.json()["quote"]
        fields = quote["fields"]
        page.wait_for_function("!document.getElementById('vwSheetApprove').disabled", timeout=10_000)
        sheet = page.locator('.vw-sheet[role="dialog"]')

        # --- above the fold: the purpose-led heading, the amount, the recipient, the badge, the network cost, the maxima
        assert page.locator("#vwSheetTitle").text_content() == f"Send {fields['amount_human']} {fields['display_symbol']}"
        summary = page.locator("#vwSheetReview")
        assert summary.is_visible()
        assert summary.locator('[data-review="amount"]').text_content() == f"Send {fields['amount_human']} {fields['display_symbol']} (exact amount)"
        assert summary.locator('[data-review="recipient"]').text_content().startswith("To wallet " + recipient[:8]), "an unidentified recipient stays an unidentified wallet with a shortened address"
        assert summary.locator('[data-field="network"] .vw-badge').text_content() == "DEVNET"
        assert summary.locator('[data-review="network_cost"]').text_content() == fields["review"]["network_line"] and "at most" in fields["review"]["network_line"]
        assert summary.locator('[data-review="max_debit_0"]').text_content() == f"Maximum wallet debit now: {fields['review']['max_debits'][0]['human']} SOL (principal + network cost, maximum)"
        assert summary.locator('[data-review="remaining_0"]').text_content().startswith("Estimated remaining after: ")
        assert page.locator("#vwSheetApprove").text_content() == "Approve and send" and page.locator("#vwSheetLater").text_content() == "Decide later"
        assert page.locator("#vwSheetCredential").is_visible() and page.locator("#vwSheetCancel").is_visible()
        # not a full-screen table: the sheet fits well inside a 900px-tall window with the details closed
        assert page.evaluate("document.querySelector('.vw-sheet').getBoundingClientRect().height") < 700

        # --- the details: collapsed by default, keyboard-reachable, and the full addresses live there
        toggle = page.locator("#vwSheetDetailsToggle")
        details = page.locator("#vwSheetDetails")
        assert toggle.text_content() == "View details" and toggle.get_attribute("aria-expanded") == "false" and not details.is_visible()
        assert sheet.locator('[data-field="to"] .vw-sheet-value').text_content() == recipient, "the full recipient address is in the details even while they are closed"
        assert sheet.locator('[data-field="from"] .vw-sheet-value').text_content().endswith(wallet["address"])
        before = _get(daemon, "/api/wallet/status")["status"]
        row_before = [row for row in before["pending"] if row["proposal_id"] == proposal_id][0]
        toggle.focus()
        page.keyboard.press("Enter")
        assert toggle.get_attribute("aria-expanded") == "true" and details.is_visible() and toggle.text_content() == "Hide details"
        assert sheet.locator('[data-field="fee"] .vw-sheet-value').text_content() == f"estimated {fields['fee_estimate_human']} {fields['gas_asset']} · at most {fields['fee_max_human']} {fields['gas_asset']}"
        toggle.click()
        assert toggle.get_attribute("aria-expanded") == "false" and not details.is_visible()
        after = _get(daemon, "/api/wallet/status")["status"]
        row_after = [row for row in after["pending"] if row["proposal_id"] == proposal_id][0]
        assert row_after["open_quote_id"] == row_before["open_quote_id"] == quote["quote_id"], "opening and closing the details never re-mints or supersedes the quote"
        assert not page.locator("#vwSheetApprove").is_disabled()

        # --- Decide later: no approval, no cancellation, the request stays pending, nothing typed survives
        page.locator("#vwSheetCredential").fill("1")
        page.click("#vwSheetLater")
        page.wait_for_selector("#vwSheet", state="detached", timeout=10_000)
        assert page.locator(f'.vw-card[data-proposal="{proposal_id}"] .vw-result').text_content() == "Review this request again whenever you are ready."
        assert proposal_id in {row["proposal_id"] for row in _get(daemon, "/api/wallet/status")["status"]["pending"]} and chain.send_count() == 0
        assert page.locator("#vwSheetCredential").count() == 0, "the credential field lives only inside the dialog"

        # --- reopened, then Escape: the same dismissal semantics, still pending, still nothing sent
        with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/quote"):
            review.click()
        page.wait_for_function("!document.getElementById('vwSheetApprove').disabled", timeout=10_000)
        page.keyboard.press("Escape")
        page.wait_for_selector("#vwSheet", state="detached", timeout=10_000)
        assert proposal_id in {row["proposal_id"] for row in _get(daemon, "/api/wallet/status")["status"]["pending"]} and chain.send_count() == 0
    finally:
        browser.close()
        manager.stop()
