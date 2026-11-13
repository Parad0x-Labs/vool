"""Sealed rejection, natural deadlines, quota, queue control and poisoned input."""
import hashlib
import io
import json
import secrets
import time
import zipfile

import pytest

from core import runtime_paths
from tests.lend.test_invites import call, connect, ok
from tests.lend.test_invites import homes as homes
from tests.lend.test_jobs import ready, submit, upload


def wire(homes, op, **fields):
    from core.lend.service import LendHome
    from core.web.api.drop_api import _home

    runtime_paths.configure_runtime_home(homes[2])
    with _home() as drop, drop.scope(), LendHome(drop) as home:
        value = home.all('invites')[-1]
        home.send(value['sid'], home.envelope(value, op, **fields))


def test_size_and_used_up_refuse_before_more_guest_bytes(homes):
    key = ready(homes, max_bytes=4)
    assert upload(homes, key, b'12345')[1]['error'] == 'INPUT_TOO_BIG'
    assert ok(homes, homes[1], 'poll')['jobs'] == []
    job = submit(homes, key, b'1234')
    assert upload(homes, key, b'abcd')[1]['error'] == 'USED_UP'
    host = ok(homes, homes[1], 'poll')
    assert len(host['jobs']) == 1 and host['jobs'][0]['id'] == job
    assert host['invites'][0]['used'] == 1


def test_decline_has_matching_receipts_and_no_worker(homes):
    key = ready(homes)
    job = submit(homes, key, b'Please decline this document')
    ok(homes, homes[1], 'poll')
    ok(homes, homes[1], 'decline', job=job)
    guest = ok(homes, homes[2], 'poll')['jobs'][0]
    host = ok(homes, homes[1], 'list')['jobs'][0]
    assert guest['state'] == host['state'] == 'declined'
    assert guest['receipt'] == host['receipt'] and guest['receipt']['result_digest'] is None
    assert not list((homes[1]/'lend').glob('worker-*')) and not (homes[1]/'lend'/job).exists()
    assert call(homes, homes[1], 'run', job=job)[0] == 409


def test_revoke_mid_queue_declines_all_jobs_and_stops_new_jobs(homes):
    key = ready(homes, jobs=3)
    first = submit(homes, key, b'First queued document')
    second = submit(homes, key, b'Second queued document')
    assert {j['state'] for j in ok(homes, homes[1], 'poll')['jobs']} == {'pending'}
    ok(homes, homes[1], 'revoke', invite=key)
    guest = ok(homes, homes[2], 'poll')
    assert guest['invites'][0]['state'] == 'revoked'
    assert {j['state'] for j in guest['jobs']} == {'revoked'}
    assert all(j['receipt']['state'] == 'revoked' for j in guest['jobs'])
    assert upload(homes, key, b'Another')[1]['error'] == 'REVOKED'
    for job in (first, second):
        assert not (homes[1]/'lend'/job).exists()
        assert call(homes, homes[1], 'run', job=job)[0] == 409


def test_reencrypted_replay_and_guest_operation_cannot_widen_terms(homes):
    key = ready(homes)
    data = b'Bound to one PDF job'
    job = submit(homes, key, data)
    ok(homes, homes[1], 'poll')
    wire(homes, 'job', job=job, name='input.txt', size=len(data), input_digest=hashlib.sha256(data).hexdigest())
    wire(homes, 'job', job=secrets.token_hex(16), name='bad.txt', size=1,
         input_digest=hashlib.sha256(b'x').hexdigest(), capability='shell')
    states = ok(homes, homes[1], 'poll')
    assert states['outcomes'] == ['rejected', 'rejected']
    assert len(states['jobs']) == 1 and states['invites'][0]['used'] == 1
    assert states['jobs'][0]['state'] == 'pending'


def test_tampered_terms_never_receive_host_confirmation(homes):
    from core.drop.session import b64, unb64
    from core.lend.terms import digest

    _, _, _, host, guest = connect(homes)
    value = ok(homes, homes[1], 'status')['defaults']
    link = ok(homes, homes[1], 'invite', fingerprint=guest, confirmed=True, terms=value)['link']
    payload = json.loads(unb64(link.removeprefix('vool-lend://invite/'), 1, 3000))
    payload['terms']['auto'] = True
    payload['digest'] = digest(payload['terms'])
    link = 'vool-lend://invite/' + b64(json.dumps(payload).encode())
    key = ok(homes, homes[2], 'join', link=link, fingerprint=host, confirmed=True)['invite']
    assert 'rejected' in ok(homes, homes[1], 'poll')['outcomes']
    assert ok(homes, homes[2], 'poll')['invites'][-1]['state'] == 'confirming'
    assert upload(homes, key, b'No bytes before confirmation')[1]['error'] == 'HOST_NOT_CONFIRMED'


