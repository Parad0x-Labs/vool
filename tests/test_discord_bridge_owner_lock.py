"""Discord bridge owner lock: only the paired (or pinned) owner's commands reach the agent."""
from __future__ import annotations

import json

import pytest

import relay.bridge_workers.discord_bridge as dbr

CHANNEL = "chan-42"
OWNER = "111"
STRANGER = "222"


@pytest.fixture
def gateway(monkeypatch, tmp_path):
    """Isolated home, no pinned owner, and a recorder in place of the channel gateway."""

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "placeholder")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", CHANNEL)
    monkeypatch.setenv("DISCORD_BOT_USER_ID", "bot-1")
    monkeypatch.delenv("DISCORD_CHANNEL_IDS_JSON", raising=False)
    monkeypatch.delenv("VOOL_DISCORD_OWNER_USER_ID", raising=False)
    reached: list = []

    class _Result:
        response_text = "Ack from Vool"

    def _process(agent, request):
        reached.append((request.user_id, request.text))
        return _Result()

    monkeypatch.setattr(dbr, "process_channel_request", _process)
    return reached


def _bridge():
    bridge = dbr.DiscordBridge()
    bridge.posted = []
    bridge.inbox = []
    bridge._ensure_agent = lambda: object()
    bridge._resolve_bot_user_id = lambda: "bot-1"
    bridge._fetch_channel_messages = lambda channel_id, limit=20: list(bridge.inbox)
    bridge._post_bot_message = lambda channel_id, content: bridge.posted.append((channel_id, content)) or True
    bridge._last_seen_message_ids[CHANNEL] = 100
    return bridge


def _deliver(bridge, *messages):
    """Newest first, as Discord returns them; ids continue above what the bridge has seen."""

    base = bridge._last_seen_message_ids[CHANNEL]
    bridge.inbox = [
        {"id": str(base + index + 1), "content": content, "author": {"id": author, "bot": False}}
        for index, (author, content) in enumerate(messages)
    ][::-1]
    bridge.fetch_discord_and_push_to_mirror()


def test_a_second_user_never_reaches_the_agent_after_the_owner_pairs(gateway, tmp_path) -> None:
    bridge = _bridge()
    _deliver(bridge, (OWNER, "vool: hello"), (STRANGER, "!vool run the wallet"), (STRANGER, "!vool again"))

    assert gateway == [(OWNER, "hello")]
    assert bridge.posted == [
        (CHANNEL, f"<@{OWNER}> Ack from Vool"),
        (CHANNEL, f"<@{STRANGER}> {dbr.REFUSAL_TEXT}"),
    ]
    saved = json.loads((tmp_path / "config" / "discord_bridge.json").read_text(encoding="utf-8"))
    assert saved == {"owner_user_id": OWNER}

    # The pairing survives a restart: the stranger is still refused and the owner still served.
    restarted = _bridge()
    _deliver(restarted, (STRANGER, "vool: status"), (OWNER, "vool: status"))
    assert gateway == [(OWNER, "hello"), (OWNER, "status")]
    assert restarted.posted == [
        (CHANNEL, f"<@{STRANGER}> {dbr.REFUSAL_TEXT}"),
        (CHANNEL, f"<@{OWNER}> Ack from Vool"),
    ]


def test_a_pinned_owner_skips_first_use_pairing(gateway, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOOL_DISCORD_OWNER_USER_ID", OWNER)
    bridge = _bridge()
    _deliver(bridge, (STRANGER, "!vool hello"), (OWNER, "vool: hello"))

    assert gateway == [(OWNER, "hello")]
    assert bridge.posted[0] == (CHANNEL, f"<@{STRANGER}> {dbr.REFUSAL_TEXT}")
    assert not (tmp_path / "config" / "discord_bridge.json").exists()


def test_chatter_that_is_not_a_command_pairs_nobody(gateway, tmp_path) -> None:
    bridge = _bridge()
    _deliver(bridge, (STRANGER, "anyone around?"))
    assert bridge.owner_user_id is None and bridge.posted == [] and gateway == []

    _deliver(bridge, (OWNER, "vool: hello"), (STRANGER, "!vool hello"))
    assert gateway == [(OWNER, "hello")]


def test_an_unreadable_owner_file_refuses_everyone(gateway, tmp_path) -> None:
    owner_file = tmp_path / "config" / "discord_bridge.json"
    owner_file.parent.mkdir(parents=True)
    owner_file.write_text("{not json", encoding="utf-8")

    bridge = _bridge()
    _deliver(bridge, (OWNER, "vool: hello"), (STRANGER, "!vool hello"))

    assert gateway == []
    assert [content for _, content in bridge.posted] == [
        f"<@{OWNER}> {dbr.REFUSAL_TEXT}",
        f"<@{STRANGER}> {dbr.REFUSAL_TEXT}",
    ]
    assert owner_file.read_text(encoding="utf-8") == "{not json"
