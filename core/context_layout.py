"""Append-only context layout — the compiler law behind provable cache hits.

The order in which a turn's context is emitted determines the cache economics
of every downstream model call: Anthropic, OpenAI and Gemini prompt caches are
strictly exact-prefix and invalidate from the first changed token. So the
compiler emits exactly three zones, in exactly this order:

    [ FROZEN   ]  system, skills metadata, tool schemas, long-lived evidence
    [ DIGEST   ]  append-only records of closed work (grows, never rewrites)
    [ FRONTIER ]  open work + pinned pages + recent turns (volatile, smallest)

Laws enforced here (OX-CONTEXT-RUNTIME, Parts II.3 and III):

    NOTHING IS EVER REWRITTEN — ONLY APPENDED OR ARCHIVED.
    A CACHE HIT CARRIES PROVENANCE, OR IT IS NOT A HIT.

``compile_context`` is deterministic: same blocks in, same bytes out, with a
receipt (per-zone hashes) that a later call can check. ``assert_frozen_stable``
catches any mid-context mutation of the frozen zone — the #1 silent cache
buster (timestamps injected into system prompts, reordered tool schemas).
``assert_digest_append_only`` catches any rewrite of history: the previous
digest must survive byte-for-byte as a prefix of the new one.

This module is pure — no I/O, no clock, no policy. Callers own what goes in
each zone; this module owns the ORDER and its verification.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum


class Role(str, Enum):
    FROZEN = "frozen"
    DIGEST = "digest"
    FRONTIER = "frontier"


class LayoutLawViolation(ValueError):
    """A context layout law was violated; the compiled bytes would lie."""


@dataclass(frozen=True)
class ContextBlock:
    """One block of compiled context. ``text`` is the exact emitted bytes."""

    name: str
    role: Role
    text: str

    def __post_init__(self) -> None:
        if not self.name:
            raise LayoutLawViolation("context block must have a name")
        if not isinstance(self.text, str):
            raise LayoutLawViolation(
                f"block {self.name!r}: text must be str, got {type(self.text).__name__}"
            )


_ZONE_ORDER: tuple[Role, ...] = (Role.FROZEN, Role.DIGEST, Role.FRONTIER)


@dataclass(frozen=True)
class CompiledLayout:
    """The compiled context plus the receipt that proves its shape."""

    blocks: tuple[ContextBlock, ...]
    frozen_text: str
    digest_text: str
    frontier_text: str
    receipt: dict[str, object]

    def render(self) -> str:
        return "\n".join(block.text for block in self.blocks)

    def messages(self, *, user_message: str) -> list[dict[str, str]]:
        """Standard chat mapping: frozen+digest ride in the system message.

        Keeping the volatile frontier out of the system message means the
        system prefix is stable across turns within one context generation,
        which is what a provider exact-prefix cache keys on.
        """
        system_parts = [part for part in (self.frozen_text, self.digest_text) if part]
        return [
            {"role": "system", "content": "\n\n".join(system_parts)},
            {"role": "user", "content": user_message},
        ]


def _zone_text(blocks: Sequence[ContextBlock], role: Role) -> str:
    return "\n".join(block.text for block in blocks if block.role is role)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compile_context(blocks: Sequence[ContextBlock]) -> CompiledLayout:
    """Compile blocks into the canonical three-zone layout, or raise.

    Input order is the caller's business; the OUTPUT order is the law's.
    Blocks are emitted grouped frozen → digest → frontier, stable within each
    zone by original input order — never re-sorted by content, because
    content-keyed re-sorting is exactly how mid-context rewrites sneak in.
    The frozen zone may not be empty: a compiled context with no stable
    prefix cannot ever cache.
    """
    if not blocks:
        raise LayoutLawViolation("compiled context may not be empty")
    if all(block.role is not Role.FROZEN for block in blocks):
        raise LayoutLawViolation("frozen zone may not be empty — no stable prefix, no cache")

    ordered = sorted(blocks, key=lambda block: _ZONE_ORDER.index(block.role))
    frozen = _zone_text(ordered, Role.FROZEN)
    digest = _zone_text(ordered, Role.DIGEST)
    frontier = _zone_text(ordered, Role.FRONTIER)
    receipt: dict[str, object] = {
        "frozen_hash": _sha256(frozen),
        "digest_hash": _sha256(digest),
        "frontier_hash": _sha256(frontier),
        "layout_hash": _sha256(
            json.dumps(
                [
                    {"name": b.name, "role": b.role.value, "hash": _sha256(b.text)}
                    for b in ordered
                ],
                sort_keys=True,
                separators=(",", ":"),
            )
        ),
        "frozen_blocks": sum(1 for b in ordered if b.role is Role.FROZEN),
        "digest_blocks": sum(1 for b in ordered if b.role is Role.DIGEST),
        "frontier_blocks": sum(1 for b in ordered if b.role is Role.FRONTIER),
    }
    return CompiledLayout(
        blocks=tuple(ordered),
        frozen_text=frozen,
        digest_text=digest,
        frontier_text=frontier,
        receipt=receipt,
    )


def assert_frozen_stable(
    previous: dict[str, object], current: dict[str, object]
) -> None:
    """The frozen zone may not change within one context generation.

    A changed frozen hash means the system prefix mutated mid-loop — every
    provider cache after that token is dead and the whole prefix re-prefills
    at full price. Call this whenever a context generation is extended.
    """
    if previous.get("frozen_hash") != current.get("frozen_hash"):
        raise LayoutLawViolation(
            "frozen zone changed between compilations "
            f"({previous.get('frozen_hash')!r} → {current.get('frozen_hash')!r}): "
            "mid-context mutation invalidates every downstream prompt cache; "
            "bump the context generation instead of mutating the frozen zone"
        )


def assert_digest_append_only(
    previous: dict[str, object], previous_text: str, current_text: str
) -> None:
    """History may only grow: the old digest must survive byte-for-byte.

    Rewriting the middle of a digest (lossy summarization, re-worded archive
    records, reordered entries) is the compaction-cliff failure mode. The
    previous digest text must be a verbatim prefix of the new digest text.
    """
    if not current_text.startswith(previous_text):
        raise LayoutLawViolation(
            "digest zone was rewritten, not appended: previous digest is not a "
            "verbatim prefix of the new digest — archive, never summarize"
        )
    del previous  # receipt hash kept for callers' provenance; text is the law


def cache_provenance(receipt: dict[str, object], *, cache_hit: bool) -> dict[str, object]:
    """A cache hit carries provenance, or it is not a hit.

    Attaches the layout receipt to a hit/miss verdict so cost reports can be
    audited against what was actually served.
    """
    return {
        "cache_hit": bool(cache_hit),
        "layout_hash": receipt.get("layout_hash"),
        "frozen_hash": receipt.get("frozen_hash"),
        "digest_hash": receipt.get("digest_hash"),
    }
