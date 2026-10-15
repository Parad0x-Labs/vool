"""Limits and small shared helpers for every chat-history reader.

Everything here is pure: no file is opened, nothing is written.
"""
from __future__ import annotations

import math
import re
from array import array
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import accumulate
from typing import Any

from core.chat_import.model import ImportedMessage, ReadResult


@dataclass(frozen=True)
class Limits:
    """Safety limits for reading untrusted chat history, all in one place.

    max_zip_bytes: largest export zip accepted on disk.
    max_member_bytes: largest single chat-history member read from a zip into memory.
    max_conversation_bytes: largest sum of all chat-history members read from one zip.
    max_total_uncompressed_bytes: largest accepted sum of all members' declared sizes
        (the zip-bomb guard for the archive as a whole).
    max_members: most archive members accepted.
    max_conversations: most chats read from one source; the rest are dropped and the result is
        marked truncated.
    max_messages_per_chat: most messages kept per chat; the rest are counted in
        ``skipped["limit"]``.
    max_message_chars: longest message text kept; longer texts are cut and the result is
        marked truncated.
    max_json_depth: deepest JSON nesting accepted in a chat-history document.
    max_local_store_bytes: largest local app store (database plus its write-ahead log, or the
        sum of transcript files) that is copied and read.
    max_local_files: most transcript files read from one local app folder.
    """

    max_zip_bytes: int = 1 << 30
    max_member_bytes: int = 512 << 20
    max_conversation_bytes: int = 1 << 30
    max_total_uncompressed_bytes: int = 4 << 30
    max_members: int = 100_000
    max_conversations: int = 20_000
    max_messages_per_chat: int = 20_000
    max_message_chars: int = 200_000
    max_json_depth: int = 64
    max_local_store_bytes: int = 2 << 30
    max_local_files: int = 20_000


#: The limits every read uses unless the caller passes stricter ones.
DEFAULT_LIMITS = Limits()


def refusal(problem: str) -> ReadResult:
    return ReadResult(source_format="", chats=(), problems=(problem,), truncated=False)


def bump(counts: dict[str, int], reason: str, amount: int = 1) -> None:
    counts[reason] = counts.get(reason, 0) + amount


def epoch_seconds(value: Any) -> float | None:
    """A finite epoch-seconds number as the source wrote it, else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number > 0 else None


def epoch_millis(value: Any) -> float | None:
    """Epoch milliseconds to seconds. Only values above 1e12 are accepted as milliseconds."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 1e12:
        return None
    return number / 1000.0


def iso_seconds(value: Any) -> float | None:
    """An ISO 8601 timestamp as UTC epoch seconds, or None when unparseable.

    A timestamp without a zone is read as UTC. Fractional seconds longer than six digits are
    cut to six, because ``datetime.fromisoformat`` rejects them on older Pythons.
    """
    if not isinstance(value, str) or not value or len(value) > 64:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def as_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


_DEPTH_STEPS = bytes.maketrans(b"{[}]", bytes([1, 1, 255, 255]))


def json_depth_exceeds(raw: bytes, limit: int) -> bool:
    """True when the JSON in ``raw`` nests deeper than ``limit``, measured before parsing.

    Escaped backslashes and quotes are removed first, so every remaining quote delimits a
    string; the bytes between string pairs are the structure. Its brackets become +1/-1 steps
    and the deepest running sum is the nesting depth. Every pass runs in C.
    """
    flat = raw.replace(b"\\\\", b"").replace(b'\\"', b"")
    structure = b"".join(flat.split(b'"')[0::2])
    brackets = re.sub(rb"[^\[\]{}]+", b"", structure)
    if not brackets:
        return False
    steps = array("b")
    steps.frombytes(brackets.translate(_DEPTH_STEPS))
    return max(accumulate(steps)) > limit


def clipped(text: str, limits: Limits) -> tuple[str, bool]:
    """``text`` cut to the per-message limit, and whether it was cut."""
    if len(text) > limits.max_message_chars:
        return text[: limits.max_message_chars], True
    return text, False


def unique_names(names: list[str]) -> tuple[str, ...]:
    """File names in first-seen order, without repeats or blanks, each at most 255 characters."""
    seen: list[str] = []
    for name in names:
        clean = " ".join(str(name).split())[:255]
        if clean and clean not in seen:
            seen.append(clean)
    return tuple(seen)


def make_message(
    role: str,
    text: str,
    created_at: float | None,
    limits: Limits,
    attachment_names: tuple[str, ...] = (),
) -> tuple[ImportedMessage, bool]:
    body, cut = clipped(text, limits)
    return (
        ImportedMessage(
            role="user" if role == "user" else "assistant",
            text=body,
            created_at=created_at,
            attachment_names=attachment_names,
        ),
        cut,
    )
