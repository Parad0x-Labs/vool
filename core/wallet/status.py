"""The public-safe wallet status served to chat and API: custody, the declared networks,
per-account views, limits, pending approvals and the last receipt. Never a key, a phrase,
a PIN, or a card number."""
from __future__ import annotations

import time
from typing import Any

from core.wallet import chains, config, custody, limits, proposals, receipts
from core.wallet.redaction import redact_wallet_record

#: Soft warnings the surface shows beside the custody line. Operator decision (2026-09-07): the absence
#: of an independent external security audit is stated, never used as a gate -- users test on devnets or
#: mainnets at their own pace. One authority; every renderer reads this list from the status payload.
SOFT_WARNINGS: tuple[dict[str, str], ...] = (
    {
        "code": "no_external_audit",
        "text": (
            "Soft warning: this wallet and payment code has not been audited by an external party. "
            "Start on a devnet or testnet, and use small amounts first on any mainnet."
        ),
    },
)


def proposal_meaning(proposal: Any) -> dict[str, Any]:
    """What approving this proposal does, in one plain sentence -- from the proposal's typed fields and its x402
    binding when it has one. Never from a model; a failure to describe is itself a red meaning."""
    from core.wallet import meaning

    try:
        try:
            from core.wallet import x402

            binding = x402.binding_for_proposal(proposal.proposal_id)
        except Exception:
            binding = None
        return meaning.describe_proposal(proposal, binding=binding).to_dict()
    except Exception as exc:
        return meaning.cannot_explain(f"the proposal could not be described ({type(exc).__name__})").to_dict()


def _is_companion_collection(p: Any) -> bool:
    """A fee collection planned to ride a provider payment is approved WITH that payment: it never gets a card of its
    own (an unbound legacy collection proposal, if any, still shows)."""
    if p.origin != proposals.ORIGIN_DNA_FEE:
        return False
    try:
        from core.wallet import dna_fees

        view = dna_fees.receipt_view(p.proposal_id) or {}
        return bool(view.get("payment_proposal_id"))
    except Exception:
        return False


def _pending_row(p: Any) -> dict[str, Any]:
    from core.wallet import quotes, transfers

    row = {
        "proposal_id": p.proposal_id, "amount_minor": p.amount_minor, "asset": p.asset, "destination": p.destination, "origin": p.origin,
        "memo": p.memo, "created_at": p.created_at, "network": p.network, "state": p.state, "meaning": proposal_meaning(p),
    }
    # a pilot transfer is approved only on the sheet; the open quote id lets an open sheet notice it was replaced
    row["pilot_transfer"] = transfers.is_pilot_transfer(p)
    # who receives what and why, from the operation owner's typed records (never from the memo's wording)
    from core.wallet import purpose

    row["purpose"] = purpose.purpose_for(p)
    if p.origin == proposals.ORIGIN_DNA_FEE:
        # a fee collection says what it is: never a transfer the owner or a model asked for
        from core.wallet import dna_fees

        row["dna_fee_collection"] = dna_fees.receipt_view(p.proposal_id)
    elif p.origin == proposals.ORIGIN_USEPOD:
        # the collection of previously accrued fees planned to ride THIS payment's approval, when there is one
        from core.wallet import dna_fees

        try:
            row["companion_collection"] = dna_fees.companion_outcome(p.proposal_id)
        except Exception as exc:
            row["companion_collection"] = {"error": f"unreadable:{type(exc).__name__}"}
    open_quote = quotes.open_quote_for(p.proposal_id) if row["pilot_transfer"] else None
    row["open_quote_id"] = open_quote["quote_id"] if open_quote else ""
    row["quote_expires_at"] = open_quote["expires_at"] if open_quote else 0
    recipient = dict(getattr(p, "recipient", {}) or {})
    # who the destination was resolved to, beside (never instead of) the exact destination above
    row["recipient"] = {
        "saved": recipient.get("resolution") == "contact", "display_name": recipient.get("display_name", ""), "label": recipient.get("label", ""),
        "verification": recipient.get("verification", ""), "warning": recipient.get("warning", ""),
        # an exact-address destination that matches saved contacts names those contacts beside the
        # address (the resolver's own saved_matches, already persisted with the proposal)
        "saved_for": [
            str(match.get("contact_display_name") or "").strip()
            for match in (recipient.get("saved_matches") or [])
            if str(match.get("contact_display_name") or "").strip()
        ],
    } if recipient else {}
    return row


