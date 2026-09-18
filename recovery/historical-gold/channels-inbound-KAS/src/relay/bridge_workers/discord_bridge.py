from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib import error, request
from urllib.parse import urlencode

from core.connector_awareness import (
    DISCORD_CONNECTOR_DESCRIPTOR_TOPIC,
    build_connector_descriptor,
    build_connector_descriptor_snapshot,
    destination_ref,
)
from core.discord_recent_observations import (
    DISCORD_READ_SOURCES_TOPIC,
    MAX_DISCORD_MESSAGES_PER_SOURCE,
    MAX_DISCORD_READ_SOURCES,
    build_discord_read_sources,
    build_discord_read_sources_snapshot,
    build_discord_recent_messages_snapshot,
    discord_message_ref,
    discord_recent_messages_topic,
)
from core.discord_recent_observations import (
    parse_discord_message_id as _parse_discord_message_id,
)
from relay.bridge_workers.discord_command_parser import extract_discord_command_text
from relay.bridge_workers.discord_external_ingress import (
    open_discord_host_reply_route,
)
from relay.bridge_workers.discord_gateway_ingress import DiscordGatewayIngress
from relay.channel_outbound import (
    TOPIC_BY_PLATFORM,
    has_valid_outbound_delivery_metadata,
    is_canonical_destination_ref,
    is_discord_host_reply_record,
    next_delivery_attempts,
    update_outbound_delivery,
)

