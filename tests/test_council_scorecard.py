"""C5 — what the council actually decided, and whose contributions carried it.

"Council completed" tells the operator nothing: not what was agreed, not when, not who
proposed the thing that won, not what is still disputed. The temptation is a popularity
score — ask the models who did best, or rank by how much prose each produced. Both invent
a number and dress it as a finding.

Everything here is derived from the structured record the council already writes:
`candidate_set` says which SEAT's diagnosis became the candidate, `candidate_rejected`
says which seat's receipt-backed counterexample killed one, `tally` says who agreed and
who did not, and the terminal event says what was committed. No prose is measured, no
similarity is computed, and no model is asked to rank itself.

Worst cases first: a real tie, a run with no structured evidence at all, a seat that only
ever failed, a seat whose model was replaced mid-run, and a run still paused.
"""

from __future__ import annotations

import json

import pytest

from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get

RUN = "council-scorecard01"


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


SEATS = [
    {"seat_id": "s1", "role_id": "builder", "model": "vendor/alpha", "votes": True},
    {"seat_id": "s2", "role_id": "falsifier", "model": "vendor/beta", "votes": True},
    {"seat_id": "s3", "role_id": "reviewer", "model": "vendor/gamma", "votes": True},
]
CANDIDATE = "the snapshot and the record are the same bytes, twice"


def _report(store, seat_id, round_no, *, status="landed", verdict=None, text="",
            outcome="VALID", attempts=1, retries=0, counterexample=None, backed=False,
            model_requested="", failure=None, superseded=False, receipts=1):
    for attempt in range(1, attempts + 1):
        store.append_event("seat_attempt", seat_id=seat_id, round_no=round_no,
                           attempt=attempt, outcome=outcome if attempt == attempts else "FAILED",
                           error=None)
    return store.append_event(
        "seat_report", seat_id=seat_id, round_no=round_no, status=status, outcome=outcome,
        attempts=attempts, retries_used=retries, verdict=verdict, counterexample=counterexample,
        counterexample_backed=backed, receipt_count=receipts, session_id=None,
        model_requested=model_requested, model_actual=None, model_evidence="unknown",
        failure=failure, superseded=superseded, report_sha256=None, text=text, phase="x",
    )


def _converged_run(store, *, agree=3, disagree=0):
    """Round 1 investigate (s1 proposes), round 2 adjudicate (everyone agrees)."""
    store.append_event("convened", problem="p", seats=SEATS, chat_session="c")
    store.append_event("round_opened", round_no=1, phase="investigate")
    _report(store, "s1", 1, text=f"DIAGNOSIS: {CANDIDATE}", model_requested="vendor/alpha")
    _report(store, "s2", 1, text="looked around", model_requested="vendor/beta")
    _report(store, "s3", 1, text="read it", model_requested="vendor/gamma")
    store.append_event("round_landed", round_no=1, phase="investigate")
    store.append_event("candidate_set", round_no=1, candidate=CANDIDATE,
                       source="diagnosis_line", from_seat="s1")
    store.append_event("round_opened", round_no=2, phase="adjudicate")
    verdicts = ["AGREE"] * agree + ["DISAGREE"] * disagree
    for seat, verdict in zip(["s1", "s2", "s3"], verdicts, strict=True):
        _report(store, seat, 2, verdict=verdict, text=f"VERDICT: {verdict}")
    store.append_event("round_landed", round_no=2, phase="adjudicate")
    store.append_event("tally", round_no=2, agree=agree, disagree=disagree,
                       voting_seats=3, converged=disagree == 0)
    store.append_event("converged", candidate=CANDIDATE, candidate_source="diagnosis_line",
                       agree=agree, disagree=disagree, rounds=2, authority_note="n")
    store.write_state({"state": "converged", "seats": SEATS, "max_rounds": 5,
                       "outcome": {"result": "adjudicated", "candidate": CANDIDATE,
                                   "agree": agree, "disagree": disagree, "rounds": 2}})


def _card(store):
    from core.council.scorecard import build_scorecard

    return build_scorecard(store.run_id)


