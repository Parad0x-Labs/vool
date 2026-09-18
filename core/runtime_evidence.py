"""What the runtime authoritatively knows it did on a turn — and, just as load-bearing, what it does not.

The failure this exists for, from production. Activity recorded an attempted fetch of
`https://github.com/VOOL-ai/openclaw-skills`. The assistant then told the user:

    "I never tried GitHub."

The durable execution trace said one thing and the prose said the opposite, and nothing in the
runtime preferred the trace. A second observed answer asserted a *provider-attested* model when the
runtime carried no provider attestation of any kind — a runtime fact stated with no runtime source
behind it.

Both are the same defect from two directions, and the existing honesty layer could not see either:
it checks that some POSITIVE action claims have a receipt behind them, and has nothing at all to say
about a NEGATIVE claim ("I never…") or about an unsourced claim of runtime provenance. A model that
denies its own trace passes every guard in the file.

**What this module is.** A read-only collector that answers three questions about one turn, per
claim class, from the stores the runtime already keeps:

* what was ATTEMPTED (attempted, not just succeeded — see `ToolAttempt`);
* whether the account of that class is COMPLETE enough to prove a negative;
* what the model provenance actually is, with attestation kept separate from selection.

**What this module is not.** It is not a fact-checker. It knows nothing about market prices, code
correctness, or whether an opinion is any good. It answers only about runtime-observable actions,
and where it cannot answer it says so rather than guessing — `unknown` is a first-class result here,
not a failure mode.

Nothing here writes. A verification layer that can break a turn is a worse bug than the one it
catches, so every collector swallows its own faults and degrades to "cannot establish".
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import urlsplit

from core.agent_runtime.file_target_contract import REAL_FILE_EXTENSIONS

# Claim classes this layer is authoritative for. Deliberately closed: a class not listed here is
# not a runtime-observable fact and must be left alone.
CLASS_TOOL_ACTION = "tool_action"  # A: did/did not call, read, run, modify
CLASS_TARGET = "target"  # B: which project, which files, which tool targets
CLASS_MODEL_PROVENANCE = "model_provenance"  # C: requested / selected / provider-attested
CLASS_EXECUTION_STATUS = "execution_status"  # D: succeeded, failed, rejected

# Where the runtime can enumerate EVERY action of a kind for a turn, and so prove a negative.
SCOPE_REMOTE = "remote"

# The Relay contract. Relay owns provider parsing; this layer owns only the shape it arrives in and
# the rule that an unattested field stays unattested. Supplied per turn as:
#
#     source_context["model_provenance"] = {
#         "requested_model":        "what the operator asked for",
#         "runtime_selected_model": "what the router actually invoked",
#         "provider_attested_model":"the model id the PROVIDER's own response carried",
#         "attestation_source":     "relay.provider_response",   # who vouches for the field above
#     }
#
# Until Relay lands, `provider_attested_model` is absent and every provider-attestation claim is
# unsupported. That is the correct state, not a gap to paper over with the selected model.
PROVENANCE_CONTEXT_KEY = "model_provenance"

# Activity event types that mean a tool was attempted. `tool_selected` is included on purpose: an
# attempt that was selected and then died before producing a result is still an attempt, and
# excluding it is exactly how "the fetch failed, so it never happened" gets written into an answer.
_ATTEMPT_EVENT_TYPES = frozenset(
    {
        "tool_selected",
        "tool_executed",
        "tool_failed",
        "workspace_mutation_completed",
        "workspace_mutation_failed",
        "workspace_mutation_rollback_completed",
        "workspace_mutation_rollback_conflict",
    }
)
_FAILED_EVENT_TYPES = frozenset(
    {"tool_failed", "workspace_mutation_failed", "workspace_mutation_rollback_conflict"}
)
# Event details that can carry what the tool acted on, in preference order.
_EVENT_TARGET_KEYS = ("canonical_target", "resolved_target", "url", "target", "path", "query")
_EVENT_TOOL_KEYS = ("tool_name", "tool_intent", "tool", "intent")
# Arguments that can carry what a tool acted on when the tool declared no ToolClaim. `web.fetch`
# declares none, so its record's `resolved_target` is empty and the URL lives only here — which is
# precisely the tool at the centre of the production failure.
_ARGUMENT_TARGET_KEYS = ("url", "path", "file_path", "target", "query", "href", "link")
# Last resort: the URL inside an Activity event's own message text.
#
# Measured against real rows from a live daemon turn, 2026-08-07. The `tool_failed` row for the
# GitHub fetch carries NO url/target detail key at all — every one of them is absent — and the only
# place the address survives is the human-readable message:
#
#     "Web fetch for `https://github.com/VOOL-ai/openclaw-skills` failed with HTTP 404."
#
# Without this the durable half of the evidence degrades to "some web.fetch failed", which cannot
# answer a denial that names GitHub specifically — the exact sentence this module exists to catch,
# and the half that is left after a restart kills the in-memory records.
_MESSAGE_URL_RE = re.compile(r"https?://[^\s`'\"<>)\]}]+", re.IGNORECASE)
# How many Activity rows to read, newest first. The store caps any single read at 200; asking for
# that cap keeps the window as wide as the ledger will serve. A session longer than the window
# loses its OLDEST rows, which is the right direction to lose them: a denial is almost always about
# recent work, and the alternative — dropping the newest — is what made a fetch invisible.
_ACTIVITY_WINDOW = 200


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


# Suffixes that make a dotted token a FILE rather than a host. Shared with the claim binder so the
# two cannot drift: `README.md` parsed as the domain `readme.md` is not a cosmetic error — it makes
# a local file read register as a remote call, which turns a truthful "I did not use the web" into
# a contradiction and rewrites a correct answer. Measured exactly that way on the first run.
#
# INTEGRATION (Harbourmaster, reliability wave): this was a second, independently maintained copy of
# the vocabulary Scalpel extracted into `core.agent_runtime.file_target_contract`. Both lists were
# verified set-identical at 65 entries before they were joined, so this is a de-duplication and not
# a behaviour change. It is now an alias, not a copy: the whole point of the shared contract is that
# "a dotted token is not a path" was fixed in one extractor on 2026-08-01 and recurred in another
# six days later, because an allowlist living inside one consumer is a fix that does not travel.
# Two copies cannot drift apart if there is only one. The name `FILE_SUFFIXES` is kept because it is
# exported here and imported by `core.agent_runtime.evidence_claim_binder`; only the right-hand side
# moved.
FILE_SUFFIXES = REAL_FILE_EXTENSIONS


def host_of(value: str) -> str:
    """The registrable-ish host label of a URL, or "" when the value is not one.

    `https://github.com/VOOL-ai/openclaw-skills` -> `github`. Reduced to the label so a claim
    written as "GitHub" matches evidence written as `github.com`, which is how a human names a
    source and how a trace records one.

    Without an explicit scheme the bar is higher, because `notes.md` and `github.com` are the same
    shape: a bare token whose final label is a file suffix is a file. Some of those suffixes are
    also real TLDs (`.sh`, `.io`), so a scheme-less `example.sh` is rejected too — a miss costs one
    unchecked sentence, while a false host turns local work into a phantom network call.
    """

    raw = _text(value)
    if not raw:
        return ""
    if "://" not in raw:
        # A bare `github.com/x` is a URL as a human writes one; a filesystem path is not.
        if raw.startswith(("/", "~", ".")) or "." not in raw.split("/", 1)[0]:
            return ""
        suffix = raw.split("/", 1)[0].rsplit(".", 1)[-1].lower()
        if suffix in FILE_SUFFIXES or not suffix.isalpha() or len(suffix) < 2:
            return ""
        raw = "https://" + raw
    try:
        netloc = urlsplit(raw).netloc.lower()
    except ValueError:
        return ""
    hostname = netloc.rsplit("@", 1)[-1].split(":", 1)[0]
    if not hostname or "." not in hostname:
        return ""
    labels = [part for part in hostname.split(".") if part and part != "www"]
    if len(labels) < 2:
        return ""
    # Second-to-last label, so `api.github.com` and `github.com` are the same source. Two-part
    # public suffixes (`co.uk`) would give "co"; accepted, because the comparison is only ever
    # label-to-label between a claim and the evidence, never a security decision.
    return labels[-2]


@dataclass(frozen=True)
class ToolAttempt:
    """One thing the runtime tried, whether or not it worked.

    `ok=False` is still an attempt. That is INVARIANT 5 and it is the half the old layer threw
    away: `execution_records.verify_claim` filters on `ok`, so a GitHub fetch that 404'd was
    invisible to every check and a denial of it could not be contradicted. A failure erases the
    RESULT, never the attempt.
    """

    intent: str = ""
    target: str = ""
    ok: bool = True
    status: str = ""
    turn_id: str = ""
    generation: int = 0
    sequence: int = 0
    source: str = ""
    # Whether the runtime saw how this attempt ENDED. A `tool_selected` row records a dispatch and
    # nothing more; reporting it as a success is a claim the trace does not make, and on real rows
    # it read as "tried web.fetch — succeeded" beside the very failure it was dispatching.
    outcome_known: bool = True

    @property
    def host(self) -> str:
        return host_of(self.target)

    @property
    def is_remote(self) -> bool:
        return bool(self.host) or self.intent.startswith(("web.", "browser.", "http."))

    def describe(self) -> str:
        where = self.host or self.target or self.intent or "an external source"
        if not self.outcome_known:
            return f"{where} — attempted, outcome not recorded"
        outcome = "failed" if not self.ok else "succeeded"
        detail = f" ({self.status})" if self.status and self.status not in {"ok", "error"} else ""
        return f"{where} — {outcome}{detail}"


@dataclass(frozen=True)
class ModelProvenance:
    """Which model was asked for, which one ran, and who says so.

    The three are kept apart because collapsing them is the bug. `runtime_selected_model` is what
    the router chose to invoke — the runtime's own claim about itself, which is worth exactly as
    much as the router's correctness. `provider_attested_model` is the provider's statement about
    what it served, which is the only thing that can back the sentence "the provider says it served
    X". A blank attestation is a fact, and copying the selected model into it would manufacture a
    second, independent-looking source out of the first one.
    """

    requested_model: str = ""
    runtime_selected_model: str = ""
    provider_attested_model: str = ""
    attestation_source: str = ""

    @property
    def has_provider_attestation(self) -> bool:
        # Both halves required. A model id with nobody vouching for it is not an attestation, and
        # demanding the source is what stops the selected model being laundered into this field.
        return bool(_text(self.provider_attested_model) and _text(self.attestation_source))


@dataclass(frozen=True)
class TurnEvidence:
    """The authoritative account of one turn, and the boundaries of that authority."""

    session_id: str = ""
    turn_id: str = ""
    attempts: tuple[ToolAttempt, ...] = ()
    provenance: ModelProvenance = field(default_factory=ModelProvenance)
    # Claim classes whose account is complete enough that absence proves a negative.
    complete_scopes: frozenset[str] = frozenset()
    # Records the runtime holds but cannot attribute to a turn. Their existence is why a
    # turn-scoped negative stops being provable even when the turn's own account looks empty.
    unattributed_records: int = 0
    # Whole-session attempts, for a question explicitly about the conversation rather than the
    # turn. Never consulted for a turn-scoped question — that conflation is INVARIANT 6's failure.
    conversation_attempts: tuple[ToolAttempt, ...] = ()

    def attempts_in_scope(self, *, whole_conversation: bool = False) -> tuple[ToolAttempt, ...]:
        return self.conversation_attempts if whole_conversation else self.attempts

    def remote_attempts(self, *, whole_conversation: bool = False) -> tuple[ToolAttempt, ...]:
        return tuple(a for a in self.attempts_in_scope(whole_conversation=whole_conversation) if a.is_remote)

    def attempts_against_host(self, label: str, *, whole_conversation: bool = False) -> tuple[ToolAttempt, ...]:
        wanted = _text(label).lower()
        if not wanted:
            return ()
        return tuple(
            a
            for a in self.attempts_in_scope(whole_conversation=whole_conversation)
            if a.host == wanted or wanted in a.target.lower()
        )

    def attempts_against_target(self, target: str, *, whole_conversation: bool = False) -> tuple[ToolAttempt, ...]:
        wanted = _text(target).lower().strip("`'\"")
        if not wanted:
            return ()
        tail = [segment for segment in wanted.replace("\\", "/").split("/") if segment]
        out: list[ToolAttempt] = []
        for attempt in self.attempts_in_scope(whole_conversation=whole_conversation):
            actual = [s for s in attempt.target.lower().replace("\\", "/").split("/") if s]
            if not actual or not tail:
                continue
            depth = min(len(tail), len(actual))
            if actual[-depth:] == tail[-depth:]:
                out.append(attempt)
        return tuple(out)

    def can_prove_absence(self, scope: str, *, whole_conversation: bool = False) -> bool:
        """Whether "none of this happened" is a provable statement, not merely an unobserved one.

        INVARIANT 2. Absence of a receipt is not proof of absence of an action; it is equally
        consistent with nobody having written the receipt. A negative is provable only when the
        runtime held a complete account of that class for the window being asked about — and an
        unattributed record means it did not.
        """

        if whole_conversation:
            return False  # nothing in this layer enumerates a whole conversation completely
        return scope in self.complete_scopes and not self.unattributed_records

    def generations(self, attempts: tuple[ToolAttempt, ...]) -> tuple[int, ...]:
        return tuple(sorted({a.generation for a in attempts if a.generation}))

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "attempt_count": len(self.attempts),
            "attempts": [
                {
                    "intent": a.intent,
                    "target": a.target,
                    "host": a.host,
                    "ok": a.ok,
                    "status": a.status,
                    "generation": a.generation,
                    "sequence": a.sequence,
                    "source": a.source,
                }
                for a in self.attempts
            ],
            "complete_scopes": sorted(self.complete_scopes),
            "unattributed_records": self.unattributed_records,
            "provenance": {
                "requested_model": self.provenance.requested_model,
                "runtime_selected_model": self.provenance.runtime_selected_model,
                "provider_attested_model": self.provenance.provider_attested_model,
                "attestation_source": self.provenance.attestation_source,
                "has_provider_attestation": self.provenance.has_provider_attestation,
            },
        }


def _attempt_from_record(entry: Any, *, session_scoped: bool) -> ToolAttempt:
    target = _text(getattr(entry, "resolved_target", ""))
    if not target:
        arguments = dict(getattr(entry, "arguments", None) or {})
        for key in _ARGUMENT_TARGET_KEYS:
            candidate = _text(arguments.get(key))
            if candidate:
                target = candidate
                break
    return ToolAttempt(
        intent=_text(getattr(entry, "intent", "")),
        target=target,
        ok=bool(getattr(entry, "ok", True)),
        status=_text(getattr(entry, "status", "")),
        turn_id=_text(getattr(entry, "turn_id", "")),
        generation=int(getattr(entry, "generation", 0) or 0),
        sequence=int(getattr(entry, "sequence", 0) or 0),
        source="execution_record" if not session_scoped else "execution_record_session",
    )


def _attempt_from_event(event: Mapping[str, Any], *, index: int) -> ToolAttempt | None:
    event_type = _text(event.get("event_type"))
    if event_type not in _ATTEMPT_EVENT_TYPES:
        return None
    intent = ""
    for key in _EVENT_TOOL_KEYS:
        intent = _text(event.get(key))
        if intent:
            break
    target = ""
    for key in _EVENT_TARGET_KEYS:
        target = _text(event.get(key))
        if target:
            break
    if not target:
        found = _MESSAGE_URL_RE.search(_text(event.get("message")) or _text(event.get("summary")))
        target = found.group(0).rstrip(".,;:") if found else ""
    if not intent and not target:
        return None
    ok = event.get("ok")
    return ToolAttempt(
        intent=intent,
        target=target,
        ok=bool(ok) if isinstance(ok, bool) else event_type not in _FAILED_EVENT_TYPES,
        status=_text(event.get("result_state") or event.get("status")),
        outcome_known=event_type != "tool_selected",
        # The client's per-turn id when it sent one, else the runtime's own. The collector resolves
        # its turn id in the same order, so the two always compare like with like.
        turn_id=_text(event.get("client_turn_id")) or _text(event.get("turn_id")),
        generation=int(event.get("execution_generation") or 0),
        # Activity rows are already time-ordered; `seq` is the ledger's own monotonic counter.
        sequence=int(event.get("seq") or index),
        source="activity",
    )


def _bucket_key(attempt: ToolAttempt) -> tuple[str, str, int]:
    return (attempt.intent.lower(), attempt.target.lower(), attempt.generation)


def _merge_two(existing: ToolAttempt, incoming: ToolAttempt) -> ToolAttempt:
    """Two reports of ONE call. Where they disagree about success, FAILURE wins.

    A store that saw the failure saw more than one that recorded only the dispatch, and the
    direction that loses information here is the one that erases an attempt.
    """

    return replace(
        existing,
        ok=existing.ok and incoming.ok,
        status=existing.status or incoming.status,
        turn_id=existing.turn_id or incoming.turn_id,
        outcome_known=existing.outcome_known or incoming.outcome_known,
    )


def _collapse_activity_rows(rows: list[ToolAttempt]) -> list[ToolAttempt]:
    """Fold each dispatch row into the terminal row of the same call.

    Activity writes up to two rows per call — a `tool_selected` that records only that a call went
    out, then a `tool_executed`/`tool_failed` that records how it ended. Collapsed here, in
    chronological order, so a dispatch is matched to the NEXT terminal row of its own intent rather
    than to any terminal row anywhere in the turn.
    """

    pending: list[ToolAttempt] = []
    out: list[ToolAttempt] = []
    for row in rows:
        if not row.outcome_known:
            pending.append(row)
            continue
        for index, dispatch in enumerate(pending):
            same_target = not dispatch.target or not row.target or dispatch.target == row.target
            if dispatch.intent == row.intent and same_target:
                pending.pop(index)
                row = _merge_two(row, dispatch)
                break
        out.append(row)
    # A dispatch with no terminal row is a call whose outcome was never recorded. It is still an
    # attempt, and dropping it would erase exactly the case where a tool died mid-flight.
    out.extend(pending)
    return sorted(out, key=lambda a: a.sequence)


def _dedupe(attempts: list[ToolAttempt]) -> tuple[ToolAttempt, ...]:
    """Collapse the two STORES' reports of one call, without collapsing two distinct calls.

    The previous key was `(intent, target, generation)`, which is not an identity when the
    generation is unknown: two real fetches of the same URL in one turn — the first failing, the
    second succeeding — landed in the same bucket and merged into a single attempt, and "failure
    wins" then reported the pair as one failed call. Chronology the runtime genuinely had was
    thrown away because a field it did not have was zero for both.

    So identity is ORDINAL within a store, not the generation. `execution_records` writes exactly
    one record per executed call, so its n-th record for a target IS the n-th call; Activity's rows
    are collapsed per call first. Pairing the n-th of each store keeps a real second attempt
    separate while still merging the two reports of the first — and invents no generation number
    for either.
    """

    activity = _collapse_activity_rows(
        sorted((a for a in attempts if a.source == "activity"), key=lambda a: a.sequence)
    )
    records = sorted((a for a in attempts if a.source != "activity"), key=lambda a: a.sequence)

    merged: dict[tuple[str, str, int, int], ToolAttempt] = {}
    for group in (records, activity):
        seen: dict[tuple[str, str, int], int] = {}
        for attempt in group:
            bucket = _bucket_key(attempt)
            ordinal = seen.get(bucket, 0)
            seen[bucket] = ordinal + 1
            key = (*bucket, ordinal)
            existing = merged.get(key)
            merged[key] = attempt if existing is None else _merge_two(existing, attempt)
    return _absorb_targetless(tuple(sorted(merged.values(), key=lambda a: a.sequence)))


def _absorb_targetless(attempts: tuple[ToolAttempt, ...]) -> tuple[ToolAttempt, ...]:
    """Fold a targetless attempt into a targeted one only when a DIFFERENT store reported it.

    One call can be targetless in one store and targeted in the other — a tool whose target sits in
    an argument key nobody declared, whose Activity message nonetheless quotes the URL. Keyed on
    target those are two attempts, and the repair then reports the same call twice: once with the
    address and once as a contentless intent.

    The cross-store condition is what keeps this from erasing evidence. Activity's own dispatch and
    terminal rows are already folded by `_collapse_activity_rows`, so a targetless attempt that
    survives with no counterpart in another store is a call whose outcome was never recorded — a
    real attempt, and precisely the one an over-eager absorb would delete.
    """

    targeted = [a for a in attempts if a.target]
    if not targeted:
        return attempts
    kept: list[ToolAttempt] = []
    for attempt in attempts:
        if attempt.target:
            kept.append(attempt)
            continue
        twin = next(
            (
                a
                for a in targeted
                if a.intent == attempt.intent
                and a.generation == attempt.generation
                and (a.source == "activity") != (attempt.source == "activity")
            ),
            None,
        )
        if twin is None:
            kept.append(attempt)
    return tuple(kept)


def _provenance(
    source_context: Mapping[str, Any],
    activity: list[Mapping[str, Any]],
    override: Mapping[str, Any] | None,
) -> ModelProvenance:
    supplied = dict(override or source_context.get(PROVENANCE_CONTEXT_KEY) or {})
    requested = _text(supplied.get("requested_model") or source_context.get("requested_model"))
    selected = _text(supplied.get("runtime_selected_model"))
    if not selected:
        # The router's own record of what it invoked. `actual_adapter_model_id` is read off the
        # manifest that actually answered, kept separate from `planned_model_id` by
        # `_lane_proof_payload` precisely so a substitution stays visible.
        for event in reversed(activity):
            if _text(event.get("event_type")) != "model_lane_proof":
                continue
            selected = _text(event.get("actual_adapter_model_id"))
            if selected:
                break
    return ModelProvenance(
        requested_model=requested,
        runtime_selected_model=selected,
        provider_attested_model=_text(supplied.get("provider_attested_model")),
        attestation_source=_text(supplied.get("attestation_source")),
    )


def remote_account_is_complete() -> bool:
    """Whether the turn's remote calls can be enumerated exhaustively, read live.

    Only meaningful INSIDE `remote_fetch_policy_scope`. Callers that run after the scope closes
    must reuse a value captured while it was open rather than calling this and getting `False` —
    see the note in `action_honesty_validator._bind_runtime_claims_to_evidence`.
    """

    return _remote_account_is_complete(None)


def _remote_account_is_complete(explicit: bool | None) -> bool:
    """Whether the turn's remote calls can be enumerated exhaustively.

    True only when something was actually counting AND the count is zero. Every remote HTTP path
    calls `note_remote_fetch_attempt` before its own policy check, so inside the scope a zero is a
    complete account of the turn and "no web call happened" is provable from it.

    A non-zero count deliberately does NOT make the account complete. The counter also ticks for
    cloud model transport, so a positive number cannot be reconciled against the enumerated tool
    attempts — and a scope that claims completeness it cannot deliver would license exactly the
    over-confident negative this module exists to stop.
    """

    if explicit is not None:
        return bool(explicit)
    try:
        from core.remote_fetch_policy import remote_fetch_attempt_count, remote_fetch_scope_active

        return bool(remote_fetch_scope_active()) and remote_fetch_attempt_count() == 0
    except Exception:
        return False


def collect_turn_evidence(
    *,
    session_id: str | None,
    source_context: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
    remote_account_complete: bool | None = None,
) -> TurnEvidence:
    """Assemble the turn's authoritative account. Reads only; never raises.

    Two stores are joined because each sees something the other misses. The in-memory execution
    records carry the tool's own resolved target and its arguments — the URL of a `web.fetch` lives
    nowhere else, since that tool declares no ToolClaim. The durable Activity ledger survives the
    process, is already stamped with the turn id the UI shows the user, and covers lanes that
    bypass the record writer. Where they overlap, `_dedupe` collapses them to one attempt.
    """

    context = dict(source_context or {})
    clean_session = _text(session_id) or _text(context.get("runtime_session_id")) or _text(context.get("session_id"))
    turn_id = _text(context.get("cancel_turn_id")) or _text(context.get("turn_id"))

    session_records: tuple[Any, ...] = ()
    unattributed = 0
    try:
        from core import execution_records

        if clean_session:
            session_records = execution_records.records_for(clean_session)
            unattributed = execution_records.unattributed_count(clean_session)
    except Exception:
        session_records, unattributed = (), 0

    activity: list[Mapping[str, Any]] = []
    try:
        # The NEWEST bounded window, not the oldest. `list_runtime_session_events(after_seq=0)`
        # orders by `seq ASC`, so on any session past 200 events it returned the session's opening
        # rows and nothing since — a GitHub fetch at row 205 of 400 was invisible, and the denial
        # it should have contradicted sailed through on exactly the long sessions where a user is
        # most likely to ask "did you try that?". `list_recent_runtime_session_events` is the
        # repository's existing newest-window helper and returns the window in chronological order,
        # so nothing downstream has to re-sort.
        from core.runtime_continuity import list_recent_runtime_session_events

        if clean_session:
            activity = list(list_recent_runtime_session_events(clean_session, limit=_ACTIVITY_WINDOW))
    except Exception:
        activity = []

    conversation: list[ToolAttempt] = [_attempt_from_record(r, session_scoped=True) for r in session_records]
    for index, event in enumerate(activity):
        attempt = _attempt_from_event(event, index=index)
        if attempt is not None:
            conversation.append(attempt)

    # Turn scoping is exact-match on the turn id, never a fallback to "everything". With no turn id
    # the turn window is EMPTY and every record is unattributed — the runtime cannot say what this
    # turn did, which is a true statement about a turn it never identified.
    if turn_id:
        this_turn = [a for a in conversation if a.turn_id == turn_id]
        unattributed += sum(1 for a in conversation if not a.turn_id and a.source == "activity")
    else:
        this_turn = []
        unattributed = max(unattributed, len(conversation))

    scopes: set[str] = set()
    if turn_id and _remote_account_is_complete(remote_account_complete):
        scopes.add(SCOPE_REMOTE)

    return TurnEvidence(
        session_id=clean_session,
        turn_id=turn_id,
        attempts=_dedupe(this_turn),
        provenance=_provenance(context, activity, provenance),
        complete_scopes=frozenset(scopes),
        unattributed_records=unattributed,
        conversation_attempts=_dedupe(conversation),
    )


__all__ = [
    "CLASS_EXECUTION_STATUS",
    "CLASS_MODEL_PROVENANCE",
    "CLASS_TARGET",
    "CLASS_TOOL_ACTION",
    "FILE_SUFFIXES",
    "PROVENANCE_CONTEXT_KEY",
    "SCOPE_REMOTE",
    "ModelProvenance",
    "ToolAttempt",
    "TurnEvidence",
    "collect_turn_evidence",
    "host_of",
    "remote_account_is_complete",
]
