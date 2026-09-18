"""Crypto Pilot, stage 1: ten network rows, the wallet-owned network environment, per-network capability.

Every assertion reads an environment fact (registry rows, the persisted preference, typed refusals,
the scripted endpoint's call log, the status payload), never prose. Chain facts were verified against
official sources on 2026-09-14 (delivery/NETWORK-MATRIX.json).
"""
from __future__ import annotations

import json
import urllib.request
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.wallet.errors import WalletFault
from tests.wallet._rig import MAINNET_GENESIS, ScriptedRpc

SOLANA_MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
SOLANA_DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"

#: caip2 -> (chain key, environment, native symbol, decimals, chain id or full genesis, explorer label,
#: transaction link prefix, first approved RPC origin)
PILOT_ROWS = {
    SOLANA_MAINNET: ("solana", "mainnet", "SOL", 9, "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d", "Solscan", "https://solscan.io/tx/", "https://api.mainnet.solana.com"),
    SOLANA_DEVNET: ("solana", "testnet", "SOL", 9, "EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG", "Solscan", "https://solscan.io/tx/", "https://api.devnet.solana.com"),
    "eip155:1": ("ethereum", "mainnet", "ETH", 18, "1", "Etherscan", "https://etherscan.io/tx/", "https://ethereum-rpc.publicnode.com"),
    "eip155:11155111": ("ethereum", "testnet", "ETH", 18, "11155111", "Etherscan", "https://sepolia.etherscan.io/tx/", "https://ethereum-sepolia-rpc.publicnode.com"),
    "eip155:8453": ("base", "mainnet", "ETH", 18, "8453", "Basescan", "https://basescan.org/tx/", "https://mainnet.base.org"),
    "eip155:84532": ("base", "testnet", "ETH", 18, "84532", "Basescan", "https://sepolia.basescan.org/tx/", "https://sepolia.base.org"),
    "eip155:56": ("bnb", "mainnet", "BNB", 18, "56", "BscScan", "https://bscscan.com/tx/", "https://bsc-dataseed.bnbchain.org"),
    "eip155:97": ("bnb", "testnet", "BNB", 18, "97", "BscScan", "https://testnet.bscscan.com/tx/", "https://bsc-testnet-dataseed.bnbchain.org"),
    "eip155:4663": ("robinhood", "mainnet", "ETH", 18, "4663", "Robinhood Explorer", "https://robinhoodchain.blockscout.com/tx/", "https://rpc.mainnet.chain.robinhood.com"),
    "eip155:46630": ("robinhood", "testnet", "ETH", 18, "46630", "Robinhood Explorer", "https://explorer.testnet.chain.robinhood.com/tx/", "https://rpc.testnet.chain.robinhood.com"),
}

CAPABILITIES = {"list", "create", "backup", "balance", "quote", "sign", "send", "receipt"}


def _sol_key() -> str:
    from core.vool_wallet import b58encode

    return b58encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


def _evm_address() -> str:
    return "0x" + uuid.uuid4().hex + uuid.uuid4().hex[:8]


