"""LiquefyLogStore — hot/cold event log with Liquefy (COL2) cold segments.

Shape (dossier 2026-08-31 §23/§36, minimized to P1):

    HOT   hot.jsonl — recent events, one JSON envelope per line, immediately
          readable. Redaction already happened before this write.
    COLD  segments/<id>.lsegh — sealed batch: ``VLS1`` magic + framed JSON
          header + Liquefy-COL2 blob (optionally AES-256-GCM sealed). Written
          temp -> fsync -> rename, then VERIFY-BEFORE-COMMIT re-reads the file
          through the full read path; only a verified segment may advance the
          cursor. Hot rotation happens last and is lazily repairable.
    CURSOR cursor.json — the atomic authority: sealed segments in chain order
          + chain tip. Written temp -> fsync -> rename after the cold segment
          is durable. Reads take cold for seq <= last_sealed_seq and hot for
          the rest, so every crash window yields either hot or cold truth for
          an event — never both (no duplication) and never neither (no loss).

Integrity law: every cold read verifies blob_sha256 BEFORE decompression and
the canonical original hash AFTER it; any mismatch raises SegmentCorruptError
and persists a CorruptionReceipt. A corrupted segment is never served and its
failure never silently narrows a search — searches fail closed too.

Recovery-path law: seal failures (compression, write, verify) leave the hot
log untouched — the original readable copy survives whatever broke in the
cold path.

Authority law: this store is persistence, never permission. Producers keep
their own journals (Blackbox journal, receipts, bug reports, activity ledger)
and their references; this module only guarantees those references keep
resolving to the same events.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.liquefy import api
from core.liquefy.redaction import redact_payload

SCHEMA = "vool.liquefy.segment.v1"
CANON_FORM = "vool.liquefy.canon.v1"
MAGIC = b"VLS1"

DEFAULT_SEGMENT_MAX_EVENTS = 2000

_ENVELOPE_REQUIRED = ("seq", "ts", "kind", "source", "payload")


class LiquefyStoreError(RuntimeError):
    """Base class for typed store failures."""


class EntryNotFound(LiquefyStoreError):  # noqa: N818 - 'not found' is an outcome, not a fault
    pass


class SegmentCorruptError(LiquefyStoreError):
    """Verify-before-read failed; the segment is never served."""

    def __init__(self, segment_id: str, check: str, detail: str):
        super().__init__(f"segment {segment_id} failed {check}: {detail}")
        self.segment_id = segment_id
        self.check = check
        self.detail = detail


class SegmentMissingError(LiquefyStoreError):
    pass


class SegmentKeyError(LiquefyStoreError):
    """Wrong sealing key / tampered ciphertext; fail closed, serve nothing."""


class SealVerifyError(LiquefyStoreError):
    """The freshly written segment did not survive its own verify pass; hot untouched."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fsync_directory(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _atomic_write_json(path: Path, payload: dict) -> None:
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    data = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(str(tmp), str(path))
    _fsync_directory(path.parent)


def _append_receipt_line(path: Path, receipt: dict) -> None:
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, json.dumps(receipt, ensure_ascii=False).encode("utf-8") + b"\n")
        os.fsync(fd)
    finally:
        os.close(fd)


@dataclass(frozen=True)
class SealResult:
    segment_id: str
    first_seq: int
    last_seq: int
    event_count: int
    original_bytes: int
    blob_bytes: int
    ratio: float


