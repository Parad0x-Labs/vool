"""The judge of a turn that no local model answers.

The memory lanes ask one-question judgements (is this a keep ask? a lasting fact? the same thing as the question?) of
the loaded local model (`core.intent_arbiter`). On a turn the owner's own cloud model answers, with local models off,
nobody answered them, and every fix built on a judgement fell back to what it did before (measured on the real app
on the cloud route, 2026-11-18: a fact said in passing was not kept, a keep ask in other words was not kept, a
question in other words was not found).

Here the turn gets a judge: the same prompt goes to the model that answers the turn, through the call the file search
already makes to it (`core.clean_loop.brain.make_brain`: the router's provider seam, the paid-call reservation under
the owner's caps, the deadline, the call ledger). No new client and no new rule about who may spend:

* only when local models are off (a machine with a local judge keeps it: the loaded model first, and when none is
  loaded yet, the local model that answers the turn, see `_local_judge_for`);
* only the owner's own local turn, outside a private chat (a remote turn or an agent's turn never spends the owner's
  key here, and a private chat sends nothing of itself anywhere new);
* only the model the owner pinned for the turn or saved for Auto (`resolve_cloud_manifest`), and only while the
  owner's cloud switch for the file search is on, plus a switch of its own.

`VOOL_CLOUD_JUDGEMENTS` (default ON; 0/false/no/off turns it off): with it off the judgements are not asked on such
a turn, as before.
"""
from __future__ import annotations

import logging
import os
from typing import Any

_log = logging.getLogger(__name__)

SWITCH_ENV = "VOOL_CLOUD_JUDGEMENTS"
_TRUTHY = {"1", "true", "yes", "on"}


def enabled(env: Any = None) -> bool:
    # An off switch the code cannot read stays off: the owner's words go nowhere on a value nobody understands.
    value = str((env if env is not None else os.environ).get(SWITCH_ENV) or "").strip().lower()
    return not value or value in _TRUTHY


def _local_judge_for(agent: Any, source_context: dict, session_id: str) -> Any | None:
    """With local models on: the Ollama model that answers this turn, asked when none is loaded yet.

    The judgements ask this turn's model at once while it is loaded (`core.intent_arbiter._judge_model_for`). On the first turn,
    or after Ollama unloaded the model when idle, none is loaded, and every judgement said "nobody judged" in a few
    milliseconds: a reminder, note or calendar ask in Lithuanian got no tool and the reply said it was done (blind
    audit of 58f60bbf3, 31 of 50 judgements None). The model this judge asks is the one the turn loads to answer
    anyway (`core.clean_loop.brain.resolve_local_manifest`), so asking it first loads nothing extra. When that model is
    not one the judgements were proven on, a proven one is asked instead if it is installed and loaded or fits in free
    memory beside the turn's model (`core.judgement_model_record`, memory wiring audit B1).

    It asks through the arbiter's own direct Ollama call (`core.intent_arbiter.ask_local_model`, on the runner the
    turn is served on), never through the turn's provider: through the provider the judge's question was a turn of
    its own, held for a send preview and counted with the person's own calls (CI on 3dfb2f34). A local model that is
    not Ollama's gets no judge here, as before. It stays on this computer, so a private chat may use it; a remote
    or agent turn may not."""
    router = getattr(agent, "memory_router", None)
    if router is None:
        return None
    from core.request_trust import request_is_owner_local, turn_speaks_for_owner

    if not request_is_owner_local(source_context) or not turn_speaks_for_owner(source_context, str(session_id or "")):
        return None
    from core import intent_arbiter
    from core.clean_loop import brain

    # Read when a judgement is first asked, not when the turn starts: finding the turn's model asks Ollama what it
    # has, and most turns ask no judgement of this judge (a loaded model answers them, or none is asked at all).
    found: list[tuple[str, str] | None] = []

    def _model() -> tuple[str, str] | None:
        if not found:
            try:
                manifest = brain.resolve_local_manifest(router, source_context)
            except Exception:
                manifest = None
            metadata = (getattr(manifest, "metadata", None) or {}) if manifest is not None else {}
            model = str(getattr(manifest, "model_name", "") or "").strip()
            ollama = str(metadata.get("runtime_family") or "").strip().lower() == "ollama"
            named = (model, str(metadata.get("bundle_role") or "general")) if model and ollama else None
            named = named or _requested_ollama_model(source_context) or _registered_chat_model(
                router, in_use=str(source_context.get("runtime_model_in_use") or ""))
            if named is not None:
                # VOOL's judgements are asked of a model they were proven on when the machine has one that fits
                # (core.judgement_model_record); the turn's own model otherwise.
                from core.judgement_model_record import judge_model_for

                judge_tag = judge_model_for(named[0])
                if judge_tag and judge_tag != named[0]:
                    named = (judge_tag, "general")
            found.append(named)
            _log.info("turn judge model: %s", (found[0] or ("none",))[0])
        return found[0]

    def judge(prompt: str) -> str:
        named = _model()
        if named is None:
            raise LookupError("no local Ollama model answers this turn")
        model, role = named
        return intent_arbiter.ask_local_model(str(prompt), model, runner_role=role, source_context=source_context) or ""

    # The arbiter asks a judgement of the turn's own model while it is loaded (`intent_arbiter._judge_model_for`),
    # never of whichever other model is loaded.
    judge.turn_model = _model  # type: ignore[attr-defined]
    return judge


