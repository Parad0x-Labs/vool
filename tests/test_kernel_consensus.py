"""Adversarial pins for the FOUR-way consensus file bus — the state machine only.

No models: the bus is deterministic plumbing between five parties (the REPL, and
judges kimi/codex/grok/fable). These pins attack its worst cases — half-written
files, out-of-order arrivals, block-less agreement, a lone falsifier veto, three
rounds of dissent, abort, stall, and artifact completeness."""
from __future__ import annotations

import json
import threading

import pytest

import core.kernel.consensus as consensus

AGENTS = ("kimi", "codex", "grok", "fable")
LEGACY = dict(voting_seats=AGENTS, contributors=())


def _file(review, name, body, verdict):
    (review / name).write_text(body + f"\nVERDICT: {verdict}\n")


BLOCK = """=== CONSENSUS ===
ROOT CAUSE: x
LAW / INVARIANT: y
AGREED FIX: z
IMPLEMENTATION BOUNDARY: b
DO NOT TOUCH: frozen app
TESTS REQUIRED: t
NEGATIVE CONTROLS: n
MUTATION / SABOTAGE TESTS: m
GROK SABOTAGES ATTEMPTED: 5 new cases
EXPECTED USER-VISIBLE BEHAVIOR: e
RAW-EVIDENCE COMPLETENESS VERIFIED: YES
KIMI: AGREE
CODEX: AGREE
GROK: AGREE
FABLE: AGREE
"""

TURNS = [
    ("what is gold at?", "  ob1 [web_lookup] gold price  ->  gold\n\nCOMMIT: committed\n\n\x01Gold is 2400. [receipt:ob1-web1]\x02\n\nreceipts: 2\n\x03EVIDENCE (recorded for review):\nob1-web1: Gold — spot 2400 (https://g)\x04", "01:00:00"),
    ("thanks", "  ob1 [chat] thanks\n\nCOMMIT: committed\n\n\x01You're welcome.\x02\n\nreceipts: 1\n\x03EVIDENCE (recorded for review):\nuser: thanks\x04", "01:00:30"),
]


def _round1(review, verdict="AGREE", block=True):
    """Drop all three independent round-1 files, then fable."""
    body = ("A. coverage\nB. findings\n" + BLOCK) if block else "A. coverage\nB. findings"
    for a in ("kimi", "codex", "grok"):
        _file(review, f"round_1_{a}.md", body, verdict)
    _file(review, "round_1_fable.md", "A. coverage\nB. ratified" + ("\n" + BLOCK if block else ""), verdict)


def test_open_review_writes_the_complete_context_for_all_four(tmp_path) -> None:
    review = consensus.open_review(TURNS, "abc1234", tmp_path)
    ctx = (review / "FULL_REVIEW_CONTEXT.md").read_text()
    assert "USER: what is gold at?" in ctx and "USER: thanks" in ctx
    assert "RAW RUN WINS" in ctx, "the raw-evidence rule must be named in the artifact"
    assert "repl_sessions.jsonl" in ctx
    proto = (review / "PROTOCOL.md").read_text()
    assert "GROK" in proto and "RAW-EVIDENCE COMPLETENESS VERIFIED" in proto and "falsifier" in proto.lower()
    st = json.loads((review / "state.json").read_text())
    assert st["awaiting"] == ["round_1_deepseekpro.md", "round_1_grok.md",
                              "round_1_terra.md", "round_1_fable.md",
                              "contrib_1_deepseekflash.md"]
    assert st["voting_seats"] == ["deepseekpro", "grok", "terra", "fable"]


def test_empty_session_is_refused(tmp_path) -> None:
    with pytest.raises(ValueError):
        consensus.open_review([], "abc1234", tmp_path)
    assert consensus.run_review([], "abc1234", tmp_path, **LEGACY) == "refused"


def test_harness_refuses_a_fragmented_transcript(tmp_path) -> None:
    from core.kernel.consensus import FragmentedTranscriptError
    with pytest.raises(FragmentedTranscriptError):
        consensus.open_review(TURNS, "abc1234", tmp_path, expected_turns=58)
    consensus.open_review(TURNS, "abc1234", tmp_path, expected_turns=2)


