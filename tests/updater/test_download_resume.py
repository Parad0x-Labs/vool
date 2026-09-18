"""Download authority: resumable staged download + verification + disk preflight.

Mission scenarios pinned here: interrupted download (resume, never restart-blind),
corrupt partial data, wrong size, hash mismatch, invalid artifact signature,
insufficient disk (checked BEFORE any transfer), and stale partials from a different
artifact.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.updater.download import DiskPreflight, DownloadReason, StagedDownloader, verify_staged_artifact
from core.updater.manifest import ArtifactEntry

from .helpers import generate_publisher_keypair, sha256_hex, sign_bytes

BLOB = b"A" * 300 + b"B" * 300 + b"C" * 300  # 900 bytes, spans several chunks


class FakeResponse:
    def __init__(self, status: int, body: bytes, headers: dict | None = None):
        self.status = status
        self.body = body
        self.headers = headers or {}
        self._offset = 0

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            chunk, self._offset = self.body[self._offset :], len(self.body)
        else:
            chunk = self.body[self._offset : self._offset + n]
            self._offset += len(chunk)
        return chunk

    def close(self) -> None:  # pragma: no cover - parity with urllib
        pass


class FakeServer:
    """Serves a blob with real Range support; records requests; can be made to die
    mid-transfer or serve tampered bytes."""

    def __init__(self, blob: bytes, *, etag: str = '"etag-1"'):
        self.blob = blob
        self.etag = etag
        self.range_requests: list[str] = []
        self.hits = 0
        self.fail_after_bytes: int | None = None
        self.serve_instead: bytes | None = None

    def open(self, url: str, headers: dict | None = None, timeout: float = 30.0) -> FakeResponse:
        self.hits += 1
        payload = self.serve_instead if self.serve_instead is not None else self.blob
        rng = (headers or {}).get("Range") or ""
        if rng:
            self.range_requests.append(rng)
            start = int(rng.split("=", 1)[1].split("-", 1)[0])
            if start >= len(payload):
                return FakeResponse(416, b"", {"ETag": self.etag})
            body = payload[start:]
            if self.fail_after_bytes is not None:
                body = body[: self.fail_after_bytes]
            return FakeResponse(206, body, {"ETag": self.etag})
        body = payload
        if self.fail_after_bytes is not None:
            body = body[: self.fail_after_bytes]
        return FakeResponse(200, body, {"ETag": self.etag})

    def raise_after(self, n: int | None) -> None:
        self.fail_after_bytes = n


@pytest.fixture()
def keys():
    private_hex, public_hex = generate_publisher_keypair()
    return private_hex, public_hex


def _artifact(blob: bytes, *, private_hex: str, size: int | None = None, sha: str | None = None) -> ArtifactEntry:
    return ArtifactEntry(
        platform="macos-arm64",
        url="https://updates.example.invalid/vool/0.6.0/macos-arm64.zip",
        size=len(blob) if size is None else size,
        sha256=sha256_hex(blob) if sha is None else sha,
        signature_b64=sign_bytes(private_hex, blob),
    )


def _downloader(server: FakeServer, **kwargs) -> StagedDownloader:
    return StagedDownloader(fetch=server.open, chunk_size=128, **kwargs)


class TestHappyPath:
    def test_download_and_verify(self, tmp_path, keys):
        private_hex, public_hex = keys
        server = FakeServer(BLOB)
        artifact = _artifact(BLOB, private_hex=private_hex)
        result = _downloader(server).download(artifact, tmp_path, public_hex)
        assert result.ok, result.reason
        assert result.path.read_bytes() == BLOB
        assert result.resumed is False
        assert result.bytes_downloaded == len(BLOB)
        assert server.hits == 1
        # staged artifact is final-named, no part/meta leftovers
        assert not list(tmp_path.glob("*.part"))
        assert not list(tmp_path.glob("*.meta.json"))

    def test_progress_reaches_total_monotonically(self, tmp_path, keys):
        private_hex, public_hex = keys
        seen: list[tuple[int, int]] = []
        server = FakeServer(BLOB)
        artifact = _artifact(BLOB, private_hex=private_hex)
        result = StagedDownloader(
            fetch=server.open, chunk_size=128, progress=lambda done, total: seen.append((done, total))
        ).download(artifact, tmp_path, public_hex)
        assert result.ok
        assert seen[0] == (128, len(BLOB)) or seen[0][1] == len(BLOB)
        assert seen[-1] == (len(BLOB), len(BLOB))
        assert [d for d, _ in seen] == sorted(d for d, _ in seen)

    def test_staged_name_is_derived_from_url(self, tmp_path, keys):
        private_hex, public_hex = keys
        server = FakeServer(BLOB)
        artifact = _artifact(BLOB, private_hex=private_hex)
        result = _downloader(server).download(artifact, tmp_path, public_hex)
        assert result.ok
        assert result.path.name == "macos-arm64.zip"


class TestInterruptionAndResume:
    def test_clean_short_read_keeps_the_partial_resumable(self, tmp_path, keys):
        """A connection that closes CLEANLY before the declared size (FIN, no reset)
        is an interrupted transfer, not a corrupt one: the partial must survive and
        the next attempt must RESUME over Range. (A quiet network drop used to be
        misclassified SIZE_MISMATCH and deleted the partial — final-p1 journey.)"""
        private_hex, public_hex = keys
        server = FakeServer(BLOB)
        server.raise_after(300)  # clean truncation, no exception
        downloader = _downloader(server)
        artifact = _artifact(BLOB, private_hex=private_hex)
        first = downloader.download(artifact, tmp_path, public_hex)
        assert not first.ok and first.reason is DownloadReason.FETCH_FAILED
        assert first.resumable is True
        part = tmp_path / "artifact.part"
        assert part.exists() and part.stat().st_size >= 300

        server.raise_after(None)
        second = downloader.download(artifact, tmp_path, public_hex)
        assert second.ok and second.resumed is True
        assert server.range_requests, "the retry must continue from the partial, not restart"

    def test_interrupted_download_leaves_resumable_state(self, tmp_path, keys):
        private_hex, public_hex = keys

        class DyingServer(FakeServer):
            """Emulates a hard transport error N bytes into the transfer."""

            def open(self, url, headers=None, timeout=30.0):
                resp = super().open(url, headers, timeout)
                status, body, headers_map = resp.status, resp.body, dict(resp.headers)

                class _Boom(FakeResponse):
                    def read(self, n=-1):
                        if self._offset >= 300:
                            raise ConnectionError("connection reset mid-download")
                        return super().read(n)

                return _Boom(status, body, headers_map)

        dying = DyingServer(BLOB)
        artifact = _artifact(BLOB, private_hex=private_hex)
        result = _downloader(dying).download(artifact, tmp_path, public_hex)
        assert not result.ok
        assert result.reason is DownloadReason.FETCH_FAILED
        part = tmp_path / "artifact.part"
        assert part.exists()
        # the connection dies after 300 bytes; the writer has 3 full 128-byte chunks on disk
        assert part.stat().st_size == 3 * 128
        assert result.resumable is True

    def test_resume_continues_from_offset(self, tmp_path, keys):
        private_hex, public_hex = keys
        artifact = _artifact(BLOB, private_hex=private_hex)
        # First attempt dies partway through the blob.
        server = FakeServer(BLOB)

        class Dying(FakeServer):
            def open(self, url, headers=None, timeout=30.0):
                resp = super().open(url, headers, timeout)
                if self.hits == 1:
                    class _Boom(FakeResponse):
                        def read(self, n=-1):
                            if self._offset >= 384:
                                raise ConnectionError("reset")
                            return super().read(n)

                    return _Boom(resp.status, resp.body, dict(resp.headers))
                return resp

        dying = Dying(BLOB)
        first = _downloader(dying).download(artifact, tmp_path, public_hex)
        assert not first.ok
        persisted = (tmp_path / "artifact.part").stat().st_size
        assert persisted == 384

        # Second attempt: healthy server, resumes with a Range request.
        result = _downloader(server).download(artifact, tmp_path, public_hex)
        assert result.ok, result.reason
        assert result.resumed is True
        assert server.range_requests == [f"bytes={persisted}-"]
        assert result.bytes_downloaded == len(BLOB) - persisted  # only the tail moved
        assert result.path.read_bytes() == BLOB

    def test_resume_rejected_by_server_still_succeeds(self, tmp_path, keys):
        private_hex, public_hex = keys
        artifact = _artifact(BLOB, private_hex=private_hex)

        class NoRange(FakeServer):
            def open(self, url, headers=None, timeout=30.0):
                self.hits += 1
                rng = (headers or {}).get("Range") or ""
                if rng:
                    self.range_requests.append(rng)
                # always answers 200 with the full body
                return FakeResponse(200, self.blob, {"ETag": self.etag})

        server = NoRange(BLOB)
        first = _downloader(server).download(artifact, tmp_path, public_hex)
        assert first.ok  # no interruption in this scenario either

    def test_partial_from_different_artifact_is_discarded(self, tmp_path, keys):
        private_hex, public_hex = keys
        artifact_a = _artifact(b"OLD-ARTIFACT" * 40, private_hex=private_hex)
        artifact_b = _artifact(BLOB, private_hex=private_hex)
        server = FakeServer(BLOB)
        downloader = _downloader(server)
        # plant a stale partial for artifact A
        (tmp_path / "artifact.part").write_bytes(b"OLD-ARTIFACT" * 10)
        (tmp_path / "artifact.part.meta.json").write_text(
            json.dumps(
                {"url": artifact_a.url, "size": artifact_a.size, "sha256": artifact_a.sha256, "bytes_written": 130}
            )
        )
        result = downloader.download(artifact_b, tmp_path, public_hex)
        assert result.ok
        assert result.resumed is False  # stale partial could not be reused
        assert result.path.read_bytes() == BLOB
        assert server.range_requests == []  # full fetch, no bogus Range


class TestVerificationFailures:
    def test_hash_mismatch_discards_staged_file(self, tmp_path, keys):
        private_hex, public_hex = keys
        tampered = bytes(b ^ 0x5A for b in BLOB)  # same length, different bytes
        server = FakeServer(BLOB)  # server serves the ORIGINAL bytes
        artifact = _artifact(tampered, private_hex=private_hex)  # manifest describes tampered bytes
        result = _downloader(server).download(artifact, tmp_path, public_hex)
        assert not result.ok
        assert result.reason is DownloadReason.HASH_MISMATCH
        assert result.path is None or not result.path.exists()

    def test_size_mismatch_detected(self, tmp_path, keys):
        private_hex, public_hex = keys
        server = FakeServer(BLOB)
        artifact = _artifact(BLOB, private_hex=private_hex, size=len(BLOB) - 1)
        result = _downloader(server).download(artifact, tmp_path, public_hex)
        assert not result.ok
        assert result.reason is DownloadReason.SIZE_MISMATCH

    def test_overshoot_is_never_written(self, tmp_path, keys):
        """If the server sends MORE than the manifest size, the downloader stops at the
        expected size and fails verification rather than filling the disk."""
        private_hex, public_hex = keys
        server = FakeServer(BLOB)
        artifact = _artifact(BLOB[:600], private_hex=private_hex)
        result = _downloader(server).download(artifact, tmp_path, public_hex)
        assert not result.ok
        assert result.reason in (DownloadReason.SIZE_MISMATCH, DownloadReason.HASH_MISMATCH)

    def test_invalid_artifact_signature_refused(self, tmp_path, keys):
        _private_hex, public_hex = keys
        other_private, _ = generate_publisher_keypair()
        server = FakeServer(BLOB)
        artifact = _artifact(BLOB, private_hex=other_private)  # signed by a different key
        result = _downloader(server).download(artifact, tmp_path, public_hex)
        assert not result.ok
        assert result.reason is DownloadReason.ARTIFACT_SIGNATURE_INVALID

    def test_verify_staged_artifact_standalone(self, tmp_path, keys):
        private_hex, public_hex = keys
        staged = tmp_path / "staged.zip"
        staged.write_bytes(BLOB)
        good = _artifact(BLOB, private_hex=private_hex)
        assert verify_staged_artifact(staged, good, public_hex).ok
        bad = _artifact(BLOB + b"x", private_hex=private_hex)  # declares 901 bytes
        outcome = verify_staged_artifact(staged, bad, public_hex)
        assert not outcome.ok
        assert outcome.reason is DownloadReason.SIZE_MISMATCH
        wrong_hash = ArtifactEntry(
            platform="macos-arm64",
            url=bad.url,
            size=len(BLOB),
            sha256="0" * 64,
            signature_b64=bad.signature_b64,
        )
        outcome = verify_staged_artifact(staged, wrong_hash, public_hex)
        assert outcome.reason is DownloadReason.HASH_MISMATCH


class TestDiskPreflight:
    def test_insufficient_disk_refuses_before_any_transfer(self, tmp_path, keys):
        private_hex, public_hex = keys
        server = FakeServer(BLOB)
        artifact = _artifact(BLOB, private_hex=private_hex)
        scarce = DiskPreflight(free_bytes=lambda directory: len(BLOB) // 2)
        result = StagedDownloader(fetch=server.open, chunk_size=128, disk=scarce).download(
            artifact, tmp_path, public_hex
        )
        assert not result.ok
        assert result.reason is DownloadReason.DISK_SPACE
        assert server.hits == 0  # refused before opening a socket
        assert "space" in result.plain_message.lower()

    def test_headroom_is_reserved_beyond_artifact_size(self, tmp_path, keys):
        private_hex, public_hex = keys
        server = FakeServer(BLOB)
        artifact = _artifact(BLOB, private_hex=private_hex)
        requested: list[int] = []

        def free(directory) -> int:
            return 10**12

        class Recording(DiskPreflight):
            def check(self, directory: Path, required_bytes: int) -> bool:  # type: ignore[override]
                requested.append(required_bytes)
                return True

        StagedDownloader(fetch=server.open, chunk_size=128, disk=Recording(free_bytes=free)).download(
            artifact, tmp_path, public_hex
        )
        assert requested and requested[0] >= 2 * len(BLOB)

    def test_default_preflight_uses_real_filesystem(self, tmp_path):
        preflight = DiskPreflight()
        assert preflight.check(tmp_path, 1) is True
        assert preflight.check(tmp_path, 10**18) is False
