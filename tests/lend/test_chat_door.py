"""GLM recheck R9 (D01-D04, L01-L03): asking chat to send something to, or hand a job to, another of the
owner's computers names the feature, says nothing was sent, and says where to turn it on.

Measured on qwen3:8b with Drop and lending off: "send the invoice in my downloads to my laptop" got
"Nothing is running right now", "drop a quick note on my phone saying buy oat milk" got a question
about the phone's brand, and "hand the whole thing to my other machine and dont ask me any questions"
got "Got it. I'll pass the whole thing to your other machine". The door runs before the model and
needs no OS feature, so these tests run the same on every platform.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._credential_intelligence_support import isolated_home

DROP_ASKS = ('send the invoice in my downloads to my laptop',
             'can you push that boarding pass pdf over to my other computer',
             'drop a quick note on my phone saying buy oat milk',
             'send the bank csv from downloads to my laptop')
LEND_ASKS = ('this renaming job is taking forever, can my other VOOL on the mini pc do it instead',
             'let the big machine downstairs finish this, its way faster than this laptop',
             'hand the whole thing to my other machine and dont ask me any questions')
NOT_OURS = ('email the invoice in my downloads to my accountant', 'send the invoice to sam@example.com',
            'send me a summary of this page', 'can you send a reminder tomorrow at 9',
            'how do i copy files to my other computer?', 'what is the best way to move photos to my laptop',
            'my other computer is a mac, which charger fits it', 'is my laptop fast enough for this model',
            'put this on my calendar', 'send a text message to my phone saying hi',
            "don't send it to my laptop yet", 'send my regards to the team', 'write a note saying buy oat milk',
            'move the meeting to friday', 'the macbook, yes',
            # The Desktop folder on this computer is the file lane's, not another computer.
            'move the cat photo from Pictures to my Desktop?', 'put the report on the desktop')
ENGINEERING = ('relay', 'session', 'token', 'sha-256', 'digest', 'api', 'vool_drop', 'drop_disabled',
               'lend_disabled', 'posix', 'capability', 'nothing is running', "i'll pass", 'i will pass')
CATALOGS = Path(__file__).resolve().parents[2] / 'core' / 'i18n' / 'catalogs'


def _agent():
    from apps.vool_agent import VoolAgent

    return VoolAgent(backend_name='test-backend', device='channel-test', persona_id='default')


def _run(ask: str, agent=None, **extra) -> dict:
    agent = agent or _agent()
    return agent.run_once(ask, source_context={'surface': 'desktop', 'platform': 'api', **extra})


def _plain(text: str) -> None:
    lowered = text.lower()
    assert not [word for word in ENGINEERING if word in lowered], text


def _turn_on(drop: bool = True, lend: bool = False) -> None:
    from core.runtime_paths import active_config_home_dir

    config = active_config_home_dir()
    config.mkdir(parents=True, exist_ok=True)
    (config / 'drop.json').write_text(json.dumps({'enabled': drop, 'relay': '/nonexistent/shared',
                                                  'allow_digest': False}))
    (config / 'lend.json').write_text(json.dumps({'enabled': lend}))


@pytest.fixture
def drop_off(isolated_home, monkeypatch):
    monkeypatch.delenv('VOOL_DROP', raising=False)
    return isolated_home


@pytest.mark.parametrize('ask', [ask for ask in DROP_ASKS if 'phone' not in ask])
def test_a_send_to_my_other_computer_with_drop_off_says_so_and_where_to_turn_it_on(drop_off, ask):
    from core.drop import enabled

    assert not enabled()
    text = str(_run(ask).get('response') or '')
    assert ('In VOOL, sending a file or a note to another of your computers is called '
            'Send with Drop. It is off, so nothing was sent.') in text, text
    assert 'Settings > Backup & restore > Send with Drop' in text, text
    assert '“Enable Drop and make this VOOL folder private”' in text and 'Shared folder' in text, text
    assert '1. ' in text and '2. ' in text and '3. ' in text and 'Create invite' in text and 'Join' in text, text
    _plain(text)


@pytest.mark.parametrize('ask', LEND_ASKS)
def test_handing_a_job_to_another_machine_with_lending_off_says_so_and_where_to_turn_it_on(drop_off, ask):
    text = str(_run(ask).get('response') or '')
    assert text.startswith('In VOOL, handing a job to another of your computers is called Lend jobs to '
                           'friends. It is off, so nothing was handed over.'), text
    assert 'Settings > Backup & restore > Send with Drop' in text, text
    assert 'Settings > Backup & restore > Lend jobs to friends' in text, text
    assert 'Make a contact sheet from photos' in text and 'Borrow a job' in text, text
    _plain(text)


def test_the_confirmed_computer_in_the_next_turn_gets_the_same_answer(drop_off):
    agent = _agent()
    first = _run(DROP_ASKS[3], agent)
    second = _run('the macbook, yes', agent, session_id=first.get('session_id'))
    assert 'In VOOL, sending a file or a note' in str(second.get('response') or ''), second.get('response')


def test_on_but_unpaired_says_how_to_pair_and_on_and_paired_is_left_alone(drop_off, monkeypatch):
    from core.drop import chat_door

    _turn_on(drop=True, lend=True)
    monkeypatch.setattr(chat_door, '_drop_paired', lambda: False)
    monkeypatch.setattr(chat_door, '_lend_paired', lambda: False)
    assert chat_door.state_for('drop') == 'unpaired' and chat_door.state_for('lend') == 'unpaired'
    drop = chat_door.reply_text('drop', 'unpaired', 'en')
    assert drop.startswith('Send with Drop is on, but this computer is not paired with another one yet, so '
                           'nothing was sent.'), drop
    assert 'To pair, open Settings > Backup & restore > Send with Drop on both computers' in drop
    lend = chat_door.reply_text('lend', 'unpaired', 'en')
    assert 'nothing was handed over' in lend and 'Lend a job' in lend and 'Review invitation' in lend, lend
    _plain(drop)
    _plain(lend)

    class Agent:
        def _fast_path_result(self, **kwargs):
            return dict(kwargs)

    assert chat_door.reply_if_unavailable(Agent(), DROP_ASKS[0], session_id='s', source_context={}) is not None
    monkeypatch.setattr(chat_door, '_drop_paired', lambda: True)
    monkeypatch.setattr(chat_door, '_lend_paired', lambda: True)
    # A phone ask is answered whatever Drop's state (see the phone test below).
    for ask in [ask for ask in DROP_ASKS if 'phone' not in ask] + list(LEND_ASKS):
        assert chat_door.reply_if_unavailable(Agent(), ask, session_id='s', source_context={}) is None, ask
    # Pairing that cannot be read is left to the existing flow, never answered as unpaired.
    monkeypatch.setattr(chat_door, '_drop_paired', lambda: None)
    assert chat_door.state_for('drop') == ''


def test_lending_on_without_drop_is_still_off(drop_off):
    from core.drop import chat_door

    _turn_on(drop=False, lend=True)
    assert chat_door.state_for('lend') == 'off' and chat_door.state_for('drop') == 'off'


def test_the_door_does_not_take_other_asks():
    from core.drop.chat_door import kind_of_ask

    assert [kind_of_ask(ask) for ask in DROP_ASKS] == ['drop'] * 4
    assert [kind_of_ask(ask) for ask in LEND_ASKS] == ['lend'] * 3
    for ask in NOT_OURS:
        assert kind_of_ask(ask) == '', ask
    # A desktop computer is still a computer.
    assert kind_of_ask('send the bank csv to my desktop computer') == 'drop'


def test_an_email_ask_is_not_answered_by_the_door(drop_off):
    text = str(_run(NOT_OURS[0]).get('response') or '')
    assert 'Send with Drop' not in text and 'nothing was sent' not in text, text


def test_the_reply_speaks_the_app_language(drop_off):
    text = str(_run(DROP_ASKS[0], ui_locale='de').get('response') or '')
    assert 'Einstellungen > Sicherung & Wiederherstellung > Mit Drop senden' in text, text
    assert 'nichts gesendet' in text, text


def test_the_replies_are_in_all_17_catalogs():
    from core.drop.chat_door import KEYS
    from core.i18n.catalog import placeholders_of, source_catalog_sha256

    files = sorted(CATALOGS.glob('*.json'))
    assert len(files) == 17
    en = json.loads((CATALOGS / 'en.json').read_text())
    assert 'source_catalog_sha256' not in en
    for key in KEYS:
        source = en['messages'][key]
        for path in files:
            data = json.loads(path.read_text())
            text = data['messages'][key]
            assert placeholders_of(text) == placeholders_of(source), (path.stem, key)
            if path.stem != 'en':
                assert text != source, (path.stem, key)
                assert data['source_catalog_sha256'] == source_catalog_sha256(), path.stem


@pytest.mark.parametrize('ask, lead', [
    ('send the invoice in my downloads to my laptop', 'I have not sent the invoice in your downloads to your laptop.'),
    ('can you push that boarding pass pdf over to my other computer',
     'I have not sent that boarding pass pdf to your other computer.'),
    ('send the bank csv from downloads to my laptop', 'I have not sent the bank csv from downloads to your laptop.'),
])
def test_the_reply_names_the_file_and_the_computer_first(drop_off, ask, lead):
    """Recheck r9b D01: the reply never said which file it was about."""
    text = str(_run(ask).get('response') or '')
    assert text.startswith(lead + ' In VOOL, sending a file'), text


@pytest.mark.parametrize('on', [False, True])
def test_a_note_for_a_phone_says_drop_cannot_reach_a_phone(drop_off, monkeypatch, on):
    """Recheck r9b D03: Drop sends between computers; a phone gets a plain no, with no setup steps."""
    from core.drop import chat_door

    if on:
        _turn_on(drop=True)
        monkeypatch.setattr(chat_door, '_drop_on', lambda: True)
        monkeypatch.setattr(chat_door, '_drop_paired', lambda: True)
    text = str(_run('drop a quick note on my phone saying buy oat milk').get('response') or '')
    assert text == ('I have not sent a quick note to your phone. Send with Drop only works between your '
                    'computers, so it cannot send anything to a phone.'), text
    _plain(text)


@pytest.mark.parametrize('state', ['off', 'unpaired'])
def test_the_lend_reply_names_only_the_jobs_this_version_can_run(drop_off, state):
    """Measured in the served app on the v0.8 lend port, 2026-11-12: the reply offered summarising and
    translating a document with a local model, which this version cannot run on any computer."""
    from core.drop import chat_door

    text = chat_door.reply_text('lend', state, 'en')
    assert ('It can take these jobs: Make a contact sheet from photos; Turn a document into a clean PDF; '
            'Trim or convert a recording.') in text, text
    assert 'Summarise' not in text and 'Translate' not in text, text
    german = json.loads((CATALOGS / 'de.json').read_text())['messages']
    text = chat_door.reply_text('lend', state, 'de')
    assert german['lend.cap_contact_sheet'] in text and german['lend.cap_recording'] in text, text
    assert german['lend.cap_summary'] not in text and german['lend.cap_translation'] not in text, text


def test_the_jobs_the_reply_names_are_the_jobs_the_worker_can_run(monkeypatch):
    """The reply and the worker read one list: with confinement and the media tools present the worker
    runs exactly the jobs the reply names, and without confinement it runs none."""
    from core.lend import worker
    from core.lend.terms import CAPABILITIES, OFFERED

    monkeypatch.setattr(worker, 'confinement_available', lambda: True)
    monkeypatch.setattr(worker.shutil, 'which', lambda name, path=None: '/usr/local/bin/' + name)
    assert tuple(job for job in CAPABILITIES if worker.available(job)) == OFFERED
    monkeypatch.setattr(worker, 'confinement_available', lambda: False)
    assert not [job for job in CAPABILITIES if worker.available(job)]
