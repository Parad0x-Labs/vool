from __future__ import annotations

import contextlib
import gzip
import hashlib
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core import audit_logger
from core.liquefy_client import LiquefyClientV1
from core.runtime_paths import data_path
from storage.db import get_connection

try:
    import zstandard as zstd

    _ZSTD_AVAILABLE = True
except ImportError:
    zstd = None
    _ZSTD_AVAILABLE = False

_CLIENT_LOCK = threading.RLock()
_CLIENT: LiquefyClientV1 | None = None
_VOOL_VAULT = Path(os.environ.get("VOOL_LIQUEFY_HOME", str(data_path("liquefy_vault")))).expanduser().resolve()
_DEFAULT_ARCHIVE_LEVEL = max(1, min(19, int(os.environ.get("VOOL_LIQUEFY_ARCHIVE_LEVEL", "12"))))
_DEFAULT_KNOWLEDGE_LEVEL = max(1, min(19, int(os.environ.get("VOOL_LIQUEFY_KNOWLEDGE_LEVEL", "19"))))


def get_liquefy_client(*, force_refresh: bool = False) -> LiquefyClientV1:
    global _CLIENT
    with _CLIENT_LOCK:
        if force_refresh or _CLIENT is None:
            _CLIENT = LiquefyClientV1()
        return _CLIENT


def liquefy_available() -> bool:
    return bool(get_liquefy_client().available)


def _vault_dir(category: str) -> Path:
    preferred = (_VOOL_VAULT / str(category or "artifacts").strip()).resolve()
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        return preferred
    except OSError:
        fallback = data_path("liquefy_vault", str(category or "artifacts").strip())
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback.resolve()


def _async_run(func):
    def _guarded(*args, **kwargs):
        # The export/telemetry work is best-effort background bookkeeping. A
        # failure here (db hiccup, disk full, etc.) must never propagate out of
        # the worker thread, where it would only surface as an unhandled-thread
        # traceback. Swallow + audit so the caller is never affected.
        try:
            func(*args, **kwargs)
        except Exception as exc:
            with contextlib.suppress(Exception):
                audit_logger.log(
                    "liquefy_async_task_error",
                    target_id=str(getattr(func, "__name__", "liquefy_async")),
                    target_type="system",
                    details={"error": str(exc)},
                )

    def wrapper(*args, **kwargs):
        # Guard thread creation/start too: if the runtime cannot spawn a thread,
        # the caller's hot path should not crash over background bookkeeping.
        try:
            worker = threading.Thread(target=_guarded, args=args, kwargs=kwargs, daemon=True)
            worker.start()
        except Exception as exc:
            with contextlib.suppress(Exception):
                audit_logger.log(
                    "liquefy_async_spawn_error",
                    target_id=str(getattr(func, "__name__", "liquefy_async")),
                    target_type="system",
                    details={"error": str(exc)},
                )

    return wrapper


def _append_local_audit_event(event_type: str, target_id: str, details: dict[str, Any]) -> None:
    audit_path = _vault_dir("audit") / "events.jsonl"
    event = {
        "schema": "vool.liquefy.audit_event.v1",
        "event_type": str(event_type or "").strip(),
        "target_id": str(target_id or "").strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "details": dict(details or {}),
    }
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True, ensure_ascii=True) + "\n")


@_async_run
def stream_telemetry_event(event_type: str, target_id: str, details: dict[str, Any]) -> None:
    try:
        _append_local_audit_event(event_type, target_id, details)
    except Exception as exc:
        audit_logger.log("liquefy_telemetry_error", target_id=target_id, target_type="system", details={"error": str(exc)})


@_async_run
def export_task_bundle(parent_task_id: str) -> None:
    _export_task_bundle_sync(parent_task_id)


