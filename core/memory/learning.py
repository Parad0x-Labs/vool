from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from core.context_scope import ContextAccessPolicy
from core.memory import entries as memory_entries
from core.memory.files import (
    MAX_DENSE_OPERATOR_PROFILE_BYTES,
    MAX_SESSION_SUMMARY_BYTES,
    MAX_USER_HEURISTICS_BYTES,
    append_jsonl,
    ensure_memory_files,
    load_jsonl,
    operator_dense_profile_path,
    rewrite_jsonl,
    session_summaries_path,
    trim_jsonl_file,
    user_heuristics_path,
    utcnow,
)
from core.memory.policies import ensure_session_policy_table, session_memory_policy
from core.privacy_guard import share_scope_label
from storage.dialogue_memory import record_dialogue_turn


def _current_request_lineage() -> str:
    """A8 payload lineage: the A0 request id bound to the current dispatch, if
    any ('' on legacy/off-HTTP lanes — never fabricated)."""
    try:
        from core.semantic.semantic_admissions import current_request_id

        return str(current_request_id() or "")
    except Exception:
        return ""

_SENTENCE_SPLIT_RE = re.compile(r"(?:[\n\r]+|(?<=[.!?;])\s+)")
_NAME_PATTERNS = [
    re.compile(r"\bmy name is\s+([a-z0-9][a-z0-9 '\-]{1,40})", re.IGNORECASE),
    re.compile(r"\bcall me\s+([a-z0-9][a-z0-9 '\-]{1,40})", re.IGNORECASE),
    re.compile(r"\bi go by\s+([a-z0-9][a-z0-9 '\-]{1,40})", re.IGNORECASE),
]
#: The name clause only -- bounded at the next conjunction or punctuation so the rest of a
#: mixed sentence ("... and I live in Vilnius") still reaches fact extraction.
_NAME_CLAUSE_RE = re.compile(
    r"\b(?:my name is|call me|i go by)\s+[A-Za-z0-9'\-]+(?:\s+[A-Za-z0-9'\-]+){0,2}?(?=\s+(?:and|but|so)\b|[,.;!?]|$)",
    re.IGNORECASE,
)
_STYLE_PATTERNS = [
    re.compile(r"\b(?:be|stay)\s+(?:more\s+)?(?:concise|brief|direct|blunt|honest|clear)\b", re.IGNORECASE),
    re.compile(r"\bkeep\s+(?:the\s+)?(?:answers|responses|reply|tone|style)\s+(?:concise|brief|direct|blunt|honest|clear)\b", re.IGNORECASE),
    re.compile(r"\bno\s+(?:fluff|hopium|copium|bullshit)\b", re.IGNORECASE),
    re.compile(r"\bbrutal(?:ly)?\s+honest\b", re.IGNORECASE),
]
_PREFERENCE_PATTERNS = [
    re.compile(r"\bi prefer\b", re.IGNORECASE),
    re.compile(r"\bi like\b", re.IGNORECASE),
    re.compile(r"\bi want you to\b", re.IGNORECASE),
    re.compile(r"\bfrom now on\b", re.IGNORECASE),
    re.compile(r"\balways\b.{0,60}\b(?:answer|respond|use|remember|be)\b", re.IGNORECASE),
    re.compile(r"\bnever\b.{0,60}\b(?:answer|respond|use|forget|be)\b", re.IGNORECASE),
]
_FACT_PATTERNS = [
    re.compile(r"\bi use\b", re.IGNORECASE),
    re.compile(r"\bi work on\b", re.IGNORECASE),
    re.compile(r"\bi(?:'m| am)\s+building\b", re.IGNORECASE),
    re.compile(r"\bmy project\b", re.IGNORECASE),
    re.compile(r"\bmy setup\b", re.IGNORECASE),
    re.compile(r"\bi maintain\b", re.IGNORECASE),
    re.compile(r"\bi live in\b", re.IGNORECASE),
    re.compile(r"\bi work in\b", re.IGNORECASE),
]
_EPHEMERAL_HINTS = (
    "fix this",
    "build this",
    "replace this",
    "change this",
    "do this now",
    "today",
    "right now",
    "currently",
)
_HEURISTIC_STYLE_MARKERS = {
    "concise_direct": ("concise", "brief", "direct", "clear", "blunt"),
    "brutal_honest": ("brutal", "brutally honest", "honest", "no fluff", "no hopium", "no copium", "no bullshit"),
}
_HEURISTIC_SOURCE_MARKERS = {
    "official_docs": ("official docs", "official documentation", "official sources"),
    "github_repos": ("github", "repo", "repos", "repository", "repositories"),
    "reputable_sources": ("reputable sources", "trusted sources", "public websites", "good sources"),
}
_HEURISTIC_STACK_MARKERS = {
    "python": ("python", "py"),
    "typescript": ("typescript",),
    "javascript": ("javascript", "node", "nodejs"),
    "rust": ("rust",),
    "go": ("golang", "go"),
}
_HEURISTIC_PROJECT_MARKERS = {
    "telegram_bot": ("telegram", "telegram bot", "tg bot", "bot api"),
    "discord_bot": ("discord", "discord bot"),
    "hive_swarm": ("hive", "swarm", "hive mind", "mesh"),
    "openclaw_runtime": ("openclaw", "vool", "runtime"),
}
_HEURISTIC_BUILD_MARKERS = ("build", "building", "create", "making", "write", "implement", "scaffold", "work on", "project")
_HEURISTIC_AUTONOMY_MARKERS = (
    "do all this",
    "do it",
    "just do it",
    "start fixing",
    "carry on",
    "don't ask",
    "dont ask",
    "no micro approval",
    "test all",
)
_SHORTHAND_PATTERNS = [
    re.compile(r"\bwhen i say\s+[\"']?(.+?)[\"']?\s*,?\s*(?:i mean|it means|that means)\s+[\"']?(.+?)[\"']?\s*$", re.IGNORECASE),
    re.compile(r"[\"'](.+?)[\"']\s+means\s+[\"'](.+?)[\"']", re.IGNORECASE),
    re.compile(r"\bby\s+[\"'](.+?)[\"']\s+i mean\s+[\"']?(.+?)[\"']?\s*$", re.IGNORECASE),
]
_NEGATIVE_FEEDBACK_PATTERNS = [
    re.compile(r"\bno,?\s+(?:that'?s?\s+)?(?:not|wrong|incorrect)\b", re.IGNORECASE),
    re.compile(r"\bthat'?s?\s+(?:not|wrong|incorrect|bad|off)\b", re.IGNORECASE),
    re.compile(r"\byou'?re\s+(?:wrong|incorrect|off|mistaken)\b", re.IGNORECASE),
    re.compile(r"\bi\s+(?:said|meant|asked|wanted)\b", re.IGNORECASE),
    re.compile(r"\bnot\s+what\s+i\s+(?:asked|meant|wanted)\b", re.IGNORECASE),
    re.compile(r"\btry\s+again\b", re.IGNORECASE),
    re.compile(r"\bwrong\s+(?:answer|response)\b", re.IGNORECASE),
]
_POSITIVE_FEEDBACK_PATTERNS = [
    re.compile(r"\b(?:perfect|exactly|correct|right|great|thanks|nice|good job|well done)\b", re.IGNORECASE),
    re.compile(r"\bthat'?s?\s+(?:it|right|correct|perfect|exactly)\b", re.IGNORECASE),
    re.compile(r"\byou'?re\s+right\b", re.IGNORECASE),
]


