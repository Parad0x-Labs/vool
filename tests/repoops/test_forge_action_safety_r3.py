"""Revision-3 mutation-safety coverage: the three review-v2 classes with DIFFERENT data,
real-ledger competing claims, novel conversion shapes, and recovery/restart evidence.

Every case crosses the production door and drives the shipped adapters through recorded
transports; no live forge is contacted."""
from __future__ import annotations

import json

import pytest

from tests.repoops._harness import context, door
from tests.repoops.test_forge_actions import (
    _armed_session,
    _calls,
    _comment_payload,
    _operator_authorizes,
    _pr_payload,
    world,
)


def _plan(world, ctx, sid, *, action, **extra):
    planned = door("repo.pr.request", {"repo_session_id": sid, "action": action, **extra}, ctx)
    assert planned.ok, planned.response_text
    return planned.details["action_hash"]


# ---------------------------------------------------------------------------
# Class 1 -- the durable dispatch CLAIM (novel: the OTHER action, a REAL competing
# claim against the real ledger, and the winner's reservation is never touched)
# ---------------------------------------------------------------------------


def test_lost_claim_on_update_action_never_cancels_the_winners_reservation(world, monkeypatch) -> None:
    """Different action than the review's create/comment probes. A REAL competitor claims the
    effect in the durable ledger first (the actual compare-and-set, returning True to the
    winner); the plane's subsequent real claim returns False. The plane must write nothing,
    cancel nothing, and the winner's row must stay exactly as the winner left it."""
    from core import runtime_continuity as continuity

    root, _bare, forge = world
    ctx = context(root)
    sid, sha, base = _armed_session(world, ctx)
    forge.route("GET /pulls/8", _pr_payload("8", base_sha=base, head_sha=sha, body="older"))
    action_hash = _plan(world, ctx, sid, action="update", number="8", body="recovery wording")
    assert _operator_authorizes(world, sid, action_hash)["ok"]
    forge.route("PATCH /pulls/8", _pr_payload("8", base_sha=base, head_sha=sha, body="recovery wording"))

    real = continuity.mark_effect_dispatched
    verdicts: list[bool] = []

    def competing_then_losing(**kwargs):
        # TWO REAL claims against the real ledger, same effect row: the first is the
        # competitor's and WINS the compare-and-set; the second is the plane's and LOSES it.
        # The wrapper hands the plane only its own (losing) verdict.
        winner = bool(real(**kwargs))
        loser = bool(real(**kwargs))
        verdicts.extend([winner, loser])
        assert winner is True and loser is False, "the real CAS must behave as a claim race"
        return loser

    monkeypatch.setattr(continuity, "mark_effect_dispatched", competing_then_losing)
    result = door("repo.pr.update", {"repo_session_id": sid}, ctx)
    monkeypatch.undo()

    assert result.ok is False and result.status == "claim_lost_to_another_executor", result.details
    assert not _calls(forge, method="PATCH")
    # The winner's row was never cancelled or released by the loser: the plane's own claim
    # returned False, and the reservation remains claimed.
    assert verdicts == [True, False]
    from core.runtime_continuity import _conn as continuity_conn  # the existing ledger, inspected read-only

    with continuity_conn() as conn:
        row = conn.execute(
            "SELECT state, claimed_by FROM runtime_unresolved_effects ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
    assert row is not None and row[0] == "dispatched", row  # the winner's claim stands


def test_lost_claim_does_not_flip_the_record_to_rearmable(world, monkeypatch) -> None:
    """The losing executor cannot assert the winner sent nothing: the record stays pending
    (dispatching) and ordinary re-authorization is refused -- unlike a KNOWN-unsent storage
    failure, which is re-armable (proven below)."""
    from core import runtime_continuity as continuity

    root, _bare, forge = world
    ctx = context(root)
    sid, sha, base = _armed_session(world, ctx)
    action_hash = _plan(world, ctx, sid, action="comment", number="17", subject="issue", body="lost claim words")
    assert _operator_authorizes(world, sid, action_hash)["ok"]

    monkeypatch.setattr(continuity, "mark_effect_dispatched", lambda **kwargs: False)
    result = door("repo.pr.comment", {"repo_session_id": sid}, ctx)
    assert result.status == "claim_lost_to_another_executor"
    assert not _calls(forge, method="POST", contains="/issues/17/comments")
    refused = _operator_authorizes(world, sid, action_hash)
    assert refused["ok"] is False and refused["status"] == "resolution_required", refused


# ---------------------------------------------------------------------------
# Class 2 -- the WHOLE accepted-response conversion boundary (novel shapes/actions)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("create", dict(_pr_payload("31", base_sha="a" * 40, head_sha="b" * 40), base="main-branch-name")),
        ("update", dict(_pr_payload("8", base_sha="a" * 40, head_sha="b" * 40), base=17)),
    ],
)
def test_novel_malformed_shapes_after_success_stay_unknown(world, kind, payload) -> None:
    """Different action+field combos than the review's head/user probes: a CREATE whose base
    is a bare string and an UPDATE whose base is a number. Valid JSON, accepted 2xx, typed
    conversion fails -- the outcome must stay unknown and ordinary re-arm must be refused."""
    root, _bare, forge = world
    ctx = context(root)
    sid, sha, base_sha = _armed_session(world, ctx)
    if kind == "create":
        args = {"action": "create", "base_ref": "main", "title": "shape probe", "body": "shape probe body"}
        route = "POST /pulls"
        action = "create"
    else:
        forge.route("GET /pulls/8", _pr_payload("8", base_sha=base_sha, head_sha=sha, body="older"))
        args = {"action": "update", "number": "8", "body": "shape probe update"}
        route = "PATCH /pulls/8"
        action = "update"
    action_hash = _plan(world, ctx, sid, **args)
    assert _operator_authorizes(world, sid, action_hash)["ok"]
    forge.route("GET /pulls?head=", [])
    forge.route(route, payload, status=201)

    first = door(f"repo.pr.{action}", {"repo_session_id": sid}, ctx)
    assert first.ok is False and first.status == "unknown_reconciliation_required", first.details
    assert first.details.get("reason") == "accepted_reply_undecodable"
    assert _operator_authorizes(world, sid, action_hash)["status"] == "resolution_required"
    retry = door(f"repo.pr.{action}", {"repo_session_id": sid}, ctx)
    assert retry.ok is False
    wire_method = "PATCH" if action == "update" else "POST"
    assert len(_calls(forge, method=wire_method)) == 1


