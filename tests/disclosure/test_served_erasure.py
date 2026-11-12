from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests import _reader_served_rig as rig
from tests.disclosure.test_served_preview import _json
from tests.disclosure.test_served_rules import recording_daemon, reviewed_chat


@pytest.mark.parametrize('operation', ['forget', 'delete'])
def test_existing_preview_closes_after_canonical_chat_erasure(tmp_path, operation):
    with recording_daemon(tmp_path) as (daemon, provider, raw):
        session = rig.canonical_session('disclosure-erasure-' + operation)
        remembered = daemon.chat('Remember Cobalt Workshop repairs clocks.', session_id=session, turn_id='seed-memory')
        assert 'remember' in remembered.get('message', {}).get('content', '').lower(), remembered
        attachment = daemon.upload(data=b'Cobalt Workshop repairs clocks.', name='workshop.txt',
                                   declared_type='text/plain', session_id=session)
        ident = attachment.get('attachment_id') or attachment.get('id') or attachment.get('attachment', {}).get('id')
        assert ident, attachment
        response, observed = reviewed_chat(daemon, 'Summarize the attached workshop document.',
                                           session_id=session, attachments=[ident], turn_id='before-' + operation)
        assert raw and observed, response
        before = _json(daemon.base_url + '/api/disclosure/pending')['requests']
        ids = {p['id'] for p in before if p['chat_id'] == session}
        assert ids and any('Cobalt Workshop' in p['body'] for p in before if p['id'] in ids)
        raw.clear()
        if operation == 'forget':
            erased = daemon.chat('Forget Cobalt Workshop.', session_id=session, turn_id='erase-preview')
            text = erased.get('message', {}).get('content', '').lower()
            assert 'could not be verified' not in text and 'please retry the forget' not in text, erased
            assert not text.startswith('nothing was removed'), erased
        else:
            erased = _json(daemon.base_url + '/api/chat/session', {'session_id': session, 'delete': True})
            assert erased.get('lifecycle_state') == 'deleted', erased
        after = _json(daemon.base_url + '/api/disclosure/pending')['requests']
        assert not ids.intersection(p['id'] for p in after), after
        assert not any('Cobalt Workshop' in p['body'] for p in after), after
        assert raw == [], {'erased': erased, 'received': raw}
        settings = _json(daemon.base_url + '/api/disclosure/settings')
        assert settings['keep_copies'] is False and settings['copies_available'] is False
        if os.environ.get('VOOL_DISCLOSURE_EVIDENCE_ROOT'):
            tag = os.environ.get('VOOL_DISCLOSURE_RUN_TAG', 'run')
            Path(os.environ['VOOL_DISCLOSURE_EVIDENCE_ROOT'], f'served-erasure-{operation}-{tag}.json').write_text(
                json.dumps({'before': before, 'erased': erased, 'after': after, 'settings': settings}, indent=2))