def wallet_status() -> dict[str, Any]:
    enabled = config.wallet_enabled()
    default = custody.default_wallet()
    status: dict[str, Any] = {
        "enabled": enabled, "custody_mode": "none", "network": default.network if default else chains.resolve_network(config.NETWORK_SOLANA_DEVNET).legacy_names[0], "mainnet_enabled": config.mainnet_enabled(),
        "signing_preference": custody.preferred_signing_mode(), "public_key": "", "label": "", "pending_approvals": 0, "pending": [],
        "last_receipt": None, "limits": {}, "x402_cap_minor": config.x402_cap_minor(), "generated_at_epoch": time.time(),
        "soft_warnings": [dict(item) for item in SOFT_WARNINGS],
    }
    if not enabled:
        # Off is a permission fact, never an existence fact: a wallet that exists stays visible as
        # existing (mode only, no keys), so the surface can say what enabling restores without
        # creating, unlocking or using anything. Reads and reconciliation stay available when off.
        status["has_wallet"] = default is not None
        if default is not None:
            status["custody_mode"] = default.mode
        try:
            status["last_receipt"] = receipts.last_receipt()
        except Exception:
            status["last_receipt"] = None
        return status
    try:
        # the reaper rides the status read the UI already polls: a crash or restart that
        # left an open signing request past its TTL is expired here, its hold released.
        from core.wallet.lifecycle import default_lifecycle

        default_lifecycle().reap_stale_signing_requests()
    except Exception:
        pass
    try:
        profile = default
    except Exception:
        profile = None
    if profile is not None:
        status.update({"custody_mode": profile.mode, "network": profile.network, "public_key": profile.public_key, "label": profile.label, "wallet_id": profile.wallet_id, "approval_method": getattr(profile, "approval_method", "pin") if profile.mode == custody.MODE_POCKET_SEALED else "", "limits": limits.load_limits(profile.wallet_id, "SOL").to_dict()})
    try:
        pending = proposals.list_proposals(state=proposals.STATE_PENDING_APPROVAL, limit=20)
        # an awaiting_signature proposal is still awaiting OPERATOR action (its request may
        # be resumable after a process/UI death): the restarted surface must show it
        pending += proposals.list_proposals(state=proposals.STATE_AWAITING_SIGNATURE, limit=20)
        pending = [p for p in pending if not _is_companion_collection(p)]
        status["pending_approvals"] = len(pending)
        status["pending"] = [_pending_row(p) for p in pending]
        status["last_receipt"] = receipts.last_receipt()
    except Exception:
        pass
    try:
        from core.wallet import transfers

        # Crypto Pilot transfers: what is in flight (a reloaded page shows it), and the newest receipts
        status["in_flight"] = transfers.in_flight()
        status["transfers"] = transfers.list_transfers(limit=10)
    except Exception:
        status["in_flight"], status["transfers"] = [], []
    try:
        # the multichain account list: chain-qualified, public-safe, every declared family
        status["accounts"] = []
        for entry in custody.list_wallets():
            spec = chains.resolve_network(entry["network"])
            status["accounts"].append({
                "wallet_id": entry["wallet_id"], "mode": entry["mode"], "network": entry["network"], "chain": spec.network,
                "family": spec.family, "testnet": spec.testnet, "public_key": entry["public_key"], "label": entry["label"], "approval_method": entry.get("approval_method", ""),
                # the durable setup state of a Crypto Pilot wallet (ready / awaiting_backup / backup_revealed / cancelled): one owner, both lists
                "setup_state": str(entry.get("setup_state") or "") or "ready", "seal_policy": str(entry.get("seal_policy") or ""),
            })
        status["declared_networks"] = [spec.network for spec in chains.all_networks()]
    except Exception:
        pass
    status["crypto_pilot"] = crypto_pilot_status()
    try:
        from core.wallet import dna_fees

        fees = dna_fees.status()
        # the page's collection-cost line reads collection.cost_bound_bps; the status embedding
        # dropped it, so the sheet could only say "the cost bound" instead of the true 1%
        # (first-ever served run of the correction, Goal 2 stage 2)
        status["dna_fees"] = {"policy": fees["policy"], "treasury": fees["treasury"], "identities": fees["identities"], "collect_min": fees["collect_min"], "collection": fees.get("collection")}
    except Exception as exc:
        status["dna_fees"] = {"error": f"unreadable:{type(exc).__name__}"}
    return redact_wallet_record(status)


