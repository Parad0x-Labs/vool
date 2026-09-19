"""Revision 5: forge dispatch is decided by the DURABLE journal and ledger, across runtime
instances and across real OS processes.

In-process cases use separate `RepoOpsRuntime` instances (separate caches and locks) over the one
shared journal directory and the one shared effect ledger, with deterministic interleavings. The
multiprocess cases start real Python processes that each build their own runtime from the same
VOOL_HOME and race through the production door (`execute_runtime_tool`) against a real loopback
HTTP service speaking the GitHub REST shapes -- every POST that reaches the service is counted
there, independent of what any process reports about itself.

No live forge, credential or owner data is used. Data differs from the review's probes (other
issues, other texts, the update action, other numbers)."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.repoops._harness import build_repo, context, door, git, head
from tests.repoops.test_forge_actions import (
    _armed_session,
    _calls,
    _comment_payload,
    _operator_authorizes,
    _pr_payload,
    world,
)
from tests.repoops.test_forge_served_journey import LocalForge
from tests.repoops.test_forge_v3_independent_review import fail_session_storage

REPO_ROOT = Path(__file__).resolve().parents[2]


def _plan(ctx: dict, sid: str, **arguments) -> str:
    planned = door("repo.pr.request", {"repo_session_id": sid, **arguments}, ctx)
    assert planned.ok, planned.response_text
    return planned.details["action_hash"]


def _journal(sid: str) -> dict:
    from core.repoops.plane import session_dir

    return json.loads((session_dir() / f"{sid}.json").read_text(encoding="utf-8"))


def _ledger_states(leid: str) -> list[str]:
    from core.runtime_continuity import logical_effect_row_states

    return sorted(row["state"] for row in logical_effect_row_states(leid))


# ===========================================================================
# In-process: separate runtime instances over the one journal and the one ledger
# ===========================================================================


def test_a_stale_runtime_that_read_the_consent_before_the_update_landed_replays_it(world, monkeypatch) -> None:
    """Deterministic post-terminal interleaving on the UPDATE action: the stale runtime reads the
    unconsumed authorization, passes every early check (including the forge head re-read), and
    reaches its reservation only after the other runtime's PATCH landed and was journaled."""
    import core.repoops.plane as plane
    from core import runtime_continuity as continuity

    root, _bare, forge = world
    ctx = context(root)
    sid, sha, base = _armed_session(world, ctx)
    forge.route("GET /pulls/8", _pr_payload("8", base_sha=base, head_sha=sha, body="novel: release notes awaiting review"))
    action_hash = _plan(ctx, sid, action="update", number="8", body="novel: the reviewed release notes")
    assert _operator_authorizes(world, sid, action_hash)["ok"]
    forge.route("PATCH /pulls/8", _pr_payload("8", base_sha=base, head_sha=sha, body="novel: the reviewed release notes"))

    original = plane.repo_ops_runtime()
    stale = plane.RepoOpsRuntime()
    assert stale._load(sid).forge_authorization["consumed"] is False
    serving = {"runtime": stale}
    monkeypatch.setattr(plane, "repo_ops_runtime", lambda: serving["runtime"])
    real_reserve = continuity.reserve_logical_effect
    raced: dict = {}

    def reserve_after_the_winner_journaled(**kwargs):
        if not raced:
            raced["started"] = True
            serving["runtime"] = original
            raced["winner"] = door("repo.pr.update", {"repo_session_id": sid}, ctx)
            serving["runtime"] = stale
        return real_reserve(**kwargs)

    monkeypatch.setattr(continuity, "reserve_logical_effect", reserve_after_the_winner_journaled)
    loser = door("repo.pr.update", {"repo_session_id": sid}, ctx)

    winner = raced["winner"]
    assert winner.ok and not winner.details.get("replayed"), winner.details
    assert loser.ok and loser.details.get("replayed") is True, loser.details
    assert len(_calls(forge, method="PATCH")) == 1
    record = _journal(sid)["forge_actions"][action_hash]
    assert record["status"] == "applied" and _journal(sid)["forge_authorization"]["consumed"] is True
    # The winner's row is applied; the loser released only its own never-dispatched reservation.
    assert _ledger_states(record["logical_effect_id"]) == ["applied", "expired_pre_dispatch"]


