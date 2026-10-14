"""Watch wallets and token prices from any chat, with alerts in the bell.

One vertical, ``market_watch``: the user says "watch this wallet <address>" or "tell me when
<token> goes above 0.5"; VOOL keeps the list, checks it while it runs, and posts a
notification in the centre the bell reads when a watched wallet moves or a price condition
is met. Nothing runs until the first watch is added.

Rules this holds:

* Read-only market data. Nothing here signs, pays, trades or moves money. A wallet watch
  reads history through the chain-data providers the operator already keyed
  (``core/chain_data``), a price watch reads the public pool data ``core/token_performance``
  reads. No new transport: every request goes through the existing outbound door, so Local
  Only and the effect ledger apply.
* The requirement is checked before anything is saved and on every poll. A wallet watch
  needs a chain-data key that serves history on that chain; a price watch needs web access
  on. Missing -> the watch is not saved (or, when lost later, stops) with the plain next
  step; a missing value is named, never guessed.
* One notification per event, deduplicated by the centre's own key. More than ten new
  transactions in one poll become one summary. A price condition fires once per crossing,
  then disarms; it re-arms only when the price is back past the threshold.
* Bounded by design: at most 50 watches, events kept 30 days, at most 60 calls per hour
  per provider, and exponential backoff after a rate limit or an outage. No watch -> no
  polling thread activity and no network at all.
"""
from __future__ import annotations

import contextlib
import json
import math
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from typing import Any

INTENTS = ("watch.add", "watch.list", "watch.remove", "watch.pause", "watch.resume")
SURFACE = "market"
CAPABILITY_ID = "market.watch"
CAPABILITY_CLAIM = "watch wallets and token prices and post a notification when they move"

MAX_WATCHES = 50
EVENT_RETENTION_DAYS = 30
WALLET_POLL_S = 5 * 60
PRICE_POLL_S = 60
TICK_S = 20.0
MAX_CALLS_PER_HOUR = 60
BACKOFF_BASE_S = 60.0
BACKOFF_CAP_S = 30 * 60.0
NEW_TX_SUMMARY_AT = 10
BASELINE_IDS_KEPT = 50
LABEL_CAP = 80

#: window -> (candle timeframe, aggregate, candle step seconds, window seconds). The
#: windows a price rule may name; candle sizes fine enough to see the move inside it.
WINDOWS: dict[str, tuple[str, int, int, int]] = {
    "5m": ("minute", 1, 60, 300),
    "1h": ("minute", 5, 300, 3600),
    "6h": ("hour", 1, 3600, 6 * 3600),
    "24h": ("hour", 1, 3600, 24 * 3600),
}

#: Why a watch could not be checked, as a code. ``last_error`` stores only one of these; Settings
#: words it through the ``vmw.err.<code>`` catalog keys, so no provider text reaches the card.
ERR_CODES = frozenset({
    "rate_limited", "provider_down", "unreachable", "bad_answer", "no_pool", "budget", "resting",
    "history_gap", "key_missing", "key_unreadable", "key_rejected", "quota_exhausted", "plan_refused",
    "local_only", "web_off",
})
#: Refusals that a re-read of the local requirement cannot clear (the key is stored but the
#: provider refuses it, or VOOL may not go online): the watch waits for the user's Resume.
HOLD_CODES = frozenset({"key_unreadable", "key_rejected", "quota_exhausted", "plan_refused", "local_only"})
_WALLET_SETUP_CODES = {
    "key_missing": "key_missing", "key_unreadable": "key_unreadable", "key_rejected": "key_rejected",
    "quota_exhausted": "quota_exhausted", "plan_refused": "plan_refused", "chain_not_enabled": "plan_refused",
    "refused": "local_only",
}
_BACKOFF_CODES = frozenset({"rate_limited", "provider_down", "provider_error", "timeout", "unreachable"})
_SETUP_WORDS = {
    "key_missing": "no saved key serves {chain} history any more. Add one in Settings → API Keys.",
    "key_unreadable": "VOOL could not read the saved key from this machine's key store. Check Settings → API Keys, "
                      "then resume the watch.",
    "key_rejected": "the saved key for {chain} history was refused. Replace it in Settings → API Keys, then resume "
                    "the watch.",
    "quota_exhausted": "the saved key for {chain} history has no quota left. Check the key's plan, then resume the "
                       "watch.",
    "plan_refused": "the saved key's plan does not include {chain} history. Check the plan or add another key, then "
                    "resume the watch.",
    "local_only": "VOOL is not allowed to go online right now (for example Local Only is on). Resume the watch when "
                  "it may go online again.",
}
FACTS_CAP = 2000


def _reason_code(code: str) -> str:
    """A provider or transport failure folded into one of ``ERR_CODES``; unknown is a bad answer."""
    code = str(code or "")
    if code in ERR_CODES:
        return code
    if code in ("provider_error",):
        return "provider_down"
    if code in ("timeout",):
        return "unreachable"
    if code == "not_found":
        return "no_pool"
    return "bad_answer"


_WEB_OFF_TEXT = ("Price watches read public trading data online, and web access is off. Turn on web "
                 "access in Settings to watch a price. Nothing was saved.")
REPLY_WATCH_PRIVATE = (
    "Private chat is on, so I won't save a watch either. "
    "Say \"turn recording back on\" first, then ask again and I'll start watching it."
)
_CAP_TEXT = f"That is already {MAX_WATCHES} watches; remove one first. Nothing was saved."


