"""Regression guard for the confused-deputy escrow drain.

A signed ``/v1/tasks/<id>/complete`` request whose body names an arbitrary
``helper_peer_id`` must NOT pay that wallet. The escrow remainder is released to
the SIGNED claimant (``request_meta['signer_peer_id']`` / the recorded
``claimed_by``), never to a caller-supplied recipient.

Pre-fix the dispatcher read ``payload['helper_peer_id']`` and paid the full
remainder to the attacker, so the first assertion below failed.
"""
from __future__ import annotations

import uuid

import pytest

from core.credit_ledger import (
    award_credits,
    escrow_credits_for_task,
    get_credit_balance,
    get_escrow_for_task,
)
from core.web.meet.routes import dispatch_request
from storage.db import get_connection
from storage.task_offer_store import claim_task_offer, get_task_offer_claimed_by


def _seed_open_task_offer(task_id: str, parent_peer_id: str) -> None:
    now = "2026-06-22T00:00:00+00:00"
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO task_offers (
                task_id, parent_peer_id, capsule_id, task_type, subtask_type,
                summary, input_capsule_hash, required_capabilities_json,
                reward_hint_json, max_helpers, priority, deadline_ts, status,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{}', 1, 'normal', ?, 'open', ?, ?)
            """,
            (
                task_id,
                parent_peer_id,
                f"capsule-{task_id}",
                "compute",
                "infer",
                "Seed offer for escrow confused-deputy guard.",
                "0" * 64,
                "[]",
                now,
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_signed_complete_does_not_pay_body_named_attacker_wallet() -> None:
    tag = uuid.uuid4().hex[:12]
    poster = f"peer-poster-{tag}"
    claimer = f"peer-claimer-{tag}"
    attacker = f"peer-attacker-{tag}"
    task_id = f"task-{tag}"

    # Poster funds the task; escrow holds 40 credits for the eventual helper.
    award_credits(poster, 100.0, "seed", receipt_id=f"seed-{tag}")
    _seed_open_task_offer(task_id, poster)
    assert escrow_credits_for_task(poster, task_id, 40.0) is True

    # The legitimate helper claims the task with a SIGNED request.
    claim_code, claim_body = dispatch_request(
        "POST",
        f"/v1/tasks/{task_id}/claim",
        {},
        {},
        service=None,
        request_meta={"signer_peer_id": claimer},
    )
    assert claim_code == 200
    assert claim_body["result"]["helper_peer_id"] == claimer
    assert get_task_offer_claimed_by(task_id) == claimer

    attacker_before = get_credit_balance(attacker)
    claimer_before = get_credit_balance(claimer)

    # The claimer completes the task but the body names the ATTACKER as the
    # payout wallet (the confused-deputy attack).
    code, body = dispatch_request(
        "POST",
        f"/v1/tasks/{task_id}/complete",
        {},
        {"result_hash": "deadbeef", "helper_peer_id": attacker},
        service=None,
        request_meta={"signer_peer_id": claimer},
    )
    assert code == 200
    assert body["result"]["credits_released"] == pytest.approx(40.0)

    # The attacker named in the body received NOTHING; the signed claimer was
    # paid the full escrow remainder.
    assert get_credit_balance(attacker) == pytest.approx(attacker_before)
    assert get_credit_balance(claimer) == pytest.approx(claimer_before + 40.0)
    assert body["result"]["helper_peer_id"] == claimer

    escrow = get_escrow_for_task(task_id)
    assert escrow is not None
    assert escrow["total_released"] == pytest.approx(40.0)


def test_signed_complete_by_non_claimer_is_rejected() -> None:
    tag = uuid.uuid4().hex[:12]
    poster = f"peer-poster-{tag}"
    claimer = f"peer-claimer-{tag}"
    intruder = f"peer-intruder-{tag}"
    task_id = f"task-{tag}"

    award_credits(poster, 100.0, "seed", receipt_id=f"seed-{tag}")
    _seed_open_task_offer(task_id, poster)
    assert escrow_credits_for_task(poster, task_id, 25.0) is True
    assert claim_task_offer(task_id, claimer) is True

    intruder_before = get_credit_balance(intruder)

    # A different signed peer tries to complete (and collect) someone else's
    # claimed task — must be rejected, and no payout occurs.
    code, body = dispatch_request(
        "POST",
        f"/v1/tasks/{task_id}/complete",
        {},
        {"result_hash": "feedface", "helper_peer_id": intruder},
        service=None,
        request_meta={"signer_peer_id": intruder},
    )
    assert code == 403
    assert body["ok"] is False
    assert get_credit_balance(intruder) == pytest.approx(intruder_before)

    escrow = get_escrow_for_task(task_id)
    assert escrow is not None
    assert escrow["total_released"] == pytest.approx(0.0)
