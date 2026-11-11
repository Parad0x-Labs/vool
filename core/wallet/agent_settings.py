"""What Settings shows and changes about agents that pay on their own (Settings, Crypto, Agent spending).

A read model and the owner's doors, nothing else: every rule lives where it is enforced (``core.wallet.allowances``,
``core.wallet.agent_wallets``, ``core.wallet.agent_pay``). Amounts cross this seam as the person types them ("0.05")
and are turned into whole minor units here with the asset's own decimals, so the page never does arithmetic on money.
"""
from __future__ import annotations

import dataclasses
import time
from typing import Any

from core.wallet import agent_pay, agent_wallets, allowances, amounts, chains, custody, environment, limits
from core.wallet.security import wallet_fault

AUTHORITY = "core.wallet.agent_settings"

#: The agents an owner can give a wallet and an allowance today, with the name the page shows.
AGENTS: dict[str, str] = {agent_pay.ASSISTANT_AGENT: "VOOL assistant"}

PERIODS: dict[str, int] = {"hour": allowances.HOUR, "day": allowances.DAY, "week": allowances.WEEK, "month": allowances.MONTH}
_PERIOD_NAMES = {seconds: name for name, seconds in PERIODS.items()}
DEFAULT_DAYS = 30
MAX_DAYS = int(allowances.MAX_LIFETIME_SECONDS // allowances.DAY)

_INVALID_TEXT = {
    "per payment must fit the window, and the window must fit the total": "One payment has to fit inside the period amount, and the period amount inside the total.",
    "window_seconds must be an hour, a day, a week or a month": "Choose an hour, a day, a week or a month as the period.",
    "expires_at must be in the future and at most 90 days away": "An allowance lasts between 1 and 90 days.",
    "an allowance names at least one service and at most 32": "Name at least one service the agent may pay (at most 32).",
    "a service must be an https address": "A service address has to start with https://.",
    "a service is listed twice": "The same service is listed twice.",
    "a service total must fit the allowance total": "A service's limit has to fit inside the total.",
    "a service recipient is not an address on this network": "A service's pay-to address is not an address on this network.",
    "helper amounts must be whole numbers": "The helper amounts could not be read.",
    "a helper's payment must fit its total, and its total must fit the allowance total": "A helper's payment has to fit inside the helper's total, and that inside the allowance total.",
    "a helper's payment must fit the allowance's payment limit": "A helper's payment cannot be more than the allowance's limit for one payment.",
}
_REFUSAL_TEXT = {
    "allowance_exists": "This agent already has an allowance for this coin on this wallet. Change that one instead.",
    "allowance_needs_the_agents_own_wallet": "An allowance is paid from the agent's own wallet. Create that wallet first.",
    "allowance_revoked": "This allowance was ended. Create a new one.",
    "allowance_not_paused": "This allowance is not paused.",
    "no_such_allowance": "This allowance no longer exists.",
    "agent_wallet_exists": "This agent already has a wallet on this network.",
    "no_owner_wallet_to_prove": "Create your own wallet first. An agent's wallet is created with your wallet's PIN or password.",
    "unknown_network": "Choose one of the listed networks.",
    "unknown_agent": "Choose one of the listed agents.",
    "not_an_agent_wallet": "That wallet is not an agent's wallet.",
    "network_not_active": "That network is not in the active network environment.",
}


class SettingsRefused(ValueError):
    """What the person typed cannot be saved; the message says why in their words."""


def _refuse(reason: str, *, source_context: dict[str, Any] | None, **context: Any) -> Exception:
    return wallet_fault("wallet_setup_state_invalid", authority=AUTHORITY, context={"reason": reason, **context}, source_context=source_context)


def refusal_text(reason: str, detail: str = "") -> str:
    """Plain words for a refusal reason from this package's agent doors; "" when there is none to add."""
    if reason == "invalid_allowance":
        return _INVALID_TEXT.get(str(detail), "Those limits cannot be saved as typed. Every amount has to be more than zero.")
    return _REFUSAL_TEXT.get(str(reason), "")


def _asset(network: str, symbol: str) -> chains.AssetSpec:
    spec = chains.resolve_network(network)
    found = spec.assets.get(str(symbol or "").strip().upper())
    if found is None:
        raise SettingsRefused("Choose one of the coins listed for this network.")
    return found


def _minor(value: Any, asset: chains.AssetSpec, what: str, *, optional: bool = False) -> int:
    text = str(value if value is not None else "").strip()
    if not text:
        if optional:
            return 0
        raise SettingsRefused(f"Type an amount for {what}.")
    try:
        minor = amounts.parse_human_amount(text, decimals=int(asset.decimals), symbol=asset.symbol)
    except Exception:
        raise SettingsRefused(f"The amount for {what} could not be read. Type a number such as 0.05.") from None
    if minor <= 0 and not optional:
        raise SettingsRefused(f"The amount for {what} has to be more than zero.")
    return int(minor)


def _whole(value: Any, what: str, *, default: int | None = None, least: int = 0, most: int = 10**9) -> int:
    text = str(value if value is not None else "").strip()
    if not text and default is not None:
        return default
    try:
        number = int(text)
    except (TypeError, ValueError):
        raise SettingsRefused(f"{what} has to be a whole number.") from None
    if not least <= number <= most:
        raise SettingsRefused(f"{what} has to be between {least} and {most}.")
    return number


def _human(minor: Any, asset: chains.AssetSpec | None) -> str:
    return amounts.format_minor(int(minor or 0), int(asset.decimals)) if asset is not None else str(int(minor or 0))


def _allowance_shown(view: dict[str, Any], *, now: float) -> dict[str, Any]:
    if view.get("unreadable"):
        return {"allowance_id": view["allowance_id"], "unreadable": True}
    try:
        asset: chains.AssetSpec | None = _asset(view["network"], view["asset"])
    except Exception:
        asset = None
    shown = {name: view[name] for name in ("allowance_id", "agent_id", "wallet_id", "network", "asset", "state", "pause_reason", "max_payments", "payments",
                                           "min_interval_seconds", "expires_at")}
    shown["period"] = _PERIOD_NAMES.get(int(view["window_seconds"]), "day")
    shown["days_left"] = max(0, int((float(view["expires_at"]) - now + allowances.DAY - 1) // allowances.DAY))
    for name, key in (("per_payment", "per_payment_minor"), ("period_amount", "window_minor"), ("period_used", "window_used_minor"), ("total", "total_minor"),
                      ("total_used", "total_used_minor")):
        shown[name] = _human(view[key], asset)
    helpers = allowances.helper_terms(view["allowance_id"])
    shown["helper_per_payment"] = _human(helpers[0], asset) if helpers else ""
    shown["helper_total"] = _human(helpers[1], asset) if helpers else ""
    shown["ask_above"] = _human(view["ask_above_minor"], asset) if int(view["ask_above_minor"] or 0) else ""
    shown["services"] = [{"address": service["origin"], "limit": _human(service["total_minor"], asset), "used": _human(service["used_minor"], asset),
                          "pay_to": service["pay_to"], "pinned": bool(service["pinned"])} for service in view["services"]]
    return shown


def _networks() -> list[dict[str, Any]]:
    """The rows of the active network environment an agent's wallet can be created on, with their coins."""
    from core.wallet import capabilities

    rows = []
    for spec in chains.rows_for_environment(environment.active_environment().environment):
        try:
            ready = bool(capabilities.pilot_transfer_ready(spec))
        except Exception:
            ready = False
        if not ready:
            continue
        coins = [{"symbol": asset.symbol, "native": bool(asset.native)} for asset in spec.assets.values()]
        # service payments are priced in a stable coin wherever the row has one: list those first
        coins.sort(key=lambda coin: (coin["native"], coin["symbol"]))
        rows.append({"network": spec.network, "name": spec.display_name, "coins": coins})
    return rows


def _waiting(wallet_ids: set[str]) -> list[dict[str, Any]]:
    from core.wallet import proposals

    rows = []
    for proposal in proposals.list_proposals(state=proposals.STATE_PENDING_APPROVAL, limit=50):
        if proposal.wallet_id not in wallet_ids:
            continue
        try:
            asset: chains.AssetSpec | None = _asset(proposal.network, proposal.asset)
        except Exception:
            asset = None
        rows.append({"proposal_id": proposal.proposal_id, "wallet_id": proposal.wallet_id, "amount": _human(proposal.amount_minor, asset), "asset": str(proposal.asset),
                     "to": str(proposal.destination), "created_at": str(proposal.created_at)})
    return rows


def _recent(limit: int = 20) -> list[dict[str, Any]]:
    rows = []
    for entry in allowances.decisions(limit=limit):
        view = allowances.allowance_view(entry["allowance_id"]) if entry["allowance_id"] else None
        asset = None
        if view and not view.get("unreadable"):
            try:
                asset = _asset(view["network"], view["asset"])
            except Exception:
                asset = None
        rows.append({"agent": AGENTS.get(entry["agent_id"], entry["agent_id"]), "service": entry["origin"], "paid": entry["decision"] == allowances.DECISION_PAY,
                     "amount": _human(entry["amount_minor"], asset) if asset is not None else "", "asset": str(view["asset"]) if view and asset is not None else "",
                     "why": agent_pay.explain(entry["reason"]), "at": float(entry["created_at"] or 0)})
    return rows


def overview(*, now: float | None = None) -> dict[str, Any]:
    """Everything the Agent spending block shows. Reads only; nothing here touches a key or the network."""
    moment = float(now if now is not None else time.time())
    owner = custody.owner_proof_wallet()
    wallets = agent_wallets.list_agent_wallets()
    names = {spec.network: spec.display_name for spec in chains.all_networks()}
    agents = []
    for agent_id, name in AGENTS.items():
        mine = [{**wallet, "network_name": names.get(wallet["network"], wallet["network"])} for wallet in wallets if wallet["agent_id"] == agent_id]
        agents.append({"agent_id": agent_id, "name": name, "wallets": mine,
                       "allowances": [_allowance_shown(view, now=moment) for view in allowances.list_allowances(agent_id=agent_id, now=moment) if not view.get("parent_id")]})
    try:
        frozen = bool(limits.is_frozen())
    except Exception:
        frozen = True
    return {
        "owner_wallet": owner is not None, "owner_wallet_label": (owner.label or "your first wallet") if owner else "",
        "owner_method": custody.shown_approval_method(""), "frozen": frozen, "agents": agents, "networks": _networks(),
        "waiting": _waiting({wallet["wallet_id"] for wallet in wallets}), "recent": _recent(), "periods": list(PERIODS), "default_days": DEFAULT_DAYS, "max_days": MAX_DAYS,
    }


def balances_of(wallet_id: str, *, source_context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """What an agent's wallet holds now, read from its network: its row's tokens first, then the native coin. Each
    answer is a number that was read or ``unavailable`` with its reason, never a zero that was not read."""
    from core.wallet import balances

    wallet = agent_wallets.agent_wallet(str(wallet_id or ""))
    if wallet is None:
        raise _refuse("not_an_agent_wallet", source_context=source_context)
    spec = chains.resolve_network(wallet["network"])
    tokens = tuple(symbol for symbol, asset in spec.assets.items() if not asset.native)
    rows = balances.read_token_balances(wallet["wallet_id"], symbols=tokens, source_context=source_context) if tokens else []
    rows.append(balances.read_balance(wallet["wallet_id"], source_context=source_context))
    return [{"asset": row["asset"], "read": row["state"] == balances.STATE_READ, "amount": row["balance_human"] if row["state"] == balances.STATE_READ else ""} for row in rows]


def create_wallet(body: dict[str, Any], *, source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create the agent's wallet and show its backup key once, on one proof of the owner."""
    agent_id = str(body.get("agent_id") or agent_pay.ASSISTANT_AGENT).strip().lower()
    if agent_id not in AGENTS:
        raise _refuse("unknown_agent", source_context=source_context)
    network = str(body.get("network") or "")
    if network not in {row["network"] for row in _networks()}:
        raise _refuse("network_not_active" if network else "unknown_network", source_context=source_context)
    credential = str(body.get("credential") or "")
    wallet = agent_wallets.create_agent_wallet(agent_id, network=network, credential=credential, source_context=source_context)
    backup = agent_wallets.reveal_backup(wallet["wallet_id"], credential=credential, source_context=source_context)
    return {"wallet": agent_wallets.agent_wallet(wallet["wallet_id"]), "backup": backup}


def show_backup(body: dict[str, Any], *, source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """The backup key of an agent wallet whose setup stopped before it was shown."""
    wallet_id = str(body.get("wallet_id") or "")
    if body.get("confirm_saved") is True:
        # the backup was shown and closed without confirming: the owner confirms it now, and it is not shown again
        return {"wallet": agent_wallets.confirm_backup_saved(wallet_id, credential=str(body.get("credential") or ""), source_context=source_context), "backup": None}
    backup = agent_wallets.reveal_backup(wallet_id, credential=str(body.get("credential") or ""), source_context=source_context)
    return {"wallet": agent_wallets.agent_wallet(wallet_id), "backup": backup}


def _spec_from(body: dict[str, Any], *, wallet: dict[str, Any], asset: chains.AssetSpec, now: float) -> allowances.AllowanceSpec:
    period = str(body.get("period") or "day").strip().lower()
    if period not in PERIODS:
        raise SettingsRefused("Choose an hour, a day, a week or a month as the period.")
    listed = body.get("services")
    if not isinstance(listed, list) or not listed:
        raise SettingsRefused("Name at least one service the agent may pay.")
    total = _minor(body.get("total"), asset, "the total")
    services = []
    for entry in listed[: allowances.MAX_SERVICES + 1]:
        entry = entry if isinstance(entry, dict) else {}
        address = str(entry.get("address") or "").strip()
        if not address:
            continue
        if "://" not in address:
            address = "https://" + address
        limit = _minor(entry.get("limit"), asset, "a service's limit", optional=True) or total
        services.append(allowances.ServiceRule(origin=address, total_minor=limit, pay_to=str(entry.get("pay_to") or "").strip()))
    if not services:
        raise SettingsRefused("Name at least one service the agent may pay.")
    days = _whole(body.get("days"), "The number of days", default=DEFAULT_DAYS, least=1, most=MAX_DAYS)
    return allowances.AllowanceSpec(
        agent_id=wallet["agent_id"], wallet_id=wallet["wallet_id"], network=wallet["network"], asset=asset.symbol,
        per_payment_minor=_minor(body.get("per_payment"), asset, "one payment"), window_seconds=PERIODS[period],
        window_minor=_minor(body.get("period_amount"), asset, "the period"), total_minor=total,
        max_payments=_whole(body.get("max_payments"), "The number of payments", default=100, least=1, most=100000),
        min_interval_seconds=_whole(body.get("min_gap_seconds"), "The pause between payments", default=0, least=0, most=allowances.DAY),
        expires_at=now + days * allowances.DAY, services=tuple(services), ask_above_minor=_minor(body.get("ask_above"), asset, "the ask-first amount", optional=True))


def save_allowance(body: dict[str, Any], *, now: float | None = None, source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create an allowance, or replace the terms of the one named by ``allowance_id``. Creating and loosening are the
    owner's act (their wallet's credential); tightening is saved as typed."""
    moment = float(now if now is not None else time.time())
    credential = str(body.get("credential") or "")
    allowance_id = str(body.get("allowance_id") or "")
    if allowance_id:
        current = allowances.allowance_view(allowance_id, now=moment)
        if current is None or current.get("unreadable"):
            raise _refuse("no_such_allowance", source_context=source_context)
        wallet = agent_wallets.agent_wallet(current["wallet_id"])
        asset_symbol = current["asset"]
    else:
        wallet = agent_wallets.agent_wallet(str(body.get("wallet_id") or ""))
        asset_symbol = str(body.get("asset") or "")
    if wallet is None:
        raise _refuse("not_an_agent_wallet", source_context=source_context)
    spec = _spec_from(body, wallet=wallet, asset=_asset(wallet["network"], asset_symbol), now=moment)
    if allowance_id:
        if "days" not in body or not str(body.get("days") or "").strip():
            # the end date stays where it was unless the person typed a new number of days
            spec = dataclasses.replace(spec, expires_at=float(current["expires_at"]))
        allowances.change_allowance(allowance_id, spec, credential=credential, now=moment, source_context=source_context)
    else:
        allowance_id = allowances.create_allowance(spec, credential=credential, now=moment, source_context=source_context)
    if "helper_per_payment" in body or "helper_total" in body:
        # what one helper agent may spend out of this allowance; both empty turns helpers off
        asset = _asset(wallet["network"], asset_symbol)
        per_helper = _minor(body.get("helper_per_payment"), asset, "one helper payment", optional=True)
        helper_total = _minor(body.get("helper_total"), asset, "a helper's total", optional=True)
        if bool(per_helper) != bool(helper_total):
            raise SettingsRefused("For helpers, type both amounts, or leave both empty to give helpers nothing.")
        held = allowances.helper_terms(allowance_id)
        if (held or (0, 0)) != (per_helper, helper_total):
            allowances.set_helper_terms(allowance_id, per_payment_minor=per_helper, total_minor=helper_total, credential=credential, source_context=source_context)
    return {"allowance": _allowance_shown(allowances.allowance_view(allowance_id, now=moment) or {"allowance_id": allowance_id, "unreadable": True}, now=moment)}


def set_state(body: dict[str, Any], *, source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Pause or end an allowance (never needs a credential), or let a paused one pay again (always does)."""
    allowance_id = str(body.get("allowance_id") or "")
    action = str(body.get("action") or "").strip().lower()
    if allowances.allowance_view(allowance_id) is None:
        raise _refuse("no_such_allowance", source_context=source_context)
    if action == "pause":
        allowances.pause_allowance(allowance_id, source_context=source_context)
    elif action == "resume":
        allowances.resume_allowance(allowance_id, credential=str(body.get("credential") or ""), source_context=source_context)
    elif action == "end":
        allowances.revoke_allowance(allowance_id, source_context=source_context)
    else:
        raise SettingsRefused("Choose pause, resume or end.")
    view = allowances.allowance_view(allowance_id)
    return {"allowance": _allowance_shown(view, now=time.time()) if view else None}


__all__ = ["AGENTS", "SettingsRefused", "balances_of", "create_wallet", "overview", "refusal_text", "save_allowance", "set_state", "show_backup"]
