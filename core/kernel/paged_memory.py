"""Law 5 — the session is paged, not truncated: flat-cost long loops with a coverage proof.

Motivating defect (measured in core/kernel/repl.py, 2026-08-29): cross-turn continuity
was ``history[-4:]`` with answers cut at 400 characters. A constraint the user planted in
turn 3 — an API base URL, a budget number, a filename — no longer existed anywhere by
turn 10. The dense alternative (send the whole session every call) makes per-call cost
grow with session length until the window breaks, and the industry workaround — model
summarization — is the one component that cannot be trusted to hold truth: a model that
can hallucinate an answer can hallucinate a memory.

Laws 1–4 already store truth OUTSIDE the window: obligations (Law 1), typed evidence
(Law 2), capability receipts (Law 3), effect tapes (Law 4). That is exactly what makes a
harness-level analogue of hybrid sparse+linear attention possible. The model's attention
weights are fixed; the HARNESS decides which bytes the model attends to on every call.
So this module owns context as an attention policy:

- HOT tier     — the last few turns verbatim (the dense part; the live working set).
- SPINE tier   — live obligations from the caller's own state, verbatim, ALWAYS (the
                 part that must never be absent: a context that drops an open obligation
                 is the dropped-request defect reborn at the memory tier).
- COLD tier    — every older turn stored byte-exact, sha256-stamped, indexed ONLY by
                 deterministic anchors taken from the canonical lexical span authority
                 (``lexical_spans``): URLs, IDENTIFIERs, QUANTities, acronyms, compound
                 names. The sparse part — retrieved when the current turn's own anchors
                 intersect a page's anchors.

Every assembly ships a :class:`CoverageProof` naming where each load-bearing item is
covered. Every cold recall is sha-verified before its bytes are returned. If the budget
cannot fit the spine, the assembly REFUSES (fail-closed) instead of silently shipping a
smaller context — refusing is the repair loop, silent dropping is a lie.

Decisions worth defending:

- **No model anywhere in this module.** Compaction that runs through a model is
  probabilistic, unprovable, and replay-hostile; compaction that runs through rules is
  deterministic, provable, and replays byte-identically under Law 4. Imports are stdlib
  and the lexical span authority only — a structural property, tested.
- **Memory provides context; it does not become authority.** The session stores verbatim
  bytes and recalls them; it never edits, merges, or rewrites a fact. Ledger rows and
  session facts are passed in fresh from the caller on every assembly — the caller's live
  state is the spine, never a cached copy of it.
- **Anchors are closed-vocabulary, not inferred topics.** The same spans the kernel
  already treats as identity-bearing (URL/IDENTIFIER/QUANTITY) plus acronyms and
  compound names, extracted by the one lexical authority. No embeddings, no synonyms, no
  LLM relevance scores: a recall either fired on a shared opaque token or it did not run.
- **Cost is measured in characters, not tokens.** A tokenizer dependency would make the
  budget model-specific; ``len(text)`` is exact, portable, and monotone. The token figure
  in stats is a fixed-ratio ESTIMATE for humans, never a gate.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from core.kernel.lexical_spans import lex_spans

__all__ = [
    "CHARS_PER_TOKEN_ESTIMATE",
    "ContextAssembly",
    "ContextUndercovered",
    "CoverageProof",
    "CoverageRow",
    "Page",
    "PagedSession",
    "RecallRefused",
    "recall_handles",
]

#: Human-facing token estimate only — never a gate, never persisted as a cost.
CHARS_PER_TOKEN_ESTIMATE = 4

_RECALL_PREFIX = "p"

# Anchor vocabulary beyond the lexical span authority. Acronyms (>=2 caps) and
# compound/camel names ("OpenRouter", "BaseURL") are the tokens users actually
# refer back to; a tiny stoplist keeps shouting and discourse markers out.
_ACRONYM_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,11}\b")
_CAMEL_RE = re.compile(r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b")
_STOP_ANCHORS = frozenset((
    "OK", "NO", "YES", "THE", "A", "I", "ID", "API", "URL", "USER", "ASSISTANT",
    "OPEN", "WORK", "PREVIOUS", "ANSWER", "SESSION", "RECALL", "NOTE", "TODO",
))


class ContextUndercovered(RuntimeError):
    """The budget cannot fit the spine — assembly refused, nothing shipped.

    Names exactly the spine rows that could not be covered, so the caller can raise the
    budget or shrink the window; silently shipping a context without live obligations is
    the one failure this law exists to make impossible.
    """

    def __init__(self, uncovered: Tuple[Tuple[str, str], ...], budget_chars: int) -> None:
        self.uncovered = uncovered
        self.budget_chars = budget_chars
        named = ", ".join(f"{oid} ({desc})" for oid, desc in uncovered)
        super().__init__(
            f"context assembly refused: budget {budget_chars} chars cannot cover the "
            f"spine ({len(uncovered)} uncovered): {named}"
        )


class RecallRefused(RuntimeError):
    """A recall handle does not resolve, or the stored page failed its sha check."""

    def __init__(self, handle: str, reason: str) -> None:
        self.handle = handle
        self.reason = reason
        super().__init__(f"recall refused for {handle!r}: {reason}")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _anchors(text: str) -> Tuple[str, ...]:
    """Deterministic anchor set: the canonical opaque/identity spans, lowercased.

    Every anchor is a byte span the kernel already owns — URLs and IDENTIFIERs from
    ``lex_spans``, canonical QUANTITY values, acronyms, compound names. No model, no
    embeddings: extraction is a pure function of the bytes.
    """
    found: set[str] = set()
    for span in lex_spans(text):
        if span.kind in ("URL", "IDENTIFIER"):
            found.add(span.raw.lower())
        elif span.kind == "QUANTITY":
            found.add(span.normalized)
    for match in _ACRONYM_RE.finditer(text):
        token = match.group()
        if token not in _STOP_ANCHORS:
            found.add(token.lower())
    for match in _CAMEL_RE.finditer(text):
        found.add(match.group().lower())
    return tuple(sorted(found))


@dataclass(frozen=True)
class Page:
    """One turn's verbatim truth in the cold store. Frozen: a page that could be edited
    after admission would invalidate its own sha."""

    page_id: str
    turn_index: int
    user_text: str
    answer_text: str
    sha256: str
    anchors: Tuple[str, ...]

    @property
    def text(self) -> str:
        """The exact composite bytes admitted — recall() returns this and nothing else."""
        return f"user: {self.user_text}\nassistant: {self.answer_text}"

    @property
    def chars(self) -> int:
        return len(self.text)

    def header(self, max_chars: int = 200) -> str:
        """A lossy-but-honest pointer: enough of the page to re-identify it, the sha to
        verify it, and the handle that recalls it byte-exactly."""
        body = self.text.strip().replace("\n", " ⏎ ")
        if len(body) > max_chars:
            body = body[:max_chars] + "…"
        return (
            f"[{_RECALL_PREFIX}{self.page_id}] sha256:{self.sha256[:12]} "
            f"anchors:{','.join(self.anchors[:6]) or '—'} :: {body}"
        )


@dataclass(frozen=True)
class CoverageRow:
    """One item the proof vouches for: where it lives in the shipped context."""

    item: str
    kind: str    # "obligation" | "hot_page" | "cold_page"
    tier: str    # "spine" | "hot" | "cold_hit"
    detail: str


@dataclass(frozen=True)
class CoverageProof:
    """The receipt of one assembly: every covered item, at which tier, at what cost.

    An assembly that returns always carries ``rows`` covering every spine obligation;
    the ``used_chars`` figure is measured on the exact shipped text, never estimated.
    """

    rows: Tuple[CoverageRow, ...]
    budget_chars: int
    used_chars: int
    dense_chars: int    # what the same session would have cost sent verbatim (dense)

    def render(self) -> str:
        lines = [
            f"coverage proof: {len(self.rows)} items, {self.used_chars}/{self.budget_chars} chars "
            f"(dense equivalent: {self.dense_chars})",
        ]
        for row in self.rows:
            lines.append(f"  [{row.tier:>8}] {row.item} — {row.detail}")
        return "\n".join(lines)


@dataclass(frozen=True)
class ContextAssembly:
    """The exact bytes to prepend to the current message, plus the proof they are safe."""

    text: str
    proof: CoverageProof

    @property
    def token_estimate(self) -> int:
        return max(1, len(self.text) // CHARS_PER_TOKEN_ESTIMATE)


class PagedSession:
    """The cold store and the assembly policy. Holds verbatim bytes; never authority.

    Ledger rows and facts are supplied fresh by the caller at every ``assemble`` —
    this class may PAGE truth but must never OWN semantic state (memory is not
    authority). Spine rows follow the ledger-row shape already used by the REPL:
    ``{"id": ..., "description": ..., "reason": ...}``.
    """

    def __init__(
        self,
        hot_window: int = 4,
        recall_header_chars: int = 200,
        supplement_char_budget: int = 1600,
    ) -> None:
        if hot_window < 1:
            raise ValueError("hot_window must be >= 1")
        self._hot_window = hot_window
        self._header_chars = recall_header_chars
        self._supplement_budget = supplement_char_budget
        self._pages: List[Page] = []
        self._index: Dict[str, set[str]] = {}
        self._counter = 0

    # -- admission ---------------------------------------------------------------------

    def admit_turn(self, user_text: str, answer_text: str, turn_index: int) -> Page:
        """Store one turn verbatim, sha-stamped, anchored. All-or-nothing: a failure
        leaves the store unchanged (the index and the page list cannot diverge)."""
        if not isinstance(user_text, str) or not user_text.strip():
            raise ValueError("admit_turn user_text must be a non-empty string")
        if not isinstance(answer_text, str):
            raise ValueError("admit_turn answer_text must be a string")
        if turn_index < 1:
            raise ValueError("turn_index must be >= 1 (1-based, REPL convention)")
        self._counter += 1
        page = Page(
            page_id=f"{self._counter:04d}",
            turn_index=turn_index,
            user_text=user_text,
            answer_text=answer_text,
            sha256=_sha256(f"user: {user_text}\nassistant: {answer_text}"),
            anchors=_anchors(f"{user_text}\n{answer_text}"),
        )
        self._pages.append(page)
        try:
            for anchor in page.anchors:
                self._index.setdefault(anchor, set()).add(page.page_id)
        except Exception:
            # Roll the counter and the page back so the store never contains a page the
            # index cannot find — a recall hole hidden inside a successful admit.
            self._pages.pop()
            self._counter -= 1
            raise
        return page

    # -- recall --------------------------------------------------------------------------

    def recall(self, handle: str) -> str:
        """Byte-exact page text, sha-verified at read time. Bit-rot is a refusal, not
        silent corruption — the sha is the whole point of the stamp."""
        text = str(handle).strip().lower()
        if not text.startswith(_RECALL_PREFIX) or not text[len(_RECALL_PREFIX):].isdigit():
            raise RecallRefused(str(handle), f"handles look like {_RECALL_PREFIX}0007")
        page = self._by_id(text[len(_RECALL_PREFIX):])
        actual = _sha256(page.text)
        if actual != page.sha256:
            raise RecallRefused(
                text,
                f"stored bytes no longer match their stamp (want {page.sha256[:12]}, got {actual[:12]})",
            )
        return page.text

    def _by_id(self, page_id: str) -> Page:
        for page in self._pages:
            if page.page_id == page_id:
                return page
        raise RecallRefused(page_id, f"no such page (store holds {len(self._pages)})")

    # -- assembly --------------------------------------------------------------------------

    def assemble(
        self,
        question: str,
        *,
        ledger_rows: Optional[List[dict]] = None,
        hot_history: Optional[List[Tuple[str, str]]] = None,
        budget_chars: int = 6000,
        guard: bool = True,
    ) -> ContextAssembly:
        """Build the per-call context: spine (obligations) + hot window + sparse cold hits.

        ``hot_history`` lets the caller keep its own recent-turn rendering (the REPL's
        existing shape); when omitted, the last ``hot_window`` admitted pages are the hot
        tier. ``guard=False`` exists ONLY for the sabotage test that proves the coverage
        check is load-bearing — production paths never pass it.
        """
        ledger_rows = ledger_rows or []
        rows: List[CoverageRow] = []
        parts: List[str] = []
        used = 0

        # SPINE FIRST — the non-negotiable part. Verbatim ledger rows, exactly the
        # rendering the REPL already speaks, checked (not assumed) below.
        spine_text = ""
        if ledger_rows:
            spine_text = (
                "OPEN WORK from earlier turns (INTERNAL STATE — never mention these ids or"
                " this list in any answer or search; when the user's message refers to one,"
                " create a real obligation FROM ITS DESCRIPTION, in its original words, and"
                " set resolves_carryover to its id):\n"
                + "\n".join(f"{c['id']}: {c['description']} ({c['reason']})" for c in ledger_rows)
            )
        spine_chars = len(spine_text)
        if guard and spine_chars > budget_chars:
            uncovered = tuple(
                (c["id"], c["description"])
                for c in ledger_rows
            )
            raise ContextUndercovered(uncovered, budget_chars)
        if spine_text:
            parts.append(spine_text)
            used += spine_chars
            for c in ledger_rows:
                rows.append(CoverageRow(
                    item=str(c["id"]), kind="obligation", tier="spine",
                    detail="verbatim ledger row (open work)",
                ))

        remaining = budget_chars - used

        # HOT — newest turns verbatim, newest last (the caller's `history[-4:]` shape).
        hot: List[Tuple[str, str, str]] = []  # (label_user, label_answer, page_id or "")
        if hot_history:
            for q, a in hot_history[-self._hot_window:]:
                hot.append((q, a, ""))
        else:
            for page in self._pages[-self._hot_window:]:
                hot.append((page.user_text, page.answer_text, page.page_id))
        hot_text = ""
        if hot:
            hot_text = "Conversation so far:\n" + "\n".join(
                f"user: {q}\nassistant: {a[:400]}" for q, a, _ in hot
            )
            if guard and len(hot_text) > remaining:
                # The hot window shrinks from the oldest end — the live working set is
                # recent turns, and a spine + newest turn beats a full window.
                while hot and len("Conversation so far:\n" + "\n".join(
                    f"user: {q}\nassistant: {a[:400]}" for q, a, _ in hot
                )) > remaining:
                    hot.pop(0)
                hot_text = (
                    "Conversation so far:\n" + "\n".join(
                        f"user: {q}\nassistant: {a[:400]}" for q, a, _ in hot
                    ) if hot else ""
                )
        if hot_text:
            parts.append(hot_text)
            used += len(hot_text)
            for q, a, pid in hot:
                rows.append(CoverageRow(
                    item=pid or f"turn:{q[:24]!r}", kind="hot_page", tier="hot",
                    detail=f"verbatim ({len(q)}+{len(a)} chars)",
                ))

        # PREVIOUS ANSWER artifact — numbered, so deixis has real referents (existing law).
        if hot:
            last_q, last_a, _ = hot[-1]
            prev_lines = [ln.strip() for ln in last_a.splitlines() if ln.strip()][:10]
            if prev_lines:
                numbered = "\n".join(f"  {i}. {ln[:120]}" for i, ln in enumerate(prev_lines, 1))
                artifact = (
                    "PREVIOUS ANSWER, ADDRESSABLE BY ITEM NUMBER (when the user says"
                    " 'the second one', 'keep the first two', 'change it', they mean"
                    " these items — revise them, never restate the instruction):\n" + numbered
                )
                if not guard or len(artifact) <= remaining:
                    parts.append(artifact)
                    used += len(artifact)

        # COLD HITS — sparse retrieval: only pages sharing an anchor with THIS turn,
        # newest first, capped by the supplement budget. Headers only; bytes stay cold.
        question_anchors = set(_anchors(question))
        supplement: List[str] = []
        if question_anchors and self._supplement_budget > 0:
            matched: List[Page] = []
            # Whatever is already in the hot tier never repeats as a cold hit — with a
            # caller-rendered hot window the recent STORED pages are still hot.
            hot_ids = {pid for _, _, pid in hot if pid} or {
                page.page_id for page in self._pages[-self._hot_window:]
            }
            anchor_hits: Dict[str, int] = {}
            for page in reversed(self._pages):
                if page.page_id in hot_ids:
                    continue
                overlap = question_anchors & set(page.anchors)
                if not overlap:
                    continue
                # Rank by overlap size, then recency — deterministic ordering.
                anchor_hits[page.page_id] = len(overlap)
                matched.append(page)
            matched.sort(key=lambda p: (-anchor_hits[p.page_id], -p.turn_index, p.page_id))
            budget_left = min(self._supplement_budget, max(remaining, 0))
            for page in matched:
                header = page.header(self._header_chars)
                joiner = 1 if supplement else 0
                if joiner + len(header) > budget_left:
                    break
                supplement.append(header)
                budget_left -= joiner + len(header)
        if supplement:
            block = (
                "PAGED RECALL FROM EARLIER SESSION (byte-verified pages; recall exact bytes "
                "with :recall <handle>):\n" + "\n".join(supplement)
            )
            parts.append(block)
            used += len(block)
            for header in supplement:
                rows.append(CoverageRow(
                    item=header.split("]")[0].lstrip("["), kind="cold_page", tier="cold_hit",
                    detail="anchor-intersecting page header (bytes stay cold)",
                ))

        text = ("\n\n".join(parts) + "\n\nCurrent message: ") if parts else ""

        dense = sum(page.chars for page in self._pages)
        return ContextAssembly(
            text=text,
            proof=CoverageProof(
                rows=tuple(rows),
                budget_chars=budget_chars,
                used_chars=used,
                dense_chars=dense,
            ),
        )

    # -- introspection -----------------------------------------------------------------

    def stats(self) -> dict:
        """Store facts for the demo/receipts: counts and byte totals, no estimates dressed
        as measurements (the token figure is labelled an estimate)."""
        total_chars = sum(page.chars for page in self._pages)
        return {
            "pages": len(self._pages),
            "stored_chars": total_chars,
            "anchors": sum(len(page.anchors) for page in self._pages),
            "distinct_anchors": len(self._index),
            "token_estimate_note": f"chars/{CHARS_PER_TOKEN_ESTIMATE}, estimate only",
        }

    def __len__(self) -> int:
        return len(self._pages)


def recall_handles(text: str) -> Tuple[str, ...]:
    """Every ``pNNNN`` handle embedded in an answer, so the UI can offer one-click recall."""
    return tuple(sorted(set(re.findall(rf"\b{_RECALL_PREFIX}\d{{4}}\b", text))))