@pytest.fixture
def pilot_env(monkeypatch, tmp_path):
    """Crypto enabled, no environment override, no RPC overrides, isolated blackbox, cold identity cache."""
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    for name in ("VOOL_WALLET_NETWORK_ENVIRONMENT", "VOOL_WALLET_RPC_URLS", "VOOL_WALLET_TESTNET_RPC_URL", "VOOL_WALLET_X402_ALLOW_LOOPBACK"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module
    from core.wallet import chains

    store_module.reset_default_store()
    chains.invalidate_chain_identity()
    yield
    chains.invalidate_chain_identity()
    store_module.reset_default_store()


def _preexisting_profile(network: str, public_key: str, *, mode: str = "watch_only") -> str:
    """A profile row exactly as a pre-pilot build wrote it (an upgraded install's data)."""
    from core.wallet.store import connection

    wallet_id = f"wallet-{uuid.uuid4().hex[:16]}"
    with connection() as conn:
        conn.execute(
            "INSERT INTO wallet_profiles (wallet_id, mode, network, public_key, label, created_at, is_default) VALUES (?, ?, ?, ?, ?, ?, 1)",
            (wallet_id, mode, network, public_key, "from before the pilot", "2026-09-01T00:00:00+00:00"),
        )
    return wallet_id


def _profile_rows() -> list[tuple]:
    from core.wallet.store import connection

    with connection() as conn:
        return conn.execute("SELECT * FROM wallet_profiles ORDER BY wallet_id").fetchall()


def _controls() -> dict[str, str]:
    from core.wallet.store import connection

    with connection() as conn:
        return {k: v for k, v in conn.execute("SELECT key, value FROM wallet_controls").fetchall()}


def _no_wallet_network(monkeypatch) -> None:
    """Every wallet network door raises: a path that must stay local proves it by not reaching one."""

    def refuse(*_args, **_kwargs):
        raise AssertionError("this path reached a network door")

    from core.wallet import outbound

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(outbound, "fetch", refuse)


# --- the registry -------------------------------------------------------------------------------------

def test_registry_declares_the_ten_pilot_rows_and_nothing_else(pilot_env):
    from core.wallet import chains, config

    assert set(chains.REGISTRY) == set(PILOT_ROWS)
    assert set(config.ALLOWED_NETWORKS) == set(PILOT_ROWS)
    environments_by_chain: dict[str, list[str]] = {}
    for network, (key, environment, symbol, decimals, identity, label, tx_prefix, origin) in PILOT_ROWS.items():
        spec = chains.resolve_network(network)
        assert spec.chain_key == key, network
        assert spec.environment == environment, network
        assert spec.testnet is (environment != "mainnet"), network
        natives = [(asset.symbol, asset.decimals) for asset in spec.assets.values() if asset.native]
        assert natives == [(symbol, decimals)], network
        if spec.is_svm:
            assert (spec.genesis_hash, spec.chain_id, network) == (identity, identity[:32], f"solana:{identity[:32]}")
        else:
            assert (spec.chain_id, network) == (identity, f"eip155:{identity}")
        assert spec.explorer_label == label, network
        assert spec.rpc_origins[0] == origin, network
        sample = ("0x" + "ab" * 32) if spec.is_evm else "5" * 88
        link = chains.explorer_tx_url(network, sample)
        assert link.startswith(tx_prefix) and link.count(sample) == 1, link
        environments_by_chain.setdefault(spec.chain_key, []).append(spec.environment)
    assert {k: sorted(v) for k, v in environments_by_chain.items()} == {k: ["mainnet", "testnet"] for k in ("solana", "ethereum", "base", "bnb", "robinhood")}
    assert "cluster=devnet" in chains.explorer_tx_url(SOLANA_DEVNET, "5" * 88)
    assert "cluster" not in chains.explorer_tx_url(SOLANA_MAINNET, "5" * 88)


@pytest.mark.parametrize("name", [
    "eip155:137", "eip155:999999", "eip155:46631", "base", "ethereum", "bnb", "robinhood", "mainnet", "testnet",
    "solana-mainnet", "mainnet-beta", "solana:DevNeT", "solana:5eykt4UsFv8P8NJdTREpYojv6fgkHiE7W", "EIP155:1",
])
def test_undeclared_aliases_and_confusables_still_refuse(pilot_env, name):
    from core.wallet import chains, config

    with pytest.raises(WalletFault) as refused:
        chains.resolve_network(name)
    assert refused.value.code == "wallet_network_disabled"
    assert config.network_allowed(name) is False


def test_rpc_origins_are_approved_per_row_not_by_a_word_in_the_url(pilot_env, monkeypatch):
    from core.wallet import chains, lifecycle

    robinhood = chains.resolve_network("eip155:4663")
    assert chains.rpc_origin_allowed(robinhood, "https://rpc.mainnet.chain.robinhood.com")
    assert chains.network_rpc_url("eip155:4663") == "https://rpc.mainnet.chain.robinhood.com"
    devnet, mainnet = chains.resolve_network(SOLANA_DEVNET), chains.resolve_network(SOLANA_MAINNET)
    assert not chains.rpc_origin_allowed(devnet, "https://api.mainnet.solana.com")
    assert not chains.rpc_origin_allowed(mainnet, "https://api.devnet.solana.com")
    assert not chains.rpc_origin_allowed(chains.resolve_network("eip155:1"), "https://sepolia.base.org")
    assert not chains.rpc_origin_allowed(chains.resolve_network("eip155:11155111"), "https://mainnet.infura.io/v3/x")
    with pytest.raises(WalletFault):
        lifecycle.RpcClient("https://api.mainnet.solana.com", network=SOLANA_DEVNET)
    assert lifecycle.RpcClient("https://api.mainnet.solana.com", network=SOLANA_MAINNET).network == SOLANA_MAINNET
    # the Solana devnet operator endpoint vouches for the devnet row and for no other row
    monkeypatch.setenv("VOOL_WALLET_TESTNET_RPC_URL", "https://rpc.operator.example")
    assert chains.rpc_origin_allowed(devnet, "https://rpc.operator.example")
    for network in PILOT_ROWS:
        if network != SOLANA_DEVNET:
            assert not chains.rpc_origin_allowed(chains.resolve_network(network), "https://rpc.operator.example"), network


def test_the_devnet_operator_endpoint_can_never_be_another_rows_origin(pilot_env, monkeypatch):
    from core.wallet import chains, lifecycle

    monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "testnet")
    devnet = chains.resolve_network(SOLANA_DEVNET)
    for foreign in ("https://api.mainnet.solana.com", "https://rpc.mainnet.chain.robinhood.com", "https://api.mainnet-beta.solana.com"):
        monkeypatch.setenv("VOOL_WALLET_TESTNET_RPC_URL", foreign)
        assert not chains.rpc_origin_allowed(devnet, foreign), foreign
        with pytest.raises(WalletFault) as refused_client:
            lifecycle.default_lifecycle()
        assert refused_client.value.code == "wallet_network_disabled", foreign
        with pytest.raises(WalletFault) as refused_route:
            chains.network_rpc_url(SOLANA_DEVNET)
        assert refused_route.value.code == "wallet_outbound_refused", foreign


