"""Stage B — the typed chain and asset registry (RED corpus).

One immutable registry under `core.wallet`: CAIP-2 identity, execution family, an
uncallable-overridable testnet flag, expected chain id/genesis, approved RPC origins,
supported x402 schemes, explicit asset records, facilitator capability and explorer
templates. Every rejection the goal names is pinned here as a test.
"""
from __future__ import annotations

import pytest

DEVNET_CAIP2 = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
BASE_SEPOLIA = "eip155:84532"
ETHEREUM_SEPOLIA = "eip155:11155111"
BSC_TESTNET = "eip155:97"
DECLARED = (DEVNET_CAIP2, BASE_SEPOLIA, ETHEREUM_SEPOLIA, BSC_TESTNET)

#: official chain ids / genesis the registry must pin
EXPECTED_CHAIN_ID = {
    DEVNET_CAIP2: "EtWTRABZaYq6iMfeYKouRu166VU2xqa1",  # 32-char prefix of the devnet genesis
    BASE_SEPOLIA: "84532",
    ETHEREUM_SEPOLIA: "11155111",
    BSC_TESTNET: "97",
}


@pytest.fixture
def wallet_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "testnet")
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module

    store_module.reset_default_store()
    yield
    store_module.reset_default_store()


def test_b1_every_declared_network_resolves_with_canonical_identity(wallet_env):
    from core.wallet import chains

    for network in DECLARED:
        spec = chains.resolve_network(network)
        assert spec.network == network
        assert spec.testnet is True
        assert spec.family in ("svm", "evm")
        assert spec.chain_id == EXPECTED_CHAIN_ID[network]
        assert spec.schemes == ("exact",)
        assert spec.explorer_tx_template  # a template exists
    assert chains.resolve_network(DEVNET_CAIP2).family == "svm"
    for evm in (BASE_SEPOLIA, ETHEREUM_SEPOLIA, BSC_TESTNET):
        assert chains.resolve_network(evm).family == "evm"


def test_b2_legacy_solana_devnet_name_maps_to_the_canonical_identity(wallet_env):
    """The stored profiles of the finished devnet lane say `solana-devnet`; they must keep
    working and mean exactly the canonical CAIP-2 identity."""
    from core.wallet import chains

    spec = chains.resolve_network("solana-devnet")
    assert spec.network == DEVNET_CAIP2
    assert spec.family == "svm"
    assert spec.testnet is True


def test_b3_the_testnet_flag_cannot_be_caller_overridden(wallet_env):
    from core.wallet import chains

    spec = chains.resolve_network(BASE_SEPOLIA)
    with pytest.raises(Exception):  # noqa: B017 - any refusal is the bite
        object.__setattr__  # noqa: B018 - existence is not the test; mutation below is
        spec.testnet = False  # type: ignore[misc] - frozen dataclass must refuse
    with pytest.raises(Exception):  # noqa: B017 - any refusal is the bite
        chains.resolve_network(BASE_SEPOLIA, testnet=False)  # type: ignore[call-arg]
    assert chains.resolve_network(BASE_SEPOLIA).testnet is True


def test_b4_aliases_confusables_and_unknown_networks_refuse(wallet_env):
    from core.wallet import chains
    from core.wallet.errors import WalletFault

    for bad in (
        "solana:5eykt4UsFv8P8NJdTREpYojv6fgkHiE7W",  # solana mainnet
        "solana-mainnet", "mainnet-beta",
        "eip155:137", "eip155:42161", "eip155:46631",  # undeclared chains; the declared Mainnet rows are asserted below
        "base-mainnet", "ethereum", "base", "bnb", "bsc", "polygon", "arbitrum", "optimism",
        "eip155:84532x", "eip155:845 32", " EIP155:84532",  # confusable mutations
        "devnet", "testnet", "localhost", "http://127.0.0.1:8899",  # never select from a URL/loose word
        "eip155:", "solana:", ":",  # malformed
        "notachain:1234", "eip155:999999",
    ):
        with pytest.raises(WalletFault) as refused:
            chains.resolve_network(bad)
        assert refused.value.code == "wallet_network_disabled", bad
    # Crypto Pilot: Ethereum, Base and BNB Smart Chain mainnets are declared rows, each its own chain and
    # environment, never a Test networks row under another name
    for network, twin in (("eip155:1", "eip155:11155111"), ("eip155:8453", "eip155:84532"), ("eip155:56", "eip155:97")):
        spec, test_row = chains.resolve_network(network), chains.resolve_network(twin)
        assert (spec.network, spec.environment, test_row.environment) == (network, "mainnet", "testnet")
        assert spec.chain_id != test_row.chain_id and spec.chain_key == test_row.chain_key