def _export_task_bundle_sync(parent_task_id: str) -> None:
    conn = None
    try:
        conn = get_connection()
        parent = conn.execute("SELECT * FROM local_tasks WHERE task_id = ?", (parent_task_id,)).fetchone()
        if not parent:
            return
        capsules = conn.execute("SELECT * FROM task_capsules WHERE task_id LIKE ?", (f"{parent_task_id}%",)).fetchall()
        final = conn.execute("SELECT * FROM finalized_responses WHERE parent_task_id = ?", (parent_task_id,)).fetchone()
        bundle = {
            "trace_id": parent_task_id,
            "metadata": dict(parent),
            "capsules": [dict(capsule) for capsule in capsules],
            "final_response": dict(final) if final else None,
            # A8 pass-001 payload lineage: the governing finalization of the
            # bundled answer ('' when the source row predates lineage — never
            # fabricated) so erasure traversal reaches this bundle deterministically.
            "source_finalization_id": str((dict(final) if final else {}).get("finalization_id") or ""),
            "vault_timestamp": datetime.now(timezone.utc).isoformat(),
        }
        proof = _pack_bundle_via_liquefy(parent_task_id, bundle)
        if proof and proof.ok:
            _append_local_audit_event(
                "task_bundle_packed",
                parent_task_id,
                {
                    "bundle_path": proof.out_dir,
                    "schema_version": proof.schema_version,
                    "tool": proof.tool,
                },
            )
            audit_logger.log(
                "liquefy_vault_packed",
                target_id=parent_task_id,
                target_type="task",
                details={
                    "bundle_path": proof.out_dir,
                    "schema_version": proof.schema_version,
                    "tool": proof.tool,
                },
            )
            return

        packed = pack_json_artifact(
            artifact_id=parent_task_id,
            payload=bundle,
            category="bundles",
            file_stem=parent_task_id,
        )
        audit_logger.log(
            "liquefy_vault_packed",
            target_id=parent_task_id,
            target_type="task",
            details={
                "bundle_path": str(packed.get("path") or ""),
                "storage_backend": str(packed.get("storage_backend") or ""),
                "fallback_local_archive": True,
                "liquefy_error": str(proof.error if proof else ""),
            },
        )
    except Exception as exc:
        audit_logger.log("liquefy_vault_error", target_id=parent_task_id, target_type="task", details={"error": str(exc)})
    finally:
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()


def _pack_bundle_via_liquefy(parent_task_id: str, bundle: dict[str, Any]):
    client = get_liquefy_client()
    if not client.available:
        return None
    out_dir = _vault_dir("bundles") / _safe_file_stem(parent_task_id)
    with tempfile.TemporaryDirectory() as tmp_dir:
        stage_dir = Path(tmp_dir) / _safe_file_stem(parent_task_id)
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "task_bundle.json").write_text(
            json.dumps(bundle, indent=2, sort_keys=True, ensure_ascii=True),
            encoding="utf-8",
        )
        proof = client.pack_run_bundle(
            stage_dir,
            out_dir,
            "vool",
            {
                "trace_id": parent_task_id,
                "task_id": parent_task_id,
                "bundle_schema": "vool.task_bundle.v1",
            },
        )
    return proof


def execution_payload_within_size_limit(
    sandbox_context: dict[str, Any], payload: dict[str, Any]
) -> bool:
    """True when the command payload is small enough to process. A SIZE CAP, nothing more.

    This was named `apply_local_execution_safety` and called from the sandbox chokepoint under the
    comment "Pre-flight check with the ExecutionGate", returning "Liquefy safety guard blocked
    execution" on False. It is neither a safety guard nor the ExecutionGate: it serialises the
    payload, computes a SHA-256, DISCARDS the hash, and returns True. Measured 2026-08-29 it
    returns True for `rm -rf /`, a fork bomb, `git push --force`, and piping a secret to netcat.

    Nothing was exploitable — `core/execution_gate.py::evaluate_command` a few lines below does the
    real work and blocks all of those — but a function named for safety that checks nothing is a
    trap for the next reader, and it occupies precisely the seam where a real pre-execution policy
    check belongs (path-risk classification and leak scanning; see docs/recovery/NOT_IMPLEMENTED.md).

    The behaviour is unchanged: a 5 MiB cap, failing closed on an unserialisable payload.
    """
    try:
        serialized = json.dumps(
            {
                "sandbox_context": dict(sandbox_context or {}),
                "payload": dict(payload or {}),
            },
            sort_keys=True,
            ensure_ascii=True,
        ).encode("utf-8")
        if len(serialized) > 5 * 1024 * 1024:
            audit_logger.log(
                "execution_payload_size_cap_exceeded",
                target_id="",
                target_type="system",
                details={"error": "execution payload exceeded the 5 MiB local size cap"},
            )
            return False
        hashlib.sha256(serialized).hexdigest()
        return True
    except Exception as exc:
        audit_logger.log("liquefy_safety_guard_error", target_id="", target_type="system", details={"error": str(exc)})
        return False


#: Deprecated name kept so existing callers and tests keep working. The old name claimed a safety
#: guarantee this function never provided; prefer `execution_payload_within_size_limit`.
apply_local_execution_safety = execution_payload_within_size_limit

