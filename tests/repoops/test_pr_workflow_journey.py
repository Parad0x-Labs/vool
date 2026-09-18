"""The PR workflow, joined end to end: diagnose from the remote, repair through the code-task
control plane, describe truthfully, and land on the remote under explicit authorization.

Everything here is real except GitHub itself: the repository is disposable, the remote is a
local bare repository (a push is REAL ref motion), and the forge is the recorded transport
driving the shipped adapter. That is the mission's prescribed boundary -- no live GitHub is
contacted and none is claimed.

Two shapes are driven: the canonical order and a reordered, differently-named-branch variant,
because a workflow that only works in one order is a script, not a capability.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def world(tmp_path, monkeypatch):
    from core import policy_engine
    from core.mode_permission_policy import reset_mode_permission_state
    from core.repoops import forge as forge_bridge
    from core.repoops.plane import repo_ops_runtime

    reset_mode_permission_state()
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    monkeypatch.setenv("VOOL_REPOOPS_DIR", str(tmp_path / "repo_sessions"))
    monkeypatch.setenv("VOOL_CODE_TASK_DIR", str(tmp_path / "code_tasks"))
    repo_ops_runtime().reset()
    from core.code_assistant.task_runtime import code_task_runtime

    code_task_runtime().reset()
    from tests.repoops._forge_fixture import RecordedForge
    from tests.repoops._harness import build_repo

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
        code_task_runtime().reset()
        reset_mode_permission_state()


def _policy_with(original, overrides):
    def _get(key, default=None):
        if key in overrides:
            return overrides[key]
        return original(key, default)

    return _get


def _arm(world, ctx: dict, *, number: str = "7", reviewer: str = "anthropic/claude-sonnet-4") -> str:
    """Open a repo session bound to PR ``number`` and return (session_id, head_sha)."""
    from tests.repoops._forge_fixture import github_pull_request
    from tests.repoops._harness import door, git, head

    root, bare, forge = world
    sha = head(root)
    base = git(root, "rev-parse", "HEAD~1").strip()
    forge.route("GET /pulls/7", github_pull_request(number, base_sha=base, head_sha=sha))
    forge.route("GET /commits/feature", {"sha": sha})
    opened = door(
        "repo.session.open",
        {
            "objective": "repair the failing required check on the pull request",
            "pull_request": number,
            "provider": "github",
            "namespace": "o/r",
            "reviewer_model": reviewer,
        },
        ctx,
    )
    assert opened.ok, opened.response_text
    sid = opened.details["repo_session_id"]
    door("repo.inspect", {"repo_session_id": sid}, ctx)
    bound = door("repo.bind", {"repo_session_id": sid}, ctx)
    assert bound.ok, bound.response_text
    return sid


def _repair_through_code_task(world, ctx: dict, *, module: str = "calc") -> str:
    """The full root-cause repair through the coding lane's control plane; returns task_id."""
    from tests.repoops._harness import FIXED

    root, bare, forge = world

    def door(intent, arguments):
        from core.runtime_execution_tools import execute_runtime_tool

        result = execute_runtime_tool(intent, arguments, source_context=ctx)
        assert result is not None, intent
        return result

    opened = door("code.task.open", {"objective": f"Find why test_{module} fails and repair the root cause"})
    assert opened.ok, opened.response_text
    task_id = opened.details["task_id"]

    read = door(
        "code.task.step",
        {"task_id": task_id, "step_id": "read", "intent": "workspace.read_file", "arguments": {"path": f"{module}.py"}},
    )
    assert read.ok, read.response_text
    repro = door(
        "code.task.step",
        {"task_id": task_id, "step_id": "repro", "intent": "workspace.run_tests", "arguments": {"command": f"python -m pytest -q test_{module}.py"}},
    )
    assert repro.details["tool_result"]["success"] is False
    identified = door(
        "code.task.identify",
        {"task_id": task_id, "path": f"{module}.py", "line": 2, "reason": "add subtracts instead of adding"},
    )
    assert identified.ok, identified.response_text
    import hashlib

    before = hashlib.sha256((root / f"{module}.py").read_bytes()).hexdigest()
    proposal = door(
        "code.task.propose",
        {
            "task_id": task_id,
            "proposal_id": "p1",
            "intent": "workspace.write_file",
            "arguments": {"path": f"{module}.py", "content": FIXED, "expected_hash": before},
            "rationale": f"Owner {module}.py: add subtracts instead of adding.",
        },
    )
    assert proposal.ok, proposal.response_text
    assert door("code.task.approve", {"task_id": task_id, "proposal_id": "p1"}).ok
    mutated = door(
        "code.task.step",
        {"task_id": task_id, "step_id": "fix", "intent": "workspace.write_file", "arguments": {"path": f"{module}.py", "content": FIXED}},
    )
    assert mutated.ok, mutated.response_text
    narrow = door(
        "code.task.step",
        {"task_id": task_id, "step_id": "narrow", "intent": "workspace.run_tests", "arguments": {"command": f"python -m pytest -q test_{module}.py"}},
    )
    assert narrow.details["tool_result"]["success"] is True, narrow.details
    cumulative = door(
        "code.task.step",
        {"task_id": task_id, "step_id": "cumulative", "intent": "workspace.run_tests", "arguments": {"command": "python -m pytest -q"}},
    )
    assert cumulative.details["tool_result"]["success"] is True, cumulative.details
    diffed = door("code.task.step", {"task_id": task_id, "step_id": "diff", "intent": "workspace.git_diff", "arguments": {}})
    assert diffed.ok, diffed.response_text
    return task_id


