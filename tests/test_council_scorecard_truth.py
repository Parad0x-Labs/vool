"""C5 truth — claim identity, text-collision honesty, and proven provenance.

The scorecard's verdicts used to bind by TEXT: a claim was "adopted" when its text matched
the converged event's candidate text, "refuted" when its text appeared in a rejection. Two
seats can propose the identical wording, and one string then carried two claims' fates: the
seat whose claim was never under adjudication was credited or refuted alongside the one that
was. Claims also had no identity of their own and attribution showed only the model the
operator REQUESTED — the model that provably answered lives in the same report row.

These tests pin the repaired contract: durable claim ids, verdicts bound to the claim that
was actually under adjudication (chain position, never text), requested and proven-actual
models reported as separate facts, and the visible winner attribution naming the model that
produced the adopted claim — not the seat's current replacement.
"""

from __future__ import annotations

import pytest

RUN = "council-scorecard-truth01"

SEATS = [
    {"seat_id": "s1", "role_id": "builder", "model": "vendor/alpha", "votes": True},
    {"seat_id": "s2", "role_id": "falsifier", "model": "vendor/beta", "votes": True},
    {"seat_id": "s3", "role_id": "reviewer", "model": "vendor/gamma", "votes": True},
]
CLAIM_TEXT = "the two writes serialize through the same WAL"


@pytest.fixture()
def store(tmp_path, monkeypatch):
    def _patched(*parts):
        out = tmp_path / "council-data"
        for part in parts:
            out = out / part
        out.mkdir(parents=True, exist_ok=True)
        return out

    monkeypatch.setattr("core.council.run_store.data_path", _patched)
    from core.council.run_store import CouncilRunStore

    return CouncilRunStore(RUN)


def _report(store, seat_id, round_no, *, verdict=None, text="", model_requested="",
            model_actual=None, model_evidence="unknown", counterexample=None, backed=False):
    return store.append_event(
        "seat_report", seat_id=seat_id, round_no=round_no, status="landed", outcome="VALID",
        attempts=1, retries_used=0, verdict=verdict, counterexample=counterexample,
        counterexample_backed=backed, receipt_count=1, session_id=None,
        model_requested=model_requested, model_actual=model_actual,
        model_evidence=model_evidence, failure=None, superseded=False,
        report_sha256=None, text=text, phase="x",
    )


def _card(store):
    from core.council.scorecard import build_scorecard

    return build_scorecard(store.run_id)


def _seat(card, seat_id):
    return next(s for s in card["seats"] if s["seat_id"] == seat_id)


def test_identical_text_from_two_seats_cannot_double_credit(store):
    """s1 proposes the wording in round 1 and it is REJECTED. Round 3: s2 proposes the
    identical wording and the council converges on it. Only s2's claim was under
    adjudication when the run converged — s1's identical text must not ride along."""
    store.append_event("convened", problem="p", seats=SEATS, chat_session="c")
    store.append_event("candidate_set", round_no=1, candidate=CLAIM_TEXT,
                       source="diagnosis_line", from_seat="s1")
    _report(store, "s1", 1, text=f"DIAGNOSIS: {CLAIM_TEXT}", model_requested="vendor/alpha")
    store.append_event(
        "candidate_rejected", round_no=2, candidate=CLAIM_TEXT,
        counterexamples=[{"seat_id": "s3", "claim": "writes interleave in the observer"}],
    )
    store.append_event("candidate_set", round_no=3, candidate=CLAIM_TEXT,
                       source="diagnosis_line", from_seat="s2")
    _report(store, "s2", 3, text=f"DIAGNOSIS: {CLAIM_TEXT}", model_requested="vendor/beta")
    store.append_event("tally", round_no=4, agree=3, disagree=0, voting_seats=3, converged=True)
    store.append_event("converged", candidate=CLAIM_TEXT, candidate_source="diagnosis_line",
                       agree=3, disagree=0, rounds=4, authority_note="n")
    store.write_state({"state": "converged", "seats": SEATS, "max_rounds": 5, "outcome": {}})

    card = _card(store)
    assert _seat(card, "s2")["claims_adopted"] == 1, "the claim that was adjudicated adopted"
    assert _seat(card, "s1")["claims_adopted"] == 0, (
        "identical wording from an earlier, REJECTED claim must not double-credit"
    )
    assert _seat(card, "s1")["claims_refuted"] == 1


def test_identical_text_cannot_double_refute(store):
    """Mirror image: s1's claim is rejected; round 3 opens a NEW claim with the same
    wording from s3 which is still unresolved when the operator stops the run. The
    unresolved claim must not inherit the earlier rejection of its twin text."""
    store.append_event("convened", problem="p", seats=SEATS, chat_session="c")
    store.append_event("candidate_set", round_no=1, candidate=CLAIM_TEXT,
                       source="diagnosis_line", from_seat="s1")
    store.append_event(
        "candidate_rejected", round_no=2, candidate=CLAIM_TEXT,
        counterexamples=[{"seat_id": "s2", "claim": "the WAL is bypassed under load"}],
    )
    store.append_event("candidate_set", round_no=3, candidate=CLAIM_TEXT,
                       source="diagnosis_line", from_seat="s3")
    store.append_event("stopped", reason="operator")
    store.write_state({"state": "stopped", "seats": SEATS, "max_rounds": 5, "outcome": {}})

    card = _card(store)
    assert _seat(card, "s1")["claims_refuted"] == 1
    assert _seat(card, "s3")["claims_refuted"] == 0, (
        "a later claim with identical wording must not inherit its twin's rejection"
    )
    assert _seat(card, "s3")["claims_unresolved"] == 1


