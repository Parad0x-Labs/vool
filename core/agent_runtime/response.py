from __future__ import annotations

import json
import re
from typing import Any

from core.incomplete_answer import (
    incomplete_answer_notice,
    inspect_answer_completeness,
    partial_answer_notice,
)
from core.model_output_guard import (
    answer_is_tool_invocation,
    answer_is_unfulfilled_intent,
    has_restored_evidence_provenance,
    is_reasoning_only,
    turn_ran_observations,
    unobserved_live_value_claims,
    unverified_live_value_notice,
)
from core.ordinary_chat_response_guard import (
    constrain_ordinary_chat_output,
    inspect_ordinary_chat_output,
    ordinary_chat_safe_fallback,
)
from core.raw_output_contract import (
    apply_raw_output_contract,
    raw_output_contract_from_metadata,
)
from core.response_constraints import (
    constraint_safe_fallback,
    enforce_response_constraint,
    response_constraint_from_metadata,
)
from core.response_language_policy import (
    check_response_language,
    response_language_safe_fallback,
)

_NO_CLEAN_ANSWER = (
    "I couldn't produce a clean final answer from that model response. No result is being claimed."
)


def raw_contract_unsalvageable_notice() -> str:
    """The runtime's own report when a stated output contract left nothing shippable.

    A model produced text, enforcement found no deliverable in it (a scaffold, a tool echo, prose
    where code was required), and the turn must say so rather than render an empty answer -- the
    measured failure this replaces showed 'Completed task with final response:' followed by
    nothing. It is a notice, not an answer, and is exempt from answer-shape contracts for the same
    reason the degraded-model notice is.
    """
    return (
        "The generated reply did not contain the requested output format, and nothing matching it "
        "could be recovered, so no answer is being shown. Ask again and I'll run it fresh."
    )

_ORCHESTRATION_LEAK_MARKERS = (
    '"schema": "vool.task_envelope.v1"',
    "task_envelope",
    "tool_permissions",
    "model_constraints",
    "latency_budget",
    "quality_target",
    "allowed_side_effects",
    "required_receipts",
    "merge_strategy",
    "cancellation_policy",
    "privacy_class",
    "routing_requirements",
    "rejected_candidates",
    "selection_notes",
    "provider_capability_truth",
    "queue_pressure_strategy",
    "required_locality",
    "preferred_locality",
    "preferred_provider_role",
    "effective_swarm_size",
    "capacity_backoff_applied",
    "capacity_backoff_notes",
    "capacity_blocked",
    "capacity_state",
    "scheduled_children",
    "merged_result",
    "step_results",
    "graph",
)
_ENVELOPE_ROLE_MARKERS = (
    "queen envelope",
    "coder envelope",
    "verifier envelope",
    "researcher envelope",
    "memory clerk envelope",
    "memory_clerk envelope",
    "narrator envelope",
)
_ORCHESTRATION_FRAGMENT_MARKERS = _ORCHESTRATION_LEAK_MARKERS + _ENVELOPE_ROLE_MARKERS
_TRACEBACK_LINE_RE = re.compile(r'^\s*File\s+"[^"]+",\s+line\s+\d+', re.IGNORECASE | re.MULTILINE)
# An exception line as Python actually emits one: the class name STARTS a line. `ValueError: x`,
# `SyntaxError: invalid syntax`, `json.decoder.JSONDecodeError: ...`.
#
# The looser test this replaces was `"error:" in lowered and ("line " in lowered or "exception" in
# lowered)` over the whole blob, which matches any multi-line text that merely MENTIONS those words
# - including ordinary source code. Measured live 2026-08-03: `pdf_rebuild.py` was read correctly
# and then discarded as a crash dump because it contains `error:` once inside a print and the word
# `exception` twice in its handlers; the operator got "I couldn't resolve that cleanly" instead of
# their file. README.md in the same folder survived, which is why the failure looked random.
# `[^\S\n]` and not `\s`: the message must sit on the SAME line as the class name. With `\s`
# the pattern spanned the newline, so prose reading "Error:\n\nsomething went wrong" convicted.
_EXCEPTION_LINE_RE = re.compile(r"^[^\S\n]*[\w.]*(?:Error|Exception|Warning):[^\S\n]+\S", re.MULTILINE)
# Two structural limits closed here after the 2026-08-01 leak.
#
# It was `.search()` with no `re.MULTILINE` on a `^\s*(...)` pattern, so it only ever fired when the
# monologue was the very FIRST token of the reply — a reply that says something innocuous and then
# drops into "Okay, so the user wants..." escaped it entirely, taking the `final answer:` recovery
# below with it. And the alternation was narrow enough that the phrases the live failure actually
# used — "Okay, so the user wants", "Hmm, let me", "But wait", "Actually,", "Let me start by",
# "Let me read" — all matched nothing.
#
# MULTILINE widens WHERE this fires, not what it condemns: a lead found mid-reply now only suppresses
# the answer when `is_reasoning_only` agrees the whole reply is monologue (see
# suppress_internal_reasoning_leak). "…but wait, actually there is a subtlety" inside a real answer
# survives.
_INTERNAL_REASONING_LEAD_RE = re.compile(
    r"^[ \t>*_`#-]*"
    r"(?:(?:ok(?:ay)?|alright|hmm+|uh+|um+|well|so|right|now|first(?:ly)?|but|and)\b[\s,.:;!?—–-]*)*"
    r"(?:the user (?:is |just |also )?(?:asking|asks|asked|wants|wanted|needs|said|mentioned)\b|"
    r"we need to (?:answer|respond|check|look|figure|work out)\b|"
    r"i need to (?:answer|respond|check|inspect|look|see|find|figure|read|verify|understand)\b|"
    r"(?:i|we) should (?:check|look|probably|first|also|make sure|verify|re-?read)\b|"
    r"let me (?:first |now |also |just |quickly |carefully )?"
    r"(?:check|inspect|look|think|see|start|begin|re-?read|read|reconsider|re-?examine|examine|"
    r"analy[sz]e|figure|try|recall|consider|parse|trace|verify|confirm|make sure|scan|search|"
    r"find|count|map|recap|go back|work out)\b|"
    r"let(?:'|’)s (?:see|think|check|look|start|figure|recap)\b|"
    r"(?:but )?wait[,.!]|actually[,.!]|hold on[,.!]|hmm+[,.!]|"
    r"looking (?:at|back at) (?:the|this|my) "
    r"(?:conversation|context|question|request|prompt|history)\b"
    r")",
    re.IGNORECASE | re.MULTILINE,
)
_INTERNAL_REASONING_PLAN_RE = re.compile(
    r"\b(?:let me|i should|i need to|we need to|from the previous|looking at the conversation|"
    r"i don(?:'|’)t see|the context provided)\b",
    re.IGNORECASE,
)


