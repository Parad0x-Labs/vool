"""The one authority for changing saved contacts: every write to the Contacts book is a reviewed operation.

Policy (the owner's):

1. A new contact whose name and aliases collide with nothing saved or recently deleted is created without a credential
   when the owner adds it in Contacts, applies it from an import preview, or names it in their own chat message (request
   provenance, core.contacts.tools).
2. Every change to a saved contact -- its name, aliases, notes, type, entry labels, adding, changing, removing or
   confirming an entry, taking a suggestion or an import into it, deleting it -- waits as a pending operation until the
   owner confirms it with the operator credential (core.operator_credential) on the trusted local Contacts surface.
3. A new contact or alias that is the same name as, or a UTS #39 lookalike of, a saved, recently deleted or same-change name
   (core.contacts.identity) waits the same way. It is never merged into, never replaces, and is never active before that.
4. Skills, plugins, imported documents, email, web pages, provider responses and model tool arguments cannot authenticate.
   The credential is typed by a person into the review; nothing a caller sends (an actor, a confirm flag, approval ids,
   headers) stands in for it. A name from such a source is a pending operation, never an active contact.
5. Confirming a contact change authorizes exactly that change. It never approves a payment, an email or an invitation.

Mechanics:

* ``propose`` plans a change against the book inside one ``BEGIN IMMEDIATE`` transaction: canonical steps holding the exact
  revisions they were read at and the full before and after of every field (core.contacts.store plans), the identity
  assessment (the collision set and its digest), the identity policy version, the source, and a SHA-256 digest over all
  of it. An operation that needs no credential is applied in that same transaction, so its collision check and its write
  cannot be separated by a concurrent create.
* ``authorize`` refuses an operation that can no longer apply (before costing a credential attempt), verifies the
  credential under its durable throttle, and mints one authorization bound to the operation id, its digest, the
  principal and the credential generation. It expires after ``AUTHORIZATION_TTL_SECONDS``; only its SHA-256 is stored.
* ``commit`` runs in ONE transaction: it re-reads every bound revision, recomputes the identity assessment and compares
  its digest, checks the policy version and the credential generation, applies the steps, consumes the authorization and
  records the result. A refusal or a failed write rolls all of it back, consumption included, so a failed write does not
  spend the owner's confirmation; a repeated commit of a committed operation returns the recorded result.
* No blanket unlock: one authorization is one operation. A reviewed batch (an import) is one operation and one
  confirmation covering every step and revision in it.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from core.contacts import identity, names, store
from core.contacts.store import ContactsError

AUTHORITY = "core.contacts.authority"

SOURCE_OWNER_UI = "owner_ui"
SOURCE_USER_CHAT = "user_chat"
SOURCE_CHAT_APPROVAL = "chat_approval"
SOURCE_IMPORT = "import"
SOURCE_MODEL = "model_tool"
SOURCES: tuple[str, ...] = (SOURCE_OWNER_UI, SOURCE_USER_CHAT, SOURCE_CHAT_APPROVAL, SOURCE_IMPORT, SOURCE_MODEL)
SOURCE_LABELS: dict[str, str] = {
    SOURCE_OWNER_UI: "You, in Contacts",
    SOURCE_USER_CHAT: "Your own chat message",
    SOURCE_CHAT_APPROVAL: "A chat request you approved",
    SOURCE_IMPORT: "An import you applied",
    SOURCE_MODEL: "A tool call that did not come from your own words",
}
#: sources that carry the owner's own intent to add someone new: a distinct new contact from them needs no credential
NO_CREDENTIAL_CREATE_SOURCES = frozenset({SOURCE_OWNER_UI, SOURCE_USER_CHAT, SOURCE_IMPORT})

STATE_PENDING = "pending"
STATE_COMMITTED = "committed"
STATE_CANCELLED = "cancelled"
STATE_EXPIRED = "expired"
STATE_STALE = "stale"

REASON_CHANGES_SAVED_CONTACT = "changes_saved_contact"
REASON_DELETES_CONTACT = "deletes_contact"
REASON_IDENTITY_CONFLICT = "identity_conflict"
REASON_UNTRUSTED_SOURCE = "untrusted_source"
REASON_TEXT: dict[str, str] = {
    REASON_CHANGES_SAVED_CONTACT: "It changes a saved contact.",
    REASON_DELETES_CONTACT: "It deletes a saved contact.",
    REASON_IDENTITY_CONFLICT: "A name in it is the same as, or looks like, a saved or recently deleted contact.",
    REASON_UNTRUSTED_SOURCE: "The name did not come from you.",
}
STALE_TEXT: dict[str, str] = {
    "policy_changed": "The lookalike rules changed after this was reviewed.",
    "contact_deleted": "The contact was deleted after this was reviewed.",
    "contact_changed": "The contact changed after this was reviewed.",
    "suggestion_decided": "That suggestion was already decided.",
    "lookalikes_changed": "The saved names this was compared with changed after it was reviewed.",
    "change_stale": "A saved entry changed after this was reviewed.",
}

AUTHORIZATION_TTL_SECONDS = 120
OPERATION_TTL_SECONDS = 7 * 24 * 3600
MAX_PENDING = 200
MAX_BATCH_CHANGES = 5000
SIMILAR_WARNINGS_UP_TO = 20
OPERATION_VERSION = 1
REVIEW_PATH = "Home → Contacts → Pending changes"
ONLY_THIS_CHANGE = "Confirming changes Contacts only. It does not send an email, approve a payment or send an invitation."

_CREATE_FIELDS = ("display_name", "kind", "aliases", "endpoints", "notes", "verification", "origin", "source_id", "source_refs")
_UPDATE_FIELDS = ("expected_revision", "display_name", "kind", "notes", "add_aliases", "remove_aliases", "add_endpoints", "change_endpoints", "remove_endpoint_ids",
                  "confirm_endpoint_ids", "verification", "source_id", "source_ref")
_STALE_STORE_REASONS = frozenset({"revision_mismatch", "contact_deleted", "contact_not_found", "endpoint_not_found", "change_stale", "suggestion_decided",
                                  "suggestion_not_found"})


class CommitNotSaved(Exception):
    """The commit transaction failed after the credential was verified. The authorization was not consumed."""

    def __init__(self, operation_id: str, authorization: dict[str, Any], cause: BaseException) -> None:
        super().__init__(f"commit of {operation_id} did not save: {type(cause).__name__}")
        self.operation_id = operation_id
        self.authorization = dict(authorization)
        self.cause = cause


# --- small helpers ------------------------------------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso_epoch(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _authorization_hash(authorization_id: str) -> str:
    return hashlib.sha256(("vool-contacts-authorization|" + str(authorization_id)).encode("utf-8")).hexdigest()


def _connect() -> sqlite3.Connection:
    from storage.db import get_connection

    conn = get_connection()
    conn.execute("BEGIN IMMEDIATE")
    return conn


def source_for_actor(actor: str) -> str:
    """The source a legacy actor string stands for. Only the owner's own paths may create a distinct contact without a credential."""
    text = str(actor or "")
    if text == store.ACTOR_OWNER:
        return SOURCE_OWNER_UI
    if text == store.ACTOR_USER_TEXT:
        return SOURCE_USER_CHAT
    if text.startswith("import:"):
        return SOURCE_IMPORT
    return SOURCE_MODEL


