"""Operator Profile: the ONE typed, privacy-governed store of what VOOL remembers about you.

This replaces the standalone "tell VOOL how to address you" settings field. A profile item is a
typed fact about the operator (preferred name, language, timezone, response preferences, email
signature, the default email/social account as an OPAQUE credential-binding reference) that VOOL
learned from conversation or from the Settings surface, and that the operator can see, edit, move
between scopes, forget, pause, export and restore.

It is not a second memory authority. It composes the authorities that already exist:

* **storage** -- the runtime SQLite database (``storage.db``) and its migration authority
  (``storage.migrations.ensure_operator_profile_tables``);
* **privacy (A8)** -- every write consults ``core.finalization.writer_may_persist_text`` (the one
  ERASE-dominance predicate), every read re-verifies it AND the source turn's request lineage
  (``payload_availability_for_request_id``), and the A8 erasure traversal runs a profile sweep step;
* **secrets** -- ``core.secret_redaction.contains_secret`` OR ``core.privacy_guard.secret_material_present``
  refuse a value before it is stored anywhere; the reported change carries the scrubbed value;
* **credentials** -- an account preference stores ``credential:<name>``, validated by presence
  against ``core.credential_store`` (name only; the value is never read here);
* **permissions** -- nothing in this module is consulted by the permission policy; a profile
  item is context, never authority. The static guard test pins that this module imports no
  permission, approval or credential-value surface.

Memory law (each clause has a test):

* explicit "remember/save/from now on" persists immediately and reports the exact change;
* a strong implicit stable preference becomes a CANDIDATE (chat-scoped, status ``candidate``)
  that the operator confirms with Save / Edit / Only this chat;
* a weak inference stays chat-local and is never promoted (``move_scope`` refuses it);
* a contradiction with an active item creates a conflict candidate; the operator resolves it with
  replace / scope / keep both -- nothing is overwritten silently;
* sensitive identity/account facts are never inferred into durable memory;
* every change is a new revision with a history row; forget is a tombstone; edits are CAS on
  ``revision`` so two concurrent writers cannot both win.

Scopes resolve deterministically: ``chat`` > ``project`` > the chat's context (``work`` /
``personal``) > ``global``. A ``chat``-scoped item never leaves its chat.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from core.memory.files import utcnow
from core.privacy_guard import scrub_secret_material, secret_material_present
from core.request_trust import request_is_owner_local
from core.secret_redaction import contains_secret, redact_secrets
from storage.db import get_connection

__all__ = [
    "CATEGORIES",
    "OWNER_PRINCIPAL",
    "SCOPES",
    "ProfileChange",
    "ProfileItem",
    "RevisionConflict",
    "decide_candidate",
    "edit_item",
    "erase_governed_items",
    "export_profile",
    "forget_item",
    "get_item",
    "history_for_item",
    "hydration_for_turn",
    "is_paused",
    "list_items",
    "move_scope",
    "note_chat_local",
    "principal_for_request",
    "propose_candidate",
    "remember",
    "resolve",
    "resolve_conflict",
    "restore_previous",
    "set_paused",
    "touch_used",
    "value_is_secret",
]

OWNER_PRINCIPAL = "owner_local"
SCOPES = ("global", "work", "personal", "project", "chat")
CONTEXT_SCOPES = ("work", "personal")
ORIGINS = ("explicit", "inferred", "settings")
STATUS_ACTIVE = "active"
STATUS_CANDIDATE = "candidate"
STATUS_DELETED = "deleted"
SENSITIVITY_LOW = "low"
SENSITIVITY_PERSONAL = "personal"
SENSITIVITY_IDENTITY = "identity"
SENSITIVITY_ACCOUNT = "account"

#: Weak inference stays chat-local: any inferred item below this confidence may never be promoted.
STRONG_INFERENCE_CONFIDENCE = 0.8

#: Category -> (value type, sensitivity class, human label, may be inferred into a CANDIDATE?).
#: ``identity`` and ``account`` classes are never learned from inference at all; ``personal``
#: classes may become a candidate but never a durable fact without the operator's confirmation.
CATEGORIES: dict[str, dict[str, Any]] = {
    "preferred_name": {"type": "text", "sensitivity": SENSITIVITY_PERSONAL, "label": "preferred name", "max": 60, "inferable": True},
    "language": {"type": "text", "sensitivity": SENSITIVITY_LOW, "label": "language", "max": 40, "inferable": True},
    "locale": {"type": "text", "sensitivity": SENSITIVITY_LOW, "label": "locale", "max": 40, "inferable": True},
    "timezone": {"type": "timezone", "sensitivity": SENSITIVITY_LOW, "label": "timezone", "max": 64, "inferable": True},
    "response_style": {"type": "text", "sensitivity": SENSITIVITY_LOW, "label": "response style", "max": 200, "inferable": True},
    "format_preference": {"type": "text", "sensitivity": SENSITIVITY_LOW, "label": "formatting", "max": 200, "inferable": True},
    "email_signature": {"type": "multiline", "sensitivity": SENSITIVITY_IDENTITY, "label": "email signature", "max": 400, "inferable": False},
    "default_account.email": {"type": "credential_binding", "sensitivity": SENSITIVITY_ACCOUNT, "label": "default email account", "max": 120, "inferable": False},
    "default_account.social": {"type": "credential_binding", "sensitivity": SENSITIVITY_ACCOUNT, "label": "default social account", "max": 120, "inferable": False},
    "context_mode": {"type": "enum", "sensitivity": SENSITIVITY_LOW, "label": "chat context", "max": 16, "inferable": True, "choices": ("work", "personal")},
    # C2: the ONLY shipping-adjacent fact is this coarse, non-sensitive REGION enum.
    # A full address is operator PII that must never become an ordinary memory item —
    # it ships only behind the future encrypted address authority exposing an opaque
    # shipping_profile_id. Never auto-captured, never inferred, enum-only.
    "shipping_region_preference": {
        "type": "enum",
        "sensitivity": SENSITIVITY_LOW,
        "label": "delivery region (coarse)",
        "max": 40,
        "inferable": False,
        "choices": ("Europe", "North America", "Asia-Pacific", "Africa", "Oceania", "South America", "Prefer not to say"),
    },
}

#: Credential-store name families an account preference may point at, per category. The store
#: itself stays the naming authority (core.email_tools / core.x_tools document these slots).
_BINDING_FAMILIES: dict[str, tuple[str, ...]] = {
    "default_account.email": ("email.smtp.", "email.imap."),
    "default_account.social": ("x.api.",),
}
_BINDING_REF_RE = re.compile(r"^credential:[A-Za-z0-9_.@+\-]{1,100}$")
_CREDENTIAL_NAME_RE = re.compile(r"^[A-Za-z0-9_.@+\-]{1,100}$")
_CHANNEL_SURFACES = frozenset({"discord", "telegram", "slack", "whatsapp", "channel"})
_WRITE_LOCK = threading.RLock()
_TABLES_READY: set[str] = set()


class RevisionConflictError(Exception):
    """A CAS write lost: the item moved past ``expected_revision``."""


#: Short alias used across the runtime and the API layer.
RevisionConflict = RevisionConflictError


class ProfileValidationError(ValueError):
    """The category or value shape is not one the profile stores."""


@dataclass(frozen=True)
class ProfileItem:
    item_id: str
    principal: str
    category: str
    value: Any
    value_text: str
    scope: str
    scope_key: str
    origin: str
    confidence: float
    sensitivity: str
    status: str
    source_session_id: str
    source_turn_id: str
    request_id: str
    conflict_with: str
    created_at: str
    updated_at: str
    last_used_at: str
    expires_at: str
    deleted_at: str
    revision: int

    @property
    def label(self) -> str:
        return str(CATEGORIES.get(self.category, {}).get("label") or self.category)

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "category": self.category,
            "label": self.label,
            "value": self.value,
            "value_text": self.value_text,
            "scope": self.scope,
            "scope_key": self.scope_key,
            "origin": self.origin,
            "confidence": self.confidence,
            "sensitivity": self.sensitivity,
            "status": self.status,
            "source_session_id": self.source_session_id,
            "source_turn_id": self.source_turn_id,
            "conflict_with": self.conflict_with,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_used_at": self.last_used_at,
            "expires_at": self.expires_at,
            "deleted_at": self.deleted_at,
            "revision": self.revision,
        }


@dataclass(frozen=True)
class ProfileChange:
    """What a write did, in words the operator can read back exactly.

    ``kind`` is one of: ``saved`` (new item), ``updated`` (new revision), ``unchanged`` (same
    value already active), ``candidate`` (awaiting Save / Edit / Only this chat), ``conflict``
    (awaiting replace / scope / keep both), ``chat_local`` (weak inference, this chat only),
    ``forgotten``, ``refused_secret``, ``refused_sensitive``, ``paused``, ``refused_privacy``
    (A8 write fence), ``refused_binding`` (unknown credential name), ``no_principal``.
    """

    kind: str
    report: str
    item: ProfileItem | None = None
    previous: ProfileItem | None = None
    used: list[dict[str, Any]] = field(default_factory=list)

    @property
    def persisted(self) -> bool:
        return self.kind in {"saved", "updated", "forgotten"}

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "report": self.report,
            "item": self.item.as_dict() if self.item else None,
            "previous": self.previous.as_dict() if self.previous else None,
        }


# --------------------------------------------------------------------------- store plumbing


def _conn():
    conn = get_connection()
    from storage.db import active_default_db_path

    key = str(active_default_db_path())
    if key not in _TABLES_READY:
        from storage.migrations import ensure_operator_profile_tables

        ensure_operator_profile_tables(conn)
        conn.commit()
        _TABLES_READY.add(key)
    return conn


def reset_table_cache_for_tests() -> None:
    _TABLES_READY.clear()


def _current_request_lineage() -> str:
    try:
        from core.semantic.semantic_admissions import current_request_id

        return str(current_request_id() or "")
    except Exception:
        return ""


def _row_to_item(row: Any) -> ProfileItem:
    try:
        value = json.loads(str(row["value_json"] or "null"))
    except (TypeError, ValueError):
        value = None
    return ProfileItem(
        item_id=str(row["item_id"]),
        principal=str(row["principal"]),
        category=str(row["category"]),
        value=value,
        value_text=str(row["value_text"] or ""),
        scope=str(row["scope"] or "global"),
        scope_key=str(row["scope_key"] or ""),
        origin=str(row["origin"] or "explicit"),
        confidence=float(row["confidence"] or 0.0),
        sensitivity=str(row["sensitivity"] or SENSITIVITY_PERSONAL),
        status=str(row["status"] or STATUS_ACTIVE),
        source_session_id=str(row["source_session_id"] or ""),
        source_turn_id=str(row["source_turn_id"] or ""),
        request_id=str(row["request_id"] or ""),
        conflict_with=str(row["conflict_with"] or ""),
        created_at=str(row["created_at"] or ""),
        updated_at=str(row["updated_at"] or ""),
        last_used_at=str(row["last_used_at"] or ""),
        expires_at=str(row["expires_at"] or ""),
        deleted_at=str(row["deleted_at"] or ""),
        revision=int(row["revision"] or 1),
    )


def _item_snapshot(item: ProfileItem | None) -> str:
    return json.dumps(item.as_dict(), ensure_ascii=False, sort_keys=True) if item else ""


def _history(conn, item: ProfileItem, *, action: str, previous: ProfileItem | None, actor: str, reason: str) -> None:
    conn.execute(
        """
        INSERT INTO operator_profile_history (
            history_id, item_id, principal, revision, action, previous_json, next_json,
            actor, reason, request_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"oph-{uuid.uuid4().hex}",
            item.item_id,
            item.principal,
            int(item.revision),
            str(action),
            _item_snapshot(previous),
            _item_snapshot(item),
            str(actor or ""),
            str(reason or "")[:300],
            _current_request_lineage(),
            utcnow(),
        ),
    )