def _seat(card, seat_id):
    return next(s for s in card["seats"] if s["seat_id"] == seat_id)


# ------------------------------------------------- 1. who actually carried the run


def test_a_converged_run_names_the_seat_whose_claim_was_adopted(store):
    _converged_run(store)
    card = _card(store)

    assert card["run"]["terminal_state"] == "converged"
    assert card["run"]["agreement_first_reached_round"] == 2
    assert card["run"]["agreement_strength"] == {"agree": 3, "disagree": 0, "voting_seats": 3}
    assert card["run"]["adopted_conclusions"] == [CANDIDATE]
    assert card["run"]["models_used"] == ["vendor/alpha", "vendor/beta", "vendor/gamma"]

    builder = _seat(card, "s1")
    assert builder["role_id"] == "builder" and builder["model"] == "vendor/alpha"
    assert builder["claims_proposed"] == 1
    assert builder["claims_adopted"] == 1
    assert builder["claims_supported"] == 2, "the other two seats agreed with its claim"
    assert builder["claims_refuted"] == 0 and builder["claims_unresolved"] == 0
    assert builder["valid_attempts"] == 2 and builder["failures"] == 0

    most = card["run"]["most_adopted"]
    assert most["seat_ids"] == ["s1"]
    assert most["claims_adopted"] == 1
    assert "most adopted contributions" in most["label"].lower(), most["label"]
    assert most["basis"], "a ranking with no stated basis is a popularity score"


def test_every_ranking_carries_the_counts_it_was_derived_from(store):
    _converged_run(store)
    card = _card(store)
    most = card["run"]["most_adopted"]
    assert "best" not in json.dumps(card).lower(), (
        "no surface may claim a model is best — only that its contributions were adopted"
    )
    assert "winner" not in json.dumps(card).lower()
    assert str(most["claims_adopted"]) in most["basis"]
    for seat in card["seats"]:
        for key in ("claims_proposed", "claims_adopted", "claims_supported",
                    "claims_refuted", "claims_unresolved", "valid_attempts",
                    "retries", "failures"):
            assert isinstance(seat[key], int), (seat["seat_id"], key)


# ------------------------------------------------------------ 2. ties stay ties


def test_a_real_tie_stays_a_tie(store):
    """Two seats each get a claim adopted. Naming one of them would be a coin toss with
    a scoreboard drawn around it."""
    store.append_event("convened", problem="p", seats=SEATS, chat_session="c")
    first, second = "the first cause", "the second cause"
    store.append_event("round_opened", round_no=1, phase="investigate")
    _report(store, "s1", 1, text=f"DIAGNOSIS: {first}")
    _report(store, "s2", 1, text=f"DIAGNOSIS: {second}")
    store.append_event("candidate_set", round_no=1, candidate=first,
                       source="diagnosis_line", from_seat="s1")
    store.append_event("tally", round_no=2, agree=2, disagree=0, voting_seats=2,
                       converged=True)
    store.append_event("converged", candidate=first, agree=2, disagree=0, rounds=2)
    # A second adoption must be a second claim UNDER ADJUDICATION when its converged
    # event lands — verdicts bind to the chain's active claim, never to raw text, so
    # a tie is two claims each genuinely committed, not two matching strings.
    store.append_event("candidate_set", round_no=3, candidate=second,
                       source="diagnosis_line", from_seat="s2")
    store.append_event("tally", round_no=4, agree=2, disagree=0, voting_seats=2,
                       converged=True)
    store.append_event("converged", candidate=second, agree=2, disagree=0, rounds=4)
    store.write_state({"state": "converged", "seats": SEATS,
                       "outcome": {"result": "adjudicated", "candidate": second}})
    card = _card(store)
    most = card["run"]["most_adopted"]
    assert sorted(most["seat_ids"]) == ["s1", "s2"], most
    assert most["tied"] is True
    assert "tied" in most["label"].lower(), most["label"]