def test_four_way_consensus_needs_all_four_and_a_block(tmp_path, capsys) -> None:
    def drive():
        review = next(d for d in tmp_path.iterdir() if d.is_dir())
        _round1(review)

    t = threading.Timer(0.05, drive)
    t.start()
    status = consensus.run_review(TURNS, "abc1234", tmp_path, poll_s=0.02, max_wait_s=5, **LEGACY)
    t.join()
    assert status == "consensus"
    review = next(d for d in tmp_path.iterdir() if d.is_dir())
    # snapshot evaluation may close on the FIRST landing when the whole round is
    # already on disk — the RECORD is the authority, not the terminal print.
    assert "GROK: AGREE" in (review / "consensus.md").read_text()
    audit = json.loads((review / "consensus_audit.json").read_text())
    assert len(audit["vote_file_sha256"]) == 4


def test_round_one_accepts_the_three_judges_in_any_order(tmp_path) -> None:
    import time as _time

    def drive():
        review = next(d for d in tmp_path.iterdir() if d.is_dir())
        _file(review, "round_1_grok.md", "A\nB falsifier\n" + BLOCK, "AGREE")
        _time.sleep(0.15)
        _file(review, "round_1_codex.md", "A\nB\n" + BLOCK, "AGREE")
        _file(review, "round_1_kimi.md", "A\nB\n" + BLOCK, "AGREE")
        _file(review, "round_1_fable.md", "A\nB ratified\n" + BLOCK, "AGREE")

    t = threading.Timer(0.05, drive)
    t.start()
    status = consensus.run_review(TURNS, "abc1234", tmp_path, poll_s=0.02, max_wait_s=5, **LEGACY)
    t.join()
    assert status == "consensus"


def test_lone_falsifier_veto_blocks_three_agrees(tmp_path) -> None:
    """Kimi+Codex+Fable AGREE over GROK's DISAGREE must run to the cap and record
    NO CONSENSUS — one valid falsifier counterexample cannot be voted away."""
    def drive():
        review = next(d for d in tmp_path.iterdir() if d.is_dir())
        for r in (1, 2, 3):
            for a in ("kimi", "codex", "fable"):
                _file(review, f"round_{r}_{a}.md", "A\nB\n" + BLOCK, "AGREE")
            _file(review, f"round_{r}_grok.md", "A\nB\nCOUNTEREXAMPLE: guard rejects a correct answer", "DISAGREE")

    t = threading.Timer(0.05, drive)
    t.start()
    status = consensus.run_review(TURNS, "abc1234", tmp_path, poll_s=0.02, max_wait_s=5, max_rounds=3, **LEGACY)
    t.join()
    assert status == "no_consensus"
    review = next(d for d in tmp_path.iterdir() if d.is_dir())
    assert not (review / "consensus.md").exists()
    assert "round_3_grok.md: DISAGREE" in (review / "no_consensus.md").read_text()


def test_agreement_without_any_block_is_not_consensus(tmp_path) -> None:
    def drive():
        review = next(d for d in tmp_path.iterdir() if d.is_dir())
        for r in (1, 2, 3):
            for a in AGENTS:
                _file(review, f"round_{r}_{a}.md", "A\nB\nAGREE: vibes", "AGREE")

    t = threading.Timer(0.05, drive)
    t.start()
    status = consensus.run_review(TURNS, "abc1234", tmp_path, poll_s=0.02, max_wait_s=5, max_rounds=3, **LEGACY)
    t.join()
    assert status == "no_consensus"
    review = next(d for d in tmp_path.iterdir() if d.is_dir())
    assert not (review / "consensus.md").exists()


def test_three_rounds_of_dissent_go_to_the_operator(tmp_path) -> None:
    def drive():
        review = next(d for d in tmp_path.iterdir() if d.is_dir())
        for r in (1, 2, 3):
            for a in AGENTS:
                _file(review, f"round_{r}_{a}.md", f"A\nB\nDISAGREE: r{r}", "DISAGREE")

    t = threading.Timer(0.05, drive)
    t.start()
    status = consensus.run_review(TURNS, "abc1234", tmp_path, poll_s=0.02, max_wait_s=5, max_rounds=3, **LEGACY)
    t.join()
    assert status == "no_consensus"
    review = next(d for d in tmp_path.iterdir() if d.is_dir())
    assert "round_3_fable.md: DISAGREE" in (review / "no_consensus.md").read_text()


