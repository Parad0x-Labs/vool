"""A forget reads the file names a command run's undo record keeps, not only a file write's path.

Found in the unwired-modules review (2026-11-12): command runs (shell, tests, lint, formatter) record their files
in the undo journal inside the scan's preimage table ("files", keyed by path) and the drift rows ("drift", each
with a "path"). ``core.memory.copies._journal_words`` read only "path", "paths" and "root", so when a file name
carried the forgotten words the forget still said the undo copies were verified. The journal is sealed evidence
and stays as it is; the reply has to say that the undo record still names the words.

The run here is the recorder's own (``recorded_capability_mutation`` with ``sandbox.run_command``) and the forget
is the chat command. All names are invented for this contract.
"""
from __future__ import annotations

import pytest

from tests.test_forget_scrubs_run_history import CANARY, _remember, command
from tests.test_forget_scrubs_run_history import isolated_pages as isolated_pages  # the fixture

KEPT = "the undo record of a file VOOL changed"


@pytest.fixture
def undo_journal(isolated_pages, tmp_path, monkeypatch):
    """The blackbox store inside the isolated profile, its own CAS keys and clean registries."""
    from core.blackbox import store as store_module
    from core.blackbox.coverage import registry as coverage_registry
    from core.blackbox.coverage.cas_keys import CasKeyring
    from core.mode_permission_policy import reset_mode_permission_state
    from core.runtime_paths import active_data_dir
    from core.tool_registry import reset as reset_tools

    keys = tmp_path / "cas-keys.json"
    keys.write_text(CasKeyring.mint().to_json(), encoding="utf-8")
    monkeypatch.setenv("VOOL_BLACKBOX_CAS_KEYS_FILE", str(keys))
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(active_data_dir() / "blackbox"))
    store_module.reset_default_store()
    coverage_registry.reset_registered()
    reset_tools()
    reset_mode_permission_state()
    try:
        yield store_module
    finally:
        store_module.reset_default_store()
        coverage_registry.reset_registered()
        reset_tools()
        reset_mode_permission_state()


def _run(workspace, chat: str, turn: str, handler):
    from core.blackbox.coverage.recorder import recorded_capability_mutation
    from core.runtime_execution_tools import RuntimeExecutionResult
    from tests.blackbox_coverage._ctx import ctx

    def run():
        handler()
        return RuntimeExecutionResult(handled=True, ok=True, status="executed", response_text="ok", details={})

    result = recorded_capability_mutation(
        "sandbox.run_command", {"command": "script", "cwd": str(workspace)}, handler=run,
        source_context=ctx(workspace, session=chat, turn=turn), workspace_root=workspace,
    )
    assert result.ok, result.response_text


def _words_only_in_names(store_module, chat: str) -> None:
    """The bytes of every undo copy are neutral: only the journal's file names carry the words."""
    store = store_module.default_store()
    needle = CANARY.lower().encode()
    assert not store.copies_carrying(lambda data: needle in data.lower(), sessions={chat, ""})
    assert not any(str(entry.get("session_id") or "") == chat and CANARY.lower() in str(entry.get("path") or "").lower()
                   for entry in store.entries()), "no entry of the run names the file in its own path field"


def test_a_file_a_command_run_made_is_named_in_its_drift_rows_and_the_forget_says_so(undo_journal, isolated_pages, tmp_path):
    chat = "command-run-made"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("rye, spelt\n", encoding="utf-8")
    _remember(isolated_pages, chat, "my sister's bakery is called Flourstone Bakehouse.")
    _run(workspace, chat, "turn-made", lambda: (workspace / "flourstone_bakehouse_order.txt").write_text("two loaves\n"))
    entries = undo_journal.default_store().entries()
    terminal = [entry for entry in entries if entry.get("kind") == "coverage_scan_terminal"]
    assert terminal and any(row.get("path", "").endswith("flourstone_bakehouse_order.txt")
                            for row in terminal[-1].get("drift") or []), terminal
    _words_only_in_names(undo_journal, chat)

    handled, reply = command(isolated_pages, chat, f"Forget {CANARY}.")

    assert handled and reply.startswith("Forget applied"), reply
    assert KEPT in reply, reply
    assert undo_journal.default_store().verify().ok


def test_a_file_a_command_run_only_saw_is_named_in_its_preimage_table_and_the_forget_says_so(undo_journal, isolated_pages, tmp_path):
    chat = "command-run-saw"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "flourstone-bakehouse-menu.txt").write_text("rye, spelt\n", encoding="utf-8")
    _remember(isolated_pages, chat, "my sister's bakery is called Flourstone Bakehouse.")
    _run(workspace, chat, "turn-saw", lambda: (workspace / "receipt.txt").write_text("paid\n"))
    intended = [entry for entry in undo_journal.default_store().entries() if entry.get("kind") == "coverage_scan_intended"]
    assert intended and any(name.endswith("flourstone-bakehouse-menu.txt") for name in intended[-1].get("files") or {}), intended
    _words_only_in_names(undo_journal, chat)

    handled, reply = command(isolated_pages, chat, f"Forget {CANARY}.")

    assert handled and reply.startswith("Forget applied"), reply
    assert KEPT in reply, reply


def test_another_chats_command_run_is_not_this_chats_undo_record(undo_journal, isolated_pages, tmp_path):
    chat, other = "command-run-mine", "command-run-theirs"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _remember(isolated_pages, chat, "my sister's bakery is called Flourstone Bakehouse.")
    _run(workspace, other, "turn-theirs", lambda: (workspace / "flourstone_bakehouse_order.txt").write_text("two loaves\n"))

    handled, reply = command(isolated_pages, chat, f"Forget {CANARY}.")

    assert handled and reply.startswith("Forget applied"), reply
    assert KEPT not in reply, reply


def test_the_journal_reader_names_every_path_a_command_run_records():
    from core.memory.copies import _journal_words

    entry = {
        "kind": "coverage_scan_terminal", "session_id": "c", "root": "/w", "intent": "sandbox.run_command",
        "files": {"a/menu.txt": {"content_id": "c0ffee" * 8}},
        "drift": [{"path": "made/order.txt", "drift_kind": "created", "before": {"sha256": "ab" * 32}}],
        "coverage": {"declared_paths": ["/w/declared.txt"], "high_risk": {"/w/.env": "credentials"}},
        "incomplete_paths": ["/w/big.bin"],
        "effect_id": "effect-" + "0" * 32, "ts": "2026-11-12T19:30:00Z",
    }
    words = _journal_words(entry)
    names = words["named_paths"]
    assert {"a/menu.txt", "made/order.txt", "/w/declared.txt", "/w/.env"} <= set(names), words
    assert words["root"] == "/w" and words["incomplete_paths"] == ["/w/big.bin"], words
    flat = repr(words)
    assert "c0ffee" not in flat and "effect-" not in flat and "2026-11-12" not in flat, words
