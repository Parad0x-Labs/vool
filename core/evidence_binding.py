"""Did the evidence this turn retrieved actually reach the answer it shipped?

The defect this exists to remove, measured live on c6eed761 (2026-08-14, local and cloud lanes
both). A turn asking for a live value ran retrieval to completion -- ``web_calls: 8``, one
``vool.web_retrieval_receipt.v1``, ``web_retrieval_started``/``web_retrieval_completed`` both in
Activity -- and the visible answer was::

    I can look up the current water temperature in the North Sea for you. Let me check that.

on the local lane, and on the cloud lane::

    Searching for current North Sea water temperature... (web search for "North Sea current ...")

Every gate on the way out agreed with it. ``inspect_answer_completeness`` is a *text-shape*
inspector -- it asks whether an answer looks cut off -- and a promise is not cut off, so it
returned ``incomplete: false``. ``output_validation_outcome`` reads that verdict, so the turn was
stamped ``fulfillment_status: fulfilled`` with ``retryable: false``, which suppressed both the
bounded repair and the fallback. The retrieved evidence was discarded in silence.

What this module asks, and what it deliberately does not
--------------------------------------------------------

It asks ONE question, from evidence rather than from prose: **the turn retrieved content -- does
the answer carry any of it?** It never classifies the wording. There is no list of promise phrases
here, and there must not be one: "Let me check that" is a reproduction of the failure, not its
definition, and a runtime that recognised that sentence would still ship the next phrasing of it.

The mechanism is per-claim structural support against the retrieved *content*
(`core.claim_support`), plus the original lexical witness kept for observability:

* Terms are taken from what retrieval actually returned -- ``summary``, ``result_title``,
  ``live_quote`` -- not from URLs or provider labels, which say where VOOL looked rather than what
  it found.
* The request's own terms are SUBTRACTED first. This is the load-bearing step. Retrieval for "North
  Sea water temperature" returns notes that naturally repeat "north", "sea", "water" and
  "temperature", so an answer that merely restates the question would overlap those and read as
  grounded. Only terms the evidence introduced can witness that the evidence was read.
* The answer is segmented into independently testable claims, and each claim is matched only
  against bound source content. Support requires structure appropriate to the claim -- a matching
  value/version/date, quoted span, or named entity, or two distinctive shared terms for anchor-less
  prose. One shared generic word ("released") never suffices: measured on a2308a26, that floor let
  four fabricated Rust headlines read as grounded because a real headline also said "Released".
* ``grounded`` is True only when evidence introduced something AND every claim in the answer is
  supported (``coverage == "full"``). One supported claim cannot ground a fabricated sibling; the
  per-claim map (``claim_support``) names exactly which claims lack support.

When retrieval proved nothing, it still refuses to convict: no notes, notes with no content, or
notes that introduced nothing beyond the question all yield ``has_evidence=False``, and
``evidence_discarded`` stays False -- the caller is told nothing is provable either way. But such a
turn no longer reads ``grounded=True``: an answer over no usable evidence is not grounded, it is
merely unprovable, and the two are no longer spelled the same way.

It is deliberately NOT a truth judgement. A claim structurally supported by a source may still
misuse it; judging that is a model's job. This is the floor beneath it: evidence that was paid
for -- in wall clock, in outbound calls, in the user's privacy budget -- must show up in the reply
per claim, or the turn is reported as carrying unsupported claims.

Callers
-------

``core.agent_runtime.turn_reasoning`` is the only seam where the retrieved notes and the final
answer text are both in scope. It stamps the verdict into the turn's response control so
``core.runtime_task_outcome.output_validation_outcome`` can translate an unbound turn into
``PARTIALLY_FULFILLED``/``retryable`` instead of ``fulfilled`` -- which is what re-opens the bounded
repair the runtime already owns.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from core.claim_support import ClaimSupportMap, match_claims

#: Fields carrying what retrieval FOUND. `result_url`, `origin_domain` and `source_profile_label`
#: are deliberately absent: they record where VOOL looked, and an answer naming the domain it was
#: about to consult would otherwise read as having read it.
_CONTENT_FIELDS = ("summary", "live_quote", "result_title", "snippet", "text", "content")

_TOKEN_RE = re.compile(r"[0-9]+(?:[.,][0-9]+)*|[^\W\d_]+", re.UNICODE)

#: Closed-class words carry no evidence. Kept small and structural -- this is not a topic list, and
#: nothing domain-specific belongs in it.
_STOPWORDS = frozenset(
    [
    "a", "an", "the", "and", "or", "but", "if", "then", "than", "that", "this", "these",
    "those", "there", "here", "it", "its", "it's", "is", "are", "was", "were", "be", "been",
    "being", "am", "do", "does", "did", "doing", "have", "has", "had", "having", "will",
    "would", "shall", "should", "can", "could", "may", "might", "must", "of", "in", "on", "at",
    "to", "from", "by", "for", "with", "without", "about", "into", "over", "under", "again",
    "further", "once", "i", "you", "he", "she", "we", "they", "me", "him", "her", "us", "them",
    "my", "your", "his", "our", "their", "what", "which", "who", "whom", "whose", "when",
    "where", "why", "how", "all", "any", "both", "each", "few", "more", "most", "other",
    "some", "such", "no", "nor", "not", "only", "own", "same", "so", "too", "very", "just",
    "also", "as", "up", "down", "out", "off", "above", "below", "between", "during", "before",
    "after", "while", "because",
    ]
)

#: A token shorter than this is noise for grounding purposes ("of", "us"), EXCEPT digits, which are
#: exactly what a live-value answer is made of ("18", "9.4") and are kept at any length.
_MIN_TERM_LENGTH = 3


@dataclass(frozen=True)
class EvidenceBinding:
    """Whether retrieved evidence supports the answer that shipped, claim by claim."""

    has_evidence: bool = False
    grounded: bool = False
    introduced_terms: tuple[str, ...] = ()
    shared_terms: tuple[str, ...] = ()
    claim_support: ClaimSupportMap | None = None

    @property
    def evidence_discarded(self) -> bool:
        """Retrieval produced content this turn and the answer's claims are not covered by it."""

        return self.has_evidence and not self.grounded

    @property
    def coverage(self) -> str:
        """The per-claim coverage status (`core.claim_support.ClaimSupportMap.coverage`)."""

        return self.claim_support.coverage if self.claim_support is not None else "no_claims"

    @property
    def claims(self) -> tuple[Any, ...]:
        return self.claim_support.claims if self.claim_support is not None else ()

    @property
    def unsupported_claims(self) -> tuple[Any, ...]:
        return (
            self.claim_support.unsupported_claims if self.claim_support is not None else ()
        )

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "has_evidence": self.has_evidence,
            "grounded": self.grounded,
            "evidence_discarded": self.evidence_discarded,
            "introduced_term_count": len(self.introduced_terms),
            "shared_terms": list(self.shared_terms),
            "coverage": self.coverage,
        }
        if self.claim_support is not None:
            payload["claim_support"] = self.claim_support.as_dict()
        return payload


