"""Step 9: runtime-owned resolution for short follow-ups against a persisted `runtime_attempts`
record -- "why did that fail?", "retry", "which assets did I ask for?".

Deterministic by construction, the same way `core.agent_runtime.proceed_intent_support`'s resume-
phrase matching is: classification is a fixed set of short, curated phrases plus a small number of
substring checks BOUNDED to short messages, never a model call and never free substring matching
across an arbitrary-length message. Found live (Step 7B's own production trace): "Retry the exact
failed request now." was handed to the adaptive-research subsystem, which used the LITERAL PHRASE
as a web-search query and returned Wikipedia/blog content about "how to retry a failed HTTP request
in Python" as grounding -- the runtime had no notion that "retry" could mean anything other than an
ordinary informational question. This module exists so that never happens again: a follow-up that
plausibly names a runtime action is checked against a persisted attempt BEFORE it can reach any
model or web-search machinery, and only falls through to ordinary conversation when no attempt
resolves.

Resolution precedence (Step 9's own list):
  1. an attempt id literally present in the text (rare, but unambiguous when it happens);
  2. an explicitly referenced visible turn (not implemented beyond (1) -- turn ids are never
     shown to a user, so there is no phrasing for a person to reference one by);
  3. the latest unresolved-or-partially-failed attempt in the session;
  4-7. ordinary conversation (unchanged, everything below this module).

A short-message length guard (`_MAX_FOLLOWUP_WORDS`) keeps a long, unrelated message that happens
to contain a word like "retry" from being misclassified -- classification only applies to genuinely
short follow-ups, matching every example in the checkpoint's own phrasing list.

Repair 5 (Mnemosyne review, 2026-08-06): the length guard alone was not enough. Free substring
matching ("does this trigger phrase appear ANYWHERE in the text") let a genuinely unrelated but
short message hijack a follow-up: "what went wrong with the Challenger shuttle" (contains "what
went wrong"), "which assets should I buy this year?" (contains "which assets"), "which cities have
the best weather in Europe?" (contains "which cities"), and "please summarise the original message
from the customer" (contains "original message", which routed it into REPEAT_ORIGINAL_REQUEST and
from there into an actual retry of an unrelated persisted attempt) were all misclassified this way.
Every trigger phrase below is now a fully anchored regex -- the WHOLE normalized message must be an
exact-or-near-exact operational utterance (optional leading politeness, optional trailing filler),
never a phrase appearing partway through an unrelated sentence. A message that starts the same way
but continues onto a different subject ("which assets should I buy...") never matches, because the
pattern requires the reflexive continuation ("...did I ask for") through to the end of the message.
"""

from __future__ import annotations

import re
from typing import Any

EXPLAIN_ATTEMPT_FAILURE = "EXPLAIN_ATTEMPT_FAILURE"
RETRY_ATTEMPT = "RETRY_ATTEMPT"
CONTINUE_ATTEMPT = "CONTINUE_ATTEMPT"
REPEAT_ORIGINAL_REQUEST = "REPEAT_ORIGINAL_REQUEST"
CORRECT_PREVIOUS_RESPONSE = "CORRECT_PREVIOUS_RESPONSE"
LIST_ORIGINAL_ENTITIES = "LIST_ORIGINAL_ENTITIES"

_MAX_FOLLOWUP_WORDS = 16

_ATTEMPT_ID_RE = re.compile(r"\battempt-[0-9a-f]{8,32}\b", re.IGNORECASE)

# Repair 5: every family below is a single fully anchored regex (^...$) against the normalized
# message -- an optional leading politeness word, the operational phrase itself (with its own small
# set of near-exact variations), and an optional trailing filler word. Nothing else may follow: a
# message that starts the same way but continues onto an unrelated clause or subject never matches.
_LIST_ENTITIES_RE = re.compile(
    r"^(?:please\s+)?"
    r"(?:"
    r"which assets and cities did i(?: originally)? ask for|"
    r"which assets did i(?: originally)? ask for|"
    r"which cities did i(?: originally)? ask for|"
    r"what did i originally ask(?: for)?|"
    r"what did i ask for originally"
    r")"
    r"(?:\s+please|\s+again)?$"
)

