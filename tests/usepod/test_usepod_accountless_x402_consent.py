"""Accountless x402 consent, the x402 pick gate and the accountless origin, in-process against the production money law.

A labelled SYNTHETIC wallet authority (``SyntheticWalletAuthority`` from the x402 money seam suite) publishes the payer and
fee facts. The approval store, the grant mint, the money law, the router's pick gate and the credential store (the
suite's isolated file vault) are production code. The served composed suite drives the same seams with the real wallet
authority; this file covers the refusal matrix quickly.
"""
from __future__ import annotations

import json
import time
import uuid

import pytest

from tests.usepod.strict_usepod_service import DOCUMENTED_MAINNET_NETWORK
from tests.usepod.test_usepod_legacy_paid_lane import _approve_route, _pick, _production_authority, _usepod_manifest
from tests.usepod.test_usepod_x402_money_seam import SyntheticWalletAuthority, _mint_x402_grant

MODEL = "meridian-synth-chat"
PAYER = "9SynthPayer" + "1" * 33
ROUTE = "key_relay+marketplace"


class _NoNetworkWallet(SyntheticWalletAuthority):
    """TEST DOUBLE: an installed wallet authority that can pay on no network (Crypto off, no ready wallet)."""

    label = "test_double:synthetic_x402_wallet_no_network"
    networks = ()


@pytest.fixture
def x402_home(tmp_path, monkeypatch):
    import os

    from storage.db import configure_default_db_path

    configure_default_db_path(os.path.join(tmp_path, "effect_budget.db"))
    from core import effect_budget, runtime_paths
    from core.usepod.transport import reset_payment_authority

    effect_budget.reset_effect_budget_process_state()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "vault")
    monkeypatch.delenv("VOOL_USEPOD_TOKEN", raising=False)
    runtime_paths.configure_runtime_home(home)
    teardown = _production_authority()
    try:
        yield home
    finally:
        reset_payment_authority()
        teardown()
        effect_budget.reset_effect_budget_process_state()
        configure_default_db_path(None)


def _install_wallet(wallet) -> None:
    from core.usepod.transport import install_payment_authority

    install_payment_authority(wallet, label=wallet.label)


def _x402_lane() -> None:
    from core.usepod.lane import LanePreference, save_lane_preference

    save_lane_preference(LanePreference(transport_mode="x402"))


# --- consent ------------------------------------------------------------------------------------------------------


def test_x402_consent_reads_no_token_and_names_the_wallet_facts_in_the_chosen_asset(x402_home) -> None:
    from core.mode_permission_policy import resolve_approval
    from core.usepod import discovery, money_law, spend_approval

    _install_wallet(SyntheticWalletAuthority(service=None, fee_asset="SOL"))
    _approve_route(x402_home)
    _x402_lane()
    assert discovery.credential_status()["configured"] is False

    usdc = spend_approval.propose_spend_grant(per_call_atomic=50_000, max_total_atomic=50_000, expiry_epoch=time.time() + 3600, asset="USDC")
    facts = usdc["facts"]
    assert (facts["account_kind"], facts["account"], facts["asset"], facts["unit"], facts["decimals"], facts["network"]) == (
        "x402_payer_wallet", PAYER, "USDC", "usdc_microunit", 6, DOCUMENTED_MAINNET_NETWORK), facts
    assert (facts["models"], facts["routes"], facts["x402"]["fee_asset"], facts["x402"]["fee_max_atomic"]) == ([MODEL], [ROUTE], "SOL", 200_000), facts
    text = spend_approval._approval_entry(usdc["approval_id"])["expected_side_effects"]
    assert "up to 50000 µUSDC" in text and "plus at most 200000 lamports network fee" in text, text

    sol = spend_approval.propose_spend_grant(per_call_atomic=350_000, max_total_atomic=350_000, expiry_epoch=time.time() + 3600, asset="sol")
    assert (sol["facts"]["asset"], sol["facts"]["unit"], sol["facts"]["decimals"]) == ("SOL", "lamport", 9), sol["facts"]
    assert "up to 350000 lamports" in spend_approval._approval_entry(sol["approval_id"])["expected_side_effects"]
    assert resolve_approval(sol["approval_id"], decision="allow")["status"] == "approved"
    minted = spend_approval.confirm_spend_grant(sol["approval_id"])
    from core.effect_budget_money import money_grants

    (grant,) = [row for row in money_grants(active_only=False) if row.grant_id == minted["grant_id"]]
    assert grant.spec["asset_key"] == f"{DOCUMENTED_MAINNET_NETWORK}|SOL|9", grant.spec
    # the SOL consent bounds a SOL payment in lamports; the unminted USDC proposal bounds nothing
    assets, bounds = money_law.x402_payment_bounds(model_id=MODEL, route=ROUTE, usdc_route_bound_atomic=10_000)
    assert bounds.get("SOL") == 350_000 and "SOL" in assets, (assets, bounds)


