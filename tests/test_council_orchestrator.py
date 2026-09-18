"""Council orchestrator law tests — worst cases first, no model anywhere.

Every scenario drives the REAL state machine through an injected seat-turn double that
scripts reports per (seat, round). The double records every prompt it received, so the
information-diet and blindness laws are asserted on the actual assembled context, not
on intent.
"""

from __future__ import annotations

import pytest

from core.council.orchestrator import (
    CouncilOrchestrator,
    CouncilRunError,
    Seat,
    parse_diagnosis,
    parse_verdict,
)


@pytest.fixture()
def isolated_store(tmp_path, monkeypatch):
    def _patched(*parts):
        base = tmp_path / "data"
        base.mkdir(parents=True, exist_ok=True)
        out = base
        for part in parts:
            out = out / part
        return out

    monkeypatch.setattr("core.council.run_store.data_path", _patched)
    return tmp_path


def _bench():
    return [
        Seat("s1", "builder", "model-a", True),
        Seat("s2", "falsifier", "model-b", True),
        Seat("s3", "reviewer", "model-c", True),
    ]


class ScriptedSeats:
    """Scripted seat turns keyed by (seat_id, round_no); records every prompt."""

    def __init__(self, script):
        self.script = script
        self.prompts: dict[tuple[str, int], str] = {}
        self.calls: list[tuple[str, int]] = []

    def __call__(self, seat, prompt, round_no, run_id):
        self.calls.append((seat.seat_id, round_no))
        self.prompts[(seat.seat_id, round_no)] = prompt
        entry = self.script[(seat.seat_id, round_no)]
        if isinstance(entry, Exception):
            raise entry
        return entry


def _report(text, receipts=0):
    return {"text": text, "receipt_count": receipts, "session_id": None}


DIAG = "DIAGNOSIS: the stream writer commits before the history writer reads the turn row."


def test_happy_path_converges_and_preserves_authority(isolated_store):
    seats = ScriptedSeats({
        ("s1", 1): _report(DIAG + "\nFIX: move the commit."),
        ("s2", 1): _report("Tried to break assumptions; nothing conclusive yet.", receipts=2),
        ("s3", 1): _report("Symptoms reviewed independently."),
        ("s1", 2): _report("Holds.\nVERDICT: AGREE"),
        ("s2", 2): _report("Could not break it.\nVERDICT: AGREE"),
        ("s3", 2): _report("Explains every symptom.\nVERDICT: AGREE"),
    })
    run = CouncilOrchestrator(problem="stream says ok, history says failed", seats=_bench(), seat_turn=seats)
    outcome = run.run()
    assert outcome["result"] == "adjudicated"
    assert outcome["agree"] == 3 and outcome["disagree"] == 0 and outcome["rounds"] == 2
    assert "operator" in outcome["authority_note"], "promotion must stay an operator authority"
    assert run.candidate.startswith("the stream writer commits")
    types = [e["type"] for e in run.store.read_events()]
    for expected in ("convened", "round_opened", "seat_report", "round_landed", "candidate_set", "tally", "converged"):
        assert expected in types


def test_round_one_is_blind_and_later_rounds_are_not(isolated_store):
    seats = ScriptedSeats({
        ("s1", 1): _report(DIAG),
        ("s2", 1): _report("falsifier own evidence trail"),
        ("s3", 1): _report("reviewer own evidence trail"),
        ("s1", 2): _report("VERDICT: AGREE"),
        ("s2", 2): _report("VERDICT: AGREE"),
        ("s3", 2): _report("VERDICT: AGREE"),
    })
    CouncilOrchestrator(problem="p", seats=_bench(), seat_turn=seats).run()
    for sid in ("s1", "s2", "s3"):
        r1 = seats.prompts[(sid, 1)]
        assert "REPORTS FROM THE PREVIOUS ROUND" not in r1
        assert "own evidence trail" not in r1, "round 1 must not leak any peer report"
        assert "deliberately blind" in r1
    r2 = seats.prompts[("s2", 2)]
    assert "REPORTS FROM THE PREVIOUS ROUND" in r2
    assert "reviewer own evidence trail" in r2
    assert "falsifier own evidence trail" not in r2, "a seat never receives its own report as a peer"
    assert "model-c" not in r2, "peer reports are labelled by role, never by model"


