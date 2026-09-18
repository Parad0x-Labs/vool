"""The Contacts store: the durable rows of saved people and services (tables: storage.migrations, versions 7 and 8).

Law:

* Contact ids and endpoint ids are stable. A change to where an endpoint reaches (value, network, channel, account) bumps
  that endpoint's revision and fingerprint; a label change does not. Every effective change bumps the contact's revision.
* On its own this module only reads. Every write happens inside one transaction of core.contacts.authority, the single
  mutation authority, through the ``plan_*`` and ``apply_*`` primitives below: a plan is computed from the rows as they are
  and records every value it would change (before and after); an apply re-checks the revisions the plan was computed from
  and refuses a stale plan. ``create_contact`` goes through that authority (a distinct new contact commits at once; a name
  that is the same as, or looks like, a saved or recently deleted one waits for review). ``update_contact``,
  ``delete_contact`` and ``accept_suggestion`` refuse: an actor string is not authority, and a change to a saved contact is
  confirmed with the operator credential in Contacts.
* Every write journals ``contact_events`` rows naming the actor and the operation.
* Delete leaves a tombstone: name, aliases, notes, endpoint values and journal details are erased, ids and times stay. The
  deleted name's identity keys are kept as keyed hashes for ``TOMBSTONE_RETENTION_DAYS`` so a deleted name cannot come back
  without review; no readable name is kept. Receipts other owners hold are never touched.
* Suggestions (destinations read from untrusted content) are rows the owner decides; they are never active endpoints and
  never resolve.
* Nothing here sends, dials, signs or pays.
"""
from __future__ import annotations

import json
import sqlite3
import unicodedata
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from core.contacts import identity, names
from core.contacts.endpoints import (
    KIND_LABELS,
    KIND_MESSAGING,
    KIND_WALLET,
    VERIFICATION_IMPORTED,
    VERIFICATION_USER_CONFIRMED,
    VERIFICATION_USER_ENTERED,
    VERIFICATIONS,
    EndpointError,
    NormalizedEndpoint,
    channel_label,
    endpoint_fingerprint,
    messaging_delivery,
    network_display,
    normalize_endpoint,
    refuse_secret_material,
)

AUTHORITY = "core.contacts.store"

ACTOR_OWNER = "owner"
ACTOR_MODEL = "model_tool"
ACTOR_MODEL_APPROVED = "model_tool_approved"
#: the model's tool call, carrying only what the owner's own message in this turn contains (proven by core.contacts.tools)
ACTOR_USER_TEXT = "user_text"

CONTACT_KINDS: tuple[str, ...] = ("person", "service")
MAX_NOTES_CHARS = 2000
MAX_ALIASES = 20
MAX_ENDPOINTS = 50
#: how long a deleted contact's keyed name hashes keep a recreated same or lookalike name in review
TOMBSTONE_RETENTION_DAYS = 90

TABLES: tuple[str, ...] = (
    "contacts", "contact_aliases", "contact_endpoints", "contact_sources", "contact_import_runs", "contact_suggestions", "contact_events",
    "contact_operations", "contact_authorizations", "contact_identity_tombstones",
)

AUTHENTICATION_REQUIRED_MESSAGE = (
    "A change to a saved contact is confirmed with your PIN or password in Home → Contacts → Pending changes; nothing was changed here."
)


class ContactsError(Exception):
    """A typed refusal. ``reason`` is stable, ``message`` is for people, ``status`` is the HTTP status an owner door uses."""

    def __init__(self, reason: str, message: str, *, details: dict[str, Any] | None = None, status: int = 400) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.details = dict(details or {})
        self.status = status

    def as_dict(self) -> dict[str, Any]:
        return {"ok": False, "reason": self.reason, "message": self.message, **({"details": self.details} if self.details else {})}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:20]}"


@contextmanager
def _tx() -> Iterator[sqlite3.Connection]:
    from storage.db import get_connection

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def _reader() -> Iterator[sqlite3.Connection]:
    from storage.db import get_connection

    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def _journal(conn: sqlite3.Connection, contact_id: str, action: str, actor: str, *, endpoint_id: str = "", detail: dict[str, Any] | None = None, now: str) -> None:
    conn.execute(
        "INSERT INTO contact_events (contact_id, endpoint_id, action, actor, detail_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (contact_id, endpoint_id, action, actor, json.dumps(detail or {}, sort_keys=True, ensure_ascii=False), now),
    )


def _as_contacts_error(exc: EndpointError, **details: Any) -> ContactsError:
    return ContactsError(exc.reason, exc.message, details={"field": exc.field, **details})


def _safe(text: Any, *, field: str) -> str:
    raw = unicodedata.normalize("NFC", str(text or ""))
    try:
        identity.require_safe_name(raw, field=field)
    except identity.IdentityError as exc:
        raise ContactsError(exc.reason, exc.message, details={"field": exc.field, **exc.details}) from None
    return raw


def _clean_name(display_name: Any) -> str:
    name = names.clean_display(_safe(display_name, field="display_name"))
    if not name:
        raise ContactsError("name_required", "A contact needs a name.", details={"field": "display_name"})
    try:
        refuse_secret_material(name, field="display_name")
    except EndpointError as exc:
        raise _as_contacts_error(exc) from None
    return name


def _clean_notes(notes: Any) -> str:
    text = str(notes or "").strip()[:MAX_NOTES_CHARS]
    try:
        refuse_secret_material(text, field="notes")
    except EndpointError as exc:
        raise _as_contacts_error(exc) from None
    return text


def _clean_aliases(aliases: Iterable[Any], *, display_name: str) -> list[str]:
    seen: dict[str, str] = {}
    own = names.name_key(display_name)
    for raw in list(aliases or [])[: MAX_ALIASES * 2]:
        alias = names.clean_display(_safe(raw, field="aliases"))
        key = names.name_key(alias)
        if not key or key == own or key in seen:
            continue
        try:
            refuse_secret_material(alias, field="aliases")
        except EndpointError as exc:
            raise _as_contacts_error(exc) from None
        seen[key] = alias
    return list(seen.values())[:MAX_ALIASES]


