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

from typing import Any

#: One read tool per owner family, in seating order. Each is read-only; none moves money, sends
#: anything or changes the machine.
OWNER_CATALOG: tuple[str, ...] = (
    "machine.list_directory",
    "machine.find_file",
    "machine.read_file",
    "pdf.extract_text",
    "machine.disk_usage",
    "machine.list_processes",
    "operator.find_notes",
    "operator.list_reminders",
    "operator.show_agenda",
    "contacts.search",
    "skill.list",
    "profile.list",
)

#: source-context key caching the local-lane verdict for one turn (one registry read per turn).
_LOCAL_LANE_KEY = "_owner_catalog_local_lane"


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
