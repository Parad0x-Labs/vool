"""Why a payment exists and how the service was found: the story a receipt keeps beside its chain facts.

The chain facts (transaction, amount, coin, network, fee, wallet, payee) live on the receipt and the transfer record;
what it paid for lives in :mod:`core.wallet.purpose`. This module keeps the rest of the answer to "what did my agent
spend on, and why": who asked (the owner in chat, the assistant from its own allowance, a named plugin), the owner's
own words on that turn, and how the service was reached (the owner's request, a web search and its query, a page the
assistant read, a plugin, an x402 endpoint a tool met). Recorded once per proposal, when it is parked, from what the
turn actually did (the tool trail the runtime hands over), never from the model's description of itself.

Read-only for everything else. Nothing here can approve, sign or move funds.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from core.wallet.store import connection, dumps, loads, utcnow

AUTHORITY = "core.wallet.payment_context"
#: the source-context key the tool loop fills with this turn's earlier tool steps (name + query or url only)
TRAIL_KEY = "wallet_turn_trail"
ASK_KEY = "wallet_ask_text"

BY_OWNER_CHAT = "you, in chat"
BY_ASSISTANT = "the assistant, from its own allowance"
BY_PLUGIN = "a plugin"
BY_HELPER = "a helper agent, from the assistant's allowance"

_MAX_ASK = 200
_MAX_QUERY = 120
_SEARCH_TOOLS = ("web.search", "web.research", "web_search", "search.web", "web.news")
_READ_TOOLS = ("web.fetch", "web.read", "browser.open", "browser.read", "web.browse", "web.open")

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS wallet_payment_context (
        proposal_id TEXT PRIMARY KEY, context_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
)


def _ensure(conn: Any) -> None:
    for statement in SCHEMA:
        conn.execute(statement)


def trail_from_steps(steps: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """The compact trail of a turn's earlier tool steps the wallet may keep: tool name, and its query or url only."""
    trail: list[dict[str, str]] = []
    for step in steps or []:
        if not isinstance(step, dict) or step.get("correction"):
            continue
        name = str(step.get("tool_name") or "")
        args = step.get("arguments") if isinstance(step.get("arguments"), dict) else {}
        entry = {"tool": name[:60], "ok": "1" if step.get("ok") else "0"}
        for key in ("query", "q", "url", "resource_url", "plugin", "addon_id"):
            if args.get(key):
                entry[key if key in ("query", "url") else ("query" if key == "q" else ("url" if key == "resource_url" else "plugin"))] = str(args[key])[:_MAX_QUERY]
        trail.append(entry)
    return trail[-12:]


def _found_via(trail: list[dict[str, str]], *, resource_url: str = "", plugin: str = "") -> dict[str, str]:
    for entry in trail:
        if entry.get("tool") in _SEARCH_TOOLS and entry.get("query"):
            return {"kind": "web_search", "text": f"a web search for “{entry['query']}”", "query": entry["query"]}
    for entry in trail:
        if entry.get("tool") in _READ_TOOLS and entry.get("url"):
            host = urlsplit(entry["url"]).hostname or entry["url"]
            return {"kind": "page", "text": f"a page the assistant read ({host})", "url": entry["url"]}
    if plugin:
        return {"kind": "plugin", "text": f"the plugin {plugin}", "plugin": plugin}
    if resource_url:
        host = urlsplit(resource_url).hostname or resource_url
        return {"kind": "x402_endpoint", "text": f"a paid endpoint ({host}) that asked for payment (x402)", "url": resource_url}
    return {"kind": "your_request", "text": "your own request"}