# --- the network environment ----------------------------------------------------------------------------

def test_a_fresh_install_defaults_to_mainnet_and_reading_it_writes_nothing(pilot_env, monkeypatch):
    from core.wallet import environment, status

    _no_wallet_network(monkeypatch)
    state = environment.active_environment()
    assert (state.environment, state.source) == ("mainnet", "fresh_install_default")
    pilot = status.wallet_status()["crypto_pilot"]
    assert (pilot["active_environment"], pilot["environment_source"]) == ("mainnet", "fresh_install_default")
    assert _controls() == {} and _profile_rows() == []


def test_an_upgraded_test_network_install_keeps_its_context_without_relabelling(pilot_env):
    from core.wallet import custody, environment

    key = _sol_key()
    wallet_id = _preexisting_profile("solana-devnet", key)
    before = _profile_rows()
    state = environment.active_environment()
    assert (state.environment, state.source) == ("testnet", "upgrade_preserved_test_networks")
    assert _controls() == {}
    assert _profile_rows() == before
    assert custody.require_wallet(wallet_id).network == "solana-devnet"


def test_an_upgrade_with_a_mainnet_row_is_not_forced_into_test_networks(pilot_env):
    from core.wallet import environment

    _preexisting_profile("solana-devnet", _sol_key())
    _preexisting_profile("eip155:8453", _evm_address())
    assert environment.active_environment().environment == "mainnet"


def test_switching_both_directions_persists_and_never_touches_accounts(pilot_env):
    from core.wallet import environment

    _preexisting_profile("solana-devnet", _sol_key())
    _preexisting_profile("eip155:8453", _evm_address())
    before = _profile_rows()
    for wanted in ("testnet", "mainnet", "testnet", "mainnet"):
        environment.set_active_environment(wanted)
        state = environment.active_environment()
        assert (state.environment, state.source) == (wanted, "stored")
    assert _profile_rows() == before
    with pytest.raises(WalletFault) as refused:
        environment.set_active_environment("staging")
    assert refused.value.code == "wallet_environment_inactive"
    assert environment.active_environment().environment == "mainnet"


