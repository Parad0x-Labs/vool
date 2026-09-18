from __future__ import annotations

import re
from typing import Any

from core import policy_engine
from core.context_history_authority import (
    AUTHORITATIVE_CORRECTIONS_PREFIX,
    CLOSED_EXCHANGE_NOTICE,
    ContextHistorySelection,
    assistant_text_is_runtime_failure_notice,
    closed_exchange_marker,
    enforce_history_budget,
    failed_exchange_marker,
    is_current_user_message,
    most_recent_completed_exchange,
    select_history_policy,
)
from core.context_namespace import load_chat_namespace
from core.context_retrieval import resolve_semantic_access_policy
from core.context_scope import ContextAccessPolicy, current_turn_corrections
from core.conversation_summarizer import KEEP_RECENT, SUMMARY_THRESHOLD, compress_if_needed
from core.persistent_memory import describe_session_memory_policy, load_memory_excerpt
from core.prompt_assembly_report import ContextItem
from core.runtime_paths import project_path
from core.secret_redaction import contains_secret
from core.user_preferences import load_preferences
from storage.dialogue_memory import get_dialogue_session, recent_dialogue_turns, session_lexicon

_PREFERENCE_DECLARATION_RE = re.compile(
    r"\b(?:i|we)\s+prefer\s+(?P<value>[^\n.!?]{1,160})",
    re.IGNORECASE,
)
_PREFERENCE_RECALL_RE = re.compile(
    r"\b(?:preference|prefer|settled|choice|choose|format)\b",
    re.IGNORECASE,
)


def _web0_null_facts_for(signal: str) -> str:
    """Compatibility seam backed by canonical repository retrieval."""
    from core.web0_project_grounding import web0_boot_context

    return web0_boot_context(signal)


def _compact_join(items: list[str], *, limit: int) -> str:
    picked = [item.strip() for item in items if item and item.strip()][:limit]
    return ", ".join(picked)



def profile_context_lines_for_session(session_id: str = "", *, principal: str = "") -> list[str]:
    """Prompt lines from the Operator Profile for one chat (the turn in flight by default).

    Reads through the ONE profile authority, so every A8 WITHHOLD/ERASE verdict and every scope
    rule applies here exactly as it does on the operator's own listing."""
    try:
        from core.operator_profile import OWNER_PRINCIPAL, hydration_for_turn
        from core.operator_profile_turn import current_turn_scope

        bound_principal, bound_session, bound_project = current_turn_scope()
        who = str(principal or bound_principal or OWNER_PRINCIPAL)
        session = str(session_id or bound_session or "")
        lines, _used = hydration_for_turn(who, session_id=session, project_id=bound_project, include_name=False)
        return list(lines)
    except Exception:
        return []


def _continuity_lines(session_state: dict[str, Any]) -> list[str]:
    # Two identity anchors, always paired and always labelled: the USER's saved settings name and
    # the ASSISTANT's own product name. Paired, because stating only one is how the runtime came to
    # answer "yo say my name!" with its own name. The user anchor also stops an unknown name being
    # filled from stray context — e.g. the SUBJECT of an image prompt ("Rick and Morty ...") that
    # bled into memory. The assistant anchor overrides stale pre-rename memory ("**My name**: VOOL"
    # in MEMORY.md, saved summaries, dense-profile entries) that the model otherwise repeats as
    # ground truth; the stored rows are left untouched (they are the user's data, no migration).
    # Only these two fields — no other settings value belongs in an ordinary turn.
    from core.user_identity_authority import identity_context_lines

    lines: list[str] = list(identity_context_lines())
    # Operator Profile (P1): the operator's OTHER stable preferences (language, timezone,
    # response/format preferences) for the turn in flight -- A8-gated at the source, scope-
    # resolved for this chat. The name is already stated by the identity lines above, so it is
    # excluded here; with no items this adds nothing and an ordinary turn is byte-identical.
    lines.extend(profile_context_lines_for_session())
    current_user_goal = str(session_state.get("current_user_goal") or "").strip()
    assistant_commitments = [str(item).strip() for item in list(session_state.get("assistant_commitments") or []) if str(item or "").strip()]
    unresolved_followups = [str(item).strip() for item in list(session_state.get("unresolved_followups") or []) if str(item or "").strip()]
    user_stance = str(session_state.get("user_stance") or "").strip()
    emotional_tone = str(session_state.get("emotional_tone") or "").strip()

    if current_user_goal:
        lines.append(f"Current user goal: {current_user_goal}.")
    if session_state.get("last_intent_mode"):
        lines.append(f"User intent mode: {session_state.get('last_intent_mode')}.")
    if user_stance or emotional_tone:
        lines.append(
            "Continuity tone: "
            f"stance={user_stance or 'none'}, emotion={emotional_tone or 'none'}."
        )
    if assistant_commitments:
        lines.append(f"Assistant commitments: {_compact_join(assistant_commitments, limit=3)}.")
    if unresolved_followups:
        lines.append(f"Unresolved followups: {_compact_join(unresolved_followups, limit=3)}.")
    return lines


def _conversation_preference_text(current_user_text: str = "") -> str:
    try:
        prefs = load_preferences()
    except Exception:
        return ""
    fragments = [
        f"humor={prefs.humor_percent}/100",
        f"boundaries={prefs.boundaries_mode}",
        f"profanity={prefs.profanity_level}/100",
    ]
    if getattr(prefs, "character_mode", ""):
        fragments.append(f"character_mode={prefs.character_mode}")
    from core.raw_output_contract import parse_raw_output_contract
    from core.response_constraints import parse_response_constraint

    # Opaque saved style is a default, not an override of this turn's output contract.
    explicit_output = (
        parse_response_constraint(current_user_text) is not None
        or parse_raw_output_contract(current_user_text) is not None
    )
    if getattr(prefs, "style_notes", "") and not explicit_output:
        fragments.append(f"style_notes={prefs.style_notes}")
    return "; ".join(fragment for fragment in fragments if str(fragment or "").strip())


