from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from urllib import error

from core import remote_fetch_policy
from relay.channel_outbound import TOPIC_BY_PLATFORM


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

    def _tg_request(self, method: str, data: dict | None = None) -> dict:
        url = f"https://api.telegram.org/bot{self.bot_token}/{method}"
        try:
            body = json.dumps(data).encode('utf-8') if data else None
            with remote_fetch_policy.open_remote_url(
                url, data=body, headers={'Content-Type': 'application/json'}, timeout=10
            ) as response:
                return json.loads(response.read().decode('utf-8'))
        except (error.URLError, remote_fetch_policy.RemoteFetchRefusedError) as e:
            print(f"[TelegramBridge] Error calling {method}: {e}")
            return {}

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
            if str(record.get("platform") or "").lower() != "telegram":
                continue
            if str(record.get("delivery_status") or "pending").lower() == "delivered":
                continue

            attempts = int(record.get("delivery_attempts") or 0) + 1
            record["delivery_attempts"] = attempts
            record["last_attempted_at"] = datetime.now(timezone.utc).isoformat()
            content = str(record.get("content") or "").strip()
            chat_id = self._resolve_chat_id(str(record.get("target") or "default"))

            if not content:
                record["last_error"] = "missing_content"
                changed = True
                raw_record.update(record)
                continue

            if not chat_id:
                record["last_error"] = "missing_chat_id"
                changed = True
                raw_record.update(record)
                continue

            result = self._tg_request("sendMessage", {"chat_id": chat_id, "text": content})
            # R-7/H-5 (K-10): the bridge PROPOSES typed evidence; canonical
            # delivery truth is authored only by core.finalization, and retry
            # is BOUNDED — past the cap the drop is a typed FAILED mark on the
            # committed truth, never silent infinite retry.
            if result.get("ok"):
                record["delivery_status"] = "delivered"
                record["delivered_at"] = record["last_attempted_at"]
                record["last_error"] = None
                _fid = str(record.get("finalization_id") or "")
                if _fid:
                    from relay.channel_outbound import propose_bridge_delivery

                    propose_bridge_delivery(_fid, evidence_class="PLATFORM_ACK")
            else:
                max_attempts = int(record.get("max_delivery_attempts") or 8)
                if attempts >= max_attempts:
                    record["delivery_status"] = "failed"
                    record["last_error"] = "delivery_retry_cap_exceeded"
                    _fid = str(record.get("finalization_id") or "")
                    if _fid:
                        from core.finalization import (
                            DELIVERY_FAILED_TRANSPORT,
                            set_delivery_status as _sds,
                        )

                        _sds(_fid, DELIVERY_FAILED_TRANSPORT, evidence_class="TRANSPORT_HANDOFF")
                else:
                    record["delivery_status"] = "pending"
                    record["last_error"] = "telegram_send_failed"
            changed = True
            raw_record.update(record)

        if changed:
            snapshot["record_count"] = len(records)
            self._mirror_request(f"/publish/{self.outbound_topic}", method="POST", data=snapshot)

    def run_forever(self):
        # R2b2a1 — the OWNING ENTRY POINT of this daemon's outbound effects.
        # One named scope binds the COMPLETE loop, in the executing thread; the
        # low-level send helpers stay dumb and the door grants nothing. The
        # binding is restored on normal exit and on exceptions. R2b2c: the
        # scope is DURABLE — every outbound effect's lifecycle is published to
        # the runtime-event store as it occurs (this loop never closes), so
        # the daemon's evidence survives it.
        from core.effect_gateway import named_background_effect_scope

        with named_background_effect_scope("relay.telegram", durable=True):
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