def test_the_operator_override_selects_test_networks_only_and_is_reported(pilot_env, monkeypatch):
    from core.wallet import environment, status

    environment.set_active_environment("mainnet")
    monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "testnet")
    state = environment.active_environment()
    assert (state.environment, state.source) == ("testnet", "operator_override")
    assert status.wallet_status()["crypto_pilot"]["environment_source"] == "operator_override"
    # a process environment can never move a person onto Mainnet
    environment.set_active_environment("testnet")
    for forced in ("mainnet", "MAINNET", "production"):
        monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", forced)
        state = environment.active_environment()
        assert (state.environment, state.source) == ("testnet", "stored"), forced


def test_the_first_effect_pins_a_derived_environment(pilot_env):
    from core.wallet import custody, environment

    assert environment.active_environment().source == "fresh_install_default"
    assert _controls() == {}
    custody.create_watch_only_wallet(_evm_address(), network="eip155:8453")
    state = environment.active_environment()
    assert (state.environment, state.source) == ("mainnet", "stored")
    assert json.loads(_controls()["network_environment"])["set_by"] == "fresh_install_default"


def test_effects_on_the_inactive_environment_refuse_before_any_network_door(pilot_env, monkeypatch):
    from core.wallet import custody, environment, proposals

    devnet_wallet = _preexisting_profile("solana-devnet", _sol_key())
    base_wallet = _preexisting_profile("eip155:8453", _evm_address())
    environment.set_active_environment("mainnet")
    _no_wallet_network(monkeypatch)
    with pytest.raises(WalletFault) as refused:
        proposals.propose_transaction(wallet_id=devnet_wallet, destination=_sol_key(), amount_minor=1000, asset="SOL", origin=proposals.ORIGIN_USER)
    assert refused.value.code == "wallet_environment_inactive"
    assert proposals.list_proposals() == []
    with pytest.raises(WalletFault) as refused_account:
        custody.create_watch_only_wallet(_evm_address(), network="eip155:84532")
    assert refused_account.value.code == "wallet_environment_inactive"
    # the active environment's rows stay usable: control
    minted = proposals.propose_transaction(wallet_id=base_wallet, destination=_evm_address(), amount_minor=10, asset="ETH", origin=proposals.ORIGIN_USER)
    assert (minted.network, minted.state) == ("eip155:8453", proposals.STATE_PROPOSED)
    environment.set_active_environment("testnet")
    again = proposals.propose_transaction(wallet_id=devnet_wallet, destination=_sol_key(), amount_minor=1000, asset="SOL", origin=proposals.ORIGIN_USER)
    assert again.state == proposals.STATE_PROPOSED


# --- capability status ---------------------------------------------------------------------------------

def test_status_reports_every_row_with_typed_capabilities_and_no_network_io(pilot_env, monkeypatch):
    from core.wallet import evm, status

    _no_wallet_network(monkeypatch)
    pilot = status.wallet_status()["crypto_pilot"]
    rows = {row["network"]: row for row in pilot["networks"]}
    assert set(rows) == set(PILOT_ROWS)
    for network, row in rows.items():
        key, environment, symbol, _decimals, _identity, label, _prefix, _origin = PILOT_ROWS[network]
        assert (row["chain_key"], row["environment"], row["native_symbol"], row["explorer_label"]) == (key, environment, symbol, label)
        assert set(row["capabilities"]) == CAPABILITIES
        for name, capability in row["capabilities"].items():
            assert isinstance(capability["available"], bool), (network, name)
            assert capability["available"] or capability["reason"], (network, name)
        if environment == "mainnet":
            assert row["badge"] == "MAINNET" and row["value_note"] == "Real funds"
        else:
            assert row["badge"] == ("DEVNET" if key == "solana" else "TESTNET")
            assert row["value_note"] == "Test funds have no monetary value"
            assert row["capabilities"]["create"] == {"available": False, "reason": "environment_inactive"}
        # stage 3: Create is offered on every row of the active environment (the storage class and the family stack
        # allow it here) and Backup only once a setup awaits it; Send stays unavailable until the dispatch lane
        # (stage 5) rebinds this line
        if environment == "mainnet":
            assert row["capabilities"]["create"] == {"available": True, "reason": ""}, network
            assert row["capabilities"]["backup"] == {"available": False, "reason": "no_setup_awaiting_backup"}, network
        assert row["capabilities"]["send"]["available"] is False, network
        assert row["capabilities"]["list"] == {"available": True, "reason": ""}
    monkeypatch.setattr(evm, "missing_evm_dependencies", lambda: ("eth_account",))
    degraded = {row["network"]: row for row in status.wallet_status()["crypto_pilot"]["networks"]}
    for network in ("eip155:1", "eip155:8453", "eip155:56", "eip155:4663"):
        assert degraded[network]["capabilities"]["create"] == {"available": False, "reason": "evm_dependencies_missing"}
    assert degraded[SOLANA_MAINNET]["capabilities"]["create"]["reason"] != "evm_dependencies_missing"
    assert _controls() == {} and _profile_rows() == []