def test_half_written_file_is_invisible_until_its_verdict_line(tmp_path, capsys) -> None:
    import time as _time

    def drive():
        review = next(d for d in tmp_path.iterdir() if d.is_dir())
        (review / "round_1_kimi.md").write_text("half a thought\nEVIDENCE: pending")
        _time.sleep(0.3)
        _round1(review)

    t = threading.Timer(0.05, drive)
    t.start()
    status = consensus.run_review(TURNS, "abc1234", tmp_path, poll_s=0.02, max_wait_s=5, **LEGACY)
    t.join()
    assert status == "consensus"
    round1 = capsys.readouterr().out.split("KIMI K3 REVIEW — ROUND 1 ===")[1].split("===")[0]
    assert "EVIDENCE: pending" not in round1


def test_operator_abort_saves_state_and_implements_nothing(tmp_path, monkeypatch) -> None:
    def interrupt(_s):
        raise KeyboardInterrupt

    monkeypatch.setattr(consensus, "_sleep", interrupt)
    status = consensus.run_review(TURNS, "abc1234", tmp_path, poll_s=0.02, **LEGACY)
    assert status == "aborted"
    review = next(d for d in tmp_path.iterdir() if d.is_dir())
    assert (review / "aborted.md").exists()


def test_silent_counterparts_stall(tmp_path) -> None:
    status = consensus.run_review(TURNS, "abc1234", tmp_path, poll_s=0.02, max_wait_s=0.1, **LEGACY)
    assert status == "stalled"


def test_rounds_continue_past_three_until_unanimous(tmp_path) -> None:
    """OPERATOR ORDER 2026-08-20: the three-round cap is abolished. Dissent through
    round 3 no longer terminates the review — deliberation continues, and a
    unanimous block at round 4 registers consensus."""
    def drive():
        review = next(d for d in tmp_path.iterdir() if d.is_dir())
        for r in (1, 2, 3):
            for a in AGENTS:
                _file(review, f"round_{r}_{a}.md", f"A\nB\nDISAGREE r{r}", "DISAGREE")
        for a in AGENTS:
            _file(review, f"round_4_{a}.md", "A\nB amended\n" + BLOCK, "AGREE")

    t = threading.Timer(0.05, drive)
    t.start()
    status = consensus.run_review(TURNS, "abc1234", tmp_path, poll_s=0.02, max_wait_s=5, **LEGACY)
    t.join()
    assert status == "consensus"
    review = next(d for d in tmp_path.iterdir() if d.is_dir())
    assert (review / "consensus.md").exists()


def test_resume_review_reenters_a_capped_dispute(tmp_path) -> None:
    """A review parked by the old cap (no_consensus written, nothing awaiting) is
    resumable: resume_review archives the stale terminal file, replays landed
    rounds from disk, and drives on — consensus forms at round 4."""
    def drive():
        review = next(d for d in tmp_path.iterdir() if d.is_dir())
        for r in (1, 2, 3):
            for a in AGENTS:
                _file(review, f"round_{r}_{a}.md", f"A\nB\nDISAGREE r{r}", "DISAGREE")

    t = threading.Timer(0.05, drive)
    t.start()
    status = consensus.run_review(TURNS, "abc1234", tmp_path, poll_s=0.02, max_wait_s=5, max_rounds=3, **LEGACY)
    t.join()
    assert status == "no_consensus"          # parked by the explicit backstop
    review = next(d for d in tmp_path.iterdir() if d.is_dir())

    def drive4():
        for a in AGENTS:
            _file(review, f"round_4_{a}.md", "A\nB amended\n" + BLOCK, "AGREE")

    t2 = threading.Timer(0.05, drive4)
    t2.start()
    status2 = consensus.resume_review(review, "abc1234", poll_s=0.02, max_wait_s=5)
    t2.join()
    assert status2 == "consensus"
    assert (review / "consensus.md").exists()
    assert not (review / "no_consensus.md").exists()          # archived, not terminal
    assert (review / "no_consensus_superseded.md").exists()


# ---- COUNCIL 2.0 (operator directive 2026-08-20): quorum + round-close policy ----

def test_majority_needed_is_over_half_of_designated_seats() -> None:
    assert consensus._majority_needed(4) == 3
    assert consensus._majority_needed(5) == 3
    assert consensus._majority_needed(3) == 2
    assert consensus._majority_needed(6) == 4