def turn_result(
    chat_turn_result_cls: type[Any],
    text: str,
    response_class: Any,
    *,
    workflow_summary: str = "",
    debug_origin: str | None = None,
    allow_planner_style: bool = False,
) -> Any:
    return chat_turn_result_cls(
        text=str(text or "").strip(),
        response_class=response_class,
        workflow_summary=str(workflow_summary or "").strip(),
        debug_origin=debug_origin,
        allow_planner_style=bool(allow_planner_style),
    )


def decorate_chat_response(
    agent: Any,
    response: Any,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
    workflow_summary: str = "",
    include_hive_footer: bool | None = None,
) -> str:
    result = response if hasattr(response, "response_class") else agent._turn_result(
        str(response or ""),
        agent.ResponseClass.GENERIC_CONVERSATION,
        workflow_summary=workflow_summary,
    )
    clean_text = agent._shape_user_facing_text(result)
    constraint = response_constraint_from_metadata(source_context)
    raw_contract = raw_output_contract_from_metadata(source_context)
    # The UI must display the same shape the model router validated.  Workflow/Hive decoration
    # is useful context for normal replies but becomes a contract violation for an explicit
    # bounded response request.
    # A runtime notice is not an answer, so an answer-shape contract has nothing to shape. Trimming
    # it to the requested word count destroys the one thing the reader needs -- why there is no
    # answer -- and the truncation reads as a crash rather than as an explanation.
    if constraint is not None and isinstance(source_context, dict) and source_context.get(
        "runtime_notice_not_an_answer"
    ):
        constraint = None
    if constraint is not None:
        application = enforce_response_constraint(clean_text, constraint)
        final_text = application.text
        fallback_applied = False
        if not application.compliant:
            final_text = constraint_safe_fallback(constraint)
            fallback_applied = True
        if isinstance(source_context, dict):
            source_context["response_constraint_final"] = {
                "compliant": application.compliant,
                "structurally_trimmed": application.structurally_trimmed,
                "violations": list(application.violations),
                "fallback_applied": fallback_applied,
            }
        return _validate_final_chat_output(
            final_text,
            source_context=source_context,
        )
    if raw_contract is not None:
        # Workflow summaries and Hive footers are useful on ordinary turns, but under an explicit
        # raw-output contract they are trailing contract violations, not decorations.
        return _validate_final_chat_output(
            clean_text,
            source_context=source_context,
        )
    if agent._should_show_workflow_for_result(result, source_context=source_context):
        decorated = agent._maybe_attach_workflow(
            clean_text,
            result.workflow_summary,
            source_context=source_context,
        )
    else:
        decorated = clean_text
    footer_allowed = (
        agent._should_attach_hive_footer(result, source_context=source_context)
        if include_hive_footer is None
        else bool(include_hive_footer)
    )
    hive_footer = agent._maybe_hive_footer(session_id=session_id, source_context=source_context) if footer_allowed else ""
    if hive_footer:
        decorated = agent._append_footer(decorated, prefix="Hive", footer=hive_footer)
    return _validate_final_chat_output(decorated, source_context=source_context)


def _mark_turn_unfulfilled(source_context: dict[str, object] | None, reason: str) -> None:
    """Record on the turn's execution identity that its final text is a stated NON-fulfilment.

    The door closes the turn's attempt from this identity (`agent.run_once`): a turn that did not
    raise used to close SUCCEEDED even when what it published was the safe fallback or a withheld-
    figures notice, so "why?" afterwards found nothing to explain (FINDINGS F14.5 / F15). The
    identity dict is shared by reference across the context copies the lanes receive, so a mark
    set here is the one the door reads. Best-effort: never raises into the turn.
    """
    try:
        identity = (source_context or {}).get("_execution_identity")
        if isinstance(identity, dict) and not identity.get("turn_outcome"):
            identity["turn_outcome"] = str(reason or "")[:160]
    except Exception:
        return


def _request_text_of(source_context: dict[str, object] | None) -> str:
    """The turn's own request text, from the same keys the ledgers read it under."""
    context = source_context if isinstance(source_context, dict) else {}
    for key in ("turn_request", "user_input", "effective_input", "request_text"):
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            return value
        for attr in ("user_text", "text"):
            text = str(getattr(value, attr, "") or "").strip()
            if text:
                return text
    return ""


