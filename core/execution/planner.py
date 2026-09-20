from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from core.response_constraints import parse_response_constraint
from core.runtime_execution_tools import (
    _find_folder_name_from_phrase,
    extract_observation_followup_hints,
    looks_like_advice_only_execution_prompt,
    looks_like_execution_request,
)
from core.runtime_flags import flag_enabled
from core.task_router import (
    build_task_envelope_for_request,
    looks_like_bounded_repo_repair_request,
    looks_like_explicit_lookup_request,
    looks_like_live_recency_lookup,
    looks_like_public_entity_lookup_request,
)

from .constants import (
    _APPEND_CONTENT_ONLY_RE,
    _BUILDER_RESEARCH_MARKERS,
    _CREATE_PATH_RE,
    _DIRECTORY_CREATE_MARKERS,
    _DIRECTORY_CREATE_QUESTION_RE,
    _ENTITY_LOOKUP_DROP_TOKENS,
    _ENTITY_LOOKUP_KEEP_SHORT_TOKENS,
    _EXACT_READBACK_RE,
    _EXPLICIT_WORKSPACE_READ_RE,
    _FOLDER_PATH_RE,
    _GENERIC_HIVE_TITLE_MARKERS,
    _HIVE_ACTION_PATTERNS,
    _HIVE_CREATE_PREFIXES,
    _INTEGRATION_DOMAIN_MARKERS,
    _INTO_PATH_RE,
    _LIVE_LOOKUP_AMBIGUOUS_MARKERS,
    _LIVE_LOOKUP_STRONG_MARKERS,
    _LOCAL_TOOL_AMBIGUOUS_MARKERS,
    _LOCAL_TOOL_STRONG_MARKERS,
    _NAMED_PATH_RE,
    _PATH_STOP_WORDS,
    _START_CODE_MARKERS,
    _TOOL_INVENTORY_MARKERS,
    _URL_RE,
    _VERB_NAME_FOLDER_RE,
    _extend_path_over_spaces,
    bound_chat_attachment_present,
    machine_diagnostics_intent,
    machine_disk_scope,
    machine_display_intent,
    machine_folder_search_intent,
    machine_largest_intent,
)
from .models import WorkflowPlannerDecision
from .write_demand import verbatim_request_text

_WINDOWS_PARENT_DIRECTORY_RE = re.compile(
    r"\b(?:in|under|inside)\s+[`\"']?(?P<path>[A-Za-z]:[^\r\n]+?)(?=\s+(?:create|make|write|add|put|place|save|read|list|with)\b|$)",
    re.IGNORECASE,
)

_CONVERSATIONAL_MEMORY_DECLARATION_RE = re.compile(
    r"^\s*(?:for|in)\s+(?:this|our)\s+(?:chat|conversation)\b.*"
    r"\b(?:please\s+)?remember\s+(?:it|this|that)\b",
    re.IGNORECASE | re.DOTALL,
)
_CONVERSATIONAL_MEMORY_RECALL_RE = re.compile(
    r"^\s*(?:what|which)\b.*\b(?:did|have)\s+(?:i|we)\s+(?:just\s+)?"
    r"(?:say|tell|name|mention|call|ask|record|remember)\b",
    re.IGNORECASE | re.DOTALL,
)
_CONVERSATIONAL_MEMORY_ACTION_RE = re.compile(
    r"\b(?:archive|build|copy|create|delete|deploy|download|edit|execute|fetch|install|"
    r"list|move|open|remove|run|save|search|send|upload|write)\b",
    re.IGNORECASE,
)


def is_conversational_memory_declaration(text: str) -> bool:
    """Return whether a user is recording a fact for the current chat, not requesting work."""
    normalized = " ".join(str(text or "").strip().split())
    lowered = normalized.lower()
    if not normalized or "remember to" in lowered:
        return False
    if not _CONVERSATIONAL_MEMORY_DECLARATION_RE.search(normalized):
        return False
    return not _CONVERSATIONAL_MEMORY_ACTION_RE.search(normalized)


def is_conversational_memory_recall(text: str) -> bool:
    """Return whether the user is asking about an earlier turn in this chat."""
    normalized = " ".join(str(text or "").strip().split())
    return bool(normalized and _CONVERSATIONAL_MEMORY_RECALL_RE.search(normalized))


def _contains_word_bounded_marker(text: str, markers: tuple[str, ...]) -> bool:
    return any(
        re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(marker)}(?![A-Za-z0-9_])",
            text,
        )
        is not None
        for marker in markers
    )


def _distinct_word_bounded_markers(text: str, markers: tuple[str, ...]) -> set[str]:
    return {
        marker
        for marker in markers
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(marker)}(?![A-Za-z0-9_])", text) is not None
    }


def has_explicit_tool_intent_request(user_text: str, *, task_class: str) -> bool:
    """Return whether the text itself asks the tool loop to act.

    This intentionally excludes the optional always-on catalog flag. Callers use it
    when a tool-shaped model reply failed before any tool ran and need to decide
    whether normal conversation is an honest fallback.
    """
    from core.raw_output_contract import parse_structured_user_directive
    from core.retrieval_constraints import analyze_retrieval_constraints
    from core.within_turn_retraction import live_request_after_retraction

    structured_directive = parse_structured_user_directive(user_text)
    intent_surface = (
        structured_directive.task_text if structured_directive is not None else user_text
    )
    # An instruction the same turn takes back is not an instruction. Measured: "Search my local
    # workspace for `password.txt`. WAIT, STOP. Cancel the search immediately. Instead, write a
    # 2-line javascript function" planned and ran `workspace.search_text` against the very filename
    # the user had just withdrawn. The neighbouring turn survived only because it happened to phrase
    # its retraction as a prohibition ("do NOT search the web"), which `analyze_retrieval_constraints`
    # already reads; retraction phrasing had no reader at all.
    _live_remainder = live_request_after_retraction(intent_surface)
    if _live_remainder is not None:
        intent_surface = _live_remainder
    constraints = analyze_retrieval_constraints(intent_surface)
    structured_constraints = (
        analyze_retrieval_constraints(". ".join(structured_directive.constraints))
        if structured_directive is not None
        else None
    )
    if structured_constraints is not None and structured_constraints.forbids_all_tools:
        return False
    if constraints.forbids_all_tools:
        return False
    text = constraints.eligible_text.strip()
    lowered = text.lower()
    if not lowered:
        return False
    if structured_constraints is not None and structured_constraints.forbids_external_retrieval:
        try:
            from core.execution_requirements import requirements_for

            requirements = requirements_for(
                text,
                task_class=task_class,
                source_context={},
            )
            external_toolsets = {
                "fresh_lookup",
                "market_prices",
                "news",
                "weather",
                "web_fetch",
                "web_search",
            }
            if requirements.tools_required and set(requirements.allowed_toolsets) <= external_toolsets:
                return False
        except Exception:
            pass
        if (
            _URL_RE.search(text)
            or looks_like_explicit_lookup_request(text)
            or looks_like_public_entity_lookup_request(text)
            or looks_like_live_recency_lookup(text)
        ):
            return False
    # Exact literal output is already a closed text contract. JSON-looking directives used to be
    # misclassified as config work because their own "No JSON wrapper" constraint contains the
    # word json, which then made this function expose the action-plan/tool catalogue. Keep this
    # gate independently safe even if a caller bypasses the front-door preemption.
    if str(user_text or "").lstrip().startswith("{"):
        try:
            from core.raw_output_contract import parse_raw_output_contract

            literal_contract = parse_raw_output_contract(user_text)
            if literal_contract is not None and literal_contract.exact_text is not None:
                return False
        except Exception:
            pass
    if not constraints.forbids_external_retrieval and constraints.forbids_candidate(text):
        return False
    from core.stipulated_frame import stipulated_frame_forbids_retrieval

    if stipulated_frame_forbids_retrieval(text):
        return False
    if looks_like_advice_only_execution_prompt(lowered):
        return False
    if (
        task_class in {"integration_orchestration", "system_design", "research"}
        and any(marker in lowered for marker in _BUILDER_RESEARCH_MARKERS)
        and any(marker in lowered for marker in _INTEGRATION_DOMAIN_MARKERS)
    ):
        return False
    if task_class == "integration_orchestration":
        return True
    # The positive readings below answer "does the text ASK the tool loop to act", so they read
    # the asking units of the message -- its request units and the statements they refer back to
    # (`core.execution_requirements.asked_text`) -- never the material it describes. The
    # prohibition, retraction and advice-only readings above stay on the whole surface: a "Do NOT
    # use tools" line is a constraint, which the asking text leaves out. Measured served
    # 2026-09-16 (candidate 035dea9b): a design brief listing "check balances", "view bot status
    # and recent errors" and "restart selected bot services" among a proposed system's features
    # opened a tool-selection round on every turn; on a pinned paid model the round was refused
    # before sending and its refusal was then recorded as a provider transport failure.
    try:
        from core.execution_requirements import asked_text

        asked = str(asked_text(text) or "").strip()
    except Exception:
        asked = ""
    if asked:
        text = asked
        lowered = text.lower()
    if _URL_RE.search(text):
        return True
    if looks_like_explicit_lookup_request(text) or looks_like_public_entity_lookup_request(text):
        return True
    if looks_like_live_recency_lookup(text):
        return True
    if _contains_word_bounded_marker(lowered, _LIVE_LOOKUP_STRONG_MARKERS):
        return True
    if _contains_word_bounded_marker(lowered, _LOCAL_TOOL_STRONG_MARKERS):
        return True
    # Ambiguous markers are ordinary English words that also mean something with zero tool
    # intent ("check", "find", "space", "schedule", ...). One bare hit proved to fire on plain
    # chat ("check this couch for comfort before purchasing"), so it only counts once a SECOND
    # distinct marker -- ambiguous or strong, from either list -- also appears in the same
    # message, mirroring the disk STRONG-phrase/AMBIGUOUS-cue co-occurrence rule below.
    ambiguous_hits = _distinct_word_bounded_markers(
        lowered, _LIVE_LOOKUP_AMBIGUOUS_MARKERS
    ) | _distinct_word_bounded_markers(lowered, _LOCAL_TOOL_AMBIGUOUS_MARKERS)
    if len(ambiguous_hits) >= 2:
        return True
    if looks_like_execution_request(text, task_class=task_class):
        return True
    if any(marker in lowered for marker in _HIVE_ACTION_PATTERNS):
        return True
    if "hive" in lowered and any(word in lowered for word in ("claim", "topic", "progress", "result", "task")):
        return True
    padded = f" {lowered} "
    if any(
        marker in padded
        for marker in (
            " proceed ", " do it ", " do all ", " go ahead ", " carry on ",
            " start working ", " continue ", " yes proceed ", " yes do it ",
            " yes go ahead ", " yes continue ", " deliver it ", " submit it ",
            " execute ", " run it ", " just do it ",
        )
    ):
        return True
    compact = lowered.strip(" \t\n\r?!.,")
    return compact in {
        "proceed",
        "do it",
        "do all",
        "go ahead",
        "carry on",
        "continue",
        "start working",
        "yes",
        "yes proceed",
        "yes do it",
        "ok do it",
        "ok proceed",
        "ok go ahead",
        "deliver it",
        "submit it",
        "execute",
        "run it",
        "just do it",
        "yes pls",
        "yes please",
        "all good carry on",
        "proceed with next steps",
        "proceed with that",
    }


_ATTACHMENT_REFERENCE_RE = re.compile(
    r"\b(?:the|this|these|those|that|my|our|your)?\s*"
    r"(?:attached|uploaded|enclosed)\s+"
    r"(?:text\s+|image\s+|photo\s+)?"
    r"(?:files?|photos?|images?|pictures?|documents?|docs?|attachments?|screenshots?|notes?|scripts?|logs?)\b"
    r"|\b(?:the|this|my)?\s*attachments?\b",
    re.IGNORECASE,
)


def _chat_attachment_names(source_context: dict[str, Any] | None) -> tuple[str, ...]:
    """Display names of the attachments the ingress bound to this turn -- never a client item."""
    names: list[str] = []
    for item in list((source_context or {}).get("external_evidence") or []):
        if isinstance(item, dict) and item.get("origin") == "chat_attachment" and item.get("attachment_id"):
            name = str(item.get("name") or "").strip()
            if name:
                names.append(name)
    return tuple(names)


def _without_attachment_references(text: str, names: tuple[str, ...]) -> str:
    """The turn's text with every reference to an attachment removed, so that a file mentioned
    only because it was attached cannot read as a request to reach a file on this machine."""
    neutral = _ATTACHMENT_REFERENCE_RE.sub(" ", str(text or ""))
    for name in sorted(names, key=len, reverse=True):
        neutral = re.sub(r"`?" + re.escape(name) + r"`?", " ", neutral, flags=re.IGNORECASE)
    return " ".join(neutral.split())


