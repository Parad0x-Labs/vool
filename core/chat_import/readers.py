"""Read chat history export files from other AI apps.

``read(path)`` turns a file the user picked into a neutral, validated list of chats in memory
(see ``core.chat_import.model``). The readers only read: no database, no settings, no file is
ever written. Which format a file holds is decided by the shape of the ``conversations.json``
content inside the archive, never by the file name.

The input is untrusted, so every limit in ``Limits`` is enforced before and while reading, and
bad input never raises out of ``read``: a refusal or a skipped item comes back as a plain
problem line on the ``ReadResult``.
"""
from __future__ import annotations

import abc
import json
import math
import os
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.chat_import.model import ImportedChat, ImportedMessage, ReadResult

#: The archive member every supported export carries.
CONVERSATIONS_MEMBER = "conversations.json"

_WINDOW_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")

#: Unix symlink file-type bits as zipfile stores them in ``external_attr``.
_SYMLINK_MODE = 0o120000

_ZIP_ENCRYPTED_FLAG = 0x1

#: How many conversations are inspected when recognising the format.
_DETECT_SAMPLE = 25


@dataclass(frozen=True)
class Limits:
    """Safety limits for reading an untrusted export file, all in one place.

    max_zip_bytes: largest zip accepted on disk.
    max_member_bytes: largest single uncompressed member read into memory.
    max_total_uncompressed_bytes: largest accepted sum of all members' uncompressed sizes
        (the zip-bomb guard for the archive as a whole).
    max_members: most archive members accepted.
    max_conversations: most conversations imported from one file; the rest are dropped and the
        result is marked truncated.
    max_messages_per_chat: most messages imported per chat; the rest are counted in
        ``skipped["limit"]``.
    max_message_chars: longest message text kept; longer texts are cut and the result is
        marked truncated.
    max_json_depth: deepest JSON nesting accepted in conversations.json.
    """

    max_zip_bytes: int = 1 << 30
    max_member_bytes: int = 1 << 30
    max_total_uncompressed_bytes: int = 2 << 30
    max_members: int = 10_000
    max_conversations: int = 100_000
    max_messages_per_chat: int = 100_000
    max_message_chars: int = 1_000_000
    max_json_depth: int = 64


#: The limits every read uses unless the caller passes stricter ones.
DEFAULT_LIMITS = Limits()


class FormatReader(abc.ABC):
    """One export format. Subclass and ``register_reader`` an instance to add a format."""

    name: str = ""

    @abc.abstractmethod
    def matches(self, conversations: Any) -> bool:
        """True when the parsed conversations.json has this format's shape."""

    @abc.abstractmethod
    def read_chats(self, conversations: Any, limits: Limits) -> tuple[tuple[ImportedChat, ...], tuple[str, ...], bool]:
        """Turn the parsed conversation list into (chats, problem lines, truncated)."""


_READER_REGISTRY: list[FormatReader] = []


def register_reader(reader: FormatReader) -> None:
    """Teach ``detect`` and ``read`` a new export format."""
    if not reader.name:
        raise ValueError("a reader needs a format name")
    _READER_REGISTRY.append(reader)


def _refusal(problem: str) -> ReadResult:
    return ReadResult(source_format="", chats=(), problems=(problem,), truncated=False)


