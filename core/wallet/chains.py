"""The ONE typed chain and asset registry, owned beneath ``core.wallet``.

Every network this build can ever talk to is a declarative row here, reviewed as code:
a CAIP-2 identity, an execution family (``svm``/``evm``), its environment (Mainnet or Test networks),
an expected chain id or genesis prefix, an approved RPC origin policy, the x402 schemes it supports,
its explicit asset records, the fee model its quotes follow, the finality its receipts wait for, and
the explorer templates used ONLY for public transaction ids and addresses. Adding a row is a code
change that must carry its chain, asset, signer, fee and settlement evidence (delivery/NETWORK-MATRIX.json
for the Crypto Pilot rows) -- there is no runtime registration door and no caller-supplied network
object that can become a network.

Identity is never taken from a URL string or a friendly label alone: :func:`resolve_network`
accepts only a canonical CAIP-2 id, one of the declared legacy names of an existing row
(the finished Solana-devnet lane stores ``solana-devnet``), or nothing. Chain identity is
ESTABLISHED by a bounded read (:func:`verify_chain_identity` -- ``eth_chainId`` or
``getGenesisHash``) and cached under a policy version and a scope that names the endpoint; a lying
endpoint is a typed ``wallet_chain_identity_mismatch`` refusal, not a payment. Admission is the row
itself, never a word inside a URL: an origin is approved because the row declares it.
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

from core.wallet.security import wallet_fault

AUTHORITY = "core.wallet.chains"

FAMILY_SVM = "svm"
FAMILY_EVM = "evm"
SCHEME_EXACT = "exact"

ENVIRONMENT_MAINNET = "mainnet"
ENVIRONMENT_TESTNET = "testnet"

FEE_SVM = "svm_base_fee"
FEE_EIP1559 = "evm_eip1559"
FEE_OP_STACK = "evm_op_stack"
FEE_ARBITRUM_NITRO = "evm_arbitrum_nitro"
FEE_BSC = "evm_bsc"

#: The receipt predicate each row waits for before a transfer reads Confirmed.
FINALITY_SVM_CONFIRMED = "confirmed"  # Solana commitment; finalized is tracked beside it
FINALITY_FINALIZED_TAG = "finalized"  # EVM block tag
FINALITY_SAFE_TAG = "safe"  # EVM block tag on rollups (L1 batch inclusion)

#: Card order and names of the Crypto Pilot's five chains.
CHAIN_KEY_ORDER: tuple[str, ...] = ("solana", "robinhood", "base", "ethereum", "bnb")
CHAIN_KEY_LABELS: Mapping[str, str] = MappingProxyType({
    "solana": "Solana", "robinhood": "Robinhood Chain", "base": "Base", "ethereum": "Ethereum", "bnb": "BNB Smart Chain",
})

#: The policy version participates in the chain-identity cache key: bumping it invalidates
#: every cached identity (a registry or policy change re-proves the chain).
IDENTITY_POLICY_VERSION = "chains-v1"


@dataclass(frozen=True)
class AssetSpec:
    """One asset on one chain. Immutable; `address` is the EVM contract or the SPL mint."""

    chain: str
    symbol: str
    address: str
    decimals: int
    native: bool = False
    #: EIP-712 domain name/version of the token contract itself, where the token carries one
    #: (an EIP-3009 capable FiatToken). Empty where not applicable.
    eip712_name: str = ""
    eip712_version: str = ""

    def key(self) -> tuple[str, str]:
        return (self.chain, self.address.lower())


@dataclass(frozen=True)
class ChainIdentity:
    network: str  # canonical CAIP-2
    family: str  # FAMILY_SVM | FAMILY_EVM
    testnet: bool  # derived from `environment`; kept as a field so no caller can override one without the other
    chain_id: str  # decimal EIP-155 id, or the 32-char genesis prefix for svm
    genesis_hash: str  # full genesis hash for svm rows; "" for evm
    display_name: str
    legacy_names: tuple[str, ...]
    rpc_origins: tuple[str, ...]  # approved default RPC origins (https)
    schemes: tuple[str, ...]  # x402 schemes; empty on rows where x402 is not offered
    explorer_tx_template: str
    assets: Mapping[str, AssetSpec] = field(default_factory=lambda: MappingProxyType({}))
    environment: str = ENVIRONMENT_TESTNET
    chain_key: str = ""
    explorer_label: str = ""
    explorer_address_template: str = ""
    fee_model: str = ""
    finality: str = ""
    #: the native symbol shown to people when it differs from the asset row's symbol (tBNB on Chapel)
    native_display_symbol: str = ""

    def __post_init__(self) -> None:
        if self.environment not in (ENVIRONMENT_MAINNET, ENVIRONMENT_TESTNET):
            raise ValueError(f"{self.network}: unknown environment {self.environment!r}")
        if self.testnet != (self.environment == ENVIRONMENT_TESTNET):
            raise ValueError(f"{self.network}: the testnet flag contradicts the environment")

    @property
    def is_evm(self) -> bool:
        return self.family == FAMILY_EVM

    @property
    def is_svm(self) -> bool:
        return self.family == FAMILY_SVM

    @property
    def is_mainnet(self) -> bool:
        return self.environment == ENVIRONMENT_MAINNET


def _native(chain: str, symbol: str, decimals: int) -> AssetSpec:
    return AssetSpec(chain=chain, symbol=symbol, address="", decimals=decimals, native=True)


SOLANA_MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
SOLANA_DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"


def _devnet_assets() -> Mapping[str, AssetSpec]:
    return MappingProxyType({
        "SOL": _native(SOLANA_DEVNET, "SOL", 9),
        "USDC": AssetSpec(
            chain=SOLANA_DEVNET, symbol="USDC",
            address="4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU", decimals=6,
        ),
    })


#: The declared rows of this build. Nothing else exists and no env var adds one. The four test rows
#: shipped before the Crypto Pilot keep their identities, names, schemes and assets; the pilot adds the
#: five Mainnet rows and Robinhood Chain Testnet. Facts verified against official sources and live
#: identity reads on 2026-09-14 (delivery/NETWORK-MATRIX.json). Mainnet rows declare no x402 scheme. The one
#: Mainnet token is USDC on Solana, for the UsePod x402 payment lane only (proposals refuse it for every other
#: origin); the pilot's own transfers move native coins only.
_REGISTRY: dict[str, ChainIdentity] = {
    SOLANA_DEVNET: ChainIdentity(
        network=SOLANA_DEVNET, family=FAMILY_SVM, testnet=True,
        chain_id="EtWTRABZaYq6iMfeYKouRu166VU2xqa1", genesis_hash="EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG",
        display_name="Solana Devnet", legacy_names=("solana-devnet",),
        rpc_origins=("https://api.devnet.solana.com",), schemes=(SCHEME_EXACT,),
        explorer_tx_template="https://solscan.io/tx/{id}?cluster=devnet", assets=_devnet_assets(),
        environment=ENVIRONMENT_TESTNET, chain_key="solana", explorer_label="Solscan",
        explorer_address_template="https://solscan.io/account/{address}?cluster=devnet",
        fee_model=FEE_SVM, finality=FINALITY_SVM_CONFIRMED,
    ),
    "eip155:84532": ChainIdentity(
        network="eip155:84532", family=FAMILY_EVM, testnet=True, chain_id="84532", genesis_hash="",
        display_name="Base Sepolia", legacy_names=("base-sepolia",),
        rpc_origins=("https://sepolia.base.org", "https://base-sepolia-rpc.publicnode.com"), schemes=(SCHEME_EXACT,),
        explorer_tx_template="https://sepolia.basescan.org/tx/{id}",
        assets=MappingProxyType({
            "ETH": _native("eip155:84532", "ETH", 18),
            "USDC": AssetSpec(
                chain="eip155:84532", symbol="USDC",
                address="0x036cbd53842c5426634e7929541ec2318f3dcf7e", decimals=6,
                eip712_name="USDC", eip712_version="2",
            ),
        }),
        environment=ENVIRONMENT_TESTNET, chain_key="base", explorer_label="Basescan",
        explorer_address_template="https://sepolia.basescan.org/address/{address}", fee_model=FEE_OP_STACK, finality=FINALITY_SAFE_TAG,
    ),
    "eip155:11155111": ChainIdentity(
        network="eip155:11155111", family=FAMILY_EVM, testnet=True, chain_id="11155111", genesis_hash="",
        display_name="Ethereum Sepolia", legacy_names=("ethereum-sepolia", "sepolia"),
        # rpc.sepolia.org was removed: sepolia.org discontinued its RPCs (HTTP 404, 2026-09-14)
        rpc_origins=("https://ethereum-sepolia-rpc.publicnode.com",), schemes=(SCHEME_EXACT,),
        explorer_tx_template="https://sepolia.etherscan.io/tx/{id}",
        assets=MappingProxyType({
            "ETH": _native("eip155:11155111", "ETH", 18),
            "USDC": AssetSpec(
                chain="eip155:11155111", symbol="USDC",
                address="0x1c7d4b196cb0c7b01d743fbc6116a902379c7238", decimals=6,
                #: No official EIP-712 name pin exists for Circle's Sepolia USDC in this
                #: lane's evidence; the domain comes from the offer's `extra` at proposal
                #: time and binds into the approval challenge. Empty means "unpinned", not
                #: "anything goes": an offer without an explicit name/version refuses.
                eip712_name="", eip712_version="",
            ),
        }),
        environment=ENVIRONMENT_TESTNET, chain_key="ethereum", explorer_label="Etherscan",
        explorer_address_template="https://sepolia.etherscan.io/address/{address}", fee_model=FEE_EIP1559, finality=FINALITY_FINALIZED_TAG,
    ),
    "eip155:97": ChainIdentity(
        network="eip155:97", family=FAMILY_EVM, testnet=True, chain_id="97", genesis_hash="",
        display_name="BNB Smart Chain Testnet", legacy_names=("bnb-smart-chain-testnet", "bsc-testnet"),
        # the official BNB Chain endpoints; bsc-testnet-rpc.publicnode.com served another host's TLS
        # certificate on 2026-09-14 and the binance.org host is not the one the official page lists
        rpc_origins=("https://bsc-testnet-dataseed.bnbchain.org", "https://data-seed-prebsc-1-s1.bnbchain.org:8545"), schemes=(SCHEME_EXACT,),
        explorer_tx_template="https://testnet.bscscan.com/tx/{id}",
        assets=MappingProxyType({"BNB": _native("eip155:97", "BNB", 18)}),
        environment=ENVIRONMENT_TESTNET, chain_key="bnb", explorer_label="BscScan",
        explorer_address_template="https://testnet.bscscan.com/address/{address}", fee_model=FEE_BSC, finality=FINALITY_FINALIZED_TAG,
        native_display_symbol="tBNB",
    ),
    "eip155:46630": ChainIdentity(
        network="eip155:46630", family=FAMILY_EVM, testnet=True, chain_id="46630", genesis_hash="",
        display_name="Robinhood Chain Testnet", legacy_names=(),
        rpc_origins=("https://rpc.testnet.chain.robinhood.com",), schemes=(),
        explorer_tx_template="https://explorer.testnet.chain.robinhood.com/tx/{id}",
        assets=MappingProxyType({"ETH": _native("eip155:46630", "ETH", 18)}),
        environment=ENVIRONMENT_TESTNET, chain_key="robinhood", explorer_label="Robinhood Explorer",
        explorer_address_template="https://explorer.testnet.chain.robinhood.com/address/{address}", fee_model=FEE_ARBITRUM_NITRO, finality=FINALITY_SAFE_TAG,
    ),
    SOLANA_MAINNET: ChainIdentity(
        network=SOLANA_MAINNET, family=FAMILY_SVM, testnet=False,
        chain_id="5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp", genesis_hash="5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d",
        display_name="Solana", legacy_names=(),
        rpc_origins=("https://api.mainnet.solana.com",), schemes=(),
        explorer_tx_template="https://solscan.io/tx/{id}",
        assets=MappingProxyType({
            "SOL": _native(SOLANA_MAINNET, "SOL", 9),
            # Circle's USDC mint on Solana (docs.usepod.ai/api/x402-payments names USDC on this network); the
            # mint's program and decimals are re-read from the chain before any transfer is built.
            "USDC": AssetSpec(chain=SOLANA_MAINNET, symbol="USDC", address="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", decimals=6),
        }),
        environment=ENVIRONMENT_MAINNET, chain_key="solana", explorer_label="Solscan",
        explorer_address_template="https://solscan.io/account/{address}", fee_model=FEE_SVM, finality=FINALITY_SVM_CONFIRMED,
    ),
    "eip155:4663": ChainIdentity(
        network="eip155:4663", family=FAMILY_EVM, testnet=False, chain_id="4663", genesis_hash="",
        display_name="Robinhood Chain", legacy_names=(),
        rpc_origins=("https://rpc.mainnet.chain.robinhood.com",), schemes=(),
        explorer_tx_template="https://robinhoodchain.blockscout.com/tx/{id}",
        assets=MappingProxyType({"ETH": _native("eip155:4663", "ETH", 18)}),
        environment=ENVIRONMENT_MAINNET, chain_key="robinhood", explorer_label="Robinhood Explorer",
        explorer_address_template="https://robinhoodchain.blockscout.com/address/{address}", fee_model=FEE_ARBITRUM_NITRO, finality=FINALITY_SAFE_TAG,
    ),
    "eip155:8453": ChainIdentity(
        network="eip155:8453", family=FAMILY_EVM, testnet=False, chain_id="8453", genesis_hash="",
        display_name="Base", legacy_names=(),
        rpc_origins=("https://mainnet.base.org",), schemes=(),
        explorer_tx_template="https://basescan.org/tx/{id}",
        assets=MappingProxyType({"ETH": _native("eip155:8453", "ETH", 18)}),
        environment=ENVIRONMENT_MAINNET, chain_key="base", explorer_label="Basescan",
        explorer_address_template="https://basescan.org/address/{address}", fee_model=FEE_OP_STACK, finality=FINALITY_SAFE_TAG,
    ),
    "eip155:1": ChainIdentity(
        network="eip155:1", family=FAMILY_EVM, testnet=False, chain_id="1", genesis_hash="",
        display_name="Ethereum", legacy_names=(),
        # ethereum.org publishes no official public RPC; one third-party origin, stated as such in the matrix
        rpc_origins=("https://ethereum-rpc.publicnode.com",), schemes=(),
        explorer_tx_template="https://etherscan.io/tx/{id}",
        assets=MappingProxyType({"ETH": _native("eip155:1", "ETH", 18)}),
        environment=ENVIRONMENT_MAINNET, chain_key="ethereum", explorer_label="Etherscan",
        explorer_address_template="https://etherscan.io/address/{address}", fee_model=FEE_EIP1559, finality=FINALITY_FINALIZED_TAG,
    ),
    "eip155:56": ChainIdentity(
        network="eip155:56", family=FAMILY_EVM, testnet=False, chain_id="56", genesis_hash="",
        display_name="BNB Smart Chain", legacy_names=(),
        rpc_origins=("https://bsc-dataseed.bnbchain.org", "https://bsc-dataseed-public.bnbchain.org"), schemes=(),
        explorer_tx_template="https://bscscan.com/tx/{id}",
        assets=MappingProxyType({"BNB": _native("eip155:56", "BNB", 18)}),
        environment=ENVIRONMENT_MAINNET, chain_key="bnb", explorer_label="BscScan",
        explorer_address_template="https://bscscan.com/address/{address}", fee_model=FEE_BSC, finality=FINALITY_FINALIZED_TAG,
    ),
}

#: Immutable view: mutation attempts raise TypeError at every caller class.
REGISTRY: Mapping[str, ChainIdentity] = MappingProxyType(_REGISTRY)

DECLARED_NETWORKS: tuple[str, ...] = tuple(_REGISTRY.keys())

EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
SVM_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
#: a Solana signature is 64 bytes of base58 (~87-88 chars); ids keep the base58 alphabet
SVM_TX_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,88}$")
EVM_TX_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")


# --- resolution --------------------------------------------------------------------------------------

def resolve_network(value: Any) -> ChainIdentity:
    """The ONLY door from a caller-supplied name to a network. Fail closed, typed."""
    if not isinstance(value, str):
        raise wallet_fault("wallet_not_found", authority=AUTHORITY, context={"reason": "caller_supplied_network_object", "type": type(value).__name__})
    name = value.strip()
    lower = name.lower()
    for spec in _REGISTRY.values():
        if name == spec.network or lower in spec.legacy_names:
            return spec
    raise wallet_fault("wallet_network_disabled", authority=AUTHORITY, context={"network": name[:80], "reason": "not_a_declared_network"})


def is_declared(value: Any) -> bool:
    try:
        resolve_network(value)
    except Exception:
        return False
    return True


def all_networks() -> tuple[ChainIdentity, ...]:
    return tuple(_REGISTRY.values())


def rows_for_environment(environment: str) -> tuple[ChainIdentity, ...]:
    """The rows of one environment in card order (Solana, Robinhood Chain, Base, Ethereum, BNB Smart Chain)."""
    rows = [spec for spec in _REGISTRY.values() if spec.environment == environment]
    return tuple(sorted(rows, key=lambda spec: CHAIN_KEY_ORDER.index(spec.chain_key) if spec.chain_key in CHAIN_KEY_ORDER else len(CHAIN_KEY_ORDER)))


def native_asset(network: Any) -> AssetSpec:
    spec = resolve_network(network)
    for asset in spec.assets.values():
        if asset.native:
            return asset
    raise wallet_fault("wallet_network_disabled", authority=AUTHORITY, context={"network": spec.network, "reason": "row_has_no_native_asset"})


def native_display_symbol(network: Any) -> str:
    spec = resolve_network(network)
    return spec.native_display_symbol or native_asset(spec.network).symbol


# --- assets ------------------------------------------------------------------------------------------

def asset_for(network: Any, symbol_or_address: str) -> AssetSpec:
    """The registered asset record for this chain, by symbol or by exact address. An asset
    address that belongs to another chain is a mismatch, not a match."""
    spec = resolve_network(network)
    wanted = str(symbol_or_address or "").strip()
    if not wanted:
        raise wallet_fault("wallet_not_found", authority=AUTHORITY, context={"network": spec.network, "reason": "asset_required"})
    exact = spec.assets.get(wanted.upper() if len(wanted) <= 12 else wanted)
    if exact is not None:
        return exact
    if spec.native_display_symbol and wanted.upper() == spec.native_display_symbol.upper():
        return native_asset(spec.network)
    needle = wanted.lower()
    if EVM_ADDRESS_RE.match(wanted) or SVM_ADDRESS_RE.match(wanted):
        for asset in spec.assets.values():
            if asset.address and asset.address.lower() == needle:
                return asset
        raise wallet_fault("wallet_network_disabled", authority=AUTHORITY, context={"network": spec.network, "reason": "asset_not_registered_on_chain", "asset": wanted[:64]})
    raise wallet_fault("wallet_network_disabled", authority=AUTHORITY, context={"network": spec.network, "reason": "asset_not_registered", "asset": wanted[:64]})


def destination_matches_family(network: Any, destination: str) -> bool:
    spec = resolve_network(network)
    if spec.is_evm:
        return bool(EVM_ADDRESS_RE.match(str(destination or "").strip()))
    return bool(SVM_ADDRESS_RE.match(str(destination or "").strip()))


# --- chain identity ----------------------------------------------------------------------------------

@dataclass(frozen=True)
class VerifiedChainIdentity:
    network: str
    chain_id: str
    method: str
    verified_at: float
    policy_version: str
    scope: str


_IDENTITY_CACHE: dict[tuple[str, str, str], VerifiedChainIdentity] = {}


def _expect_method(spec: ChainIdentity) -> str:
    return "eth_chainId" if spec.is_evm else "getGenesisHash"


def _identity_matches(spec: ChainIdentity, method: str, answer: Any) -> bool:
    text = str(answer or "").strip()
    if not text:
        return False
    if spec.is_evm:
        try:
            return int(text, 16) == int(spec.chain_id)
        except ValueError:
            return False
    return text == spec.genesis_hash


def endpoint_scope(prefix: str, url: str) -> str:
    """An identity-cache scope that names the endpoint: a proof of one endpoint never vouches for another.
    The path is part of the endpoint (one gateway origin can serve several chains under different paths),
    so it is folded in as a digest: a provider key carried in a path never sits in the cache key in clear."""
    import hashlib

    parts = urlsplit(str(url or ""))
    digest = hashlib.sha256(f"{_origin_of(url)}{parts.path or '/'}".encode()).hexdigest()[:16]
    return f"{prefix}@{_origin_of(url) or 'unknown-endpoint'}#{digest}"


def verify_chain_identity(
    spec: ChainIdentity,
    probe: Callable[[str], Any],
    *,
    scope: str = "default",
    policy_version: str = IDENTITY_POLICY_VERSION,
    force: bool = False,
) -> VerifiedChainIdentity:
    """Establish chain identity with ONE bounded read; cache it per (chain, scope, policy).
    A second verify with the same key makes no second call. A lying endpoint is typed."""
    method = _expect_method(spec)
    key = (spec.network, scope, policy_version)
    if not force:
        cached = _IDENTITY_CACHE.get(key)
        if cached is not None and cached.method == method:
            return cached
    try:
        answer = probe(method)
    except Exception as exc:
        raise wallet_fault("wallet_chain_identity_mismatch", authority=AUTHORITY, context={"network": spec.network, "reason": f"identity_probe_failed:{type(exc).__name__}"}) from None
    if not _identity_matches(spec, method, answer):
        raise wallet_fault(
            "wallet_chain_identity_mismatch", authority=AUTHORITY,
            context={"network": spec.network, "expected": spec.chain_id, "reason": "endpoint_identity_mismatch"},
        )
    verified = VerifiedChainIdentity(network=spec.network, chain_id=spec.chain_id, method=method, verified_at=time.time(), policy_version=policy_version, scope=str(scope))
    _IDENTITY_CACHE[key] = verified
    return verified


def invalidate_chain_identity(spec: ChainIdentity | None = None) -> None:
    """Drop cached identities (one chain, or all) so the next verify re-reads the chain."""
    if spec is None:
        _IDENTITY_CACHE.clear()
        return
    for key in [k for k in _IDENTITY_CACHE if k[0] == spec.network]:
        _IDENTITY_CACHE.pop(key, None)


def cached_chain_identity(spec: ChainIdentity, *, scope: str = "default", policy_version: str = IDENTITY_POLICY_VERSION) -> VerifiedChainIdentity | None:
    return _IDENTITY_CACHE.get((spec.network, scope, policy_version))


# --- RPC origin policy --------------------------------------------------------------------------------

def _origin_of(url: str) -> str:
    try:
        parts = urlsplit(str(url or ""))
        host = (parts.hostname or "").lower()
        if not host:
            return ""
        port = parts.port
        if port is None:
            return f"{parts.scheme.lower()}://{host}"
        return f"{parts.scheme.lower()}://{host}:{port}"
    except ValueError:
        return ""


#: Origins that serve no row whatever a configuration says, each with its recorded reason in
#: delivery/NETWORK-MATRIX.json: api.mainnet-beta.solana.com (excluded by the RPC rule),
#: rpc.sepolia.org (discontinued), bsc-testnet-rpc.publicnode.com (served another host's certificate),
#: sepolia.drpc.org (paid tier) and the binance.org Chapel seed (not the host the official page lists).
EXCLUDED_RPC_ORIGINS: frozenset[str] = frozenset({
    "https://api.mainnet-beta.solana.com",
    "https://rpc.sepolia.org",
    "https://bsc-testnet-rpc.publicnode.com",
    "https://sepolia.drpc.org",
    "https://data-seed-prebsc-1-s1.binance.org:8545",
})


def rpc_origin_excluded(url: str) -> bool:
    return _origin_of(url) in EXCLUDED_RPC_ORIGINS


def _declared_by_another_row(spec: ChainIdentity, origin: str) -> bool:
    return any(origin in {o.rstrip("/").lower() for o in other.rpc_origins} for other in _REGISTRY.values() if other.network != spec.network)


def _loopback_allowed() -> bool:
    import os

    return str(os.environ.get("VOOL_WALLET_X402_ALLOW_LOOPBACK") or "").strip().lower() in {"1", "true", "yes", "on"}


def _is_loopback_origin(url: str) -> bool:
    host = (urlsplit(str(url or "")).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".localhost")


def rpc_origin_allowed(spec: ChainIdentity, url: str) -> bool:
    """The approved-RPC-origin policy, per row: an origin the row declares; for the Solana devnet row,
    the operator's declared devnet endpoint (``VOOL_WALLET_TESTNET_RPC_URL``); loopback ONLY when the
    loopback switch is on (local validators and simulators). No other row inherits the devnet
    endpoint, and no word inside a URL decides anything: identity is proven separately."""
    clean = str(url or "").strip()
    if not clean:
        return False
    origin = _origin_of(clean)
    if not origin or not origin.startswith(("http://", "https://")) or origin in EXCLUDED_RPC_ORIGINS:
        return False
    if spec.network == SOLANA_DEVNET:
        from core.wallet import config

        operator = config.testnet_rpc_url()
        # the operator's devnet endpoint is theirs to declare, but never an origin another row declares
        if (clean == operator or origin == _origin_of(operator)) and not _declared_by_another_row(spec, origin):
            return True
    if _is_loopback_origin(clean):
        return _loopback_allowed()
    return origin in {o.rstrip("/").lower() for o in spec.rpc_origins}


def rpc_origin_allowed_or_refuse(spec: ChainIdentity, url: str) -> bool:
    if rpc_origin_allowed(spec, url):
        return True
    raise wallet_fault("wallet_outbound_refused", authority=AUTHORITY, context={"network": spec.network, "reason": "rpc_origin_not_approved", "host": (urlsplit(str(url or "")).hostname or "")[:80]})


def network_rpc_url(network: Any) -> str:
    """The endpoint for this chain: the operator's declared override, else (Solana devnet) the operator's
    devnet endpoint, else the row's first declared origin."""
    from core.wallet import config

    spec = resolve_network(network)
    override = config.network_rpc_override(spec.network)
    if override:
        if not rpc_origin_allowed(spec, override):
            raise wallet_fault("wallet_outbound_refused", authority=AUTHORITY, context={"network": spec.network, "reason": "rpc_override_origin_not_approved", "host": (urlsplit(override).hostname or "")[:80]})
        return override
    if spec.network == SOLANA_DEVNET:
        operator = config.testnet_rpc_url()
        if not rpc_origin_allowed(spec, operator):
            raise wallet_fault("wallet_outbound_refused", authority=AUTHORITY, context={"network": spec.network, "reason": "rpc_operator_origin_not_approved", "host": (urlsplit(operator).hostname or "")[:80]})
        return operator
    return spec.rpc_origins[0]