# AUD-20260829-003 (C10): the determiner in the last alternative was `the`, and the operator's own
# phrasing -- and the audit's own acceptance wording -- is "retry THAT exact failed request", which
# matched nothing and dropped the turn into ordinary conversation. This is a repair to a phrasing
# this curated family was already written to cover (it came from Step 7B's own production trace),
# not a new trigger: `the`/`that`/`this` are the same utterance. The C12 contract in
# `core.refused_slot_register` does NOT depend on this line -- a retry phrasing that still misses
# is answered from the record, never invented -- so the guarantee is not carried by a regex.
_RETRY_RE = re.compile(
    r"^(?:please\s+)?"
    r"(?:retry|try again|retry that|retry it|run that again|run it again|do it again|"
    r"retry (?:the|that|this) exact (?:failed )?request)"
    r"(?:\s+please|\s+now|\s+exactly)?$"
)

_EXPLAIN_FAILURE_RE = re.compile(
    r"^(?:please\s+)?"
    r"(?:why|why not|why did (?:that|it) fail|why did that request fail|"
    r"what went wrong|what failed|explain (?:that|the) failure)$"
)

_ORIGINAL_REQUEST_RE = re.compile(
    r"^(?:please\s+)?"
    r"(?:check the original message(?: and answer it)?|"
    r"what did i ask(?: for)?|"
    r"answer (?:my|the) original question)$"
)

_CORRECTION_RE = re.compile(
    r"^(?:please\s+)?"
    r"(?:that is not what i asked|that'?s not what i asked|you answered the wrong thing|"
    r"fix the previous answer|that'?s the wrong answer|wrong answer)$"
)

_CONTINUE_PHRASES = frozenset({
    "continue", "keep going", "go on", "pick up where you left off", "continue please",
})


def _normalize(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split()).strip(" \t\n\r?!.,")


def classify_followup_intent(text: str) -> str | None:
    """One of the six intents above, or None when the text does not plausibly name a follow-up
    action at all. None is the common case and the correct answer for any ordinary message.

    Repair 5: every family is now a fully anchored regex against the WHOLE normalized message --
    the length guard alone did not stop a short but genuinely unrelated message ("what went wrong
    with the Challenger shuttle", six words) from matching a bare substring."""
    normalized = _normalize(text)
    if not normalized:
        return None
    if len(normalized.split()) > _MAX_FOLLOWUP_WORDS:
        return None

    # LIST_ORIGINAL_ENTITIES first: it is a more specific shape of "the original request" and
    # would otherwise be shadowed by the broader REPEAT_ORIGINAL_REQUEST family below.
    if _LIST_ENTITIES_RE.match(normalized):
        return LIST_ORIGINAL_ENTITIES
    if _RETRY_RE.match(normalized):
        return RETRY_ATTEMPT
    if _EXPLAIN_FAILURE_RE.match(normalized):
        return EXPLAIN_ATTEMPT_FAILURE
    if _ORIGINAL_REQUEST_RE.match(normalized):
        return REPEAT_ORIGINAL_REQUEST
    if _CORRECTION_RE.match(normalized):
        return CORRECT_PREVIOUS_RESPONSE
    if normalized in _CONTINUE_PHRASES:
        return CONTINUE_ATTEMPT

    return None


def _is_the_followup_itself(attempt: Any, text: str) -> bool:
    """Whether `attempt` is the record of THIS follow-up phrase rather than of a prior request."""
    recorded = _normalize(
        str(
            (attempt or {}).get("original_request_snapshot")
            or (attempt or {}).get("request_text")
            or ""
        )
    )
    return bool(recorded) and recorded == _normalize(text)


def find_referenced_attempt_id(text: str) -> str:
    """Resolution precedence tier 1: an attempt id literally present in the text."""
    match = _ATTEMPT_ID_RE.search(str(text or ""))
    return match.group(0) if match else ""


# Intents that only make sense against something that DIDN'T fully succeed -- "why did that
# fail?"/"retry" must never hijack a clean success (a genuinely unrelated question after a
# successful request must not get re-explained as if it failed). Every other intent
# (REPEAT_ORIGINAL_REQUEST, LIST_ORIGINAL_ENTITIES, CORRECT_PREVIOUS_RESPONSE, CONTINUE_ATTEMPT)
# can legitimately apply to a SUCCEEDED attempt too -- "which assets did I ask for?" or "check the
# original message" both have well-defined answers even when nothing failed.
_FAILURE_SCOPED_INTENTS = frozenset({EXPLAIN_ATTEMPT_FAILURE, RETRY_ATTEMPT})


