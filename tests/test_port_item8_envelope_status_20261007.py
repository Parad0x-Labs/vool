"""Port of v14.6 item 8 beyond the compiler (core/unsourced_current_claim.py, core/agent_runtime/turn_reasoning.py).

The B series carried item 8's compiler hunk only: the claim envelope still said "unsupported" for a contradicted
and for a qualified binding, and the temporal decision carried no supported / chain_stage beside verified. Each
test here names the hunk whose revert fails it.
"""
from __future__ import annotations

from types import SimpleNamespace

import core.evidence_kernel.receipts as receipts
import core.evidence_kernel.temporal_binder as temporal_binder
import core.unsourced_current_claim as ucc
from core.agent_runtime import turn_reasoning


def _captured_status(monkeypatch, binding: dict) -> str:
    seen: dict = {}

    def fake_issue(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(receipt_id="r:test", status=kwargs.get("status"), assurance="test")

    monkeypatch.setattr(receipts, "issue", fake_issue)
    monkeypatch.setattr(receipts, "kernel_enabled", lambda *a, **k: True)
    out = ucc._kernel_claim_envelope("chat-x", "How much in total?", "$500 in total.", binding, [])
    assert out and out.get("status") == seen.get("status"), out
    return str(seen.get("status"))


def test_a_contradicted_binding_issues_a_contradicted_envelope(monkeypatch):
    binding = {"attempted": True, "all_supported": False, "contradicted": True, "qualifiable": False, "claims": []}
    assert _captured_status(monkeypatch, binding) == "contradicted"


def test_a_qualifiable_binding_issues_a_qualified_envelope(monkeypatch):
    binding = {"attempted": True, "all_supported": False, "contradicted": False, "qualifiable": True, "claims": []}
    assert _captured_status(monkeypatch, binding) == "qualified"


def test_a_supported_and_an_unattempted_binding_keep_their_status(monkeypatch):
    assert _captured_status(monkeypatch, {"attempted": True, "all_supported": True, "claims": []}) == "supported"
    assert _captured_status(monkeypatch, {"attempted": False, "claims": []}) == "not_attempted"


def test_the_temporal_decision_carries_supported_and_the_chain_stage(monkeypatch):
    class _Binding:
        attempted = True
        restored = True
        text = "about 12 days"

        def as_dict(self):
            return {"attempted": True}

    monkeypatch.setattr(receipts, "kernel_enabled", lambda *a, **k: True)
    monkeypatch.setattr(temporal_binder, "bind_temporal_claims", lambda *a, **k: _Binding())
    decision = turn_reasoning._kernel_temporal_decision("How long between the trips?", "about 12 days", [{}], None)
    assert decision is not None, decision
    assert decision["verified"] is True and decision["supported"] is True
    assert decision["chain_stage"] == "SUPPORTED"
    assert decision["rule"] == "clause_level_binding"
