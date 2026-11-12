"""Per-use ability decisions on the actual sealed listener; no recognizer substitute."""
import base64
import struct

import pytest

from tests.mobile.test_phone_listener_served import desktop as desktop
from tests.mobile.test_phone_listener_served import http
from tests.mobile.test_phone_notifications import call
from tests.mobile.test_sealed_transport import pair


@pytest.mark.parametrize('ability', ['camera', 'photos', 'microphone', 'location'])
def test_ability_defaults_off_pc_enable_and_pause_take_effect(desktop, ability):
    phone = pair(desktop)
    assert call(*phone, 'abilities.check', {'ability': ability})[0] == 403
    rules = {ability: True, 'attachments': True}
    assert http(desktop[0], '/api/mobile/settings', {'operation': 'device_rules', 'device_id': phone[3]['device_id'], 'rules': rules})[0] == 200
    assert call(*phone, 'abilities.check', {'ability': ability})[1]['result']['allowed'] is True
    assert call(*phone, 'devices.switch_off', {'rights': [ability]})[0] == 200
    assert call(*phone, 'abilities.check', {'ability': ability})[0] == 403


def test_photo_requires_attachment_right_and_cannot_widen_it(desktop):
    phone = pair(desktop, rights={'camera': True})
    assert call(*phone, 'abilities.check', {'ability': 'camera', 'attachments': True})[0] == 403
    assert call(*phone, 'abilities.check', {'ability': 'contacts'})[0] == 400


def test_location_is_bounded_draft_only_and_revoke_refuses(desktop):
    phone = pair(desktop, rights={'location': True})
    for latitude, longitude in [('91', '0'), ('0', '181'), ('nan', '0'), (True, '0')]:
        assert call(*phone, 'abilities.location', {'latitude': latitude, 'longitude': longitude})[0] == 400
    status, value = call(*phone, 'abilities.location', {'latitude': '54.687157', 'longitude': '25.279652'})
    assert status == 200 and value['result'] == {'text': '54.687157, 25.279652', 'draft_only': True}
    assert call(*phone, 'devices.unpair')[0] == 200
    assert call(*phone, 'abilities.location', {'latitude': '0', 'longitude': '0'})[0] == 401


def test_dictation_uses_real_availability_and_never_dispatches_a_turn(desktop):
    from core import dictation
    phone = pair(desktop)
    assert call(*phone, 'abilities.dictate', {'content_b64': 'AAAA'})[0] == 403
    assert http(desktop[0], '/api/mobile/settings', {'operation': 'device_rules', 'device_id': phone[3]['device_id'], 'rules': {'microphone': True}})[0] == 200
    for value in ['', 'bad base64!']:
        assert call(*phone, 'abilities.dictate', {'content_b64': value})[0] == 400
    if dictation.availability()['available']:
        pytest.skip('An authorized real speech recording needs the owner; no synthetic recognizer success.')
    pcm = b'\x00\x00' * 8000
    wav = b'RIFF' + struct.pack('<I', 36+len(pcm)) + b'WAVEfmt ' + struct.pack('<IHHIIHH',16,1,1,8000,16000,2,16) + b'data' + struct.pack('<I', len(pcm)) + pcm
    status, result = call(*phone, 'abilities.dictate', {'content_b64': base64.b64encode(wav).decode(), 'media_type': 'audio/wav'})
    assert status == 503 and result['result']['error'] == dictation.availability()['error']
    assert not result['result'].get('text')
