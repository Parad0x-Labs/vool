"""What a refused or inapplicable release says on screen: plain sentences, no internal codes, one period."""
from __future__ import annotations

import json

from core.updater.service import UpdateCheckService
from core.updater.status import UpdatePhase
from core.updater.trust import TrustedPublishers

from .helpers import build_manifest, generate_publisher_keypair, manifest_bytes


def _check(tmp_path, document, public, *, platform="macos-arm64"):
    service = UpdateCheckService(manifest_url="fixture", trust=TrustedPublishers({"release-2026-01": public}),
                                 installed_version="0.7.0", channel="beta", platform=platform,
                                 data_dir=tmp_path, fetch=lambda _url: manifest_bytes(document))
    service.check_once()
    return service.status.load()


def test_altered_release_information_is_described_in_plain_words(tmp_path):
    private, public = generate_publisher_keypair()
    document, _ = build_manifest(private_hex=private, version="0.7.1", channel="beta")
    document = json.loads(json.dumps(document))
    document["notes"] = "altered after signing"
    status = _check(tmp_path, document, public)
    assert status.phase is UpdatePhase.FAILED
    assert "signature_mismatch" not in status.message and "manifest" not in status.message
    assert "signature didn't match" in status.message
    assert ".." not in status.message


def test_release_without_this_machines_download_reads_as_one_sentence(tmp_path):
    private, public = generate_publisher_keypair()
    document, _ = build_manifest(private_hex=private, version="0.7.1", channel="beta")
    status = _check(tmp_path, document, public, platform="macos-x64")
    assert status.phase is UpdatePhase.UNAVAILABLE
    assert ".." not in status.message
