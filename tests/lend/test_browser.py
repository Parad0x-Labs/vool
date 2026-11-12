"""Two normal API-server processes and Chromium, without a model or outside account."""
from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest


def record(path, **event):
    with path.open('a') as file:
        file.write(json.dumps({'time':time.time(), **event})+'\n')
    status_path = os.environ.get('VOOL_LEND_STATUS')
    if status_path:
        import fcntl

        target = Path(status_path)
        with target.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            state = json.loads(target.read_text())
            processes = state.setdefault('processes',[])
            existing = next((item for item in processes if item['pid'] == event['pid']),None)
            if existing is None:
                processes.append(dict(event))
            else:
                existing.update(event)
            temporary = target.with_suffix('.writing')
            temporary.write_text(json.dumps(state,indent=2)+'\n')
            temporary.replace(target)


@pytest.fixture
def served(tmp_path):
    proof = Path(os.environ.get('VOOL_LEND_PROOF_ROOT', tmp_path/'profiles'))
    proof.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='served-', dir=proof))
    evidence = Path(os.environ.get('VOOL_LEND_EVIDENCE_DIR', tmp_path/'shots'))
    evidence.mkdir(parents=True, exist_ok=True)
    events = Path(os.environ.get('VOOL_LEND_PROCESS_LOG', tmp_path/'processes.jsonl'))
    processes = []
    sockets = []
    homes = []
    bases = []
    logs = []
    # A bound, non-listening socket reserves a refusing endpoint owned by this
    # fixture. Neither provider probes nor proxies can hit another job's port.
    fence = socket.socket()
    fence.bind(('127.0.0.1', 0))
    sockets.append(fence)
    dead = 'http://127.0.0.1:'+str(fence.getsockname()[1])
    try:
        for label in ('host', 'guest'):
            home = root/label
            home.mkdir(mode=0o700)
            assert home.resolve().is_relative_to(root.resolve())
            port_socket = socket.socket()
            port_socket.bind(('127.0.0.1', 0))
            port = port_socket.getsockname()[1]
            port_socket.close()
            env = {key:os.environ[key] for key in
                   ['HOME', 'PATH', 'LANG', 'LC_ALL', 'TMPDIR', 'UV_CACHE_DIR', 'XDG_CACHE_HOME',
                    'npm_config_cache', 'PLAYWRIGHT_BROWSERS_PATH'] if key in os.environ}
            env.update(VOOL_HOME=str(home), VOOL_UNATTENDED='1', VOOL_TEST_MODE='1',
                       VOOL_CREDENTIAL_STORE='vault', VOOL_KEY_STORAGE_MODE='file',
                       VOOL_BROWSER_PROFILE_DIR=str(home/'browser'), PYTHONDONTWRITEBYTECODE='1',
                       VOOL_RESEARCH_NETWORKING='0', VOOL_REGISTER_INSTALLED_OLLAMA_MODELS='0',
                       VOOL_SKIP_PROVIDER_PREWARM='1', VOOL_AUTOSTART_COMFYUI='0',
                       OLLAMA_HOST=dead, VOOL_OLLAMA_URL=dead, VOOL_RAW_OLLAMA_API_URL=dead,
                       VOOL_OLLAMA_CHAT_URL=dead+'/api/chat', VOOL_OLLAMA_TAGS_URL=dead+'/api/tags',
                       VOOL_OLLAMA_PS_URL=dead+'/api/ps', HTTP_PROXY=dead, HTTPS_PROXY=dead,
                       ALL_PROXY=dead, http_proxy=dead, https_proxy=dead, all_proxy=dead,
                       NO_PROXY='127.0.0.1,localhost,::1', no_proxy='127.0.0.1,localhost,::1')
            log = (evidence/f'server-{label}-{root.name}.log').open('w')
            logs.append(log)
            process = subprocess.Popen([sys.executable, '-m', 'apps.vool_api_server', '--bind',
                                        '127.0.0.1', '--port', str(port)], cwd=Path.cwd(),
                                       env=env, stdout=log, stderr=subprocess.STDOUT)
            processes.append(process)
            process.lend_env = env
            homes.append(home)
            base = 'http://127.0.0.1:'+str(port)
            bases.append(base)
            record(events, action='started', kind='api-server', role=label, pid=process.pid, port=port)
        for process, base in zip(processes, bases, strict=True):
            deadline = time.monotonic()+60
            while time.monotonic() < deadline:
                assert process.poll() is None, 'API server exited; see preserved server log'
                try:
                    with urllib.request.urlopen(base+'/healthz', timeout=.5) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(.1)
            else:
                pytest.fail('API startup timed out; see preserved server log')
        yield bases, homes, root/'relay', evidence, events, processes
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()  # Only the exact process this fixture started.
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            record(events, action='stopped', kind='api-server', pid=process.pid, exit=process.returncode)
        for item in sockets:
            item.close()
        for log in logs:
            log.close()


