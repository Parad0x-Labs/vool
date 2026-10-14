"""Chat history export readers: foreign export zips -> neutral in-memory chats.

The fixtures are synthetic zips built inside the test from the documented export shapes: real
export files are private user data, and the reader contract is pinned to those shapes, not to
samples. No binary fixtures are committed.
"""
from __future__ import annotations

import io
import json
import time
import zipfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from core.chat_import.model import ReadResult
from core.chat_import.readers import Limits, detect, read

#: Limits small enough that every cap can be crossed by an in-test fixture.
TINY = Limits(
    max_zip_bytes=10_000_000,
    max_member_bytes=10_000_000,
    max_total_uncompressed_bytes=10_000_000,
    max_members=64,
    max_conversations=64,
    max_messages_per_chat=64,
    max_message_chars=10_000,
    max_json_depth=64,
)


def _zip_files(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def _write_zip(tmp_path: Path, files: dict[str, bytes], name: str = "export.zip") -> Path:
    path = tmp_path / name
    path.write_bytes(_zip_files(files))
    return path


# --- fixture builders for the openai_export shape -------------------------------------------


def _openai_node(
    node_id: str,
    parent: str | None = None,
    children: tuple[str, ...] = (),
    *,
    role: str | None = None,
    parts: list | None = None,
    content_type: str = "text",
    metadata: dict | None = None,
    create_time: float | None = None,
) -> dict:
    message = None
    if role is not None:
        message = {
            "author": {"role": role},
            "content": {"content_type": content_type, "parts": [] if parts is None else parts},
            "create_time": create_time,
            "metadata": dict(metadata or {}),
        }
    return {"id": node_id, "message": message, "parent": parent, "children": list(children)}


def _openai_conversation(
    mapping: dict,
    current_node: str,
    *,
    title: str | None = "A chat",
    chat_id: str | None = "c1",
    create_time: float | None = 1700000000.0,
    update_time: float | None = 1700003600.0,
    **extra,
) -> dict:
    conversation = {
        "title": title,
        "create_time": create_time,
        "update_time": update_time,
        "id": chat_id,
        "mapping": mapping,
        "current_node": current_node,
    }
    conversation.update(extra)
    return conversation


def _branchy_openai_conversation() -> dict:
    # root -> user -> assistant(v1); the same user node also leads to assistant(v2, current).
    # v1 is the answer an edit threw away: not imported, counted as a hidden branch.
    return _openai_conversation(
        {
            "root": _openai_node("root"),
            "u1": _openai_node("u1", "root", ("a1", "a2"), role="user", parts=["what is 2+2?"]),
            "a1": _openai_node("a1", "u1", (), role="assistant", parts=["5"]),
            "a2": _openai_node("a2", "u1", (), role="assistant", parts=["4"]),
        },
        "a2",
    )


def _write_openai(tmp_path: Path, conversations: list, name: str = "export.zip") -> Path:
    return _write_zip(tmp_path, {"conversations.json": json.dumps(conversations).encode("utf-8")}, name)


# --- fixture builders for the anthropic_export shape ----------------------------------------


def _anthropic_message(
    sender: str,
    *,
    uuid: str = "m1",
    text: str | None = None,
    content: list | None = None,
    created_at: str | None = "2026-10-14T10:00:00.000000Z",
    attachments: tuple[str, ...] = (),
    files: tuple[str, ...] = (),
) -> dict:
    message: dict = {"uuid": uuid, "sender": sender, "created_at": created_at}
    if text is not None:
        message["text"] = text
    if content is not None:
        message["content"] = content
    if attachments:
        message["attachments"] = [{"file_name": name, "size": 1} for name in attachments]
    if files:
        message["files"] = [{"file_name": name} for name in files]
    return message


def _anthropic_conversation(
    messages: list,
    *,
    uuid: str | None = "c1",
    name: str | None = "A chat",
    created_at: str | None = "2026-10-14T09:00:00.000000Z",
    updated_at: str | None = "2026-10-14T11:00:00.000000Z",
    **extra,
) -> dict:
    conversation = {
        "uuid": uuid,
        "name": name,
        "created_at": created_at,
        "updated_at": updated_at,
        "chat_messages": messages,
    }
    conversation.update(extra)
    return conversation


def _write_anthropic(tmp_path: Path, conversations: list, name: str = "export.zip") -> Path:
    return _write_zip(tmp_path, {"conversations.json": json.dumps(conversations).encode("utf-8")}, name)


def _assert_refusal(result: ReadResult, needle: str) -> None:
    assert isinstance(result, ReadResult)
    assert result.chats == ()
    assert result.truncated is False
    assert result.problems
    assert any(needle in problem for problem in result.problems), result.problems


# --- openai_export reading -------------------------------------------------------------------


def test_openai_imports_only_the_current_branch(tmp_path):
    result = read(_write_openai(tmp_path, [_branchy_openai_conversation()]))
    assert result.source_format == "openai_export"
    assert result.problems == ()
    assert result.truncated is False
    chat = result.chats[0]
    assert [(message.role, message.text) for message in chat.messages] == [
        ("user", "what is 2+2?"),
        ("assistant", "4"),
    ]
    assert chat.skipped == {"hidden_branch": 1}
    assert chat.source_chat_id == "c1"
    assert chat.title == "A chat"
    assert chat.created_at == 1700000000.0
    assert chat.updated_at == 1700003600.0


def test_openai_skips_system_and_tool_messages(tmp_path):
    conversation = _openai_conversation(
        {
            "root": _openai_node("root"),
            "s": _openai_node("s", "root", ("t",), role="system", parts=["You are helpful."]),
            "t": _openai_node("t", "s", ("u",), role="tool", parts=["tool output"]),
            "u": _openai_node("u", "t", ("a",), role="user", parts=["hi"]),
            "a": _openai_node("a", "u", (), role="assistant", parts=["hello"]),
        },
        "a",
    )
    result = read(_write_openai(tmp_path, [conversation]))
    chat = result.chats[0]
    assert [(message.role, message.text) for message in chat.messages] == [
        ("user", "hi"),
        ("assistant", "hello"),
    ]
    assert chat.skipped == {"system": 1, "tool": 1}


def test_openai_skips_visually_hidden_messages(tmp_path):
    conversation = _openai_conversation(
        {
            "root": _openai_node("root"),
            "u": _openai_node("u", "root", ("h",), role="user", parts=["hi"]),
            "h": _openai_node(
                "h", "u", ("a",), role="assistant", parts=["draft"], metadata={"is_visually_hidden_from_conversation": True}
            ),
            "a": _openai_node("a", "h", (), role="assistant", parts=["hello"]),
        },
        "a",
    )
    result = read(_write_openai(tmp_path, [conversation]))
    chat = result.chats[0]
    assert [message.text for message in chat.messages] == ["hi", "hello"]
    assert chat.skipped == {"hidden": 1}


def test_openai_image_only_message_counted_non_text(tmp_path):
    conversation = _openai_conversation(
        {
            "root": _openai_node("root"),
            "u": _openai_node("u", "root", ("i",), role="user", parts=["show me a cat"]),
            "i": _openai_node(
                "i",
                "u",
                (),
                role="assistant",
                content_type="image_asset_pointer",
                parts=[{"content_type": "image_asset_pointer", "asset_pointer": "file://cat.png"}],
            ),
        },
        "i",
    )
    result = read(_write_openai(tmp_path, [conversation]))
    chat = result.chats[0]
    assert [message.text for message in chat.messages] == ["show me a cat"]
    assert chat.skipped == {"non_text": 1}


def test_openai_keeps_string_parts_of_mixed_content(tmp_path):
    conversation = _openai_conversation(
        {
            "root": _openai_node("root"),
            "u": _openai_node("u", "root", ("a",), role="user", parts=["what is this?"]),
            "a": _openai_node(
                "a",
                "u",
                (),
                role="assistant",
                content_type="multimodal_text",
                parts=["look at this", {"content_type": "image_asset_pointer", "asset_pointer": "file://x"}],
            ),
        },
        "a",
    )
    result = read(_write_openai(tmp_path, [conversation]))
    chat = result.chats[0]
    assert [message.text for message in chat.messages] == ["what is this?", "look at this"]
    assert chat.skipped == {}


def test_openai_empty_messages_counted_empty(tmp_path):
    conversation = _openai_conversation(
        {
            "root": _openai_node("root"),
            "u": _openai_node("u", "root", ("e",), role="user", parts=["hi"]),
            "e": _openai_node("e", "u", ("b",), role="assistant", parts=[]),
            "b": _openai_node("b", "e", ("a",), role="assistant", parts=[""]),
            "a": _openai_node("a", "b", (), role="assistant", parts=["done"]),
        },
        "a",
    )
    result = read(_write_openai(tmp_path, [conversation]))
    chat = result.chats[0]
    assert [message.text for message in chat.messages] == ["hi", "done"]
    assert chat.skipped == {"empty": 2}


def test_openai_times_ids_and_empty_title(tmp_path):
    conversation = _openai_conversation(
        {
            "root": _openai_node("root"),
            "u": _openai_node("u", "root", ("a",), role="user", parts=["hi"], create_time=1700000050.5),
            "a": _openai_node("a", "u", (), role="assistant", parts=["hello"], create_time=1700000060.25),
        },
        "a",
        title=None,
        chat_id=None,
        conversation_id="fallback-id",
    )
    result = read(_write_openai(tmp_path, [conversation]))
    chat = result.chats[0]
    assert chat.title == ""
    assert chat.source_chat_id == "fallback-id"
    assert [message.created_at for message in chat.messages] == [1700000050.5, 1700000060.25]


def test_openai_unicode_text_survives(tmp_path):
    user_text = "مرحبا بالعالم こんにちは世界 🌍👋🏽 ¿qué tal?"
    assistant_text = "مرحبا! こんにちは 🌏✨"
    conversation = _openai_conversation(
        {
            "root": _openai_node("root"),
            "u": _openai_node("u", "root", ("a",), role="user", parts=[user_text]),
            "a": _openai_node("a", "u", (), role="assistant", parts=[assistant_text]),
        },
        "a",
    )
    result = read(_write_openai(tmp_path, [conversation]))
    chat = result.chats[0]
    assert [message.text for message in chat.messages] == [user_text, assistant_text]


def test_openai_broken_conversations_are_skipped_not_fatal(tmp_path):
    conversations = [42, {"title": "no tree here"}, _branchy_openai_conversation()]
    result = read(_write_openai(tmp_path, conversations))
    assert len(result.chats) == 1
    assert len(result.problems) == 2


def test_openai_unknown_current_node_skips_the_conversation(tmp_path):
    conversation = _openai_conversation(
        {"root": _openai_node("root"), "u": _openai_node("u", "root", (), role="user", parts=["hi"])},
        "missing-node",
    )
    result = read(_write_openai(tmp_path, [conversation]))
    assert result.chats == ()
    assert result.problems


def test_openai_parent_cycle_terminates(tmp_path):
    # A malformed mapping whose parents loop must not hang the walk.
    conversation = _openai_conversation(
        {
            "a": _openai_node("a", "b", (), role="user", parts=["one"]),
            "b": _openai_node("b", "a", (), role="assistant", parts=["two"]),
        },
        "a",
    )
    result = read(_write_openai(tmp_path, [conversation]))
    assert len(result.chats) == 1
    assert len(result.chats[0].messages) == 2


# --- anthropic_export reading ----------------------------------------------------------------


def test_anthropic_imports_messages_in_order_with_iso_times(tmp_path):
    conversation = _anthropic_conversation(
        [
            _anthropic_message("human", uuid="m1", text="hello there"),
            _anthropic_message("assistant", uuid="m2", text="hi, how can I help?", created_at="2026-10-14T10:00:05.500000Z"),
        ]
    )
    result = read(_write_anthropic(tmp_path, [conversation]))
    assert result.source_format == "anthropic_export"
    assert result.problems == ()
    chat = result.chats[0]
    assert chat.title == "A chat"
    assert chat.source_chat_id == "c1"
    assert chat.created_at == datetime(2026, 10, 14, 9, 0, 0, tzinfo=timezone.utc).timestamp()
    assert chat.updated_at == datetime(2026, 10, 14, 11, 0, 0, tzinfo=timezone.utc).timestamp()
    assert [(message.role, message.created_at) for message in chat.messages] == [
        ("user", datetime(2026, 10, 14, 10, 0, 0, tzinfo=timezone.utc).timestamp()),
        ("assistant", datetime(2026, 10, 14, 10, 0, 5, 500000, tzinfo=timezone.utc).timestamp()),
    ]
    assert [message.text for message in chat.messages] == ["hello there", "hi, how can I help?"]


def test_anthropic_prefers_content_blocks_over_the_text_field(tmp_path):
    conversation = _anthropic_conversation(
        [
            _anthropic_message(
                "human",
                text="stale field text",
                content=[{"type": "text", "text": "from content"}, {"type": "text", "text": " blocks"}],
            )
        ]
    )
    result = read(_write_anthropic(tmp_path, [conversation]))
    assert [message.text for message in result.chats[0].messages] == ["from content blocks"]


def test_anthropic_falls_back_to_the_text_field(tmp_path):
    conversation = _anthropic_conversation([_anthropic_message("human", text="plain field text")])
    result = read(_write_anthropic(tmp_path, [conversation]))
    assert [message.text for message in result.chats[0].messages] == ["plain field text"]


def test_anthropic_image_only_message_counted_non_text(tmp_path):
    conversation = _anthropic_conversation(
        [
            _anthropic_message("human", text="", content=[{"type": "image", "source": {"data": "bytes"}}]),
            _anthropic_message("assistant", text="here you go"),
        ]
    )
    result = read(_write_anthropic(tmp_path, [conversation]))
    chat = result.chats[0]
    assert [message.text for message in chat.messages] == ["here you go"]
    assert chat.skipped == {"non_text": 1}


def test_anthropic_attachment_names_only(tmp_path):
    conversation = _anthropic_conversation(
        [
            _anthropic_message(
                "human",
                text="see these files",
                attachments=("report.pdf", "data.csv"),
                files=("picture.png",),
            )
        ]
    )
    result = read(_write_anthropic(tmp_path, [conversation]))
    message = result.chats[0].messages[0]
    assert message.text == "see these files"
    assert message.attachment_names == ("report.pdf", "data.csv", "picture.png")


def test_anthropic_empty_title_and_unparseable_times_stay_calm(tmp_path):
    conversation = _anthropic_conversation(
        [_anthropic_message("human", text="hi")],
        name="",
        created_at="not a date at all",
        updated_at=None,
    )
    result = read(_write_anthropic(tmp_path, [conversation]))
    chat = result.chats[0]
    assert chat.title == ""
    assert chat.created_at is None
    assert chat.updated_at is None
    assert [message.text for message in chat.messages] == ["hi"]


def test_anthropic_unicode_text_survives(tmp_path):
    user_text = "كيف حالك はじめまして 🙏🍀"
    conversation = _anthropic_conversation(
        [_anthropic_message("human", text=user_text), _anthropic_message("assistant", text="بخير، شكراً! どうぞ 🙂")]
    )
    result = read(_write_anthropic(tmp_path, [conversation]))
    assert [message.text for message in result.chats[0].messages] == [user_text, "بخير، شكراً! どうぞ 🙂"]


def test_anthropic_unknown_sender_counted_skipped(tmp_path):
    conversation = _anthropic_conversation(
        [
            _anthropic_message("human", uuid="m1", text="hi"),
            _anthropic_message("system", uuid="m2", text="internal note"),
            _anthropic_message("assistant", uuid="m3", text="hello"),
        ]
    )
    result = read(_write_anthropic(tmp_path, [conversation]))
    chat = result.chats[0]
    assert [(message.role, message.text) for message in chat.messages] == [("user", "hi"), ("assistant", "hello")]
    assert chat.skipped == {"system": 1}


# --- detection --------------------------------------------------------------------------------


def test_detect_uses_content_not_the_file_name(tmp_path):
    # The file names deliberately lie about the format; only the content may decide.
    openai_named_wrong = _write_openai(tmp_path, [_branchy_openai_conversation()], name="anthropic_export.zip")
    anthropic_named_wrong = _write_anthropic(
        tmp_path, [_anthropic_conversation([_anthropic_message("human", text="hi")])], name="openai_export.zip"
    )
    assert detect(openai_named_wrong) == "openai_export"
    assert detect(anthropic_named_wrong) == "anthropic_export"
    assert read(openai_named_wrong).source_format == "openai_export"
    assert read(anthropic_named_wrong).source_format == "anthropic_export"


def test_detect_returns_none_for_unrelated_files(tmp_path):
    assert detect(_write_zip(tmp_path, {"hello.txt": b"hi"})) is None
    assert detect(_write_zip(tmp_path, {"other.json": b"[]"})) is None
    assert detect(_write_zip(tmp_path, {"conversations.json": b"[]"})) is None

    not_a_zip = tmp_path / "plain.txt"
    not_a_zip.write_bytes(b"this is definitely not a zip archive")
    assert detect(not_a_zip) is None
    assert detect(tmp_path / "does-not-exist.zip") is None


# --- safety: refusals -------------------------------------------------------------------------


def test_read_refuses_a_zip_bomb_quickly(tmp_path):
    # Five megabytes of zeros compress to almost nothing; the member cap is far lower, so the
    # reader must refuse from the size information instead of inflating the member.
    path = _write_zip(tmp_path, {"conversations.json": b"0" * 5_000_000})
    limits = replace(TINY, max_member_bytes=100_000)
    started = time.monotonic()
    result = read(path, limits=limits)
    assert time.monotonic() - started < 5.0
    _assert_refusal(result, "larger than the import limit")


def test_read_refuses_too_many_members(tmp_path):
    path = _write_zip(tmp_path, {"a.txt": b"x", "b.txt": b"x", "conversations.json": b"[]"})
    _assert_refusal(read(path, limits=replace(TINY, max_members=2)), "more members")


def test_read_refuses_escaping_member_names(tmp_path):
    path = _write_zip(tmp_path, {"../outside.txt": b"x", "conversations.json": b"[]"})
    _assert_refusal(read(path, limits=TINY), "escapes the archive")


def test_read_refuses_absolute_member_names(tmp_path):
    path = _write_zip(tmp_path, {"/etc/passwd": b"x", "conversations.json": b"[]"})
    _assert_refusal(read(path, limits=TINY), "absolute path")


def test_read_refuses_symlink_members(tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        link = zipfile.ZipInfo("link.txt")
        link.create_system = 3  # unix attributes follow
        link.external_attr = 0o120777 << 16  # regular symlink bits
        archive.writestr(link, b"conversations.json")
        archive.writestr("conversations.json", b"[]")
    path = tmp_path / "symlink.zip"
    path.write_bytes(buffer.getvalue())
    _assert_refusal(read(path, limits=TINY), "symbolic link")


def test_read_refuses_encrypted_members(tmp_path):
    # zipfile cannot write encrypted members, so the fixture claims the encrypted flag on an
    # otherwise plain member the way a real encrypted archive's directory does.
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("secret.txt", b"x")
        archive.writestr("conversations.json", b"[]")
        archive.filelist[0].flag_bits |= 0x1
    path = tmp_path / "encrypted.zip"
    path.write_bytes(buffer.getvalue())
    _assert_refusal(read(path, limits=TINY), "encrypted")


def test_read_refuses_deeply_nested_json(tmp_path):
    payload = b"[" + b'{"a":' * 200 + b"1" + b"}" * 200 + b"]"
    path = _write_zip(tmp_path, {"conversations.json": payload})
    _assert_refusal(read(path, limits=TINY), "nests")


def test_read_refuses_invalid_utf8(tmp_path):
    path = _write_zip(tmp_path, {"conversations.json": b'["\xff\xfe broken"]'})
    _assert_refusal(read(path, limits=TINY), "UTF-8")


def test_read_refuses_invalid_json(tmp_path):
    path = _write_zip(tmp_path, {"conversations.json": b"this is not json"})
    _assert_refusal(read(path, limits=TINY), "not valid JSON")


def test_read_refuses_json_that_is_not_a_list(tmp_path):
    path = _write_zip(tmp_path, {"conversations.json": b'{"a": 1}'})
    _assert_refusal(read(path, limits=TINY), "does not hold a list")


def test_read_refuses_an_oversized_zip_on_disk(tmp_path):
    path = _write_openai(tmp_path, [_branchy_openai_conversation()])
    _assert_refusal(read(path, limits=replace(TINY, max_zip_bytes=10)), "larger than the import limit")


def test_read_refuses_total_inflation_over_the_cap(tmp_path):
    path = _write_zip(tmp_path, {"one.bin": b"0" * 60_000, "two.bin": b"0" * 60_000, "conversations.json": b"[]"})
    _assert_refusal(read(path, limits=replace(TINY, max_total_uncompressed_bytes=100_000)), "expands larger")


def test_read_returns_a_result_for_a_non_zip(tmp_path):
    not_a_zip = tmp_path / "plain.bin"
    not_a_zip.write_bytes(b"not a zip")
    _assert_refusal(read(not_a_zip, limits=TINY), "not a zip archive")


# --- safety: caps inside a good file -----------------------------------------------------------


def test_read_truncates_long_message_text(tmp_path):
    conversation = _openai_conversation(
        {
            "root": _openai_node("root"),
            "u": _openai_node("u", "root", ("a",), role="user", parts=["a" * 40]),
            "a": _openai_node("a", "u", (), role="assistant", parts=["b" * 40]),
        },
        "a",
    )
    path = _write_openai(tmp_path, [conversation])
    result = read(path, limits=replace(TINY, max_message_chars=10))
    chat = result.chats[0]
    assert [message.text for message in chat.messages] == ["a" * 10, "b" * 10]
    assert result.truncated is True


def test_read_stops_at_the_conversation_cap(tmp_path):
    conversations = [_branchy_openai_conversation(), _branchy_openai_conversation()]
    path = _write_openai(tmp_path, conversations)
    result = read(path, limits=replace(TINY, max_conversations=1))
    assert len(result.chats) == 1
    assert result.truncated is True
    assert any("stopped after 1 of 2" in problem for problem in result.problems)


def test_read_stops_at_the_message_cap(tmp_path):
    conversation = _openai_conversation(
        {
            "root": _openai_node("root"),
            "u": _openai_node("u", "root", ("a",), role="user", parts=["one"]),
            "a": _openai_node("a", "u", ("b",), role="assistant", parts=["two"]),
            "b": _openai_node("b", "a", (), role="user", parts=["three"]),
        },
        "b",
    )
    path = _write_openai(tmp_path, [conversation])
    result = read(path, limits=replace(TINY, max_messages_per_chat=2))
    chat = result.chats[0]
    assert [message.text for message in chat.messages] == ["one", "two"]
    assert chat.skipped == {"limit": 1}


# --- the readers only read ----------------------------------------------------------------------


def test_read_never_writes_anything(tmp_path, monkeypatch):
    import os

    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    export = _write_openai(tmp_path, [_branchy_openai_conversation()])

    monkeypatch.chdir(sandbox)
    monkeypatch.setenv("TMPDIR", str(sandbox))
    monkeypatch.setenv("HOME", str(fake_home))

    result = read(export)

    assert result.chats
    assert os.listdir(sandbox) == []
    assert os.listdir(fake_home) == []