def crypto_pilot_status() -> dict[str, Any]:
    """The Crypto Pilot surface: the active network environment and where it came from, every declared row
    with its accounts and typed capabilities. No network I/O, no writes (a derived environment is pinned
    only by the first wallet effect)."""
    from core.wallet import caller_binding, capabilities, environment

    state = environment.active_environment()
    try:
        wallets = custody.list_wallets()
    except Exception:
        wallets = []
    try:
        frozen = limits.is_frozen()
    except Exception:
        frozen = False
    from core.wallet import device_auth, pilot_custody

    try:
        device_available = bool(device_auth.current_authority().available())
    except Exception:
        device_available = False
    source_labels = {
        environment.SOURCE_OVERRIDE: "set by the operator environment (Test networks only; a process can never choose Mainnet)",
        environment.SOURCE_STORED: "chosen in Settings",
        environment.SOURCE_UPGRADE: "kept from an upgrade: existing test-network data",
        environment.SOURCE_FRESH: "the fresh-install default",
    }
    return {
        "active_environment": state.environment,
        "environment_source": state.source,
        "environment_source_label": source_labels.get(state.source, state.source),
        "environment_label": "Mainnet" if state.environment == chains.ENVIRONMENT_MAINNET else "Test networks",
        "environment_choices": [
            {"value": chains.ENVIRONMENT_TESTNET, "label": "Test networks", "note": "devnet and testnets; test funds have no monetary value"},
            {"value": chains.ENVIRONMENT_MAINNET, "label": "Mainnet", "note": "real funds; every row's real network"},
        ],
        "environment_note": "Choosing a network environment changes which rows new wallets, previews and transfers use. It moves no funds and changes no address: every account stays on the network it was created on.",
        "caller_binding": caller_binding.mode(),
        "frozen": frozen,
        "chain_order": list(chains.CHAIN_KEY_ORDER),
        "networks": capabilities.network_rows(active_environment=state.environment, wallets=wallets),
        "credential_policy": {
            "pin": {"min": pilot_custody.PIN_MIN, "max": pilot_custody.PIN_MAX, "text": f"{pilot_custody.PIN_MIN}–{pilot_custody.PIN_MAX} digits"},
            "password": {"min": custody._PASSWORD_MIN, "max": custody._PASSWORD_MAX, "text": f"{custody._PASSWORD_MIN}–{custody._PASSWORD_MAX} characters, not only digits"},
            "device": {"available": device_available, "text": "Touch ID or your Mac password" if device_available else "not available on this machine"},
        },
        "asset_support": {"native_coin_transfers": True, "token_transfers": False,
                          "note": "The Crypto Pilot sends each row's native coin (SOL, ETH, BNB). Tokens such as USDC are not sent by this wallet; a token balance that can be read is not a token that can be spent."},
        "pilot_notice": "A pilot feature: keep small test amounts, do not use it as a main wallet, and save the one-time backup offline.",
    }


__all__ = ["SOFT_WARNINGS", "proposal_meaning", "wallet_status"]