def _land(world, ctx: dict, sid: str, *, ref: str = "feature", message: str = "repair the failing check") -> dict:
    """Session-side evidence + review + real push to the bare remote under authorization."""
    from tests.repoops._harness import FIXED, door, git, head, remote_ref

    root, bare, forge = world
    door("repo.step", {"repo_session_id": sid, "step_id": "red", "intent": "workspace.run_tests", "arguments": {}}, ctx)
    door("repo.diagnose", {"repo_session_id": sid, "path": "calc.py", "reason": "add subtracts", "evidence_step_id": "red"}, ctx)
    door(
        "repo.step",
        {"repo_session_id": sid, "step_id": "fix", "intent": "workspace.write_file", "arguments": {"path": "calc.py", "content": FIXED}},
        ctx,
    )
    door("repo.step", {"repo_session_id": sid, "step_id": "green", "intent": "workspace.run_tests", "arguments": {}}, ctx)
    door("repo.review", {"repo_session_id": sid, "verdict": "approve"}, ctx)
    committed = door("repo.git", {"repo_session_id": sid, "operation": "commit", "message": message}, ctx)
    assert committed.ok, committed.response_text
    pushed_sha = head(root)

    requested = door("repo.push.request", {"repo_session_id": sid, "ref": ref}, ctx)
    assert requested.ok, requested.response_text
    plan_hash = requested.details["plan_hash"]

    # The operator gesture the loopback surface mints, simulated exactly as the served tests
    # simulate it: a stamped authorization naming THIS plan hash, and nothing else may pass.
    operator_ctx = {**ctx, "repo_push_authorization": {"plan_hash": plan_hash}}
    authorized = door("repo.push.authorize", {"repo_session_id": sid, "plan_hash": plan_hash}, operator_ctx)
    assert authorized.ok, authorized.response_text

    from urllib.parse import quote

    forge.route(f"GET /commits/{quote(ref, safe='')}", {"sha": pushed_sha})
    pushed = door("repo.push", {"repo_session_id": sid, "simulate": False}, ctx)
    assert pushed.ok, pushed.response_text
    return {"plan_hash": plan_hash, "pushed_sha": pushed_sha, "push_result": pushed}


# ---------------------------------------------------------------------------
# The canonical shape
# ---------------------------------------------------------------------------


