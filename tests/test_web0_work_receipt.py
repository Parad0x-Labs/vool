from __future__ import annotations

import json

from core.web0_work_receipt import Web0WorkReceipt, issue_work_receipt


def test_issue_work_receipt_returns_receipt() -> None:
    r = issue_work_receipt(task_id="task-001", result="hello world", worker_id="vool")
    assert isinstance(r, Web0WorkReceipt)
    assert r.task_id == "task-001"
    assert r.worker_id == "vool"
    assert r.result_hash
    assert r.receipt_id.startswith("wr-")
    assert r.issued_at > 0


def test_result_hash_is_deterministic() -> None:
    r1 = issue_work_receipt(task_id="t1", result="same result", worker_id="vool")
    r2 = issue_work_receipt(task_id="t1", result="same result", worker_id="vool")
    assert r1.result_hash == r2.result_hash


def test_bytes_result_is_hashed_correctly() -> None:
    r = issue_work_receipt(task_id="t1", result=b"binary output", worker_id="vool")
    import hashlib
    assert r.result_hash == hashlib.sha256(b"binary output").hexdigest()


def test_zk_proof_is_none_by_default() -> None:
    r = issue_work_receipt(task_id="t2", result="output", worker_id="vool")
    assert r.zk_proof is None


def test_zk_proof_fn_is_called_with_result_hash() -> None:
    called: list[str] = []

    def fake_zk(result_hash: str) -> str:
        called.append(result_hash)
        return f"zk:{result_hash[:8]}"

    r = issue_work_receipt(
        task_id="t3", result="output", worker_id="vool", zk_proof_fn=fake_zk
    )
    assert r.zk_proof is not None
    assert r.zk_proof.startswith("zk:")
    assert len(called) == 1
    assert called[0] == r.result_hash


def test_zk_proof_fn_failure_sets_none_not_raises() -> None:
    def bad_zk(result_hash: str) -> str:
        raise RuntimeError("zk unavailable")

    r = issue_work_receipt(
        task_id="t4", result="output", worker_id="vool", zk_proof_fn=bad_zk
    )
    assert r.zk_proof is None


def test_to_dict_is_json_serialisable() -> None:
    r = issue_work_receipt(task_id="t5", result="data", worker_id="vool")
    json.dumps(r.to_dict())


def test_zero_amount_produces_stub_receipt() -> None:
    r = issue_work_receipt(task_id="t6", result="data", worker_id="vool", amount_usdc=0.0)
    assert r.payment.amount_usdc == 0.0
    assert r.payment.mode == "stub"


def test_proof_hash_matches_proof_receipt() -> None:
    from core.proof_of_execution import verify_proof_receipt
    r = issue_work_receipt(task_id="t7", result="verifiable", worker_id="vool")
    assert verify_proof_receipt(r.proof) is True
