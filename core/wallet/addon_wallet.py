"""The Skills & plugins wallet: one wallet of its own, and per plugin the owner's tick and limits.

It is an agent's own wallet (``core.wallet.agent_wallets``) held by the reserved agent ``addons``: created with the
owner's PIN or password, funded by the owner with an ordinary approved payment, and able to sign without the owner only
inside an allowance (``core.wallet.allowances``). What is new is who those allowances belong to: one per plugin (or
skill shipped inside a plugin) the owner ticked, each with its own limits -- most per payment, most per day (or hour,
week, month), most payments in that period, an optional ask-first amount and an end date -- and each limited to the
services that plugin declared it pays.

Separation, enforced where the money moves:

* The owner's own wallets never pay for a plugin here; the assistant's agent wallet never carries a plugin's allowance
  and this wallet never carries the assistant's (``allowances._carries``).
* A plugin spends only through its own row: its own counters, its own services, its own pinned recipients.
* Nothing is allowed until the owner ticks it. Unticking ends the row at once and needs no credential; ticking and
  raising are the owner's act.
* A payment happens only on the owner's own turn at this computer (``agent_pay.agent_for_turn``): a phone or remote
  turn, or a turn a team helper wrote, pays nothing from here.

Which plugins want to pay comes from the plugin system: every installed, switched-on plugin whose manifest lists
``paid_services`` (``core.plugin_payments``), plus any :func:`register_source`; a skill that is only instructions
runs no code and pays nothing on its own.
"""
from __future__ import annotations

import hashlib
import re
import threading
from collections.abc import Callable
from typing import Any

from core.wallet import agent_pay, agent_wallets, allowances
from core.wallet.security import wallet_fault

AUTHORITY = "core.wallet.addon_wallet"
ADDONS = allowances.ADDONS_AGENT
WALLET_NAME = "Skills & plugins"

KIND_PLUGIN = "plugin"
KIND_SKILL = "skill"

_SLUG = re.compile(r"[^a-z0-9_.\-]+")


def _installed_plugins() -> list[dict[str, Any]]:
    from core.plugin_payments import requesters_from_plugins

    return requesters_from_plugins()


_SOURCES: list[Callable[[], list[dict[str, Any]]]] = [_installed_plugins]
_SOURCES_LOCK = threading.Lock()


def _fault(reason: str, *, source_context: dict[str, Any] | None = None, **context: Any) -> Exception:
    return wallet_fault("wallet_setup_state_invalid", authority=AUTHORITY, context={"reason": reason, **context}, source_context=source_context)


def addon_agent_id(kind: str, ident: str) -> str:
    """The allowance identity of a plugin or skill: ``plugin:<id>`` / ``skill:<id>``, at most 64 characters."""
    prefix = KIND_SKILL if str(kind) == KIND_SKILL else KIND_PLUGIN
    raw = str(ident or "").strip().lower()
    slug = _SLUG.sub("-", raw).strip("-.") or "unnamed"
    clean = f"{prefix}:{slug}"
    if len(clean) > 64:
        clean = clean[:47] + "-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return clean


def register_source(source: Callable[[], list[dict[str, Any]]]) -> None:
    """Add a provider of requesters: a callable returning ``[{"kind", "id", "name", "services": [https origins],
    "skills": [names]}]`` for every installed, enabled package that declares it pays. Registered once per process."""
    with _SOURCES_LOCK:
        if source not in _SOURCES:
            _SOURCES.append(source)


def requesters() -> list[dict[str, Any]]:
    """Every plugin or skill that asks to pay from the Skills & plugins wallet, with the services it declared. A
    source that fails lists nothing (and so allows nothing new); it never lists a guess."""
    with _SOURCES_LOCK:
        sources = list(_SOURCES)
    found: dict[str, dict[str, Any]] = {}
    for source in sources:
        try:
            listed = source() or []
        except Exception:
            continue
        for entry in listed:
            if not isinstance(entry, dict):
                continue
            kind = KIND_SKILL if str(entry.get("kind") or "") == KIND_SKILL else KIND_PLUGIN
            agent = addon_agent_id(kind, str(entry.get("id") or ""))
            services = sorted({origin for origin in (allowances.normalize_origin(str(s)) for s in (entry.get("services") or [])) if origin})
            if not services or agent in found:
                continue
            found[agent] = {"addon_id": agent, "kind": kind, "id": str(entry.get("id") or ""), "name": str(entry.get("name") or entry.get("id") or "")[:120],
                            "services": services[: allowances.MAX_SERVICES], "skills": [str(s)[:120] for s in (entry.get("skills") or [])][:32]}
    return sorted(found.values(), key=lambda item: item["name"].lower())


