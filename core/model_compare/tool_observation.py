"""Observe provider tool requests without retaining arguments or executing them."""
from __future__ import annotations


def requested_tool(event) -> bool:
    if not isinstance(event, dict):
        return False

    def requested(carrier):
        if not isinstance(carrier, dict):
            return False
        return any(key in carrier and carrier[key] not in (None, [])
                   for key in ("tool_calls", "function_call"))

    if requested(event) or requested(event.get("message")):
        return True
    choices = event.get("choices")
    return isinstance(choices, list) and any(
        requested(choice.get("delta")) or requested(choice.get("message"))
        for choice in choices if isinstance(choice, dict))