def test_gitlab_merge_request_conversion_shape_is_wrapped() -> None:
    """Novel provider+field: a GitLab merge-request reply whose diff_refs is a number fails
    conversion AFTER acceptance; the typed error must be ForgeAcceptedUnreadableError."""
    from core.kas.adapters.gitlab import GitLabForgeAdapter
    from core.kas.contract import AdapterConfig, ForgeAcceptedUnreadableError, KasResponse

    body = {
        "iid": 61, "title": "Draft: x", "state": "opened", "target_branch": "main",
        "source_branch": "h", "sha": "b" * 40, "diff_refs": 99,
    }
    adapter = GitLabForgeAdapter(
        config=AdapterConfig(provider_id="gitlab", base_url="https://forge.example.test/api", namespace="g/p"),
        transport=lambda req: KasResponse(status=201, body=json.dumps(body).encode()),
    )
    with pytest.raises(ForgeAcceptedUnreadableError):
        adapter.create_pull_request(title="x", body="y", head_ref="h", base_ref="main", draft=True)


def test_plane_post_accept_processing_failure_stays_unknown(world, monkeypatch) -> None:
    """A LOCAL processing failure AFTER the forge accepted (here: the verification step
    itself) can never downgrade an accepted write to safe-to-resend: unknown, blocked,
    one POST."""
    from core.repoops.plane import RepoOpsRuntime

    root, _bare, forge = world
    ctx = context(root)
    sid, sha, base = _armed_session(world, ctx)
    action_hash = _plan(world, ctx, sid, action="comment", number="17", subject="issue", body="processing probe")
    assert _operator_authorizes(world, sid, action_hash)["ok"]
    forge.route("POST /issues/17/comments", _comment_payload(9003, body="processing probe"))

    def exploding_verify(**kwargs):
        raise RuntimeError("Synthetic local processing failure after accept")

    monkeypatch.setattr(RepoOpsRuntime, "_verify_and_record", exploding_verify)
    first = door("repo.pr.comment", {"repo_session_id": sid}, ctx)
    monkeypatch.undo()
    assert first.ok is False and first.status == "unknown_reconciliation_required", first.details
    assert first.details.get("reason") == "post_accept_processing_failure"
    assert len(_calls(forge, method="POST", contains="/issues/17/comments")) == 1
    assert _operator_authorizes(world, sid, action_hash)["status"] == "resolution_required"


