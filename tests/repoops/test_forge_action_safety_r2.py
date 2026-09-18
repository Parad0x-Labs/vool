"""Revision-2 mutation-safety coverage: the durability preconditions, the replay verdict, the
reconciliation truth and the accepted-but-undecodable outcome -- each with different data than
the review fixtures, plus the restart/concurrency evidence the safety contract claims.

Everything crosses the production door, drives the shipped adapters through recorded
transports, and mints authorization through the SERVED operator surface. No live forge."""
from __future__ import annotations

import json

import pytest

from tests.repoops._forge_fixture import RecordedForge
from tests.repoops._harness import context, door
from tests.repoops.test_forge_actions import (
    _armed_session,
    _calls,
    _comment_payload,
    _operator_authorizes,
    _pr_payload,
    world,
)


def _plan_and_authorize(world, ctx, sid, *, action, **extra):
    args = {"repo_session_id": sid, "action": action, **extra}
    planned = door("repo.pr.request", args, ctx)
    assert planned.ok, planned.response_text
    action_hash = planned.details["action_hash"]
    assert _operator_authorizes(world, sid, action_hash)["ok"]
    return action_hash


# ---------------------------------------------------------------------------
# Fix 1 -- durability preconditions (different data/actions than the review fixtures)
# ---------------------------------------------------------------------------


def test_reservation_failure_blocks_update_action_on_different_text(world, monkeypatch) -> None:
    """The review proved create/comment; this is the THIRD action with different content: an
    update whose journal reservation fails must not PATCH."""
    root, _bare, forge = world
    ctx = context(root)
    sid, sha, base = _armed_session(world, ctx)
    forge.route("GET /pulls/8", _pr_payload("8", base_sha=base, head_sha=sha, body="older wording"))
    _plan_and_authorize(world, ctx, sid, action="update", number="8", body="fresh reviewed wording")

    import core.runtime_continuity as continuity

    def unavailable(**kwargs):
        raise RuntimeError("Synthetic durable journal unavailable")

    monkeypatch.setattr(continuity, "reserve_logical_effect", unavailable)
    result = door("repo.pr.update", {"repo_session_id": sid}, ctx)
    assert result.ok is False and result.status == "effect_journal_unavailable"
    assert not _calls(forge, method="PATCH")
    # The refusal is journaled as a fault, and the authorization stays unconsumed: the journal
    # coming back is enough to retry the identical action lawfully.
    assert result.details.get("fault_id")


def test_dispatch_marker_failure_blocks_the_write(world, monkeypatch) -> None:
    """A reservation without a durable DISPATCH marker is a lease crash recovery would replay:
    the marker failing must stop the write before any socket."""
    root, _bare, forge = world
    ctx = context(root)
    sid, _sha, _base = _armed_session(world, ctx)
    _plan_and_authorize(
        world, ctx, sid, action="comment", number="17", subject="issue", body="marker probe text"
    )
    forge.route("POST /issues/17/comments", _comment_payload(9100, body="marker probe text"))

    import core.runtime_continuity as continuity

    def unmarkable(**kwargs):
        raise RuntimeError("Synthetic marker store unavailable")

    monkeypatch.setattr(continuity, "mark_effect_dispatched", unmarkable)
    result = door("repo.pr.comment", {"repo_session_id": sid}, ctx)
    assert result.ok is False and result.status == "effect_journal_unavailable"
    # r3 wording: the durable dispatch CLAIM could not be written ("an unmarked dispatch
    # looks like an expired lease") -- same contract, precise claim vocabulary.
    assert "unmarked dispatch" in result.response_text
    assert not _calls(forge, method="POST", contains="/issues/17/comments")


def test_session_journal_failure_before_dispatch_blocks_the_write(world, monkeypatch) -> None:
    """The write-ahead session row (plan + authorization state) must persist before the write
    leaves the machine; a journal that cannot be written to is a reason not to send."""
    from core.repoops.plane import repo_ops_runtime

    root, _bare, forge = world
    ctx = context(root)
    sid, _sha, _base = _armed_session(world, ctx)
    _plan_and_authorize(world, ctx, sid, action="create", base_ref="main", title="journal probe", body="words")
    forge.route("GET /pulls?head=", [])

    def exploding_persist(session):
        raise RuntimeError("Synthetic session journal unavailable")

    monkeypatch.setattr(repo_ops_runtime(), "_persist", exploding_persist)
    result = door("repo.pr.create", {"repo_session_id": sid}, ctx)
    assert result.ok is False and result.status == "session_journal_unavailable"
    assert not _calls(forge, method="POST", contains="/repos/o/r/pulls")


