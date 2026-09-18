"""Revision 5: the push lane honors the SAME durable dispatch contract as the forge lane.

Every case drives a real push plan through the production door against a disposable local bare
remote. Dispatch is counted on the REMOTE side: the worktree's `remote.origin.receivepack` is a
logging wrapper around `git receive-pack`, so every push that actually reaches the remote leaves a
line -- independent of what the runtime reports about itself. The operator authorization is minted
by the served operator surface (`repoops_api.authorize_push`), never hand-written into a turn.

Data differs from the review's probes: pull request 12, a `release-candidate` ref, a different
commit. No live remote, forge or credential is used.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.repoops._forge_fixture import github_pull_request
from tests.repoops._harness import FIXED, context, door, git, head, remote_ref
from tests.repoops.test_forge_v3_independent_review import fail_session_storage
from tests.repoops.test_repoops_unknown_effect import world  # noqa: F401 -- real-push world fixture

OPEN = {"provider": "github", "namespace": "o/r", "reviewer_model": "anthropic/claude-sonnet-4"}
REF = "release-candidate"


def _count_remote_pushes(root: Path, tmp_path: Path) -> Path:
    log = tmp_path / "receive-pack.log"
    wrapper = tmp_path / "receive-pack-counter.sh"
    wrapper.write_text(f'#!/bin/sh\necho "$$" >> "{log}"\nexec git receive-pack "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    git(root, "config", "remote.origin.receivepack", str(wrapper))
    return log


def _pushes(log: Path) -> int:
    return len(log.read_text(encoding="utf-8").splitlines()) if log.exists() else 0


def _drive(root: Path, forge, ctx: dict, *, notes: str | None, pull_request: str = "12", ref: str = REF) -> tuple[str, str]:
    """A reviewed repair driven to an OPERATOR-authorized push plan through the production door.

    ``notes=None`` adds no commit, so a second session plans the SAME push as the first."""
    from core.web.api.repoops_api import authorize_push

    if notes is not None:
        (root / "NOTES.md").write_text(notes, encoding="utf-8")
        git(root, "add", "NOTES.md")
        git(root, "commit", "-q", "-m", "release notes for the reviewed repair")
    sha = head(root)
    forge.route(f"GET /pulls/{pull_request}", github_pull_request(pull_request, base_sha=git(root, "rev-parse", "HEAD~1").strip(), head_sha=sha))
    forge.route("GET /commits/feature", {"sha": sha})
    sid = door("repo.session.open", {"objective": "push the reviewed release", "pull_request": pull_request, **OPEN}, ctx).details["repo_session_id"]
    door("repo.inspect", {"repo_session_id": sid}, ctx)
    assert door("repo.bind", {"repo_session_id": sid}, ctx).ok
    door("repo.step", {"repo_session_id": sid, "step_id": "red", "intent": "workspace.run_tests", "arguments": {}}, ctx)
    door("repo.diagnose", {"repo_session_id": sid, "path": "calc.py", "reason": "add subtracts", "evidence_step_id": "red"}, ctx)
    door("repo.step", {"repo_session_id": sid, "step_id": "fix", "intent": "workspace.write_file", "arguments": {"path": "calc.py", "content": FIXED}}, ctx)
    door("repo.step", {"repo_session_id": sid, "step_id": "green", "intent": "workspace.run_tests", "arguments": {}}, ctx)
    door("repo.review", {"repo_session_id": sid, "verdict": "approve"}, ctx)
    door("repo.git", {"repo_session_id": sid, "operation": "commit", "message": "repair"}, ctx)
    planned = door("repo.push.request", {"repo_session_id": sid, "ref": ref}, ctx)
    assert planned.ok, planned.response_text
    minted = authorize_push(repo_session_id=sid, plan_hash=planned.details["plan_hash"], workspace_root=str(root))
    assert minted["ok"], minted
    return sid, planned.details["plan_hash"]


def _disk(sid: str) -> dict:
    from core.repoops.plane import session_dir

    return json.loads((session_dir() / f"{sid}.json").read_text(encoding="utf-8"))


def _rows(leid: str) -> list[dict]:
    from core.runtime_continuity import logical_effect_row_states

    return logical_effect_row_states(leid)


def _push_leid(root: Path, bare: Path, sha: str, ref: str = REF) -> str:
    from core.runtime_continuity import compute_logical_effect_id

    return compute_logical_effect_id(
        intent="repo.push", arguments={"remote": "origin", "ref": ref, "sha": sha, "url": str(bare)}
    )


def _evict(sid: str) -> None:
    from core.repoops.plane import repo_ops_runtime

    repo_ops_runtime()._sessions.pop(sid, None)


# ---------------------------------------------------------------------------
# Storage failure before dispatch: nothing runs, the SAME consent pushes once healthy
# ---------------------------------------------------------------------------


def test_reservation_storage_failure_runs_nothing_and_the_same_consent_pushes_once(world, monkeypatch, tmp_path) -> None:
    from core import runtime_continuity as continuity

    root, bare, forge = world
    log = _count_remote_pushes(root, tmp_path)
    ctx = context(root)
    sid, _plan = _drive(root, forge, ctx, notes="novel: ledger outage before the release push\n")

    with monkeypatch.context() as outage:
        def unavailable(**kwargs):
            raise OSError("Synthetic effect ledger outage")

        outage.setattr(continuity, "reserve_logical_effect", unavailable)
        refused = door("repo.push", {"repo_session_id": sid, "simulate": False}, ctx)
    assert refused.ok is False and refused.status == "effect_journal_unavailable", refused.details
    assert refused.details.get("fault_id")
    assert remote_ref(bare, REF) == "" and _pushes(log) == 0
    disk = _disk(sid)
    assert disk["authorization"]["consumed"] is False and not disk["push_outcome"]

    # The ledger is healthy again: the SAME authorization (no second operator gesture) pushes once.
    pushed = door("repo.push", {"repo_session_id": sid, "simulate": False}, ctx)
    assert pushed.ok and pushed.status == "applied", pushed.response_text
    assert remote_ref(bare, REF) == head(root) and _pushes(log) == 1
    again = door("repo.push", {"repo_session_id": sid, "simulate": False}, ctx)
    assert again.ok is False and again.status == "authorization_consumed"
    assert _pushes(log) == 1


@pytest.mark.parametrize(
    "answer",
    [
        {},
        {"outcome": "reserved"},
        {"outcome": "reserved", "logical_effect_id": "lef-some-other-effect", "effect_instance_id": "eff-not-this-push"},
        {"outcome": "granted", "logical_effect_id": "", "effect_instance_id": ""},
    ],
    ids=["empty", "no-identity", "other-effect", "unknown-outcome"],
)
def test_incoherent_reservation_answers_run_nothing(world, monkeypatch, tmp_path, answer) -> None:
    from core import runtime_continuity as continuity

    root, bare, forge = world
    log = _count_remote_pushes(root, tmp_path)
    ctx = context(root)
    sid, _plan = _drive(root, forge, ctx, notes=f"novel: incoherent ledger answer {sorted(answer)}\n")
    monkeypatch.setattr(continuity, "reserve_logical_effect", lambda **kwargs: dict(answer))
    refused = door("repo.push", {"repo_session_id": sid, "simulate": False}, ctx)
    assert refused.ok is False and refused.status == "effect_reservation_invalid", refused.details
    assert remote_ref(bare, REF) == "" and _pushes(log) == 0
    assert _disk(sid)["authorization"]["consumed"] is False


# ---------------------------------------------------------------------------
# The dispatch claim: a real competitor wins, and the loser touches nothing
# ---------------------------------------------------------------------------


def test_a_lost_claim_to_a_real_competitor_runs_nothing_and_leaves_the_winner_untouched(world, monkeypatch, tmp_path) -> None:
    from core import runtime_continuity as continuity

    root, bare, forge = world
    log = _count_remote_pushes(root, tmp_path)
    ctx = context(root)
    sid, _plan = _drive(root, forge, ctx, notes="novel: two executors, one claim\n")
    real = continuity.mark_effect_dispatched
    verdicts: list[bool] = []

    def competitor_then_this_executor(**kwargs):
        winner = bool(real(**{**kwargs, "claimed_by": "competing-executor"}))
        loser = bool(real(**kwargs))
        verdicts.extend([winner, loser])
        return loser

    with monkeypatch.context() as race:
        race.setattr(continuity, "mark_effect_dispatched", competitor_then_this_executor)
        lost = door("repo.push", {"repo_session_id": sid, "simulate": False}, ctx)
    assert verdicts == [True, False]
    assert lost.ok is False and lost.status == "claim_lost_to_another_executor", lost.details
    assert remote_ref(bare, REF) == "" and _pushes(log) == 0
    leid = _push_leid(root, bare, head(root))
    rows = _rows(leid)
    assert [r["state"] for r in rows] == ["dispatched"] and rows[0]["claimed_by"] == "competing-executor", rows
    assert _disk(sid)["push_outcome"]["outcome"] == "dispatching"

    # Asking again while the winner holds it is still refused: its dispatch is never run twice.
    again = door("repo.push", {"repo_session_id": sid, "simulate": False}, ctx)
    assert again.ok is False and again.status == "claim_lost_to_another_executor", again.details
    assert _pushes(log) == 0 and [r["state"] for r in _rows(leid)] == ["dispatched"]


def test_claim_storage_failure_is_known_unsent_and_pushes_exactly_once_after_recovery(world, monkeypatch, tmp_path) -> None:
    from core import runtime_continuity as continuity

    root, bare, forge = world
    log = _count_remote_pushes(root, tmp_path)
    ctx = context(root)
    sid, _plan = _drive(root, forge, ctx, notes="novel: the claim store fails mid-push\n")
    with monkeypatch.context() as outage:
        def unmarkable(**kwargs):
            raise OSError("Synthetic claim store outage")

        outage.setattr(continuity, "mark_effect_dispatched", unmarkable)
        refused = door("repo.push", {"repo_session_id": sid, "simulate": False}, ctx)
    assert refused.ok is False and refused.status == "effect_journal_unavailable", refused.details
    assert "unmarked dispatch" in refused.response_text
    assert remote_ref(bare, REF) == "" and _pushes(log) == 0
    leid = _push_leid(root, bare, head(root))
    assert [r["state"] for r in _rows(leid)] == ["expired_pre_dispatch"]
    disk = _disk(sid)
    assert disk["authorization"]["consumed"] is False and not disk["push_outcome"]

    pushed = door("repo.push", {"repo_session_id": sid, "simulate": False}, ctx)
    assert pushed.ok and pushed.status == "applied", pushed.response_text
    assert _pushes(log) == 1 and remote_ref(bare, REF) == head(root)


def test_compound_claim_and_recovery_outage_restores_consent_from_the_ledger_after_restart(world, monkeypatch, tmp_path) -> None:
    from core import runtime_continuity as continuity

    root, bare, forge = world
    log = _count_remote_pushes(root, tmp_path)
    ctx = context(root)
    sid, _plan = _drive(root, forge, ctx, notes="novel: claim and recovery journal both fail\n")
    with monkeypatch.context() as outage:
        def claim_and_journal_outage(**kwargs):
            # The write-ahead already recorded the spend; the recovery write now fails as well.
            fail_session_storage(outage, sid, operation="replace")
            raise OSError("Synthetic claim store outage")

        outage.setattr(continuity, "mark_effect_dispatched", claim_and_journal_outage)
        refused = door("repo.push", {"repo_session_id": sid, "simulate": False}, ctx)
    assert refused.status == "effect_journal_unavailable" and _pushes(log) == 0
    stranded = _disk(sid)
    assert stranded["push_outcome"]["outcome"] == "dispatching" and stranded["authorization"]["consumed"] is True

    _evict(sid)  # restart: the cache is gone, the stranded journal and the ledger survive
    pushed = door("repo.push", {"repo_session_id": sid, "simulate": False}, ctx)
    assert pushed.ok and pushed.status == "applied", pushed.response_text
    assert _pushes(log) == 1 and remote_ref(bare, REF) == head(root)


def test_write_ahead_journal_disk_full_runs_nothing(world, monkeypatch, tmp_path) -> None:
    root, bare, forge = world
    log = _count_remote_pushes(root, tmp_path)
    ctx = context(root)
    sid, _plan = _drive(root, forge, ctx, notes="novel: journal disk full at the write-ahead\n")
    from core.repoops.plane import session_dir

    before = (session_dir() / f"{sid}.json").read_bytes()
    with monkeypatch.context() as outage:
        fail_session_storage(outage, sid, operation="write")
        refused = door("repo.push", {"repo_session_id": sid, "simulate": False}, ctx)
    assert refused.ok is False and refused.status == "session_journal_unavailable", refused.details
    assert (session_dir() / f"{sid}.json").read_bytes() == before
    assert remote_ref(bare, REF) == "" and _pushes(log) == 0
    assert [r["state"] for r in _rows(_push_leid(root, bare, head(root)))] == ["expired_pre_dispatch"]


# ---------------------------------------------------------------------------
# Stale caches: consent spent by one runtime is never re-spent by another
# ---------------------------------------------------------------------------


def test_a_stale_runtime_that_read_the_consent_before_another_push_spent_it_runs_nothing(world, monkeypatch, tmp_path) -> None:
    """Deterministic post-terminal interleaving across two runtime instances: the stale instance
    reads the unconsumed authorization, passes every early check, and only reaches its
    reservation AFTER the other instance's push has completed and journaled."""
    import core.repoops.plane as plane
    from core import runtime_continuity as continuity

    root, bare, forge = world
    log = _count_remote_pushes(root, tmp_path)
    ctx = context(root)
    sid, _plan = _drive(root, forge, ctx, notes="novel: stale cache push race\n")
    original = plane.repo_ops_runtime()
    stale = plane.RepoOpsRuntime()
    assert stale._load(sid).authorization["consumed"] is False
    serving = {"runtime": stale}
    monkeypatch.setattr(plane, "repo_ops_runtime", lambda: serving["runtime"])
    real_reserve = continuity.reserve_logical_effect
    raced: dict = {}

    def reserve_after_the_other_push_completes(**kwargs):
        if not raced:
            raced["started"] = True
            serving["runtime"] = original
            raced["winner"] = door("repo.push", {"repo_session_id": sid, "simulate": False}, ctx)
            serving["runtime"] = stale
        return real_reserve(**kwargs)

    monkeypatch.setattr(continuity, "reserve_logical_effect", reserve_after_the_other_push_completes)
    loser = door("repo.push", {"repo_session_id": sid, "simulate": False}, ctx)
    assert raced["winner"].ok and raced["winner"].status == "applied", raced["winner"].details
    assert loser.ok is False and loser.status == "authorization_consumed", loser.details
    assert _pushes(log) == 1 and remote_ref(bare, REF) == head(root)
    # The loser released only its own never-dispatched reservation; the winner's row stays applied.
    states = sorted(r["state"] for r in _rows(_push_leid(root, bare, head(root))))
    assert states == ["applied", "expired_pre_dispatch"], states