def resolve_followup_attempt(
    session_id: str,
    text: str,
    intent: str | None = None,
    *,
    exclude_attempt_id: str = "",
) -> tuple[dict[str, Any] | None, str]:
    """(attempt_or_None, reason). Tries tiers 1 then 3 (tier 2 -- an explicit visible-turn
    reference -- has no user-facing phrasing to match, turn ids are never shown to a person).

    `intent` narrows tier 3's scope: failure/retry intents only match a non-SUCCEEDED attempt;
    everything else matches the session's latest attempt regardless of outcome.

    A9 pass-002 (CE-5): the canonical turn door mints THIS follow-up turn's own attempt
    BEFORE referential resolution runs, so tier 3 would otherwise resolve the referent to
    the follow-up phrase itself merely because it is newest. The caller passes that
    current attempt's id here; it is excluded from every newest-row lookup. An ambiguous
    or self-only reference resolves to nothing (the turn falls through honestly) rather
    than fabricating itself as its own antecedent."""
    from core.runtime_continuity import (
        get_runtime_attempt_by_id_fragment,
        latest_runtime_attempt,
        latest_unresolved_or_partial_attempt,
    )

    referenced_id = find_referenced_attempt_id(text)
    if referenced_id:
        attempt = get_runtime_attempt_by_id_fragment(session_id, referenced_id)
        if attempt:
            return attempt, "explicitly referenced attempt id"
    excluded = str(exclude_attempt_id or "").strip()
    if intent in _FAILURE_SCOPED_INTENTS:
        attempt = latest_unresolved_or_partial_attempt(session_id, exclude_attempt_id=excluded)
        if attempt and _is_the_followup_itself(attempt, text):
            # FINDINGS F14.5 / F15 (owner transcript 2026-09-10 23:20): the follow-up "why?"
            # explained ITSELF -- "Your request was: 'why?' This is not automatically
            # retryable" -- because the door's own attempt for this turn was still RUNNING
            # and the id exclusion above did not reach it on that path. An attempt whose
            # recorded request IS the follow-up phrase can never be its antecedent; it is
            # stepped over by content, not only by id.
            attempt = latest_unresolved_or_partial_attempt(
                session_id, exclude_attempt_id=str(attempt.get("attempt_id") or excluded)
            )
            if attempt and _is_the_followup_itself(attempt, text):
                attempt = None
        if attempt:
            return attempt, "latest unresolved or partially failed attempt in session"
        # AUD-20260829-003 (C9). Attempt LIFECYCLE is the wrong grain for the defect this audit is
        # about. A turn that served the FX leg and dropped three requested slots is `SUCCEEDED`, so
        # the lookup above returns nothing and the turn falls through to generation -- which is
        # exactly what the daemon's own `runtime_followup_resolutions` rows recorded for every
        # `why did that fail?` in the capture: reason `no unresolved or partially failed attempt in
        # session`, `fallback_used = 1`. The per-slot record that DOES know something is
        # outstanding is the demand ledger, and it is consulted here, at the same tier, before
        # anything falls through.
        from core.refused_slot_register import latest_attempt_with_refused_slots

        attempt = latest_attempt_with_refused_slots(session_id, exclude_attempt_id=excluded)
        if attempt:
            return attempt, "latest attempt carrying a slot recorded as unanswered"
        return None, "no unresolved or partially failed attempt in session"
    attempt = latest_runtime_attempt(session_id, exclude_attempt_id=excluded)
    if attempt:
        return attempt, "latest attempt in session"
    return None, "no resolvable attempt in session"


# --- deterministic renderers -- no model call, no invented facts -------------------------------


def _refused_slots(attempt: dict[str, Any]) -> tuple[Any, ...]:
    """The slots this attempt's turn recorded as unanswered, or () when there is no such record.

    Fail-soft on purpose: an explanation that loses the ledger rows is thinner, never wrong. What
    it must never do is take an explanation down, so every failure path here is an empty tuple.
    """
    try:
        from core.refused_slot_register import refused_slots_for_attempt

        # A control phrase the user aimed at the runtime ("retry", "why did that fail?") is minted
        # as a demand unit like anything else and is recorded unanswered like anything else. It is
        # not something the person asked to know, so reading it back to them as a slot the runtime
        # failed on would be a true row and a nonsense explanation.
        return tuple(
            slot
            for slot in refused_slots_for_attempt(attempt)
            if classify_followup_intent(slot.text) is None
        )
    except Exception:
        return ()


def _subtask_label(subtask: dict[str, Any]) -> str:
    key = subtask.get("entity_key") or subtask.get("subtask_id") or "that item"
    return str(key).strip().title() if isinstance(key, str) else str(key)