def _requested_ollama_model(source_context: dict) -> tuple[str, str] | None:
    """The Ollama model the person picked for this chat, when the manifest lookup names none.

    On the first turn of a new chat the lookup named no model, and every judgement of that turn went to the largest
    model another app had loaded (qwen3.5:9b) while the chat was answered by qwen3:8b (iMac gate on cf0d43952). The
    model the page sent is the one that answers; it is used when Ollama has it installed."""
    requested = str((source_context or {}).get("requested_model") or "").strip()
    try:
        from core.auto_local_only_mode import is_auto_selection

        if not requested or is_auto_selection(requested):
            return None
        from core.local_ollama_inventory import installed_ollama_model_names

        installed = {str(name).strip().lower() for name in installed_ollama_model_names()}
    except Exception:
        return None
    wanted = requested.lower()
    if wanted in installed or (":" not in wanted and f"{wanted}:latest" in installed):
        return requested, "general"
    return None


#: Local models VOOL registers for its own small jobs, never the model a chat is answered by.
_HELPER_ROLES = frozenset({"lightweight_utility"})


def _registered_chat_model(router: Any, *, in_use: str = "") -> tuple[str, str] | None:
    """The local Ollama chat model VOOL registered, when neither the answer lookup nor the chat's pick names one.

    The answer lookup (`core.clean_loop.brain.resolve_local_manifest`) names only a model whose final-answer check has
    passed. On a fresh profile none has passed yet on the first message, so that message had no judge at all ("turn
    judge model: none", then LookupError): a Lithuanian "remember: ..." was not kept while the reply, from qwen3:8b,
    said it was (iMac gates on f0a19600e and 64798c0fb). A yes/no needs no final-answer check: the judge asks the
    registered chat model, one that passed first, installed in Ollama, never a helper model and never a model
    another app loaded that VOOL did not register.

    The model this computer answers with now (`in_use`, stamped by the chat door from
    `core.local_model_readiness.model_in_use`) goes first: on the iMac gate of 4e06fdc5c, with qwen3:8b the chat model
    and qwen3.5:9b also registered, the first message was judged by qwen3.5:9b, which loaded it beside the chat's."""
    try:
        from core.local_model_policy import local_models_enabled

        if not local_models_enabled():
            return None
        manifests = list(router.registry.list_manifests(enabled_only=True))
        from core.clean_loop import brain
        from core.local_ollama_inventory import installed_ollama_model_names

        installed = {str(name).strip().lower() for name in installed_ollama_model_names()}
    except Exception:
        return None
    candidates = []
    for manifest in manifests:
        metadata = getattr(manifest, "metadata", None) or {}
        model = str(getattr(manifest, "model_name", "") or "").strip()
        role = str(metadata.get("bundle_role") or "general").strip().lower()
        if (not model or role in _HELPER_ROLES or str(metadata.get("runtime_family") or "").strip().lower() != "ollama"
                or not brain._is_local(manifest)):
            continue
        if model.lower() in installed or (":" not in model and f"{model.lower()}:latest" in installed):
            candidates.append((not brain._ready(manifest), model, role))
    if not candidates:
        return None
    wanted = in_use.strip().lower()
    _unready, model, role = min(candidates, key=lambda item: (item[1].lower() != wanted, item[0]))
    return model, role


def _judge_for(agent: Any, source_context: Any, session_id: str) -> Any | None:
    if not isinstance(source_context, dict):
        return None
    from core import intent_arbiter

    if not intent_arbiter._local_models_disabled():
        return _local_judge_for(agent, source_context, session_id)
    if not enabled():
        return None
    router = getattr(agent, "memory_router", None)
    if router is None:
        return None
    from core.private_chat import turn_is_private
    from core.request_trust import request_is_owner_local, turn_speaks_for_owner

    if not request_is_owner_local(source_context) or not turn_speaks_for_owner(source_context, str(session_id or "")):
        return None
    if turn_is_private(source_context, session_id):
        return None
    from core.clean_loop import brain

    manifest = brain.resolve_cloud_manifest(router, source_context)
    if manifest is None:
        return None

    def judge(prompt: str) -> str:
        return brain.make_brain(router, manifest, source_context)([{"role": "user", "content": str(prompt)}])

    return judge


def bind_for_turn(agent: Any, source_context: Any, session_id: str) -> None:
    """Bind this turn's judge, or clear the one of the turn before. Never raises: a turn with no judge is the turn
    it always was."""
    from core import intent_arbiter

    try:
        judge = _judge_for(agent, source_context, session_id)
    except Exception:
        judge = None
    intent_arbiter.bind_turn_judge(source_context, judge)
