"""Posts to Telegram or Discord through the chat app connected in Settings, only after the owner approves.

"post to Telegram: ..." never sends on its own. It stages one exact post (platform, target, text) and
shows it; nothing leaves until the owner approves that post on this computer ("approve post <id>", or
a bare "yes" right after the preview). The same rule as X (core/x_actions.py):

* The preview freezes what will be sent. Approval sends exactly that, once.
* Approval only counts from the owner on this computer. A turn from a phone or from the chat app
  itself cannot approve a post; neither can a turn in another chat.
* A preview expires after 15 minutes. A send whose answer is lost is marked unknown and never re-sent.
* Targets: your private chat with the bot (the default), a Telegram channel or group by its public
  @name where the bot is an admin, or a Discord text channel by #name in a server the bot was added to.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import time
import urllib.error
from typing import Any

from core.runtime_paths import data_path

_FILE = "chat_app_posts.json"
EXPIRY_S = 15 * 60
LIMITS = {"telegram": 3200, "discord": 1800}
PREVIEW_PREFIX = "Post preview ready."
_APPROVAL_LINE = re.compile(r"Reply with: approve post (?P<id>[0-9a-f]{12})\s*$", re.IGNORECASE)
_APPROVE_TYPED = re.compile(r"^\s*approve\s+post\s+(?P<id>[0-9a-f]{12})\s*[.!]?\s*$", re.IGNORECASE)
_GO_AHEAD = frozenset({"yes", "y", "yep", "yeah", "ok", "okay", "sure", "send it", "post it", "yes send it",
                       "yes post it", "go ahead", "go", "do it", "approve", "approved", "yes please"})
_lock = threading.RLock()
NAMES = {"telegram": "Telegram", "discord": "Discord"}
SETTINGS_LINK = "[Settings > Phone and chat apps](/settings#phone)"


class PostRefused(ValueError):
    """A fixed sentence for the person; never carries provider bodies or keys."""


def _load() -> dict[str, Any]:
    path = data_path(_FILE)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(value: dict[str, Any]) -> None:
    path = data_path(_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    temp.replace(path)


def _device(platform: str) -> dict[str, Any] | None:
    """The connected, paired chat app for this platform, or None."""
    try:
        from core.mobile_companion.bot_runtime import service

        device = service().store.device("bot:" + platform) or {}
    except Exception:
        return None
    binding = device.get("binding") or {}
    if device.get("status") != "active" or binding.get("state") != "bound":
        return None
    if not re.fullmatch(r"-?[0-9]{1,22}", str(binding.get("channel_id") or "")):
        return None
    return device


def connected(platform: str) -> bool:
    return _device(platform) is not None


#: The developer bridge (relay/bridge_workers, started by hand) reads these; a normal install has none.
_DEVELOPER_KEYS = {
    "telegram": ("TELEGRAM_BOT_TOKEN", "VOOL_MIRROR_URL"),
    "discord": ("DISCORD_WEBHOOK_URL", "DISCORD_WEBHOOK_URLS_JSON", "DISCORD_BOT_TOKEN", "VOOL_MIRROR_URL"),
}


def developer_bridge_configured(platform: str) -> bool:
    import os

    return any(str(os.environ.get(name) or "").strip() for name in _DEVELOPER_KEYS.get(platform, ()))


def not_connected_words(platform: str) -> str:
    name = NAMES.get(platform, platform)
    return f"{name} is not connected yet, so nothing was posted. Connect it in {SETTINGS_LINK}, then ask again."


def _bot(platform: str):
    import os
    from urllib.parse import urlsplit

    from core.credential_store import get_credential, strict_reads
    from core.mobile_companion.bot_runtime import provider_class

    with strict_reads():
        token = get_credential("mobile.bot." + platform)
    if not token:
        raise PostRefused(f"{NAMES[platform]} is not connected. Connect it in {SETTINGS_LINK}.")
    origin = None
    if os.environ.get("VOOL_TEST_MODE") == "1":
        value = os.environ.get("VOOL_TEST_BOT_" + platform.upper() + "_URL") or ""
        parsed = urlsplit(value)
        if parsed.scheme == "http" and parsed.hostname == "127.0.0.1":
            origin = value
    return provider_class(platform)(None, token, remote_base=origin)


def _resolve(platform: str, target: str, device: dict[str, Any]) -> tuple[str, str]:
    """(provider chat or channel id, the words the preview shows for it)."""
    from core.mobile_companion.bot_runtime import background_scope

    name = NAMES[platform]
    if target in {"", "default", "me", "my chat"}:
        return str(device["binding"]["channel_id"]), f"your private chat with the {name} bot"
    if platform == "telegram":
        handle = target.lstrip("@")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", handle):
            raise PostRefused("Name a Telegram channel or group by its public @name, for example @mychannel.")
        bot = _bot(platform)
        try:
            with background_scope(platform):
                chat = bot.call("getChat", {"chat_id": "@" + handle}).get("result") or {}
        except (urllib.error.HTTPError, ValueError, OSError):
            raise PostRefused(
                f"The bot cannot see @{handle}. Add the bot to that channel or group as an admin, then ask again."
            ) from None
        if chat.get("type") not in {"channel", "group", "supergroup"}:
            raise PostRefused(f"@{handle} is not a channel or group the bot can post to.")
        return "@" + handle, f"@{handle} on Telegram"
    channel_name = target.lstrip("#").lower()
    if not re.fullmatch(r"[a-z0-9_\-]{1,64}", channel_name):
        raise PostRefused("Name a Discord channel like #general.")
    bot = _bot(platform)
    found: list[tuple[str, str]] = []
    try:
        with background_scope(platform):
            guilds = bot.call("/users/@me/guilds")
            for guild in (guilds if isinstance(guilds, list) else [])[:10]:
                gid = str(guild.get("id") or "")
                if not re.fullmatch(r"[0-9]{1,22}", gid):
                    continue
                channels = bot.call("/guilds/" + gid + "/channels")
                for channel in channels if isinstance(channels, list) else []:
                    cid = str(channel.get("id") or "")
                    if channel.get("type") in {0, 5} and str(channel.get("name") or "").lower() == channel_name \
                            and re.fullmatch(r"[0-9]{1,22}", cid):
                        found.append((cid, str(guild.get("name") or "your server")[:80]))
    except (urllib.error.HTTPError, ValueError, OSError):
        raise PostRefused("Discord did not answer. Try again in a moment.") from None
    if not found:
        raise PostRefused(f"The bot cannot see a channel called #{channel_name}. Add the bot to that server first.")
    if len(found) > 1:
        raise PostRefused(f"More than one server has #{channel_name}. Rename one, or post to your private chat.")
    return found[0][0], f"#{channel_name} in {found[0][1]}"


def stage(platform: str, message: str, target: str, *, session_id: str) -> str:
    """Freeze one post and return the preview the chat shows. Raises PostRefused with plain words."""
    device = _device(platform)
    if device is None:
        raise PostRefused(not_connected_words(platform))
    text = str(message or "").strip()
    if not text:
        raise PostRefused("Tell me the exact words to post.")
    if len(text) > LIMITS[platform]:
        raise PostRefused(f"That is too long for one {NAMES[platform]} message. Keep it under {LIMITS[platform]} characters.")
    if re.search(r"[​-‏‪-‮⁦-⁩\U000e0000-\U000e007f]", text):
        raise PostRefused("That text has hidden formatting characters. Retype it without them.")
    chat, where = _resolve(platform, str(target or "default"), device)
    post_id = secrets.token_hex(6)
    record = {
        "id": post_id, "platform": platform, "chat": chat, "where": where, "text": text,
        "session_id": str(session_id or ""), "created": time.time(), "status": "pending",
        "connection_id": device.get("connection_id"),
    }
    record["hash"] = hashlib.sha256(json.dumps([platform, chat, text]).encode()).hexdigest()
    with _lock:
        posts = _load()
        now = time.time()
        posts = {k: v for k, v in posts.items() if v.get("status") != "pending" or now - v.get("created", 0) < EXPIRY_S}
        posts = dict(sorted(posts.items(), key=lambda kv: kv[1].get("created", 0))[-50:])
        posts[post_id] = record
        _save(posts)
    quoted = "\n".join("> " + line for line in text.splitlines())
    return (
        f"{PREVIEW_PREFIX}\n\nTo: {where}\n\n{quoted}\n\n"
        f"Nothing is sent until you approve it here on this computer. Reply with: approve post {post_id}"
    )


def _last_answer(source_context: dict[str, Any] | None, session_id: str, text: str) -> str:
    history = [item for item in (list((source_context or {}).get("client_conversation_history") or [])
                                 if isinstance(source_context, dict) else []) if isinstance(item, dict)]
    if history and history[-1].get("role") == "user" and str(history[-1].get("content") or "").strip() == text.strip():
        history = history[:-1]
    last = history[-1] if history else {}
    answer = str(last.get("content") or "").strip() if last.get("role") == "assistant" else ""
    if not answer and not history:
        from core.local_operator_actions import _served_last_assistant_text

        answer = _served_last_assistant_text(session_id)
    return answer


def approval_requested(text: str, *, session_id: str, source_context: dict[str, Any] | None) -> str | None:
    """The post id this message approves, or None. A typed "approve post <id>", or a bare go-ahead
    right after this chat's own preview."""
    typed = _APPROVE_TYPED.match(str(text or ""))
    if typed:
        return typed.group("id").lower()
    if str(text or "").strip().endswith("?"):
        return None
    words = " ".join(re.sub(r"[.!,]+", " ", str(text or "").lower()).split())
    if words not in _GO_AHEAD:
        return None
    answer = _last_answer(source_context, session_id, str(text or ""))
    offered = _APPROVAL_LINE.search(answer)
    if not answer.startswith(PREVIEW_PREFIX) or offered is None:
        return None
    return offered.group("id").lower()


