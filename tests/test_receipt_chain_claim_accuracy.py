"""What the receipt chain proves, and the one thing it does not.

The UI said "Receipt chain valid — recorded action and evidence history is
intact." The verifier walks the receipts it is GIVEN: it starts at the empty
``prev_hash`` and follows each link. So it catches a mutated receipt, a forged
signature and a receipt removed from the MIDDLE — and it cannot catch receipts
removed from the END, because the surviving prefix is a perfectly valid chain and
nothing knows how long the chain should have been.

"Intact" is a completeness claim. The verifier makes a consistency claim. This
file pins the difference in both directions: the four tampering shapes that ARE
detected, the one that is NOT, and the fact that an anchored head is what would
close it.

Nothing here asserts prose from the UI as a proxy for behaviour — the wording
tests read the shipped strings, and the detection tests drive the real verifier.
"""
from __future__ import annotations

import pathlib

import pytest

from core.honesty_receipt import (
    CHAIN_PROVEN_CLAIM,
    CHAIN_UNPROVEN_CLAIM,
    verify_honesty_chain,
)

REPO = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture()
def chain(tmp_path, monkeypatch):
    """A real signed three-receipt chain from the production issuer."""
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    from core import honesty_receipt as hr

    receipts = []
    prev = ""
    for i in range(3):
        receipt = hr.issue_honesty_receipt(
            session_id="chain-claim",
            turn_index=i,
            prompt_text=f"step {i}",
            response_text=f"answer {i}",
            prev_hash=prev,
        )
        row = receipt if isinstance(receipt, dict) else receipt.to_dict()
        receipts.append(row)
        prev = str(row.get("content_hash") or "")
    ok, reason = verify_honesty_chain(receipts)
    assert ok, f"the fixture chain must start valid: {reason}"
    return receipts


# ── what IS detected ─────────────────────────────────────────────────────────


def test_a_mutated_receipt_is_detected(chain) -> None:
    """Alter a SIGNED field.

    The receipt stores keyed digests (`prompt_hash`, `response_hash`), never the
    raw text, so editing a `response_text` key changes nothing — it is not a field
    the receipt has. Mutating the digest is the real tampering shape, and it is
    what the signature covers.
    """
    tampered = [dict(r) for r in chain]
    assert "response_hash" in tampered[1], sorted(tampered[1])
    tampered[1]["response_hash"] = "ff" * 32
    ok, reason = verify_honesty_chain(tampered)
    assert not ok and "receipt[1]" in reason, reason


def test_a_mutated_verdict_is_detected(chain) -> None:
    """The field a reader would most want to trust."""
    tampered = [dict(r) for r in chain]
    tampered[0]["verdict"] = "answered"
    tampered[0]["verdict_detail"] = "forced"
    ok, reason = verify_honesty_chain(tampered)
    assert not ok and "receipt[0]" in reason, reason


def test_a_forged_signature_is_detected(chain) -> None:
    tampered = [dict(r) for r in chain]
    tampered[2]["signature"] = "00" * 32
    ok, reason = verify_honesty_chain(tampered)
    assert not ok and "receipt[2]" in reason, reason


def test_a_receipt_deleted_from_the_middle_is_detected(chain) -> None:
    """The next receipt's prev_hash no longer names the one before it."""
    truncated = [chain[0], chain[2]]
    ok, reason = verify_honesty_chain(truncated)
    assert not ok and "broken_chain" in reason, reason


def test_a_reordered_chain_is_detected(chain) -> None:
    ok, reason = verify_honesty_chain([chain[1], chain[0], chain[2]])
    assert not ok, reason


# ── what is NOT detected — the whole point ───────────────────────────────────


def test_a_truncated_tail_is_NOT_detected_and_that_is_why_the_claim_is_narrow(
    chain,
) -> None:
    """Delete from the end and verification still passes.

    This is not a bug being asserted as correct — it is the exact boundary the
    surfaces must describe. A verifier with no independently anchored head cannot
    know the chain was longer.
    """
    for keep in (2, 1):
        ok, reason = verify_honesty_chain(chain[:keep])
        assert ok, (
            f"a {keep}-receipt prefix of a 3-receipt chain failed verification "
            f"({reason}); if this ever starts failing, the completeness claim can "
            "be widened and this test replaced"
        )
    ok, _ = verify_honesty_chain([])
    assert ok, "an empty chain also verifies — there is nothing to compare against"


def test_an_anchored_head_would_close_it(chain) -> None:
    """The control the amendment asks for: with an expected head, truncation shows.

    Demonstrated on the spot rather than shipped, because an anchor is only worth
    anything if it is persisted where the truncation cannot reach — a design
    decision this lane does not make. What this proves is that the missing piece
    is an anchor and nothing else: the same truncated chain that verifies above
    is caught the moment an expected head is supplied.
    """
    expected_head = str(chain[-1].get("content_hash") or "")
    assert expected_head

    def verify_with_anchor(receipts, head: str) -> tuple[bool, str]:
        ok, reason = verify_honesty_chain(receipts)
        if not ok:
            return ok, reason
        actual = str(receipts[-1].get("content_hash") or "") if receipts else ""
        if head and actual != head:
            return False, "truncated_tail (head does not match the anchored head)"
        return True, "ok"

    assert verify_with_anchor(chain, expected_head)[0]
    ok, reason = verify_with_anchor(chain[:2], expected_head)
    assert not ok and "truncated_tail" in reason, reason


# ── the surfaces say the narrow thing ────────────────────────────────────────


def test_the_served_ui_no_longer_claims_the_history_is_intact() -> None:
    html = (REPO / "core" / "vool_chat_page.py").read_text(encoding="utf-8")
    assert "recorded action and evidence history is intact" not in html, (
        "the served panel still claims completeness the verifier does not establish"
    )
    assert "Receipts consistent" in html
    assert "does not prove the history is complete" in html


def test_the_cli_no_longer_prints_intact() -> None:
    source = (REPO / "core" / "honesty_receipt.py").read_text(encoding="utf-8")
    assert '_c("INTACT", "green")' not in source, "the CLI still prints INTACT"
    assert "CONSISTENT" in source
    assert "NOT PROVEN" in source


def test_the_registry_command_carries_the_boundary_not_just_the_verdict() -> None:
    source = (REPO / "core" / "command_registry" / "groups" / "receipts.py").read_text(
        encoding="utf-8"
    )
    assert "completeness_proven" in source
    assert "CHAIN_UNPROVEN_CLAIM" in source


def test_the_api_payload_carries_the_boundary() -> None:
    source = (REPO / "core" / "web" / "api" / "service.py").read_text(encoding="utf-8")
    assert "chain_completeness_proven" in source
    assert "chain_not_proven" in source


def test_the_two_claim_strings_say_opposite_things() -> None:
    """One sentence for what is proven, one for what is not — and they must differ."""
    assert CHAIN_PROVEN_CLAIM and CHAIN_UNPROVEN_CLAIM
    assert CHAIN_PROVEN_CLAIM != CHAIN_UNPROVEN_CLAIM
    assert "not proven" in CHAIN_UNPROVEN_CLAIM or "no trace" in CHAIN_UNPROVEN_CLAIM
    assert "intact" not in CHAIN_PROVEN_CLAIM.lower()
