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


def networks_for_asset(asset: str, *, environment: str = "") -> set[str]:
    """The declared rows (of one environment) where this wallet can send the asset: each row's native coin, and the tokens
    the owner's own transfers may move there (``core.wallet.chains.USER_TRANSFER_TOKENS``, USDT on Solana). Any other
    token names no row here."""
    symbol = str(asset or "").strip().upper()
    if not symbol:
        return set()
    from core.wallet import chains

    out = set()
    for spec in chains.all_networks():
        if environment and spec.environment != environment:
            continue
        try:
            native = chains.native_asset(spec.network)
        except Exception:
            continue
        if symbol in {native.symbol.upper(), (spec.native_display_symbol or native.symbol).upper()} or any(token.symbol.upper() == symbol for token in chains.user_transfer_tokens(spec.network)):
            out.add(spec.network)
    return out


def _narrow_to_asset(resolution: resolver.Resolution, destination: str, *, asset: str, supported: set[str], chain: str, family: str,
                     environment: str) -> resolver.Resolution:
    """Several saved wallets: only those on a chain that carries the asset are choices. One left is that wallet; none left
    says so. A single saved wallet on a chain without the asset is not a destination for it either. Nothing is guessed."""
    from dataclasses import replace

    if resolution.status == resolver.STATUS_AMBIGUOUS_ENDPOINT:
        fitting = [c for c in resolution.choices if c.get("chain_network") in supported]
        if len(fitting) == len(resolution.choices):
            return resolution
        if len(fitting) == 1:
            return resolver.resolve(destination, kind=KIND_WALLET, network=fitting[0]["chain_network"], chain=chain, family=family, environment=environment)
        name = resolution.choices[0].get("display_name", "")
        if fitting:
            listed = "; ".join(f"{(c.get('label') + ' ') if c.get('label') else ''}{c['value']} on {c.get('network_display') or c['chain_network']}" for c in fitting)
            return replace(resolution, choices=tuple(fitting),
                           message=f"{name} has {len(fitting)} saved wallets that can take {asset.upper()}: {listed}. Which one?")
        return replace(resolution, status=resolver.STATUS_NO_ENDPOINT,
                       message=f"None of {name}'s saved wallets is on a chain where this wallet can send {asset.upper()}.")
    if resolution.status == resolver.STATUS_RESOLVED and resolution.snapshot and resolution.snapshot.get("resolution") == "contact":
        snapshot = resolution.snapshot
        if snapshot.get("chain_network") and snapshot["chain_network"] not in supported:
            where = snapshot.get("network_display") or snapshot["chain_network"]
            return replace(resolution, status=resolver.STATUS_NO_ENDPOINT, snapshot=None,
                           message=f"{snapshot.get('display_name', '')}'s saved wallet is on {where}, where this wallet cannot send {asset.upper()}.")
    return resolution


def resolve_wallet_recipient(destination: str, *, network: str = "", chain: str = "", asset: str = "", environment: str = "") -> RecipientOutcome:
    """A payment destination: an address given directly, or exactly one saved wallet address on a fitting network. When
    the request names an asset the wallet can send (a chain's own coin, or a token like USDT on the chains that carry
    it) but no network, only saved wallets on those chains count; any other asset is left to the wallet, which decides
    what it can send."""
    family = wallet_family_for_asset(asset, environment=environment)
    try:
        resolution = resolver.resolve(destination, kind=KIND_WALLET, network=network, chain=chain, family=family, environment=environment)
        supported = networks_for_asset(asset, environment=environment) if asset and not network else set()
        if supported:
            resolution = _narrow_to_asset(resolution, destination, asset=asset, supported=supported, chain=chain, family=family, environment=environment)
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


__all__ = ["REFUSAL_STATUS", "RecipientOutcome", "networks_for_asset", "resolve_email_recipients", "resolve_wallet_recipient", "verify_recipients", "wallet_family_for_asset"]