# ---------------------------------------------------------------------------
# Class 3 -- known-unsent recovery (novel: the session-journal variant, restart,
# and the exact-one-mutation guarantee with different data)
# ---------------------------------------------------------------------------


def test_session_journal_failure_recovers_same_intent_exactly_once(world, monkeypatch) -> None:
    """The write-ahead journal (not the marker store) fails before dispatch: known unsent,
    re-armable AS THE SAME ACTION HASH once healthy -- the user never rewrites content."""
    from core.repoops.plane import repo_ops_runtime

    root, _bare, forge = world
    ctx = context(root)
    sid, sha, base = _armed_session(world, ctx)
    action_hash = _plan(world, ctx, sid, action="create", base_ref="main", title="recovery title", body="recovery body")
    assert _operator_authorizes(world, sid, action_hash)["ok"]
    forge.route("GET /pulls?head=", [])

    def exploding_persist(session):
        raise RuntimeError("Synthetic session journal outage")

    # Scoped restore: a bare monkeypatch.undo() here also reverted the world fixture's
    # VOOL_REPOOPS_DIR, silently re-rooting the rest of this test onto a different journal
    # directory (masked while journal writes were unconditional).
    with monkeypatch.context() as outage:
        outage.setattr(repo_ops_runtime(), "_persist", exploding_persist)
        first = door("repo.pr.create", {"repo_session_id": sid}, ctx)
    assert first.ok is False and first.status == "session_journal_unavailable", first.details
    assert not _calls(forge, method="POST", contains="/repos/o/r/pulls")

    rearm = _operator_authorizes(world, sid, action_hash)
    assert rearm["ok"], rearm
    forge.route("POST /pulls", _pr_payload("61", base_sha=base, head_sha=sha, draft=True,
                                           title="recovery title", body="recovery body"))
    resumed = door("repo.pr.create", {"repo_session_id": sid}, ctx)
    assert resumed.ok, resumed.response_text
    assert resumed.details["number"] == "61"
    assert len(_calls(forge, method="POST", contains="/repos/o/r/pulls")) == 1


def test_known_unsent_state_survives_a_restart_and_recovers(world, monkeypatch) -> None:
    """Restart evidence: the known-unsent state is read back from disk, re-authorization is
    ordinary (no resolution hoop), and the eventual execution is exactly one."""
    from core import runtime_continuity as continuity
    from core.repoops.plane import repo_ops_runtime, session_dir

    root, _bare, forge = world
    ctx = context(root)
    sid, sha, base = _armed_session(world, ctx)
    comment_text = "restart recovery probe"
    action_hash = _plan(world, ctx, sid, action="comment", number="17", subject="issue", body=comment_text)
    assert _operator_authorizes(world, sid, action_hash)["ok"]
    forge.route("POST /issues/17/comments", _comment_payload(9400, body=comment_text))

    def unavailable(**kwargs):
        raise RuntimeError("Synthetic dispatch-marker storage outage")

    # Scoped restore (NOT monkeypatch.undo(), which would also revert the world fixture's
    # environment): only this one attribute is patched and restored around the outage.
    original_marker = continuity.mark_effect_dispatched
    continuity.mark_effect_dispatched = unavailable
    try:
        first = door("repo.pr.comment", {"repo_session_id": sid}, ctx)
    finally:
        continuity.mark_effect_dispatched = original_marker
    assert first.status == "effect_journal_unavailable"
    assert not _calls(forge, method="POST", contains="/issues/17/comments")

    # The daemon restarts: the cached session is gone, the on-disk journal is truth.
    repo_ops_runtime()._sessions.pop(sid, None)
    payload = json.loads((session_dir() / f"{sid}.json").read_text(encoding="utf-8"))
    assert payload["forge_actions"][action_hash]["status"] == "failed_safe_to_retry"
    assert payload["forge_actions"][action_hash].get("failure_kind") == "dispatch_marker_failure"

    rearm = _operator_authorizes(world, sid, action_hash)
    assert rearm["ok"], rearm
    resumed = door("repo.pr.comment", {"repo_session_id": sid}, ctx)
    assert resumed.ok and resumed.details["comment_id"] == "9400"
    assert len(_calls(forge, method="POST", contains="/issues/17/comments")) == 1


