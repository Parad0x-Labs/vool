"""Synthetic manifests, catalog rows, and scripted transport responses.

Every endpoint hostname is under the reserved ``.invalid`` TLD (RFC 2606) or a
loopback port, so even a harness bug cannot reach a real service; the guard
transport (guard_transport.py) is the enforcement layer, this file only names
the destinations.
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------- manifest archetypes
# Keys are corpus-stable. `cost_class` in metadata is read by
# core.model_selection_policy.provider_cost_class (explicit wins); loopback
# base_urls also classify free_local independently of the explicit tag.

ALL_TEXT_CAPS = [
    "summarize",
    "classify",
    "format",
    "extract",
    "code_basic",
    "code_complex",
    "long_context",
    "structured_json",
    "tool_intent",
]


def _manifest(
    *,
    provider_name: str,
    model_name: str,
    base_url: str,
    capabilities: list[str],
    context_window: int,
    metadata: dict[str, Any] | None = None,
    enabled: bool = True,
    adapter_type: str = "openai_compatible",
    source_type: str = "http",
) -> dict[str, Any]:
    md = {"deployment_class": "local", "orchestration_role": "drone"}
    md.update(metadata or {})
    return {
        "provider_name": provider_name,
        "model_name": model_name,
        "source_type": source_type,
        "adapter_type": adapter_type,
        "license_name": "mit",
        "license_reference": "synthetic://license",
        "runtime_dependency": "synthetic-harness",
        "capabilities": capabilities,
        "runtime_config": {
            "base_url": base_url,
            "context_window": context_window,
            "supports_json_mode": True,
            "timeout_seconds": 8.0,
        },
        "metadata": md,
        "enabled": enabled,
    }


MANIFESTS: dict[str, dict[str, Any]] = {
    # local lane
    "local_small": _manifest(
        provider_name="synthetic-local",
        model_name="tiny-1b",
        base_url="http://127.0.0.1:18971",
        capabilities=["summarize", "classify", "format", "extract"],
        context_window=8192,
        metadata={"cost_class": "free_local", "bundle_role": "lightweight_utility"},
    ),
    "local_big": _manifest(
        provider_name="synthetic-local",
        model_name="workhorse-8b",
        base_url="http://127.0.0.1:18972",
        capabilities=ALL_TEXT_CAPS,
        context_window=32768,
        metadata={"cost_class": "free_local", "orchestration_role": "queen"},
    ),
    # local model WITHOUT structured_json/tool_intent (capability hard-exclusion probe)
    "local_nocap": _manifest(
        provider_name="synthetic-local",
        model_name="plain-0.5b",
        base_url="http://127.0.0.1:18973",
        capabilities=["classify"],
        context_window=4096,
        metadata={"cost_class": "free_local"},
    ),
    # vision/multimodal-only local model: name carries a non-text marker, so the
    # router's is_text_generation_ollama_model filter must keep it out of text turns
    "local_vision": _manifest(
        provider_name="synthetic-local",
        model_name="synth-llava-vision",
        base_url="http://127.0.0.1:18974",
        capabilities=["multimodal", "summarize"],
        context_window=8192,
        metadata={"cost_class": "free_local"},
    ),
    # small-context local model for the long-payload scenario
    "local_smallctx": _manifest(
        provider_name="synthetic-local",
        model_name="smallctx-2b",
        base_url="http://127.0.0.1:18975",
        capabilities=ALL_TEXT_CAPS,
        context_window=4096,
        metadata={"cost_class": "free_local"},
    ),
    "local_bigctx": _manifest(
        provider_name="synthetic-local",
        model_name="bigctx-14b",
        base_url="http://127.0.0.1:18976",
        capabilities=ALL_TEXT_CAPS,
        context_window=131072,
        metadata={"cost_class": "free_local", "orchestration_role": "queen"},
    ),
    # verified-free cloud lane: openrouter-byok + ":free" name + a catalog row
    # that prices it at zero (see CATALOG). cost_class stays paid_cloud so only
    # the catalog verification can admit it -- the real production semantics.
    "free_cloud_verified": _manifest(
        provider_name="openrouter-byok",
        model_name="synthetic/tiny-free:free",
        base_url="https://synthetic-free.openrouter.invalid",
        capabilities=ALL_TEXT_CAPS,
        context_window=65536,
        metadata={"cost_class": "paid_cloud", "deployment_class": "cloud", "orchestration_role": "queen"},
    ),
    # same lane but the catalog does NOT contain the model -> verified-free must
    # fail -> the manifest charges the user -> excluded without a reservation
    "free_cloud_stale": _manifest(
        provider_name="openrouter-byok",
        model_name="synthetic/gone-free:free",
        base_url="https://synthetic-stale.openrouter.invalid",
        capabilities=ALL_TEXT_CAPS,
        context_window=65536,
        metadata={"cost_class": "paid_cloud", "deployment_class": "cloud", "orchestration_role": "queen"},
    ),
    # paid cloud lane
    "paid_cloud": _manifest(
        provider_name="synthetic-paid",
        model_name="frontier-xl",
        base_url="https://synthetic-paid.invalid",
        capabilities=ALL_TEXT_CAPS,
        context_window=200000,
        metadata={"cost_class": "paid_cloud", "deployment_class": "cloud", "orchestration_role": "queen"},
    ),
    # custom remote endpoint with UNKNOWN cost class (no explicit cost_class,
    # non-loopback http base_url): provider_cost_class -> remote_unknown
    "remote_unknown": _manifest(
        provider_name="synthetic-custom",
        model_name="custom-endpoint",
        base_url="https://synthetic-custom.invalid",
        capabilities=ALL_TEXT_CAPS,
        context_window=32768,
        metadata={"deployment_class": "cloud"},
    ),
    # deliberately NOT in the transport allowlist: used by the guard control to
    # prove a live external host can never be silently dispatched
    "guard_probe_external": _manifest(
        provider_name="synthetic-guardprobe",
        model_name="external-probe",
        base_url="https://openrouter.ai/v1",
        capabilities=ALL_TEXT_CAPS,
        context_window=32768,
        metadata={"deployment_class": "cloud"},
    ),
    "disabled_local": _manifest(
        provider_name="synthetic-local",
        model_name="disabled-4b",
        base_url="http://127.0.0.1:18977",
        capabilities=ALL_TEXT_CAPS,
        context_window=8192,
        metadata={"cost_class": "free_local"},
        enabled=False,
    ),
}


def manifest_allowlist(keys: list[str]) -> list[str]:
    """Host prefixes the guard may see for the given manifest keys.

    Only synthetic destinations are ever allowlisted: hosts under the reserved
    ``.invalid`` TLD and loopback IPs. A manifest accidentally pointing at a
    REAL service (the guard control does this deliberately) therefore stays
    refused — the allowlist cannot itself open a live door.
    """
    prefixes = []
    for key in keys:
        base_url = str(MANIFESTS[key]["runtime_config"]["base_url"])
        if ".invalid" in base_url or base_url.startswith("http://127.0.0.1") or base_url.startswith("http://localhost"):
            prefixes.append(base_url)
    return prefixes


def manifest_hosts() -> list[str]:
    """Every synthetic manifest base_url (used to classify guard violations)."""
    return [str(spec["runtime_config"]["base_url"]) for spec in MANIFESTS.values()]


# ---------------------------------------------------------------- openrouter catalog
# Written into the disposable VOOL_HOME as data/openrouter_models_cache.json.

CATALOG_ROWS: dict[str, dict[str, Any]] = {
    # zero-priced, text-only output -> model_is_free is True via the ":free" tag
    "free:tiny-free": {
        "id": "synthetic/tiny-free:free",
        "name": "Synthetic Tiny (free)",
        "context_length": 65536,
        "pricing": {"prompt": "0", "completion": "0", "request": "0"},
        "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
        "supported_parameters": ["tools", "response_format"],
        "top_provider": {"max_completion_tokens": 4096},
    },
    # priced model row (used by comparison adapters later; harmless in scenarios)
    "priced:frontier": {
        "id": "synthetic/frontier-xl",
        "name": "Synthetic Frontier XL",
        "context_length": 200000,
        "pricing": {"prompt": "0.0000015", "completion": "0.000008", "request": "0"},
        "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
        "supported_parameters": ["tools", "response_format", "reasoning"],
        "top_provider": {"max_completion_tokens": 16384},
    },
}


def catalog_payload(row_keys: list[str]) -> dict[str, Any]:
    from datetime import datetime, timezone

    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "payload": {"data": [CATALOG_ROWS[key] for key in row_keys]},
    }


# ---------------------------------------------------------------- scripted responses
# Transport scripts are strings resolved by the guard into canned HTTP behavior.
# Format: kind[:payload]
#   ok_text:<text>            200, content=<text>
#   ok_json:<json>            200, content=<json string>
#   ok_toolcall:<name>:<args> 200, native message.tool_calls entry
#   empty                     200, content=""
#   timeout                   raise requests.exceptions.Timeout
#   http:<code>               raise_for_status -> HTTPError with that status
#   partial:<text>            200, content=<text>, finish_reason="length"


def resolve_script(script: str) -> dict[str, Any]:
    kind, _, rest = script.partition(":")
    return {"kind": kind, "payload": rest}


def response_payload_for(script: str, *, model_name: str) -> dict[str, Any]:
    """The ``json()`` body a scripted 200 response returns (openai-compatible)."""
    spec = resolve_script(script)
    message: dict[str, Any] = {"content": ""}
    finish = "stop"
    if spec["kind"] == "ok_text" or spec["kind"] == "ok_json":
        message["content"] = spec["payload"]
    elif spec["kind"] == "partial":
        message["content"] = spec["payload"]
        finish = "length"
    elif spec["kind"] == "ok_toolcall":
        name, _, args = spec["payload"].partition(":")
        message = {
            "content": "",
            "tool_calls": [
                {
                    "id": "call_synth_1",
                    "type": "function",
                    "function": {"name": name, "arguments": args},
                }
            ],
        }
    elif spec["kind"] == "empty":
        message["content"] = ""
    else:
        raise ValueError(f"script kind {spec['kind']!r} has no 200 body")
    return {
        "id": "chatcmpl_synth",
        "object": "chat.completion",
        "model": model_name,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 37, "completion_tokens": 11, "total_tokens": 48},
    }