def approve(post_id: str, *, session_id: str, source_context: dict[str, Any] | None) -> str:
    """Send one staged post. Returns the words for the chat."""
    from core.request_trust import request_is_owner_local

    if not request_is_owner_local(source_context):
        return "Posts are approved only on your computer. Nothing was sent."
    with _lock:
        posts = _load()
        record = posts.get(post_id)
        if not record or record.get("session_id") != str(session_id or ""):
            return "There is no post waiting with that id in this chat. Nothing was sent."
        if record.get("status") != "pending":
            return "That post was already handled. Nothing more was sent."
        if time.time() - float(record.get("created") or 0) > EXPIRY_S:
            record["status"] = "expired"
            _save(posts)
            return "That preview expired. Ask again to post it. Nothing was sent."
        if hashlib.sha256(json.dumps([record["platform"], record["chat"], record["text"]]).encode()).hexdigest() != record.get("hash"):
            record["status"] = "refused"
            _save(posts)
            return "That post changed after its preview. Nothing was sent."
        device = _device(record["platform"])
        if device is None or device.get("connection_id") != record.get("connection_id"):
            record["status"] = "refused"
            _save(posts)
            return f"{NAMES[record['platform']]} was disconnected after the preview. Nothing was sent."
        record["status"] = "sending"
        _save(posts)
    platform = record["platform"]
    outcome, words = "sent", f"Posted to {record['where']}."
    try:
        from core.mobile_companion.bot_runtime import background_scope
        from core.secret_redaction import redact_secrets

        bot = _bot(platform)
        text = redact_secrets(record["text"])
        with background_scope(platform):
            if platform == "telegram":
                bot.call("sendMessage", {"chat_id": record["chat"], "text": text,
                                         "link_preview_options": {"is_disabled": True}})
            else:
                bot.call("/channels/" + record["chat"] + "/messages",
                         {"content": text, "allowed_mentions": {"parse": []}, "flags": 1 << 2})
    except urllib.error.HTTPError as answer:
        outcome = "failed"
        words = (f"{NAMES[platform]} refused the post ({answer.code}). Nothing was sent. "
                 "Check that the bot may post there.")
    except PostRefused as refused:
        outcome, words = "failed", str(refused)
    except (OSError, TimeoutError):
        outcome = "unknown"
        words = (f"{NAMES[platform]} did not answer, so the post may or may not have arrived. "
                 "Check there before asking again; VOOL will not send it again on its own.")
    except Exception:
        outcome, words = "failed", f"{NAMES[platform]} did not take the post. Nothing was sent."
    with _lock:
        posts = _load()
        if post_id in posts:
            posts[post_id]["status"] = outcome
            posts[post_id]["finished"] = time.time()
            _save(posts)
    try:
        from core.mobile_companion.bot_runtime import service

        service().store.append_receipt("bot.post_" + outcome, device_id="bot:" + platform,
                                       detail={"platform": platform, "post_id": post_id})
    except Exception:
        pass
    return words