def test_b5_caller_supplied_network_objects_are_refused(wallet_env):
    """A dict, dataclass or URL must never stand in for a network identity."""
    from core.wallet import chains
    from core.wallet.errors import WalletFault

    for bad in ({"network": BASE_SEPOLIA}, object(), b"eip155:84532", 84532, None, ["eip155:84532"]):
        with pytest.raises(WalletFault):
            chains.resolve_network(bad)


def test_b6_assets_are_explicit_and_chain_qualified(wallet_env):
    from core.wallet import chains

    usdc_base = chains.asset_for(BASE_SEPOLIA, "USDC")
    assert usdc_base.symbol == "USDC"
    assert usdc_base.decimals == 6
    assert usdc_base.address.startswith("0x")
    assert usdc_base.chain == BASE_SEPOLIA
    assert usdc_base.eip712_name and usdc_base.eip712_version
    sol = chains.asset_for(DEVNET_CAIP2, "SOL")
    assert sol.native is True and sol.decimals == 9
    bnb = chains.asset_for(BSC_TESTNET, "BNB")
    assert bnb.native is True and bnb.decimals == 18
    # by exact address too
    assert chains.asset_for(BASE_SEPOLIA, usdc_base.address).symbol == "USDC"
    # Base Sepolia and Ethereum Sepolia each carry their own official testnet USDC row
    rows = {chains.asset_for(net, "USDC").address for net in (BASE_SEPOLIA, ETHEREUM_SEPOLIA)}
    assert len(rows) == 2
    # BSC testnet's x402 asset set stays native-only until an official token row carries its
    # chain/asset/facilitator evidence — a missing row is a typed refusal, never a guess
    with pytest.raises(Exception):  # noqa: B017 - any refusal is the bite
        chains.asset_for(BSC_TESTNET, "USDC")


def test_b7_unregistered_or_cross_chain_assets_refuse(wallet_env):
    from core.wallet import chains
    from core.wallet.errors import WalletFault

    with pytest.raises(WalletFault):
        chains.asset_for(BASE_SEPOLIA, "USDT")  # not registered anywhere in this build
    with pytest.raises(WalletFault):
        chains.asset_for(BASE_SEPOLIA, "SOL")  # a Solana asset is not an EVM asset
    base_usdc = chains.asset_for(BASE_SEPOLIA, "USDC").address
    with pytest.raises(WalletFault) as mismatched:
        chains.asset_for(ETHEREUM_SEPOLIA, base_usdc)  # right symbol, wrong chain's contract
    assert mismatched.value.code == "wallet_network_disabled"
    with pytest.raises(WalletFault):
        chains.asset_for(BASE_SEPOLIA, "usdc with decimals 18")  # no such row


def test_b8_the_registry_is_immutable_against_every_caller(wallet_env):
    from core.wallet import chains

    with pytest.raises(TypeError):
        chains.REGISTRY[BASE_SEPOLIA] = None  # type: ignore[index]
    with pytest.raises(TypeError):
        chains.REGISTRY[DEVNET_CAIP2].assets["USDT"] = None  # type: ignore[index]
    with pytest.raises(AttributeError):  # frozen: FrozenInstanceError subclasses AttributeError
        chains.resolve_network(BASE_SEPOLIA).testnet = False  # type: ignore[misc]
    with pytest.raises(AttributeError):
        chains.asset_for(BASE_SEPOLIA, "USDC").decimals = 18  # type: ignore[misc]


def test_b9_rpc_chain_identity_is_verified_by_a_bounded_read_and_cached(wallet_env):
    """A wrong chainId/genesis from the endpoint is a typed refusal; a matching one is
    cached (the second verify makes no second call)."""
    from core.wallet import chains

    calls: list[str] = []

    def good_probe(method: str) -> object:
        calls.append(method)
        if chains.resolve_network(BASE_SEPOLIA).family == "evm":
            return "0x14a34"  # 84532
        return EXPECTED_CHAIN_ID[DEVNET_CAIP2]

    spec = chains.resolve_network(BASE_SEPOLIA)
    verified = chains.verify_chain_identity(spec, good_probe)
    assert verified.network == BASE_SEPOLIA
    verified_again = chains.verify_chain_identity(spec, good_probe)
    assert verified_again == verified
    assert len(calls) == 1  # served from cache

    def lying_probe(method: str) -> object:
        calls.append(method)
        return "0x1"  # claims mainnet

    from core.wallet.errors import WalletFault

    calls.clear()
    with pytest.raises(WalletFault) as mismatch:
        chains.verify_chain_identity(spec, lying_probe, force=True)
    assert mismatch.value.code == "wallet_chain_identity_mismatch"
    assert len(calls) == 1
    # solana side: a devnet endpoint that reports the mainnet genesis
    svm = chains.resolve_network(DEVNET_CAIP2)
    with pytest.raises(WalletFault) as svm_mismatch:
        chains.verify_chain_identity(svm, lambda method: "5eykt4UsFv8P8NJdTREpYojv6fgkHiE7WmMkeqXk4SSt", force=True)
    assert svm_mismatch.value.code == "wallet_chain_identity_mismatch"


