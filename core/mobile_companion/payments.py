"""Phone proposals over the PC wallet. No signer or credential bypass lives here."""

from __future__ import annotations

import hashlib
import json
import re
import time

from core.mobile_companion.policy import enabled

_KEY = re.compile(r"^[A-Za-z0-9_-]{16,100}$")


def _source(device):
    from core.request_trust import HTTP_INGRESS_KEY, OWNER_LOCAL_KEY

    return {
        "surface": "channel" if device.get("kind") == "bot" else "mobile",
        "via": device["platform"] if device.get("kind") == "bot" else "mobile",
        "device_id": device["device_id"],
        HTTP_INGRESS_KEY: True,
        OWNER_LOCAL_KEY: False,
    }


def _fault(reason):
    from core.wallet.errors import WalletFault
    from core.web.api.mobile_companion_api import mobile_companion_service

    # This can run inside the wallet's write transaction. Its fault-plane
    # recorder opens another DB connection, so receipt to this authority's
    # independent journal and let the caller roll the wallet transaction back.
    mobile_companion_service().store.append_receipt("security.phone_payment_refused", detail={"reason": reason})
    return WalletFault(
        "wallet_approval_rejected",
        context={"reason": reason},
        message="This phone cannot authorize that payment under its current permissions and limits.",
    )