def _pick(change: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: change[field] for field in fields if field in change and change[field] is not None}


# --- planning -----------------------------------------------------------------------------------------------------------

def _plan(conn: sqlite3.Connection, change: dict[str, Any], *, nested: bool = False) -> list[dict[str, Any]]:
    operation = str(change.get("operation") or "").strip()
    if operation == "batch" and not nested:
        items = list(change.get("changes") or [])
        if len(items) > MAX_BATCH_CHANGES:
            raise ContactsError("batch_too_large", f"One change holds at most {MAX_BATCH_CHANGES} items.")
        steps: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                raise ContactsError("change_invalid", "Each item of a batch is a change.")
            steps.extend(_plan(conn, item, nested=True))
        return steps
    if operation == "create":
        return [store.plan_create(**_pick(change, _CREATE_FIELDS))]
    if operation == "update":
        return [store.plan_update(conn, str(change.get("contact_id") or ""), **_pick(change, _UPDATE_FIELDS))]
    if operation == "delete":
        return [store.plan_delete(conn, str(change.get("contact_id") or ""), expected_revision=change.get("expected_revision"))]
    if operation == "accept_suggestion":
        return store.plan_accept_suggestion(conn, str(change.get("suggestion_id") or ""), contact_id=str(change.get("contact_id") or ""),
                                            display_name=change.get("display_name") or "")
    raise ContactsError("change_unknown", "A change creates, updates or deletes a contact, or accepts a suggestion.")


