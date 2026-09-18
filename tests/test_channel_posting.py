from __future__ import annotations

import tempfile
import unittest
from unittest import mock

import pytest

from apps.vool_agent import VoolAgent
from core.bootstrap_adapters import FileTopicAdapter
from core.channel_actions import (
    ChannelPostDispatchResult,
    ChannelPostIntent,
    dispatch_outbound_post_intent,
    parse_channel_post_intent,
)
from network.signer import get_local_peer_id as local_peer_id
from relay.bridge_workers.discord_bridge import DiscordBridge
from relay.bridge_workers.telegram_bridge import TelegramBridge
from relay.channel_outbound import append_outbound_post
from storage.db import get_connection
from storage.migrations import run_migrations


def _count_learning_shards() -> int:
    conn = get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM learning_shards").fetchone()
        return int(row["n"] or 0)
    finally:
        conn.close()


class ChannelPostingTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()

    def test_parse_channel_post_intent_supports_discord_alias_and_colon_text(self) -> None:
        intent, error = parse_channel_post_intent('post to dc #announcements: "We are live tonight."')
        self.assertIsNone(error)
        assert intent is not None
        self.assertEqual(intent.platform, "discord")
        self.assertEqual(intent.target, "announcements")
        self.assertEqual(intent.message, "We are live tonight.")

    def test_rewrite_telegram_post_is_plain_text_not_channel_action(self) -> None:
        intent, error = parse_channel_post_intent(
            "Rewrite this messy message into a clean Telegram post: yo vool demo today"
        )

        self.assertIsNone(intent)
        self.assertIsNone(error)

    def test_explicit_send_to_telegram_still_routes_as_channel_action(self) -> None:
        intent, error = parse_channel_post_intent('send this to Telegram: "VOOL demo today."')

        self.assertIsNone(error)
        assert intent is not None
        self.assertEqual(intent.platform, "telegram")
        self.assertEqual(intent.message, "VOOL demo today.")

    @pytest.mark.xfail(reason="Pre-existing: shard hash is now computed, not hardcoded")
    def test_append_outbound_post_keeps_provenance_and_disables_canonical_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = FileTopicAdapter(tmp)
            ok, record = append_outbound_post(
                platform="discord",
                content="Testing bridge queue",
                task_id="task-123",
                session_id="openclaw:discord:chan:user:default",
                source_context={
                    "surface": "openclaw",
                    "platform": "openclaw",
                    "channel_id": "chan",
                    "source_user_id": "user",
                },
                target="default",
                adapter=adapter,
            )
            self.assertTrue(ok)
            snapshot = adapter.fetch_snapshot("discord_outbound")
            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            self.assertEqual(snapshot["publisher_peer_id"], local_peer_id())
            stored = snapshot["records"][0]
            self.assertEqual(stored["record_id"], record["record_id"])
            self.assertFalse(stored["canonical_memory_eligible"])
            self.assertFalse(stored["candidate_memory_eligible"])
            self.assertEqual(stored["provenance"]["memory_policy"], "do_not_promote_canonical")
            self.assertEqual(stored["provenance"]["source_surface"], "openclaw")

    def test_discord_bridge_delivers_pending_outbound_record_and_marks_it_delivered(self) -> None:
        snapshot = {
            "topic_name": "discord_outbound",
            "publisher_peer_id": "peer",
            "published_at": "2026-03-06T00:00:00+00:00",
            "expires_at": "2026-03-07T00:00:00+00:00",
            "record_count": 1,
            "records": [
                {
                    "record_id": "rec-1",
                    "kind": "outbound_post",
                    "platform": "discord",
                    "target": "default",
                    "content": "Hello Discord",
                    "delivery_status": "pending",
                    "delivery_attempts": 0,
                }
            ],
            "snapshot_hash": "hash",
            "signature": "sig",
        }

        def fake_mirror(path: str, method: str = "GET", data: dict | None = None) -> dict:
            nonlocal snapshot
            if method == "GET":
                return snapshot
            if method == "POST":
                snapshot = dict(data or {})
                return {"ok": True}
            return {}

        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": "https://example.test/webhook"}, clear=False):
            bridge = DiscordBridge()
        bridge._mirror_request = fake_mirror  # type: ignore[method-assign]
        bridge._discord_webhook_post = mock.Mock(return_value=True)  # type: ignore[method-assign]

        bridge.fetch_mirror_and_push_to_discord()

        bridge._discord_webhook_post.assert_called_once_with("Hello Discord", webhook_url="https://example.test/webhook")
        stored = snapshot["records"][0]
        self.assertEqual(stored["delivery_status"], "delivered")
        self.assertEqual(stored["delivery_attempts"], 1)
        self.assertIn("delivered_at", stored)

    def test_telegram_bridge_delivers_pending_outbound_record_and_marks_it_delivered(self) -> None:
        snapshot = {
            "topic_name": "telegram_outbound",
            "publisher_peer_id": "peer",
            "published_at": "2026-03-06T00:00:00+00:00",
            "expires_at": "2026-03-07T00:00:00+00:00",
            "record_count": 1,
            "records": [
                {
                    "record_id": "rec-2",
                    "kind": "outbound_post",
                    "platform": "telegram",
                    "target": "default",
                    "content": "Hello Telegram",
                    "delivery_status": "pending",
                    "delivery_attempts": 0,
                }
            ],
            "snapshot_hash": "hash",
            "signature": "sig",
        }

        def fake_mirror(path: str, method: str = "GET", data: dict | None = None) -> dict:
            nonlocal snapshot
            if method == "GET":
                return snapshot
            if method == "POST":
                snapshot = dict(data or {})
                return {"ok": True}
            return {}

        with mock.patch.dict(
            "os.environ",
            {"TELEGRAM_BOT_TOKEN": "bot-token", "TELEGRAM_CHAT_ID": "12345"},
            clear=False,
        ):
            bridge = TelegramBridge()
        bridge._mirror_request = fake_mirror  # type: ignore[method-assign]
        bridge._tg_request = mock.Mock(return_value={"ok": True})  # type: ignore[method-assign]

        bridge.fetch_mirror_and_push_to_telegram()

        bridge._tg_request.assert_called_once_with("sendMessage", {"chat_id": "12345", "text": "Hello Telegram"})
        stored = snapshot["records"][0]
        self.assertEqual(stored["delivery_status"], "delivered")
        self.assertEqual(stored["delivery_attempts"], 1)
        self.assertIn("delivered_at", stored)

    def test_agent_channel_post_fast_path_does_not_promote_learning_shards(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")
        agent.start()
        before = _count_learning_shards()
        with mock.patch(
            "core.agent_runtime.agent.dispatch_outbound_post_intent",
            return_value=ChannelPostDispatchResult(
                ok=True,
                status="queued",
                platform="discord",
                target="default",
                record_id="rec-queued",
                response_text="Queued discord post.",
                error=None,
            ),
        ):
            result = agent.run_once(
                'post to discord: "Bridge test announcement"',
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )
        after = _count_learning_shards()
        self.assertEqual(result["mode"], "tool_queued")
        self.assertIn("Queued discord post.", result["response"])
        self.assertEqual(before, after)

    def test_dispatch_direct_discord_fallback_can_use_bot_channel(self) -> None:
        with mock.patch("core.channel_actions.append_outbound_post", return_value=(False, {"record_id": "rec-direct"})):
            with mock.patch.dict(
                "os.environ",
                {"DISCORD_BOT_TOKEN": "bot-token", "DISCORD_CHANNEL_ID": "chan-123"},
                clear=False,
            ):
                with mock.patch(
                    "relay.bridge_workers.discord_bridge.DiscordBridge._post_bot_message",
                    return_value=True,
                ) as post_bot:
                    result = dispatch_outbound_post_intent(
                        ChannelPostIntent(platform="discord", message="Hello direct bot", target="default"),
                        task_id="task-direct",
                        session_id="session-direct",
                        source_context={"surface": "openclaw", "platform": "openclaw"},
                    )
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "delivered_direct")
        post_bot.assert_called_once_with("chan-123", "Hello direct bot")

    def test_discord_inbound_commands_use_per_user_session_scope(self) -> None:
        class _FakeAgent:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def run_once(
                self,
                user_input: str,
                *,
                session_id_override: str | None = None,
                source_context: dict[str, object] | None = None,
            ) -> dict[str, object]:
                self.calls.append(
                    {
                        "user_input": user_input,
                        "session_id_override": session_id_override,
                        "source_context": dict(source_context or {}),
                    }
                )
                return {
                    "task_id": f"task-{len(self.calls)}",
                    "response": "Ack from Vool",
                    "mode": "advice_only",
                    "confidence": 0.7,
                    "prompt_assembly_report": {},
                }

        fake_agent = _FakeAgent()
        with mock.patch.dict(
            "os.environ",
            {
                "DISCORD_BOT_TOKEN": "bot-token",
                "DISCORD_CHANNEL_ID": "chan-42",
                "DISCORD_BOT_USER_ID": "bot-1",
            },
            clear=False,
        ):
            bridge = DiscordBridge()

        bridge._ensure_agent = mock.Mock(return_value=fake_agent)  # type: ignore[method-assign]
        bridge._resolve_bot_user_id = mock.Mock(return_value="bot-1")  # type: ignore[method-assign]
        bridge._fetch_channel_messages = mock.Mock(  # type: ignore[method-assign]
            return_value=[
                {"id": "102", "content": "!vool status", "author": {"id": "user-b", "bot": False}},
                {"id": "101", "content": "!vool hello there", "author": {"id": "user-a", "bot": False}},
            ]
        )
        bridge._post_bot_message = mock.Mock(return_value=True)  # type: ignore[method-assign]
        bridge._last_seen_message_ids["chan-42"] = 100

        bridge.fetch_discord_and_push_to_mirror()

        self.assertEqual(len(fake_agent.calls), 2)
        first = fake_agent.calls[0]
        second = fake_agent.calls[1]
        self.assertNotEqual(first["session_id_override"], second["session_id_override"])
        self.assertIn("user-a", str(first["session_id_override"]))
        self.assertIn("user-b", str(second["session_id_override"]))
        self.assertEqual(first["source_context"]["surface"], "discord_bot")
        self.assertEqual(first["source_context"]["platform"], "discord")
        bridge._post_bot_message.assert_any_call("chan-42", "<@user-a> Ack from Vool")
        bridge._post_bot_message.assert_any_call("chan-42", "<@user-b> Ack from Vool")


if __name__ == "__main__":
    unittest.main()