def test_a_legacy_door_on_a_fresh_install_cannot_flip_the_environment(pilot_env):
    from core.wallet import custody, environment

    with pytest.raises(WalletFault) as refused:
        custody.create_watch_only_wallet(_sol_key(), network="solana-devnet")
    assert refused.value.code == "wallet_environment_inactive"
    assert _profile_rows() == []
    state = environment.active_environment()
    assert state.environment == "mainnet"


def test_legacy_pocket_creation_and_restore_never_reach_a_mainnet_row(pilot_env):
    from core.wallet import custody, mnemonic

    for call in (
        lambda: custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin="12345678", network=SOLANA_MAINNET),
        lambda: custody.restore_pocket_wallet(mnemonic.generate_mnemonic(), pin="12345678", network=SOLANA_MAINNET),
    ):
        with pytest.raises(WalletFault) as refused:
            call()
        assert refused.value.code == "wallet_network_disabled"
        assert refused.value.context.get("reason") == "legacy_creation_is_test_networks_only"
    assert _profile_rows() == []


# --- chain identity on the Solana lane -------------------------------------------------------------------

def test_the_solana_lane_proves_the_endpoint_genesis_before_simulating(pilot_env, monkeypatch):
    from core.wallet import custody, lifecycle, proposals

    monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "testnet")
    with ScriptedRpc(genesis_hash=MAINNET_GENESIS) as lying:
        monkeypatch.setenv("VOOL_WALLET_TESTNET_RPC_URL", lying.url)
        profile = custody.create_watch_only_wallet(_sol_key(), network="solana-devnet")
        proposal = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=_sol_key(), amount_minor=1000, asset="SOL", origin=proposals.ORIGIN_USER)
        with pytest.raises(WalletFault) as refused:
            lifecycle.default_lifecycle().prepare(proposal.proposal_id)
        assert refused.value.code == "wallet_chain_identity_mismatch"
        methods = [call.get("method") for call in lying.calls]
        assert "getGenesisHash" in methods
        assert "simulateTransaction" not in methods and "sendTransaction" not in methods
        assert proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_FAILED
    # control: an endpoint that answers the devnet genesis proceeds, identity first
    with ScriptedRpc() as honest:
        monkeypatch.setenv("VOOL_WALLET_TESTNET_RPC_URL", honest.url)
        second = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=_sol_key(), amount_minor=1000, asset="SOL", origin=proposals.ORIGIN_USER)
        prepared = lifecycle.default_lifecycle().prepare(second.proposal_id)
        assert prepared.state == proposals.STATE_PENDING_APPROVAL
        methods = [call.get("method") for call in honest.calls]
        assert methods.index("getGenesisHash") < methods.index("simulateTransaction")