def test_b10_identity_cache_invalidation_is_policy_version_and_scope_bound(wallet_env):
    from core.wallet import chains

    calls: list[str] = []
    spec = chains.resolve_network(BSC_TESTNET)

    def probe(method: str) -> object:
        calls.append(method)
        return "0x61"  # 97

    chains.verify_chain_identity(spec, probe, scope="turn-1")
    chains.verify_chain_identity(spec, probe, scope="turn-1")  # cached within scope
    assert len(calls) == 1
    chains.verify_chain_identity(spec, probe, scope="turn-2")  # a new scope re-verifies
    assert len(calls) == 2
    chains.invalidate_chain_identity(spec)  # an explicit invalidation re-verifies
    chains.verify_chain_identity(spec, probe, scope="turn-2")
    assert len(calls) == 3


def test_b11_rpc_origin_policy_gates_every_endpoint(wallet_env):
    from core.wallet import chains

    spec = chains.resolve_network(ETHEREUM_SEPOLIA)
    assert chains.rpc_origin_allowed(spec, "https://ethereum-sepolia-rpc.publicnode.com")
    with pytest.raises(Exception):  # noqa: B017 - any refusal is the bite
        chains.rpc_origin_allowed_or_refuse(spec, "https://mainnet.infura.io/v3/x")
    with pytest.raises(Exception):  # noqa: B017 - any refusal is the bite
        chains.rpc_origin_allowed_or_refuse(spec, "http://169.254.169.254/latest")  # metadata
    with pytest.raises(Exception):  # noqa: B017 - any refusal is the bite
        chains.rpc_origin_allowed_or_refuse(spec, "ftp://example.test")


def test_b12_deterministic_capability_selection_across_accepts(wallet_env):
    """Given several accepts[] entries, selection is a pure function of the wallet's actual
    account, network, asset, scheme, facilitator and cost — and two identical inputs pick
    the same entry. This pins the pure layer; the x402 stage pins the wire layer."""
    from core.wallet import chains

    chains.resolve_network(BASE_SEPOLIA)  # identical inputs, identical identity objects
    chains.resolve_network(BASE_SEPOLIA)

    class Offer:
        def __init__(self, network: str, scheme: str, asset: str, amount: int):
            self.network, self.scheme, self.asset, self.amount = network, scheme, asset, amount

    offers = [
        Offer(BASE_SEPOLIA, "exact", "USDC", 1500),
        Offer(BASE_SEPOLIA, "unknown-scheme", "USDC", 1),
        Offer("eip155:1", "exact", "USDC", 1),  # mainnet entry: never selectable
        Offer(BSC_TESTNET, "exact", "USDC", 1),
    ]
    picked_1 = chains.select_declared_offer(offers, networks={BASE_SEPOLIA, BSC_TESTNET}, assets={BASE_SEPOLIA: {"USDC"}, BSC_TESTNET: {"USDC"}}, schemes=("exact",))
    picked_2 = chains.select_declared_offer(offers, networks={BASE_SEPOLIA, BSC_TESTNET}, assets={BASE_SEPOLIA: {"USDC"}, BSC_TESTNET: {"USDC"}}, schemes=("exact",))
    assert picked_1 is offers[0]  # the declared, exact, registered entry wins deterministically
    assert picked_1 is picked_2
    unknown = chains.select_declared_offer([offers[1]], networks={BASE_SEPOLIA}, assets={BASE_SEPOLIA: {"USDC"}}, schemes=("exact",))
    assert unknown is None  # a reasoned refusal, not a fallback to "first entry"


def test_b13_explorer_links_exist_only_for_public_ids(wallet_env):
    from core.wallet import chains

    url = chains.explorer_tx_url(BASE_SEPOLIA, "0x" + "ab" * 32)
    assert url.startswith("https://sepolia.basescan.org/tx/")
    svm_url = chains.explorer_tx_url(DEVNET_CAIP2, "5" * 87)
    assert "devnet" in svm_url
    with pytest.raises(Exception):  # noqa: B017 - any refusal is the bite
        chains.explorer_tx_url(BASE_SEPOLIA, "with spaces; not an id")
