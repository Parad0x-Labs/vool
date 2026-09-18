"""The runtime tool surface of PA Contacts: ``contacts.search``, ``resolve``, ``save``, ``update``, ``delete``.

What the model may do here, and why:

* ``contacts.search`` and ``contacts.resolve`` only read. A resolution never picks between ambiguous or lookalike matches;
  it returns the choices, so the reply asks the user.
* ``contacts.save`` uses only what the user's own words in this turn carry. The proof is the runtime's request provenance
  authority (``checkpoint_request_has_authority``): the turn checkpoint holds the visible user text and a stamp binding that
  text to this chat. A name, alias or destination value (email, phone, messaging identity, wallet address) is used only
  when it occurs in that text. Anything else -- an address the model read in an email, a web page or a provider profile --
  is recorded as a suggestion the owner confirms in Contacts. A new contact whose name is distinct from every saved and
  recently deleted name is created at once; a same or lookalike name waits for the owner's PIN or password.
* ``contacts.update`` and ``contacts.delete`` never change a saved contact from here, and neither does ``contacts.save``
  when it adds to one. Even in the user's own words, and even after the chat's approval prompt, the change is recorded
  as a pending change (core.contacts.authority) that the owner confirms with the PIN or password in Home → Contacts →
  Pending changes. A skill, a document or a tool argument cannot supply that confirmation.

Nothing here sends, dials, signs or pays.
"""
from __future__ import annotations

import re
from typing import Any

from core.contacts import authority, names, resolver, store
from core.contacts.endpoints import (
    KIND_EMAIL,
    KIND_LABELS,
    KIND_MESSAGING,
    KIND_PHONE,
    KIND_WALLET,
    EndpointError,
    NormalizedEndpoint,
    channel_label,
    kind_key,
    messaging_delivery,
    network_display,
    normalize_endpoint,
)

INTENTS: tuple[str, ...] = ("contacts.search", "contacts.resolve", "contacts.save", "contacts.update", "contacts.delete")
SUGGESTION_ORIGIN = "chat_unconfirmed"
_CONFIRM_WHERE = "Home → Contacts → Suggestions"
_REVIEW_WHERE = authority.REVIEW_PATH


# --- provenance ------------------------------------------------------------------------------------------------------

def proven_user_text(source_context: dict[str, Any] | None) -> str:
    """The visible user text of the turn in flight when the request provenance authority proves it, else ''."""
    context = source_context if isinstance(source_context, dict) else {}
    checkpoint_id = str(context.get("runtime_checkpoint_id") or "").strip()
    session_id = str(context.get("runtime_session_id") or context.get("session_id") or "").strip()
    if not checkpoint_id or not session_id:
        return ""
    try:
        from core.agent_runtime.request_authority import checkpoint_request_has_authority
        from core.runtime_continuity import get_runtime_checkpoint

        checkpoint = get_runtime_checkpoint(checkpoint_id)
        if not checkpoint or not checkpoint_request_has_authority(checkpoint, expected_session_id=session_id):
            return ""
    except Exception:
        return ""
    return str(checkpoint.get("request_text") or "")


_BETWEEN_DIGITS = re.compile(r"(?<=\d)[\s().\-]+(?=\d)")


def value_in_text(endpoint: NormalizedEndpoint, text: str) -> bool:
    """Whether the user's text carries this exact destination (as typed, or in its canonical form)."""
    if not text:
        return False
    if endpoint.kind == KIND_WALLET:
        return endpoint.value in text or (endpoint.chain_family == "evm" and endpoint.canonical in text.lower())
    folded = text.casefold()
    if endpoint.value.casefold() in folded or endpoint.canonical.casefold() in folded:
        return True
    digits = re.sub(r"\D", "", endpoint.canonical)
    if endpoint.kind in (KIND_PHONE, KIND_MESSAGING) and len(digits) >= 5 and re.fullmatch(r"\+?\d+", endpoint.canonical):
        return digits in re.sub(r"[^\d]", " ", _BETWEEN_DIGITS.sub("", text)).split() or digits in _BETWEEN_DIGITS.sub("", text).replace("+", "")
    return False