def render_attempt_failure_explanation(
    attempt: dict[str, Any],
    subtasks: list[dict[str, Any]],
    *,
    original_request: str = "",
) -> str:
    """Deterministic explanation for EXPLAIN_ATTEMPT_FAILURE, built entirely from persisted
    attempt/subtask facts. Never invents a wallet/USDC/OS-consent/provider/policy explanation --
    if the attempt does not say it, this function does not say it either.

    `original_request` is what the OPERATOR sent, when the caller knows it. A mixed turn runs
    each demand as its own planned sub-turn, and each sub-turn writes an attempt row whose
    snapshot is its own clause; the follow-up resolver returns the most recent attempt carrying
    refused slots, routinely one of those. Quoting that row told the operator their request was
    one quarter of their request -- measured at both entrances on the canonical prompt.

    The parent's text is not lost: a planned sub-turn runs under the PARENT's canonical
    TurnRequest (`turn_planner_hook`), so the caller holds it. It is passed rather than dug out
    of the row, because the row is genuinely a sub-turn's row and rewriting it would be the
    second authority this module exists to avoid. Absent, the attempt's own snapshot stands.
    """
    original = (
        str(original_request or "").strip()
        or attempt.get("original_request_snapshot")
        or "(original request not available)"
    )
    lifecycle = str(attempt.get("lifecycle_state") or "UNKNOWN")
    parts: list[str] = [f'Your request was: "{original}"']

    # AUD-20260829-003 (C9/C12). Read the PER-SLOT record before the whole-attempt lifecycle
    # verdict. A turn that served one leg and dropped three is `SUCCEEDED`, and the sentence below
    # -- "that request succeeded, there is nothing to explain" -- was a false statement about
    # exactly the turn this audit exists for. The rows come from the demand ledger; every word of
    # them is ledger text, so this explanation cannot state a value it does not hold.
    refused = _refused_slots(attempt)
    if refused:
        parts.append(
            "These parts of it were not answered:"
            if len(refused) > 1
            else "This part of it was not answered:"
        )
        parts.extend(slot.as_row() for slot in refused)

    if lifecycle == "SUCCEEDED":
        if not refused:
            parts.append("That request succeeded -- there is nothing to explain a failure for.")
            return " ".join(parts)
        parts.append(
            "Nothing was retrieved for the part(s) above, so I hold no value for them and will "
            "not state one. Ask again and I'll try the lookup fresh."
        )
        return "\n".join(parts)

    failed = [s for s in subtasks if s.get("lifecycle_state") in ("FAILED", "UNSUPPORTED_ENTITY")]
    succeeded = [s for s in subtasks if s.get("lifecycle_state") == "SUCCEEDED"]

    if not failed and lifecycle in ("FAILED_VALIDATION",):
        parts.append(f"The request could not be turned into a runnable plan: {attempt.get('terminal_reason') or 'validation failed'}.")
    else:
        for subtask in failed:
            label = _subtask_label(subtask)
            reason = str(subtask.get("failure_reason") or "no reason recorded")
            if subtask.get("lifecycle_state") == "UNSUPPORTED_ENTITY":
                parts.append(f"{label} was not a recognized entity ({reason or 'unsupported'}).")
            else:
                parts.append(f"{label} failed: {reason}.")

    if succeeded:
        names = ", ".join(_subtask_label(s) for s in succeeded)
        parts.append(f"The successful result(s) were retained: {names}.")

    if bool(attempt.get("retryable")):
        stage = attempt.get("retry_from_stage") or ""
        target = f" It would re-run: {stage}." if stage else ""
        parts.append(f"This can be retried.{target}")
    else:
        reason = attempt.get("retry_reason") or "not automatically retryable"
        parts.append(f"This is not automatically retryable ({reason}).")

    # Ledger rows are one per line when there are any, so a multi-slot explanation reads as the
    # list it is instead of running four disclosure rows together inside one paragraph.
    return ("\n" if refused else " ").join(parts)


def render_original_entities(subtasks: list[dict[str, Any]]) -> str:
    """Deterministic answer for LIST_ORIGINAL_ENTITIES, read directly from persisted typed
    subtask rows -- never by reparsing a model's or a renderer's prose."""
    if not subtasks:
        return "No entities were recorded for that request."
    names = [_subtask_label(s) for s in subtasks]
    return "You asked about: " + ", ".join(names) + "."


