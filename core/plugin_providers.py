"""Provider plugins project data into the existing model registry, never a second router."""
from __future__ import annotations

import contextlib
import json
import math
import os
import tempfile
import time

from core.plugin_services import ServiceRefused, declaration, native_request, safe_data


def _rows(payload) -> list[dict]:
    rows = payload.get("data", payload.get("models")) if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or len(rows) > 2048:
        raise ServiceRefused("The service did not return a bounded model list. Nothing was registered.")
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise ServiceRefused("The service model list contains an invalid entry.")
        identity = row.get("id")
        if not isinstance(identity, str) or not identity.strip() or len(identity) > 256 or any(ord(c) < 32 for c in identity):
            raise ServiceRefused("The service model list contains an invalid model identity.")
        pricing = row.get("pricing", {})
        public_prices = {}
        if isinstance(pricing, dict):
            for name in ("prompt", "completion", "request"):
                try:
                    value = float(pricing[name])
                except (KeyError, ValueError, TypeError, OverflowError):
                    continue
                if math.isfinite(value) and 0 <= value <= 1000:
                    public_prices[name] = str(pricing[name])[:64]
        context = row.get("context_length", 8192)
        if not isinstance(context, int) or isinstance(context, bool) or not 256 <= context <= 10000000:
            context = 8192
        parameters = row.get("supported_parameters", [])
        if (not isinstance(parameters, list) or len(parameters) > 64
                or any(not isinstance(item, str) or not item or len(item) > 64
                       or not all(c.isascii() and (c.isalnum() or c == "_") for c in item) for item in parameters)):
            raise ServiceRefused("The service model list contains an invalid entry.")
        result.append({"id": identity, "name": str(row.get("name") or identity)[:256],
                       "reported_pricing": public_prices, "context_length": context, "supported_parameters": parameters})
    if not result or len({row["id"] for row in result}) != len(result):
        raise ServiceRefused("The service model list is empty or contains duplicate identities.")
    return safe_data(result)


def refresh_models(plugin_id: str) -> dict:
    from core.model_registry import ModelRegistry
    from core.runtime_paths import data_path
    from storage.model_provider_manifest import ModelProviderManifest

    record, plugin = declaration(plugin_id, "model_provider")
    spec = plugin.vool["model_provider"]
    if money_governed(spec["base_url"]):
        raise ServiceRefused("This service is paid from a balance VOOL spends only with your wallet approvals, "
                             "so its models are not added as a plain provider. Your saved key is used through those approvals.")
    response = native_request(plugin_id, "model_provider", "GET", spec["base_url"].rstrip("/") + spec["model_list_path"],
                              expected_approval=record.approval_digest)
    try:
        rows = _rows(response.json())
    except (ValueError, TypeError):
        raise ServiceRefused("The service returned an unreadable model list. Nothing was registered.") from None
    finally:
        response.close()
    if spec["pricing_source"] != "models":
        response = native_request(plugin_id, "model_provider", "GET", spec["pricing_source"],
                                  expected_approval=record.approval_digest, _public_pricing=True)
        try:
            prices = {row["id"]: row["reported_pricing"] for row in _rows(response.json())}
        finally:
            response.close()
        for row in rows:
            row["reported_pricing"] = prices.get(row["id"], {})
    declaration(plugin_id, "model_provider", expected_approval=record.approval_digest)
    registry = ModelRegistry()
    registered = []
    for row in rows:
        manifest = ModelProviderManifest(
            provider_name="plugin-" + plugin_id, model_name=row["id"], source_type="http", adapter_type="openai_compatible",
            license_name="Provider", license_reference=plugin.vool["homepage"], weight_location="external",
            redistribution_allowed=False, runtime_dependency="remote-openai-compatible-provider",
            notes=plugin.vool["name"] + ". Connected through the installed plugin.",
            capabilities=["summarize", "classify", "format", "extract", "code_basic", "code_complex", "structured_json", "tool_intent"],
            runtime_config={"base_url": spec["base_url"], "api_path": spec.get("api_path", "/v1/chat/completions"),
                            "health_path": spec["model_list_path"], "plugin_id": plugin_id,
                            "plugin_approval": record.approval_digest, "timeout_seconds": 90, "supports_json_mode": True},
            metadata={"chat_selection_only": True, "runtime_family": "openai-compatible", "deployment_class": "remote",
                      "cost_class": "paid_cloud", "context_window": row["context_length"],
                      "supported_parameters": row["supported_parameters"],
                      "plugin_reported_pricing": row["reported_pricing"], "pricing_authority": "service_reported_not_a_charge_receipt",
                      "plugin_id": plugin_id, "confidence_baseline": 0.7, "max_safe_concurrency": 1}, enabled=True,
        )
        registry.register_manifest(manifest)
        registered.append(manifest.provider_id)
    folder = data_path("plugin-models")
    folder.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=folder, prefix=".models-")
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump({"approval_digest": record.approval_digest, "models": rows, "updated": time.time()}, handle)
        os.chmod(name, 0o600)
        os.replace(name, folder / (plugin_id + ".json"))
    finally:
        with contextlib.suppress(OSError):
            os.unlink(name)
    return {"ok": True, "models": rows, "provider_ids": registered,
            "message": "Model list refreshed. Published prices are service reports; the normal spending checks still apply."}