def should_attempt_tool_intent(
    user_text: str,
    *,
    task_class: str,
    source_context: dict[str, Any] | None = None,
) -> bool:
    source_context = dict(source_context or {})
    surface = str(source_context.get("surface") or "").strip().lower()
    platform = str(source_context.get("platform") or "").strip().lower()
    tool_capable_surfaces = {"channel", "openclaw", "api", "cli", "terminal", "local", ""}
    tool_capable_platforms = {"openclaw", "telegram", "discord", "local", "cli", ""}
    if surface not in tool_capable_surfaces and platform not in tool_capable_platforms:
        return False

    from core.raw_output_contract import (
        parse_raw_output_contract,
        parse_structured_user_directive,
    )
    from core.retrieval_constraints import analyze_retrieval_constraints
    from core.within_turn_retraction import live_request_after_retraction

    structured_directive = parse_structured_user_directive(user_text)
    intent_surface = (
        structured_directive.task_text if structured_directive is not None else user_text
    )
    # An instruction the same turn takes back is not an instruction. Measured: "Search my local
    # workspace for `password.txt`. WAIT, STOP. Cancel the search immediately. Instead, write a
    # 2-line javascript function" planned and ran `workspace.search_text` against the very filename
    # the user had just withdrawn. The neighbouring turn survived only because it happened to phrase
    # its retraction as a prohibition ("do NOT search the web"), which `analyze_retrieval_constraints`
    # already reads; retraction phrasing had no reader at all.
    _live_remainder = live_request_after_retraction(intent_surface)
    if _live_remainder is not None:
        intent_surface = _live_remainder
    constraints = analyze_retrieval_constraints(intent_surface)
    if constraints.forbids_all_tools:
        return False
    text = constraints.eligible_text.strip()
    lowered = text.lower()
    if not lowered:
        return False
    if not constraints.forbids_external_retrieval and constraints.forbids_candidate(text):
        return False

    if constraints.forbids_external_retrieval:
        try:
            from core.execution_requirements import requirements_for

            requirements = requirements_for(user_text, task_class=task_class, source_context=source_context)
            if "explicit_retrieval_prohibition" in requirements.reason_codes:
                return False
        except Exception:
            pass
        # A global no-web directive must not disable an unrelated local-file/tool request.  With no
        # explicit positive non-retrieval action left after removing the directive, however, there
        # is no reason to expose a catalogue from which the model could re-select the forbidden web
        # tool merely because always-on catalog mode or a coarse ``research`` label is active.
        if not has_explicit_tool_intent_request(text, task_class="unknown"):
            return False

    from core.stipulated_frame import stipulated_frame_active

    if stipulated_frame_active(text, source_context=source_context):
        return False

    # Set by `prepare_live_info_request` when a price-shaped request names something the
    # deterministic alias table cannot resolve. The keyword allowlist below has no vocabulary for
    # "BNB" / "ARB" / an unlisted ticker, so without this override the turn would decline the fast
    # path, skip the AI-first chat lane too, and STILL never reach a tool -- the same dead end
    # (an ungrounded model free-forming a tool-call shape that nothing executes) that produced the
    # 2026-08-03 incident. An explicit runtime signal, not a guess: something upstream already
    # determined this turn needs a real tool.
    if bool(source_context.get("live_info_partial_unresolved")):
        return True

    # Recording a fact for this chat is ordinary conversation. Do not expose it to
    # the model's tool schema merely because the optional catalog is enabled.
    if is_conversational_memory_declaration(text) or is_conversational_memory_recall(text):
        return False

    # A session in the middle of email work -- an email tool it executed recently, or a draft it
    # owns that is not finished (core.email_work_state reads the execution ledger and the draft
    # store, never these words) -- admits its follow-ups to the tool lane. Measured 2026-09-14
    # against this gate: after email.read had run in the session, "Add that the kiln is at the north
    # studio.", "Read me the final version before it goes." and "Looks good. Approve it and send it."
    # all returned False, because none of them names a mailbox; "Draft a reply saying Wednesday at
    # 10:00 works" was refused below as an authoring request. The admission sits BELOW the
    # user-stated constraints above (a tool prohibition, a retraction, a stipulated hypothetical, a
    # statement for this chat's memory) and ABOVE the heuristics below that infer a text-only
    # deliverable: in an email session the authored reply IS the reviewable draft, and saving a
    # draft never sends anything. The model still decides whether any email tool is called.
    from core.email_work_state import active_email_work_intents

    if active_email_work_intents(source_context):
        return True

    # A FIRST-TURN mailbox request is an action through the email tools, never a topic to
    # converse about — the same law the session admission above states for follow-ups, read
    # through the SAME typed recognizer the tool offer seats from
    # (core.execution_requirements._email_account_action_demand: it enforces the email lane's
    # toggles and the account-anchor/composition distinction, so this gate and the offer
    # cannot disagree). Measured 2026-09-20 through the demand-owned mixed turn:
    # "Check my unread orchard supplier mail, summarize it" classified ordinary_plain_text_chat,
    # the sub-turn answered from memory, and no email tool ran — the dead end the recognizer's
    # own docstring names, reached because this gate never asked it about turn one.
    try:
        from core.execution_requirements import _email_account_action_demand

        if _email_account_action_demand(text):
            return True
    except Exception:
        pass

    # A few ordinary knowledge/text requests in one message still need one plain answer, not the
    # always-on tool catalogue and its large intent-selection call.  The detector is intentionally
    # limited to non-operational request heads; file/web/machine/action verbs never enter this lane.
    from core.plain_task_routing import is_ordinary_multi_part_plain_task

    if is_ordinary_multi_part_plain_task(text, task_class=task_class):
        return False

    # A response-shape request is still ordinary conversation unless the same
    # turn contains a concrete tool action.  The optional always-on catalog must
    # not turn nouns such as "workspace" into an executable request merely
    # because the user also asked for a short answer.
    if (
        parse_response_constraint(text) is not None
        and not has_explicit_tool_intent_request(text, task_class=task_class)
    ):
        return False

    # A typed raw/plain-text envelope is an ordinary answer contract unless its admitted TASK
    # positively requests a tool.  Format words ("JSON", "search tools", "markdown") must never
    # be promoted into task intent, and the always-on catalog below must not overrule this typed
    # distinction. Genuine file/search/action tasks still pass because the decision is made from
    # ``task_text``; genuine JSON output remains outside this raw/no-JSON guard.
    raw_contract = parse_raw_output_contract(user_text)
    if (
        structured_directive is not None
        and raw_contract is not None
        and raw_contract.no_json
        and not has_explicit_tool_intent_request(user_text, task_class=task_class)
    ):
        return False

    # "Which file format is defined by this standard?" names a semantic category, not a local
    # file. The always-on catalogue sits below this point and intentionally fails open for novel
    # capabilities; without this typed distinction, a knowledge question spends a tool-selection
    # call and emits false tool activity before eventually recovering to plain text.
    from core.agent_runtime.intent_claims import semantic_file_type_request

    if semantic_file_type_request(text):
        return False

    # Asking for the TEXT of an artifact is authoring, not an action (MF-13, fourth seam). With
    # classification already repaired (task_class=chat_conversation, receipts 13:08), "write me a
    # quick shell command that renames every .png on my Desktop to lowercase" still reached
    # output_mode=tool_intent from HERE: the keyword allowlist below reads "shell"/"Desktop" as
    # machine work. The deliverable is text the user will run themselves; the turn asks nothing
    # of this machine, and the failed tool intent shipped "I wasn't able to turn that into a
    # completed action" over a plain generative answer.
    from core.instructional_request import asks_for_instructions_not_execution

    if asks_for_instructions_not_execution(text):
        return False

    # A turn that CARRIES the file it talks about asks nothing of this machine's tools for it.
    # Measured 2026-09-02 on the served composer: "Using the attached file, which capital is
    # named?" with facts.txt attached went to tool_intent (the always-on catalog below reads
    # "file" as machine work), the model was REQUIRED to call a tool, chose sandbox.run_command,
    # and the turn ended "I wasn't able to turn that into a completed action" over an answer that
    # was sitting in the prompt. The attachment is data already inlined for the model; the words
    # that merely refer to it are removed before the tool question is asked, and only an explicit
    # request that remains ("...and compare it with README.md") keeps the tool lane.
    attachment_names = _chat_attachment_names(source_context)
    if attachment_names:
        # A bound-material turn can still owe this machine a real observation: "Describe the
        # attached video; what is my screen resolution?" asks a host display fact NEXT TO the
        # material. The attachment-aware display authority -- the same one the direct machine
        # fast path declines whole-turn and the workflow planner plans as a substep -- says a
        # host-owned display ask is present. Without admission here the turn never reaches that
        # planner step, no model is called at all, and the local-fact backstop refuses BOTH
        # obligations (measured 2026-09-03: route local_fact_no_tool, 2 demands minted,
        # 0 satisfied, zero provider calls). Scoped to bound material: a display question with
        # no attachment is owned whole-turn by the machine fast path long before this gate.
        if bound_chat_attachment_present(source_context) and machine_display_intent(
            text, source_context=source_context
        ) is not None:
            return True
        if not has_explicit_tool_intent_request(
            _without_attachment_references(text, attachment_names), task_class=task_class
        ):
            return False

    # Everything above this line is structural: a surface that cannot run tools, or no text to
    # act on. Everything below it is the keyword allowlist — an attempt to guess, from the raw
    # words, whether the user wanted a tool, decided BEFORE the model is consulted.
    #
    # What that guess costs, measured 2026-07-28: all three of vool-video-director's own
    # `interface.defaultPrompt` examples return False here — including "prepare this local image
    # as an identity-safe reference pack", copied verbatim from the manifest — so a tool the user
    # installed is unreachable by the exact wording its author shipped. The matcher has no
    # knowledge of installed plugins and cannot acquire any, because it runs before the catalog
    # is assembled. Every plugin VOOL ever gains inherits this failure by construction.
    #
    # (Note for anyone tracing the "I couldn't map that cleanly to a real action" reports: those
    # are NOT this gate. Phrasings like "give me a rundown of <path>" pass here and fail later at
    # core/tool_intent_executor.py:237 with status "missing_intent" — the loop ran and the model
    # returned an unparseable payload. Native tool schemas and the prose-repair fallback address
    # that; this flag addresses reachability.)
    #
    # With the flag on, the catalog is offered and the model decides. A model handed tools it
    # does not need simply answers; a model never handed them cannot act however it is asked.
    if flag_enabled("always_on_tool_catalog"):
        return True
    # A turn the runtime ITSELF classified as research needs retrieval by definition. This is not
    # the keyword guess the block above warns about -- `task_class` is the classifier's own verdict,
    # produced before this gate runs, and "research" means "go and find out".
    #
    # Measured 2026-08-05, session openclaw:b964a2c7056128822d22: "most produced commercial
    # aircraft ... use authoritative sources and do not guess" classified as `research`, returned
    # False here, and never reached a tool. Its whole trace carried no search, no fetch and no tool
    # call, and the model narrated sources that were never retrieved. The keyword allowlist has no
    # vocabulary for an aviation question, exactly as it had none for "BNB"/"ARB" above and none for
    # vool-video-director's own shipped example prompts -- the same failure by construction, third
    # instance recorded in this function.
    #
    # Costs a tool catalog on a turn that may not need one; per this function's own reasoning, a
    # model handed tools it does not need simply answers, while a model never handed them cannot act
    # however it is asked.
    if str(task_class or "").strip().lower() in {"research", "chat_research"}:
        return True
    # A request that PROMISES evidence needs a tool whatever it was classified as. Keying only on
    # `task_class` makes tool reachability hostage to a keyword classifier, and that classifier
    # misfires on ordinary words: measured 2026-08-06, the EV brief -- which says "Use current
    # evidence retrieved during this turn from Tesla or Nissan disclosures" -- classified as
    # `config`, because it asks for "the most common battery-capacity CONFIGURATION". It then
    # routed to `business_advisory`, this gate returned False, and no tool was ever offered.
    #
    # Every other fix in the routing chain was correct and none of them fired, because the turn was
    # never called research. `answer_mode_for` reads the request's own contract ("use current
    # evidence", "do not guess", exact statistics, a per-country breakdown) rather than a label,
    # and it is the same discriminator the lane policy and the curiosity gate already use -- so
    # "does this request demand evidence" keeps one owner instead of three that can disagree.
    try:
        from core.execution_requirements import requirements_for

        if requirements_for(text, task_class=task_class, source_context=source_context).tools_required:
            return True
    except Exception:
        pass
    if has_explicit_tool_intent_request(text, task_class=task_class):
        return True
    # Unifying fail-open fix for Findings B and C (2026-08-04): a keyword-allowlist miss above
    # used to be final -- "what files are here" or "what is this project about" fell straight to
    # plain, tools-less chat, once because the listing vocabulary had no word for "files"/"here",
    # once because the request never looked like an execution request at all. With a REAL
    # workspace bound and SOME plausible connection to wanting information about it (a fuzzy,
    # typo-tolerant match against the audit/listing/overview vocabulary, deliberately excluding
    # ordinary small talk), let the tool catalog through so the model decides -- rather than
    # deciding "no tool" here, before the model ever sees the request.
    workspace_root = str(
        source_context.get("workspace") or source_context.get("workspace_root") or ""
    ).strip()
    if workspace_root:
        # Imported lazily: `core.agent_runtime`'s package `__init__` pulls in a long chain that
        # circles back to this module at import time, so a module-level import here deadlocks.
        from core.agent_runtime.workspace_intent_detection import plausibly_about_bound_workspace

        if plausibly_about_bound_workspace(text):
            return True

    # A CONTEXTUAL FOLLOW-UP of a tool-bearing turn keeps its family instead of
    # falling to zero tools. The allowlist above has no vocabulary for "so?" or
    # "and the skills in there?" — measured 2026-07-29, such follow-ups classified
    # plain_text and the model received NO tools at all. Admission is narrow and
    # deterministic: the text must be follow-up-shaped (few words, no task of its
    # own) AND this session must have a TTL'd record of families it was recently
    # offered. The inherited offer is the same bounded 8-seat set as any turn.
    from core.tool_offer_state import followup_inherited_families

    return bool(followup_inherited_families(text, source_context))


def _looks_like_followup_resume_request(text: str) -> bool:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return False
    padded = f" {lowered} "
    if any(
        marker in padded
        for marker in (
            " proceed ",
            " do it ",
            " do all ",
            " go ahead ",
            " carry on ",
            " continue ",
            " start working ",
            " yes proceed ",
            " yes do it ",
            " yes continue ",
            " execute ",
            " run it ",
            " just do it ",
        )
    ):
        return True
    compact = lowered.strip(" \t\n\r?!.,")
    return compact in {
        "proceed",
        "do it",
        "do all",
        "go ahead",
        "carry on",
        "continue",
        "start working",
        "yes",
        "yes proceed",
        "yes do it",
        "ok do it",
        "ok proceed",
        "ok go ahead",
        "deliver it",
        "submit it",
        "execute",
        "run it",
        "just do it",
        "yes pls",
        "yes please",
        "all good carry on",
        "proceed with next steps",
        "proceed with that",
    }


def _entity_lookup_query_variants(text: str) -> tuple[str, str]:
    normalized = " ".join(str(text or "").strip().lower().split())
    if not normalized:
        return "", ""

    tokens: list[str] = []
    for token in re.findall(r"[a-z0-9\.]+", normalized):
        clean = token.strip(".")
        if not clean or clean in _ENTITY_LOOKUP_DROP_TOKENS:
            continue
        if clean == "x.com":
            clean = "x"
        if len(clean) == 1 and clean not in _ENTITY_LOOKUP_KEEP_SHORT_TOKENS:
            continue
        tokens.append(clean)

    if not tokens:
        return normalized, normalized

    primary_tokens = list(dict.fromkeys(tokens))[:6]
    retry_tokens = [
        re.sub(r"(.)\1+", r"\1", token) if len(token) >= 3 and token not in {"solana", "twitter"} else token
        for token in primary_tokens
    ]
    retry_tokens = list(dict.fromkeys(token for token in retry_tokens if token))
    if retry_tokens == primary_tokens:
        if "x" in retry_tokens and "twitter" not in retry_tokens:
            retry_tokens.append("twitter")
        elif "profile" not in retry_tokens:
            retry_tokens.append("profile")

    primary_query = " ".join(primary_tokens).strip() or normalized
    retry_query = " ".join(retry_tokens).strip() or primary_query
    return primary_query, retry_query


def _looks_like_tool_inventory_request(text: str) -> bool:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return False
    return any(marker in lowered for marker in _TOOL_INVENTORY_MARKERS)


# Vocabulary that means nothing but git. "working tree", "uncommitted", "untracked" and "unstaged"
# are unambiguous on their own. "staged" is NOT -- "a staged rollout" and "a staged migration" are
# ordinary English -- so it only counts in the predicative shapes git actually uses it in
# ("anything staged", "what's staged", "staged changes").
_GIT_ONLY_VOCAB_RE = re.compile(
    r"\bworking\s+(?:tree|copy)\b"
    r"|\buncommitted\b|\buntracked\b|\bunstaged\b"
    r"|\b(?:anything|something|what(?:'|’)?s|which|files?|changes?)\s+(?:is\s+|are\s+)?staged\b"
    r"|\bstaged\s+(?:changes?|files?)\b",
    re.IGNORECASE,
)
# "what changed since the last commit" is a status question wearing different words.
_GIT_WHAT_CHANGED_RE = re.compile(
    r"\b(?:what|which)(?:'|’)?s?\s+(?:has\s+|have\s+)?(?:changed|modified)\b"
    r"|\bwhat\s+files?\s+(?:have\s+|has\s+)?(?:i\s+|we\s+)?(?:changed|modified|touched)\b",
    re.IGNORECASE,
)


def _planned_git_payload(text: str) -> tuple[str, dict[str, Any]] | None:
    # Sentence punctuation is separated from the words BEFORE the space-padded marker tests below,
    # because every one of them is a `" marker "` substring test and terminal punctuation welds
    # itself to the last word. Measured on this branch: "Show git status." produced
    # " show git status. ", which does not contain " git status ", so the whole deterministic git
    # lane declined and the turn went to a model that has no repository to look at -- while the
    # identical sentence without the full stop answered from the tool. A user typing a complete
    # sentence is not a different request.
    cleaned = re.sub(r"[.,;:!?]+", " ", str(text or "").strip().lower())
    lowered = f" {' '.join(cleaned.split())} "
    if not lowered.strip():
        return None
    if any(
        marker in lowered
        for marker in (" create ", " write ", " edit ", " change ", " patch ", " delete ", " remove ", " push ", " pull ", " merge ", " checkout ", " rebase ")
    ):
        if not any(marker in lowered for marker in (" git status ", " git diff ")):
            return None
    # This gate used to demand the literal word "git", "repo", "branch" or "commit" before it would
    # look at the status vocabulary below. So "is the working tree clean" was rejected here, reached
    # a model with no tool result, and came back "The working tree is clean. No changes staged or
    # unstaged." -- about a directory that is not a git repository at all. Measured on the deployed
    # build 2026-07-30. The tool's real answer was one call away and says so plainly.
    git_only_vocab = _GIT_ONLY_VOCAB_RE.search(lowered) is not None
    mentions_repo = git_only_vocab or any(
        marker in lowered
        for marker in (" git ", " repo ", " repository ", " branch ", " branches ", " commit ", " commits ")
    )
    if not mentions_repo:
        return None
    if any(marker in lowered for marker in (" git diff ", " show diff ", " unstaged diff ", " staged diff ", " git patch ")):
        return ("planned_workspace_git_diff", {"intent": "workspace.git_diff", "arguments": {}})
    if git_only_vocab or any(
        marker in lowered
        for marker in (" git status ", " dirty ", " clean repo ", " clean working tree ")
    ):
        return ("planned_workspace_git_status", {"intent": "workspace.git_status", "arguments": {}})
    if _GIT_WHAT_CHANGED_RE.search(lowered):
        # "whats changed since the last commit" carries a repo word but matched no status marker,
        # so it fell through to a model too.
        return ("planned_workspace_git_status", {"intent": "workspace.git_status", "arguments": {}})
    recent_commits_match = re.search(r"\b(?:last|recent)\s+(?P<count>\d+)\s+commits?\b", lowered)
    if recent_commits_match is not None:
        try:
            recent_limit = max(1, min(int(recent_commits_match.group("count") or "3"), 10))
        except Exception:
            recent_limit = 3
        return ("planned_workspace_git_summary", {"intent": "workspace.git_summary", "arguments": {"recent_limit": recent_limit}})
    if any(
        marker in lowered
        for marker in (
            " how many branches ",
            " how many commits ",
            " branch count ",
            " commit count ",
            " commits today ",
            " commits yesterday ",
            " branch inventory ",
            " recent commits ",
            " git summary ",
            " git activity ",
            " current branch ",
            " head commit ",
        )
    ):
        return ("planned_workspace_git_summary", {"intent": "workspace.git_summary", "arguments": {}})
    if " what branch and commit " in lowered or (
        " branch " in lowered
        and " commit " in lowered
        and any(marker in lowered for marker in (" right now ", " running on ", " current ", " are you on "))
    ):
        return ("planned_workspace_git_summary", {"intent": "workspace.git_summary", "arguments": {}})
    return None



# Every search pattern anchors the query group with `$`, so a lazy `.+?` still runs to end of line:
# "search this workspace for plan_tool_workflow, THEN read the file that defines it and explain what
# it does" searched for the whole 12-word sentence and reported "No text matches" for a symbol that
# is actually present -- a false negative the runtime then returned as ok=True. Cut at the first
# chaining connective so the query is the SYMBOL and the following clauses stay separate work.
_SEARCH_QUERY_CHAIN_RE = re.compile(
    r"(?:,\s*(?:and\s+)?then\b|;\s*(?:and\s+)?then\b|\s+and\s+then\b|\s+then\s+|"
    r"\s+after\s+that\b|\s+and\s+(?:read|open|show|explain|tell|summari[sz]e|list)\b|"
    r"\s+and\s+explain\b|\.\s)",
    re.IGNORECASE,
)


def _trim_search_query(raw: str) -> str:
    """Return just the search term, dropping any chained follow-up clauses."""
    query = str(raw or "").strip().strip("`\"'")
    if not query:
        return ""
    cut = _SEARCH_QUERY_CHAIN_RE.search(query)
    if cut:
        query = query[: cut.start()].strip()
    return query.strip().strip("`\"'").rstrip(",.;:")


