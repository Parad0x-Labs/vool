"""A write's `expected_hash` holds a belief only when it is a hash a read could have returned.

Blind audit of 58f60bbf3, item 6, on the app from source (qwen3:8b, 2026-11-22): "Sukurk failą kelione.txt ir įrašyk
į jį ..." proposed workspace.write_file with `expected_hash: "sha256"` (the model filled the optional argument from
its description). The person allowed it in the chat page and the write was refused as `stale_base` against a file
that did not exist. A real stale hash still refuses, and an existing file is still protected by its real hash.
"""
from __future__ import annotations

import hashlib
import random
from pathlib import Path

import pytest

from core.mode_permission_policy import set_active_mode
from core.runtime_execution_tools import execute_runtime_tool


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module

    store_module.reset_default_store()
    root = tmp_path / "ws"
    root.mkdir()
    yield root
    store_module.reset_default_store()


def _ctx(root: Path) -> dict:
    set_active_mode("hash-sess", "auto")
    return {"workspace": str(root), "session_id": "hash-sess", "surface": "api", "operating_mode": "auto"}


@pytest.mark.parametrize("seed", range(5))
def test_a_placeholder_hash_on_a_new_file_writes_it(seed, workspace):
    rng = random.Random(f"placeholder-{seed}")
    name = f"{rng.choice(['kelione', 'notizen', 'tareas', 'plan'])}{rng.randint(1, 99)}.txt"
    text = rng.choice(["Palanga liepą", "Milch, Eier", "pan y leche"])
    placeholder = rng.choice(["sha256", "<sha256>", "optional", "SHA-256 of the content", "none"])
    result = execute_runtime_tool(
        "workspace.write_file", {"path": name, "content": text, "expected_hash": placeholder},
        source_context=_ctx(workspace),
    )
    assert result is not None and result.ok, (placeholder, result and result.response_text)
    assert (workspace / name).read_text(encoding="utf-8") == text


def test_a_real_hash_that_no_longer_matches_still_refuses(workspace):
    (workspace / "s.txt").write_text("v1", encoding="utf-8")
    stale = hashlib.sha256(b"v0").hexdigest()
    result = execute_runtime_tool(
        "workspace.write_file", {"path": "s.txt", "content": "v2", "expected_hash": stale},
        source_context=_ctx(workspace),
    )
    assert result is not None and not result.ok
    assert (workspace / "s.txt").read_text(encoding="utf-8") == "v1"
