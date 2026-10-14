"""Read chat-history export files (the data-export zips AI apps let a user download).

``read(path)`` turns a file the user picked into a neutral, validated list of chats in memory
(see ``core.chat_import.model``). The readers only read: the zip is opened in memory and never
extracted, and no database, setting or file is written. Which format a file holds is decided by
the shape of its chat-history content, never by the file name.

The chat history sits in ``conversations.json`` or, in newer exports, is split across
``conversations-000.json``, ``conversations-001.json`` and so on. Each document is a JSON list
of conversations or an object ``{"conversations": [...]}``.

The input is untrusted, so every limit in ``Limits`` is enforced before and while reading, and
bad input never raises out of ``read``: a refusal or a skipped item comes back as a problem
code on the ``ReadResult``.
"""
from __future__ import annotations

import abc
import json
import os
import re
import zipfile
from typing import Any

from core.chat_import.common import (
    DEFAULT_LIMITS,
    Limits,
    as_text,
    bump,
    epoch_seconds,
    iso_seconds,
    json_depth_exceeds,
    make_message,
    refusal,
    unique_names,
)
from core.chat_import.model import ImportedChat, ImportedMessage, ReadResult

#: The single-document chat-history member, and the numbered parts of a split export.
CONVERSATIONS_MEMBER = "conversations.json"
_SPLIT_MEMBER = re.compile(r"^conversations-(\d{1,6})\.json$")

_WINDOWS_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")

#: Unix symlink file-type bits as zipfile stores them in ``external_attr``.
_SYMLINK_MODE = 0o120000

_ZIP_ENCRYPTED_FLAG = 0x1

#: How many conversations are inspected when recognising the format.
_DETECT_SAMPLE = 25


class FormatReader(abc.ABC):
    """One export format. Subclass and ``register_reader`` an instance to add a format."""

    name: str = ""

    @abc.abstractmethod
    def matches(self, conversations: list) -> bool:
        """True when the parsed conversation list has this format's shape."""

    @abc.abstractmethod
    def read_chat(self, conversation: Any, limits: Limits, problems: list[str]) -> tuple[ImportedChat | None, bool]:
        """One conversation as (chat or None when skipped, truncated)."""


_READER_REGISTRY: list[FormatReader] = []


def register_reader(reader: FormatReader) -> None:
    """Teach ``detect`` and ``read`` a new export format."""
    if not reader.name:
        raise ValueError("a reader needs a format name")
    _READER_REGISTRY.append(reader)


def _signal_count(conversations: list, key: str, kind: type) -> int:
    """How many sampled conversations carry ``key`` with the expected kind."""
    return sum(
        1
        for conversation in conversations[:_DETECT_SAMPLE]
        if isinstance(conversation, dict) and isinstance(conversation.get(key), kind)
    )


def _member_problem(infolist: list[zipfile.ZipInfo], limits: Limits) -> str | None:
    """Refuse archives with unsafe members or sizes, before any member is read."""
    if len(infolist) > limits.max_members:
        return "zip_too_many_members"
    total = 0
    for info in infolist:
        if info.flag_bits & _ZIP_ENCRYPTED_FLAG:
            return "zip_encrypted"
        name = info.filename
        if name.startswith(("/", "\\")) or _WINDOWS_DRIVE_PREFIX.match(name) or "\x00" in name:
            return "zip_unsafe_path"
        if ".." in re.split(r"[/\\]", name):
            return "zip_unsafe_path"
        if (info.external_attr >> 16) & 0o170000 == _SYMLINK_MODE:
            return "zip_symlink"
        total += info.file_size
    if total > limits.max_total_uncompressed_bytes:
        return "zip_too_large_unpacked"
    return None


def _history_members(names: list[str]) -> list[str]:
    """The chat-history members at the archive root, in reading order."""
    members = [CONVERSATIONS_MEMBER] if CONVERSATIONS_MEMBER in names else []
    split = sorted(
        (int(match.group(1)), name)
        for name in names
        if (match := _SPLIT_MEMBER.match(name)) is not None
    )
    return members + [name for _number, name in split]


def _parse_document(raw: bytes, limits: Limits) -> tuple[list | None, str | None]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, "conversations_not_utf8"
    if json_depth_exceeds(raw, limits.max_json_depth):
        return None, "conversations_too_deep"
    try:
        parsed = json.loads(text)
    except (ValueError, RecursionError):
        return None, "conversations_not_json"
    if isinstance(parsed, dict) and isinstance(parsed.get("conversations"), list):
        parsed = parsed["conversations"]
    if not isinstance(parsed, list):
        return None, "conversations_not_list"
    return parsed, None


