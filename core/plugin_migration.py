"""Retired service settings stay local; only explicit native-plugin enablement rebinds a key."""
from __future__ import annotations

from urllib.parse import urlsplit

SERVICES = (
    {"id": "vool-plugin-usepod", "name": "UsePod", "slot": "llm.cloud.usepod",
     "origin_slot": "llm.cloud.usepod_origin", "origin": "https://api.usepod.ai", "kind": "model_provider"},
    {"id": "vool-plugin-eyebrow", "name": "Eyebrow", "slot": "security.eyebrow",
     "origin_slot": "", "origin": "https://api.eyebrow.cc", "kind": "scanner"},
)


def retired_slots() -> tuple[str, ...]:
    return tuple(slot for service in SERVICES for slot in (service["slot"], service["origin_slot"]) if slot)


def notices() -> list[dict]:
    from core.credential_store import credential_is_indexed
    from core.plugin_lifecycle import STAGE_UNINSTALLED, record_for

    migrate_selections()
    fallback = selection_notice()
    result = [fallback] if fallback else []
    for service in SERVICES:
        if not any(credential_is_indexed(slot) for slot in (service["slot"], service["origin_slot"]) if slot):
            continue
        record = record_for(service["id"])
        installed = bool(record and record.stage != STAGE_UNINSTALLED)
        if installed and record.enabled and credential_is_indexed("plugin." + service["id"] + ".access"):
            continue
        message = (service["name"] + " now comes as a plugin. Install it to keep using it; your saved key will be reused."
                   if not installed else "Your saved " + service["name"] + " key is kept. Enable the plugin to reuse it, or connect your own service key.")
        result.append({"plugin_id": service["id"], "message": message,
                       "message_key": "plugin_migration_" + service["id"].removeprefix("vool-plugin-") + ("_installed" if installed else "")})
    return result


def bind_on_enable(plugin_id: str) -> bool:
    from core.credential_store import credential_is_indexed, get_credential, store_credential, strict_reads
    from core.plugin_services import declaration
    from core.secret_redaction import register_exact_secret

    service = next((service for service in SERVICES if service["id"] == plugin_id), None)
    if not service:
        return False
    _record, plugin = declaration(plugin_id, service["kind"])
    spec = plugin.vool[service["kind"]]
    own = f"plugin.{plugin_id}.{spec['credential_slot']}"
    if credential_is_indexed(own):
        return False
    if plugin.vool["kind"] != [service["kind"]] or plugin.vool.get("tools") or plugin.vool.get("mcpServers"):
        return False  # legacy service keys never reach downloaded executable code
    with strict_reads():
        origin = get_credential(service["origin_slot"]) if service["origin_slot"] and credential_is_indexed(service["origin_slot"]) else service["origin"]
        if not origin or spec["base_url"].rstrip("/") != origin.rstrip("/"):
            return False
        if plugin.vool["network"] != [urlsplit(origin).hostname]:
            return False
        value = get_credential(service["slot"]) if credential_is_indexed(service["slot"]) else None
    if not value:
        return False
    register_exact_secret(value)
    store_credential(own, value, label=service["name"] + " service key")
    return True


def migrate_selections() -> bool:
    """Move retired pins using actual v0.7 policy/chat rows, without probing any provider."""
    from core import cloud_escalation_policy as policy
    from core.cloud_providers import PROVIDERS
    from core.credential_store import credential_is_indexed

    with policy._COUNTER_LOCK, policy._counter_file_lock():
        raw = policy._read_raw()
        settings = raw.get("policy", {})
        chats = raw.get("chat_models", {})
        if not isinstance(settings, dict) or not isinstance(chats, dict):
            return False
        retired = settings.get("provider") == "usepod" or any(
            isinstance(row, dict) and row.get("provider") == "usepod" for row in chats.values())
        if not retired:
            return False
        # Only a provider/model the user already explicitly selected is eligible.
        # Merely finding a paid key never chooses or authorizes that provider.
        candidates = [settings, *chats.values()]
        fallback = next((row for row in candidates if isinstance(row, dict)
                         and row.get("provider") in PROVIDERS and row.get("model")
                         and credential_is_indexed(PROVIDERS[row["provider"]].credential_slot)), {})
        provider, model = str(fallback.get("provider", "")), str(fallback.get("model", ""))
        saved = raw.setdefault("plugin_migration", {})
        if settings.get("provider") == "usepod":
            saved.setdefault("previous_policy", dict(settings))
            settings.update(provider=provider, model=model, mode="off")
        previous = saved.setdefault("previous_chats", {})
        for session, row in chats.items():
            if isinstance(row, dict) and row.get("provider") == "usepod":
                previous.setdefault(session, dict(row))
                row.update(provider=provider, model=model, revision=int(row.get("revision", 0)) + 1)
        saved["fallback_provider"] = provider
        saved["fallback_model"] = model
        return policy._write_raw(raw)


def selection_notice() -> dict | None:
    from core import cloud_escalation_policy as policy

    saved = policy._read_raw().get("plugin_migration", {})
    if not saved:
        return None
    return {"plugin_id": "vool-plugin-usepod", "message_key": "plugin_migration_model_fallback",
            "message": "UsePod now comes as a plugin. Previous model choices were moved to a provider you had already selected, or cleared when none was configured. Automatic cloud escalation is off. Choose your model before sending another message."}