def test_identity_is_proven_per_endpoint_so_a_verified_endpoint_cannot_vouch_for_another(pilot_env, monkeypatch):
    from core.wallet import custody, lifecycle, proposals

    monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "testnet")
    with ScriptedRpc() as honest, ScriptedRpc(genesis_hash=MAINNET_GENESIS) as lying:
        monkeypatch.setenv("VOOL_WALLET_TESTNET_RPC_URL", honest.url)
        profile = custody.create_watch_only_wallet(_sol_key(), network="solana-devnet")
        first = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=_sol_key(), amount_minor=1000, asset="SOL", origin=proposals.ORIGIN_USER)
        assert lifecycle.default_lifecycle().prepare(first.proposal_id).state == proposals.STATE_PENDING_APPROVAL
        monkeypatch.setenv("VOOL_WALLET_TESTNET_RPC_URL", lying.url)
        second = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=_sol_key(), amount_minor=2000, asset="SOL", origin=proposals.ORIGIN_USER)
        with pytest.raises(WalletFault) as refused:
            lifecycle.default_lifecycle().prepare(second.proposal_id)
        assert refused.value.code == "wallet_chain_identity_mismatch"
        assert "simulateTransaction" not in [call.get("method") for call in lying.calls]


def test_a_mainnet_row_proposal_is_routed_to_its_own_row_endpoint(pilot_env, monkeypatch):
    import json

    from core.wallet import environment, lifecycle, proposals

    environment.set_active_environment("mainnet")
    with ScriptedRpc() as devnet_node, ScriptedRpc(genesis_hash=MAINNET_GENESIS) as mainnet_node:
        monkeypatch.setenv("VOOL_WALLET_TESTNET_RPC_URL", devnet_node.url)
        monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
        monkeypatch.setenv("VOOL_WALLET_RPC_URLS", json.dumps({SOLANA_MAINNET: mainnet_node.url}))
        wallet_id = _preexisting_profile(SOLANA_MAINNET, _sol_key())
        proposal = proposals.propose_transaction(wallet_id=wallet_id, destination=_sol_key(), amount_minor=1000, asset="SOL", origin=proposals.ORIGIN_USER)
        assert lifecycle.default_lifecycle().prepare(proposal.proposal_id).state == proposals.STATE_PENDING_APPROVAL
        assert devnet_node.calls == []
        assert {"getGenesisHash", "simulateTransaction"} <= {call.get("method") for call in mainnet_node.calls}


# --- no signature before the pilot transfer lane, and no approval across a switch ----------------------

def test_no_legacy_door_claims_or_signs_a_pilot_transfer_on_a_mainnet_row(pilot_env, monkeypatch):
    from core.wallet import approval, custody, environment, lifecycle, limits, proposals

    environment.set_active_environment("mainnet")
    with ScriptedRpc(genesis_hash=MAINNET_GENESIS) as mainnet_node:
        monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
        monkeypatch.setenv("VOOL_WALLET_RPC_URLS", json.dumps({SOLANA_MAINNET: mainnet_node.url}))
        signer = custody.register_external_signer_wallet(_sol_key(), network=SOLANA_MAINNET)
        proposal = proposals.propose_transaction(wallet_id=signer.wallet_id, destination=_sol_key(), amount_minor=1000, asset="SOL", origin=proposals.ORIGIN_USER)
        engine = lifecycle.default_lifecycle()
        assert engine.prepare(proposal.proposal_id).state == proposals.STATE_PENDING_APPROVAL
        for door in (
            lambda: engine.request_external_signature(proposal.proposal_id),
            lambda: engine.approve_and_execute(proposal.proposal_id, approver=approval.PinApprover("246810")),
        ):
            with pytest.raises(WalletFault) as refused:
                door()
            assert (refused.value.code, refused.value.context.get("reason")) == ("wallet_network_disabled", "pilot_transfer_needs_quote_approval")
        after = proposals.get_proposal(proposal.proposal_id)
        assert (after.state, after.tx_signature) == (proposals.STATE_PENDING_APPROVAL, "")
        assert limits.reservation_state(proposal.proposal_id) == ""
        assert proposals.approval_refusals(proposal.proposal_id) == 0
        assert "sendTransaction" not in [call.get("method") for call in mainnet_node.calls]


