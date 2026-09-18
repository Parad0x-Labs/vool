"""Content-addressed storage must verify on read.

Every CAS read boundary (``storage.chunk_store``, ``storage.cas`` and
``core.liquefy_cas``) recomputes the canonical sha256 digest before handing
bytes back. Missing content is a typed *missing* outcome; mutated, swapped,
truncated or malformed content is a typed *corruption* outcome. No caller can
turn verification off, and a concurrent reader never observes a half-written
object.

These tests are deliberately built around the worst case: same-length
mutations, swapped chunks, a torn file under a live reader, a manifest that
still parses but lies. A friendly round trip proves nothing on its own.
"""
from __future__ import annotations

import contextlib
import hashlib
import inspect
import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest

import core.liquefy_cas as liquefy
import storage.cas as cas
import storage.chunk_store as chunk_store
from storage.manifest_store import save_manifest

try:  # the typed vocabulary is the deliverable; at base it does not exist yet
    from storage.cas_integrity import CasCorruptionError, CasIntegrityError, CasMissingError
    _VOCABULARY_PRESENT = True
except ImportError:  # pragma: no cover - only reachable at the defect baseline

    class CasIntegrityError(Exception):
        """Stand-in so base failures name the defect, not the import."""

    class CasCorruptionError(CasIntegrityError):
        pass

    class CasMissingError(CasIntegrityError):
        pass

    _VOCABULARY_PRESENT = False


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _leaf(root: Path, digest: str) -> Path:
    return root / digest[:2] / digest[2:4] / digest


@pytest.fixture
def chunk_root(tmp_path, monkeypatch) -> Path:
    # chunk_store.chunk_root() resolves the root on every call (it used to be a
    # constant frozen at import), so the fixture replaces the resolver.
    root = tmp_path / "cas_chunks"
    monkeypatch.setattr(chunk_store, "chunk_root", lambda: root)
    return root


@pytest.fixture
def liquefy_root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "cas"
    monkeypatch.setattr(liquefy, "CAS_DIR", root)
    monkeypatch.setattr(liquefy, "CHUNK_SIZE", 1024)
    return root


def _strict(module, name):
    fn = getattr(module, name, None)
    assert fn is not None, f"{module.__name__}.{name} strict reader is missing"
    return fn


# --------------------------------------------------------------------------- #
# vocabulary
# --------------------------------------------------------------------------- #


def test_typed_vocabulary_is_one_scoped_hierarchy():
    assert _VOCABULARY_PRESENT, "storage.cas_integrity typed vocabulary is missing"
    assert issubclass(CasCorruptionError, CasIntegrityError)
    assert issubclass(CasMissingError, CasIntegrityError)
    # missing is distinct from corruption in both directions
    assert not issubclass(CasMissingError, CasCorruptionError)
    assert not issubclass(CasCorruptionError, CasMissingError)


def test_no_read_api_exposes_a_verification_toggle():
    readers = [
        chunk_store.load_chunk,
        _strict(chunk_store, "read_chunk"),
        cas.get_bytes,
        _strict(cas, "read_bytes"),
        liquefy.get_chunk,
        _strict(liquefy, "read_chunk"),
        liquefy.reconstruct_file,
        _strict(liquefy, "reconstruct_object"),
    ]
    banned = ("verify", "check", "strict", "unsafe", "skip", "trust", "raw")
    for fn in readers:
        params = inspect.signature(fn).parameters
        for name in params:
            assert not any(token in name.lower() for token in banned), (
                f"{fn.__module__}.{fn.__name__} accepts a verification toggle: {name}"
            )
        assert not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()), (
            f"{fn.__module__}.{fn.__name__} accepts **kwargs, which can smuggle a toggle"
        )


# --------------------------------------------------------------------------- #
# storage.chunk_store
# --------------------------------------------------------------------------- #


