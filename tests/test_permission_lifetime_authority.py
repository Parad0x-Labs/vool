"""Usable task/chat permissions and honest bypass lifetimes (mission 2026-09-18, Priority 4).

Covers the authority owners directly: `core.mode_permission_policy` for grant minting,
validation, revocation and persistence, and the Manual-mode chat workspace approval that rides
the internal-authority consult. Controls prove the boundaries: money/secrets/deletes never ride
a broader ordinary grant, other chats inherit nothing, and returning to Manual invalidates a
bypass token server-side.
"""
import os
from pathlib import Path

import pytest

from core.mode_permission_policy import (
    _BYPASS_GRANTS,
    _CHAT_WORKSPACE_AUTHORITY,
    _INTERNAL_AUTHORITY,
    PermissionAction,
    PermissionEffect,
    activate_bypass_grant,
    chat_workspace_authority_state,
    decide_tool_call,
    grant_chat_workspace_authority,
    revoke_bypass_grant,
    revoke_chat_workspace_authority,
    revoke_internal_authority,
    revoke_session_bypass_grants,
    set_active_mode,
    validate_bypass_grant,
)


def _activate(**kwargs):
    """Mint a single-use confirmation for these exact bindings, then activate."""
    from core.mode_permission_policy import request_bypass_confirmation

    kwargs = dict(kwargs)
    kwargs.setdefault("scope", "task")
    mint_kwargs = {
        k: v for k, v in kwargs.items() if k not in {"confirmation_id", "explicit_confirmation"}
    }
    kwargs["confirmation_id"] = request_bypass_confirmation(**mint_kwargs)
    kwargs.pop("explicit_confirmation", None)
    return activate_bypass_grant(**kwargs)

@pytest.fixture(autouse=True)
def _isolated_authority_store(tmp_path, monkeypatch, request):
    """One durable mirror per test, except the real-restart proof (which needs the real paths)."""
    from core import mode_permission_policy as policy

    if request.node.get_closest_marker("real_restart_store"):
        yield
        return
    store = tmp_path / "authority" / "bypass_grants.json"
    monkeypatch.setattr(policy, "_bypass_grants_path", lambda: store)
    yield


def _ws(tmp_path):
    root = tmp_path / "trusted"
    root.mkdir(exist_ok=True)
    (root / "sub").mkdir(exist_ok=True)
    return str(root)


def _bind_chat_to_workspace(session_id: str, ws: str) -> str:
    """Create the SERVER-OWNED trust an until-off grant is minted against.

    The chat's workspace is a registered project the server binds the chat namespace to -- the
    same authority the /api/mode grant paths resolve from. A bare directory name in a request
    body is not authority, so the tests build the binding the same way the product does.
    """
    from core import project_store
    from core.context_namespace import ensure_chat_namespace

    ok, payload = project_store.create_project("lifetime-test", ws)
    assert ok, payload
    project_id = str(payload["id"])
    namespace = ensure_chat_namespace(session_id, project_id=project_id, grant_confirmed_profile=True)
    assert namespace.lifecycle_state == "active"
    return project_id


def test_until_off_bypass_is_chat_and_workspace_bound_and_has_no_timer(tmp_path):
    ws = _ws(tmp_path)
    _bind_chat_to_workspace("chat-1", ws)
    grant = _activate(
        session_id="chat-1", scope="session", explicit_confirmation=True,
        until_off=True, workspace_root=ws,
    )
    assert grant["until_off"] is True
    assert grant["expires_at"] is None  # no enormous timestamp standing in for infinity
    assert validate_bypass_grant(grant["token"], session_id="chat-1", workspace_root=ws) is not None
    assert validate_bypass_grant(grant["token"], session_id="chat-2", workspace_root=ws) is None
    # Moving the chat to another workspace must not carry the old grant.
    other = tmp_path / "other-ws"
    other.mkdir(exist_ok=True)
    assert validate_bypass_grant(grant["token"], session_id="chat-1", workspace_root=str(other)) is None
    # A stale client root is not authority either: the chat's server-side binding is what the
    # consult re-reads, so naming the OLD root after the chat moved elsewhere fails closed.
    from core.context_namespace import set_chat_namespace_project

    moved_project = tmp_path / "moved-project"
    moved_project.mkdir(exist_ok=True)
    from core import project_store as _ps

    ok, moved = _ps.create_project("moved", str(moved_project))
    assert ok
    set_chat_namespace_project("chat-1", str(moved["id"]))
    assert validate_bypass_grant(grant["token"], session_id="chat-1", workspace_root=ws) is None
    assert validate_bypass_grant(grant["token"], session_id="chat-1", workspace_root=str(moved_project)) is None


def test_until_off_grant_survives_a_restart_through_its_persisted_mirror(tmp_path, monkeypatch):
    from core import mode_permission_policy as policy

    ws = _ws(tmp_path)
    _bind_chat_to_workspace("chat-1", ws)
    grant = _activate(
        session_id="chat-1", scope="session", explicit_confirmation=True,
        until_off=True, workspace_root=ws,
    )
    token = grant["token"]
    # Simulate the restart: drop memory, restore from the durable mirror.
    monkeypatch.setattr(policy, "_BYPASS_GRANTS_RESTORED", True)  # block the lazy re-load hook
    _BYPASS_GRANTS.clear()
    assert validate_bypass_grant(token, session_id="chat-1", workspace_root=ws) is None
    policy._BYPASS_GRANTS_RESTORED = False
    loaded = policy.restore_persisted_bypass_grants()
    assert loaded == 1
    assert validate_bypass_grant(token, session_id="chat-1", workspace_root=ws) is not None