def _fold(text: Any) -> str:
    """Casefold and strip accents so 'Reykjavík' and 'Reykjavik' are the same term."""

    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(char for char in decomposed if not unicodedata.combining(char)).casefold()


def content_terms(text: Any) -> set[str]:
    """Substantive terms in `text`: words of real length plus every number, minus closed-class words."""

    terms: set[str] = set()
    for match in _TOKEN_RE.finditer(_fold(text)):
        token = match.group(0)
        if token in _STOPWORDS:
            continue
        if token[0].isdigit():
            terms.add(token.replace(",", "."))
            continue
        if len(token) >= _MIN_TERM_LENGTH:
            terms.add(token)
    return terms


def _note_content(note: Any) -> str:
    if not isinstance(note, Mapping):
        return str(note or "")
    parts = [str(note.get(field) or "") for field in _CONTENT_FIELDS]
    return " ".join(part for part in parts if part.strip())


def evidence_terms(notes: Iterable[Any] | None) -> set[str]:
    """Every substantive term the retrieved CONTENT carries."""

    terms: set[str] = set()
    for note in list(notes or []):
        terms |= content_terms(_note_content(note))
    return terms


def inspect_evidence_binding(
    *,
    answer: Any,
    notes: Sequence[Any] | None,
    request_text: Any = "",
    as_of: Any = None,
) -> EvidenceBinding:
    """Whether the content retrieval returned supports `answer`, claim by claim.

    When retrieval introduced nothing beyond the request's own words there is nothing to witness:
    the verdict carries ``has_evidence=False`` (so ``evidence_discarded`` cannot fire) and
    ``grounded=False`` (an answer over no usable evidence is unprovable, not grounded).

    ``grounded`` is True only when the per-claim map covers every claim -- one incidental shared
    word, or one supported claim beside a fabricated one, no longer grounds the whole answer.
    ``as_of`` (optional, a date or ISO string) anchors recency checks for claims that assert
    current truth; without it the newest date the notes carry is used, and with neither, currency
    is recorded as unverified rather than guessed.
    """

    support = match_claims(
        answer=answer, notes=notes, request_text=request_text, as_of=as_of
    )

    found = evidence_terms(notes)
    if not found:
        return EvidenceBinding(has_evidence=False, grounded=False, claim_support=support)

    # Subtract the question. Terms the user already supplied cannot witness that anything was read.
    introduced = found - content_terms(request_text)
    if not introduced:
        return EvidenceBinding(has_evidence=False, grounded=False, claim_support=support)

    shared = introduced & content_terms(answer)
    return EvidenceBinding(
        has_evidence=True,
        grounded=support.coverage == "full",
        introduced_terms=tuple(sorted(introduced)),
        shared_terms=tuple(sorted(shared)),
        claim_support=support,
    )