# ---------------------------------------------------------------------------
# After the push: classification and journal gaps are reported, reconciled, never re-run
# ---------------------------------------------------------------------------


def test_classification_failure_after_a_real_push_is_reported_and_holds_identical_pushes_until_verified(world, monkeypatch, tmp_path) -> None:
    from core import runtime_continuity as continuity
    from core.runtime_continuity import find_active_unresolved_effect

    root, bare, forge = world
    log = _count_remote_pushes(root, tmp_path)
    ctx = context(root)
    sid, _plan = _drive(root, forge, ctx, notes="novel: ledger classification fails after the push\n")
    with monkeypatch.context() as outage:
        def unclassifiable(**kwargs):
            raise OSError("Synthetic classification outage")

        outage.setattr(continuity, "classify_effect_outcome", unclassifiable)
        pushed = door("repo.push", {"repo_session_id": sid, "simulate": False}, ctx)
    assert pushed.ok and pushed.status == "applied"
    assert pushed.details["ledger_classified"] is False and "did not record this outcome" in pushed.response_text
    assert _pushes(log) == 1 and remote_ref(bare, REF) == head(root)
    leid = pushed.details["logical_effect_id"]
    assert find_active_unresolved_effect(leid) is not None  # the row is still active: identical pushes are refused

    # A second operator session plans and authorizes the IDENTICAL push (same commit, ref, remote):
    # the still-active ledger row blocks it, because the first push's outcome was never recorded.
    other_ctx = context(root, session_id="second-operator-session")
    sid2, _ = _drive(root, forge, other_ctx, notes=None)
    blocked = door("repo.push", {"repo_session_id": sid2, "simulate": False}, other_ctx)
    assert blocked.ok is False and blocked.status == "duplicate_effect_blocked", blocked.details
    assert _pushes(log) == 1

    forge.route(f"GET /commits/{REF}", {"sha": head(root)})
    verified = door("repo.verify_remote", {"repo_session_id": sid}, ctx)
    assert verified.ok and verified.details["verified"] is True, verified.details
    assert find_active_unresolved_effect(leid) is None
    assert [r["state"] for r in _rows(leid)] == ["applied"]


