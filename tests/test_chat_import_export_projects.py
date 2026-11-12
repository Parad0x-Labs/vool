"""Projects carried by an account export with message-list chats (``projects.json`` next to them).

The vendor publishes no schema for this member, so the reader uses only plain string fields of
the expected names (``uuid``, ``name``, ``description``, ``prompt_template``, ``docs[].filename``,
``docs[].content``) and links a chat only through a project id the chat itself carries. Anything
else is reported, never spread over every chat. Project instructions become reviewed project
instructions; descriptions and documents become reviewed context data.
"""
from __future__ import annotations

import io
import json
import zipfile

from core.chat_import import readers, store
from core.chat_import.common import Limits
from core.user_rules import active_rules
from tests.test_chat_import_migration import _finish
from tests.test_chat_import_migration import app as app
from tests.test_chat_import_served import _call, _conversation, _upload_and_preview
from tests.test_chat_import_served import app as import_app

PROJECT = {"uuid": "proj-harbor", "name": "Harbor", "description": "Releases for the harbor app.",
           "prompt_template": "Always keep the harbor_marker ledger.",
           "docs": [{"filename": "release.md", "content": "Release steps: tide_marker first."}]}


def _zip(conversations: list, projects=None, *, raw_projects: bytes | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("conversations.json", json.dumps(conversations))
        if projects is not None:
            archive.writestr("projects.json", json.dumps(projects))
        if raw_projects is not None:
            archive.writestr("projects.json", raw_projects)
    return buffer.getvalue()


def _chats(project_link: dict | None = None) -> list:
    linked = _conversation("chat-linked", "Harbor plan", [("human", "Plan it.", "2025-03-01T09:00:00Z"),
                                                          ("assistant", "Planned.", "2025-03-01T09:00:01Z")])
    linked.update(project_link if project_link is not None else {"project_uuid": "proj-harbor"})
    loose = _conversation("chat-loose", "Bread", [("human", "Bake?", "2025-03-01T09:00:00Z")])
    return [linked, loose]


def test_a_linked_chat_carries_its_projects_instructions_and_documents():
    result = readers.read_bytes(_zip(_chats(), [PROJECT]))
    assert result.problems == ()
    linked, loose = result.chats
    assert linked.context["instructions"] == [
        {"text": "Always keep the harbor_marker ledger.", "source": "project_instructions", "scope": "project"}]
    notes = {note["source"]: note["text"] for note in linked.context["notes"]}
    assert notes["project"] == "Harbor\nReleases for the harbor app."
    assert notes["project_document"] == "release.md\n\nRelease steps: tide_marker first."
    assert linked.context["project_name"] == "Harbor"
    assert loose.context == {}


def test_the_nested_project_id_links_too():
    result = readers.read_bytes(_zip(_chats({"project": {"uuid": "proj-harbor"}}), [PROJECT]))
    assert result.chats[0].context["project_name"] == "Harbor"


def test_projects_no_chat_names_are_reported_not_spread():
    result = readers.read_bytes(_zip(_chats({}), [PROJECT]))
    assert all(chat.context == {} for chat in result.chats)
    assert result.problems == ("projects_unlinked",)
    result = readers.read_bytes(_zip(_chats({"project_uuid": "proj-missing"}), [PROJECT]))
    assert result.chats[0].context == {} and result.problems == ("projects_unlinked",)


def test_damaged_unknown_and_oversized_project_lists_fail_plainly():
    for raw in (b"{not json", json.dumps({"projects": [PROJECT]}).encode(), b"\xff\xfe"):
        result = readers.read_bytes(_zip(_chats(), raw_projects=raw))
        assert [chat.source_chat_id for chat in result.chats] == ["chat-linked", "chat-loose"]
        assert result.problems == ("projects_unreadable",)
        assert result.chats[0].context == {}
    odd = readers.read_bytes(_zip(_chats(), [PROJECT, "not a project", {"name": "no id"}]))
    assert "projects_unreadable" in odd.problems and odd.chats[0].context["project_name"] == "Harbor"
    huge = dict(PROJECT, docs=[{"filename": "big.md", "content": "x" * 150_000}])
    cut = readers.read_bytes(_zip(_chats(), [huge]))
    assert cut.problems == ("project_document_cut",)
    assert len(cut.chats[0].context["notes"][-1]["text"]) == len("big.md\n\n") + 100_000
    small = readers.read_bytes(_zip(_chats(), [huge]), limits=Limits(max_conversation_bytes=20_000))
    assert small.problems == ("projects_unreadable",) and small.chats


def test_export_without_projects_reads_as_before():
    result = readers.read_bytes(_zip(_chats()))
    assert result.problems == () and all(chat.context == {} for chat in result.chats)


def test_project_instructions_and_documents_through_the_doors(app, tmp_path):
    from core.context_namespace import load_chat_namespace
    from core.prompt_normalizer import _reviewed_working_context_messages

    root = tmp_path / "harbor"
    root.mkdir()
    preview = _upload_and_preview(app, _zip(_chats(), [PROJECT]))
    status, reviewed = _call(app, "POST", "/api/chat/import/context", body={
        "preview_id": preview["preview_id"], "ref": "0", "root": str(root)})
    assert status == 200, reviewed
    assert any("harbor_marker" in row["text"] and row["scope"] == "project" for row in reviewed["context"]["instructions"])
    run = _finish(app, preview, continuation=False, contexts={"0": {
        "plan_id": reviewed["plan_id"], "options": {"instructions": True}}})
    sid = run["first_session_id"]
    namespace = load_chat_namespace(sid)
    assert any("harbor_marker" in rule.text for rule in active_rules(chat_id=sid, project_id=namespace.project_id))
    evidence = _reviewed_working_context_messages({"runtime_session_id": sid})[0].content
    assert "tide_marker" in evidence and "Releases for the harbor app." in evidence
    assert store.continuation_history(sid) == []
    status, result = _call(app, "POST", "/api/chat/import/undo", body={"batch_id": run["run_id"]})
    assert status == 200, result
    assert not any("harbor_marker" in rule.text for rule in active_rules())


def test_the_desktop_apps_tool_servers_are_reviewed_disabled_without_secrets(app, tmp_path):
    import os
    from pathlib import Path

    from core.execution.mcp_bridge import load_mcp_server_configs

    secret = "sk-" + "D" * 48
    folder = Path(os.environ["HOME"]) / "Library" / "Application Support" / "Claude"
    folder.mkdir(parents=True)
    config = folder / "claude_desktop_config.json"
    config.write_text(json.dumps({"mcpServers": {
        "files": {"command": "npx", "args": ["-y", "files-server", "/home/example/notes"], "env": {"TOKEN": secret}},
        "web": {"type": "http", "url": "http://127.0.0.1:9/mcp", "headers": {"Authorization": "Bearer " + secret}}},
        "globalShortcut": "x"}))
    before = config.read_bytes()
    root = tmp_path / "harbor"
    root.mkdir()
    preview = _upload_and_preview(app, _zip(_chats()))
    status, reviewed = _call(app, "POST", "/api/chat/import/context", body={
        "preview_id": preview["preview_id"], "ref": "0", "root": str(root)})
    assert status == 200, reviewed
    assert secret not in json.dumps(reviewed)
    tools = reviewed["context"]["tools"]
    assert [(tool["name"], tool["command"]) for tool in tools] == [("files", "npx")]
    assert "tool_transport_unsupported" in reviewed["context"]["warnings"]
    _finish(app, preview, continuation=False, contexts={"0": {"plan_id": reviewed["plan_id"], "options": {"tools": True}}})
    assert load_mcp_server_configs() == []
    assert config.read_bytes() == before
