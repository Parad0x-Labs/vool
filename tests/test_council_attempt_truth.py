"""Council attempt truth: typed outcomes, bounded retry, ordered ledger retrieval.

Every scenario drives the REAL orchestrator/run-store/API through an injected seat-turn
double. The double is scripted PER ATTEMPT, not per (seat, round): that is the whole
point — a seat whose first attempt malforms and whose second attempt lands is
indistinguishable from a seat that landed first time unless the runtime actually counts
attempts, so the double hands out a different answer on each call.

Worst cases first, per CLAUDE.md 0.4: the adversarial verdict-on-the-second-last-line
report, the seat that never stops malforming, the ledger written before sequence numbers
existed, and a cursor pointed past the end.
"""

from __future__ import annotations

import json

import pytest

from core.council.orchestrator import CouncilOrchestrator, Seat
from core.council.run_store import CouncilRunStore


@pytest.fixture()
def isolated_store(tmp_path, monkeypatch):
    def _patched(*parts):
        base = tmp_path / "data"
        base.mkdir(parents=True, exist_ok=True)
        out = base
        for part in parts:
            out = out / part
        out.mkdir(parents=True, exist_ok=True)
        return out

    monkeypatch.setattr("core.council.run_store.data_path", _patched)
    return tmp_path


def _bench():
    return [
        Seat("s1", "builder", "model-a", True),
        Seat("s2", "falsifier", "model-b", True),
        Seat("s3", "reviewer", "model-c", True),
    ]


def _report(text, receipts=0):
    return {"text": text, "receipt_count": receipts, "session_id": None}


DIAG = "DIAGNOSIS: the ledger writer allocates no sequence, so replay order is a guess."


class PerAttemptSeats:
    """Seat turns scripted per ATTEMPT: script[(seat_id, round_no)] is a LIST consumed
    one entry per call. A list shorter than the attempts made repeats its last entry, so
    a seat that must never satisfy its contract is written once and holds forever."""

    def __init__(self, script):
        self.script = {key: list(value) for key, value in script.items()}
        self.calls: list[tuple[str, int]] = []

    def __call__(self, seat, prompt, round_no, run_id):
        key = (seat.seat_id, round_no)
        self.calls.append(key)
        queue = self.script[key]
        entry = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(entry, Exception):
            raise entry
        return entry

    def attempts_for(self, seat_id, round_no):
        return self.calls.count((seat_id, round_no))


def _seat_row(state, seat_id, round_no):
    for round_state in state["rounds"]:
        if round_state["round_no"] != round_no:
            continue
        for report in round_state["reports"]:
            if report["seat_id"] == seat_id:
                return report
    raise AssertionError(f"no report row for {seat_id} in round {round_no}")


# --------------------------------------------------------------- classification ----

def test_malformed_voting_verdict_retries_the_seat_not_the_whole_round(isolated_store):
    """A voting seat whose adjudication report carries no verdict is RE-ASKED. Today the
    whole bench is re-dispatched for the next round instead — every other seat pays for
    one seat's malformed answer."""
    seats = PerAttemptSeats({
        ("s1", 1): [_report(DIAG)],
        ("s2", 1): [_report("looked")],
        ("s3", 1): [_report("looked")],
        ("s1", 2): [_report("VERDICT: AGREE")],
        ("s2", 2): [_report("VERDICT: AGREE")],
        # First attempt forgets the verdict line; the re-ask lands it.
        ("s3", 2): [_report("The candidate reads plausible to me."), _report("VERDICT: AGREE")],
    })
    run = CouncilOrchestrator(problem="p", seats=_bench(), seat_turn=seats, max_rounds=5)
    outcome = run.run()

    assert seats.attempts_for("s3", 2) == 2, "the malformed seat was not re-asked"
    assert seats.attempts_for("s1", 2) == 1, "a peer paid for s3's malformed report"
    assert seats.attempts_for("s2", 2) == 1, "a peer paid for s3's malformed report"
    assert outcome["result"] == "adjudicated", outcome
    assert outcome["rounds"] == 2, "the round was burned instead of the seat being re-asked"
    assert not [e for e in run.store.read_events() if e["type"] == "round_inconclusive"]


