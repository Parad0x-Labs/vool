"""TIER 6 (harness reconciliation) — consensus block A, review-20260820-141451.

Each raw journal row stores exact answer bytes + length + hash + structured terminal
state + turn identity; review-open reconciles the digest against those bytes and
refuses to open a review that misrepresents the run. This is the fix for the gauntlet
digest lie: a review rebuilt from the journal printed "(nothing shipped)" x20 while
the real answers sat on tape ("that is how a false consensus gets born" — Grok).

Landed FIRST, as REVIEW integrity only — it does not satisfy the product gate.
"""
import hashlib
import json

import pytest

from core.kernel import consensus, repl
from core.kernel.effects import EffectJournal, EffectRunner


def _rows(path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def test_journal_row_carries_exact_answer_bytes_and_terminal_state(tmp_path, monkeypatch):
    """The journal writer stores the exact \\x01..\\x02 span verbatim (not stripped),
    its byte length and sha256, the turn identity, and the structured terminal state
    from the tape — while the human-readable transcript field stays marker-free."""
    monkeypatch.setattr(repl, "_SESSION_LOG", tmp_path / "j.jsonl")
    monkeypatch.setattr(repl, "_RUN_ID", "run-t6-a")
    tape = EffectJournal()
    tape._turn_terminal = [{"id": "ob1", "status": "committed", "lane": "reasoning"}]
    # A leading space inside the span proves EXACT bytes are kept (no strip).
    transcript = "COMMIT\n\x01 874\x02\nreceipts: 0 effects"
    repl._journal_turn("Calculate 46 * 19.", transcript, tape, mode="record", turn_index=1)

    rows = _rows(tmp_path / "j.jsonl")
    assert len(rows) == 1
    row = rows[0]
    assert row["answer_present"] is True
    assert row["answer"] == " 874"                    # EXACT bytes, leading space kept
    assert row["answer_len"] == len(b" 874")
    assert row["answer_sha256"] == hashlib.sha256(b" 874").hexdigest()
    assert row["turn_index"] == 1
    assert row["terminal_state"] == [{"id": "ob1", "status": "committed", "lane": "reasoning"}]
    assert "\x01" not in row["transcript"] and "\x02" not in row["transcript"]


def test_run_turn_stashes_structured_terminal_state_on_tape():
    """The repl.py change: run_turn attaches a per-obligation terminal summary to the
    tape (id/status/lane), with zero change to its return signature. Exercised through
    the real code path (extraction degrades without a model, but the return still runs
    and the stash must be populated)."""
    _, tape, _, _ = repl.run_turn(
        "Calculate 46 * 19.", EffectRunner(mode="record"), None, None)
    terminal = getattr(tape, "_turn_terminal", None)
    assert isinstance(terminal, list) and terminal
    for entry in terminal:
        assert set(entry) == {"id", "status", "lane"}
        assert isinstance(entry["status"], str) and entry["status"]


def test_reconstruction_from_journal_shows_real_answer_not_nothing_shipped(tmp_path):
    """THE BUG: turns rebuilt from a marker-stripped journal used to digest as
    "(nothing shipped)". With the stored answer bytes, the digest shows the run."""
    jpath = tmp_path / "j.jsonl"
    rows = []
    for i in range(1, 4):
        ans = f"the-real-answer-{i}"
        rows.append({"run_id": "run-recon", "turn_index": i, "question": f"q{i}",
                     "answer_present": True, "answer": ans,
                     "answer_len": len(ans.encode()),
                     "answer_sha256": hashlib.sha256(ans.encode()).hexdigest(),
                     "transcript": "COMMIT\nreceipts: 0 effects"})   # STRIPPED: no \x01
    jpath.write_text("\n".join(json.dumps(r) for r in rows))
    turns = [(r["question"], r["transcript"], "00:00:00", None) for r in rows]

    review = consensus.open_review(turns, "buildX", bus=tmp_path / "bus",
                                   run_id="run-recon", journal_path=str(jpath))
    ctx = (review / "FULL_REVIEW_CONTEXT.md").read_text()
    assert "(nothing shipped)" not in ctx
    for i in range(1, 4):
        assert f"the-real-answer-{i}" in ctx


def test_reconciliation_blocks_tampered_journal_hash(tmp_path):
    """A journal row whose own answer_sha256 does not match its answer is corrupt —
    review-open raises rather than reconcile against a lie."""
    jpath = tmp_path / "j.jsonl"
    ans = "real"
    jpath.write_text(json.dumps(
        {"run_id": "run-x", "turn_index": 1, "question": "q", "answer_present": True,
         "answer": ans, "answer_len": len(ans.encode()),
         "answer_sha256": "deadbeef" * 8, "transcript": "COMMIT"}))
    turns = [("q", "COMMIT", "00:00:00", None)]
    with pytest.raises(consensus.ReconciliationError):
        consensus.open_review(turns, "b", bus=tmp_path / "bus",
                              run_id="run-x", journal_path=str(jpath))


def test_reconciliation_blocks_digest_disagreeing_with_journal(tmp_path):
    """When the live transcript ships an answer that differs from the raw journal's
    bytes, opening the review would misrepresent the run — blocked loudly."""
    jpath = tmp_path / "j.jsonl"
    ans = "TRUE-ANSWER"
    jpath.write_text(json.dumps(
        {"run_id": "run-y", "turn_index": 1, "question": "q", "answer_present": True,
         "answer": ans, "answer_len": len(ans.encode()),
         "answer_sha256": hashlib.sha256(ans.encode()).hexdigest(),
         "transcript": "x"}))
    turns = [("q", "\x01WRONG-ANSWER\x02", "00:00:00", None)]   # live disagrees
    with pytest.raises(consensus.ReconciliationError):
        consensus.open_review(turns, "b", bus=tmp_path / "bus",
                              run_id="run-y", journal_path=str(jpath))


def test_empty_answer_is_distinct_from_nothing_shipped(tmp_path):
    """A present-but-empty answer (a silence contract) is named distinctly from a
    genuinely absent one — the two must not be indistinguishable from parser failure."""
    jpath = tmp_path / "j.jsonl"
    jpath.write_text(json.dumps(
        {"run_id": "run-e", "turn_index": 1, "question": "q", "answer_present": True,
         "answer": "", "answer_len": 0,
         "answer_sha256": hashlib.sha256(b"").hexdigest(), "transcript": "COMMIT"}))
    turns = [("q", "COMMIT", "00:00:00", None)]
    review = consensus.open_review(turns, "b", bus=tmp_path / "bus",
                                   run_id="run-e", journal_path=str(jpath))
    ctx = (review / "FULL_REVIEW_CONTEXT.md").read_text()
    assert "(empty answer" in ctx
    assert "(nothing shipped)" not in ctx


def test_sabotage_revert_structured_answer_reprints_the_lie(tmp_path):
    """SABOTAGE / mutation control (names the cause): a pre-tier-6 journal row with NO
    structured answer field leaves nothing to reconcile, so a review rebuilt from the
    marker-stripped transcript reprints "(nothing shipped)" over what was a real
    answer. This documents that the structured answer field is load-bearing — revert
    it and the digest lie returns."""
    jpath = tmp_path / "j.jsonl"
    # No answer / answer_present / answer_sha256 — exactly the pre-tier-6 record.
    jpath.write_text(json.dumps(
        {"run_id": "run-s", "turn_index": 1, "question": "q",
         "transcript": "COMMIT\n874"}))
    turns = [("q", "COMMIT\n874", "00:00:00", None)]      # stripped, no markers
    review = consensus.open_review(turns, "b", bus=tmp_path / "bus",
                                   run_id="run-s", journal_path=str(jpath))
    ctx = (review / "FULL_REVIEW_CONTEXT.md").read_text()
    assert "ANSWER:\n(nothing shipped)" in ctx           # the lie, absent the fix
