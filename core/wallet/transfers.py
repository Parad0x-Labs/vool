"""Which proposals are Crypto Pilot transfers, owned in one place.

A pilot transfer needs a typed quote and the approval sheet; everything else keeps the legacy approve contract. The rule
(PLAN-v2 V4): a transfer from a pilot-policy wallet, or a native-coin transfer on a Mainnet row, on a Robinhood Chain
row, or on any EVM row. Legacy SOL on Solana Devnet from a legacy wallet and x402 token lanes stay legacy.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

from core.wallet import chains, custody
from core.wallet.store import connection, utcnow

MAX_OPEN_PILOT_PROPOSALS = 5
OPEN_PROPOSAL_STATES: tuple[str, ...] = ("proposed", "simulated", "limits_checked", "pending_approval", "approved", "awaiting_signature")


def is_pilot_transfer_parts(*, wallet_id: str, network: str, asset: str) -> bool:
    if custody.seal_policy(wallet_id) == custody.PILOT_SEAL_POLICY:
        return True
    spec = chains.resolve_network(network)
    native = chains.native_asset(spec.network)
    symbols = {native.symbol.upper(), (spec.native_display_symbol or native.symbol).upper()}
    if str(asset or "").strip().upper() not in symbols:
        return False
    return spec.is_mainnet or spec.chain_key == "robinhood" or spec.is_evm


def is_pilot_transfer(proposal: Any) -> bool:
    return is_pilot_transfer_parts(wallet_id=str(proposal.wallet_id), network=str(proposal.network), asset=str(proposal.asset))


def open_pilot_proposals(wallet_id: str, network: str) -> int:
    """Open pilot proposals of this wallet on this exact row (legacy names compare by canonical identity)."""
    canonical = chains.resolve_network(network).network
    placeholders = ", ".join("?" for _ in OPEN_PROPOSAL_STATES)
    with connection() as conn:
        rows = conn.execute(
            f"SELECT network, asset FROM wallet_proposals WHERE wallet_id = ? AND state IN ({placeholders})",
            (str(wallet_id), *OPEN_PROPOSAL_STATES),
        ).fetchall()
    count = 0
    for row_network, row_asset in rows:
        try:
            same_row = chains.resolve_network(row_network).network == canonical
        except Exception:
            continue
        if same_row and is_pilot_transfer_parts(wallet_id=wallet_id, network=row_network, asset=row_asset):
            count += 1
    return count


# --- the caller path: which account, which row, how much (design v5 §7) ---------------------------------------

_CHAIN_HINTS: dict[str, str] = {
    "solana": "solana", "sol": "solana", "base": "base", "ethereum": "ethereum", "eth": "ethereum", "mainnet": "ethereum",
    "bnb": "bnb", "bsc": "bnb", "binance": "bnb", "robinhood": "robinhood", "robinhood chain": "robinhood",
}


def destination_family(destination: str) -> str:
    """The family a recipient's shape belongs to: ``evm`` for a 20-byte hex address, ``svm`` for a base58 key, else ``""``."""
    return _destination_family(destination)


def _destination_family(destination: str) -> str:
    value = str(destination or "").strip()
    if value.startswith("0x") and len(value) == 42:
        return chains.FAMILY_EVM
    if 32 <= len(value) <= 44 and all(c in "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz" for c in value):
        return chains.FAMILY_SVM
    return ""


