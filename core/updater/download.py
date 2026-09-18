"""Download authority: resumable staged download + full verification + disk preflight.

Contract:

  * disk space is checked BEFORE any network transfer (artifact size × 2 + headroom:
    the compressed bundle plus its extraction must both fit);
  * the transfer writes `artifact.part` + `artifact.part.meta.json`; an interrupted
    download leaves that pair behind so the next attempt RESUMES with a byte-range
    request instead of starting blind;
  * a partial from a DIFFERENT artifact (url/size/sha mismatch) is discarded, never
    resumed — that is the corrupt-partial defense;
  * the writer never accepts more bytes than the manifest's declared size (a hostile
    server cannot use the updater to fill the disk);
  * after transfer the artifact must pass size + sha256 + its own Ed25519 signature
    (over the artifact bytes, verified with the same pinned publisher key that verified
    the manifest) before it is renamed to its final staged name.

Production transport goes through the ONE outbound door inside
`named_background_effect_scope("self_update.artifact_download")`; tests inject a fake
`fetch`. A door veto surfaces as a typed refusal, never as silent "no update".
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from core.updater.manifest import ArtifactEntry, verify_artifact_bytes

logger = logging.getLogger("vool.updater.download")

#: Extra room beyond the artifact itself: extraction space plus working margin.
HEADROOM_BYTES = 64 * 1024 * 1024
SIZE_SAFETY_FACTOR = 2

_PART_NAME = "artifact.part"
_META_NAME = "artifact.part.meta.json"


def staged_artifact_name(artifact: ArtifactEntry) -> str:
    """Public form of the staged-file name derivation (resume needs the same name)."""
    return _staged_name(artifact)


class DownloadReason(Enum):
    OK = "ok"
    DISK_SPACE = "insufficient_disk_space"
    FETCH_FAILED = "download_failed"
    REFUSED = "download_refused_by_policy"
    SIZE_MISMATCH = "size_mismatch"
    HASH_MISMATCH = "hash_mismatch"
    ARTIFACT_SIGNATURE_INVALID = "artifact_signature_invalid"

    def plain_message(self) -> str:
        return {
            DownloadReason.OK: "The update finished downloading.",
            DownloadReason.DISK_SPACE: (
                "There isn't enough free space on this disk to install the update. Free up some "
                "space and try again — nothing was changed."
            ),
            DownloadReason.FETCH_FAILED: (
                "The download didn't finish. It will pick up where it left off next time you try."
            ),
            DownloadReason.REFUSED: "The download was blocked by this app's network policy.",
            DownloadReason.SIZE_MISMATCH: (
                "The downloaded file wasn't the size the update said it would be, so it was thrown away."
            ),
            DownloadReason.HASH_MISMATCH: (
                "The downloaded file didn't match its checksum, so it was thrown away and nothing was installed."
            ),
            DownloadReason.ARTIFACT_SIGNATURE_INVALID: (
                "The downloaded file's signature didn't verify, so it was thrown away and nothing was installed."
            ),
        }[self]


@dataclass(frozen=True)
class DownloadResult:
    ok: bool
    reason: DownloadReason
    path: Path | None = None
    bytes_downloaded: int = 0
    resumed: bool = False
    resumable: bool = False

    @property
    def plain_message(self) -> str:
        return self.reason.plain_message()


class Transport(Protocol):
    def open(self, url: str, headers: dict[str, str] | None = None, timeout: float = 60.0) -> object: ...


class DiskPreflight:
    """Free-space check with an injectable probe (tests simulate a full disk)."""

    def __init__(self, free_bytes: Callable[[Path], int] | None = None):
        self._free_bytes = free_bytes

    def free(self, directory: Path) -> int:
        if self._free_bytes is not None:
            return int(self._free_bytes(directory))
        return shutil.disk_usage(directory).free

    def check(self, directory: Path, required_bytes: int) -> bool:
        directory.mkdir(parents=True, exist_ok=True)
        return self.free(directory) >= int(required_bytes)

    def required_for(self, artifact: ArtifactEntry) -> int:
        return artifact.size * SIZE_SAFETY_FACTOR + HEADROOM_BYTES


def _staged_name(artifact: ArtifactEntry) -> str:
    name = artifact.url.rsplit("/", 1)[-1] or "artifact.bin"
    keep = [c if (c.isalnum() or c in "._-") else "_" for c in name]
    return "".join(keep)[:120]


def verify_staged_artifact(path: Path, artifact: ArtifactEntry, public_key_hex: str) -> DownloadResult:
    """Verify a downloaded file against its manifest entry: size, sha256, Ed25519."""
    try:
        size = Path(path).stat().st_size
    except OSError:
        return DownloadResult(False, DownloadReason.FETCH_FAILED)
    if size != artifact.size:
        return DownloadResult(False, DownloadReason.SIZE_MISMATCH)
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    if digest.hexdigest() != artifact.sha256:
        return DownloadResult(False, DownloadReason.HASH_MISMATCH)
    with open(path, "rb") as fh:
        blob = fh.read()
    if not verify_artifact_bytes(blob, artifact, public_key_hex):
        return DownloadResult(False, DownloadReason.ARTIFACT_SIGNATURE_INVALID)
    return DownloadResult(True, DownloadReason.OK, path=Path(path))


def _write_meta(meta_path: Path, meta: dict) -> None:
    tmp = meta_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, sort_keys=True), encoding="utf-8")
    os.replace(tmp, meta_path)


def _read_meta(meta_path: Path) -> dict | None:
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


class StagedDownloader:
    """Resumable, verifying downloader for one artifact at a time."""

    def __init__(
        self,
        *,
        fetch: Callable[[str, dict | None, float], object] | None = None,
        disk: DiskPreflight | None = None,
        chunk_size: int = 1 << 16,
        progress: Callable[[int, int], None] | None = None,
        timeout: float = 60.0,
    ):
        self._fetch = fetch or door_fetch
        self._disk = disk or DiskPreflight()
        self._chunk = max(1, int(chunk_size))
        self.progress = progress  # public: the flow installs its own progress sink
        self._timeout = timeout

    # -- public API --------------------------------------------------------- #

    def download(self, artifact: ArtifactEntry, staging_dir: Path, public_key_hex: str) -> DownloadResult:
        staging_dir = Path(staging_dir)
        staging_dir.mkdir(parents=True, exist_ok=True)

        if not self._disk.check(staging_dir, self._disk.required_for(artifact)):
            return DownloadResult(False, DownloadReason.DISK_SPACE)

        part_path = staging_dir / _PART_NAME
        meta_path = staging_dir / _META_NAME
        meta = _read_meta(meta_path)
        resume_from = 0
        if (
            meta is not None
            and part_path.exists()
            and str(meta.get("url")) == artifact.url
            and int(meta.get("size") or 0) == artifact.size
            and str(meta.get("sha256")) == artifact.sha256
        ):
            persisted = part_path.stat().st_size
            claimed = int(meta.get("bytes_written") or 0)
            # Trust the smaller of file size and bookkeeping: a crash between data write
            # and meta write must never make us skip bytes.
            resume_from = min(persisted, claimed) if 0 <= claimed <= persisted else 0
            if resume_from and resume_from >= artifact.size:
                resume_from = 0  # "complete" partial still has to re-verify below

        headers: dict[str, str] = {"User-Agent": "vool-updater"}
        if resume_from:
            headers["Range"] = f"bytes={resume_from}-"
            if meta and meta.get("etag"):
                headers["If-Range"] = str(meta["etag"])
            logger.info("resuming artifact download at byte %d", resume_from)

        try:
            response = self._fetch(artifact.url, headers, self._timeout)
        except PolicyRefusalError:
            return DownloadResult(False, DownloadReason.REFUSED)
        except Exception as exc:
            logger.warning("artifact fetch failed before transfer: %s", exc)
            return DownloadResult(False, DownloadReason.FETCH_FAILED, resumable=True)

        status = int(getattr(response, "status", 200) or 200)
        if status not in (200, 206):
            _close(response)
            return DownloadResult(False, DownloadReason.FETCH_FAILED, resumable=part_path.exists())

        body = _RespReader(response)
        if status == 200 and resume_from:
            # Server ignored the range: restart from zero.
            resume_from = 0
        elif status == 206:
            pass  # resuming where we asked

        transferred = 0
        try:
            mode = "ab" if resume_from and status == 206 else "wb"
            if mode == "wb":
                resume_from = 0
            with open(part_path, mode) as out:
                done = resume_from
                while True:
                    wanted = min(self._chunk, artifact.size - done) if done < artifact.size else 0
                    if wanted == 0:
                        break
                    block = body.read(wanted)
                    if not block:
                        break
                    out.write(block)
                    done += len(block)
                    transferred += len(block)
                    if self.progress:
                        self.progress(done, artifact.size)
                if done >= artifact.size:
                    # Overshoot probe: a server that keeps sending past the manifest's
                    # declared size means the document does not describe this artifact.
                    extra = body.read(1)
                    if extra:
                        part_path.unlink(missing_ok=True)
                        meta_path.unlink(missing_ok=True)
                        return DownloadResult(False, DownloadReason.SIZE_MISMATCH)
                else:
                    # The connection closed CLEANLY before the declared size (a FIN, not
                    # a reset): an interrupted transfer, not a corrupt one. Keep the
                    # partial resumable — deleting it here turned every quiet network
                    # drop into a thrown-away download (measured in the final-p1
                    # journey: resume never re-engaged after such a cut).
                    out.flush()
                    os.fsync(out.fileno())
                    _write_meta(
                        meta_path,
                        {
                            "url": artifact.url,
                            "size": artifact.size,
                            "sha256": artifact.sha256,
                            "bytes_written": done,
                            "etag": "",
                        },
                    )
                    return DownloadResult(
                        False,
                        DownloadReason.FETCH_FAILED,
                        bytes_downloaded=transferred,
                        resumed=bool(resume_from),
                        resumable=True,
                    )
                out.flush()
                os.fsync(out.fileno())
            etag = _header(response, "ETag")
            _write_meta(
                meta_path,
                {
                    "url": artifact.url,
                    "size": artifact.size,
                    "sha256": artifact.sha256,
                    "bytes_written": resume_from + transferred,
                    "etag": etag,
                },
            )
        except PolicyRefusalError:
            return DownloadResult(False, DownloadReason.REFUSED)
        except Exception as exc:
            logger.warning("artifact transfer interrupted: %s", exc)
            # Always leave a resumable pair behind: the partial plus bookkeeping that
            # never claims more than what certainly hit disk (file size caps the claim).
            safe = part_path.stat().st_size if part_path.exists() else 0
            _write_meta(
                meta_path,
                {
                    "url": artifact.url,
                    "size": artifact.size,
                    "sha256": artifact.sha256,
                    "bytes_written": safe,
                    "etag": "",
                },
            )
            return DownloadResult(
                False,
                DownloadReason.FETCH_FAILED,
                bytes_downloaded=transferred,
                resumed=bool(resume_from),
                resumable=safe > 0,
            )
        finally:
            _close(response)

        final_path = staging_dir / _staged_name(artifact)
        outcome = verify_staged_artifact(part_path, artifact, public_key_hex)
        if not outcome.ok:
            part_path.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)
            return outcome
        os.replace(part_path, final_path)
        meta_path.unlink(missing_ok=True)
        return DownloadResult(
            True,
            DownloadReason.OK,
            path=final_path,
            bytes_downloaded=transferred,
            resumed=bool(resume_from),
        )


class _RespReader:
    """Normalizes response body reads; a transport error mid-body is FETCH_FAILED with
    the partial preserved for resume."""

    __slots__ = ("_resp",)

    def __init__(self, resp: object):
        self._resp = resp

    def read(self, n: int) -> bytes:
        data = self._resp.read(n)
        if data is None:
            return b""
        if isinstance(data, str):  # pragma: no cover - defensive for odd transports
            return data.encode("utf-8", "replace")
        return bytes(data)


def _close(resp: object) -> None:
    close = getattr(resp, "close", None)
    if callable(close):
        with contextlib.suppress(Exception):  # closing must never mask the result
            close()


def _header(resp: object, name: str) -> str:
    headers = getattr(resp, "headers", None)
    if headers is None:
        return ""
    try:
        value = headers.get(name)
    except Exception:  # pragma: no cover
        return ""
    return str(value) if value else ""


class PolicyRefusalError(Exception):
    """Raised by the door transport when the outbound policy vetoes the fetch."""


def door_fetch(url: str, headers: dict[str, str] | None = None, timeout: float = 60.0) -> object:
    """The production transport: the ONE outbound door inside the updater's named
    background scope. A policy veto raises — the caller converts it to a typed refusal."""
    from core.effect_gateway import named_background_effect_scope

    with named_background_effect_scope("self_update.artifact_download"):
        try:
            from core.remote_fetch_policy import open_remote_url

            return open_remote_url(str(url), headers=dict(headers or {}), timeout=float(timeout))
        except Exception as exc:
            if _is_policy_refusal(exc):
                raise PolicyRefusalError(str(exc)) from exc
            raise


def fetch_manifest_bytes(url: str, *, timeout: float = 15.0) -> bytes:
    """Fetch the signed manifest document through the one outbound door under the
    check scope. Raises on failure — the check service converts that to 'check failed'."""
    from core.effect_gateway import named_background_effect_scope

    with named_background_effect_scope("self_update.manifest_check"):
        from core.remote_fetch_policy import open_remote_url

        response = open_remote_url(
            str(url), headers={"User-Agent": "vool-updater"}, timeout=float(timeout)
        )
        try:
            return response.read()
        finally:
            _close(response)


def _is_policy_refusal(exc: Exception) -> bool:
    name = type(exc).__name__
    return "Refused" in name or "Veto" in name or "refused" in str(exc).lower() or "veto" in str(exc).lower()


__all__ = [
    "DiskPreflight",
    "DownloadReason",
    "DownloadResult",
    "StagedDownloader",
    "door_fetch",
    "fetch_manifest_bytes",
    "staged_artifact_name",
    "verify_staged_artifact",
]
