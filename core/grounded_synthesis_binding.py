"""Retrieval must reach the model call that writes the answer -- and say so, by id.

THE DEFECT THIS CLOSES
----------------------
Measured on 0.5.0 and shipped as an open defect with the live-search UI proof
(a2308a26): a turn that needed current information ran its model call FIRST and its
retrieval AFTERWARDS. The recorded runtime order was ``model.call_completed`` then
``web_retrieval_started``. Three real, dated rows were fetched, paid for, receipted
with ``lifecycle=succeeded``, and never seen by the model that had already written
four invented headlines. The Activity rail said "Keyless search (google_news_rss) -
3 sources", truthfully, and that is precisely what made the fabrication read sourced.

A receipt proves a RETRIEVAL HAPPENED. It has never proved, and must never be read as
proving, that anything retrieved reached the answer. Those are two facts and they need
two records, which is why the binding record here is separate from the receipt in
`core.retrieval_observability` and deliberately carries no grounding verdict.

WHAT THIS MODULE DOES AND DOES NOT CLAIM
----------------------------------------
`proves="prompt_entry"` is the whole claim: these evidence-set and note ids were in the
material handed to the model call that produced the publishable bytes. Whether the model
then USED them is a different question, owned by `core.evidence_binding`, whose floor
this module does not touch and does not second-guess. Ordering is upstream of that
question: an answer written before the evidence existed cannot be grounded in it, no
matter how the binding predicate scores the text afterwards.

IDS ARE TURN-SCOPED ON PURPOSE
------------------------------
The evidence-set id is derived from the turn's identity together with the digests of the
notes. The same rows fetched on a different turn therefore mint a different id, so a
retry or a resume cannot present an earlier turn's evidence set as this turn's --
`binding_admitted_for_turn` recomputes the scope and refuses a foreign record rather
than trusting the id written on it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.runtime_task_events import emit_runtime_event

#: What a binding record asserts. Never "grounded" -- see the module docstring.
PROVES_PROMPT_ENTRY = "prompt_entry"

#: Terminal truth for the retrieval that a current-information turn depends on.
OUTCOME_BOUND = "bound"
OUTCOME_PARTIAL = "partial_no_sources"
OUTCOME_FAILED = "retrieval_failed"
OUTCOME_REFUSED = "retrieval_refused"

#: Receipt statuses, mirrored from `core.retrieval_provenance` by value rather than by
#: import so this module stays swappable independently of that one.
_RECEIPT_FAILED = "failed"
_RECEIPT_REFUSED = "refused"
_RECEIPT_UNAVAILABLE = "unavailable"

#: Content fields that make a row THIS row. `node_id` and `demand_id` ride as IDENTITY: a
#: lane that serves two demands with byte-identical rows still observed them as two pieces
#: of work, and one shared id would collapse the per-demand account into "a row exists".
_NOTE_FIELDS = (
    "summary",
    "result_title",
    "result_url",
    "origin_domain",
    "search_provider",
    "node_id",
    "demand_id",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def turn_scope(source_context: dict[str, Any] | None, *, session_id: str = "", task_id: str = "") -> str:
    """The identity an evidence set is minted against.

    Every component is one the runtime already stamps per turn. When a caller has none of
    them the scope is empty, and an empty scope is treated as UNSCOPED: ids still mint, but
    `binding_admitted_for_turn` will not admit a record across turns on an empty match,
    because "both were unidentified" is not evidence that they are the same turn.
    """
    context = source_context if isinstance(source_context, dict) else {}
    parts = [
        str(context.get("cancel_turn_id") or context.get("turn_id") or "").strip(),
        str(context.get("request_id") or "").strip(),
        str(session_id or context.get("session_id") or "").strip(),
        str(task_id or "").strip(),
    ]
    return "|".join(parts).strip("|")


def _note_digest(note: dict[str, Any]) -> str:
    payload = json.dumps(
        {field_name: " ".join(str(note.get(field_name) or "").split()).strip() for field_name in _NOTE_FIELDS},
        sort_keys=True,
    )
    return _digest(payload)


@dataclass(frozen=True)
class EvidenceSet:
    """One retrieval's rows, identified, so a later record can name exactly what it carried."""

    evidence_set_id: str
    note_ids: tuple[str, ...]
    scope_digest: str
    query: str = ""
    provider_id: str = ""
    source_domains: tuple[str, ...] = ()
    notes: tuple[dict[str, Any], ...] = field(default=(), repr=False)
    #: The DEMAND this evidence answers, when the minting lane knows it. A mixed turn
    #: serves several demands; naming which one a retrieval serves is what lets a reader
    #: ask "was the weather question answered by weather evidence?" instead of only "did
    #: this turn retrieve anything?". Identity only -- never a verdict about support.
    demand_id: str = ""
    demand_text: str = ""

    @property
    def source_count(self) -> int:
        return len(self.note_ids)

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_set_id": self.evidence_set_id,
            "note_ids": list(self.note_ids),
            "scope_digest": self.scope_digest,
            "query": self.query,
            "provider_id": self.provider_id,
            "source_domains": list(self.source_domains),
            "source_count": self.source_count,
            "demand_id": self.demand_id,
            "demand_text": self.demand_text,
        }