def test_a_stale_writer_cannot_erase_an_applied_record_that_a_third_runtime_replays(world, monkeypatch) -> None:
    import core.repoops.plane as plane

    root, _bare, forge = world
    ctx = context(root)
    sid, _sha, _base = _armed_session(world, ctx)
    body = "novel: a stale writer must not erase this comment's record"
    action_hash = _plan(ctx, sid, action="comment", number="23", subject="issue", body=body)
    assert _operator_authorizes(world, sid, action_hash)["ok"]
    forge.route("POST /issues/23/comments", _comment_payload(55123, body=body))

    stale = plane.RepoOpsRuntime()
    snapshot = stale._load(sid)
    assert snapshot.forge_authorization["consumed"] is False
    first = door("repo.pr.comment", {"repo_session_id": sid}, ctx)
    assert first.ok and first.details["comment_id"] == "55123", first.details

    journal_path = plane.session_dir() / f"{sid}.json"
    landed = journal_path.read_bytes()
    # The stale copy (planned record, unconsumed consent) attempts a whole-file rewrite.
    snapshot.objective = "a stale rewrite attempt"
    assert stale._persist(snapshot) is False
    assert journal_path.read_bytes() == landed

    third = plane.RepoOpsRuntime()
    serving = {"runtime": third}
    monkeypatch.setattr(plane, "repo_ops_runtime", lambda: serving["runtime"])
    replay = door("repo.pr.comment", {"repo_session_id": sid}, ctx)
    assert replay.ok and replay.details.get("replayed") is True and replay.details["comment_id"] == "55123"
    assert len(_calls(forge, method="POST")) == 1

    # The stale runtime itself reloads on its next read: re-planning sees the applied action.
    serving["runtime"] = stale
    replanned = door(
        "repo.pr.request",
        {"repo_session_id": sid, "action": "comment", "number": "23", "subject": "issue", "body": body},
        ctx,
    )
    assert replanned.ok and replanned.details["already_applied"] is True
    assert len(_calls(forge, method="POST")) == 1


def test_identical_content_posts_again_only_as_a_new_operator_approved_operation(world, monkeypatch) -> None:
    """Replaying an OLD authorization sends nothing; the same words become a second comment only
    through a new plan and a new operator authorization -- matching content is not banned."""
    import core.repoops.plane as plane

    root, _bare, forge = world
    body = "novel: nightly status -- every required check is green"
    monday = context(root, session="status-thread-monday")
    sid1, _sha, _base = _armed_session(world, monday)
    hash1 = _plan(monday, sid1, action="comment", number="31", subject="issue", body=body)
    assert _operator_authorizes(world, sid1, hash1)["ok"]
    forge.route("POST /issues/31/comments", _comment_payload(70001, body=body))
    stale = plane.RepoOpsRuntime()
    stale._load(sid1)
    first = door("repo.pr.comment", {"repo_session_id": sid1}, monday)
    assert first.ok and first.details["comment_id"] == "70001", first.details

    serving = {"runtime": stale}
    monkeypatch.setattr(plane, "repo_ops_runtime", lambda: serving["runtime"])
    replay = door("repo.pr.comment", {"repo_session_id": sid1}, monday)
    assert replay.ok and replay.details.get("replayed") is True and replay.details["comment_id"] == "70001"
    assert len(_calls(forge, method="POST")) == 1

    serving["runtime"] = plane.RepoOpsRuntime()
    tuesday = context(root, session="status-thread-tuesday")
    sid2, _sha, _base = _armed_session(world, tuesday)
    hash2 = _plan(tuesday, sid2, action="comment", number="31", subject="issue", body=body)
    assert hash2 == hash1  # the same exact action identity ...
    assert _operator_authorizes(world, sid2, hash2)["ok"]  # ... newly and explicitly authorized
    forge.route("POST /issues/31/comments", _comment_payload(70002, body=body))
    second = door("repo.pr.comment", {"repo_session_id": sid2}, tuesday)
    assert second.ok and not second.details.get("replayed") and second.details["comment_id"] == "70002", second.details
    assert len(_calls(forge, method="POST")) == 2
    again = door("repo.pr.comment", {"repo_session_id": sid2}, tuesday)
    assert again.ok and again.details.get("replayed") is True and again.details["comment_id"] == "70002"
    assert len(_calls(forge, method="POST")) == 2
    assert _journal(sid1)["forge_actions"][hash1]["result"]["comment_id"] == "70001"


