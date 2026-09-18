"""Telegram <-> local VOOL CHAT bridge — talk to your machine's VOOL from your phone.

Distinct from telegram_bridge.py (which syncs the hive MESH mirror). This one relays a person's
messages to the local VOOL chat API and sends the reply back, so "work from my phone" just works:

    you (phone) --Telegram--> your bot --this bridge--> 127.0.0.1:11435/api/chat --> reply --> you

Setup: create a bot with @BotFather, give VOOL the token, message the bot. No inbound ports, no
cloud — the bridge runs on your machine and reaches out to Telegram.

Security (this is a remote entry point into a machine that has tools, so it is locked down):
- **Owner-locked.** The FIRST chat to message pairs as the owner (trust-on-first-use, persisted);
  every other chat is refused. Set VOOL_TELEGRAM_OWNER_CHAT_ID to pin it explicitly and skip TOFU.
- **Channel surface.** Every relayed turn posts surface="channel", so VOOL treats it as REMOTE and
  never owner-local — the phone can chat and use read tools, but never inherits owner-gated privileges
  (cloud spend approval, the brake, destructive machine actions).
- The bot token is a secret; it is read from the env / sealed store and never echoed.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from urllib import error

from core import remote_fetch_policy

_API_URL = os.environ.get("VOOL_OPENCLAW_API_URL", "http://127.0.0.1:11435").rstrip("/")
_TG_API = "https://api.telegram.org"
_MAX_TG_CHARS = 4096            # Telegram's per-message hard limit
_POLL_TIMEOUT = 25             # long-poll seconds
_GREETING = (
    "✅ Paired — you're the owner of this VOOL now. Message me anything and I'll run it on your Mac "
    "and reply here. (Everyone else is refused.)"
)


def _owner_file() -> Path:
    home = Path(os.environ.get("VOOL_HOME") or (Path.home() / ".vool_runtime"))
    return home / "config" / "telegram_chat_bridge.json"


class TelegramChatBridge:
    """Long-poll Telegram, relay owner messages to the local VOOL chat, send replies back."""

    def __init__(self, *, token: str | None = None, tg_call=None, vool_call=None):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN") or ""
        self._tg_call = tg_call or self._default_tg_call
        self._vool_call = vool_call or self._default_vool_call
        self.last_update_id = 0
        pinned = str(os.environ.get("VOOL_TELEGRAM_OWNER_CHAT_ID") or "").strip()
        self.owner_chat_id: str | None = pinned or self._load_owner()

    # ---- configuration / owner lock ---------------------------------------------------------

    def is_configured(self) -> bool:
        return bool(self.token)

    def _load_owner(self) -> str | None:
        try:
            data = json.loads(_owner_file().read_text(encoding="utf-8"))
            owner = str(data.get("owner_chat_id") or "").strip()
            return owner or None
        except (OSError, ValueError):
            return None

    def _save_owner(self, chat_id: str) -> None:
        path = _owner_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps({"owner_chat_id": str(chat_id)}), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass

    def _authorize(self, chat_id: str) -> bool:
        """True if this chat may drive VOOL. First chat pairs as owner (TOFU); others are refused."""
        chat_id = str(chat_id)
        if self.owner_chat_id is None:
            self.owner_chat_id = chat_id
            self._save_owner(chat_id)
            return True
        return chat_id == self.owner_chat_id

    # ---- transports (overridable for tests) -------------------------------------------------

    def _default_tg_call(self, method: str, data: dict | None = None, *, timeout: float = 30.0) -> dict:
        url = f"{_TG_API}/bot{self.token}/{method}"
        try:
            body = json.dumps(data).encode("utf-8") if data else None
            with remote_fetch_policy.open_remote_url(
                url, data=body, headers={"Content-Type": "application/json"}, timeout=timeout
            ) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (error.URLError, ValueError, OSError,
                remote_fetch_policy.RemoteFetchRefusedError):
            return {}

    def _default_vool_call(self, text: str, chat_id: str) -> str:
        # Stable per-chat session (canonical openclaw id) so conversation + memory persist per phone.
        digest = hashlib.sha256(f"telegram:{chat_id}".encode()).hexdigest()[:20]
        payload = {
            "messages": [{"role": "user", "content": text}],
            "stream": False,
            "session_id": f"openclaw:{digest}",
            "surface": "channel",      # REMOTE — never owner-local (see module docstring)
            "platform": "telegram",
        }
        # INGRESS WAIVER — declared compatibility shim, not a second execution
        # authority. This bridge runs out-of-process by design (a worker reaching
        # OUT to Telegram), so it cannot call the canonical in-process ingress
        # (core/channel_gateway.process_channel_request) directly. Instead it
        # POSTs /api/chat with surface="channel"; the server's chat dispatch
        # derives OWNER_LOCAL_KEY = loopback AND surface != "channel" — the same
        # deny-only remote guarantee the gateway stamps. The reply relayed here
        # is already A7-finalized server-side canonical bytes; this bridge mints
        # no finalization identity and owns no channel business logic.
        try:
            with remote_fetch_policy.open_remote_url(
                f"{_API_URL}/api/chat",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
                timeout=600,
            ) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (error.URLError, ValueError, OSError,
                remote_fetch_policy.RemoteFetchRefusedError):
            return "VOOL is unreachable on your machine right now — is the app running?"
        msg = data.get("message") or {}
        return str(msg.get("content") or data.get("content") or "").strip() or "(no reply)"

    # ---- relay ------------------------------------------------------------------------------

    def _send(self, chat_id: str, text: str) -> None:
        for chunk in _split_for_telegram(_strip_local_image_refs(text)):
            self._tg_call("sendMessage", {"chat_id": chat_id, "text": chunk})

    def handle_update(self, update: dict) -> None:
        update_id = int(update.get("update_id") or 0)
        if update_id > self.last_update_id:
            self.last_update_id = update_id
        message = update.get("message") or {}
        text = str(message.get("text") or "").strip()
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id") or "").strip()
        if not text or not chat_id:
            return
        if not self._authorize(chat_id):
            self._tg_call("sendMessage", {"chat_id": chat_id, "text": "Not authorized. This VOOL is locked to its owner."})
            return
        if text.split()[0].lower() in {"/start", "/help"}:
            self._send(chat_id, _GREETING)
            return
        self._tg_call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
        self._send(chat_id, self._vool_call(text, chat_id))

    def poll_once(self) -> int:
        resp = self._tg_call("getUpdates", {"offset": self.last_update_id + 1, "timeout": _POLL_TIMEOUT},
                             timeout=_POLL_TIMEOUT + 10)
        updates = resp.get("result") or [] if resp.get("ok") else []
        for update in updates:
            self.handle_update(update)
        return len(updates)

    def run_forever(self) -> None:
        # R2b2a1 — the OWNING ENTRY POINT of this daemon's outbound effects.
        # One named scope binds the COMPLETE loop, in the executing thread; a
        # swallowed transient error must not shed it, and the binding is
        # restored on normal exit and on exceptions. R2b2c: the scope is
        # DURABLE — every outbound effect's lifecycle is published to the
        # runtime-event store as it occurs (this loop never closes), so the
        # daemon's evidence survives it.
        from core.effect_gateway import named_background_effect_scope

        with named_background_effect_scope("relay.telegram_chat", durable=True):
            if not self.is_configured():
                print("[TelegramChatBridge] No TELEGRAM_BOT_TOKEN. Set it (via @BotFather) and restart.")
                return
            print(f"[TelegramChatBridge] Bridging Telegram <-> {_API_URL}. Owner-locked (TOFU).")
            while True:
                try:
                    self.poll_once()
                except Exception as exc:  # a transient hiccup must not kill the bridge
                    print(f"[TelegramChatBridge] transient error: {type(exc).__name__}")
                    time.sleep(3)


def _strip_local_image_refs(text: str) -> str:
    """Drop `![alt](file://...)` local render links — a file:// path is useless on the phone. Keep the
    prose (and any http image links). Sending the actual image is a v2 (sendPhoto upload)."""
    out = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("![") and "(file://" in stripped:
            continue
        out.append(line)
    return "\n".join(out).strip() or str(text or "")


def _split_for_telegram(text: str) -> list[str]:
    text = str(text or "")
    if len(text) <= _MAX_TG_CHARS:
        return [text or "(no reply)"]
    chunks, buf = [], ""
    for line in text.splitlines(keepends=True):
        if len(buf) + len(line) > _MAX_TG_CHARS and buf:
            chunks.append(buf)
            buf = ""
        buf += line
        while len(buf) > _MAX_TG_CHARS:            # a single very long line
            chunks.append(buf[:_MAX_TG_CHARS])
            buf = buf[_MAX_TG_CHARS:]
    if buf:
        chunks.append(buf)
    return chunks


__all__ = ["TelegramChatBridge"]


if __name__ == "__main__":
    TelegramChatBridge().run_forever()
