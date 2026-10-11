"""Windows publication avoids CRT directory opens and retains refusal witnesses."""
from __future__ import annotations

import ctypes
import json
from types import SimpleNamespace

import pytest

from core.memory import revocation
from core.vool_memory import VoolMemory


def test_windows_initialization_does_not_open_directories(tmp_path, monkeypatch):
    monkeypatch.setattr(revocation, '_WINDOWS', True)
    native_replace = revocation.os.replace
    directory_open = revocation.os.open

    def guarded_open(path, *args, **kwargs):
        if revocation.Path(path).is_dir():
            raise PermissionError('Windows CRT directory open')
        return directory_open(path, *args, **kwargs)

    monkeypatch.setattr(revocation.os, 'open', guarded_open)
    monkeypatch.setattr(revocation, '_replace_file', native_replace)
    for _ in range(2):
        memory = VoolMemory(db_path=tmp_path / 'data/memory/vool_memory.db')
        memory.close()
    assert json.loads((tmp_path / '.forget-authority/ledger.json').read_text()) == {
        'version': 2, 'operations': {},
    }
    (tmp_path / '.forget-authority/ledger.json').unlink()
    with pytest.raises(RuntimeError, match='initialized memory'):
        VoolMemory(db_path=tmp_path / 'data/memory/vool_memory.db')


@pytest.mark.parametrize('succeeds', [True, False])
def test_windows_replace_requests_write_through_and_propagates_failure(tmp_path, monkeypatch, succeeds):
    calls = []

    def move(source, target, flags):
        calls.append((source, target, flags))
        if succeeds:
            revocation.os.replace(source, target)
        return int(succeeds)

    monkeypatch.setattr(revocation, '_WINDOWS', True)
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *args, **kwargs: SimpleNamespace(MoveFileExW=move), raising=False)
    monkeypatch.setattr(ctypes, 'WinError', lambda code: OSError('native rename failed'), raising=False)
    monkeypatch.setattr(ctypes, 'get_last_error', lambda: 5, raising=False)
    path = tmp_path / 'ledger.json'
    path.write_text('existing authority')
    if succeeds:
        revocation._durable_write(path, {'version': 2, 'operations': {}})
        assert json.loads(path.read_text()) == {'version': 2, 'operations': {}}
    else:
        with pytest.raises(OSError, match='native rename failed'):
            revocation._durable_write(path, {'version': 2, 'operations': {}})
        assert path.read_text() == 'existing authority'
    assert len(calls) == 1
    assert calls[0][1:] == (str(path), 0x1 | 0x8)
    assert sorted(p.name for p in tmp_path.iterdir()) == ['ledger.json']