def test_a_stale_runtime_replays_a_landed_create_whose_winner_lost_its_journal_write(world, monkeypatch) -> None:
    import core.repoops.plane as plane
    from core import runtime_continuity as continuity

    root, _bare, forge = world
    ctx = context(root)
    sid, sha, base = _armed_session(world, ctx)
    title, body = "novel: draft for the reviewed release", "novel: an evidence-backed description"
    action_hash = _plan(ctx, sid, action="create", base_ref="main", title=title, body=body)
    assert _operator_authorizes(world, sid, action_hash)["ok"]
    forge.route("GET /pulls?head=", [])
    forge.route("POST /pulls", _pr_payload("64", base_sha=base, head_sha=sha, draft=True, title=title, body=body))
    stale = plane.RepoOpsRuntime()
    assert stale._load(sid).forge_authorization["consumed"] is False

    real_claim = continuity.mark_effect_dispatched
    with monkeypatch.context() as outage:
        def claim_then_lose_the_journal(**kwargs):
            won = real_claim(**kwargs)
            fail_session_storage(outage, sid, operation="replace")
            return won

        outage.setattr(continuity, "mark_effect_dispatched", claim_then_lose_the_journal)
        first = door("repo.pr.create", {"repo_session_id": sid}, ctx)
    assert first.ok and first.details["number"] == "64" and first.details["journal_persist_failed"] is True
    assert _journal(sid)["forge_actions"][action_hash]["status"] == "dispatching"

    monkeypatch.setattr(plane, "repo_ops_runtime", lambda: stale)
    replay = door("repo.pr.create", {"repo_session_id": sid}, ctx)
    assert replay.ok and replay.details.get("replayed") is True, replay.details
    assert replay.details.get("reconciled_from") == "effect_ledger"
    assert len(_calls(forge, method="POST", contains="/repos/o/r/pulls")) == 1


def test_another_sessions_applied_row_never_reconciles_this_dispatch_as_landed(world, monkeypatch) -> None:
    """The logical effect id is content-derived: two legitimate operations with the same words
    share it. A stranded dispatch must reconcile from ITS OWN ledger row -- another session's
    applied row must not report it as landed and silently lose the second operation."""
    from core import runtime_continuity as continuity
    from core.repoops.plane import repo_ops_runtime

    root, _bare, forge = world
    body = "novel: shared wording, two separately approved operations"
    ops_a = context(root, session="ops-a")
    sid_a, _sha, _base = _armed_session(world, ops_a)
    hash_a = _plan(ops_a, sid_a, action="comment", number="44", subject="issue", body=body)
    assert _operator_authorizes(world, sid_a, hash_a)["ok"]
    forge.route("POST /issues/44/comments", _comment_payload(61001, body=body))
    assert door("repo.pr.comment", {"repo_session_id": sid_a}, ops_a).ok

    ops_b = context(root, session="ops-b")
    sid_b, _sha, _base = _armed_session(world, ops_b)
    hash_b = _plan(ops_b, sid_b, action="comment", number="44", subject="issue", body=body)
    assert _operator_authorizes(world, sid_b, hash_b)["ok"]
    with monkeypatch.context() as outage:
        def claim_and_journal_outage(**kwargs):
            fail_session_storage(outage, sid_b, operation="replace")
            raise OSError("synthetic claim store outage")

        outage.setattr(continuity, "mark_effect_dispatched", claim_and_journal_outage)
        failed = door("repo.pr.comment", {"repo_session_id": sid_b}, ops_b)
    assert failed.status == "effect_journal_unavailable", failed.details
    assert len(_calls(forge, method="POST")) == 1
    stranded = _journal(sid_b)["forge_actions"][hash_b]
    assert stranded["status"] == "dispatching"
    assert _ledger_states(stranded["logical_effect_id"]) == ["applied", "expired_pre_dispatch"]

    repo_ops_runtime()._sessions.pop(sid_b, None)  # restart
    rearm = _operator_authorizes(world, sid_b, hash_b)
    assert rearm["ok"], rearm
    forge.route("POST /issues/44/comments", _comment_payload(61002, body=body))
    second = door("repo.pr.comment", {"repo_session_id": sid_b}, ops_b)
    assert second.ok and second.details["comment_id"] == "61002", second.details
    assert len(_calls(forge, method="POST")) == 2


