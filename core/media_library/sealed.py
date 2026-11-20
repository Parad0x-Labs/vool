"""The sealed file format shared by the media library and its USB backups.

A sealed file is a header and a run of pieces::

    "VMLB1\\n" | key nonce (12) | sealed file key (32 + 16) | piece | piece | ...
    piece = nonce (12) | ciphertext | tag (16)        (plaintext pieces are PIECE bytes, the last may be shorter)

Every file has its own random 256-bit file key, sealed (AES-256-GCM) by a wrapping key that is never stored in
the file: the library's key for the library, the backup key for a USB backup. Each piece is AES-256-GCM under the
file key with its own nonce. The piece's associated data binds the file's name, the piece's number and whether it
is the last piece, so pieces cannot be reordered, swapped between files, dropped from the middle or cut off at the
end without the read failing. Any changed byte fails the read: a tampered file is never handed out as genuine.

A sealed file may be split into segments (``name``, ``name.1``, ``name.2`` ...) so one large video fits on a USB
stick formatted FAT32 (4 GiB per file). Segments are cut on piece boundaries.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import secrets
import struct
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"VMLB1\n"
PIECE = 1024 * 1024
NONCE = 12
TAG = 16
SEALED_KEY = 32 + TAG
HEADER = len(MAGIC) + NONCE + SEALED_KEY
SEALED_PIECE = NONCE + PIECE + TAG


class SealedError(Exception):
    """A sealed file that cannot be opened: wrong key, damaged, changed or cut short."""


def _piece_aad(ident: str, index: int, last: bool) -> bytes:
    return ident.encode("utf-8") + b"\x00" + struct.pack(">QB", index, 1 if last else 0)


def sealed_size(plain_size: int) -> int:
    """How many bytes a sealed file of ``plain_size`` plain bytes takes (all segments together)."""
    pieces = max(1, -(-plain_size // PIECE))
    return HEADER + pieces * (NONCE + TAG) + plain_size


def segment_paths(first: Path) -> list[Path]:
    """Every segment of a sealed file that exists, in order."""
    found = [first] if first.exists() else []
    index = 1
    while True:
        nxt = first.with_name(f"{first.name}.{index}")
        if not nxt.exists():
            return found
        found.append(nxt)
        index += 1


def _sync_dir(folder: Path) -> None:
    with contextlib.suppress(OSError):  # some USB file systems cannot sync a folder; each file is synced itself
        fd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class _SegmentWriter:
    """Writes one sealed file as segments of at most ``limit`` bytes, each flushed to the disk."""

    def __init__(self, first: Path, limit: int | None, mode: int):
        self.first, self.limit, self.mode = first, limit, mode
        self.index, self.written, self.handle, self.paths = 0, 0, None, []
        self._open()

    def _open(self) -> None:
        path = self.first if self.index == 0 else self.first.with_name(f"{self.first.name}.{self.index}")
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
        self.handle = os.fdopen(os.open(path, flags, self.mode), "wb")
        self.paths.append(path)
        self.written = 0

    def write(self, data: bytes, *, boundary: bool = False) -> None:
        if boundary and self.limit and self.written and self.written + len(data) > self.limit:
            self._close_current()
            self.index += 1
            self._open()
        assert self.handle is not None
        self.handle.write(data)
        self.written += len(data)

    def _close_current(self) -> None:
        if self.handle is not None:
            self.handle.flush()
            os.fsync(self.handle.fileno())
            self.handle.close()
            self.handle = None

    def close(self) -> list[Path]:
        self._close_current()
        return self.paths

    def abort(self) -> None:
        with contextlib.suppress(Exception):
            if self.handle is not None:
                self.handle.close()
        for path in self.paths:
            with contextlib.suppress(OSError):
                path.unlink()


def seal(source: Iterable[bytes], first: Path, *, wrap_key: bytes, ident: str, segment_limit: int | None = None,
         mode: int = 0o600) -> tuple[int, str, list[Path]]:
    """Seals the plain bytes from ``source`` into ``first`` (and its segments). Returns the plain size, the
    SHA-256 of the plain bytes and the paths written. Everything written is flushed to the disk. On any error the
    partly written files are removed and the error is raised."""
    file_key = AESGCM.generate_key(bit_length=256)
    digest = hashlib.sha256()
    size = 0
    writer = _SegmentWriter(first, segment_limit, mode)
    try:
        key_nonce = secrets.token_bytes(NONCE)
        writer.write(MAGIC + key_nonce + AESGCM(wrap_key).encrypt(key_nonce, file_key, b"key\x00" + ident.encode("utf-8")))
        aead = AESGCM(file_key)
        index = 0
        held = b""
        pending: bytes | None = None
        for chunk in source:
            if not chunk:
                continue
            held += chunk
            while len(held) >= PIECE:
                piece, held = held[:PIECE], held[PIECE:]
                if pending is not None:
                    _write_piece(writer, aead, ident, index, pending, last=False)
                    index += 1
                pending = piece
                digest.update(piece)
                size += len(piece)
        if held:
            if pending is not None:
                _write_piece(writer, aead, ident, index, pending, last=False)
                index += 1
            pending = held
            digest.update(held)
            size += len(held)
        _write_piece(writer, aead, ident, index, pending or b"", last=True)
        paths = writer.close()
    except BaseException:
        writer.abort()
        raise
    _sync_dir(first.parent)
    return size, digest.hexdigest(), paths


def _write_piece(writer: _SegmentWriter, aead: AESGCM, ident: str, index: int, plain: bytes, *, last: bool) -> None:
    nonce = secrets.token_bytes(NONCE)
    writer.write(nonce + aead.encrypt(nonce, plain, _piece_aad(ident, index, last)), boundary=True)


def file_chunks(path: Path, size: int = PIECE) -> Iterator[bytes]:
    with open(path, "rb") as handle:
        while True:
            data = handle.read(size)
            if not data:
                return
            yield data


class _SegmentReader:
    def __init__(self, paths: list[Path]):
        if not paths:
            raise SealedError("missing")
        self.paths = paths
        self.sizes = [p.stat().st_size for p in paths]
        self.total = sum(self.sizes)

    def read_at(self, offset: int, length: int) -> bytes:
        out = bytearray()
        position, remaining, start = offset, length, 0
        for path, size in zip(self.paths, self.sizes, strict=False):
            end = start + size
            if remaining <= 0:
                break
            if start <= position < end:
                with open(path, "rb") as handle:
                    handle.seek(position - start)
                    data = handle.read(min(remaining, end - position))
                out += data
                position += len(data)
                remaining -= len(data)
            start = end
        return bytes(out)


class Opened:
    """A sealed file opened for reading: whole, or piece by piece for a range."""

    def __init__(self, first: Path, *, wrap_key: bytes, ident: str):
        self.ident = ident
        self.reader = _SegmentReader(segment_paths(first))
        head = self.reader.read_at(0, HEADER)
        if len(head) != HEADER or head[: len(MAGIC)] != MAGIC:
            raise SealedError("not a sealed file")
        key_nonce = head[len(MAGIC): len(MAGIC) + NONCE]
        try:
            self.aead = AESGCM(AESGCM(wrap_key).decrypt(key_nonce, head[len(MAGIC) + NONCE:], b"key\x00" + ident.encode("utf-8")))
        except InvalidTag as error:
            raise SealedError("wrong key or changed") from error
        body = self.reader.total - HEADER
        self.pieces = max(1, -(-body // SEALED_PIECE))
        last_len = body - (self.pieces - 1) * SEALED_PIECE
        if body <= 0 or last_len < NONCE + TAG:
            raise SealedError("cut short")
        self.plain_size = (self.pieces - 1) * PIECE + (last_len - NONCE - TAG)

    def piece(self, index: int) -> bytes:
        if not 0 <= index < self.pieces:
            raise SealedError("no such piece")
        start = HEADER + index * SEALED_PIECE
        raw = self.reader.read_at(start, min(SEALED_PIECE, self.reader.total - start))
        try:
            return self.aead.decrypt(raw[:NONCE], raw[NONCE:], _piece_aad(self.ident, index, index == self.pieces - 1))
        except InvalidTag as error:
            raise SealedError("changed or damaged") from error

    def chunks(self) -> Iterator[bytes]:
        for index in range(self.pieces):
            yield self.piece(index)

    def range(self, start: int, end: int) -> Iterator[bytes]:
        """Plain bytes ``start`` .. ``end`` (exclusive)."""
        end = min(end, self.plain_size)
        index = start // PIECE
        while start < end:
            data = self.piece(index)
            skip = start - index * PIECE
            take = data[skip: skip + (end - start)]
            yield take
            start += len(take)
            index += 1


def verify(first: Path, *, wrap_key: bytes, ident: str, sha256: str | None = None, size: int | None = None) -> str:
    """Reads a whole sealed file back and returns the SHA-256 of its plain bytes. Raises :class:`SealedError` when
    any piece fails, or when the hash or size differs from the ones given."""
    opened = Opened(first, wrap_key=wrap_key, ident=ident)
    digest = hashlib.sha256()
    total = 0
    for chunk in opened.chunks():
        digest.update(chunk)
        total += len(chunk)
    got = digest.hexdigest()
    if (sha256 is not None and got != sha256) or (size is not None and total != size):
        raise SealedError("different content")
    return got


def remove(first: Path) -> None:
    """Deletes a sealed file and its segments. Its sealed key is overwritten first, so a copy of the rest that the
    disk may keep cannot be opened even with the wrapping key."""
    paths = segment_paths(first)
    if paths:
        with contextlib.suppress(OSError):
            with open(paths[0], "r+b") as handle:
                handle.seek(len(MAGIC))
                handle.write(b"\x00" * (NONCE + SEALED_KEY))
                handle.flush()
                os.fsync(handle.fileno())
    for path in paths:
        with contextlib.suppress(OSError):
            path.unlink()


def read_bytes(path: Path, limit: int) -> bytes:
    with open(path, "rb") as handle:
        return handle.read(limit)


def seal_small(data: bytes, *, wrap_key: bytes, ident: str) -> bytes:
    """A small sealed blob (metadata, a thumbnail, a manifest)."""
    nonce = secrets.token_bytes(NONCE)
    return b"VMLS1" + nonce + AESGCM(wrap_key).encrypt(nonce, data, b"small\x00" + ident.encode("utf-8"))


def open_small(blob: bytes, *, wrap_key: bytes, ident: str) -> bytes:
    if blob[:5] != b"VMLS1" or len(blob) < 5 + NONCE + TAG:
        raise SealedError("not a sealed blob")
    try:
        return AESGCM(wrap_key).decrypt(blob[5:5 + NONCE], blob[5 + NONCE:], b"small\x00" + ident.encode("utf-8"))
    except InvalidTag as error:
        raise SealedError("wrong key or changed") from error


def write_file(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    """Writes a small file atomically and durably (temporary file, flush, rename, folder flush)."""
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(temp, flags, mode)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            temp.unlink()
        raise
    _sync_dir(path.parent)


def stream_of(handle: BinaryIO, size: int = PIECE) -> Iterator[bytes]:
    while True:
        data = handle.read(size)
        if not data:
            return
        yield data