MAX_DISCORD_PROVIDER_RESPONSE_BYTES = 262_144
_REPLYABLE_DISCORD_MESSAGE_TYPES = frozenset({0, 19, 20, 23})
_DELIVERY_UPDATE_FIELDS = ("delivery_status", "delivery_attempts", "last_attempted_at", "last_error", "delivered_at")


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
        self._message_fetch_succeeded: bool | None = None
        self._agent = None
        self._gateway_ingress: DiscordGatewayIngress | None = None

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
            if type(key) is not str or type(value) is not str:
                continue
            name = key.strip().lower()
            channel_id = value.strip()
            if name and channel_id:
                out[name] = channel_id
        return out

    def _resolve_webhook_url(self, target: str | None) -> str | None:
        alias = str(target or "default").strip().lower() or "default"
        return self.webhook_urls.get(alias) or self.webhook_url

    def _resolve_channel_id(self, target: str | None) -> str | None:
        alias = str(target or "default").strip().lower() or "default"
        return self.channel_ids.get(alias) or self.default_channel_id

    def _effective_outbound_routes(self) -> dict[str, tuple[str, str]]:
        routes: dict[str, tuple[str, str]] = {}
        webhook_labels: set[str] = set()
        for alias, webhook_url in self.webhook_urls.items():
            routes[destination_ref("discord", alias, "webhook")] = ("webhook", webhook_url)
            webhook_labels.add(alias)
        if self.webhook_url:
            routes.setdefault(destination_ref("discord", "default", "webhook"), ("webhook", self.webhook_url))
            webhook_labels.add("default")
        if self.bot_token:
            for alias, channel_id in self.channel_ids.items():
                if alias not in webhook_labels:
                    routes[destination_ref("discord", alias, "bot")] = ("bot", channel_id)
            if self.default_channel_id and "default" not in webhook_labels:
                routes[destination_ref("discord", "default", "bot")] = ("bot", self.default_channel_id)
        return routes

    def _resolve_destination_ref(self, value: object) -> tuple[str, str] | None:
        if not is_canonical_destination_ref("discord", value):
            return None
        return self._effective_outbound_routes().get(value)

    @staticmethod
    def _outbound_record_for_delivery(raw_record: object) -> dict[str, Any] | None:
        if type(raw_record) is not dict:
            return None
        if type(raw_record.get("kind")) is not str or raw_record["kind"] != "outbound_post":
            return None
        if type(raw_record.get("platform")) is not str or raw_record["platform"] != "discord":
            return None
        if type(raw_record.get("record_id")) is not str or not raw_record["record_id"] or len(raw_record["record_id"]) > 128:
            return None
        if not has_valid_outbound_delivery_metadata(raw_record):
            return None
        if raw_record["delivery_status"] != "pending":
            return None
        if type(raw_record.get("content")) is not str:
            return None
        if "destination_ref" in raw_record:
            if not any(is_canonical_destination_ref(platform, raw_record["destination_ref"]) for platform in TOPIC_BY_PLATFORM):
                return None
        elif type(raw_record.get("target", "default")) is not str:
            return None
        return dict(raw_record)

    def connector_descriptor(self) -> dict[str, Any]:
        destinations: dict[str, str] = {}
        for alias in self.webhook_urls:
            destinations[alias] = "webhook"
        if self.webhook_url:
            destinations.setdefault("default", "webhook")
        if self.bot_token:
            for alias in self.channel_ids:
                destinations.setdefault(alias, "bot")
            if self.default_channel_id:
                destinations.setdefault("default", "bot")
        return build_connector_descriptor(
            connector="discord",
            configured=self.is_configured(),
            destinations=destinations.items(),
        )

    def _publish_connector_descriptor(self) -> bool:
        try:
            descriptor = self.connector_descriptor()
            snapshot = build_connector_descriptor_snapshot("discord", descriptor)
            response = self._mirror_request(
                f"/publish/{DISCORD_CONNECTOR_DESCRIPTOR_TOPIC}",
                method="POST",
                data=snapshot,
            )
            return bool(response.get("ok"))
        except Exception:
            return False

    def _discord_webhook_post(self, content: str, *, webhook_url: str | None = None) -> bool:
        target_url = str(webhook_url or "").strip() or self.webhook_url
        if not target_url:
            return False
        req = request.Request(target_url, method="POST")
        req.add_header('Content-Type', 'application/json')
        req.add_header('User-Agent', 'VoolDiscordBridge/1.0')
        payload = {"content": content}

        try:
            body = json.dumps(payload).encode('utf-8')
            with request.urlopen(req, data=body, timeout=10) as response:
                return response.status in (200, 204)
        except error.URLError:
            print("[DiscordBridge] Discord webhook request failed")
            return False

    def _discord_api_request(
        self,
        path: str,
        *,
        operation: str = "discord_api_request",
        method: str = "GET",
        data: dict | None = None,
        max_response_bytes: int | None = None,
    ) -> Any:
        if not self.bot_token:
            return {}
        url = f"https://discord.com/api/v10{path}"
        req = request.Request(url, method=method)
        req.add_header("Authorization", f"Bot {self.bot_token}")
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "VoolDiscordBridge/1.0")
        try:
            body = json.dumps(data).encode("utf-8") if data else None
            with request.urlopen(req, data=body, timeout=10) as response:
                if max_response_bytes is None:
                    raw = response.read().decode("utf-8") if response.length != 0 else ""
                else:
                    raw_bytes = response.read(max_response_bytes + 1)
                    if len(raw_bytes) > max_response_bytes:
                        return {}
                    raw = raw_bytes.decode("utf-8")
                if not raw:
                    return {}
                return json.loads(raw)
        except (error.URLError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            status = getattr(exc, "code", None)
            if type(status) is int:
                print(f"[DiscordBridge] Discord API request failed ({operation}, HTTP {status})")
            else:
                print(f"[DiscordBridge] Discord API request failed ({operation}, {type(exc).__name__})")
            return {}

    def _post_bot_message(self, channel_id: str, content: str, *, reply_message_id: str | None = None) -> bool:
        if not self.bot_token or not channel_id:
            return False
        payload = {"content": content}
        if reply_message_id is not None:
            if type(reply_message_id) is not str or _parse_discord_message_id(reply_message_id) is None:
                return False
            payload["message_reference"] = {
                "type": 0,
                "message_id": reply_message_id,
                "fail_if_not_exists": True,
            }
        resp = self._discord_api_request(
            f"/channels/{channel_id}/messages",
            operation="send_bot_message",
            method="POST",
            data=payload,
        )
        return bool(isinstance(resp, dict) and resp.get("id"))

    def _post_host_reply(self, channel_id: str, message_id: str, content: str) -> bool:
        if (
            not self.bot_token
            or _parse_discord_message_id(channel_id) is None
            or _parse_discord_message_id(message_id) is None
            or type(content) is not str
            or not content.strip()
            or len(content) > 2000
        ):
            return False
        response = self._discord_api_request(
            f"/channels/{channel_id}/messages",
            operation="send_host_reply",
            method="POST",
            data={
                "content": content,
                "message_reference": {
                    "type": 0,
                    "message_id": message_id,
                    "fail_if_not_exists": True,
                },
                "allowed_mentions": {"parse": [], "replied_user": True},
            },
        )
        return bool(type(response) is dict and type(response.get("id")) is str and response["id"])

    def _fetch_channel_messages_result(
        self, channel_id: str, *, limit: int = 20
    ) -> list[dict[str, Any]] | None:
        if not channel_id:
            return None
        query_values: dict[str, Any] = {"limit": max(1, min(100, int(limit)))}
        query = urlencode(query_values)
        payload = self._discord_api_request(
            f"/channels/{channel_id}/messages?{query}",
            operation="fetch_channel_messages",
            method="GET",
            max_response_bytes=MAX_DISCORD_PROVIDER_RESPONSE_BYTES,
        )
        self._message_fetch_succeeded = type(payload) is list
        return payload if self._message_fetch_succeeded else None

    def _fetch_channel_messages(self, channel_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        return self._fetch_channel_messages_result(channel_id, limit=limit) or []

    def _read_sources_by_channel(self) -> dict[str, list[dict[str, Any]]]:
        if not self.bot_token:
            return {}
        aliases = dict(self.channel_ids)
        if self.default_channel_id and "default" not in aliases:
            aliases["default"] = self.default_channel_id
        if len(aliases) > MAX_DISCORD_READ_SOURCES:
            return {}
        try:
            sources = build_discord_read_sources(list(aliases))
        except ValueError:
            return {}
        result: dict[str, list[dict[str, Any]]] = {}
        for source in sources:
            channel_id = str(aliases[source["label"]]).strip()
            if channel_id:
                result.setdefault(channel_id, []).append(source)
        return result

    def _reply_sources(self) -> dict[str, str]:
        return {
            str(source["ref"]): channel_id
            for channel_id, sources in self._read_sources_by_channel().items()
            for source in sources
        }

    def _inbound_channel_ids(self) -> set[str]:
        if not self.bot_token:
            return set()
        channel_ids = {channel_id for channel_id in self.channel_ids.values() if channel_id}
        if self.default_channel_id:
            channel_ids.add(self.default_channel_id)
        return channel_ids

    def _publish_read_sources(self, sources_by_channel: dict[str, list[dict[str, Any]]]) -> bool:
        try:
            sources = [source for items in sources_by_channel.values() for source in items]
            snapshot = build_discord_read_sources_snapshot(sources)
            return bool(self._mirror_request(f"/publish/{DISCORD_READ_SOURCES_TOPIC}", method="POST", data=snapshot).get("ok"))
        except Exception:
            return False

    def _publish_recent_observations(self, channel_id: str, sources: list[dict[str, Any]], messages: list[dict[str, Any]]) -> None:
        for source in sources:
            try:
                snapshot = build_discord_recent_messages_snapshot(source_ref=source["ref"], channel_id=channel_id, messages=messages)
                self._mirror_request(f"/publish/{discord_recent_messages_topic(source['ref'])}", method="POST", data=snapshot)
            except Exception:
                continue

    def _resolve_bot_user_id(self) -> str | None:
        if type(self.bot_user_id) is str and _parse_discord_message_id(self.bot_user_id) is not None:
            return self.bot_user_id
        self.bot_user_id = None
        payload = self._discord_api_request("/users/@me", operation="resolve_bot_identity", method="GET")
        bot_id = payload.get("id") if type(payload) is dict else None
        if type(bot_id) is str and _parse_discord_message_id(bot_id) is not None:
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

    def _extract_command_text(self, content: str, *, bot_user_id: str | None = None) -> str | None:
        bot_id = bot_user_id if bot_user_id is not None else self._resolve_bot_user_id()
        mention_matches = (lambda candidate: candidate == bot_id) if bot_id else None
        return extract_discord_command_text(content, mention_matches=mention_matches)

    @staticmethod
    def _looks_like_directed_command(content: object) -> bool:
        return bool(
            extract_discord_command_text(
                content,
                mention_matches=lambda _candidate: True,
            )
        )

    def _gateway_component(self) -> DiscordGatewayIngress:
        if self._gateway_ingress is None:
            if not self.bot_token:
                raise ValueError("Discord bot token is required for Gateway ingress")
            self._gateway_ingress = DiscordGatewayIngress(
                bot_token=self.bot_token,
                configured_channels=self._inbound_channel_ids,
                read_sources_by_channel=self._read_sources_by_channel,
                ensure_agent=self._ensure_agent,
            )
        return self._gateway_ingress
    def _mirror_request(self, path: str, method: str = "GET", data: dict | None = None) -> dict:
        req = request.Request(f"{self.mirror_url}{path}", method=method)
        req.add_header('Content-Type', 'application/json')
        try:
            body = json.dumps(data).encode('utf-8') if data else None
            with request.urlopen(req, data=body, timeout=10) as response:
                return json.loads(response.read().decode('utf-8'))
        except error.URLError as e:
            print(f"[DiscordBridge] Error calling mirror {path}: {e}")
            return {}

    def fetch_mirror_and_push_to_discord(self):
        """Poll the outbound queue and deliver pending Discord posts."""
        snapshot = self._mirror_request(f"/topics/{self.sync_topic}")
        if type(snapshot) is not dict:
            return
        records = snapshot.get("records")
        if type(records) is not list or not records:
            return

        for raw_record in records:
            record = self._outbound_record_for_delivery(raw_record)
            if record is None:
                continue

            attempts = next_delivery_attempts(record["delivery_attempts"])
            record["delivery_attempts"] = attempts
            record["last_attempted_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

            webhook_url: str | None = None
            channel_id: str | None = None
            reply_message_id: str | None = None
            is_reply = "reply_source_ref" in record
            is_host_reply = is_discord_host_reply_record(record)
            if is_host_reply:
                bot_user_id = self._resolve_bot_user_id()
                if bot_user_id is None:
                    record["last_error"] = "discord_host_reply_account_unavailable"
                    update_outbound_delivery(platform="discord", record_id=record["record_id"], fields={key: record.get(key) for key in _DELIVERY_UPDATE_FIELDS})
                    continue
                try:
                    channel_id, reply_message_id = open_discord_host_reply_route(
                        record.get("sealed_host_route"),
                        current_bot_user_id=bot_user_id,
                        account_ref=record.get("account_ref"),
                        conversation_ref=record.get("conversation_ref"),
                        event_ref=record.get("event_ref"),
                        reply_source_ref=record.get("reply_source_ref"),
                        reply_message_ref=record.get("reply_message_ref"),
                    )
                except Exception as exc:
                    record["delivery_status"] = "failed"
                    record["last_error"] = (
                        "discord_host_reply_account_mismatch"
                        if "account" in str(exc).lower()
                        else "discord_host_reply_route_invalid"
                    )
                    update_outbound_delivery(platform="discord", record_id=record["record_id"], fields={key: record.get(key) for key in _DELIVERY_UPDATE_FIELDS})
                    continue
            elif is_reply:
                channel_id = self._reply_sources().get(record["reply_source_ref"])
                if channel_id is None:
                    record["delivery_status"] = "failed"
                    record["last_error"] = "reply_source_not_configured"
                    update_outbound_delivery(platform="discord", record_id=record["record_id"], fields={key: record.get(key) for key in _DELIVERY_UPDATE_FIELDS})
                    continue
                messages = self._fetch_channel_messages_result(channel_id, limit=MAX_DISCORD_MESSAGES_PER_SOURCE)
                if messages is None:
                    record["last_error"] = "discord_reply_lookup_failed"
                    update_outbound_delivery(platform="discord", record_id=record["record_id"], fields={key: record.get(key) for key in _DELIVERY_UPDATE_FIELDS})
                    continue
                matched_message: dict[str, Any] | None = None
                for message in messages[:MAX_DISCORD_MESSAGES_PER_SOURCE]:
                    if type(message) is not dict:
                        continue
                    raw_message_id = message.get("id")
                    if type(raw_message_id) is not str or _parse_discord_message_id(raw_message_id) is None:
                        continue
                    if discord_message_ref(channel_id, raw_message_id) == record["reply_message_ref"]:
                        matched_message = message
                        break
                if matched_message is None:
                    record["delivery_status"] = "failed"
                    record["last_error"] = "reply_message_not_resolvable"
                    update_outbound_delivery(platform="discord", record_id=record["record_id"], fields={key: record.get(key) for key in _DELIVERY_UPDATE_FIELDS})
                    continue
                message_type = matched_message.get("type")
                if type(message_type) is not int or message_type not in _REPLYABLE_DISCORD_MESSAGE_TYPES:
                    record["delivery_status"] = "failed"
                    record["last_error"] = "reply_message_not_replyable"
                    update_outbound_delivery(platform="discord", record_id=record["record_id"], fields={key: record.get(key) for key in _DELIVERY_UPDATE_FIELDS})
                    continue
                reply_message_id = matched_message["id"]
            elif "destination_ref" in record:
                route = self._resolve_destination_ref(record.get("destination_ref"))
                if route is None:
                    record["delivery_status"] = "failed"
                    record["last_error"] = "destination_not_configured"
                    update_outbound_delivery(platform="discord", record_id=str(record.get("record_id") or ""), fields={key: record.get(key) for key in _DELIVERY_UPDATE_FIELDS})
                    continue
                transport, route_value = route
                webhook_url = route_value if transport == "webhook" else None
                channel_id = route_value if transport == "bot" else None
            else:
                target = record.get("target", "default")
                webhook_url = self._resolve_webhook_url(target)
                channel_id = self._resolve_channel_id(target) if not webhook_url else None

            content = record["content"] if is_reply else record["content"].strip()
            if not content:
                record["last_error"] = "missing_content"
                update_outbound_delivery(platform="discord", record_id=record["record_id"], fields={key: record.get(key) for key in _DELIVERY_UPDATE_FIELDS})
                continue

            if not webhook_url and not channel_id:
                record["last_error"] = "missing_delivery_target"
                update_outbound_delivery(platform="discord", record_id=record["record_id"], fields={key: record.get(key) for key in _DELIVERY_UPDATE_FIELDS})
                continue

            delivered = False
            if webhook_url:
                delivered = self._discord_webhook_post(content, webhook_url=webhook_url)
            elif channel_id:
                delivered = (
                    self._post_host_reply(channel_id, reply_message_id, content)
                    if is_host_reply and reply_message_id is not None
                    else self._post_bot_message(channel_id, content, reply_message_id=reply_message_id)
                    if is_reply
                    else self._post_bot_message(channel_id, content)
                )

            if delivered:
                record["delivery_status"] = "delivered"
                record["delivered_at"] = record["last_attempted_at"]
                record["last_error"] = None
            else:
                record["delivery_status"] = "pending"
                record["last_error"] = (
                    "discord_host_reply_failed" if is_host_reply else "discord_delivery_failed"
                )
            update_outbound_delivery(platform="discord", record_id=record["record_id"], fields={key: record.get(key) for key in _DELIVERY_UPDATE_FIELDS})

    def fetch_discord_and_push_to_mirror(self):
        """Publish the accepted M2 latest-window observation; never execute commands."""
        channel_ids = self._inbound_channel_ids()
        sources_by_channel = self._read_sources_by_channel()
        if not self.bot_token or not channel_ids:
            return
        if sources_by_channel:
            self._publish_read_sources(sources_by_channel)
        for channel_id in sorted(channel_ids):
            self._message_fetch_succeeded = None
            messages = self._fetch_channel_messages(
                channel_id, limit=MAX_DISCORD_MESSAGES_PER_SOURCE
            )
            if self._message_fetch_succeeded is False:
                continue
            self._publish_recent_observations(
                channel_id, sources_by_channel.get(channel_id, []), messages
            )
    def run_forever(self):
        self._publish_connector_descriptor()
        if not self.is_configured():
            print("[DiscordBridge] Missing Discord webhook or bot configuration. Exiting.")
            return

        gateway = self._gateway_component() if self.bot_token else None
        if gateway is not None:
            gateway.start()
        print(f"[DiscordBridge] Started syncing between {self.mirror_url} and Discord.")
        try:
            while True:
                self.fetch_discord_and_push_to_mirror()
                self.fetch_mirror_and_push_to_discord()
                time.sleep(5)
        finally:
            if gateway is not None:
                gateway.stop()
                gateway.join()


if __name__ == "__main__":
    DiscordBridge().run_forever()