def _load(conn, item_id: str) -> ProfileItem | None:
    row = conn.execute("SELECT * FROM operator_profile_items WHERE item_id = ?", (str(item_id),)).fetchone()
    return _row_to_item(row) if row is not None else None


# --------------------------------------------------------------------------- privacy gates


def _servable(item: ProfileItem) -> bool:
    """A8 serve-time gate, re-verified on every read (no read-once-trust).

    Two laws, both fail-closed: the value bytes must not be governed WITHHELD/ERASED
    (``writer_may_persist_text`` is the ONE predicate), and the source turn's request lineage
    must not be governed either. A live-store failure suppresses the item."""
    if item.status == STATUS_DELETED:
        return False
    try:
        from core.finalization import (
            AVAILABILITY_ERASED,
            AVAILABILITY_WITHHELD,
            payload_availability_for_request_id,
            writer_may_persist_text,
        )

        if not writer_may_persist_text(item.value_text):
            return False
        if item.request_id:
            verdict = payload_availability_for_request_id(item.request_id)
            if verdict in (AVAILABILITY_WITHHELD, AVAILABILITY_ERASED):
                return False
    except Exception:
        return False
    return not (item.expires_at and item.expires_at <= utcnow())


def _write_permitted(value_text: str) -> bool:
    """A8 write fence (ERASE-dominance) through the canonical predicate."""
    try:
        from core.finalization import writer_may_persist_text

        return bool(writer_may_persist_text(value_text))
    except Exception:
        return False


