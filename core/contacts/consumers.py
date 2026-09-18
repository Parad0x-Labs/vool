"""How consumers use Contacts: one resolution per recipient before a draft or proposal exists, one verification before
the consumer acts on an approval.

Each consumer keeps its own approval, sending, signing and receipts. This module only turns what a request named into
exact destinations (or a question), and says whether a destination bound earlier is still exactly what Contacts holds.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.contacts import resolver
from core.contacts.endpoints import KIND_EMAIL, KIND_WALLET

#: consumer-facing statuses for a request that cannot name one destination yet
REFUSAL_STATUS: dict[str, str] = {
    resolver.STATUS_AMBIGUOUS_CONTACT: "recipient_ambiguous",
    resolver.STATUS_AMBIGUOUS_ENDPOINT: "recipient_ambiguous",
    resolver.STATUS_NOT_FOUND: "recipient_not_found",
    resolver.STATUS_NO_ENDPOINT: "recipient_has_no_address",
    resolver.STATUS_INVALID: "invalid_recipient",
}


@dataclass(frozen=True)
class RecipientOutcome:
    ok: bool
    status: str
    message: str
    snapshots: tuple[dict[str, Any], ...] = ()
    resolution: dict[str, Any] | None = None


def resolve_email_recipients(entries: list[str]) -> RecipientOutcome:
    """Each entry (an address, ``Name <address>``, a saved name, alias, ``Name (label)`` or ``ep-`` id) to one address."""
    snapshots: list[dict[str, Any]] = []
    for entry in entries:
        try:
            resolution = resolver.resolve(entry, kind=KIND_EMAIL)
        except Exception as exc:
            return RecipientOutcome(False, "recipient_lookup_failed", f"Contacts could not be read to find '{entry}' ({type(exc).__name__}).")
        if not resolution.ok or not resolution.snapshot:
            return RecipientOutcome(False, REFUSAL_STATUS.get(resolution.status, "invalid_recipient"), resolution.message, resolution=resolution.as_dict())
        snapshots.append(dict(resolution.snapshot))
    return RecipientOutcome(True, "resolved", "", snapshots=tuple(snapshots))


def wallet_family_for_asset(asset: str, *, environment: str = "") -> str:
    """The address family an asset symbol implies on the declared rows (SOL -> svm), or '' when it does not decide."""
    symbol = str(asset or "").strip().upper()
    if not symbol:
        return ""
    from core.wallet import chains

    families = set()
    for spec in chains.all_networks():
        if environment and spec.environment != environment:
            continue
        try:
            native = chains.native_asset(spec.network)
        except Exception:
            continue
        if symbol in {native.symbol.upper(), (spec.native_display_symbol or native.symbol).upper()}:
            families.add(spec.family)
    return families.pop() if len(families) == 1 else ""


def resolve_wallet_recipient(destination: str, *, network: str = "", chain: str = "", asset: str = "", environment: str = "") -> RecipientOutcome:
    """A payment destination: an address given directly, or exactly one saved wallet address on a fitting network."""
    try:
        resolution = resolver.resolve(destination, kind=KIND_WALLET, network=network, chain=chain,
                                      family=wallet_family_for_asset(asset, environment=environment), environment=environment)
    except Exception as exc:
        return RecipientOutcome(False, "recipient_lookup_failed", f"Contacts could not be read to find '{destination}' ({type(exc).__name__}).")
    if not resolution.ok or not resolution.snapshot:
        return RecipientOutcome(False, REFUSAL_STATUS.get(resolution.status, "invalid_recipient"), resolution.message, resolution=resolution.as_dict())
    return RecipientOutcome(True, resolution.status, resolution.message, snapshots=(dict(resolution.snapshot),), resolution=resolution.as_dict())


def verify_recipients(snapshots: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None) -> RecipientOutcome:
    """Every bound saved destination is still current; a changed, removed or deleted one needs a new review."""
    for snapshot in snapshots or ():
        check = resolver.verify_snapshot(snapshot)
        if not check.ok:
            return RecipientOutcome(False, "recipient_changed", check.message, resolution=check.as_dict())
    return RecipientOutcome(True, "current", "")


__all__ = ["REFUSAL_STATUS", "RecipientOutcome", "resolve_email_recipients", "resolve_wallet_recipient", "verify_recipients", "wallet_family_for_asset"]