def test_majority_does_not_close_round_four(tmp_path) -> None:
    """Rounds 1-4 target unanimity: 3/4 AGREE at round 4 must NOT close — the
    review continues into round 5 (here it stalls awaiting round_5_kimi)."""
    def drive():
        review = next(d for d in tmp_path.iterdir() if d.is_dir())
        for r in (1, 2, 3, 4):
            for a in ("kimi", "grok", "fable"):
                _file(review, f"round_{r}_{a}.md", "A\n" + BLOCK, "AGREE")
            _file(review, f"round_{r}_codex.md", "A\nno", "DISAGREE")

    t = threading.Timer(0.05, drive)
    t.start()
    status = consensus.run_review(TURNS, "abc1234", tmp_path, poll_s=0.02, max_wait_s=3, **LEGACY)
    t.join()
    assert status == "stalled"                       # waiting on round 5, not closed
    review = next(d for d in tmp_path.iterdir() if d.is_dir())
    assert not (review / "consensus.md").exists()


def test_majority_closes_at_round_five(tmp_path) -> None:
    """From round 5, the first completed round with >=3/4 AGREE closes APPROVED."""
    def drive():
        review = next(d for d in tmp_path.iterdir() if d.is_dir())
        for r in (1, 2, 3, 4, 5):
            for a in ("kimi", "grok", "fable"):
                _file(review, f"round_{r}_{a}.md", "A\n" + BLOCK, "AGREE")
            _file(review, f"round_{r}_codex.md", "A\nstill no", "DISAGREE")

    t = threading.Timer(0.05, drive)
    t.start()
    status = consensus.run_review(TURNS, "abc1234", tmp_path, poll_s=0.02, max_wait_s=5, **LEGACY)
    t.join()
    assert status == "consensus"
    review = next(d for d in tmp_path.iterdir() if d.is_dir())
    assert (review / "consensus.md").exists()
    state = json.loads((review / "state.json").read_text())
    assert state["status"] == "consensus"
    assert state["basis"].startswith("majority 3/4")
    assert state["round_limit"] is None
    assert state["unanimity_target_through_round"] == 4
    assert state["majority_fallback_from_round"] == 5
    assert state["voting_seats"] == ["kimi", "codex", "grok", "fable"]


def test_evidence_incomplete_agree_does_not_count(tmp_path) -> None:
    """An AGREE carrying the EVIDENCE INCOMPLETE marker is not an AGREE: with only
    2 clean AGREEs at round 5, no majority forms and the review continues."""
    def drive():
        review = next(d for d in tmp_path.iterdir() if d.is_dir())
        for r in (1, 2, 3, 4, 5):
            for a in ("kimi", "grok"):
                _file(review, f"round_{r}_{a}.md", "A\n" + BLOCK, "AGREE")
            _file(review, f"round_{r}_fable.md",
                  "A\nEVIDENCE INCOMPLETE — one artifact missing\n" + BLOCK, "AGREE")
            _file(review, f"round_{r}_codex.md", "A\nno", "DISAGREE")

    t = threading.Timer(0.05, drive)
    t.start()
    status = consensus.run_review(TURNS, "abc1234", tmp_path, poll_s=0.02, max_wait_s=3, **LEGACY)
    t.join()
    assert status == "stalled"                       # 2 counted AGREEs < 3 needed
    review = next(d for d in tmp_path.iterdir() if d.is_dir())
    assert not (review / "consensus.md").exists()


def test_p0_hold_blocks_majority_close(tmp_path) -> None:
    """A DISAGREE pinning a P0-HOLD blocks the majority close at round 5+ — an
    unresolved destructive/security failure is never voted away."""
    def drive():
        review = next(d for d in tmp_path.iterdir() if d.is_dir())
        for r in (1, 2, 3, 4, 5):
            for a in ("kimi", "grok", "fable"):
                _file(review, f"round_{r}_{a}.md", "A\n" + BLOCK, "AGREE")
            _file(review, f"round_{r}_codex.md",
                  "A\nP0-HOLD: unauthorized effect reproduces on tape\n", "DISAGREE")

    t = threading.Timer(0.05, drive)
    t.start()
    status = consensus.run_review(TURNS, "abc1234", tmp_path, poll_s=0.02, max_wait_s=3, **LEGACY)
    t.join()
    assert status == "stalled"                       # hold stands; no majority close
    review = next(d for d in tmp_path.iterdir() if d.is_dir())
    assert not (review / "consensus.md").exists()


