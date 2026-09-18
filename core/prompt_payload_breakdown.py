"""Where a turn's prompt tokens actually went, by category.

Asked on 2026-08-12 -- "why did a three-word question cost 4,925 tokens" -- nothing in the runtime
could answer. `PromptAssemblyReport` itemises the RETRIEVED context (bootstrap / relevant / cold),
which is one of eight things in the prompt and, on the turns that hurt, not the expensive one. The
system prompt was a single opaque 21,220-character string, and the native tool schemas on the wire
(34,270 characters, 66 definitions) were counted nowhere at all.

This module measures the assembled prompt instead of estimating it. The measurement is not a regex
pass over the finished blob: `core/prompt_normalizer.py` LABELS and SIZES each thing it appends --
every system-prompt segment by name, every message by category -- and this module totals those
labels. A segment that stops being emitted therefore disappears from the report, rather than the
report silently keeping a stale line for it; and a new segment nobody labelled shows up as
unlabelled rather than being quietly folded into a neighbour.

The assembler records each segment's `chars`/`tokens` and NOT its prose, because that record travels
on `InternalModelRequest.metadata`, which the router copies per ranked candidate -- carrying the text
would duplicate a 17KB system prompt once per candidate to report its size. A hand-built segment
list carrying `text` instead is still measured here, so a test or a future non-chat assembler is not
silently reported as zero.

The eight categories are the ones the payload actually divides into:

    system_bootstrap    persona, tone, style, safety, capability grounding, output-format rules,
                        tooling guidance, and the server-owned per-turn runtime facts (the clock,
                        the workspace folder name, the selected model, the project binding)
    chat_history        prior turns of this conversation
    project_context     the assembled "Relevant context and evidence" block
    files_artifacts     retrieved-context capsules and file/artifact content
    tool_catalog        the runtime tool catalog -- the prose dialect in the system prompt AND the
                        native tool schemas on the wire, which are the same information twice
    activity_receipts   same-turn tool observations replayed as ground truth
    answer_binder       authoritative corrections, canonical-grounding directives, and
                        focused-follow-up provenance binding
    routing_overhead    prompt spent by the turn's OWN extra model calls (a tool-intent preflight,
                        a planner round, a verifier pass) rather than by the answering call

`current_user_turn` is tracked separately and is deliberately NOT overhead: it is the only part of
the payload the operator actually wrote. `overhead_tokens()` is everything else, which is the
number the tiny-turn guards assert on -- a 3-token question that ships 5,745 tokens has 5,742
tokens of overhead, and saying "5,745" hides the ratio that matters.

`routing_overhead` is zero unless a caller passes measured per-call payloads through
`routing_calls=`. It is never inferred: a call this module did not see is not counted, and the
report says so rather than implying the answering call was the whole turn.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from core.prompt_assembly_report import estimate_tokens

CATEGORY_SYSTEM_BOOTSTRAP = "system_bootstrap"
CATEGORY_CHAT_HISTORY = "chat_history"
CATEGORY_PROJECT_CONTEXT = "project_context"
CATEGORY_FILES_ARTIFACTS = "files_artifacts"
CATEGORY_TOOL_CATALOG = "tool_catalog"
CATEGORY_ACTIVITY_RECEIPTS = "activity_receipts"
CATEGORY_ANSWER_BINDER = "answer_binder"
CATEGORY_ROUTING_OVERHEAD = "routing_overhead"
CATEGORY_CURRENT_USER_TURN = "current_user_turn"

#: The eight overhead categories, in report order. `current_user_turn` is not among them.
OVERHEAD_CATEGORIES: tuple[str, ...] = (
    CATEGORY_SYSTEM_BOOTSTRAP,
    CATEGORY_CHAT_HISTORY,
    CATEGORY_PROJECT_CONTEXT,
    CATEGORY_FILES_ARTIFACTS,
    CATEGORY_TOOL_CATALOG,
    CATEGORY_ACTIVITY_RECEIPTS,
    CATEGORY_ANSWER_BINDER,
    CATEGORY_ROUTING_OVERHEAD,
)

ALL_CATEGORIES: tuple[str, ...] = (*OVERHEAD_CATEGORIES, CATEGORY_CURRENT_USER_TURN)

#: Key under which `core/prompt_normalizer.py` records its system-prompt segmentation.
SEGMENTS_METADATA_KEY = "prompt_payload_segments"
#: Key under which each `InternalMessage` records the category it belongs to.
MESSAGE_CATEGORY_METADATA_KEY = "payload_category"

_ROLE_FALLBACK_CATEGORIES = {
    "system": CATEGORY_SYSTEM_BOOTSTRAP,
    "context": CATEGORY_PROJECT_CONTEXT,
    "assistant": CATEGORY_CHAT_HISTORY,
}


@dataclass(frozen=True)
class PayloadSegment:
    """One labelled contributor to the prompt, with its measured size."""

    name: str
    category: str
    chars: int
    tokens: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "chars": int(self.chars),
            "tokens": int(self.tokens),
        }


@dataclass(frozen=True)
class PromptPayloadBreakdown:
    """The measured payload of one assembled prompt, attributed to categories."""

    segments: tuple[PayloadSegment, ...] = field(default_factory=tuple)
    unlabelled_segments: tuple[str, ...] = field(default_factory=tuple)
    routing_calls_measured: int = 0

    # -- totals ----------------------------------------------------------------------------

    def category_tokens(self) -> dict[str, int]:
        totals = {name: 0 for name in ALL_CATEGORIES}
        for segment in self.segments:
            totals[segment.category] = totals.get(segment.category, 0) + int(segment.tokens)
        return totals

    def category_chars(self) -> dict[str, int]:
        totals = {name: 0 for name in ALL_CATEGORIES}
        for segment in self.segments:
            totals[segment.category] = totals.get(segment.category, 0) + int(segment.chars)
        return totals

    def total_tokens(self) -> int:
        return sum(int(segment.tokens) for segment in self.segments)

    def total_chars(self) -> int:
        return sum(int(segment.chars) for segment in self.segments)

    def tokens_for(self, category: str) -> int:
        return self.category_tokens().get(str(category), 0)

    def overhead_tokens(self) -> int:
        """Everything the operator did not type."""
        return self.total_tokens() - self.tokens_for(CATEGORY_CURRENT_USER_TURN)

    def segment_tokens(self, name: str) -> int:
        return sum(int(s.tokens) for s in self.segments if s.name == str(name))

    def largest_category(self) -> tuple[str, int]:
        totals = [(name, count) for name, count in self.category_tokens().items() if count]
        if not totals:
            return ("", 0)
        return max(totals, key=lambda item: item[1])

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_tokens": self.total_tokens(),
            "total_chars": self.total_chars(),
            "overhead_tokens": self.overhead_tokens(),
            "category_tokens": self.category_tokens(),
            "category_chars": self.category_chars(),
            "segments": [segment.to_dict() for segment in self.segments],
            "unlabelled_segments": list(self.unlabelled_segments),
            "routing_calls_measured": int(self.routing_calls_measured),
            "largest_category": list(self.largest_category()),
        }

    def render_table(self) -> str:
        """A fixed-width category table, for a ledger entry or a report."""
        tokens = self.category_tokens()
        chars = self.category_chars()
        total = self.total_tokens() or 1
        rows = [f"{'category':<20} {'chars':>8} {'tokens':>8} {'share':>7}"]
        for name in ALL_CATEGORIES:
            rows.append(
                f"{name:<20} {chars.get(name, 0):>8} {tokens.get(name, 0):>8} "
                f"{tokens.get(name, 0) / total:>6.1%}"
            )
        rows.append(f"{'TOTAL':<20} {self.total_chars():>8} {self.total_tokens():>8} {1.0:>6.1%}")
        rows.append(f"{'(overhead)':<20} {'':>8} {self.overhead_tokens():>8}")
        return "\n".join(rows)


def _as_count(value: Any) -> int:
    """A non-negative integer size, or 0 for anything unreadable."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _segment(name: str, category: str, text: str) -> PayloadSegment | None:
    content = str(text or "")
    if not content.strip():
        return None
    return PayloadSegment(
        name=str(name),
        category=str(category),
        chars=len(content),
        tokens=estimate_tokens(content),
    )


