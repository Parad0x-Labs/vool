"""Availability residuals use real HTTP, parsers, stores and bounded local sockets."""
import base64
import json
import socket
import threading
import time
from http.client import HTTPConnection

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from nacl.public import Box, PublicKey
from nacl.signing import SigningKey

from core.mobile_companion.sealing import VERSION
from tests.mobile.test_phone_approval_key import evidence
from tests.mobile.test_phone_listener_served import desktop as desktop
from tests.mobile.test_phone_listener_served import enable, http
from tests.mobile.test_sealed_transport import canonical, opened, pair


def claim_request(desktop, approval=None):
    from core.mobile_companion.sealing import seal

    base, runtime, _ = desktop
    phone = 'http://127.0.0.1:' + str(enable(runtime)['port'])
    status, start = http(base, '/api/mobile/pairing/start', {})
    assert status == 200
    status, challenge = http(phone, '/api/mobile/pairing/challenge?pairing_id=' + start['pairing_id'])
    assert status == 200
    key = SigningKey.generate()
    curve = key.to_curve25519_private_key()
    shared = Box(curve, PublicKey(bytes.fromhex(challenge['sealing']['binding']['public_key']))).shared_key()
    proof = {'kind': 'vool.mobile.pairing.proof.v1', 'pairing_id': start['pairing_id'], 'code': start['code'],
        'challenge': challenge['challenge'], 'device_public_key': key.verify_key.encode().hex(),
        'expires_at': challenge['expires_at_epoch']}
    body = {'pairing_id': start['pairing_id'], 'code': start['code'], 'device_name': 'Availability phone',
        'platform': 'ios', 'device_public_key': proof['device_public_key'], 'approval_key': approval,
        'device_proof': {'algorithm': 'ed25519', 'signature': key.sign(canonical(proof)).signature.hex()}}

    def send(changes=None):
        wire = {**seal({'kind': VERSION + ':claim', 'body': {**body, **(changes or {})}}, shared, 'request'),
                'public_key': curve.public_key.encode().hex()}
        status, response = http(phone, '/api/mobile/pairing/claim', wire)
        return status, opened(response, shared, wire)

    return body, send


def test_pairing_session_precedes_real_parser_and_parser_releases_dispatch_lock(desktop, monkeypatch):
    from core.mobile_companion import approval_key
    from core.web.api.mobile_companion_api import mobile_companion_service

    service = mobile_companion_service()
    original = approval_key.record
    calls = []

    def observed(*args, **kwargs):
        lock_available = []
        def probe():
            available = service.dispatch_lock.acquire(blocking=False)
            lock_available.append(available)
            if available:
                service.dispatch_lock.release()
        thread = threading.Thread(target=probe)
        thread.start()
        thread.join(2)
        assert lock_available == [True], 'certificate verification held the shared action lock'
        calls.append(kwargs['pairing_id'])
        return original(*args, **kwargs)

    monkeypatch.setattr(approval_key, 'record', observed)
    body, send = claim_request(desktop, evidence(ec.generate_private_key(ec.SECP256R1())))
    assert send({'pairing_id': 'unknown-pairing'})[0] == 404
    assert calls == []
    assert send()[0] == 200
    assert calls == [body['pairing_id']]
    assert send()[0] == 409
    assert calls == [body['pairing_id']]
    body, expired = claim_request(desktop)
    service.store.update_pairing(body['pairing_id'], expires_at_epoch=time.time() - 1)
    assert expired()[0] == 410
    assert calls == [calls[0]]


def test_oversize_approval_key_fails_before_decode(desktop, monkeypatch):
    from core.mobile_companion import approval_key

    decoded = []
    original = base64.b64decode
    def observed(value, **kwargs):
        decoded.append(len(value))
        return original(value, **kwargs)
    monkeypatch.setattr(approval_key.base64, 'b64decode', observed)
    result = approval_key.record({'platform': 'ios', 'encoding': 'spki', 'public_key': 'a' * 100000},
                                 pairing_id='p', challenge='c', device_public_key='a' * 64)
    assert result['state'] == 'invalid_use_computer' and decoded == []


