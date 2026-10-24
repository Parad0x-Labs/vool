"""Native service declarations use the same enabled package and own credential slots."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote


class ServiceRefused(RuntimeError):
    pass


def credential_values(plugin_id: str, declaration: dict, names) -> dict[str, str]:
    """Resolve only this invocation's declared environment names; never ambient keys."""
    from core.credential_store import get_credential
    from core.secret_redaction import register_exact_secret

    slots = {spec["env"]: slot for slot, spec in declaration.get("credentials", {}).items()}
    result = {}
    for name in names:
        if name not in slots:
            raise ServiceRefused("The plugin requested a key outside its declared slots.")
        value = get_credential(f"plugin.{plugin_id}.{slots[name]}")
        if not value:
            raise ServiceRefused("Connect your service key in Add-ons before using this plugin.")
        register_exact_secret(value)
        register_exact_secret(quote(value, safe=""))
        result[name] = value
    return result


def enabled(kind: str) -> list:
    from core import plugin_lifecycle
    from core.plugin_tools import load_manifest

    result = []
    for record in plugin_lifecycle.records():
        if plugin_lifecycle.is_available(record.plugin_id):
            plugin = load_manifest(Path(record.root) / ".codex-plugin/plugin.json")
            if kind in plugin.vool.get("kind", []):
                result.append(plugin)
    return result
