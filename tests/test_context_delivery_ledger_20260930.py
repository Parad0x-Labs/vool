"""Exact-closure context delivery ledger — fresh acceptance (2026-09-30).

The receipt must measure the EXACT sealed payload (same canonical bytes the
permit hash binds), reconcile against the chain's estimate, and the
fail-closed law must refuse pre-send when the exact payload provably exceeds
a resolved window envelope — using the char-class estimator's own bound,
never chars/4 (which under-reads CJK and digit runs 3-4.6x).
"""
from __future__ import annotations

import json

import pytest

from core.context_delivery_ledger import (
    ENFORCE_FLAG,
    ProviderPayloadEnvelopeExceededError,
    build_receipt,
    enforcement_enabled,
    measure_provider_payload,
)
from core.provider_invocation_gateway import canonical_payload_bytes, payload_hash


def _payload(messages, num_ctx=0, tools=None, **extra):
    options = {"num_ctx": num_ctx} if num_ctx else {}
    p = {"model": "m", "messages": messages, "options": options}
    if tools:
        p["tools"] = tools
    p.update(extra)
    return p


def test_exact_bytes_match_canonical_serialization():
    p = _payload([{"role": "system", "content": "níght — 日本語"},
                  {"role": "user", "content": "hello"}], num_ctx=4096)
    m = measure_provider_payload(p)
    canonical = canonical_payload_bytes(p)
    assert m["payload_bytes"] == len(canonical)
    assert m["payload_chars"] == len(canonical.decode("utf-8"))
    assert m["payload_hash"] == payload_hash(p)


def test_envelope_resolution_sources():
    # wire envelope wins
    m = measure_provider_payload(
        _payload([{"role": "user", "content": "x"}], num_ctx=2048),
        metadata_num_ctx=4096)
    assert m["envelope"]["num_ctx"] == 2048
    assert m["envelope"]["source"] == "payload.options.num_ctx"
    # metadata fallback
    m2 = measure_provider_payload(
        _payload([{"role": "user", "content": "x"}]),
        metadata_num_ctx=4096)
    assert m2["envelope"]["num_ctx"] == 4096
    assert m2["envelope"]["source"] == "metadata.prompt_budget"
    # unresolved -> receipt-only, never refused
    m3 = measure_provider_payload(
        _payload([{"role": "user", "content": "x" * 9000}]))
    assert m3["envelope"]["num_ctx"] == 0
    assert m3["over_envelope"] is False


def test_tool_schemas_count_toward_the_estimate():
    huge_tool = {"type": "function", "function": {
        "name": "t", "description": "d " * 400}}
    base = measure_provider_payload(
        _payload([{"role": "user", "content": "hi"}], num_ctx=4096))
    with_tools = measure_provider_payload(
        _payload([{"role": "user", "content": "hi"}], num_ctx=4096,
                 tools=[huge_tool]))
    assert (with_tools["estimated_input_tokens"]
            > base["estimated_input_tokens"] + 100)


def test_cjk_over_envelope_detected_where_chars_over_four_passes():
    # 8000 Han chars: chars/4 claims ~2006 (fits 4096) but the char-class
    # estimator correctly reads it as over a small envelope
    p = _payload([{"role": "user", "content": "夜" * 800}], num_ctx=512)
    m = measure_provider_payload(p)
    assert m["payload_chars"] < 512 * 4  # the chars/4 law would PASS…
    assert m["over_envelope"] is True    # …the exact law refuses


def test_receipt_reconciles_against_fitter_debit():
    m = measure_provider_payload(
        _payload([{"role": "user", "content": "hello world"}], num_ctx=4096))
    r = build_receipt(
        m, request_id="r1", prompt_budget={
            "estimated_prompt_tokens_after": 10, "num_ctx": 4096})
    assert r["debits_source"] == "fitter"
    assert r["sum_of_estimated_token_debits"] == 10
    assert r["closure_error_tokens_est"] == (
        m["estimated_input_tokens"] - 10)
    assert r["schema"] == "vool.context_delivery_ledger.v1"
    # no prompt text anywhere in the receipt
    blob = json.dumps(r)
    assert "hello world" not in blob


def test_enforcement_flag_gates_the_refusal(monkeypatch):
    p = _payload([{"role": "user", "content": "夜" * 800}], num_ctx=512)
    m = measure_provider_payload(p)
    monkeypatch.delenv(ENFORCE_FLAG, raising=False)
    assert enforcement_enabled() is False
    from core.context_delivery_ledger import maybe_refuse
    maybe_refuse(m)  # default-off: no raise
    monkeypatch.setenv(ENFORCE_FLAG, "1")
    assert enforcement_enabled() is True
    with pytest.raises(ProviderPayloadEnvelopeExceededError):
        maybe_refuse(m)


def test_refusal_is_a_prompt_budget_error_for_router_routing():
    from core.prompt_budget import PromptBudgetExceededError
    assert issubclass(
        ProviderPayloadEnvelopeExceededError, PromptBudgetExceededError)