def test_verdict_on_the_second_last_line_is_malformed_and_never_agree(isolated_store):
    """Adversarial near-miss: the verdict IS present, just not last. The anti-truncation
    law says only the final non-empty line counts — so this is MALFORMED, it is re-asked,
    and it must never be tallied as AGREE."""
    trailing = "VERDICT: AGREE\nThanks for reading my report!"
    seats = PerAttemptSeats({
        ("s1", 1): [_report(DIAG)],
        ("s2", 1): [_report("looked")],
        ("s3", 1): [_report("looked")],
        ("s1", 2): [_report("VERDICT: AGREE")],
        ("s2", 2): [_report("VERDICT: AGREE")],
        ("s3", 2): [_report(trailing)],  # holds forever: never satisfies the contract
    })
    run = CouncilOrchestrator(problem="p", seats=_bench(), seat_turn=seats, max_rounds=2)
    run.run()

    row = _seat_row(run.store.read_state(), "s3", 2)
    assert row["outcome"] == "MALFORMED", row
    assert row["verdict"] is None, "a non-final verdict line was tallied — anti-truncation law broken"
    assert seats.attempts_for("s3", 2) == 2, "the malformed report was not re-asked"


def test_builder_investigation_without_a_diagnosis_is_malformed_and_reasked(isolated_store):
    """The builder's structural contract in an investigate round is the DIAGNOSIS line.
    Prose that merely reads like a diagnosis is MALFORMED, not a candidate."""
    seats = PerAttemptSeats({
        ("s1", 1): [_report("I think the writer races the reader."), _report(DIAG)],
        ("s2", 1): [_report("looked")],
        ("s3", 1): [_report("looked")],
        ("s1", 2): [_report("VERDICT: AGREE")],
        ("s2", 2): [_report("VERDICT: AGREE")],
        ("s3", 2): [_report("VERDICT: AGREE")],
    })
    run = CouncilOrchestrator(problem="p", seats=_bench(), seat_turn=seats, max_rounds=3)
    run.run()

    assert seats.attempts_for("s1", 1) == 2, "the unstructured builder report was not re-asked"
    state = run.store.read_state()
    assert state["candidate_source"] == "diagnosis_line"
    assert _seat_row(state, "s1", 1)["outcome"] == "VALID"


# ---------------------------------------------------------------- bounded retry ----

def test_failed_then_successful_seat_exposes_retries_used_in_durable_state(isolated_store):
    seats = PerAttemptSeats({
        ("s1", 1): [_report(DIAG)],
        ("s2", 1): [RuntimeError("provider hung up"), _report("looked, second try")],
        ("s3", 1): [_report("looked")],
        ("s1", 2): [_report("VERDICT: AGREE")],
        ("s2", 2): [_report("VERDICT: AGREE")],
        ("s3", 2): [_report("VERDICT: AGREE")],
    })
    run = CouncilOrchestrator(problem="p", seats=_bench(), seat_turn=seats, max_rounds=3)
    run.run()

    row = _seat_row(run.store.read_state(), "s2", 1)
    assert row["status"] == "landed"
    assert row["outcome"] == "VALID"
    assert row["attempts"] == 2
    assert row["retries_used"] == 1
    first = _seat_row(run.store.read_state(), "s1", 1)
    assert first["attempts"] == 1 and first["retries_used"] == 0, (
        "a seat that landed first time must record one attempt and zero retries"
    )


def test_retry_is_bounded_by_named_configuration_and_never_unbounded(isolated_store):
    from core.council.attempts import RetryPolicy

    seats = PerAttemptSeats({
        ("s1", 1): [_report(DIAG)],
        ("s2", 1): [RuntimeError("always dead")],
        ("s3", 1): [_report("looked")],
    })
    policy = RetryPolicy(max_attempts_per_seat=3)
    run = CouncilOrchestrator(
        problem="p", seats=_bench(), seat_turn=seats, retry_policy=policy, max_rounds=2
    )
    run.run()

    assert seats.attempts_for("s2", 1) == 3, "the configured bound was not honoured"
    row = _seat_row(run.store.read_state(), "s2", 1)
    assert row["attempts"] == 3 and row["retries_used"] == 2
    assert row["outcome"] == "FAILED"

    with pytest.raises(ValueError):
        RetryPolicy(max_attempts_per_seat=0)
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts_per_seat=10_000)