def _normalize_all(endpoints: Iterable[Any]) -> list[NormalizedEndpoint]:
    out: list[NormalizedEndpoint] = []
    identities: set[tuple[str, str, str, str, str]] = set()
    for index, raw in enumerate(list(endpoints or [])[: MAX_ENDPOINTS + 1]):
        if index >= MAX_ENDPOINTS:
            raise ContactsError("too_many_endpoints", f"A contact holds at most {MAX_ENDPOINTS} endpoints.")
        try:
            normalized = normalize_endpoint(raw)
        except EndpointError as exc:
            raise _as_contacts_error(exc, index=index) from None
        if normalized.identity() in identities:
            continue
        identities.add(normalized.identity())
        out.append(normalized)
    return out


def _insert_endpoint(conn: sqlite3.Connection, contact_id: str, endpoint: NormalizedEndpoint, *, verification: str, source_id: str = "",
                     source_ref: str = "", now: str) -> str:
    endpoint_id = _new_id("ep")
    fingerprint = endpoint_fingerprint(
        contact_id=contact_id, endpoint_id=endpoint_id, kind=endpoint.kind, canonical=endpoint.canonical, chain_network=endpoint.chain_network,
        channel=endpoint.channel, provider_account=endpoint.provider_account,
    )
    conn.execute(
        "INSERT INTO contact_endpoints (endpoint_id, contact_id, kind, label, value, canonical, channel, provider_account, chain_network, chain_family,"
        " chain_environment, verification, source_id, source_ref, state, revision, fingerprint, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 1, ?, ?, ?)",
        (endpoint_id, contact_id, endpoint.kind, endpoint.label, endpoint.value, endpoint.canonical, endpoint.channel, endpoint.provider_account,
         endpoint.chain_network, endpoint.chain_family, endpoint.chain_environment, verification, source_id, source_ref, fingerprint, now, now),
    )
    return endpoint_id


