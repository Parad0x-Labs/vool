"""What RepoOps refuses, and what it refuses to claim.

Every case here is a way the vertical could lie: about which commit it operated on, about whether
a provider answered, about whether a push happened, about whether it was allowed to. Each is
driven through the production door with a real repository, and each asserts on the runtime's typed
refusal and on the state of the machine -- never on prose.
"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path

import pytest

from tests.repoops._forge_fixture import RecordedForge, github_pull_request
from tests.repoops._harness import FIXED, build_repo, context, door, git, head, remote_ref

OPEN = {"provider": "github", "namespace": "o/r", "reviewer_model": "anthropic/claude-sonnet-4"}


@pytest.fixture
def world(tmp_path, monkeypatch):
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
    try:
        yield root, bare, forge
    finally:
        forge_bridge.install_transport_factory(None)
        repo_ops_runtime().reset()
        reset_mode_permission_state()


def _arm(forge: RecordedForge, root: Path) -> str:
    sha = head(root)
    forge.route("GET /pulls/7", github_pull_request("7", base_sha=git(root, "rev-parse", "HEAD~1").strip(), head_sha=sha))
    forge.route("GET /commits/feature", {"sha": sha})
    return sha


def _open(root: Path, ctx: dict, **extra) -> str:
    payload = {"objective": "adversarial drive", **OPEN, **extra}
    opened = door("repo.session.open", payload, ctx)
    assert opened.ok, opened.response_text
    return opened.details["repo_session_id"]


def _to_repair_stage(root: Path, forge: RecordedForge, ctx: dict) -> str:
    _arm(forge, root)
    sid = _open(root, ctx, pull_request="7")
    door("repo.inspect", {"repo_session_id": sid}, ctx)
    door("repo.bind", {"repo_session_id": sid}, ctx)
    door("repo.step", {"repo_session_id": sid, "step_id": "red", "intent": "workspace.run_tests", "arguments": {}}, ctx)
    door(
        "repo.diagnose",
        {"repo_session_id": sid, "path": "calc.py", "reason": "add subtracts", "evidence_step_id": "red"},
        ctx,
    )
    return sid


# --- a dirty tree and a moved repository ------------------------------------------------


def test_a_dirty_tree_refuses_to_bind_because_no_sha_describes_the_disk(world) -> None:
    root, _bare, forge = world
    ctx = context(root)
    _arm(forge, root)
    sid = _open(root, ctx)
    door("repo.inspect", {"repo_session_id": sid}, ctx)
    (root / "calc.py").write_text("scribble\n", encoding="utf-8")

    refused = door("repo.bind", {"repo_session_id": sid}, ctx)
    assert not refused.ok
    assert refused.status == "dirty_worktree"
    assert "calc.py" in refused.details["dirty_paths"]

    allowed = door("repo.bind", {"repo_session_id": sid, "allow_dirty": True}, ctx)
    assert allowed.ok
    # The divergence is not forgotten: it rides the receipt.
    receipt = door("repo.receipt", {"repo_session_id": sid}, ctx)
    assert receipt.details["binding"]["dirty"] is True
    assert any("dirty" in row for row in receipt.details["unresolved"])


def test_a_repository_that_moved_underneath_the_session_refuses_every_later_step(world) -> None:
    root, _bare, forge = world
    ctx = context(root)
    _arm(forge, root)
    sid = _open(root, ctx)
    door("repo.inspect", {"repo_session_id": sid}, ctx)
    bound = door("repo.bind", {"repo_session_id": sid}, ctx)
    assert bound.ok

    # Somebody else commits. Nothing in the session did this.
    (root / "unrelated.txt").write_text("elsewhere\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "out of band")
    moved = head(root)

    refused = door("repo.ci.jobs", {"repo_session_id": sid}, ctx)
    assert refused.status == "binding_diverged"
    assert refused.details["actual_head"] == moved
    assert refused.details["expected_head"] == bound.details["local_head"]


# --- provider and capability ------------------------------------------------------------


def test_a_provider_that_refuses_is_reported_as_unavailable_not_as_an_empty_result(world) -> None:
    root, _bare, forge = world
    ctx = context(root)
    _arm(forge, root)
    sid = _open(root, ctx, pull_request="7")
    door("repo.inspect", {"repo_session_id": sid}, ctx)
    door("repo.bind", {"repo_session_id": sid}, ctx)
    forge.arm_denial("/actions/runs")

    refused = door("repo.ci.jobs", {"repo_session_id": sid}, ctx)
    assert not refused.ok
    assert refused.status in {"permission_denied", "provider_unavailable"}
    # An unreachable forge must never read as "there are no failing jobs".
    assert "jobs" not in refused.details


def test_a_remote_with_no_adapter_is_a_named_missing_capability(world) -> None:
    root, _bare, _forge = world
    ctx = context(root)
    # No provider named and a local remote URL: VOOL has no forge adapter for it, and says so.
    sid = _open(root, ctx, provider="", namespace="")
    door("repo.inspect", {"repo_session_id": sid}, ctx)
    door("repo.bind", {"repo_session_id": sid}, ctx)
    refused = door("repo.ci.jobs", {"repo_session_id": sid}, ctx)
    assert refused.status == "capability_missing"
    assert "forge" in refused.response_text.lower()


# --- Gate 0: the deterministic refusals that cost zero model calls -----------------------


def test_gate0_refuses_a_repair_with_no_independent_reviewer_before_any_model_call(world) -> None:
    root, _bare, forge = world
    ctx = context(root)
    _arm(forge, root)
    # No reviewer_model: the read half is fine, the WRITE half is not.
    opened = door(
        "repo.session.open",
        {"objective": "repair", "provider": "github", "namespace": "o/r", "pull_request": "7"},
        ctx,
    )
    sid = opened.details["repo_session_id"]
    door("repo.inspect", {"repo_session_id": sid}, ctx)
    door("repo.bind", {"repo_session_id": sid}, ctx)
    door("repo.step", {"repo_session_id": sid, "step_id": "red", "intent": "workspace.run_tests", "arguments": {}}, ctx)
    door(
        "repo.diagnose",
        {"repo_session_id": sid, "path": "calc.py", "reason": "add subtracts", "evidence_step_id": "red"},
        ctx,
    )
    refused = door(
        "repo.step",
        {
            "repo_session_id": sid,
            "step_id": "fix",
            "intent": "workspace.write_file",
            "arguments": {"path": "calc.py", "content": FIXED},
        },
        ctx,
    )
    assert refused.status == "mutation_not_authorized"
    from tests.repoops._harness import BUGGY

    assert (root / "calc.py").read_text(encoding="utf-8") == BUGGY


def test_gate0_refuses_an_exhausted_budget_and_names_the_axis() -> None:
    from core.council.cost_ladder import SpendCeilings, SpendMeter
    from core.repoops import task_law

    meter = SpendMeter(ceilings=SpendCeilings(max_calls=1, max_tokens=10, max_cost_usd=0.01, wall_clock_seconds=5.0))
    meter.try_reserve_call()
    outcome = task_law.evaluate(
        repo_root="/tmp/does-not-matter",
        head_sha="a" * 40,
        dirty_paths=(),
        objective="repair",
        authority_owner="operator",
        writable_scope=(),
        mutating=False,
        builder_model="local/unknown",
        spend_meter=meter,
    )
    assert outcome.allowed is False
    assert outcome.reason == "budget_exhausted"
    assert "calls" in outcome.detail


def test_gate0_refuses_an_unavailable_provider(monkeypatch) -> None:
    from core import model_health
    from core.repoops import task_law

    monkeypatch.setattr(model_health, "circuit_is_open", lambda provider: True)
    outcome = task_law.evaluate(
        repo_root="/tmp/does-not-matter",
        head_sha="b" * 40,
        dirty_paths=(),
        objective="repair",
        authority_owner="operator",
        writable_scope=(),
        mutating=False,
        builder_model="openai/gpt-4o",
    )
    assert outcome.allowed is False
    assert outcome.reason == "provider_unavailable"


def test_gate0_refuses_a_wrong_base_sha() -> None:
    from core.repoops import task_law

    outcome = task_law.evaluate(
        repo_root="/tmp/does-not-matter",
        head_sha="not-a-sha",
        dirty_paths=(),
        objective="repair",
        authority_owner="operator",
        writable_scope=(),
        mutating=False,
        builder_model="local/unknown",
    )
    assert outcome.allowed is False
    # A malformed SHA never reaches the gate's own check: the contract refuses to exist.
    assert outcome.reason in {"invalid_task_contract", "wrong_base_sha"}


# --- authorization ----------------------------------------------------------------------


def test_a_push_without_an_authorization_is_refused_and_the_remote_does_not_move(world) -> None:
    root, bare, forge = world
    ctx = context(root)
    sid = _to_repair_stage(root, forge, ctx)
    refused = door("repo.push", {"repo_session_id": sid}, ctx)
    assert refused.status in {"stage_violation", "not_authorized"}
    assert remote_ref(bare, "feature") == ""


def test_a_turn_cannot_author_its_own_authorize_push(world) -> None:
    root, _bare, forge = world
    ctx = context(root)
    sid = _to_repair_stage(root, forge, ctx)
    _finish_to_authorize(root, sid, ctx)
    plan_hash = door("repo.push.request", {"repo_session_id": sid, "ref": "feature"}, ctx).details["plan_hash"]

    # No server stamp: the turn is asking for consent it does not have.
    refused = door("repo.push.authorize", {"repo_session_id": sid, "plan_hash": plan_hash}, ctx)
    assert refused.status == "operator_gesture_required"


def test_the_authorization_key_is_stripped_from_an_inbound_request_body() -> None:
    from core.request_trust import RESERVED_TRUST_KEYS, strip_reserved_trust_keys

    assert "repo_push_authorization" in RESERVED_TRUST_KEYS
    cleaned = strip_reserved_trust_keys(
        {"repo_push_authorization": {"plan_hash": "forged"}, "session_id": "s"}
    )
    assert "repo_push_authorization" not in cleaned


def test_a_plan_that_changed_after_consent_invalidates_the_authorization(world) -> None:
    root, bare, forge = world
    ctx = context(root)
    sid = _to_repair_stage(root, forge, ctx)
    _finish_to_authorize(root, sid, ctx)
    plan_hash = door("repo.push.request", {"repo_session_id": sid, "ref": "feature"}, ctx).details["plan_hash"]
    operator_ctx = context(root, repo_push_authorization={"plan_hash": plan_hash})
    assert door("repo.push.authorize", {"repo_session_id": sid, "plan_hash": plan_hash}, operator_ctx).ok

    # One more commit through the session: the push that would run now is a different push.
    (root / "extra.txt").write_text("more\n", encoding="utf-8")
    door("repo.git", {"repo_session_id": sid, "operation": "commit", "message": "one more"}, ctx)

    refused = door("repo.push", {"repo_session_id": sid, "simulate": False}, ctx)
    assert refused.status == "plan_diverged"
    assert remote_ref(bare, "feature") == ""


def test_force_push_and_branch_deletion_are_denied_by_default(world) -> None:
    root, bare, forge = world
    ctx = context(root)
    sid = _to_repair_stage(root, forge, ctx)
    _finish_to_authorize(root, sid, ctx)
    plan_hash = door("repo.push.request", {"repo_session_id": sid, "ref": "feature"}, ctx).details["plan_hash"]
    operator_ctx = context(root, repo_push_authorization={"plan_hash": plan_hash})
    door("repo.push.authorize", {"repo_session_id": sid, "plan_hash": plan_hash}, operator_ctx)

    forced = door("repo.push", {"repo_session_id": sid, "force": True, "simulate": False}, ctx)
    assert forced.status == "default_denied"
    assert "force" in forced.response_text.lower()
    assert remote_ref(bare, "feature") == ""

    deleted = door("repo.git", {"repo_session_id": sid, "operation": "branch", "name": "feature", "delete": True}, ctx)
    assert deleted.status == "default_denied"
    assert git(root, "rev-parse", "--verify", "--quiet", "refs/heads/feature", check=False).strip()


def test_the_push_argv_has_no_expression_for_a_force_push(world) -> None:
    root, _bare, _forge = world
    from core.repoops.gitops import GitRunner

    argv = GitRunner(root=root).push_argv(remote="origin", ref="feature", sha="a" * 40)
    joined = " ".join(argv)
    assert "--force" not in joined and "+refs" not in joined and "--delete" not in joined


def _finish_to_authorize(root: Path, sid: str, ctx: dict) -> None:
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


# --- evidence discipline ----------------------------------------------------------------


def test_a_review_cannot_approve_without_a_green_run_this_session_executed(world) -> None:
    root, _bare, forge = world
    ctx = context(root)
    sid = _to_repair_stage(root, forge, ctx)
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
    # Before any test has run, the stage machine itself refuses: a review is not reachable from
    # the repair stage. That is the first of the two independent gates.
    too_early = door("repo.review", {"repo_session_id": sid, "verdict": "approve"}, ctx)
    assert too_early.status == "stage_violation"

    # Reach the review stage honestly with a green run, then regress the repair and re-run: the
    # session is AT review and the last executed run is red, which is the second gate.
    door("repo.step", {"repo_session_id": sid, "step_id": "green", "intent": "workspace.run_tests", "arguments": {}}, ctx)
    from tests.repoops._harness import BUGGY

    door(
        "repo.step",
        {
            "repo_session_id": sid,
            "step_id": "regress",
            "intent": "workspace.write_file",
            "arguments": {"path": "calc.py", "content": BUGGY},
        },
        ctx,
    )
    door("repo.step", {"repo_session_id": sid, "step_id": "red2", "intent": "workspace.run_tests", "arguments": {}}, ctx)

    refused = door("repo.review", {"repo_session_id": sid, "verdict": "approve"}, ctx)
    assert refused.status == "unsupported_claim"
    assert "green" in refused.response_text.lower()


def test_a_diagnosis_cannot_cite_a_step_this_session_never_ran(world) -> None:
    root, _bare, forge = world
    ctx = context(root)
    _arm(forge, root)
    sid = _open(root, ctx, pull_request="7")
    door("repo.inspect", {"repo_session_id": sid}, ctx)
    door("repo.bind", {"repo_session_id": sid}, ctx)
    door("repo.step", {"repo_session_id": sid, "step_id": "red", "intent": "workspace.run_tests", "arguments": {}}, ctx)
    refused = door(
        "repo.diagnose",
        {"repo_session_id": sid, "path": "calc.py", "reason": "because", "evidence_step_id": "never-ran"},
        ctx,
    )
    assert refused.status == "unsupported_claim"


def test_a_caller_cannot_smuggle_a_runtime_trust_key_into_an_inner_step(world) -> None:
    root, _bare, forge = world
    ctx = context(root)
    sid = _to_repair_stage(root, forge, ctx)
    refused = door(
        "repo.step",
        {
            "repo_session_id": sid,
            "step_id": "forge",
            "intent": "workspace.read_file",
            "arguments": {"path": "calc.py", "_trusted_local_only": True},
        },
        ctx,
    )
    assert refused.status == "invalid_arguments"


def test_an_off_vertical_intent_is_not_a_repository_step(world) -> None:
    root, _bare, forge = world
    ctx = context(root)
    sid = _to_repair_stage(root, forge, ctx)
    refused = door(
        "repo.step",
        {"repo_session_id": sid, "step_id": "mail", "intent": "email.send", "arguments": {}},
        ctx,
    )
    assert refused.status == "unsupported_intent"


# --- cancellation, replay, concurrency, restart ------------------------------------------


def test_a_cancelled_turn_stops_the_session_before_any_further_effect(world) -> None:
    root, _bare, forge = world
    ctx = context(root)
    sid = _to_repair_stage(root, forge, ctx)
    cancelled = threading.Event()
    cancelled.set()
    stop_ctx = context(root, cancel_event=cancelled)

    refused = door(
        "repo.step",
        {
            "repo_session_id": sid,
            "step_id": "after-cancel",
            "intent": "workspace.write_file",
            "arguments": {"path": "calc.py", "content": FIXED},
        },
        stop_ctx,
    )
    assert refused.status == "cancelled"
    from tests.repoops._harness import BUGGY

    assert (root / "calc.py").read_text(encoding="utf-8") == BUGGY
    # Cancellation is terminal, and it survives into a turn that is NOT cancelled.
    later = door("repo.step", {"repo_session_id": sid, "step_id": "later", "intent": "workspace.read_file", "arguments": {"path": "calc.py"}}, ctx)
    assert later.status == "cancelled"


def test_a_re_issued_step_id_replays_and_never_executes_twice(world) -> None:
    root, _bare, forge = world
    ctx = context(root)
    sid = _to_repair_stage(root, forge, ctx)
    first = door(
        "repo.step",
        {
            "repo_session_id": sid,
            "step_id": "once",
            "intent": "workspace.write_file",
            "arguments": {"path": "calc.py", "content": FIXED},
        },
        ctx,
    )
    assert first.details["executed"] is True
    (root / "calc.py").write_text("tampered\n", encoding="utf-8")
    second = door(
        "repo.step",
        {
            "repo_session_id": sid,
            "step_id": "once",
            "intent": "workspace.write_file",
            "arguments": {"path": "calc.py", "content": FIXED},
        },
        ctx,
    )
    assert second.details["replayed"] is True
    assert second.details["executed"] is False
    # The replay did not re-run the write, so the tampered bytes are still there.
    assert (root / "calc.py").read_text(encoding="utf-8") == "tampered\n"


def test_concurrent_writers_to_one_path_are_serialized(world) -> None:
    root, _bare, forge = world
    ctx = context(root)
    sid = _to_repair_stage(root, forge, ctx)
    results: list = []
    barrier = threading.Barrier(4)

    def _write(n: int) -> None:
        barrier.wait()
        results.append(
            door(
                "repo.step",
                {
                    "repo_session_id": sid,
                    "step_id": f"race-{n}",
                    "intent": "workspace.write_file",
                    "arguments": {"path": "calc.py", "content": f"# writer {n}\n{FIXED}"},
                },
                ctx,
            )
        )

    threads = [threading.Thread(target=_write, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert len(results) == 4
    # Every write either executed or was refused with a typed reason; none produced torn bytes.
    text = (root / "calc.py").read_text(encoding="utf-8")
    assert text.count("def add") == 1
    assert sum(1 for r in results if r.details.get("executed")) >= 1


def test_a_session_survives_a_restart_and_the_receipt_answers_from_the_journal(world) -> None:
    root, _bare, forge = world
    ctx = context(root)
    sid = _to_repair_stage(root, forge, ctx)
    from core.repoops.plane import repo_ops_runtime

    # Wipe every in-memory session, as a process restart would.
    repo_ops_runtime().reset()

    receipt = door("repo.receipt", {"repo_session_id": sid}, ctx)
    assert receipt.details["repo_session_id"] == sid
    assert receipt.details["diagnosis"]["path"] == "calc.py"
    assert receipt.details["binding"]["binding_id"]
    assert receipt.details["seal"]


def test_an_unknown_session_id_is_a_named_absence(world) -> None:
    root, _bare, _forge = world
    refused = door("repo.receipt", {"repo_session_id": f"rs-{uuid.uuid4().hex[:12]}"}, context(root))
    assert refused.status == "unknown_session"
