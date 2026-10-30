"""The compact owner catalog: a small read tool set on every owner turn the local model answers.

Measured 2026-10-29 on qwen3:8b (CPU, 120 blind asks): the right tool ran on 17 of 70 asks that
needed this computer. Most of those turns ended on ``ordinary_plain_text_chat`` with NO tools
offered, because two word gates (``should_keep_ai_first_chat_lane`` and
``should_attempt_tool_intent``) decided from the raw words, before any model was consulted,
whether the model would ever see a tool. A recognizer can only seat the phrasings it was written
for; a fresh ask ("whats eating my cpu", "kas yra mano Downloads aplanke?") fell through and the
model, holding no tools, guessed or made up a result.

Handed a small, relevant tool family, qwen3:8b calls the right tool natively and answers a plain
knowledge ask with ``respond.direct``. So on an owner-local turn served by a local model the
model, not a word list, decides: the lanes release and the offer seats one read tool per owner
family (files, PDFs, disk, processes, notes, reminders, calendar, contacts, skills, profile).
Writes are not in the catalog: they are seated only by the recognizers that already seat them,
or by the model asking ``capability.expand_family``, and every write keeps its approval gate.

Scope: the owner's own local session, a local model (the turn is Local Only, the pinned model is
local, or Auto has nothing but local lanes), and no instruction from the owner to keep tools out
of the turn. A remote session, a cloud model and a tools-forbidden turn are untouched.
"""
from __future__ import annotations

import re
from typing import Any

#: One read tool per owner family, in seating order, each with the short description the local
#: model reads. Short on purpose: on a CPU every offered character is prefill time paid on every
#: round (measured 2026-10-30: a 17-tool round with the catalog's full descriptions carried 7.5k
#: characters of tool definitions). Each is read-only; none moves money, sends anything or
#: changes the machine.
OWNER_CATALOG_DESCRIPTIONS: dict[str, str] = {
    "machine.list_directory": "List a folder on this computer (Desktop, Downloads or Documents).",
    "machine.find_file": "Find files by name in Desktop, Downloads and Documents.",
    "machine.read_file": "Read a text file on this computer.",
    "pdf.extract_text": "Read the text of a PDF file.",
    "machine.disk_usage": "Free and total disk space.",
    "machine.list_processes": "Running programs, heaviest first by memory or CPU.",
    "operator.find_notes": "Search the user's saved notes.",
    "operator.list_reminders": "List the user's reminders.",
    "operator.show_agenda": "Show the user's calendar for a period.",
    "contacts.search": "Find a saved contact by name.",
    "skill.list": "List the skills installed in VOOL and whether each is on.",
    "profile.list": "Show what VOOL keeps about the user: name, language, timezone and preferences.",
}
OWNER_CATALOG: tuple[str, ...] = tuple(OWNER_CATALOG_DESCRIPTIONS)

#: source-context key caching the local-lane verdict for one turn (one registry read per turn).
_LOCAL_LANE_KEY = "_owner_catalog_local_lane"

#: The one instruction a catalog round carries. Measured 2026-10-30 on qwen3:8b with the catalog
#: seated and VOOL's system prompt: without it "kas yra mano Downloads aplanke?" was answered in
#: prose ("Downloads is a folder where files you download are saved"); with it the same payload
#: called machine.list_directory, "whats eating my cpu" called machine.list_processes, and "whats
#: the capital of australia" / "how do i take a screenshot on a mac" were answered without a tool.
OWNER_CATALOG_DIRECTIVE = (
    "The user's own files, PDFs, notes, reminders, calendar, contacts, running programs, disk, "
    "VOOL skills and VOOL settings live on this computer and only the tools can read them. When "
    "the request is about any of those, call the matching tool; never describe, guess or invent "
    "them. For general knowledge or chat, answer directly without a tool."
)

#: ModelRequest metadata key: on a catalog round a reply in plain prose is the model's direct
#: answer (respond.direct), not a malformed tool call. Local models do not honour
#: tool_choice "required", so a knowledge ask answered in prose is the model deciding no tool.
PROSE_IS_DIRECT_ANSWER_KEY = "owner_catalog_prose_is_direct_answer"


