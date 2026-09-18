"""PCC selective disclosure over sealed segments.

The disclosure evidence derives from the segment HEADER commitment alone —
proofs and verification never decompress the blob. A verifier can confirm (or
refuse) a claimed column against the sealed root with no access to the blob.
"""
from __future__ import annotations

import pytest

from core.liquefy.store import SegmentMissingError


@pytest.fixture()
def sealed_store(make_store, sample_events):
    store = make_store()
    store.append(sample_events(50))
    sealed = store.seal()
    return store, sealed


def test_disclosure_evidence_derives_from_header(sealed_store):
    store, sealed = sealed_store
    evidence = store.disclosure_evidence(sealed.segment_id, "seq")
    assert evidence["root_hex"] and evidence["proof"]["name"] == "seq"
    assert evidence["proof"]["siblings"] is not None


def test_verify_claimed_column_accepts_truth_refuses_lies(sealed_store):
    store, sealed = sealed_store
    records = [store.read(seq) for seq in range(1, 51)]
    truth = [record.get("seq") for record in records]
    assert store.verify_claimed_column(sealed.segment_id, "seq", truth) is True
    lies = list(truth)
    lies[10] = 99999
    assert store.verify_claimed_column(sealed.segment_id, "seq", lies) is False
    with pytest.raises(KeyError):
        store.disclosure_evidence(sealed.segment_id, "no-such-column")


def test_disclosure_verification_is_blob_independent(sealed_store):
    """Corrupt the blob: header commitment still adjudicates claimed columns."""
    store, sealed = sealed_store
    truth = [store.read(seq).get("kind") for seq in range(1, 51)]  # before the blob dies
    path = store.root / "segments" / f"{sealed.segment_id}.lsegh"
    raw = bytearray(path.read_bytes())
    raw[-1] ^= 0xFF
    path.write_bytes(bytes(raw))
    evidence = store.disclosure_evidence(sealed.segment_id, "kind")
    assert evidence["column"] == "kind"
    # a lying claim still fails, a truthful claim still verifies — blob irrelevant
    assert store.verify_claimed_column(sealed.segment_id, "kind", truth) is True
    lies = list(truth)
    lies[7] = "LIED"
    assert store.verify_claimed_column(sealed.segment_id, "kind", lies) is False


def test_disclosure_on_unknown_segment_refuses(sealed_store):
    store, _sealed = sealed_store
    with pytest.raises(SegmentMissingError):
        store.disclosure_evidence("no-such-segment", "seq")
