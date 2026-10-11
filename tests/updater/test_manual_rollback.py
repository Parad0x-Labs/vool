"""Real protected-code swaps and SQLite recovery for explicit rollback."""
import json
import sqlite3
from types import SimpleNamespace

import pytest

from core.updater.feed import FeedConfig
from core.updater.health import HealthResult
from core.updater.linux import LinuxSourceInstaller
from core.updater.rollback import code_digest, recover_manual_rollbacks, rollback_candidate, run_manual_rollback
from core.updater.runtime import UpdateSubsystem
from core.updater.transaction import Step, UpdateJournal


@pytest.fixture
def installed(tmp_path):
    home = tmp_path / 'home'
    data = home / 'data'
    data.mkdir(parents=True)
    (home / 'config').mkdir()
    (home / 'config/keys.json').write_text('latest disposable key and preference')
    (data / 'conversation.jsonl').write_text('history written AFTER updating')
    with sqlite3.connect(data / 'chat.db') as db:
        db.execute('CREATE TABLE chats(value TEXT)')
        db.execute("INSERT INTO chats VALUES ('chat written AFTER updating')")
    target, new = tmp_path / 'installed', tmp_path / 'new'
    target.mkdir(); new.mkdir()
    (target / 'core').mkdir(); (target / 'core/version').write_text('0.7.0')
    (new / 'core').mkdir(); (new / 'core/version').write_text('0.7.1')
    (new / 'new-only').write_text('must disappear on previous-code rollback')
    (target / 'install_receipt.json').write_text(json.dumps({'runtime_home': str(home)}))
    (target / '.venv').mkdir(); (target / '.venv/retained').write_text('environment')
    original = LinuxSourceInstaller(runtime_home=home).atomic_swap(new, target, txid='original')
    assert original.ok
    subsystem = UpdateSubsystem(data_dir=data, feed=FeedConfig(app_path=str(target)),
                                platform='linux-x64', installed_version='0.7.1')
    journal = UpdateJournal(subsystem.paths.transaction_dir('original'))
    journal.append(Step.DECIDED, {'installed_version': '0.7.0', 'target_version': '0.7.1',
                                'platform': 'linux-x64',
                                'previous_identity': '0.7.0', 'expected_identity': '0.7.1'})
    journal.append(Step.SWAPPED, {'prior_path': str(original.prior_path)})
    journal.append(Step.FINALIZED, {'prior_code_sha256': code_digest(original.prior_path)})
    stopped = []
    helper = SimpleNamespace(shutdown_app=lambda: stopped.append(True), wait_until_stopped=lambda **kw: True,
                             relaunch=lambda path: True)
    subsystem._make_helper = lambda: helper
    subsystem._make_probe = lambda: lambda **kw: HealthResult(
        (target / 'core/version').read_text() == kw['expected_version'], 'actual code version')
    return subsystem, home, target, stopped, journal


def retained(home):
    with sqlite3.connect(home / 'data/chat.db') as db:
        return db.execute('SELECT * FROM chats').fetchall(), (home / 'config/keys.json').read_text()


def test_previous_code_keeps_current_chats_keys_and_environment(installed):
    subsystem, home, target, stopped, _ = installed
    before = retained(home)
    result = run_manual_rollback(subsystem, rollback_candidate(subsystem), 'rollback-success')
    assert result['ok'] and result['current_data_preserved']
    assert (target / 'core/version').read_text() == '0.7.0'
    assert not (target / 'new-only').exists()
    assert (target / '.venv/retained').read_text() == 'environment'
    assert retained(home) == before
    assert len(stopped) == 1
    assert subsystem.status.load().fault_code == 'manual_rollback'
    assert rollback_candidate(subsystem) is None


def test_bad_previous_runtime_restores_current_code_and_current_data(installed):
    subsystem, home, target, _, _ = installed
    before = retained(home)

    def probe(**kw):
        if (target / 'core/version').read_text() == '0.7.0':
            with sqlite3.connect(home / 'data/chat.db') as db:
                db.execute('DELETE FROM chats')
            (home / 'config/keys.json').write_text('broken old runtime mutated keys')
            (home / 'data/conversation.jsonl').unlink()
            assert retained(home) == ([], 'broken old runtime mutated keys')
            return HealthResult(False, 'previous runtime failed')
        return HealthResult(True, 'current code healthy')

    subsystem._make_probe = lambda: probe
    result = run_manual_rollback(subsystem, rollback_candidate(subsystem), 'rollback-failure')
    assert result == {'ok': False, 'restored_current': True}
    assert (target / 'core/version').read_text() == '0.7.1'
    assert (target / 'new-only').exists()
    assert retained(home) == before
    assert (home / 'data/conversation.jsonl').read_text() == 'history written AFTER updating'
    assert subsystem.status.load().phase.value == 'failed'


def test_damaged_backup_refused_before_stop_or_data_change(installed):
    subsystem, home, target, stopped, _ = installed
    candidate = rollback_candidate(subsystem)
    before = retained(home)
    (target / 'update_backups/signed-original/core/version').write_text('tampered')
    assert not run_manual_rollback(subsystem, candidate, 'rollback-damaged')['ok']
    assert not stopped
    assert (target / 'core/version').read_text() == '0.7.1'
    assert retained(home) == before


def test_data_migration_does_not_offer_destructive_manual_rollback(installed):
    subsystem, home, _, _, journal = installed
    journal.append(Step.MIGRATED, {'applied': ['new-schema']})
    assert rollback_candidate(subsystem) is None
    assert retained(home)[0] == [('chat written AFTER updating',)]


def test_incompatible_schema_does_not_offer_rollback(installed):
    subsystem, home, _, _, _ = installed
    from core.updater.migrations import write_recorded_schema

    write_recorded_schema(home / 'data', '0.7.1')
    assert rollback_candidate(subsystem) is None


def test_damaged_recovery_journal_reports_failure_and_keeps_data(installed):
    subsystem, home, target, stopped, _ = installed
    before = retained(home)
    path = subsystem.paths.root / 'manual_rollbacks/broken/state.json'
    path.parent.mkdir(parents=True); path.write_text('damaged')
    assert not recover_manual_rollbacks(subsystem)['ok']
    assert subsystem.status.load().fault_code == 'rollback_failed'
    assert not stopped and retained(home) == before
    assert (target / 'core/version').read_text() == '0.7.1'


def test_explicit_rollback_respects_active_destructive_work(installed):
    from core.updater.service import UserGesture

    subsystem, home, target, stopped, _ = installed
    handle = subsystem.register_turn('deleting', 'deleting files', destructive=True)
    outcome = subsystem.press_rollback(UserGesture(pressed_at=1, origin='web-ui'))
    assert not outcome.accepted and 'deleting files' in outcome.detail
    assert not stopped and (target / 'core/version').read_text() == '0.7.1'
    assert retained(home)[0] == [('chat written AFTER updating',)]
    subsystem.complete_turn(handle.id)


def test_corrupt_schema_marker_refuses_without_changing_data(installed):
    from core.updater.migrations import SCHEMA_STATE_NAME

    subsystem, home, _, stopped, _ = installed
    (home / 'data' / SCHEMA_STATE_NAME).write_text('unknown schema')
    assert rollback_candidate(subsystem) is None
    assert not stopped and retained(home)[0] == [('chat written AFTER updating',)]


def test_previous_backup_from_another_cpu_is_not_offered(installed):
    subsystem, home, _, stopped, _ = installed
    subsystem.platform = 'linux-arm64'
    assert rollback_candidate(subsystem) is None
    assert not stopped and retained(home)[0] == [('chat written AFTER updating',)]