def _load_conversations(path: os.PathLike[str] | str, limits: Limits) -> tuple[list | None, str | None]:
    """Open the archive safely and parse every chat-history member; exactly one result is None.

    Reads happen in memory only (``zipfile`` member reads, never extraction to disk), and each
    read is bounded so a lying header cannot inflate past the cap.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return None, "file_unreadable"
    if size > limits.max_zip_bytes:
        return None, "file_too_large"
    conversations: list = []
    budget = limits.max_conversation_bytes
    try:
        with zipfile.ZipFile(path) as archive:
            infolist = archive.infolist()
            problem = _member_problem(infolist, limits)
            if problem is not None:
                return None, problem
            members = _history_members([info.filename for info in infolist])
            if not members:
                return None, "no_conversations"
            for member in members:
                info = archive.getinfo(member)
                if info.file_size > limits.max_member_bytes or info.file_size > budget:
                    return None, "conversations_too_large"
                cap = min(limits.max_member_bytes, budget)
                try:
                    with archive.open(member) as handle:
                        raw = handle.read(cap + 1)
                except (OSError, RuntimeError, ValueError, NotImplementedError, EOFError, zipfile.BadZipFile):
                    return None, "conversations_unreadable"
                if len(raw) > cap:
                    return None, "conversations_too_large"
                budget -= len(raw)
                parsed, problem = _parse_document(raw, limits)
                if parsed is None:
                    return None, problem
                conversations.extend(parsed)
    except zipfile.BadZipFile:
        return None, "not_a_zip"
    except OSError:
        return None, "file_unreadable"
    return conversations, None


def _detect_reader(conversations: list) -> FormatReader | None:
    for reader in _READER_REGISTRY:
        if reader.matches(conversations):
            return reader
    return None


def detect(path: os.PathLike[str] | str, *, limits: Limits = DEFAULT_LIMITS) -> str | None:
    """Which export format this file holds, judged only by the content's shape.

    The file name is never consulted. None means the file is not a readable export of any
    known format (missing, not a zip, unsafe, or unrecognised content).
    """
    conversations, _problem = _load_conversations(path, limits)
    if conversations is None:
        return None
    reader = _detect_reader(conversations)
    return reader.name if reader is not None else None


def read(path: os.PathLike[str] | str, *, limits: Limits = DEFAULT_LIMITS) -> ReadResult:
    """Read one export file into neutral chats in memory.

    Never raises for bad input: a refusal or a skipped conversation comes back as a problem
    code on the result instead. Nothing is written anywhere.
    """
    conversations, problem = _load_conversations(path, limits)
    if conversations is None:
        return refusal(problem or "file_unreadable")
    reader = _detect_reader(conversations)
    if reader is None:
        return refusal("format_unknown")
    return read_all(reader.name, conversations, limits, reader.read_chat)


def read_all(source_format: str, conversations: list, limits: Limits, read_one: Any) -> ReadResult:
    """Shared conversation loop: cap the count, tolerate broken items, read each id once."""
    chats: list[ImportedChat] = []
    problems: list[str] = []
    truncated = False
    seen_ids: set[str] = set()
    if len(conversations) > limits.max_conversations:
        truncated = True
        problems.append("too_many_chats")
    for conversation in conversations[: limits.max_conversations]:
        try:
            chat, chat_truncated = read_one(conversation, limits, problems)
        except Exception:
            chat, chat_truncated = None, False
            problems.append("chat_skipped_malformed")
        truncated = truncated or chat_truncated
        if chat is None:
            continue
        if chat.source_chat_id:
            if chat.source_chat_id in seen_ids:
                problems.append("chat_skipped_duplicate")
                continue
            seen_ids.add(chat.source_chat_id)
        chats.append(chat)
    return ReadResult(
        source_format=source_format,
        chats=tuple(chats),
        problems=tuple(dict.fromkeys(problems)),
        truncated=truncated,
    )


# --------------------------------------------------------------------------- mapping-tree export
#
# Shape (community-verified): conversations carry ``mapping`` (node id -> {id, message, parent,
# children}) and ``current_node``. The chat the user saw is the path from current_node up to the
# root, reversed; other branches are edits or regenerated answers.

#: Content types whose string parts are the words of the message.
_MAPPING_TEXT_TYPES = frozenset({"text", "multimodal_text"})
#: Content types that are the model's reasoning, never imported as text.
_MAPPING_THINKING_TYPES = frozenset({"thoughts", "reasoning_recap"})
#: Content types that are custom instructions or profile context, never chat words.
_MAPPING_CONTEXT_TYPES = frozenset({"user_editable_context", "model_editable_context"})


class _MappingTreeExportReader(FormatReader):
    """The export whose conversations hold ``mapping`` trees with a ``current_node``."""

    name = "openai_export"

    def matches(self, conversations: list) -> bool:
        return _signal_count(conversations, "mapping", dict) > 0

    def read_chat(self, conversation: Any, limits: Limits, problems: list[str]) -> tuple[ImportedChat | None, bool]:
        if not isinstance(conversation, dict):
            problems.append("chat_skipped_malformed")
            return None, False
        mapping = conversation.get("mapping")
        current = conversation.get("current_node")
        if not isinstance(mapping, dict) or not isinstance(current, str) or current not in mapping:
            problems.append("chat_skipped_malformed")
            return None, False

        # The seen-set keeps a malformed parent loop from hanging the walk; the cap keeps an
        # endless chain bounded.
        path: list[str] = []
        seen: set[str] = set()
        node_id: str | None = current
        walk_cap = limits.max_messages_per_chat * 4 + 1024
        while node_id is not None and node_id in mapping and node_id not in seen and len(path) <= walk_cap:
            seen.add(node_id)
            path.append(node_id)
            node = mapping.get(node_id)
            parent = node.get("parent") if isinstance(node, dict) else None
            node_id = parent if isinstance(parent, str) else None
        path.reverse()

        skipped: dict[str, int] = {}
        messages: list[ImportedMessage] = []
        truncated = False
        on_path = set(path)
        hidden_branches = sum(
            1
            for other_id, node in mapping.items()
            if other_id not in on_path and isinstance(node, dict) and isinstance(node.get("message"), dict)
        )
        if hidden_branches:
            skipped["hidden_branch"] = hidden_branches

        for node_id in path:
            node = mapping.get(node_id)
            message = node.get("message") if isinstance(node, dict) else None
            if not isinstance(message, dict):
                continue  # structural node (e.g. the root), not a message that was left out
            imported, reason, cut = self._read_message(message, limits)
            if reason is not None:
                bump(skipped, reason)
                continue
            if len(messages) >= limits.max_messages_per_chat:
                bump(skipped, "limit")
                continue
            truncated = truncated or cut
            messages.append(imported)

        chat_id = conversation.get("id")
        if not isinstance(chat_id, str) or not chat_id:
            chat_id = as_text(conversation.get("conversation_id"))
        chat = ImportedChat(
            source_format=self.name,
            source_chat_id=chat_id[:200],
            title=as_text(conversation.get("title")),
            created_at=epoch_seconds(conversation.get("create_time")),
            updated_at=epoch_seconds(conversation.get("update_time")),
            messages=tuple(messages),
            skipped=skipped,
        )
        return chat, truncated

    def _read_message(self, message: dict, limits: Limits) -> tuple[Any, str | None, bool]:
        metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
        if metadata.get("is_visually_hidden_from_conversation") is True or metadata.get("is_user_system_message") is True:
            return None, "hidden", False

        author = message.get("author")
        role = author.get("role") if isinstance(author, dict) else None
        if role not in ("user", "assistant"):
            # system, tool, function, developer and anything else: counted, never imported.
            return None, role if isinstance(role, str) and role.isidentifier() else "unknown_role", False

        content = message.get("content") if isinstance(message.get("content"), dict) else {}
        content_type = content.get("content_type")
        if content_type in _MAPPING_THINKING_TYPES:
            return None, "thinking", False
        if content_type in _MAPPING_CONTEXT_TYPES:
            return None, "context", False
        recipient = message.get("recipient")
        if role == "assistant" and isinstance(recipient, str) and recipient not in ("", "all"):
            # An assistant message addressed to a tool is a tool call, not words to the user.
            return None, "tool", False
        channel = message.get("channel")
        if isinstance(channel, str) and channel not in ("", "final"):
            return None, "thinking", False
        if content_type not in _MAPPING_TEXT_TYPES:
            # Code, execution output, browsing results and other non-chat content.
            return None, "non_text", False

        parts = content.get("parts")
        strings = [part for part in parts if isinstance(part, str) and part.strip()] if isinstance(parts, list) else []
        if not strings:
            pictures = isinstance(parts, list) and any(not isinstance(part, str) for part in parts)
            return None, "non_text" if pictures else "empty", False
        imported, cut = make_message(role, "\n".join(strings), epoch_seconds(message.get("create_time")), limits)
        return imported, None, cut


# --------------------------------------------------------------------------- message-list export
#
# Shape (community-verified): conversations carry ``uuid``, ``name`` and ``chat_messages``; each
# message has ``sender`` ("human" or "assistant"), ``text`` (may be empty in newer exports) and
# ``content`` blocks. Only ``text`` and ``voice_note`` blocks are words; thinking, tool use, tool
# results and unknown block types are never imported as text. Attachments carry their extracted
# file text inline -- only their names are kept.

_LIST_TEXT_BLOCKS = frozenset({"text", "voice_note"})
_LIST_THINKING_BLOCKS = frozenset({"thinking", "redacted_thinking"})
_LIST_TOOL_BLOCKS = frozenset({"tool_use", "tool_result", "server_tool_use", "web_search_tool_result"})


class _MessageListExportReader(FormatReader):
    """The export whose conversations hold ``chat_messages`` lists."""

    name = "anthropic_export"

    def matches(self, conversations: list) -> bool:
        return _signal_count(conversations, "chat_messages", list) > 0

    def read_chat(self, conversation: Any, limits: Limits, problems: list[str]) -> tuple[ImportedChat | None, bool]:
        if not isinstance(conversation, dict):
            problems.append("chat_skipped_malformed")
            return None, False
        message_list = conversation.get("chat_messages")
        if not isinstance(message_list, list):
            problems.append("chat_skipped_malformed")
            return None, False

        skipped: dict[str, int] = {}
        entries = self._shown_branch(conversation, message_list, skipped)
        messages: list[ImportedMessage] = []
        truncated = False
        for entry in entries:
            if not isinstance(entry, dict):
                bump(skipped, "malformed")
                continue
            sender = entry.get("sender")
            if sender == "human":
                role = "user"
            elif sender == "assistant":
                role = "assistant"
            else:
                bump(skipped, sender if isinstance(sender, str) and sender.isidentifier() else "unknown_role")
                continue
            text, reason = self._message_text(entry, skipped)
            if text is None:
                bump(skipped, reason or "empty")
                continue
            if len(messages) >= limits.max_messages_per_chat:
                bump(skipped, "limit")
                continue
            imported, cut = make_message(role, text, iso_seconds(entry.get("created_at")), limits, self._names(entry))
            truncated = truncated or cut
            messages.append(imported)

        chat = ImportedChat(
            source_format=self.name,
            source_chat_id=as_text(conversation.get("uuid"))[:200],
            title=as_text(conversation.get("name")),
            created_at=iso_seconds(conversation.get("created_at")),
            updated_at=iso_seconds(conversation.get("updated_at")),
            messages=tuple(messages),
            skipped=skipped,
        )
        return chat, truncated

    @staticmethod
    def _shown_branch(conversation: dict, message_list: list, skipped: dict[str, int]) -> list:
        """The branch the user last saw: walk from the current leaf through the parents when
        the export records both, otherwise the list order."""
        leaf = conversation.get("current_leaf_message_uuid")
        by_id = {
            entry.get("uuid"): entry
            for entry in message_list
            if isinstance(entry, dict) and isinstance(entry.get("uuid"), str)
        }
        if not isinstance(leaf, str) or leaf not in by_id:
            return message_list
        if not any(isinstance(entry.get("parent_message_uuid"), str) for entry in by_id.values()):
            return message_list
        path: list[dict] = []
        seen: set[str] = set()
        node_id: Any = leaf
        while isinstance(node_id, str) and node_id in by_id and node_id not in seen:
            seen.add(node_id)
            path.append(by_id[node_id])
            node_id = by_id[node_id].get("parent_message_uuid")
        path.reverse()
        hidden = len(by_id) - len(path)
        if hidden:
            skipped["hidden_branch"] = hidden
        return path

    @staticmethod
    def _message_text(entry: dict, skipped: dict[str, int]) -> tuple[str | None, str | None]:
        """(text, None) when the message has words, else (None, skip reason).

        When ``content`` blocks are present they are the authority: only text and voice-note
        blocks become text, joined by a blank line. The flat ``text`` field is used only when
        there are no blocks at all.
        """
        content = entry.get("content")
        if isinstance(content, list) and content:
            texts: list[str] = []
            kinds: set[str] = set()
            for block in content:
                kind = block.get("type") if isinstance(block, dict) else None
                if kind in _LIST_TEXT_BLOCKS and isinstance(block.get("text"), str) and block["text"].strip():
                    texts.append(block["text"])
                elif kind in _LIST_THINKING_BLOCKS:
                    kinds.add("thinking")
                elif kind in _LIST_TOOL_BLOCKS:
                    kinds.add("tool")
                elif kind not in _LIST_TEXT_BLOCKS:
                    kinds.add("non_text")
            if texts:
                for kind in sorted(kinds):
                    bump(skipped, kind + "_block")
                return "\n\n".join(texts), None
            if "tool" in kinds:
                return None, "tool"
            if "thinking" in kinds:
                return None, "thinking"
            return None, "non_text" if kinds else "empty"
        plain = entry.get("text")
        if isinstance(plain, str) and plain.strip():
            return plain, None
        return None, "empty"

    @staticmethod
    def _names(entry: dict) -> tuple[str, ...]:
        names: list[str] = []
        for group in ("attachments", "files", "files_v2"):
            items = entry.get(group)
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict) and isinstance(item.get("file_name"), str):
                        names.append(item["file_name"])
        return unique_names(names)


register_reader(_MappingTreeExportReader())
register_reader(_MessageListExportReader())