# --- x402 capability selection -------------------------------------------------------------------------

def select_declared_offer(
    offers: Sequence[Any],
    *,
    networks: set[str] | frozenset[str],
    assets: Mapping[str, set[str] | frozenset[str]],
    schemes: Sequence[str],
) -> Any:
    """The pure capability filter over ``accepts[]`` entries: EVERY entry is evaluated, the
    first admissible one in the offer's own order wins, and the function is deterministic —
    identical inputs pick the same entry. A mainnet or unknown entry is never selectable.
    Returns None (a reasoned refusal upstream) when nothing admissible exists."""
    scheme_set = frozenset(str(s) for s in schemes)
    for offer in offers:
        network = str(getattr(offer, "network", "") or "")
        scheme = str(getattr(offer, "scheme", "") or "")
        asset = str(getattr(offer, "asset", "") or "")
        if network not in networks or scheme not in scheme_set:
            continue
        if asset not in assets.get(network, frozenset()):
            continue
        return offer
    return None


# --- explorer links ------------------------------------------------------------------------------------

def explorer_tx_url(network: Any, tx_id: str) -> str:
    """Public explorer link for a PUBLIC transaction id only. Anything that is not the exact
    id shape of this chain refuses, so a secret can never ride the link builder."""
    spec = resolve_network(network)
    clean = str(tx_id or "").strip()
    matches = EVM_TX_RE.match(clean) if spec.is_evm else SVM_TX_RE.match(clean)
    if matches is None:
        raise wallet_fault("wallet_not_found", authority=AUTHORITY, context={"network": spec.network, "reason": "not_a_public_transaction_id"})
    return spec.explorer_tx_template.format(id=clean)


