"""A command-line AI assistant's saved sessions and checkpoints (synthetic, documented layout).

Sessions replay the way the assistant replays them: a later line with the same id replaces the
earlier one, ``$rewindTo`` drops a message and what follows, ``$patch`` edits, ``$set`` updates
the metadata. The tests pin the conversation shown, the working folder from the assistant's
project list, its context file with imports, model and tool servers, and plain failures for
damaged, unknown, locked, linked and oversized stores, with nothing changed or leaked.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

from core.chat_import import local_sources, store
from core.chat_import.common import Limits
from core.chat_import.source_context import review
from tests.test_chat_import_migration import _finish
from tests.test_chat_import_migration import app as app
from tests.test_chat_import_served import _call
from tests.test_chat_import_served import app as import_app

SECRET = "sk-" + "G" * 48


def _session_lines() -> list:
    return [
        {"sessionId": "s-harbor", "projectHash": "h", "startTime": "2026-09-01T10:00:00.000Z",
         "lastUpdated": "2026-09-01T10:05:00.000Z", "kind": "main"},
        {"id": "m1", "timestamp": "2026-09-01T10:00:01.000Z", "type": "user", "content": [{"text": "Plan the harbor release."}]},
        {"id": "m2", "timestamp": "2026-09-01T10:00:02.000Z", "type": "gemini", "content": [{"text": "draft"}],
         "model": "model-a", "toolCalls": [{"id": "t", "name": "read_file", "args": {"file_path": "/old/harbor/ledger.md"},
                                            "status": "success"}]},
        {"id": "m2", "timestamp": "2026-09-01T10:00:02.000Z", "type": "gemini", "content": [{"text": "Check the tide first."}],
         "model": "model-a", "toolCalls": [{"id": "t", "name": "read_file", "args": {"file_path": "/old/harbor/ledger.md"},
                                            "status": "success", "result": [{"text": "ledger"}]}]},
        {"id": "m3", "type": "info", "content": "Saved a memory."},
        {"id": "m4", "type": "user", "content": "Abandon this."},
        {"id": "m5", "type": "gemini", "content": "Abandoned."},
        {"$rewindTo": "m4"},
        {"id": "m6", "type": "user", "displayContent": "Now write the ledger.",
         "content": [{"text": "Now write the ledger.\n--- Content from referenced files ---\nexpanded_marker"}]},
        {"id": "m7", "type": "gemini", "content": [{"text": "hidden thought", "thought": True}, {"text": "Ledger written."}],
         "model": "model-b"},
        {"$patch": {"id": "m7", "content": [{"text": "Ledger written and checked."}]}},
        {"$set": {"summary": "Harbor release plan"}},
    ]


def _store(home: Path, *, lines: list | None = None, slug: str = "harbor", raw_tail: bytes = b"") -> Path:
    root = home / ".gemini"
    chats = root / "tmp" / slug / "chats"
    chats.mkdir(parents=True, exist_ok=True)
    (root / "projects.json").write_text(json.dumps({"projects": {"/old/harbor": "harbor"}}))
    path = chats / "session-2026-09-01T10-00-aaaa1111.jsonl"
    path.write_bytes(b"".join(json.dumps(line).encode() + b"\n" for line in (lines or _session_lines())) + raw_tail)
    return path


def _read(home: Path, **kwargs):
    return local_sources.read("local_command_line_assistant", home=home, env={}, platform="linux", **kwargs)


def _fingerprint(home: Path) -> dict:
    return {str(path.relative_to(home)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(home.rglob("*")) if path.is_file()}


def test_session_replay_and_checkpoints(tmp_path):
    home = tmp_path / "home"
    path = _store(home)
    (path.parent.parent / "checkpoint-release%20v1.json").write_text(json.dumps({"history": [
        {"role": "user", "parts": [{"text": "Saved question"}]}, {"role": "model", "parts": [{"text": "Saved answer"}]},
        {"role": "model", "parts": [{"functionCall": {"name": "x"}}]}]}))
    (path.parent / "s-harbor").mkdir()
    (path.parent / "s-harbor" / "helper.jsonl").write_text(json.dumps({"sessionId": "helper", "kind": "subagent"}) + "\n")
    before = _fingerprint(home)
    [found] = local_sources.detect(home=home, env={}, platform="darwin")
    assert found == {**found, "id": "local_command_line_assistant", "kind": "command_line_assistant"}
    result = _read(home)
    assert result.source_format == "gemini_cli_session" and result.problems == ()
    session, checkpoint = result.chats
    assert session.title == "Harbor release plan" and session.source_chat_id == "s-harbor"
    assert [(m.role, m.text) for m in session.messages] == [
        ("user", "Plan the harbor release."), ("assistant", "Check the tide first."),
        ("user", "Now write the ledger."), ("assistant", "Ledger written and checked.")]
    assert session.skipped == {"info": 1} and "expanded_marker" not in json.dumps([m.text for m in session.messages])
    assert session.context["workspace_hint"] == "/old/harbor" and session.context["models"] == ["model-a", "model-b"]
    assert session.context["file_references"] == ["/old/harbor/ledger.md"]
    assert checkpoint.title == "release v1" and checkpoint.source_chat_id == "checkpoint:harbor:release v1"
    assert [m.text for m in checkpoint.messages] == ["Saved question", "Saved answer"] and checkpoint.skipped == {"tool": 1}
    assert _fingerprint(home) == before


def test_helpers_unknown_damaged_locked_linked_and_huge_fail_plainly(tmp_path):
    home = tmp_path / "home"
    _store(home, raw_tail=b'{"id":"m9","type":"us')
    chats = home / ".gemini" / "tmp" / "harbor" / "chats"
    (chats / "session-2026-09-02T10-00-bbbb2222.jsonl").write_text(
        json.dumps({"sessionId": "helper", "kind": "subagent"}) + "\n" + json.dumps({"id": "x", "type": "user", "content": "h"}) + "\n")
    (chats / "session-2026-09-03T10-00-cccc3333.jsonl").write_text(json.dumps({"something": "else"}) + "\n")
    (chats / "session-2026-09-04T10-00-dddd4444.jsonl").write_text('{"sessionId": "d"}\n{broken\n{"id":"x","type":"user","content":"y"}\n')
    os.mkfifo(chats / "session-2026-09-05T10-00-eeee5555.jsonl")
    outside = tmp_path / "outside.jsonl"
    outside.write_text(json.dumps({"sessionId": "outside"}) + "\n")
    (chats / "session-2026-09-06T10-00-ffff6666.jsonl").symlink_to(outside)
    (chats.parent / "checkpoint-bad.json").write_text("{not json")
    done = {}
    worker = threading.Thread(target=lambda: done.setdefault("result", _read(home)), daemon=True)
    worker.start()
    worker.join(10)
    assert "result" in done, "reading blocked on a named pipe"
    result = done["result"]
    assert [chat.source_chat_id for chat in result.chats] == ["s-harbor"]
    assert set(result.problems) == {"chats_internal_skipped", "store_version_unknown", "rows_unreadable_skipped", "files_skipped"}
    small = _read(home, limits=Limits(max_local_store_bytes=64))
    assert small.chats == () and "store_too_large" in small.problems
    assert _read(tmp_path / "nobody").problems == ("store_missing",)


def test_an_older_hashed_project_folder_reads_without_a_folder_hint(tmp_path):
    home = tmp_path / "home"
    _store(home, slug="a" * 64)
    [chat] = _read(home).chats
    assert chat.context["workspace_hint"] == "" and len(chat.messages) == 4


def _settings(home: Path, project: Path) -> None:
    root = home / ".gemini"
    root.mkdir(parents=True, exist_ok=True)
    (root / "GEMINI.md").write_text("Global: use the compass_marker. @extra.md\n")
    (root / "extra.md").write_text("Imported import_marker.\n")
    (root / "settings.json").write_text(json.dumps({
        "model": {"name": "model-b"}, "security": {"auth": {"selectedType": "oauth", "token": SECRET}},
        "mcpServers": {"ledger": {"command": "python3", "args": ["-m", "ledger"], "env": {"KEY": SECRET}},
                       "remote": {"httpUrl": "http://127.0.0.1:9/mcp", "headers": {"Authorization": "Bearer " + SECRET}}}}))
    (project / ".gemini").mkdir(parents=True)
    (project / "GEMINI.md").write_text("Project: keep the project_marker ledger.\n")
    (project / ".gemini" / "settings.json").write_text(json.dumps({"mcpServers": {"local": {"command": "node", "args": ["s.js"]}}}))


def test_review_reads_context_files_model_and_servers_without_secrets(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "harbor"
    _settings(home, project)
    _store(home)
    [chat] = _read(home).chats
    before = _fingerprint(home)
    plan = review(chat, root=str(project))
    text = json.dumps(plan)
    assert SECRET not in text
    instructions = " ".join(row["text"] for row in plan["instructions"])
    assert "compass_marker" in instructions and "import_marker" in instructions and "project_marker" in instructions
    assert "model-b" in plan["models"]
    assert {(tool["name"], tool["command"]) for tool in plan["tools"]} == {("ledger", "python3"), ("local", "node")}
    assert "tool_transport_unsupported" in plan["warnings"]
    assert _fingerprint(home) == before


def test_import_through_the_doors(app, tmp_path):
    from core.execution.mcp_bridge import load_mcp_server_configs

    home = Path(os.environ["HOME"])
    project = tmp_path / "harbor-project"
    _settings(home, project)
    _store(home)
    before = _fingerprint(home)
    status, sources = _call(app, "GET", "/api/chat/import/sources")
    assert status == 200 and [s["kind"] for s in sources["local"]] == ["command_line_assistant"]
    status, preview = _call(app, "POST", "/api/chat/import/preview", body={"source": "local_command_line_assistant"})
    assert status == 200, preview
    assert [c["title"] for c in preview["chats"]] == ["Harbor release plan"]
    status, reviewed = _call(app, "POST", "/api/chat/import/context", body={
        "preview_id": preview["preview_id"], "ref": "0", "root": str(project), "old_root": "/old/harbor"})
    assert status == 200, reviewed
    assert SECRET not in json.dumps(reviewed)
    run = _finish(app, preview, continuation=True, contexts={"0": {"plan_id": reviewed["plan_id"],
                                                                   "options": {"instructions": True, "tools": True}}})
    history = store.continuation_history(run["first_session_id"])
    assert [row["content"] for row in history][-1] == "Ledger written and checked."
    assert load_mcp_server_configs() == []
    assert _fingerprint(home) == before