def test_receipt_backed_counterexample_trumps_unanimous_agreement(isolated_store):
    seats = ScriptedSeats({
        ("s1", 1): _report(DIAG),
        ("s2", 1): _report("investigating"),
        ("s3", 1): _report("investigating"),
        # Round 2: everyone AGREES — but the falsifier ALSO demonstrates a receipt-backed kill.
        ("s1", 2): _report("VERDICT: AGREE"),
        ("s2", 2): _report("COUNTEREXAMPLE: replayed turn 5; history rewrote AFTER commit.\nVERDICT: AGREE", receipts=3),
        ("s3", 2): _report("VERDICT: AGREE"),
        # Rebuild round (investigate) with the kill visible, then a clean adjudication.
        ("s1", 3): _report("DIAGNOSIS: the finalizer overwrites the history row on late failure."),
        ("s2", 3): _report("kill confirmed, new trail", receipts=1),
        ("s3", 3): _report("re-reviewed"),
        ("s1", 4): _report("VERDICT: AGREE"),
        ("s2", 4): _report("VERDICT: AGREE"),
        ("s3", 4): _report("VERDICT: AGREE"),
    })
    run = CouncilOrchestrator(problem="p", seats=_bench(), seat_turn=seats, max_rounds=6)
    outcome = run.run()
    assert outcome["result"] == "adjudicated"
    assert outcome["candidate"].startswith("the finalizer overwrites")
    events = run.store.read_events()
    rejected = [e for e in events if e["type"] == "candidate_rejected"]
    assert len(rejected) == 1, "one receipt-backed counterexample must reject the candidate despite 3x AGREE"
    assert rejected[0]["counterexamples"][0]["seat_id"] == "s2"
    r3 = seats.prompts[("s2", 3)]
    assert "COUNTEREXAMPLE" in r3, "the rebuild round must show the bench the kill"


def test_unbacked_counterexample_is_an_opinion_not_a_trump(isolated_store):
    seats = ScriptedSeats({
        ("s1", 1): _report(DIAG),
        ("s2", 1): _report("investigating"),
        ("s3", 1): _report("investigating"),
        ("s1", 2): _report("VERDICT: AGREE"),
        ("s2", 2): _report("COUNTEREXAMPLE: it feels wrong.\nVERDICT: AGREE", receipts=0),
        ("s3", 2): _report("VERDICT: AGREE"),
    })
    outcome = CouncilOrchestrator(problem="p", seats=_bench(), seat_turn=seats).run()
    assert outcome["result"] == "adjudicated", "a counterexample without receipts trumps nothing"


def test_advisor_reports_feed_judges_but_never_vote(isolated_store):
    bench = _bench() + [Seat("a1", "verifier", "cheap-model", False)]
    seats = ScriptedSeats({
        ("s1", 1): _report(DIAG),
        ("s2", 1): _report("investigating"),
        ("s3", 1): _report("investigating"),
        ("a1", 1): _report("advisor evidence audit: claims 1-3 unbacked"),
        ("s1", 2): _report("VERDICT: AGREE"),
        ("s2", 2): _report("VERDICT: AGREE"),
        ("s3", 2): _report("VERDICT: AGREE"),
        # A sneaky advisor writing a verdict line must not enter the tally.
        ("a1", 2): _report("I insist.\nVERDICT: DISAGREE"),
    })
    run = CouncilOrchestrator(problem="p", seats=bench, seat_turn=seats)
    outcome = run.run()
    assert outcome["result"] == "adjudicated"
    assert outcome["agree"] == 3 and outcome["disagree"] == 0, "advisor verdicts never count"
    assert "advisor evidence audit" in seats.prompts[("s1", 2)], "advisor reports must reach the judges"
    tally = [e for e in run.store.read_events() if e["type"] == "tally"][0]
    assert tally["voting_seats"] == 3


def test_advisor_counterexample_still_trumps(isolated_store):
    bench = _bench() + [Seat("a1", "verifier", "cheap-model", False)]
    seats = ScriptedSeats({
        ("s1", 1): _report(DIAG),
        ("s2", 1): _report("investigating"),
        ("s3", 1): _report("investigating"),
        ("a1", 1): _report("auditing"),
        ("s1", 2): _report("VERDICT: AGREE"),
        ("s2", 2): _report("VERDICT: AGREE"),
        ("s3", 2): _report("VERDICT: AGREE"),
        ("a1", 2): _report("COUNTEREXAMPLE: reran repro, bytes still leak.", receipts=2),
        ("s1", 3): _report("DIAGNOSIS: second theory."),
        ("s2", 3): _report("x"),
        ("s3", 3): _report("x"),
        ("a1", 3): _report("x"),
        ("s1", 4): _report("VERDICT: AGREE"),
        ("s2", 4): _report("VERDICT: AGREE"),
        ("s3", 4): _report("VERDICT: AGREE"),
        ("a1", 4): _report("ok"),
    })
    run = CouncilOrchestrator(problem="p", seats=bench, seat_turn=seats, max_rounds=6)
    outcome = run.run()
    assert outcome["result"] == "adjudicated"
    assert any(e["type"] == "candidate_rejected" for e in run.store.read_events()), (
        "a fact is a fact regardless of who found it — advisor counterexamples trump too"
    )