def _stipulated_frame_owns_the_values(source_context: dict[str, object] | None) -> bool:
    """Whether the turn's values are premises the USER stipulated, not live-world claims.

    The frozen requirement record first (its ``user_stipulated_frame`` reason code is the
    authority's own reading of the request); the stipulated-frame authority's text reading as the
    fallback when no record reached this seam. Both decline a frame whose lookup targets
    independent real-world facts, so a genuine current-price question never stands down.
    """
    try:
        from core.execution_requirements import current_requirement_record

        record = current_requirement_record(source_context)
        if record is not None:
            if "user_stipulated_frame" in tuple(record.requirements.reason_codes or ()):
                return True
            if tuple(record.requirements.reason_codes or ()):
                # A frozen reading that is NOT a stipulated frame is the authority's answer.
                return False
        from core.stipulated_frame import stipulated_frame_active

        return stipulated_frame_active(_request_text_of(source_context))
    except Exception:
        return False


#: Context key carrying a render the RUNTIME authored deterministically from its own durable
#: record this turn. Written at the render seam, read only here. See
#: `_is_recorded_deterministic_render`.
DETERMINISTIC_RENDER_KEY = "deterministic_explanation"


def record_deterministic_render(
    source_context: dict[str, object] | None, render: str, *, route: str
) -> None:
    """Record that THIS turn produced `render` deterministically from the durable ledger.

    The follow-up explanation is not generation. It is the runtime reading back its own
    obligation record -- the slots on file and the reason each is on file -- and the bytes are
    fixed before any guard sees them. Recording the exact render is what lets the output seam
    tell that text apart from prose that merely resembles it.

    Fail-soft, like every other stamp on this path: a turn that cannot record its render still
    answers; its explanation is simply judged as ordinary text.
    """
    if not isinstance(source_context, dict):
        return
    try:
        source_context[DETERMINISTIC_RENDER_KEY] = {
            "render": str(render or ""),
            "route": str(route or ""),
        }
    except Exception:
        return


def _is_recorded_deterministic_render(
    text: str, source_context: dict[str, object] | None
) -> bool:
    """Whether these exact bytes are the deterministic render this turn recorded.

    C9. The runtime built the correct three-slot failure explanation and then convicted it:
    the explanation quotes the operator's own request, the canonical request contains
    "1000 EUR", and the unobserved-live-value guard read that as a claim about a current
    price. The user never saw the slot list -- including the gold slot, which nothing else in
    the audit surfaces. The refused-slot branch reaches the same text by a different door.

    The exemption is PROVENANCE-KEYED and BYTE-EXACT, which is what stops it becoming the hole
    the guards exist to close:

    * no flag on the context -> no exemption. Model-authored text cannot acquire one, because
      only the render seam writes it;
    * one mutated byte -> no exemption. Text edited after the stamp is not the text that was
      recorded, so a model cannot ride along by appending to a flagged render.

    Fail-closed on every unexpected shape: anything that is not an exact match is judged
    exactly as it was before this existed.
    """
    if not isinstance(source_context, dict):
        return False
    recorded = source_context.get(DETERMINISTIC_RENDER_KEY)
    if not isinstance(recorded, dict):
        return False
    return str(recorded.get("render") or "") == str(text or "") != ""