def test_settings_freeze_records_failed_legacy_mirror(desktop):
    from core.wallet_spend_policy_store import policy_path

    path = policy_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{invalid-policy')
    assert http(desktop[0], '/api/mobile/settings', {'operation': 'freeze'})[0] == 200
    rows = http(desktop[0], '/api/mobile/receipts')[1]['receipts']
    event = next(row for row in rows if row['kind'] == 'payments.freeze_changed')
    assert event['detail']['legacy_mirror'] == 'not_updated'


def test_stop_flushes_refusal_counts_without_a_receipt_read(desktop):
    from core.web.api.mobile_companion_api import mobile_companion_service

    _base, runtime, _home = desktop
    port = enable(runtime)['port']
    service = mobile_companion_service()
    for _ in range(70):
        assert http('http://127.0.0.1:' + str(port), '/api/mobile/companion', {})[0] == 400
    runtime.phone_listener.close()
    # Read the actual persisted journal, bypassing the endpoint that flushes on read.
    rows = [json.loads(line) for line in service.store._receipts_path.read_text().splitlines()]
    assert sum(row['detail'].get('count', 1) for row in rows if row['kind'] == 'security.plaintext_refused') == 70


@pytest.mark.parametrize('storage_failure', ['write', 'read'])
def test_lost_computer_closes_bot_workers_and_receipts_canonical_freeze_failure(desktop, monkeypatch, storage_failure):
    from core.wallet import limits
    from core.web.api.mobile_companion_api import mobile_companion_service
    from tests.channels.test_managed_chat_apps import FakeChatHost, wait_for
    from tests.mobile.test_phone_notifications import call

    base, runtime, _ = desktop
    host = FakeChatHost()
    runtime.phone_bot_manager = None
    monkeypatch.setenv('VOOL_TEST_BOT_TELEGRAM_URL', host.url)
    # A real manager uses runtime provider_origins derived at construction.
    from core.mobile_companion import bot_runtime
    manager = bot_runtime.manager_for(runtime)
    manager.provider_origins['telegram'] = host.url
    try:
        import secrets
        phone = pair(desktop, activate_chat=False)
        status, linked = http(base, '/api/mobile/settings', {'operation': 'bot_connect', 'platform': 'telegram',
                                                           'token': secrets.token_urlsafe(40)})
        assert status == 200, linked
        worker = next(iter(manager.workers.values()))
        from core.wallet.store import connection

        # A real SQLite write refusal exercises the canonical freeze, rather
        # than replacing the wallet gate with a synthetic success or failure.
        with connection() as conn:
            if storage_failure == 'write':
                conn.execute("""CREATE TRIGGER fixture_freeze_write_refusal BEFORE INSERT ON wallet_controls
                    WHEN NEW.key='spend_frozen' BEGIN SELECT RAISE(FAIL,'fixture write refused'); END""")
            else:
                conn.execute('ALTER TABLE wallet_controls RENAME TO fixture_controls')
                conn.execute('CREATE VIEW wallet_controls AS SELECT key, fixture_unreadable() AS value, updated_at FROM fixture_controls')
        status, result = call(*phone, 'devices.lost_computer')
        assert status == 503 and result.get('error') == 'recovery_incomplete', result
        if storage_failure == 'write':
            assert limits.is_frozen() is False
        else:
            import sqlite3
            with pytest.raises(sqlite3.DatabaseError):
                limits.is_frozen()
        assert worker.stop_event.is_set()
        wait_for(lambda: not worker.thread.is_alive())
        assert all(row['status'] == 'revoked' for row in mobile_companion_service().store.devices().values())
        rows = http(base, '/api/mobile/receipts')[1]['receipts']
        failed = next(row for row in rows if row['kind'] == 'payments.freeze_failed')
        assert failed['detail']['frozen'] is (False if storage_failure == 'write' else None)
        assert failed['detail']['failed'] == ['wallet_freeze']
    finally:
        host.close()


def test_non_pin_wallet_fault_gets_a_friendly_brake_reply(desktop):
    from core import vool_agent_brake
    from core.wallet.errors import WalletFault

    def fail(*_args, **_kwargs):
        raise WalletFault('wallet_refused', context={'reason': 'different_failure'})
    result = vool_agent_brake.maybe_handle_agent_brake('/startx402', 'availability',
                                                     wallet_fn=fail, consent_fn=lambda _: True)
    assert result and 'could not change the freeze state' in json.dumps(result).lower()


@pytest.mark.parametrize('pause_during_churn', [0.0, 3.2])
def test_one_megabyte_upload_survives_churning_header_senders(desktop, monkeypatch, pause_during_churn):
    from core.mobile_companion import listener

    # Two real local interfaces make this test portable to macOS, where extra
    # 127/8 aliases need an administrator. The same admission algorithm runs
    # at four slots so one dripper source can fill it; production has twelve.
    addresses = listener.network_addresses()
    assert addresses, 'a second local IPv4 interface is required for the served load proof'
    address = addresses[0]
    monkeypatch.setattr(listener._Server, 'SLOTS', 4)
    value = listener.listener_for(desktop[1]).configure(enabled=True, address=address)
    port = value['port']
    from http.client import HTTPResponse

    from tests.mobile.test_phone_notifications import call
    from tests.mobile.test_sealed_transport import action
    from tests.mobile.test_zz_secprobe import _pair
    phone = _pair(desktop, value, rights={'attachments': True})
    status, prepared = call(*phone, 'chats.prepare')
    assert status == 200, prepared
    content = b'x' * 1_000_000
    wire = action(phone[1], phone[2], phone[3], 'attachments.upload', {
        'chat_id': prepared['result']['chat_id'], 'filename': 'large-upload.txt', 'kind': 'file',
        'media_type': 'text/plain', 'content_b64': base64.b64encode(content).decode()})
    stop = threading.Event()
    def churn():
        held = []
        try:
            while not stop.is_set():
                sock = None
                try:
                    sock = socket.create_connection((address, port), source_address=('127.0.0.1', 0), timeout=1)
                    sock.sendall(b'POST /api/mobile/companion HTTP/1.1\r\nX-Drip: ')
                    held.append(sock)
                except OSError:
                    if sock is not None:
                        sock.close()
                if len(held) > 4:
                    held.pop(0).close()
                time.sleep(.01)
        finally:
            for sock in held:
                sock.close()
    upload = socket.create_connection((address, port), source_address=(address, 0), timeout=5)
    raw = json.dumps(wire).encode()
    upload.sendall(('POST /api/mobile/companion HTTP/1.1\r\nHost: '+address+'\r\nContent-Length: '+str(len(raw))+'\r\n\r\n').encode())
    upload.sendall(raw[:65536])
    time.sleep(.1)
    thread = threading.Thread(target=churn, daemon=True)
    thread.start()
    try:
        time.sleep(pause_during_churn)
        for offset in range(65536, len(raw), 16384):
            upload.sendall(raw[offset:offset+16384])
            time.sleep(.015)
        response = HTTPResponse(upload)
        response.begin()
        body = json.loads(response.read())
        result = opened(body, phone[2], wire)
        assert response.status == 201, result
        assert result['result']['attachment']['size_bytes'] == len(content)
        response.close()
        conn = HTTPConnection(address, port, source_address=(address, 0), timeout=5)
        try:
            conn.request('GET', '/api/mobile/info')
            response = conn.getresponse()
            assert response.status == 200
            response.read()
        finally:
            conn.close()
    finally:
        stop.set()
        thread.join(3)
        upload.close()
        assert not thread.is_alive()


@pytest.mark.parametrize('exponent', [3, 65537])
def test_attestation_issuer_work_is_bounded_and_normal_rsa_still_records(exponent):
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    from core.mobile_companion.approval_key import record

    issuer = rsa.generate_private_key(public_exponent=exponent, key_size=2048)
    leaf = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Availability fixture issuer')])
    now = datetime.now(timezone.utc)
    root = x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(issuer.public_key()).serial_number(1).not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=1)).sign(issuer, hashes.SHA256())
    cert = x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(leaf.public_key()).serial_number(2).not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=1)).add_extension(x509.UnrecognizedExtension(x509.ObjectIdentifier('1.3.6.1.4.1.11129.2.1.17'), b'\x30\x00'), critical=False).sign(issuer, hashes.SHA256())
    value = {'platform': 'android', 'encoding': 'spki',
             'public_key': base64.b64encode(leaf.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).decode(),
             'chain': [base64.b64encode(c.public_bytes(serialization.Encoding.DER)).decode() for c in (cert, root)]}
    result = record(value, pairing_id='fixture-pair', challenge='fixture', device_public_key='a'*64)
    assert result['state'] == ('invalid_use_computer' if exponent == 3 else 'unproven_use_computer')
    assert result['build_proven'] is False