def explorer_address_url(network: Any, address: str) -> str:
    """Public explorer link for an account address of this row's family; any other shape refuses."""
    spec = resolve_network(network)
    clean = str(address or "").strip()
    if not destination_matches_family(spec.network, clean) or not spec.explorer_address_template:
        raise wallet_fault("wallet_not_found", authority=AUTHORITY, context={"network": spec.network, "reason": "not_a_public_address"})
    return spec.explorer_address_template.format(address=clean)


__all__ = [
    "CHAIN_KEY_LABELS",
    "CHAIN_KEY_ORDER",
    "DECLARED_NETWORKS",
    "ENVIRONMENT_MAINNET",
    "ENVIRONMENT_TESTNET",
    "EXCLUDED_RPC_ORIGINS",
    "FAMILY_EVM",
    "FAMILY_SVM",
    "FEE_ARBITRUM_NITRO",
    "FEE_BSC",
    "FEE_EIP1559",
    "FEE_OP_STACK",
    "FEE_SVM",
    "IDENTITY_POLICY_VERSION",
    "REGISTRY",
    "SCHEME_EXACT",
    "SOLANA_DEVNET",
    "SOLANA_MAINNET",
    "AssetSpec",
    "ChainIdentity",
    "VerifiedChainIdentity",
    "all_networks",
    "asset_for",
    "cached_chain_identity",
    "destination_matches_family",
    "endpoint_scope",
    "explorer_address_url",
    "explorer_tx_url",
    "invalidate_chain_identity",
    "is_declared",
    "native_asset",
    "native_display_symbol",
    "network_rpc_url",
    "resolve_network",
    "rows_for_environment",
    "rpc_origin_allowed",
    "rpc_origin_allowed_or_refuse",
    "rpc_origin_excluded",
    "select_declared_offer",
    "verify_chain_identity",
]
