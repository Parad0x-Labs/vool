from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

from core import policy_engine
from core.retry_policy import DEFAULT_RETRY_POLICY, next_delay, should_retry
from core.timeout_policy import DEFAULT_TIMEOUT_POLICY
from network.chunk_protocol import (
    ChunkFrame,
    TransferManifest,
    chunk_payload,
    chunk_to_dict,
    decode_frame,
    encode_frame,
    manifest_to_dict,
    reassemble_chunks,
)
from network.stream_transport import StreamClientTlsConfig, StreamEndpoint, send_frame


@dataclass
class ReceivedTransfer:
    manifest: TransferManifest
    created_at: float = field(default_factory=time.time)
    chunks: dict[int, ChunkFrame] = field(default_factory=dict)

    def add_chunk(self, frame: ChunkFrame) -> None:
        self.chunks[frame.index] = frame

    def is_complete(self) -> bool:
        return len(self.chunks) == self.manifest.chunk_count

    def payload(self) -> bytes:
        return reassemble_chunks(self.manifest, list(self.chunks.values()))


class TransferManager:
    def __init__(self, *, tls_client_config: StreamClientTlsConfig | None = None) -> None:
        self._incoming: dict[str, ReceivedTransfer] = {}
        self._incoming_lock = threading.Lock()
        self._incoming_bytes = 0
        self._max_incoming_transfers = max(1, int(policy_engine.get("network.stream.max_incoming_transfers", 512)))
        self._max_incoming_bytes = max(1024, int(policy_engine.get("network.stream.max_incoming_bytes", 67108864)))
        self._incoming_ttl_seconds = max(1.0, float(policy_engine.get("network.stream.incoming_ttl_seconds", 120.0)))
        self._tls_client_config = tls_client_config or StreamClientTlsConfig()

    def receive_frame(self, raw: bytes) -> bytes:
        message_type, payload = decode_frame(raw)
        if message_type == "manifest":
            manifest = TransferManifest(**payload)
            if manifest.total_bytes > self._max_incoming_bytes:
                return encode_frame("error", {"reason": "manifest_too_large", "transfer_id": manifest.transfer_id})
            now = time.time()
            with self._incoming_lock:
                self._prune_locked(now)
                if len(self._incoming) >= self._max_incoming_transfers:
                    return encode_frame("error", {"reason": "incoming_transfer_limit", "transfer_id": manifest.transfer_id})
                if self._incoming_bytes + manifest.total_bytes > self._max_incoming_bytes:
                    return encode_frame("error", {"reason": "incoming_memory_limit", "transfer_id": manifest.transfer_id})
                existing = self._incoming.get(manifest.transfer_id)
                if existing is not None:
                    self._incoming_bytes = max(0, self._incoming_bytes - int(existing.manifest.total_bytes))
                self._incoming[manifest.transfer_id] = ReceivedTransfer(manifest=manifest, created_at=now)
                self._incoming_bytes += int(manifest.total_bytes)
            return encode_frame("ack", {"transfer_id": manifest.transfer_id, "received": -1})

        if message_type == "chunk":
            frame = ChunkFrame(**payload)
            with self._incoming_lock:
                self._prune_locked(time.time())
                bucket = self._incoming.get(frame.transfer_id)
                if bucket is None:
                    return encode_frame("error", {"reason": "missing_manifest", "transfer_id": frame.transfer_id})
                if frame.total_chunks != bucket.manifest.chunk_count:
                    self._drop_locked(frame.transfer_id)
                    return encode_frame("error", {"reason": "chunk_count_mismatch", "transfer_id": frame.transfer_id})
                if frame.index < 0 or frame.index >= bucket.manifest.chunk_count:
                    return encode_frame("error", {"reason": "chunk_index_out_of_range", "transfer_id": frame.transfer_id})
                bucket.add_chunk(frame)
            return encode_frame("ack", {"transfer_id": frame.transfer_id, "received": frame.index})

        return encode_frame("error", {"reason": f"unknown_message_type:{message_type}"})

    def completed_payload(self, transfer_id: str) -> bytes | None:
        with self._incoming_lock:
            bucket = self._incoming.get(transfer_id)
            if not bucket or not bucket.is_complete():
                return None
            payload = bucket.payload()
            self._drop_locked(transfer_id)
            return payload

    def send_payload(
        self,
        endpoint: StreamEndpoint,
        payload: bytes,
        *,
        chunk_size: int = 64 * 1024,
    ) -> str:
        transfer_id = str(uuid.uuid4())
        manifest, chunks = chunk_payload(transfer_id, payload, chunk_size=chunk_size)
        self._send_with_retry(endpoint, encode_frame("manifest", manifest_to_dict(manifest)))
        for chunk in chunks:
            self._send_with_retry(endpoint, encode_frame("chunk", chunk_to_dict(chunk)))
        return transfer_id

    def _send_with_retry(self, endpoint: StreamEndpoint, frame: bytes) -> None:
        attempt = 0
        while True:
            response = send_frame(
                endpoint,
                frame,
                timeout_seconds=float(DEFAULT_TIMEOUT_POLICY.transfer_seconds),
                tls_config=self._tls_client_config,
            )
            if response is not None:
                msg_type, _ = decode_frame(response)
                if msg_type == "ack":
                    return
            attempt += 1
            if not should_retry(attempt, DEFAULT_RETRY_POLICY):
                raise TimeoutError("Transfer frame failed after retries.")
            import time

            time.sleep(next_delay(attempt, DEFAULT_RETRY_POLICY))

    def _drop_locked(self, transfer_id: str) -> None:
        bucket = self._incoming.pop(transfer_id, None)
        if bucket is not None:
            self._incoming_bytes = max(0, self._incoming_bytes - int(bucket.manifest.total_bytes))

    def _prune_locked(self, now: float) -> None:
        cutoff = now - self._incoming_ttl_seconds
        stale = [transfer_id for transfer_id, bucket in self._incoming.items() if float(bucket.created_at) < cutoff]
        for transfer_id in stale:
            self._drop_locked(transfer_id)