def _validate_final_chat_output(
    text: str,
    *,
    source_context: dict[str, object] | None,
) -> str:
    """Backstop the exact text rendered and later retained as chat memory."""
    raw_contract = raw_output_contract_from_metadata(source_context)
    ordinary_policy = dict(
        (source_context or {}).get("ordinary_chat_output_policy") or {}
    )
    # Match provider publication: default brevity is advisory, explicit contracts remain below.
    ordinary_policy["brevity_advisory"] = True
    ordinary_check = inspect_ordinary_chat_output(
        text,
        ordinary_policy,
    )
    language_check = check_response_language(
        text,
        dict((source_context or {}).get("response_language_policy") or {}),
    )
    final_text = str(text or "").strip()
    if not ordinary_check.allowed:
        if ordinary_check.reasons == ("ordinary_response_too_long",):
            final_text = constrain_ordinary_chat_output(final_text, ordinary_policy)
            ordinary_check = inspect_ordinary_chat_output(final_text, ordinary_policy)
        else:
            final_text = ordinary_chat_safe_fallback()
    elif not language_check.compliant:
        final_text = response_language_safe_fallback()
    # A tool call is a request the runtime should have executed, never an answer the user reads.
    # `claims_pending_tool` only guards the tool loop's synthesis step, so a model on the ordinary
    # chat lane could emit `search_web("...")` -- or promise "searching ..." -- and have it
    # committed verbatim as the visible reply (observed live on 0.5.0, turns 3/6/11).  This lane
    # has no observations to fall back on, so the honest outcome is the safe fallback, not a
    # fabricated call.
    tool_invocation = answer_is_tool_invocation(final_text)
    # A reply that only PROMISES tool work is the same failure wearing prose: "Let me search for
    # the latest data..." shipped as a complete answer, live, with nothing searched and nothing
    # delivered. Same seam, same honest outcome.
    if raw_contract is None and answer_is_unfulfilled_intent(final_text):
        tool_invocation = True
    if tool_invocation and raw_contract is None:
        final_text = ordinary_chat_safe_fallback()
        _mark_turn_unfulfilled(source_context, "chat reply replaced by the safe fallback")
    # A live claim requires an observation. Measured 2026-08-15 (operator transcript, 12:45): a
    # turn on which ZERO tools ran shipped "Warsaw: 22 C, partly cloudy; Manchester: 18 C",
    # "Luton: EUR 42; Gatwick: EUR 58" and "last 7 days: BTC +4.8%" with full confidence -- the
    # exact readings the previous turn had just failed to fetch. Both conjuncts are checked here
    # because this is the only seam that sees the final text AND the turn's same-turn evidence
    # channels together: the reply binds a quantified value (currency/degree/percent-change) to a
    # currentness anchor, and the turn observed nothing (`turn_ran_observations`). A turn WITH
    # observations keeps the jurisdiction of the guards that own it (`is_ungrounded`,
    # `core.unsourced_current_claim`); a raw-output contract wins as it does for every other
    # decoration on this path.
    live_claim_kinds: tuple[str, ...] = ()
    refused_slot_claims: tuple[dict[str, object], ...] = ()
    if raw_contract is None and not turn_ran_observations(
        source_context
    ) and not _is_recorded_deterministic_render(final_text, source_context):
        live_claim_kinds = unobserved_live_value_claims(final_text)
        if live_claim_kinds and _stipulated_frame_owns_the_values(source_context):
            # Named stand-down, same law as the restored-provenance one below: the values in this
            # text derive from premises the USER supplied in the request (the stipulated-frame
            # authority froze the turn as DIRECT/in-frame), so the notice's premise -- "any
            # specific numbers I gave would be invented" -- is false for them. Measured live
            # 2026-09-18: a fully supplied hypothetical price calculation (all prices, token
            # counts and limits in the message) was replaced by exactly that notice while the
            # correct arithmetic sat in the ledger. The stipulated authority itself declines
            # frames whose lookup targets independent real-world facts, so a genuine "current
            # price of X" keeps this guard armed.
            live_claim_kinds = ()
        if live_claim_kinds and has_restored_evidence_provenance(source_context):
            # Restored-provenance recall: the values in this text are re-rendered STORED
            # observations (a "check the original message" reconstruction), not inventions of
            # generation -- the channels carry the restored entry naming the turn that really
            # fetched them, and the answer itself discloses the fetch's timestamp. The notice's
            # premise ("any specific numbers I gave would be invented") is false for this text.
            # The entry never posed as an observation -- `turn_ran_observations` above already
            # answered False -- so this stand-down is named, not smuggled. The refused-slot
            # contract below still runs: a recall may not state a value for a slot the record
            # says was unanswered either.
            live_claim_kinds = ()
        if live_claim_kinds:
            final_text = unverified_live_value_notice(
                live_claim_kinds,
                _request_text_of(source_context),
                part_of_turn=bool((source_context or {}).get("planned_subturn")),
            )
            _mark_turn_unfulfilled(source_context, "live figures withheld: no lookup ran for them")
    # THE C12 CONTRACT (AUD-20260829-003): a slot this session's durable record says was NOT
    # answered may never acquire a value from generation.
    #
    # The live-value branch above owns the unobserved claim that says "now". It cannot own this
    # one: its currentness requirement and its hedge exemption are calibrated for an OPEN
    # question, and every fabrication measured on 2026-08-30 escaped through exactly those two
    # doors -- "the Baltic Sea is typically around 15-18 C in late summer", stated in the same
    # chat in which the runtime had just rendered `* the water temperature in the Baltic Sea --
    # no answering lane claimed this part of the request`. Two independent chats produced
    # 9-12 C, 15-18 C and 18-20 C for that one slot, which is the proof no source exists.
    #
    # THIS CONTRACT HAS ITS OWN ARMING, and deliberately does not share the gate above.
    # A provenance veto outranks output-shape preservation, and it outranks a turn-grain
    # observation count:
    #
    # * a RAW OUTPUT CONTRACT is a clause about FORMAT. It cannot authorise inventing content,
    #   and while the register sat behind `raw_contract is None` the widened 240-character
    #   literal patterns meant a fabrication could be carried inside the very clause that
    #   disarmed the guard against it (red_h `evade-terse`);
    # * `turn_ran_observations` answers a question about the WHOLE TURN, so one usable
    #   observation anywhere disarmed every refused slot at once -- a turn that fetched an FX
    #   rate could then state a water temperature nobody fetched (red_h `disarm-*`). Arming is
    #   now decided PER SLOT, by whether the turn's own evidence names THAT slot.
    #
    # What ships instead of the value is the RECORD -- the slots on file and the reason -- so
    # the reader gets more than the invented figure, not less. No prose is edited and no phrase
    # is detected: see `core.refused_slot_register`.
    if not live_claim_kinds and not _is_recorded_deterministic_render(
        final_text, source_context
    ):
        from core.model_output_guard import live_value_windows
        from core.refused_slot_register import (
            refused_slot_notice,
            register_in_scope,
            slots_still_armed,
            values_claimed_over_refused_slots,
        )

        # Ordered so the durable read only happens on a turn that actually states a value
        # shape -- the common turn pays one regex sweep and no I/O.
        if live_value_windows(final_text):
            armed = slots_still_armed(register_in_scope(source_context), source_context)
            if armed:
                refused_slot_claims = values_claimed_over_refused_slots(final_text, armed)
                if refused_slot_claims:
                    final_text = refused_slot_notice(refused_slot_claims)
                    # What ships now is the runtime's own record, not an answer -- the same
                    # category as the unsalvageable-contract notice. Re-applying the output
                    # shape to it would restore the exact literal the contract asked for,
                    # which is the fabrication this branch just declined to serve.
                    if isinstance(source_context, dict):
                        source_context["runtime_notice_not_an_answer"] = True
                    raw_contract = None
    # The last thing standing between a cut-off answer and the user. Measured on the v0.5.0 smoke
    # run (QA-050-017): the literal string `1.` reached this function and left it unchanged, and so
    # did the empty string -- `inspect_ordinary_chat_output` returns allowed=True for both. An
    # answer with nothing in it is replaced; one that is real but unfinished keeps every word it
    # has and says its state out loud, because deleting most of an answer to report the rest is
    # missing helps nobody.
    completeness = inspect_answer_completeness(final_text)
    if raw_contract is not None:
        # A runtime-authored apology or partial-answer notice is still extra text.  Under this
        # explicit contract, reject an unusable draft and otherwise preserve only its deliverable.
        # A tool call is not a deliverable under an explicit output contract either.
        if (
            completeness.degenerate
            or tool_invocation
            or not ordinary_check.allowed
            or not language_check.compliant
        ):
            final_text = ""
    elif completeness.degenerate:
        final_text = incomplete_answer_notice(completeness)
    elif completeness.incomplete:
        final_text = f"{final_text}\n\n{partial_answer_notice(completeness)}"
    if isinstance(source_context, dict):
        control = dict(source_context.get("response_control") or {})
        control["final_ui"] = {
            "ordinary_chat_output": ordinary_check.to_dict(),
            "response_language": language_check.to_dict(),
            "answer_completeness": completeness.as_dict(),
            "tool_invocation_rejected": tool_invocation,
            "fallback_applied": final_text != str(text or "").strip(),
        }
        # Recorded only when it fired: consumers assert this record's exact shape, and an
        # empty-list key on every clean turn says nothing a missing key does not.
        if live_claim_kinds:
            control["final_ui"]["unobserved_live_claims_rejected"] = list(live_claim_kinds)
        # Same discipline: recorded only when the contract actually bit, so a clean turn's record
        # is byte-identical to what it was before this branch existed.
        if refused_slot_claims:
            control["final_ui"]["refused_slot_claims_rejected"] = [
                {
                    "kind": str(claim.get("kind") or ""),
                    "slot": str(claim.get("slot") or ""),
                    "unit_id": str(claim.get("unit_id") or ""),
                    "attempt_id": str(claim.get("attempt_id") or ""),
                }
                for claim in refused_slot_claims
            ]
        source_context["response_control"] = control
    if raw_contract is not None:
        raw_application = apply_raw_output_contract(final_text, raw_contract)
        final_text = raw_application.text
        if isinstance(source_context, dict):
            control = dict(source_context.get("response_control") or {})
            control["raw_output_final"] = {
                "changed": raw_application.changed,
                "rejected": raw_application.rejected,
                "compliant": raw_application.compliant,
                "violations": list(raw_application.violations),
                "actions": list(raw_application.actions),
            }
            source_context["response_control"] = control
        if not final_text.strip():
            # Enforcement never ends a turn as a silent empty string. Reaching here empty means a
            # draft existed and was withheld -- either in THIS pass (the incoming text was
            # non-empty: a scaffold, a tool echo, prose where code was required) or upstream at
            # the model router, whose application record travels in `response_control` (measured:
            # a 1,136-token deliverable shipped as "" with 'Completed task with final response:'
            # and nothing after it). The reply becomes the runtime's own report -- a notice, not
            # an answer, so it is marked with the same `runtime_notice_not_an_answer` flag the
            # degraded lane uses and no answer-shape contract is re-applied to it.
            upstream_control = (
                dict(source_context.get("response_control") or {})
                if isinstance(source_context, dict)
                else {}
            )
            upstream_raw = dict(upstream_control.get("raw_output") or {})
            draft_was_withheld = bool(str(text or "").strip()) or bool(
                upstream_raw.get("rejected")
            )
            if draft_was_withheld:
                final_text = raw_contract_unsalvageable_notice()
                if isinstance(source_context, dict):
                    source_context["runtime_notice_not_an_answer"] = True
                    control = dict(source_context.get("response_control") or {})
                    control["raw_output_final"] = {
                        **dict(control.get("raw_output_final") or {}),
                        "unsalvageable_notice_applied": True,
                    }
                    source_context["response_control"] = control
    return final_text