def _effective(steps: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    effective: list[dict[str, Any]] = []
    already_saved: list[dict[str, Any]] = []
    touched: set[str] = set()
    for step in steps:
        if step["action"] == "update":
            already_saved.extend(step.get("already_saved") or [])
            if not step["changes"]:
                continue
        if step["action"] in ("update", "delete"):
            if step["contact_id"] in touched:
                raise ContactsError("one_change_per_contact", "One change edits each contact once; confirm the first change before the next.", status=409)
            touched.add(step["contact_id"])
        effective.append(step)
    for index, step in enumerate(effective):
        step["ref"] = f"s{index}"
        if step.get("target_ref") == "__previous__":
            step["target_ref"] = effective[index - 1]["ref"] if index else ""
    if effective and all(step["action"] == "accept_suggestion" and not step.get("contact_id") and not step.get("target_ref") for step in effective):
        raise ContactsError("change_invalid", "A suggestion is taken into a contact.")
    return effective, already_saved


# --- identity assessment ------------------------------------------------------------------------------------------------

def _proposed_names(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    proposed: list[dict[str, Any]] = []
    for step in steps:
        if step["action"] == "create":
            proposed.append({"ref": step["ref"], "role": "display_name", "text": step["display_name"], "contact_id": ""})
            proposed.extend({"ref": step["ref"], "role": "alias", "text": alias, "contact_id": ""} for alias in step["aliases"])
        elif step["action"] == "update":
            for change in step["changes"]:
                if change["action"] == "renamed":
                    proposed.append({"ref": step["ref"], "role": "display_name", "text": change["after"], "contact_id": step["contact_id"]})
                elif change["action"] == "alias_added":
                    proposed.append({"ref": step["ref"], "role": "alias", "text": change["alias"], "contact_id": step["contact_id"]})
    return proposed


def _saved_names(conn: sqlite3.Connection, *, exclude: set[str]) -> list[dict[str, Any]]:
    contacts = {row["contact_id"]: row for row in conn.execute("SELECT contact_id, display_name, revision FROM contacts WHERE state = 'active'")
                if row["contact_id"] not in exclude}
    entries = [{"contact_id": cid, "revision": int(row["revision"]), "role": "display_name", "text": row["display_name"], "display_name": row["display_name"]}
               for cid, row in contacts.items()]
    for alias in conn.execute("SELECT contact_id, alias FROM contact_aliases ORDER BY contact_id, alias"):
        row = contacts.get(alias["contact_id"])
        if row is not None:
            entries.append({"contact_id": alias["contact_id"], "revision": int(row["revision"]), "role": "alias", "text": alias["alias"], "display_name": row["display_name"]})
    return entries


def assess_identity(conn: sqlite3.Connection, steps: list[dict[str, Any]], *, now: datetime | None = None) -> dict[str, Any]:
    """Which proposed names are the same as, or look like, a saved name, a recently deleted name, or another name in this change."""
    moment = (now or _utcnow()).isoformat()
    proposed = _proposed_names(steps)
    conflicts: list[dict[str, Any]] = []
    similar: list[dict[str, Any]] = []
    if proposed:
        saved = _saved_names(conn, exclude={step["contact_id"] for step in steps if step["action"] == "delete"})
        by_comparison: dict[str, list[dict[str, Any]]] = {}
        by_skeleton: dict[str, list[dict[str, Any]]] = {}
        for entry in saved:
            keys = identity.identity_keys(entry["text"])
            entry["keys"] = keys
            by_comparison.setdefault(keys.comparison, []).append(entry)
            for value in keys.skeletons():
                by_skeleton.setdefault(value, []).append(entry)
        tombstone_key = None
        for position, name in enumerate(proposed):
            keys = identity.identity_keys(name["text"])
            candidates: dict[tuple[str, str, str], dict[str, Any]] = {}
            for entry in by_comparison.get(keys.comparison, []) + [e for value in keys.skeletons() for e in by_skeleton.get(value, [])]:
                candidates[(entry["contact_id"], entry["role"], entry["text"])] = entry
            for entry in candidates.values():
                if name["contact_id"] and entry["contact_id"] == name["contact_id"]:
                    continue
                match = identity.compare(keys, entry["keys"])
                if match:
                    conflicts.append({"with": "saved", "match": match, "ref": name["ref"], "role": name["role"], "text": name["text"], "contact_id": entry["contact_id"],
                                      "existing_role": entry["role"], "existing_text": entry["text"], "existing_display_name": entry["display_name"],
                                      "existing_revision": entry["revision"]})
            for other in proposed[position + 1:]:
                if other["ref"] == name["ref"]:
                    continue
                match = identity.compare(keys, identity.identity_keys(other["text"]))
                if match:
                    conflicts.append({"with": "same_change", "match": match, "ref": name["ref"], "role": name["role"], "text": name["text"],
                                      "other_ref": other["ref"], "other_role": other["role"], "other_text": other["text"]})
            if tombstone_key is None:
                from network.signer import derive_local_secret

                tombstone_key = derive_local_secret("vool-contacts-identity-tombstone-v1")
            tagged = identity.tombstone_hashes_for_keys(keys, secret=tombstone_key)
            hashes = tagged["same"] + tagged["confusable"]
            if hashes:
                marks = ", ".join("?" for _ in hashes)
                found: dict[str, dict[str, Any]] = {}
                for row in conn.execute(f"SELECT key_hash, contact_id, deleted_at FROM contact_identity_tombstones WHERE retained_until > ? AND key_hash IN ({marks})",
                                        (moment, *hashes)):
                    match = identity.MATCH_SAME if row["key_hash"] in tagged["same"] else identity.MATCH_CONFUSABLE
                    current = found.get(row["contact_id"])
                    if current is None or (current["match"] == identity.MATCH_CONFUSABLE and match == identity.MATCH_SAME):
                        found[row["contact_id"]] = {"match": match, "deleted_at": row["deleted_at"]}
                for contact_id, hit in sorted(found.items()):
                    conflicts.append({"with": "recently_deleted", "match": hit["match"], "ref": name["ref"], "role": name["role"], "text": name["text"],
                                      "contact_id": contact_id, "deleted_at": hit["deleted_at"]})
            if len(proposed) <= SIMILAR_WARNINGS_UP_TO:
                flagged = {c["contact_id"] for c in conflicts if c.get("text") == name["text"] and c.get("contact_id")}
                for entry in saved:
                    if entry["role"] != "display_name" or entry["contact_id"] in flagged or entry["contact_id"] == name["contact_id"]:
                        continue
                    score = names.fuzzy_score(name["text"], display_name=entry["text"])
                    if score >= identity.SIMILAR_THRESHOLD:
                        similar.append({"ref": name["ref"], "text": name["text"], "contact_id": entry["contact_id"], "display_name": entry["text"], "score": score})
    conflict_keys = sorted(
        json.dumps([c["with"], c["match"], c["ref"], c["role"], c["text"], c.get("contact_id", ""), c.get("existing_role", ""), c.get("existing_text", ""),
                    c.get("existing_revision", 0), c.get("other_ref", ""), c.get("other_role", ""), c.get("other_text", "")], ensure_ascii=False)
        for c in conflicts
    )
    return {"policy_version": identity.policy_version(), "conflicts": conflicts, "similar": similar[:10], "digest": _digest(conflict_keys)}


def _reasons(steps: list[dict[str, Any]], assessment: dict[str, Any], source: str) -> list[str]:
    reasons: list[str] = []
    if any(step["action"] == "update" for step in steps):
        reasons.append(REASON_CHANGES_SAVED_CONTACT)
    if any(step["action"] == "delete" for step in steps):
        reasons.append(REASON_DELETES_CONTACT)
    if assessment["conflicts"]:
        reasons.append(REASON_IDENTITY_CONFLICT)
    if source not in NO_CREDENTIAL_CREATE_SOURCES and any(step["action"] == "create" for step in steps):
        reasons.append(REASON_UNTRUSTED_SOURCE)
    return reasons


def _applicability(conn: sqlite3.Connection, payload: dict[str, Any]) -> str:
    """'' when the operation still applies exactly as reviewed, else the stale reason."""
    if payload["identity"]["policy_version"] != identity.policy_version():
        return "policy_changed"
    for step in payload["steps"]:
        if step["action"] in ("update", "delete"):
            row = conn.execute("SELECT state, revision FROM contacts WHERE contact_id = ?", (step["contact_id"],)).fetchone()
            if row is None or row["state"] != "active":
                return "contact_deleted"
            if int(row["revision"]) != int(step["base_revision"]):
                return "contact_changed"
        elif step["action"] == "accept_suggestion":
            row = conn.execute("SELECT state FROM contact_suggestions WHERE suggestion_id = ?", (step["suggestion_id"],)).fetchone()
            if row is None or row["state"] != "pending":
                return "suggestion_decided"
    if assess_identity(conn, payload["steps"])["digest"] != payload["identity"]["digest"]:
        return "lookalikes_changed"
    return ""


# --- applying -----------------------------------------------------------------------------------------------------------

def _apply_steps(conn: sqlite3.Connection, steps: list[dict[str, Any]], *, actor: str, operation_id: str, now: str) -> dict[str, Any]:
    result: dict[str, Any] = {"created": [], "updated": [], "deleted": [], "accepted_suggestions": []}
    refs: dict[str, str] = {}
    for step in steps:
        action = step["action"]
        if action == "create":
            contact_id = store.apply_create(conn, step, actor=actor, operation_id=operation_id, now=now)
            refs[step["ref"]] = contact_id
            result["created"].append({"ref": step["ref"], "contact_id": contact_id, "display_name": step["display_name"]})
        elif action == "update":
            changes = store.apply_update(conn, step, actor=actor, operation_id=operation_id, now=now)
            result["updated"].append({"ref": step["ref"], "contact_id": step["contact_id"], "changes": changes})
        elif action == "delete":
            result["deleted"].append({"ref": step["ref"], **store.apply_delete(conn, step, actor=actor, operation_id=operation_id, now=now)})
        elif action == "accept_suggestion":
            contact_id = step.get("contact_id") or refs.get(step.get("target_ref", ""), "")
            store.apply_accept_suggestion(conn, step, contact_id=contact_id, actor=actor, operation_id=operation_id, now=now)
            result["accepted_suggestions"].append({"suggestion_id": step["suggestion_id"], "contact_id": contact_id})
        else:
            raise ContactsError("change_unknown", f"Unknown step {action!r}; nothing changed.")
    return result


def _insert_operation(conn: sqlite3.Connection, operation_id: str, payload: dict[str, Any], digest: str, *, state: str, requires: bool, result: dict[str, Any] | None,
                      now: datetime, principal: str = "") -> None:
    stamp = now.isoformat()
    conn.execute(
        "INSERT INTO contact_operations (operation_id, state, source, source_ref, requested_by, digest, payload_json, requires_authentication, reasons_json,"
        " identity_digest, policy_version, result_json, principal, credential_generation, state_reason, created_at, updated_at, expires_at, decided_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '', ?, ?, ?, ?)",
        (operation_id, state, payload["source"], payload["source_ref"], payload["requested_by"], digest, json.dumps(payload, sort_keys=True, ensure_ascii=False),
         1 if requires else 0, json.dumps(payload["reasons"]), payload["identity"]["digest"], payload["identity"]["policy_version"],
         json.dumps(result or {}, sort_keys=True, ensure_ascii=False), principal, stamp, stamp, (now + timedelta(seconds=OPERATION_TTL_SECONDS)).isoformat(),
         stamp if state == STATE_COMMITTED else None),
    )


_ERASED_KEYS = frozenset({"display_name", "display_name_before", "before", "after", "value", "canonical", "label", "notes", "aliases", "alias", "alias_key",
                          "text", "existing_text", "existing_display_name", "other_text", "provider_account", "suggestion", "endpoints", "changes"})


def _erase_owned(value: Any, contact_ids: set[str], refs: set[str], owned: bool = False) -> Any:
    if isinstance(value, list):
        return [_erase_owned(item, contact_ids, refs, owned) for item in value]
    if not isinstance(value, dict):
        return value
    owned = owned or str(value.get("contact_id") or "") in contact_ids or any(str(value.get(key) or "") in refs for key in ("ref", "target_ref", "other_ref"))
    erased: dict[str, Any] = {}
    for key, item in value.items():
        if owned and key in _ERASED_KEYS:
            erased[key] = [] if isinstance(item, list) else {} if isinstance(item, dict) else ""
        else:
            erased[key] = _erase_owned(item, contact_ids, refs, owned)
    return erased


def _erase_deleted_from_operations(conn: sqlite3.Connection, contact_ids: set[str]) -> None:
    """A deleted contact's names, notes and destination values leave every operation record, as they leave its tombstone and
    journal: ids, actions, states, times and digests stay (the digest keeps recording what was reviewed)."""
    ids = sorted(contact_ids)
    clause = " OR ".join("(payload_json LIKE ? OR result_json LIKE ?)" for _ in ids)
    params = [pattern for contact_id in ids for pattern in (f"%{contact_id}%", f"%{contact_id}%")]
    for row in conn.execute(f"SELECT operation_id, payload_json, result_json FROM contact_operations WHERE {clause}", params).fetchall():
        payload = json.loads(row["payload_json"])
        result = json.loads(row["result_json"] or "{}")
        refs = {str(entry.get("ref") or "") for entry in result.get("created") or [] if entry.get("contact_id") in contact_ids} - {""}
        conn.execute("UPDATE contact_operations SET payload_json = ?, result_json = ? WHERE operation_id = ?",
                     (json.dumps(_erase_owned(payload, contact_ids, refs), sort_keys=True, ensure_ascii=False),
                      json.dumps(_erase_owned(result, contact_ids, refs), sort_keys=True, ensure_ascii=False), row["operation_id"]))


def propose(change: dict[str, Any], *, source: str, requested_by: str = "", source_ref: str = "") -> dict[str, Any]:
    """Record one change. Returns ``status``: ``committed`` (no credential needed; applied in the same transaction),
    ``pending_authentication`` or ``already_pending`` (waits for the owner), or ``unchanged``."""
    if source not in SOURCES:
        raise ContactsError("source_unknown", "Unknown source for a Contacts change.")
    now = _utcnow()
    conn = _connect()
    try:
        steps, already_saved = _effective(_plan(conn, dict(change or {})))
        if not steps:
            conn.commit()
            return {"ok": True, "status": "unchanged", "operation": None, "already_saved": already_saved}
        assessment = assess_identity(conn, steps, now=now)
        payload = {
            "version": OPERATION_VERSION, "source": source, "source_ref": str(source_ref or "")[:200], "requested_by": str(requested_by or "")[:120], "steps": steps,
            "identity": {"policy_version": assessment["policy_version"], "digest": assessment["digest"], "conflicts": assessment["conflicts"],
                         "similar": assessment["similar"]},
        }
        payload["reasons"] = _reasons(steps, assessment, source)
        digest = _digest(payload)
        operation_id = "op-" + uuid.uuid4().hex[:20]
        if not payload["reasons"]:
            result = _apply_steps(conn, steps, actor=source, operation_id=operation_id, now=now.isoformat())
            _insert_operation(conn, operation_id, payload, digest, state=STATE_COMMITTED, requires=False, result=result, now=now)
            conn.commit()
            return {"ok": True, "status": "committed", "operation": get_operation(operation_id), "result": result, "already_saved": already_saved}
        existing = conn.execute("SELECT operation_id FROM contact_operations WHERE digest = ? AND state = ?", (digest, STATE_PENDING)).fetchone()
        if existing is not None:
            conn.commit()
            return {"ok": True, "status": "already_pending", "operation": get_operation(existing["operation_id"]), "already_saved": already_saved}
        if conn.execute("SELECT COUNT(*) FROM contact_operations WHERE state = ?", (STATE_PENDING,)).fetchone()[0] >= MAX_PENDING:
            raise ContactsError("too_many_pending", f"{MAX_PENDING} changes already wait in {REVIEW_PATH}; review or cancel some first. Nothing was recorded.", status=429)
        _insert_operation(conn, operation_id, payload, digest, state=STATE_PENDING, requires=True, result=None, now=now)
        conn.commit()
        return {"ok": True, "status": "pending_authentication", "operation": get_operation(operation_id), "already_saved": already_saved}
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def preview(change: dict[str, Any], *, source: str) -> dict[str, Any]:
    """What ``propose`` would record, without recording it: the steps, the identity assessment and why a credential would be
    needed. Raises ``ContactsError`` for a change that cannot be planned. Nothing is written."""
    if source not in SOURCES:
        raise ContactsError("source_unknown", "Unknown source for a Contacts change.")
    with store._reader() as conn:
        steps, already_saved = _effective(_plan(conn, dict(change or {})))
        assessment = assess_identity(conn, steps) if steps else {"policy_version": identity.policy_version(), "conflicts": [], "similar": [], "digest": _digest([])}
    return {"steps": steps, "already_saved": already_saved, "identity": assessment, "reasons": _reasons(steps, assessment, source) if steps else []}


# --- confirming ---------------------------------------------------------------------------------------------------------

def _load_row(conn: sqlite3.Connection, operation_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM contact_operations WHERE operation_id = ?", (str(operation_id or "").strip(),)).fetchone()
    return dict(row) if row else None


def _state_error(row: dict[str, Any]) -> ContactsError:
    state = row["state"]
    if state == STATE_STALE:
        text = STALE_TEXT.get(row.get("state_reason") or "", "This change can no longer be applied as it was reviewed.")
        return ContactsError("operation_stale", f"{text} Review it again; nothing changed.", status=409, details={"stale_reason": row.get("state_reason") or ""})
    if state == STATE_EXPIRED:
        return ContactsError("operation_expired", "This pending change expired; nothing changed. Make the change again to review it.", status=409)
    if state == STATE_CANCELLED:
        return ContactsError("operation_cancelled", "This change was cancelled; nothing changed.", status=409)
    return ContactsError("operation_not_pending", f"This change is {state}.", status=409)


def _mark(operation_id: str, state: str, reason: str) -> None:
    stamp = _utcnow().isoformat()
    with store._tx() as conn:
        conn.execute("UPDATE contact_operations SET state = ?, state_reason = ?, decided_at = ?, updated_at = ? WHERE operation_id = ? AND state = ?",
                     (state, reason, stamp, stamp, operation_id, STATE_PENDING))
        conn.execute("UPDATE contact_authorizations SET state = 'withdrawn' WHERE operation_id = ? AND state = 'issued'", (operation_id,))


def _expired(row: dict[str, Any], now: datetime) -> bool:
    return row["state"] == STATE_PENDING and str(row["expires_at"]) <= now.isoformat()


def authorize(operation_id: str, *, secret: Any, expected_digest: str) -> dict[str, Any]:
    """Verify the operator credential for exactly this reviewed operation and mint one single-use authorization."""
    with store._reader() as conn:
        row = _load_row(conn, operation_id)
        if row is None:
            raise ContactsError("operation_not_found", "No such pending change.", status=404)
        if row["state"] == STATE_COMMITTED:
            return {"ok": True, "status": "committed", "replayed": True, "operation_id": row["operation_id"], "result": json.loads(row["result_json"] or "{}")}
        if _expired(row, _utcnow()):
            stale = "expired"
        elif row["state"] != STATE_PENDING:
            raise _state_error(row)
        else:
            stale = ""
        if not stale:
            if str(expected_digest or "") != row["digest"]:
                raise ContactsError("operation_changed", "What you reviewed is not this change; open it again. Nothing changed.", status=409)
            if not int(row["requires_authentication"]):
                raise ContactsError("operation_needs_no_confirmation", "This change needs no confirmation.", status=400)
            stale = _applicability(conn, json.loads(row["payload_json"]))
    if stale == "expired":
        _mark(row["operation_id"], STATE_EXPIRED, "expired")
        raise _state_error({**row, "state": STATE_EXPIRED})
    if stale:
        _mark(row["operation_id"], STATE_STALE, stale)
        raise _state_error({**row, "state": STATE_STALE, "state_reason": stale})
    from core.operator_credential import authority as credential

    verification = credential.verify(secret, scope=credential.SCOPE_CONTACTS)
    authorization_id = secrets.token_urlsafe(32)
    issued = time.time()
    with store._tx() as conn:
        current = _load_row(conn, row["operation_id"])
        if current is None or current["state"] != STATE_PENDING or current["digest"] != row["digest"]:
            raise ContactsError("operation_changed", "This change was decided while the PIN was checked; nothing changed.", status=409)
        conn.execute(
            "INSERT INTO contact_authorizations (authorization_hash, operation_id, digest, principal, credential_generation, state, created_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?, 'issued', ?, ?)",
            (_authorization_hash(authorization_id), row["operation_id"], row["digest"], verification.principal, verification.credential_generation,
             _iso_epoch(issued), issued + AUTHORIZATION_TTL_SECONDS),
        )
    return {"ok": True, "status": "authorized", "operation_id": row["operation_id"], "digest": row["digest"], "authorization_id": authorization_id,
            "expires_at": _iso_epoch(issued + AUTHORIZATION_TTL_SECONDS), "principal": verification.principal}


def commit(operation_id: str, *, authorization_id: str | None = None) -> dict[str, Any]:
    """Apply a reviewed operation in one transaction: validate everything it is bound to, write, consume, record."""
    deferred: tuple[str, str] | None = None
    conn = _connect()
    try:
        row = _load_row(conn, operation_id)
        if row is None:
            raise ContactsError("operation_not_found", "No such pending change.", status=404)
        if row["state"] == STATE_COMMITTED:
            conn.commit()
            return {"ok": True, "status": "committed", "replayed": True, "operation_id": row["operation_id"], "result": json.loads(row["result_json"] or "{}")}
        now = _utcnow()
        if _expired(row, now):
            deferred = (STATE_EXPIRED, "expired")
            raise _state_error({**row, "state": STATE_EXPIRED})
        if row["state"] != STATE_PENDING:
            raise _state_error(row)
        payload = json.loads(row["payload_json"])
        principal, generation, auth_hash = "", 0, ""
        if int(row["requires_authentication"]):
            if not authorization_id:
                raise ContactsError("authorization_required", f"Confirm this change with your PIN or password in {REVIEW_PATH}; nothing changed.", status=403)
            auth_hash = _authorization_hash(authorization_id)
            auth = conn.execute("SELECT * FROM contact_authorizations WHERE authorization_hash = ?", (auth_hash,)).fetchone()
            if auth is None or auth["operation_id"] != row["operation_id"] or auth["digest"] != row["digest"]:
                raise ContactsError("authorization_not_for_this_change", "That confirmation was given for a different change; nothing changed.", status=403)
            if auth["state"] != "issued":
                raise ContactsError("authorization_used", "That confirmation was already used or withdrawn; confirm again. Nothing changed.", status=409)
            if float(auth["expires_at"]) <= time.time():
                raise ContactsError("authorization_expired", "The confirmation expired before the change was saved; confirm again. Nothing changed.", status=409)
            from core.operator_credential import authority as credential

            if credential.current_generation(conn) != int(auth["credential_generation"]):
                raise ContactsError("credential_changed", "The PIN or password changed after this confirmation; confirm again with the current one. Nothing changed.",
                                    status=409)
            principal, generation = str(auth["principal"]), int(auth["credential_generation"])
        stale = _applicability(conn, payload)
        if stale:
            deferred = (STATE_STALE, stale)
            raise _state_error({**row, "state": STATE_STALE, "state_reason": stale})
        actor = f"operator:{principal}" if principal else payload["source"]
        try:
            result = _apply_steps(conn, payload["steps"], actor=actor, operation_id=row["operation_id"], now=now.isoformat())
        except ContactsError as exc:
            if exc.reason in _STALE_STORE_REASONS:
                deferred = (STATE_STALE, "change_stale" if exc.reason != "suggestion_decided" else "suggestion_decided")
            raise
        if auth_hash and conn.execute("UPDATE contact_authorizations SET state = 'consumed', consumed_at = ? WHERE authorization_hash = ? AND state = 'issued'",
                                      (now.isoformat(), auth_hash)).rowcount != 1:
            raise ContactsError("authorization_used", "That confirmation was already used; nothing changed.", status=409)
        if conn.execute(
            "UPDATE contact_operations SET state = ?, result_json = ?, principal = ?, credential_generation = ?, decided_at = ?, updated_at = ? WHERE operation_id = ? AND state = ?",
            (STATE_COMMITTED, json.dumps(result, sort_keys=True, ensure_ascii=False), principal, generation, now.isoformat(), now.isoformat(), row["operation_id"], STATE_PENDING),
        ).rowcount != 1:
            raise ContactsError("operation_not_pending", "This change was decided at the same moment; nothing changed.", status=409)
        deleted_ids = {str(entry["contact_id"]) for entry in result.get("deleted") or []}
        if deleted_ids:
            _erase_deleted_from_operations(conn, deleted_ids)
        conn.commit()
        return {"ok": True, "status": "committed", "operation_id": row["operation_id"], "result": result, "principal": principal}
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
        if deferred is not None:
            _mark(str(operation_id or "").strip(), deferred[0], deferred[1])


def confirm(operation_id: str, *, secret: Any, expected_digest: str) -> dict[str, Any]:
    """The review's one action: verify the credential for this operation, then commit it. A write that fails after the
    credential was accepted raises ``CommitNotSaved`` carrying the unconsumed authorization for a retry of ``commit``."""
    granted = authorize(operation_id, secret=secret, expected_digest=expected_digest)
    if granted.get("status") == "committed":
        return granted
    try:
        return commit(granted["operation_id"], authorization_id=granted["authorization_id"])
    except ContactsError:
        raise
    except (sqlite3.OperationalError, sqlite3.DatabaseError) as exc:
        raise CommitNotSaved(granted["operation_id"], granted, exc) from exc


def cancel(operation_id: str) -> dict[str, Any]:
    """Withdraw a pending change. Cancelling leaves saved contacts as they are, so it needs no credential."""
    stamp = _utcnow().isoformat()
    with store._tx() as conn:
        row = _load_row(conn, operation_id)
        if row is None:
            raise ContactsError("operation_not_found", "No such pending change.", status=404)
        if row["state"] == STATE_COMMITTED:
            raise ContactsError("operation_committed", "This change was already saved; make a new change to undo it.", status=409)
        if row["state"] != STATE_PENDING:
            return {"ok": True, "status": row["state"], "operation_id": row["operation_id"]}
        conn.execute("UPDATE contact_operations SET state = ?, state_reason = 'cancelled', decided_at = ?, updated_at = ? WHERE operation_id = ?",
                     (STATE_CANCELLED, stamp, stamp, row["operation_id"]))
        conn.execute("UPDATE contact_authorizations SET state = 'withdrawn' WHERE operation_id = ? AND state = 'issued'", (row["operation_id"],))
    return {"ok": True, "status": STATE_CANCELLED, "operation_id": row["operation_id"]}


# --- review views -------------------------------------------------------------------------------------------------------

def _expire_due() -> None:
    stamp = _utcnow().isoformat()
    try:
        with store._tx() as conn:
            conn.execute("UPDATE contact_operations SET state = ?, state_reason = 'expired', decided_at = ?, updated_at = ? WHERE state = ? AND expires_at <= ?",
                         (STATE_EXPIRED, stamp, stamp, STATE_PENDING, stamp))
            conn.execute("UPDATE contact_authorizations SET state = 'expired' WHERE state = 'issued' AND expires_at <= ?", (time.time(),))
    except sqlite3.OperationalError:
        pass


def _contact_card(contact_id: str) -> dict[str, Any] | None:
    contact = store.get_contact(contact_id)
    if contact is None:
        return None
    return {"contact_id": contact["contact_id"], "state": contact["state"], "display_name": contact["display_name"], "identity": identity.describe(contact["display_name"]),
            "aliases": list(contact.get("aliases") or []), "revision": int(contact.get("revision") or 0),
            "endpoints": [{key: e.get(key, "") for key in ("endpoint_id", "kind", "kind_label", "label", "value", "network_display", "chain_network", "channel",
                                                            "channel_label", "provider_account", "verification")} for e in contact.get("endpoints") or []]}


def _explain(conflict: dict[str, Any]) -> str:
    text = conflict["text"]
    if conflict["with"] == "saved":
        existing = conflict["existing_text"]
        where = "the saved contact" if conflict["existing_role"] == "display_name" else f"an alias of the saved contact {conflict['existing_display_name']!r}:"
        if conflict["match"] == identity.MATCH_SAME:
            return f"{text!r} is the same name as {where} {existing!r}."
        return f"{text!r} looks like {where} {existing!r} but is spelled with different characters."
    if conflict["with"] == "recently_deleted":
        return f"A contact deleted on {str(conflict.get('deleted_at') or '')[:10]} had this name or one that looks like it."
    return f"{text!r} and {conflict['other_text']!r} in this same change look alike."


def _step_view(step: dict[str, Any]) -> dict[str, Any]:
    action = step["action"]
    if action == "create":
        return {"action": action, "ref": step["ref"], "display_name": step["display_name"], "identity": identity.describe(step["display_name"]), "kind": step["kind"],
                "aliases": [{"alias": alias, "identity": identity.describe(alias)} for alias in step["aliases"]], "notes": step["notes"], "endpoints": step["endpoints"],
                "verification": step["verification"], "origin": step["origin"]}
    if action == "update":
        current = store.get_contact(step["contact_id"])
        changes = []
        for change in step["changes"]:
            view = dict(change)
            if change["action"] == "renamed":
                view.update(before_identity=identity.describe(change["before"]), after_identity=identity.describe(change["after"]))
            elif change["action"] in ("alias_added", "alias_removed"):
                view["identity"] = identity.describe(change["alias"])
            changes.append(view)
        return {"action": action, "ref": step["ref"], "contact_id": step["contact_id"], "display_name": step["display_name_before"],
                "identity": identity.describe(step["display_name_before"]), "reviewed_revision": step["base_revision"],
                "current_revision": int(current["revision"]) if current and current["state"] == "active" else None, "changes": changes,
                "verification": step["verification"]}
    if action == "delete":
        # a committed deletion's record keeps no names or values (see _erase_deleted_from_operations)
        before = step.get("before") or {}
        name = str(before.get("display_name") or "")
        return {"action": action, "ref": step["ref"], "contact_id": step["contact_id"], "display_name": name, "identity": identity.describe(name),
                "aliases": before.get("aliases") or [], "notes": before.get("notes") or "", "endpoints": before.get("endpoints") or [],
                "reviewed_revision": step["base_revision"], "erased": not before}
    return {"action": action, "ref": step["ref"], "suggestion_id": step["suggestion_id"], "suggestion": step["suggestion"], "contact_id": step.get("contact_id", ""),
            "target_ref": step.get("target_ref", "")}


def _title(payload: dict[str, Any]) -> str:
    main = [step for step in payload["steps"] if step["action"] != "accept_suggestion"]
    lookalike = bool(payload["identity"]["conflicts"])
    if len(main) == 1:
        step = main[0]
        if step["action"] == "create":
            return f"Add {step.get('display_name') or 'a contact'}" + (" (same as or like a saved name)" if lookalike else "")
        if step["action"] == "update":
            return f"Change {step.get('display_name_before') or 'a deleted contact'}"
        return f"Delete {(step.get('before') or {}).get('display_name') or 'a deleted contact'}"
    if not main:
        return "Take a suggested entry"
    counts = {action: sum(1 for step in main if step["action"] == action) for action in ("create", "update", "delete")}
    parts = [f"add {counts['create']}" if counts["create"] else "", f"change {counts['update']}" if counts["update"] else "",
             f"delete {counts['delete']}" if counts["delete"] else ""]
    return "Contacts: " + ", ".join(part for part in parts if part)


def review(row: dict[str, Any]) -> dict[str, Any]:
    """Everything the owner sees before confirming: each saved contact as it is, the exact change, where it came from, why
    it needs the credential, and every name it looks like with that contact's own entries."""
    payload = json.loads(row["payload_json"])
    conflicts = []
    for conflict in payload["identity"]["conflicts"]:
        view = {**conflict, "proposed": identity.describe(conflict["text"]), "explanation": _explain(conflict)}
        if conflict["with"] == "saved":
            view["existing"] = _contact_card(conflict["contact_id"])
            view["existing_matched"] = identity.describe(conflict["existing_text"])
        elif conflict["with"] == "same_change":
            view["other"] = identity.describe(conflict["other_text"])
        conflicts.append(view)
    return {
        "operation_id": row["operation_id"], "state": row["state"], "state_reason": row.get("state_reason") or "", "digest": row["digest"],
        "source": payload["source"], "source_label": SOURCE_LABELS.get(payload["source"], payload["source"]), "source_ref": payload.get("source_ref", ""),
        "requested_by": payload.get("requested_by", ""), "requires_authentication": bool(int(row["requires_authentication"])), "reasons": payload["reasons"],
        "reason_text": [REASON_TEXT[reason] for reason in payload["reasons"]], "title": _title(payload), "steps": [_step_view(step) for step in payload["steps"]],
        "identity": {"policy_version": payload["identity"]["policy_version"], "conflicts": conflicts, "similar": payload["identity"].get("similar") or []},
        "created_at": row["created_at"], "expires_at": row["expires_at"], "decided_at": row.get("decided_at"),
        "result": json.loads(row["result_json"] or "{}") if row["state"] == STATE_COMMITTED else None, "note": ONLY_THIS_CHANGE, "review_path": REVIEW_PATH,
    }


def get_operation(operation_id: str) -> dict[str, Any] | None:
    with store._reader() as conn:
        row = _load_row(conn, operation_id)
    if row is not None and _expired(row, _utcnow()):
        _mark(row["operation_id"], STATE_EXPIRED, "expired")
        with store._reader() as conn:
            row = _load_row(conn, operation_id)
    return review(row) if row else None


def list_operations(*, state: str = STATE_PENDING, limit: int = 50) -> list[dict[str, Any]]:
    _expire_due()
    with store._reader() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM contact_operations WHERE state = ? ORDER BY created_at DESC LIMIT ?", (state, max(1, min(int(limit), 200))))]
    return [review(row) for row in rows]


def pending_count() -> int:
    with store._reader() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM contact_operations WHERE state = ?", (STATE_PENDING,)).fetchone()[0])


def pending_message(operation: dict[str, Any] | None) -> str:
    title = (operation or {}).get("title") or "This change"
    return f"Nothing is changed yet: {title} waits for your PIN or password in {REVIEW_PATH}."


__all__ = [
    "AUTHORIZATION_TTL_SECONDS", "CommitNotSaved", "MAX_PENDING", "NO_CREDENTIAL_CREATE_SOURCES", "OPERATION_TTL_SECONDS", "REVIEW_PATH", "SOURCES",
    "SOURCE_CHAT_APPROVAL", "SOURCE_IMPORT", "SOURCE_MODEL", "SOURCE_OWNER_UI", "SOURCE_USER_CHAT", "STATE_CANCELLED", "STATE_COMMITTED", "STATE_EXPIRED",
    "STATE_PENDING", "STATE_STALE", "assess_identity", "authorize", "cancel", "commit", "confirm", "get_operation", "list_operations", "pending_count",
    "pending_message", "propose", "review", "source_for_actor",
]
