from __future__ import annotations


def live_info_failure_text(*, query: str, mode: str) -> str:
    from core.remote_fetch_policy import effective_web_available

    from core.agent_runtime.fast_live_info_runtime_results import (
        asks_only_for_general_knowledge,
        web_lookups_off_text,
    )

    if not effective_web_available() and not asks_only_for_general_knowledge(query):
        # Nothing went online, so "I tried the live web lane" would be false: say what held. A
        # question that needs no reading is not a lookup, so the switch is not its reason.
        return web_lookups_off_text(f'to look up "{query}"')
    if mode == "weather":
        return f'I tried the live web lane for "{query}", but no current weather results came back.'
    if mode == "news":
        return f'I tried the live web lane for "{query}", but no current news results came back.'
    return f'I checked the live web lane for "{query}", but I could not ground a current answer confidently.'
