"""PA Contacts operator commands: the owner's Contacts surface.

Reads (owner-local routes): list or search, one contact's detail, a resolution for the quick chooser, the accepted entry
kinds / messaging channels / wallet networks, the suggestions and pending changes waiting for the owner, and whether the
PIN or password is set. Mutations (operator only): create, update, delete, confirm or dismiss a suggestion, confirm or
cancel a pending change, and set, change or reset the PIN or password; each is guarded again in core.web.api.contacts_api
(owner-local peer, JSON, same origin, bounded body), and every change to a saved contact is applied only by
core.contacts.authority after the operator credential confirms it.

The model never reaches these commands: its tools are the contacts.* runtime contracts, bounded by request provenance.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from core.command_registry.groups.convergence import _from_api_response, _headers, _legacy_fault, _peer
from core.command_registry.spec import Availability, CommandSpec, GroupSpec, Handler, HandlerOk, OperatorAuthority

_MODULE = "core.command_registry.groups.contacts_group"


@dataclass(frozen=True)
class ContactsInput:
    payload: dict = dataclasses.field(default_factory=dict)


#: command id -> (action, description)
READS: dict[str, tuple[str, str]] = {
    "contacts.book.list": ("list", "List saved contacts, or search them by name, alias or part of an address"),
    "contacts.book.detail": ("detail", "One saved contact with every entry, where each came from, and its change journal"),
    "contacts.book.resolve": ("resolve", "Resolve a name to one exact destination of one kind, or return the choices"),
    "contacts.book.options": ("options", "The entry kinds, messaging channels and wallet networks Contacts accepts"),
    "contacts.book.suggestions": ("suggestions", "Destinations read from untrusted content that wait for the owner"),
    "contacts.import.sources": ("import_sources", "Import sources, their state, and the verified credential bindings an import may name"),
    "contacts.operations.list": ("operations", "Changes to saved contacts that wait for the owner's PIN or password, each with its full review"),
    "contacts.operations.detail": ("operation_detail", "One change with the saved contact as it is, the exact change and every lookalike name"),
    "contacts.credential.status": ("credential_status", "Whether the PIN or password that protects contact changes is set, and any lock in force"),
}
WRITES: dict[str, tuple[str, str]] = {
    "contacts.book.create": ("create", "Save a new contact with its aliases and entries (owner)"),
    "contacts.book.update": ("update", "Edit a saved contact: name, aliases, notes, labels and entries (owner)"),
    "contacts.book.delete": ("delete", "Delete a saved contact, leaving a tombstone without names or values (owner)"),
    "contacts.book.suggestion.accept": ("accept", "Confirm a suggested destination onto a contact (owner)"),
    "contacts.book.suggestion.dismiss": ("dismiss", "Dismiss a suggested destination (owner)"),
    "contacts.import.connect": ("import_connect", "Connect one import source with the owner's explicit consent (owner)"),
    "contacts.import.preview": ("import_preview", "Read one source once and preview what would be new or duplicate; saves nothing (owner)"),
    "contacts.import.apply": ("import_apply", "Import only the items the owner selected from a preview (owner)"),
    "contacts.import.remove": ("import_remove", "Remove an import source, keeping or erasing the entries still as imported (owner)"),
    "contacts.operations.confirm": ("operation_confirm", "Confirm one reviewed change with the PIN or password; saves exactly that change (owner)"),
    "contacts.operations.commit": ("operation_commit", "Save a change whose confirmation was accepted but not yet saved (owner)"),
    "contacts.operations.cancel": ("operation_cancel", "Cancel a pending change; saved contacts stay as they are (owner)"),
    "contacts.credential.enroll": ("credential_enroll", "Set the PIN or password that protects contact changes, after the system confirmation (owner)"),
    "contacts.credential.change": ("credential_change", "Change that PIN or password with the current one, after the system confirmation (owner)"),
    "contacts.credential.reset": ("credential_reset", "Reset a forgotten PIN or password with the recovery code, after the system confirmation (owner)"),
}


def _read(action: str, inp):
    from core.web.api.contacts_api import contacts_read

    status, payload = contacts_read(action, dict(inp.payload or {}))
    if status == 200:
        return HandlerOk(data=payload, summary=f"Contacts {action}")
    code = {400: "usage", 404: "fault_validation", 409: "conflict", 410: "fault_validation"}.get(status, "fault_tool")
    return _legacy_fault(code, f"Contacts {action} refused", status, payload)


def _write(action: str, inp, ctx):
    from core.web.api.contacts_api import contacts_write_authority
    from core.web.api.runtime import RuntimeServices

    resp = contacts_write_authority(action, dict(inp.payload or {}), _headers(ctx), RuntimeServices(display_name="VOOL"), _peer(ctx))
    return _from_api_response(resp, ok_summary=f"Contacts {action} applied", fault_summary=f"Contacts {action} refused",
                              receipts=({"kind": "contacts", "action": action},))


def _handle_list(inp, ctx):
    return _read("list", inp)


def _handle_detail(inp, ctx):
    return _read("detail", inp)


def _handle_resolve(inp, ctx):
    return _read("resolve", inp)


def _handle_options(inp, ctx):
    return _read("options", inp)


def _handle_suggestions(inp, ctx):
    return _read("suggestions", inp)


def _handle_create(inp, ctx):
    return _write("create", inp, ctx)


def _handle_update(inp, ctx):
    return _write("update", inp, ctx)


def _handle_delete(inp, ctx):
    return _write("delete", inp, ctx)


def _handle_accept(inp, ctx):
    return _write("accept", inp, ctx)


def _handle_dismiss(inp, ctx):
    return _write("dismiss", inp, ctx)


def _handle_import_sources(inp, ctx):
    return _read("import_sources", inp)


def _handle_import_connect(inp, ctx):
    return _write("import_connect", inp, ctx)


def _handle_import_preview(inp, ctx):
    return _write("import_preview", inp, ctx)


def _handle_import_apply(inp, ctx):
    return _write("import_apply", inp, ctx)


def _handle_import_remove(inp, ctx):
    return _write("import_remove", inp, ctx)


def _handle_operations(inp, ctx):
    return _read("operations", inp)


def _handle_operation_detail(inp, ctx):
    return _read("operation_detail", inp)


def _handle_credential_status(inp, ctx):
    return _read("credential_status", inp)


def _handle_operation_confirm(inp, ctx):
    return _write("operation_confirm", inp, ctx)


def _handle_operation_commit(inp, ctx):
    return _write("operation_commit", inp, ctx)


def _handle_operation_cancel(inp, ctx):
    return _write("operation_cancel", inp, ctx)


def _handle_credential_enroll(inp, ctx):
    return _write("credential_enroll", inp, ctx)


def _handle_credential_change(inp, ctx):
    return _write("credential_change", inp, ctx)


def _handle_credential_reset(inp, ctx):
    return _write("credential_reset", inp, ctx)


def _probe_contacts_store(context: dict) -> tuple[bool, str]:
    """Availability evidence for the mutating Contacts doors: the store opens and holds the
    contacts tables (the v6/v7 migrations). A store that cannot open must fail the check loudly
    rather than let a mutating door claim constant-true availability."""
    try:
        from core.contacts import store as _store

        with _store._reader() as conn:
            present = conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('contacts', 'contact_operations')"
            ).fetchone()[0]
        if int(present) != 2:
            return False, f"contacts store incomplete: {present} of 2 tables present"
        return True, ""
    except Exception as exc:
        return False, f"contacts store unreachable: {exc}"


def register(reg) -> None:
    reg.add_group(GroupSpec(group_id="contacts", description="the owner's saved people and services and their exact destinations"))
    for command_id, (action, description) in READS.items():
        reg.add(
            CommandSpec(
                command_id=command_id,
                group="contacts",
                description=description,
                input_schema=ContactsInput,
                effects="read_only",
                handler=Handler(f"{_MODULE}:_handle_{action}"),
                exit_codes=(0,),
            )
        )
    for command_id, (action, description) in WRITES.items():
        reg.add(
            CommandSpec(
                command_id=command_id,
                group="contacts",
                description=description,
                input_schema=ContactsInput,
                effects="mutating",
                capabilities=frozenset({"change_settings"}),
                permission=OperatorAuthority(kind="settings.write", verifier="core.command_registry.groups.convergence:_gate_operator"),
                availability=Availability(f"{_MODULE}:_probe_contacts_store"),
                handler=Handler(f"{_MODULE}:_handle_{action}"),
                exit_codes=(0, 2, 21, 30),
            )
        )