def render_reconstructed_answer(
    attempt: dict[str, Any],
    subtasks: list[dict[str, Any]],
    *,
    source_context: dict[str, Any] | None = None,
) -> str:
    """Re-renders a SUCCEEDED attempt's answer entirely from its persisted subtask rows -- used
    for REPEAT_ORIGINAL_REQUEST ("check the original message and answer it") when the attempt
    already has valid results, so re-answering never re-derives entities from prose and never
    re-fetches data that was already retrieved successfully.

    `source_context` records that this turn answered from STORED evidence. A recall -- "check the
    original message and answer it", "that is not what I asked" -- is the one request prior evidence
    may legitimately answer, and this lane answers it the safest way there is: deterministically,
    from typed outcome rows, with each reading still carrying the original `observed_at` the fetch
    returned. That is recall, not stale substitution, and it is not a live claim about now.

    The record is required because the answer contains real readings and the turn ran no fetch of
    its own: measured once the attribution exemption came out, a reconstructed Kaunas answer was
    replaced with "I didn't run any live lookup on this turn" -- true about this turn's network
    activity, and wrong about where the answer came from. The record is PROVENANCE only: it always
    reads `ok=False` (a reconstruction is never an observation) and carries `fresh: False` /
    `lifecycle: "restored"` plus the original attempt's turn/generation identity, so no evidence
    reader can mistake restored history for a this-turn observation. Recall is licensed by the
    request not requiring a current reading -- never by manufactured evidence.
    """
    from core.agent_runtime.live_data_render import render_live_data_answer
    from core.attempt_retry import _subtask_from_row
    from core.live_data_plan import LiveDataPlan, SubtaskLifecycle, SubtaskOutcome

    plan_id = str(attempt.get("plan_id") or "reconstructed")
    outcomes: list[SubtaskOutcome] = []
    for row in subtasks:
        subtask = _subtask_from_row(row, plan_id=plan_id)
        try:
            state = SubtaskLifecycle[str(row.get("lifecycle_state") or "FAILED")]
        except KeyError:
            state = SubtaskLifecycle.FAILED
        outcomes.append(SubtaskOutcome(
            subtask=subtask, state=state, result=dict(row.get("result_summary") or {}) or None,
            failure_reason=str(row.get("failure_reason") or ""), approval_decision=str(row.get("approval_state") or ""),
        ))
    plan = LiveDataPlan(
        plan_id=plan_id, attempt_id=str(attempt.get("attempt_id") or ""),
        original_request=str(attempt.get("original_request_snapshot") or ""),
        subtasks=tuple(o.subtask for o in outcomes),
    )
    if isinstance(source_context, dict):
        observations = [
            dict(item)
            for item in list(source_context.get("runtime_tool_observations") or [])
            if isinstance(item, dict)
        ]
        succeeded = sum(1 for outcome in outcomes if outcome.ok)
        # Provenance, never a manufactured observation. This entry used to carry
        # `ok: bool(succeeded)` -- a synthetic ok=True on a turn that fetched nothing, which
        # `turn_ran_observations` read as "this turn observed", disarming the current-value
        # guards exactly when a recall answer states a reading. A reconstruction is a recall,
        # not an observation: `ok` is False because THIS turn observed nothing, and the entry
        # says so affirmatively (`fresh: False`, `lifecycle: "restored"`) so every evidence
        # reader treats it as restored history. What makes recall legitimate is that a recall
        # phrase is not a current-information request -- not that a stored row was dressed up
        # as a fresh one. The identity fields record WHOSE turn observed: source_turn_id,
        # evidence_set_id and generation point at the original attempt, so session membership
        # can never stand in for freshness.
        attempt_id = str(attempt.get("attempt_id") or "")
        observations.append({
            "schema": "tool_observation_v1",
            "intent": "attempt.reconstruct",
            "tool_surface": "runtime_attempts",
            "ok": False,
            "status": "reconstructed" if succeeded else "no_stored_results",
            "fresh": False,
            "lifecycle": "restored",
            "source_turn_id": str(attempt.get("trigger_user_turn_id") or attempt.get("origin_user_turn_id") or ""),
            "evidence_set_id": f"attempt:{attempt_id}",
            "generation": int(attempt.get("execution_generation") or 1),
            "response_preview": (
                f"reconstructed from {succeeded} stored subtask result(s) of attempt "
                f"{attempt_id!s}"
            ),
        })
        source_context["runtime_tool_observations"] = observations[-12:]
    return render_live_data_answer(plan, outcomes)