def resolve_request(*, destination: str, asset: str = "", amount: Any = None, amount_minor: Any = None, chain: str = "", network: str = "",
                    wallet_id: str = "", source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Turn a chat or API request into one account on one row with an integer amount. Resolution order: an explicit
    account, else an explicit network, else a chain name, else the asset symbol and the recipient's shape, always
    within the active environment and among accounts that can sign. When several accounts fit, the owner's default
    account settles it if it is one of them; otherwise the refusal names the candidates. None is a typed refusal that
    says what is missing. Amounts are parsed as exact decimals."""
    from core.wallet import amounts, environment
    from core.wallet.security import wallet_fault

    active = environment.active_environment().environment
    rows = list(chains.rows_for_environment(active))
    wanted_wallet = str(wallet_id or "").strip()
    explicit_network = str(network or "").strip()
    chain_key = _CHAIN_HINTS.get(str(chain or "").strip().lower(), str(chain or "").strip().lower())
    symbol = str(asset or "").strip().upper()
    family_hint = _destination_family(destination)
    candidates: list[dict[str, Any]] = []
    for entry in custody.list_wallets():
        try:
            spec = chains.resolve_network(entry.get("network") or "")
        except Exception:
            continue
        if spec.environment != active or spec.network not in {r.network for r in rows}:
            continue
        if str(entry.get("mode") or "") == custody.MODE_WATCH_ONLY:
            continue
        if str(entry.get("setup_state") or "") not in {"", "ready"}:
            continue
        native = chains.native_asset(spec.network)
        row_symbols = {native.symbol.upper(), (spec.native_display_symbol or native.symbol).upper()}
        if wanted_wallet and str(entry.get("wallet_id")) != wanted_wallet:
            continue
        if explicit_network and chains.resolve_network(explicit_network).network != spec.network:
            continue
        if chain_key and spec.chain_key != chain_key:
            continue
        if symbol and symbol not in row_symbols:
            continue
        if family_hint and spec.family != family_hint:
            continue
        candidates.append({"wallet_id": str(entry.get("wallet_id")), "label": str(entry.get("label") or ""), "network": spec.network,
                           "display_name": spec.display_name, "chain_key": spec.chain_key, "asset": native.symbol, "decimals": native.decimals, "mode": str(entry.get("mode") or "")})
    if not candidates:
        raise wallet_fault("wallet_not_found", authority=AUTHORITY_REQUESTS, context={
            "reason": "no_signing_account_for_request", "active_environment": active, "chain": chain_key, "network": explicit_network, "asset": symbol,
        }, source_context=source_context)
    if len(candidates) > 1 and not wanted_wallet:
        # the owner's designated account settles a request that several accounts fit; a default on another row does not
        default = custody.default_wallet()
        preferred = [c for c in candidates if default is not None and c["wallet_id"] == default.wallet_id]
        if preferred:
            candidates = preferred
    if len(candidates) > 1:
        raise wallet_fault("wallet_request_ambiguous", authority=AUTHORITY_REQUESTS, context={
            "reason": "several_accounts_fit", "candidates": [{k: c[k] for k in ("wallet_id", "label", "network", "display_name", "asset")} for c in candidates[:8]],
        }, source_context=source_context)
    chosen = candidates[0]
    decimals = int(chosen["decimals"])
    minor: int | None = None
    if amount_minor not in (None, ""):
        try:
            minor = int(amount_minor)
        except (TypeError, ValueError):
            raise wallet_fault("wallet_limit_exceeded", authority=AUTHORITY_REQUESTS, context={"limit": "amount", "reason": "amount_minor_not_an_integer"}, source_context=source_context) from None
    if amount not in (None, ""):
        parsed = amounts.parse_human_amount(amount, decimals=decimals, symbol=chosen["asset"], source_context=source_context)
        if minor is not None and minor != parsed:
            raise wallet_fault("wallet_limit_exceeded", authority=AUTHORITY_REQUESTS, context={"limit": "amount", "reason": "amount_and_amount_minor_disagree"}, source_context=source_context)
        minor = parsed
    if minor is None:
        raise wallet_fault("wallet_limit_exceeded", authority=AUTHORITY_REQUESTS, context={"limit": "amount", "reason": "amount_missing"}, source_context=source_context)
    return {"wallet_id": chosen["wallet_id"], "network": chosen["network"], "asset": chosen["asset"], "amount_minor": minor, "destination": str(destination or "").strip(),
            "display_name": chosen["display_name"], "amount_human": amounts.format_minor(minor, decimals)}


AUTHORITY_REQUESTS = "core.wallet.transfers"


# --- the transfer store (design v5 §2) -----------------------------------------------------------------

STATE_CLAIMED = "claimed"
STATE_SIGNED = "signed"
STATE_SIGNED_REVOKED = "signed_revoked"
STATE_DISPATCHING = "dispatching"
STATE_UNKNOWN = "unknown"
STATE_PENDING = "pending"
STATE_CONFIRMED = "confirmed"
STATE_FAILED_ON_CHAIN = "failed_on_chain"
STATE_RELEASED = "released"
STATE_DISCARDED = "discarded"
STATE_CANCELLED = "cancelled"
STATE_STOPPED_WAITING = "stopped_waiting"

LIVE_STATES: tuple[str, ...] = (STATE_CLAIMED, STATE_SIGNED, STATE_SIGNED_REVOKED, STATE_DISPATCHING, STATE_UNKNOWN, STATE_PENDING)
NONCE_STATES: tuple[str, ...] = (*LIVE_STATES, STATE_CONFIRMED, STATE_FAILED_ON_CHAIN)
NEVER_SENT_STATES: tuple[str, ...] = (STATE_RELEASED, STATE_DISCARDED, STATE_CANCELLED)
TERMINAL_STATES: tuple[str, ...] = (STATE_CONFIRMED, STATE_FAILED_ON_CHAIN, *NEVER_SENT_STATES, STATE_STOPPED_WAITING)
#: rows the approve gate treats as in flight for an EVM account (a stopped transfer no longer blocks the account)
IN_FLIGHT_STATES: tuple[str, ...] = LIVE_STATES

_HOLD_NONE = "none"
_HOLD_RELEASE = "release"
_HOLD_SETTLE = "settle"
_HOLD_RESETTLE = "resettle"


class TransferTransitionError(Exception):
    """A transfer CAS refused: unknown transfer, another state, another owner, a cancel request, or an edge not allowed."""


@dataclass(frozen=True)
class _Projection:
    proposal_edge: tuple[str, str] | None  # (proposal state it must be in, proposal state it moves to)
    hold: str = _HOLD_NONE
    reason: str = ""
    amount_moved: bool = True
    requires_no_cancel: bool = False
    tx_signature_from_row: bool = False


_SETTLED = ("broadcast", "confirmed")
_FAILED_ON_CHAIN = ("broadcast", "failed")

#: v5 §2.2, one row per allowed transfer edge. Anything absent is refused.
PROJECTION: dict[tuple[str, str], _Projection] = {
    (STATE_CLAIMED, STATE_SIGNED): _Projection(("approved", "signed"), requires_no_cancel=True),
    (STATE_SIGNED, STATE_DISPATCHING): _Projection(("signed", "broadcast"), requires_no_cancel=True, tx_signature_from_row=True),
    (STATE_DISPATCHING, STATE_UNKNOWN): _Projection(None),
    (STATE_DISPATCHING, STATE_PENDING): _Projection(None),
    (STATE_UNKNOWN, STATE_PENDING): _Projection(None),
    (STATE_PENDING, STATE_UNKNOWN): _Projection(None),
    (STATE_UNKNOWN, STATE_CONFIRMED): _Projection(_SETTLED, _HOLD_SETTLE, "confirmed_on_chain", amount_moved=True),
    (STATE_PENDING, STATE_CONFIRMED): _Projection(_SETTLED, _HOLD_SETTLE, "confirmed_on_chain", amount_moved=True),
    (STATE_PENDING, STATE_FAILED_ON_CHAIN): _Projection(_FAILED_ON_CHAIN, _HOLD_SETTLE, "wallet_transfer_failed_on_chain", amount_moved=False),
    (STATE_CLAIMED, STATE_RELEASED): _Projection(("approved", "failed"), _HOLD_RELEASE, "approval_not_completed", requires_no_cancel=True),
    (STATE_SIGNED, STATE_SIGNED_REVOKED): _Projection(None),
    (STATE_SIGNED_REVOKED, STATE_RELEASED): _Projection(("signed", "failed"), _HOLD_RELEASE, "signed_not_sent", requires_no_cancel=True),
    (STATE_SIGNED_REVOKED, STATE_DISCARDED): _Projection(("signed", "failed"), _HOLD_RELEASE, "signed_not_sent", requires_no_cancel=True),
    (STATE_CLAIMED, STATE_CANCELLED): _Projection(("approved", "rejected"), _HOLD_RELEASE, "owner_cancelled_before_signing"),
    (STATE_SIGNED, STATE_CANCELLED): _Projection(("signed", "rejected"), _HOLD_RELEASE, "owner_cancelled_before_sending"),
    (STATE_SIGNED_REVOKED, STATE_CANCELLED): _Projection(("signed", "rejected"), _HOLD_RELEASE, "owner_cancelled_before_sending"),
    (STATE_UNKNOWN, STATE_STOPPED_WAITING): _Projection(None, _HOLD_SETTLE, "owner_stopped_waiting", amount_moved=True),
    (STATE_PENDING, STATE_STOPPED_WAITING): _Projection(None, _HOLD_SETTLE, "owner_stopped_waiting", amount_moved=True),
    (STATE_STOPPED_WAITING, STATE_CONFIRMED): _Projection(_SETTLED, _HOLD_RESETTLE, "confirmed_after_owner_stopped_waiting", amount_moved=True),
    (STATE_STOPPED_WAITING, STATE_FAILED_ON_CHAIN): _Projection(_FAILED_ON_CHAIN, _HOLD_RESETTLE, "wallet_transfer_failed_on_chain", amount_moved=False),
}

#: columns a transition may write besides state, updated_at and the terminal byte drop
_WRITABLE: frozenset[str] = frozenset({
    "tx_id", "raw_b64", "owner_token", "lease_until", "dispatch_claimed_at", "attempts_sent", "cancel_requested_at", "evidence_json",
    "evidence_kind", "charged_fee_minor", "balance_after_minor", "block_ref", "block_hash", "finality_seen", "effect_instance_id",
    "a6_resolved_at", "a6_outcome", "offered_exit_json", "crossing_block", "crossing_block_hash", "stopped_waiting_at", "observe_until",
    "fee_state", "fee_known_minor", "fee_missing_json", "fee_fork",
})

#: the fee the record settled with: exact (every component the row's receipt must carry was read) or bounded (the
#: approved ceiling stays counted; the missing components are named; a later complete receipt refines it once)
FEE_EXACT = "exact"
FEE_BOUNDED = "bounded"


def get_transfer(conn: Any, proposal_id: str) -> dict[str, Any] | None:
    cursor = conn.execute("SELECT * FROM wallet_transfers WHERE proposal_id = ?", (str(proposal_id or ""),))
    row = cursor.fetchone()
    if row is None:
        return None
    return {description[0]: row[index] for index, description in enumerate(cursor.description)}


def get_transfer_by_id(proposal_id: str) -> dict[str, Any] | None:
    with connection() as conn:
        return get_transfer(conn, proposal_id)


def update_columns(conn: Any, proposal_id: str, **columns: Any) -> None:
    """Write evidence and follow-up columns without moving the state (state changes go through :func:`transition`)."""
    unknown = sorted(set(columns) - _WRITABLE)
    if unknown:
        raise ValueError(f"not a writable transfer column: {unknown}")
    if not columns:
        return
    names = sorted(columns)
    conn.execute(f"UPDATE wallet_transfers SET {', '.join(f'{name} = ?' for name in names)}, updated_at = ? WHERE proposal_id = ?",
                 (*[columns[name] for name in names], utcnow(), str(proposal_id)))


def take_marker(key: str, *, ttl_seconds: float) -> str | None:
    """A short-lived exclusive marker in ``wallet_controls`` (an approval in flight, an observer lease). Returns the
    token that releases it, or None while another holder's marker has not expired."""
    from core.wallet import limits

    token, now = uuid.uuid4().hex, time.time()
    with connection() as conn:
        limits._begin_immediate(conn)
        row = conn.execute("SELECT value FROM wallet_controls WHERE key = ?", (str(key),)).fetchone()
        if row:
            try:
                held = json.loads(row[0] or "{}")
            except ValueError:
                held = {}
            if float(held.get("until") or 0) > now:
                return None
        conn.execute(
            "INSERT INTO wallet_controls (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (str(key), json.dumps({"token": token, "until": now + float(ttl_seconds)}), utcnow()),
        )
    return token


def release_marker(key: str, token: str) -> None:
    with connection() as conn:
        row = conn.execute("SELECT value FROM wallet_controls WHERE key = ?", (str(key),)).fetchone()
        if not row:
            return
        try:
            held = json.loads(row[0] or "{}")
        except ValueError:
            held = {}
        if str(held.get("token") or "") == str(token):
            conn.execute("DELETE FROM wallet_controls WHERE key = ?", (str(key),))


# --- the read model (DB only; every surface renders these fields, never its own money arithmetic) ---------------

TRANSFER_LABELS: dict[str, str] = {
    STATE_CLAIMED: "Approved, preparing", STATE_SIGNED: "Signed, sending", STATE_SIGNED_REVOKED: "Signed, not sent",
    STATE_DISPATCHING: "Sending", STATE_PENDING: "Pending", STATE_CONFIRMED: "Confirmed", STATE_FAILED_ON_CHAIN: "Failed on chain",
    STATE_RELEASED: "Not sent", STATE_DISCARDED: "Discarded, never sent", STATE_CANCELLED: "Cancelled", STATE_STOPPED_WAITING: "Stopped waiting, not confirmed",
}
#: the label of an ``unknown`` row comes from what the send answered (design v5 §4.3)
EVIDENCE_LABELS: dict[str, str] = {
    "submitted": "Submitted", "already_known": "Submitted: the network already has this transaction",
    "refused": "Status unknown: the network refused this transaction",
    "other_error": "Status unknown: the network answered with an error, and this transfer may still go through",
    "no_answer": "Status unknown",
}
TRANSFER_DETAILS: dict[str, str] = {
    STATE_CLAIMED: "The transfer is approved and being prepared. Nothing has been sent.",
    STATE_SIGNED: "The transfer is signed and about to be sent.",
    STATE_SIGNED_REVOKED: "The signed transfer was not sent (a control changed or its time window closed). Nothing left this device.",
    STATE_DISPATCHING: "The transfer is being sent to the network.",
    STATE_PENDING: "The network has seen this transfer; waiting for confirmation.",
    STATE_CONFIRMED: "Confirmed by the network.",
    STATE_FAILED_ON_CHAIN: "The network executed this transfer and it failed; only the network fee was charged.",
    STATE_RELEASED: "Not sent. The hold was released and nothing left this device.",
    STATE_DISCARDED: "Discarded before sending. Nothing left this device.",
    STATE_CANCELLED: "Cancelled. Nothing was sent.",
    STATE_STOPPED_WAITING: "You stopped waiting; not confirmed. It may still be included, so it keeps counting against today's limits.",
}
_SVM_ID_ALPHABET = frozenset("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
#: the exits the read model can offer, with the exact words the sheet shows before a credential is asked
EXIT_TERMS: dict[str, dict[str, str]] = {
    "stop_waiting": {
        "title": "Stop waiting on this transfer",
        "action": "Stop waiting",
        "door": "/api/wallet/transfers/stop-waiting",
        "risk": "VOOL could not confirm this transfer. It keeps counting against today's limits, because it may still be included; if it is, it pays the recipient you approved. Other transfers from this account can continue.",
    },
    "stop_waiting_slot_used": {
        "title": "Stop waiting: this account's transaction slot was used",
        "action": "Stop waiting",
        "door": "/api/wallet/transfers/stop-waiting",
        "risk": "The network shows this account's slot used by another transaction and VOOL could not find its own. It keeps counting against today's limits until the network answers for it. Other transfers from this account can continue.",
    },
    "stop_waiting_refused": {
        "title": "Stop waiting on a transfer the network refused or cannot accept now",
        "action": "Stop waiting",
        "door": "/api/wallet/transfers/stop-waiting",
        "risk": "The signed transfer stays valid until this account's next transaction uses its slot. It keeps counting against today's limits until then. Other transfers from this account can continue and will use that slot.",
    },
    "resend": {
        "title": "Send the same signed transaction again",
        "action": "Send again",
        "door": "/api/wallet/transfers/resend",
        "risk": "This sends exactly the transaction you approved, with the same amount, recipient and fee limit. It cannot pay twice.",
    },
    "discard": {
        "title": "Discard a signed transfer that was never sent",
        "action": "Discard",
        "door": "/api/wallet/transfers/discard",
        "risk": "This transfer was signed but never left this device and can no longer be sent. Discarding it releases the hold; nothing was paid.",
    },
}


def explorer_url(spec: chains.ChainIdentity, tx_id: str) -> str:
    """The row's allowlisted explorer link for a public transaction id; empty for anything that is not one."""
    value = str(tx_id or "")
    if spec.is_svm:
        if not (64 <= len(value) <= 90 and set(value) <= _SVM_ID_ALPHABET):
            return ""
    elif not (value.startswith("0x") and len(value) == 66 and all(c in "0123456789abcdefABCDEF" for c in value[2:])):
        return ""
    return spec.explorer_tx_template.format(id=value) if spec.explorer_tx_template else ""


def _human(minor: Any, decimals: int) -> str:
    from core.wallet import amounts

    return amounts.format_minor(int(minor), decimals) if minor is not None else ""


def view_of(row: dict[str, Any]) -> dict[str, Any]:
    from core.wallet import environment

    spec = chains.resolve_network(row["network"])
    native = chains.native_asset(spec.network)
    symbol = spec.native_display_symbol or native.symbol
    try:
        principal = chains.asset_for(spec.network, str(row.get("asset") or native.symbol))
    except Exception:
        principal = native
    token = not principal.native
    principal_symbol = principal.symbol if token else symbol
    state = str(row["state"])
    kind = str(row.get("evidence_kind") or "")
    label = EVIDENCE_LABELS.get(kind, EVIDENCE_LABELS["no_answer"]) if state == STATE_UNKNOWN else TRANSFER_LABELS.get(state, state)
    detail = TRANSFER_DETAILS.get(state, "")
    if state == STATE_UNKNOWN and kind in ("submitted", "already_known"):
        detail = "The network accepted this transfer; waiting for it to appear in a block."
    elif state == STATE_UNKNOWN:
        detail = "The outcome is not known yet. The transfer stays counted until the network answers."
    tx_id = str(row.get("tx_id") or "")
    dispatched = state not in (STATE_CLAIMED, STATE_SIGNED, STATE_SIGNED_REVOKED, *NEVER_SENT_STATES)
    link = explorer_url(spec, tx_id) if dispatched and tx_id else ""
    charged = row.get("charged_fee_minor")
    balance_after = row.get("balance_after_minor")
    try:
        offered = json.loads(row.get("offered_exit_json") or "{}")
    except ValueError:
        offered = {}
    fee_state = str(row.get("fee_state") or "")
    fee_known = row.get("fee_known_minor")
    try:
        fee_missing = [str(name) for name in json.loads(row.get("fee_missing_json") or "[]")]
    except ValueError:
        fee_missing = []
    if fee_state == FEE_EXACT:
        fee_label = f"{_human(charged, native.decimals)} {symbol}"
    elif fee_state == FEE_BOUNDED:
        fee_label = f"at most {_human(row['fee_max_minor'], native.decimals)} {symbol} (chain fee evidence incomplete: {', '.join(fee_missing) or 'fee'})"
    else:
        fee_label = ""
    from core.wallet import amounts, proposals, purpose

    proposal = proposals.get_proposal(row["proposal_id"])
    purpose_view = purpose.purpose_for(proposal) if proposal is not None else None
    fee_max_display = amounts.display_amount(int(row["fee_max_minor"]), native.decimals, symbol, mode=amounts.ROUND_UP)
    charged_display = amounts.display_amount(int(charged), native.decimals, symbol) if charged is not None else ""
    origin, collection = "", None
    try:
        from core.wallet import proposals as _proposals

        current = _proposals.get_proposal(str(row["proposal_id"]))
        origin = str(current.origin) if current is not None else ""
        if origin == _proposals.ORIGIN_DNA_FEE:
            from core.wallet import dna_fees

            collection = dna_fees.receipt_view(str(row["proposal_id"]))
    except Exception:
        collection = None
    return {
        "purpose": purpose_view, "fee_max_display": fee_max_display, "charged_fee_display": charged_display,
        "proposal_id": row["proposal_id"], "wallet_id": row["wallet_id"], "network": spec.network, "chain_key": spec.chain_key,
        "chain_label": chains.CHAIN_KEY_LABELS.get(spec.chain_key, spec.display_name), "display_name": spec.display_name,
        "environment": spec.environment, **environment.presentation(spec), "family": spec.family,
        "origin": origin, "dna_fee_collection": collection,
        "from_address": row["from_address"], "to_address": row["to_address"], "asset": principal.symbol if token else native.symbol, "display_symbol": principal_symbol, "decimals": principal.decimals,
        "token_transfer": token, "fee_symbol": symbol,
        "amount_minor": str(int(row["amount_minor"])), "amount_human": _human(row["amount_minor"], principal.decimals),
        "fee_max_minor": str(int(row["fee_max_minor"])), "fee_max_human": _human(row["fee_max_minor"], native.decimals),
        "charged_fee_minor": None if charged is None else str(int(charged)), "charged_fee_human": _human(charged, native.decimals),
        "fee_state": fee_state, "fee_known_minor": None if fee_known is None else str(int(fee_known)), "fee_missing": fee_missing, "charged_fee_label": fee_label,
        "fee_fork": str(row.get("fee_fork") or ""),
        # a token principal and a native fee are two assets: the maximum is shown per asset, never summed
        "max_total_human": _human(row["amount_minor"], principal.decimals) if token else _human(int(row["amount_minor"]) + int(row["fee_max_minor"]), native.decimals),
        "state": state, "state_label": label, "detail": detail, "evidence_kind": kind, "dispatched": dispatched,
        "tx_id": tx_id if dispatched else "", "explorer_url": link, "explorer_link_text": f"View on {spec.explorer_label}" if link and spec.explorer_label else "",
        "block_ref": row.get("block_ref"), "finality_seen": row.get("finality_seen") or "",
        "balance_after_minor": None if balance_after is None else str(int(balance_after)), "balance_after_human": _human(balance_after, native.decimals),
        "balance_after_label": "observed after confirmation" if state == STATE_CONFIRMED and balance_after is not None else "projected",
        "offered_exit": str(offered.get("exit") or "") if isinstance(offered, dict) else "",
        "exit_terms": dict(EXIT_TERMS.get(str(offered.get("exit") or "") if isinstance(offered, dict) else "", {})),
        "cancel_requested": bool(row.get("cancel_requested_at")), "in_flight": state in IN_FLIGHT_STATES, "terminal": state in TERMINAL_STATES,
        "attempts_sent": int(row.get("attempts_sent") or 0), "a6_outcome": row.get("a6_outcome") or "",
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def latest_receipt(proposal_id: str) -> dict[str, Any] | None:
    row = get_transfer_by_id(proposal_id)
    return view_of(row) if row else None


def list_transfers(*, limit: int = 50, wallet_id: str = "", network: str = "") -> list[dict[str, Any]]:
    clauses, values = [], []
    if wallet_id:
        clauses.append("wallet_id = ?")
        values.append(str(wallet_id))
    if network:
        clauses.append("network = ?")
        values.append(chains.resolve_network(network).network)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with connection() as conn:
        cursor = conn.execute(f"SELECT * FROM wallet_transfers{where} ORDER BY created_at DESC LIMIT ?", (*values, max(1, min(int(limit), 200))))
        rows = [{d[0]: r[i] for i, d in enumerate(cursor.description)} for r in cursor.fetchall()]
    return [view_of(row) for row in rows]


def in_flight() -> list[dict[str, Any]]:
    placeholders = ", ".join("?" for _ in IN_FLIGHT_STATES)
    with connection() as conn:
        cursor = conn.execute(f"SELECT * FROM wallet_transfers WHERE state IN ({placeholders}) ORDER BY created_at ASC LIMIT 50", IN_FLIGHT_STATES)
        rows = [{d[0]: r[i] for i, d in enumerate(cursor.description)} for r in cursor.fetchall()]
    return [view_of(row) for row in rows]


def claim_resend(conn: Any, proposal_id: str, *, owner_token: str, attempts_sent: int, new_token: str, epochs: dict[str, int], now: float) -> bool:
    """The resend's compare-and-set on an ``unknown`` row: a new owner token, one more attempt, the resend's own control
    baseline recorded. Exactly one concurrent resend wins."""
    cursor = conn.execute(
        "UPDATE wallet_transfers SET owner_token = ?, attempts_sent = attempts_sent + 1, dispatch_claimed_at = ?, epoch_freeze = ?, epoch_enabled = ?, epoch_environment = ?, updated_at = ? "
        "WHERE proposal_id = ? AND state = ? AND owner_token = ? AND attempts_sent = ? AND cancel_requested_at IS NULL",
        (str(new_token), float(now), int(epochs["freeze"]), int(epochs["enabled"]), int(epochs["environment"]), utcnow(), str(proposal_id), STATE_UNKNOWN, str(owner_token), int(attempts_sent)),
    )
    return cursor.rowcount == 1


def live_for_account(conn: Any, network: str, from_address: str) -> dict[str, Any] | None:
    """The live transfer holding this account's next slot, if any (an EVM account sends one at a time)."""
    placeholders = ", ".join("?" for _ in LIVE_STATES)
    cursor = conn.execute(f"SELECT * FROM wallet_transfers WHERE network = ? AND from_address = ? AND state IN ({placeholders}) LIMIT 1", (str(network), str(from_address), *LIVE_STATES))
    row = cursor.fetchone()
    return {d[0]: row[i] for i, d in enumerate(cursor.description)} if row else None


def insert_claim(conn: Any, *, proposal: Any, quote_id: str, quote_digest: str, challenge_digest: str, environment: str, family: str,
                 from_address: str, fee_max_minor: int, owner_token: str, lease_until: float, dispatch_deadline: float,
                 epochs: dict[str, int], enabled_generation: int, now: float, nonce: int | None = None, blockhash: str | None = None,
                 blockhash_slot: int | None = None, last_valid_block_height: int | None = None) -> dict[str, Any]:
    """The claim row of v5 §2.2 on the caller's connection (inside the claim's BEGIN IMMEDIATE): proposal
    ``pending_approval → approved``, the hold of amount plus the fee maximum, the transfer row and its receipt."""
    from core.wallet import limits, proposals, receipts
    from core.wallet.store import utcnow

    proposals._transition(conn, proposal.proposal_id, "approved", expected_state="pending_approval", detail={"transfer_state": STATE_CLAIMED})
    limits._reserve(conn, wallet_id=proposal.wallet_id, asset=proposal.asset, amount_minor=proposal.amount_minor, destination=proposal.destination,
                    proposal_id=proposal.proposal_id, now=now, fee_minor=fee_max_minor, chain=proposal.network)
    stamp = utcnow()
    conn.execute(
        "INSERT INTO wallet_transfers (proposal_id, wallet_id, network, environment, family, from_address, to_address, asset, amount_minor, "
        "fee_max_minor, quote_id, quote_digest, challenge_digest, nonce, blockhash, blockhash_slot, last_valid_block_height, owner_token, "
        "lease_until, dispatch_deadline, epoch_freeze, epoch_enabled, epoch_environment, enabled_generation, state, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (proposal.proposal_id, proposal.wallet_id, proposal.network, environment, family, from_address, proposal.destination, proposal.asset,
         int(proposal.amount_minor), int(fee_max_minor), quote_id, quote_digest, challenge_digest, nonce, blockhash, blockhash_slot,
         last_valid_block_height, owner_token, float(lease_until), float(dispatch_deadline), int(epochs["freeze"]), int(epochs["enabled"]),
         int(epochs["environment"]), int(enabled_generation), STATE_CLAIMED, stamp, stamp),
    )
    approved = proposals._get(conn, proposal.proposal_id)
    receipt = receipts._record(conn, approved, state=approved.state, extra={"transfer_state": STATE_CLAIMED})
    conn.execute("UPDATE wallet_transfers SET last_receipt_id = ? WHERE proposal_id = ?", (receipt.receipt_id, proposal.proposal_id))
    return get_transfer(conn, proposal.proposal_id) or {}


def transition(conn: Any, proposal_id: str, new_state: str, *, expected_state: str, now: float, owner_token: str | None = None,
               charged_fee_minor: int | None = None, detail: dict[str, Any] | None = None, **columns: Any) -> dict[str, Any]:
    """One v5 §2.2 row on the caller's connection: the transfer CAS, the proposal projection, the hold and the receipt.
    Raises TransferTransitionError (nothing written by this call survives the caller's rollback)."""
    from core.wallet import limits, proposals, receipts
    from core.wallet.store import utcnow

    projection = PROJECTION.get((expected_state, new_state))
    if projection is None:
        raise TransferTransitionError(f"edge_not_allowed:{expected_state}->{new_state}")
    unknown = sorted(set(columns) - _WRITABLE)
    if unknown:
        raise ValueError(f"not a writable transfer column: {unknown}")
    stamp = utcnow()
    sets = ["state = ?", "updated_at = ?"]
    values: list[Any] = [new_state, stamp]
    for name in sorted(columns):
        sets.append(f"{name} = ?")
        values.append(columns[name])
    if charged_fee_minor is not None:
        # the fee the chain charged is part of the record, not only of the hold's settlement
        sets.append("charged_fee_minor = ?")
        values.append(max(0, int(charged_fee_minor)))
    if projection.hold in (_HOLD_SETTLE, _HOLD_RESETTLE) and "fee_state" not in columns:
        # a settlement names what it knows about the fee: exact when the chain answered, else bounded by the ceiling
        sets.append("fee_state = ?")
        values.append(FEE_EXACT if charged_fee_minor is not None else FEE_BOUNDED)
        if charged_fee_minor is not None:
            sets.extend(["fee_known_minor = ?", "fee_missing_json = ?"])
            values.extend([max(0, int(charged_fee_minor)), "[]"])
        elif "fee_missing_json" not in columns:
            sets.append("fee_missing_json = ?")
            values.append(json.dumps(["fee"]))
    if new_state in TERMINAL_STATES and "raw_b64" not in columns:
        sets.append("raw_b64 = NULL")
    where = ["proposal_id = ?", "state = ?"]
    where_values: list[Any] = [str(proposal_id), expected_state]
    if owner_token is not None:
        where.append("owner_token = ?")
        where_values.append(owner_token)
    if projection.requires_no_cancel:
        where.append("cancel_requested_at IS NULL")
    cursor = conn.execute(f"UPDATE wallet_transfers SET {', '.join(sets)} WHERE {' AND '.join(where)}", (*values, *where_values))
    if cursor.rowcount != 1:
        row = get_transfer(conn, proposal_id)
        if row is None:
            raise TransferTransitionError(f"unknown_transfer:{proposal_id}")
        if row["state"] != expected_state:
            raise TransferTransitionError(f"expected_{expected_state}_found_{row['state']}")
        if owner_token is not None and row["owner_token"] != owner_token:
            raise TransferTransitionError("owner_token_mismatch")
        raise TransferTransitionError("cancel_requested")
    row = get_transfer(conn, proposal_id) or {}
    if projection.proposal_edge is not None:
        extra_columns: dict[str, Any] = {"tx_signature": row.get("tx_id") or ""} if projection.tx_signature_from_row else {}
        proposals._transition(conn, str(proposal_id), projection.proposal_edge[1], expected_state=projection.proposal_edge[0],
                              detail={"transfer_state": new_state, "reason": projection.reason, **dict(detail or {})}, **extra_columns)
    if projection.hold == _HOLD_RELEASE:
        limits._release(conn, str(proposal_id))
    elif projection.hold == _HOLD_SETTLE:
        limits._settle(conn, str(proposal_id), charged_fee_minor=charged_fee_minor, amount_moved=projection.amount_moved, now=now)
    elif projection.hold == _HOLD_RESETTLE:
        limits._resettle(conn, str(proposal_id), charged_fee_minor=charged_fee_minor, amount_moved=projection.amount_moved)
    current = proposals._get(conn, str(proposal_id))
    extra: dict[str, Any] = {"transfer_state": new_state, "reason": projection.reason, "evidence_kind": row.get("evidence_kind") or ""}
    if current is not None and str(current.origin) == proposals.ORIGIN_DNA_FEE:
        # a DNA fee collection: its ledger record moves with the transfer, in this same transaction
        from core.wallet import dna_fees

        followed = dna_fees.on_transfer_state(conn, str(proposal_id), new_state, tx_id=str(row.get("tx_id") or ""))
        if followed:
            extra["dna_fee_collection"] = {"collection_id": str(followed.get("collection_id") or ""), "state": str(followed.get("state") or ""), "amount_atomic": str(followed.get("amount_atomic") or "")}
    receipt = receipts._record(conn, current, state=current.state, tx_signature=str(row.get("tx_id") or ""), extra=extra)
    conn.execute("UPDATE wallet_transfers SET last_receipt_id = ? WHERE proposal_id = ?", (receipt.receipt_id, str(proposal_id)))
    row["last_receipt_id"] = receipt.receipt_id
    return row


__all__ = [
    "EXIT_TERMS", "IN_FLIGHT_STATES", "LIVE_STATES", "MAX_OPEN_PILOT_PROPOSALS", "NEVER_SENT_STATES", "NONCE_STATES", "OPEN_PROPOSAL_STATES", "PROJECTION",
    "STATE_CANCELLED", "STATE_CLAIMED", "STATE_CONFIRMED", "STATE_DISCARDED", "STATE_DISPATCHING", "STATE_FAILED_ON_CHAIN", "STATE_PENDING",
    "STATE_RELEASED", "STATE_SIGNED", "STATE_SIGNED_REVOKED", "STATE_STOPPED_WAITING", "STATE_UNKNOWN", "TERMINAL_STATES",
    "TransferTransitionError", "claim_resend", "explorer_url", "get_transfer", "get_transfer_by_id", "in_flight", "insert_claim", "is_pilot_transfer",
    "is_pilot_transfer_parts", "latest_receipt", "list_transfers", "live_for_account", "open_pilot_proposals", "release_marker", "take_marker",
    "transition", "update_columns", "view_of",
]
