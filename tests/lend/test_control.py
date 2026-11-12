"""Local decisions remain responsive while a real confined job is running."""
import io
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.lend.test_invites import call, ok
from tests.lend.test_invites import homes as homes
from tests.lend.test_jobs import ready, submit, upload


def test_incomplete_or_changed_upload_consumes_no_allowance(homes):
    key = ready(homes)
    for headers in [{'X-Lend-Size': '100'}, {'X-Lend-SHA256': '0'*64}]:
        assert upload(homes, key, b'partial data', extra_headers=headers)[1]['error'] == 'INPUT_CHANGED'
    state = ok(homes, homes[2], 'list')
    assert state['jobs'] == [] and state['invites'][0]['used'] == 0
    assert ok(homes, homes[1], 'poll')['jobs'] == []


def test_missing_input_fails_immediately_without_running(homes):
    key = ready(homes)
    job = submit(homes, key, b'A valid document')
    ok(homes, homes[1], 'poll')
    (homes[1]/'lend'/job/'file.bin').unlink()
    assert ok(homes, homes[1], 'run', job=job)['job']['state'] == 'failed'
    guest = ok(homes, homes[2], 'poll')['jobs'][0]
    assert guest['state'] == 'failed' and guest['receipt']['result_digest'] is None


def test_two_local_run_requests_cannot_execute_twice(homes):
    key = ready(homes)
    job = submit(homes, key, b'Concurrent local approval document')
    ok(homes, homes[1], 'poll')
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(call, homes, homes[1], 'run', job=job) for _ in range(2)]
        result = [item.result(timeout=30) for item in pending]
    assert sorted(code for code, _ in result) == [200, 409]
    assert len(ok(homes, homes[1], 'list')['jobs']) == 1
    assert ok(homes, homes[2], 'poll')['jobs'][0]['state'] == 'complete'


def test_polling_and_listing_cannot_race_the_local_database_files(homes):
    key = ready(homes)
    job = submit(homes, key, b'Delivery while local views open and close')
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = [pool.submit(call, homes, homes[1], 'poll' if n % 4 == 0 else 'list')
                   for n in range(100)]
        results = [item.result(timeout=60) for item in pending]
    assert all(code == 200 for code, _ in results), results
    assert ok(homes, homes[1], 'list')['jobs'][0]['state'] == 'pending'
    assert ok(homes, homes[1], 'run', job=job)['job']['state'] == 'returning'
    assert ok(homes, homes[2], 'poll')['jobs'][0]['state'] == 'complete'


def test_delivery_and_views_can_open_and_close_the_ledger_concurrently(homes):
    from core import runtime_paths
    from core.lend.service import LendHome
    from core.web.api.drop_api import _home

    ready(homes)
    runtime_paths.configure_runtime_home(homes[1])
    with _home() as drop:
        def views():
            for _ in range(100):
                with LendHome(drop) as home:
                    assert home.all('invites')[0]['state'] == 'ready'

        with ThreadPoolExecutor(max_workers=4) as pool:
            pending = [pool.submit(views) for _ in range(4)]
            for item in pending:
                item.result(timeout=60)


def test_unsafe_ledger_sidecars_are_refused_before_sqlite_opens(homes, tmp_path):
    from core import runtime_paths
    from core.drop import DropError
    from core.lend.service import LendHome
    from core.web.api.drop_api import _home

    ready(homes)
    runtime_paths.configure_runtime_home(homes[1])
    victim = tmp_path/'private-victim'
    victim.write_bytes(b'must not be changed')
    with _home() as drop:
        for suffix in ('-wal', '-shm'):
            path = homes[1]/'lend'/('lend.sqlite'+suffix)
            path.write_bytes(b'unsafe')
            path.chmod(0o644)
            with pytest.raises(DropError) as refused:
                LendHome(drop)
            assert refused.value.code == 'UNSAFE_STORE_PERMISSIONS'
            path.unlink()
            path.symlink_to(victim)
            with pytest.raises(DropError) as refused:
                LendHome(drop)
            assert refused.value.code == 'UNSAFE_STORE_PATH'
            path.unlink()
            assert victim.read_bytes() == b'must not be changed'


