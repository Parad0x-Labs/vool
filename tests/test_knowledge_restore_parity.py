"""Live vs restored knowledge-shard authorization parity (F11 repair).

Confirmed at e821457d: the live-row branch of
``load_canonical_shareable_shard_payload`` checked quarantine, public scope,
``evaluate_shareable_knowledge(...).can_promote`` and an active local holder.
The archive branch (row gone, dense capsule in the CAS) checked only holder/
manifest/blob availability — a payload with the WRONG shard id was served under
the request, private scope passed, expiry and freshness were never re-checked,
and manifest-recorded hashes were never bound to the fetched bytes.

This file pins the repaired contract through the REAL callers and the REAL
storage.cas (no core/liquefy_cas stand-in):
- restored identity must equal the requested shard id
- fetched compressed bytes must match the manifest's recorded compressed hash;
  decompressed bytes must match the manifest's content hash
- the restored payload re-enters the SAME shareability authority as live rows
  (scope, expiry, freshness window, quality/trust/utility gates)
- a legitimate public promoted shard still restores after row deletion
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from core.knowledge_registry import (
    load_canonical_shareable_shard_payload,
    register_local_shard,
)
from core.liquefy_bridge import pack_bytes_artifact
from network.signer import get_local_peer_id
from storage.cas import put_bytes
from storage.db import get_connection
from storage.knowledge_manifests import upsert_manifest
from storage.migrations import run_migrations
from storage.replica_table import upsert_holder

import storage.chunk_store as chunk_store

NOW = datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _insert_shard_row(
    *,
    shard_id: str,
    problem_class: str,
    summary: str,
    quality: float,
    trust: float,
    resolution: list[str],
    share_scope: str = "public_knowledge",
    freshness_ts: str | None = None,
    expires_ts: str | None = None,
) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO learning_shards (
                shard_id, schema_version, problem_class, problem_signature,
                summary, resolution_pattern_json, environment_tags_json,
                source_type, source_node_id, quality_score, trust_score,
                local_validation_count, local_failure_count,
                quarantine_status, risk_flags_json, freshness_ts, expires_ts,
                signature, origin_task_id, origin_session_id, share_scope,
                restricted_terms_json, created_at, updated_at
            ) VALUES (?, 1, ?, ?, ?, ?, ?, 'local_generated', ?, ?, ?, 0, 0, 'active', '[]', ?, ?, '', '', '', ?, '[]', ?, ?)
            """,
            (
                shard_id,
                problem_class,
                f"sig-{uuid.uuid4().hex}",
                summary,
                json.dumps(resolution),
                json.dumps({"os": "linux", "lang": "python"}),
                get_local_peer_id(),
                quality,
                trust,
                freshness_ts or _iso(NOW),
                expires_ts,
                share_scope,
                _iso(NOW),
                _iso(NOW),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _delete_shard_row(shard_id: str) -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM learning_shards WHERE shard_id = ?", (shard_id,))
        conn.commit()
    finally:
        conn.close()


def _promote_and_archive(
    *,
    shard_id: str,
    problem_class: str,
    summary: str,
    quality: float = 0.9,
    trust: float = 0.8,
    resolution: list[str] | None = None,
    share_scope: str = "public_knowledge",
    freshness_ts: str | None = None,
    expires_ts: str | None = None,
) -> dict:
    """Register a real shard through the real promotion flow, then delete its
    learning row so loads take the archive branch."""
    _insert_shard_row(
        shard_id=shard_id,
        problem_class=problem_class,
        summary=summary,
        quality=quality,
        trust=trust,
        resolution=resolution or ["capture", "compress", "verify"],
        share_scope=share_scope,
        freshness_ts=freshness_ts,
        expires_ts=expires_ts,
    )
    manifest = register_local_shard(shard_id)
    assert manifest is not None, "fixture shard must promote"
    _delete_shard_row(shard_id)
    return manifest


def _craft_archive_manifest(
    *,
    shard_id: str,
    payload: dict,
    content_hash_override: str | None = None,
    compressed_hash_override: str | None = None,
) -> None:
    """Hand-pack a dense capsule exactly the way promotion does, with optional
    hash overrides to simulate manifest/bytes disagreement."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    packed = pack_bytes_artifact(
        artifact_id=shard_id,
        payload=canonical,
        category="knowledge",
        file_stem=f"knowledge-{hashlib.sha256(canonical).hexdigest()[:24]}",
        profile="knowledge",
    )
    cas_manifest = put_bytes(bytes(packed["compressed_payload"]))
    metadata = {
        "share_scope": str(payload.get("share_scope") or "public_knowledge"),
        "dense_storage_backend": str(packed["storage_backend"]),
        "dense_compressed_sha256": compressed_hash_override or str(packed["compressed_sha256"]),
        "dense_cas_blob_hash": cas_manifest["blob_hash"],
        "canonical_status": "promoted",
    }
    upsert_manifest(
        manifest_id=f"manifest-{(content_hash_override or packed['content_sha256'])[:24]}",
        shard_id=shard_id,
        content_hash=content_hash_override or str(packed["content_sha256"]),
        version=1,
        topic_tags=[str(payload.get("problem_class") or "generic")],
        summary_digest=hashlib.sha256(str(payload.get("summary") or "").encode()).hexdigest()[:24],
        size_bytes=int(packed["compressed_bytes"]),
        metadata=metadata,
    )
    upsert_holder(
        shard_id=shard_id,
        holder_peer_id=get_local_peer_id(),
        home_region="global",
        content_hash=content_hash_override or str(packed["content_sha256"]),
        version=1,
        freshness_ts=_iso(NOW),
        expires_at=_iso(NOW + timedelta(days=30)),
        access_mode="public",
        fetch_route={"method": "request_shard", "shard_id": shard_id},
        trust_weight=0.8,
        status="active",
        source="local",
    )


def _public_payload(shard_id: str, **overrides) -> dict:
    payload = {
        "shard_id": shard_id,
        "schema_version": 1,
        "problem_class": "greenhouse_control",
        "problem_signature": "sig-greenhouse",
        "summary": "Greenhouse night-cycle control fix with vent duty calibration",
        "resolution_pattern": ["observe night cycle", "recalibrate vent duty", "verify humidity"],
        "environment_tags": {"os": "linux"},
        "quality_score": 0.9,
        "trust_score": 0.8,
        "risk_flags": [],
        "freshness_ts": _iso(NOW),
        "expires_ts": None,
        "signature": "",
        "source_type": "local_generated",
        "source_node_id": get_local_peer_id(),
        "origin_task_id": "",
        "origin_session_id": "",
        "share_scope": "public_knowledge",
        "restricted_terms": [],
    }
    payload.update(overrides)
    return payload


class KnowledgeRestoreParityTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            for table in ("learning_shards", "knowledge_manifests", "knowledge_holders"):
                try:
                    conn.execute(f"DELETE FROM {table}")
                except Exception:
                    continue  # lazily-created store tables may not exist yet
            conn.commit()
        finally:
            conn.close()
        self._cas_root = Path(tempfile.mkdtemp(prefix="kr-restore-cas-"))
        patcher = mock.patch.object(chunk_store, "CHUNK_ROOT", self._cas_root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(lambda: shutil.rmtree(self._cas_root, ignore_errors=True))

    # -- positive controls ---------------------------------------------------------------

    def test_legitimate_public_shard_still_restores_after_row_deletion(self) -> None:
        shard_id = f"shard-{uuid.uuid4().hex}"
        manifest = _promote_and_archive(
            shard_id=shard_id,
            problem_class="orchard_pruning",
            summary="Orchard pruning schedule repair with scaffold-whip ratio rules",
        )
        payload = load_canonical_shareable_shard_payload(shard_id)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["shard_id"], shard_id)
        self.assertEqual(payload["problem_class"], "orchard_pruning")
        self.assertTrue(manifest["metadata"]["dense_cas_blob_hash"])

    def test_crafted_public_capsule_restores(self) -> None:
        """A hand-packed capsule identical in shape to a promoted one restores —
        the parity gates refuse bad content, not the archive path itself."""
        shard_id = f"shard-{uuid.uuid4().hex}"
        _craft_archive_manifest(shard_id=shard_id, payload=_public_payload(shard_id))
        payload = load_canonical_shareable_shard_payload(shard_id)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["shard_id"], shard_id)
        self.assertEqual(payload["problem_class"], "greenhouse_control")

    # -- identity binding ------------------------------------------------------------------

    def test_restore_serves_only_the_requested_identity(self) -> None:
        """A manifest whose blob holds a DIFFERENT shard's capsule must refuse —
        the F11 probe served shard A under a request for shard B."""
        shard_a = f"shard-{uuid.uuid4().hex}"
        shard_b = f"shard-{uuid.uuid4().hex}"
        manifest_a = _promote_and_archive(
            shard_id=shard_a,
            problem_class="apiary_frames",
            summary="Apiary frame-spacing fix with winter-cluster volume targets",
        )
        manifest_b = _promote_and_archive(
            shard_id=shard_b,
            problem_class="cider_press",
            summary="Cider press pressure curve fix with pomace moisture bounds",
        )
        # Point shard B's manifest at shard A's dense capsule: every hash is
        # internally consistent (they are A's real hashes), so only identity
        # binding can refuse the swap.
        metadata = dict(manifest_b["metadata"])
        metadata["dense_cas_blob_hash"] = manifest_a["metadata"]["dense_cas_blob_hash"]
        metadata["dense_compressed_sha256"] = manifest_a["metadata"]["dense_compressed_sha256"]
        upsert_manifest(
            manifest_id=manifest_b["manifest_id"],
            shard_id=shard_b,
            content_hash=manifest_a["content_hash"],
            version=1,
            topic_tags=[],
            summary_digest=manifest_b["summary_digest"],
            size_bytes=manifest_b["size_bytes"],
            metadata=metadata,
        )
        served = load_canonical_shareable_shard_payload(shard_b)
        self.assertIsNone(
            served,
            "a capsule carrying another shard's identity must not be served",
        )
        # The honest capsule still restores.
        self.assertIsNotNone(load_canonical_shareable_shard_payload(shard_a))

    def test_crafted_capsule_with_foreign_identity_is_refused(self) -> None:
        requested = f"shard-{uuid.uuid4().hex}"
        foreign = f"shard-{uuid.uuid4().hex}"
        _craft_archive_manifest(
            shard_id=requested,
            payload=_public_payload(foreign),  # bytes belong to another shard
        )
        self.assertIsNone(load_canonical_shareable_shard_payload(requested))

    # -- integrity binding -------------------------------------------------------------------

    def test_manifest_compressed_hash_disagreement_is_refused(self) -> None:
        shard_id = f"shard-{uuid.uuid4().hex}"
        _craft_archive_manifest(
            shard_id=shard_id,
            payload=_public_payload(shard_id),
            compressed_hash_override=hashlib.sha256(b"not the real compressed bytes").hexdigest(),
        )
        self.assertIsNone(load_canonical_shareable_shard_payload(shard_id))

    def test_manifest_content_hash_disagreement_is_refused(self) -> None:
        shard_id = f"shard-{uuid.uuid4().hex}"
        _craft_archive_manifest(
            shard_id=shard_id,
            payload=_public_payload(shard_id),
            content_hash_override=hashlib.sha256(b"not the real raw bytes").hexdigest(),
        )
        self.assertIsNone(load_canonical_shareable_shard_payload(shard_id))

    # -- authorization parity ------------------------------------------------------------------

    def test_private_scope_capsule_is_refused(self) -> None:
        """Private conversational recall must not ride a public-shareable loader:
        a local_only payload is refused exactly as a local_only live row is."""
        shard_id = f"shard-{uuid.uuid4().hex}"
        _craft_archive_manifest(
            shard_id=shard_id,
            payload=_public_payload(shard_id, share_scope="local_only"),
        )
        self.assertIsNone(load_canonical_shareable_shard_payload(shard_id))

    def test_expired_capsule_is_refused(self) -> None:
        shard_id = f"shard-{uuid.uuid4().hex}"
        _craft_archive_manifest(
            shard_id=shard_id,
            payload=_public_payload(shard_id, expires_ts=_iso(NOW - timedelta(days=2))),
        )
        self.assertIsNone(load_canonical_shareable_shard_payload(shard_id))

    def test_stale_freshness_capsule_is_refused(self) -> None:
        shard_id = f"shard-{uuid.uuid4().hex}"
        _craft_archive_manifest(
            shard_id=shard_id,
            payload=_public_payload(shard_id, freshness_ts=_iso(NOW - timedelta(days=400))),
        )
        self.assertIsNone(load_canonical_shareable_shard_payload(shard_id))

    def test_below_gate_capsule_is_refused_like_live_rows(self) -> None:
        """The same low-value content the live branch refuses (candidate_only)
        must also be refused from the archive — parity, not archive leniency."""
        shard_id = f"shard-{uuid.uuid4().hex}"
        _craft_archive_manifest(
            shard_id=shard_id,
            payload=_public_payload(
                shard_id,
                quality_score=0.21,
                trust_score=0.24,
                summary="thin",
                resolution_pattern=[],
            ),
        )
        self.assertIsNone(load_canonical_shareable_shard_payload(shard_id))

    def test_quarantined_risk_flag_capsule_is_refused(self) -> None:
        shard_id = f"shard-{uuid.uuid4().hex}"
        _craft_archive_manifest(
            shard_id=shard_id,
            payload=_public_payload(shard_id, risk_flags=["credential_leak"]),
        )
        self.assertIsNone(load_canonical_shareable_shard_payload(shard_id))


if __name__ == "__main__":
    unittest.main()