def test_active_continuity_row_blocks_concurrent_identical_dispatch(world) -> None:
    """Another live dispatcher holds the identical effect (the A6 unique-active index): this
    execute is refused with no POST, even though this session's own journal is clean."""
    from core.runtime_continuity import reserve_logical_effect

    root, _bare, forge = world
    ctx = context(root)
    sid, sha, base = _armed_session(world, ctx)
    import hashlib

    title_sha = hashlib.sha256(b"concurrent probe").hexdigest()
    body_sha = hashlib.sha256(b"concurrent body").hexdigest()
    held = reserve_logical_effect(
        intent="repo.pr.create",
        arguments={"provider": "github", "namespace": "o/r", "head_ref": "feature", "base_ref": "main",
                   "title_sha": title_sha, "body_sha": body_sha},
        resource_identity="github:o/r#pr-create:feature->main",
        expected_evidence={"head_sha": sha},
        session_id="another-live-process",
        reconcilability="reconcilable",
    )
    assert held["outcome"] == "reserved"

    _plan_and_authorize(world, ctx, sid, action="create", base_ref="main", title="concurrent probe", body="concurrent body")
    forge.route("GET /pulls?head=", [])
    forge.route("POST /pulls", _pr_payload("41", base_sha=base, head_sha=sha, draft=True, title="concurrent probe", body="concurrent body"))
    blocked = door("repo.pr.create", {"repo_session_id": sid}, ctx)
    assert blocked.ok is False and blocked.status == "duplicate_effect_blocked"
    assert not _calls(forge, method="POST", contains="/repos/o/r/pulls")


def test_unknown_outcome_survives_a_restart_and_blocks_until_resolved(world) -> None:
    """Restart evidence: the unproven outcome and its block are read back from the journal on
    disk (cache evicted), the ordinary re-authorization is refused across the restart, and the
    operator resolution then resumes the action exactly once."""
    from core.repoops.plane import repo_ops_runtime, session_dir

    root, _bare, forge = world
    ctx = context(root)
    sid, _sha, _base = _armed_session(world, ctx)
    action_hash = _plan_and_authorize(world, ctx, sid, action="comment", number="17", subject="issue", body="restart probe")
    forge.route("POST /issues/17/comments", _comment_payload(9200, body="restart probe"))
    forge.arm_unknown("POST /repos/o/r/issues/17/comments")
    sent = door("repo.pr.comment", {"repo_session_id": sid}, ctx)
    assert sent.status == "unknown_reconciliation_required"

    # The daemon restarts: the in-memory session cache is gone, the journal on disk remains.
    repo_ops_runtime()._sessions.pop(sid, None)
    payload = json.loads((session_dir() / f"{sid}.json").read_text(encoding="utf-8"))
    assert payload["forge_actions"][action_hash]["status"] == "unknown"

    still_blocked = door("repo.pr.comment", {"repo_session_id": sid}, ctx)
    assert still_blocked.ok is False and still_blocked.status == "unknown_reconciliation_required"
    assert _operator_authorizes(world, sid, action_hash)["status"] == "resolution_required"

    assert _operator_authorizes(world, sid, action_hash, resolve="failed_safe_to_retry")["ok"]
    assert _operator_authorizes(world, sid, action_hash)["ok"]
    forge.disarm("POST /repos/o/r/issues/17/comments")
    resumed = door("repo.pr.comment", {"repo_session_id": sid}, ctx)
    assert resumed.ok, resumed.response_text
    assert resumed.details["comment_id"] == "9200"
    assert len(_calls(forge, method="POST", contains="/issues/17/comments")) == 2


# ---------------------------------------------------------------------------
# Fix 2 -- the verification verdict is a journaled fact, not a transient one
# ---------------------------------------------------------------------------


def test_replayed_verification_failure_survives_a_restart(world) -> None:
    from core.repoops.plane import repo_ops_runtime, session_dir

    root, _bare, forge = world
    ctx = context(root)
    sid, sha, base = _armed_session(world, ctx)
    # The plan carries a title AND a body; the forge's answer keeps the body but echoes a
    # DIFFERENT title: the write lands, exact verification fails, and that verdict must persist.
    forge.route("GET /pulls/8", _pr_payload("8", base_sha=base, head_sha=sha, title="old title", body="older wording"))
    planned = door(
        "repo.pr.request",
        {"repo_session_id": sid, "action": "update", "number": "8", "title": "authorized title", "body": "intended new text"},
        ctx,
    )
    assert planned.ok, planned.response_text
    action_hash = planned.details["action_hash"]
    assert _operator_authorizes(world, sid, action_hash)["ok"]
    forge.route(
        "PATCH /pulls/8",
        _pr_payload("8", base_sha=base, head_sha=sha, title="FORGED OTHER TITLE", body="intended new text"),
    )

    first = door("repo.pr.update", {"repo_session_id": sid}, ctx)
    assert first.ok is False and first.status == "verification_mismatch", first.response_text
    assert any("title" in m for m in first.details["mismatches"])

    # Restart: the failed verdict is replayed from the journal, not re-derived or promoted.
    repo_ops_runtime()._sessions.pop(sid, None)
    payload = json.loads((session_dir() / f"{sid}.json").read_text(encoding="utf-8"))
    assert payload["forge_actions"][action_hash]["status"] == "applied"
    assert payload["forge_actions"][action_hash]["result"]["mismatches"]

    replay = door("repo.pr.update", {"repo_session_id": sid}, ctx)
    assert replay.ok is False and replay.status == "verification_mismatch"
    assert replay.details.get("replayed") is True
    assert replay.details["verified"] is False
    assert any("title" in m for m in replay.details["mismatches"])
    assert len(_calls(forge, method="PATCH")) == 1
    # A landed-but-wrong write is not safe to resend: re-arming it is refused outright.
    assert _operator_authorizes(world, sid, action_hash)["status"] == "already_applied"


