"""Independent regressions: real local mail except explicit pre-connect/TLS fault probes."""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from core import email_drafts, email_tools
from tests.test_email_live_workflow import _isolated, _raw, _store_accounts, mail_service

SESSION = 'openclaw:independent-email-review'


def prepare(service, *, body='Wednesday 10:00 works.', account='default'):
    service.store.add_user('review@example.test', 'fixture-pw')
    _store_accounts('review@example.test', 'fixture-pw', account='default')
    saved = email_drafts.save_draft(to='recipient@example.test', subject='Delivery slot',
                                    body=body, account=account, session_id=SESSION)
    assert saved.ok
    draft_id = saved.draft['draft_id']
    assert email_drafts.approve_draft(draft_id, session_id=SESSION).ok
    return draft_id


@pytest.mark.parametrize('followup', ['approve-again', 'edit-and-approve'])
def test_unknown_delivery_cannot_be_reset_by_ordinary_draft_actions(mail_service, followup):
    svc = mail_service
    draft_id = prepare(svc)
    svc.drop_after_accept = True
    first = email_drafts.send_draft(draft_id, session_id=SESSION)
    assert first.status == 'delivery_unknown' and len(svc.store.captured) == 1
    svc.drop_after_accept = False
    if followup == 'edit-and-approve':
        email_drafts.save_draft(draft_id=draft_id, to='recipient@example.test',
                               subject='Delivery slot', body='Thursday 09:30 instead.',
                               account='default', session_id=SESSION)
    email_drafts.approve_draft(draft_id, session_id=SESSION)
    result = email_drafts.send_draft(draft_id, session_id=SESSION)
    assert len(svc.store.captured) == 1, (result.status, 'Unknown send was dispatched a second time')
    assert email_drafts.get_draft(draft_id, session_id=SESSION).draft['status'] == 'delivery_unknown'


def test_concurrent_calls_cannot_both_dispatch_same_approved_draft(mail_service, monkeypatch):
    draft_id = prepare(mail_service)
    original = email_tools._smtp_connect
    entered = threading.Event()
    release = threading.Event()
    def pause_first_connect(creds):
        if not entered.is_set():
            entered.set()
            assert release.wait(10), 'Review barrier timed out'
        return original(creds)
    monkeypatch.setattr(email_tools, '_smtp_connect', pause_first_connect)
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(email_drafts.send_draft, draft_id, session_id=SESSION)
        try:
            assert entered.wait(5)
            second = email_drafts.send_draft(draft_id, session_id=SESSION)
        finally:
            release.set()
        first.result(timeout=10)
    assert len(mail_service.store.captured) == 1, (second.status, len(mail_service.store.captured))


def test_receipt_persistence_failure_cannot_make_landed_send_retryable(mail_service, monkeypatch):
    draft_id = prepare(mail_service, body='Please confirm the estimate excludes tax.')
    original = email_drafts._save
    def fail_terminal_receipt(state):
        if state['drafts'][draft_id]['status'] == 'sent':
            raise OSError('Synthetic disk failure after SMTP acceptance')
        return original(state)
    monkeypatch.setattr(email_drafts, '_save', fail_terminal_receipt)
    try:
        email_drafts.send_draft(draft_id, session_id=SESSION)
    except OSError:
        pass
    assert len(mail_service.store.captured) == 1
    monkeypatch.setattr(email_drafts, '_save', original)
    # Reads the actual persisted 'sending' state, as the next request/restart would.
    result = email_drafts.send_draft(draft_id, session_id=SESSION)
    assert len(mail_service.store.captured) == 1, (result.status, 'Lost receipt caused a second SMTP submission')


@pytest.mark.parametrize('filter_name', ['sender', 'subject'])
def test_search_does_not_silently_miss_matching_mail_behind_unrelated_messages(mail_service, filter_name):
    address = 'review@example.test'
    target = _raw('supplier@example.test', address, 'Annual estimate', 'Target message',
                  '<wanted@example.test>', 'Fri, 11 Sep 2026 08:00:00 +0000')
    noise = [_raw('newsletter@example.test', address, 'News digest', 'Unrelated',
                  f'<noise-{i}@example.test>', 'Sat, 12 Sep 2026 08:00:00 +0000') for i in range(9)]
    mail_service.store.add_user(address, 'fixture-pw', {'INBOX': [target, *noise]})
    _store_accounts(address, 'fixture-pw')
    filters = {filter_name: 'supplier@example.test' if filter_name == 'sender' else 'Annual estimate'}
    result = email_tools.search_email(account='default', limit=2, **filters)
    assert result.ok, result.message
    assert any(m['message_id'] == '<wanted@example.test>' for m in result.messages), result.message
    assert not any(mail_service.store.seen_flags(address))


@pytest.mark.parametrize('mode,host', [('starttIs', 'smtp.provider.example'), ('plain', 'localhost.attacker.example')])
@pytest.mark.parametrize('protocol', ['smtp', 'imap'])
def test_invalid_tls_mode_and_deceptive_loopback_host_fail_before_connect(monkeypatch, mode, host, protocol):
    attempted = []
    class ConnectionProbe:
        def __init__(self, *args, **kwargs):
            attempted.append((args, kwargs))
    if protocol == 'smtp':
        monkeypatch.setattr(email_tools.smtplib, 'SMTP', ConnectionProbe)
        connect = email_tools._smtp_connect
    else:
        monkeypatch.setattr(email_tools.imaplib, 'IMAP4', ConnectionProbe)
        connect = email_tools._imap_connect
    try:
        connect({'host':host, 'port':587 if protocol == 'smtp' else 143, 'security':mode})
    except (ValueError, OSError):
        pass
    assert not attempted, ('Cleartext connector reached', protocol, mode, host)


def test_thread_header_edit_invalidates_review_binding(mail_service):
    prepare(mail_service)
    args = dict(to='recipient@example.test', subject='Question', body='Please clarify.', account='default',
                in_reply_to='<parent@example.test>', kind='reply', session_id=SESSION)
    saved = email_drafts.save_draft(**args, references=['<reviewed-ancestor@example.test>'])
    draft_id = saved.draft['draft_id']
    assert email_drafts.approve_draft(draft_id, session_id=SESSION).ok
    edited = email_drafts.save_draft(**args, draft_id=draft_id, references=['<different-private-thread@example.test>'])
    result = email_drafts.send_draft(draft_id, session_id=SESSION)
    assert not mail_service.store.captured, (edited.draft, result.status)


def test_default_account_change_cannot_redirect_an_approved_draft(mail_service, monkeypatch):
    selected = ['default']
    monkeypatch.setattr(email_tools, 'resolve_default_account', lambda kind: selected[0])
    draft_id = prepare(mail_service, account='')
    mail_service.store.add_user('other@example.test', 'other-pw')
    _store_accounts('other@example.test', 'other-pw', account='other')
    selected[0] = 'other'
    result = email_drafts.send_draft(draft_id, session_id=SESSION)
    assert not any(c['from'] == 'other@example.test' for c in mail_service.store.captured), result.status
