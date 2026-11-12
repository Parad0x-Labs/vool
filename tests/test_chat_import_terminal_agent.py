"""A terminal coding agent's local sessions (the documented rollout JSON-lines layout).

Every store here is synthetic, built from the agent's own published source: session files under
``sessions/YYYY/MM/DD`` and ``archived_sessions``, one ``{"timestamp","type","payload"}`` record
per line. The tests pin what a person sees after a move-in (which words, which order, which
context), that injected instruction/environment text never becomes a message, that damaged,
locked, compressed, huge and unknown-version stores fail plainly, and that the agent's files
are never changed and its secrets never read into VOOL.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

import pytest
import zstandard

from core.chat_import import local_rollout_sessions as rollout
from core.chat_import import local_sources, store
from core.chat_import.common import Limits
from core.chat_import.source_context import review
from tests.test_chat_import_migration import _finish
from tests.test_chat_import_migration import app as app
from tests.test_chat_import_served import _call
from tests.test_chat_import_served import app as import_app

LIMITS = Limits()
SECRET = "sk-" + "R" * 48


def _record(kind: str, payload: dict, when: str = "2026-09-01T10:00:00.000Z") -> dict:
    return {"timestamp": when, "type": kind, "payload": payload}


def _meta(thread: str, *, cwd: str = "/old/harbor", mode: str | None = None, **extra) -> dict:
    payload = {"id": thread, "timestamp": "2026-09-01T10:00:00.000Z", "cwd": cwd, "originator": "cli",
               "cli_version": "0.130.0", "source": "cli", "git": {"branch": "tide-check"}, **extra}
    if mode:
        payload["history_mode"] = mode
    return _record("session_meta", payload)


def _message(role: str, text: str, *, kinds: list[str] | None = None, block: str | None = None) -> dict:
    payload = {"type": "message", "role": role,
               "content": [{"type": block or ("input_text" if role in ("user", "developer") else "output_text"), "text": text}]}
    if kinds is not None:
        payload["internal_chat_message_metadata_passthrough"] = {"content_item_kinds": kinds}
    return _record("response_item", payload)


def _event(kind: str, **fields) -> dict:
    return _record("event_msg", {"type": kind, **fields})


AGENTS_INJECTION = "# AGENTS.md instructions for /old/harbor\n\n<INSTRUCTIONS>\nUse tide_marker.\n</INSTRUCTIONS>"
ENVIRONMENT = "<environment_context>\n  <cwd>/old/harbor</cwd>\n</environment_context>"


def _write(home: Path, thread: str, records: list, *, day: str = "2026/09/01", archived: bool = False,
           raw_tail: bytes = b"", compress: bool = False) -> Path:
    root = home / ".codex"
    folder = root / "archived_sessions" if archived else root / "sessions" / day
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"rollout-2026-09-01T10-00-00-{thread}.jsonl"
    data = b"".join(json.dumps(record).encode() + b"\n" for record in records) + raw_tail
    if compress:
        path = path.with_name(path.name + ".zst")
        data = zstandard.ZstdCompressor().compress(data)
    path.write_bytes(data)
    return path


def _legacy(thread: str = "thread-legacy") -> list:
    return [
        _meta(thread),
        _record("turn_context", {"model": "gpt-old", "cwd": "/old/harbor", "approval_policy": "never", "sandbox_policy": {}}),
        _message("developer", "<permissions instructions>never shown</permissions instructions>"),
        _message("user", AGENTS_INJECTION),
        _message("user", ENVIRONMENT),
        _message("user", "Plan the harbor release."),
        _event("user_message", message="Plan the harbor release."),
        _record("response_item", {"type": "function_call", "name": "update_plan", "call_id": "c1", "arguments": json.dumps(
            {"explanation": "Release steps", "plan": [{"step": "Check the tide table", "status": "completed"},
                                                     {"step": "Write the release ledger", "status": "in_progress"}]})}),
        _record("response_item", {"type": "function_call", "name": "read_file", "call_id": "c2",
                                  "arguments": json.dumps({"path": "/old/harbor/ledger.md"})}),
        _message("assistant", "First, check the tide table."),
        _event("agent_message", message="First, check the tide table."),
        _record("compacted", {"message": "Summary: the user is releasing the harbor build; tide checked."}),
        _record("turn_context", {"model": "gpt-new", "cwd": "/old/harbor", "approval_policy": "never", "sandbox_policy": {}}),
        _event("user_message", message="Now write the ledger."),
        _event("agent_message", message="The ledger is written."),
        _record("some_future_record", {"x": 1}),
    ]


def _read(home: Path, **kwargs):
    return local_sources.read("local_terminal_agent", home=home, env={}, platform="darwin", **kwargs)


def _fingerprint(home: Path) -> dict:
    return {str(path.relative_to(home)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(home.rglob("*")) if path.is_file()}


def test_legacy_session_is_the_screen_conversation_with_reviewed_context(tmp_path):
    home = tmp_path / "home"
    _write(home, "thread-legacy", _legacy())
    (home / ".codex" / "session_index.jsonl").write_text(
        json.dumps({"id": "thread-legacy", "thread_name": "Harbor release", "updated_at": "x"}) + "\n")
    before = _fingerprint(home)
    assert local_sources.detect(home=home, env={}, platform="linux")[0]["id"] == "local_terminal_agent"
    assert local_sources.detect(home=home, env={}, platform="linux")[0]["kind"] == "terminal_agent"
    result = _read(home)
    assert result.source_format == "codex_rollout_jsonl" and result.problems == ()
    [chat] = result.chats
    assert chat.title == "Harbor release" and chat.source_chat_id == "thread-legacy"
    assert [(m.role, m.text) for m in chat.messages] == [
        ("user", "Plan the harbor release."), ("assistant", "First, check the tide table."),
        ("user", "Now write the ledger."), ("assistant", "The ledger is written.")]
    joined = " ".join(m.text for m in chat.messages)
    assert "tide_marker" not in joined and "environment_context" not in joined and "never shown" not in joined
    context = chat.context
    assert context["workspace_hint"] == "/old/harbor" and context["models"] == ["gpt-old", "gpt-new"]
    assert context["branch"] == "tide-check" and context["profile_root"] == str(home / ".codex")
    notes = {note["source"]: note["text"] for note in context["notes"]}
    assert "tide checked" in notes["conversation_summary"]
    assert "[completed] Check the tide table" in notes["plan"] and "[in_progress] Write the release ledger" in notes["plan"]
    assert context["file_references"] == ["/old/harbor/ledger.md"]
    assert chat.skipped.get("unknown") == 1
    assert _fingerprint(home) == before


def test_paginated_session_uses_model_items_and_drops_every_injected_kind(tmp_path):
    home = tmp_path / "home"
    summary = "Another language model started to solve this problem and produced a summary of its thinking process."
    records = [
        _meta("thread-pages", mode="paginated"),
        _message("user", AGENTS_INJECTION),
        _message("user", "Labelled instructions", kinds=["agents_md.instructions"]),
        _message("user", "<skill>\n<name>x</name>\n</skill>"),
        _message("user", "Typed by the person", kinds=["user.text"]),
        _message("assistant", "Visible answer"),
        _message("user", summary),
        _record("response_item", {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "From compaction"}]}),
        _record("response_item", {"type": "reasoning", "summary": [], "encrypted_content": "zzz"}),
        _message("system", "system words"),
    ]
    records[7]["metadata"] = {"compaction_output": True}
    _write(home, "thread-pages", records)
    [chat] = _read(home).chats
    assert [(m.role, m.text) for m in chat.messages] == [("user", "Typed by the person"), ("assistant", "Visible answer")]
    assert chat.skipped["context"] == 5 and chat.skipped["system"] == 1


def test_rollback_removes_the_undone_turns(tmp_path):
    home = tmp_path / "home"
    _write(home, "thread-back", [
        _meta("thread-back"),
        _event("user_message", message="Keep this question."), _event("agent_message", message="Kept answer."),
        _event("user_message", message="Undo this question."), _event("agent_message", message="Undone answer."),
        _event("thread_rolled_back", num_turns=1),
        _event("user_message", message="Replacement question."),
    ])
    [chat] = _read(home).chats
    assert [m.text for m in chat.messages] == ["Keep this question.", "Kept answer.", "Replacement question."]
    assert chat.skipped["rolled_back"] == 2


def test_helper_sessions_and_unknown_versions_are_left_out_plainly(tmp_path):
    home = tmp_path / "home"
    _write(home, "thread-helper", [_meta("thread-helper", thread_source="subagent"), _event("user_message", message="x")])
    _write(home, "thread-review", [_meta("thread-review", source={"subagent": "review"}), _event("user_message", message="x")])
    unknown = _meta("thread-unknown")
    del unknown["payload"]["cli_version"]
    _write(home, "thread-unknown", [unknown, _event("user_message", message="x")])
    _write(home, "thread-headless", [_event("user_message", message="no header")])
    result = _read(home)
    assert result.chats == ()
    assert set(result.problems) == {"chats_internal_skipped", "store_version_unknown", "files_skipped"}
    assert result.source_format == ""


def test_damaged_lines_leave_the_session_out_but_a_line_still_being_written_does_not(tmp_path):
    home = tmp_path / "home"
    good = [_meta("thread-live"), _event("user_message", message="Saved question.")]
    _write(home, "thread-live", good, raw_tail=b'{"timestamp":"2026-09-01T10:00:01.000Z","type":"event_ms')
    damaged = [_meta("thread-damaged"), _event("user_message", message="lost?")]
    path = _write(home, "thread-damaged", damaged, day="2026/08/31")
    path.write_bytes(path.read_bytes().replace(b'"user_message"', b'"user_message"\n{broken', 1))
    result = _read(home)
    assert [chat.source_chat_id for chat in result.chats] == ["thread-live"]
    assert [m.text for m in result.chats[0].messages] == ["Saved question."]
    assert "rows_unreadable_skipped" in result.problems


def test_a_named_pipe_is_refused_without_blocking(tmp_path):
    home = tmp_path / "home"
    _write(home, "thread-real", [_meta("thread-real"), _event("user_message", message="Real.")])
    pipe = home / ".codex" / "sessions" / "2026" / "08" / "30"
    pipe.mkdir(parents=True)
    os.mkfifo(pipe / "rollout-2026-08-30T10-00-00-thread-pipe.jsonl")
    done = {}
    worker = threading.Thread(target=lambda: done.setdefault("result", _read(home)), daemon=True)
    worker.start()
    worker.join(10)
    assert "result" in done, "reading blocked on a named pipe"
    assert [chat.source_chat_id for chat in done["result"].chats] == ["thread-real"]
    assert "files_skipped" in done["result"].problems


def test_linked_session_files_are_not_followed(tmp_path):
    home = tmp_path / "home"
    outside = tmp_path / "outside.jsonl"
    outside.write_text(json.dumps(_meta("thread-outside")) + "\n")
    folder = home / ".codex" / "sessions" / "2026" / "09" / "02"
    folder.mkdir(parents=True)
    (folder / "rollout-2026-09-02T10-00-00-thread-link.jsonl").symlink_to(outside)
    result = _read(home)
    assert result.chats == () and result.problems == ("files_skipped",)


def test_compressed_sessions_need_a_decoder_and_archived_copies_are_read_once(tmp_path):
    home = tmp_path / "home"
    _write(home, "thread-cold", [_meta("thread-cold"), _event("user_message", message="Cold question.")], compress=True)
    _write(home, "thread-dup", [_meta("thread-dup"), _event("user_message", message="Live copy.")])
    _write(home, "thread-dup", [_meta("thread-dup"), _event("user_message", message="Archived copy.")], archived=True)
    result = _read(home)
    texts = {chat.source_chat_id: [m.text for m in chat.messages] for chat in result.chats}
    assert texts == {"thread-cold": ["Cold question."], "thread-dup": ["Live copy."]}
    paths = rollout.store_paths(home, {})
    without = rollout.read_stores(paths, LIMITS, decoder=None)
    assert {chat.source_chat_id for chat in without.chats} == {"thread-dup"}
    assert "rows_compressed_skipped" in without.problems


def test_store_over_the_limit_is_refused_and_the_home_setting_is_honoured(tmp_path):
    home = tmp_path / "home"
    moved = tmp_path / "elsewhere"
    agent = moved
    folder = agent / "sessions" / "2026" / "09" / "01"
    folder.mkdir(parents=True)
    (folder / "rollout-2026-09-01T10-00-00-thread-moved.jsonl").write_text(
        json.dumps(_meta("thread-moved")) + "\n" + json.dumps(_event("user_message", message="Moved home.")) + "\n")
    env = {"CODEX_HOME": str(agent)}
    assert [s["id"] for s in local_sources.detect(home=home, env=env, platform="linux")] == ["local_terminal_agent"]
    result = local_sources.read("local_terminal_agent", home=home, env=env, platform="linux")
    assert [m.text for m in result.chats[0].messages] == ["Moved home."]
    small = local_sources.read("local_terminal_agent", home=home, env=env, platform="linux",
                               limits=Limits(max_local_store_bytes=64))
    assert small.chats == () and small.problems == ("store_too_large",)
    assert local_sources.read("local_terminal_agent", home=tmp_path / "empty", env={}, platform="linux").problems == ("store_missing",)


def _agent_settings(home: Path) -> Path:
    root = home / ".codex"
    root.mkdir(parents=True, exist_ok=True)
    (root / "AGENTS.override.md").write_text("Global override: use the compass_marker.\n")
    (root / "AGENTS.md").write_text("Shadowed global shadow_marker.\n")
    (root / "config.toml").write_text(f'''
model = "gpt-new"
model_provider = "local-gateway"

[mcp_servers.ledger]
command = "python3"
args = ["-m", "ledger_server"]
env = {{ API_KEY = "{SECRET}" }}

[mcp_servers.remote]
url = "http://127.0.0.1:9/mcp"
http_headers = {{ Authorization = "Bearer {SECRET}" }}

[mcp_servers]
inline = {{ command = "x", env = {{ TOKEN = "{SECRET}" }} }}
''')
    (root / "prompts").mkdir()
    (root / "prompts" / "release.md").write_text("---\ndescription: Release checklist\n---\nRun the release_marker checklist.\n")
    (root / "memories").mkdir()
    (root / "memories" / "MEMORY.md").write_text("The harbor uses amber_marker labels.\n")
    return root


def test_review_follows_the_agents_instruction_precedence_and_never_reads_secrets(tmp_path):
    home = tmp_path / "home"
    _agent_settings(home)
    _write(home, "thread-legacy", _legacy())
    project = tmp_path / "harbor"
    (project / ".codex").mkdir(parents=True)
    (project / "AGENTS.override.md").write_text("")  # empty: the agent falls through to AGENTS.md
    (project / "AGENTS.md").write_text("Project rule: keep the project_marker ledger.\n")
    (project / "CLAUDE.md").write_text("Another tool's other_marker file.\n")
    (project / ".codex" / "config.toml").write_text('[mcp_servers.local]\ncommand = "node"\nargs = ["serve.js"]\n')
    (project / "ledger.md").write_text("ledger")
    [chat] = _read(home).chats
    before = _fingerprint(home)
    plan = review(chat, root=str(project), old_root="/old/harbor")
    text = json.dumps(plan)
    assert SECRET not in text
    instructions = {(row["source"], row["scope"]): row["text"] for row in plan["instructions"]}
    assert "compass_marker" in instructions[("AGENTS.override.md", "chat")]
    assert "project_marker" in instructions[("AGENTS.md", "project")]
    assert "shadow_marker" not in text and "other_marker" not in text
    assert {(tool["name"], tool["command"]) for tool in plan["tools"]} == {("ledger", "python3"), ("local", "node")}
    assert {"tool_transport_unsupported", "context_unreadable"} <= set(plan["warnings"])
    assert "gpt-new" in plan["models"] and "local-gateway" in plan["providers"]
    assert any("release_marker" in skill["body"] for skill in plan["skills"])
    assert any("amber_marker" in memory["text"] for memory in plan["memories"])
    files = {Path(row["path"]).name: row["status"] for row in plan["files"] if row["path"]}
    assert files.get("ledger.md") == "exists"
    assert _fingerprint(home) == before


def test_unreadable_override_is_not_replaced_by_the_shadowed_file(tmp_path):
    home = tmp_path / "home"
    root = home / ".codex"
    root.mkdir(parents=True)
    (root / "AGENTS.override.md").write_bytes(b"\xff\xfe broken")
    (root / "AGENTS.md").write_text("Shadowed shadow_marker.\n")
    _write(home, "thread-legacy", _legacy())
    [chat] = _read(home).chats
    project = tmp_path / "p"
    project.mkdir()
    plan = review(chat, root=str(project))
    assert "shadow_marker" not in json.dumps(plan) and "context_unreadable" in plan["warnings"]


# --------------------------------------------------------------------------- through the doors


@pytest.fixture
def served(app, monkeypatch):
    # The agent's own home setting must not point the served doors at a real folder.
    monkeypatch.delenv("CODEX_HOME", raising=False)
    return app


def test_import_through_the_doors_keeps_summary_behind_the_continue_choice(served, tmp_path):
    from core.execution.mcp_bridge import load_mcp_server_configs
    from core.prompt_normalizer import _reviewed_working_context_messages

    app = served
    home = Path(os.environ["HOME"])
    _agent_settings(home)
    _write(home, "thread-legacy", _legacy())
    project = tmp_path / "harbor-project"
    project.mkdir()
    (project / "AGENTS.md").write_text("Project rule: keep the project_marker ledger.\n")
    before = _fingerprint(home)
    status, sources = _call(app, "GET", "/api/chat/import/sources")
    assert status == 200 and [s["kind"] for s in sources["local"]] == ["terminal_agent"]
    runs = {}
    for continuation in (False, True):
        status, preview = _call(app, "POST", "/api/chat/import/preview", body={"source": "local_terminal_agent"})
        assert status == 200, preview
        # No thread name was saved, so the first thing the person typed names the chat.
        assert [c["title"] for c in preview["chats"]] == ["Plan the harbor release."]
        status, reviewed = _call(app, "POST", "/api/chat/import/context", body={
            "preview_id": preview["preview_id"], "ref": "0", "root": str(project), "old_root": "/old/harbor"})
        assert status == 200, reviewed
        assert SECRET not in json.dumps(reviewed)
        runs[continuation] = _finish(app, preview, continuation=continuation, contexts={"0": {
            "plan_id": reviewed["plan_id"], "options": {"instructions": True, "tools": True, "skills": True}}})
        if not continuation:
            status, result = _call(app, "POST", "/api/chat/import/undo", body={"batch_id": runs[False]["run_id"]})
            assert status == 200, result
    sid = runs[True]["first_session_id"]
    history = store.continuation_history(sid)
    assert [row["content"] for row in history][:2] == ["Plan the harbor release.", "First, check the tide table."]
    evidence = _reviewed_working_context_messages({"runtime_session_id": sid})[0].content
    assert "tide checked" in evidence and "Write the release ledger" in evidence
    assert load_mcp_server_configs() == []
    assert _fingerprint(home) == before


def test_summary_and_plan_need_the_continue_choice(served, tmp_path):
    from core.prompt_normalizer import _reviewed_working_context_messages

    app = served
    home = Path(os.environ["HOME"])
    _write(home, "thread-legacy", _legacy())
    project = tmp_path / "plain-project"
    project.mkdir()
    status, preview = _call(app, "POST", "/api/chat/import/preview", body={"source": "local_terminal_agent"})
    assert status == 200, preview
    status, reviewed = _call(app, "POST", "/api/chat/import/context", body={
        "preview_id": preview["preview_id"], "ref": "0", "root": str(project)})
    assert status == 200, reviewed
    run = _finish(app, preview, continuation=False, contexts={"0": {"plan_id": reviewed["plan_id"], "options": {}}})
    sid = run["first_session_id"]
    assert store.continuation_history(sid) == []
    messages = _reviewed_working_context_messages({"runtime_session_id": sid})
    text = messages[0].content if messages else ""
    assert "tide checked" not in text and "release ledger" not in text
    assert "gpt-new" in text  # settings evidence does not need the transcript choice


def test_reviewing_does_not_crawl_the_agents_session_folders(tmp_path):
    home = tmp_path / "home"
    _write(home, "thread-legacy", _legacy())
    for day in range(300):
        (home / ".codex" / "sessions" / "2025" / f"{day // 28 + 1:02d}" / f"{day % 28 + 1:02d}").mkdir(parents=True, exist_ok=True)
    [chat] = _read(home).chats
    project = tmp_path / "p"
    project.mkdir()
    plan = review(chat, root=str(project))
    assert "context_limit" not in plan["warnings"]
