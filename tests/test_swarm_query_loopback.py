from __future__ import annotations

from network import rate_limiter
from network.assist_router import RouteResult
from network.signer import get_local_peer_id as local_peer_id
from retrieval.swarm_query import broadcast_task_offer, request_specific_shard
from storage.db import get_connection
from storage.migrations import run_migrations
from tests.task_offer_fixtures import build_signed_task_offer_message


class _FakeOrderBook:
    def __init__(self) -> None:
        self.pushed: list[tuple[bytes, tuple[str, int], dict]] = []

    def push(self, raw_bytes: bytes, source_addr: tuple[str, int], offer_dict: dict) -> None:
        self.pushed.append((raw_bytes, source_addr, offer_dict))


def _clear_task_tables() -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM task_assignments")
        conn.execute("DELETE FROM task_claims")
        conn.execute("DELETE FROM task_capsules")
        conn.execute("DELETE FROM task_offers")
        conn.commit()
    finally:
        conn.close()


def _patch_loopback_env(monkeypatch, fake_book: _FakeOrderBook) -> None:
    monkeypatch.setattr("retrieval.swarm_query.get_best_helpers", lambda **kwargs: [])
    monkeypatch.setattr("retrieval.swarm_query.endpoint_for_peer", lambda peer_id: ("127.0.0.1", 49152))
    monkeypatch.setattr("retrieval.swarm_query.send_message", lambda *args, **kwargs: False)
    monkeypatch.setattr("retrieval.swarm_query.audit_logger.log", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "retrieval.swarm_query.policy_engine.get",
        lambda path, default=None: True if path == "orchestration.local_loopback_offer_on_no_helpers" else default,
    )
    monkeypatch.setattr("core.order_book.global_order_book", fake_book)


def test_loopback_offer_is_stored_locally_before_order_book_push(monkeypatch) -> None:
    run_migrations()
    _clear_task_tables()
    rate_limiter.reset_peer(local_peer_id())
    fake_book = _FakeOrderBook()
    _patch_loopback_env(monkeypatch, fake_book)

    _raw, offer, capsule = build_signed_task_offer_message()
    sent = broadcast_task_offer(
        offer_payload=offer.model_dump(mode="json"),
        required_capabilities=["research"],
        limit=3,
    )

    assert sent == 1
    assert len(fake_book.pushed) == 1
    assert fake_book.pushed[0][1] == ("127.0.0.1", 49152)

    # The self TASK_CLAIM has a FOREIGN KEY on task_offers; the loopback
    # path must persist the offer + capsule, not just enqueue raw bytes.
    conn = get_connection()
    try:
        offer_row = conn.execute(
            "SELECT * FROM task_offers WHERE task_id = ?", (offer.task_id,)
        ).fetchone()
        capsule_row = conn.execute(
            "SELECT * FROM task_capsules WHERE capsule_id = ?", (capsule.capsule_id,)
        ).fetchone()
    finally:
        conn.close()
    assert offer_row is not None
    assert capsule_row is not None


def test_loopback_skips_order_book_when_local_ingest_fails(monkeypatch) -> None:
    run_migrations()
    rate_limiter.reset_peer(local_peer_id())
    fake_book = _FakeOrderBook()
    _patch_loopback_env(monkeypatch, fake_book)
    monkeypatch.setattr(
        "network.assist_router.handle_incoming_assist_message",
        lambda **kwargs: RouteResult(False, "Envelope rejected: test", []),
    )

    _raw, offer, _capsule = build_signed_task_offer_message()
    sent = broadcast_task_offer(
        offer_payload=offer.model_dump(mode="json"),
        required_capabilities=["research"],
        limit=3,
    )

    assert sent == 0
    assert fake_book.pushed == []


def test_request_specific_shard_falls_back_to_second_endpoint(monkeypatch) -> None:
    attempts: list[tuple[str, int]] = []

    def _send(host: str, port: int, payload: bytes) -> bool:
        attempts.append((host, port))
        return (host, port) == ("198.51.100.51", 49151)

    monkeypatch.setattr(
        "retrieval.swarm_query.delivery_endpoints_for_peer",
        lambda peer_id, **kwargs: [("198.51.100.50", 49150), ("198.51.100.51", 49151)],
    )
    monkeypatch.setattr("retrieval.swarm_query.send_message", _send)
    monkeypatch.setattr("retrieval.swarm_query.audit_logger.log", lambda *args, **kwargs: None)

    ok = request_specific_shard(peer_id="peer-remote", query_id="query-1", shard_id="shard-1")

    assert ok is True
    assert attempts == [("198.51.100.50", 49150), ("198.51.100.51", 49151)]
