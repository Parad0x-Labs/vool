"""The proof projection names what it covers: conductor observations, unique sources, derived steps.

Measured 2026-09-06 on the delivered 0.5.0 build: a turn whose answer cited Yahoo Finance and
CoinGecko rendered `0 actions · 0 sources · VERIFIED`, byte-identical to a no-retrieval chat.
The projection read sources only from `web_retrieval_completed`/`fx_retrieval_completed` receipts
and actions only from execution facts; the conductor's `agent_node_completed` rows (values,
source, ok/failed, dependencies) were never joined, and the state word certified bytes alone.

These tests drive the REAL store rows the runtime writes (`emit_runtime_event`, the conversation
log binding, an `a7_finalizations` row) and read `build_turn_proof` through the same projection
the `/api/chat/proof` route serves.
"""
from __future__ import annotations

import pytest

from core.proof_projection import STATE_INCOMPLETE, STATE_VERIFIED, build_turn_proof
from tests.test_proof_projection import _bind_turn, _emit_turn_event, _insert_finalization


def _node(session: str, turn: str, request: str, node_id: str, operation: str, *, ok: bool = True,
          host: str = "", url: str = "", depends_on: list[str] | None = None, failure_code: str = "",
          duration_s: float = 0.4, cached: bool | None = None, values: str = "price=1") -> None:
    detail: dict = {
        "schema": "agent_node_completed_v1",
        "lane": "conductor",
        "node_id": node_id,
        "operation": operation,
        "state": "succeeded" if ok else "failed",
        "ok": ok,
        "failure_code": failure_code if not ok else "",
        "failure_reason": "" if ok else "transport_unreachable",
        "depends_on": list(depends_on or []),
        "duration_s": duration_s,
    }
    if ok and (host or url or values):
        observed = {"values": values}
        if url:
            observed["source_url"] = url
        if host:
            observed["origin_domain"] = host
        detail["observed"] = observed
    if cached is not None:
        detail["cached"] = cached
    _emit_turn_event(session, turn, request, "agent_node_completed", detail)


def _served(session: str, request: str, turn: str, answer: str = "Gold and BTC.") -> None:
    _bind_turn(session, request, answer)
    _insert_finalization(request, turn, answer)


@pytest.mark.parametrize("tail", ["| Total | **$10,000.", "| Warehouse Z | 19."])
def test_incomplete_table_disclosure_cannot_be_verified(tail):
    from core.incomplete_answer import inspect_answer_completeness, partial_answer_notice

    answer = "| Item | Cost | Units |\n| --- | --- | --- |\n" + tail
    answer += "\n\n" + partial_answer_notice(inspect_answer_completeness(answer))
    suffix = "total" if "Total" in tail else "warehouse"
    session, request, turn = f"cut-table-sess-{suffix}", f"cut-table-req-{suffix}", f"cut-table-turn-{suffix}"
    _served(session, request, turn, answer)
    proof = build_turn_proof(session_id=session, request_id=request)
    assert proof["compact"]["state"] == STATE_INCOMPLETE


def test_complete_table_with_prose_remains_verifiable():
    answer = "| Item | Units |\n| --- | --- |\n| Warehouse Q | 27 |\n\nAll stock is accounted for."
    _served("whole-table-sess", "whole-table-req", "whole-table-turn", answer)
    proof = build_turn_proof(session_id="whole-table-sess", request_id="whole-table-req")
    assert proof["compact"]["state"] == STATE_VERIFIED


def test_multi_source_arithmetic_counts_nodes_unique_sources_and_bound_derivation() -> None:
    session, request, turn = "cov-sess-1", "cov-req-1", "cov-turn-1"
    _served(session, request, turn)
    _node(session, turn, request, "btc", "market_quote", host="api.coingecko.com", url="https://www.coingecko.com/en/coins/bitcoin")
    _node(session, turn, request, "gold", "market_quote", host="query1.finance.yahoo.com", url="https://finance.yahoo.com/quote/GC=F")
    _node(session, turn, request, "silver", "market_quote", host="query1.finance.yahoo.com", url="https://finance.yahoo.com/quote/SI=F")
    _node(session, turn, request, "ratio", "quantitative_reasoning", depends_on=["btc", "gold"], values="ratio=17.8")
    proof = build_turn_proof(session_id=session, request_id=request)
    compact = proof["compact"]
    # Four nodes ran; nothing else. Each node is one action the runtime performed.
    assert compact["actions"] == 4
    # Two DISTINCT hosts were read (Yahoo twice, CoinGecko once): sources are unique identities.
    assert compact["sources"] == 2
    coverage = compact["coverage"]
    assert coverage["observations"] == {"total": 3, "succeeded": 3, "failed": 0, "cached": 0, "pending": 0}
    assert coverage["derived"] == {"total": 1, "bound": 1, "unbound": 0, "failed": 0}
    assert coverage["label"] == "3/3 lookups"
    assert compact["state"] == STATE_VERIFIED
    hosts = sorted({row["host"] for row in proof["expanded"]["sources"]})
    assert hosts == ["api.coingecko.com", "query1.finance.yahoo.com"]
    observations = proof["expanded"]["observations"]
    assert [row["node_id"] for row in observations] == ["btc", "gold", "silver", "ratio"]


