"""
L3 semantic memory layer for the agent turn loop.

Two-sided integration with the runtime:

  store_turn(session_id, user_text, assistant_text)
    Called after each completed turn. Embeds and persists important content
    to VoolMemory SQLite so it survives session boundaries. Filler turns
    (importance < 0.35) are skipped to keep the store signal-dense.

  inject_retrieved(session_id, query, transcript)
    Called before building the LLM prompt. Queries VoolMemory for *query*,
    filters out nodes already in the transcript (covering L2 summary and
    recent verbatim turns), and injects the remainder as a
    <retrieved_context> system message before the last user turn.

Both functions are best-effort: any error is swallowed silently so the
main response path is never interrupted. Semantic writes return redacted status
and emit diagnostic reasons without exposing content or filesystem paths.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from collections.abc import Mapping
from contextvars import ContextVar
from typing import Any

from core.context_capsule_v2 import ContextCandidate, estimate_tokens, pack_context, resolve_budget
from core.context_scope import ContextAccessPolicy
from core.embedding_service import embed
from core.local_ollama_inventory import env_flag_enabled


def _current_request_lineage() -> str:
    """A8 payload lineage: the A0 request id bound to the current dispatch, if
    any ('' on legacy/off-HTTP lanes — never fabricated)."""
    try:
        from core.semantic.semantic_admissions import current_request_id

        return str(current_request_id() or "")
    except Exception:
        return ""
from core.memory.entries import resolve_memory_access_policy
from core.vool_memory import DEFAULT_AGENT_ID, VoolMemory

LOGGER = logging.getLogger(__name__)

#: Aliased from the store so there is exactly one definition of the partition id.
SEMANTIC_MEMORY_AGENT_ID = DEFAULT_AGENT_ID
_AGENT_ID = SEMANTIC_MEMORY_AGENT_ID  # shared across sessions; session tags enforce isolation
_MIN_STORE_CHARS = 30         # short facts ("port is 5433") are still worth storing
_IMPORTANCE_THRESHOLD = 0.35  # skip pure filler turns
_RETRIEVAL_TOP_K = 4
_RETRIEVAL_MIN_SCORE = 0.42
_MAX_INJECT_TOKENS = 350
DEDUP_THRESHOLD = 0.60
DEDUP_MIN_SUBSTRING = 40
_VALUE_TOKEN_RE = re.compile(
    r"sk-[a-zA-Z0-9\-_]{6,}|\b\d{4}-\d{2}-\d{2}\b|\b\w*-\w*\d{2,}[\w-]*\b|\b\d{3,}\b",
    re.IGNORECASE,
)
# Context Capsule 2.0 (opt-in). Live recall still uses hybrid search, but the prompt injection is
# a distilled capsule, not a raw retrieved-wall dump. The packer sizes the distilled payload to the
# hardware budget and redacts secrets on the injection boundary.
_CAPSULE_V2_ENV = "VOOL_CONTEXT_CAPSULE_V2"
_V2_MAX_INJECT_ITEMS = 8
_CAPSULE_TARGET_TOKENS = 420
_CAPSULE_TELEMETRY: ContextVar[dict[str, object] | None] = ContextVar(
    "vool_context_capsule_telemetry",
    default=None,
)
_TELEMETRY_DEFAULTS: dict[str, object] = {
    "capsule_mode": "not_run",
    "raw_context_chars": 0,
    "retrieved_chars": 0,
    "distilled_chars": 0,
    "estimated_distilled_tokens": 0,
    "selected_facts": [],
    "selected_facts_count": 0,
    "dropped_noise_count": 0,
    "model_prompt_tokens": 0,
    "prompt_eval_count": 0,
    "web_calls": 0,
    "model_calls": 0,
    "memory_store_status": "not_run",
    "memory_store_count": 0,
    "memory_store_reason": "",
}


def _distinctive_value_tokens(content: str) -> set[str]:
    return {match.group(0).lower() for match in _VALUE_TOKEN_RE.finditer(str(content or ""))}


def _content_covered(content: str, context_text: str, threshold: float = DEDUP_THRESHOLD) -> bool:
    words = set(re.findall(r"\b[a-zA-Z0-9_\-]{4,}\b", content.lower()))
    if not words:
        return False
    covered = sum(1 for word in words if word in context_text)
    return (covered / len(words)) >= threshold


def _content_covered_substring(
    content: str,
    context_text: str,
    min_len: int = DEDUP_MIN_SUBSTRING,
) -> bool:
    lower = content.lower().strip()
    if not lower:
        return False
    check_len = min(len(lower), min_len)
    if check_len < 8:
        return False
    step = max(1, check_len // 4)
    return any(
        lower[start : start + check_len] in context_text
        for start in range(0, len(lower) - check_len + 1, step)
    )


# ── internal helpers ───────────────────────────────────────────────────────────


def _score_importance(content: str) -> float:
    score = 0.2
    signals = [
        # An explicit "remember this" request is the single most important thing to persist — the
        # user is literally telling us to. Previously this scored as filler (0.2 < 0.35) and was
        # dropped, so "remember the number 8932…" was never stored and could not be recalled later.
        (r"\b(remember|memoriz\w+|remind\s+me|note\s+(?:that|this)|keep\s+in\s+mind|"
         r"save\s+(?:this|that|it)\b|write\s+(?:this|that|it)\s+down|for\s+later|"
         r"don'?t\s+forget|do\s+not\s+forget)\b", 0.4),
        (r'\b\d{6,}\b', 0.25),                              # a long number/code stated (id, seed, phone, PIN, wallet idx…)
        (r"\bmy\s+[\w'-]+(?:\s+[\w'-]+){0,3}\s+is\b", 0.2), # "my X is Y" — a self-stated fact worth keeping
        (r'\b(password|secret|passphrase)\b', 0.4),
        (r'\bsk-[a-zA-Z0-9\-_]{6,}\b', 0.4),
        (r'\b(api[\s_-]?key|access[\s_-]?token)\b', 0.35),
        (r'\bport\b[\s:=]*(?:is|number|no\.?|set\s+to)?[\s:=]*\d{2,5}\b', 0.3),
        (r'\b\d{4}-\d{2}-\d{2}\b', 0.25),
        (r'\b(deadline|due\s*date|release\s*date|expires?)\b', 0.25),
        (r'\b(decided?|will\s+use|prefer[rs]?|must\b|required\b)\b', 0.2),
        (r'\b(error|exception|traceback|bug|broken|failed?)\b', 0.15),
    ]
    for pattern, weight in signals:
        if re.search(pattern, content, re.IGNORECASE):
            score = min(1.0, score + weight)
    return round(score, 2)


def _extract_keywords(text: str, max_kw: int = 8) -> list[str]:
    tokens = re.findall(r"\b[a-zA-Z0-9_\-\.]{3,}\b", text)
    seen: dict[str, int] = {}
    for tok in tokens:
        seen[tok.lower()] = seen.get(tok.lower(), 0) + 1
    return sorted(seen, key=lambda k: (-seen[k], -len(k)))[:max_kw]


def _open_memory(runtime_home: str | None = None) -> VoolMemory | None:
    try:
        return VoolMemory(runtime_home=runtime_home, agent_id=_AGENT_ID)
    except Exception:
        return None


def _open_memory_for_runtime(runtime_home: str | None) -> VoolMemory | None:
    """Open the selected runtime store while preserving the test seam for the default home."""
    if str(runtime_home or "").strip():
        return _open_memory(runtime_home=str(runtime_home).strip())
    return _open_memory()


def get_last_retrieval_telemetry() -> dict[str, object]:
    return {**_TELEMETRY_DEFAULTS, **dict(_CAPSULE_TELEMETRY.get() or {})}


def _set_retrieval_telemetry(payload: dict[str, object]) -> None:
    # Keep same-turn write diagnostics when retrieval later replaces the
    # capsule fields.  ``reset_retrieval_telemetry`` starts the next turn from
    # a clean record, so this does not carry memory status across turns.
    merged = {
        **_TELEMETRY_DEFAULTS,
        **dict(_CAPSULE_TELEMETRY.get() or {}),
        **dict(payload or {}),
    }
    selected = merged.get("selected_facts")
    if isinstance(selected, list):
        merged["selected_facts_count"] = len(selected)
    _CAPSULE_TELEMETRY.set(merged)


def reset_retrieval_telemetry() -> None:
    _CAPSULE_TELEMETRY.set(dict(_TELEMETRY_DEFAULTS))


def update_retrieval_telemetry(**fields: object) -> None:
    _set_retrieval_telemetry({**get_last_retrieval_telemetry(), **fields})


def _session_scope_key(session_id: str | None) -> str:
    raw = str(session_id or "").strip()
    if not raw:
        return ""
    return f"v2:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def resolve_semantic_access_policy(
    *,
    session_id: str | None,
    access_policy: ContextAccessPolicy | None = None,
    source_context: Mapping[str, Any] | None = None,
) -> ContextAccessPolicy:
    """Reload the persisted active policy for one semantic-memory operation.

    Reads and writes accept only an already-created namespace. Any supplied
    policy is treated as an identifier, not as authority: grants, project
    membership, and lifecycle state are reloaded from the server-owned store.
    """
    clean_session = str(session_id or "").strip()
    if not clean_session:
        raise ValueError("session_id is required for semantic context")

    context = dict(source_context or {})
    embedded_policy = context.get("_context_access_policy")
    if embedded_policy is not None:
        if not isinstance(embedded_policy, ContextAccessPolicy):
            raise TypeError(
                "_context_access_policy must be a ContextAccessPolicy"
            )
        if (
            access_policy is not None
            and embedded_policy.chat_id != access_policy.chat_id
        ):
            raise ValueError("conflicting context access policies")
        if access_policy is None:
            access_policy = embedded_policy

    context_chat = str(context.get("chat_id") or "").strip()
    if context_chat and context_chat != clean_session:
        raise ValueError("source_context chat_id does not match session_id")

    persisted = resolve_memory_access_policy(
        access_policy=access_policy,
        chat_id=clean_session,
    )
    trusted_project = str(
        context.get("_trusted_project_id") or ""
    ).strip()
    if trusted_project and trusted_project != persisted.project_id:
        raise ValueError(
            "source_context project does not match persisted namespace"
        )
    return persisted


def _node_session_scope_keys(node: object) -> set[str]:
    keys = {
        str(tag).removeprefix("session:").strip()
        for tag in list(getattr(node, "tags", []) or [])
        if str(tag).startswith("session:") and str(tag).removeprefix("session:").strip()
    }
    description = str(getattr(node, "context_description", "") or "")
    match = re.search(r"\bsession=([^\s]+)", description)
    if match:
        keys.add(match.group(1).strip())
    return keys


_CAPSULE_ADVISORY_RE = re.compile(
    r"\b(?:"
    r"how\s+much\b.{0,40}\bshould\s+i\s+budget|"
    r"what\b.{0,24}\bcap\s+should\s+i\s+choose|"
    r"(?:what|which)\b.{0,48}\bshould\s+i\s+(?:choose|use|pick)|"
    r"should\s+i\s+spend\b|"
    r"help\s+me\s+(?:plan|choose|budget)|"
    r"(?:recommend|suggest|advise)\b.{0,40}\b(?:budget|cap|spend)"
    r")",
    re.IGNORECASE,
)
_CAPSULE_REMOTE_CLAIM_RE = re.compile(
    r"(?:https?://|www\.|\baccording\s+to\b|\bsearched\s+the\s+web\b|\bsource\s*:)",
    re.IGNORECASE,
)
_CAPSULE_MONEY_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:SOL|USDC)\b", re.IGNORECASE)
_CAPSULE_DOMAIN_RE = re.compile(r"\b[a-z0-9][a-z0-9_.\-]*\.null\b", re.IGNORECASE)


def _capsule_recall_kind(query: str) -> str:
    text = " ".join(str(query or "").lower().split())
    if not text or _CAPSULE_ADVISORY_RE.search(text):
        return ""
    if re.search(r"\bwhat\s+(?:project\s+)?domain\s+did\s+i\s+(?:ask\s+for|choose|request|say)\b", text):
        return "domain"
    if "mission" in text and re.search(r"\b(?:summari[sz]e|recall|recap|state|describe|current|active|what)\b", text):
        return "mission"
    if "code" in text and (
        re.search(r"\b(?:what|which)\b.{0,48}\bcode\b", text)
        or re.search(r"\b(?:exact|recall|remember|remind)\b.{0,36}\bcode\b", text)
    ):
        return "code"
    if re.search(
        r"\bwhat(?:'s| is)\s+(?:my|the\s+(?:current|exact|latest))\s+"
        r"(?:(?:launch|spend)\s+){0,2}cap\b",
        text,
    ):
        return "cap"
    if "region" in text and re.search(r"\b(?:what|which)\b.{0,48}\b(?:current|correct|exact|region)\b", text):
        return "region"
    if re.search(r"\b(?:what|which)\b.{0,48}\b(?:id|identifier)\b", text) and any(
        marker in text for marker in ("remember", "stored", "operator", "node", "exact")
    ):
        return "identifier"
    if text.startswith(("what is ", "what's ", "which ")) and "?" in str(query or ""):
        return "absent"
    return ""


def _active_mission_slots_for_session(
    session_id: str | None,
    supplied_slots: list[dict[str, object]] | None,
) -> list[dict[str, object]]:
    if supplied_slots is not None:
        return [dict(slot) for slot in supplied_slots]
    if not str(session_id or "").strip():
        return []
    try:
        from core.active_mission import current_active_mission_slots

        return [dict(slot) for slot in current_active_mission_slots(str(session_id))]
    except Exception:
        return []


def _active_mission_values(slots: list[dict[str, object]]) -> dict[str, str]:
    values: dict[str, str] = {}
    for slot in slots:
        name = str(slot.get("slot_name") or "")
        value = str(slot.get("value") if slot.get("value") is not None else slot.get("value_raw") or "").strip()
        if name and value:
            values.setdefault(name, value)
    return values


def _capsule_source_matches_session(data: Mapping[str, object], session_id: str | None) -> bool:
    current = _session_scope_key(session_id)
    sources = {
        str(item or "").strip()
        for item in list(data.get("source_session_ids") or [])
        if str(item or "").strip()
    }
    return bool(current and sources == {current})


def _capsule_retrieval_requires_current_session(query: str) -> bool:
    recall_kind = _capsule_recall_kind(query)
    return bool(recall_kind and recall_kind != "absent")


def exact_recall_requires_current_session(query: str) -> bool:
    return _capsule_retrieval_requires_current_session(query)


def _node_has_only_current_session_source(node: object, session_id: str | None) -> bool:
    current = _session_scope_key(session_id)
    sources = _node_session_scope_keys(node)
    return bool(current and sources == {current})


def _forbidden_terms_for_session(session_id: str | None) -> list[str]:
    slots = _active_mission_slots_for_session(session_id, None)
    values = _active_mission_values(slots)
    return [
        value.lower()
        for name, value in values.items()
        if name.startswith("forbidden_term:") and value
    ]


def _filter_forbidden_selected_facts(lines: list[str], *, session_id: str | None) -> tuple[list[str], int]:
    forbidden = _forbidden_terms_for_session(session_id)
    if not forbidden:
        return lines, 0
    filtered = [line for line in lines if not any(term in str(line or "").lower() for term in forbidden)]
    return filtered, len(lines) - len(filtered)


def _mission_fact_value(mission: str, label: str) -> str:
    for part in str(mission or "").split(";"):
        clean = part.strip()
        if clean.lower().startswith(f"{label.lower()} "):
            return clean[len(label) :].strip()
    return ""


def _normalized_exact_value(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _response_conflicts_with_active_mission(response: str, slots: list[dict[str, object]]) -> bool:
    values = _active_mission_values(slots)
    cap = values.get("spend_cap", "")
    if cap and any(
        _normalized_exact_value(token) != _normalized_exact_value(cap)
        for token in _CAPSULE_MONEY_RE.findall(response)
    ):
        return True
    domain = values.get("active_domain", "")
    return bool(domain) and any(
        _normalized_exact_value(token) != _normalized_exact_value(domain)
        for token in _CAPSULE_DOMAIN_RE.findall(response)
    )


_EXACT_RESPONSE_SHAPE_RE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9 _./&()'\-:]{2,119}"
)


def _same_exact_value(left: str, right: str) -> bool:
    return " ".join(str(left or "").split()) == " ".join(str(right or "").split())


def validate_capsule_exact_response(
    response: str,
    query: str,
    *,
    session_id: str | None,
    active_mission_slots: list[dict[str, object]] | None = None,
    expected_value: str | None = None,
) -> bool:
    text = str(response or "").strip()
    recall_kind = _capsule_recall_kind(query)
    if not text or not recall_kind or _CAPSULE_REMOTE_CLAIM_RE.search(text):
        return False
    slots = _active_mission_slots_for_session(session_id, active_mission_slots)
    values = _active_mission_values(slots)
    forbidden = [
        value.lower()
        for name, value in values.items()
        if name.startswith("forbidden_term:") and value
    ]
    if any(term in text.lower() for term in forbidden):
        return False
    if _response_conflicts_with_active_mission(text, slots):
        return False
    if recall_kind in {"code", "identifier"}:
        if expected_value is not None and not _same_exact_value(text, expected_value):
            return False
        return bool(_EXACT_RESPONSE_SHAPE_RE.fullmatch(text))
    if recall_kind == "cap":
        return bool(_CAPSULE_MONEY_RE.search(text)) and len(text) <= 120
    if recall_kind == "region":
        return bool(re.fullmatch(r"[a-z]{2}-[a-z]+-\d+", text, flags=re.IGNORECASE))
    if recall_kind == "domain":
        return bool(_CAPSULE_DOMAIN_RE.search(text)) and len(text) <= 120
    if recall_kind == "mission":
        return text.startswith("Current mission:") and len(text) <= 500
    return "not present in the supplied context" in text.lower() and len(text) <= 200


def capsule_exact_response(
    query: str,
    telemetry: Mapping[str, object] | None = None,
    *,
    session_id: str | None = None,
    active_mission_slots: list[dict[str, object]] | None = None,
) -> str:
    data = dict(telemetry or get_last_retrieval_telemetry())
    if data.get("capsule_mode") != "distilled":
        return ""
    lines = [
        str(line or "").strip()
        for line in list(data.get("selected_facts") or [])
        if str(line or "").strip()
    ]
    query_l = " ".join(str(query or "").lower().split())
    recall_kind = _capsule_recall_kind(query)
    if not query_l or not lines or not recall_kind:
        return ""

    facts: dict[str, str] = {}
    for line in lines:
        clean = line.removeprefix("- ").strip()
        if ":" not in clean:
            continue
        label, value = clean.split(":", 1)
        facts[label.strip().lower()] = value.strip()

    slots = _active_mission_slots_for_session(session_id, active_mission_slots)
    active_values = _active_mission_values(slots)
    response = ""
    expected_value: str | None = None
    if recall_kind == "cap" and active_values.get("spend_cap"):
        response = active_values["spend_cap"]
    elif recall_kind == "domain" and active_values.get("active_domain"):
        response = active_values["active_domain"]
    elif recall_kind == "mission" and slots:
        try:
            from core.active_mission import render_mission_answer

            response = render_mission_answer(slots, honor_forbidden=True)
        except Exception:
            response = ""

    cap = facts.get("latest spend cap", "")
    region = facts.get("current region", "")
    node_id = facts.get("recalled identifier", "")
    mission = facts.get("active mission", "")
    exact_code = facts.get("exact code", "")
    if not response and _capsule_source_matches_session(data, session_id):
        if recall_kind == "code" and exact_code:
            response = exact_code
            expected_value = exact_code
        elif recall_kind == "cap" and cap:
            response = cap
        elif recall_kind == "region" and region:
            response = region
        elif recall_kind == "identifier" and node_id:
            response = node_id
            expected_value = node_id
        elif recall_kind == "domain" and mission:
            response = _mission_fact_value(mission, "domain")
        elif recall_kind == "mission" and mission:
            response = f"Current mission: {mission}."

    if not response and recall_kind == "absent" and _capsule_source_matches_session(data, session_id):
        for label, value in facts.items():
            if "not present in the supplied context" in value.lower():
                subject = label.removeprefix("answer ").strip()
                if subject and all(term in query_l for term in subject.split()):
                    response = f"The {subject} is not present in the supplied context."
                    break
        if not response:
            for line in lines:
                match = re.search(
                    r"\brelevant context:\s+the supplied context does not define any ([^.]+)",
                    line,
                    re.IGNORECASE,
                )
                if match:
                    subject = match.group(1).strip()
                    if all(term in query_l for term in subject.lower().split()):
                        response = f"The {subject} is not present in the supplied context."
                        break
    return (
        response
        if validate_capsule_exact_response(
            response,
            query,
            session_id=session_id,
            active_mission_slots=slots,
            expected_value=expected_value,
        )
        else ""
    )


def is_capsule_exact_recall_query(query: str) -> bool:
    return bool(_capsule_recall_kind(query))


def _interesting_windows(text: str, pattern: re.Pattern[str], *, radius: int = 140) -> list[str]:
    windows: list[str] = []
    for match in pattern.finditer(text):
        start = max(0, match.start() - radius)
        end = min(len(text), match.end() + radius)
        chunk = " ".join(text[start:end].split()).strip(" ,.;:-")
        if chunk and chunk not in windows:
            windows.append(chunk)
    return windows


def _last_match(pattern: str, text: str) -> str:
    matches = list(re.finditer(pattern, text, flags=re.IGNORECASE))
    if not matches:
        return ""
    return matches[-1].group(1).strip().rstrip(".,;")


_EXACT_VALUE_LABEL_RE = re.compile(
    r"\b(?:stored|remember(?:ed)?|memory)\b"
    r"[^.!?\n]{0,100}?\b(?:value|identifier|id|code)\b"
    r"\s*(?:is|=|:)?\s*"
    r"(?P<value>[^.!?\n]+?)"
    r"(?=\s*(?:[.!?]|$))",
    re.IGNORECASE,
)
_EXACT_VALUE_DIRECT_RE = re.compile(
    r"\b(?:stored|remembered)\s+"
    r"(?P<value>[A-Za-z0-9][^.!?\n]*)"
    r"(?=\s*(?:[.!?]|$))",
    re.IGNORECASE,
)


def _clean_exact_value(value: str) -> str:
    clean = " ".join(str(value or "").split()).strip(" \t,;:")
    # A declaration often continues with an explanation after the value. Keep the
    # declared value, but do not mistake the explanation for part of it.
    clean = re.split(
        r"\s+(?:and|but|because|so|for\s+(?:later|reference))\b",
        clean,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip(" \t,;:")
    return clean


def _exact_recalled_value(
    text: str,
    *,
    labels: tuple[str, ...] | None = None,
) -> str:
    label_re = _EXACT_VALUE_LABEL_RE
    if labels:
        choices = "|".join(re.escape(label) for label in labels)
        label_re = re.compile(
            r"\b(?:stored|remember(?:ed)?|memory)\b"
            rf"[^.!?\n]{{0,100}}?\b(?:{choices})\b"
            r"\s*(?:is|=|:)?\s*"
            r"(?P<value>[^.!?\n]+?)"
            r"(?=\s*(?:[.!?]|$))",
            re.IGNORECASE,
        )
    labelled = list(label_re.finditer(str(text or "")))
    if labelled:
        candidate = _clean_exact_value(labelled[-1].group("value"))
        if candidate:
            return candidate
    if not labels:
        direct = list(_EXACT_VALUE_DIRECT_RE.finditer(str(text or "")))
        if direct:
            candidate = _clean_exact_value(direct[-1].group("value"))
            if candidate:
                return candidate
    return ""


def _latest_sol_value(text: str) -> str:
    explicit_latest = list(re.finditer(
        r"\b(?:latest|current|correct(?:ed|ion)?|update(?:d)?|now|raise|changed?|supersed)[^.:\n]{0,90}?"
        r"(?:cap|spend|budget|max)[^.:\n]{0,40}?(\d+(?:\.\d+)?\s*SOL)\b",
        text,
        flags=re.IGNORECASE,
    ))
    if explicit_latest:
        return explicit_latest[-1].group(1).strip()
    explicit_now = list(re.finditer(
        r"\b(?:cap|spend|budget|max)[^.:\n]{0,40}?"
        r"(?:is|to|now|raised? to|changed? to)[^.:\n]{0,30}?(\d+(?:\.\d+)?\s*SOL)\b",
        text,
        flags=re.IGNORECASE,
    ))
    if explicit_now:
        return explicit_now[-1].group(1).strip()
    matches = list(re.finditer(r"\b(\d+(?:\.\d+)?\s*SOL)\b", text, flags=re.IGNORECASE))
    if not matches:
        return ""
    update_markers = re.compile(r"\b(latest|current|correct(?:ed|ion)?|update(?:d)?|now|raise|changed?|supersed)\b", re.IGNORECASE)
    marked: list[re.Match[str]] = []
    for match in matches:
        window = text[max(0, match.start() - 90):min(len(text), match.end() + 90)]
        if update_markers.search(window):
            marked.append(match)
    return (marked[-1] if marked else matches[-1]).group(1).strip()


def _latest_region(text: str) -> str:
    matches = list(re.finditer(r"\b([a-z]{2}-[a-z]+-\d+)\b", text, flags=re.IGNORECASE))
    if not matches:
        return ""
    correction_markers = re.compile(r"\b(correct(?:ed|ion)?|latest|current|now|supersed|update(?:d)?)\b", re.IGNORECASE)
    marked: list[re.Match[str]] = []
    for match in matches:
        window = text[max(0, match.start() - 100):min(len(text), match.end() + 100)]
        if correction_markers.search(window):
            marked.append(match)
    return (marked[-1] if marked else matches[-1]).group(1).strip()


def _known_fact_lines(query: str, text: str) -> list[str]:
    query_l = str(query or "").lower()
    lines: list[str] = []

    if "code" in query_l:
        code = _exact_recalled_value(text, labels=("code",)) or _last_match(
            r"\b(?:[a-z][a-z0-9_-]*\s+){0,4}code\s*"
            r"(?:(?:is|=|:)\s*)?((?!is\b)[A-Z0-9][A-Z0-9_-]{2,63})\b",
            text,
        )
        if code:
            lines.append(f"- exact code: {code}")
    if "code" in query_l and not lines:
        generic_codes = list(dict.fromkeys(re.findall(r"\b[A-Z][A-Z0-9]*-[A-Z0-9][A-Z0-9_-]{2,63}\b", text)))
        if generic_codes:
            lines.append(f"- exact code: {generic_codes[-1]}")

    region = _latest_region(text)
    if region and any(term in query_l for term in ("region", "contradiction", "correct", "deployment")):
        lines.append(f"- current region: {region}")

    sol = _latest_sol_value(text)
    if sol and any(term in query_l for term in ("cap", "sol", "mission", "latest", "spend")):
        lines.append(f"- latest spend cap: {sol}")

    domain = _last_match(r"\b([a-z0-9][a-z0-9_.\-]*\.null)\b", text)
    wallet = _last_match(r"\bwallet\s+(?:prefix|address|starting|start)?\s*(?:is\s+|starting\s+)?([1-9A-HJ-NP-Za-km-z]{4,12})\b", text)
    os_name = _last_match(r"\b(Windows(?:\s*\d+)?|macOS|Mac\b|Linux|Ubuntu|Debian)\b", text)
    auto_spend = bool(re.search(r"\b(never\s+auto[\s-]*spend|do\s+not\s+auto[\s-]*spend|no\s+auto[\s-]*spend)\b", text, re.IGNORECASE))
    if any(term in query_l for term in ("mission", "wallet", "domain", "operating", "windows", "forbidden")):
        mission_parts: list[str] = []
        if sol:
            mission_parts.append(f"cap {sol}")
        if domain:
            mission_parts.append(f"domain {domain}")
        if wallet and wallet.lower() not in {"prefix", "wallet", "address", "index", "id", "identifier"}:
            mission_parts.append(f"wallet prefix {wallet}")
        if os_name:
            mission_parts.append(f"target OS {os_name}")
        if auto_spend:
            mission_parts.append("rule never auto-spend")
        if mission_parts:
            lines.append("- active mission: " + "; ".join(mission_parts))

    stored = _exact_recalled_value(text)
    if not stored:
        stored = _last_match(
            r"\b(?:stored|remember(?:ed)?|memory|"
            r"(?:wallet|operator(?:\s+node)?|node)\s+(?:id|identifier|index)|"
            r"(?:id|identifier|index))\D{0,40}?(\d{5,})\b",
            text,
        )
    if not stored:
        stored = _last_match(r"\b(\d{7,})\b", text)
    if stored and any(
        term in query_l
        for term in ("memory", "stored", "operator", "node", "wallet", "id", "identifier", "index")
    ):
        lines.append(f"- recalled identifier: {stored}")

    unknown_markers = (
        "not present", "not provided", "not specified", "does not define",
        "no launch gate color", "missing from context",
    )
    if any(term in query_l for term in ("color", "unknown", "irrelevant", "noise")) and any(marker in text.lower() for marker in unknown_markers):
        lines.append("- answer launch gate color: not present in the supplied context; do not invent a color")

    return list(dict.fromkeys(lines))


def _query_overlap_lines(query: str, text: str, *, limit: int = 4) -> list[str]:
    query_terms = {
        tok.lower()
        for tok in re.findall(r"\b[a-zA-Z0-9_.\-]{4,}\b", query)
        if tok.lower() not in {"what", "which", "current", "latest", "answer", "context", "please"}
    }
    if not query_terms:
        return []
    chunks = re.split(r"(?<=[.!?])\s+|\n+", text)
    scored: list[tuple[int, int, str]] = []
    for idx, chunk in enumerate(chunks):
        clean = " ".join(chunk.split()).strip()
        if not clean or len(clean) > 500:
            continue
        hits = sum(1 for term in query_terms if term in clean.lower())
        if hits:
            scored.append((hits, -idx, clean[:360]))
    scored.sort(reverse=True)
    return [f"- relevant context: {chunk}" for _hits, _neg_idx, chunk in scored[:limit]]


def _distill_retrieved_hits(query: str, selected: list[tuple[str, float]]) -> tuple[str, dict[str, object]]:
    raw_context = "\n\n".join(content for content, _score in selected)
    selected_lines = _known_fact_lines(query, raw_context)
    if not selected_lines:
        selected_lines.extend(_query_overlap_lines(query, raw_context))
    if not selected_lines:
        id_windows = []
        id_windows.extend(
            _interesting_windows(
                raw_context,
                re.compile(r"\b[A-Z][A-Z0-9]*-[A-Z0-9][A-Z0-9_-]{2,63}\b", re.IGNORECASE),
            )
        )
        id_windows.extend(_interesting_windows(raw_context, re.compile(r"\b\d+(?:\.\d+)?\s*SOL\b", re.IGNORECASE)))
        id_windows.extend(_interesting_windows(raw_context, re.compile(r"\b[a-z0-9][a-z0-9_.\-]*\.null\b", re.IGNORECASE)))
        id_windows.extend(_interesting_windows(raw_context, re.compile(r"\b\d{7,}\b", re.IGNORECASE)))
        selected_lines.extend(f"- exact local fact: {line}" for line in id_windows[:6])
    if not selected_lines:
        for content, _score in selected[:4]:
            snippet = " ".join(str(content or "").split()).strip()
            if snippet:
                selected_lines.append(f"- retrieved fact: {snippet[:360]}")

    budget_chars = _CAPSULE_TARGET_TOKENS * 4
    distilled_lines: list[str] = []
    used = 0
    for line in list(dict.fromkeys(selected_lines)):
        line = " ".join(str(line or "").split()).strip()
        if not line:
            continue
        if used + len(line) + 1 > budget_chars:
            continue
        distilled_lines.append(line)
        used += len(line) + 1

    distilled = "\n".join(distilled_lines).strip()
    if distilled:
        distilled = "Distilled local facts. Answer from these exact facts only.\n" + distilled
    raw_chars = len(raw_context)
    telemetry = {
        "raw_context_chars": raw_chars,
        "retrieved_chars": raw_chars,
        "distilled_chars": len(distilled),
        "estimated_distilled_tokens": estimate_tokens(distilled),
        "selected_facts": distilled_lines,
        "dropped_noise_count": max(0, estimate_tokens(raw_context) - estimate_tokens(distilled)),
        "model_prompt_tokens": None,
        "capsule_mode": "distilled" if distilled else "empty",
        "web_calls": 0,
        "model_calls": 0,
    }
    return distilled, telemetry


# ── public API ─────────────────────────────────────────────────────────────────


def store_turn(
    session_id: str | None,
    user_text: str,
    assistant_text: str,
    *,
    access_policy: ContextAccessPolicy | None = None,
    source_context: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    """
    Embed and store high-importance content from this turn to VoolMemory.
    Low-importance (filler) turns are skipped to keep the store signal-dense.
    Best-effort for the response path, but returns a redacted status so the
    finalized-turn writer and diagnostics can distinguish stored, skipped, and
    rejected writes without exposing content or filesystem paths.
    """
    result: dict[str, object] = {
        "status": "skipped",
        "stored_count": 0,
        "reason": "no_eligible_content",
    }
    mem: VoolMemory | None = None
    try:
        from core.secret_redaction import redact_secrets

        policy = resolve_semantic_access_policy(
            session_id=session_id,
            access_policy=access_policy,
            source_context=source_context,
        )
        runtime_home = str((source_context or {}).get("runtime_home") or "").strip() or None
        mem = _open_memory_for_runtime(runtime_home)
        if mem is None:
            result.update({"status": "failed", "reason": "memory_open_failed"})
            update_retrieval_telemetry(
                memory_store_status=result["status"],
                memory_store_count=0,
                memory_store_reason=result["reason"],
            )
            return result
        sid = _session_scope_key(policy.chat_id)
        if not sid:
            result.update({"status": "failed", "reason": "missing_chat_scope"})
            update_retrieval_telemetry(
                memory_store_status=result["status"],
                memory_store_count=0,
                memory_store_reason=result["reason"],
            )
            return result
        # The transcript already preserves assistant turns. Only direct user
        # statements are eligible for semantic recall; storing generated text
        # would let an unsupported assistant guess reinforce itself later.
        del assistant_text
        for role, text in (("user", user_text),):
            # Redact high-confidence secrets (API keys, private keys, seed phrases) BEFORE the
            # turn is embedded and persisted to VoolMemory. This is a parallel store to the
            # conversation log (which already redacts), so without this a pasted key would land
            # in plaintext in the semantic memory + FTS index. Match the conversation-log path.
            content = redact_secrets(str(text or "")).strip()
            if len(content) < _MIN_STORE_CHARS:
                continue
            importance = _score_importance(content)
            if importance < _IMPORTANCE_THRESHOLD:
                continue
            vec = embed(content)
            mem.node_store(
                content=content,
                keywords=_extract_keywords(content),
                tags=[
                    role,
                    "scope:chat",
                    "authority:confirmed_memory",
                    "status:active",
                    f"session:{sid}",
                    *(
                        [f"project:{policy.project_id}"]
                        if policy.project_id
                        else []
                    ),
                    f"importance:{importance:.1f}",
                ],
                context_description=(
                    f"session={sid} role={role} scope=chat "
                    f"project={policy.project_id or 'none'} "
                    "authority=confirmed_memory status=active"
                ),
                embedding=vec,
                lineage_request_id=_current_request_lineage(),
            )
            result["stored_count"] = int(result["stored_count"]) + 1
        if int(result["stored_count"]) > 0:
            result.update({"status": "stored", "reason": "eligible_user_content"})
        update_retrieval_telemetry(
            memory_store_status=result["status"],
            memory_store_count=result["stored_count"],
            memory_store_reason=result["reason"],
        )
        return result
    except (TypeError, ValueError):
        result.update({"status": "failed", "reason": "invalid_context_policy"})
        update_retrieval_telemetry(
            memory_store_status=result["status"],
            memory_store_count=0,
            memory_store_reason=result["reason"],
        )
        return result
    except Exception as exc:
        result.update({"status": "failed", "reason": "write_error"})
        LOGGER.warning("semantic memory write failed: %s", type(exc).__name__)
        update_retrieval_telemetry(
            memory_store_status=result["status"],
            memory_store_count=0,
            memory_store_reason=result["reason"],
        )
        return result
    finally:
        if mem is not None:
            try:
                mem.close()
            except Exception as exc:
                LOGGER.warning("semantic memory close failed: %s", type(exc).__name__)


def forget_session_memory(
    session_id: str | None,
    token: str,
    *,
    access_policy: ContextAccessPolicy | None = None,
    source_context: Mapping[str, Any] | None = None,
) -> int:
    """Invalidate semantic-memory nodes containing ``token`` in one chat.

    Structured memory and semantic memory are separate persistence layers. A
    forget command must retire both, otherwise the semantic index can recall a
    value that the structured ledger already removed.
    """
    mem: VoolMemory | None = None
    try:
        policy = resolve_semantic_access_policy(
            session_id=session_id,
            access_policy=access_policy,
            source_context=source_context,
        )
        runtime_home = str((source_context or {}).get("runtime_home") or "").strip() or None
        mem = _open_memory_for_runtime(runtime_home)
        if mem is None:
            return 0
        return mem.node_invalidate_matching(
            token,
            session_id=policy.chat_id,
        )
    except (TypeError, ValueError):
        return 0
    except Exception as exc:
        LOGGER.warning("semantic memory forget failed: %s", type(exc).__name__)
        return 0
    finally:
        if mem is not None:
            try:
                mem.close()
            except Exception as exc:
                LOGGER.warning("semantic memory forget close failed: %s", type(exc).__name__)


def inject_retrieved(
    session_id: str | None,
    query: str,
    transcript: list[dict[str, str]],
    *,
    access_policy: ContextAccessPolicy | None = None,
    source_context: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
    budget=None,
) -> list[dict[str, str]]:
    """
    Query VoolMemory for *query*, filter nodes already covered by *transcript*,
    and inject the remainder before the last user turn.

    Default: semantic-only recall, 350-token injection cap (byte-identical to before). With
    VOOL_CONTEXT_CAPSULE_V2=1 the Context Capsule 2.0 booster runs instead — hybrid
    (semantic + BM25 + recency) recall, a hardware-sized injection budget, and secret redaction
    on the injected content. Best-effort: any error returns the transcript unchanged.
    """
    if not str(query or "").strip():
        _set_retrieval_telemetry({"capsule_mode": "skipped_empty_query", "web_calls": 0, "model_calls": 0})
        return transcript
    if not str(session_id or "").strip():
        _set_retrieval_telemetry(
            {
                "capsule_mode": "skipped_missing_chat_namespace",
                "web_calls": 0,
                "model_calls": 0,
            }
        )
        return transcript

    try:
        policy = resolve_semantic_access_policy(
            session_id=session_id,
            access_policy=access_policy,
            source_context=source_context,
        )
    except (TypeError, ValueError):
        _set_retrieval_telemetry(
            {
                "capsule_mode": "skipped_invalid_context_policy",
                "web_calls": 0,
                "model_calls": 0,
            }
        )
        return transcript
    resolved_session_id = policy.chat_id
    env_map = os.environ if env is None else env
    if not env_flag_enabled(env_map, _CAPSULE_V2_ENV, default=False):
        _set_retrieval_telemetry({"capsule_mode": "disabled", "web_calls": 0, "model_calls": 0})
        return _legacy_inject_retrieved(
            resolved_session_id,
            query,
            transcript,
            runtime_home=str((source_context or {}).get("runtime_home") or "").strip() or None,
        )
    try:
        return _capsule_v2_inject_retrieved(
            resolved_session_id,
            query,
            transcript,
            budget=budget,
            runtime_home=str((source_context or {}).get("runtime_home") or "").strip() or None,
        )
    except Exception:
        _set_retrieval_telemetry({"capsule_mode": "error", "web_calls": 0, "model_calls": 0})
        return transcript


def _legacy_inject_retrieved(
    session_id: str | None,
    query: str,
    transcript: list[dict[str, str]],
    *,
    runtime_home: str | None = None,
) -> list[dict[str, str]]:
    from core.secret_redaction import redact_secrets

    try:
        mem = _open_memory_for_runtime(runtime_home)
        if mem is None:
            return transcript
        q_vec = embed(query)
        hits = _session_scoped_node_search(
            mem,
            q_vec,
            session_id=session_id,
            top_k=_RETRIEVAL_TOP_K * 3,
            min_score=_RETRIEVAL_MIN_SCORE,
        )
        mem.close()
    except Exception:
        return transcript
    if not hits:
        _set_retrieval_telemetry({"capsule_mode": "disabled_no_hits", "web_calls": 0, "model_calls": 0})
        return transcript
    context_text = " ".join(m.get("content", "") for m in transcript).lower()
    budget_chars = _MAX_INJECT_TOKENS * 4
    selected: list[tuple[str, float]] = []
    for node, score in hits:
        carries_unseen_value = any(
            tok not in context_text for tok in _distinctive_value_tokens(node.content)
        )
        if not carries_unseen_value:
            if _content_covered(node.content, context_text, DEDUP_THRESHOLD):
                continue
            if _content_covered_substring(node.content, context_text, DEDUP_MIN_SUBSTRING):
                continue
        clean = redact_secrets(str(node.content or "")).strip()
        if not clean:
            continue
        selected.append((clean, score))
        budget_chars -= len(clean)
        if budget_chars <= 0 or len(selected) >= _RETRIEVAL_TOP_K:
            break
    _set_retrieval_telemetry(
        {
            "capsule_mode": "disabled",
            "raw_context_chars": sum(len(content) for content, _score in selected),
            "retrieved_chars": sum(len(content) for content, _score in selected),
            "distilled_chars": None,
            "estimated_distilled_tokens": None,
            "selected_facts": [],
            "dropped_noise_count": 0,
            "model_prompt_tokens": None,
            "web_calls": 0,
            "model_calls": 0,
        }
    )
    return _inject_retrieval_block(transcript, selected)


def _capsule_v2_inject_retrieved(
    session_id: str | None,
    query: str,
    transcript: list[dict[str, str]],
    *,
    budget=None,
    runtime_home: str | None = None,
) -> list[dict[str, str]]:
    # Hardware-sized injection budget (default bucket B when the caller doesn't supply one).
    if budget is None:
        budget = resolve_budget(bucket="B", role="general")
    try:  # lazy import avoids any import-time cycle; falls back to a no-op redactor.
        from core.local_inference_autopilot import _sanitize_text as sanitize
    except Exception:
        def sanitize(text):
            return text, 0
    try:
        mem = _open_memory_for_runtime(runtime_home)
        if mem is None:
            return transcript
        q_vec = embed(query)
        exact_recall_session_filter = _capsule_retrieval_requires_current_session(query)
        retrieval_top_k = 64 if exact_recall_session_filter else _RETRIEVAL_TOP_K * 3
        hybrid = getattr(mem, "node_search_hybrid", None)
        if callable(hybrid):
            # Hybrid = semantic + BM25(FTS5) + recency. BM25 catches exact IDs/ports/filenames the
            # small model's embeddings miss; recency supersedes stale facts. THE booster edge.
            hits = hybrid(
                query,
                q_vec,
                top_k=retrieval_top_k,
                min_score=budget.min_score,
                session_id=session_id,
            )
        else:
            hits = _session_scoped_node_search(
                mem,
                q_vec,
                session_id=session_id,
                top_k=retrieval_top_k,
                min_score=budget.min_score,
            )
        mem.close()
    except Exception:
        return transcript
    if not hits:
        _set_retrieval_telemetry({"capsule_mode": "no_hits", "web_calls": 0, "model_calls": 0})
        return transcript
    context_text = " ".join(m.get("content", "") for m in transcript).lower()
    selected: list[tuple[str, float]] = []
    source_session_ids: set[str] = set()
    dropped_foreign_session_count = 0
    dropped_no_provenance_count = 0
    for node, score in hits:
        carries_unseen_value = any(
            tok not in context_text for tok in _distinctive_value_tokens(node.content)
        )
        if not carries_unseen_value:
            if _content_covered(node.content, context_text, DEDUP_THRESHOLD):
                continue
            if _content_covered_substring(node.content, context_text, DEDUP_MIN_SUBSTRING):
                continue
        node_source_ids = _node_session_scope_keys(node)
        if not node_source_ids:
            dropped_no_provenance_count += 1
            continue
        if not _node_has_only_current_session_source(node, session_id):
            dropped_foreign_session_count += 1
            continue
        clean, _n = sanitize(node.content)  # redact secrets/paths on the injection boundary
        clean = str(clean or "").strip()
        if not clean:
            continue
        selected.append((clean, score))
        source_session_ids.update(node_source_ids)
        if len(selected) >= _V2_MAX_INJECT_ITEMS:
            break
    distilled, telemetry = _distill_retrieved_hits(query, selected)
    dropped_forbidden_fact_count = 0
    if exact_recall_session_filter:
        filtered_facts, dropped_forbidden_fact_count = _filter_forbidden_selected_facts(
            list(telemetry.get("selected_facts") or []),
            session_id=session_id,
        )
        if dropped_forbidden_fact_count:
            telemetry["selected_facts"] = filtered_facts
            distilled = "\n".join(filtered_facts).strip()
            distilled = f"Distilled local facts. Answer from these exact facts only.\n{distilled}" if distilled else ""
            telemetry["distilled_chars"] = len(distilled)
            telemetry["estimated_distilled_tokens"] = estimate_tokens(distilled)
            telemetry["capsule_mode"] = "distilled" if distilled else "empty"
    telemetry.update(
        {
            "source_session_ids": sorted(source_session_ids),
            "current_session_scope": _session_scope_key(session_id),
            "dropped_foreign_session_count": dropped_foreign_session_count,
            "dropped_no_provenance_count": dropped_no_provenance_count,
            "dropped_forbidden_fact_count": dropped_forbidden_fact_count,
            "exact_recall_session_filter": exact_recall_session_filter,
        }
    )
    if not distilled:
        _set_retrieval_telemetry(telemetry)
        return transcript
    candidate = ContextCandidate(
        id="retrieved_capsule",
        kind="semantic",
        text=distilled,
        source_score=max((float(score) for _content, score in selected), default=0.0),
        source="semantic",
        cost_tokens=estimate_tokens(distilled),
    )
    packed = pack_context(
        [candidate],
        budget=budget,
        query=query,
        sanitize_fn=sanitize,
        covered_fn=_content_covered,
        covered_substr_fn=_content_covered_substring,
        transcript_text=context_text,
        now=0.0,
    )
    telemetry.update(
        {
            "distilled_chars": len(packed.render_block),
            "estimated_distilled_tokens": packed.tokens_used,
            "capsule_mode": "distilled" if packed.blocks else "empty",
            "packed": packed.to_dict(),
        }
    )
    _set_retrieval_telemetry(telemetry)
    if not packed.render_block:
        return transcript
    return _inject_render_block(transcript, packed.render_block)


def _session_scoped_node_search(
    memory: object,
    query_embedding: list[float],
    *,
    session_id: str | None,
    top_k: int,
    min_score: float,
) -> list[tuple[object, float]]:
    """Call the mandatory scoped memory API and revalidate its output."""
    if not str(session_id or "").strip():
        return []
    search = memory.node_search
    hits = search(
        query_embedding,
        top_k=top_k,
        min_score=min_score,
        session_id=session_id,
    )
    return [
        (node, score)
        for node, score in hits
        if _node_has_only_current_session_source(node, session_id)
    ]


def _inject_retrieval_block(
    transcript: list[dict[str, str]],
    selected: list[tuple[str, float]],
) -> list[dict[str, str]]:
    """Insert the <retrieved_context> system block before the last user turn (byte-identical
    placement across the legacy and v2 paths)."""
    if not selected:
        return transcript
    lines = ["<retrieved_context>"]
    for content, score in selected:
        lines.append(f"[score={score:.2f}] {content}")
    lines.append("</retrieved_context>")
    retrieval_msg: dict[str, str] = {"role": "system", "content": "\n".join(lines)}

    result = list(transcript)
    last_user = next(
        (i for i in range(len(result) - 1, -1, -1) if result[i].get("role") == "user"),
        None,
    )
    if last_user is not None:
        result.insert(last_user, retrieval_msg)
    else:
        result.append(retrieval_msg)
    return result


def _inject_render_block(transcript: list[dict[str, str]], render_block: str) -> list[dict[str, str]]:
    if not str(render_block or "").strip():
        return transcript
    retrieval_msg: dict[str, str] = {"role": "system", "content": render_block}
    result = list(transcript)
    last_user = next(
        (i for i in range(len(result) - 1, -1, -1) if result[i].get("role") == "user"),
        None,
    )
    if last_user is not None:
        result.insert(last_user, retrieval_msg)
    else:
        result.append(retrieval_msg)
    return result


__all__ = [
    "capsule_exact_response",
    "exact_recall_requires_current_session",
    "forget_session_memory",
    "get_last_retrieval_telemetry",
    "inject_retrieved",
    "is_capsule_exact_recall_query",
    "reset_retrieval_telemetry",
    "store_turn",
    "update_retrieval_telemetry",
    "validate_capsule_exact_response",
]
