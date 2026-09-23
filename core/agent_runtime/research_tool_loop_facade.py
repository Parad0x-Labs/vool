from __future__ import annotations

import json
from contextlib import suppress
from typing import Any

from core import audit_logger, policy_engine
from core.agent_runtime.orchestrator import (
    redact_tool_arguments as redact_tool_argument_data,
)
from core.agent_runtime.orchestrator import (
    synthesis_echoes_prior_reply,
)
from core.candidate_knowledge_lane import get_candidate_by_id
from core.code_assistant.task_runtime import enforce_code_task_completion
from core.curiosity_roamer import AdaptiveResearchResult
from core.execution.planner import (
    has_explicit_tool_intent_request,
    is_conversational_memory_declaration,
)
from core.memory_first_router import apply_verifier_draft_caveat
from core.mode_permission_policy import (
    PENDING_BATCH_CALLS_KEY,
    request_batch_grant_covers,
)
from core.model_output_guard import claims_pending_tool, foreign_markers, is_ungrounded
from core.remote_fetch_policy import explicit_remote_fetch_disabled
from core.retrieval_constraints import analyze_retrieval_request_authority
from core.retrieval_observability import begin_web_retrieval, finish_web_retrieval
from core.semantic import reach as semantic_reach
from core.task_router import looks_like_explicit_lookup_request, looks_like_public_entity_lookup_request
from core.tool_arg_redaction import redact_tool_arguments as summarize_tool_arguments
from core.tool_argument_aliases import side_effect_class_for_intent

# Read-only deterministic tools whose text output IS the answer: render it directly and
# skip the model-synthesis pass entirely (one result, one user-facing answer; no chance
# for a degraded model lane to paraphrase or parrot the transcript).
_DIRECT_RENDER_TOOL_INTENTS = frozenset(
    {
        "machine.disk_usage",
        "machine.inspect_specs",
        "machine.list_directory",
        "machine.list_processes",
        "machine.event_log_errors",
        "machine.find_folder",
        # workspace.identity answers "which workspace is active" in one finished sentence, so the
        # tool text IS the answer. Without this the synthesis pass rejected the model for echoing
        # it (echo_rejected) and the user got "Incomplete — model synthesis failed" above a
        # perfectly correct result, or worse, the model waffled: "I can confirm the current
        # workspace folder, but I need to check the active directory first."
        "workspace.identity",
        # wallet: the proposal id, state and signature ARE the answer; a paraphrase would invent them.
        "wallet.status",
        "wallet.propose",
        "wallet.simulate",
        "wallet.payment_status",
    }
)
_DIRECT_RENDER_SINGLE_STEP_WORKSPACE_INTENTS = frozenset(
    {
        "workspace.git_status",
        "workspace.git_summary",
        "workspace.list_files",
        "workspace.list_tree",
        "workspace.read_file",
        "workspace.search_text",
    }
)
# When the model closes a turn with respond.direct after exactly ONE deterministic read, the tool's
# own text replaces the model's paraphrase. This used to cover workspace.* only, so a
# machine.list_directory turn returned the paraphrase -- and that is where the fabricated listings
# came from: a real listing of one folder was re-headed with the folder the user had asked about, and
# in one measured case the paraphrase invented "index.html, styles.css" for a directory that holds
# README.md, pollen.py, requirements.txt and samples.csv. Every intent in
# _DIRECT_RENDER_TOOL_INTENTS is already declared "the tool text IS the answer"; they simply were not
# consulted on this early-return path.
_DIRECT_RENDER_SINGLE_STEP_SUBSTITUTION_INTENTS = (
    _DIRECT_RENDER_SINGLE_STEP_WORKSPACE_INTENTS | _DIRECT_RENDER_TOOL_INTENTS
)



# How many members of one native batch this loop will run before handing back to the model. A red
# team executed 200 tools in a single round against an uncapped build; the model is not the thing
# bounding this, so the runtime has to. Which limit it serves (CLAUDE.md 4b): WALL CLOCK and
# provider rate limit, not tokens - every member here is a local tool call costing no model output.
# Overflow defers and is reported, so being wrong costs a round-trip and nothing else.
_MAX_BATCH_MEMBERS_PER_ROUND = 8

# How many times ONE turn may go back to the model. Which limit it serves (CLAUDE.md 4b): WALL
# CLOCK, and nothing else - each round is one provider round-trip bounded by the provider read
# timeout, so this number is the turn's worst-case duration divided by that timeout. It is not a
# token budget: rounds cost no output tokens of their own.
#
# It used to read `12 if explicit_model_pin else 5`, keyed on `source_context["requested_model"]` -
# whether the operator happened to pin a model in the composer. Measured 2026-08-03 on one
# identical 11-file script: unpinned ran 5 rounds and returned "Incomplete - the 5-round tool budget
# was exhausted"; pinned ran 12 rounds and answered. Same task, same tools, same model. A UI
# selector state is neither a wall-clock fact nor a provider fact, so it cannot be what decides how
# much work a turn is allowed to do - that is the capability difference CLAUDE.md section 1
# prohibits ("reasoning budgets restricted without cause"). One number now, for every model.
_MAX_MODEL_ROUNDS_PER_TURN = 12


def _semantic_payload_signature(payload: Any) -> str:
    """The identity of a tool call for the duplicate guard: intent + arguments, transport stripped.

    Provider-native call IDs are transport metadata, not semantics - two calls that differ only by
    `_native_tool_call_id` are the same request. The head of a batch and an inline member must
    produce the SAME string for the same call, or a member that already ran is invisible to the
    guard and the model gets to re-request it for free (measured: loader.py read twice in one turn,
    three steps for two files).
    """

    try:
        semantic = {
            key: value
            for key, value in dict(payload or {}).items()
            if not str(key).startswith("_native_")
        }
        arguments = semantic.get("arguments")
        if isinstance(arguments, dict):
            normalized = dict(arguments)
            for key in ("query", "queries", "q", "search"):
                value = normalized.get(key)
                # A free-text QUERY's identity is its bag of tokens, not their order. Measured on
                # the served surface, 2026-08-15: one turn ran the same ticket search three times
                # as "weekend prices 2026 August", "weekend prices August 2026" and "August 2026
                # weekend" -- each shuffle evaded this guard and each bought a 16-18k-token model
                # round before an exact repeat finally tripped it. Search engines are order-
                # insensitive; the guard should be too. Only query-ish keys are normalized: a
                # path or command is order-sensitive and stays exact.
                if isinstance(value, str):
                    normalized[key] = " ".join(sorted(set(value.casefold().split())))
                elif isinstance(value, list):
                    normalized[key] = [
                        " ".join(sorted(set(item.casefold().split())))
                        if isinstance(item, str)
                        else item
                        for item in value
                    ]
            semantic["arguments"] = normalized
        return json.dumps(semantic, sort_keys=True, ensure_ascii=True, default=str)
    except Exception:
        return str(payload)


def _describe_batch_member(member: dict[str, Any]) -> str:
    """`workspace.read_file(Cargo.toml)` — a deferred call the model and the operator can act on.

    Five deferred reads reported by name alone render as the same word five times, with the paths
    discarded. That is the defect this whole change is about, one level down.
    """

    intent = str(member.get("intent") or "tool")
    arguments = member.get("arguments") or {}
    for key in ("path", "file", "query", "glob", "command", "target"):
        value = str(arguments.get(key) or "").strip()
        if value:
            return f"{intent}({value[:60]})"
    return intent


def resumable_pending_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """The pending tool call as it must be stored to be RE-EXECUTED later, not merely displayed.

    ``redact_tool_argument_data`` is the right function for an event or a summary and the wrong one
    for this slot: it clips every string at 1000 characters. The pending payload is what a resumed
    turn actually runs, so storing the clipped copy meant an approved `workspace.write_file` of a
    3 KB file resumed by writing the first 1000 bytes and a literal "..." -- content the operator
    never saw and never approved -- and, because the approval fingerprint is byte-exact on content,
    the same clipped call also missed the grant and raised a SECOND prompt for the write that had
    just been allowed.

    Secret-named arguments are still masked, on the same key set, because a masked secret is not
    something a resume should reproduce either. Everything else is kept byte-exact.
    """
    from core.agent_runtime.orchestrator import _SENSITIVE_TOOL_ARGUMENT_KEYS

    if not isinstance(payload, dict):
        return {}
    arguments = payload.get("arguments")
    if not isinstance(arguments, dict):
        return dict(payload)
    masked: dict[str, Any] = {}
    for key, value in arguments.items():
        normalized = str(key or "").strip().lower().replace("-", "_")
        if normalized in _SENSITIVE_TOOL_ARGUMENT_KEYS or any(
            marker in normalized
            for marker in ("api_key", "authorization", "password", "private_key", "secret", "token")
        ):
            masked[str(key)] = "[redacted]"
        else:
            masked[str(key)] = value
    return {**payload, "arguments": masked}


def _batch_member_is_read_only(intent: str, arguments: dict[str, Any] | None = None) -> bool:
    """Whether a batch member may run inline, decided by DECLARED CAPABILITY and nothing else.

    A positive allow, never a blocklist. `execute-the-batch`, one of three rejected designs for
    this change, gated on `is_mutating_tool_intent` - a 15-name list that does not contain
    `workspace.run_tests`, `workspace.run_lint`, `machine.write_file`, `machine.ensure_directory`
    or `orchestration.execute_envelope`. `workspace.run_tests` returns the model's own `command`
    verbatim, so that build ran arbitrary local shell blind as a batch member; its red team
    measured a file overwritten with `DESTROYED`.

    Every tool contract declares `side_effect_class`. Measured 2026-08-03:

        workspace.read_file      read_only
        workspace.run_tests      validation_command
        workspace.write_file     workspace_write
        workspace.apply_patch    (undeclared)

    So requiring `read_only` excludes the dangerous ones by construction, and an UNDECLARED intent
    - the case a blocklist can never cover - is refused for free. A tool added tomorrow is safe by
    default and opts in by declaring itself.
    """

    clean = str(intent or "").strip()
    if clean == "code.task.step":
        # A step inherits the inner contract for scheduling only. It still executes
        # through the outer task door, with journal, argument and permission checks.
        envelope = arguments if isinstance(arguments, dict) else {}
        clean = str(envelope.get("intent") or "").strip()
        if clean.startswith("code.task.") or not isinstance(envelope.get("arguments", {}), dict):
            return False
    return side_effect_class_for_intent(clean) == "read_only"


# Intents that mean "no tool ran" — the set `tool_intent_direct_message` treats as a closing reply.
_NON_EXECUTING_INTENTS = {"respond.direct", "none", "no_tool"}


def _code_task_report_completed_the_turn(executed_steps: list[dict[str, Any]]) -> bool:
    """Whether this turn's last real step published a completed code task report and no coding task the
    turn worked on is unfinished (the completion law in `enforce_code_task_completion`)."""
    real = [step for step in executed_steps if isinstance(step, dict) and not step.get("correction")]
    if not real or str(real[-1].get("tool_name") or "") != "code.task.report":
        return False
    details = dict(real[-1].get("details") or {})
    if str(details.get("verdict") or "") != "completed":
        return False
    verdict = enforce_code_task_completion({"response": "", "success": True}, executed_steps)
    return not dict(verdict.get("details") or {}).get("unfinished_code_tasks")


def _first_executable_batch_member(tool_decision: Any) -> dict[str, Any] | None:
    """The first member of a native batch that actually does something, as a `structured_output`.

    Returns None when the reply is a plain closing message — the ordinary case, which must keep
    returning through the direct path untouched.
    """

    calls = tuple(getattr(tool_decision, "tool_calls", ()) or ())
    for call in calls:
        intent = str(getattr(call, "intent", "") or "").strip()
        if not intent or intent.lower() in _NON_EXECUTING_INTENTS:
            continue
        arguments = getattr(call, "arguments", None)
        return {"intent": intent, "arguments": dict(arguments) if isinstance(arguments, dict) else {}}
    return None


#: Per-row source fields a `web.search` step's details carry for each result it returned.
#: Binding one note per STEP collapses every source into "the tool" -- measured served: two
#: distinct domains came back and the claim map read `{'note-1': 'web.search'}`, so a
#: claim's supporting sources named no source at all. The rows are there, per result; the
#: projection just never read them.
_RESULT_ROW_FIELDS = ("title", "url", "snippet", "origin_domain", "domain")


def _structured_result_rows(step: dict[str, Any]) -> list[dict[str, Any]]:
    """The per-result rows a successful web step's own details carry, in note shape."""
    details = step.get("details")
    if not isinstance(details, dict):
        return []
    rows: list[dict[str, Any]] = []
    for item in list(details.get("results") or []):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        domain = str(item.get("origin_domain") or item.get("domain") or "").strip()
        if not (title or snippet):
            continue
        rows.append(
            {
                "summary": snippet or title,
                "result_title": title,
                "result_url": url,
                "origin_domain": domain or (url.split("//")[-1].split("/")[0] if url else ""),
                "source_type": "web_derived",
                "ok": True,
            }
        )
    return rows