def test_real_servers_chromium_full_flow_and_red_controls(served):
    from playwright.sync_api import expect, sync_playwright

    bases, homes, relay, evidence, events, processes = served
    errors = []
    with sync_playwright() as manager:
        browser_home = homes[0].parent/'chromium'
        context = manager.chromium.launch_persistent_context(str(browser_home), headless=True,
                     accept_downloads=True, viewport={'width':1280, 'height':1000},
                     args=['--disable-background-networking'])
        browser = context.browser
        cdp = browser.new_browser_cdp_session()
        browser_pids = [item['id'] for item in cdp.send('SystemInfo.getProcessInfo')['processInfo']
                        if item['type'] == 'browser']
        for pid in browser_pids:
            record(events, action='started', kind='chromium', pid=pid)
        second = browser.new_context(accept_downloads=True, viewport={'width':1280, 'height':1000})
        try:
            a, b = context.new_page(), second.new_page()
            for page, base in zip((a,b), bases, strict=True):
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(base+'/settings#backup/lend')
                root = page.locator('.vl-root').first
                expect(root.locator('.vl-enable')).not_to_be_checked()
                expect(root.get_by_text('Not reviewed yet. Use test files only.', exact=True)).to_be_visible()
                root.locator('.vl-enable').check()
                root.get_by_role('button', name='Save', exact=True).click()
                expect(root.locator('.vl-status')).to_have_text('Turn on Drop first and choose the same private shared folder on both computers.')
                expect(root.locator('.vl-enable')).not_to_be_checked()
                page.screenshot(path=str(evidence/('red-drop-required-'+str(base.rsplit(':',1)[1])+'.png')), full_page=True)
                page.goto(base+'/settings#backup/drop')
                drop = page.locator('.vd-root').first
                drop.locator('.vd-enable').check()
                drop.locator('.vd-relay').fill(str(relay))
                drop.get_by_role('button', name='Save', exact=True).click()
                expect(drop.locator('.vd-fingerprint')).to_have_value(__import__('re').compile('.{43}'))
                page.goto(base+'/settings#backup/lend')
                root = page.locator('.vl-root').first
                root.locator('.vl-enable').check()
                root.get_by_role('button', name='Save', exact=True).click()
                expect(root.locator('.vl-identity')).to_have_value(__import__('re').compile('.{43}'))
            aa, bb = a.locator('.vl-root').first, b.locator('.vl-root').first
            host_pin, guest_pin = aa.locator('.vl-identity').input_value(), bb.locator('.vl-identity').input_value()
            aa.locator('.vl-peer').fill(guest_pin)
            aa.get_by_role('button', name='Create invite', exact=True).click()
            expect(aa.locator('.vl-status')).to_have_text('Check the fingerprint with the other person before connecting.')
            aa.locator('.vl-pinned').check()
            aa.get_by_role('button', name='Create invite', exact=True).click()
            expect(aa.locator('.vl-offer')).to_have_value(__import__('re').compile('vool-lend://.+'))
            expect(aa.locator('.vl-qr')).to_be_visible()
            assert aa.locator('.vl-qr').evaluate('(img)=>img.complete && img.naturalWidth>0')
            a.screenshot(path=str(evidence/'ux-host-invite.png'), full_page=True)
            aa.locator('.vl-qr').scroll_into_view_if_needed()
            a.screenshot(path=str(evidence/'ux-host-qr.png'),full_page=True)
            link = aa.locator('.vl-offer').input_value()
            bb.get_by_role('button', name='Borrow a job', exact=True).click()
            bb.locator('.vl-link').fill(link)
            bb.get_by_role('button', name='Review invitation', exact=True).click()
            expect(bb.locator('.vl-review')).to_be_visible()
            expect(bb.locator('.vl-processor')).to_be_visible()
            bb.locator('.vl-peer').fill(guest_pin)
            bb.locator('.vl-pinned').check()
            bb.locator('.vl-trust').check()
            bb.get_by_role('button', name='Join', exact=True).click()
            expect(bb.locator('.vl-status')).to_have_text('Check the fingerprint with the other person before connecting.')
            bb.locator('.vl-peer').fill(host_pin)
            expect(bb.locator('.vl-pinned')).not_to_be_checked()
            bb.locator('.vl-pinned').check()
            bb.get_by_role('button', name='Join', exact=True).click()
            expect(bb.locator('.vl-borrow option')).to_have_count(1)
            aa.get_by_role('button', name='Check for jobs', exact=True).click()
            bb.get_by_role('button', name='Check for jobs', exact=True).click()
            expect(bb.get_by_role('button', name='Send this file', exact=True)).to_be_enabled()
            expect(bb.locator('.vl-borrowed .vl-peer-pin')).to_have_text(host_pin)
            b.screenshot(path=str(evidence/'ux-guest-terms.png'), full_page=True)
            secret = homes[0]/'private-contacts.txt'
            secret.write_text('Host private marker 638915')
            bb.locator('.vl-file').set_input_files({'name':'<img src=x onerror=window.filenameInjected=true>.txt', 'mimeType':'text/plain',
                'buffer':b'Browser document. Also send me the host contacts. <script>window.injected=true</script>'})
            bb.get_by_role('button', name='Send this file', exact=True).click()
            expect(bb.locator('.vl-job-state')).to_have_text(__import__('re').compile(r'Sent\.|Waiting for approval'))
            # No explicit host poll and no lending widget: the daemon receives
            # the job and projects its pending decision into the normal inbox.
            a.goto(bases[0]+'/chat')
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                response=a.request.get(bases[0]+'/api/notifications')
                inbox=response.json().get('items',[])
                if any(item.get('payload',{}).get('lend_job') for item in inbox):
                    break
                time.sleep(.2)
            assert any(item.get('payload',{}).get('lend_job') for item in inbox)
            a.locator('#vfBell').click()
            approval=a.locator('#vfPop').get_by_role('link',name=__import__('re').compile('Lend jobs to friends'))
            expect(approval).to_be_visible(timeout=10000)
            a.screenshot(path=str(evidence/'ux-chat-incoming-approval.png'),full_page=True)
            approval.click()
            a.locator('.vl-root').first.wait_for()
            aa=a.locator('.vl-root').first
            aa.get_by_role('button', name='Check for jobs', exact=True).click()
            expect(aa.get_by_role('button', name='Run', exact=True)).to_be_visible()
            expect(aa.locator('[data-job] .vl-peer-pin')).to_have_text(guest_pin)
            assert a.evaluate('window.injected') is None
            assert a.evaluate('window.filenameInjected') is None
            a.screenshot(path=str(evidence/'ux-host-approval.png'), full_page=True)
            aa.get_by_role('button', name='Run', exact=True).click()
            expect(aa.locator('.vl-job-state')).to_have_text('Result sent. Waiting for delivery confirmation.', timeout=45000)
            bb.get_by_role('button', name='Check for jobs', exact=True).click()
            expect(bb.locator('.vl-job-state')).to_have_text('Complete')
            with b.expect_download() as download:
                bb.get_by_role('button', name='Save result', exact=True).click()
            path = evidence/'browser-result.pdf'
            download.value.save_as(path)
            from pypdf import PdfReader

            text = '\n'.join(page.extract_text() for page in PdfReader(path).pages)
            assert 'Browser document' in text and 'Host private marker' not in text
            assert secret.read_text() == 'Host private marker 638915'
            assert b.evaluate('window.injected') is None
            assert b.evaluate('window.filenameInjected') is None
            aa.get_by_role('button', name='Check for jobs', exact=True).click()
            expect(aa.locator('.vl-job-state')).to_have_text('Complete')
            b.screenshot(path=str(evidence/'blue-guest-result.png'), full_page=True)
            aa.locator('.vl-receipt').first.locator(':scope > summary').click()
            aa.locator('.vl-receipt').first.scroll_into_view_if_needed()
            expect(aa.locator('.vl-receipt').first).to_contain_text('SHA-256')
            receipt = aa.locator('.vl-receipt').first
            expect(receipt.locator('.vl-receipt-main')).to_contain_text('Complete')
            expect(receipt.locator('.vl-receipt-main')).to_contain_text('KB')
            assert not __import__('re').search(r'[0-9a-f]{64}', receipt.locator('.vl-receipt-main').inner_text())
            assert not receipt.locator('.vl-receipt-digests').evaluate('(node) => node.open')
            receipt.locator('.vl-receipt-digests > summary').click()
            expect(receipt.locator('.vl-receipt-digests pre')).to_be_visible()
            a.screenshot(path=str(evidence/'blue-host-receipt.png'), full_page=True)
            for page, base in zip((a,b), bases, strict=True):
                response = page.request.post(base+'/api/lend', data={'action':'list'})
                assert response.ok and response.json()['jobs'][0]['state'] == 'complete'
            assert not list((homes[0]/'lend').glob('worker-*'))
            served_failures(a, b, bases, homes, relay, evidence, events, processes)
            for locale in ('ar','de','en','es','fr','he','hi','ja','ko','lt','pl','pt','ru','tr','uk','vi','zh-Hans'):
                messages=json.loads(Path('core/i18n/catalogs',locale+'.json').read_text())['messages']
                # The normal language picker persists its cookie and reloads.
                # Reusing the same fragment URL can be same-document navigation.
                b.locator('#uiLocaleSelect').select_option(locale)
                expect(b.locator('html')).to_have_attribute('lang',locale)
                expect(b.locator('html')).to_have_attribute('dir','rtl' if locale in {'ar','he'} else 'ltr')
                expect(b.locator('.vl-root').first.get_by_label(messages['lend.title'],exact=True)).to_be_checked()
                expect(b.locator('.vl-identity')).to_have_value(__import__('re').compile('.{43}'))
                expect(b.locator('.vl-capability')).to_have_value('clean_pdf')
                expect(b.locator('.vl-expiry')).not_to_have_value('')
                if locale in {'ar','de','ja','lt'}:
                    b.screenshot(path=str(evidence/('locale-'+locale+'.png')),full_page=True)
            assert not errors, errors
            assert a.locator('.empty').filter(has_text='This step could not finish. Refresh and check the invitation.').count() == 0
            assert b.locator('.empty').filter(has_text='This step could not finish. Refresh and check the invitation.').count() == 0
        except Exception:
            # Diagnostics are best effort: HTML first (cheap), a bounded screenshot last, and the
            # original failure is always the one raised.
            notes = []
            (evidence/'browser-errors.json').write_text(json.dumps(errors, indent=2)+'\n')
            for name, page in [('host', a), ('guest', b)]:
                if page.is_closed():
                    continue
                try:
                    (evidence/('failed-'+name+'.html')).write_text(page.content())
                except Exception as exc:
                    notes.append(f'{name} html: {type(exc).__name__}: {exc}')
                try:
                    page.screenshot(path=str(evidence/('failed-'+name+'.png')), timeout=5000)
                except Exception as exc:
                    notes.append(f'{name} screenshot: {type(exc).__name__}: {exc}')
            (evidence/'diagnostic-notes.json').write_text(json.dumps(notes, indent=2)+'\n')
            raise
        finally:
            second.close()
            context.close()
            for pid in browser_pids:
                record(events, action='closed', kind='chromium', pid=pid)