def _execution_preference_text() -> str:
    try:
        prefs = load_preferences()
    except Exception:
        return ""
    fragments = [
        f"autonomy={prefs.autonomy_mode}",
        f"show_workflow={'on' if prefs.show_workflow else 'off'}",
        f"hive_followups={'on' if prefs.hive_followups else 'off'}",
        f"idle_research_assist={'on' if prefs.idle_research_assist else 'off'}",
        f"accept_hive_tasks={'on' if prefs.accept_hive_tasks else 'off'}",
        f"social_commons={'on' if prefs.social_commons else 'off'}",
    ]
    return "; ".join(fragment for fragment in fragments if str(fragment or "").strip())


def _read_markdown_context(*parts: str, max_chars: int = 2200) -> str:
    path = project_path(*parts)
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    if not text:
        return ""
    if len(text) > max_chars:
        return text[:max_chars] + "\n[...]"
    return text


def _normalized_dialogue_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _trim_history_window(
    items: list[dict[str, str]],
    *,
    max_messages: int,
    max_chars: int,
) -> list[dict[str, str]]:
    return enforce_history_budget(
        items,
        max_messages=max_messages,
        max_chars=max_chars,
    )


def _compress_history(
    items: list[dict[str, str]],
    *,
    max_messages: int,
    max_chars: int,
    session_id: str = "",
    project_id: str = "",
) -> list[dict[str, str]]:
    """
    Smart history: LLM-compress when long, hard-trim when short.

    For short histories (≤ SUMMARY_THRESHOLD) the existing sliding-window
    behaviour is preserved. For longer ones, compress_if_needed() replaces
    older turns with a structured <context_summary> that preserves exact
    facts verbatim — instead of silently discarding them.
    """
    if len(items) > SUMMARY_THRESHOLD:
        compressed, was_compressed = compress_if_needed(
            items,
            threshold=SUMMARY_THRESHOLD,
            keep_recent=KEEP_RECENT,
            session_id=session_id,
            project_id=project_id,
        )
        if was_compressed:
            return enforce_history_budget(
                compressed,
                max_messages=max_messages,
                max_chars=max_chars,
            )
    return _trim_history_window(items, max_messages=max_messages, max_chars=max_chars)


def _history_retention_rank(items: list[dict[str, str]]) -> tuple[bool, int]:
    has_summary = any("<context_summary>" in str(item.get("content") or "") for item in items)
    return has_summary, len(items)


def _assistant_text_unavailable(text: str, request_id: str = "") -> bool:
    """A9/RC-8: thin wrapper over persistent_memory's availability verdict.

    Living here (the transcript assembler) means every client-carried assistant item is
    re-verified at the LAST seam before prompt assembly — whatever key delivered it.
    Ungoverned legacy truth (no A8 store hosted) still passes through unchanged."""
    try:
        from core.persistent_memory import _assistant_text_unavailable as _gate

        return bool(_gate(str(text or ""), str(request_id or "")))
    except Exception:
        # A9 RC-8 pass-003 (F3): resolution FAILURE must never silently convert a
        # governed payload into a servable one. Posture mirrors persistent_memory's
        # hosted-store discrimination: an ABSENT store is ungoverned legacy truth;
        # a HOSTED-but-failing store fails CLOSED.
        try:
            from core.finalization import governance_store_ready

            return bool(governance_store_ready())
        except Exception:
            return True


def _client_conversation_history(
    source_context: dict[str, Any] | None,
    *,
    current_user_text: str,
    current_user_raw_text: str = "",
    current_turn_id: str = "",
    max_messages: int,
    max_chars: int,
    selection: ContextHistorySelection | None = None,
    session_id: str = "",
    project_id: str = "",
) -> list[dict[str, str]]:
    source_context = dict(source_context or {})
    raw_history = list(
        source_context.get("client_conversation_history")
        or source_context.get("conversation_history")
        or []
    )
    normalized: list[dict[str, str]] = []
    for item in raw_history:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        raw_content = str(item.get("content") or "")
        content = _normalized_dialogue_text(raw_content)
        if role not in {"system", "user", "assistant"} or not content:
            continue
        # A9/RC-8 pass-002: A8 eligibility binds to the payload's LINEAGE BEFORE any
        # lossy normalization. The availability verdict is keyed by the exact governed
        # bytes, so adjudicating the normalized derivative destroyed identity first and
        # let whitespace-bearing ERASED/WITHHELD payloads re-enter. The original bytes
        # decide; the normalized form is also swept fail-closed so a transform can never
        # downgrade a governed verdict (substring/hash guessing is deliberately not used).
        # A9 RC-8 pass-003 (F1): the normalized sweep is now LIVE — at governance-
        # transition time finalization binds the payload's canonical collapsed
        # representation into a8_governed_derivatives, so this derivative resolves
        # through FINALIZATION LINEAGE (live availability, fail-closed), not by
        # re-hashing guesses. Any whitespace-family transform of a governed payload
        # collapses onto that bound key and is suppressed here.
        item_request_id = str(
            item.get("request_id") or item.get("requestId") or ""
        ).strip()
        if role == "assistant" and (
            _assistant_text_unavailable(raw_content, item_request_id)
            or _assistant_text_unavailable(content, item_request_id)
        ):
            # Suppress the PAYLOAD, close the EXCHANGE. Dropping this row alone left the user's
            # request standing with nothing answering it, and the model served that request in
            # the next turn (measured: build 7fe94596, acceptance turns 6 -> 7).
            normalized.append(closed_exchange_marker())
            continue
        # A turn that ended in a runtime NO-ANSWER notice keeps its request out of the next
        # prompt the same way: the notice is reader-facing truth, but in a prompt it holds the
        # failed request open beside the current message (2026-09-15: the next, unrelated
        # question was answered as the failed wallet-review task). Fail-open for anything the
        # recognizer does not know.
        if role == "assistant" and assistant_text_is_runtime_failure_notice(raw_content):
            normalized.append(failed_exchange_marker())
            continue
        # A9/RC-8: the erasure availability gate applies to EVERY transcript item
        # regardless of which carrier key delivered it (`client_conversation_history`
        # or `conversation_history`). A WITHHELD/ERASED payload surviving only in a
        # client-held copy must not re-enter prompts and decay erasure downstream.
        turn_id = str(
            item.get("turn_id")
            or item.get("turnId")
            or item.get("message_id")
            or item.get("id")
            or ""
        ).strip()
        normalized.append(
            {
                "role": role,
                "content": content,
                **({"turn_id": turn_id} if turn_id else {}),
            }
        )
    current_user = _normalized_dialogue_text(current_user_text)
    current_user_raw = _normalized_dialogue_text(current_user_raw_text)
    if normalized and is_current_user_message(
        normalized[-1],
        current_user_text=current_user,
        current_user_raw_text=current_user_raw,
        current_turn_id=current_turn_id,
    ):
        normalized = normalized[:-1]
    if selection is not None and not selection.expands_beyond_adjacency:
        return _trim_history_window(
            most_recent_completed_exchange(normalized),
            max_messages=selection.max_messages,
            max_chars=selection.max_chars,
        )
    return _compress_history(
        normalized,
        max_messages=max_messages,
        max_chars=max_chars,
        session_id=session_id,
        project_id=project_id,
    )