def test_retry_policy_should_retry_is_itself_bounded():
    """The bound is enforced in TWO places — the dispatch loop's own condition and this
    predicate. Pinning the predicate directly matters: without this, deleting the bound
    from `should_retry` is absorbed by the loop and no test moves, which would leave a
    dead guard reading as a live one."""
    from core.council.attempts import AttemptOutcome, RetryPolicy

    policy = RetryPolicy(max_attempts_per_seat=2)
    assert policy.should_retry(AttemptOutcome.MALFORMED, 1) is True
    assert policy.should_retry(AttemptOutcome.MALFORMED, 2) is False, "the bound was ignored"
    assert policy.should_retry(AttemptOutcome.FAILED, 2) is False, "the bound was ignored"
    # A stop is the operator's decision; a re-ask past it would overrule them.
    assert policy.should_retry(AttemptOutcome.CANCELLED, 1) is False
    assert policy.should_retry(AttemptOutcome.VALID, 1) is False
    assert policy.max_retries_per_seat == 1


def test_every_attempt_emits_exactly_one_ordered_event(isolated_store):
    seats = PerAttemptSeats({
        ("s1", 1): [_report(DIAG)],
        ("s2", 1): [RuntimeError("once"), _report("looked")],
        ("s3", 1): [_report("looked")],
        ("s1", 2): [_report("VERDICT: AGREE")],
        ("s2", 2): [_report("VERDICT: AGREE")],
        ("s3", 2): [_report("VERDICT: AGREE")],
    })
    run = CouncilOrchestrator(problem="p", seats=_bench(), seat_turn=seats, max_rounds=3)
    run.run()

    events = run.store.read_events()
    attempts = [e for e in events if e["type"] == "seat_attempt"]
    assert len(attempts) == len(seats.calls), (
        f"{len(seats.calls)} seat turns produced {len(attempts)} attempt events"
    )
    outcomes = [(e["seat_id"], e["round_no"], e["attempt"], e["outcome"]) for e in attempts]
    assert ("s2", 1, 1, "FAILED") in outcomes
    assert ("s2", 1, 2, "VALID") in outcomes
    assert ("s1", 1, 1, "VALID") in outcomes, "a successful first attempt went unrecorded"

    seqs = [e["seq"] for e in events]
    assert seqs == list(range(1, len(events) + 1)), "ledger sequence is not gapless from 1"


# --------------------------------------------------------------- ledger ordering ----

def test_legacy_ledger_without_seq_stays_readable_in_documented_file_order(isolated_store):
    """A ledger written before sequence numbers existed is READ in file order and never
    rewritten. Nothing is backfilled on disk; the derived ordinal is labelled as derived,
    and the first new append continues past the legacy lines instead of colliding."""
    from core.council.run_store import data_path

    run_id = "council-legacyrun01"
    ledger = data_path("council") / f"{run_id}.events.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    legacy = [
        {"ts": 1.0, "run_id": run_id, "type": "convened"},
        {"ts": 2.0, "run_id": run_id, "type": "round_opened", "round_no": 1},
        {"ts": 3.0, "run_id": run_id, "type": "round_landed", "round_no": 1},
    ]
    ledger.write_text("\n".join(json.dumps(row) for row in legacy) + "\n", encoding="utf-8")
    before = ledger.read_text(encoding="utf-8")

    store = CouncilRunStore(run_id)
    ordered = store.read_events_ordered()
    assert [e["type"] for e in ordered] == ["convened", "round_opened", "round_landed"]
    assert [e["seq"] for e in ordered] == [1, 2, 3]
    assert all(e["seq_source"] == "file_order" for e in ordered)

    store.append_event("converged", rounds=1)
    assert ledger.read_text(encoding="utf-8").startswith(before), (
        "stored history was rewritten — legacy lines must never be backfilled"
    )
    ordered = store.read_events_ordered()
    assert [e["seq"] for e in ordered] == [1, 2, 3, 4], "the new append collided with file order"
    assert ordered[-1]["seq_source"] == "stored"
    assert json.loads(ledger.read_text(encoding="utf-8").splitlines()[0]).get("seq") is None


def test_sequence_survives_a_fresh_store_instance(isolated_store):
    """The API reads a run through a NEW CouncilRunStore. Allocation must come from the
    ledger, not from per-instance memory, or two writers restart the sequence at 1."""
    run_id = "council-freshinst01"
    CouncilRunStore(run_id).append_event("convened")
    CouncilRunStore(run_id).append_event("round_opened", round_no=1)
    CouncilRunStore(run_id).append_event("round_landed", round_no=1)
    seqs = [e["seq"] for e in CouncilRunStore(run_id).read_events_ordered()]
    assert seqs == [1, 2, 3], seqs


