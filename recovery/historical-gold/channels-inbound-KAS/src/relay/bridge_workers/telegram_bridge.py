from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any
from urllib import error, request

from core.connector_awareness import (
    TELEGRAM_CONNECTOR_DESCRIPTOR_TOPIC,
    build_connector_descriptor,
    build_connector_descriptor_snapshot,
    destination_ref,
)
from relay.channel_outbound import (
    TOPIC_BY_PLATFORM,
    has_valid_outbound_delivery_metadata,
    is_canonical_destination_ref,
    next_delivery_attempts,
    update_outbound_delivery,
)

_DELIVERY_UPDATE_FIELDS = ("delivery_status", "delivery_attempts", "last_attempted_at", "last_error", "delivered_at")


class TelegramBridge:
    def __init__(self):
        self.bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        self.chat_ids = self._load_chat_map()
        self.mirror_url = os.environ.get("VOOL_MIRROR_URL", "http://127.0.0.1:8787").rstrip("/")
        self.topic_name = "telegram_bridge_topic"
        self.last_update_id = 0
        self.last_mirror_scan: dict[str, str] = {}
        self.outbound_topic = os.environ.get("VOOL_TELEGRAM_OUTBOUND_TOPIC", TOPIC_BY_PLATFORM["telegram"])

    def is_configured(self) -> bool:
        return bool(self.bot_token and (self.chat_id or self.chat_ids))

    def _load_chat_map(self) -> dict[str, str]:
        raw = str(os.environ.get("TELEGRAM_CHAT_IDS_JSON", "")).strip()
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
            chat_id = str(value or "").strip()
            if name and chat_id:
                out[name] = chat_id
        return out

    def _resolve_chat_id(self, target: str | None) -> str | None:
        alias = str(target or "default").strip().lower() or "default"
        return self.chat_ids.get(alias) or self.chat_id

    def _effective_outbound_routes(self) -> dict[str, str]:
        routes: dict[str, str] = {}
        if self.bot_token:
            for alias, chat_id in self.chat_ids.items():
                routes[destination_ref("telegram", alias, "bot")] = chat_id
            if self.chat_id:
                routes.setdefault(destination_ref("telegram", "default", "bot"), self.chat_id)
        return routes

    def _resolve_destination_ref(self, value: object) -> str | None:
        if not is_canonical_destination_ref("telegram", value):
            return None
        return self._effective_outbound_routes().get(value)

    @staticmethod
    def _outbound_record_for_delivery(raw_record: object) -> dict[str, Any] | None:
        if type(raw_record) is not dict:
            return None
        if type(raw_record.get("kind")) is not str or raw_record["kind"] != "outbound_post":
            return None
        if type(raw_record.get("platform")) is not str or raw_record["platform"] != "telegram":
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
        if self.bot_token:
            for alias in self.chat_ids:
                destinations[alias] = "bot"
            if self.chat_id:
                destinations.setdefault("default", "bot")
        return build_connector_descriptor(
            connector="telegram",
            configured=self.is_configured(),
            destinations=destinations.items(),
        )

    def _publish_connector_descriptor(self) -> bool:
        try:
            descriptor = self.connector_descriptor()
            snapshot = build_connector_descriptor_snapshot("telegram", descriptor)
            response = self._mirror_request(
                f"/publish/{TELEGRAM_CONNECTOR_DESCRIPTOR_TOPIC}",
                method="POST",
                data=snapshot,
            )
            return bool(response.get("ok"))
        except Exception:
            return False

    def _tg_request(self, method: str, data: dict | None = None) -> dict:
        url = f"https://api.telegram.org/bot{self.bot_token}/{method}"
        req = request.Request(url)
        req.add_header('Content-Type', 'application/json')
        try:
            body = json.dumps(data).encode('utf-8') if data else None
            with request.urlopen(req, data=body, timeout=10) as response:
                return json.loads(response.read().decode('utf-8'))
        except error.URLError as exc:
            operation = {"sendMessage": "send_message", "getUpdates": "get_updates"}.get(method, "telegram_provider_request")
            status = getattr(exc, "code", None)
            if type(status) is int:
                print(f"[TelegramBridge] Telegram provider request failed ({operation}, HTTP {status})")
            else:
                print(f"[TelegramBridge] Telegram provider request failed ({operation}, {type(exc).__name__})")
            return {}

    def _mirror_request(self, path: str, method: str = "GET", data: dict | None = None) -> dict:
        req = request.Request(f"{self.mirror_url}{path}", method=method)
        req.add_header('Content-Type', 'application/json')
        try:
            body = json.dumps(data).encode('utf-8') if data else None
            with request.urlopen(req, data=body, timeout=10) as response:
                return json.loads(response.read().decode('utf-8'))
        except error.URLError as e:
            print(f"[TelegramBridge] Error calling mirror {path}: {e}")
            return {}

    def fetch_telegram_and_push_to_mirror(self):
        """Polls Telegram for new messages and pushes them to the local HTTP Mirror."""
        resp = self._tg_request("getUpdates", {"offset": self.last_update_id + 1, "timeout": 5})
        if not resp or not resp.get("ok"):
            return

        updates = resp.get("result", [])
        if not updates:
            return

        records = []
        for update in updates:
            update_id = update["update_id"]
            if update_id > self.last_update_id:
                self.last_update_id = update_id

            message = update.get("message")
            if not message or not message.get("text"):
                continue

            # In a real scenario, this text might be a serialized TASK_OFFER or snapshot json.
            # We wrap it nicely.
            records.append({
                "source": "telegram",
                "message_id": message["message_id"],
                "text": message["text"],
                "timestamp": datetime.now(timezone.utc).isoformat()
            })

        if not records:
            return

        snapshot = {
            "topic_name": self.topic_name,
            "publisher_peer_id": "telegram_bridge_worker",
            "published_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": datetime.now(timezone.utc).isoformat(), # mock
            "record_count": len(records),
            "records": records,
            "snapshot_hash": "mock_hash",
            "signature": "mock_signature"
        }
        self._mirror_request(f"/publish/{self.topic_name}", method="POST", data=snapshot)
        print(f"[TelegramBridge] Pushed {len(records)} records to mirror topic {self.topic_name}")

    def fetch_mirror_and_push_to_telegram(self):
        """Poll the outbound queue and deliver pending Telegram posts."""
        snapshot = self._mirror_request(f"/topics/{self.outbound_topic}")
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
            record["last_attempted_at"] = datetime.now(timezone.utc).isoformat()
            content = record["content"].strip()
            if "destination_ref" in record:
                chat_id = self._resolve_destination_ref(record.get("destination_ref"))
                if chat_id is None:
                    record["delivery_status"] = "failed"
                    record["last_error"] = "destination_not_configured"
                    update_outbound_delivery(platform="telegram", record_id=record["record_id"], fields={key: record.get(key) for key in _DELIVERY_UPDATE_FIELDS})
                    continue
            else:
                chat_id = self._resolve_chat_id(record.get("target", "default"))

            if not content:
                record["last_error"] = "missing_content"
                update_outbound_delivery(platform="telegram", record_id=record["record_id"], fields={key: record.get(key) for key in _DELIVERY_UPDATE_FIELDS})
                continue

            if not chat_id:
                record["last_error"] = "missing_chat_id"
                update_outbound_delivery(platform="telegram", record_id=record["record_id"], fields={key: record.get(key) for key in _DELIVERY_UPDATE_FIELDS})
                continue

            result = self._tg_request("sendMessage", {"chat_id": chat_id, "text": content})
            if result.get("ok"):
                record["delivery_status"] = "delivered"
                record["delivered_at"] = record["last_attempted_at"]
                record["last_error"] = None
            else:
                record["delivery_status"] = "pending"
                record["last_error"] = "telegram_send_failed"
            update_outbound_delivery(platform="telegram", record_id=record["record_id"], fields={key: record.get(key) for key in _DELIVERY_UPDATE_FIELDS})

    def run_forever(self):
        self._publish_connector_descriptor()
        if not self.is_configured():
            print("[TelegramBridge] Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID. Exiting.")
            return

        print(f"[TelegramBridge] Started syncing between {self.mirror_url} and Telegram.")
        while True:
            self.fetch_telegram_and_push_to_mirror()
            self.fetch_mirror_and_push_to_telegram()
            time.sleep(5)


if __name__ == "__main__":
    TelegramBridge().run_forever()
