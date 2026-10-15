"""The indexed-presence check follows every change to the index files, whoever made it."""
from __future__ import annotations

import json
import os

import pytest

from core import credential_store as cs
from core import runtime_paths


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


def test_a_key_saved_or_removed_here_shows_on_the_next_check() -> None:
    assert cs.credential_is_indexed("x.api.default") is False
    cs.store_credential("x.api.default", "k", label="X")
    assert cs.credential_is_indexed("x.api.default") is True
    assert cs.delete_credential("x.api.default") is True
    assert cs.credential_is_indexed("x.api.default") is False


def test_an_index_rewritten_by_another_process_is_read_again() -> None:
    cs.store_credential("x.api.default", "k", label="X")
    assert cs.credential_is_indexed("x.api.default") is True
    vault = runtime_paths.data_path("credentials.enc.json")
    stat = vault.stat()
    # Another process removes the entry: same size class, but the file is no longer the same.
    raw = json.loads(vault.read_text(encoding="utf-8"))
    raw.pop("x.api.default")
    vault.write_text(json.dumps(raw), encoding="utf-8")
    os.utime(vault, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    meta = runtime_paths.data_path("credentials.meta.json")
    if meta.exists():
        meta.write_text(json.dumps({}), encoding="utf-8")
    assert cs.credential_is_indexed("x.api.default") is False


def test_a_moved_home_is_not_answered_from_the_old_one(tmp_path) -> None:
    cs.store_credential("x.api.default", "k", label="X")
    assert cs.credential_is_indexed("x.api.default") is True
    other = tmp_path / "other-home"
    other.mkdir()
    runtime_paths.configure_runtime_home(other)
    assert cs.credential_is_indexed("x.api.default") is False
