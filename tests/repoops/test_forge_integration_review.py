"""Independent mutation-safety regression cases using the delivered real runtime."""
import pytest
from tests.repoops.test_forge_actions import (
    world, _armed_session, _operator_authorizes, _pr_payload, _comment_payload, _calls,
)
from tests.repoops._harness import context, door


def prepare(world, action='create'):
    root, _, forge = world
    ctx = context(root)
    sid, sha, base = _armed_session(world, ctx)
    args = {'repo_session_id': sid, 'action': action, 'title': 'Reviewed title', 'body': 'Reviewed exact text'}
    if action == 'create':
        args['base_ref'] = 'main'
    else:
        args.update(number='17', subject='issue')
    plan = door('repo.pr.request', args, ctx)
    assert plan.ok, plan.response_text
    assert _operator_authorizes(world, sid, plan.details['action_hash'])['ok']
    forge.route('GET /pulls?head=', [])
    forge.route('POST /pulls', _pr_payload('31', base_sha=base, head_sha=sha, draft=True,
                                        title='Reviewed title', body='Reviewed exact text'))
    forge.route('POST /issues/17/comments', _comment_payload(9001, body='Reviewed exact text'))
    return forge, ctx, sid, sha, base, plan.details['action_hash']


@pytest.mark.parametrize('action', ['create', 'comment'])
def test_journal_reservation_failure_prevents_external_write(world, monkeypatch, action):
    forge, ctx, sid, *_ = prepare(world, action)
    import core.runtime_continuity as continuity
    def unavailable(**kwargs):
        raise RuntimeError('Synthetic durable journal unavailable')
    monkeypatch.setattr(continuity, 'reserve_logical_effect', unavailable)
    result = door('repo.pr.' + action, {'repo_session_id': sid}, ctx)
    assert not _calls(forge, method='POST'), (result.status, result.details)
    assert not result.ok


@pytest.mark.parametrize('action', ['create', 'comment'])
def test_verification_failure_remains_failed_on_replay(world, action):
    forge, ctx, sid, sha, base, _ = prepare(world, action)
    if action == 'create':
        forge.route('POST /pulls', _pr_payload('31', base_sha=base, head_sha=sha, draft=False,
                                            title='Reviewed title', body='Reviewed exact text'))
    else:
        forge.route('POST /issues/17/comments', _comment_payload(9001, body='Different text'))
    first = door('repo.pr.' + action, {'repo_session_id': sid}, ctx)
    assert not first.ok and first.status == 'verification_mismatch'
    replay = door('repo.pr.' + action, {'repo_session_id': sid}, ctx)
    assert len(_calls(forge, method='POST')) == 1
    assert not replay.ok, (replay.status, replay.details)
    assert replay.status == 'verification_mismatch'


@pytest.mark.parametrize('mismatch', ['draft', 'body'])
def test_unknown_create_cannot_claim_exact_success_for_different_pr(world, mismatch):
    forge, ctx, sid, sha, base, _ = prepare(world)
    forge.arm_unknown('POST /repos/o/r/pulls')
    first = door('repo.pr.create', {'repo_session_id': sid}, ctx)
    assert first.status == 'unknown_reconciliation_required'
    payload = _pr_payload('31', base_sha=base, head_sha=sha, draft=True,
                          title='Reviewed title', body='Reviewed exact text')
    payload[mismatch] = False if mismatch == 'draft' else 'Other actor text'
    forge.route('GET /pulls?head=', [payload])
    result = door('repo.pr.create', {'repo_session_id': sid}, ctx)
    assert len(_calls(forge, method='POST')) == 1
    assert not result.ok, (result.status, result.details)
    assert not result.details.get('verified', False)


@pytest.mark.parametrize('payload', ['{broken JSON', {}], ids=['malformed-json', 'empty-object'])
def test_success_reply_decode_failure_is_unknown_not_safe_to_retry(world, payload):
    forge, ctx, sid, _, _, action_hash = prepare(world, 'comment')
    forge.route('POST /issues/17/comments', payload, status=201)
    first = door('repo.pr.comment', {'repo_session_id': sid}, ctx)
    assert not first.ok
    # A 201 may represent a landed write; decoding its body cannot prove non-delivery.
    rearm = _operator_authorizes(world, sid, action_hash)
    assert not rearm['ok'], (first.status, rearm)
    retry = door('repo.pr.comment', {'repo_session_id': sid}, ctx)
    assert not retry.ok and retry.status == 'unknown_reconciliation_required'
    assert len(_calls(forge, method='POST')) == 1
