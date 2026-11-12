"""Real outbound PC worker and self-hosted relay; shared phone transport code."""
import json
import secrets
import subprocess
import threading
from pathlib import Path

import pytest
from nacl.signing import VerifyKey

from tests.mobile.test_phone_listener_served import desktop as desktop
from tests.mobile.test_phone_listener_served import http
from tests.mobile.test_sealed_transport import action, opened, pair


@pytest.fixture
def relay():
    from relay.mobile_relay.server import RelayServer

    room, token = secrets.token_hex(24), secrets.token_urlsafe(32)
    server = RelayServer(('127.0.0.1',0), {room:token}, response_timeout=2)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval':0.05}, daemon=True)
    thread.start()
    try: yield server, f'http://127.0.0.1:{server.server_port}', room, token
    finally:
        server.shutdown(); server.server_close(); thread.join(3)
        assert not thread.is_alive()


def connect(desktop, relay):
    base, runtime, _ = desktop
    server, endpoint, room, token = relay
    status, answer = http(base, '/api/mobile/settings', {'operation':'relay','endpoint':endpoint,'room':room,'token':token})
    assert status == 200, answer
    assert answer['enabled'] and not answer['running']
    assert runtime.phone_listener.server is None  # outbound relay needs no inbound socket
    assert token not in json.dumps(answer)
    return endpoint+'/rooms/'+room


def test_relay_shared_phone_ciphertext_only_drop_replay_tamper_and_off(desktop, relay):
    from core.web.api.mobile_companion_api import mobile_companion_service
    phone = pair(desktop)
    url = connect(desktop, relay)
    _, key, _, claim = phone
    credentials = {'baseUrl':url,'deviceId':claim['device_id'],'grantId':claim['grant_id'],'grantSecret':claim['grant_secret'],
        'deviceSeedHex':key.encode().hex(),'desktopFingerprint':claim['desktop_fingerprint'],'pairedAt':'2026-10-20T00:00:00Z',
        'protocol':'vool.mobile.sealed.v2','desktopEncryptionKey':VerifyKey(bytes.fromhex(claim['desktop_public_key'])).to_curve25519_public_key().encode().hex()}
    result = subprocess.run(['node','e2e/relay-journey.mjs'], cwd=Path(__file__).resolve().parents[2]/'mobile',
        input=json.dumps({'credentials':credentials}),capture_output=True,text=True,timeout=25)
    assert result.returncode == 0, result.stdout+result.stderr
    assert json.loads(result.stdout) == {'requests':2,'replay':409,'tamper':503}
    receipts = mobile_companion_service().store.receipts()[0]
    assert any(row['kind']=='security.seal_refused' for row in receipts)
    assert any(row['kind']=='security.replay_detected' for row in receipts)
    worker = desktop[1].phone_listener.relay
    assert http(desktop[0],'/api/mobile/settings',{'enabled':False})[0] == 200
    assert worker.stop_event.is_set() and not worker.thread.is_alive()
    assert http(url,'/api/mobile/companion',action(key,phone[2],claim))[0] == 503


def test_relay_has_no_pairing_owner_or_plaintext_door_and_pc_token_is_separate(relay):
    _, endpoint, room, _token = relay
    url=endpoint+'/rooms/'+room
    for path in ('/pc/poll','/pc/answer','/api/chat','/api/mobile/pairing/claim','/api/mobile/settings'):
        assert http(url,path,{})[0] == 403
    assert http(url,'/api/mobile/companion',{'action':'devices.me','params':{'message':'secret'}})[0] == 400


def test_old_phone_refuses_desktop_key_recovery_and_new_phone_key_is_new_device(desktop, relay):
    from core.web.api.mobile_companion_api import _state_dir, mobile_companion_service, reset_mobile_companion_service
    first=pair(desktop); second=pair(desktop)
    assert first[3]['device_id'] != second[3]['device_id']
    assert first[1].verify_key != second[1].verify_key
    url=connect(desktop,relay)
    # Recover the test desktop's companion authority using its actual key loader.
    (_state_dir()/'device_authority_key.json').unlink()
    reset_mobile_companion_service()
    assert mobile_companion_service().desktop_fingerprint() != first[3]['desktop_fingerprint']
    wire=action(first[1],first[2],first[3])
    assert http(url,'/api/mobile/companion',wire)[0] == 503


@pytest.mark.parametrize('url,room', [('http://example.invalid','a'*48),('https://user:secret@example.invalid','a'*48),('https://example.invalid/path','a'*48),('https://example.invalid','guess')])
def test_relay_configuration_never_accepts_insecure_or_credential_urls(desktop,url,room):
    assert http(desktop[0],'/api/mobile/settings',{'operation':'relay','endpoint':url,'room':room,'token':'x'*32})[0] == 400
    assert getattr(desktop[1], 'phone_listener', None) is None


def test_owner_relay_switch_in_real_settings_chromium(desktop,relay,tmp_path):
    import os

    from playwright.sync_api import sync_playwright

    from core.credential_store import get_credential
    pair(desktop)
    _,endpoint,room,token=relay
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(executable_path=os.environ['VOOL_TEST_CHROMIUM'])
        try:
            page=browser.new_page(viewport={'width':1280,'height':1000})
            page.goto(desktop[0]+'/settings#phone'); page.locator('[data-group="phone"]').click()
            page.get_by_text('Connect away from home (advanced)',exact=True).click()
            page.get_by_label('Server address',exact=True).fill(endpoint)
            page.get_by_label('Room code',exact=True).fill(room)
            page.get_by_label('Computer key',exact=True).fill(token)
            page.get_by_role('button',name='Connect through my server',exact=True).click()
            page.wait_for_function("document.querySelector('.phone-relay')?.textContent.includes('Phone connection address')")
            assert desktop[1].phone_listener.server is None
            assert get_credential('mobile.relay.token')==token
            assert token not in page.locator('.phone-settings-panel').inner_text()
            page.get_by_text('Connect away from home (advanced)',exact=True).click()
            assert endpoint+'/rooms/'+room in page.locator('.phone-relay').inner_text()
            shots=Path(os.environ.get('VOOL_PHONE_SHOTS_DIR') or tmp_path); shots.mkdir(exist_ok=True)
            page.screenshot(path=str(shots/'phone-relay-settings.png'),full_page=True)
            page.get_by_role('switch',name='Use VOOL from my phone').click()
            page.wait_for_function("document.querySelector('[aria-label=\"Use VOOL from my phone\"]').getAttribute('aria-checked')==='false'")
            assert desktop[1].phone_listener.relay is None
        finally: browser.close()


@pytest.mark.parametrize('desktop',['full'],indirect=True)
def test_inflight_queue_and_new_message_survive_home_to_relay_switch(desktop,relay):
    from tests.mobile.test_phone_chat_delivery import ledger_row
    from tests.mobile.test_phone_final_journey import credentials
    phone=pair(desktop,rights={'history':True})
    _,endpoint,room,token=relay
    result=subprocess.run(['node','e2e/route-switch-journey.mjs'],cwd=Path(__file__).resolve().parents[2]/'mobile',
        input=json.dumps({'credentials':credentials(phone),'desktop':desktop[0],
            'relay':{'endpoint':endpoint,'room':room,'token':token}}),capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
    proof=json.loads(result.stdout);assert proof['packets']>=2 and proof['remaining']==0
    for turn_id in proof['turn_ids']:
        assert ledger_row(desktop,'SELECT COUNT(*) AS count FROM invocation_requests WHERE external_value=?',(turn_id,))['count']==1
