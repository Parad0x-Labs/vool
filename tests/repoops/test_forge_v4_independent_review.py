"""Independent v4 checks: disposable git remotes, real ledger, recorded forge.

No external publication or owner repository/credentials. Each runtime uses the same
production doors; the stale-runtime tests have distinct caches and locks.
"""
import pytest

from tests.repoops._harness import context, door, git, remote_ref
from tests.repoops.test_forge_actions import world, _calls
from tests.repoops.test_forge_integration_review import prepare
from tests.repoops.test_repoops_unknown_effect import _drive_to_authorized_push


@pytest.mark.parametrize('content', ['original reviewed push', 'novel deployment notes'])
@pytest.mark.parametrize('failure', ['reservation-unavailable', 'dispatch-claim-lost'])
def test_push_requires_a_durable_reservation_and_winning_claim(world, monkeypatch, content, failure):
    from core import runtime_continuity as continuity

    root, bare, forge = world
    (root / 'NOTES.md').write_text(content + '\n')
    git(root, 'add', 'NOTES.md')
    git(root, 'commit', '-q', '-m', 'review fixture state')
    ctx = context(root)
    sid, _ = _drive_to_authorized_push(root, forge, ctx)
    before = remote_ref(bare, 'feature')
    assert before == ''
    if failure == 'reservation-unavailable':
        def refuse(**kwargs):
            raise OSError('Synthetic unavailable effect ledger')
        monkeypatch.setattr(continuity, 'reserve_logical_effect', refuse)
    else:
        monkeypatch.setattr(continuity, 'mark_effect_dispatched', lambda **kwargs: False)
    result = door('repo.push', {'repo_session_id': sid, 'simulate': False}, ctx)
    after = remote_ref(bare, 'feature')
    assert after == before, (failure, result.status, 'the real disposable remote moved', before, after)
    assert not result.ok


@pytest.mark.parametrize('action', ['create', 'comment'])
def test_separate_runtime_stale_cache_cannot_repeat_applied_action(world, monkeypatch, action):
    import core.repoops.plane as plane

    forge, ctx, sid, *_ = prepare(world, action)
    original = plane.repo_ops_runtime()
    other = plane.RepoOpsRuntime()
    first_snapshot = other._load(sid)
    assert first_snapshot is not None
    assert first_snapshot is not original._load(sid)
    assert not first_snapshot.forge_authorization['consumed']
    first = door(f'repo.pr.{action}', {'repo_session_id': sid}, ctx)
    assert first.ok, first.response_text
    assert len(_calls(forge, method='POST')) == 1
    # A second daemon was already open and cached the old unconsumed authorization.
    # It now executes through the SAME production tool door with its independent state.
    monkeypatch.setattr(plane, 'repo_ops_runtime', lambda: other)
    second = door(f'repo.pr.{action}', {'repo_session_id': sid}, ctx)
    assert len(_calls(forge, method='POST')) == 1, (first.status, second.status, _calls(forge, method='POST'))