def filter_manifests(manifests):
    # One package check per plugin per listing: a provider can list up to 2048 models.
    checked: dict = {}

    def usable(key, check):
        if key not in checked:
            try:
                checked[key] = check()
            except (ServiceRefused, ValueError, OSError):
                checked[key] = None
        return checked[key]

    result = []
    for manifest in manifests:
        if manifest.adapter_type == "usepod" and usable(("x402",), lambda: require_legacy_x402() or True) is None:
            continue
        plugin_id = manifest.runtime_config.get("plugin_id")
        if plugin_id:
            approval = manifest.runtime_config.get("plugin_approval", "")
            ids = usable(("plugin", plugin_id, approval),
                         lambda plugin_id=plugin_id, approval=approval: _plain_lane_models(plugin_id, approval))
            if not ids or manifest.model_name not in ids:
                continue
        result.append(manifest)
    return result


def _plain_lane_models(plugin_id: str, approval: str) -> frozenset[str]:
    declaration(plugin_id, "model_provider", expected_approval=approval)
    return frozenset(row["id"] for row in cached_models(plugin_id))


def money_governed(base_url: str) -> bool:
    """A service whose prepaid balance VOOL already spends under its money law.

    Its token pays from a balance the owner funded through VOOL's wallet, so it
    never becomes a plain provider lane: spending it stays on the retained lane
    with spend grants, caps and receipts.
    """
    from urllib.parse import urlsplit

    from core.usepod.legacy_config import CONFIG
    from core.usepod.legacy_config import origin as legacy_origin

    host = (urlsplit(str(base_url or "")).hostname or "").rstrip(".").lower()
    governed = {(urlsplit(url).hostname or "").rstrip(".").lower() for url in (CONFIG.base_url, legacy_origin()) if url}
    return bool(host) and host in governed


def cached_models(plugin_id: str) -> list[dict]:
    from core.runtime_paths import data_path

    try:
        record, plugin = declaration(plugin_id, "model_provider")
        if money_governed(plugin.vool["model_provider"]["base_url"]):
            return []
        cache = json.loads((data_path("plugin-models") / (plugin_id + ".json")).read_text())
        return cache["models"] if cache["approval_digest"] == record.approval_digest else []
    except (OSError, ValueError, KeyError, ServiceRefused):
        return []


def is_provider_id(value: str) -> bool:
    """Persist explicit plugin pins even while unavailable; never silently substitute a key."""
    import re

    return bool(re.fullmatch(r"plugin-[a-z0-9][a-z0-9-]{0,95}", str(value or "")))


def provider_rows() -> list[dict]:
    from core.plugin_services import enabled

    rows = []
    for plugin in enabled("model_provider"):
        spec = plugin.vool["model_provider"]
        rows.append({"id": "plugin-" + plugin.plugin_id, "plugin_id": plugin.plugin_id,
                     "label": plugin.vool["name"], "model_id_style": "bare", "user_base_url": False,
                     "auth_placement": spec["auth_placement"], "key_from_environment": False})
    return rows


def usable_provider(provider: str, model: str = "") -> bool:
    if not is_provider_id(provider):
        return False
    rows = cached_models(provider.removeprefix("plugin-"))
    return any(row["id"] == model for row in rows) if model else bool(rows)


def require_legacy_x402():
    """Gate the retained monetary lane; a declaration grants no caps, approval or signing."""
    from core.usepod.legacy_config import CONFIG
    from core.usepod.legacy_config import origin as legacy_origin

    record, plugin = declaration("vool-plugin-usepod", "model_provider")
    spec = plugin.vool["model_provider"]
    saved_origin = legacy_origin().rstrip("/")
    if (spec.get("x402") is not True
            or spec["base_url"].rstrip("/") not in {CONFIG.base_url.rstrip("/"), saved_origin}):
        raise ServiceRefused("Enable the matching provider plugin with x402 support before using this payment lane.")
    return record, plugin
