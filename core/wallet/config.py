"""Wallet policy knobs: Crypto off by default; admission is the chain registry, never a word in a name."""
from __future__ import annotations

import os
from urllib.parse import urlsplit

from core.wallet import chains

WALLET_ENABLED_ENV = "VOOL_WALLET_ENABLED"
TESTNET_RPC_ENV = "VOOL_WALLET_TESTNET_RPC_URL"
X402_CAP_ENV = "VOOL_WALLET_X402_CAP_MINOR"
#: Operator-declared per-network RPC endpoints, JSON: {"eip155:84532": "https://...", ...}.
#: Declaring one is the operator's own allowlist act: a value whose origin the chain row
#: does not approve is refused at use, never silently rerouted.
NETWORK_RPC_URLS_ENV = "VOOL_WALLET_RPC_URLS"

NETWORK_SOLANA_DEVNET = "solana-devnet"  # the legacy name the finished devnet lane stores
NETWORK_SOLANA_DEVNET_CAIP2 = chains.SOLANA_DEVNET
NETWORK_SOLANA_MAINNET = chains.SOLANA_MAINNET
NETWORK_BASE_SEPOLIA = "eip155:84532"
NETWORK_ETHEREUM_SEPOLIA = "eip155:11155111"
NETWORK_BSC_TESTNET = "eip155:97"
#: The complete set of networks this build can ever talk to: exactly the declared rows of
#: core.wallet.chains, both environments. Derived, never a second list: adding a network is a
#: registry change reviewed as such.
ALLOWED_NETWORKS: tuple[str, ...] = chains.DECLARED_NETWORKS
DEFAULT_TESTNET_RPC_URL = "https://api.devnet.solana.com"
DEFAULT_X402_CAP_MINOR = 100_000

_TRUE = {"1", "true", "yes", "on"}


_FALSE = {"0", "false", "no", "off"}


def wallet_enabled() -> bool:
    """The Crypto Pilot switch: OFF by default, turned on in Settings, read on every call.

    The environment variable is the operator override: a truthy value forces the wallet on (tests, operator
    drives), an explicit false forces it off regardless of the saved preference. With the variable unset, the
    persisted preference decides -- so the switch in Settings takes effect without a restart.
    """
    raw = str(os.environ.get(WALLET_ENABLED_ENV) or "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    try:
        from core.user_preferences import load_preferences

        return bool(getattr(load_preferences(), "wallet_enabled", False))
    except Exception:
        return False


def enabled_generation() -> int:
    """How many times the Settings switch changed, as saved in the preference file (0 when absent or unreadable)."""
    try:
        from core.user_preferences import load_preferences

        return max(0, int(getattr(load_preferences(), "wallet_enabled_generation", 0) or 0))
    except Exception:
        return 0


def record_enabled_change() -> int:
    """The Settings writer calls this after it saved a changed switch: bumps the ``enabled`` control epoch."""
    from core.wallet import controls
    from core.wallet.store import connection

    with connection() as conn:
        return controls.bump(conn, "enabled")


def network_allowed(network: str) -> bool:
    """True only for a declared row (canonical CAIP-2 or a declared legacy name)."""
    name = str(network or "").strip()
    if not name:
        return False
    return chains.is_declared(name)


def mainnet_enabled() -> bool:
    """Whether new wallet effects currently run on Mainnet rows: Crypto is on and the active network
    environment is Mainnet. Kept as one function so every status surface reports the same fact."""
    if not wallet_enabled():
        return False
    try:
        from core.wallet import environment

        return environment.active_environment().environment == chains.ENVIRONMENT_MAINNET
    except Exception:
        return False


def testnet_rpc_url() -> str:
    return str(os.environ.get(TESTNET_RPC_ENV) or DEFAULT_TESTNET_RPC_URL).strip()


def network_rpc_override(network: str) -> str:
    """The operator's JSON-declared endpoint for one chain, or ""."""
    raw = str(os.environ.get(NETWORK_RPC_URLS_ENV) or "").strip()
    if not raw:
        return ""
    import json

    try:
        mapping = json.loads(raw)
    except ValueError:
        return ""
    if not isinstance(mapping, dict):
        return ""
    value = mapping.get(str(network))
    return str(value).strip() if isinstance(value, str) else ""


def rpc_url_allowed(url: str) -> bool:
    """A well-formed http(s) endpoint URL outside the matrix's excluded origins. Which row may use it is
    chains.rpc_origin_allowed's decision."""
    clean = str(url or "").strip()
    if not clean:
        return False
    try:
        parts = urlsplit(clean)
    except ValueError:
        return False
    return parts.scheme in {"http", "https"} and bool(parts.hostname) and not chains.rpc_origin_excluded(clean)


def x402_cap_minor() -> int:
    raw = str(os.environ.get(X402_CAP_ENV) or "").strip()
    try:
        value = int(raw) if raw else DEFAULT_X402_CAP_MINOR
    except ValueError:
        value = DEFAULT_X402_CAP_MINOR
    return max(0, value)


X402_MAX_WINDOW_ENV = "VOOL_WALLET_X402_MAX_WINDOW_SECONDS"
#: The ceiling on an EIP-3009 validity window, whatever the offer's maxTimeoutSeconds asks
#: for: the signed authorization is a bearer instrument submittable by anyone holding it,
#: so its live window is a LOCAL policy decision, never the remote server's.
DEFAULT_X402_MAX_WINDOW_SECONDS = 3600


def x402_max_window_seconds() -> int:
    raw = str(os.environ.get(X402_MAX_WINDOW_ENV) or "").strip()
    try:
        value = int(raw) if raw else DEFAULT_X402_MAX_WINDOW_SECONDS
    except ValueError:
        value = DEFAULT_X402_MAX_WINDOW_SECONDS
    return max(60, value)
