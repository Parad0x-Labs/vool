"""UNKNOWN is not FAILED, and an unreconciled push is not retried.

The one outcome a repository operation cannot guess at: the push left the machine and the reply
never came back. Reporting that as a failure authorizes a retry that could apply the ref motion
twice; reporting it as a success is a lie. RepoOps reserves the logical effect before the push,
classifies the outcome UNKNOWN, blocks an identical redispatch, and reconciles from what the
remote itself says.

The injection here is deliberately narrow: only the subprocess RESULT of the push argv is
replaced, with the stderr git actually produces when a connection dies mid-transfer. Everything
after that line is the real thing -- the real reservation in the real durable store, the real
classification, the real duplicate-effect refusal, and the real reconciliation through the real
forge adapter.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.repoops._forge_fixture import RecordedForge, github_pull_request
from tests.repoops._harness import FIXED, build_repo, context, door, git, head, remote_ref

OPEN = {"provider": "github", "namespace": "o/r", "reviewer_model": "anthropic/claude-sonnet-4"}
HUNG_UP = "fatal: the remote end hung up unexpectedly\nfatal: early EOF\n"


@pytest.fixture
def world(tmp_path, monkeypatch):
    from core import policy_engine
    from core.mode_permission_policy import reset_mode_permission_state
    from core.repoops import forge as forge_bridge
    from core.repoops.plane import repo_ops_runtime

    reset_mode_permission_state()
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    monkeypatch.setenv("VOOL_REPOOPS_DIR", str(tmp_path / "repo_sessions"))
    repo_ops_runtime().reset()
    root, bare = build_repo(tmp_path)
    forge = RecordedForge()
    forge_bridge.install_transport_factory(forge.factory)
    original = policy_engine.get
    monkeypatch.setattr(
        policy_engine,
        "get",
        lambda key, default=None: True if key == "repo.real_push_enabled" else original(key, default),
    )
    try:
        yield root, bare, forge
    finally:
        forge_bridge.install_transport_factory(None)
        repo_ops_runtime().reset()
        reset_mode_permission_state()


def _drive_to_authorized_push(root: Path, forge: RecordedForge, ctx: dict) -> tuple[str, str]:
    sha = head(root)
    forge.route("GET /pulls/7", github_pull_request("7", base_sha=git(root, "rev-parse", "HEAD~1").strip(), head_sha=sha))
    forge.route("GET /commits/feature", {"sha": sha})
    sid = door("repo.session.open", {"objective": "push", "pull_request": "7", **OPEN}, ctx).details["repo_session_id"]
    door("repo.inspect", {"repo_session_id": sid}, ctx)
    door("repo.bind", {"repo_session_id": sid}, ctx)
    door("repo.step", {"repo_session_id": sid, "step_id": "red", "intent": "workspace.run_tests", "arguments": {}}, ctx)
    door(
        "repo.diagnose",
        {"repo_session_id": sid, "path": "calc.py", "reason": "add subtracts", "evidence_step_id": "red"},
        ctx,
    )
    door(
        "repo.step",
        {
            "repo_session_id": sid,
            "step_id": "fix",
            "intent": "workspace.write_file",
            "arguments": {"path": "calc.py", "content": FIXED},
        },
        ctx,
    )
    door("repo.step", {"repo_session_id": sid, "step_id": "green", "intent": "workspace.run_tests", "arguments": {}}, ctx)
    door("repo.review", {"repo_session_id": sid, "verdict": "approve"}, ctx)
    door("repo.git", {"repo_session_id": sid, "operation": "commit", "message": "repair"}, ctx)
    plan_hash = door("repo.push.request", {"repo_session_id": sid, "ref": "feature"}, ctx).details["plan_hash"]
    operator_ctx = context(root, repo_push_authorization={"plan_hash": plan_hash})
    authorized = door("repo.push.authorize", {"repo_session_id": sid, "plan_hash": plan_hash}, operator_ctx)
    assert authorized.ok, authorized.response_text
    return sid, plan_hash


def _sever_the_push(monkeypatch) -> None:
    """Replace ONLY the push subprocess result with the stderr a severed transfer produces."""

    from core.repoops.gitops import GitRunner

    real_run = GitRunner.run

    def _run(self, *argv):
        if argv and argv[0] == "push":
            return 128, "", HUNG_UP
        return real_run(self, *argv)

    monkeypatch.setattr(GitRunner, "run", _run)


def test_a_severed_push_is_unknown_not_failed_and_blocks_an_identical_retry(world, monkeypatch) -> None:
    root, _bare, forge = world
    ctx = context(root)
    sid, _plan_hash = _drive_to_authorized_push(root, forge, ctx)
    _sever_the_push(monkeypatch)

    attempted = door("repo.push", {"repo_session_id": sid, "simulate": False}, ctx)
    assert attempted.status == "unknown"
    assert attempted.details["pushed"] is False
    assert attempted.details["outcome"] == "unknown"
    assert attempted.details["logical_effect_id"]
    # It is NOT reported as a failure, because a failure would authorize a retry.
    assert "unknown" in attempted.response_text.lower()

    # The durable reservation is active, so an identical push is refused rather than repeated.
    from core.runtime_continuity import find_active_unresolved_effect

    row = find_active_unresolved_effect(attempted.details["logical_effect_id"])
    assert row is not None
    assert str(row.get("state")) == "unknown"

    # A second session asking for the SAME push is blocked by the reservation, not by memory.
    from core.repoops.plane import repo_ops_runtime

    repo_ops_runtime().reset()
    sid2, _plan2 = _drive_to_authorized_push(root, forge, context(root, session_id="second"))
    blocked = door("repo.push", {"repo_session_id": sid2, "simulate": False}, context(root, session_id="second"))
    assert blocked.status == "duplicate_effect_blocked"
    assert blocked.details["logical_effect_id"] == attempted.details["logical_effect_id"]


def test_the_unknown_push_reconciles_from_what_the_remote_says_not_from_the_runtime(world, monkeypatch) -> None:
    root, bare, forge = world
    ctx = context(root)
    sid, _plan = _drive_to_authorized_push(root, forge, ctx)
    _sever_the_push(monkeypatch)
    attempted = door("repo.push", {"repo_session_id": sid, "simulate": False}, ctx)
    assert attempted.status == "unknown"
    leid = attempted.details["logical_effect_id"]

    # The remote, asked directly, says the ref never moved. That is FAILED_SAFE_TO_RETRY -- a fact
    # about the world, not a guess by the runtime.
    assert remote_ref(bare, "feature") == ""
    forge.route("GET /commits/feature", {"message": "Not Found"}, status=404)
    resolved = door("repo.verify_remote", {"repo_session_id": sid}, ctx)
    assert resolved.details["verified"] is False
    assert resolved.details["outcome"] == "absent"

    from core.runtime_continuity import find_active_unresolved_effect, get_unresolved_effect

    assert find_active_unresolved_effect(leid) is None, "the reservation must no longer block a retry"
    row = get_unresolved_effect(leid)
    # The store keeps WHICH truth was established, not just that one was: a push proven not to
    # have landed is safe to retry, and a push proven to have landed would not be.
    assert str(row.get("state")) == "failed_safe_to_retry"

    receipt = door("repo.receipt", {"repo_session_id": sid}, ctx)
    assert receipt.details["push"]["outcome"] == "failed"
    assert receipt.details["verdict"] != "completed"


def test_the_registered_resolver_proves_an_applied_push_mechanically(world) -> None:
    """A6 can reconcile a push without this runtime being live: the evidence is the remote's answer."""

    from core.effect_reconciliation import Reconcilability, ResolutionOutcome, reconcilability_for_intent
    from core.repoops.plane import _push_mechanical_resolver

    _root, _bare, forge = world
    assert reconcilability_for_intent("repo.push") is Reconcilability.RECONCILABLE

    forge.route("GET /commits/feature", {"sha": "c" * 40})
    applied = _push_mechanical_resolver(
        {
            "tool_name": "repo.push",
            "resource_identity": "https://github.com/o/r#refs/heads/feature",
            "expected_evidence_json": '{"remote_sha": "%s", "ref": "feature"}' % ("c" * 40),
        }
    )
    assert applied.outcome is ResolutionOutcome.APPLIED
    assert applied.source == "mechanical"

    forge.route("GET /commits/feature", {"message": "Not Found"}, status=404)
    absent = _push_mechanical_resolver(
        {
            "tool_name": "repo.push",
            "resource_identity": "https://github.com/o/r#refs/heads/feature",
            "expected_evidence_json": '{"remote_sha": "%s", "ref": "feature"}' % ("c" * 40),
        }
    )
    assert absent.outcome is ResolutionOutcome.FAILED_SAFE_TO_RETRY

    # An unreachable forge proves nothing, and says so rather than guessing.
    forge.arm_denial("/commits/feature")
    unproven = _push_mechanical_resolver(
        {
            "tool_name": "repo.push",
            "resource_identity": "https://github.com/o/r#refs/heads/feature",
            "expected_evidence_json": '{"remote_sha": "%s", "ref": "feature"}' % ("c" * 40),
        }
    )
    assert unproven.outcome is ResolutionOutcome.STILL_UNKNOWN