def name_in_text(name: str, text: str) -> bool:
    key = names.name_key(name)
    return bool(key) and key in names.name_key(text)


# --- presentation ----------------------------------------------------------------------------------------------------

def _endpoint_brief(endpoint: dict[str, Any]) -> dict[str, Any]:
    brief = {
        "endpoint_id": endpoint["endpoint_id"], "kind": endpoint["kind"], "label": endpoint.get("label", ""), "value": endpoint["value"],
        "verification": endpoint.get("verification", ""),
    }
    if endpoint["kind"] == KIND_WALLET:
        brief.update(network=endpoint.get("chain_network", ""), network_display=endpoint.get("network_display") or network_display(endpoint.get("chain_network", "")))
    if endpoint["kind"] == KIND_MESSAGING:
        brief.update(channel=endpoint.get("channel", ""), account=endpoint.get("provider_account", ""),
                     delivery_available=bool((endpoint.get("delivery") or messaging_delivery(endpoint.get("channel"))).get("available")))
    if endpoint.get("source"):
        brief["source"] = {k: endpoint["source"].get(k) for k in ("provider", "account_label")}
    return brief


def _contact_brief(contact: dict[str, Any]) -> dict[str, Any]:
    return {"contact_id": contact["contact_id"], "display_name": contact["display_name"], "kind": contact.get("kind", "person"),
            "aliases": list(contact.get("aliases") or []), "endpoints": [_endpoint_brief(e) for e in contact.get("endpoints") or []]}


def _endpoint_line(endpoint: dict[str, Any]) -> str:
    label = f"{endpoint['label']} " if endpoint.get("label") else ""
    kind = KIND_LABELS.get(endpoint["kind"], endpoint["kind"]).lower()
    where = ""
    if endpoint["kind"] == KIND_WALLET:
        where = f" on {endpoint.get('network_display') or network_display(endpoint.get('chain_network', ''))}"
    if endpoint["kind"] == KIND_MESSAGING:
        where = f" on {channel_label(endpoint.get('channel'))}" + (f" ({endpoint['provider_account']})" if endpoint.get("provider_account") else "")
    flag = " (imported, not verified)" if endpoint.get("verification") == "imported_unverified" else ""
    return f"{label}{kind} {endpoint['value']}{where}{flag}"


def _contact_line(contact: dict[str, Any]) -> str:
    endpoints = "; ".join(_endpoint_line(e) for e in contact.get("endpoints") or []) or "no addresses saved"
    aliases = f" (also: {', '.join(contact['aliases'])})" if contact.get("aliases") else ""
    return f"{contact['display_name']}{aliases} [{contact['contact_id']}]: {endpoints}"


def _suggestion_line(suggestion: dict[str, Any]) -> str:
    kind = KIND_LABELS.get(suggestion["kind"], suggestion["kind"]).lower()
    where = f" on {suggestion.get('network_display') or network_display(suggestion.get('chain_network', ''))}" if suggestion["kind"] == KIND_WALLET else ""
    if suggestion["kind"] == KIND_MESSAGING:
        where = f" on {channel_label(suggestion.get('channel'))}"
    return f"{kind} {suggestion['value']}{where}"


def _record_phrase(record: dict[str, Any], *, with_kind: bool = True) -> str:
    where = f" on {record['network_display']}" if record.get("network_display") else (f" on {record['channel_label']}" if record.get("channel_label") else "")
    label = f"{record['label']} " if record.get("label") and with_kind else ""
    head = f"{label}{str(record.get('kind_label') or record.get('kind') or 'entry').lower()} " if with_kind else ""
    return f"{head}{record.get('value', '')}{where}"