def requester(addon_id: str) -> dict[str, Any] | None:
    return next((item for item in requesters() if item["addon_id"] == str(addon_id or "").strip().lower()), None)


def wallets() -> list[dict[str, Any]]:
    return [wallet for wallet in agent_wallets.list_agent_wallets() if wallet["agent_id"] == ADDONS]


def allowance_for(addon_id: str, *, now: float | None = None) -> dict[str, Any] | None:
    """The plugin's live allowance (not ended), or None when the owner has not ticked it."""
    rows = [view for view in allowances.list_allowances(agent_id=addon_id, now=now) if not view.get("parent_id")]
    return rows[0] if rows else None


def fetch_for_addon(addon_id: str, url: str, *, source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Fetch a paid address for a plugin, paying from the Skills & plugins wallet only what the plugin's own allowance
    permits. The plugin never sees a key and never signs; VOOL meets the service's 402, decides, pays and fetches.

    Returns ``status``: ``delivered`` (``body`` holds the resource), ``not_paid`` (``reason`` says why: not ticked,
    not a declared service, not the owner's turn, no ready wallet that fits, or the allowance said no -- in which
    case the payment waits for the owner in Settings), or the fetch's own outcome when nothing asked for payment."""
    from core.wallet import x402
    from core.wallet.errors import WalletFault

    agent = str(addon_id or "").strip().lower()
    if not allowances.is_addon_agent(agent):
        return {"status": "not_paid", "reason": "not_a_plugin", "body": b""}
    if agent_pay.agent_for_turn(source_context) != agent_pay.ASSISTANT_AGENT:
        # only the owner's own turn at this computer: a phone, remote or helper-written turn pays nothing from here
        return {"status": "not_paid", "reason": "not_the_owners_turn", "body": b""}
    origin = allowances.normalize_origin(url)
    declared = requester(agent)
    if declared is None or not origin or origin not in declared["services"]:
        return {"status": "not_paid", "reason": "not_a_declared_service", "body": b""}
    allowance = allowance_for(agent)
    if allowance is None or allowance.get("unreadable"):
        return {"status": "not_paid", "reason": "not_allowed", "body": b""}
    for wallet in wallets():
        if not wallet["ready"] or wallet["wallet_id"] != allowance["wallet_id"]:
            continue
        try:
            outcome = x402.fetch_paid_resource(url, wallet_id=wallet["wallet_id"], source_context=source_context)
        except WalletFault as exc:
            return {"status": "not_paid", "reason": str(exc.context.get("reason") or exc.code), "body": b""}
        answer: dict[str, Any] = {"status": outcome.status, "http_status": outcome.http_status, "proposal_id": outcome.proposal_id, "payment": None, "reason": "",
                                  "body": outcome.body if outcome.status == x402.OUTCOME_DELIVERED else b""}
        if outcome.status != x402.OUTCOME_PAYMENT_REQUIRED or not outcome.proposal_id:
            return answer
        from core.wallet import payment_context

        payment_context.capture(outcome.proposal_id, source_context=source_context, by=payment_context.BY_PLUGIN, plugin=agent, resource_url=url)
        payment = agent_pay.pay(outcome.proposal_id, agent_id=agent, source_context=source_context)
        answer["payment"] = payment
        if not payment["paid"]:
            answer.update(status="not_paid", reason=str(payment.get("reason") or "not_paid"))
            return answer
        if payment["delivered"] is not False:
            try:
                answer["body"] = x402.retry_paid_resource(outcome.proposal_id, source_context=source_context).body
            except WalletFault:
                answer["body"] = b""
        answer["status"] = x402.OUTCOME_DELIVERED if answer["body"] else answer["status"]
        return answer
    return {"status": "not_paid", "reason": "no_ready_wallet", "body": b""}


def name_of(agent_id: str) -> str:
    found = requester(agent_id)
    return found["name"] if found else str(agent_id)


__all__ = ["ADDONS", "KIND_PLUGIN", "KIND_SKILL", "WALLET_NAME", "addon_agent_id", "allowance_for", "fetch_for_addon", "name_of", "register_source", "requester",
           "requesters", "wallets"]