def test_claims_carry_durable_and_unique_ids(store):
    store.append_event("convened", problem="p", seats=SEATS, chat_session="c")
    for round_no, seat in ((1, "s1"), (2, "s2")):
        store.append_event("candidate_set", round_no=round_no, candidate=f"claim {round_no}",
                           source="diagnosis_line", from_seat=seat)
    store.append_event("stopped", reason="operator")
    store.write_state({"state": "stopped", "seats": SEATS, "max_rounds": 5, "outcome": {}})

    first = _card(store)
    second = _card(store)  # rebuilt from the same ledger
    ids_first = {a["claim_id"] for s in first["seats"] for a in s["claim_attribution"]}
    ids_second = {a["claim_id"] for s in second["seats"] for a in s["claim_attribution"]}
    assert len(ids_first) == 2, "one id per claim, none shared"
    assert ids_first == ids_second, "ids are durable: same ledger in, same ids out"
    assert all(str(i).startswith("clm:") for i in ids_first), "ids are namespaced claim ids"


def test_requested_and_proven_actual_model_stay_separate(store):
    store.append_event("convened", problem="p", seats=SEATS, chat_session="c")
    store.append_event("candidate_set", round_no=1, candidate=CLAIM_TEXT,
                       source="diagnosis_line", from_seat="s1")
    _report(store, "s1", 1, text=f"DIAGNOSIS: {CLAIM_TEXT}", model_requested="vendor/asked-for",
            model_actual="vendor/answered", model_evidence="streamed")
    store.append_event("tally", round_no=2, agree=3, disagree=0, voting_seats=3, converged=True)
    store.append_event("converged", candidate=CLAIM_TEXT, agree=3, disagree=0, rounds=2)
    store.write_state({"state": "converged", "seats": SEATS, "max_rounds": 5, "outcome": {}})

    attribution = _seat(_card(store), "s1")["claim_attribution"][0]
    assert attribution["model_requested"] == "vendor/asked-for"
    assert attribution["model_actual"] == "vendor/answered"
    assert attribution["model_evidence"] == "streamed"
    assert attribution["model_requested"] != attribution["model_actual"]


def test_the_visible_winner_names_the_model_that_produced_the_adopted_claim(store):
    """s2's claim is adopted in round 1 under vendor/beta; the operator then replaces the
    seat's model, and the run converges. The winner attribution must name the model that
    PRODUCED the adopted claim — with the proven actual model when evidence exists — never
    the seat's current replacement."""
    store.append_event("convened", problem="p", seats=SEATS, chat_session="c")
    store.append_event("candidate_set", round_no=1, candidate=CLAIM_TEXT,
                       source="diagnosis_line", from_seat="s2")
    _report(store, "s2", 1, text=f"DIAGNOSIS: {CLAIM_TEXT}", model_requested="vendor/asked-beta",
            model_actual="vendor/proven-beta", model_evidence="streamed")
    store.append_event("seat_model_replaced", seat_id="s2", round_no=2,
                       model_from="vendor/beta", model_to="vendor/delta")
    store.append_event("tally", round_no=2, agree=3, disagree=0, voting_seats=3, converged=True)
    store.append_event("converged", candidate=CLAIM_TEXT, agree=3, disagree=0, rounds=2)
    store.write_state({"state": "converged",
                       "seats": [{**s, "model": "vendor/delta"} if s["seat_id"] == "s2" else s
                                 for s in SEATS],
                       "outcome": {}})

    card = _card(store)
    adopted = card["run"]["most_adopted"]["adopted_claims"]
    assert len(adopted) == 1
    assert adopted[0]["seat_id"] == "s2"
    assert adopted[0]["produced_by"] == "vendor/proven-beta", (
        "winner attribution uses the model that produced the adopted claim"
    )
    assert adopted[0]["produced_by_is_proven"] is True
    assert adopted[0]["model_requested"] == "vendor/asked-beta"
    assert "vendor/delta" not in {c["produced_by"] for c in adopted}


def test_an_unproven_model_is_labelled_not_silently_promoted(store):
    store.append_event("convened", problem="p", seats=SEATS, chat_session="c")
    store.append_event("candidate_set", round_no=1, candidate=CLAIM_TEXT,
                       source="diagnosis_line", from_seat="s1")
    _report(store, "s1", 1, text=f"DIAGNOSIS: {CLAIM_TEXT}", model_requested="vendor/asked",
            model_actual=None, model_evidence="unknown")
    store.append_event("tally", round_no=2, agree=3, disagree=0, voting_seats=3, converged=True)
    store.append_event("converged", candidate=CLAIM_TEXT, agree=3, disagree=0, rounds=2)
    store.write_state({"state": "converged", "seats": SEATS, "max_rounds": 5, "outcome": {}})

    adopted = _card(store)["run"]["most_adopted"]["adopted_claims"][0]
    assert adopted["produced_by"] == "vendor/asked", (
        "with no proven actual, the request is the best honest attribution"
    )
    assert adopted["produced_by_is_proven"] is False
