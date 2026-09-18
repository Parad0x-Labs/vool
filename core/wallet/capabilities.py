"""Per-network capability: what each Crypto card may offer right now, and why not when it may not.

One authority for every surface (Settings cards, the approval sheet, the model's status tool): a
capability is offered only when the row belongs to the active environment, the stack it needs is present
and its lane exists end to end. Unavailable is always a typed reason, never a missing button that
silently calls an unsupported signer. Reading capabilities performs no network I/O and no write.
"""
from __future__ import annotations

from typing import Any

from core.wallet import chains

CAPABILITIES: tuple[str, ...] = ("list", "create", "backup", "balance", "quote", "sign", "send", "receipt")

REASON_ENVIRONMENT_INACTIVE = "environment_inactive"
REASON_EVM_DEPENDENCIES_MISSING = "evm_dependencies_missing"
REASON_PILOT_LANE_INCOMPLETE = "pilot_lane_incomplete"
REASON_PILOT_TRANSFER_NEEDS_QUOTE_APPROVAL = "pilot_transfer_needs_quote_approval"
REASON_NO_ACCOUNT = "no_account_on_this_network"
REASON_NO_READY_SIGNING_ACCOUNT = "no_ready_signing_account"
REASON_NO_SETUP_AWAITING_BACKUP = "no_setup_awaiting_backup"
REASON_STORAGE_CLASS_REFUSED = "storage_class_refused"

#: Lane readiness, staged with the implementation: create/backup need pilot custody (setup lifecycle),
#: balance/quote/sign/send need the quote, approval and dispatch lanes. A lane is marked ready only in
#: the commit that lands it together with its tests.
PILOT_CUSTODY_READY = True
#: The rows whose transfer lane is complete: a row joins only in the commit that carries its served drive
#: (chat request -> exact preview -> a wrong credential and a cancel send nothing -> one send -> the receipt).
#: No environment variable or runtime override adds a row.
PILOT_TRANSFER_READY_ROWS: frozenset[str] = frozenset({
    chains.SOLANA_DEVNET,  # served drive: tests/wallet/test_crypto_pilot_transfer_served.py (scripted model, simulated chain)
    # every declared row below: served matrix drive tests/wallet/test_crypto_pilot_matrix_served.py (scripted model,
    # loopback nodes answering as each row, real daemon); public testnet / live mainnet acceptance is a separate gate
    chains.SOLANA_MAINNET,
    "eip155:84532", "eip155:8453",  # Base Sepolia, Base
    "eip155:11155111", "eip155:1",  # Ethereum Sepolia, Ethereum
    "eip155:97", "eip155:56",  # BNB Smart Chain testnet, BNB Smart Chain
    "eip155:46630", "eip155:4663",  # Robinhood Chain testnet, Robinhood Chain
})


def pilot_transfer_ready(spec: chains.ChainIdentity) -> bool:
    return spec.network in PILOT_TRANSFER_READY_ROWS

MODE_LABELS = {
    "pocket_sealed": "VOOL wallet on this device",
    "external_signer": "Connected wallet",
    "watch_only": "Watch-only",
}


def is_pilot_row(spec: chains.ChainIdentity) -> bool:
    """A row whose transfers belong only to the Crypto Pilot lane: every Mainnet row, and Robinhood Chain in
    both environments (no earlier lane ever served it)."""
    return spec.is_mainnet or spec.chain_key == "robinhood"


def transfer_lane_ready(spec: chains.ChainIdentity) -> bool:
    """Whether a claim, signature or submission may proceed on this row at all."""
    return pilot_transfer_ready(spec) or not is_pilot_row(spec)


def _available() -> dict[str, Any]:
    return {"available": True, "reason": ""}


def _unavailable(reason: str) -> dict[str, Any]:
    return {"available": False, "reason": reason}


def _storage_is_ephemeral() -> bool:
    try:
        from network.signer import key_storage_mode

        return key_storage_mode() == "ephemeral"
    except Exception:
        return True  # an unreadable storage class never offers Create


def _evm_missing() -> bool:
    try:
        from core.wallet import evm

        return bool(evm.missing_evm_dependencies())
    except Exception:
        return True


def account_view(entry: dict[str, Any], spec: chains.ChainIdentity, *, shared_rows: list[str]) -> dict[str, Any]:
    mode = str(entry.get("mode") or "")
    setup_state = str(entry.get("setup_state") or "") or "ready"
    return {
        "wallet_id": entry.get("wallet_id", ""), "label": entry.get("label", ""), "address": entry.get("public_key", ""),
        "network": entry.get("network", ""), "row": spec.network, "mode": mode, "mode_label": MODE_LABELS.get(mode, mode),
        "setup_state": setup_state, "can_sign": mode != "watch_only", "approval_method": entry.get("approval_method", ""),
        "same_address_on_other_networks": sorted(shared_rows),
    }


