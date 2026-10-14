"""The neutral shape imported chats take in memory.

These types carry another app's chat history exactly as that app stored it: plain text, no
markup rewriting, nothing interpreted. A message is data the user once exchanged with another
app; it is never treated as an instruction to VOOL, and only user/assistant messages exist here
at all -- every other role (system, tool, developer, reasoning, injected context) was counted
and left out before these types were built.

Every reader, whether it reads an export file or another app's local store, returns the same
``ReadResult``. Problems are stable codes (``PROBLEM_TEXT`` holds the plain English line for
each) so the screen can show them in the user's language.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

#: The only roles an imported message can carry.
Role = Literal["user", "assistant"]


@dataclass(frozen=True)
class ImportedMessage:
    """One chat message, exactly as the user saw it in the other app.

    Times are UTC epoch seconds when the source carried a usable time, otherwise None.
    ``attachment_names`` holds file names only -- attachment contents are never read.
    """

    role: Role
    text: str
    created_at: float | None = None
    attachment_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImportedChat:
    """One conversation from a source.

    ``skipped`` counts, per plain reason word (e.g. "system", "tool", "thinking", "empty",
    "non_text", "hidden", "hidden_branch", "limit"), the messages the reader left out of this
    chat. ``source_chat_id`` is the source's own id for the chat; the importer uses it, with the
    source family, to recognise a chat that was already brought in.
    """

    source_format: str
    source_chat_id: str
    title: str
    created_at: float | None = None
    updated_at: float | None = None
    messages: tuple[ImportedMessage, ...] = ()
    skipped: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ReadResult:
    """Everything one source turned into, plus what went wrong.

    An empty ``source_format`` means nothing readable was recognised (a refusal). ``problems``
    are codes from ``PROBLEM_TEXT``; ``truncated`` is True when any content was cut to fit the
    limits.
    """

    source_format: str
    chats: tuple[ImportedChat, ...]
    problems: tuple[str, ...]
    truncated: bool


#: Every problem code a reader can report, with its plain English line. The settings screen
#: groups the codes into a few translated lines (``chatimport.problem.*`` in the catalogs).
PROBLEM_TEXT: dict[str, str] = {
    "file_unreadable": "The file could not be opened.",
    "file_too_large": "The file is larger than the import limit allows.",
    "not_a_zip": "The file is not a zip archive.",
    "zip_too_many_members": "The archive holds more files than the import limit allows.",
    "zip_encrypted": "The archive is password protected.",
    "zip_unsafe_path": "The archive contains a file path that is not safe to read.",
    "zip_symlink": "The archive contains a link to another file.",
    "zip_too_large_unpacked": "The archive unpacks larger than the import limit allows.",
    "no_conversations": "No chat history was found in the archive.",
    "conversations_unreadable": "The chat history in the archive could not be read.",
    "conversations_too_large": "The chat history in the archive is larger than the import limit allows.",
    "conversations_not_utf8": "The chat history in the archive is not valid text.",
    "conversations_not_json": "The chat history in the archive is damaged.",
    "conversations_too_deep": "The chat history in the archive is nested too deeply to read safely.",
    "conversations_not_list": "The chat history in the archive has an unknown layout.",
    "format_unknown": "This kind of chat history is not one VOOL can read yet.",
    "too_many_chats": "Only the first chats were read; the rest are over the import limit.",
    "chat_skipped_malformed": "Some chats were left out because they could not be read.",
    "chat_skipped_duplicate": "Some chats appeared twice in the source and were read once.",
    "store_missing": "The app's chat store was not found.",
    "store_unreadable": "The app's chat store could not be read.",
    "store_busy": "The app was writing to its chat store; close it and try again.",
    "store_too_large": "The app's chat store is too large to read safely.",
    "store_version_unknown": "This version of the app stores chats in a way VOOL does not know yet.",
    "rows_compressed_skipped": "Some chats are saved compressed in a way this computer cannot open; they were left out.",
    "rows_unreadable_skipped": "Some saved messages could not be read; the chats holding them were left out.",
    "chats_without_order_skipped": "Some chats were left out because their message order is not recorded.",
    "chats_internal_skipped": "Some sessions were left out because the app ran them by itself, not you.",
    "files_skipped": "Some chat files could not be read and were left out.",
}


def describe_problem(code: str) -> str:
    """The plain English line for one problem code (the code itself when it is unknown)."""
    return PROBLEM_TEXT.get(code, code)
