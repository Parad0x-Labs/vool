"""Every lookup the turn recorded is a lookup the chip counts, node or not.

Measured on the built candidate 60a91da3 (isolated bundle drive, 2026-09-07): the comparison turn
read seven pages from four organisations and its chip said "4 sources · no lookups"; the operand
turn fetched gold through the live-info fast path and silver through a plan node and said
"1/1 lookup". Coverage was read from plan-node completions only. A retrieval receipt without a
node is the same kind of record -- what was fetched, from where, with what outcome -- and is
projected as an observation; a receipt that names a node the projection already holds is the
same lookup and adds nothing.
"""
from __future__ import annotations

from core.proof_projection import _projection_of_events


def _receipt(host: str, status: str, *, subtask_id: str = "", created: str = "2026-09-07T00:00:01+00:00") -> dict:
    return {
        "event_type": "web_retrieval_completed",
        "schema": "vool.web_retrieval_receipt.v1",
        "source_domains": [host],
        "status": status,
        "provider_id": "fixture",
        "action": "live_info_search",
        "subtask_id": subtask_id,
        "created_at": created,
    }


def _node(node_id: str, host: str) -> list[dict]:
    return [
        {"event_type": "agent_node_started", "node_id": node_id, "operation": "market_quote", "created_at": "2026-09-07T00:00:00+00:00"},
        {
            "event_type": "agent_node_completed",
            "node_id": node_id,
            "operation": "market_quote",
            "ok": True,
            "state": "succeeded",
            "observed": {"origin_domain": host, "value": 1.0},
            "created_at": "2026-09-07T00:00:02+00:00",
        },
    ]


def test_receipts_without_a_node_are_counted_lookups() -> None:
    projection = _projection_of_events([_receipt("autoarchive-fixture.org", "available"), _receipt("salesdata-fixture.org", "available")])
    coverage = projection["coverage"]
    assert coverage["observations"]["total"] == 2
    assert coverage["observations"]["succeeded"] == 2
    assert coverage["label"] == "2/2 lookups"
    assert projection["unique_sources"] == 2


def test_a_failed_receipt_is_a_failed_lookup() -> None:
    projection = _projection_of_events([_receipt("autoarchive-fixture.org", "available"), _receipt("motorspec-fixture.org", "failed")])
    coverage = projection["coverage"]
    assert coverage["observations"] == {"total": 2, "succeeded": 1, "failed": 1, "cached": 0, "pending": 0}
    assert coverage["label"] == "1 of 2 lookups failed"


def test_a_receipt_naming_a_held_node_is_the_same_lookup() -> None:
    node_id = "livedata-1:market:silver"
    events = [*_node(node_id, "finance.yahoo.com"), _receipt("finance.yahoo.com", "available", subtask_id=node_id)]
    projection = _projection_of_events(events)
    assert projection["coverage"]["observations"]["total"] == 1
    assert projection["coverage"]["label"] == "1/1 lookup"
    assert projection["unique_sources"] == 1


def test_a_fast_path_receipt_beside_a_node_makes_two_lookups() -> None:
    events = [*_node("livedata-1:market:silver", "finance.yahoo.com"), _receipt("finance.yahoo.com", "available")]
    projection = _projection_of_events(events)
    assert projection["coverage"]["observations"]["total"] == 2
    assert projection["coverage"]["label"] == "2/2 lookups"
    # one organisation read twice is one source
    assert projection["unique_sources"] == 1
