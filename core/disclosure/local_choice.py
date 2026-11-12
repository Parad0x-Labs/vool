"""Explicit local retry through ordinary chat ingress; no routing or spend grants."""
from __future__ import annotations

import base64
import copy
import threading
import time
import uuid
from typing import Any

_LOCK = threading.RLock()
_RETRIES: dict[str, tuple[float, str, str | None, dict[str, Any]]] = {}


def local_models(*, images: bool = False) -> list[dict[str, str]]:
    from core.auto_local_only_mode import endpoint_is_local
    from core.chat_attachments import manifest_supports_images
    from core.local_model_tool_certification import certification_status
    from core.model_registry import ModelRegistry
    from core.model_selection_policy import provider_cost_class
    from storage.model_tool_certification_store import latest_certification_run

    result = []
    for manifest in ModelRegistry().list_manifests(enabled_only=True):
        config = manifest.runtime_config
        base = str(config.get("base_url") or "").rstrip("/")
        if (not base or not endpoint_is_local(base) or provider_cost_class(manifest) != "free_local"
                or manifest.metadata.get("deployment_class") == "cloud"
                or manifest.adapter_type not in {"openai_compatible", "local_qwen_provider"}
                or (images and manifest_supports_images(manifest) is not True)):
            continue
        # Never discover or probe an unrelated, uncertified default endpoint.
        saved = latest_certification_run(provider_name=manifest.provider_name, model_name=manifest.model_name)
        if not saved or saved.get("state") != "verified":
            continue
        try:
            if certification_status(manifest).get("state") != "verified":
                continue
            from core import provider_http
            inventory_path = str(config.get("health_path") or "/v1/models")
            if not inventory_path.startswith("/") or inventory_path.startswith("//"):
                continue
            response = provider_http.get(base + inventory_path, timeout=3)
            response.raise_for_status()
            inventory = response.json()
            entries = inventory.get("models", inventory.get("data", []))
            names = {str(row.get("id") or row.get("name") or row.get("model") or "")
                     for row in entries if isinstance(row, dict)}
            if manifest.model_name not in names:
                continue
        except Exception:
            continue
        result.append({"id": manifest.model_name, "provider": manifest.provider_name})
    return result


def choices(preview_id: str) -> list[dict[str, str]]:
    from core.disclosure.wire import get_preview

    item = get_preview(preview_id)
    if item.decision is not None or not item.local_context:
        return []
    images = bool(item.local_context.get("has_images")) or any(a.get("data_url") or a.get("images") for a in item.local_context.get("attachments", []))
    return local_models(images=images)


def prepare_retry(preview_id: str, digest: str, model: str) -> dict[str, Any]:
    from core import chat_attachments
    from core.disclosure.wire import _home, decide, get_preview

    item = get_preview(preview_id)
    if item.digest != digest or item.decision is not None or not item.local_context:
        raise ValueError("This request changed. Review it again.")
    if model not in {m["id"] for m in choices(preview_id)}:
        raise ValueError("That local model is not available for this request.")
    source = copy.deepcopy(item.local_context)
    records = []
    try:
        if any(not (entry.get("data_url") or entry.get("text") or entry.get("images")) for entry in source.get("attachments", [])):
            prepared = chat_attachments.model_attachments_from_source_context(
                source.get("attachment_context") or {}, question=source["question"])
            by_id = {entry.get("attachment_id") or entry.get("id"): entry for entry in prepared}
            source["attachments"] = [by_id.get(entry.get("attachment_id") or entry.get("id"), entry)
                                     for entry in source["attachments"]]
        # Re-stage only the already-prepared representations, through the existing
        # attachment authority. Its normal forget/delete/expiry laws own these bytes.
        for n, entry in enumerate(source.get("attachments", [])):
            name = str(entry.get("filename") or entry.get("name") or f"document-{n + 1}")
            if entry.get("data_url"):
                mime, encoded = entry["data_url"].split(";base64,", 1)
                media = mime.removeprefix("data:")
                suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(media)
                if suffix is None:
                    raise ValueError("This image cannot be retried locally here.")
                record = chat_attachments.stage_attachment(session_id=item.chat_id,
                    declared_name=f"prepared-{n + 1}{suffix}", declared_type=media,
                    data=base64.b64decode(encoded, validate=True))
            elif isinstance(entry.get("text"), str) and entry["text"].strip():
                record = chat_attachments.stage_document(session_id=item.chat_id,
                    declared_name=f"prepared-{name}.txt", data=entry["text"].encode("utf-8"))
            else:
                raise ValueError("The prepared source is unavailable. Choose a model in the composer and attach it again.")
            records.append(record)
        get_preview(preview_id)  # re-read the canonical erasure fence after staging
        retry = {"session_id": item.chat_id, "project_id": item.project_id,
                 "model": model, "question": source["question"], "workspace": source["workspace"],
                 "attachments": [r["id"] for r in records]}
        token = uuid.uuid4().hex
        with _LOCK:
            now = time.monotonic()
            for key, value in list(_RETRIES.items()):
                if now - value[0] > 300:
                    del _RETRIES[key]
            if len(_RETRIES) >= 128:
                raise ValueError("Close an open local retry and try again.")
            _RETRIES[token] = (now, _home(), item.scope_version, retry)
        decide(preview_id, digest, "local")
        # Display shape comes from the actual canonical staged records. It is
        # advisory; the capability remains bound to the original ID list.
        fields = ("id", "name", "kind", "size_bytes", "media_type", "document", "chars", "lines", "sha256")
        return {"token": token, **retry,
                "attachment_meta": [{key: record[key] for key in fields if key in record} for record in records]}
    except Exception:
        for record in records:
            chat_attachments.erase_document(session_id=item.chat_id, attachment_id=record["id"])
        raise


def consume_retry(token: str, *, session_id: str, project_id: str, model: str,
                  question: str, workspace: str, attachments: Any) -> None:
    from core.disclosure.scope import current_version
    from core.disclosure.wire import _home

    with _LOCK:
        entry = _RETRIES.get(token)
        if entry is None:
            raise ValueError("This local retry expired. Attach the source again.")
        created, home, version, retry = entry
        expected = {"session_id": session_id, "project_id": project_id, "model": model,
                    "question": question, "workspace": workspace, "attachments": attachments or []}
        if time.monotonic() - created > 300 or home != _home() or retry != expected:
            raise ValueError("This local retry changed or expired. Nothing was sent.")
    if current_version(session_id, project_id) != version:
        with _LOCK:
            _RETRIES.pop(token, None)
        raise ValueError("This chat's privacy state changed. Nothing was sent.")
    with _LOCK:
        if _RETRIES.get(token) is not entry:
            raise ValueError("This local retry expired. Nothing was sent.")
        del _RETRIES[token]