def _log_transcript_assembled(source: str, transcript: list[dict[str, str]], session_id: str | None) -> list[dict[str, str]]:
    """Record what the model will actually receive as prior dialogue.

    Every "the assistant ignores what I just said" report in this project has cost hours because the
    transcript was assembled silently: nothing anywhere said how many turns reached the prompt or
    which store they came from. One line makes the difference between reading a trace and guessing.
    """
    try:
        import logging

        logging.getLogger("vool.api").info(
            "transcript assembled: session=%s source=%s messages=%d",
            session_id or "<none>", source, len(transcript or []),
        )
    except Exception:
        pass
    return transcript


def canonical_runtime_transcript(
    *,
    session_id: str | None,
    source_context: dict[str, Any] | None,
    current_user_text: str,
    current_user_raw_text: str = "",
    current_turn_id: str = "",
    access_policy: ContextAccessPolicy | None = None,
    expansion_hint: bool | None = None,
    max_messages: int = 10,
    max_chars: int = 5000,
) -> tuple[list[dict[str, str]], str]:
    requested_session_id = str(session_id or "").strip()
    persisted_namespace = (
        load_chat_namespace(requested_session_id)
        if requested_session_id
        else None
    )
    if (
        persisted_namespace is not None
        and persisted_namespace.lifecycle_state != "active"
    ):
        return _log_transcript_assembled(
            "scope_denied",
            [],
            requested_session_id,
        ), "scope_denied"
    try:
        policy = resolve_semantic_access_policy(
            session_id=session_id,
            access_policy=access_policy,
            source_context=source_context,
        )
    except (TypeError, ValueError):
        # Request-supplied transcript is data, not authority. Real API/native ingress creates or
        # resolves the canonical namespace before prompt assembly; every lower-level unregistered
        # path fails closed so absence of a namespace can never become an implicit grant.
        return _log_transcript_assembled(
            "scope_denied",
            [],
            requested_session_id,
        ), "scope_denied"
    normalized_session_id = policy.chat_id
    project_id = policy.project_id
    selection = select_history_policy(
        scope_allows_transcript=policy.namespace_state == "active",
        expansion_hint=expansion_hint,
        authority_reason=f"chat_namespace_{policy.namespace_state}",
        requested_max_messages=max_messages,
        requested_max_chars=max_chars,
    )
    if not selection.transcript_allowed:
        return _log_transcript_assembled("scope_denied", [], normalized_session_id), "scope_denied"
    client_history = _client_conversation_history(
        source_context,
        current_user_text=current_user_text,
        current_user_raw_text=current_user_raw_text,
        current_turn_id=current_turn_id,
        max_messages=selection.max_messages,
        max_chars=selection.max_chars,
        selection=selection,
        session_id=normalized_session_id,
        project_id=project_id,
    )
    if normalized_session_id:
        turns = recent_dialogue_turns(
            normalized_session_id,
            limit=max(12, int(max_messages) * 2, SUMMARY_THRESHOLD + KEEP_RECENT + 1),
            speaker_roles=("user", "assistant"),
        )
        transcript: list[dict[str, str]] = []
        for turn in reversed(turns):
            if current_turn_id and str(turn.get("turn_id") or "").strip() == current_turn_id:
                continue
            role = str(turn.get("speaker_role") or "").strip().lower()
            # A9: prefer the verbatim bytes the user actually sent; a normalized paraphrase may
            # rewrite literal identifiers/URLs/quoted text, and an LLM-regenerated
            # reconstruction must stay last-resort, never preferred over real input.
            raw_stored = str(
                turn.get("raw_input")
                or turn.get("normalized_input")
                or turn.get("reconstructed_input")
                or ""
            )
            content = _normalized_dialogue_text(raw_stored)
            if role not in {"user", "assistant"} or not content:
                continue
            # A9 RC-8 pass-003 (F2): persisted dialogue rows are a carrier into the
            # canonical transcript and MUST clear the same A8 availability gate as
            # every other assistant carrier. Resolution is lineage-first (the row's
            # own request_id binding), then exact bytes, then the bound canonical
            # collapsed representation.
            if role == "assistant" and _assistant_text_unavailable(
                raw_stored, str(turn.get("request_id") or "")
            ):
                transcript.append(closed_exchange_marker())
                continue
            # Same law one carrier later: a persisted assistant row that is a runtime NO-ANSWER
            # notice closes its exchange instead of holding the failed request open in the next
            # prompt (2026-09-15 incident: the next question was answered as the failed task).
            if role == "assistant" and assistant_text_is_runtime_failure_notice(raw_stored):
                transcript.append(failed_exchange_marker())
                continue
            transcript.append({"role": role, "content": content})
        if (
            transcript
            and is_current_user_message(
                transcript[-1],
                current_user_text=current_user_text,
                current_user_raw_text=current_user_raw_text,
                current_turn_id=current_turn_id,
            )
        ):
            transcript = transcript[:-1]
        if selection.expands_beyond_adjacency:
            transcript = _compress_history(
                transcript,
                max_messages=selection.max_messages,
                max_chars=selection.max_chars,
                session_id=normalized_session_id,
                project_id=project_id,
            )
        else:
            transcript = _trim_history_window(
                most_recent_completed_exchange(transcript),
                max_messages=selection.max_messages,
                max_chars=selection.max_chars,
            )
        if transcript:
            if (
                selection.expands_beyond_adjacency
                and client_history
                and _history_retention_rank(client_history) > _history_retention_rank(transcript)
            ):
                transcript = client_history
                transcript = _with_authoritative_corrections(
                    transcript,
                    current_user_text=current_user_text,
                )
                transcript = enforce_history_budget(
                    transcript,
                    max_messages=selection.max_messages,
                    max_chars=selection.max_chars,
                )
                return _log_transcript_assembled(
                    "client_conversation_history",
                    transcript,
                    normalized_session_id,
                ), "client_conversation_history"
            transcript = _with_authoritative_corrections(
                transcript,
                current_user_text=current_user_text,
            )
            transcript = enforce_history_budget(
                transcript,
                max_messages=selection.max_messages,
                max_chars=selection.max_chars,
            )
            return _log_transcript_assembled(
                "structured_dialogue_memory",
                transcript,
                normalized_session_id,
            ), "structured_dialogue_memory"
    if client_history:
        client_history = _with_authoritative_corrections(
            client_history,
            current_user_text=current_user_text,
        )
        client_history = enforce_history_budget(
            client_history,
            max_messages=selection.max_messages,
            max_chars=selection.max_chars,
        )
        return _log_transcript_assembled(
            "client_conversation_history",
            client_history,
            normalized_session_id,
        ), "client_conversation_history"
    return _log_transcript_assembled("none", [], normalized_session_id), "none"


