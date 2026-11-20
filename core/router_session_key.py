"""
core/router_session_key.py
==========================
An opaque per-chat ``session_id`` for the OpenRouter lane, so the router keeps one chat on one provider
and that provider's prefix cache can be reused.

Why
---
OpenRouter keeps a conversation on one provider by a ``session_id`` when the request has one, and by
hashing the first system message and the first non-system message when it does not. VOOL's answer
requests open with the same persona message and then an evidence message whose last line carries the
current question, so that hash changes every turn and the conversation is never recognised as one. A
session key names the chat explicitly.

What the value is
-----------------
``HMAC-SHA256(install_secret, "router-session-v1" 0x00 chat_ref)``, first 16 bytes, as 32 hex
characters. Properties a privacy review can check:

* It is not the chat id, not the user, not a prefix, suffix or hash of either: without the install
  secret it cannot be linked to a chat id, and the chat id cannot be recovered from it.
* The same chat gives the same value after a restart (the secret is stored), and two installs give
  different values for the same chat id.
* It carries no product name, no version and no timestamp.
* The install secret is 32 random bytes in ``<data dir>/router_session_secret`` (owner-only file).
  Deleting the file re-keys every chat: old values are then unreachable. It must be left out of
  exports and backups that leave the machine.

When it is not sent
-------------------
Not on any lane but OpenRouter; not for a private chat (a chat whose records are not kept must not be
linkable across its own calls); not when no chat reference is known (no key is invented); not when the
flag ``router_session_key`` is off. Nothing here stores a word of a conversation, so a forget has
nothing to remove from this layer: a forgotten fact is out of the request bytes, and the session value
carries none.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
from pathlib import Path

_SECRET_FILE = "router_session_secret"
_CONTEXT = b"router-session-v1\x00"
_LOCK = threading.Lock()
_CACHE: dict[str, bytes] = {}


def _secret_path() -> Path:
    from core.runtime_paths import active_data_dir

    return Path(active_data_dir()) / _SECRET_FILE


def _install_secret() -> bytes:
    path = _secret_path()
    key = str(path)
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            return cached
        try:
            data = path.read_bytes()
            if len(data) == 32:
                _CACHE[key] = data
                return data
        except OSError:
            pass
        path.parent.mkdir(parents=True, exist_ok=True)
        data = secrets.token_bytes(32)
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, path)
        _CACHE[key] = data
        return data


def router_session_key(chat_ref: str) -> str:
    """The opaque session value for one chat, or '' when there is no chat reference."""
    ref = str(chat_ref or "").strip()
    if not ref:
        return ""
    digest = hmac.new(_install_secret(), _CONTEXT + ref.encode("utf-8"), hashlib.sha256).digest()
    return digest[:16].hex()


def chat_ref_for_request(request) -> str:
    """The chat this model request belongs to, from the request itself, or '' when it names none.

    The answer request carries it in its context; a small judgement call may not. No contextvar and no
    guess: a request that names no chat gets no key.
    """
    for source in (getattr(request, "context", None), getattr(request, "metadata", None)):
        if isinstance(source, dict):
            for name in ("chat_id", "runtime_session_id", "session_id"):
                value = str(source.get(name) or "").strip()
                if value:
                    return value
    return ""


def session_key_for_request(request, *, openrouter_lane: bool) -> str:
    """The value to send as ``session_id`` for this request, or '' for none."""
    from core.experiment_switches import switch_enabled

    if not openrouter_lane or not switch_enabled("router_session_key"):
        return ""
    try:
        from core.private_chat import PRIVATE_TURN_KEY, request_is_private

        if request_is_private():
            return ""
        for source in (getattr(request, "context", None), getattr(request, "metadata", None)):
            if isinstance(source, dict) and source.get(PRIVATE_TURN_KEY):
                return ""
    except Exception:
        return ""  # an unreadable private-chat switch fails closed, like the rest of that module
    return router_session_key(chat_ref_for_request(request))


def forget_install_secret() -> None:
    """Re-key every chat: delete the install secret. Old session values can no longer be produced."""
    path = _secret_path()
    with _LOCK:
        _CACHE.pop(str(path), None)
        path.unlink(missing_ok=True)


__all__ = ["chat_ref_for_request", "forget_install_secret", "router_session_key", "session_key_for_request"]
