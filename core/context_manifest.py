from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from core.prompt_assembly_report import normalize_context_score
from core.secret_redaction import redact_secrets
from network import signer

_SAFE_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,255}")
_SAFE_CODE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/,-]{0,159}")
_CANONICAL_HASHED_TOKEN_RE = re.compile(
    r"[A-Za-z][A-Za-z0-9_]{0,63}_sha256:[0-9a-f]{64}"
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b("
    r"authorization|proxy[-_ ]authorization|api[-_ ]?key|apikey|"
    r"access[-_ ]?token|refresh[-_ ]?token|auth[-_ ]?token|token|"
    r"password|passwd|secret|credential|client[-_ ]?secret|"
    r"callback[-_ ]?code|code|state"
    r")\b\s*[:=]\s*[^&,\r\n;]+"
)
_STRUCTURAL_REDACTION_MARKER = "context_manifest_allowlist_v1"

_ITEM_BOOLEAN_FIELDS = frozenset({"must_keep", "included"})
_ITEM_FLOAT_FIELDS = frozenset({"priority", "confidence"})
_ITEM_INTEGER_FIELDS = frozenset({"chars", "tokens"})
_ITEM_IDENTIFIER_FIELDS = frozenset({"item_id", "layer", "source_type"})
_METADATA_IDENTIFIER_FIELDS = frozenset(
    {
        "scope",
        "source_id",
        "content_hash",
        "status",
        "source_class",
        "receipt_id",
    }
)
_PROVENANCE_IDENTIFIER_FIELDS = frozenset(
    {"kind", "source_id", "content_hash"}
)


@dataclass(frozen=True)
class ContextManifest:
    """Signed context-assembly trace.

    This records loader selection. The exact provider-bound payload is recorded
    separately by ``provider_invocation_gateway`` after adapter preparation.
    """
    manifest_id: str
    task_id: str
    trace_id: str
    evidence_hashes: list[str]
    source_metadata: list[dict[str, Any]]
    redaction_markers: list[str]
    truncation_markers: list[str]
    signature: str
    chat_id: str = ""
    project_id: str = ""
    context_scopes: list[str] = field(default_factory=list)
    selected_items: list[dict[str, Any]] = field(default_factory=list)
    excluded_items: list[dict[str, Any]] = field(default_factory=list)
    capsule_version: str = "none"
    input_tokens: int = 0
    reserved_output_tokens: int = 0
    provider: str = ""
    model: str = ""
    access_policy: dict[str, Any] = field(default_factory=dict)
    candidate_items: list[dict[str, Any]] = field(default_factory=list)


def hash_evidence_item(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _redacted_text(value: Any) -> str:
    text = redact_secrets(str(value or ""))
    return _SENSITIVE_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}: [redacted]",
        text,
    ).strip()


def _safe_identifier(value: Any, *, prefix: str = "id") -> str:
    canonical_hash = str(value or "").strip()
    if _CANONICAL_HASHED_TOKEN_RE.fullmatch(canonical_hash):
        return canonical_hash
    text = _redacted_text(value)
    if not text:
        return ""
    if _SAFE_IDENTIFIER_RE.fullmatch(text) and not (
        "\\" in text
        or text.startswith("/")
        or re.match(r"^[A-Za-z]:/", text)
        or re.match(r"(?i)^file:", text)
    ):
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"{prefix}_sha256:{digest}"


def _safe_code(value: Any, *, prefix: str) -> str:
    canonical_hash = str(value or "").strip()
    if _CANONICAL_HASHED_TOKEN_RE.fullmatch(canonical_hash):
        return canonical_hash
    text = _redacted_text(value)
    if not text:
        return ""
    if _SAFE_CODE_RE.fullmatch(text):
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"{prefix}_sha256:{digest}"