def _with_authoritative_corrections(
    transcript: list[dict[str, str]],
    *,
    current_user_text: str,
) -> list[dict[str, str]]:
    """Build a model-facing transcript where explicit corrections supersede old facts.

    The stored transcript remains untouched.  Only stale, directly conflicting user/assistant
    pairs are withheld from the provider request, before any ranking or compression occurs.
    """
    latest: dict[str, str] = {}
    for item in [*list(transcript or []), {"role": "user", "content": current_user_text}]:
        if str(item.get("role") or "").strip().lower() != "user":
            continue
        for correction in current_turn_corrections(str(item.get("content") or "")):
            if not contains_secret(correction.value):
                latest[correction.fact_key] = correction.value
    if not latest:
        return list(transcript or [])
    effective_transcript = _without_superseded_transcript_turns(
        list(transcript or []),
        latest=latest,
    )
    if not _corrections_apply_to_current_turn(
        current_user_text,
        latest=latest,
    ):
        return effective_transcript
    facts = "; ".join(
        f"{fact_key} = {value}"
        for fact_key, value in sorted(latest.items())
    )
    return [
        {
            "role": "system",
            "content": AUTHORITATIVE_CORRECTIONS_PREFIX + facts,
        },
        *effective_transcript,
    ]


def _without_superseded_transcript_turns(
    transcript: list[dict[str, str]],
    *,
    latest: dict[str, str],
) -> list[dict[str, str]]:
    """Exclude only a stale declaration and its immediate historical response."""
    effective: list[dict[str, str]] = []
    skip_assistant_reply = False
    for item in transcript:
        role = str(item.get("role") or "").strip().lower()
        if role == "assistant" and skip_assistant_reply:
            skip_assistant_reply = False
            continue
        skip_assistant_reply = False
        if role == "user" and _transcript_turn_is_superseded(item, latest=latest):
            skip_assistant_reply = True
            continue
        effective.append(item)
    return effective


def _transcript_turn_is_superseded(
    item: dict[str, str],
    *,
    latest: dict[str, str],
) -> bool:
    content = str(item.get("content") or "")
    expected_preference = str(latest.get("preference") or "").strip()
    if expected_preference:
        match = _PREFERENCE_DECLARATION_RE.search(content)
        if match is not None:
            declared = str(match.group("value") or "").strip()
            if declared and _normalized_fact_value(declared) != _normalized_fact_value(expected_preference):
                return True
    for fact_key, expected_value in latest.items():
        if fact_key in {"preference", "marker"}:
            continue
        if fact_key and fact_key in _normalized_fact_value(content):
            if _normalized_fact_value(expected_value) not in _normalized_fact_value(content):
                return True
    return False


def _corrections_apply_to_current_turn(
    current_user_text: str,
    *,
    latest: dict[str, str],
) -> bool:
    """Keep correction authority available without making it a new global topic."""
    text = str(current_user_text or "")
    if current_turn_corrections(text):
        return True
    normalized_text = _normalized_fact_value(text)
    for fact_key in latest:
        if fact_key == "preference" and _PREFERENCE_RECALL_RE.search(text):
            return True
        if fact_key == "marker" and "marker" in normalized_text:
            return True
        if fact_key and fact_key in normalized_text:
            return True
    return False


def _normalized_fact_value(value: str) -> str:
    return " ".join(str(value or "").split()).casefold()