@pytest.mark.real_restart_store
def test_until_off_grant_survives_a_real_process_restart(tmp_path):
    """The restart proof in a SECOND process, not a cleared dictionary.

    Restores and validates the grant from the durable mirror in a fresh interpreter against the
    SAME run profile (VOOL_HOME, sqlite namespaces, project store) the test process uses: the
    file, the key, the chat binding and the project store are all read from disk the way a real
    app restart reads them. Every other test in this file redirects the store path; this one
    alone uses the real resolution, so the run-home mirror contains exactly this test's grant.
    """
    import json
    import os
    import subprocess
    import sys

    ws = _ws(tmp_path)
    _bind_chat_to_workspace("chat-r", ws)
    grant = _activate(
        session_id="chat-r", scope="session", explicit_confirmation=True,
        until_off=True, workspace_root=ws,
    )
    from storage.db import active_default_db_path

    # The child reads the SAME sqlite file the parent wrote (the test session isolates its db
    # away from the home default, so the child is pointed at it explicitly, the way a real
    # restart would simply find it in the profile).
    script = (
        "import json,sys\n"
        "from storage.db import active_default_db_path, configure_default_db_path\n"
        "configure_default_db_path(sys.argv[3])\n"
        "from core.mode_permission_policy import restore_persisted_bypass_grants, validate_bypass_grant\n"
        "loaded = restore_persisted_bypass_grants()\n"
        "ok = validate_bypass_grant(sys.argv[1], session_id='chat-r', workspace_root=sys.argv[2])\n"
        "print(json.dumps({'loaded': loaded, 'valid': ok is not None}))\n"
    )
    db_path = active_default_db_path()
    result = subprocess.run(
        [sys.executable, "-c", script, grant["token"], ws, db_path],
        capture_output=True, text=True, cwd=os.getcwd(), env={**os.environ}, timeout=60,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {"loaded": 1, "valid": True}, payload
    # And the durable copy is gone once revoked in the parent: the child would restore nothing.
    assert revoke_bypass_grant(grant["token"]) is True
    result2 = subprocess.run(
        [sys.executable, "-c", script, grant["token"], ws, db_path],
        capture_output=True, text=True, cwd=os.getcwd(), env={**os.environ}, timeout=60,
    )
    assert result2.returncode == 0, result2.stderr[-2000:]
    payload2 = json.loads(result2.stdout.strip().splitlines()[-1])
    assert payload2 == {"loaded": 0, "valid": False}, payload2


def test_revocation_blocks_the_next_action_even_after_a_restore(tmp_path):
    ws = _ws(tmp_path)
    _bind_chat_to_workspace("chat-1", ws)
    grant = _activate(
        session_id="chat-1", scope="session", explicit_confirmation=True,
        until_off=True, workspace_root=ws,
    )
    assert revoke_bypass_grant(grant["token"]) is True
    assert validate_bypass_grant(grant["token"], session_id="chat-1", workspace_root=ws) is None
    # The persisted mirror drops revoked grants: a restart cannot resurrect the authority.
    from core import mode_permission_policy as policy

    _BYPASS_GRANTS.clear()
    policy._BYPASS_GRANTS_RESTORED = False
    assert policy.restore_persisted_bypass_grants() == 0


def test_timed_bypass_supports_hours_and_a_custom_duration():
    grant = _activate(
        session_id="chat-1", scope="session", explicit_confirmation=True, duration_seconds=7200,
    )
    assert grant["until_off"] is False
    assert 7000 <= grant["expires_at"] - grant["issued_at"] <= 7200
    long_grant = _activate(
        session_id="chat-1", scope="session", explicit_confirmation=True, duration_seconds=5 * 3600,
    )
    assert long_grant["expires_at"] - long_grant["issued_at"] == 5 * 3600


def test_until_off_is_refused_for_task_and_project_scopes(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        _activate(
            session_id="chat-1", scope="task", task_id="t1", explicit_confirmation=True,
            until_off=True, workspace_root=_ws(tmp_path),
        )
    with pytest.raises(ValueError):
        _activate(
            session_id="chat-1", scope="project", project_id="p1", explicit_confirmation=True,
            until_off=True, workspace_root=_ws(tmp_path),
        )


def test_returning_to_manual_invalidates_the_bypass_token_server_side(tmp_path):
    ws = _ws(tmp_path)
    _bind_chat_to_workspace("chat-1", ws)
    grant = _activate(
        session_id="chat-1", scope="session", explicit_confirmation=True,
        until_off=True, workspace_root=ws,
    )
    set_active_mode("chat-1", "bypass_permissions", bypass_token=grant["token"], workspace_root=ws)
    state = set_active_mode("chat-1", "manual")
    assert _BYPASS_GRANTS[grant["token"]]["revoked"] is True
    assert "bypass_revocation_not_durable" not in state  # the durable write succeeded
    # A stale client page still holding the token cannot re-arm bypass through the context path.
    assert validate_bypass_grant(grant["token"], session_id="chat-1", workspace_root=ws) is None


def test_chat_deletion_revokes_every_session_grant(tmp_path):
    ws = _ws(tmp_path)
    _bind_chat_to_workspace("chat-1", ws)
    first = _activate(
        session_id="chat-1", scope="session", explicit_confirmation=True,
        until_off=True, workspace_root=ws,
    )
    second = _activate(
        session_id="chat-1", scope="session", explicit_confirmation=True, duration_seconds=1800,
    )
    assert revoke_session_bypass_grants("chat-1") == 2
    assert validate_bypass_grant(first["token"], session_id="chat-1", workspace_root=ws) is None
    assert validate_bypass_grant(second["token"], session_id="chat-1") is None
    assert revoke_session_bypass_grants("chat-2") == 0


# --- durable-store failure handling: no false durable-success, no silent resurrection ------------


def test_activation_when_storage_fails_is_refused_and_rolled_back(tmp_path, monkeypatch):
    import pytest

    from core import mode_permission_policy as policy

    ws = _ws(tmp_path)
    _bind_chat_to_workspace("chat-store", ws)
    real_replace = os.replace

    def _failing_replace(src, dst, *args, **kwargs):
        if str(dst).endswith("bypass_grants.json"):
            raise OSError("disk full (fixture)")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(policy.os, "replace", _failing_replace)
    with pytest.raises(ValueError, match="could not be recorded durably"):
        _activate(
            session_id="chat-store", scope="session", explicit_confirmation=True,
            until_off=True, workspace_root=ws,
        )
    # The mint was rolled back: nothing is in memory, so no consult can find authority.
    assert all(not g.get("until_off") or g.get("session_id") != "chat-store" for g in _BYPASS_GRANTS.values())


def test_revocation_when_storage_fails_is_reported_not_swallowed(tmp_path, monkeypatch):
    import pytest

    from core import mode_permission_policy as policy
    from core.mode_permission_policy import BypassStoreError

    ws = _ws(tmp_path)
    _bind_chat_to_workspace("chat-store", ws)
    grant = _activate(
        session_id="chat-store", scope="session", explicit_confirmation=True,
        until_off=True, workspace_root=ws,
    )
    token = grant["token"]
    real_replace = os.replace

    def _failing_replace(src, dst, *args, **kwargs):
        if str(dst).endswith("bypass_grants.json"):
            raise OSError("disk full (fixture)")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(policy.os, "replace", _failing_replace)
    # The revocation journal is the store's second durable record; this fixture removes BOTH, so
    # what is pinned here is the true residual: nothing could record the revocation. (A mirror
    # failure ALONE is no longer this case -- the journal records it durably, which the restart
    # acceptance below proves as the repaired product behavior.)
    monkeypatch.setattr(policy, "_bypass_revocations_path", lambda: None)
    with pytest.raises(BypassStoreError):
        revoke_bypass_grant(token)
    # The in-memory revocation stands for this process -- the action is really blocked now.
    assert _BYPASS_GRANTS[token]["revoked"] is True
    assert validate_bypass_grant(token, session_id="chat-store", workspace_root=ws) is None
    monkeypatch.undo()
    # Storage recovered: the next mirror write clears the stale entry, so a restart after the
    # recovery loads no grant (the earlier failure is repaired, not immortal).
    policy._persist_bypass_grants_locked()
    _BYPASS_GRANTS.clear()
    policy._BYPASS_GRANTS_RESTORED = False
    assert policy.restore_persisted_bypass_grants() == 0


def test_a_revocation_the_mirror_could_not_record_does_not_survive_a_restart(tmp_path, monkeypatch):
    """The restart window the named non-durable failure used to leave open, now closed.

    The mirror's atomic rewrite fails (directory unwritable, replace refused); the revocation is
    recorded in the journal instead. After a restart -- memory cleared, restore from disk -- the
    mirror still holds the grant LIVE, but the journal names it, so it restores as explicitly
    revoked and no consult can execute it. Not silently, not at all.
    """
    from core import mode_permission_policy as policy

    ws = _ws(tmp_path)
    _bind_chat_to_workspace("chat-journal", ws)
    revoked_grant = _activate(
        session_id="chat-journal", scope="session", explicit_confirmation=True,
        until_off=True, workspace_root=ws,
    )
    bystander = _activate(
        session_id="chat-journal", scope="session", explicit_confirmation=True,
        until_off=True, workspace_root=ws,
    )
    real_replace = os.replace

    def _failing_replace(src, dst, *args, **kwargs):
        if str(dst).endswith("bypass_grants.json"):
            raise OSError("directory unwritable (fixture)")
        return real_replace(src, dst, *args, **kwargs)

    # Scoped so the fixture's store-path redirection stays in force for the whole test.
    with pytest.MonkeyPatch.context() as scoped:
        scoped.setattr(policy.os, "replace", _failing_replace)
        # No BypassStoreError: the journal made the revocation durable even though the mirror could
        # not be rewritten, so there is no non-durable state to name.
        assert revoke_bypass_grant(revoked_grant["token"]) is True
    assert policy._bypass_revocations_path().exists(), "the revocation was journalled"
    assert _BYPASS_GRANTS[revoked_grant["token"]]["revoked"] is True
    assert validate_bypass_grant(revoked_grant["token"], session_id="chat-journal", workspace_root=ws) is None

    # RESTART: memory cleared, everything re-read from disk exactly as a new process does.
    _BYPASS_GRANTS.clear()
    policy._BYPASS_GRANTS_RESTORED = False
    loaded = policy.restore_persisted_bypass_grants()
    # Only the bystander restores live; the journalled revocation suppresses its grant even
    # though the mirror still holds it live.
    assert loaded == 1
    assert _BYPASS_GRANTS[revoked_grant["token"]]["revoked"] is True
    assert validate_bypass_grant(revoked_grant["token"], session_id="chat-journal", workspace_root=ws) is None
    assert validate_bypass_grant(bystander["token"], session_id="chat-journal", workspace_root=ws) is not None

    # Tamper control, under the HISTORY-TRUST law (updated with the trust repair, with
    # attribution): a forged complete journal line no longer MACs, and an unverifiable line is no
    # longer read as "no revocation" -- that was exactly the resurrection path tampering wanted.
    # The store now fails CLOSED on the whole journal: nothing restores, the bystander included,
    # and the bytes are quarantined. A forged line can still never make authority executable.
    journal = policy._bypass_revocations_path()
    lines = journal.read_text(encoding="utf-8").splitlines()
    assert lines, "the journalled revocation was written"
    import json as _json

    forged = dict(_json.loads(lines[-1]))
    forged["token"] = bystander["token"]
    journal.write_text("\n".join([lines[-1], _json.dumps(forged)]) + "\n", encoding="utf-8")
    _BYPASS_GRANTS.clear()
    policy._BYPASS_GRANTS_RESTORED = False
    assert policy.restore_persisted_bypass_grants() == 0
    assert validate_bypass_grant(bystander["token"], session_id="chat-journal", workspace_root=ws) is None
    assert list(journal.parent.glob("bypass_grants.revocations.log.corrupt-*")), "the poison bytes were quarantined"


def test_a_session_sweep_the_mirror_could_not_record_does_not_survive_a_restart(tmp_path, monkeypatch):
    """The chat-delete sweep rides the same law: every until-off grant it revoked while the
    mirror was unwritable is journalled, so a restart restores none of them as executable."""
    from core import mode_permission_policy as policy

    ws = _ws(tmp_path)
    _bind_chat_to_workspace("chat-sweep", ws)
    first = _activate(
        session_id="chat-sweep", scope="session", explicit_confirmation=True,
        until_off=True, workspace_root=ws,
    )
    second = _activate(
        session_id="chat-sweep", scope="session", explicit_confirmation=True,
        until_off=True, workspace_root=ws,
    )
    real_replace = os.replace

    def _failing_replace(src, dst, *args, **kwargs):
        if str(dst).endswith("bypass_grants.json"):
            raise OSError("directory unwritable (fixture)")
        return real_replace(src, dst, *args, **kwargs)

    # Scoped: the store-path redirection stays in force, and the sweep runs entirely inside it.
    with pytest.MonkeyPatch.context() as scoped:
        scoped.setattr(policy.os, "replace", _failing_replace)
        assert revoke_session_bypass_grants("chat-sweep") == 2
    _BYPASS_GRANTS.clear()
    policy._BYPASS_GRANTS_RESTORED = False
    assert policy.restore_persisted_bypass_grants() == 0
    assert _BYPASS_GRANTS[first["token"]]["revoked"] is True
    assert _BYPASS_GRANTS[second["token"]]["revoked"] is True
    assert validate_bypass_grant(first["token"], session_id="chat-sweep", workspace_root=ws) is None
    assert validate_bypass_grant(second["token"], session_id="chat-sweep", workspace_root=ws) is None


def test_interrupted_persistence_leaves_the_previous_complete_file(tmp_path):
    """A crash between the temp write and the replace: the old file stands, the temp is ignored."""
    from core import mode_permission_policy as policy

    ws = _ws(tmp_path)
    _bind_chat_to_workspace("chat-crash", ws)
    grant = _activate(
        session_id="chat-crash", scope="session", explicit_confirmation=True,
        until_off=True, workspace_root=ws,
    )
    path = policy._bypass_grants_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text('{"schema": 1, "grants": {"forged": {"until_off": true}}')  # torn write
    _BYPASS_GRANTS.clear()
    policy._BYPASS_GRANTS_RESTORED = False
    loaded = policy.restore_persisted_bypass_grants()
    assert loaded == 1  # the complete previous file, not the torn temp
    assert validate_bypass_grant(grant["token"], session_id="chat-crash", workspace_root=ws) is not None


def test_corrupt_or_tampered_mirror_restores_nothing_and_is_quarantined(tmp_path):
    import json as _json

    from core import mode_permission_policy as policy

    ws = _ws(tmp_path)
    _bind_chat_to_workspace("chat-corrupt", ws)
    _activate(
        session_id="chat-corrupt", scope="session", explicit_confirmation=True,
        until_off=True, workspace_root=ws,
    )
    path = policy._bypass_grants_path()
    for payload in (
        "{not json",
        _json.dumps({"schema": 1, "grants": {"x": {"until_off": True}}, "mac": "00" * 32}),
        _json.dumps({"schema": 99, "grants": {}, "mac": ""}),
    ):
        path.write_text(payload, encoding="utf-8")
        _BYPASS_GRANTS.clear()
        policy._BYPASS_GRANTS_RESTORED = False
        assert policy.restore_persisted_bypass_grants() == 0, payload
    assert list(path.parent.glob("bypass_grants.json.corrupt-*")), "the poison file was left in place"
    # The live file is gone (quarantined), so the store self-heals on the next activation.
    assert not path.exists()


def test_a_json_object_saying_until_off_is_not_evidence_of_authority(tmp_path):
    """Forged entries inside a MAC-valid envelope are still refused by the shape law."""
    import hashlib
    import hmac as _hmac
    import json as _json

    from core import mode_permission_policy as policy

    ws = _ws(tmp_path)
    path = policy._bypass_grants_path()
    key = policy._bypass_store_key()
    assert key is not None
    forged = {
        "any-token": {"until_off": True},  # the exact bare object the law refuses
        "t2": {"until_off": True, "scope": "session", "session_id": "s", "token": "t2",
               "workspace_root": "/definitely/not/absolute", "issued_at": 1.0, "revoked": False},
        "t3": {"until_off": True, "scope": "project", "session_id": "s", "token": "t3",
               "workspace_root": ws, "issued_at": 1.0, "revoked": False},
    }
    canonical = _json.dumps(forged, sort_keys=True, separators=(",", ":")).encode("utf-8")
    envelope = {
        "schema": 1,
        "grants": forged,
        "mac": _hmac.new(key, canonical, hashlib.sha256).hexdigest(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps(envelope), encoding="utf-8")
    _BYPASS_GRANTS.clear()
    policy._BYPASS_GRANTS_RESTORED = False
    assert policy.restore_persisted_bypass_grants() == 0
    assert _BYPASS_GRANTS == {}


def test_restore_drops_grants_for_deleted_chats_and_changed_workspaces(tmp_path):
    from core import mode_permission_policy as policy
    from core.context_namespace import set_chat_namespace_project, set_chat_namespace_state

    ws = _ws(tmp_path)
    _bind_chat_to_workspace("chat-gone", ws)
    other = tmp_path / "elsewhere"
    other.mkdir(exist_ok=True)
    from core import project_store as _ps

    ok, other_project = _ps.create_project("elsewhere", str(other))
    assert ok
    _bind_chat_to_workspace("chat-moved", ws)
    gone = _activate(
        session_id="chat-gone", scope="session", explicit_confirmation=True,
        until_off=True, workspace_root=ws,
    )
    moved = _activate(
        session_id="chat-moved", scope="session", explicit_confirmation=True,
        until_off=True, workspace_root=ws,
    )
    set_chat_namespace_state("chat-gone", "deleted")
    set_chat_namespace_project("chat-moved", str(other_project["id"]))
    _BYPASS_GRANTS.clear()
    policy._BYPASS_GRANTS_RESTORED = False
    assert policy.restore_persisted_bypass_grants() == 0
    assert validate_bypass_grant(gone["token"], session_id="chat-gone", workspace_root=ws) is None
    assert validate_bypass_grant(moved["token"], session_id="chat-moved", workspace_root=ws) is None


def test_the_authority_store_never_lives_inside_a_grantable_workspace(tmp_path):
    import pytest

    from core.runtime_paths import active_config_home_dir, active_data_dir

    # The home directory itself, the filesystem root, and any root containing the profile's own
    # data/config dirs are refused for standing grants: the grant would cover its own authority.
    for bad_root in (str(Path.home()), "/"):
        with pytest.raises(ValueError):
            _activate(
                session_id="chat-c", scope="session", explicit_confirmation=True,
                until_off=True, workspace_root=bad_root,
            )
        with pytest.raises(ValueError):
            grant_chat_workspace_authority(session_id="chat-c", workspace_root=bad_root)
    with pytest.raises(ValueError):
        grant_chat_workspace_authority(session_id="chat-c", workspace_root=str(active_data_dir().parent))
    assert (active_data_dir() / "bypass_grants.json").parent != active_config_home_dir()


def _decide(context, intent, args=None):
    from core.mode_permission_policy import _decide_tool_call

    decision, _meta = _decide_tool_call(
        intent=intent,
        arguments=args if args is not None else {"path": "notes.txt"},
        task_id="task-1",
        source_context=context,
        consume=False,
    )
    return decision


def test_chat_workspace_approval_covers_reads_and_edits_inside_the_workspace(tmp_path):
    ws = _ws(tmp_path)
    grant_chat_workspace_authority(session_id="chat-1", workspace_root=ws, duration_seconds=3600)
    context = {
        "runtime_session_id": "chat-1",
        "operating_mode": "manual",
        "workspace": str(tmp_path / "trusted" / "sub"),
    }
    read = _decide(context, "workspace.read_file", {"path": "notes.txt"})
    assert read.effect is PermissionEffect.ALLOW, read.reason
    write = _decide(context, "workspace.write_file", {"path": "notes.txt", "content": "hi"})
    assert write.effect is PermissionEffect.ALLOW, write.reason


def test_chat_workspace_approval_never_covers_deletes_commands_network_or_secrets(tmp_path):
    ws = _ws(tmp_path)
    grant_chat_workspace_authority(session_id="chat-1", workspace_root=ws, duration_seconds=3600)
    context = {"runtime_session_id": "chat-1", "operating_mode": "manual", "workspace": ws}
    for intent, args in (
        ("workspace.delete_file", {"path": "notes.txt"}),
        ("command.run", {"command": "rm -rf notes.txt"}),
        ("secrets.read", {"name": "llm.cloud.openrouter"}),
    ):
        decision = _decide(context, intent, args)
        assert decision.effect is not PermissionEffect.ALLOW, (intent, decision.reason)
    # web.fetch is allowed here by MANUAL MODE's own public read-only retrieval policy, never by
    # the chat grant — the reason names the mode matrix, not the internal authority.
    fetch = _decide(context, "web.fetch", {"url": "https://example.invalid"})
    assert fetch.effect is PermissionEffect.ALLOW
    assert "Manual mode allows" in fetch.reason
    assert "Internal authority" not in fetch.reason


def test_chat_workspace_approval_does_not_leak_to_another_chat_or_workspace(tmp_path):
    ws = _ws(tmp_path)
    grant_chat_workspace_authority(session_id="chat-1", workspace_root=ws, duration_seconds=3600)
    other_ws = tmp_path / "untrusted"
    other_ws.mkdir(exist_ok=True)
    write_args = {"path": "notes.txt", "content": "hi"}
    stranger = {"runtime_session_id": "chat-2", "operating_mode": "manual", "workspace": ws}
    decision = _decide(stranger, "workspace.write_file", write_args)
    assert decision.effect is PermissionEffect.REQUIRE_APPROVAL
    outside = {"runtime_session_id": "chat-1", "operating_mode": "manual", "workspace": str(other_ws)}
    decision = _decide(outside, "workspace.write_file", write_args)
    assert decision.effect is PermissionEffect.REQUIRE_APPROVAL


def test_chat_workspace_approval_expires_and_is_revocable(tmp_path, monkeypatch):
    import time as _time

    ws = _ws(tmp_path)
    original = _time.time
    monkeypatch.setattr("core.mode_permission_policy.time.time", lambda: original())
    grant_chat_workspace_authority(session_id="chat-1", workspace_root=ws, duration_seconds=60)
    assert chat_workspace_authority_state("chat-1").get("label") == "chat workspace read/edit approval"
    # Advance past expiry: the standing scope dies on its own (bounded or not minted).
    monkeypatch.setattr("core.mode_permission_policy.time.time", lambda: original() + 120)
    assert chat_workspace_authority_state("chat-1") == {}
    grant_chat_workspace_authority(session_id="chat-1", workspace_root=ws, duration_seconds=3600)
    assert revoke_chat_workspace_authority("chat-1") is True
    assert revoke_chat_workspace_authority("chat-1") is False
    assert "chat-1" not in _CHAT_WORKSPACE_AUTHORITY


def teardown_function(_fn):
    _BYPASS_GRANTS.clear()
    _INTERNAL_AUTHORITY.clear()
    _CHAT_WORKSPACE_AUTHORITY.clear()
    from core import mode_permission_policy as policy

    policy._BYPASS_GRANTS_RESTORED = False


# --- unverifiable revocation history: no executable authority, legacy stores excepted ---------


@pytest.mark.real_restart_store
@pytest.mark.parametrize("damage", ["corrupt-mac", "unreadable", "missing", "emptied", "stripped-stamp"])
def test_unverifiable_revocation_history_restores_nothing_after_a_real_restart(tmp_path, monkeypatch, damage):
    """Mirror-write failure -> journalled revocation -> the journal is damaged or gone -> a REAL
    second process restores NO executable authority from that store.

    The history-trust law: a stamped store's revocation past is REQUIRED history. A journal whose
    complete lines do not all verify, a journal that cannot be read, or a stamped mirror with no
    journal at all is UNVERIFIABLE, and "which grants were revoked?" has no answer -- least of all
    the empty set, which is exactly what tampering wants to hear. The whole store fails closed
    (both files quarantined), the journalled grant and the bystander alike: the owner re-approves
    after fixing storage; nothing stale executes.
    """
    import json
    import subprocess
    import sys

    from core import mode_permission_policy as policy
    from core import runtime_paths

    home = tmp_path / "home"
    monkeypatch.setenv("VOOL_HOME", str(home))
    runtime_paths.configure_runtime_home(home)
    policy.reset_mode_permission_state()
    policy._BYPASS_GRANTS_RESTORED = True

    ws = _ws(tmp_path)
    _bind_chat_to_workspace("chat-trust", ws)
    affected = _activate(
        session_id="chat-trust", scope="session", explicit_confirmation=True,
        until_off=True, workspace_root=ws,
    )
    bystander = _activate(
        session_id="chat-trust", scope="session", explicit_confirmation=True,
        until_off=True, workspace_root=ws,
    )
    real_replace = os.replace

    def _failing_replace(src, dst, *args, **kwargs):
        if str(dst).endswith("bypass_grants.json"):
            raise OSError("directory unwritable (fixture)")
        return real_replace(src, dst, *args, **kwargs)

    with pytest.MonkeyPatch.context() as scoped:
        scoped.setattr(policy.os, "replace", _failing_replace)
        assert revoke_bypass_grant(affected["token"]) is True  # journalled: no BypassStoreError

    journal = policy._bypass_revocations_path()
    assert journal.exists() and journal.read_text(encoding="utf-8").strip()
    if damage == "corrupt-mac":
        lines = journal.read_text(encoding="utf-8").splitlines()
        lines[0] = lines[0][:-4] + "beef"  # a COMPLETE record that no longer MACs
        journal.write_text("\n".join(lines) + "\n", encoding="utf-8")
    elif damage == "unreadable":
        journal.chmod(0o000)
    elif damage == "emptied":
        journal.write_text("", encoding="utf-8")  # every acknowledged record deleted
    elif damage == "stripped-stamp":
        import json as _json

        mirror = policy._bypass_grants_path()
        envelope = _json.loads(mirror.read_text(encoding="utf-8"))
        envelope.pop("revocation_journal")  # outside the old MAC: the downgrade this law closes
        mirror.write_text(_json.dumps(envelope, indent=2, sort_keys=True), encoding="utf-8")
    else:
        journal.unlink()

    from storage.db import active_default_db_path

    script = (
        "import json,sys\n"
        "from storage.db import configure_default_db_path\n"
        "configure_default_db_path(sys.argv[3])\n"
        "from core.mode_permission_policy import restore_persisted_bypass_grants, validate_bypass_grant\n"
        "loaded = restore_persisted_bypass_grants()\n"
        "a = validate_bypass_grant(sys.argv[1], session_id='chat-trust', workspace_root=sys.argv[2])\n"
        "b = validate_bypass_grant(sys.argv[4], session_id='chat-trust', workspace_root=sys.argv[2])\n"
        "print(json.dumps({'loaded': loaded, 'affected': a is not None, 'bystander': b is not None}))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, affected["token"], ws, active_default_db_path(), bystander["token"]],
        capture_output=True, text=True, cwd=os.getcwd(),
        env={**os.environ, "VOOL_HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"}, timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {"loaded": 0, "affected": False, "bystander": False}, payload
    # The poison bytes are quarantined, so the NEXT restart sees no store instead of re-reading
    # the same unverifiable history.
    data_dir = policy._bypass_grants_path().parent
    assert not (data_dir / "bypass_grants.json").exists(), "the mirror was quarantined, not re-read"


def test_a_legacy_store_without_the_journal_stamp_still_restores(tmp_path):
    """The upgrade path: an envelope written before the journal existed carries no stamp and no
    journal, and no journalled revocation can exist for it -- it restores exactly as it always
    did. Failing closed here would delete every owner's grant at the upgrade boundary."""
    import hashlib as _hashlib
    import hmac as _hmac
    import json as _json

    from core import mode_permission_policy as policy

    store = tmp_path / "authority" / "bypass_grants.json"
    monkeypatch_store = pytest.MonkeyPatch()
    monkeypatch_store.setattr(policy, "_bypass_grants_path", lambda: store)
    try:
        ws = _ws(tmp_path)
        _bind_chat_to_workspace("chat-legacy", ws)
        grant = _activate(
            session_id="chat-legacy", scope="session", explicit_confirmation=True,
            until_off=True, workspace_root=ws,
        )
        key = policy._bypass_store_key()
        legacy_grants = {grant["token"]: dict(_BYPASS_GRANTS[grant["token"]])}
        envelope = {
            "schema": policy._BYPASS_STORE_SCHEMA,
            "grants": legacy_grants,
            "mac": _hmac.new(key, policy._grant_canonical_bytes(legacy_grants), _hashlib.sha256).hexdigest(),
        }
        policy._bypass_revocations_path().unlink()  # no journal existed for this store
        store.write_text(_json.dumps(envelope, indent=2, sort_keys=True), encoding="utf-8")
        _BYPASS_GRANTS.clear()
        policy._BYPASS_GRANTS_RESTORED = False
        assert policy.restore_persisted_bypass_grants() == 1
        assert validate_bypass_grant(grant["token"], session_id="chat-legacy", workspace_root=ws) is not None
    finally:
        monkeypatch_store.undo()


def test_a_torn_journal_tail_fails_the_store_closed(tmp_path):
    """An unparsable trailing segment fails the WHOLE store closed (law changed, with attribution).

    The earlier version tolerated an unterminated tail as "an append interrupted before its
    fsync -- never durable". That inferred "never durably written" from the segment's SHAPE
    alone: a tail truncated to hide the last journalled revocation, or a complete record with
    only its newline stripped, read as exactly such a tail. Torn-by-crash and cut-by-tampering
    are indistinguishable on the bytes, so the store now refuses to guess: history that does not
    verify end to end restores nothing, and the owner re-approves. A crash mid-append can
    therefore require re-approval -- availability traded away in the safe direction."""
    from core import mode_permission_policy as policy

    ws = _ws(tmp_path)
    _bind_chat_to_workspace("chat-torn", ws)
    affected = _activate(
        session_id="chat-torn", scope="session", explicit_confirmation=True,
        until_off=True, workspace_root=ws,
    )
    bystander = _activate(
        session_id="chat-torn", scope="session", explicit_confirmation=True,
        until_off=True, workspace_root=ws,
    )
    real_replace = os.replace

    def _failing_replace(src, dst, *args, **kwargs):
        if str(dst).endswith("bypass_grants.json"):
            raise OSError("directory unwritable (fixture)")
        return real_replace(src, dst, *args, **kwargs)

    with pytest.MonkeyPatch.context() as scoped:
        scoped.setattr(policy.os, "replace", _failing_replace)
        assert revoke_bypass_grant(affected["token"]) is True

    journal = policy._bypass_revocations_path()
    journal.write_text(journal.read_text(encoding="utf-8") + '{"schema": 1, "seq": 2, "to', encoding="utf-8")
    _BYPASS_GRANTS.clear()
    policy._BYPASS_GRANTS_RESTORED = False
    assert policy.restore_persisted_bypass_grants() == 0
    assert validate_bypass_grant(affected["token"], session_id="chat-torn", workspace_root=ws) is None
    assert validate_bypass_grant(bystander["token"], session_id="chat-torn", workspace_root=ws) is None


def test_revocation_history_record_shape_laws(tmp_path):
    """The three directly reproduced history-reader laws, driven through the product's own writer.

    1. a valid record with its newline: revoked, trusted;
    2. the IDENTICAL record with the final newline stripped: still revoked, still trusted -- a
       missing newline says nothing about durability, and reading it as "never durably written"
       was the resurrection path;
    3. the journal emptied: UNTRUSTED -- per-record MACs prove each surviving record authentic,
       not that every acknowledged record survives; the anchor says one was acknowledged, so an
       empty journal is lost history, not zero history."""
    from core import mode_permission_policy as policy

    store = tmp_path / "authority" / "bypass_grants.json"
    with pytest.MonkeyPatch.context() as scoped:
        scoped.setattr(policy, "_bypass_grants_path", lambda: store)
        base = {
            "session_id": "chat-shape", "scope": "session", "until_off": True, "expires_at": None,
            "workspace_root": "/tmp", "issued_at": 1.0, "revoked": False, "project_id": "", "task_id": "",
        }
        _BYPASS_GRANTS["tok-shape"] = dict(base, token="tok-shape")
        policy._persist_bypass_grants_locked()
        _BYPASS_GRANTS["tok-shape"]["revoked"] = True
        real_replace = os.replace

        def _failing_replace(src, dst, *args, **kwargs):
            if str(dst).endswith("bypass_grants.json"):
                raise OSError("directory unwritable (fixture)")
            return real_replace(src, dst, *args, **kwargs)

        scoped.setattr(policy.os, "replace", _failing_replace)
        policy._record_bypass_revocation_durably_locked(["tok-shape"])
        scoped.setattr(policy.os, "replace", real_replace)

        journal = policy._bypass_revocations_path()
        raw = journal.read_text(encoding="utf-8")
        assert policy._bypass_revocation_history() == ({"tok-shape"}, True)
        journal.write_text(raw.rstrip("\n"), encoding="utf-8")
        assert policy._bypass_revocation_history() == ({"tok-shape"}, True), "newline loss is not durability evidence"
        journal.write_text("", encoding="utf-8")
        assert policy._bypass_revocation_history() == (set(), False), "an emptied journal is lost acknowledged history"


def test_a_record_whose_anchor_never_landed_still_revs(tmp_path):
    """The append's two writes are not atomic: a crash after the record's fsync but before the
    anchor's leaves one authentic-but-unacknowledged record. Restore counts it as a revocation
    anyway -- the fail-safe direction: a record that never landed cannot resurrect anything, and
    one that did must not. This pins that boundary instead of claiming atomicity."""
    from core import mode_permission_policy as policy

    ws = _ws(tmp_path)
    _bind_chat_to_workspace("chat-anchor", ws)
    affected = _activate(
        session_id="chat-anchor", scope="session", explicit_confirmation=True,
        until_off=True, workspace_root=ws,
    )
    bystander = _activate(
        session_id="chat-anchor", scope="session", explicit_confirmation=True,
        until_off=True, workspace_root=ws,
    )
    anchor_before = policy._bypass_revocations_head_path().read_text(encoding="utf-8")
    real_replace = os.replace

    def _failing_replace(src, dst, *args, **kwargs):
        if str(dst).endswith("bypass_grants.json"):
            raise OSError("directory unwritable (fixture)")
        return real_replace(src, dst, *args, **kwargs)

    with pytest.MonkeyPatch.context() as scoped:
        scoped.setattr(policy.os, "replace", _failing_replace)
        assert revoke_bypass_grant(affected["token"]) is True
    # The anchor write "never happened": it still holds the pre-append state.
    policy._bypass_revocations_head_path().write_text(anchor_before, encoding="utf-8")
    _BYPASS_GRANTS.clear()
    policy._BYPASS_GRANTS_RESTORED = False
    assert policy.restore_persisted_bypass_grants() == 1
    assert _BYPASS_GRANTS[affected["token"]]["revoked"] is True
    assert validate_bypass_grant(affected["token"], session_id="chat-anchor", workspace_root=ws) is None
    assert validate_bypass_grant(bystander["token"], session_id="chat-anchor", workspace_root=ws) is not None


@pytest.mark.real_restart_store
def test_a_revoked_grant_without_its_final_newline_cannot_execute_after_a_restart(tmp_path, monkeypatch):
    """Fresh-process proof for the newline law: the acknowledged journalled revocation with its
    trailing newline REMOVED still binds -- the affected grant cannot execute in a new process,
    while the bystander (whose revocation never happened) restores live."""
    import json
    import subprocess
    import sys

    from core import mode_permission_policy as policy
    from core import runtime_paths

    home = tmp_path / "home"
    monkeypatch.setenv("VOOL_HOME", str(home))
    runtime_paths.configure_runtime_home(home)
    policy.reset_mode_permission_state()
    policy._BYPASS_GRANTS_RESTORED = True

    ws = _ws(tmp_path)
    _bind_chat_to_workspace("chat-nl", ws)
    affected = _activate(
        session_id="chat-nl", scope="session", explicit_confirmation=True,
        until_off=True, workspace_root=ws,
    )
    bystander = _activate(
        session_id="chat-nl", scope="session", explicit_confirmation=True,
        until_off=True, workspace_root=ws,
    )
    real_replace = os.replace

    def _failing_replace(src, dst, *args, **kwargs):
        if str(dst).endswith("bypass_grants.json"):
            raise OSError("directory unwritable (fixture)")
        return real_replace(src, dst, *args, **kwargs)

    with pytest.MonkeyPatch.context() as scoped:
        scoped.setattr(policy.os, "replace", _failing_replace)
        assert revoke_bypass_grant(affected["token"]) is True
    journal = policy._bypass_revocations_path()
    journal.write_text(journal.read_text(encoding="utf-8").rstrip("\n"), encoding="utf-8")

    from storage.db import active_default_db_path

    script = (
        "import json,sys\n"
        "from storage.db import configure_default_db_path\n"
        "configure_default_db_path(sys.argv[3])\n"
        "from core.mode_permission_policy import restore_persisted_bypass_grants, validate_bypass_grant\n"
        "loaded = restore_persisted_bypass_grants()\n"
        "a = validate_bypass_grant(sys.argv[1], session_id='chat-nl', workspace_root=sys.argv[2])\n"
        "b = validate_bypass_grant(sys.argv[4], session_id='chat-nl', workspace_root=sys.argv[2])\n"
        "print(json.dumps({'loaded': loaded, 'affected': a is not None, 'bystander': b is not None}))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, affected["token"], ws, active_default_db_path(), bystander["token"]],
        capture_output=True, text=True, cwd=os.getcwd(),
        env={**os.environ, "VOOL_HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"}, timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {"loaded": 1, "affected": False, "bystander": True}, payload


# --- the WRITE paths never legitimize truncated history ----------------------------------------


def test_truncated_history_is_never_legitimized_by_a_new_append_or_persist(tmp_path):
    """The reproduced write-path sequence: append A, empty the journal, then attempt to append B
    or to persist the store. Both mutation paths must REFUSE against the still-acknowledging
    anchor -- signing a replacement head over surviving records is how acknowledged revocations
    disappear while history reads trusted again. The anchor stays exactly as it was, the journal
    stays empty, the history stays UNTRUSTED, and a fresh until-off activation is refused and
    rolled back rather than persisted atop the damage."""
    import pytest

    from core import mode_permission_policy as policy
    from core.mode_permission_policy import BypassStoreError

    ws = _ws(tmp_path)
    _bind_chat_to_workspace("chat-trunc", ws)
    store = tmp_path / "authority" / "bypass_grants.json"
    with pytest.MonkeyPatch.context() as scoped:
        scoped.setattr(policy, "_bypass_grants_path", lambda: store)
        base = {
            "session_id": "chat-trunc", "scope": "session", "until_off": True, "expires_at": None,
            "workspace_root": "/tmp", "issued_at": 1.0, "revoked": False, "project_id": "", "task_id": "",
        }
        _BYPASS_GRANTS["tok-A"] = dict(base, token="tok-A")
        policy._persist_bypass_grants_locked()
        real_replace = os.replace

        def _failing_replace(src, dst, *args, **kwargs):
            if str(dst).endswith("bypass_grants.json"):
                raise OSError("directory unwritable (fixture)")
            return real_replace(src, dst, *args, **kwargs)

        scoped.setattr(policy.os, "replace", _failing_replace)
        _BYPASS_GRANTS["tok-A"]["revoked"] = True
        policy._record_bypass_revocation_durably_locked(["tok-A"])
        assert policy._bypass_revocation_history() == ({"tok-A"}, True)

        journal = policy._bypass_revocations_path()
        anchor_bytes = policy._bypass_revocations_head_path().read_text(encoding="utf-8")
        journal.write_text("", encoding="utf-8")
        assert policy._bypass_revocation_history() == (set(), False)

        with pytest.raises(BypassStoreError, match="refusing to extend"):
            policy._append_bypass_revocation_journal_locked("tok-B")
        with pytest.raises(BypassStoreError, match="cannot be persisted"):
            policy._persist_bypass_grants_locked()
        assert journal.read_text(encoding="utf-8") == "", "the damaged journal was not extended"
        assert policy._bypass_revocations_head_path().read_text(encoding="utf-8") == anchor_bytes, (
            "the acknowledged anchor was never rewritten"
        )
        assert policy._bypass_revocation_history() == (set(), False)

        # A fresh until-off activation rides the persist path: refused and rolled back, never
        # minted on top of unverifiable history.
        _BYPASS_GRANTS["tok-fresh"] = dict(base, token="tok-fresh")
        with pytest.raises(BypassStoreError):
            policy._persist_bypass_grants_locked()
        # The activation-level refusal is the product surface for the same law:
        try:
            _activate(
                session_id="chat-trunc", scope="session", explicit_confirmation=True,
                until_off=True, workspace_root=ws,
            )
        except ValueError as exc:
            assert "could not be recorded durably" in str(exc)
        else:  # pragma: no cover - the activation must not succeed on damaged history
            pytest.fail("an until-off grant was activated atop unverifiable revocation history")


def test_genuine_first_initialization_appends_without_an_anchor(tmp_path):
    """The explicit initialization path: no journal, no anchor, no acknowledged head -- nothing
    exists to truncate, so the first append is legitimate and starts the chain at one."""
    from core import mode_permission_policy as policy

    store = tmp_path / "authority" / "bypass_grants.json"
    with pytest.MonkeyPatch.context() as scoped:
        scoped.setattr(policy, "_bypass_grants_path", lambda: store)
        policy._append_bypass_revocation_journal_locked("tok-first")
        assert policy._bypass_revocation_history() == ({"tok-first"}, True)
        records, trusted = policy._bypass_journal_chain()
        assert trusted and [r["seq"] for r in records] == [1]


@pytest.mark.real_restart_store
def test_truncated_history_with_a_live_mirror_grant_never_executes_after_a_restart(tmp_path, monkeypatch):
    """The full reproduced sequence against a REAL second process, with the mirror still holding
    the revoked grant live (its rewrite failed when A was journalled). After the journal is
    emptied: the next revocation is refused and named non-durable, and the restart restores NO
    executable authority from the store -- A cannot execute from the live mirror, the store is
    quarantined, and the operator re-approves after fixing storage."""
    import json
    import subprocess
    import sys

    import pytest

    from core import mode_permission_policy as policy
    from core import runtime_paths
    from core.mode_permission_policy import BypassStoreError

    home = tmp_path / "home"
    monkeypatch.setenv("VOOL_HOME", str(home))
    runtime_paths.configure_runtime_home(home)
    policy.reset_mode_permission_state()
    policy._BYPASS_GRANTS_RESTORED = True

    ws = _ws(tmp_path)
    _bind_chat_to_workspace("chat-trunc-restart", ws)
    affected = _activate(
        session_id="chat-trunc-restart", scope="session", explicit_confirmation=True,
        until_off=True, workspace_root=ws,
    )
    bystander = _activate(
        session_id="chat-trunc-restart", scope="session", explicit_confirmation=True,
        until_off=True, workspace_root=ws,
    )
    real_replace = os.replace

    def _failing_replace(src, dst, *args, **kwargs):
        if str(dst).endswith("bypass_grants.json"):
            raise OSError("directory unwritable (fixture)")
        return real_replace(src, dst, *args, **kwargs)

    with pytest.MonkeyPatch.context() as scoped:
        scoped.setattr(policy.os, "replace", _failing_replace)
        assert revoke_bypass_grant(affected["token"]) is True  # journalled: the mirror keeps A live
    policy._bypass_revocations_path().write_text("", encoding="utf-8")
    # The bystander's revocation now rides the persist path first, and persist refuses on the
    # unverifiable history -- named non-durable, never recorded, the mirror untouched.
    with pytest.raises(BypassStoreError):
        revoke_bypass_grant(bystander["token"])
    assert _BYPASS_GRANTS[bystander["token"]]["revoked"] is True  # in memory it still stands

    from storage.db import active_default_db_path

    script = (
        "import json,sys\n"
        "from storage.db import configure_default_db_path\n"
        "configure_default_db_path(sys.argv[3])\n"
        "from core.mode_permission_policy import restore_persisted_bypass_grants, validate_bypass_grant\n"
        "loaded = restore_persisted_bypass_grants()\n"
        "a = validate_bypass_grant(sys.argv[1], session_id='chat-trunc-restart', workspace_root=sys.argv[2])\n"
        "b = validate_bypass_grant(sys.argv[4], session_id='chat-trunc-restart', workspace_root=sys.argv[2])\n"
        "print(json.dumps({'loaded': loaded, 'affected': a is not None, 'bystander': b is not None}))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, affected["token"], ws, active_default_db_path(), bystander["token"]],
        capture_output=True, text=True, cwd=os.getcwd(),
        env={**os.environ, "VOOL_HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"}, timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {"loaded": 0, "affected": False, "bystander": False}, payload
    assert not policy._bypass_grants_path().exists(), "the poisoned store was quarantined"


# --- initialization eligibility is bound to the authenticated store lifecycle -------------------


def test_lost_required_history_on_an_established_store_is_not_initialization(tmp_path):
    """The reproduced hole: persist a grant, revoke it through the journal (the mirror keeps it
    live), then empty the journal and delete the anchor. An empty journal plus a missing anchor
    is LOST REQUIRED HISTORY on a store whose stamped mirror authenticates -- never a first
    chain. History reads untrusted, both mutation paths refuse to legitimize the state, the
    anchor is never rewritten, and a fresh until-off activation is refused and rolled back."""
    import pytest

    from core import mode_permission_policy as policy
    from core.mode_permission_policy import BypassStoreError

    ws = _ws(tmp_path)
    _bind_chat_to_workspace("chat-lost", ws)
    store = tmp_path / "authority" / "bypass_grants.json"
    with pytest.MonkeyPatch.context() as scoped:
        scoped.setattr(policy, "_bypass_grants_path", lambda: store)
        assert policy._bypass_store_lifecycle() == "fresh"
        base = {
            "session_id": "chat-lost", "scope": "session", "until_off": True, "expires_at": None,
            "workspace_root": "/tmp", "issued_at": 1.0, "revoked": False, "project_id": "", "task_id": "",
        }
        _BYPASS_GRANTS["tok-A"] = dict(base, token="tok-A")
        policy._persist_bypass_grants_locked()
        assert policy._bypass_store_lifecycle() == "established"
        real_replace = os.replace

        def _failing_replace(src, dst, *args, **kwargs):
            if str(dst).endswith("bypass_grants.json"):
                raise OSError("directory unwritable (fixture)")
            return real_replace(src, dst, *args, **kwargs)

        scoped.setattr(policy.os, "replace", _failing_replace)
        _BYPASS_GRANTS["tok-A"]["revoked"] = True
        policy._record_bypass_revocation_durably_locked(["tok-A"])  # journalled; mirror keeps A live
        assert policy._bypass_revocation_history() == ({"tok-A"}, True)

        policy._bypass_revocations_path().write_text("", encoding="utf-8")
        policy._bypass_revocations_head_path().unlink()
        assert policy._bypass_revocation_history() == (set(), False)
        assert policy._bypass_store_lifecycle() == "established", "the mirror still authenticates"

        with pytest.raises(BypassStoreError, match="requires journal history"):
            policy._append_bypass_revocation_journal_locked("tok-B")
        with pytest.raises(BypassStoreError, match="requires journal history"):
            policy._persist_bypass_grants_locked()
        with pytest.raises(ValueError, match="could not be recorded durably"):
            _activate(
                session_id="chat-lost", scope="session", explicit_confirmation=True,
                until_off=True, workspace_root=ws,
            )
        assert not policy._bypass_revocations_head_path().exists(), "no anchor was signed over the loss"


def test_fresh_and_legacy_stores_still_initialize_and_migrate(tmp_path):
    """The two legitimate shapes the binding must preserve: a FRESH store (no mirror) may start
    a chain from nothing, and a LEGACY store (unstamped envelope, legacy MAC) may treat an
    absent journal as 'no journalled revocations exist' and append its first record."""
    import hashlib as _hashlib
    import hmac as _hmac
    import json as _json

    from core import mode_permission_policy as policy

    fresh = tmp_path / "fresh" / "bypass_grants.json"
    with pytest.MonkeyPatch.context() as scoped:
        scoped.setattr(policy, "_bypass_grants_path", lambda: fresh)
        assert policy._bypass_store_lifecycle() == "fresh"
        policy._append_bypass_revocation_journal_locked("tok-fresh")
        assert policy._bypass_revocation_history() == ({"tok-fresh"}, True)

    legacy = tmp_path / "legacy" / "bypass_grants.json"
    with pytest.MonkeyPatch.context() as scoped:
        scoped.setattr(policy, "_bypass_grants_path", lambda: legacy)
        base = {
            "session_id": "chat-mig", "scope": "session", "until_off": True, "expires_at": None,
            "workspace_root": "/tmp", "issued_at": 1.0, "revoked": False, "project_id": "", "task_id": "",
        }
        grants = {"tok-legacy": dict(base, token="tok-legacy")}
        key = policy._bypass_store_key()
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(
            _json.dumps(
                {
                    "schema": policy._BYPASS_STORE_SCHEMA,
                    "grants": grants,
                    "mac": _hmac.new(key, policy._grant_canonical_bytes(grants), _hashlib.sha256).hexdigest(),
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        assert policy._bypass_store_lifecycle() == "legacy"
        established, records, reason = policy._bypass_revocation_history_state()
        assert established and records == [], reason  # migration: nothing journalled existed
        policy._append_bypass_revocation_journal_locked("tok-migrated")
        assert policy._bypass_revocation_history() == ({"tok-migrated"}, True)


@pytest.mark.real_restart_store
def test_lost_required_history_after_a_journalled_revocation_executes_nothing_after_a_restart(tmp_path, monkeypatch):
    """The full acceptance sequence in a REAL second process: persist, revoke through the
    journal (mirror keeps the grant live), empty the journal, delete the anchor, restart. No
    affected grant may execute; the store is quarantined rather than re-initialization."""
    import json
    import subprocess
    import sys

    import pytest

    from core import mode_permission_policy as policy
    from core import runtime_paths
    from core.mode_permission_policy import BypassStoreError

    home = tmp_path / "home"
    monkeypatch.setenv("VOOL_HOME", str(home))
    runtime_paths.configure_runtime_home(home)
    policy.reset_mode_permission_state()
    policy._BYPASS_GRANTS_RESTORED = True

    ws = _ws(tmp_path)
    _bind_chat_to_workspace("chat-lost-restart", ws)
    affected = _activate(
        session_id="chat-lost-restart", scope="session", explicit_confirmation=True,
        until_off=True, workspace_root=ws,
    )
    bystander = _activate(
        session_id="chat-lost-restart", scope="session", explicit_confirmation=True,
        until_off=True, workspace_root=ws,
    )
    real_replace = os.replace

    def _failing_replace(src, dst, *args, **kwargs):
        if str(dst).endswith("bypass_grants.json"):
            raise OSError("directory unwritable (fixture)")
        return real_replace(src, dst, *args, **kwargs)

    with pytest.MonkeyPatch.context() as scoped:
        scoped.setattr(policy.os, "replace", _failing_replace)
        assert revoke_bypass_grant(affected["token"]) is True  # journalled; the mirror keeps A live
    policy._bypass_revocations_path().write_text("", encoding="utf-8")
    policy._bypass_revocations_head_path().unlink()
    with pytest.raises(BypassStoreError):
        revoke_bypass_grant(bystander["token"])  # neither mutation path legitimizes the loss

    from storage.db import active_default_db_path

    script = (
        "import json,sys\n"
        "from storage.db import configure_default_db_path\n"
        "configure_default_db_path(sys.argv[3])\n"
        "from core.mode_permission_policy import restore_persisted_bypass_grants, validate_bypass_grant\n"
        "loaded = restore_persisted_bypass_grants()\n"
        "a = validate_bypass_grant(sys.argv[1], session_id='chat-lost-restart', workspace_root=sys.argv[2])\n"
        "b = validate_bypass_grant(sys.argv[4], session_id='chat-lost-restart', workspace_root=sys.argv[2])\n"
        "print(json.dumps({'loaded': loaded, 'affected': a is not None, 'bystander': b is not None}))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, affected["token"], ws, active_default_db_path(), bystander["token"]],
        capture_output=True, text=True, cwd=os.getcwd(),
        env={**os.environ, "VOOL_HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"}, timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {"loaded": 0, "affected": False, "bystander": False}, payload
    assert not policy._bypass_grants_path().exists(), "the store was quarantined, not re-initialized"
