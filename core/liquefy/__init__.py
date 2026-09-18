"""core.liquefy — compressed, searchable cold-log integration (P1).

Public surface:
    LiquefyLogStore   hot/cold event store over the vendored COL2 codec
    hooks             the additive projection hooks real producers call
    api               bytes-level wrappers over the vendored OX-LIQUEFY snapshot
    redaction         secret redaction at ingest, before any persistence

Laws this package owns (dossier 2026-08-31 §36): compression is storage
optimization, never authority; verify-before-serve; corruption fails closed
with a receipt; redaction happens before persistence; the hot log is the
original recovery path for anything not yet verified into cold storage.
"""
from core.liquefy.store import (
    EntryNotFound,
    LiquefyLogStore,
    LiquefyStoreError,
    SealResult,
    SealVerifyError,
    SegmentCorruptError,
    SegmentKeyError,
    SegmentMissingError,
)

__all__ = [
    "EntryNotFound",
    "LiquefyLogStore",
    "LiquefyStoreError",
    "SealResult",
    "SealVerifyError",
    "SegmentCorruptError",
    "SegmentKeyError",
    "SegmentMissingError",
]