def _change_phrase(change: dict[str, Any]) -> str:
    action = change["action"]
    before, after = change.get("before") or {}, change.get("after") or {}
    if action == "renamed":
        return f"rename it from {change['before']} to {change['after']}"
    if action == "kind_changed":
        return f"make it a {change['after']}"
    if action == "notes_changed":
        return "change its notes"
    if action == "alias_added":
        return f"add the alias {change['alias']}"
    if action == "alias_removed":
        return f"remove the alias {change['alias']}"
    if action == "endpoint_added":
        return f"add {_record_phrase(after)}"
    if action == "endpoint_removed":
        return f"remove {_record_phrase(before)}"
    if action == "endpoint_changed":
        return f"change its {str(before.get('kind_label') or 'entry').lower()} from {_record_phrase(before, with_kind=False)} to {_record_phrase(after, with_kind=False)}"
    if action == "endpoint_relabelled":
        return f"relabel {before.get('value', '')} from '{before.get('label', '')}' to '{after.get('label', '')}'"
    if action == "endpoint_confirmed":
        return f"mark {before.get('value', '')} as confirmed"
    return action.replace("_", " ")


def _pending_summary(operation: dict[str, Any]) -> str:
    phrases: list[str] = []
    for step in operation.get("steps") or []:
        if step["action"] == "create":
            phrases.append(f"add {step['display_name']}")
        elif step["action"] == "update":
            phrases.extend(_change_phrase(change) for change in step.get("changes") or [])
        elif step["action"] == "delete":
            phrases.append(f"delete {step['display_name']} and its {len(step.get('endpoints') or [])} saved entries")
    return "; ".join(phrases)


def _result(ok: bool, status: str, text: str, *, details: dict[str, Any] | None = None, observation: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"ok": ok, "status": status, "response_text": text, "details": dict(details or {}), "observation": dict(observation or {})}


def _pending_result(outcome: dict[str, Any], *, lines_before: list[str], suggestions: list[dict[str, Any]], notes: list[str],
                    contact: dict[str, Any] | None) -> dict[str, Any]:
    operation = outcome.get("operation") or {}
    summary = _pending_summary(operation)
    lines = list(lines_before)
    lines.append(f"Nothing is changed yet: {operation.get('title') or 'this change'} waits for your PIN or password in {_REVIEW_WHERE}"
                 + (f" (it would {summary})." if summary else "."))
    lines.extend(conflict.get("explanation", "") for conflict in ((operation.get("identity") or {}).get("conflicts") or [])[:3])
    if suggestions:
        lines.append("Not saved yet, because it was not in your message: " + "; ".join(_suggestion_line(s) for s in suggestions)
                     + f". Confirm or dismiss it in {_CONFIRM_WHERE}.")
    lines.extend(notes)
    observation: dict[str, Any] = {
        "pending_operation": {"operation_id": operation.get("operation_id"), "title": operation.get("title"), "reasons": operation.get("reasons"),
                              "review_path": _REVIEW_WHERE, "applied": False},
        "suggestions": [s["suggestion_id"] for s in suggestions],
    }
    if contact:
        observation["contact"] = _contact_brief(contact)
    return _result(True, "pending_confirmation", "\n".join(line for line in lines if line),
                   details={"operation": operation, "suggestions": suggestions}, observation=observation)


# --- argument collection ---------------------------------------------------------------------------------------------

def _clean(value: Any) -> str:
    return names.clean_display(value)