def _native_tool_text(native_tool_payload: Iterable[Any]) -> str:
    """Serialise native tool definitions the way a provider will ship them.

    Best-effort by construction: the definitions are provider-contract dataclasses, so this walks
    whatever shape it is handed. A definition that cannot be serialised still contributes its
    ``repr`` rather than vanishing -- an uncounted tool schema is the exact defect this module
    exists to remove.
    """
    parts: list[str] = []
    for definition in native_tool_payload or ():
        if isinstance(definition, (str, bytes)):
            parts.append(definition.decode() if isinstance(definition, bytes) else definition)
            continue
        payload = definition
        for attribute in ("to_dict", "as_dict", "to_wire", "model_dump"):
            method = getattr(definition, attribute, None)
            if callable(method):
                try:
                    payload = method()
                    break
                except Exception:
                    payload = definition
        if not isinstance(payload, (dict, list)):
            payload = getattr(definition, "__dict__", None) or payload
        try:
            parts.append(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
        except Exception:
            parts.append(repr(payload))
    return "".join(parts)


def measure_prompt_payload(
    request: Any,
    *,
    native_tool_payload: Iterable[Any] = (),
    routing_calls: Iterable[Any] = (),
) -> PromptPayloadBreakdown:
    """Measure an assembled `InternalModelRequest`, attributing every token to a category.

    `native_tool_payload` is the provider-native tool schema list the router attaches AFTER
    assembly (`core/memory_first_router.py`); it is counted as `tool_catalog` because it is the
    same catalog in a second encoding. `routing_calls` accepts already-measured
    `PromptPayloadBreakdown` objects for the turn's other model calls, whose totals roll up as
    `routing_overhead`.
    """

    segments: list[PayloadSegment] = []
    unlabelled: list[str] = []

    metadata = dict(getattr(request, "metadata", None) or {})
    recorded = list(metadata.get(SEGMENTS_METADATA_KEY) or [])
    system_is_segmented = bool(recorded)
    for entry in recorded:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip() or "unnamed_system_segment"
        category = str(entry.get("category") or "").strip() or CATEGORY_SYSTEM_BOOTSTRAP
        if category not in ALL_CATEGORIES:
            unlabelled.append(name)
            category = CATEGORY_SYSTEM_BOOTSTRAP
        # Already-measured sizes, because the assembler records chars/tokens rather than the prose
        # (see `_recorded_system_segments`). A `text` key is still accepted so a caller that builds
        # a segment list by hand -- a test, or a future non-chat assembler -- is measured the same
        # way rather than silently reported as zero.
        chars = _as_count(entry.get("chars"))
        tokens = _as_count(entry.get("tokens"))
        if chars <= 0 and tokens <= 0:
            built = _segment(name, category, str(entry.get("text") or ""))
            if built is not None:
                segments.append(built)
            continue
        segments.append(PayloadSegment(name=name, category=category, chars=chars, tokens=tokens))

    for index, message in enumerate(list(getattr(request, "messages", None) or [])):
        role = str(getattr(message, "role", "") or "").strip().lower()
        content = str(getattr(message, "content", "") or "")
        message_metadata = dict(getattr(message, "metadata", None) or {})
        category = str(message_metadata.get(MESSAGE_CATEGORY_METADATA_KEY) or "").strip()
        if role == "system" and system_is_segmented:
            # Already accounted for, segment by segment. Counting the concatenation too would
            # double every system token and make the report agree with itself about a total that
            # was never sent.
            continue
        if not category:
            if role == "user":
                # The last user message is the operator's own turn; earlier ones are assembled
                # context that predates the message-category labels.
                is_last_user = not any(
                    str(getattr(later, "role", "") or "").strip().lower() == "user"
                    for later in list(request.messages)[index + 1 :]
                )
                category = (
                    CATEGORY_CURRENT_USER_TURN if is_last_user else CATEGORY_PROJECT_CONTEXT
                )
            else:
                category = _ROLE_FALLBACK_CATEGORIES.get(role, CATEGORY_PROJECT_CONTEXT)
            if role == "system":
                unlabelled.append("system_prompt")
        if category not in ALL_CATEGORIES:
            unlabelled.append(f"message_{index}")
            category = CATEGORY_PROJECT_CONTEXT
        built = _segment(
            str(message_metadata.get("payload_segment_name") or f"{role}_message_{index}"),
            category,
            content,
        )
        if built is not None:
            segments.append(built)

    native_text = _native_tool_text(native_tool_payload)
    native_segment = _segment("native_tool_schemas", CATEGORY_TOOL_CATALOG, native_text)
    if native_segment is not None:
        segments.append(native_segment)

    routing_measured = 0
    for call in routing_calls or ():
        tokens = getattr(call, "total_tokens", None)
        chars = getattr(call, "total_chars", None)
        if not callable(tokens):
            continue
        routing_measured += 1
        segments.append(
            PayloadSegment(
                name=f"routing_call_{routing_measured}",
                category=CATEGORY_ROUTING_OVERHEAD,
                chars=int(chars() if callable(chars) else 0),
                tokens=int(tokens()),
            )
        )

    return PromptPayloadBreakdown(
        segments=tuple(segments),
        unlabelled_segments=tuple(dict.fromkeys(unlabelled)),
        routing_calls_measured=routing_measured,
    )


__all__ = [
    "ALL_CATEGORIES",
    "CATEGORY_ACTIVITY_RECEIPTS",
    "CATEGORY_ANSWER_BINDER",
    "CATEGORY_CHAT_HISTORY",
    "CATEGORY_CURRENT_USER_TURN",
    "CATEGORY_FILES_ARTIFACTS",
    "CATEGORY_PROJECT_CONTEXT",
    "CATEGORY_ROUTING_OVERHEAD",
    "CATEGORY_SYSTEM_BOOTSTRAP",
    "CATEGORY_TOOL_CATALOG",
    "MESSAGE_CATEGORY_METADATA_KEY",
    "OVERHEAD_CATEGORIES",
    "SEGMENTS_METADATA_KEY",
    "PayloadSegment",
    "PromptPayloadBreakdown",
    "measure_prompt_payload",
]