def _secret_check_text(category: str, value: Any) -> str:
    """The bytes the secret detectors judge. An account preference is a reference whose label is
    literally ``credential:``; the detectors must judge the NAME behind it, never the label."""
    if CATEGORIES.get(category, {}).get("type") == "credential_binding":
        ref = str(value.get("binding_ref") if isinstance(value, dict) else value or "").strip()
        return ref.split(":", 1)[1] if ref.lower().startswith("credential:") else ref
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


_SECRET_LABEL_RESIDUE_RE = re.compile(
    r"\b(?:password|passwd|pwd|secret|api[_ -]?key|apikey|access[_ -]?token|auth[_ -]?token|token|bearer|"
    r"cookie|session[_ -]?id|sid|private[_ -]?key|seed[_ -]?phrase|mnemonic|passphrase|credential)\b\s*[:=]\s*(?:\S*\[redacted[^\]]*\]\S*|\S{0,3})\s*$",
    re.IGNORECASE,
)


def value_is_secret(value_text: str) -> bool:
    """Secrets, tokens, cookies and passwords can never enter profile storage. A secret LABEL whose
    value was already stripped upstream ("... token=" / "token=[redacted]") is refused too: the
    operator pasted a credential, and the residue is not a preference."""
    text = str(value_text or "")
    return bool(contains_secret(text) or secret_material_present(text) or _SECRET_LABEL_RESIDUE_RE.search(text))


# --------------------------------------------------------------------------- principal


def principal_for_request(source_context: dict[str, Any] | None) -> str:
    """Whose profile a request may touch. Owner-local private chats own ``owner_local``;
    an identified channel user owns ``channel:<platform>:<user id>``; a group surface or an
    unidentifiable caller owns nothing (``""``), and every reader/writer treats that as no
    profile. The server stamps ``_owner_local`` from the TCP peer, never the body."""
    ctx = source_context if isinstance(source_context, dict) else {}
    surface = str(ctx.get("surface") or "").strip().lower()
    platform = str(ctx.get("platform") or "").strip().lower()
    channel_like = surface in _CHANNEL_SURFACES or platform in _CHANNEL_SURFACES
    group_like = bool(ctx.get("is_group") or ctx.get("group_id") or ctx.get("channel_is_group"))
    if group_like:
        return ""
    if request_is_owner_local(ctx) and not channel_like:
        return OWNER_PRINCIPAL
    user_id = ""
    for key in ("source_user_id", "user_id", "sender_id", "author_id", "channel_user_id"):
        candidate = str(ctx.get(key) or "").strip()
        if candidate:
            user_id = candidate
            break
    if not user_id:
        return ""
    return f"channel:{platform or surface or 'channel'}:{user_id}"


# --------------------------------------------------------------------------- validation


def _normalize_value(category: str, value: Any) -> tuple[Any, str]:
    spec = CATEGORIES.get(str(category or ""))
    if spec is None:
        raise ProfileValidationError(f"unknown profile category: {category!r}")
    kind = str(spec["type"])
    limit = int(spec.get("max") or 200)
    if kind in {"text", "multiline"}:
        text = str(value if value is not None else "").strip()
        if kind == "text":
            text = " ".join(text.split())
        text = text[:limit]
        if not text:
            raise ProfileValidationError("empty value")
        return text, text
    if kind == "timezone":
        text = " ".join(str(value or "").split())[:limit]
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(text)
        except Exception as exc:
            raise ProfileValidationError(f"not an IANA timezone: {text!r}") from exc
        return text, text
    if kind == "enum":
        text = str(value or "").strip()
        choices = tuple(spec.get("choices") or ())
        # Case-insensitive match against the enum, storing the CANONICAL casing —
        # "europe" and "Europe" are one choice, and the drawer shows the canonical one.
        match = next((str(c) for c in choices if str(c).lower() == text.lower()), "")
        if not match:
            raise ProfileValidationError(f"{category} must be one of {choices}")
        return match, match
    if kind == "credential_binding":
        if isinstance(value, dict):
            ref = str(value.get("binding_ref") or "").strip()
        else:
            ref = str(value or "").strip()
        if _CREDENTIAL_NAME_RE.match(ref) and not ref.startswith("credential:"):
            ref = f"credential:{ref}"
        if not _BINDING_REF_RE.match(ref):
            raise ProfileValidationError("an account preference is an opaque credential reference (credential:<name>)")
        name = ref.split(":", 1)[1]
        if "." not in name:
            # A bare account label ("work") names a slot of the category's credential family.
            for prefix in _BINDING_FAMILIES.get(category, ()):
                if _binding_exists(prefix + name):
                    name = prefix + name
                    ref = f"credential:{name}"
                    break
        if not _binding_exists(name):
            raise ProfileValidationError(f"no stored credential named {name!r}")
        return {"binding_ref": ref, "account": _account_part(name)}, ref
    raise ProfileValidationError(f"unsupported value type {kind}")


