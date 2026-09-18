"""THE PUBLIC-WRITE DOOR — budgets at the one public frontdoor.

`create_topic_record` / `create_post_record` are where every durable public
write is admitted; the reservation rides them: RESERVED before any store row
moves (an idempotent replay reserves nothing), CONSUMED immediately before
the durable mutation, terminal reconciled from the real outcome. Refusal
raises the door's own admission-blocked vocabulary carrying the typed code —
and ZERO store calls happen.
"""
from __future__ import annotations

import threading

import pytest

from core import effect_budget as eb
from tests.effect_budget.conftest import *  # noqa: F401,F403 — fixtures


class _Service:
    """The service surface the frontdoor needs, stubbed at its own seams."""

    def __init__(self) -> None:
        self.store_calls: list[str] = []

    def _cached_result(self, key, model):
        return None

    def _store_idempotent_result(self, key, lane, record):
        self.store_calls.append(f"idem:{lane}")

    def _visibility_requires_public_guard(self, visibility):
        return False

    def _forced_review_decision(self, moderation):
        return moderation

    def get_topic(self, topic_id, *, include_flagged=False):
        class _Topic:
            visibility = "agent_public"

        return _Topic()

    def _display_fields(self, agent_id):
        return None, None


@pytest.fixture()
def frontdoor(monkeypatch):
    import core.brain_hive_topic_post_frontdoor as fd

    class _Moderation:
        state = "approved"
        score = 0.0
        reasons: list = []
        metadata: dict = {}

    monkeypatch.setattr(fd, "moderate_topic_submission", lambda r: _Moderation())
    monkeypatch.setattr(fd, "moderate_post_submission", lambda r: _Moderation())
    monkeypatch.setattr(fd, "apply_topic_moderation", lambda **k: None)
    monkeypatch.setattr(fd, "apply_post_moderation", lambda **k: None)

    def _topic(**kwargs):
        SERVICE.store_calls.append("create_topic")
        return "topic-1"

    def _post(**kwargs):
        SERVICE.store_calls.append("create_post")
        return "post-1"

    SERVICE = _Service()
    monkeypatch.setattr(fd, "create_topic", _topic)
    monkeypatch.setattr(fd, "create_post", _post)
    monkeypatch.setattr(
        fd,
        "get_topic_record",
        lambda service, topic_id, include_flagged=False: object(),
    )
    return fd, SERVICE


def _topic_request(title="budget door topic"):
    from core.brain_hive_models import HiveTopicCreateRequest

    return HiveTopicCreateRequest(
        created_by_agent_id="agent-0000000000000001",
        title=title,
        summary="a plain summary with no governed bytes",
        topic_tags=[],
        idempotency_key="idem-topic-1",
    )


def _post_request():
    from core.brain_hive_models import HivePostCreateRequest

    return HivePostCreateRequest(
        topic_id="topic-00000000000001",
        author_agent_id="agent-0000000000000002",
        body="a plain public post with no governed bytes",
        idempotency_key="idem-post-1",
    )


def test_exhausted_budget_means_zero_public_writes(frontdoor, set_budget):
    fd, service = frontdoor
    set_budget(("public_write", eb.SCOPE_PROJECT, 1))
    fd.create_topic_record(service, _topic_request())  # the one allowed write
    assert "create_topic" in service.store_calls
    with pytest.raises(ValueError, match="Brain Hive admission blocked"):
        fd.create_topic_record(service, _topic_request("second topic"))
    assert service.store_calls.count("create_topic") == 1, (
        "ZERO additional store calls: the second write never happened"
    )
    with pytest.raises(ValueError, match="Brain Hive admission blocked"):
        fd.create_post_record(service, _post_request())
    assert "create_post" not in service.store_calls, (
        "the post door shares the one budget: zero post writes"
    )
    refusals = eb.budget_events("refused")
    assert all(event["budget_class"] == "public_write" for event in refusals)
    assert len(refusals) == 2


def test_refusal_carries_the_typed_code(frontdoor, set_budget):
    fd, service = frontdoor
    set_budget(("public_write", eb.SCOPE_PROJECT, 0))
    with pytest.raises(ValueError) as refusal:
        fd.create_topic_record(service, _topic_request())
    assert "EFFECT_BUDGET_EXCEEDED" in str(refusal.value)
    assert "public_write/project" in str(refusal.value)