def _safe_source_metadata(
    records: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    safe_records: list[dict[str, Any]] = []
    for raw in list(records or []):
        if not isinstance(raw, dict):
            continue
        metadata = dict(raw.get("metadata") or {})
        provenance = dict(raw.get("provenance") or {})
        safe: dict[str, Any] = {}
        for key in ("item_id", "source_id", "source_type", "content_hash"):
            value = (
                raw.get(key)
                or metadata.get(key)
                or provenance.get(key)
                or ""
            )
            cleaned = _safe_identifier(value, prefix=key)
            if cleaned:
                safe[key] = cleaned
        scope = _safe_code(
            raw.get("scope") or metadata.get("scope"),
            prefix="scope",
        )
        if scope:
            safe["scope"] = scope
        reason = _safe_code(raw.get("reason"), prefix="reason")
        if reason:
            safe["reason"] = reason
        kind = _safe_code(raw.get("kind"), prefix="kind")
        if kind:
            safe["kind"] = kind
        if isinstance(raw.get("count"), int) and not isinstance(
            raw.get("count"),
            bool,
        ):
            safe["count"] = max(0, int(raw["count"]))
        if safe:
            safe_records.append(safe)
    return safe_records


def _safe_metadata(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    safe: dict[str, Any] = {}
    for key in _METADATA_IDENTIFIER_FIELDS:
        cleaned = _safe_identifier(raw.get(key), prefix=key)
        if cleaned:
            safe[key] = cleaned
    return safe


def _safe_provenance(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    safe: dict[str, Any] = {}
    for key in _PROVENANCE_IDENTIFIER_FIELDS:
        cleaned = _safe_identifier(raw.get(key), prefix=key)
        if cleaned:
            safe[key] = cleaned
    return safe


def _safe_context_items(
    records: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    safe_records: list[dict[str, Any]] = []
    for raw in list(records or []):
        if not isinstance(raw, dict):
            continue
        safe: dict[str, Any] = {}
        for key in _ITEM_IDENTIFIER_FIELDS:
            cleaned = _safe_identifier(raw.get(key), prefix=key)
            if cleaned:
                safe[key] = cleaned
        reason = _safe_code(raw.get("reason"), prefix="reason")
        if reason:
            safe["reason"] = reason
        for key in _ITEM_BOOLEAN_FIELDS:
            if key in raw:
                safe[key] = bool(raw[key])
        for key in _ITEM_INTEGER_FIELDS:
            value = raw.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                safe[key] = max(0, int(value))
        for key in _ITEM_FLOAT_FIELDS:
            value = raw.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                safe[key] = round(normalize_context_score(value), 4)
        metadata = _safe_metadata(raw.get("metadata"))
        if metadata:
            safe["metadata"] = metadata
        provenance = _safe_provenance(raw.get("provenance"))
        if provenance:
            safe["provenance"] = provenance
        if safe:
            safe_records.append(safe)
    return safe_records


def _safe_markers(values: list[str] | None, *, prefix: str) -> list[str]:
    return [
        cleaned
        for value in list(values or [])
        if (cleaned := _safe_code(value, prefix=prefix))
    ]


def _safe_access_policy(raw: Any) -> dict[str, Any]:
    """Keep an auditable policy summary without persisting grants or caller data."""
    if not isinstance(raw, dict):
        return {}
    safe: dict[str, Any] = {}
    for key in ("chat_id", "project_id"):
        cleaned = _safe_identifier(raw.get(key), prefix=key)
        if cleaned:
            safe[key] = cleaned
    state = _safe_code(raw.get("namespace_state"), prefix="namespace_state")
    if state:
        safe["namespace_state"] = state
    for key in (
        "project_context_allowed",
        "profile_context_allowed",
        "action_receipts_allowed",
        "shared_context_allowed",
        "cold_context_allowed",
    ):
        if key in raw:
            safe[key] = bool(raw[key])
    for key in (
        "grant_count",
        "cross_chat_import_count",
        "project_import_count",
    ):
        value = raw.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            safe[key] = max(0, value)
    return safe


def _sanitized_manifest_fields(
    *,
    task_id: str,
    trace_id: str,
    source_metadata: list[dict[str, Any]],
    redaction_markers: list[str],
    truncation_markers: list[str],
    chat_id: str,
    project_id: str,
    context_scopes: list[str],
    selected_items: list[dict[str, Any]],
    excluded_items: list[dict[str, Any]],
    capsule_version: str,
    input_tokens: int,
    reserved_output_tokens: int,
    provider: str,
    model: str,
    access_policy: dict[str, Any],
    candidate_items: list[dict[str, Any]],
) -> dict[str, Any]:
    markers = _safe_markers(
        redaction_markers,
        prefix="redaction",
    )
    if _STRUCTURAL_REDACTION_MARKER not in markers:
        markers.append(_STRUCTURAL_REDACTION_MARKER)
    return {
        "task_id": _safe_identifier(task_id, prefix="task"),
        "trace_id": _safe_identifier(trace_id, prefix="trace"),
        "source_metadata": _safe_source_metadata(source_metadata),
        "redaction_markers": markers,
        "truncation_markers": _safe_markers(
            truncation_markers,
            prefix="truncation",
        ),
        "chat_id": _safe_identifier(chat_id, prefix="chat"),
        "project_id": _safe_identifier(project_id, prefix="project"),
        "context_scopes": sorted(
            {
                cleaned
                for scope in list(context_scopes or [])
                if (cleaned := _safe_code(scope, prefix="scope"))
            }
        ),
        "selected_items": _safe_context_items(selected_items),
        "excluded_items": _safe_context_items(excluded_items),
        "capsule_version": _safe_identifier(
            capsule_version or "none",
            prefix="capsule",
        ),
        "input_tokens": max(0, int(input_tokens or 0)),
        "reserved_output_tokens": max(
            0,
            int(reserved_output_tokens or 0),
        ),
        "provider": _safe_identifier(provider, prefix="provider"),
        "model": _safe_identifier(model, prefix="model"),
        "access_policy": _safe_access_policy(access_policy),
        "candidate_items": _safe_context_items(candidate_items),
    }


def context_manifest_storage_dict(
    manifest: ContextManifest,
) -> dict[str, Any]:
    """Return a persistence-safe manifest or fail closed.

    ``build_context_manifest`` creates this canonical shape. The storage
    boundary repeats the check so a hand-constructed dataclass cannot bypass
    the structural allowlist.
    """
    safe = _sanitized_manifest_fields(
        task_id=manifest.task_id,
        trace_id=manifest.trace_id,
        source_metadata=manifest.source_metadata,
        redaction_markers=manifest.redaction_markers,
        truncation_markers=manifest.truncation_markers,
        chat_id=manifest.chat_id,
        project_id=manifest.project_id,
        context_scopes=manifest.context_scopes,
        selected_items=manifest.selected_items,
        excluded_items=manifest.excluded_items,
        capsule_version=manifest.capsule_version,
        input_tokens=manifest.input_tokens,
        reserved_output_tokens=manifest.reserved_output_tokens,
        provider=manifest.provider,
        model=manifest.model,
        access_policy=manifest.access_policy,
        candidate_items=manifest.candidate_items,
    )
    for key, value in safe.items():
        if getattr(manifest, key) != value:
            raise ValueError(
                "context manifest contains non-canonical persistence data"
            )
    try:
        uuid.UUID(str(manifest.manifest_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("context manifest id is invalid") from exc
    if not all(
        isinstance(value, str) and _SHA256_RE.fullmatch(value)
        for value in manifest.evidence_hashes
    ):
        raise ValueError("context manifest evidence hashes are invalid")
    try:
        signature = base64.b64decode(
            str(manifest.signature or ""),
            validate=True,
        )
    except (ValueError, TypeError) as exc:
        raise ValueError("context manifest signature is invalid") from exc
    if len(signature) != 64:
        raise ValueError("context manifest signature is invalid")
    payload = {
        "manifest_id": manifest.manifest_id,
        "task_id": manifest.task_id,
        "trace_id": manifest.trace_id,
        "evidence_hashes": list(manifest.evidence_hashes),
        "source_metadata": list(manifest.source_metadata),
        "redaction_markers": list(manifest.redaction_markers),
        "truncation_markers": list(manifest.truncation_markers),
        "signature": manifest.signature,
        "chat_id": manifest.chat_id,
        "project_id": manifest.project_id,
        "context_scopes": list(manifest.context_scopes),
        "selected_items": list(manifest.selected_items),
        "excluded_items": list(manifest.excluded_items),
        "capsule_version": manifest.capsule_version,
        "input_tokens": manifest.input_tokens,
        "reserved_output_tokens": manifest.reserved_output_tokens,
        "provider": manifest.provider,
        "model": manifest.model,
        "access_policy": dict(manifest.access_policy),
        "candidate_items": list(manifest.candidate_items),
    }
    signed_payload = {
        key: value
        for key, value in payload.items()
        if key != "signature"
    }
    if not signer.verify(
        json.dumps(
            signed_payload,
            sort_keys=True,
        ).encode("utf-8"),
        manifest.signature,
        signer.get_local_peer_id(),
    ):
        raise ValueError("context manifest signature verification failed")
    return payload


def build_context_manifest(
    *,
    task_id: str,
    trace_id: str,
    evidence_items: list[Any],
    source_metadata: list[dict[str, Any]],
    redaction_markers: list[str] | None = None,
    truncation_markers: list[str] | None = None,
    chat_id: str = "",
    project_id: str = "",
    context_scopes: list[str] | None = None,
    selected_items: list[dict[str, Any]] | None = None,
    excluded_items: list[dict[str, Any]] | None = None,
    capsule_version: str = "none",
    input_tokens: int = 0,
    reserved_output_tokens: int = 0,
    provider: str = "",
    model: str = "",
    access_policy: dict[str, Any] | None = None,
    candidate_items: list[dict[str, Any]] | None = None,
) -> ContextManifest:
    manifest_id = str(uuid.uuid4())
    safe = _sanitized_manifest_fields(
        task_id=task_id,
        trace_id=trace_id,
        source_metadata=source_metadata,
        redaction_markers=list(redaction_markers or []),
        truncation_markers=list(truncation_markers or []),
        chat_id=chat_id,
        project_id=project_id,
        context_scopes=list(context_scopes or []),
        selected_items=list(selected_items or []),
        excluded_items=list(excluded_items or []),
        capsule_version=capsule_version,
        input_tokens=input_tokens,
        reserved_output_tokens=reserved_output_tokens,
        provider=provider,
        model=model,
        access_policy=dict(access_policy or {}),
        candidate_items=list(candidate_items or []),
    )
    payload = {
        "manifest_id": manifest_id,
        "task_id": safe["task_id"],
        "trace_id": safe["trace_id"],
        "evidence_hashes": [hash_evidence_item(item) for item in evidence_items],
        "source_metadata": safe["source_metadata"],
        "redaction_markers": safe["redaction_markers"],
        "truncation_markers": safe["truncation_markers"],
        "chat_id": safe["chat_id"],
        "project_id": safe["project_id"],
        "context_scopes": safe["context_scopes"],
        "selected_items": safe["selected_items"],
        "excluded_items": safe["excluded_items"],
        "capsule_version": safe["capsule_version"],
        "input_tokens": safe["input_tokens"],
        "reserved_output_tokens": safe["reserved_output_tokens"],
        "provider": safe["provider"],
        "model": safe["model"],
        "access_policy": safe["access_policy"],
        "candidate_items": safe["candidate_items"],
    }
    signature = signer.sign(json.dumps(payload, sort_keys=True).encode("utf-8"))
    return ContextManifest(
        manifest_id=manifest_id,
        task_id=payload["task_id"],
        trace_id=payload["trace_id"],
        evidence_hashes=payload["evidence_hashes"],
        source_metadata=payload["source_metadata"],
        redaction_markers=payload["redaction_markers"],
        truncation_markers=payload["truncation_markers"],
        signature=signature,
        chat_id=payload["chat_id"],
        project_id=payload["project_id"],
        context_scopes=payload["context_scopes"],
        selected_items=payload["selected_items"],
        excluded_items=payload["excluded_items"],
        capsule_version=payload["capsule_version"],
        input_tokens=payload["input_tokens"],
        reserved_output_tokens=payload["reserved_output_tokens"],
        provider=payload["provider"],
        model=payload["model"],
        access_policy=payload["access_policy"],
        candidate_items=payload["candidate_items"],
    )


__all__ = [
    "ContextManifest",
    "build_context_manifest",
    "context_manifest_storage_dict",
    "hash_evidence_item",
]