def test_unanimity_still_closes_before_round_five(tmp_path) -> None:
    """Unanimity remains preferred and closes immediately at any round <= 4."""
    def drive():
        review = next(d for d in tmp_path.iterdir() if d.is_dir())
        for a in AGENTS:
            _file(review, f"round_1_{a}.md", "A\n" + BLOCK, "AGREE")

    t = threading.Timer(0.05, drive)
    t.start()
    status = consensus.run_review(TURNS, "abc1234", tmp_path, poll_s=0.02, max_wait_s=5, **LEGACY)
    t.join()
    assert status == "consensus"
    state = json.loads((next(d for d in tmp_path.iterdir() if d.is_dir()) / "state.json").read_text())
    assert state["basis"].startswith("unanimous")


def test_non_voting_contributor_file_never_touches_quorum(tmp_path) -> None:
    """A shadow/sidekick file on the bus (round_5_deepseek.md) neither counts
    toward nor blocks the majority — quorum is the designated seats only."""
    def drive():
        review = next(d for d in tmp_path.iterdir() if d.is_dir())
        (review / "contrib_1_deepseekflash.md").write_text("y\nCONTRIB: COMPLETE\n")
        for r in (1, 2, 3, 4, 5):
            _file(review, f"round_{r}_shadowseat.md", "A\nshadow attack notes", "DISAGREE")
            for a in ("deepseekpro", "grok", "fable"):
                _file(review, f"round_{r}_{a}.md", "A\n" + BLOCK, "AGREE")
            _file(review, f"round_{r}_terra.md", "A\nno", "DISAGREE")

    t = threading.Timer(0.05, drive)
    t.start()
    status = consensus.run_review(TURNS, "abc1234", tmp_path, poll_s=0.02, max_wait_s=5)
    t.join()
    assert status == "consensus"                     # 3/4 designated seats at R5


# ---- CLOWN-WAGON TOPOLOGY (operator directive): waves, contribs, new seats ----

def test_wave1_awaits_all_six_and_unanimity_still_stops_early(tmp_path) -> None:
    """Default topology: round 1 is one parallel wave of kimi/grok/terra/fable +
    two contrib files. Unanimous judges close IMMEDIATELY (stop deliberation) even
    while contribs are pending — unanimity is preferred always."""
    def drive():
        review = next(d for d in tmp_path.iterdir() if d.is_dir())
        for a in ("deepseekpro", "grok", "terra", "fable"):
            _file(review, f"round_1_{a}.md", "A\n" + BLOCK, "AGREE")

    t = threading.Timer(0.05, drive)
    t.start()
    status = consensus.run_review(TURNS, "abc1234", tmp_path, poll_s=0.02, max_wait_s=5)
    t.join()
    assert status == "consensus"
    state = json.loads((next(d for d in tmp_path.iterdir() if d.is_dir()) / "state.json").read_text())
    assert state["voting_seats"] == ["deepseekpro", "grok", "terra", "fable"]
    assert state["contributors"] == ["deepseekflash"]


def test_wave2_gated_on_contrib_completion(tmp_path) -> None:
    """Without unanimity, wave 2 starts ONLY after ALL round-1 material — judge
    files AND contrib files — is complete. Missing contribs hold the round open."""
    def drive():
        review = next(d for d in tmp_path.iterdir() if d.is_dir())
        for a in ("deepseekpro", "grok", "terra"):
            _file(review, f"round_1_{a}.md", "A\n" + BLOCK, "AGREE")
        _file(review, "round_1_fable.md", "A\nno", "DISAGREE")   # no unanimity
        # deepseekpro lands COMPLETE; deepseekflash half-written (no terminator)
        (review / "contrib_1_deepseekflash.md").write_text("mutations still streaming")

    t = threading.Timer(0.05, drive)
    t.start()
    status = consensus.run_review(TURNS, "abc1234", tmp_path, poll_s=0.02, max_wait_s=2)
    t.join()
    assert status == "stalled"
    review = next(d for d in tmp_path.iterdir() if d.is_dir())
    state = json.loads((review / "state.json").read_text())
    assert state["awaiting"] == ["contrib_1_deepseekflash.md"]   # the half-written one