def _safe_epoch(value: Any) -> float | None:
    """An epoch float as the export wrote it, or None when it is absent or not a finite number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _iso_epoch(value: Any) -> float | None:
    """An ISO 8601 timestamp as UTC epoch seconds, or None when unparseable.

    A timestamp without a zone is read as UTC.
    """
    if not isinstance(value, str) or not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _bump(counts: dict[str, int], reason: str) -> None:
    counts[reason] = counts.get(reason, 0) + 1


def _signal_count(conversations: Any, key: str, kind: type) -> int:
    """How many sampled conversations carry ``key`` with the expected kind."""
    if not isinstance(conversations, list):
        return 0
    count = 0
    for conversation in conversations[:_DETECT_SAMPLE]:
        if isinstance(conversation, dict) and isinstance(conversation.get(key), kind):
            count += 1
    return count


def _max_json_depth(raw: bytes) -> int:
    """Deepest brace/bracket nesting, measured on bytes before json.loads runs.

    Scanning bytes is safe for UTF-8 text: multi-byte sequences never collide with the ASCII
    bytes counted here. Strings are skipped so braces inside text do not count.
    """
    depth = 0
    deepest = 0
    in_string = False
    escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:  # backslash
                escaped = True
            elif byte == 0x22:  # double quote
                in_string = False
            continue
        if byte == 0x22:  # double quote
            in_string = True
        elif byte in (0x7B, 0x5B):  # { or [
            depth += 1
            if depth > deepest:
                deepest = depth
        elif byte in (0x7D, 0x5D):  # } or ]
            if depth > 0:
                depth -= 1
    return deepest


def _member_problem(infolist: list[zipfile.ZipInfo], limits: Limits) -> str | None:
    """Refuse archives with unsafe members or sizes, before any member is read."""
    if len(infolist) > limits.max_members:
        return "the archive holds more members than the import limit allows"
    total = 0
    for info in infolist:
        if info.flag_bits & _ZIP_ENCRYPTED_FLAG:
            return "the archive contains an encrypted member"
        name = info.filename
        if name.startswith(("/", "\\")) or _WINDOW_DRIVE_PREFIX.match(name):
            return "the archive contains a member with an absolute path"
        if ".." in name.split("/"):
            return "the archive contains a member path that escapes the archive"
        if (info.external_attr >> 16) & 0o170000 == _SYMLINK_MODE:
            return "the archive contains a symbolic link member"
        total += info.file_size
    if total > limits.max_total_uncompressed_bytes:
        return "the archive expands larger than the import limit allows"
    return None


def _load_conversations(path: os.PathLike[str] | str, limits: Limits) -> tuple[list | None, str | None]:
    """Open the archive safely and parse conversations.json; exactly one of the two results is None.

    Reads happen in memory only (``zipfile`` member reads, never extraction to disk), and each
    read is bounded so a lying header cannot inflate past the cap.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return None, "the file could not be opened"
    if size > limits.max_zip_bytes:
        return None, "the file is larger than the import limit allows"
    try:
        with zipfile.ZipFile(path) as archive:
            problem = _member_problem(archive.infolist(), limits)
            if problem is not None:
                return None, problem
            if CONVERSATIONS_MEMBER not in archive.namelist():
                return None, "no conversations.json in the archive"
            info = archive.getinfo(CONVERSATIONS_MEMBER)
            if info.file_size > limits.max_member_bytes:
                return None, "conversations.json is larger than the import limit allows"
            try:
                with archive.open(CONVERSATIONS_MEMBER) as member:
                    raw = member.read(limits.max_member_bytes + 1)
            except (OSError, RuntimeError, ValueError, NotImplementedError, zipfile.BadZipFile):
                return None, "conversations.json could not be read"
            if len(raw) > limits.max_member_bytes:
                return None, "conversations.json expands larger than the import limit allows"
    except zipfile.BadZipFile:
        return None, "the file is not a zip archive"
    except OSError:
        return None, "the file could not be opened"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, "conversations.json is not valid UTF-8"
    if _max_json_depth(raw) > limits.max_json_depth:
        return None, "conversations.json nests more deeply than the import limit allows"
    try:
        parsed = json.loads(text)
    except (ValueError, RecursionError):
        return None, "conversations.json is not valid JSON"
    if not isinstance(parsed, list):
        return None, "conversations.json does not hold a list of conversations"
    return parsed, None


def _detect_format(conversations: list) -> str | None:
    for reader in _READER_REGISTRY:
        if reader.matches(conversations):
            return reader.name
    return None


def detect(path: os.PathLike[str] | str, *, limits: Limits = DEFAULT_LIMITS) -> str | None:
    """Which export format this file holds, judged only by the content's shape.

    The file name is never consulted. None means the file is not a readable export of any
    known format (missing, not a zip, unsafe, or unrecognised content).
    """
    conversations, _problem = _load_conversations(path, limits)
    if conversations is None:
        return None
    return _detect_format(conversations)


