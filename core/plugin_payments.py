"""A plugin asks; VOOL pays from the Skills & plugins wallet, inside the limits the owner ticked for that plugin.

A plugin never holds a key, never signs and never approves. Its manifest may list ``paid_services``: https origins
inside its own listed websites (``core.plugin_manifest.paid_services``). A tool of that plugin may then end its result
with ``"observation": {"paid_fetch": {"url": "https://..."}}``. VOOL itself fetches that address, meets the service's
402, and pays only through ``core.wallet.addon_wallet.fetch_for_addon``: the plugin's own allowance on the Skills &
plugins wallet, its own counters, its declared services, the owner's own turn at this computer. Whatever the service
delivers comes back to the chat as the tool's text, still untrusted.

The same file lists, for the wallet's Settings, every installed and switched-on plugin that declares paid services.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

PAID_FETCH_KEY = "paid_fetch"
BODY_LIMIT = 64 * 1024

_REFUSALS = {
    "not_the_owners_turn": "Plugins pay only on your own turn at this computer. Nothing was paid.",
    "not_a_declared_service": "This plugin asked to pay a service it did not list. Nothing was paid.",
    "not_allowed": "This plugin is not allowed to pay yet. Tick it under Settings, Skills & plugins wallet. Nothing was paid.",
    "no_ready_wallet": "The Skills & plugins wallet is not set up on that network. Nothing was paid.",
    "network_off": "Network access is off right now. Nothing was paid.",
}


def requesters_from_plugins() -> list[dict[str, Any]]:
    """Every installed, verified and switched-on plugin that lists paid services. A plugin that cannot be read lists
    nothing."""
    from core import plugin_lifecycle as lifecycle
    from core.plugin_tools import PluginManifestError, _owner_disabled, load_manifest

    found: list[dict[str, Any]] = []
    for record in lifecycle.records():
        try:
            if _owner_disabled(record.plugin_id) or not lifecycle.is_available(record.plugin_id):
                continue
            plugin = load_manifest(Path(record.root) / ".codex-plugin" / "plugin.json")
        except (PluginManifestError, lifecycle.LifecycleError, OSError, ValueError, TypeError):
            continue
        services = list((plugin.vool or {}).get("paid_services") or [])
        if services and plugin.plugin_id == record.plugin_id:
            found.append({"kind": "plugin", "id": plugin.plugin_id, "name": str(plugin.vool.get("name") or plugin.plugin_id),
                          "services": services, "skills": [str(skill.get("name") or "") for skill in plugin.skills if isinstance(skill, dict)]})
    return found


def _network_off(context: dict[str, Any]) -> bool:
    from core.remote_fetch_policy import remote_fetch_forbidden
    from core.turn_prohibitions import FAMILY_TOOLS, FAMILY_WEB, conserved_request_prohibitions

    prohibitions = conserved_request_prohibitions(context)
    return bool(remote_fetch_forbidden() or context.get("allow_remote_fetch") is False
                or prohibitions.prohibits_family(FAMILY_WEB) or prohibitions.prohibits_family(FAMILY_TOOLS))


def requested_url(result: Any) -> str:
    ask = (getattr(result, "observation", None) or {}).get(PAID_FETCH_KEY)
    url = ask.get("url") if isinstance(ask, dict) else None
    return url.strip()[:2000] if isinstance(url, str) else ""


def settle(plugin_id: str, extension: dict[str, Any] | None, result: Any, context: dict[str, Any] | None) -> Any:
    """Carry out a plugin tool's paid-fetch ask, if it made one. Returns the result to hand back, never raises."""
    if not getattr(result, "ok", False) or PAID_FETCH_KEY not in (getattr(result, "observation", None) or {}):
        return result
    observation = {key: value for key, value in result.observation.items() if key != PAID_FETCH_KEY}
    url = requested_url(result)
    context = dict(context or {})
    try:
        from core.wallet import addon_wallet, allowances

        declared = {allowances.normalize_origin(str(entry)) for entry in (extension or {}).get("paid_services") or []}
        if not url or allowances.normalize_origin(url) not in declared - {""}:
            outcome = {"status": "not_paid", "reason": "not_a_declared_service", "body": b""}
        elif _network_off(context):
            outcome = {"status": "not_paid", "reason": "network_off", "body": b""}
        else:
            outcome = addon_wallet.fetch_for_addon(addon_wallet.addon_agent_id("plugin", plugin_id), url, source_context=context)
    except Exception as exc:  # the wallet decides; a fault there pays nothing and must not crash the turn
        outcome = {"status": "not_paid", "reason": type(exc).__name__, "body": b""}
    payment = outcome.get("payment") or {}
    summary = {"url": url, "status": str(outcome.get("status") or ""), "reason": str(outcome.get("reason") or ""),
               "http_status": outcome.get("http_status"), "paid": bool(payment.get("paid")),
               "decision": str(payment.get("decision") or ""), "proposal_id": str(outcome.get("proposal_id") or "")}
    body = outcome.get("body") or b""
    if summary["status"] == "delivered" and body:
        text = bytes(body[:BODY_LIMIT]).decode("utf-8", errors="replace")
        return replace(result, text=text, observation={**observation, PAID_FETCH_KEY: summary})
    note = _REFUSALS.get(summary["reason"]) or ("The payment waits for you in Settings. Nothing was paid yet."
                                                 if summary["decision"] == "ask_owner" else "Nothing was paid.")
    text = f"{result.text}\n\n{note}" if result.text else note
    return replace(result, text=text, observation={**observation, PAID_FETCH_KEY: summary})


__all__ = ["BODY_LIMIT", "PAID_FETCH_KEY", "requested_url", "requesters_from_plugins", "settle"]