def test_pr_workflow_from_remote_diagnosis_to_a_landed_fix(world) -> None:
    from tests.repoops._forge_fixture import UNIFIED_DIFF
    from tests.repoops._harness import context, door, git, head, remote_ref

    root, bare, forge = world
    ctx = context(root)
    sid = _arm(world, ctx)
    sha = head(root)

    # Remote truth first: the PR diff and its failing required check at the bound SHA.
    forge.route("GET /pulls/7", UNIFIED_DIFF)
    diffed = door("repo.diff", {"repo_session_id": sid}, ctx)
    assert diffed.ok and diffed.details["paths"] == ["calc.py"]
    forge.route(
        "GET /actions/runs?head_sha=",
        {
            "workflow_runs": [
                {"id": 4242, "name": "unit", "status": "completed", "conclusion": "failure", "head_sha": sha, "logs_url": "u"}
            ]
        },
    )
    jobs = door("repo.ci.jobs", {"repo_session_id": sid}, ctx)
    assert jobs.ok and [j["name"] for j in jobs.details["failed"]] == ["unit"]
    forge.route("GET /actions/runs/4242/logs", "FAILED test_calc.py::test_add - assert -1 == 5\n")

    # Repair through the coding lane; the PR description is prepared from that journal.
    task_id = _repair_through_code_task(world, ctx)
    description = door("code.task.pr_description", {"task_id": task_id}, ctx)
    assert description.ok, description.response_text
    assert "calc.py" in description.details["title"]
    assert "python -m pytest -q test_calc.py" in description.details["body"]
    assert "no pull request was opened" in description.details["body"].lower()

    # Land it on the session's own evidence, under the operator gesture.
    landed = _land(world, ctx, sid)
    assert remote_ref(bare, "feature") == landed["pushed_sha"], "the bare remote must carry the pushed identity"

    verified = door("repo.verify_remote", {"repo_session_id": sid}, ctx)
    assert verified.ok, verified.response_text
    assert verified.details["remote_sha"] == landed["pushed_sha"]

    sealed = door("repo.receipt", {"repo_session_id": sid}, ctx)
    assert sealed.ok, sealed.response_text
    assert sealed.details["push"]["outcome"] == "applied"
    assert sealed.details["remote_verification"]["verified"] is True


# ---------------------------------------------------------------------------
# The reordered, differently-named shape
# ---------------------------------------------------------------------------


def test_pr_workflow_repeated_with_a_new_shape_and_reordered_steps(world) -> None:
    """Same capability, different branch name and a different step order: CI read before the
    diff, the PR description prepared before the session-side landing. Order is not authority."""
    from tests.repoops._forge_fixture import UNIFIED_DIFF
    from tests.repoops._harness import context, door, git, head, remote_ref

    root, bare, forge = world
    # A differently named branch, so nothing about `feature` is baked in.
    from tests.repoops._harness import git as _git

    _git(root, "branch", "-m", "fix/add-operator")
    ctx = context(root)
    sid = _arm(world, ctx)
    sha = head(root)

    forge.route(
        "GET /actions/runs?head_sha=",
        {
            "workflow_runs": [
                {"id": 5151, "name": "unit", "status": "completed", "conclusion": "failure", "head_sha": sha, "logs_url": "u"}
            ]
        },
    )
    jobs = door("repo.ci.jobs", {"repo_session_id": sid}, ctx)
    assert jobs.ok and jobs.details["failed"], jobs.response_text

    forge.route("GET /pulls/7", UNIFIED_DIFF)
    diffed = door("repo.diff", {"repo_session_id": sid}, ctx)
    assert diffed.ok and diffed.details["paths"] == ["calc.py"]

    task_id = _repair_through_code_task(world, ctx)
    description = door("code.task.pr_description", {"task_id": task_id}, ctx)
    assert description.ok and "calc.py" in description.details["title"]

    landed = _land(world, ctx, sid, ref="fix/add-operator", message="repair add operator")
    assert remote_ref(bare, "fix/add-operator") == landed["pushed_sha"]

    verified = door("repo.verify_remote", {"repo_session_id": sid}, ctx)
    assert verified.ok and verified.details["remote_sha"] == landed["pushed_sha"]