def test_a_stale_runtime_cannot_blind_retry_an_unproven_comment(world, monkeypatch) -> None:
    import core.repoops.plane as plane

    root, _bare, forge = world
    ctx = context(root)
    sid, _sha, _base = _armed_session(world, ctx)
    body = "novel: this reply was lost in transit"
    action_hash = _plan(ctx, sid, action="comment", number="52", subject="issue", body=body)
    assert _operator_authorizes(world, sid, action_hash)["ok"]
    stale = plane.RepoOpsRuntime()
    assert stale._load(sid).forge_authorization["consumed"] is False
    forge.arm_unknown("POST /repos/o/r/issues/52/comments")
    first = door("repo.pr.comment", {"repo_session_id": sid}, ctx)
    assert not first.ok
    assert _journal(sid)["forge_actions"][action_hash]["status"] == "unknown"
    assert len(_calls(forge, method="POST")) == 1

    monkeypatch.setattr(plane, "repo_ops_runtime", lambda: stale)
    retry = door("repo.pr.comment", {"repo_session_id": sid}, ctx)
    assert retry.ok is False and retry.status == "unknown_reconciliation_required", retry.details
    assert len(_calls(forge, method="POST")) == 1
    refused = _operator_authorizes(world, sid, action_hash)
    assert refused["ok"] is False and refused["status"] == "resolution_required", refused


# ===========================================================================
# Real OS processes: each builds its own runtime from the same VOOL_HOME
# ===========================================================================