def test_post_push_journal_gap_reconciles_from_the_ledger_after_restart_and_never_re_runs(world, monkeypatch, tmp_path) -> None:
    from core import runtime_continuity as continuity

    root, bare, forge = world
    log = _count_remote_pushes(root, tmp_path)
    ctx = context(root)
    sid, _plan = _drive(root, forge, ctx, notes="novel: journal gap after a landed push\n")
    real_claim = continuity.mark_effect_dispatched
    with monkeypatch.context() as outage:
        def claim_then_lose_the_journal(**kwargs):
            won = real_claim(**kwargs)
            fail_session_storage(outage, sid, operation="replace")
            return won

        outage.setattr(continuity, "mark_effect_dispatched", claim_then_lose_the_journal)
        pushed = door("repo.push", {"repo_session_id": sid, "simulate": False}, ctx)
    assert pushed.ok and pushed.status == "applied" and pushed.details["journal_persist_failed"] is True
    assert "could not record this outcome" in pushed.response_text
    assert _pushes(log) == 1 and remote_ref(bare, REF) == head(root)
    assert _disk(sid)["push_outcome"]["outcome"] == "dispatching"

    _evict(sid)
    replayed = door("repo.push", {"repo_session_id": sid, "simulate": False}, ctx)
    assert replayed.ok and replayed.status == "applied" and replayed.details.get("replayed") is True, replayed.details
    assert replayed.details["reconciled_from"] == "effect_ledger"
    assert _pushes(log) == 1
    forge.route(f"GET /commits/{REF}", {"sha": head(root)})
    verified = door("repo.verify_remote", {"repo_session_id": sid}, ctx)
    assert verified.ok and verified.details["verified"] is True, verified.details
    receipt = door("repo.receipt", {"repo_session_id": sid}, ctx)
    assert receipt.details["verdict"] == "completed", receipt.details["unresolved"]