def test_chunk_store_mutated_bytes_under_same_name_are_refused(chunk_root):
    data = b"evidence line one\nevidence line two\n"
    digest, path = chunk_store.store_chunk(data)
    assert path == _leaf(chunk_root, digest)
    path.write_bytes(data + b"appended by a same-user mutation\n")

    with pytest.raises(CasCorruptionError):
        chunk_store.load_chunk(digest)
    with pytest.raises(CasCorruptionError):
        _strict(chunk_store, "read_chunk")(digest)


def test_chunk_store_same_length_mutation_is_refused(chunk_root):
    data = b"amount: 100 EUR paid on 2026-09-02"
    digest, path = chunk_store.store_chunk(data)
    flipped = bytearray(data)
    flipped[8] = ord("9")  # 100 -> 900, same length
    path.write_bytes(bytes(flipped))
    assert path.stat().st_size == len(data)

    with pytest.raises(CasCorruptionError):
        chunk_store.load_chunk(digest)


def test_chunk_store_swapped_chunks_are_refused(chunk_root):
    a, b = b"chunk alpha " * 40, b"chunk bravo " * 40
    ha, pa = chunk_store.store_chunk(a)
    hb, pb = chunk_store.store_chunk(b)
    pa.write_bytes(b)
    pb.write_bytes(a)

    with pytest.raises(CasCorruptionError):
        chunk_store.load_chunk(ha)
    with pytest.raises(CasCorruptionError):
        chunk_store.load_chunk(hb)