# ---------------------------------------------------------------------------
# Fix 3 -- reconciliation verifies the AUTHORIZED state, with accurate wording
# ---------------------------------------------------------------------------


def test_reconciliation_with_a_different_title_stays_unproven_and_never_resends(world) -> None:
    """Different mismatching field than the review's draft/body probes: the found PR's TITLE
    differs. No verified success, no replacement POST, no auto-resolution."""
    root, _bare, forge = world
    ctx = context(root)
    sid, sha, base = _armed_session(world, ctx)
    action_hash = _plan_and_authorize(
        world, ctx, sid, action="create", base_ref="main", title="authorized title", body="authorized body"
    )
    forge.route("GET /pulls?head=", [])
    forge.arm_unknown("POST /repos/o/r/pulls")
    first = door("repo.pr.create", {"repo_session_id": sid}, ctx)
    assert first.status == "unknown_reconciliation_required"

    forge.route(
        "GET /pulls?head=",
        [_pr_payload("55", base_sha=base, head_sha=sha, draft=True, title="SOMEONE ELSE'S TITLE", body="authorized body")],
    )
    result = door("repo.pr.create", {"repo_session_id": sid}, ctx)
    assert result.ok is False, (result.status, result.details)
    assert result.details.get("verified") is not True
    assert result.status == "reconciliation_mismatch"
    assert any("title" in m for m in result.details.get("mismatches", []))
    assert len(_calls(forge, method="POST", contains="/repos/o/r/pulls")) == 1
    # Still unproven: ordinary re-authorization is refused until the operator resolves.
    assert _operator_authorizes(world, sid, action_hash)["status"] == "resolution_required"


def test_reconciliation_full_match_words_state_not_causation(world) -> None:
    """When the forge's current state equals the authorized state EXACTLY, the result is
    positive but the wording claims only what the read proves -- and `reconciled` (state
    equality) is distinct from causal claims."""
    root, _bare, forge = world
    ctx = context(root)
    sid, sha, base = _armed_session(world, ctx)
    _plan_and_authorize(
        world, ctx, sid, action="create", base_ref="main", title="authorized title", body="authorized body"
    )
    forge.route("GET /pulls?head=", [])
    forge.arm_unknown("POST /repos/o/r/pulls")
    assert door("repo.pr.create", {"repo_session_id": sid}, ctx).status == "unknown_reconciliation_required"

    forge.route(
        "GET /pulls?head=",
        [_pr_payload("56", base_sha=base, head_sha=sha, draft=True, title="authorized title", body="authorized body")],
    )
    result = door("repo.pr.create", {"repo_session_id": sid}, ctx)
    assert result.ok, result.response_text
    assert result.details["state_matches_authorized"] is True
    assert result.details["number"] == "56"
    assert "consistent with" in result.response_text and "cannot prove" in result.response_text
    assert len(_calls(forge, method="POST", contains="/repos/o/r/pulls")) == 1


def test_update_reconciliation_with_prior_text_stays_unknown(world) -> None:
    """v1 auto-cleared an unknown update when the PR still carried the prior text. The same
    epistemics as the create case apply: one unchanged read is not proof of non-delivery."""
    from tests.repoops._harness import door as _door

    root, _bare, forge = world
    ctx = context(root)
    sid, sha, base = _armed_session(world, ctx)
    forge.route("GET /pulls/8", _pr_payload("8", base_sha=base, head_sha=sha, title="old title", body="old body"))
    planned = _door(
        "repo.pr.request",
        {"repo_session_id": sid, "action": "update", "number": "8", "body": "new body"},
        ctx,
    )
    assert planned.ok
    action_hash = planned.details["action_hash"]
    assert _operator_authorizes(world, sid, action_hash)["ok"]
    forge.arm_unknown("PATCH /repos/o/r/pulls/8")
    sent = door("repo.pr.update", {"repo_session_id": sid}, ctx)
    assert sent.status == "unknown_reconciliation_required"

    # The PR still carries exactly the prior text: unknown stays unknown, nothing is resent.
    result = door("repo.pr.update", {"repo_session_id": sid}, ctx)
    assert result.ok is False and result.status == "unknown_reconciliation_required"
    assert len(_calls(forge, method="PATCH")) == 1
    assert _operator_authorizes(world, sid, action_hash)["status"] == "resolution_required"