def read(path: os.PathLike[str] | str, *, limits: Limits = DEFAULT_LIMITS) -> ReadResult:
    """Read one export file into neutral chats in memory.

    Never raises for bad input: a refusal or a skipped conversation comes back as a plain
    problem line on the result instead. Nothing is written anywhere.
    """
    conversations, problem = _load_conversations(path, limits)
    if conversations is None:
        return _refusal(problem or "the file could not be read")
    name = _detect_format(conversations)
    if name is None:
        return _refusal("the export format was not recognized")
    for reader in _READER_REGISTRY:
        if reader.name == name:
            chats, problems, truncated = reader.read_chats(conversations, limits)
            return ReadResult(source_format=name, chats=chats, problems=problems, truncated=truncated)
    return _refusal("the export format was not recognized")


def _import_all(
    conversations: list,
    limits: Limits,
    read_one: Any,
) -> tuple[tuple[ImportedChat, ...], tuple[str, ...], bool]:
    """Shared conversation loop: cap the count, tolerate broken items, keep going."""
    chats: list[ImportedChat] = []
    problems: list[str] = []
    truncated = False
    if len(conversations) > limits.max_conversations:
        truncated = True
        problems.append(f"stopped after {limits.max_conversations} of {len(conversations)} conversations (limit)")
    for conversation in conversations[: limits.max_conversations]:
        try:
            chat, chat_truncated = read_one(conversation, limits, problems)
        except Exception:
            problems.append("a conversation was skipped because its content was unexpected")
            continue
        truncated = truncated or chat_truncated
        if chat is not None:
            chats.append(chat)
    return tuple(chats), tuple(problems), truncated


class _OpenAIExportReader(FormatReader):
    """The export whose conversations.json holds mapping trees with a current_node."""

    name = "openai_export"

    def matches(self, conversations: Any) -> bool:
        return _signal_count(conversations, "mapping", dict) > 0

    def read_chats(self, conversations: Any, limits: Limits) -> tuple[tuple[ImportedChat, ...], tuple[str, ...], bool]:
        return _import_all(conversations, limits, self._read_chat)

    def _read_chat(self, conversation: Any, limits: Limits, problems: list[str]) -> tuple[ImportedChat | None, bool]:
        if not isinstance(conversation, dict):
            problems.append("a conversation that was not an object was skipped")
            return None, False
        mapping = conversation.get("mapping")
        current = conversation.get("current_node")
        if not isinstance(mapping, dict) or not isinstance(current, str) or current not in mapping:
            problems.append("a conversation without a readable message tree was skipped")
            return None, False

        # The visible chat is the path from current_node up through parents to the root,
        # reversed into chronological order. The seen-set keeps a malformed parent loop from
        # hanging the walk; the length cap keeps an endless chain bounded.
        path: list[str] = []
        seen: set[str] = set()
        node_id: str | None = current
        walk_cap = limits.max_messages_per_chat + 1024
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

        # Everything with a message that is not on the visible path is an edited-away branch.
        hidden_branches = sum(
            1
            for node_id, node in mapping.items()
            if node_id not in on_path and isinstance(node, dict) and isinstance(node.get("message"), dict)
        )
        if hidden_branches:
            skipped["hidden_branch"] = hidden_branches

        for node_id in path:
            node = mapping.get(node_id)
            if not isinstance(node, dict):
                continue
            message = node.get("message")
            if not isinstance(message, dict):
                continue  # structural node (e.g. the root), not a message that was left out
            if len(messages) >= limits.max_messages_per_chat:
                _bump(skipped, "limit")
                continue
            imported, reason, was_truncated = self._read_message(message, limits)
            truncated = truncated or was_truncated
            if reason is not None:
                _bump(skipped, reason)
                continue
            if imported is not None:
                messages.append(imported)

        chat_id = conversation.get("id")
        if not isinstance(chat_id, str) or not chat_id:
            fallback = conversation.get("conversation_id")
            chat_id = fallback if isinstance(fallback, str) else ""
        chat = ImportedChat(
            source_format=self.name,
            source_chat_id=chat_id,
            title=_as_text(conversation.get("title")),
            created_at=_safe_epoch(conversation.get("create_time")),
            updated_at=_safe_epoch(conversation.get("update_time")),
            messages=tuple(messages),
            skipped=skipped,
        )
        return chat, truncated

    def _read_message(self, message: dict, limits: Limits) -> tuple[ImportedMessage | None, str | None, bool]:
        metadata = message.get("metadata")
        if isinstance(metadata, dict) and metadata.get("is_visually_hidden_from_conversation") is True:
            return None, "hidden", False

        author = message.get("author")
        role = author.get("role") if isinstance(author, dict) else None
        if role not in ("user", "assistant"):
            # system, tool, function, developer and anything else: counted, never imported.
            return None, role if isinstance(role, str) and role else "unknown_role", False

        content = message.get("content")
        content_type = content.get("content_type") if isinstance(content, dict) else None
        parts = content.get("parts") if isinstance(content, dict) else None
        strings = [part for part in parts if isinstance(part, str)] if isinstance(parts, list) else []
        if strings:
            text = "".join(strings)
        elif content_type is None or content_type == "text":
            return None, "empty", False
        else:
            # Images, execution output and other non-text content: counted, never imported.
            return None, "non_text", False
        if text == "":
            return None, "empty", False

        truncated = False
        if len(text) > limits.max_message_chars:
            text = text[: limits.max_message_chars]
            truncated = True
        return (
            ImportedMessage(
                role=role,
                text=text,
                created_at=_safe_epoch(message.get("create_time")),
                attachment_names=(),
            ),
            None,
            truncated,
        )


