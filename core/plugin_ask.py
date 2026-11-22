"""Whether a turn asks for what one of the person's added plugins does, in any language and any words.

Every lane that decides whether an added plugin's tools reach the model reads one decision: the plugin family in
`core.tool_demand_signals.resolve_demand_signals` (then `core.execution_requirements`, the chat-first lane, the tool
offer's family expansion, the conductor and the files loop all follow it). That decision came only from the words
of the plugin's own manifest, which its maker wrote once, usually in English. Measured on the 0.8 release candidate
(a7b18403, 2026-11-22, real local model, a parcel lookup plugin enabled): "is shipment PX-3065-D at the pickup point
yet" reached the plugin; "Kur dabar yra mano siunta PX-3065-B?" and "my box PX-3065-B never came, any idea where
it's sitting?" were never offered it, and the model invented a location. A plugin is often added for a person's own
language; a closed English vocabulary cannot reach it.

So at the start of the turn, when the words do not already settle it and an added plugin has tools, the turn's judge
(`core.intent_arbiter.arbitrate_plugin_ask`: the loaded local model, or the owner's model bound for the turn) is
asked one multiple-choice question over the plugins' own descriptions, with the previous message for a short
follow-up. Its answer is recorded here for this turn's text, and the plugin family reads it beside the words. It
only widens what the model is OFFERED: which tool runs, if any, is still the model's call and the owner's
permissions. No judge, a failed call or an unreadable answer records nothing: the turn is what it was before.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Any

#: Judgements kept, by the turn text they were made for. The current turn's text is judged again at every turn,
#: so an entry is only ever read stale for an earlier message of a chat (a follow-up's lookback).
_KEPT = 256
_LIFETIME_S = 1800.0
_JUDGED: OrderedDict[str, tuple[float, tuple[str, ...], tuple[Any, ...]]] = OrderedDict()
_LOCK = threading.Lock()
_log = logging.getLogger(__name__)


def _key(text: str) -> str:
    return " ".join(str(text or "").split()).casefold()


def _installed_with_tools() -> list[tuple[str, str]]:
    """(plugin id, its maker's description) for every installed, reviewed package that brings tools."""
    from pathlib import Path

    from core import plugin_lifecycle
    from core.plugin_tools import load_manifest

    rows: list[tuple[str, str]] = []
    for record in plugin_lifecycle.records():
        if record.stage == plugin_lifecycle.STAGE_UNINSTALLED or not record.root:
            continue
        try:
            block = load_manifest(Path(record.root) / ".codex-plugin" / "plugin.json").vool
        except Exception:
            continue
        tools = [tool for tool in (block or {}).get("tools") or () if isinstance(tool, dict)]
        if not block or not tools:
            continue
        said = " ".join(str(tool.get("description") or "") for tool in tools)
        rows.append((record.plugin_id, f"{block.get('name') or record.plugin_id}: {block.get('description') or ''} {said}"))
    return rows


def _signature(rows: list[tuple[str, str]]) -> tuple[Any, ...]:
    return tuple(rows)


def judgement(text: str) -> tuple[str, ...] | None:
    """What the turn's judge said this text asks for: the plugins (empty when it asks for none of them), or None when
    nothing judged it (no judge, a failed call, an unreadable answer, a judgement too old, or plugins changed since)."""
    key = _key(text)
    if not key:
        return None
    with _LOCK:
        entry = _JUDGED.get(key)
    if entry is None:
        return None
    stamped, plugins, signature = entry
    if time.monotonic() - stamped > _LIFETIME_S:
        return None
    try:
        if _signature(_installed_with_tools()) != signature:
            return None
    except Exception:
        return None
    return plugins


def judged_plugins(text: str) -> tuple[str, ...]:
    return judgement(text) or ()


def _record(text: str, plugins: tuple[str, ...], signature: tuple[Any, ...]) -> None:
    key = _key(text)
    if not key:
        return
    with _LOCK:
        _JUDGED[key] = (time.monotonic(), plugins, signature)
        _JUDGED.move_to_end(key)
        while len(_JUDGED) > _KEPT:
            _JUDGED.popitem(last=False)


def _drop(text: str) -> None:
    with _LOCK:
        _JUDGED.pop(_key(text), None)


def forget() -> None:
    with _LOCK:
        _JUDGED.clear()


def _previous_request(text: str, source_context: dict[str, Any]) -> str:
    current = _key(text)
    earlier = [
        " ".join(str(message.get("content") or "").split())
        for message in list(source_context.get("conversation_history") or [])
        if isinstance(message, dict) and str(message.get("role") or "").strip().lower() == "user"
    ]
    while earlier and _key(earlier[-1]) == current:
        earlier.pop()   # the turn being read is usually the last message of its own history
    return earlier[-1] if earlier else ""


def judge_turn(text: str, *, also: str = "", source_context: Any, session_id: str = "") -> tuple[str, ...]:
    """Judge this turn's text once, before any lane reads the plugin family; `also` is the same turn's other
    spelling (the normalized text the lanes read), recorded with the same judgement. Never raises."""
    try:
        _drop(text)
        if str(also or "").strip():
            _drop(also)
        plugins = _judge_turn(str(text or ""), source_context if isinstance(source_context, dict) else {}, session_id)
        if plugins is not None and str(also or "").strip() and _key(also) != _key(text):
            _record(also, plugins, _signature(_installed_with_tools()))
        return plugins or ()
    except Exception as exc:
        _log.info("plugin ask: not judged (%s)", type(exc).__name__)
        return ()


def _plugins_named_by_tool_id(text: str, installed: set[str]) -> tuple[str, ...]:
    """The added plugins whose tools the text names by their exact ids (the demand seam's own reading)."""
    from core.plugin_tools import plugin_id_for_intent
    from core.tool_demand_signals import _plugin_intents

    named = (plugin_id_for_intent(intent) for intent in _plugin_intents(text))
    return tuple(dict.fromkeys(plugin_id for plugin_id in named if plugin_id and plugin_id in installed))


def _judge_turn(text: str, source_context: dict[str, Any], session_id: str) -> tuple[str, ...] | None:
    """The judgement for this turn's text, recorded; None when nothing judged it (the words then decide alone)."""
    if not text.strip():
        return None
    rows = _installed_with_tools()
    if not rows:
        return None
    signature = _signature(rows)
    # A plugin tool named by its own id ("use vool-plugin-template.sha256 ...") is not a guess from vocabulary: the
    # words settle which plugin is asked for, and no model is asked. Asking anyway spent a model call on every such
    # turn, and on a plugin that is turned off it was a model call on a turn that must say so before any model is
    # asked (CI on fe98e849, test_plugin_served_journey: an inference after the plugin was disabled).
    named = _plugins_named_by_tool_id(text, {plugin_id for plugin_id, _description in rows})
    if named:
        _record(text, named, signature)
        return named
    from core import intent_arbiter

    # Asked whatever the words matched. The plugins' own words, and other families' word rules, guess from English
    # vocabulary: "what does a tracking number usually look like?" named a parcel plugin by its word "tracking", and
    # "any sign of it?" was taken by a wallet rule on "sign" (both measured on a7b18403, 2026-11-22). The judge
    # reads the plugins' descriptions and the whole message; its answer is what the plugin family follows.
    picked = intent_arbiter.arbitrate_plugin_ask(
        text,
        [description for _plugin_id, description in rows[: intent_arbiter.PLUGIN_ASK_CHOICES]],
        previous=_previous_request(text, source_context),
        chat_id=str(session_id or ""),
        source_context=source_context,
    )
    _log.info("plugin ask judged: %s of %d plugin(s)", picked, len(rows))
    if picked is None:
        return None
    plugins = (rows[picked - 1][0],) if picked else ()
    _record(text, plugins, signature)
    return plugins