def test_the_model_is_never_an_effect_truth_source() -> None:
    from core.runtime_continuity import resolve_unresolved_effect

    with pytest.raises(ValueError) as caught:
        resolve_unresolved_effect(
            logical_effect_id="lef-anything",
            resolution="CONFIRMED_APPLIED",
            source="model",
            evidence="the assistant is confident the push landed",
        )
    assert "not an effect-truth source" in str(caught.value)


def test_a_successful_push_resolves_its_reservation_so_the_next_one_is_not_blocked(world) -> None:
    """A reservation that outlives a proven outcome is a lock nobody can open.

    The A6 store's outcome vocabulary is closed -- applied | failed_safe_to_retry | unknown -- and
    an unsupported value raises there. A helper that swallowed that raise would leave a SUCCESSFUL
    push's reservation active forever, and the next legitimate push of the same ref would be
    refused as a duplicate of one that had already landed.
    """

    from core.runtime_continuity import find_active_unresolved_effect

    root, bare, forge = world
    ctx = context(root)
    sid, _plan = _drive_to_authorized_push(root, forge, ctx)
    pushed = door("repo.push", {"repo_session_id": sid, "simulate": False}, ctx)
    assert pushed.details["outcome"] == "applied"
    assert remote_ref(bare, "feature") == pushed.details["sha"]

    leid = pushed.details["logical_effect_id"]
    assert leid
    assert find_active_unresolved_effect(leid) is None, (
        "a proven push must not leave an active reservation behind"
    )


def test_a_simulated_push_releases_its_reservation_rather_than_classifying_an_outcome(world) -> None:
    from core.runtime_continuity import find_active_unresolved_effect

    root, bare, forge = world
    ctx = context(root)
    sid, _plan = _drive_to_authorized_push(root, forge, ctx)
    simulated = door("repo.push", {"repo_session_id": sid, "simulate": True}, ctx)
    assert simulated.details["outcome"] == "simulated"
    assert remote_ref(bare, "feature") == ""
    # Nothing dispatched, so there is no outcome to record -- the reservation is released.
    assert find_active_unresolved_effect(simulated.details["logical_effect_id"]) is None


def test_an_unsupported_outcome_is_refused_rather_than_silently_dropped() -> None:
    from core.repoops.plane import EFFECT_OUTCOMES, _classify_effect

    assert {"applied", "failed_safe_to_retry", "unknown"} == EFFECT_OUTCOMES
    with pytest.raises(ValueError) as caught:
        _classify_effect("lef-x", "eff-y", "succeeded")
    assert "succeeded" in str(caught.value)