def mint_evidence_set(
    notes: list[dict[str, Any]] | None,
    *,
    scope: str,
    query: str = "",
    provider_id: str = "",
    demand_id: str = "",
    demand_text: str = "",
) -> EvidenceSet:
    """Stable ids for one retrieval's rows, bound to the turn that paid for them.

    Stable means: the same rows, on the same turn, mint the same ids on every recomputation
    (a resume that re-reads the same context does not invent a second identity for one set).
    Turn-bound means: the same rows on a DIFFERENT turn mint different ids.

    `demand_id`/`demand_text` ride as identity when the lane knows which demand the rows
    answer; they take no part in the digests, so the same rows mint the same ids whether or
    not the demand was named -- a lane that cannot name a demand is not a different turn.
    """
    clean = [dict(note) for note in list(notes or []) if isinstance(note, dict)]
    scope_digest = _digest(str(scope or ""))
    note_digests = [_note_digest(note) for note in clean]
    note_ids = tuple(f"evnote-{_digest(scope_digest + digest)[:20]}" for digest in note_digests)
    # Order-invariant on purpose. The same observations can arrive in different orders --
    # a conductor's parallel observation nodes complete in whatever order the pool
    # finishes them -- and one plan's rows minting TWO set ids (one while running, one
    # after) would leave every answering call that carried the earlier id referencing a
    # set the record no longer holds. A set's identity is its membership, not its order.
    set_digest = _digest(scope_digest + "".join(sorted(note_digests)))
    domains: list[str] = []
    for note in clean:
        domain = str(note.get("origin_domain") or "").strip()
        if domain and domain not in domains:
            domains.append(domain)
    resolved_provider = str(provider_id or "").strip()
    if not resolved_provider:
        for note in clean:
            candidate = str(note.get("search_provider") or "").strip().lower()
            if candidate and candidate not in {"none", "disabled"}:
                resolved_provider = candidate
                break
    return EvidenceSet(
        evidence_set_id=f"evset-{set_digest[:24]}",
        note_ids=note_ids,
        scope_digest=scope_digest,
        query=" ".join(str(query or "").split()).strip(),
        provider_id=resolved_provider,
        source_domains=tuple(domains[:8]),
        notes=tuple(clean),
        demand_id=" ".join(str(demand_id or "").split()).strip(),
        demand_text=" ".join(str(demand_text or "").split()).strip()[:200],
    )


