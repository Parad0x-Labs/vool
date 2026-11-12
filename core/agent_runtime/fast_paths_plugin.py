"""When the words ask for an added plugin that cannot run right now, say so and say what to do.

Measured in real chat (2026-11-11, local model): with chat tools switched off, or the plugin
turned off, or its files changed after review, "what is the SHA-256 fingerprint of ..." was
answered by the model alone -- an invented fingerprint, and never a word that the plugin was off.
The person had no way to know the answer did not come from the plugin they added.

Whether a plugin may run is already decided elsewhere (`plugin_lifecycle.is_available`, the
owner's chat-tools switch). This lane only reads that decision and tells the person, before any
model is asked. It never enables, reviews or runs anything.
"""

from __future__ import annotations

from typing import Any

WHERE = "Open Skills & Plugins, then My add-ons, then Plugins"
SWITCHED_OFF = (
    "I can\u2019t use the {name} plugin here yet, because plugin tools are switched off for chats. "
    + WHERE
    + ", and turn on \u201cLet chats use plugin tools\u201d. Then ask me again."
)
TURNED_OFF = (
    "The {name} plugin is turned off, so I can\u2019t use it. "
    + WHERE
    + ", and choose \u201cReview access and enable\u201d on its card. Then ask me again."
)
CHANGED = (
    "The {name} plugin changed after you reviewed it, so VOOL turned it off. "
    + WHERE
    + ", review it again and enable it. Then ask me again."
)
REMOVED = (
    "The {name} plugin was removed, so I can\u2019t use it. "
    "Open Skills & Plugins to add it again, then ask me again."
)


def unavailable_plugin_notice(text: str) -> str:
    """The sentence for a turn that asks only for plugins that cannot run; "" otherwise."""
    from core.tool_demand_signals import plugins_named_in_words, resolve_demand_signals

    signals = resolve_demand_signals(text)
    if not signals.required_families and not signals.explicit_intents:
        from core.tool_demand_signals import removed_plugins_named_in_words

        removed = removed_plugins_named_in_words(text)
        if removed:
            return REMOVED.format(name=removed[0])
    if tuple(signals.required_families) != ("plugin",):
        return ""   # nothing asked of a plugin, or another tool family owns the turn
    from core import plugin_manager
    from core.plugin_tools import plugin_id_for_intent, plugin_tools_on

    asked = [plugin_id_for_intent(intent) for intent in signals.explicit_intents] or list(plugins_named_in_words(text))
    states: list[dict[str, Any]] = []
    for plugin_id in dict.fromkeys(p for p in asked if p):
        try:
            states.append(plugin_manager.inspect(plugin_id))
        except Exception:
            return ""   # not a reviewed package, or unreadable: leave the turn to the ordinary path
    if not states:
        return ""
    names = " and ".join(str(state.get("name") or state.get("id") or "") for state in states[:2])
    usable = [state for state in states if state.get("enabled")]
    if usable and plugin_tools_on():
        return ""
    if usable:
        return SWITCHED_OFF.format(name=str(usable[0].get("name") or usable[0].get("id")))
    first = states[0]
    template = CHANGED if str(first.get("reason") or "") else TURNED_OFF
    return template.format(name=names if len(states) > 1 and not first.get("reason") else str(first.get("name") or first.get("id")))


def maybe_explain_unavailable_plugin(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    try:
        notice = unavailable_plugin_notice(user_input)
    except Exception:
        return None
    if not notice:
        return None
    return agent._fast_path_result(
        session_id=session_id,
        user_input=user_input,
        response=notice,
        confidence=0.9,
        source_context=source_context,
        reason="plugin_unavailable_explained",
    )
