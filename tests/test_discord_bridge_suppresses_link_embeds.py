"""The Discord bridge posts VOOL's own messages with link embeds suppressed.

Discord's servers fetch a link in a message to draw its embed, so an address the model was led to
write (with what it read put in the address) would leave with no click (security review 2026-11-13).
The managed chat app's path is proven served in tests/channels/test_managed_chat_apps.py.
"""

from __future__ import annotations

from relay.bridge_workers import discord_bridge


def test_a_bot_message_carries_suppress_embeds(monkeypatch) -> None:
    bridge = discord_bridge.DiscordBridge.__new__(discord_bridge.DiscordBridge)
    bridge.bot_token = "t"
    seen: list[tuple[str, dict]] = []

    def request(path, *, method="GET", data=None):
        seen.append((path, dict(data or {})))
        return {"id": "1"}

    monkeypatch.setattr(bridge, "_discord_api_request", request)
    assert bridge._post_bot_message("444", "Done. https://beacon.invalid/q.gif?d=SECRET")
    assert seen == [("/channels/444/messages", {"content": "Done. https://beacon.invalid/q.gif?d=SECRET", "flags": 4})]
