"""Durable action-bound approval for native email send/reply (B4, PA lane).

The old gate was two booleans (``allow_send`` + ``approve``) inside the SAME model
tool call — a prompt-injected or hallucinated tool call could satisfy its own
confirmation. This module replaces that with the operator pending-action ledger:

* the MODEL lane may only create/refresh a PREVIEW (a ``pending_approval`` row of
  kind ``email_send`` whose scope is the exact content: recipients, subject, body
  hash, account, thread headers);
* the SEND executes only from the CHAT lane, when the operator's own text carries
  an approval phrase plus the action id ("approve send <uuid>") — the same
  durable-approval pattern the calendar action already uses. The action id is a
  UUID capability token: typing it is the consent;
* a NEWER pending draft supersedes an older one: approving a stale id after the
  draft changed is refused, so an approval can never fire content it did not
  preview (changed-draft-after-approval journey);
* "cancel/deny/reject send <id>" retires a pending approval without sending.

Outward-facing by law: ``email_send`` is classified outward_facing + privacy
sensitive in the execution gate, so it requires the user's explicit approval in
EVERY autonomy mode — hands-off and bypass included, like money-class actions.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

ACTION_KIND = "email_send"

#: A pending email preview older than this is STALE: the draft it previewed was
#: authored in a different context; approving it now would send unreviewed
#: content. Refused with a fresh-preview instruction (trust hygiene the reviewer
#: flagged as missing on operator approvals generally — applied here first).
DEFAULT_PREVIEW_TTL_SECONDS = 24 * 3600

_DENY_MARKERS = ("cancel", "deny", "reject", "don't send", "do not send", "don't approve", "do not approve")


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def body_fingerprint(body: str) -> str:
    return hashlib.sha256(str(body or "").encode("utf-8")).hexdigest()


def _normalize_scope(scope: dict[str, Any]) -> dict[str, Any]:
    to = scope.get("to")
    recipients = [to] if isinstance(to, str) else [str(r) for r in (to or [])]
    body = str(scope.get("body") or "")
    return {
        "kind": str(scope.get("kind") or "send"),
        "to": sorted(r.strip() for r in recipients if str(r).strip()),
        "subject": str(scope.get("subject") or ""),
        "body": body,
        "body_sha256": str(scope.get("body_sha256") or body_fingerprint(body)),
        "account": str(scope.get("account") or ""),
        "in_reply_to": str(scope.get("in_reply_to") or ""),
    }


def scope_for(*, kind: str, to: Any, subject: str, body: str, account: str, in_reply_to: str = "") -> dict[str, Any]:
    return _normalize_scope({
        "kind": kind, "to": to, "subject": subject, "body": body,
        "body_sha256": body_fingerprint(body),
        "account": account, "in_reply_to": in_reply_to,
    })


def _get_connection():
    from storage.db import get_connection

    return get_connection()


def find_pending_send(*, get_connection_fn: Callable[[], Any] | None = None) -> dict[str, Any] | None:
    """The newest still-pending email draft across sessions. One operator, one
    local store: a conversation-scoped lookup would let a second conversation's
    preview hide a first one the user is about to approve."""
    conn = (get_connection_fn or _get_connection)()
    try:
        row = conn.execute(
            """
            SELECT * FROM operator_action_requests
            WHERE action_kind = ? AND status = 'pending_approval'
            ORDER BY created_at DESC LIMIT 1
            """,
            (ACTION_KIND,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_send_preview(
    *,
    kind: str,
    to: Any,
    subject: str,
    body: str,
    account: str,
    in_reply_to: str = "",
    session_id: str = "",
    task_id: str = "",
    now_fn: Callable[[], str] = _now_iso,
    get_connection_fn: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Create (or reuse) the durable pending approval for this exact content and
    return the preview payload. Reusing a scope-identical pending row keeps the
    preview idempotent: asking twice yields ONE approval id, never two."""
    from core.operator.approvals import create_pending_action

    scope = scope_for(
        kind=kind, to=to, subject=subject, body=body, account=account, in_reply_to=in_reply_to
    )
    conn = (get_connection_fn or _get_connection)()
    try:
        row = conn.execute(
            """
            SELECT * FROM operator_action_requests
            WHERE action_kind = ? AND status = 'pending_approval'
            ORDER BY created_at DESC LIMIT 1
            """,
            (ACTION_KIND,),
        ).fetchone()
    finally:
        conn.close()
    if row is not None:
        existing_scope = _normalize_scope(json.loads(str(dict(row).get("scope_json") or "{}")))
        if existing_scope == scope:
            return {"action_id": str(dict(row)["action_id"]), "scope": scope, "reused": True}
    action_id = create_pending_action(
        session_id=str(session_id or ""),
        task_id=str(task_id or ""),
        action_kind=ACTION_KIND,
        scope=scope,
        now_fn=now_fn,
        get_connection_fn=get_connection_fn or _get_connection,
    )
    return {"action_id": action_id, "scope": scope, "reused": False}