def shape_user_facing_text(agent: Any, result: Any) -> str:
    text = agent._sanitize_user_chat_text(
        result.text,
        response_class=result.response_class,
        allow_planner_style=result.allow_planner_style,
    )
    if result.response_class == agent.ResponseClass.TASK_STARTED:
        started_research_match = re.match(
            r"^Started research on\s+`?([^`]+?)`?\.?$",
            text,
            flags=re.IGNORECASE,
        )
        if started_research_match:
            title = " ".join(str(started_research_match.group(1) or "").split()).strip()
            if title:
                text = f"Started Hive research on `{title}`."
        text = re.sub(
            r"^Autonomous research on\s+`?([^`]+)`?\s+packed\s+\d+\s+research queries,\s*\d+\s+candidate notes,\s*and\s*\d+\s+gate decisions\.?",
            r"Started Hive research on `\1`. First bounded pass is underway.",
            text,
            flags=re.IGNORECASE,
        )
        text = text.replace(
            "The first bounded research pass already ran and posted its result.",
            "The first bounded pass already landed.",
        )
        text = text.replace(
            "This fast reply only means the first bounded research pass finished.",
            "The first bounded pass finished.",
        )
        text = text.replace(
            "Topic stays `researching` because VOOL still needs more evidence before it can honestly mark the task solved.",
            "It is still open because the solve threshold was not met yet.",
        )
        text = text.replace(
            "The research lane is active.",
            "First bounded pass is underway.",
        )
        text = re.sub(r"\bBounded queries run:\s*\d+\.\s*", "", text)
        text = re.sub(r"\bArtifacts packed:\s*\d+\.\s*", "", text)
        text = re.sub(r"\bCandidate notes:\s*\d+\.\s*", "", text)
        return " ".join(text.split()).strip()
    if result.response_class == agent.ResponseClass.RESEARCH_PROGRESS:
        text = re.sub(r"^Research follow-up:\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"^Research result:\s*", "Here’s what I found: ", text, flags=re.IGNORECASE)
        return " ".join(text.split()).strip()
    return text


def should_show_workflow_for_result(
    agent: Any,
    result: Any,
    *,
    source_context: dict[str, object] | None,
) -> bool:
    if result.response_class in {
        agent.ResponseClass.SMALLTALK,
        agent.ResponseClass.UTILITY_ANSWER,
        agent.ResponseClass.GENERIC_CONVERSATION,
        agent.ResponseClass.TASK_FAILED_USER_SAFE,
        agent.ResponseClass.SYSTEM_ERROR_USER_SAFE,
        agent.ResponseClass.TASK_STARTED,
        agent.ResponseClass.RESEARCH_PROGRESS,
    }:
        return False
    return agent._should_show_workflow_summary(
        response=result.text,
        workflow_summary=result.workflow_summary,
        source_context=source_context,
    )


def sanitize_user_chat_text(
    agent: Any,
    text: str,
    *,
    response_class: Any,
    allow_planner_style: bool = False,
) -> str:
    base_text = str(text or "").strip()
    # A model may not stamp its own guesses "verified". The runtime has never awarded that label --
    # `grep "Verified fact" core/ tools/` finds nothing -- so its presence proves the model wrote it,
    # which makes it unearned by definition and needs no evidence lookup to refuse. Measured
    # 2026-08-05: a car question answered with a "Verified fact" column whose cells included an
    # engine code contradicting its own displacement and a sales year assembled from two unrelated
    # real figures. See core/agent_runtime/verification_labels.py.
    from core.agent_runtime.verification_labels import downgrade_unearned_verification_labels

    base_text, _downgraded = downgrade_unearned_verification_labels(base_text)
    # A tool-call envelope in a supposedly-final answer means the model asked to run a tool and the
    # runtime rendered the request instead of executing it. Measured 2026-08-05 on the aviation
    # prompt: `{"tool":"search","queries":["most produced commercial aircraft ...` was displayed as
    # the answer, with zero tool events in the trace. `foreign_markers` already detects this
    # envelope and was only wired into the tool loop's synthesis check, never into the chat answer
    # path -- so on this path raw JSON reached the user unchallenged.
    #
    # The replacement names the stage rather than adding another interchangeable apology. Six
    # different causes already reach people as the same sentence (see core/turn_failure_stage.py);
    # a seventh would make this harder to diagnose, not easier.
    from core.model_output_guard import foreign_markers

    leaked = foreign_markers(base_text)
    if leaked:
        return (
            "I asked to run a tool and the runtime did not execute it, so I have no result to give "
            "you rather than a half-finished one. Ask again and it will retry."
        )
    sanitized = agent._strip_runtime_preamble(base_text, allow_planner_style=False)
    sanitized = agent._strip_planner_leakage(sanitized)
    reasoning_safe = suppress_internal_reasoning_leak(sanitized)
    if reasoning_safe is not None:
        return reasoning_safe
    if agent._contains_generic_planner_scaffold(sanitized):
        if response_class == agent.ResponseClass.UTILITY_ANSWER:
            return "I couldn't answer that utility request cleanly."
        if response_class in {agent.ResponseClass.TASK_FAILED_USER_SAFE, agent.ResponseClass.SYSTEM_ERROR_USER_SAFE}:
            return "I couldn't map that cleanly to a real action."
        return "I'm here and ready to help. What do you want to do?"
    lowered = sanitized.lower()
    forbidden = (
        "invalid tool payload",
        "missing_intent",
        "i won't fake it",
        "tool_failed",
        '"mode": "tool_failed"',
        '"status": "missing_intent"',
    )
    if any(marker in lowered for marker in forbidden):
        if response_class == agent.ResponseClass.UTILITY_ANSWER:
            return "I couldn't answer that utility request cleanly."
        if response_class in {agent.ResponseClass.TASK_FAILED_USER_SAFE, agent.ResponseClass.SYSTEM_ERROR_USER_SAFE}:
            return "I couldn't map that cleanly to a real action."
        return "I couldn't resolve that cleanly."
    if looks_like_runtime_traceback(sanitized):
        if response_class in {agent.ResponseClass.TASK_FAILED_USER_SAFE, agent.ResponseClass.SYSTEM_ERROR_USER_SAFE}:
            return "I hit an internal failure while handling that request."
        return "I couldn't resolve that cleanly."
    degraded_fallback_markers = (
        "couldn't produce a clean final synthesis in this run",
        "couldn't produce a grounded conversational reply in this run",
        "couldn't produce a grounded help reply in this run",
        "couldn't produce a clean final summary",
    )
    if any(marker in lowered for marker in degraded_fallback_markers):
        if response_class == agent.ResponseClass.UTILITY_ANSWER:
            return "I checked, but I couldn't ground a confident answer from the evidence I found."
        if response_class in {agent.ResponseClass.TASK_FAILED_USER_SAFE, agent.ResponseClass.SYSTEM_ERROR_USER_SAFE}:
            return "I got part of the work done, but I couldn't close it out cleanly."
        return "I couldn't answer that cleanly. Ask it another way."
    orchestration_safe_text = humanize_orchestration_leak(
        agent,
        sanitized,
        response_class=response_class,
    )
    if orchestration_safe_text is not None:
        return orchestration_safe_text
    return sanitized


def suppress_internal_reasoning_leak(text: str) -> str | None:
    """Fail closed when a provider emits its private planning monologue as answer content.

    Last hop before the user. The router escalates a reasoning-only reply to the next ranked model
    (core/memory_first_router.py::_soft_failure_details); this catches what survives that — the run
    where every candidate produced monologue and the first contract-failed decision is served
    anyway, and any lane that reaches the shaper without passing through the router loop.

    Three routes in, deliberately different in what they cost:
      * a monologue that reached a `final answer:` handoff gives up the answer and keeps it;
      * `is_reasoning_only` — the whole reply is reasoning, wherever the phrasing sits in it;
      * a lead pattern that OPENS the reply plus planning vocabulary, the original rule.
    A lead pattern found mid-reply is never enough on its own: "…but wait, actually there is a
    subtlety" is how a real answer corrects itself, and suppressing that would delete the answer.
    """

    clean = str(text or "").strip()
    lead = _INTERNAL_REASONING_LEAD_RE.search(clean)
    monologue = is_reasoning_only(clean)
    if lead is None and not monologue:
        return None
    final_match = re.search(r"(?:^|[.\n])\s*(?:final answer|answer)\s*:\s*(.+)\Z", clean, re.IGNORECASE | re.DOTALL)
    if final_match and final_match.group(1).strip():
        return final_match.group(1).strip()
    if monologue:
        return _NO_CLEAN_ANSWER
    if lead.start() > 0:
        return None
    if not _INTERNAL_REASONING_PLAN_RE.search(clean):
        return None
    return _NO_CLEAN_ANSWER


def internal_payload_or_monologue(text: str) -> bool:
    """Machine scaffolding or a bare reasoning monologue — never an answer to a person.

    The single question every passthrough point asks before printing model text: the payload
    shapes (a filename array welded to code, an unclosed reasoning block, a fabricated tool
    result) and the reasoning-lead check that catches a model narrating its plan instead of
    answering. One function so the buffered turn, the structured-synthesis branch, the
    respond.direct lane and the streaming release gate cannot drift apart in what they refuse.
    Fail-soft — a guard that raises must not take the turn down with it.
    """

    body = str(text or "").strip()
    if not body:
        return False
    try:
        from core.tool_call_dialects import looks_like_internal_payload

        if looks_like_internal_payload(body):
            return True
    except Exception:
        pass
    try:
        return suppress_internal_reasoning_leak(body) is not None
    except Exception:
        return False


def strip_runtime_preamble(text: str, *, allow_planner_style: bool = False) -> str:
    clean = str(text or "").strip()
    if allow_planner_style:
        return clean
    if not clean.startswith(("Real steps completed:", "Tool results:")):
        return clean
    parts = clean.split("\n\n", 1)
    if len(parts) == 2 and parts[1].strip():
        return parts[1].strip()
    return "I couldn't resolve that cleanly."


def looks_like_runtime_traceback(text: str) -> bool:
    lowered = str(text or "").lower()
    if "traceback (most recent call last)" in lowered:
        return True
    if _TRACEBACK_LINE_RE.search(str(text or "")):
        return True
    # Structural, not lexical: an exception CLASS opening a line, not the word "error" appearing
    # somewhere in a multi-line blob. Source code that handles errors is not a crash.
    return lowered.count("\n") >= 2 and bool(_EXCEPTION_LINE_RE.search(str(text or "")))


def strip_planner_leakage(agent: Any, text: str) -> str:
    clean = str(text or "").strip()
    if not clean:
        return ""

    clean = agent._unwrap_summary_or_action_payload(clean)

    lowered = clean.lower()
    if lowered.startswith("workflow:"):
        parts = clean.split("\n\n", 1)
        if len(parts) == 2 and parts[1].strip():
            clean = parts[1].strip()
        else:
            clean = re.sub(r"^workflow:\s*", "", clean, flags=re.IGNORECASE).strip()

    clean = re.sub(r"^here(?:'|’)s what i(?:'|’)d suggest:\s*", "", clean, flags=re.IGNORECASE).strip()
    clean = re.sub(r"^(summary_block|action_plan)\s*:\s*", "", clean, flags=re.IGNORECASE).strip()
    return clean


def humanize_orchestration_leak(
    agent: Any,
    text: str,
    *,
    response_class: Any,
) -> str | None:
    clean = str(text or "").strip()
    if not clean:
        return None
    lowered = clean.lower()
    if lowered.startswith("search matches for ") or lowered.startswith("file `") or lowered.startswith("local file `"):
        return None
    if not any(marker in lowered for marker in _ORCHESTRATION_LEAK_MARKERS) and not any(
        marker in lowered for marker in _ENVELOPE_ROLE_MARKERS
    ):
        return None
    safe_text = _strip_orchestration_fragments(clean)
    if safe_text:
        return safe_text
    if "missing required receipts" in lowered:
        return "I finished part of that run, but I couldn't close it out because the required proof receipts were missing."
    if "not allowed to run" in lowered or "not allowed to trigger" in lowered:
        return "I couldn't complete that bounded worker step because its permissions did not allow the requested action."
    if (
        "capacity_blocked" in lowered
        or "provider-capacity policy" in lowered
        or ("requires_local_provider" in lowered and "capacity_state" in lowered)
    ):
        return "I couldn't run that bounded worker step because the available provider lane did not meet the task's local execution requirements."
    if "has no child envelopes" in lowered or "has no runtime tool steps" in lowered:
        return "I couldn't continue that bounded run because it did not contain executable steps."
    if "failed to merge child results" in lowered:
        return "I couldn't merge the worker results into a clean final answer."
    if (
        "routing_requirements" in lowered
        or "rejected_candidates" in lowered
        or "selection_notes" in lowered
        or "provider_capability_truth" in lowered
    ):
        if response_class in {agent.ResponseClass.TASK_FAILED_USER_SAFE, agent.ResponseClass.SYSTEM_ERROR_USER_SAFE}:
            return "I couldn't find a provider lane that satisfied the task's routing and execution requirements cleanly."
        return "I finished the work and stripped the internal routing details from the reply."
    if "capacity_backoff_applied" in lowered or "skipped_saturated_candidates" in lowered or "reduced_to_single_degraded_lane" in lowered:
        return "I finished the work using the least-busy available provider lane."
    if "completed merge" in lowered or (" envelope" in lowered and "completed" in lowered):
        return "I finished the bounded multi-step run."
    if response_class == agent.ResponseClass.UTILITY_ANSWER:
        return "I couldn't surface that utility result cleanly."
    if response_class in {agent.ResponseClass.TASK_FAILED_USER_SAFE, agent.ResponseClass.SYSTEM_ERROR_USER_SAFE}:
        return "I couldn't complete that bounded multi-step run cleanly."
    return "I finished the work, but I'm stripping internal orchestration details from the reply."


def _strip_orchestration_fragments(text: str) -> str:
    """Remove internal orchestration fragments while preserving adjacent user-facing output."""
    safe_paragraphs: list[str] = []
    marker_pattern = re.compile(
        "|".join(re.escape(marker) for marker in _ORCHESTRATION_FRAGMENT_MARKERS),
        flags=re.IGNORECASE,
    )
    for paragraph in re.split(r"\n\s*\n", str(text or "").strip()):
        clean_paragraph = paragraph.strip()
        if not clean_paragraph:
            continue
        lowered = clean_paragraph.lower()
        if not marker_pattern.search(lowered):
            safe_paragraphs.append(clean_paragraph)
            continue
        if clean_paragraph.startswith("```"):
            continue
        lines: list[str] = []
        for line in clean_paragraph.splitlines():
            line = _strip_marked_structured_fragments(line, marker_pattern).strip()
            match = marker_pattern.search(line)
            if match is None:
                if line:
                    lines.append(line)
                continue
            safe_sentences = [
                sentence.strip()
                for sentence in re.split(r"(?<=[.!?])\s+", line)
                if sentence.strip() and not marker_pattern.search(sentence)
            ]
            if safe_sentences:
                lines.append(" ".join(safe_sentences))
        lines = [line for line in lines if not line.strip().startswith("```")]
        residual = "\n".join(lines).strip()
        if residual and not marker_pattern.search(residual):
            safe_paragraphs.append(residual)
    return "\n\n".join(safe_paragraphs).strip()


def _strip_marked_structured_fragments(text: str, marker_pattern: re.Pattern[str]) -> str:
    """Remove balanced JSON-like fragments containing orchestration markers."""
    chars = str(text or "")
    output: list[str] = []
    index = 0
    while index < len(chars):
        if chars[index] not in "{[":
            output.append(chars[index])
            index += 1
            continue
        start = index
        stack = [chars[index]]
        index += 1
        in_string = False
        escaped = False
        while index < len(chars) and stack:
            char = chars[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
            elif char in "{[":
                stack.append(char)
            elif char in "}]" and ((stack[-1] == "{" and char == "}") or (stack[-1] == "[" and char == "]")):
                stack.pop()
            index += 1
        if stack:
            output.append(chars[start:])
            break
        fragment = chars[start:index]
        if not marker_pattern.search(fragment):
            output.append(fragment)
    return re.sub(r"[ \t]{2,}", " ", "".join(output)).strip()


def contains_generic_planner_scaffold(agent: Any, text: str) -> bool:
    clean = agent._unwrap_summary_or_action_payload(str(text or "").strip())
    if not clean:
        return False
    generic_lines = {"review problem", "choose safe next step", "validate result"}
    normalized_lines: list[str] = []
    for raw_line in clean.splitlines():
        line = re.sub(r"^[\-\*\d\.\)\s]+", "", raw_line).strip().lower()
        line = re.sub(r"[.!?]+$", "", line).strip()
        if line:
            normalized_lines.append(line)
    if not normalized_lines:
        return False
    unique_lines = set(normalized_lines)
    return len(unique_lines) >= 2 and unique_lines.issubset(generic_lines)


def unwrap_summary_or_action_payload(text: str) -> str:
    raw = str(text or "").strip()
    if not (raw.startswith("{") and raw.endswith("}")):
        return raw
    try:
        payload = json.loads(raw)
    except Exception:
        return raw
    if not isinstance(payload, dict):
        return raw

    summary = str(payload.get("summary") or payload.get("message") or "").strip()
    bullet_source = payload.get("bullets") or payload.get("steps") or []
    bullets = [str(item).strip() for item in list(bullet_source) if str(item).strip()]
    lines: list[str] = []
    if summary:
        lines.append(summary)
    lines.extend(f"- {item}" for item in bullets[:6])
    return "\n".join(line for line in lines if line.strip()) or raw