def load_operator_dense_profile() -> dict[str, Any]:
    _ensure_memory_files()
    path = operator_dense_profile_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace") or "{}")
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def refresh_operator_dense_profile(*, session_id: str | None = None) -> dict[str, Any]:
    _ensure_memory_files()
    heuristics = [
        dict(row)
        for row in load_jsonl(user_heuristics_path())
        if str(row.get("scope") or "") == "user_profile"
        and str(row.get("status") or "") == "active"
        and isinstance(row.get("provenance"), dict)
    ]

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in heuristics:
        category = str(row.get("category") or "").strip().lower()
        if not category:
            continue
        bucket = grouped.setdefault(category, [])
        bucket.append(dict(row))
    for bucket in grouped.values():
        bucket.sort(
            key=lambda row: (
                int(row.get("mentions") or 0),
                float(row.get("confidence") or 0.0),
                str(row.get("updated_at") or row.get("created_at") or ""),
            ),
            reverse=True,
        )

    response_style = [dense_signal_name(item) for item in grouped.get("response_style", [])[:2]]
    source_preferences = [dense_signal_name(item) for item in grouped.get("source_preference", [])[:3]]
    preferred_stacks = [dense_signal_name(item) for item in grouped.get("preferred_stack", [])[:2]]

    dense_summary_parts: list[str] = []
    if response_style:
        dense_summary_parts.append("Style: " + ", ".join(response_style) + ".")
    if source_preferences:
        dense_summary_parts.append("Sources: " + ", ".join(source_preferences) + ".")
    if preferred_stacks:
        dense_summary_parts.append("Stacks: " + ", ".join(preferred_stacks) + ".")
    payload: dict[str, Any] = {
        "updated_at": utcnow(),
        "last_session_id": str(session_id or ""),
        "share_scope": "local_only",
        "policy_tags": ["LOCAL_ONLY", "CONFIRMED_PROFILE_ONLY"],
        "response_style": response_style,
        "source_preferences": source_preferences,
        "preferred_stacks": preferred_stacks,
        "project_focus": [],
        "active_projects": [],
        "recent_session_summaries": [],
        "memory_facts": [],
        "dense_summary": " ".join(part for part in dense_summary_parts if part).strip(),
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    if len(encoded.encode("utf-8")) > MAX_DENSE_OPERATOR_PROFILE_BYTES:
        payload["dense_summary"] = " ".join(part for part in dense_summary_parts[:4] if part).strip()
        encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    operator_dense_profile_path().write_text(encoded + "\n", encoding="utf-8")
    return payload


def normalized_history(history: Any) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for item in list(history or []):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = " ".join(str(item.get("content") or "").split()).strip()
        if role not in {"system", "user", "assistant"} or not content:
            continue
        normalized.append({"role": role, "content": content[:600]})
    return normalized


def record_assistant_dialogue_turn(*, session_id: str, assistant_output: str) -> str | None:
    clean = str(assistant_output or "").strip()
    if not clean:
        return None
    normalized = " ".join(clean.split()).strip()
    return record_dialogue_turn(
        session_id,
        raw_input=clean,
        normalized_input=normalized,
        reconstructed_input=clean,
        speaker_role="assistant",
        topic_hints=[],
        reference_targets=[],
        understanding_confidence=1.0,
        quality_flags=[],
    )


def auto_capture_memory(*, session_id: str, user_input: str, project_id: str | None = None) -> None:
    if not should_auto_extract(user_input):
        return

    _auto_learn_shorthand(session_id=session_id, user_input=user_input)

    seen: set[str] = set()
    authority = (
        "user_correction"
        if memory_entries.is_user_correction(user_input)
        else "confirmed_memory"
    )
    for candidate in extract_memory_candidates(user_input):
        text = memory_entries.sanitize_fact(str(candidate.get("text") or ""))
        if not text:
            continue
        lowered = text.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        memory_entries.add_memory_fact(
            text,
            category=str(candidate.get("category") or "fact"),
            session_id=session_id,
            source="auto_dialogue",
            confidence=float(candidate.get("confidence") or 0.78),
            keywords=list(candidate.get("keywords") or []),
            project_id=project_id,
            authority=authority,
        )


def update_user_heuristics(*, session_id: str, user_input: str, project_id: str | None = None) -> None:
    candidates = extract_user_heuristic_candidates(user_input)
    _apply_feedback_to_heuristics(session_id=session_id)
    if not candidates:
        return
    existing = {
        str(row.get("heuristic_id") or "").strip(): row
        for row in load_jsonl(user_heuristics_path())
        if str(row.get("heuristic_id") or "").strip()
    }
    created_at = utcnow()
    for candidate in candidates:
        heuristic_id = str(candidate.get("heuristic_id") or "").strip()
        if not heuristic_id:
            continue
        previous = dict(existing.get(heuristic_id) or {})
        mentions = int(previous.get("mentions") or 0) + 1
        confidence = max(float(previous.get("confidence") or 0.0), float(candidate.get("confidence") or 0.72))
        category = str(
            candidate.get("category")
            or previous.get("category")
            or "heuristic"
        )
        status = (
            "candidate"
            if category == "project_focus" and mentions < 2
            else "active"
        )
        merged_keywords = memory_entries.keyword_tokens_filtered(
            " ".join(
                [
                    " ".join(str(item) for item in list(previous.get("keywords") or [])),
                    " ".join(str(item) for item in list(candidate.get("keywords") or [])),
                    str(candidate.get("text") or ""),
                ]
            ),
            limit=18,
        )
        text = str(candidate.get("text") or previous.get("text") or "").strip()
        existing[heuristic_id] = {
            "heuristic_id": heuristic_id,
            "category": category,
            "signal": str(candidate.get("signal") or previous.get("signal") or heuristic_id),
            "text": text,
            "confidence": round(confidence, 4),
            "mentions": mentions,
            "keywords": merged_keywords,
            "scope": "user_profile",
            "source": "user_heuristic",
            "source_id": "profile:confirmed",
            "status": status,
            "authority": "confirmed_memory",
            "origin_chat_id": session_id,
            "origin_project_id": str(project_id or "").strip(),
            "session_id": session_id,
            "project_id": str(project_id or "").strip(),
            "provenance": {
                "kind": "direct_user_observation",
                "source_id": "profile:confirmed",
                "content_hash": hashlib.sha256(text.encode()).hexdigest(),
                "origin_chat_id": session_id,
                "origin_project_id": str(project_id or "").strip(),
            },
            "created_at": str(previous.get("created_at") or created_at),
            "updated_at": created_at,
            # A8 pass-001 payload lineage ('' on legacy lanes — never fabricated).
            "request_id": _current_request_lineage(),
        }
    rows = sorted(
        existing.values(),
        key=lambda row: (str(row.get("updated_at") or ""), int(row.get("mentions") or 0)),
        reverse=True,
    )
    rewrite_jsonl(user_heuristics_path(), rows[:64])
    trim_jsonl_file(user_heuristics_path(), max_bytes=MAX_USER_HEURISTICS_BYTES)


def detect_implicit_feedback(
    *,
    session_id: str,
    user_input: str,
    assistant_output: str,
) -> None:
    text = str(user_input or "").strip()
    if not text or len(text) < 3:
        return

    from storage.dialogue_memory import record_response_feedback

    lowered = text.lower()
    is_negative = any(pattern.search(lowered) for pattern in _NEGATIVE_FEEDBACK_PATTERNS)
    is_positive = any(pattern.search(lowered) for pattern in _POSITIVE_FEEDBACK_PATTERNS)

    if is_negative:
        record_response_feedback(
            session_id,
            feedback_type="implicit_rejection",
            feedback_value=-0.6,
            user_correction=text[:500] if len(text) > 20 else None,
            context_snapshot=(assistant_output or "")[:300],
        )
    elif is_positive and len(text) < 80:
        record_response_feedback(
            session_id,
            feedback_type="implicit_approval",
            feedback_value=0.6,
            context_snapshot=(assistant_output or "")[:300],
        )


def update_session_summary(
    *,
    session_id: str,
    user_input: str,
    assistant_output: str,
    project_id: str | None = None,
    access_policy: ContextAccessPolicy | None = None,
) -> None:
    resolved_policy = memory_entries.resolve_memory_access_policy(
        access_policy=access_policy,
        chat_id=session_id,
    )
    clean_project = str(project_id or "").strip()
    if clean_project and clean_project != resolved_policy.project_id:
        raise ValueError("session summary project does not match chat namespace")
    recent = memory_entries.recent_conversation_events(session_id, limit=6)
    if not recent:
        return
    recent_user = [str(row.get("user") or "").strip() for row in recent if str(row.get("user") or "").strip()][-3:]
    if not recent_user:
        return
    memory_hits = memory_entries.search_relevant_memory(
        " ".join(recent_user[-2:]),
        access_policy=resolved_policy,
        limit=2,
    )
    last_assistant = memory_entries.sanitize_fact(assistant_output) or memory_entries.sanitize_fact(str(recent[-1].get("assistant") or ""))

    # Recent asks (verbatim) are the truthful signal — a keyword-token "Session topics" line was dropped:
    # it surfaced filler and typos ("hey, check, pls, waht") as if they were topics.
    parts: list[str] = []
    parts.append("Recent asks: " + " | ".join(memory_entries.trim_text(item, 100) for item in recent_user) + ".")
    if memory_hits:
        # This is a SECOND recall path, separate from load_memory_excerpt, and it was injecting
        # "**My name**: VOOL" from pre-rename memory as durable fact. Canonicalise the assistant's
        # own name here too -- stored rows are never rewritten, only what the model reads.
        parts.append(
            "Relevant durable memory: "
            + "; ".join(
                memory_entries.canonical_agent_name_in_memory(memory_entries.trim_text(str(item.get("text") or ""), 90))
                for item in memory_hits
            )
            + "."
        )
    if last_assistant:
        # A prior reply that used the old name would otherwise be fed back as "last outcome" and
        # re-copied verbatim -- the self-reinforcing loop that kept the wrong name alive.
        parts.append(
            f"Last assistant outcome: {memory_entries.canonical_agent_name_in_memory(memory_entries.trim_text(last_assistant, 140))}."
        )

    summary = " ".join(part for part in parts if part.strip()).strip()
    if not summary:
        return

    latest: dict[str, Any] | None = None
    for row in reversed(load_jsonl(session_summaries_path())):
        if str(row.get("session_id") or "") == str(session_id):
            latest = row
            break
    if latest and str(latest.get("summary") or "") == summary:
        return

    policy = session_memory_policy(session_id)
    created_at = utcnow()
    source_id = f"session-summary:{session_id}:{created_at}"
    append_jsonl(
        session_summaries_path(),
        {
            "created_at": created_at,
            # A8 pass-001 payload lineage ('' on legacy lanes — never fabricated).
            "request_id": _current_request_lineage(),
            "session_id": str(session_id),
            "project_id": clean_project,
            "scope": "chat",
            "source": "session_summary",
            "source_id": source_id,
            "status": "active",
            "authority": "confirmed_memory",
            "origin_chat_id": str(session_id),
            "origin_project_id": clean_project,
            "provenance": {
                "kind": "derived_session_summary",
                "source_id": source_id,
                "content_hash": hashlib.sha256(summary.encode()).hexdigest(),
                "origin_chat_id": str(session_id),
                "origin_project_id": clean_project,
            },
            "summary": summary,
            "keywords": memory_entries.keyword_tokens_filtered(summary),
            "turn_count": len(recent),
            "share_scope": policy["share_scope"],
            "realm_label": share_scope_label(policy["share_scope"]),
            "restricted_terms": list(policy.get("restricted_terms") or []),
        },
    )
    trim_jsonl_file(session_summaries_path(), max_bytes=MAX_SESSION_SUMMARY_BYTES)


def extract_user_heuristic_candidates(user_input: str) -> list[dict[str, Any]]:
    lowered = " ".join(str(user_input or "").split()).strip().lower()
    if not lowered or len(lowered) < 10:
        return []
    out: list[dict[str, Any]] = []

    for signal, markers in _HEURISTIC_STYLE_MARKERS.items():
        if any(marker in lowered for marker in markers):
            out.append(
                {
                    "heuristic_id": f"response_style:{signal}",
                    "category": "response_style",
                    "signal": signal,
                    "text": heuristic_text("response_style", signal),
                    "confidence": 0.86 if signal == "brutal_honest" else 0.80,
                    "keywords": memory_entries.keyword_tokens_filtered(heuristic_text("response_style", signal)),
                }
            )

    for signal, markers in _HEURISTIC_SOURCE_MARKERS.items():
        if any(marker in lowered for marker in markers):
            out.append(
                {
                    "heuristic_id": f"source_preference:{signal}",
                    "category": "source_preference",
                    "signal": signal,
                    "text": heuristic_text("source_preference", signal),
                    "confidence": 0.78,
                    "keywords": memory_entries.keyword_tokens_filtered(heuristic_text("source_preference", signal)),
                }
            )

    wants_build = any(marker in lowered for marker in _HEURISTIC_BUILD_MARKERS)
    for signal, markers in _HEURISTIC_STACK_MARKERS.items():
        if any(re.search(rf"\b{re.escape(marker)}\b", lowered) for marker in markers) and wants_build:
            out.append(
                {
                    "heuristic_id": f"preferred_stack:{signal}",
                    "category": "preferred_stack",
                    "signal": signal,
                    "text": heuristic_text("preferred_stack", signal),
                    "confidence": 0.76,
                    "keywords": memory_entries.keyword_tokens_filtered(heuristic_text("preferred_stack", signal)),
                }
            )

    for signal, markers in _HEURISTIC_PROJECT_MARKERS.items():
        if any(marker in lowered for marker in markers):
            out.append(
                {
                    "heuristic_id": f"project_focus:{signal}",
                    "category": "project_focus",
                    "signal": signal,
                    "text": heuristic_text("project_focus", signal),
                    "confidence": 0.74,
                    "keywords": memory_entries.keyword_tokens_filtered(heuristic_text("project_focus", signal)),
                }
            )

    if any(marker in lowered for marker in _HEURISTIC_AUTONOMY_MARKERS):
        out.append(
            {
                "heuristic_id": "autonomy_preference:hands_off",
                "category": "autonomy_preference",
                "signal": "hands_off",
                "text": heuristic_text("autonomy_preference", "hands_off"),
                "confidence": 0.82,
                "keywords": memory_entries.keyword_tokens_filtered(heuristic_text("autonomy_preference", "hands_off")),
            }
        )

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in out:
        heuristic_id = str(item.get("heuristic_id") or "").strip()
        if not heuristic_id or heuristic_id in seen:
            continue
        seen.add(heuristic_id)
        deduped.append(item)
    return deduped


#: A sentence that is NOTHING BUT an instruction to remember, governing the sentences beside it:
#: "Remember that.", "Remember these two things.", "Please note both.", "Save all of that."
#:
#: The explicit-capture lane (`persistent_memory._MEMORY_CAPTURE_RE`) is `^`-anchored, so it only
#: sees the directive when it OPENS the turn ("Remember that my cat is Mira"). The ordinary
#: phrasing puts it last, and nothing read it there. Measured 2026-08-18 on the three turns that
#: seeded a 50-turn chat:
#:
#:   "My name is Alex and I live in Vilnius. Remember that."          -> stored (matched `i live in`)
#:   "My project is called VOOL and my budget is 4200 euros. Remember that too."
#:                                                                       -> stored (matched `my project`)
#:   "My favourite colour is teal and my cat is called Mira. Remember these two things."
#:                                                                       -> NOTHING STORED
#:
#: The two that survived did so by accident of the eight-phrase `_FACT_PATTERNS` list, not because
#: the user asked. The third asked in exactly the same way and was dropped. Adding "my favourite
#: colour" and "my cat" to that list would be the same defect with two more rows: the user's
#: explicit instruction is the admission, and a heuristic for IMPLICIT facts should not be able to
#: veto it.
_STANDALONE_REMEMBER_RE = re.compile(
    r"^(?:and\s+|also\s+|please\s+|oh\s+|okay\s+|ok\s+)*"
    r"(?:remember|note|store|save|memorise|memorize|keep\s+in\s+mind)"
    r"(?:\s+(?:that|this|these|those|them|it|both|all|the\s+above))?"
    r"(?:\s+(?:two|three|four|five|\d+))?"
    r"(?:\s+(?:things?|facts?|items?|points?|details?))?"
    r"(?:\s+(?:too|as\s+well|also|please|for\s+me|pls))*"
    r"\s*[.!]?$",
    re.IGNORECASE,
)


def _turn_carries_an_explicit_remember_directive(sentences: list[str]) -> bool:
    """Whether one of these sentences is a bare instruction to remember the others."""

    return any(_STANDALONE_REMEMBER_RE.match(sentence.strip()) for sentence in sentences)


def extract_memory_candidates(user_input: str) -> list[dict[str, Any]]:
    text = " ".join(str(user_input or "").split()).strip()
    if not text:
        return []

    out: list[dict[str, Any]] = []
    # The operator's NAME is an Operator Profile item (core.operator_profile), learned at the
    # turn front door as a candidate the user confirms -- never harvested here into a durable
    # "Operator name is X." fact behind their back. The clause is dropped from free-text
    # extraction too, so the memory store cannot become a second name authority.
    text = _NAME_CLAUSE_RE.sub(" ", text)
    text = re.sub(r"(?:^|(?<=[.!?;]\s))(?:and|but|so)\b[\s,]*", "", " ".join(text.split()), flags=re.IGNORECASE)
    text = " ".join(text.split())
    if not text:
        return out

    sentences = candidate_sentences(text)
    directed = _turn_carries_an_explicit_remember_directive(sentences)
    for sentence in sentences:
        clean = memory_entries.sanitize_fact(sentence)
        if not clean or should_skip_memory_sentence(clean):
            continue
        # The directive itself is an instruction, not a fact about the user.
        if _STANDALONE_REMEMBER_RE.match(sentence.strip()):
            continue
        if any(pattern.search(clean) for pattern in _STYLE_PATTERNS):
            out.append(
                {
                    "text": clean,
                    "category": "instruction",
                    "confidence": 0.88,
                    "keywords": memory_entries.keyword_tokens_filtered(clean),
                }
            )
            continue
        if any(pattern.search(clean) for pattern in _PREFERENCE_PATTERNS):
            out.append(
                {
                    "text": clean,
                    "category": "preference",
                    "confidence": 0.82,
                    "keywords": memory_entries.keyword_tokens_filtered(clean),
                }
            )
            continue
        if directed or any(pattern.search(clean) for pattern in _FACT_PATTERNS):
            out.append(
                {
                    "text": clean,
                    "category": "fact",
                    # An explicit instruction outranks a pattern guess about the same sentence.
                    "confidence": 0.94 if directed else 0.76,
                    "keywords": memory_entries.keyword_tokens_filtered(clean),
                }
            )
    return out


def candidate_sentences(text: str) -> list[str]:
    rough = _SENTENCE_SPLIT_RE.split(text)
    parts: list[str] = []
    for chunk in rough:
        chunk = chunk.strip()
        if not chunk:
            continue
        parts.extend(
            piece.strip()
            for piece in re.split(
                r",\s+(?=(?:keep|be|stay|please|always|never|from now on|i prefer|i like|i want you to|i use|i work on|my project|my setup|call me|my name is))",
                chunk,
                flags=re.IGNORECASE,
            )
            if piece.strip()
        )
    return parts


def should_auto_extract(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    if lowered.startswith(
        (
            "remember ",
            "remember that ",
            "forget ",
            "erase ",
            "correction ",
            "correct ",
            "update ",
            "replace ",
            "/memory",
        )
    ):
        return False
    return len(lowered) >= 12


def should_skip_memory_sentence(sentence: str) -> bool:
    lowered = sentence.lower()
    if sentence.endswith("?") or len(sentence) < 12:
        return True
    return any(hint in lowered for hint in _EPHEMERAL_HINTS)


def clean_name(value: str) -> str:
    candidate = str(value or "").strip().strip("\"'.,!?")
    if not candidate:
        return ""
    for marker in (" and ", " because ", " but ", " please ", " for "):
        index = candidate.lower().find(marker)
        if index != -1:
            candidate = candidate[:index].strip()
    candidate = candidate[:48].strip()
    if not candidate or candidate.lower() in {"vool", "assistant", "you"}:
        return ""
    parts = [piece for piece in candidate.split() if piece]
    if not parts:
        return ""
    return " ".join(part[:1].upper() + part[1:] for part in parts[:3])


def heuristic_text(category: str, signal: str) -> str:
    mapping = {
        ("response_style", "concise_direct"): "The operator prefers concise, direct answers with low filler.",
        ("response_style", "brutal_honest"): "The operator prefers brutally honest answers with no fluff or copium.",
        ("source_preference", "official_docs"): "The operator wants official documentation prioritized over generic summaries.",
        ("source_preference", "github_repos"): "The operator wants strong GitHub repos used as implementation references.",
        ("source_preference", "reputable_sources"): "The operator wants reputable public sources instead of low-signal search noise.",
        ("preferred_stack", "python"): "The operator leans toward Python for implementation work.",
        ("preferred_stack", "typescript"): "The operator leans toward TypeScript for implementation work.",
        ("preferred_stack", "javascript"): "The operator leans toward JavaScript and Node-based implementation work.",
        ("preferred_stack", "rust"): "The operator is open to Rust for implementation work.",
        ("preferred_stack", "go"): "The operator is open to Go for implementation work.",
        ("project_focus", "telegram_bot"): "The operator repeatedly works on Telegram bot projects.",
        ("project_focus", "discord_bot"): "The operator repeatedly works on Discord bot projects.",
        ("project_focus", "hive_swarm"): "The operator actively cares about Hive and swarm coordination work.",
        ("project_focus", "openclaw_runtime"): "The operator repeatedly works on VOOL and OpenClaw runtime behavior.",
        ("autonomy_preference", "hands_off"): "The operator prefers low-friction execution with minimal micro-approvals.",
    }
    return mapping.get((category, signal), f"Observed operator heuristic: {category} {signal}.")


def dense_signal_name(row: dict[str, Any]) -> str:
    signal = str(row.get("signal") or "").strip().lower()
    mapping = {
        "concise_direct": "concise_direct",
        "brutal_honest": "brutal_honest",
        "official_docs": "official_docs_first",
        "github_repos": "github_references",
        "reputable_sources": "reputable_sources",
        "python": "python",
        "typescript": "typescript",
        "javascript": "javascript",
        "rust": "rust",
        "go": "go",
        "telegram_bot": "telegram_bot",
        "discord_bot": "discord_bot",
        "hive_swarm": "hive_swarm",
        "openclaw_runtime": "openclaw_runtime",
        "hands_off": "hands_off_execution",
    }
    return mapping.get(signal, signal)


def project_focus_label(signal: str) -> str:
    mapping = {
        "telegram_bot": "Telegram bot build",
        "discord_bot": "Discord bot build",
        "hive_swarm": "Hive/mesh work",
        "openclaw_runtime": "OpenClaw/VOOL runtime work",
    }
    return mapping.get(str(signal or "").strip().lower(), "")


def _auto_learn_shorthand(*, session_id: str, user_input: str) -> None:
    text = str(user_input or "").strip()
    if not text or len(text) < 10:
        return
    for pattern in _SHORTHAND_PATTERNS:
        match = pattern.search(text)
        if match:
            term = match.group(1).strip()
            canonical = match.group(2).strip()
            if term and canonical and len(term) <= 40 and len(canonical) <= 80:
                from core.human_input_adapter import learn_user_shorthand

                learn_user_shorthand(term, canonical, session_id=session_id)
                memory_entries.add_memory_fact(
                    f'Shorthand: "{term}" means "{canonical}".',
                    category="shorthand",
                    session_id=session_id,
                    source="auto_dialogue",
                    confidence=0.90,
                    keywords=memory_entries.keyword_tokens_filtered(f"{term} {canonical}"),
                )
                break


def _apply_feedback_to_heuristics(*, session_id: str) -> None:
    try:
        from storage.dialogue_memory import feedback_stats

        stats = feedback_stats(lookback_days=7)
        if stats["total"] < 3:
            return
        by_type = stats.get("by_type") or {}
        rejections = by_type.get("implicit_rejection") or {}
        approvals = by_type.get("implicit_approval") or {}
        total_feedback = int(rejections.get("count") or 0) + int(approvals.get("count") or 0)
        rejection_rate = (int(rejections.get("count") or 0) / total_feedback) if total_feedback > 0 else 0.0
        if rejection_rate > 0.6:
            rows = load_jsonl(user_heuristics_path())
            for row in rows:
                if str(row.get("category") or "") == "response_style":
                    old_conf = float(row.get("confidence") or 0.5)
                    row["confidence"] = round(max(0.3, old_conf - 0.05), 4)
            rewrite_jsonl(user_heuristics_path(), rows[:64])
    except Exception:
        pass


def _ensure_memory_files() -> None:
    ensure_memory_files(ensure_policy_table=ensure_session_policy_table)