def load_for_approval(
    action_id: str,
    *,
    get_connection_fn: Callable[[], Any] | None = None,
) -> dict[str, Any] | None:
    """Load an email draft by its action id for the approval/cancellation lane.
    Refuses (returns None) when a NEWER pending draft exists — approving a stale
    preview after the content changed must not send the old content."""
    conn = (get_connection_fn or _get_connection)()
    try:
        row = conn.execute(
            "SELECT * FROM operator_action_requests WHERE action_id = ? AND action_kind = ?",
            (str(action_id or ""), ACTION_KIND),
        ).fetchone()
        if row is None:
            return None
        row = dict(row)
        if str(row.get("status")) != "pending_approval":
            return row  # caller reports executed/superseded honestly
        try:
            from datetime import datetime, timezone

            created = datetime.fromisoformat(str(row.get("created_at") or ""))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - created).total_seconds()
            if age > DEFAULT_PREVIEW_TTL_SECONDS:
                return {"expired": True, **row}
        except (TypeError, ValueError):
            pass  # an unreadable timestamp never blocks an approval
        newer = conn.execute(
            """
            SELECT action_id FROM operator_action_requests
            WHERE action_kind = ? AND status = 'pending_approval'
              AND created_at > ? AND action_id != ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (ACTION_KIND, str(row.get("created_at") or ""), str(row.get("action_id") or "")),
        ).fetchone()
        if newer is not None:
            return {"stale": True, "superseded_by": str(dict(newer)["action_id"]), **row}
        return row
    finally:
        conn.close()


def cancel_pending_send(
    action_id: str,
    *,
    now_fn: Callable[[], str] = _now_iso,
    get_connection_fn: Callable[[], Any] | None = None,
) -> bool:
    """Retire a pending draft without sending (deny/cancel journey). Only a
    still-pending row can be cancelled; True when it was."""
    conn = (get_connection_fn or _get_connection)()
    try:
        cur = conn.execute(
            """
            UPDATE operator_action_requests
            SET status = 'cancelled', updated_at = ?
            WHERE action_id = ? AND action_kind = ? AND status = 'pending_approval'
            """,
            (now_fn(), str(action_id or ""), ACTION_KIND),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def denial_requested(raw_text: str) -> bool:
    lowered = str(raw_text or "").lower()
    return any(marker in lowered for marker in _DENY_MARKERS)


def render_preview_text(scope: dict[str, Any], *, action_id: str, body: str) -> str:
    recipients = ", ".join(str(r) for r in scope.get("to") or [])
    verb = "Reply" if str(scope.get("kind")) == "reply" else "Email"
    digest = " ".join(str(body or "").split())
    if len(digest) > 200:
        digest = digest[:200] + "…"
    lines = [
        f"{verb} preview ready.",
        f"- To: {recipients or '(no recipient)'}",
        f"- Subject: {scope.get('subject') or '(no subject)'}",
        f"- Account: {scope.get('account') or 'default'}",
        f"- Body ({len(str(body or ''))} chars): {digest}",
    ]
    if str(scope.get("in_reply_to") or ""):
        lines.append(f"- In-Reply-To: {scope['in_reply_to']}")
    lines.append("")
    lines.append(f"Reply with: approve send {action_id} — or: cancel send {action_id}")
    return "\n".join(lines)


__all__ = [
    "ACTION_KIND",
    "body_fingerprint",
    "cancel_pending_send",
    "create_send_preview",
    "denial_requested",
    "find_pending_send",
    "load_for_approval",
    "render_preview_text",
    "scope_for",
]