def test_success_consumes_and_terminalizes(frontdoor, set_budget):
    fd, service = frontdoor
    set_budget(("public_write", eb.SCOPE_PROJECT, 2))
    fd.create_topic_record(service, _topic_request())
    rows = eb.reservation_rows()
    assert len(rows) == 1 and rows[0]["state"] == eb.RESERVATION_CONSUMED
    assert rows[0]["budget_class"] == "public_write"


def test_mid_write_failure_terminalizes_failed_and_is_not_refunded(frontdoor, set_budget, monkeypatch):
    fd, service = frontdoor
    set_budget(("public_write", eb.SCOPE_PROJECT, 2))

    def _exploding_topic(**kwargs):
        service.store_calls.append("create_topic")
        raise RuntimeError("store is down")

    monkeypatch.setattr(fd, "create_topic", _exploding_topic)
    with pytest.raises(RuntimeError, match="store is down"):
        fd.create_topic_record(service, _topic_request())
    assert eb.reservation_rows()[0]["state"] == eb.RESERVATION_CONSUMED, (
        "a write that attempted and failed is not refunded"
    )
    assert eb.budget_status("public_write", project_key="default")[0].used == 1


def test_admission_refused_before_execution_releases_the_unit(frontdoor, set_budget, monkeypatch):
    """A public write refused by the A8 fence BEFORE execution: the gate
    never consumed, and the still-held reservation releases at scope close —
    the pre-execution rollback law, at this door."""
    fd, service = frontdoor
    set_budget(("public_write", eb.SCOPE_PROJECT, 1))
    monkeypatch.setattr(
        "core.finalization.writer_may_publish_public_text", lambda text: False
    )
    with pytest.raises(ValueError, match="governed"):
        fd.create_topic_record(service, _topic_request())
    rows = eb.reservation_rows()
    assert len(rows) == 1 and rows[0]["state"] == eb.RESERVATION_RELEASED, (
        "the authorized-but-never-executed write returned its unit"
    )
    assert eb.budget_status("public_write", project_key="default")[0].remaining == 1


def test_final_unit_race_through_the_frontdoor(frontdoor, set_budget):
    fd, service = frontdoor
    limit = 3
    set_budget(("public_write", eb.SCOPE_PROJECT, limit))
    results: list = []
    barrier = threading.Barrier(8)
    lock = threading.Lock()

    def worker(index: int) -> None:
        barrier.wait()
        try:
            fd.create_topic_record(service, _topic_request(f"race topic {index}"))
            outcome = "wrote"
        except ValueError as exc:
            outcome = "EFFECT_BUDGET_EXCEEDED" if "EFFECT_BUDGET_EXCEEDED" in str(exc) else str(exc)
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    wrote = [r for r in results if r == "wrote"]
    refused = [r for r in results if r == "EFFECT_BUDGET_EXCEEDED"]
    assert len(wrote) == limit, f"exactly {limit} durable public writes (got {len(wrote)})"
    assert len(refused) == 8 - limit
    assert service.store_calls.count("create_topic") == limit


def test_unbudgeted_public_writes_are_unchanged(frontdoor):
    fd, service = frontdoor
    fd.create_topic_record(service, _topic_request())
    assert "create_topic" in service.store_calls
    assert eb.reservation_rows() == []


def test_sabotage_dropped_door_guard_lets_the_write_through(frontdoor, set_budget, monkeypatch):
    """RED-PROOF: with the frontdoor's budget gate sabotaged away, an
    exhausted budget still writes publicly — exactly the hole the guard
    prevents."""
    fd, service = frontdoor
    set_budget(("public_write", eb.SCOPE_PROJECT, 1))
    fd.create_topic_record(service, _topic_request())
    with pytest.raises(ValueError, match="admission blocked"):
        fd.create_topic_record(service, _topic_request("honest refusal"))
    monkeypatch.setattr(fd, "_public_write_budget_gate", lambda *a, **k: None)
    try:
        fd.create_topic_record(service, _topic_request("smuggled write"))
        assert service.store_calls.count("create_topic") == 2, (
            "sabotage failed — the red-proof would be vacuous"
        )
    finally:
        monkeypatch.undo()
    with pytest.raises(ValueError, match="admission blocked"):
        fd.create_topic_record(service, _topic_request("restored refusal"))
    assert service.store_calls.count("create_topic") == 2