def test_wrong_fingerprint_unconfirmed_join_and_replay_refused(homes):
    _, link, _, host, guest = connect(homes)
    assert call(homes, homes[2], 'join', link=link, fingerprint=guest, confirmed=True)[1]['error'] == 'WRONG_FINGERPRINT'
    assert call(homes, homes[2], 'join', link=link, fingerprint=host, confirmed=False)[1]['error'] == 'FINGERPRINT_CONFIRMATION_REQUIRED'
    assert call(homes, homes[2], 'join', link=link, fingerprint=host, confirmed=True)[1]['error'] == 'REPLAY'


def test_expired_queue_refuses_execution_and_erases_input(homes):
    # Three seconds, not one: on a slow hosted runner the invite, join and upload above took longer
    # than a one-second window, so the job expired before it was ever queued (empty poll).
    expires = int(time.time()*1000)+3000
    key = ready(homes, expires=expires)
    job = submit(homes, key, b'Expires before approval')
    ok(homes, homes[1], 'poll')
    time.sleep(max(0.0, expires/1000-time.time())+0.1)
    host = ok(homes, homes[1], 'poll')['jobs'][0]
    guest = ok(homes, homes[2], 'poll')['jobs'][0]
    assert host['state'] == guest['state'] == 'expired'
    assert host['receipt'] == guest['receipt']
    assert not (homes[1]/'lend'/job).exists()
    assert call(homes, homes[1], 'run', job=job)[1]['error'] == 'EXPIRED'


def test_malformed_document_fails_without_result_or_surviving_worker(homes):
    key = ready(homes, auto=True)
    job = submit(homes, key, b'%PDF-1.7\nnot a PDF document')
    host = ok(homes, homes[1], 'poll')['jobs'][0]
    guest = ok(homes, homes[2], 'poll')['jobs'][0]
    assert host['state'] == guest['state'] == 'failed' and host['receipt'] == guest['receipt']
    assert call(homes, homes[2], 'download', job=job)[0] == 409
    assert not list((homes[1]/'lend').glob('worker-*'))


def test_real_heavy_photo_job_times_out_and_erases_worker(homes):
    from PIL import Image

    key = ready(homes, auto=True, capability='contact_sheet', max_seconds=1, max_length=100)
    photo = io.BytesIO()
    Image.new('RGB', (4000, 4000), 'blue').save(photo, format='PNG')
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as z:
        for n in range(50):
            z.writestr(f'{n}.png', photo.getvalue())
    job = submit(homes, key, archive.getvalue(), name='photos.zip')
    host = ok(homes, homes[1], 'poll')['jobs'][0]
    guest = ok(homes, homes[2], 'poll')['jobs'][0]
    assert host['state'] == guest['state'] == 'timeout', host
    assert host['receipt'] == guest['receipt']
    assert host['receipt']['seconds'] >= .5
    assert not list((homes[1]/'lend').glob('worker-*')) and not (homes[1]/'lend'/job).exists()


def test_host_offline_has_a_real_bounded_wait(homes):
    key = ready(homes)
    submit(homes, key, b'The host does not poll again')
    time.sleep(30.1)
    guest = ok(homes, homes[2], 'poll')['jobs'][0]
    assert guest['state'] == 'host_unreachable' and guest['receipt'] is None


def test_out_of_order_file_and_result_frames_recover(homes):
    key = ready(homes, max_length=100000)
    host = ok(homes, homes[1], 'identity', door='drop')['fingerprint']
    guest = ok(homes, homes[2], 'identity', door='drop')['fingerprint']
    data = b'A distinct longer document.\n'*2000
    job = submit(homes, key, data)
    mailbox = homes[3]/host
    frames = sorted(mailbox.glob('*.wire'))
    assert len(frames) >= 3
    frames[0].rename(mailbox/'zz-delayed-header.wire')
    assert ok(homes, homes[1], 'poll')['jobs'][0]['state'] == 'pending'
    assert ok(homes, homes[1], 'run', job=job)['job']['state'] == 'returning'
    frames = sorted((homes[3]/guest).glob('*.wire'))
    assert len(frames) >= 3
    frames[1].rename(homes[3]/guest/'zz-delayed-result-header.wire')
    result = ok(homes, homes[2], 'poll')['jobs'][0]
    assert result['state'] == 'complete' and result['receipt']['state'] == 'complete'


def test_changed_file_digest_never_reaches_the_worker(homes):
    from core.drop.session import b64

    ready(homes)
    job = secrets.token_hex(16)
    wire(homes, 'job', job=job, name='input.txt', size=7, input_digest=hashlib.sha256(b'correct').hexdigest())
    wire(homes, 'chunk', job=job, index=0, data=b64(b'changed'))
    host = ok(homes, homes[1], 'poll')['jobs'][0]
    assert host['state'] == 'failed' and host['receipt']['result_digest'] is None
    assert not list((homes[1]/'lend').glob('worker-*')) and not (homes[1]/'lend'/job).exists()