def accounts_by_row(wallets: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Every registered account on the row it lives on; an address present on several rows is listed on
    each with the other rows named (same address, separate network and balance). Nothing is merged."""
    rows: dict[str, list[tuple[dict[str, Any], chains.ChainIdentity]]] = {}
    by_address: dict[str, set[str]] = {}
    for entry in wallets:
        try:
            spec = chains.resolve_network(entry.get("network", ""))
        except Exception:
            continue
        rows.setdefault(spec.network, []).append((entry, spec))
        address = str(entry.get("public_key") or "")
        by_address.setdefault(address.lower() if spec.is_evm else address, set()).add(spec.network)
    out: dict[str, list[dict[str, Any]]] = {}
    for network, items in rows.items():
        views = []
        for entry, spec in items:
            address = str(entry.get("public_key") or "")
            key = address.lower() if spec.is_evm else address
            views.append(account_view(entry, spec, shared_rows=[row for row in by_address.get(key, set()) if row != network]))
        out[network] = views
    return out


def row_capabilities(spec: chains.ChainIdentity, *, active_environment: str, accounts: list[dict[str, Any]], evm_missing: bool | None = None) -> dict[str, dict[str, Any]]:
    missing = _evm_missing() if evm_missing is None else bool(evm_missing)
    inactive = spec.environment != active_environment
    ready_signers = [a for a in accounts if a.get("can_sign") and a.get("setup_state") == "ready"]
    caps: dict[str, dict[str, Any]] = {"list": _available(), "receipt": _available()}

    def gated(*, lane_ready: bool, needs_account: bool, needs_signer: bool) -> dict[str, Any]:
        if inactive:
            return _unavailable(REASON_ENVIRONMENT_INACTIVE)
        if spec.is_evm and missing:
            return _unavailable(REASON_EVM_DEPENDENCIES_MISSING)
        if not lane_ready:
            return _unavailable(REASON_PILOT_LANE_INCOMPLETE)
        if needs_signer and not ready_signers:
            return _unavailable(REASON_NO_READY_SIGNING_ACCOUNT)
        if needs_account and not accounts:
            return _unavailable(REASON_NO_ACCOUNT)
        return _available()

    caps["create"] = gated(lane_ready=PILOT_CUSTODY_READY, needs_account=False, needs_signer=False)
    if caps["create"]["available"] and _storage_is_ephemeral():
        caps["create"] = _unavailable(REASON_STORAGE_CLASS_REFUSED)
    caps["backup"] = gated(lane_ready=PILOT_CUSTODY_READY, needs_account=False, needs_signer=False)
    if caps["backup"]["available"] and not any(a.get("setup_state") == "awaiting_backup" for a in accounts):
        caps["backup"] = _unavailable(REASON_NO_SETUP_AWAITING_BACKUP)
    caps["balance"] = gated(lane_ready=pilot_transfer_ready(spec), needs_account=True, needs_signer=False)
    for name in ("quote", "sign", "send"):
        caps[name] = gated(lane_ready=pilot_transfer_ready(spec), needs_account=True, needs_signer=True)
    return {name: caps[name] for name in CAPABILITIES}


def network_rows(*, active_environment: str, wallets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The status payload for every declared row, active environment first, card order within each."""
    from core.wallet import environment as environment_module

    accounts = accounts_by_row(wallets)
    missing = _evm_missing()
    ordered = [*chains.rows_for_environment(active_environment), *chains.rows_for_environment(
        chains.ENVIRONMENT_TESTNET if active_environment == chains.ENVIRONMENT_MAINNET else chains.ENVIRONMENT_MAINNET)]
    rows = []
    for spec in ordered:
        native = chains.native_asset(spec.network)
        row_accounts = accounts.get(spec.network, [])
        rows.append({
            "network": spec.network, "chain_key": spec.chain_key, "chain_label": chains.CHAIN_KEY_LABELS.get(spec.chain_key, spec.display_name),
            "display_name": spec.display_name, "environment": spec.environment, "family": spec.family, "active": spec.environment == active_environment,
            **environment_module.presentation(spec),
            "native_symbol": native.symbol, "native_display_symbol": spec.native_display_symbol or native.symbol, "native_decimals": native.decimals,
            "explorer_label": spec.explorer_label, "explorer_link_text": f"View on {spec.explorer_label}" if spec.explorer_label else "",
            "fee_model": spec.fee_model, "finality": spec.finality,
            "accounts": row_accounts,
            "capabilities": row_capabilities(spec, active_environment=active_environment, accounts=row_accounts, evm_missing=missing),
        })
    return rows


__all__ = [
    "CAPABILITIES",
    "MODE_LABELS",
    "PILOT_CUSTODY_READY",
    "PILOT_TRANSFER_READY_ROWS",
    "REASON_ENVIRONMENT_INACTIVE",
    "REASON_EVM_DEPENDENCIES_MISSING",
    "REASON_NO_ACCOUNT",
    "REASON_NO_READY_SIGNING_ACCOUNT",
    "REASON_PILOT_LANE_INCOMPLETE",
    "REASON_PILOT_TRANSFER_NEEDS_QUOTE_APPROVAL",
    "accounts_by_row",
    "network_rows",
    "pilot_transfer_ready",
    "row_capabilities",
]