def test_revoked_invitation_history_does_not_permanently_disable_lending(homes):
    from core import runtime_paths
    from core.lend.service import LendHome
    from core.lend.terms import parse_link
    from core.web.api.drop_api import _home

    ready(homes)
    guest = ok(homes, homes[2], 'identity', door='drop')['fingerprint']
    terms = ok(homes, homes[1], 'status')['defaults']
    runtime_paths.configure_runtime_home(homes[1])
    with _home() as drop, drop.scope(), LendHome(drop) as home:
        for _ in range(99):
            link = home.invite(guest, terms, confirmed=True)
            home.revoke(parse_link(link, now=drop.clock())['id'])
        assert len(home.all('invites')) == 100
    # The real owner route must still offer a job; retained receipts/history
    # cannot permanently consume the active-invitation allowance.
    offer = ok(homes, homes[1], 'invite', fingerprint=guest, confirmed=True, terms=terms)
    assert ok(homes, homes[2], 'preview', link=offer['link'])['terms'] == terms


def test_expiry_releases_unused_transport_invite_once(homes):
    from core import runtime_paths
    from core.lend.service import LendHome
    from core.lend.terms import parse_link
    from core.web.api.drop_api import _home

    ready(homes)
    peer = ok(homes, homes[2], 'identity', door='drop')['fingerprint']
    terms = ok(homes, homes[1], 'status')['defaults']
    runtime_paths.configure_runtime_home(homes[1])
    with _home() as drop, drop.scope(), LendHome(drop) as home:
        terms['expires'] = drop.clock()+5000
        value = parse_link(home.invite(peer, terms, confirmed=True), now=drop.clock())
        token = value['drop'].rsplit('/', 1)[1]
        assert drop.db.execute('SELECT 1 FROM invites WHERE token=?', (token,)).fetchone()
        time.sleep(max(0, (terms['expires']-drop.clock())/1000)+.05)
        home.sweep()
        assert home.get('invites', value['id'])['state'] == 'expired'
        assert drop.db.execute('SELECT 1 FROM invites WHERE token=?', (token,)).fetchone() is None
        writes = home.db.total_changes
        home.sweep()
        assert home.db.total_changes == writes


def test_revoke_during_execution_is_immediate_and_withholds_result(homes):
    from PIL import Image

    key = ready(homes, jobs=2, capability='contact_sheet', max_seconds=3, max_length=100)
    photo = io.BytesIO()
    Image.new('RGB', (4000, 4000), 'blue').save(photo, format='PNG')
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as z:
        for n in range(50):
            z.writestr(f'{n}.png', photo.getvalue())
    first = submit(homes, key, archive.getvalue(), name='photos.zip')
    second = submit(homes, key, photo.getvalue(), name='photo.png')
    ok(homes, homes[1], 'poll')
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(call, homes, homes[1], 'run', job=first)
        deadline = time.monotonic()+10
        while time.monotonic() < deadline:
            jobs = ok(homes, homes[1], 'list')['jobs']
            if jobs[0]['state'] == 'running':
                break
            time.sleep(.02)
        assert jobs[0]['state'] == 'running'
        start = time.monotonic()
        ok(homes, homes[1], 'revoke', invite=key)
        assert time.monotonic()-start < 1.5
        assert running.result(timeout=10)[1]['job']['state'] == 'revoked'
    jobs = ok(homes, homes[1], 'list')['jobs']
    assert jobs[1]['id'] == second and jobs[1]['state'] == 'revoked'
    assert not list((homes[1]/'lend').glob('worker-*'))
    assert all(j['state'] == 'revoked' for j in ok(homes, homes[2], 'poll')['jobs'])


def test_disable_revokes_host_offers_even_when_drop_is_already_off(homes):
    key = ready(homes, jobs=2)
    job = submit(homes, key, b'Only this selected document')
    ok(homes, homes[1], 'poll')
    ok(homes, homes[1], 'settings', door='drop', enabled=False,
       relay=str(homes[3]), allow_digest=False)
    assert ok(homes, homes[1], 'settings', enabled=False)['enabled'] is False
    # Repeated disable must stay local and must not reactivate transport.
    assert ok(homes, homes[1], 'settings', enabled=False)['enabled'] is False
    assert ok(homes, homes[1], 'status')['drop_enabled'] is False
    ok(homes, homes[1], 'settings', door='drop', enabled=True,
       relay=str(homes[3]), allow_digest=False)
    ok(homes, homes[1], 'settings', enabled=True)
    listing = ok(homes, homes[1], 'list')
    assert listing['invites'][0]['id'] == key
    assert listing['invites'][0]['state'] == 'revoked'
    assert listing['jobs'][0]['id'] == job and listing['jobs'][0]['state'] == 'revoked'
    assert call(homes, homes[1], 'run', job=job)[1]['error'] == 'REVOKED'
    assert not (homes[1]/'lend'/job).exists()
    assert not list((homes[1]/'lend').glob('worker-*'))
