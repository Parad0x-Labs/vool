"""Contact import: an owner-started, read-only copy from one source account, previewed before anything is saved.

Law:

* Nothing is read until the owner connects a source with explicit consent, naming the provider and the account (and, for
  Google or Microsoft, a verified credential binding). Nothing is saved until the owner applies a preview with an explicit
  action for each item they selected; an item without a selection is not imported.
* Every imported entry is ``imported_unverified`` and carries its source and the provider's record reference.
* A preview names what would duplicate: entries already saved (same destination), people whose exact name is already
  saved with other entries, names that look like a saved name (core.contacts.identity), entries the import itself repeats,
  and entries Contacts refuses (invalid values, key material -- never stored, not even in the preview). A same-name or
  lookalike item is never preselected for creation.
* Applying goes through core.contacts.authority. New people whose names collide with nothing are created at once, as one
  batch. Everything else -- entries added to a saved contact, a same or lookalike name -- is one pending batch the owner
  confirms with the PIN or password, bound to every contact revision it touches.
* A read failure records its typed reason and recovery on the source and the run. A partial read is never applied.
* Import never writes to the source. Removing a source stops future previews at once; its imported entries stay, shown with
  a removed source, unless the owner asks to erase the entries still exactly as imported -- which is a pending change like
  any other change to saved contacts.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from core.contacts import authority, identity, names, store
from core.contacts.endpoints import VERIFICATION_IMPORTED, EndpointError, normalize_endpoint, secret_material_reason
from core.contacts.importers import PROVIDERS, adapter_for
from core.contacts.importers.base import REASON_BINDING_REQUIRED, RECOVERY, ImportSourceError, SourceAccount

MAX_IMPORT = 5000
ACTION_CREATE = "create"
ACTION_SKIP = "skip"
ACTION_MERGE_PREFIX = "merge:"


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _binding_refusal(binding: str, providers: tuple[str, ...] = ()) -> str:
    """Why a source cannot name this credential binding ('' when it can): it must exist, be verified and belong to one of the
    adapter's binding providers. The KAS transport attaches whatever a verified binding holds, so a key stored for another
    service must be refused here, before it could be sent to a contacts provider."""
    try:
        from core.credential_intelligence.binding import load_index

        rows = load_index()
    except Exception:
        return "credential_binding_index_unreadable"
    row = rows.get(binding) or next((entry for entry in rows.values() if str(entry.get("binding_id") or "") == binding), None)
    if row is None:
        return "unknown_credential_binding"
    if str(row.get("status") or "") != "verified":
        return "credential_binding_not_verified"
    if providers and str(row.get("provider_id") or "") not in providers:
        return "credential_binding_wrong_provider"
    return ""


def _source_view(row: Any) -> dict[str, Any]:
    view = dict(row)
    view["provider_label"] = PROVIDERS.get(view.get("provider", ""), view.get("provider", ""))
    view["recovery"] = RECOVERY.get(str(view.get("status_detail") or ""), "") if view.get("status") not in ("connected", "removed") else ""
    return view


def list_sources(*, include_removed: bool = True) -> list[dict[str, Any]]:
    with store._reader() as conn:
        rows = conn.execute("SELECT * FROM contact_sources ORDER BY created_at").fetchall()
    return [_source_view(row) for row in rows if include_removed or row["status"] != "removed"]


def get_source(source_id: str) -> dict[str, Any] | None:
    with store._reader() as conn:
        row = conn.execute("SELECT * FROM contact_sources WHERE source_id = ?", (str(source_id or ""),)).fetchone()
    return _source_view(row) if row else None


def connect_source(provider: str, *, consent: bool, account_label: str = "", auth_binding: str = "", adapter: Any = None) -> dict[str, Any]:
    if consent is not True:
        raise store.ContactsError("consent_required", "Choose to import from this account first; nothing was connected.", status=409)
    if provider not in PROVIDERS:
        raise store.ContactsError("provider_unknown", "Contacts can import from Apple Contacts, Google Contacts or Microsoft Outlook.")
    source_adapter = adapter or adapter_for(provider)
    binding = str(auth_binding or "").strip()
    if getattr(source_adapter, "needs_binding", False):
        if not binding:
            raise store.ContactsError(REASON_BINDING_REQUIRED, RECOVERY[REASON_BINDING_REQUIRED])
        refusal = _binding_refusal(binding, tuple(getattr(source_adapter, "binding_providers", ()))) if adapter is None else ""
        if refusal == "credential_binding_wrong_provider":
            raise store.ContactsError(refusal, f"That stored credential is for another service; {PROVIDERS[provider]} needs its own connection. Nothing was connected.")
        if refusal:
            raise store.ContactsError(refusal, "That credential binding cannot be used: verify it under Keys first. Nothing was connected.")
    label = names.clean_display(account_label)[:120] or PROVIDERS[provider]
    now = _now()
    with store._tx() as conn:
        existing = conn.execute("SELECT * FROM contact_sources WHERE provider = ? AND auth_binding = ? AND account_label = ? AND status != 'removed'",
                                (provider, binding, label)).fetchone()
        if existing is not None:
            return _source_view(existing)
        source_id = store._new_id("src")
        conn.execute(
            "INSERT INTO contact_sources (source_id, provider, account_label, account_identity, auth_binding, status, status_detail, consented_at, created_at, updated_at)"
            " VALUES (?, ?, ?, '', ?, 'connected', '', ?, ?, ?)",
            (source_id, provider, label, binding, now, now, now),
        )
        row = conn.execute("SELECT * FROM contact_sources WHERE source_id = ?", (source_id,)).fetchone()
    return _source_view(row)


def _item_id(source_id: str, source_ref: str, index: int) -> str:
    return "itm-" + hashlib.sha256(f"{source_id}|{source_ref}|{index}".encode()).hexdigest()[:16]


def _identity_key(endpoint: Any) -> str:
    return json.dumps(list(endpoint.identity()))


def _build_preview(source: dict[str, Any], people: tuple[Any, ...]) -> dict[str, Any]:
    local = store.active_contacts()
    by_identity: dict[str, list[dict[str, Any]]] = {}
    by_name: dict[str, list[dict[str, Any]]] = {}
    for contact in local:
        by_name.setdefault(names.name_key(contact["display_name"]), []).append(contact)
        for endpoint in contact["endpoints"]:
            key = json.dumps([endpoint["kind"], endpoint["canonical"], endpoint["chain_network"], endpoint["channel"], str(endpoint["provider_account"]).casefold()])
            by_identity.setdefault(key, []).append(contact)
    seen_in_import: dict[str, str] = {}
    items: list[dict[str, Any]] = []
    for index, person in enumerate(people):
        item_id = _item_id(source["source_id"], person.source_ref, index)
        entries: list[dict[str, Any]] = []
        rejected: list[dict[str, str]] = []
        already: list[dict[str, Any]] = []
        repeated: list[str] = []
        matches: dict[str, dict[str, Any]] = {}
        for entry in person.entries:
            raw = {"kind": entry.kind, "value": entry.value, "label": entry.label, "channel": entry.channel, "provider_account": entry.provider_account}
            try:
                normalized = normalize_endpoint(raw)
            except EndpointError as exc:
                # the refused value is not kept: it may be key material
                rejected.append({"kind": entry.kind, "label": entry.label, "reason": exc.reason})
                continue
            key = _identity_key(normalized)
            if key in seen_in_import and seen_in_import[key] != item_id:
                repeated.append(normalized.value)
            seen_in_import.setdefault(key, item_id)
            owners = by_identity.get(key) or []
            view = {"kind": normalized.kind, "value": normalized.value, "label": normalized.label, "channel": normalized.channel,
                    "provider_account": normalized.provider_account, "network": normalized.chain_network}
            if owners:
                already.append({**view, "saved_on": [{"contact_id": c["contact_id"], "display_name": c["display_name"]} for c in owners]})
                for contact in owners:
                    matches.setdefault(contact["contact_id"], {"contact_id": contact["contact_id"], "display_name": contact["display_name"], "shared": 0})["shared"] += 1
            else:
                entries.append(view)
        display_name = names.clean_display(person.display_name)
        if display_name and (secret_material_reason(display_name) or identity.unsafe_characters(person.display_name)):
            # a name field carrying key material, or characters that make it display as another name, is refused and not kept
            reason = "secret_material_refused" if secret_material_reason(display_name) else "unsafe_characters"
            name_ok, display_name = False, ""
            rejected.append({"kind": "name", "label": "", "reason": reason})
        else:
            name_ok = bool(display_name)
        same_name = [c for c in by_name.get(names.name_key(display_name), []) if c["contact_id"] not in matches] if display_name else []
        lookalike = ([c for c in local if c["contact_id"] not in matches and c not in same_name
                      and identity.lookalike_of(display_name, display_name=c["display_name"], aliases=c["aliases"])] if display_name else [])
        if not name_ok:
            category, suggested = "no_name", ACTION_SKIP
        elif not entries and not already:
            category, suggested = "no_usable_entries", ACTION_SKIP
        elif not entries:
            category, suggested = "already_saved", ACTION_SKIP
        elif matches:
            best = max(matches.values(), key=lambda m: m["shared"])
            category, suggested = "adds_to_existing", ACTION_MERGE_PREFIX + best["contact_id"]
        elif same_name:
            category, suggested = "same_name_different_entries", ACTION_SKIP
        elif lookalike:
            category, suggested = "looks_like_saved_contact", ACTION_SKIP
        else:
            category, suggested = "new", ACTION_CREATE
        items.append({
            "item_id": item_id, "source_ref": person.source_ref, "display_name": display_name, "organization": person.organization,
            "identity": identity.describe(display_name) if display_name else None,
            "entries": entries, "already_saved": already, "rejected": rejected, "repeated_in_import": repeated,
            "matches": sorted(matches.values(), key=lambda m: -m["shared"]),
            "same_name": [{"contact_id": c["contact_id"], "display_name": c["display_name"]} for c in same_name],
            "lookalike_of": [{"contact_id": c["contact_id"], "display_name": c["display_name"], "identity": identity.describe(c["display_name"])} for c in lookalike],
            "category": category, "suggested_action": suggested,
        })
    counts: dict[str, int] = {}
    for item in items:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    return {"items": items, "counts": counts, "read": len(people)}


def preview(source_id: str, *, adapter: Any = None, limit: int = MAX_IMPORT) -> dict[str, Any]:
    """Read the source once and record a preview run (``previewed``), or a ``failed`` run with the typed reason."""
    source = get_source(source_id)
    if source is None:
        raise store.ContactsError("source_not_found", "No such import source.", status=404)
    if source["status"] == "removed":
        raise store.ContactsError("source_removed", "That import source was removed; connect it again to import.", status=410)
    source_adapter = adapter or adapter_for(source["provider"])
    account = SourceAccount(provider=source["provider"], account_label=source["account_label"], account_identity=source["account_identity"],
                            auth_binding=source["auth_binding"])
    run_id = store._new_id("imp")
    now = _now()
    try:
        people = source_adapter.read(account, limit=max(1, min(int(limit), MAX_IMPORT)))
    except ImportSourceError as exc:
        error = exc.as_dict()
        error["partial_read"] = len(exc.partial)
        with store._tx() as conn:
            conn.execute("UPDATE contact_sources SET status = ?, status_detail = ?, updated_at = ? WHERE source_id = ?", ("attention", exc.reason, now, source_id))
            conn.execute("INSERT INTO contact_import_runs (run_id, source_id, state, preview_json, error, created_at, updated_at) VALUES (?, ?, 'failed', '{}', ?, ?, ?)",
                         (run_id, source_id, json.dumps(error), now, now))
        return {"run_id": run_id, "source_id": source_id, "state": "failed", "error": error, "items": [], "counts": {}}
    built = _build_preview(source, tuple(people))
    with store._tx() as conn:
        conn.execute("UPDATE contact_sources SET status = 'connected', status_detail = '', last_read_at = ?, updated_at = ? WHERE source_id = ?", (now, now, source_id))
        conn.execute("INSERT INTO contact_import_runs (run_id, source_id, state, preview_json, created_at, updated_at) VALUES (?, ?, 'previewed', ?, ?, ?)",
                     (run_id, source_id, json.dumps(built), now, now))
    return {"run_id": run_id, "source_id": source_id, "state": "previewed", **built}


def get_run(run_id: str) -> dict[str, Any] | None:
    with store._reader() as conn:
        row = conn.execute("SELECT * FROM contact_import_runs WHERE run_id = ?", (str(run_id or ""),)).fetchone()
    if row is None:
        return None
    run = dict(row)
    run["preview"] = json.loads(run.pop("preview_json") or "{}")
    run["applied"] = json.loads(run.pop("applied_json") or "{}")
    run["error"] = json.loads(run["error"]) if str(run.get("error") or "").startswith("{") else run.get("error")
    return run


def _pending_brief(operation: dict[str, Any] | None, items: list[str]) -> dict[str, Any]:
    operation = operation or {}
    return {"operation_id": operation.get("operation_id", ""), "title": operation.get("title", ""), "reasons": operation.get("reasons", []), "items": items,
            "review_path": authority.REVIEW_PATH}


def apply(run_id: str, selections: dict[str, str]) -> dict[str, Any]:
    """Apply the owner's explicit selections to a previewed run: distinct new people at once, everything else as one pending batch."""
    run = get_run(run_id)
    if run is None:
        raise store.ContactsError("run_not_found", "No such import preview.", status=404)
    if run["state"] != "previewed":
        raise store.ContactsError("run_not_applicable", f"That preview is {run['state']}; preview the source again.", status=409)
    source = get_source(run["source_id"])
    if source is None or source["status"] == "removed":
        raise store.ContactsError("source_removed", "That import source was removed; nothing was imported.", status=410)
    items = {item["item_id"]: item for item in run["preview"].get("items") or []}
    plan: list[tuple[dict[str, Any], str]] = []
    for item_id, action in dict(selections or {}).items():
        item = items.get(str(item_id))
        if item is None:
            raise store.ContactsError("item_not_in_preview", "A selection names something that is not in this preview; nothing was imported.")
        action = str(action or "")
        if action == ACTION_SKIP:
            continue
        if action == ACTION_CREATE:
            if not item["display_name"] or not item["entries"]:
                raise store.ContactsError("item_has_nothing_to_create", f"'{item['display_name'] or item['source_ref']}' has no new entries to create; nothing was imported.")
        elif action.startswith(ACTION_MERGE_PREFIX):
            target = store.get_contact(action[len(ACTION_MERGE_PREFIX):])
            if target is None or target["state"] != "active":
                raise store.ContactsError("merge_target_missing", "A merge names a contact that is no longer saved; nothing was imported.", status=409)
        else:
            raise store.ContactsError("action_unknown", "Each selected item is created, merged into a saved contact, or skipped; nothing was imported.")
        plan.append((item, action))
    summary: dict[str, list[Any]] = {"created": [], "merged": [], "pending": [], "failed": []}
    distinct: list[tuple[dict[str, Any], dict[str, Any]]] = []
    protected: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for item, action in plan:
        endpoints = [{"kind": e["kind"], "value": e["value"], "label": e["label"], "channel": e["channel"], "provider_account": e["provider_account"], "network": e["network"]}
                     for e in item["entries"]]
        if action == ACTION_CREATE:
            change = {"operation": "create", "display_name": item["display_name"], "endpoints": endpoints, "verification": VERIFICATION_IMPORTED, "origin": "import",
                      "source_id": source["source_id"], "source_refs": {str(index): item["source_ref"] for index in range(len(endpoints))}}
        else:
            change = {"operation": "update", "contact_id": action[len(ACTION_MERGE_PREFIX):], "add_endpoints": endpoints, "verification": VERIFICATION_IMPORTED,
                      "source_id": source["source_id"], "source_ref": item["source_ref"]}
        try:
            looked = authority.preview(change, source=authority.SOURCE_IMPORT)
        except store.ContactsError as exc:
            summary["failed"].append({"item_id": item["item_id"], "reason": exc.reason, "message": exc.message})
            continue
        (protected if looked["reasons"] else distinct).append((item, change))
    requested_by = f"import:{source['source_id']}"
    for group in (distinct, protected):
        if not group:
            continue
        try:
            outcome = authority.propose({"operation": "batch", "changes": [change for _item, change in group]}, source=authority.SOURCE_IMPORT,
                                        requested_by=requested_by, source_ref=run_id)
        except store.ContactsError as exc:
            summary["failed"].extend({"item_id": item["item_id"], "reason": exc.reason, "message": exc.message} for item, _change in group)
            continue
        if outcome["status"] == "committed":
            result = outcome["result"]
            created = iter(result.get("created") or [])
            updated = {entry["contact_id"]: entry for entry in result.get("updated") or []}
            for item, change in group:
                if change["operation"] == "create":
                    summary["created"].append({"item_id": item["item_id"], "contact_id": next(created)["contact_id"]})
                else:
                    added = [c["endpoint_id"] for c in (updated.get(change["contact_id"]) or {}).get("changes", []) if c["action"] == "endpoint_added"]
                    summary["merged"].append({"item_id": item["item_id"], "contact_id": change["contact_id"], "added": added})
        elif outcome["status"] == "unchanged":
            summary["merged"].extend({"item_id": item["item_id"], "contact_id": change.get("contact_id", ""), "added": []} for item, change in group)
        else:
            summary["pending"].append(_pending_brief(outcome.get("operation"), [item["item_id"] for item, _change in group]))
    now = _now()
    with store._tx() as conn:
        conn.execute("UPDATE contact_import_runs SET state = 'applied', applied_json = ?, updated_at = ? WHERE run_id = ? AND state = 'previewed'",
                     (json.dumps(summary), now, run_id))
    return {"run_id": run_id, "state": "applied", **summary}