def lookup_cold_archive_candidates(query_text: str, *, limit: int = 3) -> list[dict[str, Any]]:
    query_tokens = {
        token
        for token in "".join(ch if ch.isalnum() else " " for ch in (query_text or "").lower()).split()
        if len(token) >= 3
    }
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT parent_task_id, rendered_persona_text, raw_synthesized_text, status_marker, confidence_score, created_at
            FROM finalized_responses
            ORDER BY created_at DESC
            LIMIT 40
            """
        ).fetchall()
    finally:
        conn.close()

    ranked: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        rendered = str(row["rendered_persona_text"] or "")
        raw = str(row["raw_synthesized_text"] or "")
        combined = f"{rendered} {raw}".lower()
        overlap = len(query_tokens & {token for token in "".join(ch if ch.isalnum() else " " for ch in combined).split() if len(token) >= 3})
        if query_tokens and overlap == 0:
            continue
        ranked.append(
            (
                overlap,
                {
                    "archive_id": row["parent_task_id"],
                    "source_type": "cold_archive",
                    "storage_backend": "liquefy" if liquefy_available() else "local_archive",
                    "status_marker": row["status_marker"],
                    "confidence_score": float(row["confidence_score"] or 0.0),
                    "created_at": row["created_at"],
                    "preview": (rendered or raw)[:220],
                },
            )
        )
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in ranked[:limit]]


def pack_bytes_artifact(
    *,
    artifact_id: str,
    payload: bytes,
    category: str = "artifacts",
    file_stem: str | None = None,
    compression_level: int | None = None,
    text_like: bool = True,
    profile: str = "archive",
) -> dict[str, Any]:
    del text_like
    clean_artifact_id = str(artifact_id or "").strip()
    if not clean_artifact_id:
        raise ValueError("artifact_id is required")

    raw = bytes(payload)
    raw_digest = hashlib.sha256(raw).hexdigest()
    safe_stem = _safe_file_stem(file_stem or clean_artifact_id)
    out_dir = _vault_dir(str(category or "artifacts"))
    level = int(compression_level or (_DEFAULT_KNOWLEDGE_LEVEL if profile == "knowledge" else _DEFAULT_ARCHIVE_LEVEL))
    if _ZSTD_AVAILABLE and zstd is not None:
        compressed = zstd.ZstdCompressor(level=max(1, min(19, level))).compress(raw)
        suffix = ".zst"
        storage_backend = "liquefy"
    else:
        compressed = gzip.compress(raw, compresslevel=max(1, min(9, level)))
        suffix = ".gz"
        storage_backend = "local_archive"
    compressed_digest = hashlib.sha256(compressed).hexdigest()
    out_path = (out_dir / f"{safe_stem}{suffix}").resolve()
    out_path.write_bytes(compressed)
    return {
        "artifact_id": clean_artifact_id,
        "path": str(out_path),
        "storage_backend": storage_backend,
        "content_sha256": raw_digest,
        "compressed_sha256": compressed_digest,
        "raw_bytes": len(raw),
        "compressed_bytes": len(compressed),
        "compression_ratio": round(len(raw) / max(1, len(compressed)), 4),
        "compression_level": max(1, level),
        "profile": profile,
        "compressed_payload": compressed,
    }


def load_packed_bytes(*, payload: bytes, storage_backend: str) -> bytes:
    clean_backend = str(storage_backend or "").strip().lower()
    if clean_backend == "liquefy":
        if not _ZSTD_AVAILABLE or zstd is None:
            raise RuntimeError("Liquefy payload requested but zstandard runtime is unavailable.")
        return zstd.ZstdDecompressor().decompress(payload)
    if clean_backend in {"local_archive", "gzip"}:
        return gzip.decompress(payload)
    raise ValueError(f"Unsupported packed payload backend: {storage_backend}")


def pack_json_artifact(
    *,
    artifact_id: str,
    payload: Any,
    category: str = "artifacts",
    file_stem: str | None = None,
) -> dict[str, Any]:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    packed = pack_bytes_artifact(
        artifact_id=artifact_id,
        payload=raw,
        category=category,
        file_stem=file_stem,
        profile="archive",
        text_like=True,
    )
    _append_local_audit_event(
        "research_artifact_packed",
        str(packed["artifact_id"]),
        {
            "artifact_id": str(packed["artifact_id"]),
            "storage_backend": str(packed["storage_backend"]),
            "path": str(packed["path"]),
            "raw_bytes": int(packed["raw_bytes"]),
            "compressed_bytes": int(packed["compressed_bytes"]),
            "compression_ratio": float(packed["compression_ratio"]),
            "content_sha256": str(packed["content_sha256"]),
        },
    )
    audit_logger.log(
        "liquefy_json_artifact_packed",
        target_id=str(packed["artifact_id"]),
        target_type="artifact",
        details={
            "storage_backend": str(packed["storage_backend"]),
            "path": str(packed["path"]),
            "raw_bytes": int(packed["raw_bytes"]),
            "compressed_bytes": int(packed["compressed_bytes"]),
            "compression_ratio": float(packed["compression_ratio"]),
            "content_sha256": str(packed["content_sha256"]),
        },
    )
    return packed


def _safe_file_stem(value: str) -> str:
    text = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in str(value or "").strip())
    compact = "-".join(part for part in text.split("-") if part)
    return compact[:96] or "artifact"
