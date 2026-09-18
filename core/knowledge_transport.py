from __future__ import annotations

from typing import Any

from core.knowledge_registry import load_transportable_shard_payload, local_manifest, summary_digest_for_text
from core.shard_synthesizer import shard_signable_bytes
from network.signer import verify


def build_transport_shard_response(*, query_id: str, shard_id: str) -> dict[str, Any] | None:
    shard = load_transportable_shard_payload(shard_id)
    manifest = local_manifest(shard_id)
    if not shard or not manifest:
        return None
    return {
        "query_id": str(query_id or "").strip(),
        "manifest_id": str(manifest.get("manifest_id") or "").strip(),
        "content_hash": str(manifest.get("content_hash") or "").strip(),
        "version": int(manifest.get("version") or 1),
        "summary_digest": str(manifest.get("summary_digest") or summary_digest_for_text(str(shard.get("summary") or ""))).strip(),
        "shard": dict(shard),
    }


def validate_incoming_transport_shard(payload: dict[str, Any]) -> dict[str, Any]:
    shard = dict(payload.get("shard") or {})
    shard_id = str(shard.get("shard_id") or "").strip()
    manifest_id = str(payload.get("manifest_id") or "").strip()
    content_hash = str(payload.get("content_hash") or "").strip()
    raw_version = payload.get("version")
    summary_digest = str(payload.get("summary_digest") or "").strip()
    source_node_id = str(shard.get("source_node_id") or "").strip()
    signature = str(shard.get("signature") or "").strip()

    errors: list[str] = []
    if not shard_id:
        errors.append("missing_shard_id")
    if not source_node_id:
        errors.append("missing_source_node_id")
    if not signature:
        errors.append("missing_signature")
    expected_summary_digest = summary_digest_for_text(str(shard.get("summary") or ""))
    if not summary_digest:
        errors.append("missing_summary_digest")
    elif summary_digest != expected_summary_digest:
        errors.append("summary_digest_mismatch")

    signature_verified = False
    if not errors:
        signature_verified = verify(shard_signable_bytes(shard), signature, source_node_id)
        if not signature_verified:
            errors.append("invalid_signature")

    manifest = local_manifest(shard_id) if shard_id else None
    manifest_bound = False
    if manifest:
        manifest_bound = True
        if manifest_id and manifest_id != str(manifest.get("manifest_id") or "").strip():
            errors.append("manifest_id_mismatch")
        if content_hash and content_hash != str(manifest.get("content_hash") or "").strip():
            errors.append("content_hash_mismatch")
        if raw_version not in {None, ""} and int(raw_version) != int(manifest.get("version") or 1):
            errors.append("version_mismatch")
        manifest_summary_digest = str(manifest.get("summary_digest") or "").strip()
        if manifest_summary_digest and summary_digest and summary_digest != manifest_summary_digest:
            errors.append("manifest_summary_digest_mismatch")

    accepted = not errors and signature_verified
    if accepted and manifest_bound:
        validation_state = "signature_and_manifest_verified"
    elif accepted:
        validation_state = "signature_verified"
    else:
        validation_state = errors[0] if errors else "validation_failed"

    citation = {
        "kind": "remote_shard",
        "shard_id": shard_id,
        "source_node_id": source_node_id,
        "manifest_id": manifest_id or None,
        "content_hash": content_hash or None,
        "version": None if raw_version in {None, ""} else int(raw_version),
        "summary_digest": summary_digest or None,
        "validation_state": validation_state,
    }
    return {
        "accepted": accepted,
        "validation_state": validation_state,
        "signature_verified": signature_verified,
        "manifest_bound": manifest_bound,
        "citation": citation,
        "details": {
            "errors": list(errors),
            "manifest_bound": manifest_bound,
            "expected_summary_digest": expected_summary_digest,
        },
    }