def test_no_structured_evidence_produces_insufficient_evidence_not_a_winner(store):
    """A run where nothing was proposed, adopted or refuted. There is no contributor to
    name, and inventing one is exactly the failure this whole surface exists to avoid."""
    store.append_event("convened", problem="p", seats=SEATS, chat_session="c")
    store.append_event("round_opened", round_no=1, phase="investigate")
    _report(store, "s1", 1, text="I could not find anything conclusive.")
    _report(store, "s2", 1, text="Nor could I.")
    store.append_event("no_convergence", max_rounds=5)
    store.write_state({"state": "no_convergence", "seats": SEATS, "outcome": {}})
    card = _card(store)
    most = card["run"]["most_adopted"]
    assert most["seat_ids"] == []
    assert most["insufficient_evidence"] is True
    assert "insufficient evidence" in most["label"].lower(), most["label"]
    assert card["run"]["adopted_conclusions"] == []
    assert card["run"]["agreement_first_reached_round"] is None


# --------------------------------------------------- 3. refutation, and reliability


def test_a_refuted_claim_is_recorded_against_the_seat_that_proposed_it(store):
    store.append_event("convened", problem="p", seats=SEATS, chat_session="c")
    wrong = "the cache is stale"
    store.append_event("candidate_set", round_no=1, candidate=wrong,
                       source="diagnosis_line", from_seat="s1")
    _report(store, "s2", 2, counterexample="the cache is empty on this path", backed=True,
            text="COUNTEREXAMPLE: the cache is empty on this path", verdict="DISAGREE")
    store.append_event("candidate_rejected", round_no=2, candidate=wrong,
                       counterexamples=[{"seat_id": "s2",
                                         "claim": "the cache is empty on this path"}])
    store.append_event("candidate_set", round_no=3, candidate=CANDIDATE,
                       source="diagnosis_line", from_seat="s3")
    store.append_event("tally", round_no=4, agree=2, disagree=0, voting_seats=2, converged=True)
    store.append_event("converged", candidate=CANDIDATE, agree=2, disagree=0, rounds=4)
    store.write_state({"state": "converged", "seats": SEATS,
                       "outcome": {"result": "adjudicated", "candidate": CANDIDATE}})
    card = _card(store)

    assert _seat(card, "s1")["claims_refuted"] == 1
    assert _seat(card, "s1")["claims_adopted"] == 0
    assert _seat(card, "s2")["refutations_landed"] == 1, (
        "killing a wrong candidate IS a contribution, not a black mark"
    )
    assert _seat(card, "s3")["claims_adopted"] == 1
    refuted = card["run"]["substantially_refuted"]
    assert refuted["seat_ids"] == ["s1"]
    assert refuted["claims_refuted"] == 1
    assert "refuted" in refuted["label"].lower()
    assert "worst" not in json.dumps(card).lower()


def test_a_seat_that_only_ever_failed_is_never_called_the_least_contributor(store):
    """Reliability and contribution quality are different dimensions. A seat whose
    provider was down proposed nothing — that is not the same as being wrong."""
    store.append_event("convened", problem="p", seats=SEATS, chat_session="c")
    store.append_event("candidate_set", round_no=1, candidate=CANDIDATE,
                       source="diagnosis_line", from_seat="s1")
    _report(store, "s3", 1, status="failed", outcome="TIMED_OUT", attempts=2, retries=1,
            failure="TimeoutError: seat read timed out", text="", receipts=0)
    store.append_event("tally", round_no=2, agree=2, disagree=0, voting_seats=2, converged=True)
    store.append_event("converged", candidate=CANDIDATE, agree=2, disagree=0, rounds=2)
    store.write_state({"state": "converged", "seats": SEATS,
                       "outcome": {"result": "adjudicated", "candidate": CANDIDATE}})
    card = _card(store)

    dead = _seat(card, "s3")
    assert dead["failures"] == 1 and dead["timeouts"] == 1
    assert dead["claims_proposed"] == 0 and dead["claims_refuted"] == 0
    assert dead["valid_attempts"] == 0
    assert card["run"]["substantially_refuted"]["seat_ids"] == [], (
        "a seat that never got to speak was ranked as though it had been wrong"
    )
    assert dead["reliability_note"], "a failed seat's reliability must be stated separately"
    assert "refuted" not in dead["reliability_note"].lower()