def test_the_prepaid_lane_refuses_a_sol_consent(x402_home) -> None:
    from core.usepod import spend_approval

    with pytest.raises(ValueError, match="prepaid lane spends the UsePod account's USDC balance"):
        spend_approval.propose_spend_grant(per_call_atomic=1_000, max_total_atomic=1_000, asset="SOL")


def test_x402_consent_refuses_an_asset_usepod_does_not_document(x402_home) -> None:
    from core.usepod import spend_approval

    _install_wallet(SyntheticWalletAuthority(service=None, fee_asset="SOL"))
    _x402_lane()
    with pytest.raises(ValueError, match="pays in USDC or SOL"):
        spend_approval.propose_spend_grant(per_call_atomic=1_000, max_total_atomic=1_000, asset="BTC")


def test_x402_consent_refuses_when_the_wallet_pays_on_no_network(x402_home) -> None:
    from core.usepod import spend_approval

    _install_wallet(_NoNetworkWallet(service=None, fee_asset="SOL"))
    _approve_route(x402_home)
    _x402_lane()
    with pytest.raises(ValueError, match="needs a wallet that can pay now"):
        spend_approval.propose_spend_grant(per_call_atomic=1_000, max_total_atomic=1_000, asset="USDC")


# --- the router's pick gate on the x402 lane ----------------------------------------------------------------------


def test_an_x402_pick_without_a_payment_authority_refuses_before_anything(x402_home) -> None:
    _approve_route(x402_home)
    _x402_lane()
    authorization, denial = _pick(_usepod_manifest())
    assert (authorization, denial.get("reason")) == (None, "wallet_payment_authority_unavailable")


def test_an_x402_pick_refuses_when_the_wallet_pays_on_no_network(x402_home) -> None:
    _install_wallet(_NoNetworkWallet(service=None, fee_asset="SOL"))
    _approve_route(x402_home)
    _x402_lane()
    authorization, denial = _pick(_usepod_manifest())
    assert (authorization, denial.get("reason")) == (None, "wallet_payment_network_unverified")


def test_a_prepaid_grant_never_authorizes_an_x402_pick(x402_home) -> None:
    from core.effect_budget import grant_operator_budget_authority
    from core.effect_budget_money import AssetIdentity, MoneyGrantSpec, grant_money_authority
    from core.usepod.money_law import USEPOD_ACCOUNT_NETWORK

    _install_wallet(SyntheticWalletAuthority(service=None, fee_asset="SOL"))
    grant_money_authority(
        grant_operator_budget_authority(note="synthetic test funds: accountless x402 consent"),
        MoneyGrantSpec(
            kind="single_payment", operation_kinds=("inference_prepaid",), provider_id="usepod",
            asset=AssetIdentity(network=USEPOD_ACCOUNT_NETWORK, asset="USDC", decimals=6),
            max_total_atomic=8_000_000, per_operation_max_atomic=4_000_000, models=(MODEL,), routes=(ROUTE,),
            expires_epoch=time.time() + 3600.0, credit_liquidity="not_required", provider_account="upc_" + "3" * 32,
            approval_ref="synthetic:test", note="synthetic test funds: accountless x402 consent",
        ),
    )
    _approve_route(x402_home)
    _x402_lane()
    authorization, denial = _pick(_usepod_manifest())
    assert (authorization, denial.get("reason")) == (None, "MONEY_AUTHORITY_INVALID")