def compose_grounded_report(notes: Sequence[Any] | None, *, limit: int = 3) -> str:
    """Render what retrieval actually returned, attributed, for a turn that dropped it.

    This is NOT a canned answer and carries no authored prose beyond the one line that says where
    the text came from: every sentence in it is content a source returned on this turn, and a note
    with no content contributes nothing. It exists because the alternative, when the model discards
    the evidence, is to ship the user a promise -- and a promise is the one thing the turn is
    provably not delivering.

    Follows the ``Source: [label](url).`` convention already used by the live-data lane
    (`core.weather_result_contract`, `core.live_quote_contract`) so an evidence-backed answer looks
    the same wherever it is composed.
    """

    rendered: list[str] = []
    for note in list(notes or []):
        if len(rendered) >= max(1, limit):
            break
        if not isinstance(note, Mapping):
            continue
        body = " ".join(
            str(note.get(field) or "").strip()
            for field in ("summary", "live_quote")
            if str(note.get(field) or "").strip()
        ).strip()
        if not body:
            continue
        url = str(note.get("result_url") or "").strip()
        label = str(note.get("origin_domain") or note.get("source_profile_label") or "").strip()
        if url and label:
            body = f"{body.rstrip('.')}. Source: [{label}]({url})."
        elif not body.endswith((".", "!", "?")):
            body = f"{body}."
        rendered.append(body)

    if not rendered:
        return ""
    lead = "Here is what the sources returned for this turn:"
    return lead + "\n\n" + "\n\n".join(f"- {item}" for item in rendered)


__all__ = [
    "ClaimSupportMap",
    "EvidenceBinding",
    "compose_grounded_report",
    "content_terms",
    "evidence_terms",
    "inspect_evidence_binding",
]