def capture(proposal_id: str, *, source_context: dict[str, Any] | None, by: str, resource_url: str = "", plugin: str = "", note: str = "") -> dict[str, Any]:
    """Keep why this proposal exists. The first record wins: a later door reaching the same proposal changes nothing."""
    context = source_context if isinstance(source_context, dict) else {}
    trail = context.get(TRAIL_KEY) if isinstance(context.get(TRAIL_KEY), list) else []
    ask = " ".join(str(context.get(ASK_KEY) or "").split())[:_MAX_ASK]
    record = {
        "by": by, "ask": ask, "found_via": _found_via(trail, resource_url=resource_url, plugin=plugin), "trail": trail,
        "session_id": str(context.get("session_id") or "")[:80], "plugin": plugin[:80], "resource_url": resource_url[:300], "note": note[:200],
    }
    if not proposal_id:
        return record
    try:
        with connection() as conn:
            _ensure(conn)
            conn.execute("INSERT OR IGNORE INTO wallet_payment_context (proposal_id, context_json, created_at) VALUES (?, ?, ?)",
                         (str(proposal_id), dumps(record), utcnow()))
    except Exception:
        return record  # the story is a note beside the payment: failing to keep it never stops or changes the payment
    return get(proposal_id) or record


def who_for_turn(source_context: dict[str, Any] | None) -> str:
    """Who asked, for a proposal parked from a chat turn: the owner, or a team helper writing in its own session."""
    try:
        from core.wallet import agent_pay

        return BY_HELPER if agent_pay.is_team_turn(source_context) else BY_OWNER_CHAT
    except Exception:
        return BY_OWNER_CHAT


def copy(from_proposal_id: str, to_proposal_id: str, *, note: str = "") -> None:
    """A proposal parked BECAUSE of another (the send a conversion was for): the same story, with a note."""
    found = get(from_proposal_id)
    if not found or not to_proposal_id:
        return
    try:
        with connection() as conn:
            _ensure(conn)
            conn.execute("INSERT OR IGNORE INTO wallet_payment_context (proposal_id, context_json, created_at) VALUES (?, ?, ?)",
                         (str(to_proposal_id), dumps({**found, "note": note[:200] or found.get("note", "")}), utcnow()))
    except Exception:
        return


def get(proposal_id: str) -> dict[str, Any] | None:
    try:
        with connection() as conn:
            _ensure(conn)
            row = conn.execute("SELECT context_json FROM wallet_payment_context WHERE proposal_id = ?", (str(proposal_id or ""),)).fetchone()
    except Exception:
        return None
    found = loads(row[0], {}) if row else {}
    return found or None


def get_on(conn: Any, proposal_id: str) -> dict[str, Any] | None:
    """:func:`get` on the caller's connection (a receipt being written inside its own transaction)."""
    try:
        _ensure(conn)
        row = conn.execute("SELECT context_json FROM wallet_payment_context WHERE proposal_id = ?", (str(proposal_id or ""),)).fetchone()
    except Exception:
        return None
    found = loads(row[0], {}) if row else {}
    return found or None


def receipt_block(found: dict[str, Any] | None) -> dict[str, Any]:
    """What a receipt keeps of the story: who asked, the owner's words, how the service was found (no full trail)."""
    if not found:
        return {}
    via = found.get("found_via") or {}
    return {"why": {"asked_by": str(found.get("by") or ""), "ask": str(found.get("ask") or ""), "found_via": str(via.get("kind") or ""),
                    "found_via_text": str(via.get("text") or ""), "query": str(via.get("query") or ""), "url": str(via.get("url") or found.get("resource_url") or ""),
                    "plugin": str(found.get("plugin") or ""), "note": str(found.get("note") or "")}}


def why_text(proposal_id: str) -> str:
    """One plain line: who asked, in what words, and how the service was found ('' when nothing was recorded)."""
    found = get(proposal_id)
    if not found:
        return ""
    parts = [f"asked by {found.get('by') or 'unknown'}"]
    if found.get("ask"):
        parts.append(f"your words: “{found['ask'][:120]}”")
    via = (found.get("found_via") or {}).get("text")
    if via:
        parts.append(f"found through {via}")
    if found.get("note"):
        parts.append(str(found["note"]))
    return "; ".join(parts)


__all__ = ["ASK_KEY", "BY_ASSISTANT", "BY_HELPER", "BY_OWNER_CHAT", "BY_PLUGIN", "TRAIL_KEY", "capture", "copy", "get", "get_on", "receipt_block", "trail_from_steps", "who_for_turn", "why_text"]