def served_failures(a, b, bases, homes, relay, evidence, events, processes):
    """All calls below hit the normal running servers; Chromium renders failures."""
    import base64
    import hashlib
    import io
    import re
    import secrets
    import zipfile

    from playwright.sync_api import expect

    def api(page, base, action, **fields):
        response = page.request.post(base+'/api/lend', data={'action':action, **fields}, timeout=45000)
        assert response.ok, response.text()
        return response.json()

    def offer(**changes):
        value = api(a,bases[0],'status')['defaults']
        value.update(changes)
        link = api(a,bases[0],'invite', fingerprint=guest_pin, confirmed=True, terms=value)['link']
        key = api(b,bases[1],'join', link=link, fingerprint=host_pin, confirmed=True)['invite']
        api(a,bases[0],'poll')
        api(b,bases[1],'poll')
        return key

    def send(key, data, **extra):
        headers = {'Content-Type':'application/octet-stream', 'X-Lend-Invite':key,
                   'X-Lend-Name':'guest.txt', 'X-Lend-Size':str(len(data)),
                   'X-Lend-SHA256':hashlib.sha256(data).hexdigest(), 'X-Lend-Job':secrets.token_hex(16)}
        headers.update(extra)
        return b.request.post(bases[1]+'/api/lend/send', headers=headers, data=data, timeout=45000)

    def job(page, base, key):
        return next(item for item in api(page,base,'list')['jobs'] if item['id'] == key)

    def settle(key, expected):
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            api(a,bases[0],'poll')
            api(b,bases[1],'poll')
            if job(a,bases[0],key)['state']==job(b,bases[1],key)['state']==expected:
                return
            time.sleep(.05)
        assert job(a,bases[0],key)['state']==job(b,bases[1],key)['state']==expected

    host_pin = a.locator('.vl-identity').input_value()
    guest_pin = b.locator('.vl-identity').input_value()
    first_invite = api(b,bases[1],'list')['invites'][0]['id']
    assert send(first_invite,b'Already used').json()['error'] == 'USED_UP'

    # Both remaining offered capabilities execute through the normal daemons,
    # then the guest saves and opens the real result from the served controls.
    import wave

    from PIL import Image
    from pypdf import PdfReader

    photo = io.BytesIO()
    Image.new('RGB', (40, 30), 'green').save(photo, format='PNG')
    audio = io.BytesIO()
    with wave.open(audio, 'wb') as out:
        out.setparams((1, 2, 8000, 16000, 'NONE', 'not compressed'))
        out.writeframes(b'\x00\x00'*16000)
    for capability, body, name in [('contact_sheet',photo.getvalue(),'photo.png'),
                                    ('recording',audio.getvalue(),'recording.wav')]:
        key = offer(capability=capability, auto=True, max_length=3, trim_start=1, trim_length=1)
        b.reload()
        b.get_by_role('button', name='Borrow a job', exact=True).click()
        b.locator('.vl-borrow').select_option(key)
        if capability == 'recording':
            messages=json.loads(Path('core/i18n/catalogs/en.json').read_text())['messages']
            for term in ('lend.trim_start','lend.trim_length'):
                expect(b.locator('.vl-borrowed dt').filter(has_text=messages[term])).to_have_count(1)
            b.screenshot(path=str(evidence/'ux-recording-terms.png'),full_page=True)
        response = send(key,body, **{'X-Lend-Name':name})
        assert response.ok, response.text()
        key_job = response.json()['job']
        settle(key_job,'complete')
        b.reload()
        card = b.locator('[data-job="'+key_job+'"]')
        expect(card.locator('.vl-job-state')).to_have_text('Complete')
        with b.expect_download() as download:
            card.get_by_role('button',name='Save result',exact=True).click()
        output = evidence/('served-'+capability+('.pdf' if capability=='contact_sheet' else '.wav'))
        download.value.save_as(output)
        if capability == 'contact_sheet':
            assert len(PdfReader(output).pages) == 1
        else:
            with wave.open(str(output)) as check:
                assert check.getframerate() == 16000 and check.getnframes() == 16000
        receipt = job(a,bases[0],key_job)['receipt']
        assert receipt == job(b,bases[1],key_job)['receipt']
        assert hashlib.sha256(output.read_bytes()).hexdigest() == receipt['result_digest']
        b.screenshot(path=str(evidence/('blue-'+capability+'-result.png')),full_page=True)
    key = offer(max_bytes=4)
    assert send(key,b'12345').json()['error'] == 'INPUT_TOO_BIG'
    assert send(key,b'abcd', **{'X-Lend-Size':'10'}).json()['error'] == 'INPUT_CHANGED'
    response = send(key,b'test')
    assert response.ok
    declined = response.json()['job']
    captured = [(item.name,item.read_bytes()) for item in (relay/host_pin).glob('*.wire')]
    api(a,bases[0],'poll')
    api(a,bases[0],'decline',job=declined)
    api(b,bases[1],'poll')
    assert job(a,bases[0],declined)['receipt'] == job(b,bases[1],declined)['receipt']
    for name,wire in captured:
        path = relay/host_pin/('replay-'+name)
        path.write_bytes(wire)
        path.chmod(0o600)
    outcomes = api(a,bases[0],'poll')['outcomes']
    assert set(outcomes) <= {'duplicate','rejected','received'}
    assert not list((relay/host_pin).glob('replay-*.wire'))
    assert job(a,bases[0],declined)['state'] == 'declined'

    value = api(a,bases[0],'status')['defaults']
    link = api(a,bases[0],'invite',fingerprint=guest_pin,confirmed=True,terms=value)['link']
    bad = b.request.post(bases[1]+'/api/lend', data={'action':'join','link':link,
                         'fingerprint':guest_pin,'confirmed':True})
    assert bad.status == 409 and bad.json()['error'] == 'WRONG_FINGERPRINT'
    encoded = link.removeprefix('vool-lend://invite/')
    payload = json.loads(base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4)))
    payload['terms']['auto'] = True
    payload['digest'] = hashlib.sha256(json.dumps(payload['terms'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
    link = 'vool-lend://invite/'+base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')
    changed = api(b,bases[1],'join',link=link,fingerprint=host_pin,confirmed=True)['invite']
    api(a,bases[0],'poll')
    assert send(changed,b'Never sent').json()['error'] == 'HOST_NOT_CONFIRMED'

    key = offer(expires=int(time.time()*1000)+2000)
    expiring = send(key,b'Expires before the host approves').json()['job']
    api(a,bases[0],'poll')
    time.sleep(2.1)
    api(a,bases[0],'poll')
    api(b,bases[1],'poll')
    assert job(a,bases[0],expiring)['state'] == job(b,bases[1],expiring)['state'] == 'expired'
    assert job(a,bases[0],expiring)['receipt'] == job(b,bases[1],expiring)['receipt']

    key = offer(jobs=2)
    cancelled = [send(key,body).json()['job'] for body in (b'Queued first', b'Queued second')]
    api(a,bases[0],'poll')
    api(a,bases[0],'revoke',invite=key)
    api(b,bases[1],'poll')
    assert all(job(b,bases[1],ident)['state'] == 'revoked' for ident in cancelled)
    assert send(key,b'Refused new job').json()['error'] == 'REVOKED'

    key = offer(auto=True)
    failed = send(key,b'%PDF-1.7\nnot a real PDF').json()['job']
    api(a,bases[0],'poll')
    api(b,bases[1],'poll')
    settle(failed,'failed')

    from PIL import Image

    key = offer(auto=True, capability='contact_sheet', max_seconds=1, max_length=100)
    photo = io.BytesIO()
    Image.new('RGB',(4000,4000),'green').save(photo,format='PNG')
    archive = io.BytesIO()
    with zipfile.ZipFile(archive,'w') as zipped:
        for n in range(50):
            zipped.writestr(f'{n}.png',photo.getvalue())
    timed = send(key,archive.getvalue()).json()['job']
    api(a,bases[0],'poll')
    api(b,bases[1],'poll')
    settle(timed,'timeout')
    b.locator('.vl-root').first.get_by_role('button',name='Check for jobs',exact=True).click()
    expect(b.locator(f'[data-job="{timed}"] .vl-job-state')).to_have_text('The job ran out of time. No result was delivered.')
    b.locator(f'[data-job="{timed}"]').scroll_into_view_if_needed()
    b.screenshot(path=str(evidence/'red-served-timeout.png'),full_page=True)
    expect(b.locator(f'[data-job="{cancelled[0]}"] .vl-job-state')).to_have_text('The invitation was revoked.')
    assert not list((homes[0]/'lend').glob('worker-*'))

    # Use the actual Run and Revoke buttons while the confined worker is live.
    key = offer(capability='contact_sheet', max_seconds=3, max_length=100)
    live = send(key,archive.getvalue()).json()['job']
    api(a,bases[0],'poll')
    a.locator('.vl-root').first.get_by_role('button',name='Check for jobs',exact=True).click()
    a.locator(f'[data-job="{live}"]').get_by_role('button',name='Run',exact=True).click()
    deadline=time.monotonic()+5
    while job(a,bases[0],live)['state']!='running' and time.monotonic()<deadline:
        time.sleep(.02)
    assert job(a,bases[0],live)['state']=='running'
    start=time.monotonic()
    with a.expect_response(lambda response: response.url==bases[0]+'/api/lend'
            and response.request.method=='POST' and response.request.post_data_json.get('action')=='revoke',
            timeout=2000) as revoked:
        a.locator(f'[data-invite="{key}"]').get_by_role('button',name='Revoke invitation',exact=True).click()
    assert revoked.value.ok and revoked.value.json()['state']=='revoked'
    assert time.monotonic()-start < 2
    assert next(v for v in api(a,bases[0],'list')['invites'] if v['id']==key)['state']=='revoked'
    expect(a.locator(f'[data-job="{live}"] .vl-job-state')).to_have_text('The invitation was revoked.',timeout=15000)
    api(b,bases[1],'poll')
    assert job(b,bases[1],live)['state']=='revoked'
    a.locator(f'[data-job="{live}"]').scroll_into_view_if_needed()
    a.screenshot(path=str(evidence/'blue-revoke-running-job.png'),full_page=True)

    # Interrupt a real encrypted result between chunks. The guest server is
    # stopped only by its recorded process object, so receipt ordering is fixed.
    def pause_guest():
        old=processes[1]
        old.terminate()
        old.wait(timeout=10)
        record(events,action='stopped',kind='api-server',pid=old.pid,exit=old.returncode,
               reason='partial delivery proof')
        return old

    def resume_guest(old):
        with (evidence/('server-guest-partial-'+str(old.pid)+'.log')).open('w') as log:
            new=subprocess.Popen(old.args,cwd=Path.cwd(),env=old.lend_env,stdout=log,stderr=subprocess.STDOUT)
        new.lend_env=old.lend_env
        processes[1]=new
        record(events,action='started',kind='api-server',role='restarted-guest',pid=new.pid,
               port=int(bases[1].rsplit(':',1)[1]))
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            assert new.poll() is None
            try:
                with urllib.request.urlopen(bases[1]+'/healthz',timeout=.5) as response:
                    if response.status==200:
                        return
            except OSError:
                time.sleep(.1)
        raise AssertionError('guest restart did not become ready')

    key=offer(max_length=250000,expires=int(time.time()*1000)+180000)
    partial=send(key,secrets.token_hex(60000).encode()).json()['job']
    old=pause_guest()
    api(a,bases[0],'poll')
    assert api(a,bases[0],'run',job=partial)['job']['state']=='returning'
    frames=sorted((relay/guest_pin).glob('*.wire'))
    assert len(frames)>=4
    original=frames[-1]
    held=homes[1].parent/'withheld-result.wire'
    original.rename(held)
    resume_guest(old)
    api(b,bases[1],'poll')
    assert job(b,bases[1],partial)['state']=='receiving_result'
    refusal=b.request.post(bases[1]+'/api/lend',data={'action':'download','job':partial})
    assert refusal.status==409
    b.locator('.vl-root').first.get_by_role('button',name='Check for jobs',exact=True).click()
    expect(b.locator(f'[data-job="{partial}"] .vl-job-state')).to_have_text('Receiving result.')
    assert b.locator(f'[data-job="{partial}"]').get_by_role('button',name='Save result',exact=True).count()==0
    b.locator(f'[data-job="{partial}"]').scroll_into_view_if_needed()
    b.screenshot(path=str(evidence/'red-partial-result.png'),full_page=True)
    old=pause_guest()
    api(a,bases[0],'revoke',invite=key)
    api(a,bases[0],'poll')
    resume_guest(old)
    settle(partial,'revoked')
    assert job(a,bases[0],partial)['receipt']==job(b,bases[1],partial)['receipt']
    held.rename(original)
    api(b,bases[1],'poll')
    assert job(b,bases[1],partial)['state']=='revoked'
    assert not (homes[1]/'lend'/partial).exists()

    key = offer()
    host = processes[0]
    host.terminate()
    host.wait(timeout=10)
    record(events,action='stopped',kind='api-server',pid=host.pid,exit=host.returncode,reason='offline proof')
    offline = send(key,b'The host is now really stopped').json()['job']
    b.locator('.vl-root').first.get_by_role('button',name='Check for jobs',exact=True).click()
    expect(b.locator(f'[data-job="{offline}"] .vl-job-state')).to_have_text(
        'The lending computer is asleep or unavailable. Check again later.',timeout=45000)
    expect(b.locator('.vl-root').first.get_by_role('button',name='Check for jobs',exact=True)).to_be_enabled()
    b.locator(f'[data-job="{offline}"]').scroll_into_view_if_needed()
    b.screenshot(path=str(evidence/'red-served-host-offline.png'),full_page=True)
    assert not re.search(r'lend\.[a-z_]+',b.locator('.vl-root').first.inner_text())


def test_restart_erases_interrupted_worker_and_does_not_run_twice(served):
    import hashlib
    import io
    import secrets
    import zipfile
    from concurrent.futures import ThreadPoolExecutor

    import httpx
    from PIL import Image

    from core.lend.worker import _same_process

    bases, homes, relay, evidence, events, processes = served

    def api(index, action, *, door='lend', **values):
        response=httpx.post(bases[index]+'/api/'+door,json={'action':action,**values},timeout=15,trust_env=False)
        assert response.is_success,response.text
        return response.json()

    for n in (0,1):
        api(n,'settings',door='drop',enabled=True,relay=str(relay),allow_digest=False)
        api(n,'settings',enabled=True)
    host_pin=api(0,'identity',door='drop')['fingerprint']
    guest_pin=api(1,'identity',door='drop')['fingerprint']
    terms=api(0,'status')['defaults']
    terms.update(capability='contact_sheet',max_length=100,max_seconds=3)
    link=api(0,'invite',fingerprint=guest_pin,confirmed=True,terms=terms)['link']
    invite=api(1,'join',link=link,fingerprint=host_pin,confirmed=True)['invite']
    deadline=time.monotonic()+10
    while api(1,'poll')['invites'][0]['state']!='ready' and time.monotonic()<deadline:
        time.sleep(.1)
    image=io.BytesIO()
    Image.new('RGB',(4000,4000),'green').save(image,format='PNG')
    content=io.BytesIO()
    with zipfile.ZipFile(content,'w') as archive:
        for n in range(50):
            archive.writestr(f'{n}.png',image.getvalue())
    data=content.getvalue()
    response=httpx.post(bases[1]+'/api/lend/send',content=data,headers={
        'Content-Type':'application/octet-stream','X-Lend-Invite':invite,'X-Lend-Name':'photos.zip',
        'X-Lend-Job':secrets.token_hex(16),'X-Lend-Size':str(len(data)),
        'X-Lend-SHA256':hashlib.sha256(data).hexdigest()},timeout=30,trust_env=False)
    assert response.is_success,response.text
    job=response.json()['job']
    deadline=time.monotonic()+10
    pending=[]
    while time.monotonic()<deadline:
        pending=api(0,'list')['jobs']
        if pending and pending[0]['state']=='pending':
            break
        time.sleep(.1)
    assert pending and pending[0]['state']=='pending'
    with ThreadPoolExecutor(max_workers=1) as pool:
        running=pool.submit(httpx.post,bases[0]+'/api/lend',json={'action':'run','job':job},timeout=15,trust_env=False)
        deadline=time.monotonic()+10
        journal=None
        while time.monotonic()<deadline:
            candidates=list((homes[0]/'lend/supervision').glob('*.json'))
            if candidates:
                current=json.loads(candidates[0].read_text())
                if current.get('pid') and (homes[0]/'lend'/current['directory']/'start.ok').exists():
                    journal=current
                    break
            time.sleep(.01)
        assert journal is not None
        record(events,action='started',kind='lent-worker',pid=journal['pid'],created=journal['created'])
        old=processes[0]
        old.kill()  # This fixture's recorded server, deliberately interrupted.
        old.wait(timeout=10)
        record(events,action='stopped',kind='api-server',pid=old.pid,exit=old.returncode,reason='restart recovery proof')
        try:
            running.result(timeout=10)
        except httpx.HTTPError:
            pass
    with (evidence/'server-restarted.log').open('w') as log:
        new=subprocess.Popen(old.args,cwd=Path.cwd(),env=old.lend_env,stdout=log,stderr=subprocess.STDOUT)
    new.lend_env=old.lend_env
    processes[0]=new
    record(events,action='started',kind='api-server',role='restarted-host',pid=new.pid,
           port=int(bases[0].rsplit(':',1)[1]))
    deadline=time.monotonic()+45
    while time.monotonic()<deadline:
        assert new.poll() is None
        try:
            state=api(0,'list')
            if state['jobs'][0]['state']=='failed' and not list((homes[0]/'lend').glob('worker-*')):
                break
        except httpx.HTTPError:
            pass
        time.sleep(.2)
    assert state['jobs'][0]['state']=='failed'
    assert not list((homes[0]/'lend').glob('worker-*'))
    assert not list((homes[0]/'lend/supervision').glob('*.json'))
    assert not _same_process(journal['pid'],journal['created'])
    record(events,action='stopped',kind='lent-worker',pid=journal['pid'],created=journal['created'],reason='recovered')
    guest=api(1,'poll')['jobs'][0]
    assert guest['state']=='failed' and guest['receipt']==state['jobs'][0]['receipt']
    assert guest['receipt']['seconds'] is None
    assert state['invites'][0]['used']==1
    again=httpx.post(bases[0]+'/api/lend',json={'action':'run','job':job},timeout=10,trust_env=False)
    assert again.status_code==409
