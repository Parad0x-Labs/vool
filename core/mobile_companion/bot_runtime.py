"""PC-managed private chat bridges. All authority stays in the companion policy.

The chat lane reuses the existing canonical /api/chat dispatch compatibility
contract with surface=channel and a server-derived remote peer. No bot response
can approve, grant a right, change a contact or unfreeze the wallet.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

from core import remote_fetch_policy
from core.mobile_companion.policy import defaults, enabled

PLATFORMS = frozenset({"telegram", "discord"})
BOT_RIGHTS = frozenset({"chat", "history", "attachments", "alerts", "wallet_view", "payment_propose"})


def background_scope(platform):
    from core.effect_gateway import named_background_effect_scope

    if platform == "telegram":
        return named_background_effect_scope("mobile.bot.telegram", durable=True)
    if platform == "discord":
        return named_background_effect_scope("mobile.bot.discord", durable=True)
    raise ValueError("Unknown chat app.")


def service():
    from core.web.api.mobile_companion_api import mobile_companion_service

    return mobile_companion_service()


def provider_class(platform):
    if platform == "telegram":
        from relay.bridge_workers.telegram_chat_bridge import ManagedTelegramChatBridge

        return ManagedTelegramChatBridge
    if platform == "discord":
        from relay.bridge_workers.discord_bridge import ManagedDiscordChatBridge

        return ManagedDiscordChatBridge
    raise ValueError("Unknown chat app.")


def private_discord_channel(value):
    match = re.fullmatch(r"https://(?:www\.)?discord\.com/channels/@me/([0-9]{1,22})/?", value.strip())
    if not match:
        raise ValueError("Paste the link to your private conversation with this bot.")
    return match[1]


def connect(runtime, platform, token, *, channel_link=""):
    from core.credential_store import store_credential
    from core.mobile_companion.listener import load_settings
    from core.mobile_companion.notifications import initial_state
    from core.mobile_companion.sealing import VERSION
    from core.secret_redaction import register_exact_secret

    if platform not in PLATFORMS or not load_settings()["enabled"]:
        raise ValueError("Turn phone and chat access on first.")
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{20,256}", token):
        raise ValueError("Enter the bot token on this computer.")
    channel_id = private_discord_channel(channel_link) if platform == "discord" else ""
    register_exact_secret(token)
    target = manager_for(runtime)
    # Validate only with the selected provider before replacing a working connection.
    probe = provider_class(platform)(runtime, token, remote_base=target.provider_origins.get(platform))
    try:
        with background_scope(platform):
            identity = probe.call("getMe" if platform == "telegram" else "/users/@me")
            valid = (
                identity.get("ok") and (identity.get("result") or {}).get("is_bot")
                if platform == "telegram"
                else identity.get("bot") and identity.get("id")
            )
            if not valid:
                raise ValueError("invalid bot identity")
            if platform == "discord" and probe.call("/channels/" + channel_id).get("type") != 1:
                raise ValueError("private conversation required")
    except (OSError, ValueError, KeyError, TypeError, remote_fetch_policy.RemoteFetchRefusedError):
        raise ValueError(
            "Could not verify this bot. Check its token and private conversation on this computer."
        ) from None
    target.stop(platform)
    store_credential("mobile.bot." + platform, token, label=platform.capitalize() + " chat bot")
    code = "".join(secrets.choice("ABCDEFGHJKMNPQRSTUVWXYZ23456789") for _ in range(12))
    current = service().store.device("bot:" + platform) or {}
    service().store.put_device(
        {
            "device_id": "bot:" + platform,
            "kind": "bot",
            "platform": platform,
            "name": platform.capitalize(),
            "status": "active",
            "connection_id": secrets.token_hex(16),
            "sealed_protocol": VERSION,
            "rules": defaults(),
            "right_expiry": {},
            "payment_cap": {},
            "paired_at": service().utcnow().isoformat(),
            "last_seen_at": "",
            "permission_revision": int(current.get("permission_revision") or 0) + 1,
            "notification_state": initial_state(),
            "binding": {
                "state": "waiting",
                "code_hash": hashlib.sha256(code.encode()).hexdigest(),
                "expires_at": time.time() + 300,
                "attempts": 0,
                "channel_id": channel_id,
                "user_id": "",
            },
            "last_update": 0,
        }
    )
    service().store.append_receipt("bot.connection_started", device_id="bot:" + platform, detail={"platform": platform})
    target.start(platform)
    return {"platform": platform, "code": code, "expires_in": 300}


def disconnect(runtime, platform):
    from core.credential_store import delete_credential
    from core.web.api.mobile_companion_api import revoke_device

    if platform not in PLATFORMS:
        raise ValueError("Unknown chat app.")
    revoke_device(service(), "bot:" + platform, reason="chat app disconnected")
    manager_for(runtime).stop(platform)
    delete_credential("mobile.bot." + platform)
    return {"disconnected": True}


class ManagedBot:
    platform = ""

    def __init__(self, runtime, token, *, remote_base=None):
        self.runtime = runtime
        self.token = token
        self.stop_event = threading.Event()
        self.thread = None
        self.connection_id = (service().store.device("bot:" + self.platform) or {}).get("connection_id")
        self.remote_base = remote_base or (
            "https://api.telegram.org" if self.platform == "telegram" else "https://discord.com/api/v10"
        )
        if remote_base:
            # Only the outside service is replaced in local served tests.
            parsed = urlsplit(remote_base)
            if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
                raise ValueError("Only loopback test servers may replace the provider.")
        from core.secret_redaction import register_exact_secret

        register_exact_secret(token)

    @property
    def device_id(self):
        return "bot:" + self.platform

    def device(self):
        return service().store.device(self.device_id) or {}

    def active(self):
        from core.mobile_companion.listener import load_settings

        return (
            not self.stop_event.is_set()
            and load_settings()["enabled"]
            and self.device().get("status") == "active"
            and self.device().get("connection_id") == self.connection_id
        )

    def call(self, path, value=None):
        headers = {"Content-Type": "application/json"}
        if self.platform == "telegram":
            url = self.remote_base + "/bot" + self.token + "/" + path
        else:
            url = self.remote_base + path
            headers["Authorization"] = "Bot " + self.token
        with remote_fetch_policy.open_remote_url(
            url,
            data=None if value is None else json.dumps(value).encode(),
            headers=headers,
            method="GET" if value is None else "POST",
            timeout=4,
            no_proxy=True,
            redirect_policy="refuse",
            credential_headers=("Authorization",) if self.platform == "discord" else None,
        ) as response:
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError("Chat service response too large.")
            result = json.loads(raw)
            if self.platform == "telegram" and (not isinstance(result, dict) or result.get("ok") is not True):
                raise ValueError("Chat service refused the request.")
            return result

    def send(self, channel, text, *, right="chat"):
        from core.secret_redaction import redact_secrets
        from relay.bridge_workers.telegram_chat_bridge import _strip_local_image_refs

        if not self.active() or (right and not enabled(self.device(), right)):
            return
        clean = redact_secrets(_strip_local_image_refs(str(text)))
        size = 3200 if self.platform == "telegram" else 1800
        for offset in range(0, min(len(clean), size * 4), size):
            if not self.active() or (right and not enabled(self.device(), right)):
                break
            chunk = clean[offset : offset + size]
            if self.platform == "telegram":
                self.call(
                    "sendMessage", {"chat_id": channel, "text": chunk, "link_preview_options": {"is_disabled": True}}
                )
            else:
                self.call("/channels/" + channel + "/messages", {"content": chunk, "allowed_mentions": {"parse": []}})

    def refused(self, reason, channel=""):
        service().store.append_receipt("bot.request_refused", device_id=self.device_id, detail={"reason": reason})
        if channel and service().store.allow_rate("bot-refusal:" + self.platform + ":" + channel, cap=3, window=60):
            self.send(channel, "This request is not allowed. Manage this chat app on your computer.", right=None)

    def consume(self, channel, user, text, event_id, *, private=True, files=None):
        if not self.active():
            return
        if not private or not re.fullmatch(r"-?[0-9]{1,22}", channel) or not re.fullmatch(r"[0-9]{1,22}", user):
            return self.refused("private_conversation_required")
        with service().dispatch_lock:
            if not self.active():
                return
            device = self.device()
            binding = device.get("binding") or {}
            if binding.get("state") != "bound":
                if not text.startswith("/pair "):
                    return self.refused("not_paired")
                if binding.get("expires_at", 0) <= time.time() or binding.get("attempts", 0) >= 5:
                    return self.refused("pairing_code_expired", channel)
                expected_channel = binding.get("channel_id")
                if expected_channel and expected_channel != channel:
                    return self.refused("wrong_conversation", channel)
                binding = {**binding, "attempts": binding.get("attempts", 0) + 1}
                correct = secrets.compare_digest(
                    hashlib.sha256(text[6:].strip().upper().encode()).hexdigest(), binding.get("code_hash", "")
                )
                if correct:
                    binding = {"state": "bound", "channel_id": channel, "user_id": user}
                service().store.mark_device(self.device_id, binding=binding)
                if not correct:
                    return self.refused("wrong_pairing_code", channel)
                service().store.append_receipt(
                    "bot.owner_bound", device_id=self.device_id, detail={"platform": self.platform}
                )
                self.send(
                    channel,
                    "Paired. Allow chat on your computer to begin. Approve payments and actions there.",
                    right=None,
                )
                return
            if binding.get("channel_id") != channel or binding.get("user_id") != user:
                return self.refused("stranger", channel)
            if not enabled(device, "chat"):
                return self.refused("chat_right_off_or_expired", channel)
            service().store.mark_device(self.device_id, last_seen_at=service().utcnow().isoformat())
        identity = hashlib.sha256(
            (
                self.device_id
                + ":"
                + str(device.get("connection_id"))
                + ":"
                + channel
                + ":"
                + user
                + ":"
                + str(event_id)
            ).encode()
        ).hexdigest()
        chat_id = (
            "mob-"
            + hashlib.sha256(
                (self.platform + ":" + str(device.get("connection_id")) + ":" + channel + ":" + user).encode()
            ).hexdigest()[:40]
        )
        if text.startswith("/history"):
            if not enabled(self.device(), "history"):
                return self.refused("history_off_or_expired", channel)
            from core.web.api.mobile_companion_api import _action_chats_history

            code, value = _action_chats_history(
                {"chat_id": chat_id, "limit": 20},
                runtime=self.runtime,
                model_name=self.runtime.runtime_model_tag,
                device=self.device(),
            )
            reply = (
                "\n".join(
                    str(row.get("role") or "") + ": " + str(row.get("content") or "")
                    for row in value.get("messages") or []
                )
                if code == 200
                else ""
            )
            self.send(channel, reply or "No conversation history is available.", right="history")
            return
        if text.startswith("/payconfirm "):
            from core.mobile_companion.payments import handle

            review = self.device().get("bot_payment_review") or {}
            if (
                not secrets.compare_digest(text.partition(" ")[2].strip(), str(review.get("review_token") or ""))
                or not review
            ):
                return self.refused("payment_review_required", channel)
            status, result = handle("wallet.propose", review["params"], service=service(), device=self.device())
            self.send(
                channel,
                "A payment proposal is waiting on your computer. Review its exact facts and approve in VOOL."
                if status == 200
                else "No new payment was approved. Check the request and limits on your computer.",
            )
            return
        if text.startswith("/pay "):
            if not enabled(device, "payment_propose"):
                return self.refused("payment_propose_off_or_expired", channel)
            parts = text.split(maxsplit=2)
            if len(parts) != 3:
                return self.send(
                    channel,
                    "Use /pay followed by the exact amount and a contact or address. Review and approve on your computer.",
                )
            from core.mobile_companion.payments import handle

            params = {"amount": parts[1], "destination": parts[2], "idempotency_key": identity}
            status, result = handle("wallet.preview", params, service=service(), device=self.device())
            if status != 200:
                return self.send(channel, "Could not prepare this request. Check its limits on your computer.")
            with service().dispatch_lock:
                if not self.active() or not enabled(self.device(), "payment_propose"):
                    return
                service().store.mark_device(
                    self.device_id,
                    bot_payment_review={
                        "review_token": result["review_token"],
                        "params": {**params, "review_token": result["review_token"], "warnings_acknowledged": result["warnings"]},
                    },
                )
            warnings = ("New payee. " if "new_payee" in result["warnings"] else "") + (
                "Unusual amount. " if "unusual_amount" in result["warnings"] else ""
            )
            self.send(
                channel,
                warnings
                + "Review "
                + result["amount"]
                + " "
                + result["asset"]
                + " on "
                + result["network"]
                + "\n"
                + result["destination"]
                + "\nNever share your wallet secret phrase or private key. To create this proposal, send /payconfirm "
                + result["review_token"]
                + ". Fees and approval stay on your computer. This does not send a payment.",
                right="payment_propose",
            )
            return
        if text.startswith(("/approve", "/unfreeze", "/freeze", "/rules", "/contacts-add")):
            return self.refused("pc_action_required", channel)
        if text.startswith(("/balance", "/receipts", "/contacts")):
            if not enabled(device, "wallet_view"):
                return self.refused("wallet_view_off_or_expired", channel)
            from core.mobile_companion.payments import handle

            if text.startswith("/contacts"):
                action = "wallet.contacts"
                params = {"search": text.partition(" ")[2]}
            elif text.startswith("/receipts"):
                action = "wallet.receipts"
                params = {}
            else:
                action = "wallet.status"
                params = {}
            status, result = handle(action, params, service=service(), device=device)
            if status != 200:
                reply = "Check this on your computer."
            elif action == "wallet.contacts":
                reply = (
                    "\n".join(
                        row["display_name"] + ": " + ", ".join(str(e["value"]) for e in row["endpoints"])
                        for row in result["contacts"][:10]
                    )
                    or "No matching payment contacts."
                )
            elif action == "wallet.receipts":
                reply = (
                    "\n".join(
                        str(row["state"])
                        + ": "
                        + str(row.get("display_amount") or "")
                        + " "
                        + str(row["asset"])
                        + " · "
                        + str(row["network"])
                        for row in result["receipts"][:5]
                    )
                    or "No payment receipts yet."
                )
            else:
                rows = []
                for account in result["status"].get("accounts", [])[:5]:
                    code, balance = handle(
                        "wallet.balances", {"wallet_id": account["wallet_id"]}, service=service(), device=device
                    )
                    rows.append(
                        str(account.get("label") or account["network"])
                        + ": "
                        + (
                            str(balance["balance_human"]) + " " + str(balance["asset"])
                            if code == 200 and balance.get("state") == "read"
                            else "Balance unavailable."
                        )
                    )
                reply = "\n".join(rows) or "No wallet accounts. Set up a wallet on your computer."
            self.send(channel, reply, right="wallet_view")
            return
        if text.startswith("/pair "):
            return self.refused("already_bound", channel)
        attachment_ids = []
        if files:
            from core.mobile_companion.bot_attachments import stage

            try:
                attachment_ids = stage(self, identity, chat_id, files)
            except (OSError, ValueError, remote_fetch_policy.RemoteFetchRefusedError):
                return self.refused("attachment_refused", channel)
        if not self.active() or not enabled(self.device(), "chat"):
            return
        from core.web.api.mobile_companion_api import _action_chats_send
        from core.web.api.runtime import default_workspace_root

        status, result = _action_chats_send(
            {"message": text[:16000], "chat_id": chat_id, "attachments": attachment_ids, "idempotency_key": identity},
            runtime=self.runtime,
            model_name=self.runtime.runtime_model_tag,
            service=service(),
            device=device,
            client_host="bot:" + self.platform,
            workspace_root_provider=default_workspace_root,
        )
        service().store.append_receipt(
            "bot.turn_finished" if status == 200 else "bot.turn_pending" if status == 202 else "bot.turn_refused",
            device_id=self.device_id,
            detail={"status": status, "turn_id": result.get("turn_id"), "commit": result.get("commit")},
        )
        self.send(
            channel,
            result.get("reply") or "Check the ongoing turn on your computer."
            if status in {200, 202}
            else "Could not complete that turn. Check your computer.",
        )

    def poll_once(self):
        if not self.active():
            return 0
        with background_scope(self.platform):
            return self._poll_once()

    def _poll_once(self):
        device = self.device()
        after = int(device.get("last_update") or 0)
        count = 0
        if self.platform == "telegram":
            value = self.call("getUpdates", {"offset": after + 1, "timeout": 1, "allowed_updates": ["message"]})
            if not value.get("ok"):
                raise ValueError("Chat service unavailable.")
            messages = sorted(value.get("result") or [], key=lambda r: int(r.get("update_id") or 0))
            for update in messages[:100]:
                if not self.active():
                    return count
                number = int(update.get("update_id") or 0)
                if number <= after:
                    continue
                message = update.get("message") or {}
                chat = message.get("chat") or {}
                author = message.get("from") or {}
                text = str(message.get("text") or message.get("caption") or "")
                from core.mobile_companion.bot_attachments import descriptors

                files = descriptors(self.platform, message)
                if (text or files) and not author.get("is_bot"):
                    self.consume(
                        str(chat.get("id") or ""),
                        str(author.get("id") or ""),
                        text,
                        number,
                        private=chat.get("type") == "private",
                        files=files,
                    )
                after = number
                count += 1
                with service().dispatch_lock:
                    if not self.active():
                        return count
                    service().store.mark_device(self.device_id, last_update=after)
        else:
            channel = (device.get("binding") or {}).get("channel_id")
            if not re.fullmatch(r"[0-9]{1,22}", str(channel or "")):
                return 0
            info = self.call("/channels/" + channel)
            if info.get("type") != 1:
                return self.refused("private_conversation_required") or 0
            values = self.call(
                "/channels/" + channel + "/messages?limit=50" + ("&after=" + str(after) if after else "")
            )
            if not isinstance(values, list):
                raise ValueError("Chat service unavailable.")
            for message in sorted(values, key=lambda r: int(r.get("id") or 0)):
                if not self.active():
                    return count
                number = int(message.get("id") or 0)
                if number <= after:
                    continue
                author = message.get("author") or {}
                text = str(message.get("content") or "")
                from core.mobile_companion.bot_attachments import descriptors

                files = descriptors(self.platform, message)
                if (text or files) and not author.get("bot"):
                    self.consume(channel, str(author.get("id") or ""), text, number, files=files)
                after = number
                count += 1
                with service().dispatch_lock:
                    if not self.active():
                        return count
                    service().store.mark_device(self.device_id, last_update=after)
        from core.mobile_companion.notifications import poll

        current = self.device()
        if self.active() and (current.get("binding") or {}).get("state") == "bound":
            cursor = int(current.get("bot_alert_after") or 0)
            status, feed = poll({"after": cursor}, service=service(), device=current)
            if status == 200:
                with service().dispatch_lock:
                    if not self.active():
                        return count
                    service().store.mark_device(self.device_id, bot_alert_after=feed["next_after"])
                from core.mobile_companion.notifications import mark_read

                mark_read({}, service=service(), device=current)
                for event in feed["events"]:
                    if event.get("os_notification_allowed") is not False:
                        self.send(current["binding"]["channel_id"], event["message"], right="alerts")
        return count

    def run_forever(self):
        while self.active():
            try:
                self.poll_once()
            except Exception:
                # Keep provider failures isolated from the daemon; never log response bodies or tokens.
                service().store.append_receipt("bot.connection_unavailable", device_id=self.device_id)
                self.stop_event.wait(3)
            self.stop_event.wait(2)

    def start(self):
        self.thread = threading.Thread(target=self.run_forever, name="managed-" + self.platform, daemon=True)
        self.thread.start()

    def close(self, *, wait=True):
        self.stop_event.set()
        if wait and self.thread and self.thread is not threading.current_thread():
            self.thread.join(5)


class BotManager:
    def __init__(self, runtime, *, provider_origins=None):
        self.runtime = runtime
        self.workers = {}
        self.retired = []
        self.lock = threading.RLock()
        self.provider_origins = provider_origins or {}

    def start(self, platform):
        from core.credential_store import CredentialReadError, get_credential, strict_reads

        with self.lock:
            if platform in self.workers:
                return
            try:
                with strict_reads():
                    token = get_credential("mobile.bot." + platform)
            except CredentialReadError:
                service().store.append_receipt("bot.credential_unavailable", device_id="bot:" + platform)
                return
            if not token:
                return
            cls = provider_class(platform)
            worker = cls(self.runtime, token, remote_base=self.provider_origins.get(platform))
            self.workers[platform] = worker
            worker.start()

    def restore(self):
        for device in service().store.devices().values():
            if device.get("kind") == "bot" and device.get("status") == "active":
                self.start(device["platform"])

    def stop(self, platform, *, wait=True):
        with self.lock:
            worker = self.workers.pop(platform, None)
        if worker:
            worker.close(wait=wait)
            if not wait:
                with self.lock:
                    self.retired.append(worker)

    def close(self, *, wait=True):
        for platform in list(self.workers):
            self.stop(platform, wait=wait)
        if wait:
            with self.lock:
                retired, self.retired = self.retired, []
            for worker in retired:
                worker.close()


def manager_for(runtime):
    if getattr(runtime, "phone_bot_manager", None) is None:
        import os

        origins = {}
        if os.environ.get("VOOL_TEST_MODE") == "1":
            for platform in PLATFORMS:
                value = os.environ.get("VOOL_TEST_BOT_" + platform.upper() + "_URL")
                if value:
                    parsed = urlsplit(value)
                    if parsed.scheme == "http" and parsed.hostname == "127.0.0.1":
                        origins[platform] = value
        runtime.phone_bot_manager = BotManager(runtime, provider_origins=origins)
    return runtime.phone_bot_manager
