"""Owner-local Contacts doors behind the /api/contacts routes (core.command_registry.groups.contacts_group).

Reads feed the owner's Contacts surface. Writes run only after the operator guard (owner-local TCP peer, JSON, same
origin, bounded body). That guard proves a local caller, not a person, so these doors hold no authority of their own:
every change goes to core.contacts.authority. A distinct new contact is created at once; any change to a saved contact and
any same or lookalike name comes back as a pending change, which only ``operation_confirm`` with the operator credential
(core.operator_credential) applies. Fields such as ``actor``, ``confirm``, ``approval_id`` or ``human_confirmed`` in a body
are ignored. Secret fields are removed from the payload before anything else runs and are never echoed, logged or stored.
Nothing here sends, messages or pays.
"""
from __future__ import annotations

from typing import Any

from core.contacts import resolver, store
from core.contacts.endpoints import KIND_LABELS, KINDS, MESSAGING_CHANNELS

_SECRET_FIELDS = ("secret", "current_secret", "new_secret", "recovery_code")


def _as_list(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _endpoint_in(raw: Any) -> dict[str, Any]:
    """The Contacts form's entry fields in the owner's vocabulary: account is the messaging provider account."""
    entry = dict(raw) if isinstance(raw, dict) else {}
    if "account" in entry and "provider_account" not in entry:
        entry["provider_account"] = entry.pop("account")
    return entry


def _brief(contact: dict[str, Any]) -> dict[str, Any]:
    from core.contacts.tools import _contact_brief

    brief = {**_contact_brief(contact), "revision": int(contact.get("revision") or 0)}
    if contact.get("match"):
        brief["match"] = contact["match"]
    return brief


def _suggestion_payload(suggestion: dict[str, Any]) -> dict[str, Any]:
    out = dict(suggestion)
    if suggestion.get("contact_id"):
        contact = store.get_contact(suggestion["contact_id"])
        out["contact_display_name"] = contact["display_name"] if contact and contact["state"] == "active" else ""
    replaced = store.get_endpoint(suggestion["replaces_endpoint_id"]) if suggestion.get("replaces_endpoint_id") else None
    out["replaces"] = ({"value": replaced["value"], "label": replaced.get("label", ""), "network_display": replaced.get("network_display", "")}
                       if replaced and replaced.get("state") == "active" else None)
    return out


def _options() -> dict[str, Any]:
    from core.wallet import chains

    return {
        "ok": True,
        "kinds": [{"id": kind, "label": KIND_LABELS[kind]} for kind in KINDS],
        "channels": [{"id": key, "label": spec["label"], "account_required": bool(spec["account_required"]), "hint": spec["hint"]}
                     for key, spec in MESSAGING_CHANNELS.items()],
        "networks": [{"network": spec.network, "display_name": spec.display_name, "environment": spec.environment, "family": spec.family,
                      "chain_key": spec.chain_key} for spec in chains.all_networks()],
    }


def _verified_bindings(providers: set[str]) -> list[dict[str, Any]]:
    """The verified credential bindings an import may name, for the contacts providers only: id, provider, account and
    status, never a secret."""
    try:
        from core.credential_intelligence.binding import binding_from_row, load_index

        bindings = [binding.to_dict() for binding in (binding_from_row(row) for row in load_index().values()) if binding is not None]
    except Exception:
        return []
    return [binding for binding in bindings if binding.get("status") == "verified" and binding.get("provider_id") in providers]


def _import_sources_payload() -> dict[str, Any]:
    from core.contacts import importing
    from core.contacts.importers import PROVIDERS, adapter_for
    from core.contacts.importers.base import SourceAccount

    providers = []
    for provider, label in PROVIDERS.items():
        adapter = adapter_for(provider)
        providers.append({"provider": provider, "label": label, "needs_binding": bool(getattr(adapter, "needs_binding", False)),
                          "binding_providers": list(getattr(adapter, "binding_providers", ())),
                          "authorization": adapter.authorization(SourceAccount(provider=provider))})
    wanted = {name for entry in providers for name in entry["binding_providers"]}
    return {"ok": True, "providers": providers, "sources": importing.list_sources(), "bindings": _verified_bindings(wanted)}


def _credential_brief() -> dict[str, Any]:
    from core.operator_credential import authority as credential

    status = credential.status()
    return {key: status[key] for key in ("enrolled", "kind", "retry_after_seconds", "setup_path", "pin_digits", "password_chars", "recovery_available")}


def contacts_read(action: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """(status, payload) for one read of the owner's Contacts surface."""
    from core.contacts import authority
    from core.operator_credential import CredentialError

    try:
        if action == "import_sources":
            return 200, _import_sources_payload()
        if action == "list":
            contacts = store.list_contacts(str(payload.get("query") or ""))
            return 200, {"ok": True, "contacts": [_brief(c) for c in contacts], "pending_suggestions": len(store.list_suggestions()),
                         "pending_changes": authority.pending_count()}
        if action == "detail":
            contact = store.get_contact(str(payload.get("contact_id") or ""))
            if contact is None or contact.get("state") != "active":
                return 404, {"ok": False, "reason": "contact_not_found", "message": "No saved contact has that id."}
            events = [{key: event.get(key) for key in ("seq", "action", "actor", "endpoint_id", "created_at")}
                      for event in store.list_events(contact["contact_id"], limit=20)]
            from core.contacts import identity

            return 200, {"ok": True, "contact": {**contact, "identity": identity.describe(contact["display_name"])}, "events": events}
        if action == "resolve":
            resolution = resolver.resolve(
                payload.get("name") or "", kind=str(payload.get("kind") or ""), label=str(payload.get("label") or ""),
                network=str(payload.get("network") or ""), chain=str(payload.get("chain") or ""), channel=str(payload.get("channel") or ""),
                provider_account=str(payload.get("account") or ""),
            )
            return 200, resolution.as_dict()
        if action == "options":
            return 200, _options()
        if action == "suggestions":
            return 200, {"ok": True, "suggestions": [_suggestion_payload(s) for s in store.list_suggestions()]}
        if action == "operations":
            return 200, {"ok": True, "operations": authority.list_operations(), "credential": _credential_brief()}
        if action == "operation_detail":
            operation = authority.get_operation(str(payload.get("operation_id") or ""))
            if operation is None:
                return 404, {"ok": False, "reason": "operation_not_found", "message": "No such pending change."}
            return 200, {"ok": True, "operation": operation, "credential": _credential_brief()}
        if action == "credential_status":
            from core.operator_credential import authority as credential

            return 200, {"ok": True, **_credential_brief(), "policy": credential.lock_policy()}
    except (store.ContactsError, CredentialError) as exc:
        return exc.status, exc.as_dict()
    return 400, {"ok": False, "reason": "unknown_action", "message": "Unknown Contacts read."}


def _outcome(outcome: dict[str, Any]) -> dict[str, Any]:
    """A door's answer to a proposed change: applied (with the contact) or pending (with its review), never ambiguous."""
    from core.contacts import authority

    status = outcome["status"]
    data: dict[str, Any] = {"status": status, "applied": status == "committed", "operation": outcome.get("operation"),
                            "already_saved": outcome.get("already_saved") or []}
    if status == "committed":
        result = outcome.get("result") or {}
        touched = [entry.get("contact_id") for key in ("created", "updated") for entry in result.get(key) or []]
        if touched and touched[0]:
            data["contact"] = store.get_contact(touched[0])
        data["message"] = "Saved."
    elif status == "unchanged":
        data["message"] = "Nothing changed: " + ("; ".join(f"{a['kind']} {a['value']} is already saved" for a in data["already_saved"]) or "it is already so") + "."
    else:
        data["message"] = authority.pending_message(outcome.get("operation"))
    return data


def contacts_write_authority(action: str, body: dict, headers: dict, runtime, client_host: str = "127.0.0.1"):
    """OPERATOR: one owner action on Contacts, behind the operator guard. Returns the route's ApiResponse."""
    from core.contacts import authority
    from core.operator_credential import authority as credential
    from core.web.api.registry_authorities import operator_json_guard
    from core.web.api.service import apply_runtime_headers, json_response

    refused = operator_json_guard(body, headers, runtime, client_host)
    if refused is not None:
        return refused
    payload = dict(body or {})
    typed = {field: payload.pop(field, None) for field in _SECRET_FIELDS}

    def answer(status: int, data: dict[str, Any]):
        return apply_runtime_headers(json_response(status, data), runtime)

    def ok(data: dict[str, Any]):
        return answer(200, {"ok": True, **data})

    try:
        if action == "create":
            return ok(_outcome(authority.propose(
                {"operation": "create", "display_name": payload.get("display_name"), "kind": str(payload.get("kind") or "person"),
                 "aliases": _as_list(payload.get("aliases")), "endpoints": [_endpoint_in(e) for e in _as_list(payload.get("endpoints"))],
                 "notes": payload.get("notes") or ""},
                source=authority.SOURCE_OWNER_UI, requested_by="owner")))
        if action == "update":
            change = {
                "operation": "update", "contact_id": str(payload.get("contact_id") or ""), "expected_revision": _int_or_none(payload.get("expected_revision")),
                "add_aliases": _as_list(payload.get("add_aliases")), "remove_aliases": _as_list(payload.get("remove_aliases")),
                "add_endpoints": [_endpoint_in(e) for e in _as_list(payload.get("add_endpoints"))],
                "change_endpoints": [_endpoint_in(e) for e in _as_list(payload.get("change_endpoints"))],
                "remove_endpoint_ids": [str(x) for x in _as_list(payload.get("remove_endpoint_ids"))],
                "confirm_endpoint_ids": [str(x) for x in _as_list(payload.get("confirm_endpoint_ids"))],
            }
            for field in ("display_name", "kind", "notes"):
                if payload.get(field) is not None:
                    change[field] = payload.get(field)
            return ok(_outcome(authority.propose(change, source=authority.SOURCE_OWNER_UI, requested_by="owner")))
        if action == "delete":
            if payload.get("confirm") is not True:
                return answer(409, {"ok": False, "reason": "confirmation_required", "message": "Confirm deleting the contact; nothing was deleted."})
            return ok(_outcome(authority.propose({"operation": "delete", "contact_id": str(payload.get("contact_id") or ""),
                                                  "expected_revision": _int_or_none(payload.get("expected_revision"))},
                                                 source=authority.SOURCE_OWNER_UI, requested_by="owner")))
        if action == "accept":
            return ok(_outcome(authority.propose({"operation": "accept_suggestion", "suggestion_id": str(payload.get("suggestion_id") or ""),
                                                  "contact_id": str(payload.get("contact_id") or ""), "display_name": payload.get("display_name") or ""},
                                                 source=authority.SOURCE_OWNER_UI, requested_by="owner")))
        if action == "dismiss":
            return ok(store.dismiss_suggestion(str(payload.get("suggestion_id") or ""), actor=store.ACTOR_OWNER))
        if action == "operation_confirm":
            try:
                result = authority.confirm(str(payload.get("operation_id") or ""), secret=typed["secret"], expected_digest=str(payload.get("digest") or ""))
            except authority.CommitNotSaved as exc:
                return answer(503, {"ok": False, "reason": "commit_not_saved",
                                    "message": "Your PIN was accepted, but the change could not be saved just now. Save it again before the confirmation expires; nothing changed yet.",
                                    "retry": {"operation_id": exc.operation_id, "authorization_id": exc.authorization["authorization_id"],
                                              "expires_at": exc.authorization["expires_at"]}})
            return ok({"status": result["status"], "replayed": bool(result.get("replayed")), "result": result.get("result"),
                       "operation": authority.get_operation(result["operation_id"])})
        if action == "operation_commit":
            result = authority.commit(str(payload.get("operation_id") or ""), authorization_id=str(payload.get("authorization_id") or ""))
            return ok({"status": result["status"], "replayed": bool(result.get("replayed")), "result": result.get("result"),
                       "operation": authority.get_operation(result["operation_id"])})
        if action == "operation_cancel":
            return ok(authority.cancel(str(payload.get("operation_id") or "")))
        if action == "credential_enroll":
            return ok(credential.enroll(typed["secret"], kind=str(payload.get("kind") or "")))
        if action == "credential_change":
            return ok(credential.change(typed["current_secret"], typed["new_secret"], kind=str(payload.get("kind") or "")))
        if action == "credential_reset":
            return ok(credential.reset_with_recovery(typed["recovery_code"], typed["new_secret"], kind=str(payload.get("kind") or "")))
        if action.startswith("import_"):
            from core.contacts import importing

            if action == "import_connect":
                source = importing.connect_source(str(payload.get("provider") or ""), consent=payload.get("consent") is True,
                                                  account_label=str(payload.get("account_label") or ""), auth_binding=str(payload.get("auth_binding") or ""))
                return ok({"source": source})
            if action == "import_preview":
                run = importing.preview(str(payload.get("source_id") or ""))
                if run.get("state") != "previewed":
                    error = run.get("error") or {}
                    return answer(409, {"ok": False, "reason": error.get("reason", "read_failed"),
                                        "message": error.get("recovery") or "The source could not be read; nothing was imported.", "run": run})
                return ok({"run": run})
            if action == "import_apply":
                selections = payload.get("selections") if isinstance(payload.get("selections"), dict) else {}
                return ok({"applied": importing.apply(str(payload.get("run_id") or ""), {str(k): str(v) for k, v in selections.items()})})
            if action == "import_remove":
                return ok({"removed": importing.remove_source(str(payload.get("source_id") or ""), erase_imported=payload.get("erase_imported") is True)})
    except (store.ContactsError, credential.CredentialError) as exc:
        return answer(exc.status, exc.as_dict())
    finally:
        typed.clear()
    return answer(400, {"ok": False, "reason": "unknown_action", "message": "Unknown Contacts action."})


__all__ = ["contacts_read", "contacts_write_authority"]