def _planned_workspace_search_payload(text: str) -> tuple[str, dict[str, Any]] | None:
    normalized = " ".join(str(text or "").strip().split())
    if not normalized:
        return None
    patterns = (
        re.compile(
            r"\b(?:find|search|look for)\s+(?:a\s+)?file(?:s)?\s+(?:in|inside|under)\s+(?:(?:this|the|my|our|its)\s+)?(?:workspace|repo(?:sitory)?|project|codebase)\s+(?:mentioning|containing|matching)\s+[`\"']?(?P<query>.+?)[`\"']?$",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bsearch\s+(?:(?:this|the|my|our|its)\s+)?(?:workspace|repo(?:sitory)?|project|codebase)\s+for\s+[`\"']?(?P<query>.+?)[`\"']?$",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:look\s+through|scan)\s+(?:the\s+)?(?:workspace|repo(?:sitory)?|project)\s+and\s+find\s+[`\"']?(?P<query>.+?)[`\"']?$",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bshow\s+me\s+(?:a\s+)?file(?:s)?\s+(?:in|inside|under)\s+(?:(?:this|the|my|our|its)\s+)?(?:workspace|repo(?:sitory)?|project|codebase)\s+(?:that\s+mentions?|matching|containing)\s+[`\"']?(?P<query>.+?)[`\"']?$",
            re.IGNORECASE,
        ),
    )
    for pattern in patterns:
        match = pattern.search(normalized)
        if not match:
            continue
        query = _trim_search_query(str(match.group("query") or ""))
        if not query:
            continue
        return (
            "planned_workspace_search_text",
            {"intent": "workspace.search_text", "arguments": {"query": query, "limit": 10}},
        )
    code_query = _code_search_query(normalized)
    if code_query:
        return (
            "planned_workspace_search_text",
            {"intent": "workspace.search_text", "arguments": {"query": code_query, "limit": 10}},
        )
    return None


# The patterns above all require the sentence to NAME the workspace ("search the codebase for X").
# Seven of eight live phrasings did not, and none of them reached this tool. Measured on the
# deployed build 2026-07-30: "grep for _GROUNDED_LISTING_VERDICTS", "where is explicit_path_in
# defined", "which files mention list_directory", "show me where count_only is used" and "hunt down
# _ASKS_HOW_MANY_RE in the repo" each returned "I couldn't map that cleanly to a real action" after
# about a minute -- and "find all references to directories_only" was sent to WEB SEARCH, which
# answered with MDN Web Docs pages.
_CODE_SEARCH_VERB_RE = re.compile(
    r"\bgrep\b"
    r"|\b(?:find|show|list)\s+(?:me\s+)?(?:all\s+)?(?:uses?|usages?|references?|occurrences?|call\s?sites?)\b"
    r"|\breferences?\s+to\b"
    r"|\bwhere\s+(?:is|are)\b[^.\n]{0,60}?\b(?:defined|declared|implemented|wired|used|set|called)\b"
    r"|\bwhere\b[^.\n]{0,40}?\bis\s+used\b"
    r"|\b(?:which|what)\s+files?\s+(?:mention|contain|use|reference|have)\b"
    r"|\blook\s+for\s+the\s+(?:string|text|symbol|function|class|constant|name)\b"
    r"|\bhunt\s+(?:down|for)\b",
    re.IGNORECASE,
)
# Naming the repository is enough on its own -- "hunt down X in the repo".
_IN_THE_CODEBASE_RE = re.compile(
    r"\bin\s+(?:this|the|my|our)\s+(?:workspace|repo(?:sitory)?|project|codebase|code\s?base|source)\b"
    r"|\bin\s+(?:the\s+)?code\b",
    re.IGNORECASE,
)
# A question about the internet is not a question about this checkout.
_SEARCH_MEANS_THE_WEB_RE = re.compile(
    r"\b(?:web|internet|online|google|duckduckgo|stack\s?overflow|docs?\s+online|news)\b",
    re.IGNORECASE,
)
# What a code search is FOR: an identifier. `directories_only`, `_ASKS_HOW_MANY_RE` and
# `machine_path_listing_intent` are not phrases anyone means to type into a search engine.
# The third alternative is a LEADING-underscore name with no second underscore: `_percent`, `_trim`,
# `_run`. The first alternative cannot match those -- it requires an underscore to appear after the
# opening character -- and that shape is the most common private symbol in this codebase. Measured
# live 2026-07-31: "find all uses of _percent in the codebase" fell through to the trailing-phrase
# branch and searched the workspace for the literal string "of _percent in the codebase", which
# reported "No text matches" for a symbol that is defined in core/operator/handlers.py.
_CODE_IDENTIFIER_RE = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b|\b[a-z]+[A-Z][A-Za-z0-9]*\b|_[A-Za-z][A-Za-z0-9]*\b"
)
_QUOTED_TOKEN_RE = re.compile(r"[`\"'](?P<token>[^`\"']{2,80})[`\"']")
# Verb shapes that take a symbol NAME and nothing else. Each captures one bare token.
_CODE_NEEDLE_SHAPES = (
    re.compile(r"\bgrep\s+(?:for\s+)?(?P<query>[^\s.?!]+)", re.IGNORECASE),
    re.compile(
        r"\bwhere\s+(?:is|are)\s+(?P<query>[^\s.?!]+)\s+"
        r"(?:defined|declared|implemented|wired|used|set|called)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:which|what)\s+files?\s+(?:mention|contain|use|reference|have)\s+(?P<query>[^\s.?!]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:find|show|list)\s+(?:me\s+)?(?:all\s+)?(?:uses?|usages?|references?|occurrences?|call\s?sites?)\s+(?:of|to)\s+"
        r"(?P<query>[^\s.?!]+)",
        re.IGNORECASE,
    ),
    re.compile(r"\bshow\s+me\s+where\s+(?P<query>[^\s.?!]+)\s+is\s+used", re.IGNORECASE),
)
# A determiner in the symbol slot means the sentence names a topic, not a symbol.
_CODE_NEEDLE_STOPWORDS = frozenset(
    {"the", "a", "an", "this", "that", "these", "those", "my", "our", "its", "all", "any", "some", "it"}
)


def _code_search_query(normalized: str) -> str:
    """The identifier or phrase a code-search sentence is hunting for, else ''."""

    if _SEARCH_MEANS_THE_WEB_RE.search(normalized):
        return ""
    if not (_CODE_SEARCH_VERB_RE.search(normalized) or _IN_THE_CODEBASE_RE.search(normalized)):
        return ""
    quoted = _QUOTED_TOKEN_RE.search(normalized)
    if quoted:
        return _trim_search_query(str(quoted.group("token") or ""))
    identifiers = _CODE_IDENTIFIER_RE.findall(normalized)
    if identifiers:
        # The longest identifier is the subject; short camelCase words in the prose lose to it.
        return _trim_search_query(max(identifiers, key=len))
    # A one-word needle after an unambiguous code verb. `chunkify` is a real function in the bound
    # workspace, but it carries no underscore and no camelCase, so the identifier probe above misses
    # it -- "grep for chunkify" spent 60.5s and returned "I couldn't map that cleanly to a real
    # action" for a symbol defined in `g6/chunk.py`. These shapes take a SINGLE bare token, which is
    # what a symbol name is; a stopword in that slot means the sentence is about a topic, not a
    # symbol, which is what keeps "find all references to the Treaty of Versailles" out.
    for shape in _CODE_NEEDLE_SHAPES:
        shaped = shape.search(normalized)
        if not shaped:
            continue
        token = str(shaped.group("query") or "").strip("`\"'.,;:")
        if token and token.lower() not in _CODE_NEEDLE_STOPWORDS:
            return _trim_search_query(token)
    # No identifier and no quotes means nothing code-shaped to search for. "find all references to
    # the Treaty of Versailles" uses the same verb as a code search and is not one, so the trailing
    # phrase only counts when the sentence ALSO says it means a literal in this checkout -- either
    # by naming what kind of thing it is ("the string ...") or by naming the repo.
    literal_marker = re.search(
        r"\b(?:the|a|an)\s+(?:string|text|symbol|function|class|constant|name|word|phrase)\b",
        normalized,
        re.IGNORECASE,
    )
    if not literal_marker and not _IN_THE_CODEBASE_RE.search(normalized):
        return ""
    trailing = re.search(
        r"\b(?:for|to|mention|mentions|contain|contains|use|uses|reference|references)\s+"
        r"(?P<query>[^.?!]{2,80})$",
        normalized,
        re.IGNORECASE,
    )
    if trailing:
        # "look for THE STRING safe local directories" -- the qualifier introduces the needle, it
        # is not part of it, and searching for it verbatim finds nothing.
        needle = re.sub(
            r"^(?:the|a|an)\s+(?:string|text|symbol|function|class|constant|name|word|phrase)\s+",
            "",
            str(trailing.group("query") or "").strip(),
            flags=re.IGNORECASE,
        )
        return _trim_search_query(needle)
    return ""


_EXPLICIT_WORKSPACE_SEARCH_RE = re.compile(
    r"\b(?:find|locate|search|look\s+(?:for|through)|scan)\b.*"
    r"\b(?:where|what|which|how)\b.*"
    r"\b(?:wired|defined|implemented|used|called|located)\b.*"
    r"\b(?:in|inside|within)\s+(?:this|the)\s+(?:workspace|repo(?:sitory)?|project)\b",
    re.IGNORECASE,
)


def looks_like_explicit_workspace_search_request(text: str) -> bool:
    """True when a request explicitly asks to search the bound workspace.

    The planner already has deterministic payloads for common "find a file" wording. This
    companion predicate also covers symbol/location questions such as "find where X is wired in
    this workspace", so the folder-overview fast path cannot answer a search request with a
    directory summary.
    """
    normalized = " ".join(str(text or "").strip().split())
    return bool(normalized) and (
        _planned_workspace_search_payload(normalized) is not None
        or _EXPLICIT_WORKSPACE_SEARCH_RE.search(normalized) is not None
    )


def _clean_workspace_path(candidate: str) -> str:
    clean = str(candidate or "").strip().strip("`\"'").strip().rstrip(".,!?")
    if not clean:
        return ""
    if clean.lower() in _PATH_STOP_WORDS:
        return ""
    if clean.startswith("/"):
        clean = clean.lstrip("/")
    clean = clean.lstrip("./")
    if not clean or clean.lower() in _PATH_STOP_WORDS:
        return ""
    if ".." in clean.split("/"):
        return ""
    return clean


def _clean_workspace_directory_path(candidate: str, *, workspace_root: str = "") -> str:
    raw = str(candidate or "").strip().strip("`\"'")
    clean = ""
    if raw and workspace_root:
        try:
            resolved_workspace_root = Path(workspace_root).expanduser().resolve()
            resolved_candidate = Path(raw).expanduser().resolve()
            if resolved_candidate == resolved_workspace_root:
                return ""
            if resolved_workspace_root in resolved_candidate.parents:
                clean = resolved_candidate.relative_to(resolved_workspace_root).as_posix()
        except Exception:
            clean = ""
    if not clean:
        clean = _clean_workspace_path(candidate)
    if clean in {"", "."}:
        return ""
    return clean.rstrip("/")


def _extract_workspace_bootstrap_path(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    lowered = " ".join(raw.lower().split())
    file_like_prompt = (
        any(marker in lowered for marker in (" file ", " files ", " text file ", ".txt", ".md", ".json", ".yaml", ".yml", ".toml", ".py", ".ts", ".js"))
        and not any(marker in lowered for marker in _START_CODE_MARKERS)
    )
    # `_INTO_PATH_RE` reads a locative preposition ("in X", "under X", "inside X") as a destination.
    # That is only a destination when the sentence is actually asking for something to be created —
    # otherwise ordinary English supplies a folder name. "…in no more than three bullets" yielded
    # `no`; "…in three bullets" would yield `three`. A preposition is not an instruction, so it only
    # participates when a real create marker is present in the text.
    wants_creation = any(marker in lowered for marker in _DIRECTORY_CREATE_MARKERS) or any(
        marker in lowered for marker in _START_CODE_MARKERS
    )
    locative = (_INTO_PATH_RE,) if wants_creation else ()
    patterns = (
        (_NAMED_PATH_RE, _VERB_NAME_FOLDER_RE, *locative)
        if file_like_prompt
        else (
            _NAMED_PATH_RE,
            _VERB_NAME_FOLDER_RE,
            _FOLDER_PATH_RE,
            _CREATE_PATH_RE,
            *locative,
        )
    )
    for pattern in patterns:
        match = pattern.search(raw)
        if not match:
            continue
        clean = _clean_workspace_path(match.group("path"))
        if clean:
            return clean
    return ""


def _extract_workspace_parent_directory(text: str, *, workspace_root: str = "") -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    match = _WINDOWS_PARENT_DIRECTORY_RE.search(raw) or _INTO_PATH_RE.search(raw)
    if not match:
        return ""
    return _clean_workspace_directory_path(
        str(match.group("path") or "").strip(),
        workspace_root=workspace_root,
    )


# The safe home folders this lane may list. A path the user names is honoured only when it sits under
# one of them; anything else returns "" so the planner declines instead of silently retargeting.
_SAFE_LISTING_ROOTS = ("Desktop", "Downloads", "Documents")
_HOME_ROOTED_PATH_RE = re.compile(
    r"(?:^|[\s('\"`])(?P<path>(?:~|/Users/[^/\s]+|/home/[^/\s]+)/[^\s'\"`,;)]+)",
    re.IGNORECASE,
)


def _explicit_home_rooted_path(text: str) -> str:
    """A concrete path under Desktop/Downloads/Documents that the user wrote out, else ''."""
    for match in _HOME_ROOTED_PATH_RE.finditer(str(text or "")):
        candidate = str(match.group("path") or "").rstrip(".,;:!?)")
        # Second extractor, same cut-at-the-space bug as `explicit_path_in`. Measured on the
        # deployed build 2026-07-30: "check ~/Documents/Vool testing and tell me whats there"
        # became "~/Documents/Vool", which does not exist, and the turn ended in "I wasn't able to
        # turn that into a completed action" for a folder sitting right there.
        candidate = _extend_path_over_spaces(str(text or ""), candidate, match.end("path"))
        segments = [part for part in candidate.split("/") if part and part != "~"]
        # Drop the /Users/<name> prefix so the first meaningful segment is the home folder.
        if segments and segments[0].lower() in {"users", "home"}:
            segments = segments[2:]
        if not segments:
            continue
        if any(segments[0].lower() == root.lower() for root in _SAFE_LISTING_ROOTS):
            return candidate
    return ""


# Ways people ask to SEE what is in a place. The old list was nine literal markers ("list ",
# "show ", "contents of", ...) and four of eight measured phrasings missed it -- "whats inside X",
# "give me an inventory of X", "open up X and name every file", "I need the file listing for X" all
# fell through, and the turn was then answered by lanes that guessed: one relabelled a Desktop
# listing under the requested folder, one FABRICATED file names, one ran a whole-disk scan using the
# path as a folder name, and one refused with "this chat is not bound to a project folder".
_WANTS_LISTING_RE = re.compile(
    r"\b(?:list|listing|show|display|print|enumerate|inventory|rundown|breakdown)\b"
    r"|\bcontents?\s+of\b"
    # "lives in" / "sits in" / "is kept in" are how people ask without using a listing verb.
    # Their absence is what let "give me a rundown of what lives in <path>" fall through to the
    # model lane: the deterministic extractor returned None, the 0.6B arbiter then routed it
    # nondeterministically (the same sentence answered fine against one folder and refused
    # against another), and on the miss the 8B picked workspace.list_files — a workspace-relative
    # tool with no workspace bound — for an absolute path. Matching here keeps the turn on the
    # deterministic path, where a typed path always wins and no model is consulted at all.
    r"|\bwhat(?:'?s| is| are)?\s+(?:in|inside|under|stored|sitting|there|on|lives?|sits?|kept)\b"
    r"|\bwhat\s+lives\b"
    r"|\bwhat\s+do\s+we\s+have\s+on\b"
    r"|\btell\s+me\s+what\b"
    r"|\bcan\s+you\s+see\b"
    r"|\bwalk\s+me\s+through\b"
    r"|\bname\s+every\s+(?:file|folder|entry)\b"
    r"|\bwhich\s+(?:files?|folders?|entries)\b"
    r"|\b(?:files?|folders?|entries)\s+(?:are|is)\s+(?:in|inside|under|sitting|stored)\b"
    r"|\b(?:peek|look|glance)\s+(?:in|into|inside|at)\b"
    # Noun-led asks: "what are the folders and files on my desktop?" led the old marker list and
    # must keep working -- the verb-led alternatives above do not cover it.
    r"|\bwhat\s+(?:are|is)\s+(?:the\s+)?(?:files?|folders?|entries|contents?|stuff|things)\b"
    r"|\b(?:folders?|files?)\s+and\s+(?:files?|folders?)\b"
    # "what folders live on my desktop" names the listing noun straight after "what", with no
    # linking verb for the alternatives above to catch. Measured on the deployed build 2026-07-30:
    # it matched nothing here, fell through every lane, and after 83 seconds came back as "that
    # tool didn't run -- so I won't guess" for a directory the tool reads in a tenth of a second.
    # A refusal we did not need is still a wrong answer.
    r"|\bwhat\s+(?:sub)?(?:folders|directories|dirs|files|entries)\b"
    r"|\bhow\s+many\s+(?:sub)?(?:folders|directories|dirs|files|entries)\b"
    # "anything in ~/Desktop/nonexistent-xyz?" asks what is in a place as plainly as a sentence
    # can. It matched nothing, went to the CLOUD model for 49 seconds, and came back with "I
    # wasn't able to turn that into a completed action" -- when the true answer, one stat call
    # away, is that the folder is not there.
    r"|\b(?:anything|something|much|any\s+files?)\s+(?:in|inside|under)\b"
    # "i cant remember what i PUT in the Documents dir, can you check" -- the user's own past
    # write is how they refer to the contents. Measured 2026-07-30: this reached the model and
    # got "Sure. Let me check the contents of the Documents directory for you." and nothing else.
    r"|\bwhat\s+i\s+(?:put|saved|stored|left|dropped|wrote)\b",
    re.IGNORECASE,
)

# A count question wants a NUMBER. "how many files are in ~/Downloads" was answered with the first
# thirty filenames and a bare "-..." -- the count it asked for appeared nowhere in the reply, and
# the reader cannot recover it from a truncated list. Measured on the deployed build 2026-07-30.
_ASKS_HOW_MANY_RE = re.compile(
    r"\bhow\s+many\b|\bhow\s+much\s+(?:stuff|is\s+in)\b|\bcount\s+(?:the\s+)?(?:files?|folders?|entries)\b"
    r"|\bnumber\s+of\s+(?:files?|folders?|entries|items)\b",
    re.IGNORECASE,
)


# Asking WHICH workspace/project is active, as opposed to what is inside it. The distinction is
# the whole point: "what folder is our workspace set on" wants a path, "what is in our workspace"
# wants a listing, and answering the first with the second is what happened live on 2026-07-28.
_WORKSPACE_IDENTITY_RE = re.compile(
    r"\b(?:what|which|where)\b[^.\n]{0,40}?\b(?:workspace|project)\b"
    r"|\b(?:workspace|project)\b[^.\n]{0,30}?\b(?:set\s+(?:on|to)|am\s+i\s+(?:in|on)|are\s+we\s+(?:in|on)|"
    r"is\s+(?:this|it|active|bound)|currently)\b"
    r"|\bwhere\s+am\s+i\b",
    re.IGNORECASE,
)
# Words that turn it back into a content question, so "what files are in our workspace" still lists.
_WORKSPACE_CONTENT_RE = re.compile(
    # NOTE: plural "folders" only. "what FOLDER is our workspace set on" uses the singular for
    # identity, and treating that as a content word was exactly the miss this detector exists to fix.
    # Also excluded: verbs that hunt for something INSIDE the workspace. "find where tool intent
    # execution is wired in this workspace" mentions where+workspace but is a code search, and
    # claiming it for the identity tool broke a real openclaw test.
    r"\b(?:files?|folders|contents?|entries|tree|list|inside|what(?:'?s| is)\s+in"
    r"|find|search|locate|grep|look\s+for|wired|defined|implemented|declared)\b", re.IGNORECASE
)


def _asks_which_workspace(text: str) -> bool:
    lowered = " ".join(str(text or "").strip().lower().split())
    if (
        not lowered
        or is_conversational_memory_recall(lowered)
        or not _WORKSPACE_IDENTITY_RE.search(lowered)
    ):
        return False
    return not _WORKSPACE_CONTENT_RE.search(lowered)


# A folders-only VIEW is something the sentence has to ASK FOR: the directory noun has to be the
# thing being listed ("what are the FOLDERS on my desktop", "show me the SUBFOLDERS of Downloads").
#
# The previous test was `" folder" in lowered`, which cannot tell that apart from the directory
# noun NAMING THE TARGET -- "what's sitting in my Desktop FOLDER" is the folder called Desktop, not
# a request to hide every file in it. Measured on the deployed build 2026-07-30: that phrasing
# returned 81 folders and silently dropped all 83 files on the Desktop, under a header that read
# "Visible folders" so nothing looked wrong. The same substring test also got it backwards the
# other way -- "show me the subfolders of Downloads" missed (no space before "folder" in
# "subfolders") and "list everything in my downloads folder" filtered a request that said
# "everything".
#
# So: require a PLURAL directory noun standing as the object of the listing ask. Singular
# "<name> folder" is target naming and never a filter. `mentions_files` still overrides, which is
# what keeps "what are the folders and files on my desktop" a full listing.
_ASKS_FOR_FOLDERS_ONLY_RE = re.compile(
    r"\b(?:what|which)\s+(?:are\s+|is\s+)?(?:the\s+|all\s+(?:the\s+)?)?(?:sub)?(?:folders|directories|dirs)\b"
    r"|\b(?:list|show|see|display|name|enumerate|print)\s+(?:me\s+)?(?:the\s+|all\s+(?:the\s+)?|every\s+)?"
    r"(?:sub)?(?:folders|directories|dirs)\b"
    r"|\bhow\s+many\s+(?:sub)?(?:folders|directories|dirs)\b"
    r"|\b(?:only|just)\s+(?:the\s+)?(?:sub)?(?:folders|directories|dirs)\b"
    r"|\b(?:sub)(?:folders|directories)\b",
    re.IGNORECASE,
)


def _extract_safe_machine_directory_listing(text: str) -> dict[str, Any] | None:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return None
    if any(marker in lowered for marker in ("create ", "make ", "mkdir", "write ", "edit ", "change ", "delete ")):
        return None
    if not _WANTS_LISTING_RE.search(lowered):
        return None
    # A path the user typed OUT WINS. This used to be the bare substring test below, so
    # "peek into /Users/me/Desktop/vool-audit-scratch/anvil-notes and list what is stored" matched
    # "desktop" INSIDE the path, set the target to ~/Desktop, and threw the real path away -- the
    # planner then listed the whole Desktop and the answer was presented as the contents of the
    # folder that was asked about. That is the fabricated-grounding case, and this is where it starts.
    path = _explicit_home_rooted_path(text)
    from_word_fallback = False
    if not path:
        if "desktop" in lowered:
            path = "~/Desktop"
        elif "downloads" in lowered:
            path = "~/Downloads"
        elif "documents" in lowered or "docs" in lowered:
            path = "~/Documents"
        from_word_fallback = bool(path)
    if not path:
        return None
    mentions_files = any(token in lowered for token in (" file", " files", " entry", " entries", " contents"))
    directories_only = _ASKS_FOR_FOLDERS_ONLY_RE.search(lowered) is not None and not mentions_files
    if from_word_fallback:
        # The location word is not the target. "show me the files in my garden folder on the
        # desktop" matched `"desktop" in lowered` above, listed the whole Desktop, and threw
        # "my garden folder" away — the audit's keyword-in-a-phrase bug class, second instance
        # (the first matched inside typed-out paths; this one matches inside prose). Traced live
        # 2026-07-28: both reported wrong-folder listings came from this exact fallback, with
        # zero model calls.
        #
        # When the phrase names a folder beyond the location word, resolve it among the root's
        # direct children. Only a UNIQUE match changes anything; zero or several keep today's
        # root listing, so "what's on my desktop" and genuinely ambiguous names are untouched.
        named = _named_child_within(path, text)
        if named:
            path = named
            # The " folder" token named the target, it did not ask for a folders-only view.
            # A full listing of the right folder beats a filtered view of it either way.
            directories_only = False
    arguments: dict[str, Any] = {"path": path, "directories_only": directories_only, "limit": 200}
    arguments.update(listing_view_arguments(text))
    return arguments


def listing_view_arguments(text: str) -> dict[str, Any]:
    """The VIEW the sentence asks for -- folders only, a count -- independent of which path it names.

    Shared with the machine read fast path, which reaches `machine.list_directory` by a different
    route and was sending a bare `{"path": ...}`. That made "just the folders in ~/Desktop please"
    answer with all 164 entries: the right folder, the wrong view, and nothing in the reply saying
    so. One reading of the sentence, used by both callers.
    """
    lowered = " ".join(str(text or "").strip().lower().split())
    view: dict[str, Any] = {}
    mentions_files = any(token in lowered for token in (" file", " files", " entry", " entries", " contents"))
    if _ASKS_FOR_FOLDERS_ONLY_RE.search(lowered) and not mentions_files:
        view["directories_only"] = True
    if _ASKS_HOW_MANY_RE.search(lowered):
        view["count_only"] = True
    return view


def _named_child_within(root: str, text: str) -> str:
    """The one direct child of ``root`` the phrase names, or "" when that is not unambiguous.

    Matching is per meaningful word, compact and separator-insensitive, the same comparison the
    find_folder scoped probe uses: "garden" finds `my-garden-folder`. Stray verbs the phrase
    reducer does not strip ("peek", "tell") simply match no child and drop out, so they cannot
    poison the result. Read-only, one scandir, never recursive.
    """

    name, _scope = _find_folder_name_from_phrase(text)
    tokens = {re.sub(r"[\s_\-]+", "", t) for t in re.split(r"[^\w\-]+", str(name or "").lower()) if len(t) >= 3}
    if not tokens:
        return ""
    import os as _os

    matches: set[str] = set()
    try:
        with _os.scandir(_os.path.expanduser(root)) as entries:
            for entry in entries:
                try:
                    if not entry.is_dir(follow_symlinks=False) or entry.name.startswith("."):
                        continue
                except OSError:
                    continue
                compact = re.sub(r"[\s_\-]+", "", entry.name.lower())
                if any(token in compact for token in tokens):
                    matches.add(entry.name)
    except OSError:
        return ""
    if len(matches) != 1:
        return ""
    return f"{root.rstrip('/')}/{matches.pop()}"


def _extract_safe_machine_directory_create(text: str) -> dict[str, Any] | None:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return None
    if _DIRECTORY_CREATE_QUESTION_RE.search(lowered) is not None:
        # Same law as `_looks_like_workspace_bootstrap_request`: a question about creation
        # never plans one.
        return None
    if not any(marker in lowered for marker in _DIRECTORY_CREATE_MARKERS):
        return None
    if any(marker in lowered for marker in (" write ", " file ", " append ", " edit ", " change ", " delete ", " remove ", " rename ", " move ")):
        return None
    if not any(marker in lowered for marker in (" folder", " directory", " dir ", "mkdir")):
        return None
    root = ""
    if "desktop" in lowered:
        root = "~/Desktop"
    elif "downloads" in lowered:
        root = "~/Downloads"
    elif "documents" in lowered or "docs" in lowered:
        root = "~/Documents"
    if not root:
        return None
    relative_path = _extract_workspace_bootstrap_path(text)
    if not relative_path:
        return None
    return {"path": f"{root.rstrip('/')}/{relative_path}".replace("//", "/")}


def _extract_machine_specs_request(text: str) -> dict[str, Any] | None:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return None
    if any(
        marker in lowered
        for marker in (
            "what can you do",
            "what can you actually do",
            "what are your capabilities",
            "what can you help with",
            "actual local powers",
        )
    ):
        return None
    if any(marker in lowered for marker in ("write ", "create ", "edit ", "change ", "delete ", "remove ", "rename ", "move ", "build ", "generate ", "implement ", "code ")):
        return None
    spec_markers = (
        "machine specs",
        "machine spec",
        "pc specs",
        "pc spec",
        "pc's specs",
        "pc's spec",
        "our machine",
        "this machine",
        "what machine",
        "what is machine",
        "system specs",
        "hardware specs",
        "cpu",
        "ram",
        "memory",
        "gpu",
        "vram",
        "chip",
        "cores",
    )
    # The short hardware words are matched as WHOLE WORDS. "ram" as a substring lived inside
    # "progRAM" and matched a spec marker on its own -- measured live 2026-09-17, "Build me a
    # small Python provider-health checker ... The program should accept a model ID" was
    # answered, wholesale, with this machine's hardware specs: "program" carried "ram", the
    # "checker" carried "check" as the inspection verb, and "build" was missing from the
    # authoring guard above. Multi-word markers stay substring-safe as written.
    _SHORT_SPEC_WORDS = ("cpu", "ram", "gpu", "vram", "chip", "cores", "memory")
    words = set(re.findall(r"[a-z']+", lowered))
    # Display/screen/resolution questions are NOT machine-spec questions: machine.inspect_specs
    # has no display data on Windows and would answer them with CPU/OS specs. They route to the
    # dedicated display capability (and, until it exists, to the honest-blocker gate).
    if any(marker in lowered for marker in ("screen", "resolution", "display", "monitor")):
        return None
    mentions_machine_shape = any(marker in lowered for marker in spec_markers if marker not in _SHORT_SPEC_WORDS) or any(
        word in _SHORT_SPEC_WORDS for word in words
    )
    mentions_running_host = "running on" in lowered and "machine" in lowered
    if not mentions_machine_shape and not mentions_running_host:
        return None
    wants_inspection = any(
        marker in lowered
        for marker in (
            "what",
            "tell me",
            "show me",
            "check",
            "inspect",
            "which",
            "how much",
            # "how many" sat missing next to "how much", so "How many CPU cores?" extracted no
            # specs request, the machine fast path found no machine.* intent and stood down, and
            # the question reached the model with no tool result. On a 10-core host it answered
            # "4 cores." -- a fabricated number for a value the specs probe was already holding.
            "how many",
            "running on",
        )
    )
    return {} if wants_inspection else None


# Word-bounded, because a bare substring scan for "dir" matched inside "directly". Measured
# 2026-07-31 on a live turn: "Now fix only the proven bug… Run all directly relevant existing
# tests… Explain any remaining risk in no more than three bullets." created a DIRECTORY NAMED `no`.
# Three heuristics, none of them about creating a directory, ANDed into a filesystem mutation:
# "dir" inside "directly" satisfied the workspace-target test, "make" inside "Make the smallest
# safe production change" satisfied the create-verb test, and `_INTO_PATH_RE` read "in no" as a
# destination. A request to FIX CODE became a mkdir named out of prose.
_WORKSPACE_TARGET_NOUN_RE = re.compile(
    r"\b(?:folders?|directory|directories|dirs?|workspaces?|repos?|repository|repositories)\b"
)


def _looks_like_workspace_bootstrap_request(text: str) -> bool:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return False
    if _DIRECTORY_CREATE_QUESTION_RE.search(lowered) is not None:
        # A past-tense question about creation ("did you created folder or not?") is an
        # information request, not an instruction; planning a mkdir from it created a
        # directory named after prose. See the constant's note in core/execution/constants.py.
        return False
    if (
        any(marker in lowered for marker in (" file ", " files ", " text file ", ".txt", ".md", ".json", ".yaml", ".yml", ".toml", ".py", ".ts", ".js"))
        and not any(marker in lowered for marker in _START_CODE_MARKERS)
    ):
        return False
    creates_folder = any(marker in lowered for marker in _DIRECTORY_CREATE_MARKERS)
    starts_code = any(marker in lowered for marker in _START_CODE_MARKERS)
    mentions_workspace_target = _WORKSPACE_TARGET_NOUN_RE.search(lowered) is not None
    has_path = bool(_extract_workspace_bootstrap_path(text))
    fuzzy_create_verb = any(v in lowered for v in ("create", "make", "mkdir", "crate", "creat"))
    return bool(
        (creates_folder and (starts_code or has_path))
        or (starts_code and mentions_workspace_target)
        or (starts_code and has_path)
        or (fuzzy_create_verb and mentions_workspace_target and has_path)
    )


def _clean_workspace_file_path(candidate: str, *, base_dir: str = "", workspace_root: str = "") -> str:
    """The confinement-aware file-path cleaner, delegated to the one write-demand authority.

    A parent-walk ("../x.txt") is refused, never relocated; a ROOTED path outside the
    workspace is refused ("/etc/evil.txt" used to lose its "/" and become the in-workspace
    write "etc/evil.txt"); everything else resolves to its workspace-relative form.
    """
    from core.execution.write_demand import confine_target

    resolved, reason = confine_target(candidate, base_dir=base_dir, workspace_root=workspace_root)
    if reason:
        return ""
    return resolved


def _history_messages(source_context: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [dict(item) for item in list((source_context or {}).get("conversation_history") or []) if isinstance(item, dict)]


def _extract_history_observation_payload(message: dict[str, Any]) -> dict[str, Any] | None:
    content = str(message.get("content") or "").strip()
    if not content.startswith("Grounding observations for this turn."):
        return None
    start = content.find("{")
    if start < 0:
        return None
    try:
        payload = json.loads(content[start:])
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _recover_last_workspace_path_from_history(source_context: dict[str, Any] | None) -> str:
    history = _history_messages(source_context)
    workspace_root = str((source_context or {}).get("workspace") or (source_context or {}).get("workspace_root") or "").strip()
    for message in reversed(history[-12:]):
        observation = _extract_history_observation_payload(message)
        if observation is not None:
            intent = str(observation.get("intent") or "").strip()
            if intent in {"workspace.write_file", "workspace.read_file", "workspace.replace_in_file"}:
                path = _clean_workspace_file_path(
                    str(observation.get("path") or "").strip(),
                    workspace_root=workspace_root,
                )
                if path:
                    return path
        if str(message.get("role") or "").strip().lower() != "user":
            continue
        content = str(message.get("content") or "")
        history_writes, _history_directory = _extract_workspace_writes_from_text(
            content,
            workspace_root=workspace_root,
        )
        if history_writes:
            return str(history_writes[-1].get("path") or "").strip()
    return ""


def _extract_workspace_writes_from_text(
    text: str,
    *,
    workspace_root: str = "",
) -> tuple[list[dict[str, Any]], str]:
    """The LITERAL writes this text names, via the one typed write-demand authority.

    The regex loop this function used to own moved into
    `core.execution.write_demand.resolve_write_demand` (the audit's single resolver):
    targets, content classified literal-or-brief, mode, and a confinement verdict per
    target. This wrapper keeps the planner's `(writes, directory)` contract; only items
    the resolver classified LITERAL come back here — a brief demand stays out of the
    write plan and belongs to the builder under its EXACT mutation scope.
    """
    from core.execution.write_demand import resolve_write_demand

    demand = resolve_write_demand(str(text or ""), workspace_root=workspace_root)
    if demand is None:
        return [], ""
    writes = [
        {"path": item.path, "content": item.content, "mode": "append" if item.action == "append" else "write"}
        for item in demand.literal_items()
    ]
    return writes, demand.directory or ""


def _extract_workspace_file_plan(
    text: str,
    *,
    source_context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    original = str(text or "").strip()
    if not original:
        return None
    raw = re.sub(
        r"(?P<stem>[A-Za-z0-9_./-]+)\.\s+(?P<ext>py|js|ts|tsx|jsx|txt|md|json|yaml|yml|toml)\b",
        r"\g<stem>.\g<ext>",
        original,
    )
    compact = " ".join(raw.split()).strip()
    workspace_root = str((source_context or {}).get("workspace") or (source_context or {}).get("workspace_root") or "").strip()
    base_dir = _extract_workspace_parent_directory(compact, workspace_root=workspace_root)
    if not base_dir and any(marker in compact.lower() for marker in _DIRECTORY_CREATE_MARKERS):
        base_dir = _extract_workspace_bootstrap_path(compact)
    list_requested = any(
        marker in compact.lower()
        for marker in (
            "list the folder contents",
            "list the directory contents",
            "list folder contents",
            "list directory contents",
            "list the contents",
        )
    )

    # THE one write-demand authority: create/overwrite/append shapes, literal-or-brief
    # classification, and per-target confinement are all resolved by
    # `core.execution.write_demand` now. This plan keeps only the readback arms below.
    demand_writes, planned_directory = _extract_workspace_writes_from_text(
        raw,
        workspace_root=workspace_root,
    )
    append_content_only_match = _APPEND_CONTENT_ONLY_RE.search(raw)
    if append_content_only_match is not None and not demand_writes:
        # A follow-up append names no file: the path is recovered from the turn's history,
        # which only this planner can see.
        path = _clean_workspace_file_path(
            _recover_last_workspace_path_from_history(source_context),
            base_dir=base_dir,
        )
        content = str(append_content_only_match.group("content") or "").strip()
        if path and content:
            demand_writes = [{"path": path, "content": content, "mode": "append"}]
    if demand_writes:
        list_path = planned_directory or base_dir
        if list_requested and not list_path:
            parent = str(Path(str(demand_writes[0].get("path") or "")).parent)
            list_path = "" if parent in {"", "."} else parent
        return {"directory": planned_directory, "writes": demand_writes, "read_path": "", "verbatim_read": False, "list_path": list_path}

    explicit_read_match = _EXPLICIT_WORKSPACE_READ_RE.search(raw)
    if explicit_read_match is not None:
        path = _clean_workspace_file_path(
            str(explicit_read_match.group("path") or "").strip(),
            base_dir=base_dir,
            workspace_root=workspace_root,
        )
        if path:
            return {"directory": "", "writes": [], "read_path": path, "verbatim_read": True, "list_path": ""}

    if _EXACT_READBACK_RE.search(raw):
        path = _recover_last_workspace_path_from_history(source_context)
        if path:
            return {"directory": "", "writes": [], "read_path": path, "verbatim_read": True, "list_path": ""}
    return None


def _pending_workspace_writes(plan: dict[str, Any], steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    writes = [dict(item) for item in list(plan.get("writes") or []) if isinstance(item, dict)]
    if not writes:
        return []
    completed: list[tuple[str, str]] = []
    for step in list(steps or []):
        tool_name = str(step.get("tool_name") or "").strip()
        if tool_name != "workspace.write_file":
            continue
        args = dict(step.get("arguments") or {})
        completed.append((str(args.get("path") or "").strip(), "write"))
        completed.append((str(args.get("path") or "").strip(), "append"))
    return [item for item in writes if (str(item.get("path") or "").strip(), str(item.get("mode") or "write").strip()) not in completed]


def _planned_write_batch(pending_writes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The remaining writes as concrete `workspace.write_file` payloads, for the approval prompt.

    Only the LEADING run of plain writes is handed over, and byte-for-byte the payload the planner
    itself will emit for each of them on a later round -- otherwise the operator would be shown, and
    the controller would fingerprint, a call that never arrives in that shape and the grant would
    silently fail to cover it.

    An `append` write stops the run: the planner answers that one with a `workspace.read_file`
    first (see `planned_read_before_append`), so neither it nor anything behind it is the next write
    to run.
    """
    batch: list[dict[str, Any]] = []
    for item in [dict(entry) for entry in list(pending_writes or []) if isinstance(entry, dict)]:
        if str(item.get("mode") or "write").strip() == "append":
            break
        path = str(item.get("path") or "").strip()
        if not path:
            break
        batch.append(
            {
                "intent": "workspace.write_file",
                "arguments": {"path": path, "content": item.get("content")},
            }
        )
    return batch


def _normalize_hive_title_candidate(text: str) -> str:
    normalized = " ".join(str(text or "").split()).strip().strip("`\"'").strip().strip(".!?")
    normalized = normalized.lstrip("-:–—/ ").strip()
    return normalized


def _is_generic_hive_title_candidate(text: str) -> bool:
    normalized = _normalize_hive_title_candidate(text).lower()
    if normalized in _GENERIC_HIVE_TITLE_MARKERS or len(normalized) < 4:
        return True
    tokens = [token for token in re.split(r"[^a-z0-9]+", normalized) if token]
    if not tokens:
        return True
    generic_tokens = {"create", "creating", "new", "task", "tasks", "topic", "topics", "hive", "mind", "the", "this", "these"}
    return all(token in generic_tokens for token in tokens)


def _recover_hive_create_from_history(source_context: dict[str, Any] | None) -> tuple[str, str] | None:
    history = [dict(item) for item in list((source_context or {}).get("conversation_history") or []) if isinstance(item, dict)]
    for message in reversed(history[-8:]):
        if str(message.get("role") or "").strip().lower() != "user":
            continue
        content = " ".join(str(message.get("content") or "").split()).strip()
        lowered = content.lower()
        if not any(marker in lowered for marker in _HIVE_ACTION_PATTERNS) and " task:" not in lowered:
            continue
        sections = {
            "task": re.search(r"\btask\b\s*[:=-]\s*(.+?)(?=(?:\b(?:goal|summary)\b\s*[:=-])|(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", content, re.IGNORECASE),
            "title": re.search(r"\b(?:name it|title|call it|called)\b\s*[:=-]?\s*(.+?)(?=(?:\bsummary\b\s*[:=-])|(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", content, re.IGNORECASE),
            "goal": re.search(r"\bgoal\b\s*[:=-]\s*(.+?)(?=(?:\bsummary\b\s*[:=-])|(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", content, re.IGNORECASE),
            "summary": re.search(r"\bsummary\b\s*[:=-]\s*(.+?)(?=(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", content, re.IGNORECASE),
        }
        raw_title = ""
        if sections["title"] is not None:
            raw_title = str(sections["title"].group(1) or "")
        elif sections["task"] is not None:
            raw_title = str(sections["task"].group(1) or "")
        else:
            raw_title = content
            for prefix in _HIVE_CREATE_PREFIXES:
                if raw_title.lower().startswith(prefix):
                    raw_title = raw_title[len(prefix):].strip().lstrip("-:–").strip()
                    break
            if ":" in raw_title and raw_title.count(":") == 1:
                raw_title = raw_title.split(":", 1)[-1].strip()
        title = _normalize_hive_title_candidate(raw_title[:180])
        if _is_generic_hive_title_candidate(title):
            continue
        summary = ""
        if sections["summary"] is not None:
            summary = _normalize_hive_title_candidate(str(sections["summary"].group(1) or "")[:4000])
        elif sections["goal"] is not None:
            summary = _normalize_hive_title_candidate(str(sections["goal"].group(1) or "")[:4000])
        summary = summary or title
        return title, summary
    return None


def _recover_lookup_followup_from_history(source_context: dict[str, Any] | None) -> str:
    history = [dict(item) for item in list((source_context or {}).get("conversation_history") or []) if isinstance(item, dict)]
    for message in reversed(history[-8:]):
        if str(message.get("role") or "").strip().lower() != "user":
            continue
        content = " ".join(str(message.get("content") or "").split()).strip()
        if not content:
            continue
        if _looks_like_followup_resume_request(content):
            continue
        if looks_like_explicit_lookup_request(content) or looks_like_public_entity_lookup_request(content):
            return content
        break
    return ""


def _workflow_step_exists(steps: list[dict[str, Any]], intent: str, *, key: str | None = None, value: str | None = None) -> bool:
    normalized_intent = str(intent or "").strip()
    normalized_value = str(value or "").strip()
    for step in list(steps or []):
        if str(step.get("tool_name") or "").strip() != normalized_intent:
            continue
        if key is None:
            return True
        arguments = dict(step.get("arguments") or {})
        step_value = str(arguments.get(key) or "").strip()
        if step_value == normalized_value:
            return True
    return False


def _last_command_from_steps(steps: list[dict[str, Any]]) -> str:
    for step in reversed(list(steps or [])):
        if str(step.get("tool_name") or "").strip() != "sandbox.run_command":
            continue
        command = str(dict(step.get("arguments") or {}).get("command") or "").strip()
        if command:
            return command
    return ""


def _workflow_retry_already_happened(steps: list[dict[str, Any]], command: str) -> bool:
    normalized = str(command or "").strip()
    if not normalized:
        return False
    count = 0
    for step in list(steps or []):
        if str(step.get("tool_name") or "").strip() != "sandbox.run_command":
            continue
        step_command = str(dict(step.get("arguments") or {}).get("command") or "").strip()
        if step_command == normalized:
            count += 1
    return count >= 2


def _latest_failed_validation_hints(steps: list[dict[str, Any]]) -> dict[str, Any]:
    for step in reversed(list(steps or [])):
        observation = dict(step.get("observation") or {})
        intent = str(observation.get("intent") or "").strip()
        if intent not in {"workspace.run_tests", "workspace.run_lint", "workspace.run_formatter"}:
            if intent == "orchestration.execute_envelope":
                _, hints, rollback = _failed_validation_from_orchestration_step(step)
                if hints and bool(rollback.get("ok", False)):
                    return hints
            continue
        hints = extract_observation_followup_hints(observation)
        if int(hints.get("returncode") or 0) != 0:
            return hints
    return {}


def _latest_failed_validation_observation(steps: list[dict[str, Any]]) -> dict[str, Any]:
    for step in reversed(list(steps or [])):
        observation = dict(step.get("observation") or {})
        intent = str(observation.get("intent") or "").strip()
        if intent not in {"workspace.run_tests", "workspace.run_lint", "workspace.run_formatter"}:
            if intent == "orchestration.execute_envelope":
                nested_observation, hints, rollback = _failed_validation_from_orchestration_step(step)
                if nested_observation and hints and bool(rollback.get("ok", False)):
                    return nested_observation
            continue
        hints = extract_observation_followup_hints(observation)
        if int(hints.get("returncode") or 0) != 0:
            return observation
    return {}


def _failed_validation_from_orchestration_step(step: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    details = dict(step.get("details") or {})
    merged_result = dict(details.get("merged_result") or {})
    winner = dict(merged_result.get("winner") or {})
    winner_details = dict(winner.get("details") or {})
    rollback = dict(winner_details.get("failure_rollback") or {})
    if rollback and not bool(rollback.get("ok", False)):
        return {}, {}, {}
    if not rollback:
        return {}, {}, {}
    for step_payload in reversed(list(winner_details.get("step_results") or [])):
        if not isinstance(step_payload, dict):
            continue
        step_details = dict(step_payload.get("details") or {})
        observation = dict(step_details.get("observation") or {})
        intent = str(observation.get("intent") or "").strip()
        if intent not in {"workspace.run_tests", "workspace.run_lint", "workspace.run_formatter"}:
            continue
        hints = extract_observation_followup_hints(observation)
        if int(hints.get("returncode") or 0) != 0:
            return observation, hints, rollback
    return {}, {}, {}


def _latest_read_file_hints(steps: list[dict[str, Any]], *, path: str) -> dict[str, Any]:
    normalized_path = str(path or "").strip()
    if not normalized_path:
        return {}
    for step in reversed(list(steps or [])):
        observation = dict(step.get("observation") or {})
        if str(observation.get("intent") or "").strip() != "workspace.read_file":
            continue
        hints = extract_observation_followup_hints(observation)
        if str(hints.get("path") or "").strip() == normalized_path:
            return hints
    return {}


def _workflow_validation_command_already_attempted(steps: list[dict[str, Any]], command: str) -> bool:
    normalized = str(command or "").strip()
    if not normalized:
        return False
    for step in reversed(list(steps or [])):
        observation = dict(step.get("observation") or {})
        intent = str(observation.get("intent") or "").strip()
        if intent in {"workspace.run_tests", "workspace.run_lint", "workspace.run_formatter"}:
            step_command = str(dict(step.get("arguments") or {}).get("command") or "").strip()
            if step_command == normalized:
                return True
            hints = extract_observation_followup_hints(observation)
            if str(hints.get("command") or "").strip() == normalized:
                return True
            continue
        if intent == "orchestration.execute_envelope":
            _, _, rollback = _failed_validation_from_orchestration_step(step)
            if not bool(rollback.get("ok", False)):
                continue
            details = dict(step.get("details") or {})
            merged_result = dict(details.get("merged_result") or {})
            winner = dict(merged_result.get("winner") or {})
            winner_details = dict(winner.get("details") or {})
            for step_payload in reversed(list(winner_details.get("step_results") or [])):
                if not isinstance(step_payload, dict):
                    continue
                nested_intent = str(step_payload.get("intent") or "").strip()
                if nested_intent not in {"workspace.run_tests", "workspace.run_lint", "workspace.run_formatter"}:
                    continue
                nested_arguments = dict(step_payload.get("arguments") or {})
                nested_command = str(nested_arguments.get("command") or "").strip()
                if nested_command == normalized:
                    return True
                nested_details = dict(step_payload.get("details") or {})
                nested_observation = dict(nested_details.get("observation") or {})
                hints = extract_observation_followup_hints(nested_observation)
                if str(hints.get("command") or "").strip() == normalized:
                    return True
    return False


def _iter_read_file_hints(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hints_list: list[dict[str, Any]] = []
    for step in reversed(list(steps or [])):
        observation = dict(step.get("observation") or {})
        if str(observation.get("intent") or "").strip() != "workspace.read_file":
            continue
        hints = extract_observation_followup_hints(observation)
        if hints:
            hints_list.append(hints)
    return hints_list


def _latest_lookup_hints(steps: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    for step in reversed(list(steps or [])):
        observation = dict(step.get("observation") or {})
        intent = str(observation.get("intent") or "").strip()
        if intent not in {"workspace.symbol_search", "workspace.search_text"}:
            continue
        return intent, extract_observation_followup_hints(observation)
    return "", {}


def _next_unread_lookup_path(
    *,
    steps: list[dict[str, Any]],
    hints: dict[str, Any],
    current_path: str,
) -> tuple[str, int]:
    primary_path = str(hints.get("primary_path") or "").strip()
    primary_line = int(hints.get("primary_line") or 0)
    candidate_paths: list[str] = []
    for candidate in [primary_path, *list(hints.get("paths") or [])]:
        normalized = str(candidate or "").strip()
        if normalized and normalized not in candidate_paths:
            candidate_paths.append(normalized)
    for candidate in candidate_paths:
        if candidate == current_path:
            continue
        if _workflow_step_exists(steps, "workspace.read_file", key="path", value=candidate):
            continue
        return candidate, primary_line if candidate == primary_path else 0
    return "", 0


def _diagnostic_symbol_query(query: str) -> str:
    normalized = str(query or "").strip()
    if not normalized:
        return ""
    call_match = re.search(r"([A-Za-z_][A-Za-z0-9_\.]*)\s*\(", normalized)
    candidate = str(call_match.group(1) or "").strip() if call_match else ""
    if not candidate:
        full_match = re.fullmatch(r"[A-Za-z_][A-Za-z0-9_\.]*", normalized)
        candidate = str(full_match.group(0) or "").strip() if full_match else ""
    if not candidate:
        return ""
    symbol = candidate.split(".")[-1].strip()
    if not symbol:
        return ""
    if symbol.lower() in {"assertionerror", "traceback", "exception", "error", "failed"}:
        return ""
    return symbol


def _planned_diagnostic_lookup_followup(
    *,
    steps: list[dict[str, Any]],
    diagnostic_query: str,
    symbol_reason: str,
    search_reason: str,
) -> WorkflowPlannerDecision | None:
    symbol = _diagnostic_symbol_query(diagnostic_query)
    if symbol and not _workflow_step_exists(steps, "workspace.symbol_search", key="symbol", value=symbol):
        return WorkflowPlannerDecision(
            handled=True,
            reason=symbol_reason,
            next_payload={"intent": "workspace.symbol_search", "arguments": {"symbol": symbol, "limit": 10}},
        )
    if diagnostic_query and not _workflow_step_exists(steps, "workspace.search_text", key="query", value=diagnostic_query):
        return WorkflowPlannerDecision(
            handled=True,
            reason=search_reason,
            next_payload={"intent": "workspace.search_text", "arguments": {"query": diagnostic_query, "limit": 10}},
        )
    return None


def _planned_validation_failure_followup(
    *,
    steps: list[dict[str, Any]],
    hints: dict[str, Any],
    inspect_reason: str,
    symbol_reason: str,
    search_reason: str,
    stop_reason: str,
) -> WorkflowPlannerDecision:
    error_path = str(hints.get("error_path") or "").strip()
    error_line = int(hints.get("error_line") or 0)
    if error_path and not _workflow_step_exists(steps, "workspace.read_file", key="path", value=error_path):
        return WorkflowPlannerDecision(
            handled=True,
            reason=inspect_reason,
            next_payload={
                "intent": "workspace.read_file",
                "arguments": {
                    "path": error_path,
                    "start_line": max(1, error_line - 8) if error_line else 1,
                    "max_lines": 60,
                },
            },
        )
    diagnostic_query = str(hints.get("diagnostic_query") or "").strip()
    if diagnostic_query:
        lookup_followup = _planned_diagnostic_lookup_followup(
            steps=steps,
            diagnostic_query=diagnostic_query,
            symbol_reason=symbol_reason,
            search_reason=search_reason,
        )
        if lookup_followup is not None:
            return lookup_followup
    return WorkflowPlannerDecision(handled=True, reason=stop_reason, stop_after=True)


def _can_plan_candidate_repair_envelope(steps: list[dict[str, Any]]) -> bool:
    envelope_steps = [
        step
        for step in list(steps or [])
        if str(dict(step.get("observation") or {}).get("intent") or "").strip() == "orchestration.execute_envelope"
    ]
    if not envelope_steps:
        return True
    if len(envelope_steps) > 1:
        return False
    last_envelope = dict(envelope_steps[-1] or {})
    _, hints, rollback = _failed_validation_from_orchestration_step(last_envelope)
    if not hints or not bool(rollback.get("ok", False)):
        return False
    observation = dict(last_envelope.get("observation") or {})
    return not bool(observation.get("ok", False))


def _has_retryable_failed_envelope(steps: list[dict[str, Any]]) -> bool:
    envelope_steps = [
        step
        for step in list(steps or [])
        if str(dict(step.get("observation") or {}).get("intent") or "").strip() == "orchestration.execute_envelope"
    ]
    if not envelope_steps:
        return False
    last_envelope = dict(envelope_steps[-1] or {})
    _, hints, rollback = _failed_validation_from_orchestration_step(last_envelope)
    if not hints or not bool(rollback.get("ok", False)):
        return False
    observation = dict(last_envelope.get("observation") or {})
    return not bool(observation.get("ok", False))


def _infer_literal_candidate_repair(
    *,
    user_text: str,
    steps: list[dict[str, Any]],
    current_read_hints: dict[str, Any],
) -> dict[str, str] | None:
    latest_validation = _latest_failed_validation_observation(steps)
    if str(latest_validation.get("intent") or "").strip() != "workspace.run_tests":
        return None
    if not _looks_like_failing_test_repair_request(user_text, validation_step=latest_validation):
        return None
    error_path, function_name, expected_literal = _failing_test_expectation_from_steps(steps)
    current_path = str(current_read_hints.get("path") or "").strip()
    if not error_path or not current_path or current_path == error_path:
        return None
    implementation_content = str(current_read_hints.get("content") or "").strip()
    if not function_name or not expected_literal or not implementation_content:
        return None

    direct_repair = _literal_return_candidate_repair(
        lines=_read_hint_lines(current_read_hints),
        function_name=function_name,
        expected_literal=expected_literal,
        current_path=current_path,
    )
    if direct_repair is not None:
        return direct_repair
    binding_repair = _literal_binding_candidate_repair(
        lines=_read_hint_lines(current_read_hints),
        function_name=function_name,
        expected_literal=expected_literal,
        current_path=current_path,
    )
    if binding_repair is not None:
        return binding_repair

    current_lines = _read_hint_lines(current_read_hints)
    if not current_lines:
        return None
    for prior_read_hints in _iter_read_file_hints(steps):
        prior_path = str(prior_read_hints.get("path") or "").strip()
        if not prior_path or prior_path in {current_path, error_path}:
            continue
        imported_binding_name = _imported_binding_name_from_path(
            lines=_read_hint_lines(prior_read_hints),
            function_name=function_name,
            current_path=current_path,
        )
        if imported_binding_name:
            imported_binding_repair = _top_level_literal_binding_candidate_repair(
                lines=current_lines,
                binding_name=imported_binding_name,
                expected_literal=expected_literal,
                current_path=current_path,
            )
            if imported_binding_repair is not None:
                return imported_binding_repair
        delegate_function = _single_delegate_function_name(
            lines=_read_hint_lines(prior_read_hints),
            function_name=function_name,
        )
        if not delegate_function:
            continue
        delegated_repair = _literal_return_candidate_repair(
            lines=current_lines,
            function_name=delegate_function,
            expected_literal=expected_literal,
            current_path=current_path,
        )
        if delegated_repair is not None:
            return delegated_repair
        delegated_binding_repair = _literal_binding_candidate_repair(
            lines=current_lines,
            function_name=delegate_function,
            expected_literal=expected_literal,
            current_path=current_path,
        )
        if delegated_binding_repair is not None:
            return delegated_binding_repair
    return None


def _failing_test_expectation_from_steps(steps: list[dict[str, Any]]) -> tuple[str, str, str]:
    latest_validation = _latest_failed_validation_observation(steps)
    error_path = str(latest_validation.get("error_path") or "").strip()
    if not error_path:
        return "", "", ""
    test_read_hints = _latest_read_file_hints(steps, path=error_path)
    test_content = str(test_read_hints.get("content") or "").strip()
    if not test_content:
        return error_path, "", ""
    expected_match = re.search(
        r"assert\s+(?P<call>[A-Za-z_][A-Za-z0-9_\.]*)\s*\(\s*\)\s*==\s*(?P<expected>-?\d+|True|False|None|'[^']*'|\"[^\"]*\")",
        test_content,
    )
    if not expected_match:
        return error_path, "", ""
    function_name = str(expected_match.group("call") or "").strip().split(".")[-1]
    expected_literal = str(expected_match.group("expected") or "").strip()
    return error_path, function_name, expected_literal


def _read_hint_lines(read_hints: dict[str, Any]) -> list[str]:
    return [str(item.get("text") or "") for item in list(read_hints.get("lines") or []) if isinstance(item, dict)]


def _single_function_body_line(lines: list[str], function_name: str) -> str:
    function_start = None
    for index, raw_line in enumerate(list(lines or [])):
        if re.match(rf"^\s*def\s+{re.escape(function_name)}\s*\(", raw_line):
            function_start = index
            break
    if function_start is None:
        return ""
    body_lines: list[str] = []
    for raw_line in list(lines or [])[function_start + 1 :]:
        if re.match(r"^\s*(def|class)\s+", raw_line):
            break
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        body_lines.append(raw_line)
    if len(body_lines) != 1:
        return ""
    return str(body_lines[0] or "")


def _literal_return_candidate_repair(
    *,
    lines: list[str],
    function_name: str,
    expected_literal: str,
    current_path: str,
) -> dict[str, str] | None:
    body_line = _single_function_body_line(lines, function_name)
    if not body_line:
        return None
    return_match = re.match(r"^(?P<indent>\s*)return\s+(?P<value>-?\d+|True|False|None|'[^']*'|\"[^\"]*\")\s*$", body_line)
    if not return_match:
        return None
    actual_literal = str(return_match.group("value") or "").strip()
    if actual_literal == expected_literal:
        return None
    return {
        "path": current_path,
        "old_text": body_line.strip(),
        "new_text": f"return {expected_literal}",
    }


def _literal_binding_candidate_repair(
    *,
    lines: list[str],
    function_name: str,
    expected_literal: str,
    current_path: str,
) -> dict[str, str] | None:
    body_line = _single_function_body_line(lines, function_name)
    if not body_line:
        return None
    binding_return_match = re.match(r"^\s*return\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*$", body_line)
    if not binding_return_match:
        return None
    binding_name = str(binding_return_match.group("name") or "").strip()
    if not binding_name:
        return None
    return _top_level_literal_binding_candidate_repair(
        lines=lines,
        binding_name=binding_name,
        expected_literal=expected_literal,
        current_path=current_path,
    )


def _top_level_literal_binding_candidate_repair(
    *,
    lines: list[str],
    binding_name: str,
    expected_literal: str,
    current_path: str,
) -> dict[str, str] | None:
    binding_matches: list[tuple[str, str]] = []
    binding_pattern = re.compile(
        rf"^(?P<name>{re.escape(binding_name)})\s*=\s*(?P<value>-?\d+|True|False|None|'[^']*'|\"[^\"]*\")\s*$"
    )
    for raw_line in list(lines or []):
        if raw_line.startswith((" ", "\t")):
            continue
        match = binding_pattern.match(raw_line)
        if not match:
            continue
        binding_matches.append((raw_line, str(match.group("value") or "").strip()))
    if len(binding_matches) != 1:
        return None
    binding_line, actual_literal = binding_matches[0]
    if actual_literal == expected_literal:
        return None
    return {
        "path": current_path,
        "old_text": binding_line.strip(),
        "new_text": f"{binding_name} = {expected_literal}",
    }


def _imported_binding_name_from_path(
    *,
    lines: list[str],
    function_name: str,
    current_path: str,
) -> str:
    body_line = _single_function_body_line(lines, function_name)
    if not body_line:
        return ""
    binding_return_match = re.match(r"^\s*return\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*$", body_line)
    if not binding_return_match:
        return ""
    local_binding_name = str(binding_return_match.group("name") or "").strip()
    if not local_binding_name:
        return ""
    path_value = str(current_path or "").strip()
    if not path_value:
        return ""
    module_stem = Path(path_value).with_suffix("").as_posix().replace("/", ".").strip(".")
    module_variants = {module_stem}
    if "." in module_stem:
        module_variants.add(module_stem.split(".")[-1])
    import_pattern = re.compile(
        r"^\s*from\s+(?P<module>[A-Za-z_][A-Za-z0-9_\.]*)\s+import\s+(?P<imports>.+?)\s*$"
    )
    alias_pattern = re.compile(
        r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+as\s+(?P<alias>[A-Za-z_][A-Za-z0-9_]*)$"
    )
    for raw_line in list(lines or []):
        if raw_line.startswith((" ", "\t")):
            continue
        match = import_pattern.match(raw_line)
        if not match:
            continue
        module_name = str(match.group("module") or "").strip()
        if module_name not in module_variants:
            continue
        imports_text = str(match.group("imports") or "").strip()
        for part in [item.strip() for item in imports_text.split(",") if item.strip()]:
            alias_match = alias_pattern.match(part)
            if alias_match:
                imported_name = str(alias_match.group("name") or "").strip()
                local_name = str(alias_match.group("alias") or "").strip()
                if local_name == local_binding_name:
                    return imported_name
                continue
            if part == local_binding_name:
                return part
    return ""


def _single_delegate_function_name(*, lines: list[str], function_name: str) -> str:
    body_line = _single_function_body_line(lines, function_name)
    if not body_line:
        return ""
    delegate_match = re.match(r"^\s*return\s+(?P<callee>[A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\)\s*$", body_line)
    if not delegate_match:
        return ""
    return str(delegate_match.group("callee") or "").strip()


def _planned_delegate_lookup_after_read(
    *,
    user_text: str,
    steps: list[dict[str, Any]],
    current_read_hints: dict[str, Any],
) -> WorkflowPlannerDecision | None:
    latest_validation = _latest_failed_validation_observation(steps)
    if str(latest_validation.get("intent") or "").strip() != "workspace.run_tests":
        return None
    if not _looks_like_failing_test_repair_request(user_text, validation_step=latest_validation):
        return None
    error_path, function_name, _ = _failing_test_expectation_from_steps(steps)
    current_path = str(current_read_hints.get("path") or "").strip()
    if not error_path or not function_name or not current_path or current_path == error_path:
        return None
    delegate_function = _single_delegate_function_name(
        lines=_read_hint_lines(current_read_hints),
        function_name=function_name,
    )
    if not delegate_function:
        return None
    if _workflow_step_exists(steps, "workspace.symbol_search", key="symbol", value=delegate_function):
        return None
    return WorkflowPlannerDecision(
        handled=True,
        reason="planned_symbol_search_after_delegate_inspection",
        next_payload={"intent": "workspace.symbol_search", "arguments": {"symbol": delegate_function, "limit": 10}},
    )


def _explicit_replace_request(user_text: str) -> dict[str, str] | None:
    text = str(user_text or "").strip()
    if not text:
        return None
    fenced = re.search(
        r"replace\s+`(?P<old>[^`]+)`\s+with\s+`(?P<new>[^`]+)`(?:\s+in\s+(?P<path>[A-Za-z0-9_./-]+\.[A-Za-z0-9_+-]+))?",
        text,
        re.IGNORECASE,
    )
    if fenced:
        return {
            "old_text": str(fenced.group("old") or "").strip(),
            "new_text": str(fenced.group("new") or "").strip(),
            "path": _normalize_inline_path(str(fenced.group("path") or "").strip()),
        }
    plain = re.search(
        r"replace\s+(?P<old>[A-Za-z0-9_.:/-]+)\s+with\s+(?P<new>[A-Za-z0-9_.:/-]+)(?:\s+in\s+(?P<path>[A-Za-z0-9_./-]+\.[A-Za-z0-9_+-]+))?",
        text,
        re.IGNORECASE,
    )
    if plain:
        return {
            "old_text": str(plain.group("old") or "").strip(),
            "new_text": str(plain.group("new") or "").strip(),
            "path": _normalize_inline_path(str(plain.group("path") or "").strip()),
        }
    return None


def _explicit_unified_diff_request(user_text: str) -> str:
    text = str(user_text or "")
    if not text.strip():
        return ""
    fenced = re.search(
        r"```(?:diff|patch)\s*\n(?P<patch>.*?)(?:\n```|```)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if fenced:
        patch = str(fenced.group("patch") or "").strip("\n")
        if patch and "--- " in patch and "+++ " in patch and "@@ " in patch:
            return patch
    return ""


def _explicit_command_request(user_text: str) -> str:
    text = str(user_text or "").strip()
    if not text:
        return ""
    fenced = re.search(r"(?:run|execute|retry|rerun)\s+`(?P<command>[^`]+)`", text, re.IGNORECASE)
    if fenced:
        return _normalize_inline_command(str(fenced.group("command") or "").strip())
    common = re.search(
        r"\b(?:run|execute|retry|rerun)\s+(?P<command>(?:pytest(?:\s+-[A-Za-z0-9-]+)*(?:\s+[A-Za-z0-9_./:-]+)*)|(?:python3?\s+[A-Za-z0-9_./:-]+(?:\s+[A-Za-z0-9_./:=+-]+)*)|(?:npm\s+(?:test|run\s+[A-Za-z0-9:_-]+))|(?:cargo\s+test(?:\s+[A-Za-z0-9_./:-]+)*))",
        text,
        re.IGNORECASE,
    )
    if common:
        return _normalize_inline_command(str(common.group("command") or "").strip())
    return ""


def _normalize_inline_command(command: str) -> str:
    clean = " ".join(str(command or "").split()).strip()
    if not clean:
        return ""
    clean = re.sub(r"(?P<stem>[A-Za-z0-9_/-]+)\.\s+(?P<ext>py|js|ts|json|yaml|yml|toml|sh|md)\b", r"\g<stem>.\g<ext>", clean)
    return clean


def _normalize_inline_path(path: str) -> str:
    return _normalize_inline_command(path)


def _validation_step_from_request(*, user_text: str, explicit_command: str) -> dict[str, Any] | None:
    lowered = f" {' '.join(str(user_text or '').lower().split())} "
    command = _normalize_inline_command(explicit_command)
    if command:
        normalized = command.lower()
        if re.match(r"^(?:python\d?(?:\.\d+)?\s+-m\s+)?pytest\b", normalized):
            return {"intent": "workspace.run_tests", "arguments": {"command": command}}
        if re.match(r"^(?:python\d?(?:\.\d+)?\s+-m\s+)?ruff\s+check\b", normalized):
            return {"intent": "workspace.run_lint", "arguments": {"command": command}}
        if re.match(r"^(?:python\d?(?:\.\d+)?\s+-m\s+)?ruff\s+format\b", normalized):
            return {
                "intent": "workspace.run_formatter",
                "arguments": {
                    "command": command,
                    "apply": " --check" not in normalized,
                },
            }
        return None
    if any(marker in lowered for marker in (" run tests ", " rerun tests ", " pytest ")):
        return {"intent": "workspace.run_tests", "arguments": {}}
    if any(marker in lowered for marker in (" run lint ", " rerun lint ", " lint it ", " ruff check ")):
        return {"intent": "workspace.run_lint", "arguments": {}}
    if any(marker in lowered for marker in (" format it ", " run formatter ", " check formatting ", " ruff format ")):
        return {
            "intent": "workspace.run_formatter",
            "arguments": {
                "apply": any(marker in lowered for marker in (" format it ", " apply formatting ", " run formatter ")),
            },
        }
    return None


def _looks_like_failing_test_repair_request(
    user_text: str,
    *,
    validation_step: dict[str, Any] | None,
) -> bool:
    if not isinstance(validation_step, dict):
        return False
    if str(validation_step.get("intent") or "").strip() != "workspace.run_tests":
        return False
    lowered = re.sub(r"[^a-z0-9]+", " ", str(user_text or "").lower())
    lowered = f" {' '.join(lowered.split())} "
    failure_markers = (
        " failing test ",
        " failing tests ",
        " tests are failing ",
        " test is failing ",
        " broken test ",
        " broken tests ",
        " pytest is failing ",
        " traceback ",
        " stack trace ",
        " assertionerror ",
        " fix the test ",
        " fix the tests ",
    )
    return any(marker in lowered for marker in failure_markers)


#: Broader-suite phrasings that license a DISTINCT cumulative-regression child
#: beside the specific recurrence test (root-cause contract, amendment gap 3).
#: Structure-bounded markers on the normalized text, the same bounded-marker
#: discipline the rest of this planner uses; a bare specific command never
#: matches them.
_SUITE_MARKERS = (
    " run the tests ",
    " run all the tests ",
    " run the full test suite ",
    " run the test suite ",
    " run the whole test suite ",
    " run the whole suite ",
    " full test suite ",
    " whole test suite ",
    " all the tests ",
)


def _regression_step_from_request(
    user_text: str,
    *,
    validation_step: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """The DISTINCT cumulative-suite validation step, when the request names one.

    Only when a SPECIFIC validation step exists (the recurrence identity) and
    the request separately asks for the broader suite: the default-suite step
    (`workspace.run_tests` with no command) is a genuinely different identity,
    so the completion gate's anti-vacuous check (same-identity recycling) can
    never be satisfied by accident. Returns None otherwise — nothing wider was
    asked for, and nothing is invented.
    """
    if not isinstance(validation_step, dict):
        return None
    lowered = re.sub(r"[^a-z0-9]+", " ", str(user_text or "").lower())
    lowered = f" {' '.join(lowered.split())} "
    if not any(marker in lowered for marker in _SUITE_MARKERS):
        return None
    specific = str(dict(validation_step.get("arguments") or {}).get("command") or "").strip()
    default_identity = "workspace.run_tests"
    if not specific or specific == default_identity:
        # The "specific" step already IS the default suite: there is no
        # broader identity to schedule.
        return None
    return {"intent": "workspace.run_tests", "arguments": {}}


def _planned_command_payload(*, user_text: str, command: str) -> tuple[str, dict[str, Any]] | None:
    validation_step = _validation_step_from_request(user_text=user_text, explicit_command=command)
    if validation_step is not None:
        return ("planned_validation_run", validation_step)
    normalized_command = _normalize_inline_command(command)
    if not normalized_command:
        return None
    return (
        "planned_command_run",
        {"intent": "sandbox.run_command", "arguments": {"command": normalized_command}},
    )


def _planned_orchestrated_operator_payload(
    *,
    user_text: str,
    task_class: str,
    source_context: dict[str, Any] | None,
    replacement: dict[str, Any] | None,
    patch_text: str,
    explicit_command: str,
) -> dict[str, Any] | None:
    # The root-cause contract must ride the CALLER's context dict (the turn's
    # own object), not the local copy below — a contract written to the copy
    # dies with this frame and the turn's executor/finalizer never see it.
    caller_context = source_context if isinstance(source_context, dict) else None
    source_context = dict(source_context or {})
    workspace = str(source_context.get("workspace") or source_context.get("workspace_root") or "").strip()
    if not workspace:
        return None
    replace_payload = dict(replacement or {})
    normalized_patch = str(patch_text or "").strip()
    path = str(replace_payload.get("path") or "").strip()
    old_text = str(replace_payload.get("old_text") or "").strip()
    new_text = str(replace_payload.get("new_text") or "").strip()
    if not normalized_patch and not (path and old_text and new_text):
        if not (old_text and new_text):
            return None
    validation_step = _validation_step_from_request(user_text=user_text, explicit_command=explicit_command)
    if validation_step is None:
        return None
    bounded_repo_repair = looks_like_bounded_repo_repair_request(user_text)
    normalized_request = re.sub(r"[^a-z0-9]+", " ", str(user_text or "").lower())
    normalized_request = f" {' '.join(normalized_request.split())} "
    if not normalized_patch and not any(
        marker in normalized_request
        for marker in (" apply ", " replace ", " patch ", " edit ", " change ", " fix ")
    ):
        return None
    normalized_task_class = str(task_class or "").strip().lower()
    if normalized_task_class not in {
        "unknown",
        "debugging",
        "dependency_resolution",
        "config",
        "security_hardening",
        "integration_orchestration",
    } and not bounded_repo_repair:
        return None
    planned_task_class = "debugging" if bounded_repo_repair else (normalized_task_class or "debugging")
    from core.orchestration import build_task_envelope

    task_suffix = hashlib.sha1(
        f"{planned_task_class}|{path}|{old_text}|{new_text}|{normalized_patch}|{explicit_command}".encode()
    ).hexdigest()[:12]
    queen_id = f"queen-{task_suffix}"
    privacy_class = str(source_context.get("share_scope") or "local_only")
    preflight_verifier = None
    preflight_task_id = f"preflight-verify-{task_suffix}"
    final_verifier_dependencies = [f"coder-{task_suffix}"]
    if _looks_like_failing_test_repair_request(user_text, validation_step=validation_step):
        preflight_step = {
            "step_id": "capture-failing-validation",
            **dict(validation_step),
            "allow_failure": True,
        }
        preflight_verifier = build_task_envelope(
            role="verifier",
            task_id=preflight_task_id,
            parent_task_id=queen_id,
            goal="Capture the current failing test state before any workspace mutation.",
            inputs={
                "task_class": planned_task_class,
                "runtime_tools": [preflight_step],
            },
            required_receipts=("tool_receipt", "validation_result"),
            privacy_class=privacy_class,
        )
    if normalized_patch:
        coder_tools = [
            {
                "step_id": "apply-patch",
                "intent": "workspace.apply_unified_diff",
                "arguments": {"patch": normalized_patch},
            },
        ]
    elif path:
        coder_tools = [
            {"step_id": "inspect-target", "intent": "workspace.read_file", "arguments": {"path": path, "start_line": 1, "max_lines": 240}},
            {
                "step_id": "apply-replacement",
                "intent": "workspace.replace_in_file",
                "arguments": {
                    "path": path,
                    "old_text": old_text,
                    "new_text": new_text,
                    "replace_all": True,
                },
            },
        ]
    else:
        path_ref = {
            "$from_step": "locate-replacement-target",
            "$path": "observation.primary_path",
            "$require_single_match": True,
        }
        coder_tools = [
            {
                "step_id": "locate-replacement-target",
                "intent": "workspace.search_text",
                "arguments": {"query": old_text, "limit": 2},
            },
            {
                "step_id": "inspect-target",
                "intent": "workspace.read_file",
                "arguments": {"path": dict(path_ref), "start_line": 1, "max_lines": 240},
            },
            {
                "step_id": "apply-replacement",
                "intent": "workspace.replace_in_file",
                "arguments": {
                    "path": dict(path_ref),
                    "old_text": old_text,
                    "new_text": new_text,
                    "replace_all": True,
                },
            },
        ]
    coder = build_task_envelope(
        role="coder",
        task_id=f"coder-{task_suffix}",
        parent_task_id=queen_id,
        goal=(
            "Apply the requested unified diff inside the active workspace."
            if normalized_patch
            else f"Apply the requested workspace change in `{path}`."
            if path
            else "Locate the requested workspace change target, inspect it, and apply the requested replacement."
        ),
        inputs={
            "task_class": planned_task_class,
            "depends_on": [preflight_task_id] if preflight_verifier is not None else [],
            "runtime_tools": coder_tools,
        },
        required_receipts=("tool_receipt",),
        privacy_class=privacy_class,
    )
    verifier = build_task_envelope(
        role="verifier",
        task_id=f"verify-{task_suffix}",
        parent_task_id=queen_id,
        goal="Validate the requested workspace change.",
        inputs={
            "task_class": planned_task_class,
            "depends_on": final_verifier_dependencies,
            "rollback_on_failure": True,
            "runtime_tools": [validation_step],
        },
        required_receipts=("tool_receipt", "validation_result"),
        privacy_class=privacy_class,
    )
    # ROOT-CAUSE CONTRACT (amendment gap 3) — a DISTINCT cumulative-regression
    # child when the request separately names the broader suite. Its identity
    # (the workspace default suite) differs from the recurrence test, so a
    # repair can reach `root_cause_repaired` through the real executor with
    # real receipts — and a red suite can honestly refuse completion.
    regression_step = _regression_step_from_request(user_text, validation_step=validation_step)
    regression_verifier = None
    if regression_step is not None:
        regression_verifier = build_task_envelope(
            role="verifier",
            task_id=f"regression-verify-{task_suffix}",
            parent_task_id=queen_id,
            goal="Run the wider workspace test suite after the change.",
            inputs={
                "task_class": planned_task_class,
                "depends_on": final_verifier_dependencies,
                "runtime_tools": [regression_step],
            },
            required_receipts=("tool_receipt", "validation_result"),
            privacy_class=privacy_class,
        )
    queen = build_task_envelope_for_request(
        user_text,
        context={"share_scope": privacy_class},
        task_id=queen_id,
        chat_surface=False,
        planner_style_requested=False,
    )
    queen_payload = {
        **queen.to_dict(),
        "role": "queen",
        "inputs": {
            **dict(queen.inputs or {}),
            "task_class": planned_task_class,
            "planner_source": "execution_planner",
            "subtasks": [
                *( [preflight_verifier.to_dict()] if preflight_verifier is not None else [] ),
                coder.to_dict(),
                verifier.to_dict(),
                *( [regression_verifier.to_dict()] if regression_verifier is not None else [] ),
            ],
        },
        "merge_strategy": "highest_score",
        "required_receipts": [],
    }
    # ROOT-CAUSE CONTRACT — the orchestrated repair lane opens the typed
    # diagnosis for this problem on the turn's own context. The problem key is
    # the content-derived task suffix, so a RETRY of the same repair resumes
    # the same diagnosis identity with its evidence intact (rule 6). The
    # user's request is recorded as the DECLARED hypothesis — attributed to
    # its source, never invented by the runtime; validation truth lands later
    # from the envelope receipts (`record_envelope_outcome` at the executor).
    try:
        from core import root_cause_contract as _rcc

        _validation_arguments = dict(validation_step or {}).get("arguments")
        _validation_arguments = (
            _validation_arguments if isinstance(_validation_arguments, dict) else {}
        )
        _validation_identity = str(
            _validation_arguments.get("command")
            or dict(validation_step or {}).get("intent")
            or ""
        )
        _rcc.open_root_cause_scope(
            caller_context,
            problem_key=f"orchestrated-repair|{task_suffix}",
            problem_statement=" ".join(str(user_text or "").split())[:400],
            owning_seam=str(
                path or ("workspace unified diff" if normalized_patch else "")
            ).strip(),
            symptoms=(
                (f"failing validation: {_validation_identity}",)
                if preflight_verifier is not None
                else ()
            ),
        )
        _rcc.set_hypothesis(
            caller_context,
            f"user-declared repair request: {' '.join(str(user_text or '').split())[:240]}",
            source="user_declared",
        )
    except Exception:
        pass
    return {"intent": "orchestration.execute_envelope", "arguments": {"task_envelope": queen_payload}}


def _web_planning_excluded_by_requirements(text: str, *, task_class: str, source_context: dict[str, Any] | None) -> bool:
    """True when the turn's frozen requirements confine tools to families that do not include the web."""
    try:
        from core.capability_graph import _TOOLSET_HINT_TO_FAMILY
        from core.execution_requirements import requirements_for

        requirements = requirements_for(text, task_class=task_class, source_context=source_context)
    except Exception:
        return False
    if not requirements.tools_required or not requirements.allowed_toolsets:
        return False
    families = {_TOOLSET_HINT_TO_FAMILY.get(str(hint).lower().strip(), "") for hint in requirements.allowed_toolsets}
    return "web" not in families


def plan_tool_workflow(
    *,
    user_text: str,
    task_class: str,
    executed_steps: list[dict[str, Any]],
    source_context: dict[str, Any] | None,
) -> WorkflowPlannerDecision:
    raw_text = str(user_text or "")
    text = " ".join(raw_text.split()).strip()
    lowered = f" {text.lower()} "
    followup_resume = _looks_like_followup_resume_request(text)
    steps = [dict(step) for step in list(executed_steps or []) if isinstance(step, dict)]
    replacement = _explicit_replace_request(raw_text)
    patch_text = _explicit_unified_diff_request(raw_text)
    explicit_command = _explicit_command_request(raw_text)
    compare_or_verify = any(marker in lowered for marker in (" compare ", " versus ", " vs ", " verify ", " confirm ", " is it true "))
    lookup_followup_text = ""
    if followup_resume and not (looks_like_explicit_lookup_request(text) or looks_like_public_entity_lookup_request(text)):
        lookup_followup_text = _recover_lookup_followup_from_history(source_context)
    research_text = lookup_followup_text or text
    public_entity_lookup = looks_like_public_entity_lookup_request(research_text)
    # The turn's ONE requirements authority may confine the toolsets ("email" for a mailbox request that
    # names a sender). A confined turn is never planned onto the web -- the same law the inferred research
    # flow below already follows. Measured served 2026-09-14: "Now check my personal inbox for the orchard
    # supplier." read as a public-entity lookup, was planned onto web.search, ran the research lane and
    # reached no email tool although the requirements had confined it to the email family.
    if public_entity_lookup and _web_planning_excluded_by_requirements(text, task_class=task_class, source_context=source_context):
        public_entity_lookup = False
    explicit_lookup = looks_like_explicit_lookup_request(research_text) or public_entity_lookup
    tool_inventory_request = _looks_like_tool_inventory_request(text)
    machine_directory_list = _extract_safe_machine_directory_listing(text)
    machine_directory_create = _extract_safe_machine_directory_create(text)
    machine_specs_request = _extract_machine_specs_request(text)
    machine_diagnostics = machine_diagnostics_intent(text)
    git_payload = _planned_git_payload(text)
    workspace_search_payload = _planned_workspace_search_payload(text)
    workspace_bootstrap_path = _extract_workspace_bootstrap_path(text)
    workspace_bootstrap_request = _looks_like_workspace_bootstrap_request(text)
    workspace_file_plan = _extract_workspace_file_plan(
        verbatim_request_text(source_context, raw_text) or raw_text,
        source_context=source_context,
    )
    entity_query, entity_retry_query = _entity_lookup_query_variants(research_text)
    last_step = dict(steps[-1] or {}) if steps else {}
    last_intent = str(last_step.get("tool_name") or "").strip()
    workspace_scoped_lookup = any(
        marker in lowered
        for marker in (" this workspace ", " current workspace ", " in the workspace ", " this repo ", " this repository ")
    ) and any(marker in lowered for marker in (" find ", " search ", " locate ", " where "))
    # Inferred research reads the user's OWN eligible words (core.retrieval_constraints owns which words
    # those are): a reported example utterance -- "for example, a user might type pull up my latest
    # email" -- is not a request for the latest anything. Measured 2026-09-14: this branch planned
    # web.search for that sentence. An explicit lookup keeps its own reading above.
    try:
        from core.retrieval_constraints import analyze_retrieval_constraints as _eligible_constraints

        eligible_lowered = f" {' '.join(_eligible_constraints(text).eligible_text.split()).lower()} "
    except Exception:
        eligible_lowered = lowered
    research_flow = not workspace_scoped_lookup and bool(
        explicit_lookup
        or compare_or_verify
        or task_class in {"research", "chat_research", "system_design"}
        or any(marker in eligible_lowered for marker in (" latest ", " current ", " docs ", " documentation ", " research ", " source "))
        or (followup_resume and last_intent in {"web.search", "web.fetch", "web.research"})
    )
    # The turn's ONE requirements authority may confine the toolsets ("wallet" for a payment demand).
    # A confined turn is never planned onto the web: measured 2026-09-03, "What is the status of payment
    # pay-...?" classified as research and this planner sent the proposal id to a search engine before
    # any wallet tool ran. Live-data confinements (prices, weather) map to the web family and keep it.
    if research_flow and _web_planning_excluded_by_requirements(text, task_class=task_class, source_context=source_context):
        research_flow = False
    # An open email conversation owns what its follow-ups refer to
    # (core.email_work_state.email_work_owns_follow_up_references): "Confirm it's in the Sent folder." or
    # "Is this the latest estimate?" there is about the mailbox as plausibly as the web, so research inferred
    # from such words is left to the model, which is offered the session's email tools. An explicit web lookup
    # keeps its plan. Measured 2026-09-14: this branch planned web.search for "Confirm it's in the Sent
    # folder." as a served reconcile turn's first step; the search failed and the model was never asked.
    if research_flow and not explicit_lookup:
        from core.email_work_state import email_work_owns_follow_up_references

        if email_work_owns_follow_up_references(source_context):
            research_flow = False

    # RETIRED with the fuzzy create/task markers (product decision of 2026-09-17): planning
    # hive.create_topic is decided by `hive_create_requested` -- the Hive product named as the
    # action's target -- plus a genuinely bound "proceed" continuation below. Only the
    # proceed-with-task recognizer remains, for that bound continuation.
    def _proceed_with_task(lo: str) -> bool:
        return any(m in lo for m in (" proceed ", " do it ", " do all ", " start working ", " go ahead ", " carry on ")) and (
            "task" in lo or "hive" in lo or "create" in lo
        )

    if not steps:
        from core.instructional_request import asks_for_instructions_not_execution
        from core.tool_demand_signals import resolve_demand_signals

        try:
            repair_demanded = "code.task.open" in resolve_demand_signals(text).explicit_intents
        except Exception:
            repair_demanded = False
        if repair_demanded and asks_for_instructions_not_execution(text):
            # The resolver's rule is instruction-blind ("how do I fix a failing test?" seats
            # the tool); the instruction authority owns whether the user asked for DOING or
            # for EXPLAINING. A repair seat that the instruction authority declines is a
            # question, and a question must not start mutating work.
            repair_demanded = False
        if repair_demanded and not explicit_command:
            # An open code task in THIS session owns repair-worded follow-ups: its journal --
            # read from disk, so a restarted daemon makes the same call -- already seats the
            # control plane the recovery needs. Opening a SECOND task here would strand the
            # first mid-recovery (measured served: a "re-diagnose the defect" follow-up forked
            # a fresh task at `reproduce` while the open one waited at a failed verification).
            try:
                from core.code_assistant.task_runtime import active_task_control_intents

                session_task_open = bool(active_task_control_intents(source_context))
            except Exception:
                session_task_open = False
            if not session_task_open:
                # Request interpretation already owns this demand. Start its existing journal
                # through the tool door before generic discovery; the model still owns the
                # diagnosis and repair, and every later effect retains the normal approvals.
                # The resolver is the ONE demand authority: the tool offer seats `code.task.open`
                # from exactly this signal, and the folder-overview fast path declines on it --
                # reading it here too is what keeps all three seams from disagreeing about what
                # a repair demand is (measured: "fix the bug in this project" without the word
                # "test" was seated by the resolver, consumed by the overview, and never planned).
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_code_task_start",
                    next_payload={"intent": "code.task.open", "arguments": {"objective": raw_text}},
                )
        if lookup_followup_text and explicit_lookup:
            if public_entity_lookup:
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_entity_lookup_search",
                    next_payload={"intent": "web.search", "arguments": {"query": entity_query or research_text, "limit": 4}},
                )
            if research_flow:
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_research_search",
                    next_payload={"intent": "web.search", "arguments": {"query": research_text, "limit": 4}},
                )
        if tool_inventory_request:
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_operator_tool_inventory",
                next_payload={"intent": "operator.list_tools", "arguments": {}},
            )
        if machine_largest_intent(text) is not None:
            largest_arguments: dict[str, Any] = {}
            largest_scope = machine_disk_scope(text)
            if largest_scope:
                largest_arguments["drive"] = largest_scope
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_machine_find_largest",
                next_payload={"intent": "machine.find_largest", "arguments": largest_arguments},
            )
        if machine_display_intent(text, source_context=source_context) is not None:
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_machine_display_inspect",
                next_payload={"intent": "machine.display_inspect", "arguments": {}},
            )
        if machine_diagnostics is not None:
            diagnostics_arguments: dict[str, Any] = {}
            if machine_diagnostics == "machine.disk_usage":
                disk_scope = machine_disk_scope(text)
                if disk_scope:
                    diagnostics_arguments["drive"] = disk_scope
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_machine_diagnostics",
                next_payload={"intent": machine_diagnostics, "arguments": diagnostics_arguments},
            )
        if machine_specs_request is not None:
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_machine_specs_inspection",
                next_payload={"intent": "machine.inspect_specs", "arguments": machine_specs_request},
            )
        if _asks_which_workspace(text) and not any(
            str(step.get("tool_name") or "") == "workspace.identity" for step in steps
        ):
            # The `not any(...)` matters: this branch is deterministic, so without it the planner
            # re-plans the same call on every loop step. Measured — it ran workspace.identity
            # twice, tripped the repeated-tool guard, and synthesis then answered "I cannot
            # determine the current workspace folder" while holding the grounded result.
            # Claimed before the listing branch below on purpose: "what folder is our workspace set
            # on" is a question about a SETTING, and the listing extractor would otherwise be the
            # nearest thing that fires. Measured live 2026-07-28 — the model, having no tool that
            # reports the workspace, ran workspace.list_tree and returned ~50 lines and 13,938
            # tokens without ever naming the folder.
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_workspace_identity",
                next_payload={"intent": "workspace.identity", "arguments": {}},
            )
        if machine_directory_list:
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_safe_machine_directory_list",
                next_payload={"intent": "machine.list_directory", "arguments": machine_directory_list},
            )
        if machine_directory_create:
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_safe_machine_directory_create",
                next_payload={"intent": "machine.ensure_directory", "arguments": machine_directory_create},
            )
        folder_search = machine_folder_search_intent(text)
        if folder_search is not None:
            # A mailbox has folders too (core.email_work_state.email_work_owns_follow_up_references): in a
            # session with open email work the reference is left to the model, which is offered both
            # tool families. Measured 2026-09-14: with the front door declining, this branch planned
            # machine.find_folder for "Did it actually go out? Check the sent folder." as the turn's
            # first step, and the served turn walked this machine's drives.
            from core.email_work_state import email_work_owns_follow_up_references

            if email_work_owns_follow_up_references(source_context):
                folder_search = None
        if folder_search is not None:
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_machine_folder_search",
                next_payload={"intent": "machine.find_folder", "arguments": {"name": folder_search[1]}},
            )
        if git_payload is not None:
            reason, next_payload = git_payload
            return WorkflowPlannerDecision(
                handled=True,
                reason=reason,
                next_payload=next_payload,
            )
        if workspace_search_payload is not None:
            reason, next_payload = workspace_search_payload
            return WorkflowPlannerDecision(
                handled=True,
                reason=reason,
                next_payload=next_payload,
            )
        if workspace_file_plan is not None:
            pending_writes = _pending_workspace_writes(workspace_file_plan, steps)
            planned_directory = str(workspace_file_plan.get("directory") or "").strip()
            if planned_directory:
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_workspace_directory_bootstrap",
                    next_payload={"intent": "workspace.ensure_directory", "arguments": {"path": planned_directory}},
                    planned_batch=_planned_write_batch(pending_writes),
                )
            if pending_writes:
                first_write = dict(pending_writes[0] or {})
                if str(first_write.get("mode") or "").strip() == "append":
                    return WorkflowPlannerDecision(
                        handled=True,
                        reason="planned_read_before_append",
                        next_payload={"intent": "workspace.read_file", "arguments": {"path": first_write["path"], "start_line": 1, "max_lines": 400, "verbatim": True}},
                    )
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_workspace_write_file",
                    next_payload={"intent": "workspace.write_file", "arguments": {"path": first_write["path"], "content": first_write["content"]}},
                    planned_batch=_planned_write_batch(pending_writes[1:]),
                )
            read_path = str(workspace_file_plan.get("read_path") or "").strip()
            if read_path:
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_workspace_readback",
                    next_payload={"intent": "workspace.read_file", "arguments": {"path": read_path, "start_line": 1, "max_lines": 400, "verbatim": bool(workspace_file_plan.get("verbatim_read", False))}},
                )
        if workspace_bootstrap_request and workspace_bootstrap_path:
            wants_desktop = any(m in lowered for m in (" desktop ", " on my desktop", " my desktop", " on desktop", "~/desktop"))
            wants_home = any(m in lowered for m in (" home ", " home/", " my machine", " this machine", "~/", "folder in my machine"))
            home_dir = str(Path.home())
            if wants_desktop:
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_desktop_directory_create",
                    next_payload={"intent": "sandbox.run_command", "arguments": {"command": f"mkdir -p {home_dir}/Desktop/{workspace_bootstrap_path}"}},
                )
            if wants_home:
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_home_directory_create",
                    next_payload={"intent": "sandbox.run_command", "arguments": {"command": f"mkdir -p {home_dir}/{workspace_bootstrap_path}"}},
                )
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_workspace_directory_bootstrap",
                next_payload={"intent": "workspace.ensure_directory", "arguments": {"path": workspace_bootstrap_path}},
            )
        # HIVE PRODUCT OWNERSHIP (product decision of 2026-09-17): the planner plans
        # hive.create_topic only when the request names the Hive product as the action's target
        # -- the ONE matcher the frontdoor and dispatch seams also consult -- or when a
        # "proceed with the task" follow-up has a real Hive draft behind it in this
        # conversation. The old fuzzy "create + task" markers, and the build-vocabulary
        # blacklist that papered over them, are retired: a task-board spec's own "Add new
        # tasks" line and a quoted "create a hive task" inside a review ask instruct nothing.
        from core.agent_runtime.hive_topic_draft_intents import hive_create_requested

        hive_create_intent = hive_create_requested(lowered) or (
            _proceed_with_task(lowered)
            and _recover_hive_create_from_history(source_context) is not None
        )
        if hive_create_intent:
            raw_title = text.strip()
            for prefix in _HIVE_CREATE_PREFIXES:
                if raw_title.lower().startswith(prefix):
                    raw_title = raw_title[len(prefix):].strip().lstrip("-:–").strip()
                    break
            if "task:" in lowered:
                task_match = re.search(r"\btask\b\s*[:=-]\s*(.+?)(?=(?:\b(?:goal|summary)\b\s*[:=-])|(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", text, re.IGNORECASE)
                if task_match is not None:
                    raw_title = str(task_match.group(1) or "").strip()
            title = _normalize_hive_title_candidate(raw_title[:180])
            if _is_generic_hive_title_candidate(title):
                recovered = _recover_hive_create_from_history(source_context)
                if recovered is None:
                    return WorkflowPlannerDecision(handled=False, reason="no_workflow_plan")
                title, recovered_summary = recovered
                summary = recovered_summary[:4000] or title
            else:
                summary = text.strip()[:4000] or title
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_hive_create_topic",
                next_payload={
                    "intent": "hive.create_topic",
                    "arguments": {"title": title, "summary": summary, "topic_tags": ["research"]},
                },
            )
        orchestrated_payload = _planned_orchestrated_operator_payload(
            user_text=raw_text,
            task_class=task_class,
            source_context=source_context,
            replacement=replacement,
            patch_text=patch_text,
            explicit_command=explicit_command,
        )
        if orchestrated_payload is not None:
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_orchestrated_operator_envelope",
                next_payload=orchestrated_payload,
            )
        if explicit_command and any(marker in lowered for marker in (" retry ", " then retry", " then rerun", " rerun ")):
            command_payload = _planned_command_payload(user_text=raw_text, command=explicit_command)
            if command_payload is not None:
                reason, next_payload = command_payload
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_diagnose_run" if reason == "planned_command_run" else reason,
                    next_payload=next_payload,
                )
        if replacement is not None:
            path = str(replacement.get("path") or "").strip()
            if path:
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_read_before_edit",
                    next_payload={"intent": "workspace.read_file", "arguments": {"path": path, "start_line": 1, "max_lines": 120}},
                )
            query = str(replacement.get("old_text") or "").strip()
            if query:
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_search_before_edit",
                    next_payload={"intent": "workspace.search_text", "arguments": {"query": query, "limit": 10}},
                )
        if public_entity_lookup:
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_entity_lookup_search",
                next_payload={"intent": "web.search", "arguments": {"query": entity_query or text, "limit": 4}},
            )
        if research_flow:
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_research_search",
                next_payload={"intent": "web.search", "arguments": {"query": research_text, "limit": 4}},
            )
        if explicit_command:
            command_payload = _planned_command_payload(user_text=raw_text, command=explicit_command)
            if command_payload is not None:
                reason, next_payload = command_payload
                return WorkflowPlannerDecision(handled=True, reason=reason, next_payload=next_payload)
        return WorkflowPlannerDecision(handled=False, reason="no_workflow_plan")

    last_observation = dict(last_step.get("observation") or {})
    hints = extract_observation_followup_hints(last_observation)

    if research_flow:
        if last_intent == "web.search":
            current_query = str(dict(last_step.get("arguments") or {}).get("query") or "").strip()
            result_count = int(hints.get("result_count") or 0)
            if public_entity_lookup and result_count <= 0:
                if entity_retry_query and entity_retry_query != current_query and not _workflow_step_exists(steps, "web.search", key="query", value=entity_retry_query):
                    return WorkflowPlannerDecision(
                        handled=True,
                        reason="planned_entity_lookup_retry",
                        next_payload={"intent": "web.search", "arguments": {"query": entity_retry_query, "limit": 4}},
                    )
                if not _workflow_step_exists(steps, "web.research"):
                    return WorkflowPlannerDecision(
                        handled=True,
                        reason="planned_entity_lookup_research",
                        next_payload={"intent": "web.research", "arguments": {"query": entity_retry_query or entity_query or research_text}},
                    )
            if not compare_or_verify and int(hints.get("result_count") or 0) >= 2:
                return WorkflowPlannerDecision(handled=True, reason="research_enough_after_search", stop_after=True)
            primary_url = str(hints.get("primary_url") or "").strip()
            if primary_url and not _workflow_step_exists(steps, "web.fetch", key="url", value=primary_url):
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_fetch_after_search",
                    next_payload={"intent": "web.fetch", "arguments": {"url": primary_url}},
                )
            if compare_or_verify and not _workflow_step_exists(steps, "web.research"):
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_verify_after_search",
                    next_payload={"intent": "web.research", "arguments": {"query": research_text}},
                )
            if public_entity_lookup and not _workflow_step_exists(steps, "web.research") and result_count < 2:
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_entity_lookup_verify",
                    next_payload={"intent": "web.research", "arguments": {"query": entity_retry_query or entity_query or research_text}},
                )
            return WorkflowPlannerDecision(handled=True, reason="research_stop_after_search", stop_after=True)
        if last_intent == "web.fetch":
            if compare_or_verify and not _workflow_step_exists(steps, "web.research"):
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_research_after_fetch",
                    next_payload={"intent": "web.research", "arguments": {"query": research_text}},
                )
            return WorkflowPlannerDecision(handled=True, reason="research_stop_after_fetch", stop_after=True)
        if last_intent == "web.research":
            return WorkflowPlannerDecision(handled=True, reason="research_stop_after_verify", stop_after=True)

    if last_intent == "workspace.search_text":
        path = str(hints.get("primary_path") or "").strip()
        line = int(hints.get("primary_line") or 0)
        candidate_paths = []
        for candidate in [path, *list(hints.get("paths") or [])]:
            normalized = str(candidate or "").strip()
            if normalized and normalized not in candidate_paths:
                candidate_paths.append(normalized)
        next_path = ""
        next_line = 0
        for candidate in candidate_paths:
            if _workflow_step_exists(steps, "workspace.read_file", key="path", value=candidate):
                continue
            next_path = candidate
            next_line = line if candidate == path else 0
            break
        if next_path:
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_read_after_search",
                next_payload={
                    "intent": "workspace.read_file",
                    "arguments": {
                        "path": next_path,
                        "start_line": max(1, next_line - 8) if next_line else 1,
                        "max_lines": 60,
                    },
                },
            )
        return WorkflowPlannerDecision(handled=True, reason="workspace_stop_after_search", stop_after=True)

    if last_intent == "workspace.symbol_search":
        path = str(hints.get("primary_path") or "").strip()
        line = int(hints.get("primary_line") or 0)
        candidate_paths = []
        for candidate in [path, *list(hints.get("paths") or [])]:
            normalized = str(candidate or "").strip()
            if normalized and normalized not in candidate_paths:
                candidate_paths.append(normalized)
        next_path = ""
        next_line = 0
        for candidate in candidate_paths:
            if _workflow_step_exists(steps, "workspace.read_file", key="path", value=candidate):
                continue
            next_path = candidate
            next_line = line if candidate == path else 0
            break
        if next_path:
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_read_after_symbol_search",
                next_payload={
                    "intent": "workspace.read_file",
                    "arguments": {
                        "path": next_path,
                        "start_line": max(1, next_line - 8) if next_line else 1,
                        "max_lines": 60,
                    },
                },
            )
        latest_validation_hints = _latest_failed_validation_hints(steps)
        diagnostic_query = str(latest_validation_hints.get("diagnostic_query") or "").strip()
        if diagnostic_query:
            fallback = _planned_diagnostic_lookup_followup(
                steps=steps,
                diagnostic_query=diagnostic_query,
                symbol_reason="planned_symbol_search_after_validation_inspection",
                search_reason="planned_search_after_symbol_search",
            )
            if fallback is not None and fallback.next_payload["intent"] == "workspace.search_text":
                return fallback
        return WorkflowPlannerDecision(handled=True, reason="workspace_stop_after_symbol_search", stop_after=True)

    if last_intent == "workspace.read_file":
        read_path = str(hints.get("path") or "").strip()
        latest_validation_hints = _latest_failed_validation_hints(steps)
        effective_replacement = None if _has_retryable_failed_envelope(steps) else replacement
        pending_writes = _pending_workspace_writes(workspace_file_plan or {}, steps)
        if workspace_file_plan is not None and pending_writes:
            next_write = dict(pending_writes[0] or {})
            if str(next_write.get("mode") or "").strip() == "append" and read_path == str(next_write.get("path") or "").strip():
                existing_content = str(hints.get("content") or "")
                if existing_content:
                    content = existing_content + ("\n" if not existing_content.endswith("\n") else "") + str(next_write.get("content") or "")
                else:
                    content = str(next_write.get("content") or "")
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_append_after_read",
                    next_payload={"intent": "workspace.write_file", "arguments": {"path": read_path, "content": content}},
                )
        if effective_replacement is not None:
            target_path = str(effective_replacement.get("path") or read_path).strip()
            old_text = str(effective_replacement.get("old_text") or "").strip()
            new_text = str(effective_replacement.get("new_text") or "").strip()
            if target_path and old_text and new_text and not _workflow_step_exists(steps, "workspace.replace_in_file", key="path", value=target_path):
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_edit_after_read",
                    next_payload={
                        "intent": "workspace.replace_in_file",
                        "arguments": {
                            "path": target_path,
                            "old_text": old_text,
                            "new_text": new_text,
                            "replace_all": True,
                        },
                    },
                )
        if effective_replacement is None and not patch_text and _can_plan_candidate_repair_envelope(steps):
            candidate_repair = _infer_literal_candidate_repair(
                user_text=user_text,
                steps=steps,
                current_read_hints=hints,
            )
            if candidate_repair is not None:
                validation_command = str(
                    explicit_command
                    or _latest_failed_validation_observation(steps).get("command")
                    or ""
                ).strip()
                orchestrated_payload = _planned_orchestrated_operator_payload(
                    user_text=user_text,
                    task_class=task_class,
                    source_context=source_context,
                    replacement=candidate_repair,
                    patch_text="",
                    explicit_command=validation_command,
                )
                if orchestrated_payload is not None:
                    return WorkflowPlannerDecision(
                        handled=True,
                        reason="planned_candidate_repair_after_validation_diagnosis",
                        next_payload=orchestrated_payload,
                    )
        latest_lookup_intent, latest_lookup_hints = _latest_lookup_hints(steps)
        if str(latest_validation_hints.get("diagnostic_query") or "").strip() and latest_lookup_hints:
            next_lookup_path, next_lookup_line = _next_unread_lookup_path(
                steps=steps,
                hints=latest_lookup_hints,
                current_path=read_path,
            )
            if next_lookup_path:
                return WorkflowPlannerDecision(
                    handled=True,
                    reason=(
                        "planned_next_read_after_symbol_diagnosis"
                        if latest_lookup_intent == "workspace.symbol_search"
                        else "planned_next_read_after_search_diagnosis"
                    ),
                    next_payload={
                        "intent": "workspace.read_file",
                        "arguments": {
                            "path": next_lookup_path,
                            "start_line": max(1, next_lookup_line - 8) if next_lookup_line else 1,
                            "max_lines": 60,
                        },
                    },
                )
        delegate_lookup = _planned_delegate_lookup_after_read(
            user_text=user_text,
            steps=steps,
            current_read_hints=hints,
        )
        if delegate_lookup is not None:
            return delegate_lookup
        if explicit_command and not (
            _workflow_step_exists(steps, "sandbox.run_command", key="command", value=explicit_command)
            or _workflow_validation_command_already_attempted(steps, explicit_command)
        ):
            command_payload = _planned_command_payload(user_text=user_text, command=explicit_command)
            if command_payload is not None:
                reason, next_payload = command_payload
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_command_after_read" if reason == "planned_command_run" else "planned_validation_after_read",
                    next_payload=next_payload,
                )
        diagnostic_query = str(latest_validation_hints.get("diagnostic_query") or "").strip()
        if diagnostic_query:
            lookup_followup = _planned_diagnostic_lookup_followup(
                steps=steps,
                diagnostic_query=diagnostic_query,
                symbol_reason="planned_symbol_search_after_validation_inspection",
                search_reason="planned_search_after_validation_inspection",
            )
            if lookup_followup is not None:
                return lookup_followup
        return WorkflowPlannerDecision(handled=True, reason="workspace_stop_after_read", stop_after=True)

    if last_intent == "workspace.ensure_directory":
        pending_writes = _pending_workspace_writes(workspace_file_plan or {}, steps)
        if workspace_file_plan is not None and pending_writes:
            next_write = dict(pending_writes[0] or {})
            if str(next_write.get("mode") or "").strip() == "append":
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_read_before_append",
                    next_payload={"intent": "workspace.read_file", "arguments": {"path": next_write["path"], "start_line": 1, "max_lines": 400, "verbatim": True}},
                )
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_workspace_write_after_bootstrap",
                next_payload={"intent": "workspace.write_file", "arguments": {"path": next_write["path"], "content": next_write["content"]}},
                planned_batch=_planned_write_batch(pending_writes[1:]),
            )
        return WorkflowPlannerDecision(handled=True, reason="workspace_stop_after_directory_bootstrap", stop_after=True)

    if last_intent == "workspace.write_file":
        pending_writes = _pending_workspace_writes(workspace_file_plan or {}, steps)
        if workspace_file_plan is not None and pending_writes:
            next_write = dict(pending_writes[0] or {})
            if str(next_write.get("mode") or "").strip() == "append":
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_read_before_append",
                    next_payload={"intent": "workspace.read_file", "arguments": {"path": next_write["path"], "start_line": 1, "max_lines": 400, "verbatim": True}},
                )
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_workspace_next_write",
                next_payload={"intent": "workspace.write_file", "arguments": {"path": next_write["path"], "content": next_write["content"]}},
                planned_batch=_planned_write_batch(pending_writes[1:]),
            )
        list_path = str((workspace_file_plan or {}).get("list_path") or "").strip()
        if list_path and not _workflow_step_exists(steps, "workspace.list_files", key="path", value=list_path):
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_workspace_list_after_write",
                next_payload={"intent": "workspace.list_files", "arguments": {"path": list_path, "limit": 200}},
            )
        if explicit_command and not (
            _workflow_step_exists(steps, "sandbox.run_command", key="command", value=explicit_command)
            or _workflow_validation_command_already_attempted(steps, explicit_command)
        ):
            command_payload = _planned_command_payload(user_text=user_text, command=explicit_command)
            if command_payload is not None:
                reason, next_payload = command_payload
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_command_after_write" if reason == "planned_command_run" else "planned_validation_after_write",
                    next_payload=next_payload,
                )
        return WorkflowPlannerDecision(handled=True, reason="workspace_stop_after_write", stop_after=True)

    if last_intent == "workspace.list_files":
        return WorkflowPlannerDecision(handled=True, reason="workspace_stop_after_list", stop_after=True)

    if last_intent == "machine.list_directory":
        return WorkflowPlannerDecision(handled=True, reason="machine_stop_after_list", stop_after=True)

    if last_intent == "machine.inspect_specs":
        return WorkflowPlannerDecision(handled=True, reason="machine_stop_after_specs", stop_after=True)

    if last_intent in {"machine.find_folder", "machine.find_largest", "machine.disk_usage", "machine.event_log_errors", "machine.list_processes", "machine.display_inspect"}:
        # Deterministic read-only diagnostics answer in one step; never chain a web
        # search (or anything else) after them.
        return WorkflowPlannerDecision(handled=True, reason="machine_stop_after_diagnostics", stop_after=True)

    if last_intent in {"workspace.git_status", "workspace.git_diff", "workspace.git_summary"}:
        return WorkflowPlannerDecision(handled=True, reason="workspace_stop_after_git_inspection", stop_after=True)

    if last_intent == "workspace.replace_in_file":
        retry_command = _last_command_from_steps(steps) or explicit_command
        if retry_command and not _workflow_retry_already_happened(steps, retry_command):
            command_payload = _planned_command_payload(user_text=user_text, command=retry_command)
            if command_payload is not None:
                _, next_payload = command_payload
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_retry_after_edit",
                    next_payload=next_payload,
                )
        return WorkflowPlannerDecision(handled=True, reason="workspace_stop_after_edit", stop_after=True)

    if last_intent == "orchestration.execute_envelope":
        _, envelope_validation_hints, rollback = _failed_validation_from_orchestration_step(last_step)
        if envelope_validation_hints and bool(rollback.get("ok", False)):
            return _planned_validation_failure_followup(
                steps=steps,
                hints=envelope_validation_hints,
                inspect_reason="planned_inspect_after_envelope_failure",
                symbol_reason="planned_symbol_search_after_envelope_failure",
                search_reason="planned_search_after_envelope_failure",
                stop_reason="orchestration_stop_after_failed_envelope",
            )
        return WorkflowPlannerDecision(handled=True, reason="orchestration_stop_after_envelope", stop_after=True)

    if last_intent == "sandbox.run_command":
        returncode = int(hints.get("returncode") or 0)
        if returncode == 0:
            return WorkflowPlannerDecision(handled=True, reason="command_stop_after_success", stop_after=True)
        error_path = str(hints.get("error_path") or "").strip()
        error_line = int(hints.get("error_line") or 0)
        if error_path and not _workflow_step_exists(steps, "workspace.read_file", key="path", value=error_path):
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_inspect_after_command_failure",
                next_payload={
                    "intent": "workspace.read_file",
                    "arguments": {
                        "path": error_path,
                        "start_line": max(1, error_line - 8) if error_line else 1,
                        "max_lines": 60,
                    },
                },
            )
        if replacement is not None:
            target_path = str(replacement.get("path") or "").strip()
            if target_path and not _workflow_step_exists(steps, "workspace.read_file", key="path", value=target_path):
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_explicit_inspect_after_command_failure",
                    next_payload={"intent": "workspace.read_file", "arguments": {"path": target_path, "start_line": 1, "max_lines": 120}},
                )
        diagnostic_query = str(hints.get("diagnostic_query") or "").strip()
        if diagnostic_query:
            lookup_followup = _planned_diagnostic_lookup_followup(
                steps=steps,
                diagnostic_query=diagnostic_query,
                symbol_reason="planned_symbol_search_after_command_failure",
                search_reason="planned_search_after_command_failure",
            )
            if lookup_followup is not None:
                return lookup_followup
        return WorkflowPlannerDecision(handled=True, reason="command_stop_after_failure", stop_after=True)

    if last_intent in {"workspace.run_tests", "workspace.run_lint", "workspace.run_formatter"}:
        returncode = int(hints.get("returncode") or 0)
        if returncode == 0:
            return WorkflowPlannerDecision(handled=True, reason="validation_stop_after_success", stop_after=True)
        return _planned_validation_failure_followup(
            steps=steps,
            hints=hints,
            inspect_reason="planned_inspect_after_validation_failure",
            symbol_reason="planned_symbol_search_after_validation_failure",
            search_reason="planned_search_after_validation_failure",
            stop_reason="validation_stop_after_failure",
        )

    return WorkflowPlannerDecision(handled=False, reason="no_followup_plan")