def test_one_failed_dependency_is_named_and_unbinds_the_derived_step() -> None:
    session, request, turn = "cov-sess-2", "cov-req-2", "cov-turn-2"
    _served(session, request, turn)
    _node(session, turn, request, "btc", "market_quote", host="api.coingecko.com")
    _node(session, turn, request, "gold", "market_quote", host="query1.finance.yahoo.com")
    _node(session, turn, request, "silver", "market_quote", ok=False, failure_code="transport_failed")
    _node(session, turn, request, "grams", "quantitative_reasoning", depends_on=["btc", "silver"], values="")
    proof = build_turn_proof(session_id=session, request_id=request)
    coverage = proof["compact"]["coverage"]
    assert coverage["observations"] == {"total": 3, "succeeded": 2, "failed": 1, "cached": 0, "pending": 0}
    assert coverage["derived"] == {"total": 1, "bound": 0, "unbound": 1, "failed": 0}
    assert coverage["label"] == "1 of 3 lookups failed · 1 derived step unbound"
    # A failed lookup is not a source.
    assert proof["compact"]["sources"] == 2
    failed = [row for row in proof["expanded"]["observations"] if not row["ok"]]
    assert failed and failed[0]["node_id"] == "silver" and failed[0]["failure_code"] == "transport_failed"


def test_a_chat_with_no_lookups_says_so() -> None:
    session, request, turn = "cov-sess-3", "cov-req-3", "cov-turn-3"
    _served(session, request, turn, answer="Hello.")
    proof = build_turn_proof(session_id=session, request_id=request)
    compact = proof["compact"]
    assert compact["actions"] == 0 and compact["sources"] == 0
    assert compact["coverage"]["label"] == "no lookups"
    assert compact["coverage"]["observations"]["total"] == 0


def test_repeated_reads_of_one_source_count_one_source_and_every_lookup() -> None:
    session, request, turn = "cov-sess-4", "cov-req-4", "cov-turn-4"
    _served(session, request, turn)
    _node(session, turn, request, "btc", "market_quote", host="api.coingecko.com")
    _node(session, turn, request, "eth", "market_quote", host="api.coingecko.com")
    proof = build_turn_proof(session_id=session, request_id=request)
    assert proof["compact"]["sources"] == 1
    assert proof["compact"]["coverage"]["label"] == "2/2 lookups"
    assert proof["compact"]["actions"] == 2


def test_a_derived_step_whose_operand_is_not_a_node_of_this_turn_is_unbound() -> None:
    """Negative control for operand binding: the arithmetic names a dependency no lookup of this
    turn produced. Correct quotes must not confer coverage on a calculation they do not feed."""
    session, request, turn = "cov-sess-5", "cov-req-5", "cov-turn-5"
    _served(session, request, turn)
    _node(session, turn, request, "btc", "market_quote", host="api.coingecko.com")
    _node(session, turn, request, "silver", "market_quote", host="query1.finance.yahoo.com")
    _node(session, turn, request, "gold_amount", "quantitative_reasoning", depends_on=["btc", "gold"], values="oz=17.8")
    proof = build_turn_proof(session_id=session, request_id=request)
    coverage = proof["compact"]["coverage"]
    assert coverage["observations"]["succeeded"] == 2
    assert coverage["derived"] == {"total": 1, "bound": 0, "unbound": 1, "failed": 0}
    assert coverage["label"] == "2/2 lookups · 1 derived step unbound"


def test_another_requests_lookups_never_count_for_this_one() -> None:
    session = "cov-sess-6"
    _served(session, "cov-req-6a", "cov-turn-6a", answer="First.")
    _served(session, "cov-req-6b", "cov-turn-6b", answer="Second.")
    _node(session, "cov-turn-6a", "cov-req-6a", "btc", "market_quote", host="api.coingecko.com")
    _node(session, "cov-turn-6a", "cov-req-6a", "gold", "market_quote", host="query1.finance.yahoo.com")
    second = build_turn_proof(session_id=session, request_id="cov-req-6b")
    assert second["compact"]["sources"] == 0
    assert second["compact"]["coverage"]["label"] == "no lookups"
    first = build_turn_proof(session_id=session, request_id="cov-req-6a")
    assert first["compact"]["sources"] == 2


