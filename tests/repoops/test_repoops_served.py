"""The served surface: the operator's Authorize Push exists at the door, and only there.

These drive `core.web.api.service.dispatch_get` / `dispatch_post` -- the same functions the HTTP
server calls -- so a green row here is a row a real request produces.
"""

from __future__ import annotations

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


class _Runtime:
    """The minimum RuntimeServices surface `apply_runtime_headers` reads."""

    version = "test"

    def __getattr__(self, name):  # pragma: no cover - attribute probe
        return ""


def _get(path: str, **query):
    from core.web.api.service import dispatch_get

    return dispatch_get(
        path=path,
        query={k: [v] for k, v in query.items()},
        runtime=_Runtime(),
        model_name="test",
        client_host="127.0.0.1",
    )


def _post(path: str, body: dict, *, root: Path, client_host: str = "127.0.0.1"):
    from core.web.api.service import dispatch_post

    return dispatch_post(
        path=path,
        body=body,
        headers={"content-type": "application/json"},
        runtime=_Runtime(),
        model_name="test",
        workspace_root_provider=lambda: str(root),
        client_host=client_host,
    )


def _payload(response):
    import json

    body = getattr(response, "body", None)
    if isinstance(body, (bytes, bytearray)):
        return json.loads(body.decode("utf-8"))
    return json.loads(str(body))


def _drive_to_plan(root: Path, forge: RecordedForge, ctx: dict) -> tuple[str, str]:
    sha = head(root)
    forge.route("GET /pulls/7", github_pull_request("7", base_sha=git(root, "rev-parse", "HEAD~1").strip(), head_sha=sha))
    forge.route("GET /commits/feature", {"sha": sha})
    sid = door("repo.session.open", {"objective": "served", "pull_request": "7", **OPEN}, ctx).details["repo_session_id"]
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
    return sid, plan_hash


def test_the_console_lists_sessions_and_says_which_await_authorization(world) -> None:
    root, _bare, forge = world
    ctx = context(root)
    sid, _plan = _drive_to_plan(root, forge, ctx)

    listed = _payload(_get("/api/repoops/sessions"))
    assert listed["count"] >= 1
    row = next(r for r in listed["sessions"] if r["repo_session_id"] == sid)
    assert row["provider"] == "github"
    assert row["local_head"]
    assert sid in listed["awaiting_authorization"]

    one = _payload(_get("/api/repoops/session", id=sid))
    assert one["found"] is True
    assert one["session"]["diagnosis"]["path"] == "calc.py"

    missing = _payload(_get("/api/repoops/session", id="rs-nope"))
    assert missing["found"] is False


def test_authorize_push_is_owner_local_only(world) -> None:
    root, _bare, forge = world
    ctx = context(root)
    sid, plan_hash = _drive_to_plan(root, forge, ctx)

    remote_attempt = _post(
        "/api/repoops/authorize-push",
        {"repo_session_id": sid, "plan_hash": plan_hash},
        root=root,
        client_host="203.0.113.9",
    )
    assert remote_attempt.status == 403
    assert _payload(remote_attempt)["error"] == "owner_local_required"


def test_the_operator_gesture_authorizes_and_then_the_push_runs(world, monkeypatch) -> None:
    from core import policy_engine

    root, bare, forge = world
    ctx = context(root)
    original = policy_engine.get
    monkeypatch.setattr(
        policy_engine,
        "get",
        lambda key, default=None: True if key == "repo.real_push_enabled" else original(key, default),
    )
    sid, plan_hash = _drive_to_plan(root, forge, ctx)

    # The turn cannot authorize itself.
    assert door("repo.push.authorize", {"repo_session_id": sid, "plan_hash": plan_hash}, ctx).status == (
        "operator_gesture_required"
    )
    assert remote_ref(bare, "feature") == ""

    authorized = _payload(_post("/api/repoops/authorize-push", {"repo_session_id": sid, "plan_hash": plan_hash}, root=root))
    assert authorized["ok"] is True
    assert authorized["plan_hash"] == plan_hash
    assert authorized["expires_at"]

    pushed = door("repo.push", {"repo_session_id": sid, "simulate": False}, ctx)
    assert pushed.ok, pushed.response_text
    assert remote_ref(bare, "feature") == head(root)

    forge.route("GET /commits/feature", {"sha": head(root)})
    assert door("repo.verify_remote", {"repo_session_id": sid}, ctx).details["verified"] is True

    listed = _payload(_get("/api/repoops/sessions"))
    row = next(r for r in listed["sessions"] if r["repo_session_id"] == sid)
    assert row["remote_verified"] is True
    assert row["push_outcome"] == "applied"


def test_an_authorization_for_a_different_plan_is_refused_at_the_door(world) -> None:
    root, _bare, forge = world
    ctx = context(root)
    sid, _plan = _drive_to_plan(root, forge, ctx)
    refused = _payload(_post("/api/repoops/authorize-push", {"repo_session_id": sid, "plan_hash": "0" * 64}, root=root))
    assert refused["ok"] is False
    assert refused["status"] == "plan_mismatch"


def test_the_lifecycle_console_reports_state_and_never_conflates_it_with_availability(tmp_path, monkeypatch) -> None:
    from tests._toolchain_fixtures import PLUGIN_ID, make_plugin, reset_toolchain_state

    monkeypatch.setenv("VOOL_PLUGIN_LIFECYCLE_PATH", str(tmp_path / "lifecycle.json"))
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path))
    make_plugin(tmp_path, admit=False)
    reset_toolchain_state()

    empty = _payload(_get("/api/plugins/lifecycle"))
    assert empty["plugins"] == []
    assert empty["available"] == []

    installed = _payload(_post("/api/plugins/lifecycle", {"action": "install", "plugin_id": PLUGIN_ID}, root=tmp_path))
    assert installed["ok"] is True
    assert installed["stage"] == "installed"
    assert installed["available"] is False

    verified = _payload(_post("/api/plugins/lifecycle", {"action": "verify", "plugin_id": PLUGIN_ID}, root=tmp_path))
    assert verified["stage"] == "verified"
    assert verified["available"] is False

    enabled = _payload(_post("/api/plugins/lifecycle", {"action": "enable", "plugin_id": PLUGIN_ID}, root=tmp_path))
    assert enabled["available"] is True

    snapshot = _payload(_get("/api/plugins/lifecycle"))
    assert snapshot["available"] == [PLUGIN_ID]

    revoked = _payload(_post("/api/plugins/lifecycle", {"action": "revoke", "plugin_id": PLUGIN_ID}, root=tmp_path))
    assert revoked["available"] is False
    assert revoked["stage"] == "revoked"
    assert _payload(_get("/api/plugins/lifecycle"))["available"] == []
    reset_toolchain_state()


def test_the_lifecycle_console_is_owner_local_only(tmp_path) -> None:
    refused = _post("/api/plugins/lifecycle", {"action": "enable", "plugin_id": "x"}, root=tmp_path, client_host="8.8.8.8")
    assert refused.status == 403


def test_an_unknown_lifecycle_action_is_named_not_guessed(tmp_path) -> None:
    payload = _payload(_post("/api/plugins/lifecycle", {"action": "yolo", "plugin_id": "x"}, root=tmp_path))
    assert payload["ok"] is False
    assert "yolo" in payload["error"]
