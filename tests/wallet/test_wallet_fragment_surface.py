"""Static pins for the wallet fragment's honest-state surface (the served-browser proof
rides the L3/L4 gates; these keep the source truthful between them)."""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.safety]


@pytest.fixture(scope="module")
def fragment() -> str:
    from core.wallet_fragment import render_wallet_fragment

    return render_wallet_fragment()


def test_disabled_state_explains_the_opt_in_and_names_no_secret_door(fragment):
    assert "VOOL_WALLET_ENABLED" not in fragment, "the switch lives in Settings now, not in an environment variable"
    assert "Settings" in fragment
    assert "No wallet or key exists yet, and none is ever created automatically" in fragment
    assert "Crypto is optional" in fragment, "keyless use must be stated as the default"


def test_the_recommended_watch_only_mode_has_a_creation_surface(fragment):
    assert "vwWatchBtn" in fragment and "/api/wallet/watch-only" in fragment
    assert "nothing can ever be signed here" in fragment, "the no-signer boundary is stated"


def test_states_and_networks_are_distinguished_not_blurred(fragment):
    # custody modes are distinct understandable states, not one string
    for mode in ("watch_only", "pocket_sealed", "external_signer"):
        assert mode in fragment
    # testnet is badged; mainnet would be too; chains are named, not just CAIP-2 ids
    assert "testnet only" in fragment and "Base Sepolia" in fragment and "Solana Devnet" in fragment


def test_payment_cards_show_the_network_and_testnet_truth_pre_approval(fragment):
    assert "Payment approval needed" in fragment
    assert "testnet funds, not real money" in fragment
    assert "chainLabel(p.network)" in fragment


def test_watch_only_cards_do_not_offer_a_pin_dead_end(fragment):
    assert "watch-only: this proposal can be approved in an external wallet, not signed here" in fragment


def test_addresses_are_masked_by_default_with_an_explicit_reveal(fragment):
    assert "function maskedLine" in fragment and "short(value)" in fragment
    assert fragment.count("vw-toggle") >= 2  # the class + its construction; every masked line uses it


def test_receipts_are_visible_in_settings(fragment):
    assert "/api/wallet/receipts" in fragment and "no payment receipts yet" in fragment


def test_the_fragment_keeps_its_laws(fragment):
    import re

    assert "innerHTML" not in fragment, "server/model text is never assigned as HTML"
    assert len(re.findall(r"<script>", fragment)) == 1 and len(re.findall(r"<style>", fragment)) == 1
    assert "window.VoolWallet" in fragment and "localStorage" not in fragment
    # the reveal overlay is a labelled modal with an escape route
    assert 'aria-modal' in fragment and "Escape" in fragment


def test_the_off_state_carries_an_explicit_enable_entry(fragment):
    # OFF is served inside the wallet's own surface now: a short description plus an Enable button
    # through the preferences authority — not a pointer to a switch that only exists elsewhere.
    assert "Enable VOOL Wallet" in fragment and "vwEnableBtn" in fragment
    assert "/api/settings/prefs" in fragment and "wallet_enabled" in fragment
    # existence and permission are separate facts: the off state distinguishes "saved, kept" from "none"
    assert "nothing was deleted" in fragment and "Enabling restores your setup state" in fragment


def test_creation_entries_wait_for_the_enabled_state(fragment):
    # the whole setup row starts hidden; renderStatus shows it only when the wallet is enabled
    assert "row.id = 'vwActions'; row.hidden = true" in fragment
    assert "actions.hidden = !st.enabled" in fragment


def test_user_facing_branding_is_vool_wallet(fragment):
    assert "Create VOOL Wallet…" in fragment and "VOOL Wallet created." in fragment
    assert "Create pocket wallet" not in fragment and "Pocket wallet created" not in fragment
    # stored identifiers stay: the custody mode value is not renamed for branding
    assert "pocket_sealed" in fragment


def test_onboarding_leads_with_two_safety_points_and_collapses_the_rest(fragment):
    # two short points up front, the full guidance behind Learn more, the authority facts unwrapped
    assert "vwSafetyPoints" in fragment
    assert "Protect your recovery material" in fragment
    assert "Check the destination and network before sending" in fragment
    assert "Learn more" in fragment and "vwLearnMore" in fragment and "el('details'" in fragment
    # the typed confirmation phrase is an authority fact: it stays at the decision, never collapsed
    assert "Type this exact phrase to confirm" in fragment and "I ACCEPT THAT THIS DEVICE HOLDS THE KEY" not in fragment


def test_phantom_support_is_stated_by_where_it_actually_runs(fragment):
    # provider detection follows the documented injection shape; absence gets the honest route
    assert "window.phantom" in fragment and "isPhantom" in fragment
    assert "cannot load extensions" in fragment, "the native window states its real limit"
    assert "Chrome or Brave" in fragment, "the actionable supported-browser route is named"
    # account changes and disconnects are followed through the same owner door, never faked
    assert "accountChanged" in fragment and "disconnectEvent" in fragment and "bindPhantomEvents" in fragment
    # no desktop-wallet requirement is invented and no key is imported to imitate Phantom
    assert "wallet.insert" not in fragment and "importKey" not in fragment