def test_a_revoked_x402_consent_is_named_at_the_pick(x402_home) -> None:
    _install_wallet(SyntheticWalletAuthority(service=None, fee_asset="SOL"))
    _mint_x402_grant(per_operation=350_000, asset="SOL", decimals=9, fee_asset="SOL", model=MODEL, revoked=True)
    _approve_route(x402_home)
    _x402_lane()
    authorization, denial = _pick(_usepod_manifest())
    assert (authorization, denial.get("reason")) == (None, "MONEY_AUTHORITY_REVOKED")


def test_an_active_x402_consent_authorizes_the_pick_for_its_own_provider(x402_home) -> None:
    _install_wallet(SyntheticWalletAuthority(service=None, fee_asset="SOL"))
    _mint_x402_grant(per_operation=50_000, asset="USDC", decimals=6, fee_asset="SOL", model=MODEL)
    _approve_route(x402_home)
    _x402_lane()
    manifest = _usepod_manifest()
    authorization, denial = _pick(manifest)
    assert authorization is not None, denial
    assert (authorization.provider_id, authorization.model_call_id) == (manifest.provider_id, "")


# --- the accountless origin -----------------------------------------------------------------------------------------


def test_the_accountless_origin_is_chosen_explicitly_and_can_return_to_the_documented_one(x402_home) -> None:
    from core import credential_store
    from core.cloud_providers import USEPOD_ORIGIN_SLOT
    from core.usepod import descriptor, discovery

    assert discovery.save_accountless_origin("http://127.0.0.1:8123") == "http://127.0.0.1:8123"
    assert discovery.configured_origin() == "http://127.0.0.1:8123"
    assert discovery.save_accountless_origin("") == descriptor.DEFAULT_ORIGIN
    assert credential_store.get_credential(USEPOD_ORIGIN_SLOT) is None


def test_a_stored_token_keeps_its_origin(x402_home, monkeypatch) -> None:
    from core.usepod import discovery

    monkeypatch.setenv("VOOL_USEPOD_TOKEN", str(uuid.uuid4()))
    before = discovery.configured_origin()
    with pytest.raises(discovery.AccountlessOriginRefusedError) as caught:
        discovery.save_accountless_origin("http://127.0.0.1:8123")
    assert (caught.value.code, caught.value.http_status, discovery.configured_origin()) == ("origin_bound_to_stored_token", 409, before)


def test_an_origin_that_is_not_a_usepod_origin_is_refused(x402_home) -> None:
    from core.usepod import discovery

    with pytest.raises(discovery.AccountlessOriginRefusedError) as caught:
        discovery.save_accountless_origin("http://gateway.example.test")
    assert caught.value.http_status == 400 and caught.value.code.startswith("origin_invalid:"), caught.value.code


def test_an_unresolved_credential_operation_blocks_the_origin_change(x402_home) -> None:
    from core.cloud_providers import USEPOD_ORIGIN_SLOT
    from core.credential_intelligence.store import JOURNAL_FILE
    from core.runtime_paths import active_data_dir
    from core.usepod import discovery

    journal = active_data_dir() / JOURNAL_FILE
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(json.dumps([{"phase": "write_pending", "slot": USEPOD_ORIGIN_SLOT}]), encoding="utf-8")
    with pytest.raises(discovery.AccountlessOriginRefusedError) as caught:
        discovery.save_accountless_origin("http://127.0.0.1:8123")
    assert caught.value.code == "credential_pair_unresolved"