# ---------------------------------------------------------------------------
# Negative edges specific to this workflow
# ---------------------------------------------------------------------------


def test_a_required_check_from_an_old_sha_cannot_serve_a_new_sha(world) -> None:
    """CI truth is requested FOR THE BOUND HEAD SHA. After the repair moves HEAD, the forge is
    asked for the NEW sha; a green run recorded at the old one never enters the picture."""
    from tests.repoops._harness import context, door, head

    root, bare, forge = world
    ctx = context(root)
    sid = _arm(world, ctx)
    old_sha = head(root)

    forge.route(
        "GET /actions/runs?head_sha=",
        {
            "workflow_runs": [
                {"id": 4242, "name": "unit", "status": "completed", "conclusion": "success", "head_sha": old_sha, "logs_url": "u"}
            ]
        },
    )
    first = door("repo.ci.jobs", {"repo_session_id": sid}, ctx)
    assert first.ok and first.details["failed"] == []

    # The repair lands; HEAD moves. The session's binding now describes a stale picture, and
    # the plane REFUSES to read CI through it -- that refusal is the law under test. Re-binding
    # is the only lawful way forward, and the fresh read is asked for the NEW sha.
    _repair_through_code_task(world, ctx)
    from tests.repoops._harness import FIXED, git

    git(root, "add", "calc.py")
    git(root, "-c", "user.name=fx", "-c", "user.email=fx@local", "commit", "-q", "-m", "repair")
    new_sha = head(root)
    assert new_sha != old_sha

    stale = door("repo.ci.jobs", {"repo_session_id": sid}, ctx)
    assert stale.ok is False and stale.status == "binding_diverged"

    # The landed repair is what the remote PR now carries (in the real flow the push moved it).
    from tests.repoops._forge_fixture import github_pull_request

    forge.route("GET /pulls/7", github_pull_request("7", base_sha=old_sha, head_sha=new_sha))
    forge.route("GET /commits/feature", {"sha": new_sha})
    door("repo.bind", {"repo_session_id": sid}, ctx)
    forge.calls.clear()
    again = door("repo.ci.jobs", {"repo_session_id": sid}, ctx)
    assert not again.ok and again.details.get('reason') == 'ci_revision_mismatch', again.response_text
    asked_shas = [c["url"].split("head_sha=")[-1].split("&")[0] for c in forge.calls if "actions/runs" in c["url"]]
    # The adapter asked for the sha the binding resolves NOW -- never the old one -- and the
    # old run (recorded at old_sha) is not silently reused.
    assert asked_shas == [new_sha], asked_shas
    forge.route("GET /actions/runs?head_sha=", {'workflow_runs': [
        {'id': 4243, 'name': 'unit', 'status': 'completed', 'conclusion': 'success', 'head_sha': new_sha}
    ]})
    fresh = door('repo.ci.jobs', {'repo_session_id': sid}, ctx)
    assert fresh.ok and all(row['head_sha'] == new_sha for row in fresh.details['jobs'])


def test_read_only_auth_refuses_ci_control_without_pretending(world) -> None:
    """A read-only token on a mutating forge call is a definitive 403: reported as refused,
    never as a performed rerun."""
    from tests.repoops._harness import context, door

    root, bare, forge = world
    ctx = context(root)
    sid = _arm(world, ctx)

    forge.route("GET /actions/runs?head_sha=", {"workflow_runs": []})
    forge.route("POST /actions/runs/4242/rerun", {"message": "Resource not accessible by integration"}, status=403)
    result = door("repo.ci.rerun", {"repo_session_id": sid, "job_id": "4242"}, ctx)
    assert result.ok is False
    assert result.status == "refused_by_forge"
    assert result.details.get("requested") is False