def test_chunk_store_truncated_object_is_refused(chunk_root):
    data = os.urandom(8192)
    digest, path = chunk_store.store_chunk(data)
    path.write_bytes(data[: len(data) // 2])

    with pytest.raises(CasCorruptionError):
        chunk_store.load_chunk(digest)


def test_chunk_store_missing_is_a_typed_missing_outcome(chunk_root):
    digest = _sha(b"never stored")
    assert chunk_store.load_chunk(digest) is None
    with pytest.raises(CasMissingError) as excinfo:
        _strict(chunk_store, "read_chunk")(digest)
    assert not isinstance(excinfo.value, CasCorruptionError)


def test_chunk_store_valid_legacy_object_still_reads(chunk_root):
    # An object written by the pre-verification store: a plain file at the
    # hashed path, no sidecar, no marker. It must keep reading.
    data = b"legacy object written before verify-on-read landed"
    digest = _sha(data)
    leaf = _leaf(chunk_root, digest)
    leaf.parent.mkdir(parents=True)
    leaf.write_bytes(data)

    assert chunk_store.load_chunk(digest) == data
    assert _strict(chunk_store, "read_chunk")(digest) == data


@pytest.mark.parametrize(
    "address",
    ["", "abc", "../../etc/passwd", "ZZ" * 32, ("a" * 63) + "/", "A" * 64],
)
def test_chunk_store_malformed_address_is_refused_without_touching_disk(chunk_root, address):
    # A reference that is not a digest cannot resolve to any object: the
    # lenient reader reports it exactly like an unknown digest, the strict
    # reader raises the missing outcome, and nothing on disk is touched.
    assert chunk_store.load_chunk(address) is None
    with pytest.raises(CasMissingError) as excinfo:
        _strict(chunk_store, "read_chunk")(address)
    assert excinfo.value.reason == "malformed_address"
    assert not isinstance(excinfo.value, CasCorruptionError)
    assert not chunk_root.exists() or not any(chunk_root.rglob("*")), (
        "a malformed address must not create directories under the chunk root"
    )


def _slow_slicing_writer(calls: list[Path]):
    def slow_write_bytes(self: Path, payload: bytes):
        calls.append(self)
        step = max(1, len(payload) // 16)
        with open(self, "wb") as fh:
            for idx in range(0, len(payload), step):
                fh.write(payload[idx : idx + step])
                fh.flush()
                time.sleep(0.003)
        return len(payload)

    return slow_write_bytes


def _concurrent_reader_never_sees_partial(monkeypatch, store, load, data: bytes, digest: str):
    calls: list[Path] = []
    monkeypatch.setattr(Path, "write_bytes", _slow_slicing_writer(calls))

    observations: list[tuple[str, object]] = []
    barrier = threading.Barrier(2)
    done = threading.Event()

    def writer():
        barrier.wait()
        try:
            store(data)
        finally:
            done.set()

    def reader():
        barrier.wait()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                got = load(digest)
            except Exception as exc:  # we record every outcome
                observations.append(("error", type(exc).__name__))
            else:
                observations.append(("ok", None if got is None else got == data))
            if done.is_set() and observations and observations[-1] == ("ok", True):
                break

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert calls, "the store never went through the patched write; the atomic publish path was bypassed"
    partial = [o for o in observations if o == ("ok", False)]
    errors = [o for o in observations if o[0] == "error"]
    assert not partial, f"a concurrent reader observed partial bytes {len(partial)} time(s)"
    assert not errors, f"a concurrent reader hit an error while the writer was publishing: {errors[:3]}"
    assert ("ok", None) in observations, "reader never sampled during the write window"
    assert ("ok", True) in observations, "reader never saw the completed object"


def test_chunk_store_concurrent_reader_never_observes_partial_object(chunk_root, monkeypatch):
    data = os.urandom(256 * 1024)
    _concurrent_reader_never_sees_partial(
        monkeypatch, chunk_store.store_chunk, chunk_store.load_chunk, data, _sha(data)
    )


def test_chunk_store_crash_mid_write_publishes_nothing(chunk_root, monkeypatch):
    data = os.urandom(64 * 1024)
    digest = _sha(data)
    fired: list[Path] = []

    def crashing_write_bytes(self: Path, payload: bytes):
        fired.append(self)
        with open(self, "wb") as fh:
            fh.write(payload[: len(payload) // 2])
        raise OSError("simulated power loss half-way through the write")

    monkeypatch.setattr(Path, "write_bytes", crashing_write_bytes)
    with pytest.raises(OSError):
        chunk_store.store_chunk(data)

    assert fired, "store did not go through the patched write"
    assert not _leaf(chunk_root, digest).exists(), "a half-written object was published under its address"
    assert chunk_store.load_chunk(digest) is None
    leftovers = [p for p in chunk_root.rglob("*") if p.is_file()]
    assert not leftovers, f"crash left temp files behind: {leftovers}"


# --------------------------------------------------------------------------- #
# storage.cas (blob = manifest + chunks)
# --------------------------------------------------------------------------- #


def _put_multi_chunk_blob(size: int = 3000, chunk_size: int = 1000):
    data = os.urandom(size)
    manifest = cas.put_bytes(data, chunk_size=chunk_size)
    assert len(manifest["chunk_hashes"]) == 3
    return data, manifest


def test_blob_round_trip_reads_back_exact_bytes(chunk_root):
    data, manifest = _put_multi_chunk_blob()
    assert cas.get_bytes(manifest["blob_hash"]) == data
    assert _strict(cas, "read_bytes")(manifest["blob_hash"]) == data


def test_blob_chunk_mutation_is_refused_at_the_blob_boundary(chunk_root):
    _data, manifest = _put_multi_chunk_blob()
    victim = manifest["chunk_hashes"][1]
    leaf = _leaf(chunk_root, victim)
    mutated = bytearray(leaf.read_bytes())
    mutated[0] ^= 0xFF
    leaf.write_bytes(bytes(mutated))

    with pytest.raises(CasCorruptionError):
        cas.get_bytes(manifest["blob_hash"])
    with pytest.raises(CasCorruptionError):
        _strict(cas, "read_bytes")(manifest["blob_hash"])


def test_blob_manifest_with_swapped_chunk_order_is_refused(chunk_root):
    # Every chunk verifies on its own; only the whole-blob digest exposes the
    # swap. The blob address is the sha256 of the joined bytes and must be
    # recomputed on read.
    _data, manifest = _put_multi_chunk_blob()
    swapped = dict(manifest)
    swapped["chunk_hashes"] = list(reversed(manifest["chunk_hashes"]))
    save_manifest(manifest["manifest_id"], manifest["blob_hash"], swapped)

    with pytest.raises(CasCorruptionError):
        cas.get_bytes(manifest["blob_hash"])


def test_blob_manifest_swapped_with_another_blobs_manifest_is_refused(chunk_root):
    _data_a, man_a = _put_multi_chunk_blob()
    _data_b, man_b = _put_multi_chunk_blob()
    # blob A's manifest row now describes blob B's chunks
    forged = dict(man_b)
    forged["manifest_id"] = man_a["manifest_id"]
    save_manifest(man_a["manifest_id"], man_a["blob_hash"], forged)

    with pytest.raises(CasCorruptionError):
        cas.get_bytes(man_a["blob_hash"])


@pytest.mark.parametrize(
    "mutation",
    [
        "drop_last_chunk",
        "chunk_hashes_not_a_list",
        "chunk_hashes_missing",
        "total_bytes_lies",
        "blob_hash_lies",
        "garbage_json",
        "truncated_json",
    ],
)
def test_blob_malformed_or_truncated_manifest_is_refused(chunk_root, mutation):
    _data, manifest = _put_multi_chunk_blob()
    mid, bh = manifest["manifest_id"], manifest["blob_hash"]
    bad = dict(manifest)
    if mutation == "drop_last_chunk":
        bad["chunk_hashes"] = manifest["chunk_hashes"][:-1]
        bad["total_bytes"] = 2000
    elif mutation == "chunk_hashes_not_a_list":
        bad["chunk_hashes"] = "".join(manifest["chunk_hashes"])
    elif mutation == "chunk_hashes_missing":
        bad.pop("chunk_hashes")
    elif mutation == "total_bytes_lies":
        bad["total_bytes"] = 2999
    elif mutation == "blob_hash_lies":
        bad["blob_hash"] = "0" * 64

    if mutation in {"garbage_json", "truncated_json"}:
        from storage.db import get_connection

        raw = json.dumps(manifest, sort_keys=True)
        payload = "{not json at all" if mutation == "garbage_json" else raw[: len(raw) // 2]
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE manifest_store SET manifest_json = ? WHERE manifest_id = ?",
                (payload, mid),
            )
            conn.commit()
        finally:
            conn.close()
    else:
        save_manifest(mid, bh, bad)

    with pytest.raises(CasCorruptionError):
        cas.get_bytes(bh)
    with pytest.raises(CasCorruptionError):
        _strict(cas, "read_bytes")(bh)


def test_blob_missing_chunk_is_missing_not_corruption(chunk_root):
    _data, manifest = _put_multi_chunk_blob()
    _leaf(chunk_root, manifest["chunk_hashes"][2]).unlink()

    assert cas.get_bytes(manifest["blob_hash"]) is None
    with pytest.raises(CasMissingError) as excinfo:
        _strict(cas, "read_bytes")(manifest["blob_hash"])
    assert not isinstance(excinfo.value, CasCorruptionError)


def test_blob_unknown_address_is_missing(chunk_root):
    unknown = _sha(b"never put")
    assert cas.get_bytes(unknown) is None
    with pytest.raises(CasMissingError):
        _strict(cas, "read_bytes")(unknown)


def test_blob_missing_manifest_row_is_missing(chunk_root):
    from storage.db import get_connection

    _data, manifest = _put_multi_chunk_blob()
    conn = get_connection()
    try:
        conn.execute("DELETE FROM manifest_store WHERE manifest_id = ?", (manifest["manifest_id"],))
        conn.commit()
    finally:
        conn.close()
    assert cas.get_bytes(manifest["blob_hash"]) is None
    with pytest.raises(CasMissingError):
        _strict(cas, "read_bytes")(manifest["blob_hash"])


# --------------------------------------------------------------------------- #
# core.liquefy_cas (file chunking + reconstruction)
# --------------------------------------------------------------------------- #


def _make_source(tmp_path: Path, size: int = 3 * 1024 + 100) -> tuple[Path, bytes]:
    data = os.urandom(size)
    src = tmp_path / "source.bin"
    src.write_bytes(data)
    return src, data


def test_liquefy_chunk_mutation_and_same_length_mutation_are_refused(liquefy_root):
    data = b"liquefy block " * 100
    digest = liquefy.store_chunk(data)
    leaf = _leaf(liquefy_root, digest)

    leaf.write_bytes(data + b"!")
    with pytest.raises(CasCorruptionError):
        liquefy.get_chunk(digest)

    same_len = bytearray(data)
    same_len[-1] ^= 0x01
    leaf.write_bytes(bytes(same_len))
    with pytest.raises(CasCorruptionError):
        liquefy.get_chunk(digest)
    with pytest.raises(CasCorruptionError):
        _strict(liquefy, "read_chunk")(digest)


def test_liquefy_swapped_chunks_are_refused(liquefy_root):
    a, b = b"A" * 2048, b"B" * 2048
    ha, hb = liquefy.store_chunk(a), liquefy.store_chunk(b)
    _leaf(liquefy_root, ha).write_bytes(b)
    _leaf(liquefy_root, hb).write_bytes(a)
    with pytest.raises(CasCorruptionError):
        liquefy.get_chunk(ha)
    with pytest.raises(CasCorruptionError):
        liquefy.get_chunk(hb)


def test_liquefy_missing_chunk_is_typed_missing(liquefy_root):
    digest = _sha(b"absent")
    assert liquefy.get_chunk(digest) is None
    with pytest.raises(CasMissingError) as excinfo:
        _strict(liquefy, "read_chunk")(digest)
    assert not isinstance(excinfo.value, CasCorruptionError)


def test_liquefy_valid_legacy_chunk_still_reads(liquefy_root):
    data = b"legacy liquefy chunk"
    digest = _sha(data)
    leaf = _leaf(liquefy_root, digest)
    leaf.parent.mkdir(parents=True)
    leaf.write_bytes(data)
    assert liquefy.get_chunk(digest) == data


def test_liquefy_round_trip_declares_and_verifies_final_digest(liquefy_root, tmp_path):
    src, data = _make_source(tmp_path)
    manifest = liquefy.chunk_file(str(src))
    assert len(manifest["chunks"]) == 4
    assert manifest.get("file_sha256") == _sha(data), "new manifests must declare the whole-file digest"

    out = tmp_path / "out.bin"
    assert liquefy.reconstruct_file(manifest["root_hash"], str(out)) is True
    assert out.read_bytes() == data


def test_liquefy_reconstruction_verifies_every_chunk_and_leaves_no_partial_output(liquefy_root, tmp_path):
    src, _data = _make_source(tmp_path)
    manifest = liquefy.chunk_file(str(src))
    victim = manifest["chunks"][2]
    leaf = _leaf(liquefy_root, victim)
    mutated = bytearray(leaf.read_bytes())
    mutated[5] ^= 0x10
    leaf.write_bytes(bytes(mutated))

    out = tmp_path / "out.bin"
    with pytest.raises(CasCorruptionError):
        liquefy.reconstruct_file(manifest["root_hash"], str(out))
    assert not out.exists(), "a refused reconstruction left a partial output file"
    assert not list(tmp_path.glob("out.bin*")), "a refused reconstruction left temp output behind"


def test_liquefy_reconstruction_verifies_final_digest_when_declared(liquefy_root, tmp_path):
    src, _data = _make_source(tmp_path)
    manifest = liquefy.chunk_file(str(src))
    # Every chunk is genuine; the order is not. Only the declared final digest
    # can expose this.
    forged = {
        "filename": manifest["filename"],
        "size_bytes": manifest["size_bytes"],
        "chunks": list(reversed(manifest["chunks"])),
        "chunk_size_bytes": manifest["chunk_size_bytes"],
        "file_sha256": manifest["file_sha256"],
    }
    root = liquefy.store_chunk(json.dumps(forged, sort_keys=True).encode("utf-8"))

    out = tmp_path / "out.bin"
    with pytest.raises(CasCorruptionError):
        liquefy.reconstruct_file(root, str(out))
    assert not out.exists()


def test_liquefy_legacy_manifest_without_final_digest_still_reconstructs(liquefy_root, tmp_path):
    src, data = _make_source(tmp_path)
    manifest = liquefy.chunk_file(str(src))
    legacy = {
        "filename": manifest["filename"],
        "size_bytes": manifest["size_bytes"],
        "chunks": list(manifest["chunks"]),
        "chunk_size_bytes": manifest["chunk_size_bytes"],
    }
    root = liquefy.store_chunk(json.dumps(legacy, sort_keys=True).encode("utf-8"))
    out = tmp_path / "legacy_out.bin"
    assert liquefy.reconstruct_file(root, str(out)) is True
    assert out.read_bytes() == data


def test_liquefy_legacy_manifest_with_lying_size_is_refused(liquefy_root, tmp_path):
    src, _data = _make_source(tmp_path)
    manifest = liquefy.chunk_file(str(src))
    lying = {
        "filename": manifest["filename"],
        "size_bytes": manifest["size_bytes"] - 1,
        "chunks": list(manifest["chunks"]),
        "chunk_size_bytes": manifest["chunk_size_bytes"],
    }
    root = liquefy.store_chunk(json.dumps(lying, sort_keys=True).encode("utf-8"))
    out = tmp_path / "out.bin"
    with pytest.raises(CasCorruptionError):
        liquefy.reconstruct_file(root, str(out))
    assert not out.exists()


@pytest.mark.parametrize(
    "payload",
    [
        b"not json at all",
        b'{"chunks": "abc"}',
        b'{"filename": "x", "size_bytes": 1',
        b'{"chunks": ["not-a-digest"]}',
        b'[1, 2, 3]',
    ],
)
def test_liquefy_malformed_or_truncated_manifest_is_refused(liquefy_root, tmp_path, payload):
    root = liquefy.store_chunk(payload)
    out = tmp_path / "out.bin"
    with pytest.raises(CasCorruptionError):
        liquefy.reconstruct_file(root, str(out))
    with pytest.raises(CasCorruptionError):
        _strict(liquefy, "reconstruct_object")(root, str(out))
    assert not out.exists()


def test_liquefy_tampered_manifest_bytes_are_refused(liquefy_root, tmp_path):
    src, _data = _make_source(tmp_path)
    manifest = liquefy.chunk_file(str(src))
    root = manifest["root_hash"]
    leaf = _leaf(liquefy_root, root)
    stored = json.loads(leaf.read_bytes())
    stored["chunks"] = list(reversed(stored["chunks"]))
    leaf.write_bytes(json.dumps(stored, sort_keys=True).encode("utf-8"))
    out = tmp_path / "out.bin"
    with pytest.raises(CasCorruptionError):
        liquefy.reconstruct_file(root, str(out))
    assert not out.exists()


def test_liquefy_missing_chunk_during_reconstruction_is_missing_not_corruption(liquefy_root, tmp_path):
    src, _data = _make_source(tmp_path)
    manifest = liquefy.chunk_file(str(src))
    _leaf(liquefy_root, manifest["chunks"][1]).unlink()
    out = tmp_path / "out.bin"
    assert liquefy.reconstruct_file(manifest["root_hash"], str(out)) is False
    assert not out.exists()
    with pytest.raises(CasMissingError) as excinfo:
        _strict(liquefy, "reconstruct_object")(manifest["root_hash"], str(out))
    assert not isinstance(excinfo.value, CasCorruptionError)
    assert not out.exists()


def test_liquefy_missing_root_is_missing(liquefy_root, tmp_path):
    out = tmp_path / "out.bin"
    assert liquefy.reconstruct_file(_sha(b"no such manifest"), str(out)) is False
    with pytest.raises(CasMissingError):
        _strict(liquefy, "reconstruct_object")(_sha(b"no such manifest"), str(out))


def test_liquefy_concurrent_reader_never_observes_partial_object(liquefy_root, monkeypatch):
    data = os.urandom(256 * 1024)
    _concurrent_reader_never_sees_partial(
        monkeypatch, liquefy.store_chunk, liquefy.get_chunk, data, _sha(data)
    )


@pytest.mark.parametrize("address", ["", "abc", "../../etc/passwd", "ZZ" * 32, "A" * 64])
def test_liquefy_malformed_address_is_refused(liquefy_root, address, tmp_path):
    assert liquefy.get_chunk(address) is None
    with pytest.raises(CasMissingError) as excinfo:
        _strict(liquefy, "read_chunk")(address)
    assert excinfo.value.reason == "malformed_address"
    out = tmp_path / "out.bin"
    assert liquefy.reconstruct_file(address, str(out)) is False
    assert not out.exists()
    assert not liquefy_root.exists() or not any(liquefy_root.rglob("*"))


def test_blob_malformed_caller_address_is_missing_not_corruption(chunk_root):
    for address in ("", "abc", "../../etc/passwd", "Z" * 64, "A" * 64):
        assert cas.get_bytes(address) is None
        with pytest.raises(CasMissingError) as excinfo:
            _strict(cas, "read_bytes")(address)
        assert excinfo.value.reason == "malformed_address"
    assert not chunk_root.exists() or not any(chunk_root.rglob("*"))


def test_manifest_internal_malformed_address_is_corruption(chunk_root):
    # The same non-digest string inside stored content is a malformed object.
    _data, manifest = _put_multi_chunk_blob()
    bad = dict(manifest)
    bad["chunk_hashes"] = [manifest["chunk_hashes"][0], "Z" * 64, manifest["chunk_hashes"][2]]
    save_manifest(manifest["manifest_id"], manifest["blob_hash"], bad)
    with pytest.raises(CasCorruptionError) as excinfo:
        cas.get_bytes(manifest["blob_hash"])
    assert excinfo.value.reason == "malformed_address"


# --------------------------------------------------------------------------- #
# cross-cutting
# --------------------------------------------------------------------------- #


def test_callers_cannot_suppress_corruption_and_still_receive_bytes(chunk_root, liquefy_root):
    data = b"payload that will be tampered"
    digest, path = chunk_store.store_chunk(data)
    path.write_bytes(b"tampered payload that must not go" + b"\x00" * (len(data) - 33))

    received = "unset"
    with contextlib.suppress(CasIntegrityError):
        received = chunk_store.load_chunk(digest)
    assert received == "unset", "a swallowing caller still received bytes from a corrupt read"

    ldigest = liquefy.store_chunk(data)
    _leaf(liquefy_root, ldigest).write_bytes(data[::-1])
    received = "unset"
    with contextlib.suppress(CasIntegrityError):
        received = liquefy.get_chunk(ldigest)
    assert received == "unset"


def test_cas_read_and_write_paths_make_no_network_or_model_calls(chunk_root, liquefy_root, tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("CAS touched the network")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    import core.liquefy_cas as lq  # noqa: F401 - guards against a lazy model import below
    for banned in ("core.model_router", "core.model_bridge", "core.agent_runtime"):
        assert banned not in inspect.getsource(liquefy) and banned not in inspect.getsource(cas), (
            f"CAS boundary imports {banned}"
        )

    data, manifest = _put_multi_chunk_blob()
    assert cas.get_bytes(manifest["blob_hash"]) == data
    src, fdata = _make_source(tmp_path)
    m = liquefy.chunk_file(str(src))
    out = tmp_path / "out.bin"
    assert liquefy.reconstruct_file(m["root_hash"], str(out)) is True
    assert out.read_bytes() == fdata
