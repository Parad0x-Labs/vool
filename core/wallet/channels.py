"""Payment channels: the ways of paying a paid request that the owner lets this wallet use.

A payment channel is one way a 402 is paid: who builds the payment and over which protocol. VOOL's own x402 client
is one; Solana pay-kit's x402 and MPP builders are two more; the x402 Foundation SDK, paying USDC on Base from a Crypto
Pilot wallet, is another. The owner turns each one on or off in Settings (Crypto,
Payment channels). Whatever the channel, the money authority is this wallet's and never the channel's: the cap, the
owner's approval, the hold against the spend limits, the one signature and the receipt.

A channel that is off pays nothing. A door refuses to park a new payment on it (:func:`require_origin_on`, called by
``proposals.propose_transaction``), and a payment parked before it was turned off is refused at every approval door
before the owner is asked, inside the claim transaction before anything is held, when a signing request is asked for,
and at submission before its signature leaves. The off switch does not end such a payment: it stays waiting for
approval and can be approved once the channel is on again. A signing request already handed to an external wallet
keeps its own expiry, and the reaper ends it as it ends any expired request.

The choice lives in the wallet's own store, one ``wallet_controls`` row per channel the owner set. A channel the owner
never set takes its declared default. Reading it does no network I/O and imports no payment library.
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from typing import Any

from core.wallet import chains, proposals
from core.wallet.security import wallet_fault
from core.wallet.store import connection, utcnow

AUTHORITY = "core.wallet.channels"

CHANNEL_X402 = "x402"
CHANNEL_PAYKIT_X402 = "paykit_x402"
CHANNEL_PAYKIT_MPP = "paykit_mpp"
CHANNEL_X402_SDK = "x402_sdk"

PROTOCOL_X402 = "x402"
PROTOCOL_MPP = "mpp"

REASON_AVAILABLE = "available"
REASON_PAYKIT_NOT_INSTALLED = "paykit_not_installed"
REASON_PYTHON_BELOW_3_11 = "python_below_3_11"
REASON_X402_SDK_NOT_INSTALLED = "x402_sdk_not_installed"

_ON = "on"
_OFF = "off"
_KEY_PREFIX = "channel."


@dataclass(frozen=True)
class PaymentChannel:
    channel_id: str
    label: str
    protocol: str
    built_by: str
    #: the proposal origins this channel's doors mint: a payment belongs to exactly one channel, by its origin
    origins: tuple[str, ...]
    #: what a channel the owner never set is: the channels that already paid before the choice existed stay on
    default_on: bool
    description: str
    #: the optional install this channel needs, named to the owner ("" when it ships with every install)
    needs: str = ""


CHANNELS: tuple[PaymentChannel, ...] = (
    PaymentChannel(
        channel_id=CHANNEL_X402, label="x402, built into VOOL", protocol=PROTOCOL_X402, built_by="VOOL",
        origins=(proposals.ORIGIN_X402,), default_on=True,
        description="VOOL's own x402 client: USDC offers signed with EIP-3009 on the EVM test networks, and VOOL's own Solana Devnet offers.",
    ),
    PaymentChannel(
        channel_id=CHANNEL_PAYKIT_X402, label="x402 on Solana, through Solana pay-kit", protocol=PROTOCOL_X402, built_by="Solana pay-kit (Solana Foundation)",
        origins=(proposals.ORIGIN_X402_PAYKIT,), default_on=True,
        description="Canonical Solana x402 offers, where the resource's fee payer cosigns and settles: pay-kit builds the payment, this wallet approves and signs it.",
        needs="pay extra",
    ),
    PaymentChannel(
        channel_id=CHANNEL_PAYKIT_MPP, label="MPP on Solana, through Solana pay-kit", protocol=PROTOCOL_MPP, built_by="Solana pay-kit (Solana Foundation)",
        origins=(proposals.ORIGIN_MPP_PAYKIT,), default_on=True,
        description="MPP charges on Solana (WWW-Authenticate: Payment): pay-kit builds the charge, this wallet approves and signs it.",
        needs="pay extra",
    ),
    PaymentChannel(
        channel_id=CHANNEL_X402_SDK, label="x402 on Base, through the x402 Foundation SDK", protocol=PROTOCOL_X402, built_by="x402 Foundation SDK",
        origins=(proposals.ORIGIN_X402_SDK,), default_on=True,
        description="USDC offers on Base, paid from a Crypto Pilot wallet: the SDK builds the EIP-3009 authorization, this wallet approves and signs it, and the site's facilitator settles it and pays the network fee.",
        needs="x402 extra",
    ),
)

_BY_ID: dict[str, PaymentChannel] = {channel.channel_id: channel for channel in CHANNELS}
_BY_ORIGIN: dict[str, PaymentChannel] = {origin: channel for channel in CHANNELS for origin in channel.origins}


def get(channel_id: str) -> PaymentChannel | None:
    return _BY_ID.get(str(channel_id or ""))


def for_origin(origin: str) -> PaymentChannel | None:
    """The channel a proposal of this origin pays through, or None for a payment no channel carries (a transfer the
    owner or a model asked for, a provider's own lane, a fee collection)."""
    return _BY_ORIGIN.get(str(origin or ""))


def _key(channel_id: str) -> str:
    return _KEY_PREFIX + channel_id


def _stored(conn: Any, channel_id: str) -> str | None:
    row = conn.execute("SELECT value FROM wallet_controls WHERE key = ?", (_key(channel_id),)).fetchone()
    return None if row is None else str(row[0] or "")


def _resolve(channel: PaymentChannel, stored: str | None) -> bool:
    # only the exact "on" the writer stores turns a channel on: an unreadable value is off, never a hopeful default
    return channel.default_on if stored is None else stored == _ON


def is_on(channel_id: str, *, conn: Any = None) -> bool:
    """The owner's choice. ``conn`` reads it inside the caller's transaction (a claim reads it under its own write
    lock, so a switch is either before the claim or after it, never between its check and its hold)."""
    channel = get(channel_id)
    if channel is None:
        return False
    if conn is not None:
        return _resolve(channel, _stored(conn, channel.channel_id))
    with connection() as own:
        return _resolve(channel, _stored(own, channel.channel_id))


def require_origin_on(origin: str, *, source_context: dict[str, Any] | None = None) -> None:
    """The gate every payment of a channel passes: refused typed, naming the channel, while the owner has it off. A
    payment no channel carries passes untouched."""
    channel = for_origin(origin)
    if channel is None or is_on(channel.channel_id):
        return
    raise wallet_fault(
        "wallet_payment_channel_off", authority=AUTHORITY,
        context={"reason": "payment_channel_off", "channel": channel.channel_id, "channel_label": channel.label},
        source_context=source_context,
    )


def set_on(channel_id: str, enabled: bool, *, source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """The owner's choice (Settings, Crypto, Payment channels). Touches nothing but the choice: a parked payment
    keeps its state and whatever it holds, and is refused at its next door while its channel is off."""
    from core.wallet import custody, receipts

    custody.require_enabled(source_context=source_context)
    channel = get(channel_id)
    if channel is None:
        raise wallet_fault(
            "wallet_not_found", authority=AUTHORITY,
            context={"reason": "unknown_payment_channel", "channel": str(channel_id or "")[:40], "channels": [c.channel_id for c in CHANNELS]},
            source_context=source_context,
        )
    wanted = _ON if enabled else _OFF
    with connection() as conn:
        before = _resolve(channel, _stored(conn, channel.channel_id))
        conn.execute(
            "INSERT INTO wallet_controls (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (_key(channel.channel_id), wanted, utcnow()),
        )
    receipts.journal_security_event("wallet_payment_channel_changed", {"channel": channel.channel_id, "from": _ON if before else _OFF, "to": wanted}, source_context=source_context)
    return view(channel)


def _availability(channel: PaymentChannel) -> tuple[bool, str]:
    """Whether this install can pay through the channel at all. Cheap and import-free: the pay-kit and SDK doors
    re-check with the library itself, and refuse typed, naming the extra, when it does not load."""
    if channel.needs == "x402 extra":
        try:
            found = importlib.util.find_spec("x402") is not None
        except (ImportError, ValueError):
            found = False
        return (True, REASON_AVAILABLE) if found else (False, REASON_X402_SDK_NOT_INSTALLED)
    if channel.needs != "pay extra":
        return True, REASON_AVAILABLE
    if sys.version_info < (3, 11):
        return False, REASON_PYTHON_BELOW_3_11
    try:
        found = importlib.util.find_spec("solana_pay_kit") is not None
    except (ImportError, ValueError):
        found = False
    return (True, REASON_AVAILABLE) if found else (False, REASON_PAYKIT_NOT_INSTALLED)


def _networks(channel: PaymentChannel) -> list[str]:
    """The rows this channel can pay on today, derived from the chain registry and the lanes, never a second list."""
    from core.wallet import capabilities

    if channel.channel_id == CHANNEL_X402:
        rows = [spec for spec in chains.all_networks() if chains.SCHEME_EXACT in spec.schemes]
    elif channel.channel_id == CHANNEL_X402_SDK:
        # EVM rows with a registered EIP-3009 token whose Crypto Pilot lane is ready (core.wallet.x402_sdk.lane_rows)
        from core.wallet import x402_sdk

        rows = x402_sdk.lane_rows()
    else:
        # the pay-kit lane pays on every Solana row whose transfer lane is ready: from a VOOL pocket wallet on a row no
        # Crypto Pilot lane owns, and from a Crypto Pilot wallet's sheet on a row whose Pilot lane is ready
        rows = [spec for spec in chains.all_networks() if spec.is_svm and capabilities.transfer_lane_ready(spec)]
    return [spec.network for spec in rows]


def view(channel: PaymentChannel, *, on: bool | None = None) -> dict[str, Any]:
    available, reason = _availability(channel)
    networks = _networks(channel)
    return {
        "channel_id": channel.channel_id, "label": channel.label, "protocol": channel.protocol, "built_by": channel.built_by,
        "description": channel.description, "on": is_on(channel.channel_id) if on is None else bool(on), "default_on": channel.default_on,
        "available": available, "reason": reason, "needs": channel.needs, "networks": networks,
        "network_names": [chains.resolve_network(network).display_name for network in networks],
    }


def status() -> list[dict[str, Any]]:
    """Every channel with the owner's choice, its availability on this install and the rows it pays on. One read of
    the store; no network I/O."""
    with connection() as conn:
        stored = {channel.channel_id: _stored(conn, channel.channel_id) for channel in CHANNELS}
    return [view(channel, on=_resolve(channel, stored[channel.channel_id])) for channel in CHANNELS]


__all__ = [
    "AUTHORITY",
    "CHANNELS",
    "CHANNEL_PAYKIT_MPP",
    "CHANNEL_PAYKIT_X402",
    "CHANNEL_X402",
    "CHANNEL_X402_SDK",
    "PaymentChannel",
    "for_origin",
    "get",
    "is_on",
    "require_origin_on",
    "set_on",
    "status",
    "view",
]
