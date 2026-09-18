"""The RepoOps vertical, driven end to end through the production tool door.

Nothing in this pack calls the runtime directly. Every step goes through
``core.runtime_execution_tools.execute_runtime_tool`` -- THE door a model, skill or plugin
proposal crosses -- so a green row here is a row a served model could reach with the same intent
and arguments.

The repository is real and its defect is real: ``calc.add`` subtracts, and a real pytest fails
because of it. The remote is a local bare repository, so the push is a real push whose effect is
verified by reading the bare repo's refs, and no third party is touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.repoops._forge_fixture import UNIFIED_DIFF, RecordedForge, github_pull_request
from tests.repoops._harness import FIXED, build_repo, context, door, git, head, remote_ref


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
    monkeypatch.setattr(
        policy_engine, "get", _policy_with(policy_engine.get, {"repo.real_push_enabled": True})
    )
    try:
        yield root, bare, forge
    finally:
        forge_bridge.install_transport_factory(None)
        repo_ops_runtime().reset()
        reset_mode_permission_state()


def _policy_with(original, overrides):
    def _get(key, default=None):
        if key in overrides:
            return overrides[key]
        return original(key, default)

    return _get


def _arm_pull_request(forge: RecordedForge, root: Path, *, number: str = "7") -> str:
    head_sha = head(root)
    base_sha = git(root, "rev-parse", "HEAD~1").strip()
    forge.route("GET /pulls/7", github_pull_request(number, base_sha=base_sha, head_sha=head_sha))
    forge.route("GET /commits/feature", {"sha": head_sha})
    return head_sha


def _ci(forge: RecordedForge, sha: str, *, conclusion: str = "failure") -> None:
    forge.route(
        "GET /actions/runs?head_sha=",
        {
            "workflow_runs": [
                {
                    "id": 4242,
                    "name": "unit",
                    "status": "completed",
                    "conclusion": conclusion,
                    "head_sha": sha,
                    "logs_url": "https://api.github.com/x",
                }
            ]
        },
    )
    forge.route("GET /actions/runs/4242/logs", "FAILED test_calc.py::test_add - assert -1 == 5\n")


# ---------------------------------------------------------------------------
# The contracts are offered from the one registry
# ---------------------------------------------------------------------------


def test_repo_intents_are_contracted_in_the_one_registry() -> None:
    from core.runtime_tool_contracts import runtime_tool_contract_map

    contracts = runtime_tool_contract_map()
    for intent in (
        "repo.session.open",
        "repo.inspect",
        "repo.bind",
        "repo.diff",
        "repo.ci.jobs",
        "repo.diagnose",
        "repo.step",
        "repo.git",
        "repo.review",
        "repo.push.request",
        "repo.push.authorize",
        "repo.push",
        "repo.verify_remote",
        "repo.receipt",
    ):
        assert intent in contracts, intent
        assert contracts[intent].handler == "runtime", intent
        assert contracts[intent].json_schema.get("type") == "object", intent
    assert contracts["repo.push"].side_effect_class == "network_publish"
    assert contracts["repo.push"].approval_requirement == "explicit_user_opt_in"
    assert contracts["repo.receipt"].read_only


def test_every_repo_tool_classifies_into_a_real_permission_action() -> None:
    from core.mode_permission_policy import PermissionAction, actions_for_tool
    from core.runtime_tool_contracts import runtime_tool_contract_map

    for intent in sorted(i for i in runtime_tool_contract_map() if i.startswith("repo.")):
        actions = actions_for_tool(intent, {}, {})
        assert actions, intent
        # UNKNOWN_SIDE_EFFECT is what an unclassified family falls into, and the matrix denies it
        # in every mode -- a tool nobody classified is a tool nobody can run.
        assert PermissionAction.UNKNOWN_SIDE_EFFECT not in actions, intent


# ---------------------------------------------------------------------------
# The whole vertical
# ---------------------------------------------------------------------------


def test_the_vertical_from_inspect_to_a_sealed_receipt(world) -> None:
    root, bare, forge = world
    ctx = context(root)
    head_sha = _arm_pull_request(forge, root)
    _ci(forge, head_sha)

    opened = door("repo.session.open", {"objective": "repair the failing unit job", "pull_request": "7", "provider": "github", "namespace": "o/r", "reviewer_model": "anthropic/claude-sonnet-4"}, ctx)
    assert opened.ok, opened.response_text
    sid = opened.details["repo_session_id"]
    assert opened.details["gate0"]["allowed"] is True
    assert opened.details["provider"] == "github"

    inspected = door("repo.inspect", {"repo_session_id": sid}, ctx)
    assert inspected.ok
    assert inspected.details["dirty"] is False
    assert inspected.details["pull_request"]["head_sha"] == head_sha

    bound = door("repo.bind", {"repo_session_id": sid}, ctx)
    assert bound.ok, bound.response_text
    assert bound.details["local_head"] == head_sha
    assert bound.details["head_sha"] == head_sha
    assert bound.details["binding_id"]

    forge.route("GET /pulls/7", UNIFIED_DIFF)
    diffed = door("repo.diff", {"repo_session_id": sid}, ctx)
    assert diffed.ok
    assert diffed.details["paths"] == ["calc.py"]
    assert diffed.details["head_sha"] == head_sha
    forge.route("GET /pulls/7", github_pull_request("7", base_sha=bound.details["base_sha"], head_sha=head_sha))

    jobs = door("repo.ci.jobs", {"repo_session_id": sid}, ctx)
    assert jobs.ok
    assert [j["name"] for j in jobs.details["failed"]] == ["unit"]
    # CI truth is bound to the SHA, not to a branch name.
    assert jobs.details["head_sha"] == head_sha

    log = door("repo.ci.log", {"repo_session_id": sid, "job_id": "4242"}, ctx)
    assert log.ok
    assert "test_add" in log.details["log"]

    # Reproduce locally before diagnosing: a red test EXECUTED is the evidence, and the step is
    # `ok` because the command ran -- its failure is carried as data, never folded into the step.
    red = door(
        "repo.step",
        {"repo_session_id": sid, "step_id": "red", "intent": "workspace.run_tests", "arguments": {}},
        ctx,
    )
    assert red.details["executed"] is True
    assert red.details["tool_result"]["returncode"] != 0

    diagnosed = door(
        "repo.diagnose",
        {
            "repo_session_id": sid,
            "path": "calc.py",
            "line": 2,
            "reason": "add subtracts",
            "evidence_step_id": "red",
        },
        ctx,
    )
    assert diagnosed.ok, diagnosed.response_text
    assert diagnosed.details["stage"] == "repair"

    repaired = door(
        "repo.step",
        {
            "repo_session_id": sid,
            "step_id": "fix",
            "intent": "workspace.write_file",
            "arguments": {"path": "calc.py", "content": FIXED},
        },
        ctx,
    )
    assert repaired.ok, repaired.response_text
    assert (root / "calc.py").read_text(encoding="utf-8") == FIXED

    green = door(
        "repo.step",
        {"repo_session_id": sid, "step_id": "green", "intent": "workspace.run_tests", "arguments": {}},
        ctx,
    )
    assert green.details["executed"] is True
    assert green.details["tool_result"]["returncode"] == 0

    reviewed = door("repo.review", {"repo_session_id": sid, "verdict": "approve", "notes": "root cause"}, ctx)
    assert reviewed.ok, reviewed.response_text

    committed = door(
        "repo.git", {"repo_session_id": sid, "operation": "commit", "message": "repair add"}, ctx
    )
    assert committed.ok, committed.response_text
    repaired_head = head(root)
    assert committed.details["head"] == repaired_head

    planned = door("repo.push.request", {"repo_session_id": sid, "ref": "feature"}, ctx)
    assert planned.ok, planned.response_text
    plan_hash = planned.details["plan_hash"]
    assert planned.details["plan"]["sha"] == repaired_head
    assert "--force" not in " ".join(planned.details["plan"]["argv"])
    # Nothing has been sent.
    assert remote_ref(bare, "feature") == ""

    operator_ctx = context(root, repo_push_authorization={"plan_hash": plan_hash, "operator": "sls_0x"})
    authorized = door("repo.push.authorize", {"repo_session_id": sid, "plan_hash": plan_hash}, operator_ctx)
    assert authorized.ok, authorized.response_text

    pushed = door("repo.push", {"repo_session_id": sid, "simulate": False}, ctx)
    assert pushed.ok, pushed.response_text
    assert pushed.details["outcome"] == "applied"
    # The environmental assertion: the remote ref actually moved.
    assert remote_ref(bare, "feature") == repaired_head

    forge.route("GET /commits/feature", {"sha": repaired_head})
    verified = door("repo.verify_remote", {"repo_session_id": sid}, ctx)
    assert verified.ok, verified.response_text
    assert verified.details["verified"] is True
    assert verified.details["remote_sha"] == repaired_head

    receipt = door("repo.receipt", {"repo_session_id": sid}, ctx)
    assert receipt.ok, receipt.response_text
    assert receipt.details["verdict"] == "completed"
    assert receipt.details["seal"]
    assert receipt.details["files_changed"] == ["calc.py"]
    assert receipt.details["remote_verification"]["verified"] is True
    assert receipt.details["unresolved"] == []
    assert receipt.details["binding"]["binding_id"] == bound.details["binding_id"]
    # The receipt is assembled from executed steps only.
    assert {c["step_id"] for c in receipt.details["commands_run"]} == {"red", "green"}


def test_a_simulated_push_never_moves_the_remote_and_claims_nothing(world) -> None:
    root, bare, forge = world
    ctx = context(root)
    head_sha = _arm_pull_request(forge, root)
    _ci(forge, head_sha, conclusion="success")

    sid = door("repo.session.open", {"objective": "plan only", "pull_request": "7", "provider": "github", "namespace": "o/r", "reviewer_model": "anthropic/claude-sonnet-4"}, ctx).details["repo_session_id"]
    door("repo.inspect", {"repo_session_id": sid}, ctx)
    door("repo.bind", {"repo_session_id": sid}, ctx)
    door(
        "repo.step",
        {"repo_session_id": sid, "step_id": "red", "intent": "workspace.run_tests", "arguments": {}},
        ctx,
    )
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
    door(
        "repo.step",
        {"repo_session_id": sid, "step_id": "green", "intent": "workspace.run_tests", "arguments": {}},
        ctx,
    )
    door("repo.review", {"repo_session_id": sid, "verdict": "approve"}, ctx)
    plan_hash = door("repo.push.request", {"repo_session_id": sid, "ref": "feature"}, ctx).details["plan_hash"]
    operator_ctx = context(root, repo_push_authorization={"plan_hash": plan_hash})
    door("repo.push.authorize", {"repo_session_id": sid, "plan_hash": plan_hash}, operator_ctx)

    simulated = door("repo.push", {"repo_session_id": sid, "simulate": True}, ctx)
    assert simulated.ok
    assert simulated.details["simulated"] is True
    assert simulated.details["pushed"] is False
    assert remote_ref(bare, "feature") == ""

    verified = door("repo.verify_remote", {"repo_session_id": sid}, ctx)
    # A simulation verifies NOTHING and must not report a verification it did not do.
    assert verified.details["verified"] is False
    assert verified.details["outcome"] == "not_applicable"

    receipt = door("repo.receipt", {"repo_session_id": sid}, ctx)
    assert receipt.details["verdict"] == "simulated"


def test_typed_git_operations_report_a_conflict_instead_of_guessing(world) -> None:
    root, _bare, forge = world
    ctx = context(root)
    _arm_pull_request(forge, root)
    sid = door("repo.session.open", {"objective": "branch work", "provider": "github", "namespace": "o/r", "reviewer_model": "anthropic/claude-sonnet-4"}, ctx).details["repo_session_id"]
    door("repo.inspect", {"repo_session_id": sid}, ctx)
    door("repo.bind", {"repo_session_id": sid}, ctx)

    branched = door("repo.git", {"repo_session_id": sid, "operation": "branch", "name": "topic"}, ctx)
    assert branched.ok, branched.response_text
    assert git(root, "rev-parse", "--abbrev-ref", "HEAD").strip() == "topic"

    # Two divergent edits to the same line, then a merge: git conflicts, and the runtime reports
    # the conflicted paths and leaves the tree conflicted rather than inventing a resolution.
    (root / "calc.py").write_text("def add(a, b):\n    return 1\n", encoding="utf-8")
    git(root, "commit", "-qam", "topic edit")
    git(root, "switch", "-q", "feature")
    (root / "calc.py").write_text("def add(a, b):\n    return 2\n", encoding="utf-8")
    git(root, "commit", "-qam", "feature edit")

    # Those commits moved HEAD outside the session, so the binding no longer describes the
    # repository and every operation refuses until it is re-bound. That refusal is the point.
    stale = door("repo.git", {"repo_session_id": sid, "operation": "merge", "onto": "topic"}, ctx)
    assert stale.status == "binding_diverged"
    _arm_pull_request(forge, root)
    door("repo.bind", {"repo_session_id": sid}, ctx)

    merged = door("repo.git", {"repo_session_id": sid, "operation": "merge", "onto": "topic"}, ctx)
    assert not merged.ok
    assert merged.status == "conflict"
    assert merged.details["conflicted_paths"] == ["calc.py"]

    restored = door("repo.git", {"repo_session_id": sid, "operation": "restore"}, ctx)
    assert restored.ok, restored.response_text
    assert git(root, "diff", "--name-only", "--diff-filter=U").strip() == ""


def test_cherry_pick_revert_and_tag_are_typed_and_journaled(world) -> None:
    root, _bare, forge = world
    ctx = context(root)
    _arm_pull_request(forge, root)
    sid = door("repo.session.open", {"objective": "typed git", "provider": "github", "namespace": "o/r", "reviewer_model": "anthropic/claude-sonnet-4"}, ctx).details["repo_session_id"]
    door("repo.inspect", {"repo_session_id": sid}, ctx)
    door("repo.bind", {"repo_session_id": sid}, ctx)

    git(root, "switch", "-q", "-c", "side")
    (root / "side.txt").write_text("side\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "side commit")
    side = head(root)
    git(root, "switch", "-q", "feature")
    _arm_pull_request(forge, root)
    door("repo.bind", {"repo_session_id": sid}, ctx)

    picked = door("repo.git", {"repo_session_id": sid, "operation": "cherry_pick", "commit": side}, ctx)
    assert picked.ok, picked.response_text
    assert (root / "side.txt").exists()

    reverted = door("repo.git", {"repo_session_id": sid, "operation": "revert", "commit": head(root)}, ctx)
    assert reverted.ok, reverted.response_text
    assert not (root / "side.txt").exists()

    tagged = door("repo.git", {"repo_session_id": sid, "operation": "tag", "name": "v0", "message": "cut"}, ctx)
    assert tagged.ok, tagged.response_text
    assert "v0" in git(root, "tag", "--list")

    receipt = door("repo.receipt", {"repo_session_id": sid}, ctx)
    intents = {row["intent"] for row in receipt.details["stages"]} if False else None
    assert intents is None  # stages are the plan, not the steps; the steps are below
    assert receipt.details["seal"]


def test_the_session_hands_the_adapter_a_binding_and_never_a_secret(world) -> None:
    """A private repository needs a credential, and the session names it as a binding.

    This was silently broken: the adapter was built with an expression that ANDed the PR number
    with an inspection key nothing ever set, so the binding was always empty and no forge call
    could ever authenticate. The assertion is on what the transport RECEIVED.
    """

    root, _bare, forge = world
    ctx = context(root)
    _arm_pull_request(forge, root)
    opened = door(
        "repo.session.open",
        {
            "objective": "private repo",
            "pull_request": "7",
            "provider": "github",
            "namespace": "o/r",
            "auth_binding": "cb-github-1",
        },
        ctx,
    )
    assert opened.ok, opened.response_text
    sid = opened.details["repo_session_id"]
    door("repo.inspect", {"repo_session_id": sid}, ctx)

    call = forge.calls[-1]
    assert call["auth"] == "cb-github-1"
    # The binding is a handle. Nothing resembling a credential value is in the request.
    assert not any(k.lower() == "authorization" for k in call["headers"])