def test_round2_is_one_parallel_wave_of_the_judges(tmp_path) -> None:
    """After a complete round 1 without unanimity, the bus awaits ALL FOUR judges
    of round 2 at once (parallel wave) — not one seat serially — and contribs are
    no longer gated."""
    def drive():
        review = next(d for d in tmp_path.iterdir() if d.is_dir())
        for a in ("deepseekpro", "grok", "terra"):
            _file(review, f"round_1_{a}.md", "A\n" + BLOCK, "AGREE")
        _file(review, "round_1_fable.md", "A\nno", "DISAGREE")
        (review / "contrib_1_deepseekflash.md").write_text("y\nCONTRIB: COMPLETE\n")

    t = threading.Timer(0.05, drive)
    t.start()
    status = consensus.run_review(TURNS, "abc1234", tmp_path, poll_s=0.02, max_wait_s=2)
    t.join()
    assert status == "stalled"
    review = next(d for d in tmp_path.iterdir() if d.is_dir())
    state = json.loads((review / "state.json").read_text())
    assert sorted(state["awaiting"]) == ["round_2_deepseekpro.md", "round_2_fable.md",
                                         "round_2_grok.md", "round_2_terra.md"]


def test_new_topology_majority_closes_at_round_five(tmp_path) -> None:
    """Council 2.0 majority works on the new seats: terra dissents forever, the
    other three AGREE — closes at round 5, basis majority 3/4."""
    def drive():
        review = next(d for d in tmp_path.iterdir() if d.is_dir())
        (review / "contrib_1_deepseekflash.md").write_text("y\nCONTRIB: COMPLETE\n")
        for r in (1, 2, 3, 4, 5):
            for a in ("deepseekpro", "grok", "fable"):
                _file(review, f"round_{r}_{a}.md", "A\n" + BLOCK, "AGREE")
            _file(review, f"round_{r}_terra.md", "A\nstill no", "DISAGREE")

    t = threading.Timer(0.05, drive)
    t.start()
    status = consensus.run_review(TURNS, "abc1234", tmp_path, poll_s=0.02, max_wait_s=5)
    t.join()
    assert status == "consensus"
    state = json.loads((next(d for d in tmp_path.iterdir() if d.is_dir()) / "state.json").read_text())
    assert state["basis"].startswith("majority 3/4")


def test_unbound_agree_blocks_closure_until_digest_declared(tmp_path) -> None:
    """HARD binding (round-7 P0-HOLD): an AGREE that neither carries the block nor
    declares a matching BLOCK-DIGEST blocks closure; adding the declaration closes."""
    import hashlib as _h
    import time as _time

    def drive():
        review = next(d for d in tmp_path.iterdir() if d.is_dir())
        holder_body = "A\n" + BLOCK
        _file(review, "round_1_deepseekpro.md", holder_body, "AGREE")       # holder-quality
        _file(review, "round_1_grok.md", holder_body, "AGREE")
        _file(review, "round_1_terra.md", holder_body, "AGREE")
        _file(review, "round_1_fable.md", "A\nunbound ratification", "AGREE")  # UNBOUND
        (review / "contrib_1_deepseekflash.md").write_text("y\nCONTRIB: COMPLETE\n")
        _time.sleep(0.6)          # bus must NOT close while fable is unbound
        holder_text = (review / "round_1_deepseekpro.md").read_text()
        hd = _h.sha256(holder_text[holder_text.index("=== CONSENSUS ==="):].rstrip()
                       .encode()).hexdigest()          # FULL 64 hex (SOL dispute 4)
        (review / "round_1_fable.md").write_text(
            f"A\nunbound ratification\nBLOCK-DIGEST: {hd}\nVERDICT: AGREE\n")

    t = threading.Timer(0.05, drive)
    t.start()
    status = consensus.run_review(TURNS, "abc1234", tmp_path, poll_s=0.02, max_wait_s=6)
    t.join()
    assert status == "consensus"
    state = json.loads((next(d for d in tmp_path.iterdir() if d.is_dir()) / "state.json").read_text())
    assert state["block_sha256"]


# ---- SOL RULING 1 pins: snapshot authorization (the two reproduced exploits + matrix) ----

def _full_digest(text):
    import hashlib as _h
    return _h.sha256(text[text.index("=== CONSENSUS ==="):].rstrip().encode()).hexdigest()


