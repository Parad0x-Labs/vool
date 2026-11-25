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

import os
from typing import Any

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
    anyway (`core.clean_loop.brain.resolve_local_manifest`), so asking it first loads nothing extra.

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
            manifest = brain.resolve_local_manifest(router, source_context)
            metadata = (getattr(manifest, "metadata", None) or {}) if manifest is not None else {}
            model = str(getattr(manifest, "model_name", "") or "").strip()
            ollama = str(metadata.get("runtime_family") or "").strip().lower() == "ollama"
            found.append((model, str(metadata.get("bundle_role") or "general")) if model and ollama else None)
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