#: The self-knowledge document answers questions ABOUT the assistant: identity, capabilities,
#: architecture, endpoints, privacy, x402. These are the shapes of that ask; a turn matching none
#: of them cannot use the document and must not carry it.
_SELF_KNOWLEDGE_TOPIC_RE = re.compile(
    r"\b(?:what|who)\s+(?:are|r|is)\s+(?:you|u)\b"
    r"|\bwhat\s+can\s+(?:you|u)\s+do\b"
    r"|\bhow\s+do(?:es)?\s+(?:you|u|it|this(?:\s+(?:app|assistant|agent|runtime|thing|product))?)\s+work\b"
    r"|\byour?\s+(?:capabilit\w*|features?|endpoints?|versions?|architecture|memory|privacy|"
    r"maker|creator|models?|tools?|runtime|self[-\s]?knowledge|limitations?)\b"
    r"|\babout\s+(?:yourself|this\s+(?:app|assistant|runtime))\b"
    r"|\bare\s+(?:you|u)\s+an?\s+(?:ai|llm|model|agent|bot|assistant)\b"
    r"|\b(?:vool|vool)\b"
    r"|\bx402\b"
    r"|\borchestration\s+framework\b"
    r"|\bintroduce\s+(?:yourself|urself)\b",
    re.IGNORECASE,
)


#: Topics the OpenClaw tool doctrine actually governs. The doctrine is about TOOL and
#: INTERNET behavior; a turn that names none of these subjects gains nothing from it.
#: Direct subjects are unconditional; everyday nouns people also talk ABOUT (email,
#: calendar, workflow, reminders, automation) are only doctrine-relevant when an action
#: verb nearby shows the turn is asking to DO something with them -- "I got so much email
#: today" is conversation, "check my email" is a tool request.
_TOOL_DOCTRINE_TOPIC_RE = re.compile(
    r"\b(?:tool|tools|command|commands|execute|run|launch|open|browse|web|website|internet|online|"
    r"search|look\s?up|fetch|download|upload|integrat\w*|openclaw|mcp|plugin|plugins|skill|skills|"
    r"install|uninstall|file|files|folder|directory|workspace|screenshot|scrape|api|terminal|shell|"
    r"script|scripts|code\s?base|repo|repository|remind(?:s|ed|ing)?|automat\w+)\b",
    re.IGNORECASE,
)
_ACTION_VERB = (
    r"(?:send|check|read|write|set|create|make|schedule|add|open|draft|reply|forward|put|book|"
    r"plan|sync|run|start|build|list|show|get|pull|prepare|organize|manage|handle|clean|sort|"
    r"delete|remove|cancel|move|copy|share|export|import|backup|file|track|log|note|summarize|"
    r"update|review|follow)"
)
_TOOL_DOCTRINE_ACTION_RE = re.compile(
    rf"{_ACTION_VERB}(?:\s+\w+){{0,4}}\s+(?:calendar|e-?mail|workflow|reminders?|automation)\b",
    re.IGNORECASE,
)


def _turn_needs_tool_doctrine(turn_text: str) -> bool:
    """Whether this turn plausibly touches tools, files or the internet."""
    text = " ".join(str(turn_text or "").split())
    if not text:
        return False
    return bool(_TOOL_DOCTRINE_TOPIC_RE.search(text)) or bool(
        _TOOL_DOCTRINE_ACTION_RE.search(text)
    )


def _turn_is_about_the_assistant(turn_text: str) -> bool:
    """Whether this turn plausibly asks about the assistant/runtime itself."""
    text = " ".join(str(turn_text or "").split())
    if not text:
        return False
    if _SELF_KNOWLEDGE_TOPIC_RE.search(text):
        return True
    try:
        from core.task_router import looks_like_assistant_identity_question

        return looks_like_assistant_identity_question(text)
    except Exception:
        return False