def test_voting_seat_dying_twice_pauses_the_run_typed(isolated_store):
    """C4 changed the ENDING here and nothing else. A voting seat that exhausts its bounded
    attempts used to end the run `failed` — terminal, with every other seat's work thrown
    away and nothing to do but convene again and pay for all of it twice. It now pauses at
    NEEDS_ATTENTION: the same refusal to vote a missing vote away, held open for the
    operator instead of closed against them. Every attempt-truth assertion below is the C1
    law, unchanged."""
    boom = RuntimeError("provider dead")
    seats = ScriptedSeats({
        ("s1", 1): _report(DIAG),
        ("s2", 1): boom,  # both attempts raise — ScriptedSeats returns the same entry
        ("s3", 1): _report("x"),
    })
    run = CouncilOrchestrator(problem="p", seats=_bench(), seat_turn=seats)
    outcome = run.run()
    assert outcome["result"] == "needs_attention"
    assert [row["seat_id"] for row in outcome["blocking_seats"]] == ["s2"]
    assert "never voted away" in outcome["detail"]
    assert run.current_state == "needs_attention"
    # Every attempt leaves exactly one `seat_attempt` row carrying its typed outcome —
    # the successes included, so "how many tries did this take" is answerable from the
    # ledger alone rather than inferable from the absence of failure rows.
    attempts = [
        e for e in run.store.read_events()
        if e["type"] == "seat_attempt" and e["seat_id"] == "s2"
    ]
    assert len(attempts) == 2, "the seat must be retried exactly once before the failure is final"
    assert [e["outcome"] for e in attempts] == ["FAILED", "FAILED"]
    assert seats.calls.count(("s2", 1)) == 2
    landed = [
        e for e in run.store.read_events()
        if e["type"] == "seat_attempt" and e["seat_id"] == "s1"
    ]
    assert [e["outcome"] for e in landed] == ["VALID"], "a first-try success went unrecorded"


def test_truncated_verdict_is_evidence_incomplete_never_agree(isolated_store):
    script = {
        ("s1", 1): _report(DIAG),
        ("s2", 1): _report("x"),
        ("s3", 1): _report("x"),
    }
    for round_no in (2, 3, 4, 5):
        script[("s1", round_no)] = _report("VERDICT: AGREE")
        script[("s2", round_no)] = _report("VERDICT: AGREE")
        # Truncated mid-thought: verdict line never arrives.
        script[("s3", round_no)] = _report("The candidate looks plausible but I ran out of")
    seats = ScriptedSeats(script)
    run = CouncilOrchestrator(problem="p", seats=_bench(), seat_turn=seats, max_rounds=5)
    outcome = run.run()
    assert outcome["result"] == "no_convergence", "2x AGREE + 1 truncated must never converge"
    inconclusive = [e for e in run.store.read_events() if e["type"] == "round_inconclusive"]
    assert inconclusive and inconclusive[0]["missing"] == ["s3"]
    assert "cap" in outcome["detail"]


def test_tally_law_unanimity_small_majority_large(isolated_store):
    # Four voting seats, 3 AGREE / 1 DISAGREE: unanimity required — must NOT converge.
    bench4 = _bench() + [Seat("s4", "verifier", "m", True)]
    script = {(f"s{i}", 1): _report(DIAG if i == 1 else "x") for i in range(1, 5)}
    for round_no in (2, 3):
        for i in range(1, 4):
            script[(f"s{i}", round_no)] = _report("VERDICT: AGREE")
        script[("s4", round_no)] = _report("VERDICT: DISAGREE")
    run4 = CouncilOrchestrator(problem="p", seats=bench4, seat_turn=ScriptedSeats(script), max_rounds=3)
    assert run4.run()["result"] == "no_convergence"

    # Five voting seats, 3 AGREE / 2 DISAGREE: strict majority — converges.
    bench5 = bench4 + [Seat("s5", "adjudicator", "m", True)]
    script5 = {(f"s{i}", 1): _report(DIAG if i == 1 else "x") for i in range(1, 6)}
    for i in range(1, 4):
        script5[(f"s{i}", 2)] = _report("VERDICT: AGREE")
    script5[("s4", 2)] = _report("VERDICT: DISAGREE")
    script5[("s5", 2)] = _report("VERDICT: DISAGREE")
    outcome = CouncilOrchestrator(problem="p", seats=bench5, seat_turn=ScriptedSeats(script5)).run()
    assert outcome["result"] == "adjudicated" and outcome["agree"] == 3 and outcome["disagree"] == 2