def _binding_exists(name: str) -> bool:
    """Presence check only. The credential VALUE is never read by this module."""
    try:
        from core.credential_store import has_credential

        return bool(has_credential(name))
    except Exception:
        return False


def _account_part(name: str) -> str:
    parts = str(name or "").split(".")
    return parts[-1] if parts else str(name or "")


def _sensitivity(category: str) -> str:
    return str(CATEGORIES.get(category, {}).get("sensitivity") or SENSITIVITY_PERSONAL)


def _describe(category: str, value_text: str, scope: str, scope_key: str = "") -> str:
    label = str(CATEGORIES.get(category, {}).get("label") or category)
    where = scope if scope != "chat" else "this chat only"
    if scope == "project" and scope_key:
        where = f"project {scope_key}"
    shown = value_text if len(value_text) <= 80 else value_text[:77] + "..."
    if CATEGORIES.get(category, {}).get("type") == "credential_binding":
        shown = value_text
    return f"{label} -> {shown} ({where})"


# --------------------------------------------------------------------------- reads


def get_item(item_id: str) -> ProfileItem | None:
    conn = _conn()
    try:
        item = _load(conn, item_id)
    finally:
        conn.close()
    if item is None or not _servable(item):
        return None
    return item


def list_items(
    principal: str,
    *,
    include_candidates: bool = False,
    include_deleted: bool = False,
    session_id: str = "",
) -> list[ProfileItem]:
    """Every servable item the principal owns. Chat-scoped items are returned only for the chat
    they belong to (``session_id``); with no session they are omitted entirely."""
    clean = str(principal or "").strip()
    if not clean:
        return []
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM operator_profile_items WHERE principal = ? ORDER BY created_at, rowid",
            (clean,),
        ).fetchall()
    finally:
        conn.close()
    out: list[ProfileItem] = []
    for row in rows:
        item = _row_to_item(row)
        if item.status == STATUS_DELETED:
            if include_deleted:
                out.append(item)
            continue
        if item.status == STATUS_CANDIDATE:
            # A candidate is a PENDING proposal, not an applied fact: the operator's own listing
            # shows every pending one so it can be decided from Settings, whatever chat raised it.
            if not include_candidates:
                continue
        elif item.scope == "chat" and item.scope_key != str(session_id or ""):
            continue
        if not _servable(item):
            continue
        out.append(item)
    return out


def _context_mode_for(principal: str, session_id: str, active_items: list[ProfileItem]) -> str:
    for item in active_items:
        if item.category == "context_mode" and item.scope == "chat" and item.scope_key == session_id:
            return str(item.value_text)
    return ""


def resolve(
    principal: str,
    category: str,
    *,
    session_id: str = "",
    project_id: str = "",
    context_mode: str = "",
    touch: bool = False,
) -> ProfileItem | None:
    """Deterministic precedence: chat > project > context (work/personal) > global."""
    items = [
        item
        for item in list_items(principal, session_id=session_id)
        if item.category == category and item.status == STATUS_ACTIVE
    ]
    if not items:
        return None
    mode = str(context_mode or "").strip().lower() or _context_mode_for(
        principal, session_id, list_items(principal, session_id=session_id)
    )
    order: list[tuple[str, str]] = []
    if session_id:
        order.append(("chat", session_id))
    if project_id:
        order.append(("project", project_id))
    if mode in CONTEXT_SCOPES:
        order.append((mode, ""))
    order.append(("global", ""))
    for scope, key in order:
        for item in items:
            if item.scope == scope and (scope == "global" or item.scope_key == key or (scope in CONTEXT_SCOPES)):
                if touch:
                    touch_used(item.item_id)
                return item
    return None


def touch_used(item_id: str) -> None:
    conn = _conn()
    try:
        conn.execute(
            "UPDATE operator_profile_items SET last_used_at = ? WHERE item_id = ?",
            (utcnow(), str(item_id)),
        )
        conn.commit()
    finally:
        conn.close()


def history_for_item(item_id: str) -> list[dict[str, Any]]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM operator_profile_history WHERE item_id = ? ORDER BY revision, rowid",
            (str(item_id),),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "history_id": str(row["history_id"]),
            "revision": int(row["revision"]),
            "action": str(row["action"]),
            "actor": str(row["actor"] or ""),
            "reason": str(row["reason"] or ""),
            "created_at": str(row["created_at"] or ""),
            "previous": json.loads(row["previous_json"]) if row["previous_json"] else None,
            "next": json.loads(row["next_json"]) if row["next_json"] else None,
        }
        for row in rows
    ]


# --------------------------------------------------------------------------- pause


def is_paused(principal: str) -> bool:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT memory_paused FROM operator_profile_state WHERE principal = ?", (str(principal),)
        ).fetchone()
    finally:
        conn.close()
    return bool(row and int(row["memory_paused"] or 0))


def set_paused(principal: str, paused: bool) -> bool:
    clean = str(principal or "").strip()
    if not clean:
        return False
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO operator_profile_state (principal, memory_paused, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(principal) DO UPDATE SET memory_paused = excluded.memory_paused, updated_at = excluded.updated_at",
            (clean, 1 if paused else 0, utcnow()),
        )
        conn.commit()
    finally:
        conn.close()
    return bool(paused)


# --------------------------------------------------------------------------- writes


