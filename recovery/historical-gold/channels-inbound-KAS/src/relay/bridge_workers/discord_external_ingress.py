from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from core.channel_gateway import ChannelGatewayResult, ChannelRequest, render_host_response
from core.discord_recent_observations import discord_message_ref, parse_discord_message_id
from core.external_ingress import (
    ExternalIngressIdentity,
    external_persona_ref,
    issue_external_ingress_identity,
    trusted_external_identity,
)
from core.plugin_catalog import plugin_enabled
from network.signer import derive_local_secret
from relay.channel_outbound import (
    enqueue_discord_host_response,
    is_canonical_discord_reply_source_ref,
)

_HOST_ROUTE_DOMAIN = "vool:discord-host-reply-route:v1"
_HOST_ROUTE_KEY_LABEL = "vool:discord-host-reply-route:key:v1"
_SEALED_SCHEMA_KEYS = frozenset({"schema_version", "nonce_b64", "ciphertext_b64"})
_MAX_SEALED_CIPHERTEXT_BYTES = 512
_MAX_SEALED_WRAPPER_CHARS = 2048


class DiscordExternalIngressError(RuntimeError):
    pass


class DiscordCursorStateError(DiscordExternalIngressError):
    pass


def _snowflake(value: Any, *, name: str) -> str:
    if type(value) is not str or parse_discord_message_id(value) is None:
        raise ValueError(f"{name} must be an exact Discord snowflake string")
    return value


def _digest_ref(prefix: str, raw_value: Any, *, name: str) -> str:
    value = _snowflake(raw_value, name=name)
    digest = hashlib.sha256(f"discord\0{value}".encode()).hexdigest()[:24]
    return f"{prefix}-{digest}"


def discord_account_ref(raw_bot_user_id: Any) -> str:
    return _digest_ref("discord-account", raw_bot_user_id, name="bot user id")


def discord_conversation_ref(raw_channel_id: Any) -> str:
    return _digest_ref("discord-conversation", raw_channel_id, name="channel id")


def discord_principal_ref(raw_author_id: Any) -> str:
    return _digest_ref("discord-principal", raw_author_id, name="author id")


def select_discord_reply_source_ref(sources: Any) -> str:
    if type(sources) is not list or not sources:
        raise ValueError("configured Discord read source is required")
    candidates: list[tuple[bool, str]] = []
    for source in sources:
        if type(source) is not dict or set(source) != {"ref", "label", "transport", "is_default"}:
            raise ValueError("configured Discord read source is invalid")
        ref = source.get("ref")
        if not is_canonical_discord_reply_source_ref(ref):
            raise ValueError("configured Discord read source is invalid")
        if source.get("transport") != "bot" or type(source.get("is_default")) is not bool:
            raise ValueError("configured Discord read source is invalid")
        candidates.append((source["is_default"], ref))
    defaults = sorted(ref for is_default, ref in candidates if is_default)
    return defaults[0] if defaults else sorted(ref for _is_default, ref in candidates)[0]


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _require_safe_binding(value: Any, *, prefix: str, name: str) -> str:
    if type(value) is not str or len(value) != len(prefix) + 24 or not value.startswith(prefix):
        raise ValueError(f"{name} is invalid")
    suffix = value[len(prefix) :]
    if any(character not in "0123456789abcdef" for character in suffix):
        raise ValueError(f"{name} is invalid")
    return value


def _host_route_aad(
    *,
    account_ref: Any,
    conversation_ref: Any,
    event_ref: Any,
    reply_source_ref: Any,
    reply_message_ref: Any,
) -> bytes:
    account = _require_safe_binding(account_ref, prefix="discord-account-", name="account ref")
    conversation = _require_safe_binding(
        conversation_ref, prefix="discord-conversation-", name="conversation ref"
    )
    event = _require_safe_binding(event_ref, prefix="discord-msg-", name="event ref")
    if not is_canonical_discord_reply_source_ref(reply_source_ref):
        raise ValueError("reply source ref is invalid")
    reply_message = _require_safe_binding(
        reply_message_ref, prefix="discord-msg-", name="reply message ref"
    )
    if event != reply_message:
        raise ValueError("event and reply message refs must match")
    return _canonical_json(
        {
            "account_ref": account,
            "conversation_ref": conversation,
            "domain": _HOST_ROUTE_DOMAIN,
            "event_ref": event,
            "reply_message_ref": reply_message,
            "reply_source_ref": reply_source_ref,
            "schema_version": 1,
        }
    )


