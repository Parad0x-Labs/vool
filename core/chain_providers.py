"""Single source of truth for the blockchain-data APIs VOOL can bring-your-own-key to.

The chain-data twin of ``core/search_providers.py``: the settings list, the credential slots, the
key verification and the read-only chain tools all read this one table, so a provider fact lives in
one place.

A pasted key is the permission. Nothing here is contacted on a fresh install: a provider is asked
only after the operator saved its key (which verification proves), and only for the chain reads the
operator asked for in a chat. None of these providers can move money; the wallet's payment
endpoints (``core/wallet/chains.py``) are a separate, fixed table this module never touches.

Where the key rides each request is the provider's documented placement, because a wrong placement
is indistinguishable to the user from a bad key:

* Helius takes the key only as the ``api-key`` query parameter (helius.dev authentication docs: no
  header or Bearer form).
* Alchemy takes ``Authorization: Bearer <key>`` against ``https://<network>.g.alchemy.com/v2``
  (alchemy.com "How to make HTTP header-based API requests"), which keeps the key out of the URL.
* Solscan Pro API v2 takes the key in the ``token`` request header (pro-api.solscan.io docs).
* Etherscan API V2 takes the key only as the ``apikey`` query parameter, one key for every chain it
  serves, the chain chosen by ``chainid``.

A key that must ride the URL (Helius, Etherscan) is registered as an exact secret before any request
is built, so every ledger, receipt and error that could quote the URL scrubs it, and such a request
never follows a redirect.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

#: Credential-store slot prefix. Distinct from ``llm.cloud.`` and ``search.web.``.
_SLOT_PREFIX = "chain.data."

#: The chains a chain-data tool can be asked about, keyed the way ``core/wallet/chains.py`` names them
#: (``chain_key``). Mainnet only: these are read-only lookups of public chain data.
CHAIN_SOLANA = "solana"
CHAIN_ETHEREUM = "ethereum"
CHAIN_BASE = "base"
CHAIN_BNB = "bnb"
CHAIN_ROBINHOOD = "robinhood"
CHAINS: tuple[str, ...] = (CHAIN_SOLANA, CHAIN_ETHEREUM, CHAIN_BASE, CHAIN_BNB, CHAIN_ROBINHOOD)
EVM_CHAINS: tuple[str, ...] = (CHAIN_ETHEREUM, CHAIN_BASE, CHAIN_BNB, CHAIN_ROBINHOOD)

#: EVM chain ids (the same ids ``core/wallet/chains.py`` declares for these mainnets).
EVM_CHAIN_IDS: dict[str, str] = {CHAIN_ETHEREUM: "1", CHAIN_BASE: "8453", CHAIN_BNB: "56", CHAIN_ROBINHOOD: "4663"}

CHAIN_LABELS: dict[str, str] = {
    CHAIN_SOLANA: "Solana", CHAIN_ETHEREUM: "Ethereum", CHAIN_BASE: "Base",
    CHAIN_BNB: "BNB Smart Chain", CHAIN_ROBINHOOD: "Robinhood Chain",
}

#: Words a person uses for each chain, mapped to its key. Anything else is refused, never guessed.
CHAIN_ALIASES: dict[str, str] = {
    "solana": CHAIN_SOLANA, "sol": CHAIN_SOLANA, "svm": CHAIN_SOLANA,
    "ethereum": CHAIN_ETHEREUM, "eth": CHAIN_ETHEREUM, "mainnet": CHAIN_ETHEREUM, "ethereum mainnet": CHAIN_ETHEREUM,
    "base": CHAIN_BASE,
    "bnb": CHAIN_BNB, "bsc": CHAIN_BNB, "bnb smart chain": CHAIN_BNB, "bnb chain": CHAIN_BNB, "binance smart chain": CHAIN_BNB,
    "robinhood": CHAIN_ROBINHOOD, "robinhood chain": CHAIN_ROBINHOOD,
}

#: Native asset of each chain: (symbol, decimals).
NATIVE_ASSETS: dict[str, tuple[str, int]] = {
    CHAIN_SOLANA: ("SOL", 9), CHAIN_ETHEREUM: ("ETH", 18), CHAIN_BASE: ("ETH", 18),
    CHAIN_BNB: ("BNB", 18), CHAIN_ROBINHOOD: ("ETH", 18),
}

#: The read operations the chain tool offers. Every one is a read of public chain data.
OP_BALANCE = "balance"
OP_TOKENS = "tokens"
OP_HISTORY = "history"
OP_TOKEN_INFO = "token_info"
OP_HOLDERS = "holders"
OP_TRANSACTION = "transaction"
OPERATIONS: tuple[str, ...] = (OP_BALANCE, OP_TOKENS, OP_HISTORY, OP_TOKEN_INFO, OP_HOLDERS, OP_TRANSACTION)

#: Alchemy's documented network hosts for the chains this table serves.
ALCHEMY_NETWORKS: dict[str, str] = {
    CHAIN_SOLANA: "solana-mainnet", CHAIN_ETHEREUM: "eth-mainnet", CHAIN_BASE: "base-mainnet",
    CHAIN_BNB: "bnb-mainnet", CHAIN_ROBINHOOD: "robinhood-mainnet",
}

HELIUS_RPC_URL = "https://mainnet.helius-rpc.com/"
SOLSCAN_API_ORIGIN = "https://pro-api.solscan.io"
ETHERSCAN_API_URL = "https://api.etherscan.io/v2/api"

#: Wrapped SOL's mint: a token every Solscan plan can describe, used for the one verification read.
WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112"


def etherscan_body_verdict(payload: object) -> str:
    """Etherscan answers every request with HTTP 200; ``status`` "0" plus its ``result`` text names
    what went wrong. Only the texts below are read as a verdict about the key or the plan; any other
    "0" answer is not judged here (it then fails the documented-shape check, never verifies)."""
    if not isinstance(payload, dict) or str(payload.get("status")) != "0":
        return ""
    text = str(payload.get("result") or payload.get("message") or "").lower()
    if "invalid api key" in text or "missing/invalid api key" in text:
        return "invalid"
    if "rate limit" in text:
        return "rate_limited"
    if "free api access is not supported" in text or "upgrade your api plan" in text or "not supported for this chain" in text:
        return "unauthorized"
    return ""


def alchemy_url(chain: str) -> str:
    network = ALCHEMY_NETWORKS.get(chain, "")
    return f"https://{network}.g.alchemy.com/v2" if network else ""


@dataclass(frozen=True)
class ChainProviderConfig:
    provider_id: str
    label: str
    #: "query_token" (key in a named query parameter), "bearer", or "header" (key in ``auth_name``).
    auth_style: str
    auth_name: str = ""
    #: chain -> the operations this provider serves on it.
    coverage: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: The one verification read: a cheap request every working key answers.
    verify_url: str = ""
    verify_method: str = "GET"
    verify_query: dict[str, str] = field(default_factory=dict)
    verify_body: dict[str, object] = field(default_factory=dict)
    #: Where a genuine answer holds its data (an object).
    verify_response_path: tuple[str, ...] = ()
    #: Reads a key verdict from a 2xx body, for a provider that reports errors with HTTP 200.
    body_verdict: Callable[[object], str] | None = None
    signup_url: str = ""
    description: str = ""
    #: Guided setup steps shown in Settings, in order. Plain English; the i18n catalog may override.
    setup_steps: tuple[str, ...] = ()
    #: Extra facts shown under the steps (what this release does NOT use, plan limits).
    setup_notes: tuple[str, ...] = ()

    @property
    def credential_slot(self) -> str:
        return f"{_SLOT_PREFIX}{self.provider_id}"

    @property
    def registry_id(self) -> str:
        """The id the credential-intelligence registry knows this provider by."""
        return f"chain.{self.provider_id}"

    def serves(self, chain: str, op: str) -> bool:
        return op in self.coverage.get(chain, ())


_SOLANA_RPC_OPS = (OP_BALANCE, OP_TOKENS, OP_HISTORY, OP_TOKEN_INFO, OP_HOLDERS, OP_TRANSACTION)
_ALCHEMY_EVM_OPS = (OP_BALANCE, OP_TOKENS, OP_HISTORY, OP_TOKEN_INFO, OP_TRANSACTION)
_ETHERSCAN_OPS = (OP_BALANCE, OP_HISTORY, OP_TRANSACTION)

# Order matters: it is the order the settings list shows and the order a chain's providers are tried
# when the operator names none.
CHAIN_PROVIDERS: dict[str, ChainProviderConfig] = {
    "helius": ChainProviderConfig(
        provider_id="helius",
        label="Helius",
        auth_style="query_token",
        auth_name="api-key",
        coverage={CHAIN_SOLANA: _SOLANA_RPC_OPS},
        verify_url=HELIUS_RPC_URL,
        verify_method="POST",
        verify_body={"jsonrpc": "2.0", "id": 1, "method": "getLatestBlockhash", "params": []},
        verify_response_path=("result", "value"),
        signup_url="https://dashboard.helius.dev/",
        description="Solana balances, tokens, history and transactions.",
        setup_steps=(
            "Sign in at dashboard.helius.dev (the free plan works).",
            "Open API Keys and copy your key. It looks like 8 characters, a dash, then more groups of letters and numbers.",
            "Choose Helius in the list, paste the key, and press Save key. VOOL asks Helius once whether it accepts it.",
        ),
        setup_notes=(
            "VOOL uses your Helius key for Solana lookups in chat: balances, tokens, recent transactions, token info and largest holders.",
            "LaserStream and Yellowstone gRPC are live data streams on paid Helius plans. Chat lookups do not use them, and VOOL does not open a stream.",
        ),
    ),
    "alchemy": ChainProviderConfig(
        provider_id="alchemy",
        label="Alchemy",
        auth_style="bearer",
        coverage={
            CHAIN_SOLANA: _SOLANA_RPC_OPS,
            CHAIN_ETHEREUM: _ALCHEMY_EVM_OPS, CHAIN_BASE: _ALCHEMY_EVM_OPS,
            CHAIN_BNB: _ALCHEMY_EVM_OPS, CHAIN_ROBINHOOD: _ALCHEMY_EVM_OPS,
        },
        verify_url=alchemy_url(CHAIN_ETHEREUM),
        verify_method="POST",
        verify_body={"jsonrpc": "2.0", "id": 1, "method": "eth_getBlockByNumber", "params": ["latest", False]},
        verify_response_path=("result",),
        signup_url="https://dashboard.alchemy.com/",
        description="Ethereum, Base, BNB, Robinhood Chain and Solana lookups.",
        setup_steps=(
            "Sign in at dashboard.alchemy.com (the free plan works) and create an app, or open an existing one.",
            "In the app's Networks tab, turn on every chain you want to ask about: Ethereum Mainnet, Base Mainnet, BNB Smart Chain Mainnet, Robinhood Chain Mainnet and Solana Mainnet. Keep Ethereum Mainnet on: VOOL checks the key there.",
            "Copy the app's API key (not the whole URL), choose Alchemy in the list, paste it, and press Save key.",
        ),
        setup_notes=(
            "One Alchemy key covers every chain its app has turned on. A chain left off answers with a clear refusal in chat, never a guess.",
            "Alchemy has no top-holders list for EVM tokens, so holder questions on those chains need another provider.",
        ),
    ),
    "solscan": ChainProviderConfig(
        provider_id="solscan",
        label="Solscan",
        auth_style="header",
        auth_name="token",
        coverage={CHAIN_SOLANA: (OP_BALANCE, OP_HISTORY, OP_TOKEN_INFO, OP_HOLDERS, OP_TRANSACTION)},
        verify_url=f"{SOLSCAN_API_ORIGIN}/v2.0/token/meta",
        verify_query={"address": WRAPPED_SOL_MINT},
        verify_response_path=("data",),
        signup_url="https://solscan.io/apis",
        description="Solana token info, holders, history and transactions.",
        setup_steps=(
            "Sign in at solscan.io and open API Management in your profile menu.",
            "Generate a key and copy it. It is a long token that starts with eyJ.",
            "Choose Solscan in the list, paste the key, and press Save key.",
        ),
    ),
    "etherscan": ChainProviderConfig(
        provider_id="etherscan",
        label="Etherscan",
        auth_style="query_token",
        auth_name="apikey",
        coverage={CHAIN_ETHEREUM: _ETHERSCAN_OPS, CHAIN_BASE: _ETHERSCAN_OPS, CHAIN_BNB: _ETHERSCAN_OPS},
        verify_url=ETHERSCAN_API_URL,
        verify_query={"chainid": "1", "module": "stats", "action": "ethprice"},
        verify_response_path=("result",),
        body_verdict=etherscan_body_verdict,
        signup_url="https://etherscan.io/myapikey",
        description="Ethereum, Base and BNB balances, history and transactions.",
        setup_steps=(
            "Sign in at etherscan.io and open API Keys (etherscan.io/myapikey).",
            "Add a key and copy it. One Etherscan key works for every chain Etherscan serves.",
            "Choose Etherscan in the list, paste the key, and press Save key.",
        ),
        setup_notes=(
            "Etherscan's free plan may not cover Base or BNB Smart Chain. If it does not, chat says so and names the chain.",
        ),
    ),
}


def config_for(provider_id: str) -> ChainProviderConfig | None:
    text = str(provider_id or "").strip().lower()
    if text.startswith("chain."):
        text = text[len("chain."):]
    return CHAIN_PROVIDERS.get(text)


def slot_for(provider_id: str) -> str:
    cfg = config_for(provider_id)
    return cfg.credential_slot if cfg else ""


def provider_for_slot(slot: str) -> str:
    text = str(slot or "").strip()
    for cfg in CHAIN_PROVIDERS.values():
        if cfg.credential_slot == text:
            return cfg.provider_id
    return ""


def all_slots() -> tuple[str, ...]:
    return tuple(cfg.credential_slot for cfg in CHAIN_PROVIDERS.values())


def resolve_chain(value: object) -> str:
    """The chain key a person's word names, or "" when it names none this table serves."""
    text = " ".join(str(value or "").strip().lower().replace("_", " ").replace("-", " ").split())
    return CHAIN_ALIASES.get(text, "")


def providers_for(chain: str, op: str) -> tuple[ChainProviderConfig, ...]:
    return tuple(cfg for cfg in CHAIN_PROVIDERS.values() if cfg.serves(chain, op))


def setup_guide() -> list[dict[str, object]]:
    """Presentation rows for the Settings guide: no transport destination is read from here."""
    return [
        dict(provider=cfg.provider_id, label=cfg.label, slot=cfg.credential_slot, url=cfg.signup_url,
             description=cfg.description, steps=list(cfg.setup_steps), notes=list(cfg.setup_notes),
             chains=[CHAIN_LABELS[c] for c in CHAINS if c in cfg.coverage])
        for cfg in CHAIN_PROVIDERS.values()
    ]


__all__ = [
    "ALCHEMY_NETWORKS",
    "CHAINS",
    "CHAIN_LABELS",
    "CHAIN_PROVIDERS",
    "EVM_CHAINS",
    "EVM_CHAIN_IDS",
    "NATIVE_ASSETS",
    "OPERATIONS",
    "ChainProviderConfig",
    "alchemy_url",
    "all_slots",
    "config_for",
    "etherscan_body_verdict",
    "provider_for_slot",
    "providers_for",
    "resolve_chain",
    "setup_guide",
    "slot_for",
]