class WatchError(Exception):
    """A watch request that saved nothing. ``message`` is safe to show: no key, no path."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------- time and connections


def _utcnow() -> str:
    from core.time_authority import CLOCK

    return CLOCK.now_utc().isoformat()


def _epoch(value: Any) -> float | None:
    from datetime import datetime, timezone

    try:
        moment = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def _default_connection():
    from storage.db import get_connection

    return get_connection()


def _ensure_tables(get_connection_fn: Callable[[], Any] = _default_connection) -> None:
    conn = get_connection_fn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_watches (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                chain TEXT NOT NULL,
                subject TEXT NOT NULL,
                label TEXT NOT NULL,
                rule_json TEXT NOT NULL DEFAULT '{}',
                state TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                last_checked_at TEXT,
                last_seen_json TEXT,
                last_error TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_market_watches_subject ON market_watches(kind, chain, subject)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_watch_events (
                id TEXT PRIMARY KEY,
                watch_id TEXT NOT NULL,
                at TEXT NOT NULL,
                kind TEXT NOT NULL,
                facts_json TEXT NOT NULL DEFAULT '{}',
                notification_id TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_market_watch_events_watch ON market_watch_events(watch_id, at)")
        conn.commit()
    finally:
        conn.close()


def _decode(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _watch(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["rule"] = _decode(data.pop("rule_json", "{}"))
    data["last_seen"] = _decode(data.pop("last_seen_json", ""))
    return data


# --------------------------------------------------------------------------- requirements


def _wallet_requirement(chain: str) -> tuple[bool, str]:
    """A wallet watch needs a stored key that serves history on that chain."""
    from core import chain_providers as cp

    serving = cp.providers_for(chain, cp.OP_HISTORY)
    if not serving:
        return False, f"None of VOOL's chain-data providers offer transaction history on {cp.CHAIN_LABELS.get(chain, chain)}. Nothing was saved."
    stored = [cfg for cfg in serving if _stored(cfg)]
    if not stored:
        return False, (f"To watch a wallet on {cp.CHAIN_LABELS.get(chain, chain)}, add a {_names(serving)} key in "
                       "Settings → API Keys. Nothing was saved, and VOOL did not go online.")
    return True, ""


def _price_requirement(source_context: dict[str, Any] | None = None) -> tuple[bool, str]:
    from core import token_performance

    if token_performance._web_on(source_context):
        return True, ""
    return False, _WEB_OFF_TEXT


def _requirement_of(watch: dict[str, Any], source_context: dict[str, Any] | None = None) -> tuple[bool, str]:
    if watch["kind"] == "wallet":
        return _wallet_requirement(watch["chain"])
    return _price_requirement(source_context)


def _stored(cfg: Any) -> bool:
    from core.credential_store import has_credential

    try:
        return bool(has_credential(cfg.credential_slot))
    except Exception:
        return False


def _names(cfgs) -> str:
    labels = [c.label for c in cfgs]
    if len(labels) <= 1:
        return "".join(labels)
    return ", ".join(labels[:-1]) + " or " + labels[-1]


# --------------------------------------------------------------------------- rules


def _rule_of(args: dict[str, Any]) -> dict[str, Any]:
    """The price rule, exactly one shape, or the refusal that names what is wrong."""
    rule = args.get("rule") if isinstance(args.get("rule"), dict) else {}
    repeat = rule.get("repeat", args.get("repeat", False))
    repeat = bool(repeat)
    out: dict[str, Any] = {"repeat": repeat}
    if "above" in rule and "below" not in rule and "move_pct" not in rule:
        out["above"] = _positive_number(rule.get("above"), "the price to watch for must be a positive number")
    elif "below" in rule and "above" not in rule and "move_pct" not in rule:
        out["below"] = _positive_number(rule.get("below"), "the price to watch for must be a positive number")
    elif "move_pct" in rule and "above" not in rule and "below" not in rule:
        pct = _number(rule.get("move_pct"), "the percent to watch for must be a number")
        if not 0 < abs(pct) <= 100:
            raise WatchError("invalid_rule", "the percent to watch for must be between 0 and 100, with a sign for up or down")
        window = str(rule.get("window") or args.get("window") or "")
        if window not in WINDOWS:
            raise WatchError("invalid_rule", f"the window must be one of {', '.join(WINDOWS)} (for example 1h)")
        out["move_pct"] = pct
        out["window"] = window
    else:
        raise WatchError("invalid_rule", "say one thing to watch for: above a price, below a price, or a percent move with a window")
    return out


def _number(value: Any, complaint: str) -> float:
    if isinstance(value, bool):
        raise WatchError("invalid_rule", complaint)
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise WatchError("invalid_rule", complaint) from None
    if number != number or number in (float("inf"), float("-inf")):
        raise WatchError("invalid_rule", complaint)
    return number


def _positive_number(value: Any, complaint: str) -> float:
    number = _number(value, complaint)
    if number <= 0:
        raise WatchError("invalid_rule", complaint)
    return number


def rule_in_words(rule: dict[str, Any]) -> str:
    if "above" in rule:
        words = f"goes above {rule['above']:g}"
    elif "below" in rule:
        words = f"drops below {rule['below']:g}"
    else:
        move = float(rule.get("move_pct") or 0)
        words = f"{'rises' if move > 0 else 'drops'} {abs(move):g}% within {rule.get('window')}"
    return words + (" every time" if rule.get("repeat") else "")


# --------------------------------------------------------------------------- the store


def add_watch(args: dict[str, Any], *, source_context: dict[str, Any] | None = None,
              now_fn: Callable[[], str] = _utcnow,
              get_connection_fn: Callable[[], Any] = _default_connection) -> dict[str, Any]:
    from core import chain_providers as cp
    from core.chain_data import ChainDataError, _validate_subject

    kind = str(args.get("kind") or args.get("watch") or "").strip().lower()
    if kind in ("wallet", "wallets"):
        kind = "wallet"
    elif kind in ("price", "token"):
        kind = "price"
    elif kind:
        raise WatchError("invalid_kind", "say which kind to watch: wallet or price. Nothing was saved.")
    else:
        raise WatchError("missing_kind", "Say which kind to watch: wallet or price. Nothing was saved.")

    subject = str(args.get("subject") or args.get("address") or args.get("wallet") or args.get("token") or "").strip()
    if not subject:
        raise WatchError("missing_subject", "I need the address to watch. Nothing was saved.")

    named_chain = str(args.get("chain") or args.get("network") or "").strip()
    price_chains = _price_chains()
    if named_chain:
        chain = cp.resolve_chain(named_chain)
        if not chain:
            raise WatchError("unknown_chain", f"VOOL's watches cover {', '.join(cp.CHAIN_LABELS[c] for c in cp.CHAINS)}; {named_chain[:40]!r} is not one. Nothing was saved.")
        if kind == "price" and chain not in price_chains:
            raise WatchError("unknown_chain", f"Price watches cover {_price_chain_labels()}; {_chain_label(chain)} has no public pool prices. Nothing was saved.")
    else:
        from core.chain_data import _EVM_ADDRESS_RE, _SVM_ADDRESS_RE

        if _SVM_ADDRESS_RE.fullmatch(subject):
            chain = cp.CHAIN_SOLANA
        elif _EVM_ADDRESS_RE.fullmatch(subject):
            raise WatchError("chain_needed", "That address could be on Ethereum, Base, BNB Smart Chain or Robinhood Chain. Which chain is it on? Nothing was saved.")
        else:
            raise WatchError("bad_subject", "That is not a Solana or EVM address. Check it and ask again. Nothing was saved.")
        if kind == "price" and chain not in price_chains:
            raise WatchError("unknown_chain", f"Price watches cover {_price_chain_labels()}; {_chain_label(chain)} has no public pool prices. Nothing was saved.")

    try:
        subject = _validate_subject("history", chain, subject)
    except ChainDataError as exc:
        # the same validation the chain lookup uses, in the watch's own voice
        raise WatchError(exc.code, str(exc.message).replace("Nothing was looked up.", "Nothing was saved.")) from None

    rule: dict[str, Any] = {}
    if kind == "price":
        rule = _rule_of(args)
    ok, refusal = _requirement_of({"kind": kind, "chain": chain}, source_context)
    if not ok:
        raise WatchError("requirement_missing", refusal)

    label = str(args.get("label") or "").strip()[:LABEL_CAP] or _short(subject)
    _ensure_tables(get_connection_fn)
    conn = get_connection_fn()
    try:
        count = conn.execute("SELECT COUNT(*) FROM market_watches").fetchone()[0]
        existing = conn.execute(
            "SELECT * FROM market_watches WHERE kind = ? AND chain = ? AND subject = ?", (kind, chain, subject)
        ).fetchone()
        if existing is None and count >= MAX_WATCHES:
            raise WatchError("too_many", _CAP_TEXT)
        now = now_fn()
        if existing is not None:
            rule_json = json.dumps(rule, sort_keys=True)
            if rule_json != existing["rule_json"]:
                # A new rule starts fresh: the old rule's armed/disarmed state does not carry over.
                conn.execute("UPDATE market_watches SET last_seen_json = NULL WHERE id = ?", (existing["id"],))
            conn.execute(
                "UPDATE market_watches SET label = ?, rule_json = ?, state = 'active', last_error = '' WHERE id = ?",
                (label, rule_json, existing["id"]),
            )
            conn.commit()
            watch_id = existing["id"]
            created = False
        else:
            watch_id = "mw-" + uuid.uuid4().hex
            conn.execute(
                """
                INSERT INTO market_watches (id, kind, chain, subject, label, rule_json, state, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'active', ?)
                """,
                (watch_id, kind, chain, subject, label, json.dumps(rule, sort_keys=True), now),
            )
            conn.commit()
            created = True
    finally:
        conn.close()
    _purge_old_events(now_fn=now_fn, get_connection_fn=get_connection_fn)
    return {"ok": True, "created": created, "watch": load_watch(watch_id, get_connection_fn=get_connection_fn)}


def _price_chains() -> tuple[str, ...]:
    from core import token_performance

    networks = token_performance._NETWORKS
    return tuple(chain for chain in ("solana", "ethereum", "base", "bnb", "arbitrum") if chain in networks)


def _price_chain_labels() -> str:
    """The price-watch chains in the words the chat and Settings show them in."""
    return ", ".join(_chain_label(chain) for chain in _price_chains())


def _short(subject: str) -> str:
    return subject if len(subject) <= 12 else f"{subject[:4]}…{subject[-4:]}"


def load_watch(watch_id: str, *, get_connection_fn: Callable[[], Any] = _default_connection) -> dict[str, Any] | None:
    conn = get_connection_fn()
    try:
        row = conn.execute("SELECT * FROM market_watches WHERE id = ?", (str(watch_id or ""),)).fetchone()
    finally:
        conn.close()
    return _watch(row) if row is not None else None


def list_watches(*, get_connection_fn: Callable[[], Any] = _default_connection) -> list[dict[str, Any]]:
    _ensure_tables(get_connection_fn)
    conn = get_connection_fn()
    try:
        rows = conn.execute("SELECT * FROM market_watches ORDER BY created_at").fetchall()
    finally:
        conn.close()
    return [_watch(row) for row in rows]


def _find(args: dict[str, Any], *, get_connection_fn: Callable[[], Any]) -> dict[str, Any] | None:
    """A watch by its id, or by the subject (address) it watches."""
    conn = get_connection_fn()
    try:
        key = str(args.get("id") or args.get("watch_id") or "").strip()
        if key:
            row = conn.execute("SELECT * FROM market_watches WHERE id = ?", (key,)).fetchone()
            if row is not None:
                return _watch(row)
        subject = str(args.get("subject") or args.get("address") or args.get("wallet") or args.get("token") or "").strip()
        if subject:
            row = conn.execute("SELECT * FROM market_watches WHERE subject = ?", (subject,)).fetchone()
            if row is not None:
                return _watch(row)
        return None
    finally:
        conn.close()


def remove_watch(args: dict[str, Any], *, get_connection_fn: Callable[[], Any] = _default_connection) -> dict[str, Any]:
    _ensure_tables(get_connection_fn)
    watch = _find(args, get_connection_fn=get_connection_fn)
    if watch is None:
        raise WatchError("unknown_watch", "There is no such watch. Say 'what am I watching' to see the list. Nothing was removed.")
    conn = get_connection_fn()
    try:
        conn.execute("PRAGMA secure_delete=ON")
        conn.execute("DELETE FROM market_watch_events WHERE watch_id = ?", (watch["id"],))
        conn.execute("DELETE FROM market_watches WHERE id = ?", (watch["id"],))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "removed": watch["id"], "label": watch["label"]}


def set_state(args: dict[str, Any], state: str, *, get_connection_fn: Callable[[], Any] = _default_connection) -> dict[str, Any]:
    _ensure_tables(get_connection_fn)
    watch = _find(args, get_connection_fn=get_connection_fn)
    if watch is None:
        raise WatchError("unknown_watch", "There is no such watch. Say 'what am I watching' to see the list. Nothing was changed.")
    if watch["state"] == state:
        return {"ok": True, "watch": watch, "changed": False}
    conn = get_connection_fn()
    try:
        conn.execute("UPDATE market_watches SET state = ? WHERE id = ?", (state, watch["id"]))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "watch": load_watch(watch["id"], get_connection_fn=get_connection_fn), "changed": True}


def _purge_old_events(*, now_fn: Callable[[], str] = _utcnow,
                      get_connection_fn: Callable[[], Any] = _default_connection) -> int:
    from datetime import datetime, timedelta, timezone

    try:
        moment = datetime.fromisoformat(str(now_fn()).replace("Z", "+00:00"))
    except ValueError:
        return 0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    cutoff = (moment - timedelta(days=EVENT_RETENTION_DAYS)).isoformat()
    conn = get_connection_fn()
    try:
        conn.execute("PRAGMA secure_delete=ON")
        cursor = conn.execute("DELETE FROM market_watch_events WHERE at < ?", (cutoff,))
        conn.commit()
        return int(cursor.rowcount or 0)
    finally:
        conn.close()


def _record_event(watch: dict[str, Any], kind: str, facts: dict[str, Any], notification_id: str, *,
                  now_fn: Callable[[], str], get_connection_fn: Callable[[], Any]) -> None:
    conn = get_connection_fn()
    try:
        conn.execute(
            "INSERT INTO market_watch_events (id, watch_id, at, kind, facts_json, notification_id) VALUES (?, ?, ?, ?, ?, ?)",
            ("mwe-" + uuid.uuid4().hex, watch["id"], now_fn(), kind,
             _facts_json(facts), str(notification_id or "")),
        )
        conn.commit()
    finally:
        conn.close()


def _facts_json(facts: dict[str, Any], cap: int = FACTS_CAP) -> str:
    """The event's facts as JSON of at most ``cap`` characters, always valid JSON.

    Too long: lists lose items from the end, then long strings are shortened, then keys are
    dropped from the end; the cut is marked with ``"trimmed": true``.
    """
    text = json.dumps(facts, sort_keys=True, default=str)
    if len(text) <= cap:
        return text
    data: dict[str, Any] = json.loads(text)  # plain JSON types from here on
    data["trimmed"] = True
    for key in sorted(data):
        while isinstance(data[key], list) and data[key] and len(json.dumps(data, sort_keys=True)) > cap:
            data[key].pop()
    if len(json.dumps(data, sort_keys=True)) > cap:
        for key in sorted(data):
            if isinstance(data[key], str) and len(data[key]) > 80:
                data[key] = data[key][:80]
            elif isinstance(data[key], (dict, list)):
                inner = json.dumps(data[key], sort_keys=True)
                if len(inner) > 200:
                    data[key] = inner[:200]
    keys = [key for key in sorted(data) if key != "trimmed"]
    while keys and len(json.dumps(data, sort_keys=True)) > cap:
        data.pop(keys.pop())
    return json.dumps(data, sort_keys=True)


def _update_watch(watch_id: str, *, last_checked_at: str | None = None, last_seen: dict[str, Any] | None = None,
                  last_error: str | None = None, state: str | None = None,
                  get_connection_fn: Callable[[], Any] = _default_connection) -> None:
    conn = get_connection_fn()
    try:
        sets, values = ["last_checked_at = COALESCE(?, last_checked_at)"], [last_checked_at]
        if last_seen is not None:
            sets.append("last_seen_json = ?")
            values.append(json.dumps(last_seen, sort_keys=True, default=str))
        if last_error is not None:
            sets.append("last_error = ?")
            values.append(str(last_error)[:300])
        if state is not None:
            sets.append("state = ?")
            values.append(state)
        values.append(watch_id)
        conn.execute(f"UPDATE market_watches SET {', '.join(sets)} WHERE id = ?", values)
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- notifications


def _notify(watch: dict[str, Any], *, title: str, body: str, dedupe_key: str, payload: dict[str, Any] | None = None,
            now_fn: Callable[[], str] = _utcnow, get_connection_fn: Callable[[], Any] = _default_connection) -> str:
    """One item in the centre the bell reads. The key makes a replay add nothing."""
    from core.operator import notification_center

    item = notification_center.record_item(
        dedupe_key=dedupe_key, source_kind="market_watch", title=title, body=body,
        payload={"watch_id": watch["id"], "kind": watch["kind"], **(payload or {})},
        now_fn=now_fn, get_connection_fn=get_connection_fn,
    )
    if item.get("created"):
        notification_center.record_delivery(item["notification_id"], channel="in_app", state="recorded",
                                            now_fn=now_fn, get_connection_fn=get_connection_fn)
    return str(item.get("notification_id") or "")


# --------------------------------------------------------------------------- the tool contract


def market_watch_contracts() -> list[Any]:
    from core.runtime_tool_contracts import RuntimeToolContract

    rule_schema = ("rule: object for a price watch, one of {above: number} | {below: number} | "
                   "{move_pct: signed number, window: 5m|1h|6h|24h}; repeat: boolean optional (default false)")
    common = dict(
        tool_surface=SURFACE, capability_id=CAPABILITY_ID, capability_claim=CAPABILITY_CLAIM,
        supported=True, unsupported_reason="",
        # A watch list row in this runtime's own database; nothing outside the machine.
        # The reads the poller makes are the chain and price tools' own, already gated.
        side_effect_class="creative_state", approval_requirement="none",
        timeout_policy="local_only_default", retry_policy="none", artifact_emission="none",
        error_contract="returns_structured_error_result",
    )
    return [
        RuntimeToolContract(
            intent="watch.add",
            description=(
                "Start watching a wallet or a token price and get a notification when something happens. "
                "A wallet watch (kind wallet) needs the chain and the address, and a chain-data key that serves "
                "history on that chain. A price watch (kind price) needs the chain, the token's contract address "
                "and a rule: above a price, below a price, or a percent move within 5m, 1h, 6h or 24h; repeat "
                "alerts on every crossing. While VOOL runs it checks every 5 minutes (wallets) or every minute "
                "(prices) and posts one notification per event. Read-only market data: nothing is signed or sent."
            ),
            input_schema={
                "kind": "str: wallet | price",
                "chain": "str: solana | ethereum | base | bnb | arbitrum | robinhood (optional for a Solana address)",
                "subject": "str: the wallet address or token contract address to watch",
                "label": "str optional: a name for the notification (default: a short form of the address)",
                "rule": rule_schema,
            },
            output_schema={"watch": "object"},
            **common,
        ),
        RuntimeToolContract(
            intent="watch.list",
            description=("Every watch with its label, chain, rule in words, state (active, paused or needs_setup) "
                         "and when it was last checked. Use it for 'what am I watching'."),
            input_schema={},
            output_schema={"watches": "list"},
            **common,
        ),
        RuntimeToolContract(
            intent="watch.remove",
            description="Stop watching: delete one watch by id or by the address it watches. Its rows are deleted.",
            input_schema={"id": "str optional: the watch id from watch.list", "subject": "str optional: the watched address"},
            output_schema={"removed": "str"},
            **common,
        ),
        RuntimeToolContract(
            intent="watch.pause",
            description="Pause one watch (no checks, no notifications) by id or by the address it watches.",
            input_schema={"id": "str optional: the watch id from watch.list", "subject": "str optional: the watched address"},
            output_schema={"watch": "object"},
            **common,
        ),
        RuntimeToolContract(
            intent="watch.resume",
            description=("Resume a paused watch, or re-arm a watch that stopped because its requirement was missing "
                         "(its key removed, web access off). Re-checks the requirement first."),
            input_schema={"id": "str optional: the watch id from watch.list", "subject": "str optional: the watched address"},
            output_schema={"watch": "object"},
            **common,
        ),
    ]


def _list_text(watches: list[dict[str, Any]]) -> str:
    from core import chain_providers as cp

    if not watches:
        return ("Nothing is being watched. Ask in any chat: watch this wallet <address>, or tell me when <token> "
                "goes above 0.5.")
    lines = []
    for watch in watches:
        chain_label = cp.CHAIN_LABELS.get(watch["chain"], watch["chain"])
        if watch["kind"] == "price":
            what = f"{watch['label']} on {chain_label}: {rule_in_words(watch['rule'])}"
        else:
            what = f"{watch['label']} ({watch['subject']}) on {chain_label}: new transactions"
        line = f"- {what}, {watch['state'].replace('_', ' ')}"
        if watch.get("last_checked_at"):
            line += f", last checked {watch['last_checked_at'][:16].replace('T', ' ')} UTC"
        lines.append(line)
    return f"{len(watches)} watch(es):\n" + "\n".join(lines)


def run_tool(intent: str, arguments: dict[str, Any] | None, source_context: dict[str, Any] | None = None):
    from core.execution.models import _tool_observation
    from core.runtime_execution_tools import RuntimeExecutionResult

    def result(ok: bool, status: str, text: str, **details: Any):
        observation = _tool_observation(intent=intent, tool_surface=SURFACE, ok=ok, status=status)
        return RuntimeExecutionResult(handled=True, ok=ok, status=status, response_text=text,
                                      details={**details, "observation": observation})

    args = dict(arguments or {})
    if intent == "watch.add" and _turn_is_private(source_context):
        # Private chat (core.private_chat): nothing from a private chat is saved, a watch included.
        return result(False, "private_chat", REPLY_WATCH_PRIVATE)
    try:
        if intent == "watch.add":
            out = add_watch(args, source_context=source_context)
            watch = out["watch"]
            what = (f"{rule_in_words(watch['rule'])}" if watch["kind"] == "price" else "every new transaction")
            return result(True, "ok", f"{'Now watching' if out['created'] else 'Updated the watch on'} {watch['label']} "
                                      f"({watch['subject']}) on {_chain_label(watch['chain'])}: {what}. "
                                      "While VOOL runs, a notification comes when it happens.", watch=watch)
        if intent == "watch.list":
            watches = list_watches()
            return result(True, "ok", _list_text(watches), watches=watches)
        if intent == "watch.remove":
            out = remove_watch(args)
            return result(True, "ok", f"Stopped watching {out['label']}. Its rows were deleted.", removed=out["removed"])
        if intent == "watch.pause":
            out = set_state(args, "paused")
            return result(True, "ok", f"Paused the watch on {out['watch']['label']}. No checks while it is paused.")
        if intent == "watch.resume":
            watch = resume_watch(args)
            return result(True, "ok", f"Resumed the watch on {watch['label']} ({watch['state'].replace('_', ' ')}).")
    except WatchError as exc:
        return result(False, exc.code, exc.message)
    return result(False, "unsupported_intent", "That is not a watch tool.")


def _turn_is_private(source_context: dict[str, Any] | None) -> bool:
    """The private-chat check for this turn; an unreadable answer counts as private (fail closed)."""
    try:
        from core.private_chat import turn_is_private

        return bool(turn_is_private(source_context))
    except Exception:
        return True


def _chain_label(chain: str) -> str:
    from core import chain_providers as cp

    if chain == "arbitrum":  # a price-source chain the chain-data table does not name
        return "Arbitrum"
    return cp.CHAIN_LABELS.get(chain, chain)


def resume_watch(args: dict[str, Any], *, get_connection_fn: Callable[[], Any] = _default_connection) -> dict[str, Any]:
    """Resume re-checks the requirement: a watch that cannot run says so, not 'resumed'."""
    _ensure_tables(get_connection_fn)
    watch = _find(args, get_connection_fn=get_connection_fn)
    if watch is None:
        raise WatchError("unknown_watch", "There is no such watch. Say 'what am I watching' to see the list. Nothing was changed.")
    ok, refusal = _requirement_of(watch)
    if not ok:
        set_state(args, "needs_setup", get_connection_fn=get_connection_fn)
        raise WatchError("requirement_missing", refusal)
    out = set_state(args, "active", get_connection_fn=get_connection_fn)
    return out["watch"]


# --------------------------------------------------------------------------- polling


class _ProviderBudget:
    """Calls per provider in the last hour, and the backoff after a rate limit or an outage."""

    def __init__(self, max_calls: int = MAX_CALLS_PER_HOUR, window_s: float = 3600.0):
        self._max_calls = max_calls
        self._window_s = window_s
        self._calls: dict[str, deque[float]] = {}
        self._blocked_until: dict[str, float] = {}
        self._failures: dict[str, int] = {}
        self._lock = threading.Lock()

    def blocked_for(self, provider: str, now: float) -> float:
        with self._lock:
            return max(0.0, self._blocked_until.get(provider, 0.0) - now)

    def allow(self, provider: str, now: float) -> tuple[bool, str]:
        with self._lock:
            wait = self._blocked_until.get(provider, 0.0) - now
            if wait > 0:
                return False, "resting"
            calls = self._calls.setdefault(provider, deque())
            while calls and now - calls[0] > self._window_s:
                calls.popleft()
            if len(calls) >= self._max_calls:
                return False, "budget"
            return True, ""

    def allow_and_spend(self, provider: str, now: float) -> tuple[bool, str]:
        allowed, reason = self.allow(provider, now)
        if allowed:
            self.spent(provider, now)
        return allowed, reason

    def spent(self, provider: str, now: float) -> None:
        with self._lock:
            self._calls.setdefault(provider, deque()).append(now)

    def failed(self, provider: str, now: float) -> float:
        with self._lock:
            self._failures[provider] = self._failures.get(provider, 0) + 1
            backoff = min(BACKOFF_BASE_S * (2 ** (self._failures[provider] - 1)), BACKOFF_CAP_S)
            self._blocked_until[provider] = now + backoff
            return backoff

    def recovered(self, provider: str) -> None:
        with self._lock:
            self._failures.pop(provider, None)
            self._blocked_until.pop(provider, None)


class MarketWatchPoller:
    """Checks the watch list on a schedule. With no active watch it does nothing at all."""

    def __init__(self, *, tick_seconds: float = TICK_S, get_connection_fn: Callable[[], Any] = _default_connection,
                 now_fn: Callable[[], str] = _utcnow, sleep_fn: Callable[[float], None] | None = None,
                 wallet_interval_s: float = WALLET_POLL_S, price_interval_s: float = PRICE_POLL_S) -> None:
        self._tick = max(1.0, float(tick_seconds))
        self._get_connection_fn = get_connection_fn
        self._now_fn = now_fn
        self._sleep_fn = sleep_fn
        self._wallet_interval_s = float(wallet_interval_s)
        self._price_interval_s = float(price_interval_s)
        self._budget = _ProviderBudget()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._seen_this_process: set[str] = set()
        self._price_chain_read: dict[str, float] = {}
        self._last_purge: float | None = None

    # -- one sweep, separately callable so tests use the SAME code ------------------------

    def _active_watches(self) -> list[dict[str, Any]] | None:
        """The watches to consider, read only. None when the table was never created."""
        conn = self._get_connection_fn()
        try:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'market_watches'"
            ).fetchone()
            if exists is None:
                return None
            rows = conn.execute(
                "SELECT * FROM market_watches WHERE state != 'paused' ORDER BY created_at"
            ).fetchall()
        finally:
            conn.close()
        return [_watch(row) for row in rows]

    def _purge_hourly(self, now_epoch: float) -> None:
        if self._last_purge is not None and now_epoch - self._last_purge < 3600.0:
            return
        self._last_purge = now_epoch
        _purge_old_events(now_fn=self._now_fn, get_connection_fn=self._get_connection_fn)

    def sweep_once(self, *, source_context: dict[str, Any] | None = None) -> dict[str, Any]:
        """One pass over due watches. Never raises; a failure is recorded on its watch.

        A fresh install (no watch table) and a list with nothing active read one row of the
        schema and stop: no table is created, nothing is written, nothing goes online."""
        outcome: dict[str, Any] = {"checked": 0, "events": 0, "needs_setup": 0, "skipped": 0, "errors": 0}
        try:
            watches = self._active_watches()
            if not watches:
                return outcome  # nothing watched: no write, no requirement read, no network
            now_epoch = _epoch(self._now_fn()) or time.time()
            self._purge_hourly(now_epoch)
        except Exception:
            outcome["errors"] += 1
            return outcome

        def last_checked(watch: dict[str, Any]) -> float:
            return _epoch(watch.get("last_checked_at")) or 0.0

        due = []
        for watch in watches:
            interval = self._wallet_interval_s if watch["kind"] == "wallet" else self._price_interval_s
            checked = _epoch(watch.get("last_checked_at"))
            if checked is not None and now_epoch - checked < interval:
                continue
            due.append(watch)
        # Price reads share one hourly budget: the least recently checked watch goes first, so
        # the one read per chain per price interval rotates through the list and none starves.
        due.sort(key=lambda w: (w["kind"] != "wallet", last_checked(w)))
        # The poller's reads leave any turn: they run under the one named background scope
        # the effect door provides, exactly like the calendar sync, so the ledger sees them.
        from core.effect_gateway import named_background_effect_scope

        with named_background_effect_scope("market.watch.poller"):
            for watch in due:
                try:
                    counts = self._poll_one(watch, now_epoch, source_context=source_context)
                except Exception:
                    outcome["errors"] += 1
                    continue
                for key in ("checked", "events", "needs_setup", "skipped"):
                    outcome[key] += counts.get(key, 0)
        return outcome

    def _poll_one(self, watch: dict[str, Any], now_epoch: float, *,
                  source_context: dict[str, Any] | None) -> dict[str, int]:
        counts = {"checked": 0, "events": 0, "needs_setup": 0, "skipped": 0}
        if watch["state"] == "needs_setup" and str(watch.get("last_error") or "") in HOLD_CODES:
            # The provider refused (or VOOL may not go online): the notice went out once and
            # the watch waits for the user's Resume. No call, no write.
            return counts
        ok, refusal = _requirement_of(watch, source_context)
        if not ok:
            if watch["state"] == "active":
                code = "key_missing" if watch["kind"] == "wallet" else "web_off"
                return self._needs_setup_now(watch, code, refusal)
            # needs_setup already told its story once; only the local requirement is re-read
            _update_watch(watch["id"], last_checked_at=self._now_fn(), get_connection_fn=self._get_connection_fn)
            return counts
        if watch["state"] == "needs_setup":
            _update_watch(watch["id"], state="active", get_connection_fn=self._get_connection_fn)
        if watch["kind"] == "wallet":
            counts.update(self._poll_wallet(watch, now_epoch))
        else:
            counts.update(self._poll_price(watch, now_epoch))
        return counts

    # -- wallets: recent history through the chain-data providers -------------------------

    def _catching_up(self, watch: dict[str, Any], now_epoch: float) -> bool:
        """True when this read covers time VOOL did not watch: the first read of this process,
        or a gap of more than two wallet intervals since the last good read."""
        if watch["id"] not in self._seen_this_process:
            return True
        seen = watch.get("last_seen") or {}
        last_good = _epoch(seen.get("at")) or _epoch(watch.get("last_checked_at"))
        return last_good is None or now_epoch - last_good > 2 * self._wallet_interval_s

    def _poll_wallet(self, watch: dict[str, Any], now_epoch: float) -> dict[str, int]:
        from core import chain_data
        from core import chain_providers as cp
        from core.chain_data import ChainDataError

        try:
            cfg, _others = chain_data._choose_provider({}, watch["chain"], cp.OP_HISTORY)
        except ChainDataError as exc:
            return self._wallet_failure(watch, "", exc.code, now_epoch)
        provider = cfg.registry_id
        # Spent before the call: a failed call is a call the provider counted too.
        allowed, code = self._budget.allow_and_spend(provider, now_epoch)
        if not allowed:
            _update_watch(watch["id"], last_checked_at=self._now_fn(), last_error=code,
                          get_connection_fn=self._get_connection_fn)
            return {"checked": 0, "events": 0, "needs_setup": 0, "skipped": 1}
        catch_up = self._catching_up(watch, now_epoch)
        self._seen_this_process.add(watch["id"])
        try:
            answer = chain_data.lookup({"query": "history", "address": watch["subject"], "chain": watch["chain"],
                                        "limit": 25})
        except ChainDataError as exc:
            return self._wallet_failure(watch, provider, exc.code, now_epoch)
        except Exception:
            return self._wallet_failure(watch, provider, "provider_error", now_epoch)
        self._budget.recovered(provider)
        rows = [row for row in (answer.get("rows") or []) if isinstance(row, dict)]
        ids = [str(row.get("signature") or row.get("hash") or "") for row in rows]
        seen = (watch.get("last_seen") or {}).get("ids")
        if not isinstance(seen, list):
            # First poll (or the store lost its baseline): record what exists, alert on nothing.
            _update_watch(watch["id"], last_checked_at=self._now_fn(),
                          last_seen={"ids": ids[:BASELINE_IDS_KEPT], "at": self._now_fn()}, last_error="",
                          get_connection_fn=self._get_connection_fn)
            return {"checked": 1, "events": 0, "needs_setup": 0, "skipped": 0}
        known = {str(value) for value in seen}
        fresh = [row for row, ident in zip(rows, ids, strict=True) if ident and ident not in known]
        if not fresh:
            _update_watch(watch["id"], last_checked_at=self._now_fn(),
                          last_seen={"ids": ids[:BASELINE_IDS_KEPT], "at": self._now_fn()}, last_error="",
                          get_connection_fn=self._get_connection_fn)
            return {"checked": 1, "events": 0, "needs_setup": 0, "skipped": 0}
        events = self._wallet_events(watch, fresh, catch_up=catch_up)
        _update_watch(watch["id"], last_checked_at=self._now_fn(),
                      last_seen={"ids": list(dict.fromkeys([*ids, *map(str, seen)]))[:BASELINE_IDS_KEPT],
                                 "at": self._now_fn()},
                      last_error="", get_connection_fn=self._get_connection_fn)
        return {"checked": 1, "events": events, "needs_setup": 0, "skipped": 0}

    def _wallet_failure(self, watch: dict[str, Any], provider: str, code: str, now_epoch: float) -> dict[str, int]:
        if code in _WALLET_SETUP_CODES:
            return self._needs_setup_now(watch, _WALLET_SETUP_CODES[code])
        if provider and code in _BACKOFF_CODES:
            self._budget.failed(provider, now_epoch)
        stored = "bad_answer" if code == "not_found" else _reason_code(code)
        _update_watch(watch["id"], last_checked_at=self._now_fn(), last_error=stored,
                      get_connection_fn=self._get_connection_fn)
        return {"checked": 0, "events": 0, "needs_setup": 0, "skipped": 0}

    def _wallet_events(self, watch: dict[str, Any], fresh: list[dict[str, Any]], *, catch_up: bool = False) -> int:
        from core.chain_data import _explorer

        chain_label = _chain_label(watch["chain"])
        if len(fresh) > NEW_TX_SUMMARY_AT or (catch_up and len(fresh) > 1):
            # Many at once, or the time VOOL was closed: one summary, never one per row.
            link = _explorer(watch["chain"], address=watch["subject"])
            when = " since VOOL last checked" if catch_up else " in one check"
            notification_id = _notify(
                watch, title=f"{watch['label']}: {len(fresh)} new transactions",
                body=(f"{len(fresh)} new transactions appeared on {watch['label']} on {chain_label}{when}. "
                      f"Open the history to see them.{' ' + link if link else ''}").strip(),
                dedupe_key=f"market_watch:{watch['id']}:summary:{self._now_fn()[:16]}",
                payload={"count": len(fresh)},
                now_fn=self._now_fn, get_connection_fn=self._get_connection_fn,
            )
            _record_event(watch, "wallet_summary", {"count": len(fresh), "rows": fresh[:NEW_TX_SUMMARY_AT + 2]},
                          notification_id, now_fn=self._now_fn, get_connection_fn=self._get_connection_fn)
            return 1
        events = 0
        from core import chain_providers as _cp

        native_symbol = _cp.NATIVE_ASSETS.get(watch["chain"], ("", 0))[0]
        for row in fresh:
            ident = str(row.get("signature") or row.get("hash") or "")
            link = _explorer(watch["chain"], tx=ident) or _explorer(watch["chain"], address=watch["subject"])
            if "direction" in row and row.get("value") is not None:
                direction = "sent" if row.get("direction") == "sent" else "received"
                way = "to" if direction == "sent" else "from"
                counterparty = str(row.get("counterparty") or "")
                asset = str(row.get("asset") or "") or native_symbol
                title = f"{watch['label']} {direction} {row.get('value')} {asset}".strip()
                body = f"{watch['label']} {direction} {row.get('value')} {asset} {way} {counterparty} on {chain_label}."
            else:
                title = f"{watch['label']} has a new transaction"
                body = (f"A new transaction appeared on {watch['label']} on {chain_label}. The history read does not "
                        "show its amount or direction.")
            if link:
                body = f"{body} {link}"
            notification_id = _notify(
                watch, title=title[:200], body=body,
                dedupe_key=f"market_watch:{watch['id']}:{ident}",
                payload={"row": row}, now_fn=self._now_fn, get_connection_fn=self._get_connection_fn,
            )
            _record_event(watch, "wallet_tx", row, notification_id, now_fn=self._now_fn,
                          get_connection_fn=self._get_connection_fn)
            events += 1
        return events

    # -- prices: the pool price through the public price source ---------------------------

    def _spend_price_call(self, url: str, now_epoch: float) -> tuple[bool, str]:
        """Spend one price-source call, unless the shared cache already holds this read."""
        from core import token_performance

        with token_performance._cache_lock:
            hit = token_performance._cache.get(url)
            if hit and time.time() - hit[0] < token_performance._CACHE_S:
                return True, ""
        return self._budget.allow_and_spend("market.geckoterminal", now_epoch)

    def _claim_chain(self, chain: str, now_epoch: float) -> bool:
        """At most one price read per chain per price interval, whatever the number of watches."""
        last = self._price_chain_read.get(chain)
        if last is not None and now_epoch - last < self._price_interval_s:
            return False
        self._price_chain_read[chain] = now_epoch
        return True

    def _price_failure(self, watch: dict[str, Any], code: str, now_epoch: float) -> dict[str, int]:
        if code in _BACKOFF_CODES:
            self._budget.failed("market.geckoterminal", now_epoch)
        _update_watch(watch["id"], last_checked_at=self._now_fn(), last_error=_reason_code(code),
                      get_connection_fn=self._get_connection_fn)
        return {"checked": 0, "events": 0, "needs_setup": 0, "skipped": 0}

    def _poll_price(self, watch: dict[str, Any], now_epoch: float) -> dict[str, int]:
        from urllib.parse import quote

        from core import token_performance
        from core.token_performance import PriceError

        network = token_performance._NETWORKS.get(watch["chain"], "")
        token = watch["subject"]
        if not self._claim_chain(watch["chain"], now_epoch):
            # Another watch on this chain read this interval; this one stays due and, being the
            # least recently checked, goes first next time.
            return {"checked": 0, "events": 0, "needs_setup": 0, "skipped": 0}
        pools_url = token_performance.API_ORIGIN + f"/networks/{network}/tokens/{quote(token)}/pools" + "?page=1"
        allowed, code = self._spend_price_call(pools_url, now_epoch)
        if not allowed:
            _update_watch(watch["id"], last_checked_at=self._now_fn(), last_error=code,
                          get_connection_fn=self._get_connection_fn)
            return {"checked": 0, "events": 0, "needs_setup": 0, "skipped": 1}
        try:
            payload = token_performance._get(f"/networks/{network}/tokens/{quote(token)}/pools", {"page": 1})
            pool = _best_pool(payload)
            price = _usable_price(pool.get("price_usd"))
        except PriceError as exc:
            if exc.status == "refused":
                return self._needs_setup_now(watch, "local_only")
            return self._price_failure(watch, exc.status, now_epoch)
        except (KeyError, TypeError, ValueError, IndexError):
            return self._price_failure(watch, "bad_answer", now_epoch)
        self._budget.recovered("market.geckoterminal")

        rule = watch.get("rule") or {}
        state = dict(watch.get("last_seen") or {})
        state.setdefault("armed", True)
        fired_text = ""
        try:
            if "move_pct" in rule:
                outcome = self._window_move(network, str(pool.get("address") or ""), token, rule, price, now_epoch)
                if outcome[0] == "budget":
                    _update_watch(watch["id"], last_checked_at=self._now_fn(),
                                  last_seen={**state, "last_price": price}, last_error="budget",
                                  get_connection_fn=self._get_connection_fn)
                    return {"checked": 0, "events": 0, "needs_setup": 0, "skipped": 1}
                pct = outcome[1]
                if pct is None:
                    _update_watch(watch["id"], last_checked_at=self._now_fn(),
                                  last_seen={**state, "last_price": price}, last_error="history_gap",
                                  get_connection_fn=self._get_connection_fn)
                    return {"checked": 1, "events": 0, "needs_setup": 0, "skipped": 0}
                target = float(rule["move_pct"])
                met = pct <= target if target < 0 else pct >= target
                retrace = pct >= target + abs(target) * 0.01 if target < 0 else pct <= target - abs(target) * 0.01
                if state.get("armed") and met:
                    fired_text = (f"{watch['label']} {'dropped' if target < 0 else 'rose'} {abs(pct):.1f}% in the last "
                                  f"{rule.get('window')}: now {price:.6g}")
            else:
                threshold = float(rule.get("above") or rule.get("below") or 0)
                above = "above" in rule
                met = price >= threshold if above else price <= threshold
                retrace = price <= threshold * 0.99 if above else price >= threshold * 1.01
                if state.get("armed") and met:
                    word = "is above" if above else "is below"
                    fired_text = f"{watch['label']} {word} {threshold:g}: now {price:.6g}"
            # Re-armed only once the price is back past the threshold by the 1% margin, with or
            # without repeat: a price hovering at the line never alerts on every wobble.
            if not state.get("armed") and retrace:
                state["armed"] = True
        except (KeyError, TypeError, ValueError):
            pass
        if fired_text:
            state["armed"] = False
            notification_id = _notify(
                watch, title=fired_text[:200], body=fired_text + f" ({_chain_label(watch['chain'])}).",
                dedupe_key=f"market_watch:{watch['id']}:price:{self._now_fn()[:16]}",
                payload={"price": price, "rule": rule}, now_fn=self._now_fn, get_connection_fn=self._get_connection_fn,
            )
            _record_event(watch, "price", {"price": price, "rule": rule, "text": fired_text}, notification_id,
                          now_fn=self._now_fn, get_connection_fn=self._get_connection_fn)
            events = 1
        else:
            events = 0
        _update_watch(watch["id"], last_checked_at=self._now_fn(),
                      last_seen={**state, "last_price": price, "last_at": self._now_fn()}, last_error="",
                      get_connection_fn=self._get_connection_fn)
        return {"checked": 1, "events": events, "needs_setup": 0, "skipped": 0}

    def _needs_setup_now(self, watch: dict[str, Any], code: str, words: str = "") -> dict[str, int]:
        """Stop the watch with its one notice; ``last_error`` keeps only the reason code."""
        reason = words or _SETUP_WORDS.get(code, "").format(chain=_chain_label(watch["chain"]))
        _update_watch(watch["id"], state="needs_setup", last_checked_at=self._now_fn(), last_error=code,
                      get_connection_fn=self._get_connection_fn)
        notification_id = _notify(
            watch, title=f"{watch['label']} stopped checking", body=f"The watch on {watch['label']} stopped: {reason}",
            dedupe_key=f"market_watch.setup:{watch['id']}",
            now_fn=self._now_fn, get_connection_fn=self._get_connection_fn,
        )
        _record_event(watch, "needs_setup", {"reason": code}, notification_id,
                      now_fn=self._now_fn, get_connection_fn=self._get_connection_fn)
        return {"checked": 0, "events": 0, "needs_setup": 1, "skipped": 0}

    def _window_move(self, network: str, pool: str, token: str, rule: dict[str, Any], price_now: float,
                     now_epoch: float) -> tuple[str, float | None]:
        """The percent move over the rule's window, from the candles that cover it.

        Returns ("budget", None) when the price-source budget is used up, ("gap", None)
        when the candles do not cover the window, or ("ok", percent)."""
        from core import token_performance

        timeframe, aggregate, step, window_s = WINDOWS[str(rule.get("window"))]
        started = now_epoch - window_s
        candles_url = (token_performance.API_ORIGIN + f"/networks/{network}/pools/{pool}/ohlcv/{timeframe}"
                       + f"?aggregate={aggregate}")
        allowed, _code = self._spend_price_call(candles_url, now_epoch)
        if not allowed:
            return "budget", None
        try:
            candles = token_performance._candles(network, pool, token, timeframe, aggregate, int(started), step)
        except Exception:
            return "gap", None
        if not candles:
            return "gap", None
        candles.sort(key=lambda c: c[0])
        ended = [c for c in candles if c[0] + step <= started]
        start_price = float(ended[-1][4]) if ended else float(candles[0][1])
        if start_price <= 0:
            return "gap", None
        return "ok", (price_now / start_price - 1) * 100

    # -- thread lifecycle -----------------------------------------------------------------

    def start(self) -> bool:
        if self._thread is not None and self._thread.is_alive():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="vool-market-watch", daemon=True)
        self._thread.start()
        return True

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            with contextlib.suppress(Exception):
                self.sweep_once()
            self._stop.wait(self._tick)


def _usable_price(value: Any) -> float:
    """A quoted price VOOL may compare: finite and above zero. Anything else is a bad answer."""
    if isinstance(value, bool):
        raise ValueError("not a price")
    price = float(value)
    if not math.isfinite(price) or price <= 0:
        raise ValueError("not a usable price")
    return price


def _best_pool(payload: Any) -> dict[str, Any]:
    """The most liquid pool and its price, from the pools page the price source answers."""
    best: dict[str, Any] = {}
    for entry in (payload.get("data") or []) if isinstance(payload, dict) else []:
        attrs = entry.get("attributes") or {} if isinstance(entry, dict) else {}
        try:
            liquidity = float(attrs.get("reserve_in_usd") or 0)
        except (TypeError, ValueError):
            liquidity = 0.0
        if liquidity > float(best.get("liquidity_usd") or 0):
            best = {"address": str(attrs.get("address") or ""), "liquidity_usd": liquidity,
                    "symbol": str(attrs.get("name") or "").split("/")[0].strip()[:20],
                    "price_usd": attrs.get("base_token_price_usd")}
    if not best or not best.get("address") or best.get("price_usd") in (None, ""):
        from core.token_performance import PriceError

        raise PriceError("not_found", "no trading pool was found for it")
    return best


_DEFAULT_POLLER: MarketWatchPoller | None = None
_POLLER_LOCK = threading.Lock()


def start_default_poller() -> MarketWatchPoller | None:
    """Start the one process-wide watch poller. Called by the served boot; safe to call twice."""
    global _DEFAULT_POLLER

    with _POLLER_LOCK:
        if _DEFAULT_POLLER is not None and _DEFAULT_POLLER._thread is not None and _DEFAULT_POLLER._thread.is_alive():
            return _DEFAULT_POLLER
        _DEFAULT_POLLER = MarketWatchPoller(get_connection_fn=_default_connection)
        _DEFAULT_POLLER.start()
        return _DEFAULT_POLLER


def stop_default_poller() -> None:
    global _DEFAULT_POLLER

    with _POLLER_LOCK:
        if _DEFAULT_POLLER is not None:
            _DEFAULT_POLLER.stop()
        _DEFAULT_POLLER = None


# --------------------------------------------------------------------------- Settings view


def settings_view(*, get_connection_fn: Callable[[], Any] = _default_connection) -> dict[str, Any]:
    """The Watching card's rows: what is watched, its rule in words, its state and last check."""
    from core import chain_providers as cp

    rows = []
    for watch in list_watches(get_connection_fn=get_connection_fn):
        rows.append({
            "id": watch["id"], "kind": watch["kind"], "label": watch["label"],
            "chain": cp.CHAIN_LABELS.get(watch["chain"], watch["chain"]),
            "subject": watch["subject"], "rule": watch["rule"],
            "rule_words": rule_in_words(watch["rule"]) if watch["kind"] == "price" else "new transactions",
            "state": watch["state"],
            "last_checked": str(watch.get("last_checked_at") or ""),
            # A reason code only (ERR_CODES); the card words it through vmw.err.<code>.
            "last_error": _shown_error(watch.get("last_error")),
        })
    return {"ok": True, "watches": rows}


def _shown_error(value: Any) -> str:
    code = str(value or "")
    if not code:
        return ""
    return code if code in ERR_CODES else "unknown"


__all__ = [
    "CAPABILITY_ID",
    "INTENTS",
    "MarketWatchPoller",
    "WatchError",
    "add_watch",
    "list_watches",
    "load_watch",
    "market_watch_contracts",
    "remove_watch",
    "rule_in_words",
    "run_tool",
    "set_state",
    "settings_view",
    "start_default_poller",
    "stop_default_poller",
]
