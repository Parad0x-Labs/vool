from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from core.meet_and_greet_models import (
    KnowledgeChallengeIssueRequest,
    KnowledgeChallengeRecord,
    KnowledgeChallengeResponse,
    KnowledgeChallengeResponseRequest,
    KnowledgeChallengeVerifyRequest,
)
from network.signer import get_local_peer_id
from storage.chunk_store import hash_bytes, load_chunk
from storage.knowledge_manifests import manifest_for_shard
from storage.knowledge_possession_store import get_challenge, insert_challenge, update_challenge_status
from storage.manifest_store import load_manifest
from storage.replica_table import holders_for_shard


def issue_knowledge_possession_challenge(
    request: KnowledgeChallengeIssueRequest,
    *,
    ttl_seconds: int = 300,
) -> KnowledgeChallengeRecord:
    manifest = manifest_for_shard(request.shard_id)
    if not manifest:
        raise ValueError("Unknown shard for possession challenge.")
    holder = _holder_row(request.shard_id, request.holder_peer_id)
    if not holder:
        raise ValueError("Requested holder is not currently active for this shard.")
    metadata = dict(manifest.get("metadata") or {})
    chunk_hashes = list(metadata.get("cas_chunk_hashes") or [])
    cas_manifest_id = str(metadata.get("cas_manifest_id") or "")
    if not chunk_hashes or not cas_manifest_id:
        raise ValueError("Shard does not currently expose proof-capable CAS chunk metadata.")
    nonce = uuid.uuid4().hex
    chunk_index = int(hashlib.sha256(f"{request.shard_id}:{nonce}".encode()).hexdigest(), 16) % len(chunk_hashes)
    created_at = datetime.now(timezone.utc)
    record = KnowledgeChallengeRecord(
        challenge_id=str(uuid.uuid4()),
        shard_id=request.shard_id,
        holder_peer_id=request.holder_peer_id,
        requester_peer_id=request.requester_peer_id,
        content_hash=str(manifest["content_hash"]),
        manifest_id=cas_manifest_id,
        chunk_index=chunk_index,
        expected_chunk_hash=str(chunk_hashes[chunk_index]),
        nonce=nonce,
        status="issued",
        created_at=created_at.isoformat(),
        expires_at=(created_at + timedelta(seconds=max(30, ttl_seconds))).isoformat(),
        verification_note=None,
    )
    insert_challenge(record.model_dump(mode="json"))
    return record


def respond_to_knowledge_possession_challenge(
    request: KnowledgeChallengeResponseRequest,
) -> KnowledgeChallengeResponse:
    if request.holder_peer_id != get_local_peer_id():
        raise ValueError("This node cannot answer a possession challenge for another holder peer id.")
    manifest = manifest_for_shard(request.shard_id)
    if not manifest:
        raise ValueError("Unknown shard for challenge response.")
    metadata = dict(manifest.get("metadata") or {})
    cas_manifest_id = str(metadata.get("cas_manifest_id") or "")
    if not cas_manifest_id:
        raise ValueError("Shard does not expose CAS manifest data for possession proof.")
    cas_manifest = load_manifest(cas_manifest_id)
    if not cas_manifest:
        raise ValueError("Local CAS manifest missing for challenge response.")
    chunk_hashes = list(cas_manifest.get("chunk_hashes") or [])
    if request.chunk_index >= len(chunk_hashes):
        raise ValueError("Challenge chunk index is out of range.")
    chunk_hash = str(chunk_hashes[request.chunk_index])
    chunk = load_chunk(chunk_hash)
    if chunk is None:
        raise ValueError("Local CAS chunk missing for challenge response.")
    return KnowledgeChallengeResponse(
        challenge_id=request.challenge_id,
        shard_id=request.shard_id,
        holder_peer_id=request.holder_peer_id,
        requester_peer_id=request.requester_peer_id,
        chunk_index=request.chunk_index,
        chunk_hash=chunk_hash,
        chunk_b64=base64.b64encode(chunk).decode("utf-8"),
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def verify_knowledge_possession_response(
    request: KnowledgeChallengeVerifyRequest,
) -> KnowledgeChallengeRecord:
    row = get_challenge(request.challenge_id)
    if not row:
        raise ValueError("Unknown possession challenge.")
    record = KnowledgeChallengeRecord.model_validate(row)
    if request.requester_peer_id != record.requester_peer_id:
        raise ValueError("Requester is not authorized to verify this challenge.")
    if record.status not in {"issued", "failed"}:
        raise ValueError("Challenge is no longer in a verifiable state.")
    now = datetime.now(timezone.utc)
    expires_at = datetime.fromisoformat(record.expires_at.replace("Z", "+00:00"))
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    else:
        expires_at = expires_at.astimezone(timezone.utc)
    if now > expires_at:
        update_challenge_status(record.challenge_id, status="expired", verification_note="challenge expired before verification")
        raise ValueError("Possession challenge expired before verification.")

    response = request.response
    if response.challenge_id != record.challenge_id or response.shard_id != record.shard_id:
        update_challenge_status(record.challenge_id, status="failed", verification_note="challenge identity mismatch")
        raise ValueError("Challenge response does not match the issued challenge.")
    if response.holder_peer_id != record.holder_peer_id or response.requester_peer_id != record.requester_peer_id:
        update_challenge_status(record.challenge_id, status="failed", verification_note="challenge actor mismatch")
        raise ValueError("Challenge response actor mismatch.")
    if int(response.chunk_index) != int(record.chunk_index):
        update_challenge_status(record.challenge_id, status="failed", verification_note="challenge chunk mismatch")
        raise ValueError("Challenge response chunk mismatch.")
    chunk = base64.b64decode(response.chunk_b64.encode("utf-8"))
    observed_hash = hash_bytes(chunk)
    if observed_hash != record.expected_chunk_hash or response.chunk_hash != record.expected_chunk_hash:
        update_challenge_status(record.challenge_id, status="failed", verification_note="chunk hash verification failed")
        raise ValueError("Challenge response failed chunk hash verification.")

    update_challenge_status(record.challenge_id, status="passed", verification_note="holder returned the expected CAS chunk")
    updated = get_challenge(record.challenge_id)
    return KnowledgeChallengeRecord.model_validate(updated or row)


def _holder_row(shard_id: str, holder_peer_id: str) -> dict[str, Any] | None:
    for row in holders_for_shard(shard_id, active_only=True):
        if str(row.get("holder_peer_id") or "") == holder_peer_id:
            return row
    return None