# ---------------------------------------------------------------------------
# Fix 4 -- an accepted-but-undecodable reply is an unproven outcome, on both forges
# ---------------------------------------------------------------------------


def test_gitlab_create_with_empty_object_reply_is_unknown(world) -> None:
    """Novel provider AND action: a GitLab merge-request create whose 201 body is `{}` is an
    accepted write with no readable result -- unknown, not refused, and not re-armable."""
    from core.kas.registry import forge_adapter

    forge = RecordedForge()
    forge.route("POST /merge_requests", {}, status=201)
    adapter = forge_adapter("gitlab", namespace="g/p", transport_factory=forge.factory)
    from core.kas.contract import ForgeAcceptedUnreadableError

    with pytest.raises(ForgeAcceptedUnreadableError):
        adapter.create_pull_request(title="Draft: x", body="y", head_ref="h", base_ref="main", draft=True)


def test_github_update_with_broken_json_reply_is_unknown_at_the_boundary(world) -> None:
    from core.kas.contract import ForgeAcceptedUnreadableError
    from core.kas.registry import forge_adapter

    forge = RecordedForge()
    forge.route("PATCH /pulls/8", "{broken", status=200)
    adapter = forge_adapter("github", namespace="o/r", transport_factory=forge.factory)
    with pytest.raises(ForgeAcceptedUnreadableError):
        adapter.update_pull_request("8", body="new text")


def test_plane_keeps_accepted_unreadable_comment_blocked_after_reauthorization_attempt(world) -> None:
    """The review's two comment cases proved the first response and re-auth refusal on GitHub
    o/r; this drives the same class through a differently-worded comment and additionally
    proves the resolution -> resume loop terminates in exactly one more dispatch."""
    root, _bare, forge = world
    ctx = context(root)
    sid, _sha, _base = _armed_session(world, ctx)
    action_hash = _plan_and_authorize(
        world, ctx, sid, action="comment", number="17", subject="issue", body="uniquely worded probe text"
    )
    forge.route("POST /issues/17/comments", "{truncated", status=201)
    first = door("repo.pr.comment", {"repo_session_id": sid}, ctx)
    assert first.ok is False and first.status == "unknown_reconciliation_required"
    assert first.details.get("reason") == "accepted_reply_undecodable"
    assert len(_calls(forge, method="POST", contains="/issues/17/comments")) == 1

    assert _operator_authorizes(world, sid, action_hash)["status"] == "resolution_required"
    assert _operator_authorizes(world, sid, action_hash, resolve="failed_safe_to_retry")["ok"]
    assert _operator_authorizes(world, sid, action_hash)["ok"]
    forge.route("POST /issues/17/comments", _comment_payload(9300, body="uniquely worded probe text"))
    resumed = door("repo.pr.comment", {"repo_session_id": sid}, ctx)
    assert resumed.ok and resumed.details["comment_id"] == "9300"
    assert len(_calls(forge, method="POST", contains="/issues/17/comments")) == 2


def test_pre_dispatch_denials_stay_definitive(world) -> None:
    """Preservation control for fix 4: a 404 refusal BEFORE any acceptance is still a
    definitive failed-safe-to-retry, the authorization is consumed, and re-arming is an
    ordinary operator decision again (no resolution hoop for a write the forge refused)."""
    root, _bare, forge = world
    ctx = context(root)
    sid, _sha, _base = _armed_session(world, ctx)
    action_hash = _plan_and_authorize(
        world, ctx, sid, action="comment", number="17", subject="issue", body="denial probe"
    )
    forge.route("POST /issues/17/comments", {"message": "not found"}, status=404)
    first = door("repo.pr.comment", {"repo_session_id": sid}, ctx)
    assert first.ok is False and first.status == "not_found"
    # Definitive refusal consumed the authorization; re-arming is an ordinary mint (no unknown).
    re_armed = _operator_authorizes(world, sid, action_hash)
    assert re_armed["ok"] is True, re_armed
    # And the typed rate limit keeps its own shape on the write path.
    forge.route("POST /issues/17/comments", {"message": "no"}, status=429, headers={"retry-after": "12"})
    second = door("repo.pr.comment", {"repo_session_id": sid}, ctx)
    assert second.ok is False and second.status == "provider_rate_limited"
    assert second.details.get("retry_after") == 12.0