class LiquefyLogStore:
    def __init__(
        self,
        root: Path | str,
        *,
        sealing_key: bytes | None = None,
        segment_max_events: int = DEFAULT_SEGMENT_MAX_EVENTS,
        retention_class: str = "OPERATIONAL",
    ) -> None:
        self.root = Path(root)
        self.segments_dir = self.root / "segments"
        self.receipts_dir = self.root / "receipts"
        self.hot_path = self.root / "hot.jsonl"
        self.cursor_path = self.root / "cursor.json"
        self.sealing_key = bytes(sealing_key) if sealing_key else None
        self.segment_max_events = max(1, int(segment_max_events))
        self.retention_class = str(retention_class or "OPERATIONAL")
        self._lock = threading.RLock()
        self._index: dict[str, dict] = {}
        self._last_sealed_seq = 0
        self._chain_tip = ""
        for directory in (self.root, self.segments_dir, self.receipts_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self._load_cursor()
        self._repair_hot_tail()

    # ------------------------------------------------------------------ state
    def _load_cursor(self) -> None:
        self._index = {}
        self._last_sealed_seq = 0
        self._chain_tip = ""
        if not self.cursor_path.exists():
            return
        try:
            cursor = json.loads(self.cursor_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return
        for row in cursor.get("segments") or []:
            self._index[str(row.get("segment_id"))] = dict(row)
        self._last_sealed_seq = int(cursor.get("last_sealed_seq") or 0)
        self._chain_tip = str(cursor.get("chain_tip") or "")

    def _repair_hot_tail(self) -> None:
        """A crash mid-append can leave a malformed final hot line. Entries before
        it stay readable; the torn bytes are moved aside verbatim as evidence."""
        if not self.hot_path.exists():
            return
        raw = self.hot_path.read_bytes()
        if not raw:
            return
        lines = raw.split(b"\n")
        trailing_newline = raw.endswith(b"\n")
        body, tail = (lines[:-1], lines[-1]) if not trailing_newline else (lines, b"")
        good: list[bytes] = []
        torn_at: bytes | None = None
        for line in body:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                json.loads(stripped)
            except ValueError:
                torn_at = line
                break
            good.append(stripped)
        torn = bool(torn_at) or bool(tail.strip())
        if not torn:
            return
        keep = b"".join(ln + b"\n" for ln in good)
        quarantine = self.receipts_dir / f"hot-torn-{_sha256_hex(raw)[:12]}.jsonl"
        if not quarantine.exists():
            quarantine.write_bytes(raw)
        fd = os.open(str(self.hot_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, keep)
            os.fsync(fd)
        finally:
            os.close(fd)

    def _hot_lines(self) -> list[tuple[int, dict]]:
        out: list[tuple[int, dict]] = []
        if not self.hot_path.exists():
            return out
        for raw in self.hot_path.read_bytes().splitlines():
            if not raw.strip():
                continue
            try:
                envelope = json.loads(raw)
            except ValueError:
                continue  # torn tail never blocks reads; _repair_hot_tail quarantined it
            out.append((int(envelope["seq"]), envelope))
        return out

    # ------------------------------------------------------------------ write
    def append(self, entries: list[dict]) -> list[int]:
        """Redact + assign seq + durably append to the hot log. Returns seqs."""
        if not entries:
            return []
        seqs: list[int] = []
        lines: list[bytes] = []
        with self._lock:
            next_seq = self._next_seq_locked()
            for entry in entries:
                if not isinstance(entry, dict):
                    raise TypeError("liquefy log entries must be dicts")
                payload, counts = redact_payload(entry.get("payload", {}))
                envelope = {
                    "seq": next_seq,
                    "ts": str(entry.get("ts") or _utcnow()),
                    "kind": str(entry.get("kind") or "event"),
                    "source": str(entry.get("source") or "unknown"),
                    "session_id": entry.get("session_id") or None,
                    "trace_id": entry.get("trace_id") or None,
                    "ref": entry.get("ref") if isinstance(entry.get("ref"), dict) else None,
                    "payload": payload,
                }
                if counts:
                    envelope["redactions"] = counts
                envelope = {k: v for k, v in envelope.items() if v is not None or k in _ENVELOPE_REQUIRED}
                lines.append(json.dumps(envelope, ensure_ascii=False, allow_nan=False).encode("utf-8"))
                seqs.append(next_seq)
                next_seq += 1
            fd = os.open(str(self.hot_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                for line in lines:
                    os.write(fd, line + b"\n")
                os.fsync(fd)
            finally:
                os.close(fd)
        return seqs

    def _next_seq_locked(self) -> int:
        hottest = max((seq for seq, _ in self._hot_lines()), default=0)
        return max(self._last_sealed_seq, hottest) + 1

    # ------------------------------------------------------------------- seal
    def seal(self) -> SealResult | None:
        """Atomically move the sealed prefix of the hot log into a cold segment.

        Order is the safety argument: segment durable -> segment verified ->
        cursor advanced -> hot rotated. Any crash leaves either the old cursor
        (events still served from hot; a re-seal overwrites the orphan segment
        under the same content-derived id) or the new cursor with a stale hot
        prefix that reads already ignore (seq <= last_sealed_seq)."""
        with self._lock:
            hot = [(seq, env) for seq, env in self._hot_lines() if seq > self._last_sealed_seq]
            if not hot:
                if any(seq <= self._last_sealed_seq for seq, _ in self._hot_lines()):
                    self._rotate_hot_locked()  # lazy repair of an interrupted rotation
                return None
            take = hot[: self.segment_max_events]
            records = [env for _, env in take]
            first_seq, last_seq = take[0][0], take[-1][0]
            canonical = api.canonical_jsonl(records)
            original_sha = _sha256_hex(canonical)

            commitment = api.commit_records(records)
            blob = api.engine().compress(canonical)
            stored_blob = blob
            if self.sealing_key:
                stored_blob = api.seal_blob(blob, self.sealing_key, pcc_root=commitment.root)

            segment_id = f"{first_seq:012d}-{last_seq:012d}-{original_sha[:12]}"
            header = {
                "schema": SCHEMA,
                "canonical_form": CANON_FORM,
                "segment_id": segment_id,
                "first_seq": first_seq,
                "last_seq": last_seq,
                "event_count": len(records),
                "original_sha256": original_sha,
                "original_bytes": len(canonical),
                "blob_sha256": _sha256_hex(stored_blob),
                "blob_bytes": len(stored_blob),
                "codec": {"id": api.CODEC_ID, "version": api.CODEC_VERSION, "level": api.DEFAULT_LEVEL},
                "encrypted": bool(self.sealing_key),
                "pcc": commitment.to_dict(),
                "prev_segment_hash": self._chain_tip,
                "retention_class": self.retention_class,
                "created_at": _utcnow(),
                "sealed_at": _utcnow(),
            }
            missing = [
                field
                for field in ("schema", "segment_id", "first_seq", "last_seq", "event_count", "original_sha256",
                              "blob_sha256", "codec", "pcc", "retention_class", "sealed_at")
                if header.get(field) in (None, "", b"")
            ]
            if header.get("prev_segment_hash") is None:  # genesis chain link is ""
                missing.append("prev_segment_hash")
            if missing:
                raise SealVerifyError(f"segment header missing mandatory fields: {missing}")

            header_json = json.dumps(header, ensure_ascii=False).encode("utf-8")
            frame = MAGIC + len(header_json).to_bytes(4, "big") + header_json + stored_blob
            final_path = self.segments_dir / f"{segment_id}.lsegh"
            tmp_path = self.segments_dir / f".{segment_id}.tmp-{os.getpid()}"
            fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, frame)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(str(tmp_path), str(final_path))
            _fsync_directory(self.segments_dir)

            # Verify-before-commit: read the durable file through the exact read
            # path. Any failure deletes the segment file; hot is untouched.
            try:
                recovered = self._read_segment_records(final_path)
                if api.canonical_jsonl(recovered) != canonical:
                    raise SegmentCorruptError(segment_id, "roundtrip", "recovered canonical bytes differ")
            except SegmentKeyError as exc:
                final_path.unlink(missing_ok=True)
                raise SealVerifyError(f"seal verify failed ({exc}); hot log untouched") from exc
            except SegmentCorruptError as exc:
                final_path.unlink(missing_ok=True)
                raise SealVerifyError(f"seal verify failed ({exc}); hot log untouched") from exc

            segment_hash = _sha256_hex(frame)
            self._index[segment_id] = {
                "segment_id": segment_id,
                "first_seq": first_seq,
                "last_seq": last_seq,
                "segment_hash": segment_hash,
            }
            self._chain_tip = _sha256_hex((self._chain_tip + segment_hash).encode("utf-8"))
            self._last_sealed_seq = last_seq
            self._write_cursor_locked()
            self._rotate_hot_locked()
            return SealResult(
                segment_id=segment_id,
                first_seq=first_seq,
                last_seq=last_seq,
                event_count=len(records),
                original_bytes=len(canonical),
                blob_bytes=len(stored_blob),
                ratio=round(len(canonical) / max(1, len(stored_blob)), 3),
            )

    def _write_cursor_locked(self) -> None:
        _atomic_write_json(
            self.cursor_path,
            {
                "schema": "vool.liquefy.cursor.v1",
                "last_sealed_seq": self._last_sealed_seq,
                "chain_tip": self._chain_tip,
                "updated_at": _utcnow(),
                "segments": sorted(self._index.values(), key=lambda row: row["first_seq"]),
            },
        )

    def _rotate_hot_locked(self) -> None:
        active = [env for seq, env in self._hot_lines() if seq > self._last_sealed_seq]
        data = b"".join(json.dumps(env, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n" for env in active)
        tmp = self.root / f".hot.jsonl.tmp-{os.getpid()}"
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(str(tmp), str(self.hot_path))
        _fsync_directory(self.root)

    # ------------------------------------------------------------------- read
    def _segment_path(self, segment_id: str) -> Path:
        return self.segments_dir / f"{segment_id}.lsegh"

    def _read_segment_records(self, path: Path) -> list[dict]:
        segment_id = path.stem
        raw = path.read_bytes()
        if raw[:4] != MAGIC:
            raise SegmentCorruptError(segment_id, "magic", "not a VLS1 frame")
        header_len = int.from_bytes(raw[4:8], "big")
        try:
            file_header = json.loads(raw[8 : 8 + header_len])
        except ValueError as exc:
            raise SegmentCorruptError(segment_id, "header", str(exc)) from exc
        blob = raw[8 + header_len :]
        actual_blob_sha = _sha256_hex(blob)
        if actual_blob_sha != file_header.get("blob_sha256"):
            raise SegmentCorruptError(segment_id, "blob_sha256", f"expected {file_header.get('blob_sha256')}, got {actual_blob_sha}")
        if self.sealing_key or file_header.get("encrypted"):
            if not self.sealing_key:
                raise SegmentKeyError(f"segment {segment_id} is sealed; no key configured")
            try:
                blob, _meta = api.unseal_blob(blob, self.sealing_key)
            except PermissionError as exc:
                raise SegmentKeyError(f"segment {segment_id} refused the configured key") from exc
        try:
            recovered = api.decompress(blob)
        except ValueError as exc:
            raise SegmentCorruptError(segment_id, "decompress", str(exc)) from exc
        try:
            records = [json.loads(line) for line in recovered.decode("utf-8").splitlines() if line]
        except (ValueError, UnicodeDecodeError) as exc:
            raise SegmentCorruptError(segment_id, "rows", str(exc)) from exc
        # The hash gate is canonical-over-canonical: the codec's own serializer
        # ASCII-escapes non-ASCII text (measured on real logs), so raw recovered
        # bytes may differ from the sealed canonical encoding while every value
        # is identical. Re-canonicalizing recovered rows and hashing THAT is the
        # true integrity statement — a single altered value still fails.
        recovered_canonical = api.canonical_jsonl(records)
        if _sha256_hex(recovered_canonical) != file_header.get("original_sha256"):
            raise SegmentCorruptError(segment_id, "original_sha256", "recovered canonical bytes do not match the sealed hash")
        return records

    def _segment_records(self, segment_id: str) -> list[dict]:
        path = self._segment_path(segment_id)
        if not path.exists():
            raise SegmentMissingError(f"segment file missing: {segment_id}")
        return self._read_segment_records(path)

    def _segment_records_receipted(self, segment_id: str) -> list[dict]:
        """Read surface wrapper: a verify failure persists a CorruptionReceipt
        before the typed error propagates. The segment file itself is retained
        read-only as evidence — never quarantined into silence."""
        try:
            return self._segment_records(segment_id)
        except SegmentCorruptError as exc:
            _append_receipt_line(
                self.receipts_dir / f"{segment_id}.corrupt.json",
                {
                    "segment_id": segment_id,
                    "check": exc.check,
                    "expected": "recorded hash",
                    "actual": exc.detail,
                    "action": "retained_read_only",
                    "served": False,
                    "at": _utcnow(),
                },
            )
            raise

    def _find_segment_for(self, seq: int) -> dict | None:
        for row in sorted(self._index.values(), key=lambda r: r["first_seq"]):
            if row["first_seq"] <= seq <= row["last_seq"]:
                return row
        return None

    def _append_restore_receipt(self, segment_id: str, seq: int, requester: str) -> None:
        _append_receipt_line(
            self.receipts_dir / "restore.jsonl",
            {
                "segment_id": segment_id,
                "seq": seq,
                "verified_before_serve": True,
                "requester": requester or "local",
                "at": _utcnow(),
            },
        )

    def read(self, seq: int, *, requester: str | None = None) -> dict:
        """Read one event by seq. Cold reads verify both hashes before serving."""
        seq = int(seq)
        with self._lock:
            if seq <= self._last_sealed_seq:
                row = self._find_segment_for(seq)
                if row is None:
                    raise EntryNotFound(f"seq {seq} is below the sealed horizon but covered by no segment")
                for record in self._segment_records_receipted(row["segment_id"]):
                    if record.get("seq") == seq:
                        self._append_restore_receipt(row["segment_id"], seq, requester or "")
                        return record
                raise EntryNotFound(f"seq {seq} absent from covering segment {row['segment_id']}")
            for hot_seq, env in self._hot_lines():
                if hot_seq == seq:
                    return env
            raise EntryNotFound(f"seq {seq} not found in hot log")

    def hot_entries(self, limit: int | None = None) -> list[dict]:
        """Recent UNSEALED events, immediately readable, oldest first. A stale
        sealed prefix (interrupted rotation) belongs to cold and is not shown."""
        horizon = self._last_sealed_seq
        rows = [env for seq, env in self._hot_lines() if seq > horizon]
        return rows[-limit:] if limit else rows

    def find(
        self,
        *,
        event_id: str | None = None,
        session_id: str | None = None,
        trace_id: str | None = None,
        kind: str | None = None,
        turn_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Reference-based retrieval: consumers keep their own ids; this resolves them."""

        def matches(env: dict) -> bool:
            ref = env.get("ref") or {}
            if event_id and ref.get("event_id") != event_id:
                return False
            if session_id and env.get("session_id") != session_id:
                return False
            if trace_id and env.get("trace_id") != trace_id:
                return False
            if kind and env.get("kind") != kind:
                return False
            return not (turn_id and ref.get("turn_id") != turn_id)

        out: list[dict] = []
        with self._lock:
            for row in sorted(self._index.values(), key=lambda r: r["first_seq"]):
                for record in self._segment_records_receipted(row["segment_id"]):
                    if matches(record):
                        out.append(record)
                        if len(out) >= limit:
                            return out
            for _, env in self._hot_lines():
                if matches(env):
                    out.append(env)
                    if len(out) >= limit:
                        break
        return out

    # ----------------------------------------------------------------- search
    def search(self, query: str, *, limit: int = 20, requester: str | None = None) -> dict:
        """Bounded search across hot + cold WITHOUT full cold decompression.

        Cold segments pass the blob hash gate, then the engine's column-scoped
        grep (partial decompression of candidate columns only). Corruption
        anywhere fails the whole search closed — a silent partial result would
        be a false answer."""
        started = time.perf_counter()
        hits: list[dict] = []
        scanned_segments = 0
        with self._lock:
            for row in sorted(self._index.values(), key=lambda r: r["first_seq"]):
                path = self._segment_path(row["segment_id"])
                if not path.exists():
                    raise SegmentMissingError(f"segment file missing: {row['segment_id']}")
                raw = path.read_bytes()
                header_len = int.from_bytes(raw[4:8], "big")
                file_header = json.loads(raw[8 : 8 + header_len])
                blob = raw[8 + header_len :]
                if _sha256_hex(blob) != file_header.get("blob_sha256"):
                    raise SegmentCorruptError(row["segment_id"], "blob_sha256", "hash gate failed during search")
                if self.sealing_key or file_header.get("encrypted"):
                    if not self.sealing_key:
                        raise SegmentKeyError(f"segment {row['segment_id']} is sealed; no key configured")
                    try:
                        blob, _meta = api.unseal_blob(blob, self.sealing_key)
                    except PermissionError as exc:
                        raise SegmentKeyError(f"segment {row['segment_id']} refused the configured key") from exc
                result = api.search_blob(blob, str(query))
                if result.get("error"):
                    raise SegmentCorruptError(row["segment_id"], "column integrity", str(result["error"]))
                for row_index in result["matches"]:
                    seq = file_header["first_seq"] + int(row_index)
                    hit: dict[str, Any] = {
                        "segment_id": row["segment_id"],
                        "seq": seq,
                        "method": result["method"],
                        "latency_ms": result["latency_ms"],
                    }
                    with contextlib.suppress(LiquefyStoreError):
                        hit["event"] = self.read(seq, requester=requester)  # fetch refused -> provenance stays
                    hits.append(hit)
                    if len(hits) >= limit:
                        break
                scanned_segments += 1
                if len(hits) >= limit:
                    break
            if len(hits) < limit:
                needle = str(query)
                for _, env in self._hot_lines():
                    if needle in json.dumps(env, ensure_ascii=False):
                        hits.append({"segment_id": None, "seq": env.get("seq"), "method": "hot_scan", "latency_ms": None, "event": env})
                        if len(hits) >= limit:
                            break
        return {
            "query": query,
            "hits": hits[:limit],
            "total": len(hits),
            "segments_scanned": scanned_segments,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }

    # ------------------------------------------------------------- disclosure
    def _commitment_from_header(self, header: dict):
        from core.liquefy.vendor.pcc import ColumnLeaf, Commitment

        leaves = [ColumnLeaf(name=leaf["name"], zone=leaf["zone"], leaf=bytes.fromhex(leaf["leaf"])) for leaf in header["pcc"]["leaves"]]
        return Commitment(root=bytes.fromhex(header["pcc"]["root"]), leaves=leaves)

    def disclosure_evidence(self, segment_id: str, column: str) -> dict:
        """PCC proof material for one column, derived from the header commitment
        alone — no blob is read. Pair with verify_claimed_column()."""
        from core.liquefy.vendor.pcc import inclusion_proof

        header = self.segment_header(segment_id)
        commitment = self._commitment_from_header(header)
        proof = inclusion_proof(commitment, column)
        return {
            "segment_id": segment_id,
            "column": column,
            "root_hex": header["pcc"]["root"],
            "proof": proof.to_dict(),
        }

    def verify_claimed_column(self, segment_id: str, column: str, values: list) -> bool:
        """Tamper-evident disclosure verification: does this claimed column match
        the sealed commitment? Reads no blob; verification is pure hashing."""
        from core.liquefy.vendor.pcc import _zone_for, inclusion_proof, verify_disclosure

        header = self.segment_header(segment_id)
        try:
            commitment = self._commitment_from_header(header)
            proof = inclusion_proof(commitment, column)
            return bool(verify_disclosure(commitment.root, column, _zone_for(values), values, proof))
        except Exception:
            return False

    def segment_header(self, segment_id: str) -> dict:
        path = self._segment_path(segment_id)
        if not path.exists():
            raise SegmentMissingError(f"segment file missing: {segment_id}")
        raw = path.read_bytes()
        if raw[:4] != MAGIC:
            raise SegmentCorruptError(segment_id, "magic", "not a VLS1 frame")
        header_len = int.from_bytes(raw[4:8], "big")
        return json.loads(raw[8 : 8 + header_len])

    # -------------------------------------------------------------- integrity
    def verify(self, deep: bool = False) -> dict:
        """Walk the sealed chain. deep=True re-reads every blob through the full
        read path (hash gate + decompress + original hash)."""
        report: dict[str, Any] = {"ok": True, "segments": 0, "events": 0, "chain_tip": self._chain_tip, "first_bad": None}
        tip = ""
        with self._lock:
            for row in sorted(self._index.values(), key=lambda r: r["first_seq"]):
                report["segments"] += 1
                segment_id = row["segment_id"]
                path = self._segment_path(segment_id)
                if not path.exists():
                    report.update(ok=False, first_bad={"segment_id": segment_id, "reason": "missing"})
                    return report
                raw = path.read_bytes()
                if _sha256_hex(raw) != row.get("segment_hash"):
                    report.update(ok=False, first_bad={"segment_id": segment_id, "reason": "segment_hash"})
                    return report
                header_len = int.from_bytes(raw[4:8], "big")
                file_header = json.loads(raw[8 : 8 + header_len])
                if file_header.get("prev_segment_hash") != tip:
                    report.update(ok=False, first_bad={"segment_id": segment_id, "reason": "prev_segment_hash"})
                    return report
                tip = _sha256_hex((tip + row["segment_hash"]).encode("utf-8"))
                report["events"] += int(file_header.get("event_count") or 0)
                if deep:
                    try:
                        self._segment_records_receipted(segment_id)
                    except LiquefyStoreError as exc:
                        report.update(ok=False, first_bad={"segment_id": segment_id, "reason": str(exc)})
                        return report
        if tip != self._chain_tip:
            report.update(ok=False, first_bad={"segment_id": None, "reason": "chain_tip"})
        return report

    # -------------------------------------------------------------------- ops
    def export(self, out_path: Path | str, *, first_seq: int | None = None, last_seq: int | None = None) -> int:
        """Deterministic replay: seq-ordered JSONL, cold segments then hot."""
        out = Path(out_path)
        count = 0
        lo = first_seq if first_seq is not None else 0
        hi = last_seq if last_seq is not None else float("inf")
        with self._lock:
            with open(out, "wb") as handle:
                for row in sorted(self._index.values(), key=lambda r: r["first_seq"]):
                    if row["last_seq"] < lo or row["first_seq"] > hi:
                        continue
                    for record in self._segment_records(row["segment_id"]):
                        seq = record.get("seq")
                        if seq is not None and lo <= seq <= hi:
                            handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n")
                            count += 1
                for _, env in self._hot_lines():
                    seq = env.get("seq")
                    if seq is None or seq <= self._last_sealed_seq:
                        continue  # sealed prefix of a not-yet-rotated hot file: cold is authoritative
                    if lo <= seq <= hi:
                        handle.write(json.dumps(env, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n")
                        count += 1
        return count

    def delete_cold_before(self, seq: int, *, requester: str | None = None) -> list[str]:
        """Deterministic erasure of whole segments entirely below ``seq``.

        Cursor first (the events become unreachable), files second; a crash in
        between leaves an inert orphan that gc misses but no read can reach. An
        erasure receipt (hashes only) is appended."""
        removed: list[str] = []
        with self._lock:
            for row in sorted(self._index.values(), key=lambda r: r["first_seq"]):
                if row["last_seq"] < seq:
                    removed.append(row["segment_id"])
            if not removed:
                return []
            tip_before = self._chain_tip
            for segment_id in removed:
                self._index.pop(segment_id, None)
            if self._index:
                self._relink_genesis_locked()
            self._chain_tip = ""
            for row in sorted(self._index.values(), key=lambda r: r["first_seq"]):
                self._chain_tip = _sha256_hex((self._chain_tip + row["segment_hash"]).encode("utf-8"))
            self._last_sealed_seq = max((row["last_seq"] for row in self._index.values()), default=0)
            self._write_cursor_locked()
            for segment_id in removed:
                path = self._segment_path(segment_id)
                if path.exists():
                    os.unlink(str(path))
            _fsync_directory(self.segments_dir)
            _append_receipt_line(
                self.receipts_dir / "erasure.jsonl",
                {
                    "action": "erase_cold_before",
                    "boundary_seq": int(seq),
                    "segment_ids": removed,
                    "chain_tip_before": tip_before,
                    "chain_tip_after": self._chain_tip,
                    "requester": requester or "retention",
                    "at": _utcnow(),
                },
            )
        return removed

    def _relink_genesis_locked(self) -> None:
        """After a prefix erasure the first surviving segment becomes the chain
        genesis: its prev_segment_hash is rewritten to "" and its frame (hence
        segment_hash) recomputed atomically. Blob bytes are untouched."""
        first = sorted(self._index.values(), key=lambda r: r["first_seq"])[0]
        path = self._segment_path(first["segment_id"])
        raw = path.read_bytes()
        header_len = int.from_bytes(raw[4:8], "big")
        header = json.loads(raw[8 : 8 + header_len])
        if header.get("prev_segment_hash") == "":
            return
        header["prev_segment_hash"] = ""
        header_json = json.dumps(header, ensure_ascii=False).encode("utf-8")
        frame = MAGIC + len(header_json).to_bytes(4, "big") + header_json + raw[8 + header_len :]
        tmp = self.segments_dir / f".{first['segment_id']}.relink-{os.getpid()}"
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, frame)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(str(tmp), str(path))
        _fsync_directory(self.segments_dir)
        first["segment_hash"] = _sha256_hex(frame)

    def prune_hot(self, keep_last: int) -> int:
        """Deterministic hot trim — only sealed lines may ever be dropped."""
        with self._lock:
            lines = self._hot_lines()
            active = [env for seq, env in lines if seq > self._last_sealed_seq]
            trimmable = len(lines) - len(active)
            doomed = len(active) - max(0, int(keep_last))
            if doomed <= 0:
                return 0
            if doomed > trimmable:
                raise LiquefyStoreError(
                    f"refusing to prune {doomed} unsealed hot events; seal first (only {trimmable} sealed lines trimmable)"
                )
            self._rotate_hot_locked()
            return doomed

    def stats(self) -> dict:
        with self._lock:
            hot = self._hot_lines()
            return {
                "hot_events": len(hot),
                "hot_min_seq": hot[0][0] if hot else None,
                "hot_max_seq": hot[-1][0] if hot else None,
                "last_sealed_seq": self._last_sealed_seq,
                "segments": len(self._index),
                "chain_tip": self._chain_tip,
            }

    def segments(self) -> list[dict]:
        """Sealed segments in chain order (cursor-registered rows only). The
        read-side accessor the operator layer needs so it never pokes privates."""
        with self._lock:
            return [dict(row) for row in sorted(self._index.values(), key=lambda r: r["first_seq"])]
