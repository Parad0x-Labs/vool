from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

DISCORD_READ_SOURCES_TOPIC = "discord_read_sources"
DISCORD_OBSERVATION_TTL_SECONDS = 300
MAX_DISCORD_READ_SOURCES = 64
MAX_DISCORD_MESSAGES_PER_SOURCE = 20
MAX_DISCORD_AUTHOR_LABEL_CHARS = 128
MAX_DISCORD_MESSAGE_CONTENT_CHARS = 2_048
MAX_DISCORD_OBSERVATION_SNAPSHOT_BYTES = 262_144
MAX_DISCORD_MESSAGE_ID = (1 << 64) - 1


def parse_discord_message_id(value: object) -> int | None:
    if type(value) is not str or not 1 <= len(value) <= 20:
        return None
    if any(character < "0" or character > "9" for character in value):
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if 0 <= parsed <= MAX_DISCORD_MESSAGE_ID else None


def _digest_ref(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _utc_timestamp(value: str | None = None) -> str:
    if value is None:
        moment = datetime.now(timezone.utc)
    elif type(value) is str:
        moment = datetime.fromisoformat(value)
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
    else:
        raise ValueError("timestamp must be a string")
    return moment.astimezone(timezone.utc).isoformat()


def _expires_at(observed_at: str) -> str:
    return (datetime.fromisoformat(observed_at) + timedelta(seconds=DISCORD_OBSERVATION_TTL_SECONDS)).astimezone(timezone.utc).isoformat()


def normalize_read_source_label(value: object) -> str:
    if type(value) is not str:
        raise ValueError("read source label must be a string")
    label = value.strip().lower()
    if not label or len(label) > MAX_DISCORD_AUTHOR_LABEL_CHARS or not label.isprintable():
        raise ValueError("read source label is invalid")
    return label


def discord_read_source_ref(label: str) -> str:
    return _digest_ref("discord-read", "discord", normalize_read_source_label(label), "bot")


def discord_message_ref(channel_id: str, message_id: str) -> str:
    if type(channel_id) is not str or not channel_id or parse_discord_message_id(message_id) is None:
        raise ValueError("Discord message identity is invalid")
    return _digest_ref("discord-msg", "discord", channel_id, message_id)


def discord_recent_messages_topic(source_ref: str) -> str:
    prefix = "discord-read-"
    if type(source_ref) is not str or not source_ref.startswith(prefix) or len(source_ref) != len(prefix) + 24:
        raise ValueError("read source ref is invalid")
    suffix = source_ref[len(prefix):]
    if any(char not in "0123456789abcdef" for char in suffix):
        raise ValueError("read source ref is invalid")
    return f"discord_recent_messages_{suffix}"


def build_discord_read_sources(selectors: list[str]) -> list[dict[str, Any]]:
    if type(selectors) is not list or len(selectors) > MAX_DISCORD_READ_SOURCES:
        raise ValueError("read source count is invalid")
    labels: set[str] = set()
    sources: list[dict[str, Any]] = []
    for raw_label in selectors:
        label = normalize_read_source_label(raw_label)
        if label in labels:
            raise ValueError("duplicate read source label")
        labels.add(label)
        sources.append({"ref": discord_read_source_ref(label), "label": label, "transport": "bot", "is_default": label == "default"})
    return sorted(sources, key=lambda item: item["label"])


def _validated_read_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if type(sources) is not list or len(sources) > MAX_DISCORD_READ_SOURCES:
        raise ValueError("read source count is invalid")
    labels: set[str] = set()
    validated: list[dict[str, Any]] = []
    for source in sources:
        if type(source) is not dict or set(source) != {"ref", "label", "transport", "is_default"}:
            raise ValueError("read source is invalid")
        label = normalize_read_source_label(source["label"])
        if label in labels or source["ref"] != discord_read_source_ref(label):
            raise ValueError("read source is invalid")
        if source["transport"] != "bot" or source["is_default"] is not (label == "default"):
            raise ValueError("read source is invalid")
        labels.add(label)
        validated.append({"ref": discord_read_source_ref(label), "label": label, "transport": "bot", "is_default": label == "default"})
    return sorted(validated, key=lambda item: item["label"])


def _snapshot(topic_name: str, record: dict[str, Any], observed_at: str) -> dict[str, Any]:
    body = {
        "topic_name": topic_name,
        "publisher_peer_id": "discord-bridge",
        "published_at": observed_at,
        "expires_at": _expires_at(observed_at),
        "record_count": 1,
        "records": [record],
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return {**body, "snapshot_hash": hashlib.sha256(canonical).hexdigest(), "signature": "unsigned_local"}


def build_discord_read_sources_snapshot(sources: list[dict[str, Any]], *, reported_at: str | None = None) -> dict[str, Any]:
    observed_at = _utc_timestamp(reported_at)
    record = {"schema_version": 1, "connector": "discord", "reported_at": observed_at, "sources": _validated_read_sources(sources)}
    return _snapshot(DISCORD_READ_SOURCES_TOPIC, record, observed_at)


def _author_label(author: dict[str, Any]) -> str | None:
    for key in ("global_name", "display_name", "username"):
        value = author.get(key)
        if type(value) is str and value and len(value) <= MAX_DISCORD_AUTHOR_LABEL_CHARS and value.isprintable():
            return value
    return None


def _timestamp(value: object) -> str | None:
    try:
        return _utc_timestamp(value if type(value) is str else None) if value is not None else None
    except ValueError:
        return None


def build_discord_recent_messages_snapshot(
    *,
    source_ref: str,
    channel_id: str,
    messages: list[dict[str, Any]],
    observed_at: str | None = None,
) -> dict[str, Any]:
    if type(channel_id) is not str or not channel_id:
        raise ValueError("channel id is invalid")
    if type(messages) is not list or len(messages) > MAX_DISCORD_MESSAGES_PER_SOURCE:
        raise ValueError("message window is invalid")
    topic_name = discord_recent_messages_topic(source_ref)
    normalized: list[tuple[int, dict[str, Any]]] = []
    for message in messages:
        if type(message) is not dict:
            continue
        raw_message_id = message.get("id")
        content = message.get("content")
        author = message.get("author")
        message_id = parse_discord_message_id(raw_message_id)
        if message_id is None or type(content) is not str or not content or type(author) is not dict:
            continue
        author_id = author.get("id")
        author_bot = author.get("bot", False)
        if type(author_id) is not str or not author_id or type(author_bot) is not bool:
            continue
        normalized.append((message_id, {
            "ref": discord_message_ref(channel_id, raw_message_id),
            "author_ref": _digest_ref("discord-user", "discord", author_id),
            "author_label": _author_label(author),
            "author_is_bot": author_bot,
            "content": content[:MAX_DISCORD_MESSAGE_CONTENT_CHARS],
            "content_truncated": len(content) > MAX_DISCORD_MESSAGE_CONTENT_CHARS,
            "timestamp": _timestamp(message.get("timestamp")),
        }))
    observed = _utc_timestamp(observed_at)
    record = {
        "schema_version": 1,
        "connector": "discord",
        "source_ref": source_ref,
        "observed_at": observed,
        "window_limit": MAX_DISCORD_MESSAGES_PER_SOURCE,
        "messages": [item for _message_id, item in sorted(normalized, key=lambda item: item[0])],
    }
    snapshot = _snapshot(topic_name, record, observed)
    if len(json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")) > MAX_DISCORD_OBSERVATION_SNAPSHOT_BYTES:
        raise ValueError("observation snapshot exceeds the consumer limit")
    return snapshot