def _pinned_manifests(requested: str, manifests: list[Any]) -> list[Any]:
    wanted = requested.strip().lower()
    matched = []
    for manifest in manifests:
        provider = str(getattr(manifest, "provider_name", "") or "").strip().lower()
        model = str(getattr(manifest, "model_name", "") or "").strip().lower()
        if wanted in {model, f"{provider}:{model}", f"{provider}/{model}"}:
            matched.append(manifest)
    return matched


def turn_answers_on_local_model(source_context: dict[str, Any] | None) -> bool:
    """Whether this turn's answering model runs on this machine.

    Local Only binds. A concrete pin is local when every enabled lane carrying that model name is
    local. Auto (no pin) is local when every enabled lane is local. An unreadable registry is not
    local: the catalog is an addition, and the unknown case keeps today's behaviour.
    """
    context = source_context if isinstance(source_context, dict) else {}
    cached = context.get(_LOCAL_LANE_KEY)
    if isinstance(cached, bool):
        return cached
    verdict = False
    try:
        from core.auto_local_only_mode import is_auto_selection, manifest_is_local, turn_is_local_only

        if turn_is_local_only(context):
            verdict = True
        else:
            from core.model_registry import ModelRegistry

            manifests = list(ModelRegistry().list_manifests(enabled_only=True))
            requested = str(context.get("requested_model") or "").strip()
            if requested and not is_auto_selection(requested):
                manifests = _pinned_manifests(requested, manifests)
            verdict = bool(manifests) and all(manifest_is_local(m) for m in manifests)
    except Exception:
        verdict = False
    if isinstance(source_context, dict):
        source_context[_LOCAL_LANE_KEY] = verdict
    return verdict


def owner_catalog_applies(text: str, source_context: dict[str, Any] | None) -> bool:
    """Whether this turn gets the compact owner catalog and leaves the tools-less chat lane."""
    from core.runtime_flags import flag_enabled

    if not flag_enabled("owner_local_tool_catalog"):
        return False
    if not isinstance(source_context, dict) or not str(text or "").strip():
        return False
    try:
        from core.request_trust import request_is_owner_local

        if not request_is_owner_local(source_context):
            return False
        from core.agent_runtime.intent_claims import ActionPolicy, action_policy_from_context

        if action_policy_from_context(source_context) is ActionPolicy.FORBIDDEN:
            return False
        from core.retrieval_constraints import analyze_retrieval_constraints

        if analyze_retrieval_constraints(str(text)).forbids_all_tools:
            return False
    except Exception:
        return False
    return turn_answers_on_local_model(source_context)


def owner_catalog_intents(text: str, source_context: dict[str, Any] | None) -> tuple[str, ...]:
    """The catalog intents to seat on this turn, or () when the catalog does not apply."""
    return OWNER_CATALOG if owner_catalog_applies(text, source_context) else ()


#: The safe folders machine.list_directory may read (core.runtime_execution_tools).
_SAFE_FOLDER_NAMES: tuple[str, ...] = ("Desktop", "Downloads", "Documents")
#: source-context key: the safe folders the owner's own words name on a catalog turn.
OWNER_NAMED_FOLDERS_KEY = "_owner_catalog_named_folders"


def owner_named_safe_folders(text: str) -> tuple[str, ...]:
    """The safe folders the words name, in any language's sentence ("kas yra mano Downloads aplanke?")."""
    return tuple(name for name in _SAFE_FOLDER_NAMES if re.search(rf"\b{name}\b", str(text or ""), re.IGNORECASE))


def list_directory_target_named_by_owner(path: Any, source_context: dict[str, Any] | None) -> bool:
    """Whether a model's machine.list_directory path is a folder the owner's own words named.

    The binding is the owner's words, not the model's proposal: the path must be one of the safe
    folders the turn's text named (``OWNER_NAMED_FOLDERS_KEY``, stamped only on catalog turns), or
    inside one. The tool keeps enforcing its own safe roots.
    """
    named = {str(n).lower() for n in (source_context or {}).get(OWNER_NAMED_FOLDERS_KEY) or ()}
    raw = str(path or "").strip().replace("\\", "/")
    if not named or not raw or ".." in raw.split("/"):
        return False
    parts = [p for p in raw.split("/") if p and p != "~"]
    if raw.startswith("/"):
        try:
            from pathlib import Path

            home_parts = [p for p in Path.home().resolve().as_posix().split("/") if p]
        except Exception:
            return False
        if parts[: len(home_parts)] != home_parts:
            return False
        parts = parts[len(home_parts):]
    return bool(parts) and parts[0].lower() in named