@pytest.mark.parametrize('ending', ['revoked', 'expired'])
def test_delivery_ack_remains_valid_after_invite_ends(homes, ending):
    # The guest has already received and verified the result. A late host poll
    # must accept that delivery receipt even if the invitation ended meanwhile.
    key = ready(homes, auto=True, expires=int(time.time()*1000)+5000)
    job = submit(homes, key, b'A completed document before the invitation ends')
    host = ok(homes, homes[1], 'poll')['jobs'][0]
    guest = ok(homes, homes[2], 'poll')['jobs'][0]
    assert host['state'] == 'returning' and guest['state'] == 'complete'
    receipt = guest['receipt']
    if ending == 'revoked':
        ok(homes, homes[1], 'revoke', invite=key)
    else:
        expiry = ok(homes, homes[1], 'list')['invites'][0]['terms']['expires']
        time.sleep(max(0, (expiry-time.time()*1000)/1000)+.05)
    # Sweep before reading the queued delivery ack, as a timer or restart can.
    ok(homes, homes[1], 'list')
    host = ok(homes, homes[1], 'poll')['jobs'][0]
    assert host['state'] == 'complete' and host['receipt'] == receipt
    assert ok(homes, homes[2], 'poll')['jobs'][0]['receipt'] == receipt


def test_partial_result_times_out_and_accepts_the_final_revocation_receipt(homes):
    key = ready(homes, auto=True, max_length=250000, expires=int(time.time()*1000)+120000)
    peer = ok(homes, homes[2], 'identity', door='drop')['fingerprint']
    job = submit(homes, key, secrets.token_hex(60000).encode())
    assert ok(homes, homes[1], 'poll')['jobs'][0]['state'] == 'returning'
    frames = sorted((homes[3]/peer).glob('*.wire'))
    assert len(frames) >= 4
    last = frames[-1]
    held = homes[3].parent/'withheld-result.wire'
    last.rename(held)
    partial = ok(homes, homes[2], 'poll')['jobs'][0]
    assert partial['state'] == 'receiving_result'
    assert list((homes[2]/'lend'/job).glob('*.part'))
    assert call(homes, homes[2], 'download', job=job)[0] == 409
    time.sleep(30.1)
    assert ok(homes, homes[2], 'poll')['jobs'][0]['state'] == 'delivery_failed'
    assert not (homes[2]/'lend'/job).exists()

    ok(homes, homes[1], 'revoke', invite=key)
    host = ok(homes, homes[1], 'poll')['jobs'][0]
    guest = ok(homes, homes[2], 'poll')['jobs'][0]
    assert host['state'] == guest['state'] == 'revoked'
    assert host['receipt'] == guest['receipt']
    held.rename(last)
    assert ok(homes, homes[2], 'poll')['jobs'][0]['state'] == 'revoked'
    assert not (homes[2]/'lend'/job).exists()


@pytest.mark.parametrize('ending', ['revoked', 'expired'])
def test_empty_ack_cannot_mark_an_unexecuted_job_complete(homes, ending):
    key = ready(homes, expires=int(time.time()*1000)+5000)
    job = submit(homes, key, b'This document must never execute')
    ok(homes, homes[1], 'poll')
    if ending == 'revoked':
        ok(homes, homes[1], 'revoke', invite=key)
    else:
        expiry = ok(homes, homes[1], 'list')['invites'][0]['terms']['expires']
        time.sleep(max(0, (expiry-time.time()*1000)/1000)+.05)
    before = ok(homes, homes[1], 'list')['jobs'][0]
    wire(homes, 'delivered', job=job, receipt=None)
    after = ok(homes, homes[1], 'poll')
    assert 'rejected' in after['outcomes']
    assert after['jobs'][0]['state'] == before['state'] == ending
    assert after['jobs'][0]['receipt'] == before['receipt']


@pytest.mark.parametrize('name', ['bad\ud800.txt', 'bad\udfff.txt', 'bad\x1f.txt'])
def test_malformed_peer_filename_cannot_poison_the_local_view(homes, name):
    key = ready(homes)
    wire(homes, 'job', job=secrets.token_hex(16), name=name, size=1,
         input_digest=hashlib.sha256(b'x').hexdigest())
    state = ok(homes, homes[1], 'poll')
    assert 'rejected' in state['outcomes']
    assert state['jobs'] == [] and state['invites'][0]['used'] == 0
    submit(homes, key, b'A readable later document', name='résumé.txt')
    assert ok(homes, homes[1], 'poll')['jobs'][0]['name'] == 'résumé.txt'