def _table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS mobile_payment_claims (
        proposal_id TEXT PRIMARY KEY, device_id TEXT NOT NULL, payload_digest TEXT NOT NULL,
        fee_minor INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
        permission_revision INTEGER NOT NULL DEFAULT 0, grant_id TEXT NOT NULL DEFAULT '',
        notification_state TEXT NOT NULL DEFAULT '', request_key TEXT NOT NULL DEFAULT '')""")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(mobile_payment_claims)")}
    for name, declaration in {
        "permission_revision": "INTEGER NOT NULL DEFAULT 0",
        "grant_id": "TEXT NOT NULL DEFAULT ''",
        "notification_state": "TEXT NOT NULL DEFAULT ''",
        "request_key": "TEXT NOT NULL DEFAULT ''",
        "freeze_epoch": "INTEGER NOT NULL DEFAULT -1",
        "review_json": "TEXT NOT NULL DEFAULT '{}'",
    }.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE mobile_payment_claims ADD COLUMN {name} {declaration}")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS mobile_payment_request_identity ON mobile_payment_claims(device_id,request_key) WHERE request_key<>''"
    )



def refuse_unresolved_duplicate(conn, binding, proposal_id=""):
    """Consult canonical transfers across every origin, until their outcome is known."""
    row = conn.execute(
        """SELECT p.proposal_id FROM wallet_proposals p
        LEFT JOIN wallet_transfers t ON t.proposal_id=p.proposal_id
        WHERE p.wallet_id=? AND p.network=? AND p.asset=? AND p.amount_minor=?
        AND p.destination=? AND p.proposal_id<>? AND (
            t.state IN ('signed','signed_revoked','dispatching','unknown','pending','stopped_waiting')
            OR (t.proposal_id IS NULL AND
                (p.state IN ('signed','broadcast') OR
                 (p.tx_signature<>'' AND p.state IN ('rejected','failed','expired')))))
        LIMIT 1""",
        (*(binding[key] for key in ("wallet_id","network","asset","amount_minor","destination")), proposal_id),
    ).fetchone()
    if row:
        raise _fault("phone_payment_outcome_unknown_reconcile_first")


def pc_review(proposal, *, conn=None):
    """PC-owned review facts, bound into the canonical quote and execution claim."""
    if not str(proposal.idempotency_key or "").startswith("mobile:"):
        return None
    if conn is None:
        from core.wallet.store import connection

        with connection() as opened:
            return pc_review(proposal, conn=opened)
    _table(conn)
    row = conn.execute("SELECT payload_digest,review_json FROM mobile_payment_claims WHERE proposal_id=?", (proposal.proposal_id,)).fetchone()
    try:
        review = json.loads(row[1]) if row else {}
    except (ValueError, TypeError):
        review = {}
    if (
        not row or review.get("digest") != row[0]
        or not isinstance(review.get("warnings"), list)
        or review.get("acknowledged") != review["warnings"]
        or any(warning not in {"new_payee", "unusual_amount"} for warning in review["warnings"])
    ):
        raise _fault("phone_payment_review_evidence_missing")
    return review

def _check(conn, proposal, device, *, fee=0):
    from core.mobile_companion.listener import load_settings
    from core.wallet import chains, limits

    if not load_settings()["enabled"]:
        raise _fault("phone_access_off")
    if device.get("status") != "active" or not enabled(device, "payment_propose"):
        raise _fault("phone_revoked_or_payment_right_off_or_expired")
    cap = device.get("payment_cap") or {}
    if (proposal.wallet_id, chains.resolve_network(proposal.network).network, proposal.asset) != (
        cap.get("wallet_id"),
        cap.get("network"),
        cap.get("asset"),
    ):
        raise _fault("phone_account_or_asset_not_allowed")
    # Use the wallet's transaction-aware authority helpers. Opening a second
    # store connection while this claim holds its writer would deadlock.
    wallet_cap = limits._load_limits(conn, proposal.wallet_id, proposal.asset)
    amount = int(proposal.amount_minor) + max(0, int(fee))
    per = min(
        int(cap.get("per_payment_minor") or 0),
        max(0, wallet_cap.per_tx_minor - 1) if wallet_cap.per_tx_minor else 2**63 - 1,
    )
    daily = min(
        int(cap.get("daily_minor") or 0), max(0, wallet_cap.daily_minor - 1) if wallet_cap.daily_minor else 2**63 - 1
    )
    if amount <= 0 or amount > per or limits._frozen(conn):
        raise _fault("phone_payment_cap_or_freeze")
    # Pending requests consume their phone allowance too. Unknown or signed
    # outcomes retain it without aging out. Confirmed payments count from
    # confirmation time; only a positively unsent terminal row releases it.
    rows = conn.execute(
        """SELECT p.amount_minor, m.fee_minor FROM mobile_payment_claims m
        JOIN wallet_proposals p ON p.proposal_id=m.proposal_id
        WHERE m.device_id=? AND p.wallet_id=? AND p.asset=? AND p.network=?
        AND p.proposal_id<>? AND (
            p.state NOT IN ('rejected','failed','expired','confirmed')
            OR (p.state='confirmed' AND COALESCE(CAST(strftime('%s',p.updated_at) AS REAL),9e30)>?)
            OR (p.state IN ('rejected','failed','expired') AND p.tx_signature<>''))""",
        (
            device["device_id"],
            proposal.wallet_id,
            proposal.asset,
            proposal.network,
            proposal.proposal_id,
            time.time() - 86400,
        ),
    ).fetchall()
    if sum(int(row[0]) + int(row[1]) for row in rows) + amount > daily:
        raise _fault("phone_daily_cap")


def execution_guard(proposal, *, conn=None, fee_minor=None):
    """Called by the wallet, including its atomic claim and transmit guards."""
    from core.wallet.store import connection
    from core.web.api.mobile_companion_api import mobile_companion_service

    if not str(proposal.idempotency_key or "").startswith("mobile:"):
        return
    if conn is None:
        with connection() as opened:
            return execution_guard(proposal, conn=opened, fee_minor=fee_minor)
    _table(conn)
    row = conn.execute(
        "SELECT device_id, fee_minor, permission_revision, grant_id, freeze_epoch FROM mobile_payment_claims WHERE proposal_id=?",
        (proposal.proposal_id,),
    ).fetchone()
    if row is None:
        raise _fault("phone_payment_claim_missing")
    device = mobile_companion_service().store.device(row[0]) or {}
    from core.wallet import controls

    if row[4] != controls.epochs(conn)["freeze"]:
        raise _fault("phone_payment_freeze_changed_new_request_required")
    if (
        int(device.get("permission_revision") or 0) != int(row[2])
        or (device.get("grant") or {}).get("grant_id") != row[3]
    ):
        raise _fault("phone_permission_changed_new_request_required")
    fee = int(row[1]) if fee_minor is None else int(fee_minor)
    pc_review(proposal, conn=conn)
    _check(conn, proposal, device, fee=fee)
    if fee_minor is not None:
        conn.execute("UPDATE mobile_payment_claims SET fee_minor=? WHERE proposal_id=?", (fee, proposal.proposal_id))


def intent(params, device):
    from core.contacts.consumers import resolve_wallet_recipient
    from core.wallet import amounts, chains

    token = str(params.get("idempotency_key") or "")
    if not _KEY.fullmatch(token):
        return 400, {"error": "A saved payment request identity is required."}
    cap = device.get("payment_cap") or {}
    if "amount" in params:
        if "amount_minor" in params:
            return 400, {"error": "Choose one exact amount."}
        asset = chains.native_asset(str(cap.get("network") or ""))
        params = {
            **params,
            "amount_minor": amounts.parse_human_amount(params["amount"], decimals=asset.decimals, symbol=asset.symbol),
        }
    if type(params.get("amount_minor")) is not int or params["amount_minor"] <= 0:
        return 400, {"error": "Choose a positive amount in the asset's smallest units."}
    target = resolve_wallet_recipient(
        str(params.get("destination") or ""), network=str(cap.get("network") or ""), asset=str(cap.get("asset") or "")
    )
    if not target.ok:
        return 400, {"error": target.message, "code": "wallet_recipient_refused"}
    recipient = target.snapshots[0]
    binding = {
        "wallet_id": cap.get("wallet_id"),
        "network": cap.get("network"),
        "asset": cap.get("asset"),
        "amount_minor": params["amount_minor"],
        "destination": recipient["value"],
    }
    digest = hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return 200, {"token": token, "binding": binding, "recipient": recipient, "digest": digest}


def propose(params, *, service, device):
    from core.wallet import amounts, chains, lifecycle, proposals

    code, value = intent(params, device)
    if code != 200:
        return code, value
    token, binding, recipient, digest = (value[key] for key in ("token", "binding", "recipient", "digest"))

    def claim(conn, proposal, minted):
        from core.mobile_companion.payment_review import require

        review = require(params, service=service, device=device, digest=digest, conn=conn)
        refuse_unresolved_duplicate(conn, binding, proposal.proposal_id)
        _table(conn)
        identity = conn.execute(
            "SELECT proposal_id, payload_digest FROM mobile_payment_claims WHERE device_id=? AND request_key=?",
            (device["device_id"], token),
        ).fetchone()
        if identity and (identity[0] != proposal.proposal_id or identity[1] != digest):
            raise _fault("phone_request_already_decided_or_identity_conflict")
        row = conn.execute(
            "SELECT payload_digest, permission_revision, grant_id, freeze_epoch FROM mobile_payment_claims WHERE proposal_id=?",
            (proposal.proposal_id,),
        ).fetchone()
        if row and row[0] != digest:
            raise _fault("phone_payment_identity_conflict")
        fresh = service.store.device(device["device_id"]) or {}
        from core.wallet import controls

        freeze_epoch = controls.epochs(conn)["freeze"]
        if row and row[3] != freeze_epoch:
            raise _fault("phone_payment_freeze_changed_new_request_required")
        if row and (
            row[1] != int(fresh.get("permission_revision") or 0) or row[2] != (fresh.get("grant") or {}).get("grant_id")
        ):
            raise _fault("phone_permission_changed_new_request_required")
        _check(conn, proposal, fresh)
        conn.execute(
            "INSERT OR IGNORE INTO mobile_payment_claims (proposal_id,device_id,payload_digest,created_at,permission_revision,grant_id,request_key,freeze_epoch,review_json) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                proposal.proposal_id,
                device["device_id"],
                digest,
                time.time(),
                int(fresh.get("permission_revision") or 0),
                (fresh.get("grant") or {}).get("grant_id", ""),
                token,
                freeze_epoch,
                json.dumps(review, sort_keys=True),
            ),
        )

    context = _source(device)
    proposal = proposals.propose_transaction(
        **binding,
        origin=proposals.ORIGIN_USER,
        recipient=recipient,
        idempotency_key="mobile:" + hashlib.sha256(device["device_id"].encode()).hexdigest()[:20] + ":" + token,
        source_context=context,
        claim=claim,
    )
    prepared = lifecycle.default_lifecycle(source_context=context).prepare(proposal.proposal_id)
    service.store.append_receipt(
        "payment.proposed",
        device_id=device["device_id"],
        detail={"proposal_id": proposal.proposal_id, "via": _source(device)["via"]},
    )
    return 200, {
        "proposal": prepared.to_dict(),
        "display_amount": amounts.format_minor(prepared.amount_minor, chains.native_asset(prepared.network).decimals),
        "asset": prepared.asset,
        "network": prepared.network,
        "approval_location": "computer",
        "message": "Review and approve this payment on your computer. Completed payments cannot be undone.",
    }


def handle(action, params, *, service, device):
    from core.wallet import balances, limits, proposals, receipts, settlement, status
    from core.wallet.errors import WalletFault

    try:
        if action == "wallet.status":
            view = status.wallet_status()
            from core.wallet.store import connection

            with connection() as conn:
                _table(conn)
                owned = {
                    row[0]
                    for row in conn.execute(
                        "SELECT proposal_id FROM mobile_payment_claims WHERE device_id=?", (device["device_id"],)
                    )
                }
            for row in view.get("pending", []):
                row["mobile_owned"] = row["proposal_id"] in owned
            return 200, {
                "status": view,
                "frozen": limits.is_frozen(),
                "phone_approval": "unavailable_hardware_key_unproven",
            }
        if action == "wallet.balances":
            return 200, balances.read_balance(str(params.get("wallet_id") or ""), source_context=_source(device))
        if action == "wallet.receipts":
            from core.wallet import amounts, chains
            from core.wallet.store import connection

            rows = receipts.list_receipts(limit=50)
            with connection() as conn:
                _table(conn)
                origins = dict(conn.execute("SELECT proposal_id,device_id FROM mobile_payment_claims"))
            for row in rows:
                # JavaScript cannot represent every on-chain integer. The exact
                # human string is computed on the wallet side, never in the UI.
                raw = row.get("amount_minor")
                row["amount_minor"] = str(raw) if type(raw) is int else raw
                try:
                    asset = chains.asset_for(row["network"], row["asset"])
                    row["display_amount"] = amounts.format_minor(int(raw), asset.decimals)
                except (ValueError, KeyError, TypeError, WalletFault):
                    row["display_amount"] = ""
                origin = origins.get(row.get("proposal_id"))
                row["via"] = (
                    (origin.partition(":")[2] if origin.startswith("bot:") else "mobile")
                    if origin
                    else row.get("origin", "")
                )
                row["device_id"] = origin or ""
            return 200, {"receipts": rows}
        if action == "wallet.contacts":
            from core.contacts.store import list_contacts

            rows = list_contacts(str(params.get("search") or "")[:100], limit=50)
            contacts = []
            for row in rows:
                endpoints = [
                    {key: endpoint.get(key) for key in ("kind", "label", "value", "chain_network", "verification")}
                    for endpoint in row.get("endpoints", [])
                    if endpoint.get("kind") == "wallet"
                ]
                if endpoints:
                    contacts.append(
                        {"display_name": row["display_name"], "contact_id": row["contact_id"], "endpoints": endpoints}
                    )
            return 200, {"contacts": contacts}
        if action == "wallet.pending":
            return 200, {"pending": status.wallet_status().get("pending", [])}
        if action == "wallet.approve":
            service.store.append_receipt(
                "payment.approval_refused", device_id=device["device_id"], detail={"reason": "hardware_key_unproven"}
            )
            return 403, {
                "code": "approval_key_unproven_use_computer",
                "error": "Approve this payment on your computer.",
            }
        if action == "wallet.preview":
            from core.mobile_companion.payment_review import preview

            return preview(params, service=service, device=device)
        if action == "wallet.propose":
            return propose(params, service=service, device=device)
        if action == "wallet.decline":
            from core.wallet.store import connection

            proposal = proposals.get_proposal(str(params.get("proposal_id") or ""))
            with connection() as conn:
                _table(conn)
                row = conn.execute(
                    "SELECT device_id FROM mobile_payment_claims WHERE proposal_id=?", (params.get("proposal_id"),)
                ).fetchone()
            if not proposal or not row or row[0] != device["device_id"]:
                return 404, {"error": "This phone did not propose that payment."}
            result = settlement.request_cancel(proposal.proposal_id)
            service.store.append_receipt(
                "payment.decline_requested",
                device_id=device["device_id"],
                detail={
                    "proposal_id": proposal.proposal_id,
                    "cancelled": bool(result.get("cancelled")),
                    "state": result.get("state"),
                },
            )
            return 200, result
    except WalletFault as exc:
        identity = hashlib.sha256(
            (
                device["device_id"] + ":" + str(params.get("idempotency_key") or params.get("proposal_id") or action)
            ).encode()
        ).hexdigest()
        payment_action = action in {"wallet.propose", "wallet.approve", "wallet.decline"}
        if payment_action:
            _notify("payment_refused", identity)
        service.store.append_receipt(
            "payment.refused" if payment_action else "wallet.view_refused",
            device_id=device["device_id"],
            detail={"code": exc.code, "action": action},
        )
        return 403, {"code": exc.code, "error": exc.user_message}
    return 403, {"error": "Payment action unavailable."}


def _notify(kind, identity, *, proposal_id="", occurred_at=""):
    from core.mobile_companion.notifications import TYPES
    from core.operator import notification_center

    return notification_center.record_item(
        dedupe_key="mobile-payment:" + identity,
        source_kind="background_run",
        title=TYPES[kind],
        payload={"event_type": kind, "proposal_id": proposal_id, "occurred_at": occurred_at},
    )


def sync_wallet_events(device=None, service=None):
    """Project committed wallet states, with canonical inbox deduplication across crashes.

    Catch-up is bounded per poll, not by age. A phone returning after days must
    still find every missed terminal state. Events before its pairing remain
    available as records without becoming new alerts.
    """
    from core.wallet.store import connection

    if device is not None and not any(
        enabled(device, name) for name in ("alerts", "wallet_view", "payment_propose", "payment_approve")
    ):
        return
    with connection() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS mobile_wallet_event_cursor (proposal_id TEXT PRIMARY KEY, state TEXT NOT NULL)"
        )
        _table(conn)
        rows = conn.execute("""SELECT p.proposal_id, p.state, m.device_id,
            (SELECT receipt_id FROM wallet_receipts WHERE proposal_id=p.proposal_id ORDER BY created_at DESC LIMIT 1), p.updated_at
            FROM wallet_proposals p LEFT JOIN mobile_wallet_event_cursor c ON c.proposal_id=p.proposal_id
            LEFT JOIN mobile_payment_claims m ON m.proposal_id=p.proposal_id
            WHERE COALESCE(c.state,'')<>p.state
            ORDER BY p.updated_at,p.proposal_id LIMIT 200""").fetchall()
    for proposal_id, state, device_id, receipt_id, updated_at in rows:
        kind = {
            "pending_approval": "payment_pending",
            "awaiting_signature": "payment_pending",
            "confirmed": "payment_done",
            "failed": "payment_refused",
            "rejected": "payment_refused",
            "expired": "payment_refused",
        }.get(state)
        if kind:
            _notify(kind, proposal_id + ":" + state, proposal_id=proposal_id, occurred_at=updated_at)
            if service is not None and device_id:
                service.store.append_receipt(
                    "payment.wallet_state",
                    device_id=device_id,
                    detail={
                        "via": "mobile",
                        "proposal_id": proposal_id,
                        "state": state,
                        "wallet_receipt_id": receipt_id or "",
                    },
                )
        with connection() as conn:
            conn.execute(
                "INSERT INTO mobile_wallet_event_cursor (proposal_id,state) VALUES (?,?) ON CONFLICT(proposal_id) DO UPDATE SET state=excluded.state",
                (proposal_id, state),
            )