# ------------------------------------------- 4. attribution survives C4 operator acts


def test_a_replaced_model_keeps_the_attribution_of_what_the_old_model_did(store):
    """C4 lets an operator put a different model behind a seat. The claim the FIRST model
    proposed stays attributed to that model — the seat is the role, and a scorecard that
    credits the replacement for its predecessor's work is a fabricated provenance."""
    store.append_event("convened", problem="p", seats=SEATS, chat_session="c")
    store.append_event("candidate_set", round_no=1, candidate=CANDIDATE,
                       source="diagnosis_line", from_seat="s2")
    _report(store, "s2", 1, text=f"DIAGNOSIS: {CANDIDATE}", model_requested="vendor/beta")
    store.append_event("seat_model_replaced", seat_id="s2", round_no=2,
                       model_from="vendor/beta", model_to="vendor/delta")
    _report(store, "s2", 2, verdict="AGREE", text="VERDICT: AGREE",
            model_requested="vendor/delta")
    store.append_event("tally", round_no=2, agree=3, disagree=0, voting_seats=3, converged=True)
    store.append_event("converged", candidate=CANDIDATE, agree=3, disagree=0, rounds=2)
    store.write_state({"state": "converged",
                       "seats": [{**s, "model": "vendor/delta"} if s["seat_id"] == "s2" else s
                                 for s in SEATS],
                       "outcome": {"result": "adjudicated", "candidate": CANDIDATE}})
    card = _card(store)
    seat = _seat(card, "s2")
    assert seat["model"] == "vendor/delta", "the seat's CURRENT model is what it runs now"
    assert seat["claims_adopted"] == 1
    attributed = seat["claim_attribution"]
    assert attributed == [{
        "claim_id": attributed[0]["claim_id"],
        "round_no": 1,
        "model_requested": "vendor/beta",
        "model_actual": None,
        "model_evidence": "unknown",
        "adopted": True,
    }], (
        "the adopted claim must stay credited to the model that actually proposed it, "
        "with the request and the (here unproven) actual kept separate: " + repr(attributed)
    )
    assert "vendor/beta" in json.dumps(card), "the replaced model vanished from the record"


# ------------------------------------------------------- 5. disputes stay visible


def test_remaining_disagreements_name_their_parties_and_carry_their_text(store):
    store.append_event("convened", problem="p", seats=SEATS, chat_session="c")
    store.append_event("candidate_set", round_no=1, candidate=CANDIDATE,
                       source="diagnosis_line", from_seat="s1")
    _report(store, "s1", 2, verdict="AGREE", text="VERDICT: AGREE")
    _report(store, "s2", 2, verdict="AGREE", text="VERDICT: AGREE")
    _report(store, "s3", 2, verdict="DISAGREE",
            counterexample="it still reproduces on the reload path", backed=False,
            text="COUNTEREXAMPLE: it still reproduces on the reload path\nVERDICT: DISAGREE")
    store.append_event("tally", round_no=2, agree=2, disagree=1, voting_seats=3, converged=False)
    store.append_event("no_convergence", max_rounds=2)
    store.write_state({"state": "no_convergence", "seats": SEATS, "outcome": {}})
    card = _card(store)

    disputes = card["run"]["remaining_disagreements"]
    assert len(disputes) == 1, disputes
    assert disputes[0]["seat_id"] == "s3"
    assert disputes[0]["role_id"] == "reviewer"
    assert disputes[0]["round_no"] == 2
    assert disputes[0]["verdict"] == "DISAGREE"
    assert "it still reproduces on the reload path" in disputes[0]["counterexample"]
    assert disputes[0]["receipt_backed"] is False, (
        "an unverified counterexample must be labelled as one, not promoted"
    )


# ------------------------------------------------------ 6. durable, not in-memory