def _endpoint_view(row: sqlite3.Row | dict[str, Any], *, sources: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    view = dict(row)
    view["kind_label"] = KIND_LABELS.get(view.get("kind", ""), view.get("kind", ""))
    if view.get("kind") == KIND_MESSAGING:
        view["channel_label"] = channel_label(view.get("channel"))
        view["delivery"] = messaging_delivery(view.get("channel"))
    if view.get("kind") == KIND_WALLET:
        view["network_display"] = network_display(str(view.get("chain_network") or ""))
    source = (sources or {}).get(str(view.get("source_id") or ""))
    view["source"] = {k: source[k] for k in ("source_id", "provider", "account_label", "status")} if source else None
    return view


def _sources_by_id(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {row["source_id"]: dict(row) for row in conn.execute("SELECT source_id, provider, account_label, status FROM contact_sources")}


def _load_contacts(conn: sqlite3.Connection, where: str, params: tuple[Any, ...], *, include_removed: bool = False) -> list[dict[str, Any]]:
    rows = [dict(r) for r in conn.execute(f"SELECT * FROM contacts WHERE {where} ORDER BY name_key, created_at", params)]
    if not rows:
        return []
    ids = [r["contact_id"] for r in rows]
    marks = ", ".join("?" for _ in ids)
    aliases: dict[str, list[str]] = {cid: [] for cid in ids}
    for row in conn.execute(f"SELECT contact_id, alias FROM contact_aliases WHERE contact_id IN ({marks}) ORDER BY created_at, alias", ids):
        aliases[row["contact_id"]].append(row["alias"])
    endpoint_filter = "" if include_removed else " AND state = 'active'"
    endpoints: dict[str, list[dict[str, Any]]] = {cid: [] for cid in ids}
    sources = _sources_by_id(conn)
    for row in conn.execute(f"SELECT * FROM contact_endpoints WHERE contact_id IN ({marks}){endpoint_filter} ORDER BY kind, created_at", ids):
        endpoints[row["contact_id"]].append(_endpoint_view(row, sources=sources))
    for contact in rows:
        contact["aliases"] = aliases[contact["contact_id"]]
        contact["endpoints"] = endpoints[contact["contact_id"]]
    return rows


# --- reads -----------------------------------------------------------------------------------------------------------

def get_contact(contact_id: str, *, include_removed_endpoints: bool = False) -> dict[str, Any] | None:
    """One contact by id, tombstones included (state ``deleted``, no names or values)."""
    with _reader() as conn:
        found = _load_contacts(conn, "contact_id = ?", (str(contact_id or "").strip(),), include_removed=include_removed_endpoints)
    return found[0] if found else None


def active_contacts() -> list[dict[str, Any]]:
    with _reader() as conn:
        return _load_contacts(conn, "state = 'active'", ())


def get_endpoint(endpoint_id: str) -> dict[str, Any] | None:
    """One endpoint by id in any state, with its contact's state and display name."""
    with _reader() as conn:
        row = conn.execute(
            "SELECT e.*, c.state AS contact_state, c.display_name AS contact_display_name, c.revision AS contact_revision, c.kind AS contact_kind"
            " FROM contact_endpoints e JOIN contacts c ON c.contact_id = e.contact_id WHERE e.endpoint_id = ?",
            (str(endpoint_id or "").strip(),),
        ).fetchone()
        return _endpoint_view(row, sources=_sources_by_id(conn)) if row else None


def endpoints_with_identity(kind: str, canonical: str, *, chain_network: str = "") -> list[dict[str, Any]]:
    """Active endpoints saved with this exact destination identity, with the contact each belongs to."""
    query = ("SELECT e.*, c.display_name AS contact_display_name FROM contact_endpoints e JOIN contacts c ON c.contact_id = e.contact_id"
             " WHERE e.kind = ? AND e.canonical = ? AND e.state = 'active' AND c.state = 'active'")
    params: list[Any] = [kind, canonical]
    if chain_network:
        query += " AND e.chain_network = ?"
        params.append(chain_network)
    with _reader() as conn:
        return [_endpoint_view(row) for row in conn.execute(query, params)]


def list_contacts(query: str = "", *, limit: int = 500) -> list[dict[str, Any]]:
    """Active contacts; with a query, strong name matches first, then lookalike names, then address matches, then close spellings."""
    contacts = active_contacts()
    wanted = str(query or "").strip()
    if not wanted:
        return contacts[: max(1, int(limit))]
    ranked: list[tuple[int, float, str, dict[str, Any]]] = []
    lowered = wanted.casefold()
    for contact in contacts:
        tier = names.match_tier(wanted, display_name=contact["display_name"], aliases=contact["aliases"])
        if tier:
            ranked.append((names.MATCH_RANK[tier], 1.0, contact["name_key"], {**contact, "match": tier}))
            continue
        if identity.lookalike_of(wanted, display_name=contact["display_name"], aliases=contact["aliases"]):
            ranked.append((names.MATCH_RANK[names.MATCH_CONFUSABLE], 1.0, contact["name_key"],
                           {**contact, "match": names.MATCH_CONFUSABLE, "identity": identity.describe(contact["display_name"])}))
            continue
        if len(lowered) >= 3 and any(lowered in str(ep.get("canonical") or "").casefold() or lowered in str(ep.get("value") or "").casefold() for ep in contact["endpoints"]):
            ranked.append((names.MATCH_RANK[names.MATCH_FUZZY], 0.995, contact["name_key"], {**contact, "match": "endpoint"}))
            continue
        score = names.fuzzy_score(wanted, display_name=contact["display_name"], aliases=contact["aliases"])
        if score >= names.FUZZY_THRESHOLD:
            ranked.append((names.MATCH_RANK[names.MATCH_FUZZY], score, contact["name_key"], {**contact, "match": names.MATCH_FUZZY, "score": score}))
    ranked.sort(key=lambda item: (item[0], -item[1], item[2]))
    return [item[3] for item in ranked[: max(1, int(limit))]]


def list_events(contact_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    with _reader() as conn:
        rows = conn.execute(
            "SELECT seq, contact_id, endpoint_id, action, actor, detail_json, created_at FROM contact_events WHERE contact_id = ? ORDER BY seq DESC LIMIT ?",
            (str(contact_id or ""), max(1, int(limit))),
        ).fetchall()
    return [{**dict(row), "detail": json.loads(row["detail_json"] or "{}")} for row in rows]


# --- plans: what a change would do, computed from the rows as they are -------------------------------------------------

def _identity_of(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (row["kind"], row["canonical"], row["chain_network"], row["channel"], str(row["provider_account"]).casefold())


def _endpoint_record(row: dict[str, Any]) -> dict[str, Any]:
    """An endpoint as a review shows it and a plan binds it: the full value, its network or channel, its revision."""
    kind = row["kind"]
    return {
        "endpoint_id": row["endpoint_id"], "kind": kind, "kind_label": KIND_LABELS.get(kind, kind), "label": row.get("label", ""), "value": row["value"],
        "canonical": row["canonical"], "network": row.get("chain_network", ""), "network_display": network_display(str(row.get("chain_network") or "")) if kind == KIND_WALLET else "",
        "chain_environment": row.get("chain_environment", ""), "channel": row.get("channel", ""),
        "channel_label": channel_label(row.get("channel")) if kind == KIND_MESSAGING else "", "provider_account": row.get("provider_account", ""),
        "verification": row.get("verification", ""), "source_id": row.get("source_id", ""), "revision": int(row.get("revision") or 0), "fingerprint": row.get("fingerprint", ""),
    }


def _normalized_record(endpoint: NormalizedEndpoint) -> dict[str, Any]:
    return {
        "kind": endpoint.kind, "kind_label": KIND_LABELS.get(endpoint.kind, endpoint.kind), "label": endpoint.label, "value": endpoint.value,
        "canonical": endpoint.canonical, "network": endpoint.chain_network, "network_display": network_display(endpoint.chain_network) if endpoint.kind == KIND_WALLET else "",
        "chain_environment": endpoint.chain_environment, "channel": endpoint.channel,
        "channel_label": channel_label(endpoint.channel) if endpoint.kind == KIND_MESSAGING else "", "provider_account": endpoint.provider_account,
    }


def _renormalize(record: dict[str, Any]) -> NormalizedEndpoint:
    try:
        endpoint = normalize_endpoint({"kind": record["kind"], "value": record["value"], "label": record.get("label", ""), "channel": record.get("channel", ""),
                                       "provider_account": record.get("provider_account", ""), "network": record.get("network", "")})
    except EndpointError as exc:
        raise _as_contacts_error(exc) from None
    if endpoint.canonical != record.get("canonical", endpoint.canonical) or endpoint.chain_network != record.get("network", endpoint.chain_network):
        raise ContactsError("change_stale", "An entry no longer reads the way it was reviewed; review the change again.", status=409)
    return endpoint


def _require_active(conn: sqlite3.Connection, contact_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM contacts WHERE contact_id = ?", (str(contact_id or "").strip(),)).fetchone()
    if row is None:
        raise ContactsError("contact_not_found", "No saved contact has that id.", status=404)
    if row["state"] != "active":
        raise ContactsError("contact_deleted", "That contact was deleted.", status=410)
    return row


def _require_revision(row: sqlite3.Row, reviewed: Any) -> None:
    if int(row["revision"]) != int(reviewed):
        raise ContactsError("revision_mismatch", f"{row['display_name']} changed after this was reviewed; review it again. Nothing changed.",
                            details={"contact_id": row["contact_id"], "current_revision": int(row["revision"]), "reviewed_revision": int(reviewed)}, status=409)


def plan_create(*, display_name: Any, kind: Any = "person", aliases: Iterable[Any] = (), endpoints: Iterable[Any] = (), notes: Any = "",
                verification: str = VERIFICATION_USER_ENTERED, origin: str = "local", source_id: str = "", source_refs: dict[Any, Any] | None = None) -> dict[str, Any]:
    name = _clean_name(display_name)
    contact_kind = str(kind or "person").strip().lower()
    if contact_kind not in CONTACT_KINDS:
        raise ContactsError("contact_kind_unknown", "A contact is a person or a service.", details={"field": "kind"})
    if verification not in VERIFICATIONS:
        raise ContactsError("verification_unknown", "Unknown verification state.")
    normalized = _normalize_all(endpoints)
    refs = {str(key): str(value or "") for key, value in (source_refs or {}).items()}
    return {
        "action": "create", "display_name": name, "kind": contact_kind, "aliases": _clean_aliases(aliases, display_name=name), "notes": _clean_notes(notes),
        "endpoints": [_normalized_record(e) for e in normalized], "verification": verification, "origin": str(origin or "local")[:40],
        "source_id": str(source_id or ""), "source_refs": {str(index): refs.get(str(index), "") for index in range(len(normalized))},
    }


def plan_update(conn: sqlite3.Connection, contact_id: str, *, expected_revision: Any = None, display_name: Any = None, kind: Any = None, notes: Any = None,
                add_aliases: Iterable[Any] = (), remove_aliases: Iterable[Any] = (), add_endpoints: Iterable[Any] = (),
                change_endpoints: Iterable[dict[str, Any]] = (), remove_endpoint_ids: Iterable[str] = (), confirm_endpoint_ids: Iterable[str] = (),
                verification: str = VERIFICATION_USER_ENTERED, source_id: str = "", source_ref: str = "") -> dict[str, Any]:
    """Everything an edit of one saved contact would change, with the before and after of each field and the revisions read.

    ``confirm_endpoint_ids`` marks imported entries as checked: only their verification changes, so the endpoint fingerprint and
    revision a draft or proposal bound stay as they are."""
    change_list = [dict(item) for item in (change_endpoints or []) if isinstance(item, dict)]
    remove_list = [str(item).strip() for item in (remove_endpoint_ids or []) if str(item).strip()]
    confirm_list = [str(item).strip() for item in (confirm_endpoint_ids or []) if str(item).strip()]
    if verification not in VERIFICATIONS:
        raise ContactsError("verification_unknown", "Unknown verification state.")
    row = _require_active(conn, contact_id)
    revision = int(row["revision"])
    if expected_revision not in (None, ""):
        try:
            shown = int(expected_revision)
        except (TypeError, ValueError):
            raise ContactsError("revision_invalid", "The contact revision must be a number.") from None
        if shown != revision:
            raise ContactsError("revision_mismatch", "This contact changed since it was shown; reload it and try again.",
                                details={"current_revision": revision}, status=409)
    changes: list[dict[str, Any]] = []
    name = row["display_name"]
    if display_name is not None:
        new_name = _clean_name(display_name)
        if new_name != name:
            changes.append({"action": "renamed", "before": name, "after": new_name})
            name = new_name
    if kind is not None:
        new_kind = str(kind or "").strip().lower()
        if new_kind not in CONTACT_KINDS:
            raise ContactsError("contact_kind_unknown", "A contact is a person or a service.", details={"field": "kind"})
        if new_kind != row["kind"]:
            changes.append({"action": "kind_changed", "before": row["kind"], "after": new_kind})
    if notes is not None:
        new_notes = _clean_notes(notes)
        if new_notes != row["notes"]:
            changes.append({"action": "notes_changed", "before": row["notes"], "after": new_notes})
    existing_aliases = {r["alias_key"]: r["alias"] for r in conn.execute("SELECT alias, alias_key FROM contact_aliases WHERE contact_id = ? ORDER BY created_at, alias",
                                                                          (row["contact_id"],))}
    for raw in remove_aliases or []:
        key = names.name_key(names.clean_display(raw))
        if key in existing_aliases:
            changes.append({"action": "alias_removed", "alias": existing_aliases.pop(key), "alias_key": key})
    for alias in _clean_aliases(add_aliases, display_name=name):
        key = names.name_key(alias)
        if key in existing_aliases:
            continue
        if len(existing_aliases) >= MAX_ALIASES:
            raise ContactsError("too_many_aliases", f"A contact holds at most {MAX_ALIASES} aliases.")
        existing_aliases[key] = alias
        changes.append({"action": "alias_added", "alias": alias, "alias_key": key})
    active = {r["endpoint_id"]: dict(r) for r in conn.execute("SELECT * FROM contact_endpoints WHERE contact_id = ? AND state = 'active' ORDER BY kind, created_at",
                                                               (row["contact_id"],))}
    for endpoint_id in remove_list:
        current = active.pop(endpoint_id, None)
        if current is None:
            raise ContactsError("endpoint_not_found", "That endpoint is not saved on this contact.", details={"endpoint_id": endpoint_id}, status=404)
        changes.append({"action": "endpoint_removed", "endpoint_id": endpoint_id, "endpoint_revision": int(current["revision"]), "before": _endpoint_record(current)})
    for change in change_list:
        endpoint_id = str(change.get("endpoint_id") or "").strip()
        current = active.get(endpoint_id)
        if current is None:
            raise ContactsError("endpoint_not_found", "That endpoint is not saved on this contact.", details={"endpoint_id": endpoint_id}, status=404)
        merged = {
            "kind": current["kind"], "value": change.get("value", current["value"]), "label": change.get("label", current["label"]),
            "channel": change.get("channel", current["channel"]), "provider_account": change.get("provider_account", current["provider_account"]),
            "network": change.get("network", current["chain_network"]),
        }
        try:
            normalized = normalize_endpoint(merged)
        except EndpointError as exc:
            raise _as_contacts_error(exc, endpoint_id=endpoint_id) from None
        before = _endpoint_record(current)
        if normalized.identity() == _identity_of(current):
            if normalized.label != current["label"] or normalized.value != current["value"]:
                changes.append({"action": "endpoint_relabelled", "endpoint_id": endpoint_id, "endpoint_revision": int(current["revision"]), "before": before,
                                "after": {**before, "label": normalized.label, "value": normalized.value}})
            continue
        changes.append({"action": "endpoint_changed", "endpoint_id": endpoint_id, "endpoint_revision": int(current["revision"]), "before": before,
                        "after": _normalized_record(normalized)})
        active[endpoint_id] = {**current, "canonical": normalized.canonical, "chain_network": normalized.chain_network, "channel": normalized.channel,
                               "provider_account": normalized.provider_account}
    for endpoint_id in confirm_list:
        current = active.get(endpoint_id)
        if current is None:
            raise ContactsError("endpoint_not_found", "That endpoint is not saved on this contact.", details={"endpoint_id": endpoint_id}, status=404)
        if current.get("verification") != VERIFICATION_IMPORTED:
            continue
        changes.append({"action": "endpoint_confirmed", "endpoint_id": endpoint_id, "endpoint_revision": int(current["revision"]), "before": _endpoint_record(current)})
    identities = {_identity_of(e) for e in active.values()}
    additions = _normalize_all(add_endpoints)
    if len(identities) + len(additions) > MAX_ENDPOINTS:
        raise ContactsError("too_many_endpoints", f"A contact holds at most {MAX_ENDPOINTS} endpoints.")
    already_saved: list[dict[str, Any]] = []
    for endpoint in additions:
        if endpoint.identity() in identities:
            already_saved.append({"kind": endpoint.kind, "value": endpoint.value, "chain_network": endpoint.chain_network})
            continue
        identities.add(endpoint.identity())
        changes.append({"action": "endpoint_added", "after": _normalized_record(endpoint)})
    return {
        "action": "update", "contact_id": row["contact_id"], "base_revision": revision, "display_name_before": row["display_name"], "kind_before": row["kind"],
        "changes": changes, "already_saved": already_saved, "verification": verification, "source_id": str(source_id or ""), "source_ref": str(source_ref or "")[:200],
    }


def plan_delete(conn: sqlite3.Connection, contact_id: str, *, expected_revision: Any = None) -> dict[str, Any]:
    row = _require_active(conn, contact_id)
    if expected_revision not in (None, ""):
        try:
            shown = int(expected_revision)
        except (TypeError, ValueError):
            raise ContactsError("revision_invalid", "The contact revision must be a number.") from None
        if shown != int(row["revision"]):
            raise ContactsError("revision_mismatch", "This contact changed since it was shown; reload it and try again.",
                                details={"current_revision": int(row["revision"])}, status=409)
    aliases = [r["alias"] for r in conn.execute("SELECT alias FROM contact_aliases WHERE contact_id = ? ORDER BY created_at, alias", (row["contact_id"],))]
    endpoints = [_endpoint_record(dict(r)) for r in conn.execute("SELECT * FROM contact_endpoints WHERE contact_id = ? AND state = 'active' ORDER BY kind, created_at",
                                                                 (row["contact_id"],))]
    return {"action": "delete", "contact_id": row["contact_id"], "base_revision": int(row["revision"]),
            "before": {"display_name": row["display_name"], "kind": row["kind"], "aliases": aliases, "notes": row["notes"], "endpoints": endpoints}}


def plan_accept_suggestion(conn: sqlite3.Connection, suggestion_id: str, *, contact_id: str = "", display_name: Any = "") -> list[dict[str, Any]]:
    """The owner takes a suggestion: an edit of the saved contact it names (replace or add), or a new contact, plus the decision."""
    row = conn.execute("SELECT * FROM contact_suggestions WHERE suggestion_id = ?", (str(suggestion_id or "").strip(),)).fetchone()
    if row is None:
        raise ContactsError("suggestion_not_found", "No such suggestion.", status=404)
    if row["state"] != "pending":
        raise ContactsError("suggestion_decided", f"That suggestion was already {row['state']}.", status=409)
    view = _suggestion_view(row)
    endpoint = {"kind": row["kind"], "value": row["value"], "label": row["label"], "channel": row["channel"], "provider_account": row["provider_account"],
                "network": row["chain_network"]}
    marker: dict[str, Any] = {"action": "accept_suggestion", "suggestion_id": row["suggestion_id"],
                              "suggestion": {key: view.get(key, "") for key in ("kind", "kind_label", "label", "value", "chain_network", "network_display", "channel",
                                                                                "channel_label", "provider_account", "origin", "origin_ref", "reason", "display_name",
                                                                                "replaces_endpoint_id")}}
    target = str(contact_id or row["contact_id"] or "").strip()
    if row["replaces_endpoint_id"]:
        main = plan_update(conn, row["contact_id"], change_endpoints=[{"endpoint_id": row["replaces_endpoint_id"], **endpoint}], verification=VERIFICATION_USER_CONFIRMED)
        marker["contact_id"] = main["contact_id"]
    elif target:
        main = plan_update(conn, target, add_endpoints=[endpoint], verification=VERIFICATION_USER_CONFIRMED)
        marker["contact_id"] = main["contact_id"]
    else:
        main = plan_create(display_name=names.clean_display(display_name) or row["display_name"], endpoints=[endpoint], verification=VERIFICATION_USER_CONFIRMED,
                           origin="suggestion")
        marker["target_ref"] = "__previous__"
    return [main, marker]


# --- applies: only inside core.contacts.authority's commit transaction ----------------------------------------------

def apply_create(conn: sqlite3.Connection, step: dict[str, Any], *, actor: str, operation_id: str, now: str) -> str:
    contact_id = _new_id("ct")
    conn.execute(
        "INSERT INTO contacts (contact_id, kind, display_name, name_key, notes, origin, state, revision, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, 'active', 1, ?, ?)",
        (contact_id, step["kind"], step["display_name"], names.name_key(step["display_name"]), step["notes"], step["origin"], now, now),
    )
    for alias in step["aliases"]:
        conn.execute("INSERT INTO contact_aliases (contact_id, alias, alias_key, created_at) VALUES (?, ?, ?, ?)", (contact_id, alias, names.name_key(alias), now))
    created: list[str] = []
    for index, record in enumerate(step["endpoints"]):
        created.append(_insert_endpoint(conn, contact_id, _renormalize(record), verification=step["verification"], source_id=step["source_id"],
                                        source_ref=str(step["source_refs"].get(str(index), "")), now=now))
    _journal(conn, contact_id, "created", actor, now=now,
             detail={"display_name": step["display_name"], "aliases": step["aliases"], "endpoints": created, "origin": step["origin"], "operation_id": operation_id})
    return contact_id


def _current_endpoint(conn: sqlite3.Connection, contact_id: str, change: dict[str, Any]) -> sqlite3.Row:
    current = conn.execute("SELECT * FROM contact_endpoints WHERE endpoint_id = ? AND contact_id = ?", (change["endpoint_id"], contact_id)).fetchone()
    before = change.get("before") or {}
    if (current is None or current["state"] != "active" or int(current["revision"]) != int(change["endpoint_revision"])
            or current["value"] != before.get("value") or current["label"] != before.get("label") or current["verification"] != before.get("verification")):
        raise ContactsError("change_stale", "A saved entry changed after this was reviewed; review it again. Nothing changed.",
                            details={"endpoint_id": change["endpoint_id"]}, status=409)
    return current


def apply_update(conn: sqlite3.Connection, step: dict[str, Any], *, actor: str, operation_id: str, now: str) -> list[dict[str, Any]]:
    row = _require_active(conn, step["contact_id"])
    _require_revision(row, step["base_revision"])
    contact_id = row["contact_id"]
    applied: list[dict[str, Any]] = []
    for change in step["changes"]:
        action = change["action"]
        done = dict(change)
        if action == "renamed":
            conn.execute("UPDATE contacts SET display_name = ?, name_key = ? WHERE contact_id = ?", (change["after"], names.name_key(change["after"]), contact_id))
        elif action == "kind_changed":
            conn.execute("UPDATE contacts SET kind = ? WHERE contact_id = ?", (change["after"], contact_id))
        elif action == "notes_changed":
            conn.execute("UPDATE contacts SET notes = ? WHERE contact_id = ?", (change["after"], contact_id))
        elif action == "alias_removed":
            if conn.execute("DELETE FROM contact_aliases WHERE contact_id = ? AND alias_key = ?", (contact_id, change["alias_key"])).rowcount != 1:
                raise ContactsError("change_stale", "An alias changed after this was reviewed; review it again. Nothing changed.", status=409)
        elif action == "alias_added":
            if conn.execute("SELECT 1 FROM contact_aliases WHERE contact_id = ? AND alias_key = ?", (contact_id, change["alias_key"])).fetchone():
                raise ContactsError("change_stale", "An alias changed after this was reviewed; review it again. Nothing changed.", status=409)
            conn.execute("INSERT INTO contact_aliases (contact_id, alias, alias_key, created_at) VALUES (?, ?, ?, ?)", (contact_id, change["alias"], change["alias_key"], now))
        elif action == "endpoint_removed":
            current = _current_endpoint(conn, contact_id, change)
            conn.execute("UPDATE contact_endpoints SET state = 'removed', removed_at = ?, updated_at = ? WHERE endpoint_id = ?", (now, now, current["endpoint_id"]))
            _journal(conn, contact_id, "endpoint_removed", actor, endpoint_id=current["endpoint_id"], now=now,
                     detail={"value": current["value"], "chain_network": current["chain_network"], "operation_id": operation_id})
        elif action == "endpoint_changed":
            current = _current_endpoint(conn, contact_id, change)
            endpoint = _renormalize(change["after"])
            revision = int(current["revision"]) + 1
            fingerprint = endpoint_fingerprint(contact_id=contact_id, endpoint_id=current["endpoint_id"], kind=endpoint.kind, canonical=endpoint.canonical,
                                               chain_network=endpoint.chain_network, channel=endpoint.channel, provider_account=endpoint.provider_account)
            conn.execute(
                "UPDATE contact_endpoints SET label = ?, value = ?, canonical = ?, channel = ?, provider_account = ?, chain_network = ?, chain_family = ?,"
                " chain_environment = ?, verification = ?, source_ref = '', revision = ?, fingerprint = ?, updated_at = ? WHERE endpoint_id = ?",
                (endpoint.label, endpoint.value, endpoint.canonical, endpoint.channel, endpoint.provider_account, endpoint.chain_network, endpoint.chain_family,
                 endpoint.chain_environment, step["verification"], revision, fingerprint, now, current["endpoint_id"]),
            )
            done["revision"] = revision
            _journal(conn, contact_id, "endpoint_changed", actor, endpoint_id=current["endpoint_id"], now=now,
                     detail={"before": current["value"], "after": endpoint.value, "before_network": current["chain_network"], "after_network": endpoint.chain_network,
                             "operation_id": operation_id})
        elif action == "endpoint_relabelled":
            current = _current_endpoint(conn, contact_id, change)
            conn.execute("UPDATE contact_endpoints SET label = ?, value = ?, updated_at = ? WHERE endpoint_id = ?",
                         (change["after"]["label"], change["after"]["value"], now, current["endpoint_id"]))
        elif action == "endpoint_confirmed":
            current = _current_endpoint(conn, contact_id, change)
            if current["verification"] != VERIFICATION_IMPORTED:
                raise ContactsError("change_stale", "That entry was already confirmed; review the change again. Nothing changed.", status=409)
            conn.execute("UPDATE contact_endpoints SET verification = ?, updated_at = ? WHERE endpoint_id = ?", (VERIFICATION_USER_CONFIRMED, now, current["endpoint_id"]))
            _journal(conn, contact_id, "endpoint_confirmed", actor, endpoint_id=current["endpoint_id"], now=now,
                     detail={"value": current["value"], "chain_network": current["chain_network"], "operation_id": operation_id})
        elif action == "endpoint_added":
            endpoint = _renormalize(change["after"])
            saved = {_identity_of(dict(r)) for r in conn.execute("SELECT * FROM contact_endpoints WHERE contact_id = ? AND state = 'active'", (contact_id,))}
            if endpoint.identity() in saved:
                raise ContactsError("change_stale", "That entry was saved after this was reviewed; review it again. Nothing changed.", status=409)
            if len(saved) >= MAX_ENDPOINTS:
                raise ContactsError("too_many_endpoints", f"A contact holds at most {MAX_ENDPOINTS} endpoints.")
            endpoint_id = _insert_endpoint(conn, contact_id, endpoint, verification=step["verification"], source_id=step["source_id"], source_ref=step["source_ref"], now=now)
            done["endpoint_id"] = endpoint_id
            _journal(conn, contact_id, "endpoint_added", actor, endpoint_id=endpoint_id, now=now,
                     detail={"value": endpoint.value, "chain_network": endpoint.chain_network, "operation_id": operation_id})
        else:
            raise ContactsError("change_unknown", f"Unknown change {action!r}; nothing changed.", status=400)
        applied.append(done)
    conn.execute("UPDATE contacts SET revision = revision + 1, updated_at = ? WHERE contact_id = ?", (now, contact_id))
    _journal(conn, contact_id, "updated", actor, now=now, detail={"changes": [c["action"] for c in applied], "operation_id": operation_id})
    return applied


def apply_delete(conn: sqlite3.Connection, step: dict[str, Any], *, actor: str, operation_id: str, now: str) -> dict[str, Any]:
    """Delete one contact, leaving a tombstone without names, aliases, notes or endpoint values, and keyed hashes of its name keys."""
    row = _require_active(conn, step["contact_id"])
    _require_revision(row, step["base_revision"])
    contact_id = row["contact_id"]
    aliases = [r["alias"] for r in conn.execute("SELECT alias FROM contact_aliases WHERE contact_id = ?", (contact_id,))]
    retained_until = (datetime.fromisoformat(now) + timedelta(days=TOMBSTONE_RETENTION_DAYS)).isoformat()
    conn.execute("DELETE FROM contact_identity_tombstones WHERE retained_until <= ?", (now,))
    for key_hash in identity.tombstone_hashes([row["display_name"], *aliases]):
        conn.execute("INSERT OR REPLACE INTO contact_identity_tombstones (key_hash, contact_id, deleted_at, retained_until) VALUES (?, ?, ?, ?)",
                     (key_hash, contact_id, now, retained_until))
    erased = conn.execute("SELECT COUNT(*) FROM contact_endpoints WHERE contact_id = ? AND state != 'deleted'", (contact_id,)).fetchone()[0]
    conn.execute("UPDATE contacts SET display_name = '', name_key = '', notes = '', state = 'deleted', deleted_at = ?, updated_at = ?, revision = revision + 1"
                 " WHERE contact_id = ?", (now, now, contact_id))
    conn.execute("DELETE FROM contact_aliases WHERE contact_id = ?", (contact_id,))
    conn.execute("UPDATE contact_endpoints SET value = '', canonical = '', label = '', provider_account = '', source_ref = '', fingerprint = '',"
                 " state = 'deleted', removed_at = COALESCE(removed_at, ?), updated_at = ? WHERE contact_id = ?", (now, now, contact_id))
    conn.execute("UPDATE contact_events SET detail_json = '{}' WHERE contact_id = ?", (contact_id,))
    conn.execute("UPDATE contact_suggestions SET state = 'dismissed', value = '', canonical = '', display_name = '', decided_at = ?"
                 " WHERE contact_id = ? AND state = 'pending'", (now, contact_id))
    _journal(conn, contact_id, "deleted", actor, now=now, detail={"endpoints_erased": int(erased), "operation_id": operation_id})
    return {"contact_id": contact_id, "state": "deleted", "deleted_at": now, "endpoints_erased": int(erased)}


def apply_accept_suggestion(conn: sqlite3.Connection, step: dict[str, Any], *, contact_id: str, actor: str, operation_id: str, now: str) -> None:
    row = conn.execute("SELECT state FROM contact_suggestions WHERE suggestion_id = ?", (step["suggestion_id"],)).fetchone()
    if row is None:
        raise ContactsError("suggestion_not_found", "No such suggestion.", status=404)
    if row["state"] != "pending":
        raise ContactsError("suggestion_decided", f"That suggestion was already {row['state']}.", status=409)
    conn.execute("UPDATE contact_suggestions SET state = 'accepted', contact_id = ?, decided_at = ? WHERE suggestion_id = ? AND state = 'pending'",
                 (contact_id, now, step["suggestion_id"]))
    if contact_id:
        _journal(conn, contact_id, "suggestion_accepted", actor, now=now, detail={"suggestion_id": step["suggestion_id"], "operation_id": operation_id})


# --- the retired direct writers ----------------------------------------------------------------------------------------

def create_contact(*, display_name: Any, actor: str, kind: str = "person", aliases: Iterable[Any] = (), endpoints: Iterable[Any] = (),
                   notes: Any = "", verification: str = VERIFICATION_USER_ENTERED, origin: str = "local", source_id: str = "",
                   source_refs: dict[Any, str] | None = None) -> dict[str, Any]:
    """Create one contact through core.contacts.authority. A distinct new name from the owner's own intent commits at once;
    a name that is the same as, or looks like, a saved, recently deleted or same-change name -- or a name from a source that
    is not the owner -- raises ``protected_review_required`` naming the pending operation. ``actor`` names the source only."""
    from core.contacts import authority

    outcome = authority.propose(
        {"operation": "create", "display_name": display_name, "kind": kind, "aliases": list(aliases or []), "endpoints": list(endpoints or []), "notes": notes,
         "verification": verification, "origin": origin, "source_id": source_id, "source_refs": {str(k): v for k, v in (source_refs or {}).items()}},
        source=authority.source_for_actor(actor), requested_by=str(actor or ""),
    )
    if outcome["status"] == "committed":
        contact = get_contact(outcome["result"]["created"][0]["contact_id"])
        assert contact is not None
        return contact
    operation = outcome.get("operation") or {}
    raise ContactsError("protected_review_required", authority.pending_message(operation),
                        details={"operation_id": operation.get("operation_id", ""), "reasons": list(operation.get("reasons") or [])}, status=409)


def _refuse_direct_change(contact_id: str = "") -> ContactsError:
    return ContactsError("authentication_required", AUTHENTICATION_REQUIRED_MESSAGE, status=403,
                         details={"authority": "core.contacts.authority", **({"contact_id": str(contact_id)} if contact_id else {})})


def update_contact(contact_id: str, *, actor: str, **changes: Any) -> dict[str, Any]:
    """Refused. A change to a saved contact is proposed to core.contacts.authority and confirmed with the operator credential;
    no ``actor`` value, confirmation flag or approval id stands in for that."""
    raise _refuse_direct_change(contact_id)


def delete_contact(contact_id: str, *, actor: str, expected_revision: int | None = None) -> dict[str, Any]:
    """Refused, like ``update_contact``: deletion is a protected change confirmed in Contacts."""
    raise _refuse_direct_change(contact_id)


def accept_suggestion(suggestion_id: str, *, actor: str, contact_id: str = "", display_name: Any = "") -> dict[str, Any]:
    """Refused, like ``update_contact``: accepting goes through core.contacts.authority."""
    raise _refuse_direct_change(contact_id)


# --- suggestions: destinations read from untrusted content wait for the owner ---------------------------------------

def _suggestion_view(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    view = dict(row)
    view["kind_label"] = KIND_LABELS.get(view.get("kind", ""), view.get("kind", ""))
    if view.get("kind") == KIND_WALLET:
        view["network_display"] = network_display(str(view.get("chain_network") or ""))
    if view.get("kind") == KIND_MESSAGING:
        view["channel_label"] = channel_label(view.get("channel"))
    return view


def add_suggestion(*, endpoint: dict[str, Any], origin: str, actor: str, contact_id: str = "", display_name: Any = "", replaces_endpoint_id: str = "",
                   origin_ref: str = "", reason: str = "") -> dict[str, Any]:
    """Record a destination the owner has not confirmed. Returns ``{"status": "recorded"|"already_pending"|"already_saved", ...}``."""
    try:
        normalized = normalize_endpoint(endpoint)
    except EndpointError as exc:
        raise _as_contacts_error(exc) from None
    name = names.clean_display(display_name)
    now = _now()
    with _tx() as conn:
        if contact_id:
            _require_active(conn, contact_id)
            saved = conn.execute(
                "SELECT endpoint_id FROM contact_endpoints WHERE contact_id = ? AND state = 'active' AND kind = ? AND canonical = ? AND chain_network = ? AND channel = ?",
                (contact_id, normalized.kind, normalized.canonical, normalized.chain_network, normalized.channel),
            ).fetchone()
            if saved is not None and not replaces_endpoint_id:
                return {"status": "already_saved", "endpoint_id": saved["endpoint_id"], "contact_id": contact_id}
        if replaces_endpoint_id:
            target = conn.execute("SELECT contact_id, state, kind FROM contact_endpoints WHERE endpoint_id = ?", (replaces_endpoint_id,)).fetchone()
            if target is None or target["state"] != "active" or (contact_id and target["contact_id"] != contact_id) or target["kind"] != normalized.kind:
                raise ContactsError("endpoint_not_found", "The address this would replace is not saved.", details={"endpoint_id": replaces_endpoint_id}, status=404)
            contact_id = target["contact_id"]
        pending = conn.execute(
            "SELECT * FROM contact_suggestions WHERE state = 'pending' AND contact_id = ? AND replaces_endpoint_id = ? AND kind = ? AND canonical = ? AND chain_network = ? AND channel = ?",
            (contact_id, replaces_endpoint_id, normalized.kind, normalized.canonical, normalized.chain_network, normalized.channel),
        ).fetchone()
        if pending is not None:
            return {"status": "already_pending", "suggestion": _suggestion_view(pending)}
        suggestion_id = _new_id("sg")
        conn.execute(
            "INSERT INTO contact_suggestions (suggestion_id, contact_id, display_name, replaces_endpoint_id, kind, label, value, canonical, channel, provider_account,"
            " chain_network, origin, origin_ref, reason, state, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            (suggestion_id, contact_id, name, replaces_endpoint_id, normalized.kind, normalized.label, normalized.value, normalized.canonical, normalized.channel,
             normalized.provider_account, normalized.chain_network, str(origin or "unknown")[:60], str(origin_ref or "")[:200], str(reason or "")[:200], now),
        )
        if contact_id:
            _journal(conn, contact_id, "suggestion_recorded", actor, endpoint_id=replaces_endpoint_id,
                     detail={"suggestion_id": suggestion_id, "origin": origin, "value": normalized.value}, now=now)
        row = conn.execute("SELECT * FROM contact_suggestions WHERE suggestion_id = ?", (suggestion_id,)).fetchone()
    return {"status": "recorded", "suggestion": _suggestion_view(row)}


def list_suggestions(*, state: str = "pending", limit: int = 100) -> list[dict[str, Any]]:
    with _reader() as conn:
        rows = conn.execute("SELECT * FROM contact_suggestions WHERE state = ? ORDER BY created_at DESC LIMIT ?", (state, max(1, int(limit)))).fetchall()
    return [_suggestion_view(row) for row in rows]


def get_suggestion(suggestion_id: str) -> dict[str, Any] | None:
    with _reader() as conn:
        row = conn.execute("SELECT * FROM contact_suggestions WHERE suggestion_id = ?", (str(suggestion_id or "").strip(),)).fetchone()
    return _suggestion_view(row) if row else None


def dismiss_suggestion(suggestion_id: str, *, actor: str) -> dict[str, Any]:
    """Dismissing leaves every saved contact as it is, so it needs no credential; it only closes the suggestion."""
    if actor != ACTOR_OWNER:
        raise ContactsError("owner_confirmation_required", "Only the owner can dismiss a suggested address.", status=403)
    now = _now()
    with _tx() as conn:
        row = conn.execute("SELECT state, contact_id FROM contact_suggestions WHERE suggestion_id = ?", (str(suggestion_id or "").strip(),)).fetchone()
        if row is None:
            raise ContactsError("suggestion_not_found", "No such suggestion.", status=404)
        if row["state"] != "pending":
            raise ContactsError("suggestion_decided", f"That suggestion was already {row['state']}.", status=409)
        conn.execute("UPDATE contact_suggestions SET state = 'dismissed', decided_at = ? WHERE suggestion_id = ?", (now, str(suggestion_id).strip()))
        if row["contact_id"]:
            _journal(conn, row["contact_id"], "suggestion_dismissed", actor, detail={"suggestion_id": str(suggestion_id).strip()}, now=now)
    return {"suggestion_id": str(suggestion_id).strip(), "state": "dismissed"}


def reset_contacts_for_tests() -> None:
    """Tests: empty every Contacts table in the active database."""
    with _tx() as conn:
        for table in TABLES:
            conn.execute(f"DELETE FROM {table}")


__all__ = [
    "ACTOR_MODEL", "ACTOR_MODEL_APPROVED", "ACTOR_OWNER", "ACTOR_USER_TEXT", "AUTHORITY", "CONTACT_KINDS", "ContactsError", "TOMBSTONE_RETENTION_DAYS",
    "VERIFICATION_IMPORTED", "VERIFICATION_USER_CONFIRMED", "VERIFICATION_USER_ENTERED", "accept_suggestion", "active_contacts", "add_suggestion", "apply_accept_suggestion",
    "apply_create", "apply_delete", "apply_update", "create_contact", "delete_contact", "dismiss_suggestion", "endpoints_with_identity", "get_contact", "get_endpoint",
    "get_suggestion", "list_contacts", "list_events", "list_suggestions", "plan_accept_suggestion", "plan_create", "plan_delete", "plan_update",
    "reset_contacts_for_tests", "update_contact",
]