# ---------------------------------------------------------------------------
# r4: BOUNDED ACTUAL CONTENTION -- two real executors, one claim, one POST
# ---------------------------------------------------------------------------


DURABLE_REFUSALS = {
    "claim_lost_to_another_executor",
    "duplicate_effect_blocked",
    "authorization_consumed",
    "not_authorized",
    "plan_mismatch",
    "unknown_reconciliation_required",
    "reconciliation_mismatch",
}


def _forge_record(sid: str, action_hash: str) -> dict:
    from core.repoops.plane import session_dir

    return json.loads((session_dir() / f"{sid}.json").read_text(encoding="utf-8"))["forge_actions"][action_hash]


def _ledger_states(leid: str) -> list[str]:
    from core.runtime_continuity import logical_effect_row_states

    return sorted(str(row["state"]) for row in logical_effect_row_states(leid))


def _captured(outcome) -> dict:
    details = dict(getattr(outcome, "details", {}) or {})
    return {
        "ok": bool(outcome.ok),
        "status": str(outcome.status),
        "replayed": bool(details.get("replayed")),
        "comment_id": str(details.get("comment_id") or ""),
    }


def _comment_session(world, body: str, comment_id: int):
    from tests.repoops._harness import context as _context

    root, _bare, forge = world
    ctx = _context(root)
    sid, _sha, _base = _armed_session(world, ctx)
    planned = door(
        "repo.pr.request",
        {"repo_session_id": sid, "action": "comment", "number": "17", "subject": "issue", "body": body},
        ctx,
    )
    assert planned.ok, planned.response_text
    assert _operator_authorizes(world, sid, planned.details["action_hash"])["ok"]
    forge.route("POST /issues/17/comments", _comment_payload(comment_id, body=body))
    return ctx, sid, planned.details["action_hash"]


def _assert_one_applied_dispatch(forge, sid: str, action_hash: str, *, body: str, comment_id: str) -> None:
    posts = _calls(forge, method="POST", contains="/issues/17/comments")
    assert len(posts) == 1, [p["url"] for p in posts]
    assert json.loads(posts[0]["body"]) == {"body": body}
    record = _forge_record(sid, action_hash)
    assert record["status"] == "applied" and str(record["result"]["comment_id"]) == comment_id, record
    assert _ledger_states(record["logical_effect_id"]).count("applied") == 1, _ledger_states(record["logical_effect_id"])