def _sealed_wrapper(
    value: Any,
    *,
    max_ciphertext_bytes: int = _MAX_SEALED_CIPHERTEXT_BYTES,
    max_ciphertext_b64_chars: int = _MAX_SEALED_WRAPPER_CHARS,
) -> tuple[dict[str, Any], bytes, bytes]:
    if type(value) is not dict or set(value) != _SEALED_SCHEMA_KEYS:
        raise ValueError("sealed wrapper is invalid")
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValueError("sealed wrapper is invalid")
    nonce_b64 = value.get("nonce_b64")
    ciphertext_b64 = value.get("ciphertext_b64")
    if (
        type(nonce_b64) is not str
        or type(ciphertext_b64) is not str
        or len(nonce_b64) > 32
        or len(ciphertext_b64) > max_ciphertext_b64_chars
    ):
        raise ValueError("sealed wrapper is invalid")
    try:
        nonce = base64.b64decode(nonce_b64, validate=True)
        ciphertext = base64.b64decode(ciphertext_b64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("sealed wrapper is invalid") from exc
    if len(nonce) != 12 or not 16 <= len(ciphertext) <= max_ciphertext_bytes:
        raise ValueError("sealed wrapper is invalid")
    return dict(value), nonce, ciphertext


def seal_discord_host_reply_route(
    *,
    raw_channel_id: Any,
    raw_message_id: Any,
    account_ref: Any,
    conversation_ref: Any,
    event_ref: Any,
    reply_source_ref: Any,
    reply_message_ref: Any,
) -> dict[str, Any]:
    channel_id = _snowflake(raw_channel_id, name="channel id")
    message_id = _snowflake(raw_message_id, name="message id")
    if discord_conversation_ref(channel_id) != conversation_ref:
        raise ValueError("conversation ref does not match the route")
    if discord_message_ref(channel_id, message_id) != event_ref or event_ref != reply_message_ref:
        raise ValueError("message ref does not match the route")
    aad = _host_route_aad(
        account_ref=account_ref,
        conversation_ref=conversation_ref,
        event_ref=event_ref,
        reply_source_ref=reply_source_ref,
        reply_message_ref=reply_message_ref,
    )
    plaintext = _canonical_json({"channel_id": channel_id, "message_id": message_id})
    nonce = os.urandom(12)
    ciphertext = AESGCM(derive_local_secret(_HOST_ROUTE_KEY_LABEL, length=32)).encrypt(
        nonce, plaintext, aad
    )
    return {
        "schema_version": 1,
        "nonce_b64": base64.b64encode(nonce).decode("ascii"),
        "ciphertext_b64": base64.b64encode(ciphertext).decode("ascii"),
    }


def open_discord_host_reply_route(
    sealed_route: Any,
    *,
    current_bot_user_id: Any,
    account_ref: Any,
    conversation_ref: Any,
    event_ref: Any,
    reply_source_ref: Any,
    reply_message_ref: Any,
) -> tuple[str, str]:
    current_account_ref = discord_account_ref(current_bot_user_id)
    if current_account_ref != account_ref:
        raise PermissionError("Discord host response account does not match")
    _wrapper, nonce, ciphertext = _sealed_wrapper(sealed_route)
    aad = _host_route_aad(
        account_ref=account_ref,
        conversation_ref=conversation_ref,
        event_ref=event_ref,
        reply_source_ref=reply_source_ref,
        reply_message_ref=reply_message_ref,
    )
    try:
        plaintext = AESGCM(derive_local_secret(_HOST_ROUTE_KEY_LABEL, length=32)).decrypt(
            nonce, ciphertext, aad
        )
        payload = json.loads(plaintext.decode("utf-8"))
    except (InvalidTag, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PermissionError("Discord host response route is invalid") from exc
    if type(payload) is not dict or set(payload) != {"channel_id", "message_id"}:
        raise PermissionError("Discord host response route is invalid")
    try:
        channel_id = _snowflake(payload.get("channel_id"), name="channel id")
        message_id = _snowflake(payload.get("message_id"), name="message id")
    except ValueError as exc:
        raise PermissionError("Discord host response route is invalid") from exc
    if (
        discord_conversation_ref(channel_id) != conversation_ref
        or discord_message_ref(channel_id, message_id) != event_ref
        or event_ref != reply_message_ref
    ):
        raise PermissionError("Discord host response route binding is invalid")
    return channel_id, message_id


def bounded_discord_host_response(value: Any) -> tuple[str, bool]:
    return render_host_response(value, platform="discord")


class DiscordExternalIngressAdapter:
    def __init__(
        self,
        *,
        raw_channel_id: Any,
        raw_author_id: Any,
        raw_message_id: Any,
        reply_source_ref: Any,
        raw_bot_user_id: Any = None,
        account_ref: Any = None,
    ) -> None:
        if (raw_bot_user_id is None) == (account_ref is None):
            raise ValueError("exactly one Discord account binding is required")
        self._account_ref = (
            discord_account_ref(_snowflake(raw_bot_user_id, name="bot user id"))
            if raw_bot_user_id is not None
            else _require_safe_binding(account_ref, prefix="discord-account-", name="account ref")
        )
        self._channel_id = _snowflake(raw_channel_id, name="channel id")
        self._author_id = _snowflake(raw_author_id, name="author id")
        self._message_id = _snowflake(raw_message_id, name="message id")
        if not is_canonical_discord_reply_source_ref(reply_source_ref):
            raise ValueError("reply source ref is invalid")
        self._reply_source_ref = reply_source_ref

    def build_request(self, agent: Any, command_text: Any) -> ChannelRequest:
        if not plugin_enabled("discord"):
            raise PermissionError("Discord connector is disabled")
        if type(command_text) is not str or not command_text.strip():
            raise ValueError("Discord command text is invalid")
        try:
            persona_id = agent.persona_id
            persona_ref = external_persona_ref(persona_id)
        except (AttributeError, TypeError, ValueError) as exc:
            raise PermissionError("Discord agent persona is invalid") from exc
        account_ref = self._account_ref
        conversation_ref = discord_conversation_ref(self._channel_id)
        principal_ref = discord_principal_ref(self._author_id)
        event_ref = discord_message_ref(self._channel_id, self._message_id)
        identity = issue_external_ingress_identity(
            connector="discord",
            account_ref=account_ref,
            conversation_ref=conversation_ref,
            principal_ref=principal_ref,
            persona_ref=persona_ref,
            event_ref=event_ref,
        )
        sealed_route = seal_discord_host_reply_route(
            raw_channel_id=self._channel_id,
            raw_message_id=self._message_id,
            account_ref=account_ref,
            conversation_ref=conversation_ref,
            event_ref=event_ref,
            reply_source_ref=self._reply_source_ref,
            reply_message_ref=event_ref,
        )
        reply_source_ref = self._reply_source_ref

        def host_response_sink(
            gateway_result: ChannelGatewayResult, sink_identity: ExternalIngressIdentity
        ) -> bool:
            trusted = trusted_external_identity(sink_identity)
            if trusted is None or trusted.public_metadata() != identity.public_metadata():
                return False
            accepted, _record, _deduplicated = enqueue_discord_host_response(
                content=gateway_result.response_text,
                task_id=gateway_result.task_id,
                session_id=gateway_result.session_id,
                account_ref=account_ref,
                conversation_ref=conversation_ref,
                event_ref=event_ref,
                reply_source_ref=reply_source_ref,
                reply_message_ref=event_ref,
                sealed_host_route=sealed_route,
            )
            return accepted

        return ChannelRequest(
            platform="discord",
            user_id=principal_ref,
            channel_id=None,
            text=command_text,
            persona_id=persona_id,
            device_hint="channel",
            surface="discord_bot",
            external_identity=identity,
            host_response_sink=host_response_sink,
        )


__all__ = [
    "DiscordExternalIngressAdapter",
    "DiscordExternalIngressError",
    "bounded_discord_host_response",
    "discord_account_ref",
    "discord_conversation_ref",
    "discord_principal_ref",
    "open_discord_host_reply_route",
    "seal_discord_host_reply_route",
    "select_discord_reply_source_ref",
]