# ---------------------------------------------------------------------------
# Preservation: simulation spends consent durably and sends nothing; drift and force refuse
# ---------------------------------------------------------------------------


def test_simulated_push_spends_consent_durably_and_sends_nothing(world, tmp_path) -> None:
    root, bare, forge = world
    log = _count_remote_pushes(root, tmp_path)
    ctx = context(root)
    sid, _plan = _drive(root, forge, ctx, notes="novel: a simulated release push\n")
    simulated = door("repo.push", {"repo_session_id": sid, "simulate": True}, ctx)
    assert simulated.ok and simulated.status == "simulated", simulated.details
    assert remote_ref(bare, REF) == "" and _pushes(log) == 0
    rows = _rows(simulated.details["logical_effect_id"])
    assert [(r["state"], r["reason"]) for r in rows] == [("expired_pre_dispatch", "simulated")], rows
    assert _disk(sid)["authorization"]["consumed"] is True
    again = door("repo.push", {"repo_session_id": sid, "simulate": True}, ctx)
    assert again.ok is False and again.status == "authorization_consumed"


def test_plan_drift_and_unauthorized_force_still_refuse_before_any_reservation(world, tmp_path) -> None:
    root, bare, forge = world
    log = _count_remote_pushes(root, tmp_path)
    ctx = context(root)
    sid, _plan = _drive(root, forge, ctx, notes="novel: drift after consent\n")
    forced = door("repo.push", {"repo_session_id": sid, "simulate": False, "force": True}, ctx)
    assert forced.ok is False and forced.status == "default_denied"
    (root / "NOTES.md").write_text("moved after the operator saw the plan\n", encoding="utf-8")
    git(root, "commit", "-qam", "drift")
    drifted = door("repo.push", {"repo_session_id": sid, "simulate": False}, ctx)
    # A commit made OUTSIDE the session is refused at the binding gate, before the plan is even
    # re-derived (plan drift made through the session itself is test_repoops_adversarial's case).
    assert drifted.ok is False and drifted.status == "binding_diverged", drifted.details
    assert remote_ref(bare, REF) == "" and _pushes(log) == 0
    assert _disk(sid)["authorization"]["consumed"] is False
    assert _rows(_push_leid(root, bare, head(root))) == []


def test_a_plan_changed_through_the_session_after_consent_refuses_and_spends_nothing(world, tmp_path) -> None:
    root, bare, forge = world
    log = _count_remote_pushes(root, tmp_path)
    ctx = context(root)
    sid, _plan = _drive(root, forge, ctx, notes="novel: one more reviewed entry follows consent\n")
    (root / "CHANGELOG.md").write_text("a further reviewed entry written after the operator's consent\n", encoding="utf-8")
    committed = door("repo.git", {"repo_session_id": sid, "operation": "commit", "message": "changelog after consent"}, ctx)
    assert committed.ok, committed.response_text
    refused = door("repo.push", {"repo_session_id": sid, "simulate": False}, ctx)
    assert refused.ok is False and refused.status == "plan_diverged", refused.details
    assert remote_ref(bare, REF) == "" and _pushes(log) == 0
    assert _disk(sid)["authorization"]["consumed"] is False
    assert _rows(_push_leid(root, bare, head(root))) == []