_CHILD = r'''
import errno, json, os, sys, time
from pathlib import Path

sys.path.insert(0, os.environ["R5_REPO_ROOT"])
home = Path(os.environ["VOOL_HOME"])
db = home / "data" / "vool_web0_v2.db"
db.parent.mkdir(parents=True, exist_ok=True)
from core.runtime_paths import configure_runtime_home
from storage.db import configure_default_db_path
from core.runtime_continuity import configure_runtime_continuity_db_path
configure_runtime_home(home)
configure_default_db_path(db)
configure_runtime_continuity_db_path(db)
import faulthandler
faulthandler.enable(file=sys.stderr, all_threads=True)
# Storage bootstrap completes BEFORE this process reports ready, as a daemon migrates at boot
# rather than while serving. Several processes running first-connection migrations of one store
# at the same instant is a storage-layer hazard measured separately
# (evidence/raw/probe-race-diagnostic); it is not what these races measure.
from core.runtime_continuity import logical_effect_row_states
logical_effect_row_states("lef-process-bootstrap")
from core.runtime_execution_tools import execute_runtime_tool

mode = sys.argv[1]
spec = json.loads(os.environ["R5_SPEC"])
ctx = {"workspace": spec["workspace"], "workspace_root": spec["workspace"],
       "session_id": spec["chat_session"], "operating_mode": "auto"}


def door(intent, arguments):
    result = execute_runtime_tool(intent, arguments, source_context=ctx)
    assert result is not None, intent
    return result


def emit(payload):
    sys.stdout.write("R5-RESULT " + json.dumps(payload, default=str) + "\n")
    sys.stdout.flush()
    os._exit(0)


if mode == "prepare":
    opened = door("repo.session.open", {"objective": spec["objective"], "pull_request": "8",
                                        "provider": "github", "namespace": "o/r"})
    sid = opened.details["repo_session_id"]
    door("repo.inspect", {"repo_session_id": sid})
    bound = door("repo.bind", {"repo_session_id": sid})
    planned = door("repo.pr.request", {"repo_session_id": sid, **spec["plan"]})
    from core.web.api.repoops_api import authorize_forge_action
    minted = authorize_forge_action(repo_session_id=sid, action_hash=planned.details.get("action_hash", ""),
                                    workspace_root="")
    emit({"sid": sid, "action_hash": planned.details.get("action_hash", ""), "bound": bool(bound.ok),
          "planned": bool(planned.ok), "minted": minted, "pid": os.getpid()})
elif mode == "authorize":
    from core.web.api.repoops_api import authorize_forge_action
    emit({**authorize_forge_action(repo_session_id=spec["sid"], action_hash=spec["action_hash"],
                                   workspace_root=""), "pid": os.getpid()})
elif mode == "dispatch":
    from core.repoops.plane import repo_ops_runtime, session_dir
    import core.runtime_continuity as continuity

    snapshot = repo_ops_runtime()._load(spec["sid"])
    Path(os.environ["R5_READY"]).write_text(json.dumps({
        "pid": os.getpid(),
        "snapshot_consumed": bool((snapshot.forge_authorization or {}).get("consumed")),
        "snapshot_authorization_id": str((snapshot.forge_authorization or {}).get("authorization_id") or ""),
    }))
    go = Path(os.environ["R5_GO"])
    deadline = time.monotonic() + 120
    while not go.exists():
        if time.monotonic() > deadline:
            emit({"error": "never released", "pid": os.getpid()})
        time.sleep(0.002)
    target = session_dir() / (spec["sid"] + ".json")

    def arm_journal_rename_failure():
        real_replace = os.replace

        def replace(src, dst, *args, **kwargs):
            if Path(dst) == target:
                raise OSError(errno.EACCES, "synthetic journal rename denied")
            return real_replace(src, dst, *args, **kwargs)

        os.replace = replace

    faults = set(spec.get("faults") or [])
    if "final_journal_write" in faults:
        real_claim = continuity.mark_effect_dispatched

        def claim_then_lose_the_journal(**kwargs):
            won = real_claim(**kwargs)
            arm_journal_rename_failure()
            return won

        continuity.mark_effect_dispatched = claim_then_lose_the_journal
    if "claim_and_recovery_journal" in faults:
        def claim_store_outage(**kwargs):
            arm_journal_rename_failure()
            raise OSError(errno.EIO, "synthetic claim store outage")

        continuity.mark_effect_dispatched = claim_store_outage
    result = door(spec["intent"], {"repo_session_id": spec["sid"]})
    details = dict(result.details or {})
    emit({"pid": os.getpid(), "ok": bool(result.ok), "status": str(result.status),
          "replayed": bool(details.get("replayed")), "number": str(details.get("number") or ""),
          "comment_id": str(details.get("comment_id") or ""),
          "journal_persist_failed": bool(details.get("journal_persist_failed")),
          "reconciled_from": str(details.get("reconciled_from") or ""),
          "text": str(result.response_text or "")[:600]})
'''

MULTIPROCESS_ACTIONS = {
    "create": {
        "intent": "repo.pr.create",
        "plan": {"action": "create", "base_ref": "main", "title": "Draft: reviewed repair (process race)",
                 "body": "Evidence-backed description of the reviewed repair."},
        "post": "/repos/o/r/pulls",
    },
    "comment": {
        "intent": "repo.pr.comment",
        "plan": {"action": "comment", "number": "23", "subject": "issue",
                 "body": "Novel: the reviewed repair is ready for another look."},
        "post": "/repos/o/r/issues/23/comments",
    },
}

# Every way a losing process may end without a second write: the ledger's active row,
# the lost compare-and-set, consent the durable journal already records spent, a journal that moved
# under it, or a replay of the landed result.
LOSING_STATUSES = {
    "duplicate_effect_blocked",
    "claim_lost_to_another_executor",
    "authorization_consumed",
    "journal_contended",
}


