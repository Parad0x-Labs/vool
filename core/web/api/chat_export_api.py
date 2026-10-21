"""PB05 — complete TXT/Markdown export of ONE selected conversation.

Every exported byte comes from the authoritative persisted transcript (the conversation
log the transcript reader ``/api/chat/history`` serves), never from the rendered DOM and
never from live client state. The snapshot is ONE bounded immutable capture
(:func:`capture_log_bytes` — a filesystem read that requests at most
``MAX_CAPTURE_BYTES + 1`` bytes, so the bound limits allocation, not merely acceptance),
and ``scan_snapshot`` gates that capture into turns in a SINGLE pass: the counts the
preamble declares and the turns the formatter renders come from the same bytes, so a
concurrent append, trim/rewrite (the store's trim rewrites the retained half), or
deletion cannot make them diverge. Formatting then walks the retained turns in bounded
batches. Availability (``WITHHELD``/``ERASED``) is consulted live during that gate, so a
payload the authority has revoked is never republished from a stale capture, and history
the store had already trimmed before the request is absent rather than claimed.

This module is additive by contract. The one place it touches the served API is the named
delegation arm ``/api/chat/export`` in ``service.py``; no existing service logic is
rewritten. The availability gate (a payload WITHHELD or ERASED by privacy policy is never
served) mirrors the transcript reader's semantics by calling the same ``core.finalization``
authorities in the same precedence — parity with ``/api/chat/history`` is pinned by test,
not assumed.

What an export contains is disclosed in its preamble and in the preview manifest: user
and assistant message sources in conversation order, attachment references (names, kinds,
sizes, outcomes — never bytes), and assistant-authored artifact cards. Model reasoning,
tool/activity traces and per-user send times are not part of the persisted transcript and
are therefore named as absent rather than silently missing.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from core.web.api.service import (
    ApiResponse,
    RuntimeServices,
    apply_runtime_headers,
    is_loopback_host,
    json_response,
)

# A single-file export ceiling. The conversation log itself is trimmed at 8 MiB
# (core.memory.files.MAX_CONVERSATION_LOG_BYTES, shared across sessions), so one session
# can genuinely hold more than this cap; when it does, the export refuses with exact
# counts instead of shipping a quietly truncated file (PB05: explicit refusal, never
# silent truncation). The ceiling counts the WHOLE delivered file — preamble included.
MAX_EXPORT_BYTES = 4 * 1024 * 1024

# The bounded immutable capture. The snapshot is ONE read of the log's bytes, held for
# the lifetime of the request; counting and formatting both walk that capture, so a
# concurrent append, trim/rewrite (the store's trim rewrites the retained half), or
# deletion cannot make the declared counts describe different bytes than the body
# delivers. 16 MiB sits above the store's own 8 MiB trim ceiling, so the bound is a
# guard, not a limit a healthy store meets.
MAX_CAPTURE_BYTES = 16 * 1024 * 1024

# Events carried per formatting batch — the collection is paginated so no pass ever
# materialises a whole conversation of formatted blocks at once. (The gated turns
# themselves are held once, bounded by the capture bound above.)
PAGE_SIZE = 200

# The file name is built from the chat's title. These characters cannot appear in a file name
# on at least one desktop system (or would be read as a path), so they become spaces.
_FILENAME_FORBIDDEN = frozenset('/\\:*?"<>|')
# A title's share of the file name, in UTF-8 bytes: the name as a whole must stay well under
# the 255-byte limit common file systems set, with room for " - VOOL chat - 2026-10-19.pdf".
MAX_FILENAME_TITLE_BYTES = 120
_WHITESPACE = re.compile(r"\s+")
_ZWJ = "\u200d"     # joins emoji into one picture; kept
_ZWNJ = "\u200c"    # part of how Persian and Indic words are spelled; kept

_MD_SEPARATOR = "\n\n---\n\n"
_TXT_SEPARATOR = "\n\n"


class ExportRefused(Exception):
    """A typed refusal to export. Carries the HTTP status the door should serve."""

    def __init__(self, status: int, error: str, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.status = int(status)
        self.error = str(error)
        self.message = str(message)
        self.detail: dict[str, Any] = dict(detail)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": self.error,
            "message": self.message,
            **({"detail": self.detail} if self.detail else {}),
        }


@dataclass
class ExportCounts:
    """What the snapshot actually holds, counted before a byte is formatted."""

    turns: int = 0
    user_messages: int = 0
    assistant_messages: int = 0
    attachment_items: int = 0
    artifact_cards: int = 0
    unavailable: int = 0
    unreadable_rows: int = 0
    malformed_rows: int = 0
    blank_rows: int = 0
    invalid_bytes_rows: int = 0
    snapshot_rows: int = 0

    def message_count(self) -> int:
        return self.user_messages + self.assistant_messages


@dataclass
class ExportOptions:
    session_id: str
    fmt: str = "md"                    # "md" | "txt" | "pdf"
    include_timestamps: bool = False   # assistant-side completion times, opt-in
    include_attachments: bool = True   # attachment reference lines, opt-out
    request_id: str = ""              # optional exact answer; never the surrounding chat
    locale: str = "en"                 # the app language the page asked for (its UI locale)
    title: str = ""                   # the chat's title as the sidebar shows it; "" = untitled


@dataclass
class _Turn:
    """One persisted exchange, already availability-gated, ready to format."""

    user: str = ""
    assistant: str = ""
    ts: str = ""
    request_id: str = ""
    attachments: list[dict[str, Any]] = field(default_factory=list)
    artifact_kind: str = ""
    unavailable: str = ""             # availability word when the payload is withheld/erased


# --------------------------------------------------------------------------------------
# One bounded immutable capture, gated once
# --------------------------------------------------------------------------------------


def capture_log_bytes() -> bytes:
    """ONE bounded read of the conversation log: the request's immutable source representation.

    The read REQUEST itself is bounded — at most ``MAX_CAPTURE_BYTES + 1`` bytes are ever
    asked of the filesystem (``handle.read(MAX_CAPTURE_BYTES + 1)``), so the bound limits
    allocation, not merely acceptance. (The original implementation called
    ``Path.read_bytes()``, whose size −1 read requests the whole file and only then checks
    the bound.) Both the counting and the formatting walk these captured bytes, so the
    store appending, trimming/rewriting (the production trim rewrites the retained half),
    or deleting the file mid-request cannot make counts and contents diverge. No lock is
    held — the capture is a plain bounded read. History the store had already trimmed
    before the request is simply not here, and is never claimed to be.

    A capture at or under the bound is the whole file as the read observed it — the export
    is never a silently truncated accepted prefix. Over the bound is a typed refusal whose
    size metadata is honest about what a bounded read knows: the observed length is a
    LOWER bound (``min_file_bytes``, ``observed_complete: false``), never dressed up as an
    exact whole-file size. A missing log captures as empty (the door then answers 404).
    If a shared snapshot primitive for this ever moves into the storage modules, its
    contract is exactly this function's; the storage owner owns it then.
    """
    from core.memory.files import conversation_log_path

    try:
        handle = conversation_log_path().open("rb")
    except FileNotFoundError:
        return b""
    with handle:
        # The bound is a read budget: never more than cap+1 bytes requested. Reading cap+1
        # distinguishes "exactly cap" (accepted, whole) from "over cap" (refused) without
        # a second unbounded read; stat() is deliberately not trusted for the decision,
        # so a file that grows into the bound is still refused by what the read observed.
        observed = handle.read(MAX_CAPTURE_BYTES + 1)
    if len(observed) > MAX_CAPTURE_BYTES:
        raise ExportRefused(
            413,
            "snapshot_too_large",
            "The conversation log is larger than the bounded snapshot an export captures, so nothing partial is offered.",
            min_file_bytes=len(observed),
            observed_complete=False,
            max_capture_bytes=MAX_CAPTURE_BYTES,
        )
    return observed


def _captured_lines(data: bytes) -> Iterator[tuple[str, str | None]]:
    """Yield (status, line) over the capture: "ok", or the reason the row is unreadable
    ("blank", "invalid_bytes"). Unreadable rows are disclosed, never a silent skip and
    never a fabricated message."""
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()  # the writer terminates every row with \n; the split artifact is not a row
    for raw_line in lines:
        if not raw_line.strip():
            yield "blank", None
            continue
        try:
            yield "ok", raw_line.decode("utf-8")
        except UnicodeDecodeError:
            yield "invalid_bytes", None


def _availability_gate(row: dict[str, Any]) -> tuple[str, str]:
    """The transcript reader's availability precedence, against the same authorities.

    Returns (assistant_text_or_empty, availability_word). A WITHHELD or ERASED payload
    yields no bytes — the same law ``/api/chat/history`` serves under. The gate's
    lookups probe the trimmed text exactly as the transcript reader does, but the text
    returned for export is the stored source verbatim: an export that quietly trimmed
    whitespace would not be a fidelity-preserving export. Parity of the gate itself is
    pinned by test, not assumed.
    """
    from core.finalization import (
        AVAILABILITY_AVAILABLE,
        AVAILABILITY_WITHHELD,
        get_finalization_by_content,
        get_finalization_by_request_id,
        payload_availability_for_text,
    )

    assistant_text = str(row.get("assistant") or "")
    probe = assistant_text.strip()
    request_id = str(row.get("request_id") or "").strip()
    binding = None
    if request_id:
        try:
            binding = get_finalization_by_request_id(request_id, principal="owner_local")
        except Exception:
            binding = None
    if binding is not None:
        availability = str(binding.get("availability") or "")
    else:
        availability = payload_availability_for_text(probe)
        if availability == AVAILABILITY_AVAILABLE:
            binding = get_finalization_by_content(probe)
    if availability in (AVAILABILITY_WITHHELD, "ERASED"):
        return "", availability
    return assistant_text, (availability or "")


def _row_to_turn(row: dict[str, Any], counts: ExportCounts) -> _Turn | None:
    """Fold one persisted event into the export's turn shape, counting as it goes.

    Message content is carried verbatim from the store; presence and counting use the
    trimmed truth so an export's counts agree with what the transcript reader serves.
    """
    turn = _Turn(
        user=str(row.get("user") or ""),
        ts=str(row.get("ts") or ""),
        request_id=str(row.get("request_id") or "").strip(),
        artifact_kind=str(row.get("artifact_kind") or "").strip(),
    )
    receipt = row.get("attachments")
    if isinstance(receipt, list):
        turn.attachments = [entry for entry in receipt if isinstance(entry, dict)]
    counts.attachment_items += len(turn.attachments)
    if turn.artifact_kind:
        counts.artifact_cards += 1
    assistant_text, availability = _availability_gate(row)
    if availability in ("WITHHELD", "ERASED"):
        counts.unavailable += 1
        turn.unavailable = availability
    else:
        turn.assistant = assistant_text
    if not (turn.user.strip() or turn.assistant.strip() or turn.unavailable or turn.artifact_kind):
        return None
    if turn.user.strip():
        counts.user_messages += 1
    if turn.assistant.strip() or turn.unavailable:
        counts.assistant_messages += 1
    counts.turns += 1
    return turn


def scan_snapshot(session_id: str, capture: bytes | None = None) -> tuple[list[_Turn], ExportCounts]:
    """Gate ONE captured representation and return (gated turns, counts) from the same pass.

    The counts that reach the preamble and preview come from THIS pass, and the format
    phase formats the turns THIS pass retained — so declared counts and delivered
    contents cannot diverge. Availability is consulted live, during this pass, against
    the governing authority: a payload WITHHELD or ERASED before the pass yields no
    bytes here, and the export never republishes a payload from a stale capture that
    the authority has since revoked (revocation is enforced at gating time, not by the
    capture's age). A corrupt row — blank, undecodable, unparseable, non-object — is
    counted and disclosed, never silently dropped.
    """
    if capture is None:
        capture = capture_log_bytes()
    counts = ExportCounts()
    turns: list[_Turn] = []
    exists = False
    rows_seen = 0
    for status, line in _captured_lines(capture):
        rows_seen += 1
        if status != "ok":
            counts.unreadable_rows += 1
            if status == "blank":
                counts.blank_rows += 1
            else:
                counts.invalid_bytes_rows += 1
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            counts.unreadable_rows += 1
            counts.malformed_rows += 1
            continue
        if not isinstance(row, dict):
            counts.unreadable_rows += 1
            continue
        if str(row.get("session_id") or "") != session_id:
            continue
        exists = True
        try:
            turn = _row_to_turn(row, counts)
        except Exception:
            counts.unreadable_rows += 1
            continue
        if turn is not None:
            turns.append(turn)
    counts.snapshot_rows = rows_seen
    if not exists:
        raise ExportRefused(404, "session_not_found", "No persisted transcript exists for this session.")
    return turns, counts


def _batches(turns: list[_Turn], size: int) -> Iterator[list[_Turn]]:
    for start in range(0, len(turns), size):
        yield turns[start : start + size]


# --------------------------------------------------------------------------------------
# Formatting — verbatim message sources, disclosed structure
# --------------------------------------------------------------------------------------


def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} bytes"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _text(opts: ExportOptions, key: str, english: str, **counts: int) -> str:
    """One piece of VOOL's own words in the file, in the app language the page asked for.

    Only counts are ever formatted through the catalog (its placeholder law HTML-escapes
    values, which is right for the page and wrong for a text file), so a chat title or a file
    name is always joined to the catalog text here, never passed into it. A key missing from
    the shipped catalogs serves the English source given here, never a bare key.
    """
    from core.i18n.catalog import catalog_for, format_message

    catalog = catalog_for(opts.locale)
    if catalog.text(key) == key:
        return format_message(english, counts) if counts else english
    return catalog.format(key, **counts) if counts else catalog.text(key)


def _local_time_label(ts: str) -> str:
    """A stored completion instant as a person reads it: this computer's local date and minute."""
    try:
        moment = datetime.fromisoformat(str(ts).strip().replace("Z", "+00:00"))
    except ValueError:
        return str(ts)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone().strftime("%Y-%m-%d %H:%M")


def _attachment_lines(turn: _Turn, bullet: str, opts: ExportOptions) -> list[str]:
    lines: list[str] = []
    reference = _text(opts, "chat_export.attachment_reference", "Attachment reference")
    outcome_word = _text(opts, "chat_export.attachment_outcome", "outcome")
    note = _text(opts, "chat_export.attachment_note", "names and outcomes only, never the file bytes")
    for entry in turn.attachments:
        name = str(entry.get("name") or "attachment")
        kind = str(entry.get("media_type") or entry.get("kind") or "file")
        size = entry.get("size_bytes")
        outcome = str(entry.get("outcome") or "")
        parts = [kind]
        if isinstance(size, int):
            parts.append(_fmt_bytes(size))
        if outcome:
            parts.append(f"{outcome_word}: {outcome}")
        if entry.get("document"):
            parts.append("document retained")
        lines.append(f"{bullet} {reference}: {name} ({', '.join(parts)}) — {note}")
    return lines


def _assistant_time_ref(turn: _Turn, opts: ExportOptions) -> str:
    """Optional answer time in the heading, opt-in with the timestamps control.

    The store carries the assistant-side completion instant only, so the heading says when the
    answer finished and never dresses that up as a send time.
    """
    if not opts.include_timestamps or not turn.ts:
        return ""
    answered = _text(opts, "chat_export.answered_at", "answered {time}")
    return " (" + answered.replace("{time}", _local_time_label(turn.ts)) + ")"


def _role(opts: ExportOptions, role: str) -> str:
    if role == "user":
        return _text(opts, "chat_export.role_user", "User")
    return _text(opts, "chat_export.role_assistant", "Assistant")


def _unavailable_heading(opts: ExportOptions) -> str:
    return _role(opts, "assistant") + " — " + _text(
        opts, "chat_export.unavailable_heading", "unavailable by privacy policy"
    )


def _unavailable_body(opts: ExportOptions) -> str:
    return "[" + _text(
        opts,
        "chat_export.unavailable_body",
        "This answer is not included: it was withheld or erased under the privacy policy.",
    ) + "]"


def _artifact_line(turn: _Turn, opts: ExportOptions) -> str:
    return f"({_text(opts, 'chat_export.artifact_card', 'VOOL artifact card')}: {turn.artifact_kind})"


def _fallback_title(opts: ExportOptions) -> str:
    return _text(opts, "chat_export.fallback_title", "VOOL chat")


_MD_TITLE_ESCAPES = re.compile(r"([\\`*_\[\]<>!|#~])")


def _markdown_title(title: str) -> str:
    """The title as a Markdown heading's text: its punctuation is shown, never interpreted
    (a chat named ``*draft*`` or ``![x](…)`` is a title, not formatting or an image)."""
    return _MD_TITLE_ESCAPES.sub(r"\\\1", title)


def _pdf_drawable(text: str) -> str:
    """The header title with only the characters the PDF font can draw.

    The title is VOOL's heading, not message content: an emoji in a chat's name must not make
    the whole PDF refuse, so characters the PDF font has no glyph for are left out of the
    HEADING only. Message content keeps the renderer's own all-or-nothing glyph law.
    """
    try:
        from reportlab.pdfbase import pdfmetrics

        from core.presentation.render_pdf import _font

        _font()
        glyphs = pdfmetrics.getFont("VoolPDFBold").face.charToGlyph
    except Exception:
        return text
    kept = "".join(ch for ch in text if ch.isspace() or ord(ch) in glyphs)
    return _WHITESPACE.sub(" ", kept).strip()


def _header_title(opts: ExportOptions) -> str:
    title = opts.title or _fallback_title(opts)
    if opts.fmt == "pdf":
        title = _pdf_drawable(title) or _pdf_drawable(_fallback_title(opts)) or "VOOL chat"
    return title


def _preamble_lines(opts: ExportOptions, counts: ExportCounts) -> list[str]:
    """A short header a person reads: the chat's name, when it was exported, how many messages,
    and, in plain words, what the file leaves out. Nothing about logs, rows or formats."""
    exported_on = _text(opts, "chat_export.exported_on", "Exported from VOOL on {date}").replace(
        "{date}", datetime.now().astimezone().strftime("%Y-%m-%d")
    )
    messages = _text(
        opts,
        "chat_export.message_count",
        "{n, plural, one {{n} message} other {{n} messages}}",
        n=counts.message_count(),
    )
    notes = [_text(opts, "chat_export.not_included", "Not included: how VOOL worked things out and its activity log.")]
    # What happened to attached files is said only when the chat has any.
    if counts.attachment_items and opts.include_attachments:
        notes.append(_text(opts, "chat_export.attachments_by_name", "Attached files are listed by name only."))
    elif counts.attachment_items:
        notes.append(_text(opts, "chat_export.attachments_left_out", "Attached files are left out, as you chose."))
    if counts.unavailable:
        notes.append(_text(
            opts,
            "chat_export.withheld_count",
            "{n, plural, one {{n} answer is not included because of the privacy policy.}"
            " other {{n} answers are not included because of the privacy policy.}}",
            n=counts.unavailable,
        ))
    if counts.unreadable_rows:
        notes.append(_text(
            opts,
            "chat_export.unreadable_count",
            "{n, plural, one {{n} saved part of this chat could not be read, so it is not in this file.}"
            " other {{n} saved parts of this chat could not be read, so they are not in this file.}}",
            n=counts.unreadable_rows,
        ))
    if opts.include_timestamps:
        notes.append(_text(opts, "chat_export.times_note", "Times show when each answer finished."))
    title = _header_title(opts)
    heading = "# " + _markdown_title(title) if opts.fmt in ("md", "pdf") else title
    return [heading, "", f"{exported_on} · {messages}", "", " ".join(notes)]


def format_turn_markdown(turn: _Turn, opts: ExportOptions) -> list[str]:
    out: list[str] = ["## " + _role(opts, "user")]
    if turn.user:
        out.append("")
        out.append(turn.user)
    if opts.include_attachments and turn.attachments:
        out.append("")
        out.extend(_attachment_lines(turn, "-", opts))
    out.append("")
    if turn.unavailable:
        out.append("## " + _unavailable_heading(opts))
        out.append("")
        out.append(_unavailable_body(opts))
    else:
        out.append("## " + _role(opts, "assistant") + _assistant_time_ref(turn, opts))
        if turn.assistant:
            out.append("")
            out.append(turn.assistant)
        if turn.artifact_kind:
            out.append("")
            out.append(_artifact_line(turn, opts))
    return out


def format_turn_text(turn: _Turn, opts: ExportOptions) -> list[str]:
    rule = "=" * 72
    thin = "-" * 72
    out: list[str] = [rule, _role(opts, "user").upper(), rule, ""]
    if turn.user:
        out.append(turn.user)
        out.append("")
    if opts.include_attachments and turn.attachments:
        out.extend(_attachment_lines(turn, "*", opts))
        out.append("")
    if turn.unavailable:
        out.extend([thin, _unavailable_heading(opts).upper(), thin, ""])
        out.append(_unavailable_body(opts))
        out.append("")
    else:
        out.extend([thin, _role(opts, "assistant").upper() + _assistant_time_ref(turn, opts), thin, ""])
        if turn.assistant:
            out.append(turn.assistant)
            out.append("")
        if turn.artifact_kind:
            out.append(_artifact_line(turn, opts))
            out.append("")
    return out


def build_export_document(opts: ExportOptions, preview: bool) -> tuple[str | None, ExportCounts]:
    """Capture once, gate once, format the gated turns in bounded batches.

    The byte ceiling counts the WHOLE delivered file — the preamble and the separators
    included — so a served export can never be larger than the cap it was accepted
    under, and a refusal never ships a quietly partial file.
    """
    turns, counts = scan_snapshot(opts.session_id)
    # A single answer's file holds only that answer, so its name must not come from a message.
    opts.title = chat_title(opts.session_id, turns, from_messages=not opts.request_id)
    if opts.request_id:
        selected = [turn for turn in turns if turn.request_id == opts.request_id]
        if len(selected) != 1:
            raise ExportRefused(404 if not selected else 409, "answer_identity_unavailable",
                                "The selected answer is missing or its identity is not unique.")
        turn = selected[0]
        if turn.unavailable or not turn.assistant.strip():
            raise ExportRefused(403, "answer_unavailable", "This answer is unavailable under the current privacy policy.")
        if len(turn.assistant.encode("utf-8")) > MAX_EXPORT_BYTES:
            raise ExportRefused(413, "export_too_large", "The selected answer exceeds the export limit.")
        answer_counts = ExportCounts(turns=1, assistant_messages=1, snapshot_rows=counts.snapshot_rows)
        return (None if preview else turn.assistant), answer_counts
    if preview:
        return None, counts

    fmt_turn = format_turn_markdown if opts.fmt in ("md", "pdf") else format_turn_text
    separator = _MD_SEPARATOR if opts.fmt in ("md", "pdf") else _TXT_SEPARATOR
    header = "\n".join(_preamble_lines(opts, counts))
    sep_len = len(separator.encode("utf-8"))
    # `total` tracks the size of the exact bytes the response will carry:
    # header + sep + block + (sep + block)…  — the preamble counts from the first byte.
    total = len(header.encode("utf-8")) + sep_len
    chunks: list[str] = []
    first = True
    for batch in _batches(turns, PAGE_SIZE):
        for turn in batch:
            block = "\n".join(fmt_turn(turn, opts))
            if first:
                first = False
            else:
                total += sep_len
            total += len(block.encode("utf-8"))
            chunks.append(block)
            if total > MAX_EXPORT_BYTES:
                raise ExportRefused(
                    413,
                    "export_too_large",
                    "This conversation is larger than the single-file export cap, so nothing partial is offered.",
                    message_count=counts.message_count(),
                    formatted_bytes=total,
                    max_bytes=MAX_EXPORT_BYTES,
                )
    body = separator.join(chunks)
    document = header + separator + body
    return document, counts


def chat_title(session_id: str, turns: list[_Turn], *, from_messages: bool = True) -> str:
    """The name an export carries (file name, heading, PDF title), or "" for none.

    The same rule ``list_conversation_sessions`` serves the sidebar with: a name the person
    gave the chat wins, else the chat's first message (its first 80 characters). The name
    never carries message text the file leaves out: a whole-chat export contains every turn
    read here, and a single-answer export (``from_messages=False``) contains only the answer,
    so it is named only by a name the person gave the chat, never by the question it answers.
    """
    try:
        from core.persistent_memory import load_session_meta

        custom = str((load_session_meta().get(session_id) or {}).get("title") or "")
    except Exception:
        custom = ""
    custom = _WHITESPACE.sub(" ", custom).strip()
    if custom or not from_messages:
        return custom
    for turn in turns:
        first = turn.user.strip()
        if first:
            return _WHITESPACE.sub(" ", first[:80]).strip()
    return ""


def _extends_previous(ch: str) -> bool:
    """True for a character that belongs to the one before it: a joiner, a combining mark,
    an emoji variation selector, a skin tone or an emoji tag."""
    code = ord(ch)
    return (ch == _ZWJ or unicodedata.category(ch) in ("Mn", "Me", "Mc")
            or code in (0xFE0E, 0xFE0F) or 0x1F3FB <= code <= 0x1F3FF or 0xE0020 <= code <= 0xE007F)


def _grapheme_safe_cut(text: str, index: int) -> int:
    """Move a cut point back until it falls between two whole characters as a person sees
    them, so a cut never leaves half an emoji, a lone mark or half of a flag."""
    while 0 < index < len(text) and (_extends_previous(text[index]) or text[index - 1] == _ZWJ):
        index -= 1
    if 0 < index < len(text) and "\U0001f1e6" <= text[index] <= "\U0001f1ff":
        flags = 0
        while flags < index and "\U0001f1e6" <= text[index - 1 - flags] <= "\U0001f1ff":
            flags += 1
        if flags % 2:
            index -= 1
    return index


def filename_title(title: str, *, max_bytes: int = MAX_FILENAME_TITLE_BYTES) -> str:
    """A chat title made safe to be part of a file name on Windows, macOS and Linux.

    Letters of every script and emoji are kept. Path characters, characters a desktop system
    forbids in file names, control characters and invisible direction overrides (which could
    make ``report<RLO>fdp.exe`` look like a PDF) become spaces. Leading dots (a hidden file) and
    dashes (read as an option by command-line tools) go, and so do trailing dots and spaces
    (dropped by Windows). A long title is cut at a word where
    possible, within ``max_bytes`` of UTF-8, and marked with an ellipsis.
    """
    text = unicodedata.normalize("NFC", str(title or ""))
    out: list[str] = []
    for ch in text:
        category = unicodedata.category(ch)
        if ch in (_ZWJ, _ZWNJ):
            out.append(ch)
        elif ch in _FILENAME_FORBIDDEN or category[0] in ("C", "Z"):
            out.append(" ")
        else:
            out.append(ch)
    cleaned = _WHITESPACE.sub(" ", "".join(out)).strip()
    cleaned = cleaned.lstrip(". -").rstrip(". ")
    if len(cleaned.encode("utf-8")) <= max_bytes:
        return cleaned
    budget = max_bytes - len("…".encode())
    index = len(cleaned)
    while index and len(cleaned[:index].encode("utf-8")) > budget:
        index -= 1
    index = _grapheme_safe_cut(cleaned, index)
    cut = cleaned[:index]
    space = cut.rfind(" ")
    if space >= len(cut) // 2:
        cut = cut[:space]
    cut = cut.rstrip(" .-")
    return (cut + "…") if cut else ""


def _ascii_filename(name: str) -> str:
    """The plain-ASCII ``filename=`` fallback for clients that ignore ``filename*``."""
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    folded = "".join(ch if (ch.isalnum() or ch in " .-_()&,+'!") else " " for ch in folded)
    return _WHITESPACE.sub(" ", folded).strip()


def export_filenames(opts: ExportOptions, *, today: str = "") -> tuple[str, str]:
    """(file name, ASCII fallback): ``<title> - VOOL chat - <date>.<ext>``.

    An untitled chat is ``VOOL chat - <date>.<ext>``; a single answer says "VOOL answer".
    The label follows the app language; the ASCII fallback keeps the English label when the
    localized one has no ASCII form.
    """
    date = today or datetime.now().astimezone().strftime("%Y-%m-%d")
    if opts.request_id:
        label = _text(opts, "chat_export.answer_label", "VOOL answer")
        english_label = "VOOL answer"
    else:
        label = _fallback_title(opts)
        english_label = "VOOL chat"
    title = filename_title(opts.title)
    if title.casefold() in (label.casefold(), english_label.casefold()):
        title = ""
    parts = [title, label, date] if title else [label, date]
    name = " - ".join(parts) + "." + opts.fmt
    ascii_title = _ascii_filename(title)
    ascii_label = _ascii_filename(label) if _ascii_filename(label) == label else english_label
    # A title that loses most of itself to ASCII folding (Arabic, CJK, emoji) is left out of
    # the fallback rather than shown as a stray fragment.
    if len(ascii_title) * 2 < len(title):
        ascii_title = ""
    ascii_parts = [ascii_title, ascii_label, date] if ascii_title else [ascii_label, date]
    return name, " - ".join(ascii_parts) + "." + opts.fmt


def content_disposition(name: str, ascii_name: str) -> str:
    """``attachment`` with an ASCII ``filename=`` and the exact UTF-8 name in ``filename*``
    (RFC 6266 / RFC 5987): every byte outside RFC 5987's attr-char is percent-encoded, so a
    quote, semicolon, backslash, percent sign or newline in a title can never break the header."""
    encoded = quote(name, safe="!#$&+-.^_`|~")
    safe_ascii = ascii_name.replace("\\", " ").replace('"', " ").replace("%", " ")
    return f"attachment; filename=\"{safe_ascii}\"; filename*=UTF-8''{encoded}"


def _export_locale(requested: str) -> str:
    """The app language the page asked for (``ui_locale``), or English. Never guessed."""
    from core.i18n.catalog import resolve_render_locale

    try:
        return resolve_render_locale(requested, None, "en")
    except Exception:
        return "en"


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def handle_chat_export_get(
    *,
    query: dict[str, list[str]],
    runtime: RuntimeServices,
    client_host: str = "",
) -> ApiResponse:
    """The one door behind service.py's ``/api/chat/export`` delegation arm.

    Owner-local only (the same loopback-peer trust the other private chat doors use) —
    there is no unauthenticated export path.
    """
    if not is_loopback_host(client_host):
        return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)

    session_id = str((query.get("session") or [""])[0] or "").strip()
    if not session_id:
        return apply_runtime_headers(
            json_response(400, {"ok": False, "error": "session_required", "message": "Which chat should be exported?"}),
            runtime,
        )

    fmt = str((query.get("format") or ["md"])[0] or "md").strip().lower()
    if fmt not in ("md", "txt", "pdf"):
        return apply_runtime_headers(
            json_response(400, {"ok": False, "error": "unknown_format", "message": "Export format must be md, txt or pdf."}),
            runtime,
        )

    preview = _truthy((query.get("preview") or ["0"])[0])
    opts = ExportOptions(
        session_id=session_id,
        fmt=fmt,
        include_timestamps=_truthy((query.get("timestamps") or ["0"])[0]),
        include_attachments=not _truthy((query.get("no_attachments") or ["0"])[0]),
        request_id=str((query.get("request_id") or [""])[0] or "").strip(),
        locale=_export_locale(str((query.get("ui_locale") or [""])[0] or "")),
    )

    # A deleted session's namespace is not exportable — the same lifecycle truth the
    # transcript reader serves.
    from core.context_namespace import load_chat_namespace

    namespace = load_chat_namespace(session_id)
    if namespace is not None and namespace.lifecycle_state == "deleted":
        return apply_runtime_headers(
            json_response(404, {"ok": False, "error": "session_not_found", "message": "This chat was deleted."}),
            runtime,
        )

    try:
        document, counts = build_export_document(opts, preview=preview)
    except ExportRefused as exc:
        return apply_runtime_headers(json_response(exc.status, exc.to_dict()), runtime)

    if preview:
        return apply_runtime_headers(
            json_response(
                200,
                {
                    "ok": True,
                    "session_id": session_id,
                    "format": opts.fmt,
                    "turns": counts.turns,
                    "user_messages": counts.user_messages,
                    "assistant_messages": counts.assistant_messages,
                    "message_count": counts.message_count(),
                    "attachment_items": counts.attachment_items,
                    "artifact_cards": counts.artifact_cards,
                    "unavailable_by_policy": counts.unavailable,
                    "unreadable_rows": counts.unreadable_rows,
                    "unreadable_detail": {
                        "malformed_json": counts.malformed_rows,
                        "blank_rows": counts.blank_rows,
                        "invalid_bytes": counts.invalid_bytes_rows,
                    },
                    "snapshot_rows": counts.snapshot_rows,
                    "page_size": PAGE_SIZE,
                    "max_bytes": MAX_EXPORT_BYTES,
                    "complete": True,
                    "source": "persisted conversation transcript (conversation log)",
                    "excluded": [
                        "model reasoning",
                        "tool/activity traces",
                        "user send times (not persisted)",
                        "attachment file bytes (referenced by name, kind, size, outcome only)",
                    ],
                },
            ),
            runtime,
        )

    assert document is not None
    content_type = "text/markdown; charset=utf-8" if opts.fmt == "md" else "text/plain; charset=utf-8"
    body = document.encode('utf-8')
    if opts.fmt == 'pdf':
        from core.presentation.render_pdf import PdfRefused, render_markdown_pdf

        try:
            body = render_markdown_pdf(document, title=opts.title or _fallback_title(opts))
        except ImportError:
            return apply_runtime_headers(json_response(503,{'ok':False,'error':'pdf_renderer_unavailable',
                'message':'The PDF renderer is not installed in this build.'}),runtime)
        except PdfRefused as exc:
            return apply_runtime_headers(json_response(422,{'ok':False,'error':'pdf_export_refused',
                'message':str(exc)}),runtime)
        content_type = 'application/pdf'
    filename, ascii_filename = export_filenames(opts)
    return apply_runtime_headers(
        ApiResponse(
            status=200,
            content_type=content_type,
            body=body,
            headers={
                "Content-Disposition": content_disposition(filename, ascii_filename),
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        ),
        runtime,
    )