def _tool_step_evidence_rows(executed_steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """This loop's SUCCESSFUL tool steps, projected into the note shape M2's binder takes.

    A projection, not an extraction: every value is a field the step already carries. A
    step that reports structured RESULT ROWS (a web search) binds ONE NOTE PER SOURCE --
    each with its own domain, url and content -- so a claim's supporting sources name the
    sources. A step without structured rows falls back to its own `summary` plus its
    `response_text`/`observation` rendered to text, bounded, which is what
    `core.claim_support` reads through the same content fields it reads every other
    source through. A step that reports its own failure contributes nothing, by the same
    rule `core.observation_evidence` applies everywhere else: an attempt is not an
    observation.
    """

    rows: list[dict[str, Any]] = []
    for step in list(executed_steps or []):
        if not isinstance(step, dict):
            continue
        if str(step.get("mode") or "") != "tool_executed" or step.get("ok") is False:
            continue
        structured = _structured_result_rows(step)
        if structured:
            query = str((step.get("arguments") or {}).get("query") or "").strip()
            for row in structured:
                row["search_provider"] = str(step.get("tool_name") or "")
                if query:
                    # Demand identity: the query IS the demand a research step serves.
                    row["demand_text"] = query[:200]
            rows.extend(structured)
            continue
        summary = str(step.get("summary") or "").strip()
        body = str(step.get("response_text") or "").strip()
        if not body:
            observation = step.get("observation")
            try:
                body = (
                    json.dumps(observation, ensure_ascii=False)
                    if isinstance(observation, (dict, list)) and observation
                    else str(observation or "")
                )
            except Exception:
                body = ""
        if not body.strip():
            # A step with a label and no body binds nothing. Binding it would present a
            # one-line rendering as the turn's evidence, and every claim would then read
            # unsupported against a source set that is not the one the model saw.
            continue
        rows.append(
            {
                "summary": summary,
                "snippet": body[:4000],
                "origin_domain": str(step.get("tool_name") or ""),
                "search_provider": str(step.get("tool_name") or ""),
                "source_type": "tool_result",
                "ok": True,
            }
        )
    return rows

#: Executor refusals made BEFORE any tool owner ran: nothing executed, so the evidence-required
#: fallthrough to the research path still applies (the research lanes may retrieve on their own).
_PRE_OWNER_FAILURE_STATUSES = frozenset({"unsupported", "mcp_server_not_configured", "mcp_server_unavailable"})
def _within_turn_navigation(loop: Any) -> Any:
    """Run the model tool loop inside its turn's navigation scope.

    The loop owns the turn's model rounds, so it owns the turn's navigation state (core.tool_offer_state):
    a family the model expands stays in every later round's offer while the loop runs, and the scope is
    closed on every exit -- an answer, a pause for approval, a failure or a cancellation. A retry, a resumed
    approval or a reused task id therefore starts from its own state and never inherits another request's
    expansions. The scope wraps the loop's entry point itself, so every caller gets it and the loop asks
    nothing more of its host than it did before.
    """
    import functools

    @functools.wraps(loop)
    def run_in_turn_scope(self: Any, **arguments: Any) -> Any:
        from core.tool_offer_state import begin_turn_navigation, end_turn_navigation

        source_context = arguments.get("source_context")
        scope = begin_turn_navigation(source_context)
        try:
            return loop(self, **arguments)
        finally:
            end_turn_navigation(source_context, scope)

    return run_in_turn_scope


class ResearchToolLoopFacadeMixin:
    def _collect_live_web_notes(
        self,
        *,
        task_id: str,
        query_text: str,
        classification: dict[str, Any],
        interpretation: Any,
        source_context: dict[str, object] | None,
    ) -> list[dict[str, Any]]:
        if not policy_engine.allow_web_fallback():
            return []
        # The CALLER's text, captured before the next line rebinds `query_text` to the
        # prohibition-stripped candidate. The canonical record is keyed on the turn's own text, so
        # handing the door a rewritten string is how a lane mints a SECOND record nobody
        # downstream reads: the scheduler would escalate one decision while the current-claim
        # guard reads another, which is precisely the silent disagreement the M1 freeze exists to
        # make impossible.
        authority_text = " ".join(str(query_text or "").split()).strip()
        request_authority = analyze_retrieval_request_authority(query_text)
        query_text = " ".join(str(request_authority.candidate_text or "").split()).strip()
        if request_authority.constraints.forbids_external_retrieval:
            return []
        if not query_text or (
            request_authority.constraints.has_prohibition
            and request_authority.constraints.forbids_candidate(query_text)
        ):
            return []
        source_context = source_context if isinstance(source_context, dict) else {}
        if explicit_remote_fetch_disabled(source_context):
            return []
        surface = str(source_context.get("surface", "") or "").lower()
        platform = str(source_context.get("platform", "") or "").lower()
        explicit_remote_policy = "allow_remote_fetch" in source_context
        allow_remote_fetch = bool(source_context.get("allow_remote_fetch", False))
        trusted_live_surface = (
            surface in {"channel", "openclaw", "api"}
            or platform in {"openclaw", "web_companion", "telegram", "discord"}
        )
        # Explicit false is an operator/privacy boundary; trusted surfaces only default
        # to live research when the caller omits the remote-fetch policy entirely.
        remote_fetch_allowed = allow_remote_fetch if explicit_remote_policy else trusted_live_surface
        if not remote_fetch_allowed:
            return []

        task_class = str(classification.get("task_class", "unknown"))
        wants_fresh_info = self._wants_fresh_info(query_text, interpretation=interpretation)
        wants_live_lookup = task_class in {"research", "system_design", "integration_orchestration"}
        if not wants_fresh_info:
            return []
        # M2b -- the canonical door, between this lane's PROPOSAL and any retrieval.
        #
        # `_wants_fresh_info` is a private recognizer and stays one: it proposes. What it may
        # not do any more is BE the decision. Before the synthesis freeze the authority
        # escalates on this claim -- the frozen record widens and gains a
        # `current_info_signal:lane:reasoning_fallback_search` code naming this lane -- so the
        # decision that scheduled the search is the same decision every downstream guard reads.
        # After the freeze it fails closed, which is what stops the post-answer retrieval that
        # made an already-written fabrication look sourced.
        #
        # Placed ABOVE `begin_web_retrieval` so it covers BOTH branches below (the planned
        # search and the plain one): they share this single receipt, so gating the receipt gates
        # the pair. A refusal is recorded as a typed row rather than a silent empty list.
        from core.retrieval_authority_gate import authorize_retrieval

        if not authorize_retrieval(
            source_context,
            authority_text,
            lane="reasoning_fallback_search",
            proposal_reason="_wants_fresh_info",
        ):
            return []
        receipt = begin_web_retrieval(
            source_context,
            kind="reasoning_fallback_search",
            query=query_text,
            task_id=task_id,
            action="fallback_search",
        )
        try:
            notes: list[dict[str, Any]] = []
            if wants_live_lookup:
                notes = self._planned_search_query(
                    query_text,
                    task_id=task_id,
                    limit=3,
                    task_class=task_class,
                    topic_hints=list(getattr(interpretation, "topic_hints", []) or []),
                    source_label="duckduckgo.com",
                )
                if notes:
                    finish_web_retrieval(source_context, receipt, notes=notes)
                    return notes
            # THE ALTERNATE BRANCH, and the reason it needs its own door. A planned search that
            # came back empty falls THROUGH to a second, DIFFERENT transport under the receipt
            # minted above -- one authorization, two fetches. The unit this invariant protects is
            # a TRANSPORT, not a receipt, so the second one is asked for separately rather than
            # inheriting the planned branch's answer.
            #
            # On refusal the open receipt is finished as REFUSED rather than left to close as an
            # empty result: `refused` is a policy fact and `unavailable` is a provider that had
            # nothing, and closing a denial as the latter is what lets a turn answer from weights
            # behind a green-looking row.
            if not authorize_retrieval(
                source_context,
                authority_text,
                lane="reasoning_fallback_search",
                proposal_reason="planned_search_empty_fallthrough",
                query=str(query_text or ""),
            ):
                finish_web_retrieval(source_context, receipt, refused=True)
                return []
            notes = self._search_query(
                query_text,
                task_id=task_id,
                limit=3,
                source_label="duckduckgo.com",
            )
            finish_web_retrieval(source_context, receipt, notes=notes)
            return notes
        except Exception as exc:
            finish_web_retrieval(source_context, receipt, failure=exc)
            audit_logger.log(
                "agent_live_web_lookup_error",
                target_id=task_id,
                target_type="task",
                details={"error": str(exc)},
            )
            return []

    def _collect_adaptive_research(
        self,
        *,
        task_id: str,
        query_text: str,
        classification: dict[str, Any],
        interpretation: Any,
        source_context: dict[str, object] | None,
        skip_for_local_answer: bool = False,
    ) -> AdaptiveResearchResult:
        from core.agent_runtime.intent_claims import (
            ActionPolicy,
            action_policy_from_context,
        )

        if action_policy_from_context(source_context) is ActionPolicy.FORBIDDEN:
            return AdaptiveResearchResult(
                enabled=False,
                reason="action_policy_forbidden",
                strategy="not_allowed",
            )
        if explicit_remote_fetch_disabled(source_context):
            return AdaptiveResearchResult(
                enabled=False,
                reason="remote_fetch_disabled",
                strategy="not_allowed",
            )
        # A workspace audit is a question about code ON THIS MACHINE. Its evidence has already been
        # read off disk, so a live web lookup can add nothing the answer needs — and it can add
        # something the answer must not have: an operator who says "use only what is on this
        # machine" has stated a permission, and adaptive research is a network access. The audit's
        # normalized execution policy owns that axis (core/agent_runtime/audit_policy.py); this is
        # where it is enforced for the research lane.
        if bool((source_context or {}).get("workspace_audit_evidence_collected")):
            from core.agent_runtime.audit_policy import AuditExecutionPolicy

            policy = (source_context or {}).get("audit_execution_policy")
            allowed = bool(dict(policy).get("network_research")) if isinstance(policy, dict) else (
                AuditExecutionPolicy().network_research
            )
            if not allowed:
                return AdaptiveResearchResult(
                    enabled=False,
                    reason="local_workspace_audit_no_research",
                    strategy="not_allowed",
                )
        # A high-confidence local/memory/exact-recall/personal question is answered from local
        # state; adaptive research (a live web search) must not run before it.
        if skip_for_local_answer:
            return AdaptiveResearchResult(
                enabled=False,
                reason="local_answer_no_research",
                strategy="not_needed",
            )
        # Adaptive research is enrichment, not a freshness recognizer: its ask widens the turn
        # PROVISIONALLY (core.execution_requirements.PROVISIONAL_ESCALATION_SOURCES). A roam that
        # finds evidence is bound and gated like any current turn; a roam that finds nothing
        # retracts the widening before synthesis, so a stable-knowledge question answered from
        # the model is never refused for lacking support that was never found.
        try:
            return self.curiosity.adaptive_research(
                task_id=task_id,
                user_input=query_text,
                classification=classification,
                interpretation=interpretation,
                source_context=source_context if isinstance(source_context, dict) else {},
            )
        except Exception as exc:
            audit_logger.log(
                "adaptive_research_error",
                target_id=task_id,
                target_type="task",
                details={"error": str(exc)},
            )
            return AdaptiveResearchResult(
                enabled=False,
                reason="controller_error",
                strategy="tool_gap",
                tool_gap_note="Adaptive research failed for this turn, so I should stay cautious about unsupported claims.",
                admitted_uncertainty=True,
                uncertainty_reason="Adaptive research failed for this turn.",
            )

    def _should_frontload_curiosity(
        self,
        *,
        query_text: str,
        classification: dict[str, Any],
        interpretation: Any,
    ) -> bool:
        task_class = str(classification.get("task_class", "unknown"))
        # A request that demands current evidence must NOT be pre-loaded with the curiosity
        # roamer's own research. Those candidates are about whatever the roamer was curious about,
        # and `turn_reasoning` merges them into `context_snippets` -- so the model receives them as
        # THIS turn's grounding observations.
        #
        # That is the source of every "irrelevant sources" report in this work, measured across
        # three separate prompts on 2026-08-05/06:
        #
        #   aviation  -> "Apple/Android developer docs, GitHub"
        #   EV        -> "calendar software, project management trade-offs, Wikipedia meta-pages"
        #   aviation  -> "The GitHub link mentions the Boeing 737's dominance"
        #
        # The model was not inventing those. It was accurately describing what it was handed, which
        # is why the earlier "fabricated tool history" reading was wrong -- and why this looked like
        # cross-turn contamination when no cache or leak existed.
        #
        # Frontloading made sense when a research turn could not reach a tool. It now can (see the
        # routing chain fixed in 203bc97d / 1f573145 / 81cff485), so the crutch only poisons the
        # evidence set. `answer_mode_for` is the same discriminator the lane policy uses, keeping
        # one owner for "does this request demand evidence".
        from core.execution_requirements import requirements_for

        if requirements_for(query_text, task_class=task_class).external_evidence_required:
            return False
        if task_class in {"research", "system_design"}:
            return True
        if task_class != "integration_orchestration":
            return False
        lowered = str(query_text or "").lower()
        if any(
            marker in lowered
            for marker in (
                "build",
                "design",
                "architecture",
                "best practice",
                "best practices",
                "framework",
                "stack",
                "github",
                "repo",
                "repos",
                "docs",
                "documentation",
            )
        ):
            return True
        topic_hints = {str(item).lower() for item in getattr(interpretation, "topic_hints", []) or []}
        return bool({"telegram bot", "discord bot"} & topic_hints)

    def _curiosity_candidate_evidence(self, candidate_ids: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        plan_candidates: list[dict[str, Any]] = []
        context_snippets: list[dict[str, Any]] = []
        for candidate_id in list(candidate_ids or [])[:3]:
            candidate = get_candidate_by_id(candidate_id)
            if not candidate:
                continue
            structured = dict(candidate.get("structured_output") or {})
            metadata = dict(candidate.get("metadata") or {})
            snippets = [dict(item) for item in list(structured.get("snippets") or []) if isinstance(item, dict)]
            topic = str(structured.get("topic") or metadata.get("curiosity_topic") or "technical research").strip()
            topic_kind = str(structured.get("topic_kind") or "technical").strip().lower() or "technical"
            score = self._curiosity_candidate_score(candidate=candidate, snippets=snippets)
            summary = self._curiosity_candidate_summary(
                topic=topic,
                topic_kind=topic_kind,
                snippets=snippets,
                fallback_text=str(candidate.get("normalized_output") or candidate.get("raw_output") or ""),
            )
            plan_candidates.append(
                {
                    "summary": summary,
                    "resolution_pattern": self._curiosity_candidate_steps(topic_kind=topic_kind, snippets=snippets),
                    "score": score,
                    "source_type": "curiosity_candidate",
                    "source_node_id": "curiosity_roamer",
                    "provider_name": "curiosity_roamer",
                    "model_name": str(candidate.get("model_name") or "bounded_web_research"),
                    "candidate_id": candidate_id,
                }
            )
            for index, snippet in enumerate(snippets[:4], start=1):
                snippet_summary = " ".join(str(snippet.get("summary") or "").split()).strip()
                if not snippet_summary:
                    continue
                label = str(
                    snippet.get("source_profile_label")
                    or snippet.get("origin_domain")
                    or snippet.get("source_label")
                    or "curated source"
                ).strip()
                context_snippets.append(
                    {
                        "title": f"{label} note {index}",
                        "source_type": "curiosity_research",
                        "summary": snippet_summary[:320],
                        "confidence": score,
                        "priority": score,
                        "metadata": {
                            "origin_domain": snippet.get("origin_domain"),
                            "result_url": snippet.get("result_url"),
                            "source_profile_id": snippet.get("source_profile_id"),
                            "created_at": candidate.get("created_at"),
                            "candidate_id": candidate_id,
                        },
                    }
                )
        return plan_candidates, context_snippets

    def _curiosity_candidate_summary(
        self,
        *,
        topic: str,
        topic_kind: str,
        snippets: list[dict[str, Any]],
        fallback_text: str,
    ) -> str:
        clean_topic = " ".join(str(topic or "").split()).strip() or "this topic"
        labels = {
            str(snippet.get("source_profile_label") or snippet.get("source_profile_id") or "").strip().lower()
            for snippet in snippets
        }
        domains = {
            str(snippet.get("origin_domain") or "").strip().lower()
            for snippet in snippets
            if str(snippet.get("origin_domain") or "").strip()
        }
        official_docs = bool({"official docs", "messaging platform docs"} & labels) or bool(
            domains & {"core.telegram.org", "discord.com", "docs.python.org", "developer.mozilla.org"}
        )
        repo_examples = "reputable repositories" in labels or "github.com" in domains

        lead = f"Research brief for {clean_topic}:"
        if topic_kind in {"technical", "integration"} and official_docs and repo_examples:
            lead = f"For {clean_topic}, start with official docs first and use reputable GitHub repos as implementation references."
        elif official_docs:
            lead = f"For {clean_topic}, anchor the answer on official documentation before applying examples."
        elif repo_examples:
            lead = f"For {clean_topic}, compare a few reputable GitHub implementations before locking the design."

        highlights = [
            " ".join(str(snippet.get("summary") or "").split()).strip().rstrip(".")
            for snippet in snippets[:2]
            if str(snippet.get("summary") or "").strip()
        ]
        if highlights:
            return f"{lead} {' '.join(highlights)}"[:420]
        clean_fallback = " ".join(str(fallback_text or "").split()).strip()
        if clean_fallback:
            return f"{lead} {clean_fallback}"[:420]
        return lead[:420]

    def _curiosity_candidate_steps(self, *, topic_kind: str, snippets: list[dict[str, Any]]) -> list[str]:
        labels = {
            str(snippet.get("source_profile_label") or snippet.get("source_profile_id") or "").strip().lower()
            for snippet in snippets
        }
        domains = {
            str(snippet.get("origin_domain") or "").strip().lower()
            for snippet in snippets
            if str(snippet.get("origin_domain") or "").strip()
        }
        steps: list[str] = []
        if {"official docs", "messaging platform docs"} & labels or domains & {"core.telegram.org", "discord.com"}:
            steps.append("review_official_platform_docs")
        if "github.com" in domains or "reputable repositories" in labels:
            steps.append("compare_reputable_repo_examples")
        if topic_kind in {"technical", "integration"}:
            steps.extend(["define_minimal_architecture", "validate_auth_limits_and_deployment_constraints"])
        elif topic_kind == "design":
            steps.extend(["compare_reference_patterns", "shape_minimal_user_flow"])
        elif topic_kind == "news":
            steps.extend(["compare_multiple_reputable_sources", "separate_verified_facts_from_speculation"])
        if not steps:
            steps.append("summarize_grounded_findings")
        deduped: list[str] = []
        seen: set[str] = set()
        for step in steps:
            if step in seen:
                continue
            seen.add(step)
            deduped.append(step)
        return deduped[:4]

    def _curiosity_candidate_score(self, *, candidate: dict[str, Any], snippets: list[dict[str, Any]]) -> float:
        score = float(candidate.get("trust_score") or candidate.get("confidence") or 0.0)
        labels = {
            str(snippet.get("source_profile_label") or snippet.get("source_profile_id") or "").strip().lower()
            for snippet in snippets
        }
        domains = {
            str(snippet.get("origin_domain") or "").strip().lower()
            for snippet in snippets
            if str(snippet.get("origin_domain") or "").strip()
        }
        if {"official docs", "messaging platform docs"} & labels or domains & {"core.telegram.org", "discord.com"}:
            score += 0.08
        if "github.com" in domains or "reputable repositories" in labels:
            score += 0.05
        if len(domains) >= 2:
            score += 0.03
        return max(0.50, min(0.90, score))

    def _web_note_plan_candidates(
        self,
        *,
        query_text: str,
        classification: dict[str, Any],
        web_notes: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        notes = [dict(note) for note in list(web_notes or []) if isinstance(note, dict)]
        if not notes:
            return []
        labels = {
            str(note.get("source_profile_label") or note.get("source_profile_id") or "").strip().lower()
            for note in notes
        }
        domains = {
            str(note.get("origin_domain") or "").strip().lower()
            for note in notes
            if str(note.get("origin_domain") or "").strip()
        }
        official_docs = bool({"official docs", "messaging platform docs"} & labels) or bool(
            domains & {"core.telegram.org", "discord.com", "docs.python.org", "developer.mozilla.org"}
        )
        repo_examples = "reputable repositories" in labels or "github.com" in domains
        topic = " ".join(str(query_text or "").split()).strip() or str(classification.get("task_class") or "research")
        lead = f"Research notes for {topic}:"
        if official_docs and repo_examples:
            lead = f"For {topic}, anchor the design on official docs first, then use reputable GitHub repos as implementation references."
        elif official_docs:
            lead = f"For {topic}, anchor the answer on official documentation."
        elif repo_examples:
            lead = f"For {topic}, compare reputable GitHub implementations before locking the design."
        highlights = [
            " ".join(str(note.get("summary") or "").split()).strip().rstrip(".")
            for note in notes[:2]
            if str(note.get("summary") or "").strip()
        ]
        steps: list[str] = []
        if official_docs:
            steps.append("review_official_docs")
        if repo_examples:
            steps.append("compare_reputable_repo_examples")
        if str(classification.get("task_class") or "") in {"system_design", "integration_orchestration"}:
            steps.extend(["define_minimal_architecture", "validate_runtime_constraints"])
        elif str(classification.get("task_class") or "") == "research":
            steps.extend(["compare_findings", "summarize_grounded_recommendation"])
        score = max(float(note.get("confidence") or 0.0) for note in notes)
        if official_docs:
            score += 0.08
        if repo_examples:
            score += 0.05
        summary = lead if not highlights else f"{lead} {' '.join(highlights)}"
        deduped_steps: list[str] = []
        seen_steps: set[str] = set()
        for step in steps:
            if step in seen_steps:
                continue
            seen_steps.add(step)
            deduped_steps.append(step)
        return [
            {
                "summary": summary[:420],
                "resolution_pattern": deduped_steps[:4] or ["summarize_grounded_findings"],
                "score": max(0.45, min(0.86, score)),
                "source_type": "planned_web_candidate",
                "source_node_id": "web_source_planner",
                "provider_name": "web_source_planner",
                "model_name": "source_ranked_web_notes",
            }
        ]

    # Failures the model can fix by rewriting its arguments. Everything else — a permission
    # refusal, a missing capability, a tool that genuinely errored — is a real answer about the
    # world and must reach the user, not be retried into a loop.
    _CORRECTABLE_STATUSES = frozenset(
        {
            "invalid_arguments",
            "invalid_argument_shape",
            "missing_argument",
            "unsupported_argument",
            "unsupported_arguments",
            # The open code task's typed guard for a call that bypasses its plane. Its refusal
            # text IS the correction -- "re-issue this as `code.task.step` with the same
            # arguments" for an evidence command, the proposal path for a mutation -- and it
            # rides the same per-turn correction budget as every argument-shaped refusal.
            # Measured on the pinned base: without this seat the refused call ended the turn
            # with the guidance stillborn, exactly the dead end the native coding runs hit.
            # `owner_not_read` and the stage machine's `stage_violation` carry the same shape:
            # each names the exact act and stage that unlocks the next step, so a model that
            # steps out of order gets the recovery instead of a dead turn.
            "code_task_control_required",
            "owner_not_read",
            "stage_violation",
        }
    )
    _MAX_ARGUMENT_CORRECTIONS = 2
    #: Refusals by an open code task's own control plane that name the lawful next act: the reviewed
    #: base changed (re-read, propose again), a landed repair's checkpoint is not validated yet (run its
    #: checks), a repair unit is part-applied or not wholly approved, an approval already ran or is
    #: running, a proposal id already records a different request, the evidence a PR description needs
    #: is missing. Each is decided from the task journal before anything changes, so it is a correction
    #: the model can act on, not an answer to end the turn with -- measured in the served coding journeys:
    #: a stale-base or checkpoint refusal ended the turn and the recovery the refusal named never ran.
    #: Held to `code.task.*` intents (the same status word from any other tool keeps its meaning) and to
    #: the same per-turn correction budget as every argument-shaped refusal.
    _CODE_TASK_RECOVERABLE_STATUSES = frozenset(
        {
            "stale_base",
            "checkpoint_required",
            "unit_in_progress",
            "unit_not_approved",
            "unit_closed",
            "unit_duplicate_target",
            "approval_consumed",
            "approval_in_flight",
            "approval_mismatch",
            "approval_requires_review",
            "base_not_reviewed",
            "proposal_id_conflict",
            "step_id_conflict",
            "unbound_target",
            "unconfined_path",
            "not_a_mutation",
            "unknown_proposal",
            "insufficient_evidence",
        }
    )

    def _retry_as_observation(
        self,
        *,
        execution: Any,
        tool_payload: dict[str, Any],
        executed_steps: list[dict[str, Any]],
        loop_source_context: dict[str, Any],
        seen_tool_payloads: set[str],
    ) -> bool:
        """Hand a correctable argument error back to the model instead of ending the turn.

        Measured 2026-07-28 against a registered plugin tool: qwen3:8b chose the right tool on 2 of
        2 unseen phrasings and wrote correct JQL, then sent `limit="100"` where the schema declares
        an integer. The executor refused it with `limit: expected integer, got string ('100')`, and
        the turn died — on a mistake the model could have fixed in one step, with the fix already
        written in the error message.

        Deliberately not solved by coercing "100" to 100. A validator that quietly repairs its
        input has stopped being a validator, and the next wrong value will be one that cannot be
        repaired safely. The error was already right; it just had nowhere to go.

        Bounded twice over: only argument-shaped failures qualify, and only twice per turn, so a
        model that cannot satisfy a schema fails the turn rather than burning the step budget.
        """

        status = str(getattr(execution, "status", "") or "")
        intent = str(tool_payload.get("intent") or "unknown")
        task_plane_refusal = intent.startswith("code.task.") and status in self._CODE_TASK_RECOVERABLE_STATUSES
        if status not in self._CORRECTABLE_STATUSES and not task_plane_refusal:
            return False
        corrections = sum(1 for step in executed_steps if step.get("correction"))
        if corrections >= self._MAX_ARGUMENT_CORRECTIONS:
            return False

        detail = str(
            getattr(execution, "response_text", "")
            or (getattr(execution, "details", None) or {}).get("error")
            or "the arguments were rejected"
        ).strip()
        executed_steps.append(
            {
                "tool_name": intent,
                "correction": True,
                "ok": False,
                "status": status,
                # Phrased as an instruction because this text is what the model reads next.
                "observation": (
                    f"The call to {intent} was refused by the code task before anything changed: {detail} "
                    "Take the lawful next action it names."
                    if task_plane_refusal
                    else f"The call to {intent} was rejected before it ran: {detail} "
                    "Correct the arguments and call the tool again."
                ),
            }
        )
        # The rejected payload is already in seen_tool_payloads, which is what stops the model
        # simply resending it: an identical retry trips the repeated-request guard and the loop
        # moves to grounded synthesis rather than spinning.
        self._emit_runtime_event(
            loop_source_context,
            event_type="code_task_refusal_returned" if task_plane_refusal else "tool_argument_rejected",
            message=(
                f"{intent}: {detail} Returning the task's refusal to the model."
                if task_plane_refusal
                else f"{intent}: {detail} Asking the model to correct its arguments."
            ),
            tool_name=intent,
            status=status,
        )
        return True

    @_within_turn_navigation
    def _maybe_execute_model_tool_intent(
        self,
        *,
        task: Any,
        effective_input: str,
        classification: dict[str, Any],
        interpretation: Any,
        context_result: Any,
        persona: Any,
        session_id: str,
        source_context: dict[str, object] | None,
        surface: str,
    ) -> dict[str, Any] | None:
        from core.agent_runtime.intent_claims import (
            ActionPolicy,
            action_policy_from_context,
        )

        # A conversational no-action instruction is authoritative for the full
        # turn.  Do not ask a model to construct a tool payload only to reject it
        # later: that still produces a false action/approval status in the UI.
        if action_policy_from_context(source_context) is ActionPolicy.FORBIDDEN:
            return None
        # A bound workspace audit has already executed the authoritative read-only tool sequence
        # and attached its results as model evidence.  Running the generic tool-intent planner here
        # would ask the model to choose tools a second time, prepend the entire tool catalog, and
        # sometimes return that intermediate tool-plan response as the final audit verdict.  Keep
        # one owner per phase: deterministic audit tools collect evidence; the selected answer model
        # interprets it through the normal plain-text workspace_audit profile.
        if bool((source_context or {}).get("workspace_audit_evidence_collected")):
            return None
        checkpoint_id = self._runtime_checkpoint_id(source_context)
        checkpoint = self._get_runtime_checkpoint(checkpoint_id) if checkpoint_id else None
        checkpoint_state = dict((checkpoint or {}).get("state") or {})
        if self._should_keep_ai_first_chat_lane(
            user_input=effective_input,
            classification=classification,
            interpretation=interpretation,
            source_context=source_context,
            checkpoint_state=checkpoint_state,
        ):
            return None
        if self._should_run_builder_controller(
            effective_input=effective_input,
            classification=classification,
            source_context=dict(source_context or {}),
        ):
            # The wording gate is deliberately broad; the CLAIM authority is not. Skipping the
            # tool loop because the words look builder-shaped, when controller_profile then
            # declines the turn, strands a plain workspace request in a tools-less model lane:
            # nothing executes, nothing is written, and the turn still implies it would (the
            # pa_beta_gate file-edit matrix pinned this exact dead end). So consult the same
            # claim the dispatcher consults (turn_dispatch's _maybe_run_builder_controller ->
            # controller_profile): bypass only when the builder will actually take the turn.
            builder_claim: dict[str, Any] | None = None
            try:
                builder_claim = self._builder_controller_profile(
                    effective_input=effective_input,
                    classification=classification,
                    interpretation=interpretation,
                    source_context=dict(source_context or {}),
                )
            except Exception:
                builder_claim = None
            if builder_claim is not None and builder_claim.get("should_handle"):
                return None
            # else: fall through to the tool gate -- the request is not a build this runtime
            # will serve, and the generic tool loop is the lane that can.
        # Plan (and the retired Ask compatibility mode) is read-only. Manual/Review continue to the
        # controller so it can produce an exact action/edit-batch approval. A request blocked here
        # it never falls through to the tool loop and reads a not-yet-created file ("File `tasks.json`
        # does not exist"). The user gets a clear pointer to Build/Auto instead of a confusing error.
        _write_mode = str((source_context or {}).get("operating_mode") or "").strip().lower()
        if _write_mode in {"ask", "plan"} and self._looks_like_write_intent_request(effective_input):
            return {
                "response": (
                    f"You're in {_write_mode.capitalize()} mode, which is read-only, so I didn't create or "
                    "change any files. Switch to Manual, Review edits, or Auto and I'll continue under "
                    "that mode's controller-enforced permissions."
                ),
                "confidence": 1.0,
                "success": False,
                "status": "mode_read_only_blocked_write",
                "mode": "tool_failed",
                "task_outcome": "blocked",
                "details": {
                    "operating_mode": _write_mode,
                    "blocked_action": "workspace_write",
                    "executed": False,
                    "files_modified": False,
                    "files_deleted": False,
                },
                "learned_plan": None,
                "workflow_summary": "- write-intent request blocked in read-only mode; no files changed",
            }
        # REACH: this is the gate that decides whether the model is offered tools at all, and it
        # decides it from the raw words BEFORE the model is consulted -- see the comment block in
        # `core.execution.planner.should_attempt_tool_intent` naming that as its own known defect.
        # Recorded, not changed: `blocked` and `reached` return nothing and cannot raise.
        if not self._should_attempt_tool_intent(
            effective_input,
            task_class=str(classification.get("task_class", "unknown")),
            source_context=source_context,
        ):
            semantic_reach.blocked(
                semantic_reach.GATE_TOOL_INTENT_GATE,
                detail=f"task_class={classification.get('task_class', 'unknown')}",
            )
            return None
        # OFFERED: the catalogue was made available. Whether the model then called anything is a
        # different fact, and one this line is in no position to know.
        semantic_reach.offered(semantic_reach.GATE_TOOL_INTENT_GATE)
        loop_source_context = self._merge_runtime_source_contexts(
            dict(checkpoint_state.get("loop_source_context") or {}),
            dict(source_context or {}),
        )
        # The navigation scope is THIS request's (`_maybe_execute_model_tool_intent`): a context
        # stored with a paused request's checkpoint must never reopen that request's scope here.
        from core.tool_offer_state import TURN_SCOPE_KEY

        current_scope = str((source_context or {}).get(TURN_SCOPE_KEY) or "")
        if current_scope:
            loop_source_context[TURN_SCOPE_KEY] = current_scope
        else:
            loop_source_context.pop(TURN_SCOPE_KEY, None)
        executed_steps: list[dict[str, Any]] = []
        last_tool_decision = None
        seen_tool_payloads: set[str] = set()
        pending_tool_payload: dict[str, Any] | None = None
        # The further calls the paused reply had already asked for. Restored with the pending head so
        # a turn that resumes after an approval still knows the plan the operator approved, instead
        # of rediscovering it one model round at a time.
        pending_batch_members: list[dict[str, Any]] = []
        pending_decision_source = "checkpoint"
        # Stored loop state (previous steps, pending payload) belongs to the turn that
        # wrote it. Adopt it ONLY when this turn explicitly resumed that checkpoint
        # (proceed/retry via prepare_runtime_checkpoint, which stamps the flag below).
        # A fresh turn must start with clean transient state -- never inherit another
        # turn's tool results as its own.
        checkpoint_resumed = bool((source_context or {}).get("runtime_checkpoint_resumed"))
        if checkpoint_state and checkpoint_resumed:
            executed_steps = [dict(step) for step in list(checkpoint_state.get("executed_steps") or []) if isinstance(step, dict)]
            seen_tool_payloads = {
                str(item)
                for item in list(checkpoint_state.get("seen_tool_payloads") or [])
                if str(item).strip()
            }
            saved_pending = checkpoint_state.get("pending_tool_payload") or (checkpoint or {}).get("pending_intent") or {}
            if isinstance(saved_pending, dict) and saved_pending:
                pending_tool_payload = dict(saved_pending)
                pending_batch_members = [
                    dict(item)
                    for item in list(checkpoint_state.get("pending_batch_calls") or [])
                    if isinstance(item, dict) and str(item.get("intent") or "").strip()
                ]
                # The pending call was already selected before the interruption. Record its
                # semantic identity now so that, after it succeeds, a model that repeats the same
                # call is stopped at the duplicate guard instead of executing the side effect a
                # second time. Provider-native call IDs are transport metadata, not semantics.
                try:
                    semantic_pending = {
                        key: value
                        for key, value in pending_tool_payload.items()
                        if not str(key).startswith("_native_")
                    }
                    pending_signature = json.dumps(
                        semantic_pending, sort_keys=True, ensure_ascii=True, default=str
                    )
                except Exception:
                    pending_signature = str(pending_tool_payload)
                # Model-selected payloads are inserted into seen_tool_payloads before execution;
                # planner-selected payloads are not. Preserve that ownership across a restart so
                # a resumed deterministic workflow returns to the planner, while a resumed native
                # model loop still gives its observation back to the same model.
                pending_decision_source = (
                    "model_tool_intent"
                    if pending_signature in seen_tool_payloads
                    else "workflow_planner"
                )
                seen_tool_payloads.add(pending_signature)
        if not checkpoint_resumed:
            # Blank any stale per-turn tool state in the local view so nothing downstream
            # (progress records, direct render) can read another turn's results.
            checkpoint_state["last_tool_payload"] = None
            checkpoint_state["last_tool_response"] = None
        if checkpoint and (executed_steps or pending_tool_payload):
            self._emit_runtime_event(
                loop_source_context,
                event_type="tool_loop_resumed",
                message=(
                    f"Resuming tool loop from {len(executed_steps)} completed step"
                    f"{'' if len(executed_steps) == 1 else 's'}."
                ),
                step_count=len(executed_steps),
            )
        # ROUNDS, not steps. The budget has always meant "how many times may this turn go back to
        # the model", and while one step was one round `len(executed_steps)` measured that exactly.
        # Inline batch execution breaks that identity: a single round can now append several steps,
        # so counting steps would spend a 5-round budget on one round of six reads and report "the
        # step budget was exhausted" - a cause that is false on its face.
        #
        # All three red teams on the rejected batch designs found this independently, through three
        # different lenses, and one measured the resumed turn dropping an approval the operator had
        # just granted. It is fixed here rather than there because batching is what creates it.
        #
        # `round_index` is stamped on every step below, so a RESUMED turn recovers the true count.
        # Steps written before this change carry none; `len(executed_steps)` is the honest fallback
        # for them, being exactly what the count meant when they were written.
        max_rounds = _MAX_MODEL_ROUNDS_PER_TURN
        recorded_rounds = [
            int(step.get("round_index"))
            for step in executed_steps
            if isinstance(step, dict) and step.get("round_index") is not None
        ]
        model_rounds = (max(recorded_rounds) + 1) if recorded_rounds else len(executed_steps)
        loop_stop_reason = ""
        # This turn carries an approval the operator just granted, so it is REPLAYING a call that was
        # already selected (and recorded in seen_tool_payloads) before the pause. The duplicate guard
        # would read that replay as a loop and break to grounded synthesis -- the approved action would
        # never run, which is the opposite of what the operator just clicked. Each signature is exempted
        # exactly once, so a model that genuinely loops still stops on the next repeat.
        resuming_approval = bool(str((source_context or {}).get("mode_approval_token") or "").strip())
        approval_resume_exemptions: set[str] = set()
        confidence_hint = 0.55
        code_completion_corrections = 0
        # Per-turn tool candidate fingerprint.  Reset each turn so a fresh
        # turn never inherits a stale fingerprint from a previous turn on
        # the same agent instance.
        self._toolloop_candidate_fp: tuple[str, ...] | None = None

        while pending_tool_payload or model_rounds < max_rounds:
            # Replaying the pending approval uses its already-recorded decision round. It must
            # still execute at the limit, and must not spend a second model call. New decisions
            # increment here so every continue/return path remains bounded.
            replaying_pending = bool(pending_tool_payload)
            current_round = max(0, model_rounds - 1) if replaying_pending else model_rounds
            if not replaying_pending:
                model_rounds += 1
            tool_decision = None
            tool_payload: dict[str, Any] = {}
            decision_source = pending_decision_source if pending_tool_payload else "model_tool_intent"
            provider_id = None
            validation_state = "not_run"
            confidence_hint = 0.55

            # NO-ELIGIBLE-TOOL GATE: detect when the tool loop has made no meaningful
            # progress because the model keeps selecting the same tool intent across
            # consecutive rounds (e.g. different search queries against the same empty
            # catalog).  After 2+ consecutive rounds with the same intent AND the
            # turn's authoritative requirements not demanding external tools, the loop
            # is cycling — stop rather than burning the remaining budget.
            #
            # This is a liveness mechanism, not a semantic classifier: it runs AFTER
            # the keyword-based should_attempt_tool_intent already admitted the turn
            # to the loop, so it only catches cases the pre-loop heuristics got wrong.
            # The synthesis path below still runs with whatever observations exist, so
            # a tool that genuinely collected evidence is preserved and rendered.
            if not pending_tool_payload and self._no_eligible_tool_detected(
                effective_input=effective_input,
                task_class=str(classification.get("task_class", "unknown")),
                source_context=loop_source_context,
                executed_steps=executed_steps,
                candidate_fingerprint=self._tool_candidate_fingerprint(loop_source_context),
                previous_candidate_fingerprint=self._toolloop_candidate_fp,
            ):
                loop_stop_reason = "no_eligible_tool"
                self._emit_runtime_event(
                    loop_source_context,
                    event_type="tool_failed",
                    message=(
                        "No tool in the available catalog can satisfy this request's requirements. "
                        "Stopping the loop rather than continuing through unchanged candidates."
                    ),
                    tool_name="",
                    status="no_eligible_tool",
                    step_count=len(executed_steps),
                )
                break
            self._toolloop_candidate_fp = self._tool_candidate_fingerprint(loop_source_context)

            if pending_tool_payload:
                tool_payload = dict(pending_tool_payload)
                pending_tool_payload = None
                tool_name = str(tool_payload.get("intent") or "").strip()
                self._emit_runtime_event(
                    loop_source_context,
                    event_type="tool_selected" if tool_name else "tool_failed",
                    message=(
                        f"Resuming pending tool {tool_name}."
                        if tool_name
                        else "Resuming invalid pending tool payload with no intent name."
                    ),
                    tool_name=tool_name or "unknown",
                    tool_args=summarize_tool_arguments(tool_payload.get("arguments")),
                    arguments=redact_tool_argument_data(tool_payload.get("arguments")),
                )
            else:
                # Once a model-directed loop has chosen a tool, the same model must get the real
                # observation and decide whether to call another tool or respond.direct. Letting the
                # deterministic workflow planner jump in after that first step discards the model's
                # pending continuation and can synthesize an empty failure instead.
                planner_owns_loop = not executed_steps or str(
                    executed_steps[-1].get("decision_source") or ""
                ) == "workflow_planner"
                workflow_decision = (
                    self._plan_tool_workflow(
                        user_text=effective_input,
                        task_class=str(classification.get("task_class") or "unknown"),
                        executed_steps=executed_steps,
                        source_context=loop_source_context,
                    )
                    if planner_owns_loop
                    else None
                )
                if workflow_decision is not None and workflow_decision.handled and workflow_decision.stop_after:
                    loop_stop_reason = "workflow_planner_stop"
                    self._emit_runtime_event(
                        loop_source_context,
                        event_type="workflow_planner_stop",
                        message="Workflow planner gathered enough state and stopped before another tool step.",
                        status=workflow_decision.reason,
                        step_count=len(executed_steps),
                    )
                    break
                if workflow_decision is not None and workflow_decision.handled and workflow_decision.next_payload:
                    decision_source = "workflow_planner"
                    tool_payload = dict(workflow_decision.next_payload)
                    tool_name = str(tool_payload.get("intent") or "").strip()
                    # A planner round has no `last_tool_decision` to recover a batch from, so
                    # without this the controller saw one call and could only offer one prompt --
                    # a scaffold of one `ensure_directory` and N writes cost N+1 approvals even
                    # though the planner had decided all N+1 before the first one ran. The planner
                    # states the rest of its concrete plan; the controller still re-derives and
                    # re-filters every member of it.
                    pending_batch_members = [
                        dict(item)
                        for item in list(getattr(workflow_decision, "planned_batch", None) or [])
                        if isinstance(item, dict) and str(item.get("intent") or "").strip()
                    ]
                    self._emit_runtime_event(
                        loop_source_context,
                        event_type="workflow_planner_step",
                        message=f"Workflow planner selected {tool_name}.",
                        tool_name=tool_name or "unknown",
                        status=workflow_decision.reason,
                        tool_args=summarize_tool_arguments(tool_payload.get("arguments")),
                        arguments=redact_tool_argument_data(tool_payload.get("arguments")),
                    )
                else:
                    tool_decision = self.memory_router.resolve_tool_intent(
                        task=task,
                        classification=classification,
                        interpretation=interpretation,
                        context_result=context_result,
                        persona=persona,
                        surface=surface,
                        source_context=loop_source_context,
                    )
                    loop_source_context.pop("code_task_completion_feedback", None)
                    last_tool_decision = tool_decision
                    # NOTE: a provider that never answered (structured_output is None and
                    # not used_model) is NOT handled here. KAS's PR #80 added an early
                    # `return None` at this exact point (merged 2026-08-04) that duplicated,
                    # pre-empted, and regressed the existing handling ~100 lines down (the
                    # `provider_did_not_answer` branch): that branch `break`s to synthesis
                    # over whatever already ran, so a timeout on round 3 keeps rounds 1-2's
                    # work, and it emits the specific, tested `tool_failed`/
                    # `provider_did_not_answer` event instead of a bare `tool_model_unavailable`.
                    # KAS's early return threw both of those away because it fired first and
                    # `return`ed unconditionally. Removed here rather than kept alongside it --
                    # two independent fixes for the same gap is not a reason to keep the worse
                    # one. See tests/test_the_tool_loop_reports_what_it_actually_ran.py
                    # (test_a_provider_that_never_answered_is_not_blamed_on_the_model,
                    # test_a_timeout_mid_turn_keeps_the_steps_that_already_ran).
                    # A model that NARRATES while it requests files puts `respond.direct` at
                    # position 1 of a native batch and the real work behind it. `structured_output`
                    # is call #1 re-parsed, so the loop used to read the narration, take the
                    # direct-response exit, and never look at the remaining members -- no execution,
                    # no step record, and no `tool_batch_deferred`, because that accounting lives
                    # after the loop. The batch vanished with no receipt anywhere and the preamble
                    # shipped as the finished answer with success=True. That is the exact
                    # user-visible output of the 2026-08-03 incident: "I'll do a proper audit. Let me
                    # read the key files first."
                    #
                    # The first fix asked `_tool_intent_direct_message` FIRST and only promoted when
                    # it returned a message -- so it depended on how the monologue guard scored the
                    # prose. Measured 2026-08-03: for "Let me look.", "Let me check the files.",
                    # "First I need to see the code." the guard correctly returns None (it is
                    # scaffolding, not a reply), the promotion branch was skipped, `respond.direct`
                    # was executed as a tool, came back handled=False, and the loop returned None
                    # with ZERO tools run -- the better the guard, the more certainly the batch was
                    # destroyed.
                    #
                    # So the batch is inspected BEFORE the reply is classified. "Does this reply
                    # carry real work?" is answerable from the calls alone, and it is the question
                    # that decides. Only a head that does nothing is displaced, so an ordinary
                    # closing reply and an already-executable head are both untouched.
                    head_intent = str(
                        dict(tool_decision.structured_output or {}).get("intent") or ""
                    ).strip().lower()
                    if not head_intent or head_intent in _NON_EXECUTING_INTENTS:
                        promoted = _first_executable_batch_member(tool_decision)
                        if promoted is not None:
                            self._emit_runtime_event(
                                loop_source_context,
                                event_type="tool_batch_narration_set_aside",
                                message=(
                                    "The model narrated and requested tools in one reply; running "
                                    f"`{promoted.get('intent')}` instead of closing on the narration."
                                ),
                                status="narration_set_aside",
                                step_count=len(executed_steps),
                            )
                            tool_decision.structured_output = promoted
                    # Task completion owns continuation even when the prose filter rejects
                    # a respond.direct preamble. Rejected narration must not fall through
                    # into executing respond.direct as a tool and abandon the active task.
                    selected_response = str(
                        dict(tool_decision.structured_output or {}).get("intent") or ""
                    ).strip().lower() == "respond.direct"
                    if selected_response:
                        code_verdict = enforce_code_task_completion(
                            {"response": "", "success": True}, executed_steps,
                        )
                        unfinished_code = [
                            row for row in code_verdict.get("details", {}).get("unfinished_code_tasks", [])
                            if row.get("stage") not in {"unknown", "cancelled"}
                        ]
                        if unfinished_code and code_completion_corrections < 2:
                            code_completion_corrections += 1
                            loop_source_context["code_task_completion_feedback"] = unfinished_code
                            self._emit_runtime_event(
                                loop_source_context, event_type="code_task_early_response_rejected",
                                message="The coding task is unfinished; requesting the next real action instead of ending on narration.",
                                status="code_task_incomplete", correction=code_completion_corrections,
                            )
                            continue
                    direct_message = self._tool_intent_direct_message(tool_decision.structured_output)
                    if direct_message is not None:
                        # K-05 MODEL-PROSE-NOT-EVIDENCE: a bound obligation set
                        # with pending machine-effect intents blocks prose
                        # termination — the reply may not claim success over
                        # work that has no A6-reconciled evidence.
                        try:
                            from core.conductor import obligation_ledger as _ob_ledger

                            _active = _ob_ledger.active_set()
                            if (
                                _active is not None
                                and _ob_ledger.assert_prose_cannot_close_pending_effect(*_active)
                            ):
                                verdict = _ob_ledger.closure_verdict(*_active)
                                self._emit_runtime_event(
                                    loop_source_context,
                                    event_type="obligation_open_blocked_prose_exit",
                                    message=(
                                        "Prose termination refused: "
                                        f"{verdict['open_count']} required intent(s) remain open; "
                                        "model prose cannot close a pending machine effect."
                                    ),
                                    status="obligation_open",
                                )
                                direct_message = None
                        except Exception:
                            raise
                    if direct_message is not None:
                        if (
                            len(executed_steps) == 1
                            and str(executed_steps[-1].get("tool_name") or "")
                            in _DIRECT_RENDER_SINGLE_STEP_SUBSTITUTION_INTENTS
                        ):
                            grounded_tool_text = str(
                                ((checkpoint_state.get("last_tool_response") or {}).get("response_text")) or ""
                            ).strip()
                            if grounded_tool_text:
                                # The exact workspace result is stronger evidence than a model
                                # paraphrase and avoids losing filenames/query tokens in synthesis.
                                direct_message = grounded_tool_text
                        self._emit_runtime_event(
                            loop_source_context,
                            event_type="tool_loop_completed",
                            message=(
                                f"Returning grounded reply after {len(executed_steps)} real tool step"
                                f"{'' if len(executed_steps) == 1 else 's'}."
                            ),
                            step_count=len(executed_steps),
                        )
                        confidence = max(0.35, min(0.96, float(tool_decision.trust_score or tool_decision.confidence or 0.55)))
                        direct_rendered = apply_verifier_draft_caveat(
                            self._render_tool_loop_response(
                                final_message=direct_message,
                                executed_steps=executed_steps,
                                include_step_summary=not self._live_runtime_stream_enabled(loop_source_context),
                            ),
                            tool_decision,
                            # Without these the seal at memory_first_router.py:5145 is guarded by
                            # `if user_text:` and never runs, so the caveat prepends even to a reply
                            # under a raw-output contract that forbids anything but the literal.
                            user_text=effective_input,
                            source_context=dict(source_context or {}),
                        )
                        return enforce_code_task_completion({
                            "response": direct_rendered,
                            "confidence": confidence,
                            "success": True,
                            "status": "direct_response_after_tools" if executed_steps else "direct_response",
                            "mode": "tool_executed" if executed_steps else "advice_only",
                            "task_outcome": "success",
                            "details": {
                                "tool_name": "respond.direct",
                                "tool_provider": tool_decision.provider_id,
                                "tool_validation": tool_decision.validation_state,
                                "tool_steps": [step["tool_name"] for step in executed_steps],
                            },
                            "learned_plan": None,
                            "workflow_summary": self._tool_intent_loop_workflow_summary(
                                executed_steps=executed_steps,
                                provider_id=tool_decision.provider_id,
                                validation_state=tool_decision.validation_state,
                            ),
                        }, executed_steps)

                    # No intent came back. Two DIFFERENT causes hide behind that, and the lane used
                    # to report both as the model's fault: "Model returned an invalid tool payload
                    # with no intent name." Measured 2026-08-03 on the local lane - the ledger for
                    # the same turn carried `model.call_failed (Read timed out, read timeout=59.99)`
                    # and `Provider fallback budget (60s) exceeded` three events earlier. The
                    # provider never answered; the model emitted nothing to be invalid. CLAUDE.md
                    # section 5 requires that distinction to be explicit, and the decision object
                    # already carries it in `used_model`.
                    #
                    # Stopping here (rather than executing an empty payload) also stops a timeout on
                    # round 3 from DISCARDING the real steps of rounds 1-2:
                    # `_should_fallback_after_tool_failure` returns False once `executed_steps` is
                    # non-empty, so that turn used to return the tooling error and throw the
                    # grounded work away. Now it breaks to synthesis over what it has.
                    selected_intent = str(
                        dict(tool_decision.structured_output or {}).get("intent") or ""
                    ).strip()
                    if not selected_intent and not bool(getattr(tool_decision, "used_model", False)):
                        loop_stop_reason = "provider_did_not_answer"
                        # A THIRD cause hides here, and it is local: the router refused the
                        # selection round before any request left (an explicit paid pin is not
                        # spent on the turn's internal step -- `internal_tool_intent_call` in
                        # core/paid_call_reservation.py). Measured served 2026-09-16 (candidate
                        # 035dea9b): that refusal was recorded as "The provider returned no reply
                        # ... This is a transport failure", and Activity showed a failed provider
                        # call that never happened. The decision names its own source; say so.
                        block_reason = str(
                            dict(getattr(tool_decision, "details", None) or {}).get("block_reason")
                            or dict(getattr(tool_decision, "details", None) or {}).get("reason")
                            or ""
                        )
                        refused_before_send = (
                            str(getattr(tool_decision, "source", "") or "") == "selected_model_blocked"
                            and dict(getattr(tool_decision, "details", None) or {}).get("model_was_attempted") is False
                        )
                        if refused_before_send:
                            self._emit_runtime_event(
                                loop_source_context,
                                event_type="tool_failed",
                                message=(
                                    "No tool was selected: the selected model was not asked for "
                                    "this internal tool-selection step"
                                    + (f" ({block_reason})" if block_reason else "")
                                    + ". Nothing was sent; this is a local decision, not a "
                                    "provider failure."
                                ),
                                tool_name="unknown",
                                status="tool_selection_refused_before_send",
                                refusal_reason=block_reason,
                                step_count=len(executed_steps),
                            )
                            break
                        self._emit_runtime_event(
                            loop_source_context,
                            event_type="tool_failed",
                            message=(
                                "No usable provider reply was available for this step, so no tool "
                                "was selected."
                                + (f" Recorded cause: {block_reason}." if block_reason else "")
                            ),
                            tool_name="unknown",
                            status="provider_did_not_answer",
                            step_count=len(executed_steps),
                        )
                        break
                    payload_signature = _semantic_payload_signature(tool_decision.structured_output)
                    if payload_signature in seen_tool_payloads and resuming_approval and payload_signature not in approval_resume_exemptions:
                        approval_resume_exemptions.add(payload_signature)
                        self._emit_runtime_event(
                            loop_source_context,
                            event_type="tool_repeat_allowed_for_approval",
                            message="Resuming the action you approved.",
                        )
                    elif payload_signature in seen_tool_payloads:
                        loop_stop_reason = "repeated_tool_request"
                        self._emit_runtime_event(
                            loop_source_context,
                            event_type="tool_repeat_blocked",
                            message="Repeated tool request detected. Switching to grounded synthesis instead of looping.",
                        )
                        if checkpoint_id:
                            self._record_runtime_tool_progress(
                                checkpoint_id,
                                executed_steps=executed_steps,
                                loop_source_context=loop_source_context,
                                seen_tool_payloads=seen_tool_payloads,
                                pending_tool_payload=None,
                                last_tool_payload=checkpoint_state.get("last_tool_payload"),
                                last_tool_response=checkpoint_state.get("last_tool_response"),
                                last_tool_name=str((executed_steps[-1] if executed_steps else {}).get("tool_name") or ""),
                                task_class=str(classification.get("task_class") or "unknown"),
                                status="running",
                            )
                        break
                    seen_tool_payloads.add(payload_signature)
                    tool_payload = dict(tool_decision.structured_output or {})
                    tool_name = str(tool_payload.get("intent") or "").strip()
                    provider_id = tool_decision.provider_id
                    validation_state = tool_decision.validation_state
                    confidence_hint = float(tool_decision.trust_score or tool_decision.confidence or 0.55)
                    self._emit_runtime_event(
                        loop_source_context,
                        event_type="tool_selected" if tool_name else "tool_failed",
                        message=(
                            f"Running real tool {tool_name}."
                            if tool_name
                            else "Model returned an invalid tool payload with no intent name."
                        ),
                        tool_name=tool_name or "unknown",
                        tool_args=summarize_tool_arguments(tool_payload.get("arguments")),
                        arguments=redact_tool_argument_data(tool_payload.get("arguments")),
                    )

            tool_name = str(tool_payload.get("intent") or "").strip() or "unknown"
            # The rest of THIS reply's batch, resolved before the head is gated. Two things need it.
            #
            # The permission controller needs it to offer one bounded approval for the writes this
            # request has already planned. Without it the controller can only ever see one write and
            # ask about one write, so a request planning seven of them asked seven times -- and each
            # answer arrived as a fresh submission of the original sentence, which re-classified and
            # re-planned the whole turn before reaching write number two. Measured symptom: one
            # request, seven prompts, seven replans.
            #
            # The batch walk below needs it because a RESUMED turn has no model decision to re-derive
            # it from: `last_tool_decision` is None when the head comes back from the checkpoint, so
            # the members the operator just authorized would be invisible and defer one per round.
            pending_batch_calls = (
                [dict(item) for item in pending_batch_members]
                if pending_batch_members
                else self._unexecuted_batch_calls(last_tool_decision, tool_payload)
            )
            pending_batch_members = []
            # Server-owned, and passed per call rather than merged into `loop_source_context`: the
            # controller reads it while gating this head and nothing else carries it onward.
            gating_source_context = (
                {**loop_source_context, PENDING_BATCH_CALLS_KEY: pending_batch_calls}
                if pending_batch_calls
                else loop_source_context
            )
            if checkpoint_id:
                self._record_runtime_tool_progress(
                    checkpoint_id,
                    executed_steps=executed_steps,
                    loop_source_context=loop_source_context,
                    seen_tool_payloads=seen_tool_payloads,
                    pending_tool_payload=resumable_pending_payload(tool_payload),
                    pending_batch_calls=[
                        resumable_pending_payload(item) for item in pending_batch_calls
                    ],
                    last_tool_payload=checkpoint_state.get("last_tool_payload"),
                    last_tool_response=checkpoint_state.get("last_tool_response"),
                    last_tool_name=tool_name,
                    task_class=str(classification.get("task_class") or "unknown"),
                    status="running",
                )

            execution = self._execute_tool_intent(
                tool_payload,
                task_id=task.task_id,
                session_id=session_id,
                source_context=gating_source_context,
                hive_activity_tracker=self.hive_activity_tracker,
                public_hive_bridge=self.public_hive_bridge,
                checkpoint_id=checkpoint_id,
                step_index=len(executed_steps),
                tool_call_id=str(tool_payload.get("_native_tool_call_id") or "") or None,
            )
            # Result-type validation: the executed tool must be the requested one. A
            # mismatch means an internal wiring fault -- stop safely instead of showing
            # a result the user never asked for.
            executed_name = str(getattr(execution, "tool_name", "") or "").strip()
            if execution.handled and executed_name and tool_name != "unknown" and executed_name != tool_name:
                self._emit_runtime_event(
                    loop_source_context,
                    event_type="stale_result_rejected",
                    message=(
                        f"Internal tool result mismatch: requested {tool_name} but received {executed_name}. Stopping safely."
                    ),
                    tool_name=tool_name,
                    status="result_mismatch",
                )
                return {
                    "response": "Internal tool result did not match the requested operation. The task was stopped safely.",
                    "confidence": 0.35,
                    "success": False,
                    "status": "result_mismatch",
                    "mode": "tool_failed",
                    "task_outcome": "failed",
                    "details": {
                        "tool_name": tool_name,
                        "received_tool_name": executed_name,
                        "tool_steps": [step["tool_name"] for step in executed_steps],
                    },
                    "learned_plan": None,
                    "workflow_summary": "- internal tool result mismatch; task stopped safely",
                }
            if not execution.handled:
                break
            if self._should_fallback_after_tool_failure(
                execution=execution,
                effective_input=effective_input,
                classification=classification,
                interpretation=interpretation,
                executed_steps=executed_steps,
            ):
                self._emit_runtime_event(
                    loop_source_context,
                    event_type="tool_fallback_to_research",
                    message="Tool-intent failed before any real tool ran. Continuing with grounded research instead of returning a tooling error.",
                    tool_name=execution.tool_name or tool_name,
                    status=str(execution.status or "failed"),
                )
                checkpoint_state["last_tool_payload"] = redact_tool_argument_data(tool_payload)
                checkpoint_state["last_tool_response"] = {
                    "handled": bool(execution.handled),
                    "ok": bool(execution.ok),
                    "status": str(execution.status or ""),
                    "response_text": redact_tool_argument_data(str(execution.response_text or "")),
                    "mode": str(execution.mode or ""),
                    "tool_name": str(execution.tool_name or tool_name),
                    "details": redact_tool_argument_data(dict(execution.details or {})),
                }
                if checkpoint_id:
                    self._record_runtime_tool_progress(
                        checkpoint_id,
                        executed_steps=executed_steps,
                        loop_source_context=loop_source_context,
                        seen_tool_payloads=seen_tool_payloads,
                        pending_tool_payload=None,
                        last_tool_payload=checkpoint_state.get("last_tool_payload"),
                        last_tool_response=checkpoint_state.get("last_tool_response"),
                        last_tool_name=str(execution.tool_name or tool_name),
                        task_class=str(classification.get("task_class") or "unknown"),
                        status="running",
                    )
                return None

            # Every schema-valid call the provider returned beyond the one being executed. The
            # step loop runs exactly one call per step; before this, members 2..N were dropped with
            # no record anywhere, so a model that asked to read two files saw one result and no sign
            # the other request had existed.
            batch_members = [dict(member) for member in pending_batch_calls]
            # `respond.direct`/`none`/`no_tool` are not tools. They are DECLARED `read_only` in the
            # contract map, so the capability gate below admitted them, `_execute_tool_intent`
            # returned handled=False, and the walk broke at position 0 - measured 2026-08-03: a
            # narrated 9-read batch ran ONE tool where the identical batch without the narration ran
            # eight. They also cannot be "deferred": naming `respond.direct` as a call the operator
            # could re-request is noise in the event and a falsehood in the model-facing note. Drop
            # them here, before either accounting sees them.
            batch_members = [
                member
                for member in batch_members
                if str(member.get("intent") or "").strip().lower() not in _NON_EXECUTING_INTENTS
            ]
            # The HEAD's own outcome gates the walk before any member is considered. Measured while
            # driving this change: a batch headed by `workspace.write_file` in manual mode paused
            # for approval (mode `tool_preview`) and the read behind it ran anyway - work happening
            # while the operator was still being asked, and out of the order the model wrote. Only a
            # head that actually EXECUTED earns the rest of its batch.
            if str(getattr(execution, "mode", "") or "") != "tool_executed":
                batch_members = []

            # THE HEAD IS RECORDED FIRST, because it RAN first. The member walk used to sit above
            # this block, so a 4-read batch reached the model as f01, f02, f03, f00 and told the
            # operator "Real steps completed: loader.py, parser_util.py, README.md, calc.py" with
            # calc.py - the file that ran first - listed last. Every pair inverted. The head's
            # `deferred_calls` is the one thing the walk still has to fill in, so it is set after.
            execution_details = dict(execution.details or {})
            head_step: dict[str, Any] = {
                "tool_name": execution.tool_name or tool_name,
                "ok": bool(execution.ok),
                "status": str(execution.status or "executed"),
                "mode": execution.mode,
                "deferred_calls": [],
                "arguments": redact_tool_argument_data(tool_payload.get("arguments")),
                "observation": redact_tool_argument_data(dict((execution.details or {}).get("observation") or {})),
                "details": redact_tool_argument_data(dict(execution.details or {})),
                "summary": redact_tool_argument_data(
                    self._tool_step_summary(execution.user_safe_response_text or execution.response_text, fallback=str(execution.status or "executed"))
                ),
                # The tool's FULL response -- the text the model is about to be shown. `summary`
                # is a one-line rendering for the operator ("Search results for X:"), so binding
                # a step by its summary alone binds a label instead of the evidence, and a gate
                # judging an answer against that label refuses correct answers. Redacted through
                # the same helper every other carried tool value uses.
                "response_text": redact_tool_argument_data(str(execution.response_text or "")),
                "tool_call_id": str((execution.details or {}).get("tool_call_id") or ""),
                "decision_source": decision_source,
                "round_index": current_round,
            }
            executed_steps.append(head_step)
            step_summary = str(head_step["summary"] or "").strip()
            action_receipt = self._emit_runtime_event(
                loop_source_context,
                event_type=str(execution.mode or "tool_failed"),
                message=(
                    f"{'Finished' if execution.mode == 'tool_executed' else 'Approval required for' if execution.mode == 'tool_preview' else 'Tool failed:'} "
                    f"{execution.tool_name or tool_name}. {step_summary}"
                ),
                tool_name=execution.tool_name or tool_name,
                status=str(execution.status or "executed"),
                mode=execution.mode,
                ok=bool(execution.ok),
                summary=step_summary,
                tool_call_id=str((execution.details or {}).get("tool_call_id") or ""),
                tool_args=summarize_tool_arguments(tool_payload.get("arguments")),
                arguments=redact_tool_argument_data(tool_payload.get("arguments")),
                approval=redact_tool_argument_data(execution_details.get("approval") or {}),
                approval_request=redact_tool_argument_data(
                    execution_details.get("approval_request") or {}
                ),
                approval_id=execution_details.get("approval_id"),
                approval_required=execution_details.get("approval_required"),
                approval_requirement=execution_details.get("approval_requirement"),
                approval_state=execution_details.get("approval_state"),
                approved_by=execution_details.get("approved_by"),
            )
            loop_source_context = self._append_tool_result_to_source_context(
                loop_source_context,
                execution=execution,
                tool_name=execution.tool_name or tool_name,
                receipt=action_receipt,
            )

            # The rest of the batch. Members declared `read_only` RUN here, in provider order,
            # through the same `_execute_tool_intent` seam as the head - so the permission gate and
            # the turn's cancel signal fire per member, not once for the round.
            #
            # Measured 2026-08-03: the model asked for 11 tools, 3 ran, and the 8 it never received
            # were re-requested and scored as looping. Running one member per model round-trip also
            # resent full context at ~20k tokens each, which on a free cloud lane is rate limit, not
            # money.
            #
            # Order is preserved and the walk STOPS at the first member that cannot run inline. A
            # write, a validation command, an unknown intent, a member that errored, or the cap all
            # end the walk, and everything from that point defers together - so nothing runs out of
            # the order the model asked for, and nothing runs after something that needed the
            # operator or after something that failed.
            ran_inline: list[str] = []
            failed_inline: list[dict[str, Any]] = []
            deferred_members: list[dict[str, Any]] = list(batch_members)
            for position, member in enumerate(batch_members):
                member_intent = str(member.get("intent") or "").strip()
                if position >= _MAX_BATCH_MEMBERS_PER_ROUND - 1:
                    break
                member_payload = {
                    "intent": member_intent,
                    "arguments": dict(member.get("arguments") or {}),
                }
                # A member runs inline when it is declared `read_only`, OR when the operator has a
                # live request-scope grant that names this exact call -- same session, same turn,
                # same target, same bytes, same mode revision. That grant is the whole point of the
                # "allow all planned changes for this request" button: the writes it covers are the
                # writes that run, in the model's own order, without another round-trip each.
                #
                # `request_batch_grant_covers` only peeks; the gate inside `_execute_tool_intent`
                # still decides and still spends the grant. A member the grant does not name gets
                # False here and the walk defers from that point, exactly as it does today for every
                # write -- so the failure direction is "ask again", never "run unasked".
                if not _batch_member_is_read_only(member_intent, member_payload["arguments"]) and not request_batch_grant_covers(
                    intent=member_intent,
                    arguments=member_payload["arguments"],
                    task_id=task.task_id,
                    source_context=loop_source_context,
                ):
                    break
                member_execution = self._execute_tool_intent(
                    member_payload,
                    task_id=task.task_id,
                    session_id=session_id,
                    source_context=loop_source_context,
                    hive_activity_tracker=self.hive_activity_tracker,
                    public_hive_bridge=self.public_hive_bridge,
                    checkpoint_id=checkpoint_id,
                    step_index=len(executed_steps),
                    tool_call_id=str(member.get("_native_tool_call_id") or "") or None,
                )
                member_mode = str(getattr(member_execution, "mode", "") or "")
                member_handled = bool(getattr(member_execution, "handled", False))
                member_ran = member_handled and member_mode == "tool_executed"
                # A member that EXECUTED AND ERRORED is not a member that did not run. Measured
                # 2026-08-03: a batch [read calc.py, read does_not_exist.py, read loader.py] ran the
                # missing read, and the model was told it was "NOT run" - so the only rational next
                # move it had was to request the same read again. The head reports its failures; a
                # member has to as well, with the same error text, or the turn hides a tool failure
                # (CLAUDE.md section 4). `handled` false, or a mode that needs the operator, still
                # means nothing happened - those defer, as before.
                member_failed = member_handled and member_mode == "tool_failed"
                if not member_ran and not member_failed:
                    break
                member_receipt = self._emit_runtime_event(
                    loop_source_context,
                    event_type=member_mode or "tool_failed",
                    message=(
                        f"{'Finished' if member_ran else 'Tool failed:'} "
                        f"{member_execution.tool_name or member_intent} "
                        f"(batch member {position + 2})."
                    ),
                    tool_name=member_execution.tool_name or member_intent,
                    status=str(member_execution.status or ("executed" if member_ran else "failed")),
                    mode=member_execution.mode,
                    ok=bool(member_execution.ok),
                    tool_args=summarize_tool_arguments(member_payload.get("arguments")),
                    arguments=redact_tool_argument_data(member_payload.get("arguments")),
                )
                loop_source_context = self._append_tool_result_to_source_context(
                    loop_source_context,
                    execution=member_execution,
                    tool_name=member_execution.tool_name or member_intent,
                    receipt=member_receipt,
                )
                executed_steps.append(
                    {
                        "tool_name": member_execution.tool_name or member_intent,
                        "ok": bool(member_execution.ok),
                        "status": str(member_execution.status or ("executed" if member_ran else "failed")),
                        "mode": member_execution.mode,
                        "deferred_calls": [],
                        "arguments": redact_tool_argument_data(member_payload.get("arguments")),
                        "observation": redact_tool_argument_data(
                            dict((member_execution.details or {}).get("observation") or {})
                        ),
                        "details": redact_tool_argument_data(dict(member_execution.details or {})),
                        "summary": redact_tool_argument_data(
                            self._tool_step_summary(
                                member_execution.user_safe_response_text or member_execution.response_text,
                                fallback=str(member_execution.status or "executed"),
                            )
                        ),
                        "tool_call_id": str((member_execution.details or {}).get("tool_call_id") or ""),
                        "decision_source": "model_tool_intent_batch_member",
                        "round_index": current_round,
                    }
                )
                # NOT registered in `seen_tool_payloads`, and the reason is the guard's behaviour
                # rather than its intent. At :941 a repeat does not SKIP the call - it sets
                # `loop_stop_reason = "repeated_tool_request"` and BREAKS, ending the turn. So
                # registering a member would mean any file first read as a batch member can never be
                # legitimately re-read in this turn, and the attempt kills every round behind it:
                # "read a.py and b.py, fix b.py, now show me it is fixed" dies at round 3, on the
                # verification step, which is the one step that proves the work.
                #
                # It also would not buy what it was for. This walk only ADDS; it never checks. The
                # in-batch duplicate it was meant to stop (the same file twice in one reply) still
                # executes twice either way.
                deferred_members = batch_members[position + 1 :]
                if member_failed:
                    failed_inline.append(member)
                    break
                ran_inline.append(member_intent)
            unexecuted_calls = [
                str(member.get("intent") or "") for member in deferred_members if member.get("intent")
            ]
            head_step["deferred_calls"] = list(unexecuted_calls)
            if unexecuted_calls:
                self._emit_runtime_event(
                    loop_source_context,
                    event_type="tool_batch_deferred",
                    message=(
                        f"The model requested {len(batch_members) + 1} tools in one reply. "
                        f"Ran {1 + len(ran_inline) + len(failed_inline)}; "
                        f"{', '.join(_describe_batch_member(member) for member in deferred_members)} not run."
                    ),
                    deferred_intents=list(unexecuted_calls),
                    deferred_calls=[_describe_batch_member(member) for member in deferred_members],
                )
                # Told to the MODEL, not just logged. Without this the model cannot distinguish
                # "my second call ran and returned nothing" from "my second call never ran", and
                # its next step is a guess either way.
                #
                # The counts come from the same variables the operator-facing event above uses.
                # They used to be re-derived inside the note as `len(deferred) + 1` with the literal
                # sentence "Only the first ran" - written before inline execution existed. Measured
                # 2026-08-03 on one 12-read batch: the event said "requested 12 ... Ran 8" and the
                # model was told "you requested 5 tools ... Only the first ran", with all twelve
                # paths discarded. A model that can see eight results while being told one ran has
                # to resolve a contradiction by guessing.
                loop_source_context = self._note_deferred_batch_for_model(
                    loop_source_context,
                    deferred=unexecuted_calls,
                    requested=len(batch_members) + 1,
                    ran=1 + len(ran_inline) + len(failed_inline),
                    described=[_describe_batch_member(member) for member in deferred_members],
                )
            checkpoint_state["last_tool_payload"] = redact_tool_argument_data(tool_payload)
            checkpoint_state["last_tool_response"] = {
                "handled": bool(execution.handled),
                "ok": bool(execution.ok),
                "status": str(execution.status or ""),
                "response_text": redact_tool_argument_data(str(execution.response_text or "")),
                "mode": str(execution.mode or ""),
                "tool_name": str(execution.tool_name or tool_name),
                "details": redact_tool_argument_data(dict(execution.details or {})),
                "receipt": (
                    {
                        "receipt_id": str(
                            (action_receipt or {}).get("receipt_id") or ""
                        ),
                        "safe_summary": str(
                            dict(
                                (action_receipt or {}).get("result") or {}
                            ).get("summary")
                            or step_summary
                            or ""
                        ),
                    }
                    if action_receipt
                    else {}
                ),
            }
            if checkpoint_id:
                self._record_runtime_tool_progress(
                    checkpoint_id,
                    executed_steps=executed_steps,
                    loop_source_context=loop_source_context,
                    seen_tool_payloads=seen_tool_payloads,
                    pending_tool_payload=(
                        resumable_pending_payload(tool_payload)
                        if execution.mode == "tool_preview"
                        else None
                    ),
                    # This record is written AFTER the pre-execution one and merges over it, so the
                    # plan has to be repeated here or a pause would store its head with an empty
                    # batch -- the resume would then have nothing to run but write number one, which
                    # is the defect with an extra step in it.
                    pending_batch_calls=(
                        [resumable_pending_payload(item) for item in pending_batch_calls]
                        if execution.mode == "tool_preview"
                        else []
                    ),
                    last_tool_payload=checkpoint_state.get("last_tool_payload"),
                    last_tool_response=checkpoint_state.get("last_tool_response"),
                    last_tool_name=str(execution.tool_name or tool_name),
                    task_class=str(classification.get("task_class") or "unknown"),
                    status=(
                        "pending_approval"
                        if execution.mode == "tool_preview"
                        else "running"
                    ),
                )
            # A refused step is not a terminal turn: the correction path or grounded synthesis
            # below still owns it. Finalization records failure when the turn actually ends.
            # Marking it failed here seals the checkpoint and discards a later approval pause,
            # forcing the same reviewed edit to ask again under a fresh turn on resume.
            if execution.mode != "tool_executed" and self._retry_as_observation(
                execution=execution,
                tool_payload=tool_payload,
                executed_steps=executed_steps,
                loop_source_context=loop_source_context,
                seen_tool_payloads=seen_tool_payloads,
            ):
                continue

            if execution.mode != "tool_executed":
                # A turn whose contract REQUIRES evidence must not dead-end here. When the model
                # emits no usable tool intent, returning the failure text ends the turn with
                # "I wasn't able to turn that into a completed action" and zero retrieval -- the
                # same disease this routing work exists to remove, just one lane further in.
                #
                # Returning None hands the turn back to `turn_reasoning`, which runs the planned
                # research path. That fallthrough is what served these requests before the lane
                # fix (6ce20405) started routing them here, and it is why
                # `test_openclaw_tool_intent_missing_intent_falls_through_to_planned_research`
                # went red: the routing was now right and the destination could not serve it.
                #
                # `tool_preview` is excluded deliberately -- pending approval is a legitimate
                # outcome awaiting the user, not a failure to retrieve.
                #
                # And not when a NAMED tool ran and answered with text written for the person: that
                # refusal is the answer, not material for research. Measured served 2026-09-14 ("check
                # my personal inbox" after the account's grant was removed): email.read answered
                # needs_setup with the reconnect instruction, this branch handed the turn to the
                # research path, and the model was asked for a plain answer with neither tools nor the
                # refusal in front of it. An unusable intent (nothing ran) and a bare operational
                # failure (no user-facing text from the tool) still degrade through research, as the
                # tests above and `test_openclaw_safe_local_file_read_returns_grounded_text` pin.
                if execution.mode != "tool_preview":
                    from core.execution_requirements import requirements_for

                    failed_status = str(execution.status or "").strip().lower()
                    failed_name = str(execution.tool_name or tool_name or "").strip().lower()
                    no_usable_intent = failed_status in {"missing_intent", "invalid_payload"} or failed_name in {"", "unknown"}
                    answered_for_the_person = bool(str(getattr(execution, "user_safe_response_text", "") or "").strip())
                    if (no_usable_intent or not answered_for_the_person) and requirements_for(
                        effective_input,
                        task_class=str(classification.get("task_class") or "unknown"),
                        source_context=loop_source_context,
                    ).tools_required:
                        # A refusal the EXECUTOR made before any owner ran (an unwired or
                        # unconfigured tool) executed nothing: the original fallthrough stands --
                        # the research path may still retrieve.
                        _pre_owner = str(execution.status or "") in _PRE_OWNER_FAILURE_STATUSES
                        if not executed_steps or _pre_owner:
                            return None
                        # Real tools ran and their observations -- including a typed tool
                        # refusal -- are already on this loop's steps and appended to the
                        # conversation the synthesis reads. The pre-repair `return None` here
                        # discarded them: the tools-less research continuation answered from
                        # nothing and the owner's typed refusal never reached the user
                        # (measured on the unmodified base with a plain invalid-address
                        # wallet.propose; both transports). Break to this loop's own synthesis
                        # so the executed evidence is narrated truthfully; the terminal
                        # integrity check falls back to the deterministic grounded summary of
                        # these same steps if the model will not answer from them.
                        loop_stop_reason = "tool_failed_after_evidence"
                        break
                confidence = max(0.35, min(0.96, confidence_hint))
                task_outcome = "pending_approval" if execution.mode == "tool_preview" else "failed"
                safe_response = self._tool_failure_user_message(
                    execution=execution,
                    effective_input=effective_input,
                    session_id=session_id,
                )
                return {
                    "response": self._render_tool_loop_response(
                        final_message=safe_response,
                        executed_steps=executed_steps,
                        include_step_summary=not self._live_runtime_stream_enabled(loop_source_context),
                    ),
                    "confidence": confidence,
                    "success": bool(execution.ok),
                    "status": str(execution.status or "executed"),
                    "mode": execution.mode,
                    "task_outcome": task_outcome,
                    "details": {
                        "tool_name": execution.tool_name,
                        "tool_provider": provider_id,
                        "tool_validation": validation_state,
                        "tool_steps": [step["tool_name"] for step in executed_steps],
                        **dict(execution.details or {}),
                    },
                    "learned_plan": execution.learned_plan,
                    "workflow_summary": self._tool_intent_loop_workflow_summary(
                        executed_steps=executed_steps,
                        provider_id=provider_id,
                        validation_state=validation_state,
                    ),
                }

        if not executed_steps:
            rejection = dict((loop_source_context or {}).get("last_tool_call_rejection") or {})
            if rejection and loop_stop_reason == "provider_did_not_answer":
                # The provider DID answer this turn: the model expressed a tool call and the
                # runtime refused it (a typed ToolCallParseError — unknown name, malformed or
                # missing-name arguments, duplicate id). Returning None here handed the turn to
                # plain chat, which — knowing it needed a tool it no longer had — narrated its
                # plan as if it were an answer, and the turn closed SUCCEEDED/fulfilled
                # (measured live 2026-09-01, isolated daemon, drive B). A refused call with
                # nothing executed is a typed tool failure, not material for synthesis.
                error_kind = str(rejection.get("error_kind") or "rejected_tool_call")
                resolution = dict(rejection.get("resolution") or {})
                rejection_kind = str(resolution.get("rejection_kind") or error_kind)
                detail = str(resolution.get("detail") or "")
                response_text = (
                    "I could not complete this: the model proposed a tool call that could not "
                    f"be executed ({rejection_kind}{': ' + detail if detail else ''}). "
                    "Nothing was run, and I won't substitute a guess for the tool's answer."
                )
                self._emit_runtime_event(
                    loop_source_context,
                    event_type="tool_failed",
                    message="A rejected tool call ended the turn as a typed failure; no tool was executed.",
                    tool_name="unknown",
                    status="tool_call_rejected",
                    error_kind=error_kind,
                    tool_call_resolution=resolution or None,
                )
                return {
                    "response": response_text,
                    "confidence": 0.2,
                    "success": False,
                    "status": "tool_call_rejected",
                    "mode": "tool_failed",
                    "task_outcome": "failed",
                    "details": {
                        "tool_name": "unknown",
                        "tool_steps": [],
                        "step_count": 0,
                        "loop_stop_reason": "tool_call_rejected",
                        "error_kind": error_kind,
                        "tool_call_resolution": resolution,
                    },
                    "learned_plan": None,
                    "workflow_summary": None,
                }
            return None
        if not loop_stop_reason and model_rounds >= max_rounds:
            loop_stop_reason = "step_budget_exhausted"

        # Deterministic direct render: for read-only machine tools the tool text IS the
        # answer. Return it as-is -- no model synthesis pass, so a degraded model lane can
        # never paraphrase it into a duplicate or parrot the transcript over it.
        last_step = executed_steps[-1]
        direct_response_text = str(((checkpoint_state.get("last_tool_response") or {}).get("response_text")) or "").strip()
        direct_workspace_step = (
            len(executed_steps) == 1
            and str(last_step.get("tool_name") or "") in _DIRECT_RENDER_SINGLE_STEP_WORKSPACE_INTENTS
        )
        if (
            loop_stop_reason != "step_budget_exhausted"
            and
            str(last_step.get("mode") or "") == "tool_executed"
            and direct_response_text
            and (
                direct_workspace_step
                or all(str(step.get("tool_name") or "") in _DIRECT_RENDER_TOOL_INTENTS for step in executed_steps)
            )
        ):
            self._emit_runtime_event(
                loop_source_context,
                event_type="tool_loop_completed",
                message="Returning the direct tool result (deterministic tool, no model synthesis).",
                step_count=len(executed_steps),
                tool_name=str(last_step.get("tool_name") or ""),
                render_mode="direct_tool_result",
            )
            if checkpoint_id:
                self._record_runtime_tool_progress(
                    checkpoint_id,
                    executed_steps=executed_steps,
                    loop_source_context=loop_source_context,
                    seen_tool_payloads=seen_tool_payloads,
                    pending_tool_payload=None,
                    last_tool_payload=checkpoint_state.get("last_tool_payload"),
                    last_tool_response=checkpoint_state.get("last_tool_response"),
                    last_tool_name=str(last_step.get("tool_name") or ""),
                    task_class=str(classification.get("task_class") or "unknown"),
                    status="running",
                )
            return {
                "response": direct_response_text,
                "confidence": max(0.35, min(0.96, confidence_hint)),
                "success": True,
                "status": "multi_step_executed",
                "mode": "tool_executed",
                "task_outcome": "success",
                "details": {
                    "tool_name": last_step.get("tool_name"),
                    "tool_provider": None,
                    "tool_validation": "deterministic_tool",
                    "tool_steps": [step["tool_name"] for step in executed_steps],
                    "step_count": len(executed_steps),
                    "render_mode": "direct_tool_result",
                    # WHY the loop stopped, on the path that omitted it. A turn that ended on the
                    # repeat guard returned success=True, mode=tool_executed and no stop reason at
                    # all, so nothing downstream - and no operator reading the details - could tell
                    # a completed turn from one the guard cut short. The sibling terminal return
                    # (the synthesis one, below) has always carried it; this one is reached from
                    # the same `loop_stop_reason` computation twenty lines up and dropped it.
                    "loop_stop_reason": loop_stop_reason,
                },
                "learned_plan": None,
                "workflow_summary": self._tool_intent_loop_workflow_summary(
                    executed_steps=executed_steps,
                    provider_id=None,
                    validation_state="deterministic_tool",
                ),
            }

        self._emit_runtime_event(
            loop_source_context,
            event_type="tool_synthesizing",
            message="Synthesizing final reply from real tool results.",
            step_count=len(executed_steps),
        )
        if checkpoint_id:
            self._record_runtime_tool_progress(
                checkpoint_id,
                executed_steps=executed_steps,
                loop_source_context=loop_source_context,
                seen_tool_payloads=seen_tool_payloads,
                pending_tool_payload=None,
                last_tool_payload=checkpoint_state.get("last_tool_payload"),
                last_tool_response=checkpoint_state.get("last_tool_response"),
                last_tool_name=str(executed_steps[-1].get("tool_name") or ""),
                task_class=str(classification.get("task_class") or "unknown"),
                status="running",
            )
        # Validate-then-render: the synthesis must not stream raw tokens to the user,
        # because streamed chunks are unretractable -- the live incident shipped a
        # transcript echo that had already reached the screen before any check could
        # run. Stripping the stream id buffers the synthesis; the validated final
        # message is streamed by the transport afterwards, and the live status card
        # keeps progress visible via the separate task-event channel.
        synthesis_context = {key: value for key, value in loop_source_context.items() if key != "runtime_event_stream_id"}
        # M3/M2: BIND what this loop retrieved into the call that is about to write the answer.
        #
        # Measured on the isolated daemon (2026-09-01): a current-information question routed
        # here rather than to the grounded lane, ran a real keyed Brave retrieval (4 sources,
        # receipt `lifecycle=succeeded`), handed the results to the model -- and left no record
        # anywhere that any of it reached the answering call. The receipt says a search ran; only
        # a binding says the answer was made of what it found, and the difference is the whole
        # fabrication class. Every other retrieving lane already binds; this one did not, so the
        # publication gate had nothing to judge its answers against.
        #
        # M2's own contract is used verbatim -- `mint_evidence_set`, `binding_record`,
        # `emit_evidence_bound` -- so the ids, the turn scope and the `proves="prompt_entry"`
        # claim are identical to the grounded lane's. The rows are a PROJECTION of the steps this
        # loop already executed into the note shape those functions take; nothing is
        # re-extracted, re-judged or re-worded.
        with suppress(Exception):
            _bound_rows = _tool_step_evidence_rows(executed_steps)
            if _bound_rows:
                from core.agent_runtime.current_evidence_prefetch import (
                    evidence_manifest,
                )
                from core.grounded_synthesis_binding import (
                    binding_record,
                    emit_evidence_bound,
                    mint_evidence_set,
                    turn_scope,
                )
                from core.grounding_lifecycle import adopt_lifecycle_id, record_bound

                _scope = turn_scope(
                    loop_source_context, task_id=str(getattr(task, "task_id", "") or "")
                )
                _set = mint_evidence_set(
                    _bound_rows, scope=_scope, query=str(effective_input or "")
                )
                _record = binding_record(_set, model_call_stage="tool_loop_synthesis")
                adopt_lifecycle_id(loop_source_context)
                emit_evidence_bound(
                    loop_source_context, _record, task_id=str(getattr(task, "task_id", "") or "")
                )
                record_bound(
                    loop_source_context, binding=_record, notes=_bound_rows, scope=_scope
                )
                synthesis_context["evidence_synthesis_binding"] = dict(_record)
                # The ids ride INSIDE the material the answering model is shown, not merely
                # alongside it. `proves="prompt_entry"` is checkable by reading the prompt
                # only if the prompt carries the ids; before this the envelope stamped the
                # claim while the synthesis conversation held the rows with no identity at
                # all. The manifest block is the same one the grounded lane's pre-model
                # binding appends -- ids and the sources they name, no instructions. It is
                # ATTACHED to the turn's observation message rather than appended after it,
                # so the evidence the model is shown and the identity naming it arrive as
                # one piece and the observation stays the conversation's last word.
                _manifest_text = evidence_manifest(_set)
                _history = [
                    dict(item)
                    for item in list(synthesis_context.get("conversation_history") or [])
                    if isinstance(item, dict)
                ]
                if (
                    _history
                    and str(_history[-1].get("role") or "") == "user"
                    and "Grounding observations for this turn"
                    in str(_history[-1].get("content") or "")
                    and _manifest_text not in str(_history[-1].get("content") or "")
                ):
                    _history[-1] = {
                        **_history[-1],
                        "content": f"{_history[-1].get('content')}\n\n{_manifest_text}",
                    }
                elif not any(_manifest_text in str(item.get("content") or "") for item in _history):
                    _history.append({"role": "user", "content": _manifest_text})
                synthesis_context["conversation_history"] = _history[-12:]
        # A tool that DECLARES its result is the answer (`renders_final_answer`, e.g. the coding
        # assistant's evidence report) is published as the grounded tool result; a model is never
        # asked to narrate evidence it did not produce, and no authorship gate has to judge prose.
        renders_final = False
        try:
            from core.runtime_tool_contracts import runtime_tool_contract_map

            _last_contract = runtime_tool_contract_map().get(str(executed_steps[-1].get("tool_name") or ""))
            renders_final = bool(getattr(_last_contract, "renders_final_answer", False))
        except Exception:
            renders_final = False
        if renders_final:
            synthesis = None
            synthesis_text = ""
            # The composed bytes are backed by runtime-minted rows (the executed steps), exactly
            # as the demand-owned composite lane records its units: no author is claimed, and the
            # publication gate adjudicates per claim against these rows instead of model prose.
            try:
                from core.final_answer_authorship import record_runtime_support

                record_runtime_support(
                    loop_source_context,
                    support_rows=[
                        {
                            "summary": str(step.get("summary") or step.get("response_text") or ""),
                            "intent": str(step.get("tool_name") or ""),
                            "source": "tool_loop_rendered_result",
                            "observation": step.get("observation") if isinstance(step.get("observation"), dict) else {},
                        }
                        for step in executed_steps
                        if str(step.get("summary") or step.get("response_text") or "").strip()
                    ],
                    request_text=str(effective_input or ""),
                )
            except Exception:
                pass
            self._emit_runtime_event(
                loop_source_context,
                event_type="tool_result_published",
                message="The tool declares its result as the answer; published the grounded tool result without model synthesis.",
                tool_name=str(executed_steps[-1].get("tool_name") or ""),
            )
        else:
            synthesis = self.memory_router.resolve(
                task=task,
                classification=classification,
                interpretation=interpretation,
                context_result=context_result,
                persona=persona,
                force_model=True,
                surface=surface,
                source_context=synthesis_context,
            )
            synthesis_text = str(getattr(synthesis, "output_text", "") or "").strip()
        history_messages = [item for item in list(loop_source_context.get("conversation_history") or []) if isinstance(item, dict)]
        # Terminal synthesis integrity. The observations are already gathered, so a "final" answer
        # that is empty, echoes an earlier reply, still asks to run a tool (or carries leaked tool
        # syntax), or ignores every observation is not a real answer -- reject it and fall back to
        # the deterministic grounded summary built from the executed steps (the same fallback the
        # echo guard already used). The fallback is itself grounded, so a conservative false reject
        # costs only fluency, never correctness.
        synthesis_observations: list[str] = []
        for step in executed_steps:
            step_summary_text = str(step.get("summary") or "").strip()
            if step_summary_text:
                synthesis_observations.append(step_summary_text)
            step_observation = step.get("observation")
            if isinstance(step_observation, dict) and step_observation:
                synthesis_observations.append(json.dumps(step_observation, default=str))
        reject_reason = ""
        if renders_final:
            reject_reason = ""
        elif not synthesis_text:
            reject_reason = "empty_synthesis"
        elif synthesis_echoes_prior_reply(synthesis_text, history_messages):
            reject_reason = "echo_rejected"
        elif foreign_markers(synthesis_text):
            reject_reason = "foreign_tool_syntax"
        elif claims_pending_tool(synthesis_text):
            reject_reason = "claims_pending_tool"
        elif is_ungrounded(synthesis_text, synthesis_observations):
            reject_reason = "ungrounded"
        if renders_final:
            # This contract publishes its complete, already-redacted runtime result.
            # A one-line activity summary is not the requested report or PR body.
            final_message = str(executed_steps[-1].get("response_text") or "").strip()
            if not final_message:
                final_message = self._tool_loop_final_message(None, executed_steps)
        elif reject_reason:
            # The model didn't produce a usable grounded answer; use the tool results directly.
            self._emit_runtime_event(
                loop_source_context,
                event_type="stale_result_rejected",
                message=f"Model synthesis rejected ({reject_reason}); replaced with the grounded tool result.",
                tool_name=str(executed_steps[-1].get("tool_name") or ""),
                status=reject_reason,
            )
            final_message = self._tool_loop_final_message(None, executed_steps)
        else:
            final_message = self._tool_loop_final_message(synthesis, executed_steps)
        _syn_provider = str(getattr(synthesis, "provider_id", "") or "")
        final_provider_id = _syn_provider if _syn_provider else (
            last_tool_decision.provider_id if last_tool_decision else None
        )
        _syn_validation = str(getattr(synthesis, "validation_state", "not_run") or "not_run")
        final_validation = _syn_validation if _syn_validation != "not_run" else (
            last_tool_decision.validation_state if last_tool_decision else "not_run"
        )
        confidence = max(
            0.35,
            min(
                0.96,
                float(
                    getattr(synthesis, "trust_score", None)
                    or getattr(synthesis, "confidence", None)
                    or (last_tool_decision.trust_score if last_tool_decision else 0.55)
                    or 0.55
                ),
            ),
        )
        rendered = self._render_tool_loop_response(
            final_message=final_message,
            executed_steps=executed_steps,
            include_step_summary=not self._live_runtime_stream_enabled(loop_source_context),
        )
        # Verifier gate -> visible caveat on the tool-loop final answer too (Codex F5):
        # if the synthesis was flagged by the verifier, mark it a draft. Non-blocking.
        if synthesis is not None:
            rendered = apply_verifier_draft_caveat(
                rendered,
                synthesis,
                user_text=effective_input,
                source_context=dict(source_context or {}),
            )
        terminal_failure = bool(reject_reason) or loop_stop_reason in {"step_budget_exhausted", "no_eligible_tool"}
        if (
            terminal_failure
            and not reject_reason
            and loop_stop_reason == "step_budget_exhausted"
            and _code_task_report_completed_the_turn(executed_steps)
        ):
            # The round budget ran out on the closing round AFTER every coding task this turn worked on
            # published its own completed report. That runtime-rendered report is the answer the
            # contract declares; prefixing it with "not a completed audit" contradicted the task's own
            # evidence. `loop_stop_reason` still records the budget stop.
            terminal_failure = False
        if terminal_failure:
            if reject_reason:
                reason_text = f"model synthesis failed ({reject_reason})"
            elif loop_stop_reason == "step_budget_exhausted":
                reason_text = f"the {max_rounds}-round tool budget was exhausted before the model finished"
            else:
                reason_text = "no eligible tool could satisfy this request"
            rendered = (
                f"Incomplete — {reason_text}. The grounded work collected so far is below; "
                f"this is not a completed audit.\n\n{rendered}"
            ).strip()
        # A turn that ends by narrating a FAILED tool's typed refusal is a truthful answer, not a
        # completed effect: the evidence reaches the user, but the turn never claims the tool's
        # work succeeded (never convert a failed tool into success merely to get narration).
        narrated_failure = loop_stop_reason == "tool_failed_after_evidence"
        return enforce_code_task_completion({
            "response": rendered,
            "confidence": confidence,
            "success": not terminal_failure and not narrated_failure,
            "status": (
                "tool_synthesis_failed" if reject_reason
                else "no_eligible_tool" if loop_stop_reason == "no_eligible_tool"
                else "tool_step_budget_exhausted" if terminal_failure
                else "tool_failed_narrated" if narrated_failure
                else "multi_step_executed"
            ),
            "mode": "tool_failed" if (terminal_failure or narrated_failure) else "tool_executed",
            "task_outcome": "failed" if (terminal_failure or narrated_failure) else "success",
            # Typed authorship declaration for the grounding lifecycle: when the last tool's
            # contract renders the final answer, runtime code composed the published bytes.
            "runtime_rendered_final": bool(renders_final),
            "details": {
                "tool_name": executed_steps[-1]["tool_name"],
                "tool_provider": final_provider_id,
                "tool_validation": final_validation,
                "tool_steps": [step["tool_name"] for step in executed_steps],
                "step_count": len(executed_steps),
                "loop_stop_reason": loop_stop_reason or "completed",
                "max_rounds": max_rounds,
                "max_steps": max_rounds,
            },
            "learned_plan": None,
            "workflow_summary": self._tool_intent_loop_workflow_summary(
                executed_steps=executed_steps,
                provider_id=final_provider_id,
                validation_state=final_validation,
            ),
        }, executed_steps)

    def _should_fallback_after_tool_failure(
        self,
        *,
        execution: Any,
        effective_input: str,
        classification: dict[str, Any],
        interpretation: Any,
        executed_steps: list[dict[str, Any]],
    ) -> bool:
        if bool(getattr(execution, "ok", False)):
            return False
        if str(getattr(execution, "mode", "") or "").strip().lower() != "tool_failed":
            return False
        if executed_steps:
            return False
        status = str(getattr(execution, "status", "") or "").strip().lower()
        tool_name = str(getattr(execution, "tool_name", "") or "").strip().lower()
        if status not in {"missing_intent", "invalid_payload"} and tool_name not in {"", "unknown"}:
            return False
        task_class = str(classification.get("task_class", "unknown"))
        if is_conversational_memory_declaration(effective_input):
            return True
        # A catalog-enabled conversational turn can still receive a malformed
        # tool-shaped model reply. No tool ran, so recover into normal routing
        # unless the user actually asked for a tool operation.
        if not has_explicit_tool_intent_request(effective_input, task_class=task_class):
            return True
        if task_class in {"research", "system_design", "integration_orchestration"}:
            return True
        if self._wants_fresh_info(effective_input, interpretation=interpretation):
            return True
        return self._should_frontload_curiosity(
            query_text=effective_input,
            classification=classification,
            interpretation=interpretation,
        )

    def _no_eligible_tool_detected(
        self,
        *,
        effective_input: str,
        task_class: str,
        source_context: dict[str, Any] | None,
        executed_steps: list[dict[str, Any]],
        candidate_fingerprint: tuple[str, ...] | None = None,
        previous_candidate_fingerprint: tuple[str, ...] | None = None,
    ) -> bool:
        """Return True when the tool loop is cycling with no legitimate tool need.

        Scoped to task_class values where the pre-model keyword gate (planner.py
        :456) can admit a zero-tool turn that no eligible tool can satisfy.
        ``dependency_resolution`` is the known identifier-list canary entry point
        from the routing audit (pass-001-20260825).

        Detects: 2+ consecutive rounds where the model chooses the SAME tool
        intent AND the SAME outcome (status+mode), AND the candidate tool set
        has NOT materially changed, AND the turn's authoritative requirements
        do not demand external tools.

        This is a liveness mechanism, not a semantic classifier.
        """
        if task_class not in {"dependency_resolution", "config"}:
            return False
        # Need at least 2 consecutive rounds to detect a cycle.
        if len(executed_steps) < 2:
            return False
        last_two = executed_steps[-2:]
        # Same tool intent across both rounds?
        last_intents = {
            str(step.get("tool_name") or "")
            for step in last_two
            if step.get("tool_name")
        }
        if len(last_intents) != 1:
            return False
        # Same execution outcome (status+mode) across both rounds?
        # A different outcome means materially new observation/rejection state.
        last_outcomes = {
            (str(step.get("status") or ""), str(step.get("mode") or ""))
            for step in last_two
        }
        if len(last_outcomes) != 1:
            return False
        # Candidate tool set changed?  If the eligible catalog changed between
        # rounds, reconsider rather than terminating.
        if (candidate_fingerprint is not None
                and previous_candidate_fingerprint is not None
                and candidate_fingerprint != previous_candidate_fingerprint):
            return False
        # Same intent, same outcome, same candidates.  Also check whether the
        # material typed observation/rejection evidence has changed between
        # rounds.  A different observation fingerprint (typed fields only: ok,
        # status, tool_surface) means new evidence was produced — do not
        # terminate as no-progress.
        # Tests may inject ``_observation_fingerprints`` into source_context
        # to simulate changing observation evidence without a real tool call.
        obs_fps = (source_context or {}).get("_observation_fingerprints")
        if obs_fps is not None:
            # Use injected fingerprints keyed by step index.
            last_obs_fps = set()
            for idx in range(len(executed_steps) - 2, len(executed_steps)):
                if 0 <= idx < len(obs_fps):
                    last_obs_fps.add(tuple(obs_fps[idx]))
            if len(last_obs_fps) > 1:
                return False
        else:
            # Compute from the real observation dicts.
            last_obs_fps = set()
            for step in last_two:
                obs = step.get("observation") or {}
                if isinstance(obs, dict):
                    last_obs_fps.add(
                        (obs.get("ok"), obs.get("status"), obs.get("tool_surface"))
                    )
            if len(last_obs_fps) > 1:
                return False
        # Same intent, same outcome, same candidates, same observation.
        # turn's authoritative requirements NEED tools.  If not, the loop
        # is cycling through a catalog that cannot serve this request.
        try:
            from core.execution_requirements import requirements_for

            reqs = requirements_for(
                effective_input,
                task_class=task_class,
                source_context=source_context,
            )
            if not reqs.tools_required:
                return True
        except Exception:
            pass
        return False

    def _tool_candidate_fingerprint(
        self, source_context: dict[str, Any] | None
    ) -> tuple[str, ...] | None:
        """A typed identity of the available tool candidate set.

        Tests may inject ``_tool_candidate_ids`` into source_context to
        simulate a changing catalog without calling the real spec builder.
        """
        injected = (source_context or {}).get("_tool_candidate_ids")
        if injected is not None:
            return tuple(sorted(str(x) for x in injected))
        try:
            from core.execution.capabilities import runtime_tool_specs

            specs = runtime_tool_specs()
            return tuple(
                sorted(
                    s.get("intent", "")
                    for s in specs
                    if isinstance(s, dict) and s.get("intent")
                )
            )
        except Exception:
            return None

    def _wants_fresh_info(self, text: str, *, interpretation: Any) -> bool:
        # Read over what the message ASKS for -- the request units of the one interpretation,
        # through the requirements authority's reader -- never over the material it describes.
        # Measured served 2026-09-16 (candidate 035dea9b): a design brief for a Telegram/Discord
        # community bot listed "recent errors" among a proposed admin panel's features; "recent"
        # matched here, the "integration" topic hint matched below, the fast live-info lane took
        # the hint tail, retrieved, paid for a wording call and widened the turn to
        # current-information while the authority read DIRECT.
        from core.execution_requirements import asked_text

        whole = " ".join(str(text or "").strip().lower().split())
        lowered = " ".join(asked_text(str(text or "")).strip().lower().split()) or whole
        if looks_like_explicit_lookup_request(lowered) or looks_like_public_entity_lookup_request(lowered):
            return True
        for marker in (
            "latest",
            "newest",
            "today",
            "current",
            "recent",
            "fresh",
            "just released",
            "release notes",
            "status page",
            "news",
            "update",
            "version",
            "price now",
            "weather",
            "forecast",
            "temperature",
            "search online",
            "check online",
            "look up",
            "browse",
            "on x",
            "on twitter",
            "on the web",
            "on web",
            "google",
        ):
            if marker in lowered:
                return True
        if lowered != whole:
            # The message describes a system or situation and asks about it: its nouns are the
            # subject of the question, not a request for the world's current state of them. A
            # topic hint is the weakest evidence this reader has, and it came from the description.
            return False
        hints = {str(item).lower() for item in getattr(interpretation, "topic_hints", []) or []}
        return bool({"news", "weather", "web", "telegram", "discord", "integration"} & hints)
