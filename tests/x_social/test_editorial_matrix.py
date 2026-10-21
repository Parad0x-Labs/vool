from __future__ import annotations

import json

import pytest

from tests._credential_intelligence_support import isolated_home, vault_home
from tests.x_social.test_amendment_served import served


@pytest.mark.parametrize('served', ['', 'editorial'], indirect=True, ids=['not-installed', 'installed'])
def test_supplied_source_native_xdraft_preserves_editorial_validation(served):
    draft = {'mode': 'post', 'audience': 'office workers', 'purpose': 'show a simple tea break',
             'thesis': 'tea fits a short break', 'title': '', 'hook': '',
             'posts': ['Our tea brews in two minutes.'], 'sections': [],
             'media_notes': [], 'links': [], 'cta': ''}
    envelope = '===XDRAFT===\n' + json.dumps(draft) + '\n===XDRAFT-END==='
    reply = served.chat('Write an X post from these operator-supplied notes for social-manager: Our tea brews in two minutes.',
        [{'final': envelope}], 'supplied-editorial', session='supplied-editorial')
    events = served.daemon.session_events(reply['vool_session_id'])
    validated = [event for event in events if event.get('event_type') == 'x_editorial_validation']
    assert validated and validated[-1]['clean'] is True, events[-8:]
    assert reply['choices'][0]['message']['content'].strip() == 'Our tea brews in two minutes.'
