"""core.updater — the signed atomic self-update foundation.

Separate authorities, one flow:

  trust / manifest   fail-closed Ed25519 verification of the platform-neutral manifest
  decision           channel / version / replay / downgrade / platform gate (pure)
  download           resumable staged download + size/hash/signature/disk verification
  work               pause-new-work coordination + destructive-work guard + state receipts
  migrations         transactional user-data migrations with rollback
  platforms / macos  platform installer registry; macOS bundle swap, codesign, helper
  transaction        the journaled install state machine + crash recovery + rollback
  health             post-restart health probing
  status             plain-language phases + persisted status ("Update ready")
  service            non-blocking background check + the explicit user-press controller

The 2026-09-01 design record: docs/design/signed-atomic-self-update-20260901.md
"""
from core.updater.decision import UpdateDecision, UpdateDecisionReason, decide_update
from core.updater.manifest import (
    ArtifactEntry,
    ManifestReason,
    ManifestVerification,
    VerifiedManifest,
    parse_and_verify_manifest,
)
from core.updater.trust import TrustedPublishers

__all__ = [
    "ArtifactEntry",
    "ManifestReason",
    "ManifestVerification",
    "TrustedPublishers",
    "UpdateDecision",
    "UpdateDecisionReason",
    "VerifiedManifest",
    "decide_update",
    "parse_and_verify_manifest",
]
