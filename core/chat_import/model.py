"""The neutral shape imported chats take in memory.

These types carry a foreign app's export data exactly as the export wrote it: plain text, no
markup rewriting, nothing interpreted. A message is data the user once exchanged with another
app; it is never treated as an instruction to VOOL, and only user/assistant messages exist here
at all -- every other role was counted and left out before these types were built.

The types exist only in memory. Nothing in this package writes to VOOL's database, memory,
settings or any file.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

#: The only roles an imported message can carry.
Role = Literal["user", "assistant"]


@dataclass(frozen=True)
class ImportedMessage:
    """One chat message, exactly as the user saw it in the other app.

    Times are UTC epoch seconds when the export carried a usable time, otherwise None.
    ``attachment_names`` holds file names only -- attachment contents are never read.
    """

    role: Role
    text: str
    created_at: float | None = None
    attachment_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImportedChat:
    """One conversation from an export file.

    ``skipped`` counts, per plain reason word (e.g. "system", "tool", "empty", "non_text",
    "hidden", "hidden_branch", "limit"), the messages the reader left out of this chat.
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
    """Everything one export file turned into, plus what went wrong.

    An empty ``source_format`` means no known format was recognized (a refusal). ``problems``
    are plain lines describing what was refused or skipped; ``truncated`` is True when any
    content was cut to fit the limits.
    """

    source_format: str
    chats: tuple[ImportedChat, ...]
    problems: tuple[str, ...]
    truncated: bool
