from __future__ import annotations

import re
from typing import Any

from core.plain_task_routing import plain_task_kind
from core.reasoning_engine import explicit_planner_style_requested
from core.runtime_execution_tools import looks_like_advice_only_execution_prompt, looks_like_execution_request
from core.task_router import (
    build_task_envelope_for_request,
    chat_surface_execution_task_class,
    looks_like_explicit_lookup_request,
    looks_like_public_entity_lookup_request,
    model_execution_profile,
)


def model_routing_profile(
    agent: Any,
    *,
    user_input: str,
    classification: dict[str, Any],
    interpretation: Any,
    source_context: dict[str, object] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    routed = dict(classification or {})
    is_chat_surface = agent._is_chat_truth_surface(source_context)
    planner_style_requested = bool(is_chat_surface and explicit_planner_style_requested(user_input))
    if is_chat_surface:
        routed["task_class"] = chat_surface_execution_task_class(
            str(classification.get("task_class") or "unknown"),
            user_input=user_input,
            context=getattr(interpretation, "as_context", lambda: {})(),
        )
        routed["routing_origin_task_class"] = str(classification.get("task_class") or "unknown")
        routed["planner_style_requested"] = planner_style_requested
    profile = model_execution_profile(
        str(routed.get("task_class") or "unknown"),
        chat_surface=is_chat_surface,
        planner_style_requested=planner_style_requested,
    )
    plain_kind = (
        plain_task_kind(user_input)
        if is_chat_surface
        and not bool((source_context or {}).get("canonical_grounding_required"))
        else ""
    )
    if plain_kind:
        profile["plain_task_kind"] = plain_kind
        profile["plain_task_minimal"] = True
    envelope = build_task_envelope_for_request(
        user_input,
        context={
            **getattr(interpretation, "as_context", lambda: {})(),
            **routed,
            "share_scope": str((source_context or {}).get("share_scope") or "local_only"),
        },
        task_id=str((source_context or {}).get("task_id") or ""),
        parent_task_id=str((source_context or {}).get("parent_task_id") or ""),
        chat_surface=is_chat_surface,
        planner_style_requested=planner_style_requested,
    )
    routed["task_role"] = envelope.role
    profile["task_envelope"] = envelope.to_dict()
    profile["task_role"] = envelope.role
    return routed, profile


# "Which file defines handle_inspect_processes" supplies the NOUN the gate below wants (`file`) and
# none of its VERBS (find/inspect/trace/locate/search/read/open), so the gate said no, the tool loop
# was skipped, and an 8b model answered a question about the user's own repo from memory --
# `workspace/process_inspector.py`, a path that exists nowhere. Measured 2026-07-31: the same
# question prefixed with "inspect the code and" was answered correctly, with no model call at all.
# One word of phrasing decided whether the runtime looked at the disk.
#
# This is the recurring defect in this codebase -- a verb allowlist deciding a whole turn -- and the
# established fix is to decide by what the sentence ASKS FOR. Asking WHERE A SYMBOL LIVES is itself a
# request to go and look; it cannot be answered without reading the repo. So the frame is matched,
# not the vocabulary: an interrogative head, a code-container noun, and a definition/containment
# relation to a named symbol.
#
# Deliberately NOT matched (each verified in the test file):
#   * "what defines a good API" / "which file should I read first" -- abstract or determiner slot,
#     no symbol to locate.
#   * "where is Paris" / "locate Ulaanbaatar" -- no code-container noun.
#   * "how should I position my B2B analytics product" -- advice, not a lookup.
# Returning True here does not answer anything: it declines to SKIP the tool loop, so the model is
# offered the tool catalogue and the loop's own containment still applies.
_CODE_CONTAINER = r"(?:file|files|module|modules|script|scripts|source\s+file|package|class|method|function)"
_DEFINITION_REL = r"(?:defines?|defined|declares?|declared|implements?|implemented|holds?|contains?|has|lives?(?:\s+in)?|comes?\s+from)"
_SYMBOL_SLOT = r"[A-Za-z_][A-Za-z0-9_]*"

# The symbol is CAPTURED per alternative, never inferred by position: in "which file defines X" the
# symbol is last, but in "where is X defined" the last token is the relation. Guessing by position
# silently read "defined" as the symbol and dropped the turn back into the chat lane.
_CODE_LOCATION_QUESTION_RE = re.compile(
    r"(?:"
    # "which/what file defines X", "what module contains the class X"
    rf"\b(?:which|what)\s+{_CODE_CONTAINER}\b[^?]{{0,40}}?\b{_DEFINITION_REL}\s+(?:the\s+)?"
    rf"(?:{_CODE_CONTAINER}\s+)?`?(?P<sym_which>{_SYMBOL_SLOT})`?"
    # "in which file is X defined", "what file is X in"
    rf"|\b(?:in\s+)?(?:which|what)\s+{_CODE_CONTAINER}\s+(?:is|are)\s+`?(?P<sym_infile>{_SYMBOL_SLOT})`?"
    rf"\s+(?:{_DEFINITION_REL}|in)\b"
    # "where is X defined", "where does X live", "where does X come from"
    rf"|\bwhere\s+(?:is|are|does|do)\s+(?:the\s+)?(?:{_CODE_CONTAINER}\s+)?`?(?P<sym_where>{_SYMBOL_SLOT})`?"
    rf"\s+{_DEFINITION_REL}\b"
    # "point me at / show me the file that defines X"
    rf"|\b(?:point\s+me\s+at|show\s+me)\s+(?:the\s+)?{_CODE_CONTAINER}\s+(?:that\s+)?{_DEFINITION_REL}"
    rf"\s+`?(?P<sym_point>{_SYMBOL_SLOT})`?"
    r")",
    re.IGNORECASE,
)

# A symbol slot filled by an ordinary English word is a question about a concept, not a lookup of a
# name that exists in the tree. "what defines a good API" must not send anyone to grep.
_NOT_A_SYMBOL = frozenset({
    "a", "an", "the", "this", "that", "these", "those", "it", "them", "us", "me", "my", "our",
    "good", "bad", "best", "first", "next", "last", "any", "some", "all", "each", "every",
    "success", "failure", "quality", "value", "truth", "beauty", "love", "life", "art",
})


# Two different jobs, deliberately two lists. `_CODE_CONTAINER` is the GRAMMATICAL SLOT -- the noun in
# "which FILE defines X" -- so it holds only things a definition can live in. This one asks a looser
# question: does the sentence mention code AT ALL? "in the repo", "in this codebase" and "in the
# workspace" all settle the ambiguity of "where does X live" without ever filling that slot.
_CODE_CONTEXT_NOUN_RE = re.compile(
    rf"\b(?:{_CODE_CONTAINER}|repo|repository|codebase|code\s*base|workspace|project|directory|dir|source|codes?)\b",
    re.IGNORECASE,
)
# An identifier a person would not write in prose: snake_case or an internal capital.
_LOOKS_LIKE_IDENTIFIER_RE = re.compile(r"^(?:[A-Za-z]+_[A-Za-z0-9_]*|[a-z]+[A-Z]\w*|[A-Z][a-z]+[A-Z]\w*)$")


def _asks_where_code_lives(text: str) -> bool:
    sentence = text or ""
    match = _CODE_LOCATION_QUESTION_RE.search(sentence)
    if match is None:
        return False
    symbol = next(
        (value for value in match.groupdict().values() if value),
        "",
    )
    if not symbol:
        return False
    if symbol.lower() in _NOT_A_SYMBOL:
        return False
    # "where does X live" is the one frame that reads naturally about people and places as well as
    # code ("where does my sister live"). When the sentence names no code container, only an
    # identifier-SHAPED symbol makes it a code question. With a container noun present
    # ("where does chunkify live in the repo") the frame is unambiguous and the shape does not matter.
    if _CODE_CONTEXT_NOUN_RE.search(sentence):
        return True
    return bool(_LOOKS_LIKE_IDENTIFIER_RE.match(symbol))


def explicit_runtime_workflow_request(*, user_input: str, task_class: str) -> bool:
    text = " ".join(str(user_input or "").split()).strip()
    if not text:
        return False
    lowered = f" {text.lower()} "
    if looks_like_execution_request(text, task_class="unknown"):
        return True
    if any(
        marker in lowered
        for marker in (
            " what branch and commit ",
            " current branch ",
            " head commit ",
            " recent commits ",
            " git summary ",
            " git activity ",
            " git status ",
            " working tree ",
            " how many branches ",
            " how many commits ",
            " branch count ",
            " commit count ",
            " commits today ",
            " commits yesterday ",
        )
    ):
        return True
    if re.search(r"\b(?:last|recent)\s+\d+\s+commits?\b", lowered):
        return True
    if any(marker in lowered for marker in (" retry ", " rerun ", " rerun it ", " run tests ", " inspect logs ")):
        return True
    if _asks_where_code_lives(text):
        return True
    if any(marker in lowered for marker in (" find ", " inspect ", " trace ", " locate ", " search ", " read ", " open ")) and any(
        marker in lowered
        for marker in (
            " repo ",
            " repository ",
            " workspace ",
            " code ",
            " file ",
            " files ",
            " folder ",
            " folders ",
            " directory ",
            " wiring ",
            " path ",
            " line ",
            " lines ",
            " function ",
            " symbol ",
            " import ",
        )
    ):
        return True
    if ("http://" in lowered or "https://" in lowered) and any(marker in lowered for marker in (" open ", " fetch ", " browse ", " render ")):
        return True
    return bool(
        str(task_class or "").strip().lower() == "integration_orchestration"
        and any(
            marker in lowered
            for marker in (" write the files ", " edit the files ", " patch the files ", " create the files ", " generate the files ")
        )
    )


# Classes that describe INSPECTING something. Harmless as chat when the user is asking in general,
# but an action the moment they point at a real path.
_PATH_MAKES_IT_AN_ACTION = frozenset({"file_inspection", "shell_guidance", "config", "debugging"})

# A place, not a topic: rooted at `/`, `~/`, `./` or `../`. Anchored on a leading boundary so an
# ordinary "read/write" or "24/7" -- a slash between two words, rooted nowhere -- is never a path.
_CONCRETE_PATH_RE = re.compile(r"""(?:^|[\s('"`])(?:~|\.{1,2})?/[^\s'"`,;)]{2,}""")


def _names_a_concrete_path(user_input: str) -> bool:
    return _CONCRETE_PATH_RE.search(str(user_input or "")) is not None


# Asking to SEE what is at a place. Measured: every path-read phrasing drove to task_class
# "chat_conversation", never "file_inspection", so a class-based test alone never fired and the turn
# kept the tools-less chat lane -- the model then answered "I can't directly peek into files or
# folders on your system", which is false. Path + one of these verbs is an action, whatever the class.
_READ_A_PLACE_RE = re.compile(
    r"\b(?:peek|look|glance)\s+(?:in|into|inside|at)\b"
    r"|\b(?:list|show|display|print|name|enumerate|inventory)\b"
    r"|\b(?:read|open|inspect|examine|check|explore|browse|scan)\b"
    r"|\bwhat(?:'?s| is| are)?\s+(?:in|inside|under|stored|sitting|there)\b"
    r"|\bcontents?\s+of\b"
    r"|\bwalk\s+me\s+through\b"
    r"|\bfiles?\s+(?:in|inside|under|at|for)\b"
    # "which files are sitting in <path>" leads with the noun, not a verb.
    r"|\bwhich\s+(?:files?|folders?|things?)\b"
    r"|\b(?:files?|folders?|entries)\s+(?:are|is)\s+(?:in|inside|under|sitting|stored)\b",
    re.IGNORECASE,
)


def _asks_to_read_a_place(user_input: str) -> bool:
    return _READ_A_PLACE_RE.search(str(user_input or "")) is not None


def _email_work_owns_follow_up(user_input: str, source_context: dict[str, object] | None) -> bool:
    try:
        from core.email_work_state import active_email_work_intents
        from core.retrieval_constraints import analyze_retrieval_constraints

        if analyze_retrieval_constraints(str(user_input or "")).forbids_all_tools:
            return False
        return bool(active_email_work_intents(dict(source_context or {})))
    except Exception:
        return False


def should_keep_ai_first_chat_lane(
    agent: Any,
    *,
    user_input: str,
    classification: dict[str, Any],
    interpretation: Any,
    source_context: dict[str, object] | None,
    checkpoint_state: dict[str, Any] | None,
) -> bool:
    if not agent._is_chat_truth_surface(source_context):
        return False
    if bool((source_context or {}).get("live_info_partial_unresolved")):
        # Set by `prepare_live_info_request` when a price-shaped request names something the
        # deterministic alias table cannot resolve. That decline must reach a real tool, not the
        # tools-less AI-first chat lane this function otherwise keeps a plain "unknown"/
        # "chat_conversation" turn in -- staying in that lane here is the exact dead end that
        # rendered an unexecuted tool-call as the final answer on 2026-08-03.
        return False
    # A bound-material turn that also asks a host-owned display fact ("Describe the attached
    # video; what is my screen resolution?") must leave this tools-less lane: the lane cannot
    # produce the machine observation, and staying here is the dead end where the local-fact
    # backstop then refused BOTH obligations with zero model calls (measured 2026-09-03, route
    # local_fact_no_tool). The same attachment-aware display authority the machine fast path
    # and the workflow planner consume decides; pure material questions are unaffected.
    from core.execution.constants import bound_chat_attachment_present, machine_display_intent

    if bound_chat_attachment_present(source_context) and machine_display_intent(
        str(user_input or ""), source_context=dict(source_context or {})
    ) is not None:
        return False
    checkpoint_state = dict(checkpoint_state or {})
    if checkpoint_state.get("executed_steps") or checkpoint_state.get("pending_tool_payload") or checkpoint_state.get(
        "last_tool_payload"
    ):
        return False
    if agent._looks_like_explicit_resume_request(user_input):
        return False
    # A code task open in this session (core.code_assistant journal) owns the follow-up turns:
    # "reproduce it" / "approve" are steps of that task, not conversation. Read from the task
    # journal, never from the wording, so a restarted daemon makes the same call.
    try:
        from core.code_assistant.task_runtime import active_task_control_intents

        if active_task_control_intents(source_context):
            return False
    except Exception:
        pass
    # An open RepoOps session in this chat (core.repoops journal) owns its follow-up turns by
    # the same law: "inspect it" / "bind it" / "open the draft pull request" are steps of that
    # repository workflow, not conversation. Same journal-read, same restart determinism, same
    # ownership check the code-task lane defined; bounded to the session's own sessions.
    try:
        from core.repoops.plane import active_repo_session_intents

        if active_repo_session_intents(source_context):
            return False
    except Exception:
        pass
    task_class = str(classification.get("task_class") or "unknown")
    routed_task_class = chat_surface_execution_task_class(
        task_class,
        user_input=user_input,
        context=getattr(interpretation, "as_context", lambda: {})(),
    )
    if explicit_runtime_workflow_request(
        user_input=user_input,
        task_class=task_class,
    ):
        return False
    from core.stipulated_frame import stipulated_frame_active

    if stipulated_frame_active(user_input, source_context=source_context):
        return True
    if agent._live_info_mode(user_input, interpretation=interpretation):
        return True
    lowered_input = " ".join(str(user_input or "").split()).strip().lower()
    if agent._looks_like_hive_topic_drafting_request(lowered_input):
        return True
    if looks_like_public_entity_lookup_request(lowered_input) or looks_like_explicit_lookup_request(lowered_input):
        return False
    if any(marker in lowered_input for marker in ("create task", "create new task", "new task for", "add task", "add to hive", "add to the hive")):
        return False
    if "create" in lowered_input and "task" in lowered_input and ("hive" in lowered_input or "topic" in lowered_input):
        return False
    if agent._looks_like_builder_request(user_input.lower()):
        return True
    # A request that names a concrete path on this machine is an ACTION, not a conversation about
    # one. `file_inspection` and `shell_guidance` sit in the chat-lane list below, so "give me an
    # inventory of everything stored in /Users/me/Desktop/tide-charts" was routed to a tools-less
    # plain-text lane and the model replied "I'll check the contents ... Let me run the scan" while
    # nothing ran. Advice questions ("how do I list a directory in Python?") name no path and are
    # unaffected; naming a path is the discriminator.
    # A typed path is the signal; the verb is noise. This was `path AND (class OR read-verb)`, and the
    # verb list was an allowlist that missed "summarize <path>", "tell me about <path>", "anything
    # interesting in <path>" and "is there a README under <path>" -- all of which then kept the
    # tools-less chat lane and were answered with "I can't directly peek into files or folders on your
    # system". Naming a concrete local path is by itself unambiguous evidence of a file action, so the
    # test is inverted: the path decides, and only an explicitly advice-only request opts back out.
    if _names_a_concrete_path(user_input) and not looks_like_advice_only_execution_prompt(user_input):
        return False
    # A `chat_research` turn that ASKS FOR EVIDENCE must leave this lane, because it cannot
    # retrieve here. Measured 2026-08-06, session openclaw:86ae9a9b420d418c7254: the aviation
    # prompt ("use authoritative sources and do not guess") classified `research`, routed to
    # `chat_research`, was kept here, and its 22-event trace carried no search, no fetch and no
    # tool call. The model then fabricated the tool history it never had -- "The grounding search
    # returned irrelevant sources (Apple/Android developer docs, GitHub)" -- which is where the
    # "contaminated retrieval" reports came from: nothing was retrieved and nothing leaked.
    #
    # This is the third link of a chain that each independently blocked retrieval:
    # `model_execution_profile` stripped the `summarize` capability, `should_attempt_tool_intent`
    # refused the gate, and this kept the turn out of the loop even once both said yes.
    #
    # The class alone is the WRONG discriminator, which the suite caught: "tell me about stoicism"
    # is also `chat_research`, and it belongs here -- stable knowledge answered as conversation,
    # with no browsing and no tool-catalog latency. What separates the two is whether the request
    # demands evidence, which `answer_mode_for` already decides and tests: DIRECT for stoicism,
    # GROUNDED for "use authoritative sources", "do not guess", exact statistics or a per-country
    # breakdown. Deferred import -- this module is imported early enough that a top-level one
    # circles back.
    # Applied to EVERY class, not just `chat_research`. Restricting it to that one class assumed
    # the classifier labels these turns correctly, and it does not: measured 2026-08-06, the EV
    # brief classified as `config` (it asks for "the most common battery-capacity CONFIGURATION")
    # and routed to `business_advisory` -- also in the keep-set below -- so it stayed in the
    # tools-less lane despite explicitly demanding current evidence.
    #
    # The request's own contract is the reliable signal; the label is not. `answer_mode_for` is the
    # same discriminator the tool gate and the curiosity gate use.
    from core.execution_requirements import requirements_for

    if requirements_for(
        user_input, task_class=task_class, source_context=source_context
    ).forbids_toolless_lane():
        return False

    # A session in the middle of email work (core.email_work_state: an email tool it executed
    # recently, or a draft it owns that is not finished -- read from the execution ledger and the
    # draft store, never from the wording) owns its follow-ups, the way an open code task does
    # above. Measured 2026-09-14 on a served daemon under shipped routing: after turn 1 executed
    # email.read, "Open that delivery thread and show me what they wrote." classified `unknown` and
    # was kept HERE, so the model answered without a tool although the planner gate admitted the
    # wording. Placed after every specific lane decision above, so only the class-based keep below
    # is overridden -- and never for a turn that forbids tools.
    if _email_work_owns_follow_up(user_input, source_context):
        return False

    return routed_task_class in {
        "chat_conversation",
        "chat_research",
        "general_advisory",
        "business_advisory",
        "food_nutrition",
        "relationship_advisory",
        "creative_ideation",
        "debugging",
        "dependency_resolution",
        "config",
        "system_design",
        "file_inspection",
        "shell_guidance",
        "integration_orchestration",
    }