def _insert(
    conn,
    *,
    principal: str,
    category: str,
    value: Any,
    value_text: str,
    scope: str,
    scope_key: str,
    origin: str,
    confidence: float,
    status: str,
    session_id: str,
    turn_id: str,
    conflict_with: str = "",
    expires_at: str = "",
    actor: str,
    reason: str,
) -> ProfileItem:
    now = utcnow()
    item = ProfileItem(
        item_id=f"opi-{uuid.uuid4().hex}",
        principal=principal,
        category=category,
        value=value,
        value_text=value_text,
        scope=scope,
        scope_key=scope_key,
        origin=origin,
        confidence=max(0.0, min(1.0, float(confidence))),
        sensitivity=_sensitivity(category),
        status=status,
        source_session_id=str(session_id or ""),
        source_turn_id=str(turn_id or ""),
        request_id=_current_request_lineage(),
        conflict_with=str(conflict_with or ""),
        created_at=now,
        updated_at=now,
        last_used_at="",
        expires_at=str(expires_at or ""),
        deleted_at="",
        revision=1,
    )
    conn.execute(
        """
        INSERT INTO operator_profile_items (
            item_id, principal, category, value_json, value_text, scope, scope_key, origin,
            confidence, sensitivity, status, source_session_id, source_turn_id, request_id,
            conflict_with, created_at, updated_at, last_used_at, expires_at, deleted_at, revision
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            item.item_id, item.principal, item.category, json.dumps(item.value, ensure_ascii=False),
            item.value_text, item.scope, item.scope_key, item.origin, item.confidence, item.sensitivity,
            item.status, item.source_session_id, item.source_turn_id, item.request_id,
            item.conflict_with, item.created_at, item.updated_at, item.last_used_at, item.expires_at,
            item.deleted_at, item.revision,
        ),
    )
    _history(conn, item, action="create" if status == STATUS_ACTIVE else status, previous=None, actor=actor, reason=reason)
    return item


def _update(
    conn,
    current: ProfileItem,
    *,
    expected_revision: int | None,
    actor: str,
    reason: str,
    action: str,
    **changes: Any,
) -> ProfileItem:
    """CAS update: the row must still be at ``expected_revision`` (or at ``current.revision``
    when the caller passed none) or the write is refused with ``RevisionConflict``."""
    expected = int(expected_revision if expected_revision is not None else current.revision)
    fields = dict(current.as_dict())
    fields.pop("label", None)
    fields.update(changes)
    now = utcnow()
    new_revision = expected + 1
    cursor = conn.execute(
        """
        UPDATE operator_profile_items SET value_json = ?, value_text = ?, scope = ?, scope_key = ?,
            origin = ?, confidence = ?, status = ?, conflict_with = ?, updated_at = ?,
            expires_at = ?, deleted_at = ?, revision = ?
        WHERE item_id = ? AND revision = ?
        """,
        (
            json.dumps(fields["value"], ensure_ascii=False), str(fields["value_text"]), str(fields["scope"]),
            str(fields["scope_key"]), str(fields["origin"]), float(fields["confidence"]), str(fields["status"]),
            str(fields["conflict_with"]), now, str(fields.get("expires_at") or ""), str(fields.get("deleted_at") or ""),
            new_revision, current.item_id, expected,
        ),
    )
    if cursor.rowcount != 1:
        conn.rollback()
        raise RevisionConflict(f"{current.item_id} is no longer at revision {expected}")
    updated = _load(conn, current.item_id)
    assert updated is not None
    _history(conn, updated, action=action, previous=current, actor=actor, reason=reason)
    return updated


def _active_same(principal: str, category: str, scope: str, scope_key: str, session_id: str) -> ProfileItem | None:
    for item in list_items(principal, session_id=session_id):
        if item.category == category and item.status == STATUS_ACTIVE and item.scope == scope and (
            scope == "global" or item.scope_key == scope_key or scope in CONTEXT_SCOPES
        ):
            return item
    return None


def _preflight(principal: str, category: str, value: Any, origin: str) -> tuple[ProfileChange | None, Any, str]:
    clean_principal = str(principal or "").strip()
    if not clean_principal:
        return ProfileChange("no_principal", "No profile is available for this caller."), None, ""
    if is_paused(clean_principal):
        return ProfileChange("paused", "Memory is paused; nothing was saved. Resume it under What VOOL remembers about you."), None, ""
    raw_text = _secret_check_text(category, value)
    if value_is_secret(raw_text):
        return (
            ProfileChange(
                "refused_secret",
                "That looks like a secret (a key, token, cookie or password). I never store those in your profile; "
                f"nothing was saved. Scrubbed: {scrub_secret_material(redact_secrets(raw_text))[:120]}",
            ),
            None,
            "",
        )
    try:
        normalized, value_text = _normalize_value(category, value)
    except ProfileValidationError as exc:
        kind = "refused_binding" if "credential" in str(exc) else "refused_invalid"
        return ProfileChange(kind, f"Not saved: {exc}"), None, ""
    if origin == "inferred" and _sensitivity(category) in {SENSITIVITY_IDENTITY, SENSITIVITY_ACCOUNT}:
        return (
            ProfileChange(
                "refused_sensitive",
                f"I don't learn {CATEGORIES[category]['label']} from context. Tell me explicitly to remember it.",
            ),
            None,
            "",
        )
    if not _write_permitted(value_text):
        return ProfileChange("refused_privacy", "Not saved: that content is under a privacy hold."), None, ""
    return None, normalized, value_text


def remember(
    principal: str,
    category: str,
    value: Any,
    *,
    scope: str = "global",
    scope_key: str = "",
    origin: str = "explicit",
    confidence: float = 1.0,
    session_id: str = "",
    turn_id: str = "",
    actor: str = "chat",
    reason: str = "",
    expected_revision: int | None = None,
    replace: bool = False,
    expires_at: str = "",
) -> ProfileChange:
    """Explicit persistence. Reports the exact change. A different active value in the same
    scope is a CONFLICT unless ``replace`` is set -- the operator decides."""
    refusal, normalized, value_text = _preflight(principal, category, value, origin)
    if refusal is not None:
        return refusal
    scope = str(scope or "global").strip().lower()
    if scope not in SCOPES:
        return ProfileChange("refused_invalid", f"Not saved: unknown scope {scope!r}")
    if scope == "chat":
        scope_key = str(session_id or scope_key or "")
        if not scope_key:
            return ProfileChange("refused_invalid", "Not saved: a chat-scoped item needs a chat.")
    if scope == "project" and not scope_key:
        return ProfileChange("refused_invalid", "Not saved: a project-scoped item needs a project.")
    if scope in {"global", "work", "personal"}:
        scope_key = ""
    with _WRITE_LOCK:
        conn = _conn()
        try:
            current = _active_same(principal, category, scope, scope_key, session_id)
            if current is not None:
                if current.value_text == value_text:
                    return ProfileChange("unchanged", f"Already saved: {_describe(category, value_text, scope, scope_key)}.", item=current)
                if not replace:
                    candidate = _insert(
                        conn, principal=principal, category=category, value=normalized, value_text=value_text,
                        scope=scope, scope_key=scope_key, origin=origin, confidence=confidence, status=STATUS_CANDIDATE,
                        session_id=session_id, turn_id=turn_id, conflict_with=current.item_id, actor=actor,
                        reason=reason or "conflict with active item",
                    )
                    conn.commit()
                    return ProfileChange(
                        "conflict",
                        f"I already have {_describe(category, current.value_text, current.scope, current.scope_key)}. "
                        f"Replace it with {value_text!r}, keep both under different scopes, or keep the old one?",
                        item=candidate,
                        previous=current,
                    )
                updated = _update(
                    conn, current, expected_revision=expected_revision, actor=actor, reason=reason or "replace",
                    action="update", value=normalized, value_text=value_text, origin=origin,
                    confidence=max(0.0, min(1.0, float(confidence))), conflict_with="",
                )
                conn.commit()
                return ProfileChange(
                    "updated",
                    f"Updated: {_describe(category, value_text, scope, scope_key)} (was {current.value_text!r}). Say 'undo that' to restore.",
                    item=updated,
                    previous=current,
                )
            item = _insert(
                conn, principal=principal, category=category, value=normalized, value_text=value_text, scope=scope,
                scope_key=scope_key, origin=origin, confidence=confidence, status=STATUS_ACTIVE, session_id=session_id,
                turn_id=turn_id, expires_at=expires_at, actor=actor, reason=reason,
            )
            conn.commit()
            return ProfileChange("saved", f"Saved to your profile: {_describe(category, value_text, scope, scope_key)}.", item=item)
        except RevisionConflict:
            conn.rollback()
            raise
        finally:
            conn.close()


def propose_candidate(
    principal: str,
    category: str,
    value: Any,
    *,
    session_id: str,
    turn_id: str = "",
    confidence: float = 0.85,
    reason: str = "",
) -> ProfileChange:
    """A strong implicit preference becomes a candidate the operator confirms. Never durable
    by itself. One candidate per (principal, category, chat): a newer proposal replaces it."""
    refusal, normalized, value_text = _preflight(principal, category, value, "inferred")
    if refusal is not None:
        return refusal
    if not str(session_id or "").strip():
        return ProfileChange("refused_invalid", "A candidate needs a chat.")
    if not CATEGORIES[category].get("inferable"):
        return ProfileChange("refused_sensitive", f"I don't learn {CATEGORIES[category]['label']} from context.")
    with _WRITE_LOCK:
        conn = _conn()
        try:
            active = resolve(principal, category, session_id=session_id)
            if active is not None and active.value_text == value_text:
                return ProfileChange("unchanged", f"Already saved: {_describe(category, value_text, active.scope, active.scope_key)}.", item=active)
            existing = [
                item
                for item in list_items(principal, include_candidates=True, session_id=session_id)
                if item.status == STATUS_CANDIDATE and item.category == category and item.source_session_id == session_id
            ]
            for stale in existing:
                _update(conn, stale, expected_revision=None, actor="runtime", reason="superseded candidate",
                        action="dismiss", status=STATUS_DELETED, deleted_at=utcnow())
            item = _insert(
                conn, principal=principal, category=category, value=normalized, value_text=value_text, scope="chat",
                scope_key=session_id, origin="inferred", confidence=confidence, status=STATUS_CANDIDATE,
                session_id=session_id, turn_id=turn_id, conflict_with=active.item_id if active else "",
                actor="runtime", reason=reason or "inferred from conversation",
            )
            conn.commit()
            return ProfileChange("candidate", f"Remember: {_candidate_phrase(category, value_text)}", item=item, previous=active)
        finally:
            conn.close()


def _candidate_phrase(category: str, value_text: str) -> str:
    if category == "preferred_name":
        return f"address you as {value_text}"
    if category == "response_style":
        return f"{value_text} replies"
    if category == "language":
        return f"reply in {value_text}"
    if category == "timezone":
        return f"your timezone is {value_text}"
    if category == "context_mode":
        return f"treat this chat as {value_text}"
    return f"{CATEGORIES[category]['label']}: {value_text}"


def note_chat_local(
    principal: str,
    category: str,
    value: Any,
    *,
    session_id: str,
    turn_id: str = "",
    confidence: float = 0.5,
) -> ProfileChange:
    """A weak inference: active for THIS chat only, origin inferred, confidence below the
    promotion floor -- ``move_scope`` will refuse to promote it."""
    refusal, normalized, value_text = _preflight(principal, category, value, "inferred")
    if refusal is not None:
        return refusal
    if not str(session_id or "").strip():
        return ProfileChange("refused_invalid", "A chat-local note needs a chat.")
    if not CATEGORIES[category].get("inferable"):
        return ProfileChange("refused_sensitive", f"I don't learn {CATEGORIES[category]['label']} from context.")
    confidence = min(float(confidence), STRONG_INFERENCE_CONFIDENCE - 0.01)
    with _WRITE_LOCK:
        conn = _conn()
        try:
            current = _active_same(principal, category, "chat", session_id, session_id)
            if current is not None:
                if current.value_text == value_text:
                    return ProfileChange("unchanged", "", item=current)
                if current.origin == "explicit":
                    return ProfileChange("unchanged", "", item=current)
                updated = _update(conn, current, expected_revision=None, actor="runtime", reason="weak inference",
                                  action="update", value=normalized, value_text=value_text, confidence=confidence)
                conn.commit()
                return ProfileChange("chat_local", "", item=updated, previous=current)
            item = _insert(
                conn, principal=principal, category=category, value=normalized, value_text=value_text, scope="chat",
                scope_key=session_id, origin="inferred", confidence=confidence, status=STATUS_ACTIVE,
                session_id=session_id, turn_id=turn_id, actor="runtime", reason="weak inference (chat-local)",
            )
            conn.commit()
            return ProfileChange("chat_local", "", item=item)
        finally:
            conn.close()


def decide_candidate(
    candidate_id: str,
    action: str,
    *,
    value: Any = None,
    scope: str = "global",
    scope_key: str = "",
    actor: str = "operator",
) -> ProfileChange:
    """Save / Edit / Only this chat / Dismiss on a candidate. Save and Edit make it an explicit
    item in ``scope``; Only this chat keeps it chat-scoped and explicit; Dismiss tombstones it."""
    action = str(action or "").strip().lower()
    conn = _conn()
    try:
        candidate = _load(conn, candidate_id)
    finally:
        conn.close()
    if candidate is None or candidate.status != STATUS_CANDIDATE:
        return ProfileChange("refused_invalid", "That suggestion is no longer pending.")
    if not _servable(candidate):
        return ProfileChange("refused_privacy", "That suggestion is under a privacy hold.")
    with _WRITE_LOCK:
        conn = _conn()
        try:
            if action == "dismiss":
                gone = _update(conn, candidate, expected_revision=None, actor=actor, reason="dismissed",
                               action="dismiss", status=STATUS_DELETED, deleted_at=utcnow())
                conn.commit()
                return ProfileChange("forgotten", "Okay, not remembering that.", item=gone, previous=candidate)
            if action == "only_this_chat":
                promoted = _update(conn, candidate, expected_revision=None, actor=actor, reason="only this chat",
                                   action="confirm_chat", status=STATUS_ACTIVE, origin="explicit", confidence=1.0,
                                   scope="chat", scope_key=candidate.source_session_id, conflict_with="")
                conn.commit()
                return ProfileChange("saved", f"Saved for this chat only: {_describe(candidate.category, candidate.value_text, 'chat')}.", item=promoted, previous=candidate)
            if action not in {"save", "edit"}:
                return ProfileChange("refused_invalid", f"Unknown action {action!r}.")
            new_value = candidate.value if action == "save" or value is None else value
            gone = _update(conn, candidate, expected_revision=None, actor=actor, reason=f"candidate {action}",
                           action="consume_candidate", status=STATUS_DELETED, deleted_at=utcnow())
            conn.commit()
        finally:
            conn.close()
    return remember(
        candidate.principal, candidate.category, new_value, scope=scope, scope_key=scope_key, origin="explicit",
        confidence=1.0, session_id=candidate.source_session_id, turn_id=candidate.source_turn_id, actor=actor,
        reason=f"confirmed candidate {candidate.item_id}", replace=True,
    )


def resolve_conflict(
    candidate_id: str,
    action: str,
    *,
    scope: str = "",
    scope_key: str = "",
    actor: str = "operator",
) -> ProfileChange:
    """replace -- the new value supersedes the old (new revision, undoable); scope -- the new value
    is saved under ``scope`` and the old one stays; keep -- the old value stays, new one dropped."""
    action = str(action or "").strip().lower()
    conn = _conn()
    try:
        candidate = _load(conn, candidate_id)
        current = _load(conn, candidate.conflict_with) if candidate and candidate.conflict_with else None
    finally:
        conn.close()
    if candidate is None or candidate.status != STATUS_CANDIDATE:
        return ProfileChange("refused_invalid", "That conflict is no longer pending.")
    if action == "keep":
        return decide_candidate(candidate_id, "dismiss", actor=actor)
    if action == "replace":
        target_scope = current.scope if current else candidate.scope
        target_key = current.scope_key if current else candidate.scope_key
        return decide_candidate(candidate_id, "save", scope=target_scope, scope_key=target_key, actor=actor)
    if action == "scope":
        if scope not in SCOPES or scope == (current.scope if current else ""):
            return ProfileChange("refused_invalid", "Pick a different scope to keep both.")
        if scope == "chat":
            return decide_candidate(candidate_id, "only_this_chat", actor=actor)
        return decide_candidate(candidate_id, "save", scope=scope, scope_key=scope_key, actor=actor)
    return ProfileChange("refused_invalid", f"Unknown resolution {action!r}.")


def edit_item(item_id: str, value: Any, *, expected_revision: int, actor: str = "operator", reason: str = "edit") -> ProfileChange:
    conn = _conn()
    try:
        current = _load(conn, item_id)
    finally:
        conn.close()
    if current is None or current.status != STATUS_ACTIVE:
        return ProfileChange("refused_invalid", "No such profile item.")
    refusal, normalized, value_text = _preflight(current.principal, current.category, value, "explicit")
    if refusal is not None:
        return refusal
    with _WRITE_LOCK:
        conn = _conn()
        try:
            updated = _update(conn, current, expected_revision=expected_revision, actor=actor, reason=reason,
                              action="update", value=normalized, value_text=value_text, origin="explicit", confidence=1.0)
            conn.commit()
        finally:
            conn.close()
    return ProfileChange("updated", f"Updated: {_describe(updated.category, value_text, updated.scope, updated.scope_key)} (was {current.value_text!r}).", item=updated, previous=current)


def forget_item(item_id: str, *, expected_revision: int | None = None, actor: str = "operator", reason: str = "forget") -> ProfileChange:
    conn = _conn()
    try:
        current = _load(conn, item_id)
    finally:
        conn.close()
    if current is None or current.status == STATUS_DELETED:
        return ProfileChange("refused_invalid", "Nothing to forget.")
    with _WRITE_LOCK:
        conn = _conn()
        try:
            gone = _update(conn, current, expected_revision=expected_revision, actor=actor, reason=reason,
                           action="forget", status=STATUS_DELETED, deleted_at=utcnow())
            conn.commit()
        finally:
            conn.close()
    return ProfileChange("forgotten", f"Forgotten: {_describe(current.category, current.value_text, current.scope, current.scope_key)}. Say 'undo that' to restore.", item=gone, previous=current)


def move_scope(item_id: str, scope: str, *, scope_key: str = "", expected_revision: int | None = None, actor: str = "operator") -> ProfileChange:
    scope = str(scope or "").strip().lower()
    if scope not in SCOPES:
        return ProfileChange("refused_invalid", f"Unknown scope {scope!r}.")
    conn = _conn()
    try:
        current = _load(conn, item_id)
    finally:
        conn.close()
    if current is None or current.status != STATUS_ACTIVE:
        return ProfileChange("refused_invalid", "No such profile item.")
    if scope != "chat" and current.origin == "inferred" and current.confidence < STRONG_INFERENCE_CONFIDENCE:
        return ProfileChange("refused_invalid", "That was only inferred for this chat; confirm it explicitly before it can apply elsewhere.")
    if scope == "chat":
        scope_key = str(scope_key or current.source_session_id or "")
        if not scope_key:
            return ProfileChange("refused_invalid", "A chat-scoped item needs a chat.")
    elif scope == "project":
        if not scope_key:
            return ProfileChange("refused_invalid", "A project-scoped item needs a project.")
    else:
        scope_key = ""
    with _WRITE_LOCK:
        conn = _conn()
        try:
            updated = _update(conn, current, expected_revision=expected_revision, actor=actor, reason=f"scope -> {scope}",
                              action="move_scope", scope=scope, scope_key=scope_key)
            conn.commit()
        finally:
            conn.close()
    return ProfileChange("updated", f"Moved: {_describe(updated.category, updated.value_text, scope, scope_key)} (was {current.scope}).", item=updated, previous=current)


def restore_previous(item_id: str, *, actor: str = "operator") -> ProfileChange:
    """Undo the last change: re-apply the previous snapshot as a NEW revision (never a rewrite)."""
    rows = history_for_item(item_id)
    if not rows:
        return ProfileChange("refused_invalid", "No history for that item.")
    last = rows[-1]
    previous = last.get("previous")
    if not previous:
        return ProfileChange("refused_invalid", "That item has no earlier version.")
    conn = _conn()
    try:
        current = _load(conn, item_id)
    finally:
        conn.close()
    if current is None:
        return ProfileChange("refused_invalid", "No such profile item.")
    if not _write_permitted(str(previous.get("value_text") or "")):
        return ProfileChange("refused_privacy", "The earlier version is under a privacy hold.")
    with _WRITE_LOCK:
        conn = _conn()
        try:
            restored = _update(
                conn, current, expected_revision=None, actor=actor, reason=f"restore revision {int(previous.get('revision') or 0)}",
                action="restore", value=previous.get("value"), value_text=str(previous.get("value_text") or ""),
                scope=str(previous.get("scope") or "global"), scope_key=str(previous.get("scope_key") or ""),
                origin=str(previous.get("origin") or "explicit"), confidence=float(previous.get("confidence") or 1.0),
                status=str(previous.get("status") or STATUS_ACTIVE), conflict_with=str(previous.get("conflict_with") or ""),
                deleted_at=str(previous.get("deleted_at") or ""), expires_at=str(previous.get("expires_at") or ""),
            )
            conn.commit()
        finally:
            conn.close()
    return ProfileChange("updated", f"Restored: {_describe(restored.category, restored.value_text, restored.scope, restored.scope_key)}.", item=restored, previous=current)


def export_profile(principal: str) -> dict[str, Any]:
    """Servable active items only. Account preferences export the opaque reference, never
    credential material; every value passes the secret detectors again on the way out."""
    items = [
        item.as_dict()
        for item in list_items(principal)
        if item.status == STATUS_ACTIVE and not value_is_secret(_secret_check_text(item.category, item.value))
    ]
    return {"format": "vool.operator_profile.v1", "exported_at": utcnow(), "items": items}


# --------------------------------------------------------------------------- hydration


def hydration_for_turn(
    principal: str,
    *,
    session_id: str = "",
    project_id: str = "",
    include_name: bool = True,
) -> tuple[list[str], list[dict[str, str]]]:
    """Compact prompt lines for an ordinary turn plus the 'used' list for the folded indicator.
    The email signature is NOT hydrated here (it is applied by the email tools); account
    bindings are named by label only. With no items this returns ([], []) -- an ordinary chat
    stays byte-identical."""
    lines: list[str] = []
    used: list[dict[str, str]] = []
    if not str(principal or "").strip():
        return lines, used
    for category, phrase in (
        ("preferred_name", 'Address the user as "{v}" (their saved preferred name).'),
        ("language", "Reply in {v} unless the user writes in another language."),
        ("locale", "Use the {v} locale for dates, numbers and units."),
        ("timezone", "The user's timezone is {v}."),
        ("response_style", "Response preference: {v}."),
        ("format_preference", "Formatting preference: {v}."),
    ):
        if category == "preferred_name" and not include_name:
            continue
        item = resolve(principal, category, session_id=session_id, project_id=project_id, touch=True)
        if item is None:
            continue
        lines.append(phrase.format(v=item.value_text))
        used.append({"category": category, "label": item.label, "scope": item.scope, "item_id": item.item_id})
    return lines, used


# --------------------------------------------------------------------------- A8 traversal


def erase_governed_items(request_id: str, content_hash: str, plaintext: str = "") -> str:
    """A8 erasure sweep step: tombstone every item lineaged to the erased request, and every
    item whose value text is or contains the erased plaintext (derivative law). Idempotent."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM operator_profile_items WHERE status != ?", (STATUS_DELETED,)
        ).fetchall()
        removed = 0
        for row in rows:
            item = _row_to_item(row)
            hit = bool(request_id) and item.request_id == request_id
            if not hit and content_hash:
                digest = "sha256:" + hashlib.sha256(item.value_text.encode("utf-8")).hexdigest()
                hit = digest == content_hash or digest.split(":", 1)[1] == content_hash
            if not hit and plaintext and item.value_text:
                hit = item.value_text in plaintext or plaintext in item.value_text
            if not hit:
                continue
            _update(conn, item, expected_revision=None, actor="a8_traversal", reason="erasure",
                    action="erase", status=STATUS_DELETED, deleted_at=utcnow(), value="", value_text="")
            # Erasure keeps no plaintext anywhere: the audit trail retains WHAT happened
            # (action, revision, actor) and drops the snapshots that carried the bytes.
            conn.execute(
                "UPDATE operator_profile_history SET previous_json = '', next_json = '', reason = '' WHERE item_id = ?",
                (item.item_id,),
            )
            removed += 1
        conn.commit()
        return f"ok:{removed}"
    finally:
        conn.close()