def remove_source(source_id: str, *, erase_imported: bool = False) -> dict[str, Any]:
    source = get_source(source_id)
    if source is None:
        raise store.ContactsError("source_not_found", "No such import source.", status=404)
    now = _now()
    erase: dict[str, Any] | None = None
    if erase_imported:
        with store._reader() as conn:
            rows = conn.execute("SELECT endpoint_id, contact_id FROM contact_endpoints WHERE source_id = ? AND state = 'active' AND verification = ?",
                                (source_id, VERIFICATION_IMPORTED)).fetchall()
        by_contact: dict[str, list[str]] = {}
        for row in rows:
            by_contact.setdefault(row["contact_id"], []).append(row["endpoint_id"])
        changes: list[dict[str, Any]] = []
        endpoints_to_erase = 0
        contacts_to_delete: list[str] = []
        for contact_id, endpoint_ids in by_contact.items():
            contact = store.get_contact(contact_id)
            if contact is None or contact["state"] != "active":
                continue
            endpoints_to_erase += len(endpoint_ids)
            if contact["origin"] == "import" and not [e for e in contact["endpoints"] if e["endpoint_id"] not in endpoint_ids]:
                changes.append({"operation": "delete", "contact_id": contact_id})
                contacts_to_delete.append(contact_id)
            else:
                changes.append({"operation": "update", "contact_id": contact_id, "remove_endpoint_ids": endpoint_ids})
        if changes:
            outcome = authority.propose({"operation": "batch", "changes": changes}, source=authority.SOURCE_OWNER_UI, requested_by="import_remove", source_ref=source_id)
            erase = {**_pending_brief(outcome.get("operation"), []), "endpoints_to_erase": endpoints_to_erase, "contacts_to_delete": contacts_to_delete,
                     "status": outcome["status"]}
    with store._tx() as conn:
        conn.execute("UPDATE contact_sources SET status = 'removed', status_detail = '', removed_at = ?, updated_at = ? WHERE source_id = ?", (now, now, source_id))
        conn.execute("UPDATE contact_import_runs SET state = 'discarded', preview_json = '{}', updated_at = ? WHERE source_id = ? AND state = 'previewed'", (now, source_id))
    return {"source_id": source_id, "state": "removed", "erased_endpoints": 0, "deleted_contacts": [], "erase_pending": erase}


__all__ = ["ACTION_CREATE", "ACTION_MERGE_PREFIX", "ACTION_SKIP", "apply", "connect_source", "get_run", "get_source", "list_sources", "preview", "remove_source"]