def test_sol_exploit1_agree_edited_to_disagree_with_hold_never_closes(tmp_path) -> None:
    """SOL dispute 1 (reproduced exploit): a landed AGREE edited in place to a bound
    DISAGREE + P0-HOLD must NOT be counted as AGREE from a stale cache — no close."""
    import time as _time

    def drive():
        review = next(d for d in tmp_path.iterdir() if d.is_dir())
        body = "A\n" + BLOCK
        for a in ("deepseekpro", "grok", "terra"):
            _file(review, f"round_1_{a}.md", body, "AGREE")
        _file(review, "round_1_fable.md", "A\nno block yet", "AGREE")   # unbound: holds close
        (review / "contrib_1_deepseekflash.md").write_text("y\nCONTRIB: COMPLETE\n")
        _time.sleep(0.4)
        hd = _full_digest((review / "round_1_deepseekpro.md").read_text())
        # the exploit: bind AND flip to DISAGREE with a hold, in place
        (review / "round_1_fable.md").write_text(
            f"A\ncurrent dissent\nP0-HOLD: live objection\nBLOCK-DIGEST: {hd}\nVERDICT: DISAGREE\n")

    t = threading.Timer(0.05, drive)
    t.start()
    status = consensus.run_review(TURNS, "abc1234", tmp_path, poll_s=0.02, max_wait_s=3)
    t.join()
    assert status == "stalled"                       # never closed on the stale AGREE
    review = next(d for d in tmp_path.iterdir() if d.is_dir())
    assert not (review / "consensus.md").exists()


def test_sol_exploit2_holder_drift_after_declarations_blocks_close(tmp_path) -> None:
    """SOL dispute 2 (reproduced exploit): the holder's bytes drift AFTER the other
    seats declared the old digest — the close must refuse (declarations no longer
    match the drifted holder), and nothing attacker-written can reach consensus.md."""
    import time as _time

    def drive():
        review = next(d for d in tmp_path.iterdir() if d.is_dir())
        holder_body = "A\n" + BLOCK
        _file(review, "round_1_deepseekpro.md", holder_body, "AGREE")          # the holder
        hd = _full_digest((review / "round_1_deepseekpro.md").read_text())
        for a in ("grok", "terra"):
            _file(review, f"round_1_{a}.md", f"A\nBLOCK-DIGEST: {hd}", "AGREE")
        _file(review, "round_1_fable.md", "A\nno digest yet", "AGREE")  # unbound: delays close
        (review / "contrib_1_deepseekflash.md").write_text("y\nCONTRIB: COMPLETE\n")
        _time.sleep(0.3)
        # ATTACK: mutate the holder text, then bind fable to the OLD digest
        (review / "round_1_deepseekpro.md").write_text(
            holder_body.replace("AGREED FIX: z", "AGREED FIX: ATTACKER-NEW-TEXT")
            + "\nVERDICT: AGREE\n")
        (review / "round_1_fable.md").write_text(f"A\nBLOCK-DIGEST: {hd}\nVERDICT: AGREE\n")

    t = threading.Timer(0.05, drive)
    t.start()
    status = consensus.run_review(TURNS, "abc1234", tmp_path, poll_s=0.02, max_wait_s=3)
    t.join()
    review = next(d for d in tmp_path.iterdir() if d.is_dir())
    if status == "consensus":                        # only legal if it closed BEFORE the drift
        assert "ATTACKER-NEW-TEXT" not in (review / "consensus.md").read_text()
    else:
        assert status == "stalled"
        assert not (review / "consensus.md").exists()


def test_sol_current_verdict_controls_agree_to_disagree(tmp_path) -> None:
    """SOL test 3: an AGREE flipped to DISAGREE (no hold) stops counting — 3 bound
    AGREEs remain at round 1, no unanimity, no close."""
    import time as _time

    def drive():
        review = next(d for d in tmp_path.iterdir() if d.is_dir())
        body = "A\n" + BLOCK
        for a in ("deepseekpro", "grok", "terra"):
            _file(review, f"round_1_{a}.md", body, "AGREE")
        _file(review, "round_1_fable.md", "A\nnot bound", "AGREE")
        (review / "contrib_1_deepseekflash.md").write_text("y\nCONTRIB: COMPLETE\n")
        _time.sleep(0.3)
        (review / "round_1_fable.md").write_text("A\nchanged my mind\nVERDICT: DISAGREE\n")

    t = threading.Timer(0.05, drive)
    t.start()
    status = consensus.run_review(TURNS, "abc1234", tmp_path, poll_s=0.02, max_wait_s=3)
    t.join()
    assert status == "stalled"
    assert not (next(d for d in tmp_path.iterdir() if d.is_dir()) / "consensus.md").exists()


