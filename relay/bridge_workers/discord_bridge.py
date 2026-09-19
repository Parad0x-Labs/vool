from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib import error
from urllib.parse import urlencode

from core import remote_fetch_policy
from core.channel_gateway import ChannelRequest, process_channel_request
from relay.channel_outbound import TOPIC_BY_PLATFORM


class DiscordBridge:
    def __init__(self):
        self.webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
        self.webhook_urls = self._load_webhook_map()
        self.bot_token = os.environ.get("DISCORD_BOT_TOKEN")
        self.bot_user_id = os.environ.get("DISCORD_BOT_USER_ID")
        self.default_channel_id = os.environ.get("DISCORD_CHANNEL_ID")
        self.channel_ids = self._load_channel_map()
        self.mirror_url = os.environ.get("VOOL_MIRROR_URL", "http://127.0.0.1:8787").rstrip("/")
        self.sync_topic = os.environ.get("VOOL_DISCORD_OUTBOUND_TOPIC", TOPIC_BY_PLATFORM["discord"])
        self._last_seen_message_ids: dict[str, int] = {}
        self._agent = None

    def is_configured(self) -> bool:
        outbound_ready = bool(self.webhook_url or self.webhook_urls or (self.bot_token and (self.default_channel_id or self.channel_ids)))
        inbound_ready = bool(self.bot_token and (self.default_channel_id or self.channel_ids))
        return outbound_ready or inbound_ready

    def _load_webhook_map(self) -> dict[str, str]:
        raw = str(os.environ.get("DISCORD_WEBHOOK_URLS_JSON", "")).strip()
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        out: dict[str, str] = {}
        for key, value in payload.items():
            name = str(key or "").strip().lower()
            url = str(value or "").strip()
            if name and url:
                out[name] = url
        return out

    def _load_channel_map(self) -> dict[str, str]:
        raw = str(os.environ.get("DISCORD_CHANNEL_IDS_JSON", "")).strip()
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        out: dict[str, str] = {}
        for key, value in payload.items():
            name = str(key or "").strip().lower()
            channel_id = str(value or "").strip()
            if name and channel_id:
                out[name] = channel_id
        return out

    def _resolve_webhook_url(self, target: str | None) -> str | None:
        alias = str(target or "default").strip().lower() or "default"
        return self.webhook_urls.get(alias) or self.webhook_url

    def _resolve_channel_id(self, target: str | None) -> str | None:
        alias = str(target or "default").strip().lower() or "default"
        return self.channel_ids.get(alias) or self.default_channel_id

    def _discord_webhook_post(self, content: str, *, webhook_url: str | None = None) -> bool:
        target_url = str(webhook_url or "").strip() or self.webhook_url
        if not target_url:
            return False
        payload = {"content": content}

        try:
            body = json.dumps(payload).encode('utf-8')
            with remote_fetch_policy.open_remote_url(
                target_url,
                data=body,
                headers={
                    'Content-Type': 'application/json',
                    'User-Agent': 'VoolDiscordBridge/1.0',
                },
                method="POST",
                timeout=10,
            ) as response:
                return response.status in (200, 204)
        except (error.URLError, remote_fetch_policy.RemoteFetchRefusedError) as e:
            print(f"[DiscordBridge] Error posting to Discord webhook: {e}")
            return False

    def _discord_api_request(self, path: str, *, method: str = "GET", data: dict | None = None) -> Any:
        if not self.bot_token:
            return {}
        url = f"https://discord.com/api/v10{path}"
        try:
            body = json.dumps(data).encode("utf-8") if data else None
            with remote_fetch_policy.open_remote_url(
                url,
                data=body,
                headers={
                    "Authorization": f"Bot {self.bot_token}",
                    "Content-Type": "application/json",
                    "User-Agent": "VoolDiscordBridge/1.0",
                },
                method=method,
                timeout=10,
            ) as response:
                raw = response.read().decode("utf-8") if response.length != 0 else ""
                if not raw:
                    return {}
                return json.loads(raw)
        except (error.URLError, remote_fetch_policy.RemoteFetchRefusedError) as e:
            print(f"[DiscordBridge] Error calling Discord API {path}: {e}")
            return {}

    def _post_bot_message(self, channel_id: str, content: str) -> bool:
        if not self.bot_token or not channel_id:
            return False
        payload = {"content": content}
        resp = self._discord_api_request(f"/channels/{channel_id}/messages", method="POST", data=payload)
        return bool(isinstance(resp, dict) and resp.get("id"))

    def _fetch_channel_messages(self, channel_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        if not channel_id:
            return []
        query = urlencode({"limit": max(1, min(100, int(limit)))})
        payload = self._discord_api_request(f"/channels/{channel_id}/messages?{query}", method="GET")
        return payload if isinstance(payload, list) else []

    def _resolve_bot_user_id(self) -> str | None:
        if self.bot_user_id:
            return self.bot_user_id
        payload = self._discord_api_request("/users/@me", method="GET")
        bot_id = str((payload or {}).get("id") or "").strip()
        if bot_id:
            self.bot_user_id = bot_id
        return self.bot_user_id

    def _ensure_agent(self):
        if self._agent is not None:
            return self._agent
        from apps.vool_agent import VoolAgent

        backend = os.environ.get("VOOL_CHANNEL_AGENT_BACKEND", "auto")
        device = os.environ.get("VOOL_CHANNEL_AGENT_DEVICE", "discord-bridge")
        persona_id = os.environ.get("VOOL_CHANNEL_PERSONA_ID", "default")
        agent = VoolAgent(backend_name=backend, device=device, persona_id=persona_id)
        agent.start()
        self._agent = agent
        return agent

    def _extract_command_text(self, content: str) -> str | None:
        text = str(content or "").strip()
        if not text:
            return None
        lowered = text.lower()
        if lowered.startswith("!vool "):
            return text[7:].strip()
        if lowered.startswith("vool:"):
            return text[6:].strip()
        if lowered.startswith("vool,"):
            return text[6:].strip()
        bot_id = self._resolve_bot_user_id()
        if bot_id:
            mention_prefixes = (f"<@{bot_id}>", f"<@!{bot_id}>")
            for prefix in mention_prefixes:
                if text.startswith(prefix):
                    return text[len(prefix):].strip()
        return None

    def _mirror_request(self, path: str, method: str = "GET", data: dict | None = None) -> dict:
        try:
            body = json.dumps(data).encode('utf-8') if data else None
            with remote_fetch_policy.open_remote_url(
                f"{self.mirror_url}{path}",
                data=body,
                headers={'Content-Type': 'application/json'},
                method=method,
                timeout=10,
            ) as response:
                return json.loads(response.read().decode('utf-8'))
        except (error.URLError, remote_fetch_policy.RemoteFetchRefusedError) as e:
            print(f"[DiscordBridge] Error calling mirror {path}: {e}")
            return {}

    def fetch_mirror_and_push_to_discord(self):
        """Poll the outbound queue and deliver pending Discord posts."""
        snapshot = self._mirror_request(f"/topics/{self.sync_topic}")
        records = snapshot.get("records")
        if not isinstance(records, list) or not records:
            return

        changed = False
        for raw_record in records:
            if not isinstance(raw_record, dict):
                continue
            record = dict(raw_record)
            if str(record.get("kind") or "") != "outbound_post":
                continue
            if str(record.get("platform") or "").lower() != "discord":
                continue
            if str(record.get("delivery_status") or "pending").lower() == "delivered":
                continue

            webhook_url = self._resolve_webhook_url(str(record.get("target") or "default"))
            attempts = int(record.get("delivery_attempts") or 0) + 1
            record["delivery_attempts"] = attempts
            record["last_attempted_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

            content = str(record.get("content") or "").strip()
            if not content:
                record["last_error"] = "missing_content"
                changed = True
                raw_record.update(record)
                continue

            channel_id = self._resolve_channel_id(str(record.get("target") or "default")) if not webhook_url else None
            if not webhook_url and not channel_id:
                record["last_error"] = "missing_delivery_target"
                changed = True
                raw_record.update(record)
                continue

            delivered = False
            if webhook_url:
                delivered = self._discord_webhook_post(content, webhook_url=webhook_url)
            elif channel_id:
                delivered = self._post_bot_message(channel_id, content)

            if delivered:
                record["delivery_status"] = "delivered"
                record["delivered_at"] = record["last_attempted_at"]
                record["last_error"] = None
                # R-7/H-5 (K-10): the bridge PROPOSES typed recipient-class
                # evidence; canonical reconciliation authors DELIVERED.
                _fid = str(record.get("finalization_id") or "")
                if _fid:
                    from relay.channel_outbound import propose_bridge_delivery

                    propose_bridge_delivery(_fid, evidence_class="PLATFORM_ACK")
            else:
                # Bounded retry: past the cap the drop is a typed FAILED mark
                # on the committed truth — never silent infinite retry.
                attempts = int(record.get("delivery_attempts") or 0)
                max_attempts = int(record.get("max_delivery_attempts") or 8)
                if attempts >= max_attempts:
                    record["delivery_status"] = "failed"
                    record["last_error"] = "delivery_retry_cap_exceeded"
                    _fid = str(record.get("finalization_id") or "")
                    if _fid:
                        from core.finalization import (
                            DELIVERY_FAILED_TRANSPORT,
                        )
                        from core.finalization import (
                            set_delivery_status as _sds,
                        )

                        _sds(_fid, DELIVERY_FAILED_TRANSPORT, evidence_class="TRANSPORT_HANDOFF")
                else:
                    record["delivery_status"] = "pending"
                    record["last_error"] = "discord_delivery_failed"
            changed = True
            raw_record.update(record)

        if changed:
            snapshot["record_count"] = len(records)
            self._mirror_request(f"/publish/{self.sync_topic}", method="POST", data=snapshot)

    def fetch_discord_and_push_to_mirror(self):
        """Poll configured Discord channels for directed commands and route them into VOOL."""
        inbound_channel_ids = {str(v).strip() for v in self.channel_ids.values() if str(v).strip()}
        if self.default_channel_id:
            inbound_channel_ids.add(str(self.default_channel_id).strip())
        if not self.bot_token or not inbound_channel_ids:
            return

        agent = self._ensure_agent()
        bot_user_id = self._resolve_bot_user_id()
        for channel_id in sorted(inbound_channel_ids):
            messages = self._fetch_channel_messages(channel_id, limit=20)
            if not messages:
                continue

            newest_seen = max(int(str(msg.get("id") or "0")) for msg in messages if str(msg.get("id") or "0").isdigit())
            if channel_id not in self._last_seen_message_ids:
                self._last_seen_message_ids[channel_id] = newest_seen
                continue

            last_seen = self._last_seen_message_ids.get(channel_id, 0)
            pending = [
                msg
                for msg in reversed(messages)
                if str(msg.get("id") or "0").isdigit() and int(str(msg.get("id"))) > last_seen
            ]
            if newest_seen > last_seen:
                self._last_seen_message_ids[channel_id] = newest_seen

            for message in pending:
                author = message.get("author") or {}
                author_id = str(author.get("id") or "").strip()
                if not author_id:
                    continue
                if author.get("bot") or (bot_user_id and author_id == bot_user_id):
                    continue

                command_text = self._extract_command_text(str(message.get("content") or ""))
                if not command_text:
                    continue

                # CANONICAL INGRESS (see core/channel_gateway.process_channel_request):
                # Discord enters the agent through the one declared KAS→VOOL
                # channel ingress — owner-local=False stamped at the gateway,
                # A7-finalized bytes served back.
                result = process_channel_request(
                    agent,
                    ChannelRequest(
                        platform="discord",
                        user_id=author_id,
                        channel_id=channel_id,
                        text=command_text,
                        device_hint="channel",
                        surface="discord_bot",
                    ),
                )
                reply = f"<@{author_id}> {result.response_text}".strip()
                self._post_bot_message(channel_id, reply)

    def run_forever(self):
        # R2b2a1 — the OWNING ENTRY POINT of this daemon's outbound effects.
        # One named scope binds the COMPLETE loop, in the executing thread; the
        # low-level send helpers stay dumb and the door grants nothing. The
        # binding is restored on normal exit and on exceptions. R2b2c: the
        # scope is DURABLE — every outbound effect's lifecycle is published to
        # the runtime-event store as it occurs (this loop never closes), so
        # the daemon's evidence survives it.
        from core.effect_gateway import named_background_effect_scope

        with named_background_effect_scope("relay.discord", durable=True):
            if not self.is_configured():
                print("[DiscordBridge] Missing Discord webhook or bot configuration. Exiting.")
                return

            print(f"[DiscordBridge] Started syncing between {self.mirror_url} and Discord.")
            while True:
                self.fetch_discord_and_push_to_mirror()
                self.fetch_mirror_and_push_to_discord()
                time.sleep(5)


if __name__ == "__main__":
    DiscordBridge().run_forever()
