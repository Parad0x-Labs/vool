from __future__ import annotations

import base64
import hashlib
import json
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class TransferManifest:
    transfer_id: str
    total_bytes: int
    chunk_size: int
    chunk_count: int
    payload_sha256: str


@dataclass(frozen=True)
class ChunkFrame:
    transfer_id: str
    index: int
    total_chunks: int
    checksum: str
    payload_b64: str

    @property
    def payload(self) -> bytes:
        return base64.b64decode(self.payload_b64.encode("ascii"))


def checksum_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_manifest(transfer_id: str, payload: bytes, chunk_size: int) -> TransferManifest:
    return TransferManifest(
        transfer_id=transfer_id,
        total_bytes=len(payload),
        chunk_size=chunk_size,
        chunk_count=max(1, math.ceil(len(payload) / chunk_size)),
        payload_sha256=checksum_bytes(payload),
    )


def chunk_payload(transfer_id: str, payload: bytes, *, chunk_size: int = 64 * 1024) -> tuple[TransferManifest, list[ChunkFrame]]:
    manifest = build_manifest(transfer_id, payload, chunk_size)
    frames: list[ChunkFrame] = []
    for idx in range(manifest.chunk_count):
        start = idx * chunk_size
        chunk = payload[start : start + chunk_size]
        frames.append(
            ChunkFrame(
                transfer_id=transfer_id,
                index=idx,
                total_chunks=manifest.chunk_count,
                checksum=checksum_bytes(chunk),
                payload_b64=base64.b64encode(chunk).decode("ascii"),
            )
        )
    return manifest, frames


def encode_frame(message_type: str, payload: dict) -> bytes:
    return json.dumps(
        {"message_type": message_type, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def decode_frame(raw: bytes) -> tuple[str, dict]:
    data = json.loads(raw.decode("utf-8"))
    return str(data["message_type"]), dict(data["payload"])


def manifest_to_dict(manifest: TransferManifest) -> dict:
    return manifest.__dict__.copy()


def chunk_to_dict(chunk: ChunkFrame) -> dict:
    return chunk.__dict__.copy()


def reassemble_chunks(manifest: TransferManifest, frames: list[ChunkFrame]) -> bytes:
    ordered = sorted(frames, key=lambda item: item.index)
    if len(ordered) != manifest.chunk_count:
        raise ValueError("Missing chunks for transfer.")
    payload = bytearray()
    for expected_index, frame in enumerate(ordered):
        if frame.index != expected_index:
            raise ValueError("Out-of-order or missing chunk index.")
        chunk = frame.payload
        if checksum_bytes(chunk) != frame.checksum:
            raise ValueError("Chunk checksum mismatch.")
        payload.extend(chunk)
    raw = bytes(payload)
    if checksum_bytes(raw) != manifest.payload_sha256:
        raise ValueError("Transfer payload checksum mismatch.")
    return raw