def test_actual_two_executor_contention_yields_exactly_one_post(world) -> None:
    """Two REAL executors dispatch the SAME authorized action concurrently: both threads run the
    genuine plane code against the genuine ledger and recorded transport. Whatever the interleaving,
    the durable claim admits exactly ONE dispatch, the forge sees exactly one POST whose body is the
    authorized content, and the journal and ledger hold that one applied result. The other caller
    either was refused at a durable boundary or -- having arrived after the winner journaled --
    received the winner's recorded result as a replay (`replayed: true`, the same comment id), which
    sends nothing. Every result's replay flag and result identity are captured, so a second success
    counts only as a verified replay, never blindly. Bounded: the barrier starts the race, a join
    ends it.

    Revision 6 correction: this test used to assert that exactly one caller returned ok. A run in the
    revision-5 review failed that assertion with `[(True, 'ok'), (True, 'ok')]` while its one-POST and
    approved-body assertions passed; the capture had no replay flag, so it could not show whether the
    second success was a replay. The late-caller interleaving is now reproduced deterministically
    (`test_a_caller_arriving_after_the_winner_journaled_replays_the_recorded_result`) and this
    assertion accepts a second success only with that replay identity proven."""
    import threading

    _root, _bare, forge = world
    ctx, sid, action_hash = _comment_session(world, "contention probe", 9700)

    results: list[dict] = []
    results_lock = threading.Lock()
    start = threading.Barrier(2)

    def executor():
        start.wait()
        outcome = door("repo.pr.comment", {"repo_session_id": sid}, ctx)
        with results_lock:
            results.append(_captured(outcome))

    threads = [threading.Thread(target=executor) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert all(not thread.is_alive() for thread in threads), "a racing executor hung"
    assert len(results) == 2, results

    _assert_one_applied_dispatch(forge, sid, action_hash, body="contention probe", comment_id="9700")
    winners = [r for r in results if r["ok"] and not r["replayed"]]
    assert len(winners) == 1, results
    assert winners[0]["comment_id"] == "9700", results
    other = next(r for r in results if r is not winners[0])
    if other["ok"]:
        assert other["replayed"] is True and other["comment_id"] == winners[0]["comment_id"], results
    else:
        assert other["status"] in DURABLE_REFUSALS, results


def test_a_caller_arriving_while_the_winner_is_sending_is_refused_durably(world) -> None:
    """Deterministic overlap: the winner is held INSIDE its forge POST while a second executor asks
    for the same authorized action. The second caller sees the durable in-flight dispatch and is
    refused without sending; released, the winner lands its one POST; a caller after it replays."""
    import threading

    from core.repoops import forge as forge_bridge

    _root, _bare, forge = world
    ctx, sid, action_hash = _comment_session(world, "overlap probe", 9711)
    entered, release = threading.Event(), threading.Event()

    def gated_factory(*, config=None, source_context=None):
        send = forge.factory(config=config, source_context=source_context)

        def _send(request):
            if request.method.upper() == "POST" and "/issues/17/comments" in request.url:
                entered.set()
                assert release.wait(timeout=30), "the overlap probe was never released"
            return send(request)

        return _send

    forge_bridge.install_transport_factory(gated_factory)
    winner: list[dict] = []
    thread = threading.Thread(target=lambda: winner.append(_captured(door("repo.pr.comment", {"repo_session_id": sid}, ctx))))
    thread.start()
    try:
        assert entered.wait(timeout=30), "the winner never reached its POST"
        overlapping = _captured(door("repo.pr.comment", {"repo_session_id": sid}, ctx))
    finally:
        release.set()
        thread.join(timeout=60)
    assert not thread.is_alive(), "the winner hung"
    assert overlapping["ok"] is False and overlapping["status"] in DURABLE_REFUSALS, overlapping
    assert winner and winner[0]["ok"] and not winner[0]["replayed"] and winner[0]["comment_id"] == "9711", winner
    _assert_one_applied_dispatch(forge, sid, action_hash, body="overlap probe", comment_id="9711")
    late = _captured(door("repo.pr.comment", {"repo_session_id": sid}, ctx))
    assert late["ok"] and late["replayed"] and late["comment_id"] == "9711", late
    _assert_one_applied_dispatch(forge, sid, action_hash, body="overlap probe", comment_id="9711")


def test_a_caller_arriving_after_the_winner_journaled_replays_the_recorded_result(world) -> None:
    """The interleaving the review's run captured as two successes, reproduced in order: the second
    caller arrives after the winner journaled. It succeeds as a REPLAY of the recorded result -- the
    same comment id, `replayed: true` -- and sends nothing."""
    _root, _bare, forge = world
    ctx, sid, action_hash = _comment_session(world, "late caller probe", 9722)
    first = _captured(door("repo.pr.comment", {"repo_session_id": sid}, ctx))
    second = _captured(door("repo.pr.comment", {"repo_session_id": sid}, ctx))
    assert first["ok"] and not first["replayed"] and first["comment_id"] == "9722", first
    assert second["ok"] and second["replayed"] and second["comment_id"] == "9722", second
    _assert_one_applied_dispatch(forge, sid, action_hash, body="late caller probe", comment_id="9722")