def test_the_scorecard_is_rebuilt_from_the_ledger_with_no_run_state_at_all(store):
    """Requirement: reload reconstructs the same scorecard from durable evidence. The
    state file is a VIEW; deleting it must change nothing."""
    _converged_run(store)
    before = _card(store)
    store._state_path().unlink()
    after = _card(store)
    assert after == before, "the scorecard needed the snapshot, so it was never durable"
    assert after["run"]["most_adopted"]["seat_ids"] == ["s1"]


def test_the_terminal_run_writes_its_scorecard_into_the_ledger(store):
    from core.council.scorecard import persist_scorecard

    _converged_run(store)
    assert persist_scorecard(store.run_id) is True
    assert persist_scorecard(store.run_id) is False, "a second write would duplicate the record"
    rows = [r for r in store.read_events_ordered() if r["type"] == "scorecard"]
    assert len(rows) == 1
    assert rows[0]["run"]["most_adopted"]["seat_ids"] == ["s1"]


def test_a_paused_run_is_provisional_and_says_so(store):
    store.append_event("convened", problem="p", seats=SEATS, chat_session="c")
    store.append_event("candidate_set", round_no=1, candidate=CANDIDATE,
                       source="diagnosis_line", from_seat="s1")
    _report(store, "s2", 1, status="failed", outcome="FAILED", attempts=2, retries=1,
            failure="RuntimeError: dead", receipts=0)
    store.append_event("needs_attention", round_no=1, blocking_seats=[{"seat_id": "s2"}])
    store.write_state({"state": "needs_attention", "seats": SEATS,
                       "outcome": {"result": "needs_attention"}})
    card = _card(store)
    assert card["run"]["provisional"] is True
    assert "not final" in card["run"]["provisional_note"].lower(), card["run"]
    assert card["run"]["terminal_state"] == "needs_attention"
    assert card["run"]["most_adopted"]["insufficient_evidence"] is True, (
        "nothing has been committed yet, so nothing has been adopted"
    )


def test_the_scorecard_endpoint_serves_the_ledger_derived_card(store):
    _converged_run(store)
    res = dispatch_get(path="/api/council/scorecard", query={"run": [RUN]},
                       runtime=RuntimeServices(display_name="N"), model_name="vool",
                       client_host="127.0.0.1")
    assert res.status == 200
    payload = json.loads(res.body.decode("utf-8"))
    assert payload["ok"] is True
    assert payload["scorecard"]["run"]["most_adopted"]["seat_ids"] == ["s1"]
    remote = dispatch_get(path="/api/council/scorecard", query={"run": [RUN]},
                          runtime=RuntimeServices(display_name="N"), model_name="vool",
                          client_host="203.0.113.9")
    assert remote.status == 403, "the scorecard is owner-local like every council surface"


def test_prose_length_and_confidence_never_enter_the_score(store):
    """The cheap fake: rank by who wrote most. A seat that produced one adopted claim in
    twelve words must outrank one that produced none in two thousand."""
    store.append_event("convened", problem="p", seats=SEATS, chat_session="c")
    store.append_event("candidate_set", round_no=1, candidate=CANDIDATE,
                       source="diagnosis_line", from_seat="s1")
    _report(store, "s1", 1, text=f"DIAGNOSIS: {CANDIDATE}")
    _report(store, "s2", 1, text="I am extremely confident. " * 400)
    store.append_event("tally", round_no=2, agree=2, disagree=0, voting_seats=2, converged=True)
    store.append_event("converged", candidate=CANDIDATE, agree=2, disagree=0, rounds=2)
    store.write_state({"state": "converged", "seats": SEATS,
                       "outcome": {"result": "adjudicated", "candidate": CANDIDATE}})
    card = _card(store)
    assert card["run"]["most_adopted"]["seat_ids"] == ["s1"]
    assert _seat(card, "s2")["claims_adopted"] == 0
    blob = json.dumps(card)
    assert "confiden" not in blob.lower(), "confidence theatre reached the record"
    for seat in card["seats"]:
        assert "characters" not in seat and "length" not in seat, (
            "prose volume is not a contribution measure"
        )
