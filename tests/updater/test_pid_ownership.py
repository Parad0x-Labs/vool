"""A stale app PID file cannot authorize stopping another live process."""
import json
import subprocess

import pytest

from core.updater.macos import MacProcessHelper
from core.updater.runtime import _pidfile_pid_for


def test_stale_pid_does_not_signal_another_process(tmp_path):
    process = subprocess.Popen(['sleep', '30'])
    (tmp_path / 'owned-pids.json').write_text(json.dumps([process.pid]))
    pidfile = tmp_path / 'vool_api.pid'
    pidfile.write_text(str(process.pid))
    try:
        helper = MacProcessHelper(pid_file=pidfile, app_path=tmp_path / 'VOOL.app')
        helper.shutdown_app()
        assert helper.wait_until_stopped(timeout=.1)
        with pytest.raises(subprocess.TimeoutExpired):
            process.wait(timeout=.2)
    finally:
        process.terminate(); process.wait(timeout=5)


def test_native_pid_override_is_bound_to_the_app(tmp_path, monkeypatch):
    process = subprocess.Popen(['sleep', '30'])
    (tmp_path / 'owned-pids.json').write_text(json.dumps([process.pid]))
    pidfile = tmp_path / 'vool_native.pid'
    pidfile.write_text(str(process.pid))
    monkeypatch.setenv('VOOL_UPDATE_TARGET_PIDFILE', str(pidfile))
    try:
        assert _pidfile_pid_for(tmp_path / 'VOOL.app') == 0
        with pytest.raises(subprocess.TimeoutExpired):
            process.wait(timeout=.2)
    finally:
        process.terminate(); process.wait(timeout=5)