def test_a_lookup_that_started_and_never_completed_leaves_the_turn_incomplete() -> None:
    session, request, turn = "cov-sess-7", "cov-req-7", "cov-turn-7"
    _served(session, request, turn)
    _node(session, turn, request, "btc", "market_quote", host="api.coingecko.com")
    _emit_turn_event(session, turn, request, "agent_node_started", {
        "schema": "agent_node_started_v1", "lane": "conductor", "node_id": "gold", "operation": "market_quote",
    })
    proof = build_turn_proof(session_id=session, request_id=request)
    coverage = proof["compact"]["coverage"]
    assert coverage["observations"]["pending"] == 1
    assert coverage["label"] == "1 of 2 lookups pending"
    assert proof["state"] == STATE_INCOMPLETE
    assert "missing_terminal" in proof["state_reasons"]


def test_a_lookup_the_fact_ledger_already_holds_is_counted_once() -> None:
    """The live-data lane files one execution fact per subtask AND emits the node completion;
    the same lookup must not be two actions. The union is keyed on the subtask id the fact carries."""
    session, request, turn = "cov-sess-8", "cov-req-8", "cov-turn-8"
    _served(session, request, turn)
    node_id = "livedata-abc:market:gold"
    _emit_turn_event(session, turn, request, "tool_selected", {"tool_name": "live_data_market_quote", "subtask_id": node_id})
    _emit_turn_event(session, turn, request, "tool_executed", {"tool_name": "live_data_market_quote", "subtask_id": node_id, "ok": True})
    _node(session, turn, request, node_id, "market_quote", host="query1.finance.yahoo.com")
    _node(session, turn, request, "conductor-only", "market_quote", host="api.coingecko.com")
    proof = build_turn_proof(session_id=session, request_id=request)
    fact_names = [row["name"] for row in proof["expanded"]["actions"]]
    assert fact_names, "the tool_executed row must yield an execution fact"
    # one fact (gold, also a node) + one node the ledger does not hold (conductor-only) = 2 actions
    assert proof["compact"]["actions"] == 2, (proof["compact"], fact_names)
    assert proof["compact"]["coverage"]["label"] == "2/2 lookups"


def test_two_hosts_of_one_organisation_are_one_source() -> None:
    """The receipt names the API endpoint, the node names the human page: one source (Yahoo)."""
    session, request, turn = "cov-sess-9", "cov-req-9", "cov-turn-9"
    _served(session, request, turn)
    _node(session, turn, request, "gold", "market_quote", host="finance.yahoo.com", url="https://finance.yahoo.com/quote/GC=F")
    _emit_turn_event(session, turn, request, "web_retrieval_completed", {
        "plan_id": "p1",
        "receipts": [{"schema": "vool.web_retrieval_receipt.v1", "action": "market_quote", "status": "available",
                      "source_domains": ["query1.finance.yahoo.com"], "subtask_id": "gold"}],
    })
    proof = build_turn_proof(session_id=session, request_id=request)
    assert proof["compact"]["sources"] == 1, proof["expanded"]["sources"]
    assert len(proof["expanded"]["sources"]) == 2, "both records stay visible in the expanded view"


def test_a_hostless_receipt_of_a_node_this_turn_ran_adds_no_second_source() -> None:
    session, request, turn = "cov-sess-10", "cov-req-10", "cov-turn-10"
    _served(session, request, turn)
    _node(session, turn, request, "btc", "market_quote", host="www.coingecko.com")
    _emit_turn_event(session, turn, request, "web_retrieval_completed", {
        "plan_id": "p1",
        "receipts": [{"schema": "vool.web_retrieval_receipt.v1", "action": "market_quote", "status": "available",
                      "source_domains": [], "subtask_id": "btc"}],
    })
    proof = build_turn_proof(session_id=session, request_id=request)
    assert proof["compact"]["sources"] == 1
    # A hostless receipt that names NO node of this turn still counts once (legacy rows).
    _emit_turn_event(session, turn, request, "web_retrieval_completed", {
        "plan_id": "p2",
        "receipts": [{"schema": "vool.web_retrieval_receipt.v1", "action": "web_search", "status": "available", "source_domains": []}],
    })
    proof = build_turn_proof(session_id=session, request_id=request)
    assert proof["compact"]["sources"] == 2


@pytest.mark.parametrize("status", ["failed", "blocked", "partially_fulfilled", "cancelled"])
def test_terminal_fulfillment_receipt_prevents_verified_even_with_intact_bytes(status):
    session, request, turn = (f"terminal-proof-{part}-{status}" for part in ("session", "request", "turn"))
    _served(session, request, turn, "The selected model could not answer.")
    _emit_turn_event(session, turn, request, "turn.trace_completed", {
        "fulfillment_outcome": {"fulfillment_status": status, "failure_stage": "provider_execution"},
    })
    proof = build_turn_proof(session_id=session, request_id=request)
    assert proof["state"] == STATE_INCOMPLETE
    assert "terminal_task_unfulfilled" in proof["state_reasons"]