class _AnthropicExportReader(FormatReader):
    """The export whose conversations.json holds chat_messages lists."""

    name = "anthropic_export"

    def matches(self, conversations: Any) -> bool:
        return _signal_count(conversations, "chat_messages", list) > 0

    def read_chats(self, conversations: Any, limits: Limits) -> tuple[tuple[ImportedChat, ...], tuple[str, ...], bool]:
        return _import_all(conversations, limits, self._read_chat)

    def _read_chat(self, conversation: Any, limits: Limits, problems: list[str]) -> tuple[ImportedChat | None, bool]:
        if not isinstance(conversation, dict):
            problems.append("a conversation that was not an object was skipped")
            return None, False
        message_list = conversation.get("chat_messages")
        if not isinstance(message_list, list):
            problems.append("a conversation without a readable message list was skipped")
            return None, False

        skipped: dict[str, int] = {}
        messages: list[ImportedMessage] = []
        truncated = False
        for entry in message_list:
            if not isinstance(entry, dict):
                _bump(skipped, "malformed")
                continue
            if len(messages) >= limits.max_messages_per_chat:
                _bump(skipped, "limit")
                continue
            sender = entry.get("sender")
            if sender == "human":
                role = "user"
            elif sender == "assistant":
                role = "assistant"
            else:
                _bump(skipped, sender if isinstance(sender, str) and sender else "unknown_sender")
                continue
            text, reason = self._message_text(entry)
            if reason is not None:
                _bump(skipped, reason)
                continue
            if len(text) > limits.max_message_chars:
                text = text[: limits.max_message_chars]
                truncated = True
            names: list[str] = []
            for group in ("attachments", "files"):
                items = entry.get(group)
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict) and isinstance(item.get("file_name"), str):
                            names.append(item["file_name"])
            messages.append(
                ImportedMessage(
                    role=role,
                    text=text,
                    created_at=_iso_epoch(entry.get("created_at")),
                    attachment_names=tuple(names),
                )
            )

        chat_id = conversation.get("uuid")
        chat = ImportedChat(
            source_format=self.name,
            source_chat_id=chat_id if isinstance(chat_id, str) else "",
            title=_as_text(conversation.get("name")),
            created_at=_iso_epoch(conversation.get("created_at")),
            updated_at=_iso_epoch(conversation.get("updated_at")),
            messages=tuple(messages),
            skipped=skipped,
        )
        return chat, truncated

    def _message_text(self, entry: dict) -> tuple[str | None, str | None]:
        """(text, None) when the message has text, else (None, skip reason).

        Text blocks inside ``content`` win; a plain ``text`` field is the fallback, exactly as
        the export shapes define.
        """
        content = entry.get("content")
        plain = entry.get("text")
        if isinstance(content, list) and content:
            texts = [
                block.get("text")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
            ]
            if texts:
                return "".join(texts), None
            if isinstance(plain, str) and plain:
                return plain, None
            return None, "non_text"
        if isinstance(plain, str) and plain:
            return plain, None
        return None, "empty"


register_reader(_OpenAIExportReader())
register_reader(_AnthropicExportReader())