def test_sol_digest_syntax_matrix() -> None:
    """SOL test 8: exactly one full-64-lowercase-hex declaration; everything else None."""
    import hashlib as _h
    good = _h.sha256(b"x").hexdigest()
    f = consensus._declared_digest_from
    assert f(f"BLOCK-DIGEST: {good}\n") == good
    assert f(f"BLOCK-DIGEST: {good[:63]}\n") is None          # 63
    assert f(f"BLOCK-DIGEST: {good}a\n") is None              # 65
    assert f(f"BLOCK-DIGEST: {good.upper()}\n") is None       # uppercase
    assert f(f"BLOCK-DIGEST: {good} junk\n") is None          # suffix junk on the line
    assert f(f"BLOCK-DIGEST: {good}\nBLOCK-DIGEST: {good}\n") is None   # duplicate
    other = _h.sha256(b"y").hexdigest()
    assert f(f"BLOCK-DIGEST: {good}\nBLOCK-DIGEST: {other}\n") is None  # conflicting
    assert f("no declaration here\n") is None


def test_sol_mixed_round_agrees_cannot_close(tmp_path) -> None:
    """SOL test 9 (the round-6 class, pinned directly): three round-1 AGREEs plus the
    fourth seat AGREEING only in round 2 never closes either round."""
    def drive():
        review = next(d for d in tmp_path.iterdir() if d.is_dir())
        body = "A\n" + BLOCK
        for a in ("deepseekpro", "grok", "terra"):
            _file(review, f"round_1_{a}.md", body, "AGREE")
        _file(review, "round_1_fable.md", "A\nr1 dissent", "DISAGREE")
        (review / "contrib_1_deepseekflash.md").write_text("y\nCONTRIB: COMPLETE\n")
        _file(review, "round_2_fable.md", body, "AGREE")       # only fable files R2

    t = threading.Timer(0.05, drive)
    t.start()
    status = consensus.run_review(TURNS, "abc1234", tmp_path, poll_s=0.02, max_wait_s=3)
    t.join()
    assert status == "stalled"
    assert not (next(d for d in tmp_path.iterdir() if d.is_dir()) / "consensus.md").exists()


def test_sol_recorded_digest_recomputes_from_consensus_md(tmp_path) -> None:
    """SOL test 11: consensus.md holds ONLY the captured holder bytes; the recorded
    block_sha256 recomputes exactly from its block section (audit lives separately)."""
    def drive():
        review = next(d for d in tmp_path.iterdir() if d.is_dir())
        body = "A\n" + BLOCK
        for a in ("deepseekpro", "grok", "terra", "fable"):
            _file(review, f"round_1_{a}.md", body, "AGREE")

    t = threading.Timer(0.05, drive)
    t.start()
    status = consensus.run_review(TURNS, "abc1234", tmp_path, poll_s=0.02, max_wait_s=5)
    t.join()
    assert status == "consensus"
    review = next(d for d in tmp_path.iterdir() if d.is_dir())
    state = json.loads((review / "state.json").read_text())
    assert _full_digest((review / "consensus.md").read_text()) == state["block_sha256"]
    assert (review / "consensus_audit.json").exists()


def test_negated_or_quoted_evidence_marker_does_not_discount_agree() -> None:
    """round-9 artifact: 'No EVIDENCE INCOMPLETE on this AGREE' must NOT discount
    the vote; a real declaration line (with or without markdown bold) must."""
    f = consensus._declares_evidence_incomplete
    assert f("No P0-HOLD. No EVIDENCE INCOMPLETE on this AGREE.\n") is False
    assert f("the prior round said EVIDENCE INCOMPLETE somewhere\n") is False
    assert f("EVIDENCE INCOMPLETE — REVIEW CANNOT CLOSE\n") is True
    assert f("**EVIDENCE INCOMPLETE — REVIEW CANNOT CLOSE.**\n") is True
    assert f("> EVIDENCE INCOMPLETE: missing tape\n") is True
