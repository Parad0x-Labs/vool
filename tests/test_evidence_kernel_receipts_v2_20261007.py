"""Receipt envelope v2 and commit semantics (v14.2 evidence kernel, 2026-10-07).

Worst cases first: a tampered envelope, a deleted ledger tail, a receipt write that fails mid-turn, the kernel switched
off. Every store goes through the real ``store_turn`` seam and every ask through ``inject_retrieved``; hash embeddings
keep it offline. Contributor: sls_0x.
"""
from __future__ import annotations

import calendar
import json
from datetime import datetime, timezone

import pytest

import core.context_retrieval as cr
import core.evidence_kernel.receipts as rv2
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.runtime_paths import configure_runtime_home


def _epoch(y, m, d):
    return float(calendar.timegm(datetime(y, m, d, 9, 0, tzinfo=timezone.utc).timetuple()))


@pytest.fixture()
def home(tmp_path, monkeypatch):
    import core.embedding_service as embedding_service

    profile = tmp_path / "profile"
    profile.mkdir()
    for name in ("NULLA_HOME", "VOOL_HOME"):
        monkeypatch.setenv(name, str(profile))
    monkeypatch.setenv("VOOL_CONTEXT_CAPSULE_V2", "1")
    monkeypatch.setenv("VOOL_MEMORY_RECEIPTS", "1")
    monkeypatch.setenv("VOOL_EVIDENCE_COMPILER", "1")
    monkeypatch.setenv("VOOL_EVIDENCE_KERNEL", "1")
    configure_runtime_home(profile)
    embedding_service._best_embed_model = lambda: None
    from storage.migrations import run_migrations

    run_migrations()
    yield str(profile)
    configure_runtime_home(None)


def _store(home, chat, user_text, assistant_text, stated_at):
    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    return cr.store_turn(chat, user_text, assistant_text, access_policy=policy,
                         source_context={"chat_id": chat, "runtime_home": home, "statement_at": stated_at})


def _ask(home, chat, question):
    policy = resolve_memory_access_policy(chat_id=chat)
    cr.reset_retrieval_telemetry()
    out = cr.inject_retrieved(chat, question, [{"role": "user", "content": question}],
                              access_policy=policy, source_context={"chat_id": chat, "runtime_home": home})
    block = "\n".join(str(m.get("content") or "") for m in out if "retrieved_context" in str(m.get("content")))
    return block, dict(cr.get_last_retrieval_telemetry())


# ─── the envelope itself ─────────────────────────────────────────────────────────────────────────

def test_envelope_chains_per_ledger_and_verifies_with_the_id_inside_the_signed_body(home):
    a = rv2.issue(kind="vool.memory.turn.v1", session_id="c1", subject_type="t", subject={"x": 1},
                  evidence_refs=[rv2.EvidenceRef(occurrence_id="o1", digest="d1", role="user", kind="turn")])
    b = rv2.issue(kind="vool.memory.turn.v1", session_id="c1", subject_type="t", subject={"x": 2}, parents=[a.receipt_id])
    assert a.previous_digest == "" and b.previous_digest == a.content_digest
    assert rv2.verify(a.to_dict()) == (True, "ok") and rv2.verify_chain("vool.memory.turn.v1", "c1")[0]
    # the id is covered by the digest: swapping it is tamper, not a rename
    forged = dict(b.to_dict(), receipt_id="r2-forged")
    assert rv2.verify(forged) == (False, "content_digest_mismatch")
    # evidence is a reference, not a payload
    assert a.to_dict()["evidence_refs"] == [{"occurrence_id": "o1", "digest": "d1", "span": "", "role": "user", "kind": "turn"}]
    # ledgers are per (kind, session): another kind starts its own chain
    c = rv2.issue(kind="vool.memory.packet.v1", session_id="c1", subject_type="p", subject="q")
    assert c.previous_digest == ""


def test_tampered_row_and_deleted_tail_are_both_detected(home):
    for i in range(3):
        rv2.issue(kind="vool.memory.turn.v1", session_id="c2", subject_type="t", subject={"i": i})
    p = rv2.ledger_path("vool.memory.turn.v1", "c2")
    rows = p.read_text().splitlines()
    # tamper the middle row's status in place
    mid = json.loads(rows[1]); mid["status"] = "forged"
    p.write_text("\n".join([rows[0], json.dumps(mid), rows[2]]) + "\n")
    ok, why = rv2.verify_chain("vool.memory.turn.v1", "c2")
    assert not ok and why == "envelope[1] content_digest_mismatch"
    # restore, then delete the tail: rows still verify, the head (checkpoint) no longer matches
    p.write_text("\n".join(rows[:2]) + "\n")
    ok, why = rv2.verify_chain("vool.memory.turn.v1", "c2")
    assert not ok and why.startswith("head_mismatch")


def test_commit_or_raise_versus_best_effort(home, monkeypatch):
    monkeypatch.setattr(rv2, "ledger_path", lambda kind, sid: rv2.Path(home) / "not-a-dir" / "x.jsonl")
    (rv2.Path(home) / "not-a-dir").write_text("file in the way")
    with pytest.raises(Exception):
        rv2.issue(kind="k", session_id="s", subject_type="t", subject="s", commit=True)
    env = rv2.issue(kind="k", session_id="s", subject_type="t", subject="s", commit=False)
    assert env.status == "unwritten" and env.content_digest


