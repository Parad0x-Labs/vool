"""A Blackbox store built while the key source did not answer asks again on its next use.

CI 44970482057 (shard 4): `test_sabotage_stripping_rollback_id_is_detected` was refused as
`blackbox_unavailable` on its second write. The process store had been built, earlier in the same
process, at a moment the credential store did not answer; `default_store()` kept that keyless store
for the life of the process, so every later change to an existing file (whose "before" copy needs
the encrypted store) was refused. In the app the same thing happens when the keychain does not
answer once: every file change is refused until VOOL restarts.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import mock

import pytest

from core import credential_store
from core.blackbox import store as bbstore
from core.runtime_execution_tools import execute_runtime_tool


@pytest.fixture
def blackbox_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    monkeypatch.delenv("VOOL_BLACKBOX_CAS_KEYS_FILE", raising=False)
    bbstore.reset_default_store()
    yield tmp_path
    bbstore.reset_default_store()


def _keyless_store():
    with mock.patch.object(credential_store, "get_credential", side_effect=RuntimeError("keychain did not answer")):
        store = bbstore.default_store()
    assert store.cas_keyring is None  # built while the key source failed: fails closed
    return store


def test_the_next_use_asks_the_key_source_again(blackbox_dir):
    keyless = _keyless_store()
    again = bbstore.default_store()
    assert again is not keyless
    assert again.cas_keyring is not None
    assert bbstore.default_store() is again  # a store that has its key is kept


def test_while_the_key_source_still_fails_the_store_stays_closed(blackbox_dir):
    _keyless_store()
    with mock.patch.object(credential_store, "get_credential", side_effect=RuntimeError("still locked")):
        assert bbstore.default_store().cas_keyring is None


def test_a_change_to_an_existing_file_runs_once_the_key_answers(blackbox_dir):
    workspace = Path(tempfile.mkdtemp(dir=blackbox_dir))
    (workspace / "a.txt").write_text("before", encoding="utf-8")
    _keyless_store()
    result = execute_runtime_tool(
        "workspace.write_file",
        {"path": "a.txt", "content": "after"},
        source_context={"workspace": str(workspace), "session_id": "keyless-then-keyed"},
    )
    assert result is not None and result.ok, (result.status, result.response_text)
    assert (workspace / "a.txt").read_text(encoding="utf-8") == "after"