def binding_record(
    evidence_set: EvidenceSet,
    *,
    outcome: str = OUTCOME_BOUND,
    model_call_stage: str = "grounded_synthesis",
) -> dict[str, Any]:
    """The envelope stamp: which ids entered the answering call's prompt, and nothing more."""
    record: dict[str, Any] = {
        "schema": "vool.evidence_synthesis_binding.v1",
        "evidence_set_id": evidence_set.evidence_set_id,
        "note_ids": list(evidence_set.note_ids),
        "scope_digest": evidence_set.scope_digest,
        "source_count": evidence_set.source_count,
        "provider_id": evidence_set.provider_id,
        "source_domains": list(evidence_set.source_domains),
        "outcome": str(outcome or OUTCOME_BOUND),
        "model_call_stage": str(model_call_stage or "grounded_synthesis"),
        # The claim, spelled out, so no reader has to infer it and none can inflate it.
        "proves": PROVES_PROMPT_ENTRY,
        "grounded": None,
        "bound_at": _utc_now(),
    }
    if evidence_set.demand_id or evidence_set.demand_text:
        # Present only when the minting lane could name the demand. Absent is a fact about
        # the lane (a single-demand turn need not name its only demand), not a default.
        record["demand_id"] = evidence_set.demand_id
        record["demand_text"] = evidence_set.demand_text
    return record


def binding_admitted_for_turn(record: dict[str, Any] | None, *, scope: str) -> bool:
    """Is this binding record this turn's, rather than one carried in from another?

    Recomputes the scope digest instead of trusting the id. An unscoped record (no turn
    identity was available when it was minted) is never admitted across a call, because
    an empty scope matches every other empty scope and would make reuse invisible.
    """
    if not isinstance(record, dict):
        return False
    expected = _digest(str(scope or ""))
    if not str(scope or "").strip():
        return False
    return str(record.get("scope_digest") or "") == expected


def outcome_for_receipt(receipt: dict[str, Any] | None, *, notes: list[dict[str, Any]] | None) -> str:
    """Terminal truth for the retrieval, read off its own receipt rather than guessed.

    `_collect_live_web_notes` returns `[]` for a refusal, a transport failure and an empty
    result set alike, so the notes alone cannot tell a broken provider from a policy denial
    from a provider that simply had nothing. The receipt distinguishes them, and the three
    deserve three different answers to the user.
    """
    clean = [note for note in list(notes or []) if isinstance(note, dict)]
    status = str((receipt or {}).get("status") or "").strip().lower() if isinstance(receipt, dict) else ""
    if status == _RECEIPT_REFUSED:
        return OUTCOME_REFUSED
    if status == _RECEIPT_FAILED:
        return OUTCOME_FAILED
    if clean:
        return OUTCOME_BOUND
    if status == _RECEIPT_UNAVAILABLE:
        return OUTCOME_PARTIAL
    return OUTCOME_PARTIAL


def last_retrieval_receipt(source_context: dict[str, Any] | None) -> dict[str, Any]:
    """The terminal receipt this turn's retrieval just wrote, if it wrote one."""
    receipts = (source_context or {}).get("web_retrieval_receipts") if isinstance(source_context, dict) else None
    if not isinstance(receipts, list) or not receipts:
        return {}
    last = receipts[-1]
    return dict(last) if isinstance(last, dict) else {}


def emit_evidence_bound(
    source_context: dict[str, Any] | None,
    record: dict[str, Any],
    *,
    task_id: str = "",
) -> dict[str, Any]:
    """The typed event that sits between retrieval and the model call, and orders them.

    Emitted only for a set that actually entered the prompt. A turn whose retrieval failed
    or returned nothing emits no binding event at all -- silence is the honest record, and
    an event saying "bound zero sources" would be one more green-looking row behind which a
    fabrication could hide, which is the exact shape of the defect this module closes.
    """
    payload = dict(record)
    if task_id:
        payload.setdefault("task_id", str(task_id)[:160])
    emit_runtime_event(
        source_context,
        event_type="evidence_bound_to_synthesis",
        message=(
            f"Bound {int(payload.get('source_count') or 0)} retrieved source(s) into the answering model call."
        ),
        details=payload,
    )
    return payload


__all__ = [
    "OUTCOME_BOUND",
    "OUTCOME_FAILED",
    "OUTCOME_PARTIAL",
    "OUTCOME_REFUSED",
    "PROVES_PROMPT_ENTRY",
    "EvidenceSet",
    "binding_admitted_for_turn",
    "binding_record",
    "emit_evidence_bound",
    "last_retrieval_receipt",
    "mint_evidence_set",
    "outcome_for_receipt",
    "turn_scope",
]