@pytest.fixture
def shared_home(tmp_path):
    workspace, _bare = build_repo(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    sha = head(workspace)
    base = git(workspace, "rev-parse", "HEAD~1").strip()
    create = MULTIPROCESS_ACTIONS["create"]["plan"]
    comment = MULTIPROCESS_ACTIONS["comment"]["plan"]
    forge = LocalForge()
    forge.routes.update(
        {
            "/repos/o/r/commits/feature": {"sha": sha},
            "/repos/o/r/pulls/8": _pr_payload("8", base_sha=base, head_sha=sha, url="https://github.local/o/r/pull/8"),
            "/repos/o/r/pulls?head=": [],
            "/repos/o/r/pulls": _pr_payload("41", base_sha=base, head_sha=sha, draft=True, title=create["title"],
                                            body=create["body"], url="https://github.local/o/r/pull/41"),
            "/repos/o/r/issues/23/comments": _comment_payload(
                88001, body=comment["body"], url="https://github.local/o/r/issues/23#issuecomment-88001"
            ),
        }
    )
    forge.__enter__()
    env = dict(os.environ)
    env.pop("PYTEST_CURRENT_TEST", None)
    env.update(
        {
            "PYTHONPATH": str(REPO_ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
            "R5_REPO_ROOT": str(REPO_ROOT),
            "VOOL_HOME": str(home),
            "VOOL_REPOOPS_DIR": str(home / "repo_sessions"),
            "VOOL_BLACKBOX_DIR": str(home / "blackbox"),
            "VOOL_CODE_TASK_DIR": str(home / "code_tasks"),
            "VOOL_FORGE_BASE_URL_GITHUB": forge.base_url,
            "VOOL_KEY_STORAGE_MODE": "file",
            "VOOL_SKIP_PROVIDER_PREWARM": "1",
            "VOOL_LOCAL_MODELS_ENABLED": "0",
            "VOOL_DISABLE_MESH_DAEMON": "1",
            "VOOL_DISABLE_COMPUTE_MODE": "1",
            "VOOL_DISABLE_STUN": "1",
        }
    )
    children: list[subprocess.Popen] = []
    try:
        yield {"workspace": workspace, "home": home, "forge": forge, "env": env, "children": children,
               "tmp": tmp_path}
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=30)
        forge.__exit__(None, None, None)


def _spec(world_home: dict, **extra) -> dict:
    return {"workspace": str(world_home["workspace"]), "chat_session": "multiprocess-forge",
            "objective": "race one authorized forge action across processes", **extra}


def _result_of(stdout: str, stderr: str, returncode: int) -> dict:
    lines = [line for line in stdout.splitlines() if line.startswith("R5-RESULT ")]
    assert returncode == 0 and lines, (returncode, stdout[-3000:], stderr[-3000:])
    return json.loads(lines[-1][len("R5-RESULT "):])


def _run(world_home: dict, mode: str, spec: dict, **env_extra) -> dict:
    completed = subprocess.run(
        [sys.executable, "-c", _CHILD, mode],
        env={**world_home["env"], "R5_SPEC": json.dumps(spec), **env_extra},
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=240,
    )
    return _result_of(completed.stdout, completed.stderr, completed.returncode)


def _start_dispatchers(world_home: dict, spec: dict, count: int, name: str) -> tuple[list[subprocess.Popen], list[Path], Path]:
    """Start ``count`` dispatcher processes, each holding its OWN snapshot of the session, all
    waiting on one release file. They are brought up one at a time (each finishes its storage
    bootstrap and reads its snapshot before the next starts); the race itself -- reservation,
    write-ahead, claim, write -- begins for all of them at the same release."""
    go = world_home["tmp"] / f"{name}-go"
    readies = [world_home["tmp"] / f"{name}-ready-{index}" for index in range(count)]
    processes = []
    for ready in readies:
        process = subprocess.Popen(
            [sys.executable, "-c", _CHILD, "dispatch"],
            env={**world_home["env"], "R5_SPEC": json.dumps(spec), "R5_READY": str(ready), "R5_GO": str(go)},
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        world_home["children"].append(process)
        processes.append(process)
        deadline = time.monotonic() + 240
        while not ready.exists():
            assert time.monotonic() < deadline, "a dispatcher never became ready"
            assert process.poll() is None, process.communicate()
            time.sleep(0.02)
    return processes, readies, go


def _collect(processes: list[subprocess.Popen]) -> list[dict]:
    results = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=240)
        results.append(_result_of(stdout, stderr, process.returncode))
    return results


def _shared_journal(world_home: dict, sid: str) -> dict:
    return json.loads((world_home["home"] / "repo_sessions" / f"{sid}.json").read_text(encoding="utf-8"))


def _shared_ledger(world_home: dict, leid: str) -> list[tuple[str, str]]:
    conn = sqlite3.connect(str(world_home["home"] / "data" / "vool_web0_v2.db"), timeout=30)
    try:
        rows = conn.execute(
            "SELECT state, reason FROM runtime_unresolved_effects WHERE logical_effect_id = ? ORDER BY created_at",
            (leid,),
        ).fetchall()
    finally:
        conn.close()
    return [(str(state), str(reason)) for state, reason in rows]


def _prepared(world_home: dict, action: str) -> tuple[dict, dict]:
    config = MULTIPROCESS_ACTIONS[action]
    prepared = _run(world_home, "prepare", _spec(world_home, plan=config["plan"]))
    assert prepared["bound"] and prepared["planned"] and prepared["minted"]["ok"], prepared
    spec = _spec(world_home, sid=prepared["sid"], action_hash=prepared["action_hash"], intent=config["intent"])
    return prepared, spec


@pytest.mark.parametrize("action", ["create", "comment"])
def test_real_processes_race_one_authorized_action_and_exactly_one_write_lands(shared_home, action) -> None:
    forge = shared_home["forge"]
    config = MULTIPROCESS_ACTIONS[action]
    prepared, spec = _prepared(shared_home, action)
    processes, readies, go = _start_dispatchers(shared_home, spec, 4, f"race-{action}")
    snapshots = [json.loads(ready.read_text()) for ready in readies]
    assert all(snapshot["snapshot_consumed"] is False for snapshot in snapshots), snapshots
    assert len({snapshot["pid"] for snapshot in snapshots}) == 4
    go.write_text("go")
    results = _collect(processes)

    assert len(forge.posts(config["post"])) == 1, (results, forge.calls)
    winners = [r for r in results if r["ok"] and not r["replayed"]]
    assert len(winners) == 1, results
    for loser in (r for r in results if r is not winners[0]):
        assert (loser["ok"] and loser["replayed"]) or (not loser["ok"] and loser["status"] in LOSING_STATUSES), results
    journal = _shared_journal(shared_home, prepared["sid"])
    record = journal["forge_actions"][prepared["action_hash"]]
    assert record["status"] == "applied" and journal["forge_authorization"]["consumed"] is True
    if action == "create":
        assert record["result"]["number"] == "41", record["result"]
    else:
        assert record["result"]["comment_id"] == "88001", record["result"]
    states = [state for state, _reason in _shared_ledger(shared_home, record["logical_effect_id"])]
    assert states.count("applied") == 1 and set(states) <= {"applied", "expired_pre_dispatch"}, states


def test_a_process_holding_a_stale_snapshot_after_the_winner_journaled_never_resends(shared_home) -> None:
    forge = shared_home["forge"]
    _, spec = _prepared(shared_home, "comment")
    (stale,), (stale_ready,), stale_go = _start_dispatchers(shared_home, spec, 1, "stale")
    assert json.loads(stale_ready.read_text())["snapshot_consumed"] is False

    winner_go = shared_home["tmp"] / "winner-go"
    winner_go.write_text("go")
    winner = _run(shared_home, "dispatch", spec, R5_READY=str(shared_home["tmp"] / "winner-ready"), R5_GO=str(winner_go))
    assert winner["ok"] and not winner["replayed"] and winner["comment_id"] == "88001", winner
    assert len(forge.posts("/repos/o/r/issues/23/comments")) == 1

    stale_go.write_text("go")
    (late,) = _collect([stale])
    assert late["ok"] and late["replayed"] and late["comment_id"] == "88001", late
    assert len(forge.posts("/repos/o/r/issues/23/comments")) == 1


def test_a_journal_gap_in_the_winning_process_is_reconciled_by_a_stale_process_without_resending(shared_home) -> None:
    forge = shared_home["forge"]
    prepared, spec = _prepared(shared_home, "create")
    (stale,), (stale_ready,), stale_go = _start_dispatchers(shared_home, spec, 1, "gap-stale")
    assert json.loads(stale_ready.read_text())["snapshot_consumed"] is False

    winner_go = shared_home["tmp"] / "gap-winner-go"
    winner_go.write_text("go")
    winner = _run(
        shared_home, "dispatch", {**spec, "faults": ["final_journal_write"]},
        R5_READY=str(shared_home["tmp"] / "gap-winner-ready"), R5_GO=str(winner_go),
    )
    assert winner["ok"] and winner["number"] == "41" and winner["journal_persist_failed"], winner
    stranded = _shared_journal(shared_home, prepared["sid"])["forge_actions"][prepared["action_hash"]]
    assert stranded["status"] == "dispatching"

    stale_go.write_text("go")
    (late,) = _collect([stale])
    assert late["ok"] and late["replayed"] and late["reconciled_from"] == "effect_ledger", late
    assert len(forge.posts("/repos/o/r/pulls")) == 1
    record = _shared_journal(shared_home, prepared["sid"])["forge_actions"][prepared["action_hash"]]
    assert record["status"] == "applied" and record["reconciled_from"] == "effect_ledger"


def test_a_compound_claim_and_journal_outage_across_processes_ends_in_exactly_one_write(shared_home) -> None:
    forge = shared_home["forge"]
    prepared, spec = _prepared(shared_home, "comment")
    (held,), (held_ready,), held_go = _start_dispatchers(shared_home, spec, 1, "compound-held")
    held_snapshot = json.loads(held_ready.read_text())
    assert held_snapshot["snapshot_consumed"] is False

    failing_go = shared_home["tmp"] / "compound-failing-go"
    failing_go.write_text("go")
    failed = _run(
        shared_home, "dispatch", {**spec, "faults": ["claim_and_recovery_journal"]},
        R5_READY=str(shared_home["tmp"] / "compound-failing-ready"), R5_GO=str(failing_go),
    )
    assert not failed["ok"] and failed["status"] == "effect_journal_unavailable", failed
    assert not forge.posts("/repos/o/r/issues/23/comments")
    stranded = _shared_journal(shared_home, prepared["sid"])
    assert stranded["forge_actions"][prepared["action_hash"]]["status"] == "dispatching"
    assert stranded["forge_authorization"]["consumed"] is True

    # A separate operator process re-arms the provably-unsent action with a NEW authorization.
    rearmed = _run(shared_home, "authorize", spec)
    assert rearmed["ok"], rearmed
    fresh_authorization = _shared_journal(shared_home, prepared["sid"])["forge_authorization"]
    assert fresh_authorization["authorization_id"] != held_snapshot["snapshot_authorization_id"]

    # The process still holding the OLD snapshot dispatches on the consent the journal holds NOW.
    held_go.write_text("go")
    (resumed,) = _collect([held])
    assert resumed["ok"] and not resumed["replayed"] and resumed["comment_id"] == "88001", resumed
    assert len(forge.posts("/repos/o/r/issues/23/comments")) == 1

    after_go = shared_home["tmp"] / "compound-after-go"
    after_go.write_text("go")
    after = _run(shared_home, "dispatch", spec, R5_READY=str(shared_home["tmp"] / "compound-after-ready"), R5_GO=str(after_go))
    assert after["ok"] and after["replayed"], after
    assert len(forge.posts("/repos/o/r/issues/23/comments")) == 1