def test_adjudicator_diet_never_contains_workspace_or_exhibits(isolated_store):
    bench = _bench() + [Seat("s4", "adjudicator", "m", True)]
    script = {(f"s{i}", 1): _report(DIAG if i == 1 else "x") for i in range(1, 5)}
    for i in range(1, 5):
        script[(f"s{i}", 2)] = _report("VERDICT: AGREE")
    seats = ScriptedSeats(script)
    CouncilOrchestrator(
        problem="p", seats=bench, seat_turn=seats,
        exhibits="secret raw log dump", workspace_root="/work/space",
    ).run()
    adjudicator_prompts = [seats.prompts[("s4", 1)], seats.prompts[("s4", 2)]]
    for prompt in adjudicator_prompts:
        assert "/work/space" not in prompt, "the adjudicator weighs reports, never the workspace"
        assert "secret raw log dump" not in prompt, "exhibits are outside the adjudicator's diet"
    assert "secret raw log dump" in seats.prompts[("s1", 1)], "the builder's diet includes exhibits"


def test_exhibits_replace_blindness_note_as_the_starting_point(isolated_store):
    seats = ScriptedSeats({
        ("s1", 1): _report(DIAG),
        ("s2", 1): _report("x"),
        ("s3", 1): _report("x"),
        ("s1", 2): _report("VERDICT: AGREE"),
        ("s2", 2): _report("VERDICT: AGREE"),
        ("s3", 2): _report("VERDICT: AGREE"),
    })
    CouncilOrchestrator(
        problem="p", seats=_bench(), seat_turn=seats, exhibits="here is the log: line 42 leaks",
    ).run()
    r1 = seats.prompts[("s1", 1)]
    assert "EXHIBIT A" in r1 and "line 42 leaks" in r1
    assert "deliberately blind" not in r1, "operator-supplied proof means round 1 starts from it"


def test_convene_contract_violations_are_typed(isolated_store):
    fn = ScriptedSeats({})
    with pytest.raises(CouncilRunError, match="problem"):
        CouncilOrchestrator(problem="   ", seats=_bench(), seat_turn=fn)
    with pytest.raises(CouncilRunError, match="voting seat"):
        CouncilOrchestrator(problem="p", seats=[Seat("a1", "verifier", "m", False)], seat_turn=fn)
    with pytest.raises(CouncilRunError, match="unknown council roles"):
        CouncilOrchestrator(problem="p", seats=[Seat("s1", "prophet", "m", True)], seat_turn=fn)
    with pytest.raises(CouncilRunError, match="unique"):
        CouncilOrchestrator(
            problem="p",
            seats=[Seat("s1", "builder", "m", True), Seat("s1", "reviewer", "m", True)],
            seat_turn=fn,
        )


def test_stop_request_ends_the_run_typed(isolated_store):
    seats = ScriptedSeats({
        ("s1", 1): _report(DIAG),
        ("s2", 1): _report("x"),
        ("s3", 1): _report("x"),
    })
    run = CouncilOrchestrator(problem="p", seats=_bench(), seat_turn=seats)
    run.request_stop()
    outcome = run.run()
    assert outcome["result"] == "stopped"
    assert seats.calls == [], "a stopped run must not dispatch any seat"


def test_verdict_parser_is_anti_truncation_strict():
    assert parse_verdict("prose\nVERDICT: AGREE") == "AGREE"
    assert parse_verdict("prose\nVERDICT: DISAGREE\n\n") == "DISAGREE"
    assert parse_verdict("VERDICT: AGREE\nbut actually let me add") is None
    assert parse_verdict("VERDICT: AGREE with caveats") is None
    assert parse_verdict("verdict: agree") is None
    assert parse_verdict("") is None


def test_diagnosis_parse_and_unstructured_fallback():
    candidate, source = parse_diagnosis("noise\nDIAGNOSIS: the cache is stale\nFIX: flush")
    assert candidate == "the cache is stale" and source == "diagnosis_line"
    candidate, source = parse_diagnosis("just prose about things")
    assert source == "unstructured" and candidate.startswith("just prose")


def test_state_file_tracks_live_progress_and_report_hashes(isolated_store):
    seats = ScriptedSeats({
        ("s1", 1): _report(DIAG),
        ("s2", 1): _report("x"),
        ("s3", 1): _report("x"),
        ("s1", 2): _report("VERDICT: AGREE"),
        ("s2", 2): _report("VERDICT: AGREE"),
        ("s3", 2): _report("VERDICT: AGREE"),
    })
    run = CouncilOrchestrator(problem="p", seats=_bench(), seat_turn=seats)
    run.run()
    state = run.store.read_state()
    assert state["state"] == "converged"
    assert len(state["rounds"]) == 2
    first_report = state["rounds"][0]["reports"][0]
    assert first_report["report_sha256"] and len(first_report["report_sha256"]) == 64
    assert state["seats"][0]["role_id"] == "builder"
