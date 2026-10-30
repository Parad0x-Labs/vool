"""A release host that cannot be reached is a network failure the person can retry, not a policy block.

The production transport classified any error whose text contained "refused" as a policy veto. A firewall
or a down host ("[Errno 111] Connection refused") was therefore shown as "The download was blocked by this
app's network policy" and marked not resumable.
"""
from __future__ import annotations

import errno
import urllib.error

from core.remote_fetch_policy import RemoteFetchRefusedError
from core.updater import download
from core.updater.download import DownloadReason, StagedDownloader
from core.updater.manifest import ArtifactEntry

from .helpers import generate_publisher_keypair, sha256_hex, sign_bytes

BLOB = b"vool update payload" * 64


def _attempt(tmp_path, monkeypatch, error: Exception):
    private, public = generate_publisher_keypair()

    def unreachable(url, *, headers, timeout):
        raise error

    monkeypatch.setattr(download, "_open_release_url", unreachable)
    artifact = ArtifactEntry(platform="macos-arm64",
                             url="https://github.com/Parad0x-Labs/vool/releases/download/v0.7.6-beta/x.zip",
                             size=len(BLOB), sha256=sha256_hex(BLOB), signature_b64=sign_bytes(private, BLOB))
    return StagedDownloader(fetch=download.door_fetch).download(artifact, tmp_path / "staging", public)


def test_refused_connection_is_a_retryable_download_failure(tmp_path, monkeypatch):
    refused = urllib.error.URLError(ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused"))
    result = _attempt(tmp_path, monkeypatch, refused)
    assert result.reason is DownloadReason.FETCH_FAILED
    assert result.resumable


def test_a_real_policy_refusal_is_still_reported_as_blocked(tmp_path, monkeypatch):
    result = _attempt(tmp_path, monkeypatch, RemoteFetchRefusedError("remote fetch is not permitted for this turn"))
    assert result.reason is DownloadReason.REFUSED
