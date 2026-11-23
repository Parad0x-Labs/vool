"""PC-managed private chat bridges. All authority stays in the companion policy.

The chat lane reuses the existing canonical /api/chat dispatch compatibility
contract with surface=channel and a server-derived remote peer. No bot response
can approve, grant a right, change a contact or unfreeze the wallet.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import re
import secrets
import threading
import time
import urllib.error
from urllib.parse import urlsplit

from core import remote_fetch_policy
from core.mobile_companion.policy import defaults, enabled

logger = logging.getLogger(__name__)

#: Connecting is one question the person waits on: give a slow answer time, and try twice
#: before saying the service could not be reached. A single stall is not "no internet".
CONNECT_TIMEOUT_SECONDS = 10
CONNECT_ATTEMPTS = 2
DISCORD_USER_AGENT = "DiscordBot (https://github.com/Parad0x-Labs/vool, 0.8)"

PLATFORMS = frozenset({"telegram", "discord"})
# ``computer_tools`` for a chat app always comes with folders the owner chose (core.mobile_companion.policy):
# it may find, read and send files from them, and nothing else on the computer.
BOT_RIGHTS = frozenset({"chat", "history", "attachments", "alerts", "wallet_view", "payment_propose", "computer_tools", "everyday_tools", "cloud_models"})
#: The largest file each app takes from a bot (Telegram's Bot API: 50 MB; Discord without boosts: 10 MB).
FILE_LIMITS = {"telegram": 50 * 1024 * 1024, "discord": 10 * 1024 * 1024}


class ConnectRefused(ValueError):
    """Connecting a chat app was refused for a reason the person can act on. The words are fixed
    sentences written for them, and never carry anything they typed."""


# Chat apps run on their own: they reach out to Telegram or Discord, so they need no phone access
# switch and no open port on this computer (owner, 2026-11-16: setup with as few steps as possible).
KEY_SHAPE = "That does not look like a bot key. Copy the whole key and paste it here."
#: How long a new connection waits for its owner: Telegram's Start button, or Discord's invite.
PAIRING_SECONDS = {"telegram": 300, "discord": 900}
#: Rights a chat app has once its owner is paired. Chat only: history, files, alerts, wallet views and
#: payments stay off until the owner turns them on, and payments and actions still need this computer.
PAIRED_RULES = frozenset({"chat"})
NOTHING_TO_CONFIRM = "There is nothing waiting to be confirmed. Connect the chat app again."
NOT_DISCORD_OWNER = (
    "Discord did not say who owns this bot. Paste the link to your private conversation with it instead."
)
PRIVATE_LINK_NEEDED = (
    "Paste the link to your private conversation with this bot. It starts with https://discord.com/channels/@me/"
)
KEY_NOT_ACCEPTED = "{app} did not accept this bot key. Copy the key again and paste the whole thing."
NOT_REACHED = "VOOL could not reach {app}. Check your internet connection and try again."
NOT_A_PRIVATE_CONVERSATION = (
    "Discord could not open that link as a private conversation with this bot. "
    "Send the bot a direct message, then copy that conversation\u2019s link."
)


CONFIRM_ON_COMPUTER = "Almost done. Confirm this connection on your computer, in VOOL Settings."


def person_label(value):
    """A name the chat app sent, made safe to show: printable, one line, at most 80 characters."""
    clean = "".join(ch for ch in str(value or "") if ch.isprintable() and ch not in "\u200b\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
    return " ".join(clean.split())[:80]


#: How long the worker waits for a turn the computer is still answering before saying where it is.
TURN_WAIT_SECONDS = 900
#: Turns one chat app may hold for an approval on the computer at once.
HELD_TURNS_MAX = 8
#: The app's own English words for where an answer ran and how an action went (core/i18n en catalog
#: keys proof.route.* and receipt.state.*), so a chat app reads what the app shows under an answer.
ROUTE_WORDS = {
    "local": "On this computer",
    "own_network": "On your own network",
    "cloud": "Online",
    "mixed": "This computer + online",
    "none": "No AI model",
}
RECEIPT_STATE_WORDS = {
    "checked": "Done, checked",
    "done": "Done",
    "sent": "Sent",
    "waiting": "Waiting for your OK",
    "failed": "Not done",
    "mismatch": "Reported done, but VOOL could not find it",
}


def _catalog_text(key, fallback):
    try:
        from core.i18n.catalog import catalog_for

        text = catalog_for("en").text(key)
        return text if text and text != key else fallback
    except Exception:
        return fallback


def turn_notes(platform, result):
    """Plain lines for a chat app under a finished answer: where it ran and each action's receipt.

    Read from the turn's own proof (core.proof_projection.build_turn_proof), which the app's answer
    chip reads too. An answer with no bound record, or a route that was not recorded, says nothing.
    """
    session = str(result.get("canonical_chat_id") or "")
    # The served turn is bound by the request id its committed answer names (the app's chip reads
    # the same one); the phone-side turn id is only the fallback for an answer with no commit.
    request = str((result.get("commit") or {}).get("request_id") or result.get("turn_id") or "")
    if not session or not request:
        return []
    try:
        from core.proof_projection import build_turn_proof

        proof = build_turn_proof(session_id=session, request_id=request, principal="channel:http:bot:" + platform)
    except Exception:
        return []
    compact = proof.get("compact") or {} if isinstance(proof, dict) else {}
    lines = []
    for receipt in compact.get("action_receipts") or []:
        if not isinstance(receipt, dict):
            continue
        state = receipt.get("state") if receipt.get("state") in RECEIPT_STATE_WORDS else "done"
        action = _catalog_text(str(receipt.get("action_key") or ""), "") or _catalog_text("receipt.action_generic", "An action")
        bits = [action[:1].upper() + action[1:]]
        if receipt.get("note"):
            bits.append(str(receipt["note"]))
        when = _receipt_time(str(receipt.get("due_utc") or ""), str(receipt.get("due_tz") or ""))
        if when:
            bits.append(when)
        if receipt.get("file"):
            bits.append(str(receipt["file"]) + (" in " + str(receipt["folder"]) if receipt.get("folder") else ""))
        if receipt.get("to"):
            bits.append("to " + str(receipt["to"]))
        line = " \u00b7 ".join(bits) + " \u2014 " + _catalog_text("receipt.state." + state, RECEIPT_STATE_WORDS[state])
        if receipt.get("reason") and state in {"failed", "waiting"}:
            line += ": " + str(receipt["reason"])
        lines.append(line)
    route = compact.get("route") or {}
    summary = str(route.get("summary") or "")
    if summary == "local" and route.get("own_network"):
        summary = "own_network"
    if summary in ROUTE_WORDS:
        key = "proof.route." + summary
        model = str(((proof.get("expanded") or {}).get("model")) or "") if summary != "none" else ""
        lines.append(_catalog_text(key, ROUTE_WORDS[summary]) + (" \u00b7 " + model if model else ""))
    return lines


def _receipt_time(due_utc, tz_name):
    if not due_utc:
        return ""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        moment = datetime.fromisoformat(due_utc.replace("Z", "+00:00"))
        if tz_name:
            moment = moment.astimezone(ZoneInfo(tz_name))
        return moment.strftime("%a %d %b %H:%M")
    except Exception:
        return due_utc


def approval_words(waiting):
    """What a chat app reads when its turn stopped for an approval: approved on the computer only."""
    what = str(waiting.get("action_plain") or "")
    if not what:
        what = _catalog_text("approval.action." + str(waiting.get("intent") or ""), "")
    head = "This needs your OK on your computer" + (": " + what if what else "") + "."
    return head + " Open this chat in VOOL there and allow it. I'll send the result here."


PAIRED_WORDS = "Paired. Chat with your VOOL here. Payments and actions still need your computer."


def paired_rules(device):
    """The rights a newly paired chat app starts with: chat, and nothing that spends or acts."""
    rules = dict(device.get("rules") or defaults())
    for right in PAIRED_RULES:
        rules[right] = True
    return rules


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
        raise ConnectRefused(PRIVATE_LINK_NEEDED)
    return match[1]


def connect(runtime, platform, token, *, channel_link=""):
    from core.credential_store import store_credential
    from core.mobile_companion.notifications import initial_state
    from core.mobile_companion.sealing import VERSION
    from core.secret_redaction import register_exact_secret

    if platform not in PLATFORMS:
        raise ValueError("Unknown chat app.")
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{20,256}", token):
        raise ConnectRefused(KEY_SHAPE)
    app = platform.capitalize()
    channel_id = private_discord_channel(channel_link) if platform == "discord" and channel_link.strip() else ""
    bot_name, app_id, owner_id, owner_label, bot_user_id = "", "", "", "", ""
    register_exact_secret(token)
    target = manager_for(runtime)
    # Validate only with the selected provider before replacing a working connection.
    probe = provider_class(platform)(runtime, token, remote_base=target.provider_origins.get(platform))
    # Which question the service was being asked when it failed decides what the person is told:
    # a key it turns away, a conversation it will not open, or a service that never answered.
    key_refused = KEY_NOT_ACCEPTED.format(app=app)
    asking, said_no = key_refused, {401, 404}
    try:
        with background_scope(platform):
            identity = probe.ask("getMe" if platform == "telegram" else "/users/@me")
            valid = (
                identity.get("ok") and (identity.get("result") or {}).get("is_bot")
                if platform == "telegram"
                else identity.get("bot") and identity.get("id")
            )
            if not valid:
                raise ValueError("invalid bot identity")
            if platform == "telegram":
                bot_name = str((identity.get("result") or {}).get("username") or "")
            else:
                bot_name = str(identity.get("username") or "")
                bot_user_id = str(identity.get("id") or "")
                if not re.fullmatch(r"[0-9]{1,22}", bot_user_id):
                    bot_user_id = ""
            if not re.fullmatch(r"[A-Za-z0-9_.]{2,32}", bot_name):
                bot_name = ""
            if platform == "discord" and channel_id:
                asking, said_no = NOT_A_PRIVATE_CONVERSATION, {400, 403, 404}
                if probe.ask("/channels/" + channel_id).get("type") != 1:
                    raise ValueError("private conversation required")
            elif platform == "discord":
                # The person who made this bot in Discord's developer page is the owner VOOL talks to.
                # Discord names them; nobody types an id or copies a conversation link.
                asking, said_no = NOT_DISCORD_OWNER, {403, 404}
                application = probe.ask("/oauth2/applications/@me")
                app_id = str(application.get("id") or "")
                team = application.get("team") or {}
                owner = team.get("owner_user_id") if team else (application.get("owner") or {}).get("id")
                owner_id = str(owner or "")
                person = {} if team else (application.get("owner") or {})
                owner_label = person_label(str(person.get("global_name") or person.get("username") or ""))
                if not re.fullmatch(r"[0-9]{1,22}", app_id) or not re.fullmatch(r"[0-9]{1,22}", owner_id):
                    raise ValueError("no Discord owner")
    except urllib.error.HTTPError as answered:
        logger.warning("%s connect: the service answered %s", app, answered.code)
        # The service answered and said no. Only its own "no such key" / "not yours to open"
        # answers count; a busy service or a network gate in between is not the key's fault.
        if answered.code == 401:
            raise ConnectRefused(key_refused) from None
        if answered.code in said_no:
            raise ConnectRefused(asking) from None
        raise ConnectRefused(NOT_REACHED.format(app=app)) from None
    except (OSError, remote_fetch_policy.RemoteFetchRefusedError) as failed:
        # The class names only: the request URL carries the Telegram key.
        logger.warning(
            "%s connect could not reach the service: %s (%s)",
            app, type(failed).__name__, type(getattr(failed, "reason", None)).__name__,
        )
        raise ConnectRefused(NOT_REACHED.format(app=app)) from None
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ConnectRefused(asking) from None
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
                "expires_at": time.time() + PAIRING_SECONDS[platform],
                "attempts": 0,
                "channel_id": channel_id,
                "user_id": "",
                "owner_id": owner_id,
                "owner_label": owner_label,
            },
            "bot_name": bot_name,
            # Discord: the bot's own user id, so Settings can open the bot in Discord for the owner's hello.
            "bot_user_id": bot_user_id,
            "discord_in_server": False,
            "invite_url": (
                "https://discord.com/oauth2/authorize?client_id=" + app_id + "&scope=bot&permissions=0"
                if platform == "discord" and owner_id
                else ""
            ),
            "last_update": 0,
        }
    )
    service().store.append_receipt("bot.connection_started", device_id="bot:" + platform, detail={"platform": platform})
    target.start(platform)
    result = {"platform": platform, "code": code, "expires_in": PAIRING_SECONDS[platform], "bot_name": bot_name}
    if platform == "telegram" and bot_name:
        # Telegram's own deep link: opening it shows the bot with a Start button, and pressing Start
        # sends "/start CODE". Nobody types the code.
        result["start_link"] = "https://t.me/" + bot_name + "?start=" + code
    if platform == "discord" and owner_id:
        # Discord's own invite page for this bot; no permissions are asked for.
        result["invite_url"] = (
            "https://discord.com/oauth2/authorize?client_id=" + app_id + "&scope=bot&permissions=0"
        )
    return result


def disconnect(runtime, platform):
    from core.credential_store import delete_credential
    from core.web.api.mobile_companion_api import revoke_device

    if platform not in PLATFORMS:
        raise ValueError("Unknown chat app.")
    revoke_device(service(), "bot:" + platform, reason="chat app disconnected")
    manager_for(runtime).stop(platform)
    delete_credential("mobile.bot." + platform)
    return {"disconnected": True}


def answer_candidate(runtime, platform, allow):
    """The owner's answer on this computer to "<name> pressed Start. Is this you?"."""
    if platform not in PLATFORMS:
        raise ValueError("Unknown chat app.")
    with service().dispatch_lock:
        device = service().store.device("bot:" + platform) or {}
        binding = device.get("binding") or {}
        candidate = binding.get("candidate") or {}
        if device.get("status") != "active" or binding.get("state") != "waiting" or not candidate:
            raise ConnectRefused(NOTHING_TO_CONFIRM)
        if not allow:
            service().store.mark_device(device["device_id"], binding={k: v for k, v in binding.items() if k != "candidate"})
            service().store.append_receipt("bot.pairing_candidate_refused", device_id=device["device_id"],
                                           detail={"platform": platform})
            return {"allowed": False}
        if binding.get("expires_at", 0) <= time.time():
            raise ConnectRefused(NOTHING_TO_CONFIRM)
        service().store.mark_device(
            device["device_id"],
            binding={"state": "bound", "channel_id": candidate["channel_id"], "user_id": candidate["user_id"]},
            owner_label=str(candidate.get("name") or ""),
            rules=paired_rules(device),
            permission_revision=int(device.get("permission_revision") or 0) + 1,
        )
    service().store.append_receipt("bot.owner_bound", device_id=device["device_id"], detail={"platform": platform})
    worker = manager_for(runtime).workers.get(platform)
    if worker is not None:
        try:
            with background_scope(platform):
                worker.send(candidate["channel_id"], PAIRED_WORDS, right=None)
        except Exception:
            service().store.append_receipt("bot.connection_unavailable", device_id=device["device_id"])
    return {"allowed": True}


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
        return (
            not self.stop_event.is_set()
            and self.device().get("status") == "active"
            and self.device().get("connection_id") == self.connection_id
        )

    def ask(self, path):
        """A connect-time read: a longer wait, and one more try when nothing answered at all."""
        for attempt in range(CONNECT_ATTEMPTS):
            try:
                return self.call(path, timeout=CONNECT_TIMEOUT_SECONDS)
            except urllib.error.HTTPError:
                raise
            except OSError:
                if attempt + 1 >= CONNECT_ATTEMPTS:
                    raise

    def call(self, path, value=None, *, timeout=4):
        headers = {"Content-Type": "application/json"}
        if self.platform == "telegram":
            url = self.remote_base + "/bot" + self.token + "/" + path
        else:
            url = self.remote_base + path
            headers["Authorization"] = "Bot " + self.token
            # Discord's front door answers a request without a bot User-Agent with 403 before it
            # reads the key (Python's default "Python-urllib" is turned away). Its documented form:
            headers["User-Agent"] = DISCORD_USER_AGENT
        with remote_fetch_policy.open_remote_url(
            url,
            data=None if value is None else json.dumps(value).encode(),
            headers=headers,
            method="GET" if value is None else "POST",
            timeout=timeout,
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
            # No link preview or embed on either app: their servers fetch a link in a message to draw
            # it, so an address the model was led to write would leave with no tap (security review
            # 2026-11-13). Discord's flag 1 << 2 is SUPPRESS_EMBEDS.
            if self.platform == "telegram":
                self.call(
                    "sendMessage", {"chat_id": channel, "text": chunk, "link_preview_options": {"is_disabled": True}}
                )
            else:
                self.call(
                    "/channels/" + channel + "/messages",
                    {"content": chunk, "allowed_mentions": {"parse": []}, "flags": 1 << 2},
                )

    def post_file(self, channel, name, media_type, data):
        """Post one file into the owner's private chat, as a file (no recompression)."""
        import uuid

        boundary = "vool" + uuid.uuid4().hex
        safe = re.sub(r'[\r\n"\\]', "_", str(name))[:200] or "file"
        parts = []
        if self.platform == "telegram":
            parts.append(("chat_id", None, None, str(channel).encode()))
            parts.append(("document", safe, media_type, data))
            url = self.remote_base + "/bot" + self.token + "/sendDocument"
            headers = {}
        else:
            payload = {"content": "", "allowed_mentions": {"parse": []}, "attachments": [{"id": 0, "filename": safe}]}
            parts.append(("payload_json", None, "application/json", json.dumps(payload).encode()))
            parts.append(("files[0]", safe, media_type, data))
            url = self.remote_base + "/channels/" + channel + "/messages"
            headers = {"Authorization": "Bot " + self.token, "User-Agent": DISCORD_USER_AGENT}
        body = b""
        for field, filename, kind, value in parts:
            disposition = f'form-data; name="{field}"' + (f'; filename="{filename}"' if filename else "")
            body += f"--{boundary}\r\nContent-Disposition: {disposition}\r\n".encode()
            if kind:
                body += f"Content-Type: {kind}\r\n".encode()
            body += b"\r\n" + value + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
        with remote_fetch_policy.open_remote_url(
            url,
            data=body,
            headers=headers,
            method="POST",
            timeout=60,
            no_proxy=True,
            redirect_policy="refuse",
            credential_headers=("Authorization",) if self.platform == "discord" else None,
        ) as response:
            raw = response.read(1024 * 1024 + 1)
        if self.platform == "telegram":
            result = json.loads(raw)
            if not isinstance(result, dict) or result.get("ok") is not True:
                raise ValueError("Chat service refused the file.")

    def deliver_files(self, channel):
        """Post the files a turn of this chat app picked (machine.send_to_phone), then drop the copies.

        Only while the owner still allows files for this app; a file too big for the app is said, not sent.
        """
        from core.mobile_companion import outgoing_files

        for record, path in outgoing_files.pending(self.device_id):
            if not self.active() or not enabled(self.device(), "chat") or not enabled(self.device(), "computer_tools"):
                return
            try:
                if int(record["size_bytes"]) > FILE_LIMITS[self.platform]:
                    limit = FILE_LIMITS[self.platform] // (1024 * 1024)
                    app = "Discord" if self.platform == "discord" else "Telegram"
                    self.send(channel, f"{record['name']} is too big for {app} (up to {limit} MB). It is still on your computer.")
                else:
                    self.post_file(channel, record["name"], record["media_type"], path.read_bytes())
                    service().store.append_receipt(
                        "bot.file_sent", device_id=self.device_id,
                        detail={"size_bytes": int(record["size_bytes"]), "sha256": record["sha256"]},
                    )
            except (OSError, ValueError, remote_fetch_policy.RemoteFetchRefusedError) as error:
                logger.info("%s file: not delivered (%s)", self.platform, type(error).__name__)
                self.send(channel, f"I couldn't send {record['name']} here. It is still on your computer.")
            outgoing_files.done(self.device_id, str(record["id"]))

    def refused(self, reason, channel=""):
        service().store.append_receipt("bot.request_refused", device_id=self.device_id, detail={"reason": reason})
        if channel and service().store.allow_rate("bot-refusal:" + self.platform + ":" + channel, cap=3, window=60):
            self.send(channel, "This request is not allowed. Manage this chat app on your computer.", right=None)

    def consume(self, channel, user, text, event_id, *, private=True, files=None, who=""):
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
                start = re.fullmatch(r"/start\s+([A-Za-z0-9]{6,32})", text.strip())
                if start:
                    # Telegram's Start button sends the code from the link VOOL showed.
                    text = "/pair " + start[1]
                if text.strip() == "/start":
                    # Telegram's Start button on the plain bot link (no code in it).
                    return self._ask_owner(device, binding, channel, user, who)
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
                    service().store.mark_device(
                        self.device_id,
                        binding=binding,
                        owner_label=person_label(who),
                        rules=paired_rules(device),
                        permission_revision=int(device.get("permission_revision") or 0) + 1,
                    )
                else:
                    service().store.mark_device(self.device_id, binding=binding)
                if not correct:
                    return self.refused("wrong_pairing_code", channel)
                service().store.append_receipt(
                    "bot.owner_bound", device_id=self.device_id, detail={"platform": self.platform}
                )
                self.send(channel, PAIRED_WORDS, right=None)
                return
            if binding.get("channel_id") != channel or binding.get("user_id") != user:
                return self.refused("stranger", channel)
            if text.strip().lower() in {"/freeze", "freeze", "/stopx402"}:
                from core.web.api.mobile_companion_api import _action_freeze

                try:
                    _code, result = _action_freeze({}, service=service(), device=device)
                except Exception:
                    service().store.append_receipt("payments.freeze_failed", device_id=self.device_id,
                                                  detail={"via": self.platform})
                    self.send(channel, "Could not confirm the freeze. Check your computer.", right=None)
                else:
                    self.send(channel, result["message"], right=None)
                return
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
        if text.startswith(("/approve", "/unfreeze", "/rules", "/contacts-add")):
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
        params = {"message": text[:16000], "chat_id": chat_id, "attachments": attachment_ids, "idempotency_key": identity}
        status, result = self._run_turn(params, device)
        service().store.append_receipt(
            "bot.turn_finished" if status == 200 else "bot.turn_pending" if status == 202 else "bot.turn_refused",
            device_id=self.device_id,
            detail={"status": status, "turn_id": result.get("turn_id"), "commit": result.get("commit")},
        )
        self._answer(channel, status, result, params=params)

    def _run_turn(self, params, device, *, approval_token="", resume_turn_id=""):
        """Run one chat turn through the real /api/chat, and wait for it while it is still running.

        A turn the computer is still answering (a slow local model, or this message read again after a
        failure) comes back as pending. The chat app has no screen to watch it on, so the worker asks
        again with the same identity: the chat door never runs a turn twice, it waits for the first
        run's committed answer and hands it back (core.web.api.service accept-once replay).
        """
        from core.web.api.mobile_companion_api import _action_chats_send
        from core.web.api.runtime import default_workspace_root

        deadline = time.monotonic() + TURN_WAIT_SECONDS
        while True:
            status, result = _action_chats_send(
                params,
                runtime=self.runtime,
                model_name=self.runtime.runtime_model_tag,
                service=service(),
                device=device,
                client_host="bot:" + self.platform,
                workspace_root_provider=default_workspace_root,
                approval_token=approval_token,
                resume_turn_id=resume_turn_id,
            )
            if status != 202 or time.monotonic() >= deadline or not self.active():
                return status, result
            self.stop_event.wait(2)

    def _answer(self, channel, status, result, *, params):
        """Send a finished turn's reply, then where it ran and what its actions did, then its files.

        The notes are read from the turn's own records (core.proof_projection), the same ones the app
        shows under an answer; a turn with no record gets no note rather than a guessed one. A turn
        that stopped for an approval says so, and is finished here once the owner answers it on the
        computer (``_resume_approved``).
        """
        if status == 202:
            self.send(channel, "Still working on that. The answer will be in VOOL on your computer.")
            return
        if status != 200:
            self.send(channel, "Could not complete that turn. Check your computer.")
            return
        waiting = self._approval_waiting(result)
        reply = str(result.get("reply") or "")
        notes = turn_notes(self.platform, result)
        if waiting:
            self._hold_for_approval(channel, waiting, result, params)
            notes.append(approval_words(waiting))
        self.send(channel, "\n\n".join(part for part in (reply, "\n".join(notes)) if part) or "Done.")
        self.deliver_files(channel)

    def _approval_waiting(self, result):
        """The approval this turn is stopped on, read from the one approval store, or None."""
        import core.mode_permission_policy as mpp

        turn = str(result.get("turn_id") or "")
        if not turn:
            return None
        mpp._ensure_approvals_restored()
        now = time.time()
        with mpp._LOCK:
            for token, approval in mpp._APPROVALS.items():
                if (
                    approval.get("remote_device") == self.device_id
                    and str(approval.get("task_id") or "") == turn
                    and approval.get("status") == "pending"
                    and float(approval.get("expires_at") or 0) > now
                ):
                    return {"approval_id": token, **{k: approval.get(k) for k in ("intent", "action_plain", "expires_at")}}
        return None

    def _hold_for_approval(self, channel, waiting, result, params):
        with service().dispatch_lock:
            if not self.active():
                return
            held = [row for row in self.device().get("bot_held_turns") or [] if isinstance(row, dict)]
            held = [row for row in held if row.get("approval_id") != waiting["approval_id"]][-(HELD_TURNS_MAX - 1):]
            held.append({
                "approval_id": waiting["approval_id"],
                "channel": channel,
                "turn_id": str(result.get("turn_id") or ""),
                "params": {k: params[k] for k in ("message", "chat_id", "attachments", "idempotency_key")},
                "expires_at": float(waiting.get("expires_at") or 0),
            })
            service().store.mark_device(self.device_id, bot_held_turns=held)

    def _resume_approved(self):
        """Finish the turns that waited for an approval the owner has now answered on the computer.

        Allowed: the same message runs again under that approval, as the app's own resume does (the
        approval names its turn; core.web.api.service._turn_named_by_approval), and its answer is sent
        here. Denied or expired: said plainly, and nothing runs.
        """
        import core.mode_permission_policy as mpp

        held = [row for row in self.device().get("bot_held_turns") or [] if isinstance(row, dict)]
        if not held:
            return
        mpp._ensure_approvals_restored()
        now = time.time()
        keep = []
        for row in held:
            with mpp._LOCK:
                approval = dict(mpp._APPROVALS.get(str(row.get("approval_id") or "")) or {})
            state = str(approval.get("status") or "")
            if state == "pending" and float(approval.get("expires_at") or 0) > now:
                keep.append(row)
                continue
            with service().dispatch_lock:
                if not self.active():
                    return
                rest = [r for r in self.device().get("bot_held_turns") or [] if r.get("approval_id") != row.get("approval_id")]
                service().store.mark_device(self.device_id, bot_held_turns=rest)
            channel = str(row.get("channel") or "")
            if state != "approved":
                self.send(
                    channel,
                    "You said no on your computer, so that was not done."
                    if state == "denied"
                    else "That approval expired before it was answered, so nothing was done. Ask again if you still want it.",
                )
                continue
            params = dict(row.get("params") or {})
            # A new request for the same turn: the first one's committed answer was the question.
            params["idempotency_key"] = hashlib.sha256(
                (str(params.get("idempotency_key") or "") + ":approved:" + str(row["approval_id"])).encode()
            ).hexdigest()
            status, result = self._run_turn(
                params, self.device(), approval_token=str(row["approval_id"]), resume_turn_id=str(row.get("turn_id") or "")
            )
            service().store.append_receipt(
                "bot.turn_resumed" if status == 200 else "bot.turn_refused",
                device_id=self.device_id,
                detail={"status": status, "turn_id": result.get("turn_id")},
            )
            self._answer(channel, status, result, params=params)

    def _ask_owner(self, device, binding, channel, user, who):
        """Someone pressed Start without the code (the plain link BotFather shows, say): ask the owner.

        While this chat app waits for its owner, the newest private chat becomes the one the computer
        shows: "<name> pressed Start. Is this you?" Nothing is bound until the owner says yes there,
        so a stranger who finds the bot gets no further than a name on the owner's screen.
        Caller holds the dispatch lock.
        """
        if self.platform != "telegram" or binding.get("expires_at", 0) <= time.time() or binding.get("channel_id"):
            return self.refused("not_paired")
        if not service().store.allow_rate("bot-candidate:" + self.platform, cap=6, window=60):
            return self.refused("not_paired")
        candidate = {"channel_id": channel, "user_id": user, "name": person_label(who), "at": time.time()}
        service().store.mark_device(self.device_id, binding={**binding, "candidate": candidate})
        service().store.append_receipt("bot.pairing_candidate", device_id=self.device_id, detail={"platform": self.platform})
        self.send(channel, CONFIRM_ON_COMPUTER, right=None)

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
                        who=" ".join(
                            str(author.get(k) or "") for k in ("first_name", "last_name")
                        ).strip()
                        + (" (@" + str(author["username"]) + ")" if author.get("username") else ""),
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
                return self._reach_discord_owner(device)
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
                    self.consume(
                        channel,
                        str(author.get("id") or ""),
                        text,
                        number,
                        files=files,
                        who=str(author.get("global_name") or author.get("username") or ""),
                    )
                after = number
                count += 1
                with service().dispatch_lock:
                    if not self.active():
                        return count
                    service().store.mark_device(self.device_id, last_update=after)
        from core.mobile_companion.notifications import poll

        current = self.device()
        if self.active() and (current.get("binding") or {}).get("state") == "bound" and enabled(current, "chat"):
            self._resume_approved()
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
            # A file a longer turn picked after its reply went out.
            self.deliver_files(current["binding"]["channel_id"])
        return count

    def _reach_discord_owner(self, device):
        """Open the private conversation with the bot's owner once Discord lets the bot write to them.

        Discord lets a bot write to a person only after they share a server, so this waits for the
        owner to add the bot with the invite VOOL showed. The owner is the person Discord names as the
        bot's maker, read with the key itself; nobody else can be chosen here. Returns 0 (no messages).
        """
        binding = device.get("binding") or {}
        owner = str(binding.get("owner_id") or "")
        if binding.get("state") != "waiting" or not re.fullmatch(r"[0-9]{1,22}", owner):
            return 0
        if binding.get("expires_at", 0) <= time.time() or time.monotonic() < getattr(self, "_next_owner_try", 0):
            return 0
        self._next_owner_try = time.monotonic() + 5
        greeted, channel = True, ""
        try:
            conversation = self.call("/users/@me/channels", {"recipient_id": owner})
            channel = str(conversation.get("id") or "")
            if conversation.get("type") != 1 or not re.fullmatch(r"[0-9]{1,22}", channel):
                return 0
            self.call(
                "/channels/" + channel + "/messages",
                {"content": PAIRED_WORDS, "allowed_mentions": {"parse": []}, "flags": 1 << 2},
            )
        except urllib.error.HTTPError as answer:
            if answer.code not in {400, 403}:
                raise
            if not channel:
                return 0
            if not device.get("discord_in_server"):
                # Settings moves from "add the bot" to "say hi" once the bot is in a server.
                with contextlib.suppress(urllib.error.HTTPError, OSError, ValueError):
                    servers = self.call("/users/@me/guilds")
                    if isinstance(servers, list) and servers:
                        with service().dispatch_lock:
                            if self.active():
                                service().store.mark_device(self.device_id, discord_in_server=True)
            # Discord will not let the bot write first: no shared server yet, or the owner's
            # "direct messages from server members" is off for that server. A message the owner
            # writes to the bot opens the same private conversation both ways, so it pairs too.
            try:
                recent = self.call("/channels/" + channel + "/messages?limit=10")
            except urllib.error.HTTPError:
                return 0
            if not isinstance(recent, list) or not any(
                str((m.get("author") or {}).get("id") or "") == owner for m in recent if isinstance(m, dict)
            ):
                return 0
            greeted = False
        with service().dispatch_lock:
            current = self.device()
            if not self.active() or (current.get("binding") or {}).get("state") != "waiting":
                return 0
            service().store.mark_device(
                self.device_id,
                binding={"state": "bound", "channel_id": channel, "user_id": owner},
                owner_label=str(binding.get("owner_label") or ""),
                rules=paired_rules(current),
                permission_revision=int(current.get("permission_revision") or 0) + 1,
            )
        service().store.append_receipt("bot.owner_bound", device_id=self.device_id, detail={"platform": self.platform})
        if not greeted:
            # The owner wrote first, so Discord now lets the bot answer; their message is answered next.
            with contextlib.suppress(urllib.error.HTTPError, OSError, ValueError):
                self.call(
                    "/channels/" + channel + "/messages",
                    {"content": PAIRED_WORDS, "allowed_mentions": {"parse": []}, "flags": 1 << 2},
                )
        return 0

    def run_forever(self):
        while self.active():
            try:
                self.poll_once()
            except Exception as error:
                # Keep provider failures isolated from the daemon; never log response bodies or tokens.
                # The kind of failure is kept (never its words), so a dropped answer can be traced.
                service().store.append_receipt(
                    "bot.connection_unavailable", device_id=self.device_id, detail={"error": type(error).__name__}
                )
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
