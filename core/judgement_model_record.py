"""The local models VOOL's own judgements were proven on, and which one judges a turn.

Every keep, recall and forget decision, and every tool pick's own yes/no, is a question put to a local model
(core.intent_arbiter, core.plugin_ask). Each of those questions was tuned and proven on one model only. Since the
judgement follows the model that answers the turn (0c3d4404) and Auto answers with the daily lane's pick (4424d7e8),
the gate iMac judged with qwen3.5:9b and qwen2.5:7b, models no memory probe ever ran on, and told facts were dropped
(memory wiring audit of efb8e474c, B1; gates 52 and 53).

So a turn is judged by a proven model when one is installed and it is loaded already or fits in free memory next to
what is loaded; otherwise by the turn's own model, as before. A model joins PROVEN_JUDGE_MODELS only with a probe
record of its own on the memory judges (fresh wordings, several languages) and a real-app gate.
"""

from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

#: Model tag -> the evidence it passed on. Keep the evidence line short and checkable.
PROVEN_JUDGE_MODELS: dict[str, str] = {
    "qwen3:8b": "memory judge probe22 87/96 and Mac run22 17/24 (2026-11-22/23); real-app gates 14-51 (2026-11-23/27)",
}

#: Room kept free beside a proven judge that is not loaded yet, so loading it never pushes the machine into swap.
_LOAD_HEADROOM_GB = 2.0
#: A proven judge whose size Ollama does not report is taken as this big.
_UNREPORTED_SIZE_GB = 6.0


def is_proven_judge(model: str) -> bool:
    return str(model or "").strip().lower() in PROVEN_JUDGE_MODELS


def judge_model_for(turn_model: str) -> str:
    """The model the turn's judgements are asked of: the turn's own when it is proven, else a proven one that is
    installed and loaded or fits, else the turn's own. Never raises; an unreadable machine keeps the turn's own."""
    turn = str(turn_model or "").strip()
    if not turn or is_proven_judge(turn):
        return turn
    try:
        from core.local_ollama_inventory import installed_ollama_model_inventory
        from core.memory_first_router import _local_model_free_memory_gb, _recent_resident_model_tags

        installed = {item.name.strip().lower(): item for item in installed_ollama_model_inventory()}
        candidates = [tag for tag in PROVEN_JUDGE_MODELS if tag in installed]
        if not candidates:
            return turn
        resident = {str(tag or "").strip().lower() for tag in (_recent_resident_model_tags() or ())}
        for tag in candidates:
            if tag in resident:
                _log.info("judgement model: %s (proven, loaded) for a turn answered by %s", tag, turn)
                return tag
        free = _local_model_free_memory_gb()
        if free is None:
            # No GPU or unified-memory reading (Intel Mac, Linux): the RAM free right now.
            from core.system_resources import snapshot

            free = float(snapshot().available_ram_gb)
        # The turn's own model still has to load beside the judge when it is not loaded yet.
        own = installed.get(turn.lower())
        own_gb = 0.0
        if own is not None and turn.lower() not in resident:
            own_gb = (own.size_bytes or 0) / 1e9 or _UNREPORTED_SIZE_GB
        for tag in candidates:
            size = (installed[tag].size_bytes or 0) / 1e9 or _UNREPORTED_SIZE_GB
            if free >= size + own_gb + _LOAD_HEADROOM_GB:
                _log.info("judgement model: %s (proven, fits in %.1f GB free) for a turn answered by %s", tag, free, turn)
                return tag
    except Exception as exc:
        _log.info("judgement model: machine unreadable (%s), the turn's own model judges", type(exc).__name__)
    return turn
