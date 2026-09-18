"""Independent filesystem-failure probes; no external forge or owner data."""
import errno
import json
from pathlib import Path

import pytest

from tests.repoops._harness import door
from tests.repoops.test_forge_actions import world, _calls, _operator_authorizes
from tests.repoops.test_forge_integration_review import prepare


def fail_session_storage(monkeypatch, sid, *, operation):
    from core.repoops.plane import session_dir

    target = session_dir() / f"{sid}.json"
    if operation == "write":
        original = Path.write_text

        def write(path, *args, **kwargs):
            if path == target.with_suffix(".json.tmp"):
                raise OSError(errno.ENOSPC, "synthetic journal disk full")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", write)
    else:
        import os
        original = os.replace

        def replace(src, dst, *args, **kwargs):
            if Path(dst) == target:
                raise OSError(errno.EACCES, "synthetic journal rename denied")
            return original(src, dst, *args, **kwargs)

        monkeypatch.setattr(os, "replace", replace)
    return target


@pytest.mark.parametrize("action,operation", [("create", "write"), ("comment", "replace")])
def test_real_journal_io_failure_must_prevent_dispatch(world, monkeypatch, action, operation):
    forge, ctx, sid, *_ = prepare(world, action)
    target = fail_session_storage(monkeypatch, sid, operation=operation)
    before = target.read_bytes()
    result = door(f"repo.pr.{action}", {"repo_session_id": sid}, ctx)
    assert target.read_bytes() == before
    assert not _calls(forge, method="POST"), (result.status, result.details)
    assert not result.ok


@pytest.mark.parametrize("action,operation", [("create", "replace"), ("comment", "write")])
def test_restart_after_real_journal_outage_never_duplicates(world, monkeypatch, action, operation):
    from core.repoops.plane import repo_ops_runtime

    forge, ctx, sid, *_ = prepare(world, action)
    with monkeypatch.context() as failure:
        fail_session_storage(failure, sid, operation=operation)
        first = door(f"repo.pr.{action}", {"repo_session_id": sid}, ctx)
    # Storage is healthy, cached state is lost, but the actual continuity ledger survives.
    repo_ops_runtime()._sessions.pop(sid, None)
    second = door(f"repo.pr.{action}", {"repo_session_id": sid}, ctx)
    assert len(_calls(forge, method="POST")) <= 1, (first.status, second.status, _calls(forge, method="POST"))


@pytest.mark.parametrize("action", ["create", "comment"])
def test_marker_and_recovery_journal_outage_is_restart_recoverable(world, monkeypatch, action):
    from core import runtime_continuity as continuity
    from core.repoops.plane import repo_ops_runtime, session_dir

    forge, ctx, sid, _, _, action_hash = prepare(world, action)
    with monkeypatch.context() as failure:
        def marker_failure(**kwargs):
            # The write-ahead session was persisted. Its recovery write now fails too.
            fail_session_storage(failure, sid, operation="replace")
            raise OSError(errno.EIO, "synthetic marker storage failure")

        failure.setattr(continuity, "mark_effect_dispatched", marker_failure)
        first = door(f"repo.pr.{action}", {"repo_session_id": sid}, ctx)
    assert not _calls(forge, method="POST")
    assert first.status == "effect_journal_unavailable"
    disk = json.loads((session_dir() / f"{sid}.json").read_text())
    repo_ops_runtime()._sessions.pop(sid, None)
    rearm = _operator_authorizes(world, sid, action_hash)
    assert rearm["ok"], (disk["forge_actions"][action_hash], rearm)
    resumed = door(f"repo.pr.{action}", {"repo_session_id": sid}, ctx)
    assert resumed.ok, resumed.details
    assert len(_calls(forge, method="POST")) == 1