# ------------------------------------------------------------------ events API ----

@pytest.fixture()
def isolated_council(tmp_path, monkeypatch):
    def _patched(*parts):
        base = tmp_path / "data"
        base.mkdir(parents=True, exist_ok=True)
        out = base
        for part in parts:
            out = out / part
        out.mkdir(parents=True, exist_ok=True)
        return out

    monkeypatch.setattr("core.council.run_store.data_path", _patched)
    return tmp_path


def _get(path, host, query=None):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    res = dispatch_get(path=path, query=query or {}, runtime=RuntimeServices(display_name="N"),
                       model_name="vool", client_host=host)
    return res.status, json.loads(res.body.decode("utf-8"))


def _seeded_run(run_id="council-eventsapi01"):
    store = CouncilRunStore(run_id)
    store.write_state({"state": "converged", "problem": "p", "seats": [], "rounds": []})
    for index in range(1, 6):
        store.append_event("round_opened", round_no=index)
    return store


def test_events_endpoint_is_owner_local(isolated_council):
    _seeded_run()
    status, payload = _get("/api/council/events", "10.0.0.9", {"run": ["council-eventsapi01"]})
    assert status == 403 and payload["error"] == "owner_local_required"


def test_events_after_cursor_returns_only_later_events(isolated_council):
    _seeded_run()
    status, payload = _get("/api/council/events", "127.0.0.1", {"run": ["council-eventsapi01"]})
    assert status == 200 and payload["ok"]
    assert [e["seq"] for e in payload["events"]] == [1, 2, 3, 4, 5]
    assert payload["next_after"] == 5

    status, payload = _get("/api/council/events", "127.0.0.1",
                           {"run": ["council-eventsapi01"], "after": ["3"]})
    assert status == 200
    assert [e["seq"] for e in payload["events"]] == [4, 5], "the after cursor was ignored"

    status, payload = _get("/api/council/events", "127.0.0.1",
                           {"run": ["council-eventsapi01"], "after": ["99"]})
    assert status == 200 and payload["events"] == []
    assert payload["next_after"] == 99, "a cursor past the end must round-trip, not reset"


def test_events_endpoint_rejects_invalid_run_and_after_typed(isolated_council):
    _seeded_run()
    for query, expected in (
        ({}, 400),                                                  # no run
        ({"run": ["  "]}, 400),                                     # blank run
        ({"run": ["../../etc/passwd"]}, 400),                       # traversal
        ({"run": ["council-nosuchrun"]}, 404),                      # unknown run
        ({"run": ["council-eventsapi01"], "after": ["-1"]}, 400),   # negative cursor
        ({"run": ["council-eventsapi01"], "after": ["abc"]}, 400),  # non-numeric cursor
    ):
        status, payload = _get("/api/council/events", "127.0.0.1", query)
        assert status == expected, (query, status, payload)
        assert payload["ok"] is False and payload["error"]


def test_no_production_module_depends_on_the_dead_turn_manager():
    """The typed outcomes live in `core.council.attempts`, NOT in `turn_manager`.

    `turn_manager` is a different topology (N advisors -> one sealed judge) reachable
    from no production caller. Importing it here to save writing an enum would put the
    live council on a code path nothing else exercises — and would make deleting the
    dead module a breaking change.
    """
    import pathlib
    import re

    # An IMPORT, not a mention: this module's own docstring names turn_manager to say
    # why it is not imported, and a grep that cannot tell those apart would forbid
    # documenting the decision.
    importer = re.compile(r"^\s*(?:from\s+[\w.]*turn_manager|import\s+[\w.]*turn_manager)", re.M)
    root = pathlib.Path(__file__).resolve().parent.parent
    offenders = [
        str(path.relative_to(root))
        for path in sorted(root.glob("core/**/*.py")) + sorted(root.glob("apps/**/*.py"))
        if path.name != "turn_manager.py" and importer.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"production now imports the dead turn_manager: {offenders}"


def test_status_and_runs_clients_are_unchanged_by_the_events_addition(isolated_council):
    _seeded_run()
    status, payload = _get("/api/council/status", "127.0.0.1", {"run": ["council-eventsapi01"]})
    assert status == 200 and payload["ok"] and payload["run"]["state"] == "converged"
    status, payload = _get("/api/council/runs", "127.0.0.1")
    assert status == 200 and payload["ok"] and payload["runs"]
