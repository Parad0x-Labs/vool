"""Package-local isolation: the root conftest resets the DB and the JSONL stores per test, but
the attachment stage directory and `chat_session_meta.json` live on across tests in the same
pytest session (the home directory is per-session, not per-test). The portability lane asserts
exact counts of both, so it clears them itself before each test."""

from __future__ import annotations

import json
import shutil

import pytest

from core import runtime_paths


@pytest.fixture(autouse=True)
def _clean_attachment_stage_and_meta():
    stage = runtime_paths.active_data_dir() / "chat_attachments"
    if stage.is_dir():
        shutil.rmtree(stage, ignore_errors=True)
    meta = runtime_paths.active_data_dir() / "chat_session_meta.json"
    for path in (meta, meta.with_suffix(".json.bak")):
        if path.is_file():
            path.unlink()
    yield


@pytest.fixture()
def fresh_home(tmp_path):
    """A truly empty second VOOL home (its own directory, its own DB)."""

    def _make(name: str = "fresh-home") -> str:
        home = tmp_path / name
        home.mkdir(parents=True, exist_ok=True)
        return str(home)

    return _make