def _as_list(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _requested_endpoints(args: dict[str, Any]) -> list[dict[str, Any]]:
    label = args.get("label")
    out: list[dict[str, Any]] = [dict(item) for item in _as_list(args.get("endpoints")) if isinstance(item, dict)]
    for value in _as_list(args.get("email")):
        out.append({"kind": KIND_EMAIL, "value": value, "label": label})
    for value in _as_list(args.get("phone")):
        out.append({"kind": KIND_PHONE, "value": value, "label": label})
    for value in _as_list(args.get("wallet_address")):
        out.append({"kind": KIND_WALLET, "value": value, "network": args.get("network"), "label": label})
    for value in _as_list(args.get("messaging_identity")):
        out.append({"kind": KIND_MESSAGING, "value": value, "channel": args.get("channel"), "account": args.get("account"), "label": label})
    return out


def _normalize(raw_endpoints: list[dict[str, Any]]) -> list[tuple[dict[str, Any], NormalizedEndpoint]]:
    pairs = []
    for index, raw in enumerate(raw_endpoints):
        try:
            pairs.append((raw, normalize_endpoint(raw)))
        except EndpointError as exc:
            raise store.ContactsError(exc.reason, exc.message, details={"field": exc.field, "index": index}) from None
    return pairs


def _raw_for(endpoint: NormalizedEndpoint) -> dict[str, Any]:
    return {"kind": endpoint.kind, "value": endpoint.value, "label": endpoint.label, "network": endpoint.chain_network,
            "channel": endpoint.channel, "provider_account": endpoint.provider_account}


def _exact_name_matches(name: str) -> list[dict[str, Any]]:
    key = names.name_key(name)
    return [c for c in store.active_contacts() if key and (names.name_key(c["display_name"]) == key or any(names.name_key(a) == key for a in c["aliases"]))]


def _target_contact(target: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """One active contact by id or exact name, or a refusal result."""
    wanted = _clean(target)
    if not wanted:
        return None, _result(False, "contact_required", "Say which saved contact.")
    if wanted.startswith("ct-"):
        contact = store.get_contact(wanted)
        if contact is None:
            return None, _result(False, "contact_not_found", "No saved contact has that id.")
        if contact["state"] != "active":
            return None, _result(False, "contact_deleted", "That contact was already deleted.")
        return contact, None
    matches = _exact_name_matches(wanted)
    if not matches:
        found = resolver.resolve(wanted, kind=KIND_EMAIL)
        hint = f" {found.message}" if found.suggestions or found.choices else ""
        return None, _result(False, "contact_not_found", f"No saved contact is called '{wanted}'.{hint}".strip(),
                             observation={"suggestions": list(found.suggestions), "choices": list(found.choices)})
    if len(matches) > 1:
        choices = [_contact_brief(c) for c in matches]
        return None, _result(False, "contact_ambiguous", f"{len(matches)} saved contacts are called '{wanted}': "
                             + "; ".join(_contact_line(c) for c in matches) + ". Which one?",
                             details={"choices": choices}, observation={"choices": choices})
    return matches[0], None


# --- tools -----------------------------------------------------------------------------------------------------------

def _search(args: dict[str, Any]) -> dict[str, Any]:
    contact_id = _clean(args.get("contact_id"))
    query = _clean(args.get("query") or args.get("name"))
    try:
        limit = min(max(int(args.get("limit") or 20), 1), 50)
    except (TypeError, ValueError):
        limit = 20
    if contact_id:
        found = store.get_contact(contact_id)
        contacts = [found] if found and found["state"] == "active" else []
    else:
        contacts = store.list_contacts(query, limit=limit)
    briefs = [{**_contact_brief(c), **({"match": c["match"]} if c.get("match") else {})} for c in contacts]
    if not contacts:
        text = f"No saved contact matches '{query}'." if query or contact_id else "No contacts are saved yet."
    else:
        text = "\n".join(_contact_line(c) + (" (looks like the name searched for, spelled with different characters)" if c.get("match") == names.MATCH_CONFUSABLE else "")
                         for c in contacts)
    return _result(True, "ok", text, details={"contacts": briefs}, observation={"count": len(briefs), "contacts": briefs})


def _resolve(args: dict[str, Any]) -> dict[str, Any]:
    kind = kind_key(args.get("kind"))
    resolution = resolver.resolve(
        args.get("name") or args.get("query") or "", kind=kind, label=_clean(args.get("label")), network=_clean(args.get("network")),
        chain=_clean(args.get("chain")), channel=_clean(args.get("channel")), provider_account=_clean(args.get("account")),
    )
    text = resolution.message
    observation: dict[str, Any] = {"resolution": resolution.status, "kind": kind}
    if resolution.ok and resolution.snapshot:
        text += f"\nExact destination: {resolver.describe_snapshot(resolution.snapshot)}"
        observation["destination"] = {k: resolution.snapshot.get(k) for k in ("resolution", "display_name", "label", "value", "chain_network", "network_display",
                                                                             "channel", "provider_account", "verification", "contact_id", "endpoint_id")}
        if resolution.snapshot.get("lookalikes"):
            observation["lookalikes"] = resolution.snapshot["lookalikes"]
        if kind == KIND_MESSAGING:
            delivery = messaging_delivery(resolution.snapshot.get("channel"))
            text += f"\n{delivery['message']}"
            observation["delivery_available"] = bool(delivery.get("available"))
    if resolution.choices:
        observation["choices"] = list(resolution.choices)
    if resolution.suggestions:
        observation["suggestions"] = list(resolution.suggestions)
    return _result(resolution.ok, "ok" if resolution.ok else resolution.status, text, details={"resolution": resolution.as_dict()}, observation=observation)


def _record_suggestions(pending: list[NormalizedEndpoint], *, contact_id: str, display_name: str, replaces: dict[str, str] | None = None) -> list[dict[str, Any]]:
    recorded = []
    for endpoint in pending:
        outcome = store.add_suggestion(
            endpoint=_raw_for(endpoint), origin=SUGGESTION_ORIGIN, actor=store.ACTOR_MODEL, contact_id=contact_id, display_name=display_name,
            replaces_endpoint_id=(replaces or {}).get(endpoint.value, ""), reason="not in the user's own message",
        )
        if outcome.get("suggestion"):
            recorded.append(outcome["suggestion"])
    return recorded


def _save(args: dict[str, Any], source_context: dict[str, Any] | None) -> dict[str, Any]:
    proof = proven_user_text(source_context)
    pairs = _normalize(_requested_endpoints(args))
    contact_id = _clean(args.get("contact_id"))
    name = _clean(args.get("name"))
    aliases = [_clean(a) for a in _as_list(args.get("aliases")) if _clean(a)]
    proven = [e for _raw, e in pairs if value_in_text(e, proof)]
    unproven = [e for _raw, e in pairs if not value_in_text(e, proof)]
    dropped_aliases = [a for a in aliases if not name_in_text(a, proof)]
    kept_aliases = [a for a in aliases if name_in_text(a, proof)]
    notes = args.get("notes")
    dropped_note = [f"Aliases not added (not in your message): {', '.join(dropped_aliases)}."] if dropped_aliases else []

    if contact_id:
        target, refusal = _target_contact(contact_id)
        if refusal is not None:
            return refusal
        assert target is not None
        suggestions = _record_suggestions(unproven, contact_id=target["contact_id"], display_name=target["display_name"])
        outcome = {"status": "unchanged", "already_saved": []}
        if kept_aliases or proven:
            outcome = authority.propose({"operation": "update", "contact_id": target["contact_id"], "add_aliases": kept_aliases,
                                         "add_endpoints": [_raw_for(e) for e in proven]}, source=authority.SOURCE_USER_CHAT, requested_by="contacts.save")
        if outcome["status"] == "unchanged":
            already = outcome.get("already_saved") or []
            lines = ["Already saved: " + "; ".join(f"{a['kind']} {a['value']}" for a in already) + "."] if already else []
            return _finish_save("saved_as_suggestion" if suggestions else "unchanged", lines, target, suggestions, dropped_aliases)
        return _pending_result(outcome, lines_before=[], suggestions=suggestions, notes=dropped_note, contact=target)

    if not name:
        return _result(False, "name_required", "A new contact needs a name.")
    existing = _exact_name_matches(name)
    if existing and not bool(args.get("create_new")):
        choices = [_contact_brief(c) for c in existing]
        return _result(False, "contact_exists",
                       f"A contact named '{name}' is already saved: " + "; ".join(_contact_line(c) for c in existing)
                       + ". Nothing was saved. Say whether these details belong to that contact or to a separate contact with the same name.",
                       details={"choices": choices}, observation={"choices": choices})
    if not name_in_text(name, proof):
        suggestions = _record_suggestions([e for _raw, e in pairs], contact_id="", display_name=name)
        text = (f"'{name}' was not in your message, so nothing was saved as a contact."
                + (f" The {len(suggestions)} address(es) wait for your confirmation in {_CONFIRM_WHERE}: " + "; ".join(_suggestion_line(s) for s in suggestions) + "." if suggestions else ""))
        return _result(bool(suggestions), "saved_as_suggestion" if suggestions else "not_saved", text,
                       details={"suggestions": suggestions}, observation={"suggestions": [s["suggestion_id"] for s in suggestions]})
    kind = str(args.get("kind") or "person").strip().lower() or "person"
    outcome = authority.propose({"operation": "create", "display_name": name, "kind": kind, "aliases": kept_aliases, "endpoints": [_raw_for(e) for e in proven],
                                 "notes": notes or ""}, source=authority.SOURCE_USER_CHAT, requested_by="contacts.save")
    if outcome["status"] == "committed":
        contact = store.get_contact(outcome["result"]["created"][0]["contact_id"])
        assert contact is not None
        suggestions = _record_suggestions(unproven, contact_id=contact["contact_id"], display_name=contact["display_name"])
        return _finish_save("saved_with_suggestions" if suggestions else "saved", [f"Saved {_contact_line(contact)}."], contact, suggestions, dropped_aliases)
    suggestions = _record_suggestions(unproven, contact_id="", display_name=name)
    return _pending_result(outcome, lines_before=[], suggestions=suggestions, notes=dropped_note, contact=None)


def _finish_save(status: str, lines: list[str], contact: dict[str, Any], suggestions: list[dict[str, Any]], dropped_aliases: list[str]) -> dict[str, Any]:
    if suggestions:
        lines.append("Not saved yet, because it was not in your message: " + "; ".join(_suggestion_line(s) for s in suggestions)
                     + f". Confirm or dismiss it in {_CONFIRM_WHERE}.")
    if dropped_aliases:
        lines.append(f"Aliases not added (not in your message): {', '.join(dropped_aliases)}.")
    brief = _contact_brief(contact)
    return _result(True, status, "\n".join(lines) or "Nothing changed.", details={"contact": brief, "suggestions": suggestions},
                   observation={"contact": brief, "suggestions": [s["suggestion_id"] for s in suggestions]})


def _update(args: dict[str, Any], source_context: dict[str, Any] | None) -> dict[str, Any]:
    proof = proven_user_text(source_context)
    target, refusal = _target_contact(args.get("contact_id") or args.get("target") or "")
    if refusal is not None:
        return refusal
    assert target is not None
    notes_lines: list[str] = []
    new_name = _clean(args.get("name")) or None
    if new_name and new_name != target["display_name"] and not name_in_text(new_name, proof):
        notes_lines.append(f"Not renamed to '{new_name}': that name was not in your message.")
        new_name = None
    add_aliases = [_clean(a) for a in _as_list(args.get("add_aliases")) if _clean(a)]
    dropped = [a for a in add_aliases if not name_in_text(a, proof)]
    if dropped:
        notes_lines.append(f"Aliases not added (not in your message): {', '.join(dropped)}.")
    add_aliases = [a for a in add_aliases if a not in dropped]
    remove_aliases = [_clean(a) for a in _as_list(args.get("remove_aliases")) if _clean(a)]
    additions = _normalize(_requested_endpoints({**args, "endpoints": args.get("add_endpoints")}))
    proven_adds = [e for _raw, e in additions if value_in_text(e, proof)]
    unproven_adds = [e for _raw, e in additions if not value_in_text(e, proof)]
    current = {e["endpoint_id"]: e for e in target["endpoints"]}
    changes: list[dict[str, Any]] = []
    pending_changes: list[NormalizedEndpoint] = []
    replaces: dict[str, str] = {}
    for item in _as_list(args.get("change_endpoints")):
        if not isinstance(item, dict):
            continue
        endpoint_id = _clean(item.get("endpoint_id"))
        saved = current.get(endpoint_id)
        if saved is None:
            return _result(False, "endpoint_not_found", f"{target['display_name']} has no saved entry {endpoint_id or '(missing endpoint_id)'}.")
        merged = {"kind": saved["kind"], "value": item.get("value", saved["value"]), "label": item.get("label", saved["label"]),
                  "network": item.get("network", saved.get("chain_network", "")), "channel": item.get("channel", saved.get("channel", "")),
                  "provider_account": item.get("account", item.get("provider_account", saved.get("provider_account", "")))}
        try:
            normalized = normalize_endpoint(merged)
        except EndpointError as exc:
            return _result(False, exc.reason, exc.message)
        same = (normalized.canonical, normalized.chain_network, normalized.channel, normalized.provider_account.casefold()) == (
            saved["canonical"], saved.get("chain_network", ""), saved.get("channel", ""), str(saved.get("provider_account", "")).casefold())
        if same or value_in_text(normalized, proof):
            changes.append({"endpoint_id": endpoint_id, **{k: v for k, v in merged.items() if k != "kind"}})
        else:
            pending_changes.append(normalized)
            replaces[normalized.value] = endpoint_id
    removals: list[str] = []
    for endpoint_id in [_clean(x) for x in _as_list(args.get("remove_endpoint_ids")) if _clean(x)]:
        saved = current.get(endpoint_id)
        if saved is None:
            return _result(False, "endpoint_not_found", f"{target['display_name']} has no saved entry {endpoint_id}.")
        if saved["value"] in proof or saved["value"].casefold() in proof.casefold():
            removals.append(endpoint_id)
        else:
            notes_lines.append(f"Not removed: {_endpoint_line(saved)}. Removing a saved address needs the address in your message, or remove it in Home → Contacts.")
    suggestions = _record_suggestions(unproven_adds, contact_id=target["contact_id"], display_name=target["display_name"])
    suggestions += _record_suggestions(pending_changes, contact_id=target["contact_id"], display_name=target["display_name"], replaces=replaces)
    change: dict[str, Any] = {"operation": "update", "contact_id": target["contact_id"], "add_aliases": add_aliases, "remove_aliases": remove_aliases,
                              "add_endpoints": [_raw_for(e) for e in proven_adds], "change_endpoints": changes, "remove_endpoint_ids": removals}
    if new_name:
        change["display_name"] = new_name
    if args.get("notes") is not None:
        change["notes"] = args.get("notes")
    head = f"{target['display_name']} [{target['contact_id']}]: "
    outcome = {"status": "unchanged", "already_saved": []}
    if new_name or "notes" in change or add_aliases or remove_aliases or proven_adds or changes or removals:
        outcome = authority.propose(change, source=authority.SOURCE_USER_CHAT, requested_by="contacts.update")
    if outcome["status"] == "unchanged":
        lines = []
        if suggestions:
            lines.append("Waiting for your confirmation (not in your message): " + "; ".join(_suggestion_line(s) for s in suggestions)
                         + f". Confirm or dismiss it in {_CONFIRM_WHERE}.")
        lines.extend(notes_lines)
        brief = _contact_brief(target)
        return _result(bool(suggestions), "saved_as_suggestion" if suggestions else "unchanged", head + (" ".join(lines) if lines else "nothing changed."),
                       details={"contact": brief, "suggestions": suggestions}, observation={"contact": brief, "suggestions": [s["suggestion_id"] for s in suggestions]})
    return _pending_result(outcome, lines_before=[head.rstrip(": ") + ":"], suggestions=suggestions, notes=notes_lines, contact=target)


def _delete(args: dict[str, Any]) -> dict[str, Any]:
    target, refusal = _target_contact(args.get("target") or args.get("contact_id") or "")
    if refusal is not None:
        return refusal
    assert target is not None
    outcome = authority.propose({"operation": "delete", "contact_id": target["contact_id"]}, source=authority.SOURCE_CHAT_APPROVAL, requested_by="contacts.delete")
    return _pending_result(outcome, lines_before=[], suggestions=[],
                           notes=["Drafts, proposals and receipts that already recorded an address keep their own record."], contact=target)


def run(intent: str, arguments: dict[str, Any] | None, source_context: dict[str, Any] | None) -> dict[str, Any]:
    args = arguments if isinstance(arguments, dict) else {}
    try:
        if intent == "contacts.search":
            return _search(args)
        if intent == "contacts.resolve":
            return _resolve(args)
        if intent == "contacts.save":
            return _save(args, source_context)
        if intent == "contacts.update":
            return _update(args, source_context)
        if intent == "contacts.delete":
            return _delete(args)
    except store.ContactsError as exc:
        return _result(False, exc.reason, exc.message + (" Nothing was saved." if intent in ("contacts.save", "contacts.update") else ""),
                       details={"refusal": exc.as_dict()}, observation={"reason": exc.reason})
    return _result(False, "unsupported", f"Unknown contacts intent {intent}.")


__all__ = ["INTENTS", "name_in_text", "proven_user_text", "run", "value_in_text"]