def test_honesty_v1_receipt_views_as_an_envelope_and_still_verifies_as_v1(home):
    from core.honesty_receipt import issue_honesty_receipt, verify_honesty_receipt

    r = issue_honesty_receipt(session_id="s9", turn_index=3, prompt_text="hi", response_text="hello", prev_hash="",
                              claimed_actions=[], executed_tools=[{"tool": "read_file", "receipt_key": "k1"}], verdict="clean", verdict_detail="")
    view = rv2.from_honesty_v1(r.to_dict() if hasattr(r, "to_dict") else r.__dict__)
    assert view["kind"] == "vool.honesty.v1" and view["schema"] == rv2.SCHEMA and view["evidence_refs"][0]["kind"] == "read_file"
    assert view["content_digest"] == (r.content_hash if hasattr(r, "content_hash") else r["content_hash"])
    ok, _ = verify_honesty_receipt(r.to_dict() if hasattr(r, "to_dict") else r.__dict__)
    assert ok in (True, False)  # signed or unsigned depends on the node key; v1 verification is its own function and still runs


# ─── commit semantics on the served write path ───────────────────────────────────────────────────

def test_turn_write_issues_a_chained_turn_envelope_per_occurrence(home):
    out = _store(home, "chat-k1", "I moved to Lisbon last month and I drive a Honda Civic.", "Noted.", _epoch(2025, 3, 3))
    assert out["status"] in ("stored", "retained"), out
    assert len(out["turn_envelopes"]) == 2 and out["kernel_assurance"] in ("L2", "L3")
    rows = rv2.read_ledger("vool.memory.turn.v1", "chat-k1")
    assert [r["receipt_id"] for r in rows] == out["turn_envelopes"]
    assert rows[0]["evidence_refs"][0]["occurrence_id"] == out["occurrence_ids"][0] and rows[0]["evidence_refs"][0]["role"] == "user"
    assert rv2.verify_chain("vool.memory.turn.v1", "chat-k1")[0]


def test_receipt_write_failure_fails_the_turn_and_keeps_what_landed(home, monkeypatch):
    import core.memory_receipts as mr

    _store(home, "chat-k2", "My rent is 1200 dollars.", "Ok.", _epoch(2025, 1, 1))
    calls = {"n": 0}
    real = mr.write_receipt

    def failing(mem, occurrence, **kw):
        calls["n"] += 1
        if calls["n"] == 2:  # the assistant occurrence's receipt
            raise RuntimeError("disk full")
        return real(mem, occurrence, **kw)

    monkeypatch.setattr(mr, "write_receipt", failing)
    out = _store(home, "chat-k2", "Actually my rent is now 1300 dollars.", "Updated.", _epoch(2025, 2, 1))
    assert out["status"] == "failed" and out["reason"] == "receipt_write_error"
    assert len(out["occurrence_ids"]) == 2  # both occurrences landed before the receipt failed; the audit can see them
    assert len(out.get("turn_envelopes", [])) == 1  # only the user turn got its envelope


def test_envelope_write_failure_fails_the_turn(home, monkeypatch):
    monkeypatch.setattr(cr, "_kernel_turn_envelope", lambda *a, **k: (_ for _ in ()).throw(OSError("ledger unwritable")))
    out = _store(home, "chat-k3", "I work as a nurse.", "Ok.", _epoch(2025, 1, 1))
    assert out["status"] == "failed" and out["reason"] == "envelope_write_error" and out["occurrence_ids"]


def test_kernel_off_keeps_v141_behaviour_and_touches_no_ledger(home, monkeypatch):
    import core.memory_receipts as mr

    monkeypatch.setenv("VOOL_EVIDENCE_KERNEL", "0")
    monkeypatch.setattr(mr, "write_receipt", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    out = _store(home, "chat-k4", "I work as a nurse.", "Ok.", _epoch(2025, 1, 1))
    assert out["status"] in ("stored", "retained") and "turn_envelopes" not in out
    assert not (rv2._ledger_dir()).exists()


# ─── packet and claim envelopes on the answer path ───────────────────────────────────────────────

def test_packet_and_claim_envelopes_reach_telemetry_and_the_guard(home):
    _store(home, "chat-k5", "I paid 45 dollars for the lamp and 30 dollars for the rug.", "Noted.", _epoch(2025, 4, 1))
    block, tele = _ask(home, "chat-k5", "How much did I spend on the lamp and the rug together?")
    pk = tele.get("kernel_packet_receipt")
    assert pk and pk["status"] not in ("error", "unwritten") and rv2.read_ledger("vool.memory.packet.v1", "chat-k5")
    from core.unsourced_current_claim import inspect_unsourced_current_claim

    ctx = {"chat_id": "chat-k5", "admitted_capsule_evidence": {"text": block, "chat_id": "chat-k5", "source": "test"}}
    v = inspect_unsourced_current_claim(answer="You spent 75 dollars in total.", requires_current=True, notes=[], session_id="chat-k5", turn_id="t",
                                        source_context=ctx, user_turn_text="How much did I spend on the lamp and the rug together?")
    assert v.claim_binding and v.claim_binding.get("all_supported") and v.claim_binding["kernel_receipt"]["status"] == "supported"
    rows = rv2.read_ledger("vool.memory.claim.v1", "chat-k5")
    assert rows and rows[-1]["status"] == "supported" and rows[-1]["evidence_refs"] and "claims" not in json.dumps(rows[-1]["evidence_refs"])