def test_a_switch_cannot_carry_a_prepared_request_into_the_other_environment(pilot_env, monkeypatch):
    from core.wallet import custody, environment, lifecycle, proposals

    environment.set_active_environment("testnet")
    with ScriptedRpc() as devnet_node:
        monkeypatch.setenv("VOOL_WALLET_TESTNET_RPC_URL", devnet_node.url)
        signer = custody.register_external_signer_wallet(_sol_key(), network="solana-devnet")
        proposal = proposals.propose_transaction(wallet_id=signer.wallet_id, destination=_sol_key(), amount_minor=1000, asset="SOL", origin=proposals.ORIGIN_USER)
        engine = lifecycle.default_lifecycle()
        assert engine.prepare(proposal.proposal_id).state == proposals.STATE_PENDING_APPROVAL
        environment.set_active_environment("mainnet")
        with pytest.raises(WalletFault) as refused:
            engine.request_external_signature(proposal.proposal_id)
        assert refused.value.code == "wallet_environment_inactive"
        assert proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_PENDING_APPROVAL
        # control: back on Test networks the prepared proposal opens its signing request on its own row
        environment.set_active_environment("testnet")
        view = engine.request_external_signature(proposal.proposal_id)
        assert proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_AWAITING_SIGNATURE
        # an open request is still unsent: a switch refuses its submission before any signature is read
        environment.set_active_environment("mainnet")
        with pytest.raises(WalletFault) as refused_submit:
            engine.submit_external_signature(view["request_id"], signature_b58="1" * 88)
        assert refused_submit.value.code == "wallet_environment_inactive"
        assert proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_AWAITING_SIGNATURE
        assert "sendTransaction" not in [call.get("method") for call in devnet_node.calls]


def test_a_proposal_minted_before_a_switch_is_not_prepared_after_it(pilot_env, monkeypatch):
    from core.wallet import custody, environment, lifecycle, proposals

    environment.set_active_environment("testnet")
    with ScriptedRpc() as devnet_node:
        monkeypatch.setenv("VOOL_WALLET_TESTNET_RPC_URL", devnet_node.url)
        watcher = custody.create_watch_only_wallet(_sol_key(), network="solana-devnet")
        proposal = proposals.propose_transaction(wallet_id=watcher.wallet_id, destination=_sol_key(), amount_minor=1000, asset="SOL", origin=proposals.ORIGIN_USER)
        environment.set_active_environment("mainnet")
        with pytest.raises(WalletFault) as refused:
            lifecycle.default_lifecycle().prepare(proposal.proposal_id)
        assert refused.value.code == "wallet_environment_inactive"
        assert proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_PROPOSED
        assert devnet_node.calls == []


def test_an_endpoint_swapped_after_the_signing_request_is_proven_again_before_broadcast(pilot_env, monkeypatch):
    import base64

    from core.vool_wallet import b58encode
    from core.wallet import custody, environment, lifecycle, limits, proposals

    environment.set_active_environment("testnet")
    key = Ed25519PrivateKey.generate()
    public_key = b58encode(key.public_key().public_bytes_raw())
    with ScriptedRpc() as honest, ScriptedRpc(genesis_hash=MAINNET_GENESIS) as lying:
        monkeypatch.setenv("VOOL_WALLET_TESTNET_RPC_URL", honest.url)
        signer = custody.register_external_signer_wallet(public_key, network="solana-devnet")
        proposal = proposals.propose_transaction(wallet_id=signer.wallet_id, destination=_sol_key(), amount_minor=1000, asset="SOL", origin=proposals.ORIGIN_USER)
        engine = lifecycle.default_lifecycle()
        engine.prepare(proposal.proposal_id)
        view = engine.request_external_signature(proposal.proposal_id)
        assert limits.reservation_state(proposal.proposal_id) == limits.RESERVATION_RESERVED
        signature = b58encode(key.sign(base64.b64decode(view["message_b64"])))
        # the configured endpoint changes between the signing request and the submission
        monkeypatch.setenv("VOOL_WALLET_TESTNET_RPC_URL", lying.url)
        with pytest.raises(WalletFault) as refused:
            lifecycle.default_lifecycle().submit_external_signature(view["request_id"], signature_b58=signature)
        assert refused.value.code == "wallet_chain_identity_mismatch"
        assert "sendTransaction" not in [call.get("method") for call in honest.calls + lying.calls]
        after = proposals.get_proposal(proposal.proposal_id)
        assert (after.state, after.tx_signature) == (proposals.STATE_FAILED, "")
        assert limits.reservation_state(proposal.proposal_id) == limits.RESERVATION_RELEASED