def build_bootstrap_context(
    *,
    persona: Any,
    task: Any,
    classification: dict[str, Any],
    interpretation: Any,
    session_id: str,
    source_context: dict[str, Any] | None = None,
    max_lexicon_items: int = 4,
    include_private_context: bool = True,
    include_user_profile_context: bool | None = None,
    include_history_context: bool = True,
    include_mission_context: bool | None = None,
) -> list[ContextItem]:
    if include_user_profile_context is None:
        include_user_profile_context = include_private_context
    if include_mission_context is None:
        include_mission_context = include_history_context
    session_state = (
        get_dialogue_session(session_id)
        if include_private_context and include_history_context
        else {}
    )
    recent_turns = (
        recent_dialogue_turns(session_id, limit=2, speaker_roles=("user", "assistant"))
        if include_private_context and include_history_context
        else []
    )
    lexicon = session_lexicon(session_id) if include_private_context else {}
    quality_flags = list(getattr(interpretation, "quality_flags", []) or [])
    topic_hints = list(getattr(interpretation, "topic_hints", []) or [])
    references = list(getattr(interpretation, "reference_targets", []) or [])
    continuity_lines = _continuity_lines(session_state)

    active_mission_block = ""
    if include_private_context and include_mission_context:
        try:
            from core.active_mission import current_active_mission_slots, render_active_slots

            active_mission_block = render_active_slots(current_active_mission_slots(session_id))
        except Exception:
            active_mission_block = ""

    canonical_project_items: list[ContextItem] = []
    try:
        from core.canonical_project_knowledge import canonical_context_items

        _q = " ".join(str(v or "") for v in (
            getattr(interpretation, "reconstructed_text", ""),
            getattr(interpretation, "normalized_text", ""),
            getattr(task, "task_summary", ""),
        ))
        canonical_project_items = canonical_context_items(_q)
    except Exception:
        canonical_project_items = []

    # A turn that supplies its own premises needs them declared binding BEFORE the model reasons,
    # or a stipulated fact that contradicts the real world reads as a user error to correct and a
    # premise-supplied name reads as something to look up. Built from `raw_text` rather than the
    # normalized or reconstructed forms: the frame lives in the user's own punctuation and clause
    # openers, which normalization is free to move.
    hypothetical_lines: list[str] = []
    try:
        from core.hypothetical_frame import detect_hypothetical_frame, hypothetical_frame_context_lines

        _frame_text = str(
            getattr(interpretation, "raw_text", "")
            or getattr(interpretation, "reconstructed_text", "")
            or getattr(interpretation, "normalized_text", "")
            or ""
        )
        hypothetical_lines = hypothetical_frame_context_lines(detect_hypothetical_frame(_frame_text))
    except Exception:
        hypothetical_lines = []

    character_mode = ""
    try:
        if include_user_profile_context:
            _prefs = load_preferences()
            character_mode = str(getattr(_prefs, "character_mode", "") or "").strip()
    except Exception:
        pass
    persona_content = (
        f"Persona: {persona.display_name}. Tone: {persona.tone}. "
        f"Spirit anchor: {persona.spirit_anchor}."
    )
    if character_mode:
        persona_content += (
            f"\n\nACTIVE ROLEPLAY: You are currently roleplaying as '{character_mode}'. "
            f"Stay fully in character at all times. Adopt the speech patterns, mannerisms, "
            f"personality, and vocabulary of '{character_mode}'. Do not break character unless "
            f"the user explicitly asks you to stop the roleplay."
        )
    # The same controller authority that gates tools owns the model's mode guidance.
    # Persona preferences and the legacy execution default cannot override this turn.
    from core.mode_permission_policy import OperatingMode, effective_mode_state

    mode_state = effective_mode_state({**(source_context or {}), "session_id": session_id})
    action_guidance = (
        "Read and plan only; do not mutate files or execute side effects."
        if mode_state["mode"] == OperatingMode.PLAN.value
        else "Use tools for requested work; the runtime checks each action and requests approval when required."
    )
    if mode_state["mode"] == OperatingMode.PLAN.value:
        action_guidance += " The runtime enforces this limit; approval does not change the mode."
    items: list[ContextItem] = [
        ContextItem(
            item_id="bootstrap-persona",
            layer="bootstrap",
            source_type="persona",
            title="Agent identity",
            content=persona_content,
            priority=1.0,
            confidence=0.95,
            must_keep=True,
            include_reason="stable_identity",
        ),
        ContextItem(
            item_id="bootstrap-session",
            layer="bootstrap",
            source_type="session_state",
            title="Session topic hints",
            # Current-turn signals only. The stale session last_subject / session_topics are
            # deliberately NOT resurrected here: that was a cross-turn bleed (a screen question
            # inherited a prior "telegram" subject). A genuine follow-up still carries its
            # subject through `references`, which adapt_user_input resolves for real anaphora.
            content=(
                f"Current topics: {_compact_join(topic_hints, limit=4) or 'none'}. "
                f"References: {_compact_join(references, limit=3) or 'none'}."
            ),
            priority=0.95,
            confidence=float(getattr(interpretation, "understanding_confidence", 0.0) or 0.0),
            must_keep=True,
            include_reason="session_grounding",
        ),
        *(
            [
                ContextItem(
                    item_id="bootstrap-continuity",
                    layer="bootstrap",
                    source_type="dialogue_continuity",
                    title="Conversation continuity",
                    content=" ".join(continuity_lines),
                    priority=0.93,
                    confidence=0.84,
                    must_keep=True,
                    include_reason="continuity_state",
                )
            ]
            if continuity_lines
            else []
        ),
        *(
            [
                ContextItem(
                    item_id="bootstrap-hypothetical-frame",
                    layer="bootstrap",
                    source_type="turn_frame",
                    title="Stipulated premises for this turn",
                    content=" ".join(hypothetical_lines),
                    # Above the continuity item and must_keep: the budgeter trims item content from
                    # the end, and half of this instruction ("answer inside the frame" without "do
                    # not look it up") is worse than none.
                    priority=0.97,
                    confidence=0.9,
                    must_keep=True,
                    include_reason="hypothetical_frame",
                )
            ]
            if hypothetical_lines
            else []
        ),
        *(
            [
                ContextItem(
                    item_id="bootstrap-active-mission",
                    layer="bootstrap",
                    source_type="active_mission",
                    title="Active mission (exact, latest-wins)",
                    content=active_mission_block,
                    priority=0.98,
                    confidence=0.9,
                    must_keep=True,
                    include_reason="active_mission_slots",
                )
            ]
            if active_mission_block
            else []
        ),
        *canonical_project_items,
        ContextItem(
            item_id="bootstrap-task",
            layer="bootstrap",
            source_type="task_constraints",
            title="Active task constraints",
            content=(
                f"Task class: {classification.get('task_class', 'unknown')}. "
                f"Summary: {getattr(task, 'task_summary', '')}. "
                f"Risk flags: {_compact_join(list(classification.get('risk_flags') or []), limit=4) or 'none'}."
            ),
            priority=0.92,
            confidence=float(classification.get("confidence_hint", 0.0) or 0.0),
            must_keep=True,
            include_reason="task_constraints",
        ),
        ContextItem(
            item_id="bootstrap-safety",
            layer="bootstrap",
            source_type="policy",
            title="Safety mode",
            content=(
                f"Operating mode: {mode_state['label']}. {action_guidance} "
                f"Persona core locked: {bool(policy_engine.get('personality.persona_core_locked', True))}. "
                f"Understanding confidence: {float(getattr(interpretation, 'understanding_confidence', 0.0) or 0.0):.2f}."
            ),
            # Ranked above every other bootstrap item. Measured 2026-07-31 on an ordinary chat
            # turn: at priority 0.88 this block sorted BELOW bootstrap-persona (1.0) and
            # bootstrap-self-knowledge (0.97, 586 tokens), the latter consumed the whole 180-token
            # layer by itself, and this 23-token block was dropped at budget_exhausted along with
            # bootstrap-conversation-safety and bootstrap-task. The model answered with no safety
            # block and was not told. Whatever the layer budget is, the cheapest safety-bearing
            # item must not be the first thing a persona essay evicts.
            priority=1.0,
            confidence=0.9,
            must_keep=True,
            include_reason="safety_policy",
            metadata={"exclude_from_chat_minimal_system_prompt": True},
        ),
        ContextItem(
            item_id="bootstrap-conversation-safety",
            layer="bootstrap",
            source_type="conversation_policy",
            title="Conversation policy",
            content=(
                "Sensitive conversation is allowed when the user is asking for discussion, analysis, or explanation only. "
                "Do not confuse conversation about intimate, controversial, or offensive topics with permission to take action, reveal private data, or bypass safety gates."
            ),
            # Same reason as bootstrap-safety above: ranked ahead of the descriptive blocks so the
            # rule separating "discuss this" from "act on this" survives a tight layer.
            priority=0.999,
            confidence=0.92,
            must_keep=True,
            include_reason="conversation_safety_split",
        ),
    ]

    working = getattr(interpretation, "working_interpretation", None)
    if working and getattr(working, "grounding_note", ""):
        items.append(
            ContextItem(
                item_id="bootstrap-short-input-grounding",
                layer="bootstrap",
                source_type="context_understanding",
                title="Short/fragmented input grounding",
                content=getattr(working, "grounding_note", ""),
                priority=0.91,
                confidence=0.9,
                must_keep=True,
                include_reason="anti_hallucination",
            )
        )

    # Self-knowledge: load VOOL's self-awareness document ONLY when the turn is about this
    # assistant or runtime. Unconditional injection (must_keep, priority 0.97, on every non-plain
    # turn) is the measured root of the canned x402/self-description text leaking into unrelated
    # answers -- the old D-item, confirmed still ungated by the 2026-08-15 pipeline audit. A turn
    # about weather, code, or the user's own files gains nothing from the runtime's self-portrait;
    # a turn asking what the assistant is, can do, or how it works still gets the full document.
    _turn_text = " ".join(
        str(value or "")
        for value in (
            getattr(interpretation, "raw_text", ""),
            getattr(interpretation, "normalized_text", ""),
            getattr(task, "task_summary", ""),
        )
    )
    sk_text = (
        _read_markdown_context("docs", "VOOL_SELF_KNOWLEDGE.md", max_chars=2800)
        if _turn_is_about_the_assistant(_turn_text)
        else ""
    )
    if sk_text:
        items.append(
            ContextItem(
                item_id="bootstrap-self-knowledge",
                layer="bootstrap",
                source_type="self_knowledge",
                title="Self-knowledge",
                content=sk_text,
                priority=0.97,
                confidence=1.0,
                must_keep=True,
                include_reason="agent_self_awareness",
            )
        )

    # Operational doctrine: OpenClaw integrations + live internet behavior.
    # Tool doctrine: relevance-gated like self-knowledge above (A9 P0). Unconditional
    # injection put a 2k-char operating doctrine about tool/internet behavior on every
    # bootstrap — weather small talk rode the same contract as a "run this command" turn.
    # The gate keeps the item for turns that plausibly touch tools, commands, files or the
    # internet (the doctrine's whole subject), and drops it from purely conversational ones.
    if _turn_needs_tool_doctrine(
        " ".join(
            str(value or "")
            for value in (
                getattr(interpretation, "raw_text", ""),
                getattr(interpretation, "normalized_text", ""),
                getattr(task, "task_summary", ""),
            )
        )
    ):
        doctrine_text = _read_markdown_context("docs", "VOOL_OPENCLAW_TOOL_DOCTRINE.md", max_chars=2000)
        if doctrine_text:
            items.append(
                ContextItem(
                    item_id="bootstrap-openclaw-doctrine",
                    layer="bootstrap",
                    source_type="operating_doctrine",
                    title="OpenClaw tool doctrine",
                    content=doctrine_text,
                    priority=0.965,
                    confidence=1.0,
                    include_reason="tooling_behavior_contract",
                    metadata={"exclude_from_chat_minimal_system_prompt": True},
                )
            )

    # Owner identity: display name, privacy pact, and owner authority.
    try:
        from core.onboarding import load_identity
        identity = load_identity()
        # Map through the canonical accessor rather than trusting the raw row: get_agent_display_name()
        # rewrites the legacy default to the current product name, and reading identity["agent_name"]
        # directly injected "My current display name is VOOL" into every prompt -- which is what the
        # model was repeating when asked its own name.
        #
        # Still gated on the row actually carrying a name. The accessor always returns something, so
        # calling it unconditionally would emit a must_keep identity item for operators who never set
        # one, and that item evicts grounding facts from a tight evidence budget.
        from core.onboarding import get_agent_display_name as _canonical_agent_name

        stored_name = str(identity.get("agent_name", "") or "").strip()
        agent_name = (str(_canonical_agent_name() or "").strip() or stored_name) if stored_name else ""
        privacy_pact = identity.get("privacy_pact", "")
        if agent_name:
            content = (
                f"My current display name is {agent_name}. "
                "The operator can rename me or give me a nickname at any time. "
                "Internal runtime identity and display naming are separate."
            )
            items.append(
                ContextItem(
                    item_id="bootstrap-owner-identity",
                    layer="bootstrap",
                    source_type="owner_identity",
                    title="Owner identity",
                    content=content,
                    priority=0.99,
                    confidence=1.0,
                    must_keep=True,
                    include_reason="owner_identity_contract",
                )
            )
            if privacy_pact:
                items.append(
                    ContextItem(
                        item_id="bootstrap-owner-privacy-pact",
                        layer="bootstrap",
                        source_type="privacy_pact",
                        title="Privacy pact",
                        content=f"Privacy pact: {privacy_pact}",
                        priority=0.98,
                        confidence=1.0,
                        must_keep=True,
                        include_reason="owner_privacy_contract",
                        metadata={"exclude_from_chat_minimal_system_prompt": True},
                    )
                )
    except Exception:
        pass

    try:
        from core.voolbook_identity import get_profile
        from network.signer import get_local_peer_id
        nb_profile = get_profile(get_local_peer_id())
        if nb_profile and nb_profile.status == "active":
            nb_content = (
                f"I have a VoolBook account with handle '{nb_profile.handle}'. "
                f"VoolBook is the public web surface for agent work in the VOOL hive. "
                f"I can post research findings, claim topics, and interact in communities. "
                f"My posts are authenticated with a dedicated posting token (X-VoolBook-Token). "
                f"VoolBook etiquette: evidence-backed posts, no spam, proof-of-useful-work matters. "
                f"Stats: {nb_profile.post_count} posts, {nb_profile.claim_count} claims."
            )
            if nb_profile.bio:
                nb_content += f" Bio: {nb_profile.bio}"
            items.append(
                ContextItem(
                    item_id="bootstrap-voolbook-identity",
                    layer="bootstrap",
                    source_type="voolbook_identity",
                    title="VoolBook profile",
                    content=nb_content,
                    priority=0.90,
                    confidence=1.0,
                    must_keep=True,
                    include_reason="voolbook_social_identity",
                )
            )
    except Exception:
        pass

    if include_private_context:
        # Runtime memory: rendered from the GOVERNED canonical entries (see
        # core/memory/entries.py load_memory_excerpt) — never from the raw
        # MEMORY.md projection, which is a human-readable mirror and can carry
        # stale, restored or hand-edited bytes no read path may trust.
        try:
            memory_excerpt = load_memory_excerpt(max_chars=2000).strip()
            if include_user_profile_context and memory_excerpt:
                items.append(
                    ContextItem(
                        item_id="bootstrap-runtime-memory",
                        layer="bootstrap",
                        source_type="runtime_memory",
                        title="Persistent memory",
                        content=memory_excerpt,
                        priority=0.94,
                        confidence=0.9,
                        include_reason="persistent_runtime_memory",
                    )
                )
        except Exception:
            pass

        try:
            policy_text = describe_session_memory_policy(session_id)
            if policy_text:
                items.append(
                    ContextItem(
                        item_id="bootstrap-session-memory-policy",
                        layer="bootstrap",
                        source_type="session_policy",
                        title="Session memory policy",
                        content=policy_text,
                        priority=0.985,
                        confidence=1.0,
                        must_keep=True,
                        include_reason="memory_sharing_scope",
                        metadata={"exclude_from_chat_minimal_system_prompt": True},
                    )
                )
        except Exception:
            pass

        conversation_pref_text = _conversation_preference_text(
            str(getattr(interpretation, "raw_text", "")
                or getattr(interpretation, "reconstructed_text", "")
                or getattr(interpretation, "normalized_text", "") or "")
        ) if include_user_profile_context else ""
        if conversation_pref_text:
            items.append(
                ContextItem(
                    item_id="bootstrap-conversation-preferences",
                    layer="bootstrap",
                    source_type="user_preferences",
                    title="Conversation preferences",
                    content=conversation_pref_text,
                    priority=0.91,
                    confidence=1.0,
                    include_reason="persistent_user_preferences",
                )
            )

        execution_pref_text = _execution_preference_text() if include_user_profile_context else ""
        if execution_pref_text:
            items.append(
                ContextItem(
                    item_id="bootstrap-execution-preferences",
                    layer="bootstrap",
                    source_type="execution_preferences",
                    title="Execution preferences",
                    content=execution_pref_text,
                    priority=0.9,
                    confidence=1.0,
                    include_reason="execution_policy_preferences",
                    metadata={"exclude_from_chat_minimal_system_prompt": True},
                )
            )

    if quality_flags:
        items.append(
            ContextItem(
                item_id="bootstrap-quality",
                layer="bootstrap",
                source_type="input_quality",
                title="Input quality",
                content=f"Quality flags: {_compact_join(quality_flags, limit=5)}.",
                priority=0.72,
                confidence=0.7,
                include_reason="input_quality",
            )
        )

    if recent_turns:
        # A9 RC-8 pass-003: the summary join is a transcript carrier too — a
        # governed assistant turn must not survive into it as an 80-char
        # fragment. Same lineage-first availability resolution as every other
        # carrier. A suppressed row is CLOSED here rather than omitted: omitting it
        # left the preceding user request reading as still-pending work in exactly
        # the way the full transcript did.
        def _summary_line(turn: dict[str, Any]) -> str:
            role = str(turn.get("speaker_role") or "user").strip().lower()
            if role == "assistant" and _assistant_text_unavailable(
                str(turn.get("raw_input") or ""),
                str(turn.get("request_id") or ""),
            ):
                return f"Assistant: {CLOSED_EXCHANGE_NOTICE}"
            return f"{role.title()}: {str(turn.get('reconstructed_input') or '')[:80]}"

        recent_summary = " | ".join(_summary_line(turn) for turn in recent_turns[:2])
        items.append(
            ContextItem(
                item_id="bootstrap-dialogue",
                layer="bootstrap",
                source_type="recent_dialogue",
                title="Recent dialogue state",
                content=f"Recent turns: {recent_summary}",
                priority=0.82,
                confidence=0.72,
                include_reason="recent_dialogue",
            )
        )

    if lexicon:
        selected: list[str] = []
        input_text = (
            f"{getattr(interpretation, 'normalized_text', '')} "
            f"{getattr(interpretation, 'reconstructed_text', '')}"
        ).lower()
        for term, canonical in lexicon.items():
            if term in input_text or canonical in input_text or canonical in topic_hints:
                selected.append(f"{term}->{canonical}")
            if len(selected) >= max_lexicon_items:
                break
        if selected:
            items.append(
                ContextItem(
                    item_id="bootstrap-lexicon",
                    layer="bootstrap",
                    source_type="shorthand",
                    title="Active shorthand mappings",
                    content=f"Shorthand: {', '.join(selected[:max_lexicon_items])}.",
                    priority=0.7,
                    confidence=0.75,
                    include_reason="active_shorthand",
                )
            )

    return items
