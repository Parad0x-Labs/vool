from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

CONNECTOR_DESCRIPTOR_SCHEMA_VERSION = 1
DISCORD_CONNECTOR_DESCRIPTOR_TOPIC = "discord_connector_descriptor"
TELEGRAM_CONNECTOR_DESCRIPTOR_TOPIC = "telegram_connector_descriptor"

_TOPIC_BY_CONNECTOR = {
    "discord": DISCORD_CONNECTOR_DESCRIPTOR_TOPIC,
    "telegram": TELEGRAM_CONNECTOR_DESCRIPTOR_TOPIC,
}
CONNECTOR_DESCRIPTOR_TOPICS = frozenset(_TOPIC_BY_CONNECTOR.values())
_DEFAULT_DESTINATION_REF_UNSET = object()


def normalize_destination_label(value: object) -> str:
    return str(value or "").strip().lower()


def connector_descriptor_topic(connector: str) -> str:
    try:
        return _TOPIC_BY_CONNECTOR[connector]
    except KeyError as exc:
        raise ValueError(f"unsupported connector: {connector}") from exc


def destination_ref(connector: str, label: str, transport: str) -> str:
    normalized_label = normalize_destination_label(label)
    normalized_transport = str(transport or "").strip().lower()
    if not normalized_label or not normalized_transport:
        raise ValueError("destination label and transport are required")
    connector_descriptor_topic(connector)
    digest_input = "\x00".join((connector, normalized_label, normalized_transport))
    digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:24]
    return f"{connector}-dest-{digest}"


def normalize_reported_at(value: str | datetime | None) -> str:
    if value is None:
        timestamp = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        timestamp = value
    elif isinstance(value, str):
        try:
            timestamp = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("reported_at must be an ISO-8601 timestamp") from exc
    else:
        raise ValueError("reported_at must be an ISO-8601 timestamp")
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("reported_at must be timezone-aware")
    return timestamp.astimezone(timezone.utc).isoformat()


def validate_connector_descriptor(descriptor: dict[str, Any]) -> dict[str, Any]:
    required_fields = {
        "schema_version",
        "connector",
        "reported_at",
        "configured",
        "default_destination_ref",
        "destinations",
    }
    if set(descriptor) != required_fields:
        raise ValueError("connector descriptor has an invalid shape")
    connector = descriptor.get("connector")
    if not isinstance(connector, str) or connector not in _TOPIC_BY_CONNECTOR:
        raise ValueError("connector descriptor has an unsupported connector")
    if descriptor.get("schema_version") != CONNECTOR_DESCRIPTOR_SCHEMA_VERSION:
        raise ValueError("connector descriptor has an unsupported schema version")
    configured = descriptor.get("configured")
    if not isinstance(configured, bool):
        raise ValueError("connector descriptor configured must be a boolean")
    raw_destinations = descriptor.get("destinations")
    if not isinstance(raw_destinations, list):
        raise ValueError("connector descriptor destinations must be a list")

    destinations: list[dict[str, Any]] = []
    labels: set[str] = set()
    default_items: list[dict[str, Any]] = []
    for raw_destination in raw_destinations:
        if not isinstance(raw_destination, dict) or set(raw_destination) != {"ref", "label", "transport", "is_default"}:
            raise ValueError("connector descriptor destination has an invalid shape")
        label = raw_destination.get("label")
        transport = raw_destination.get("transport")
        ref = raw_destination.get("ref")
        is_default = raw_destination.get("is_default")
        if not isinstance(label, str) or label != normalize_destination_label(label) or not label:
            raise ValueError("connector descriptor destination label must be normalized")
        if not isinstance(transport, str) or transport != transport.strip().lower() or not transport:
            raise ValueError("connector descriptor destination transport must be normalized")
        if not isinstance(ref, str) or ref != destination_ref(connector, label, transport):
            raise ValueError("connector descriptor destination ref is invalid")
        if not isinstance(is_default, bool):
            raise ValueError("connector descriptor destination default marker must be a boolean")
        if is_default != (label == "default"):
            raise ValueError("connector descriptor default marker is inconsistent with its label")
        if label in labels:
            raise ValueError("connector descriptor has duplicate normalized destination labels")
        labels.add(label)
        item = {"ref": ref, "label": label, "transport": transport, "is_default": is_default}
        destinations.append(item)
        if is_default:
            default_items.append(item)

    default_destination_ref = descriptor.get("default_destination_ref")
    if default_destination_ref is not None and not isinstance(default_destination_ref, str):
        raise ValueError("connector descriptor default destination ref is invalid")
    if not configured and (destinations or default_destination_ref is not None):
        raise ValueError("unconfigured connector descriptors cannot expose destinations")
    if default_destination_ref is None:
        if default_items:
            raise ValueError("connector descriptor default marker requires a default destination ref")
    elif len(default_items) != 1 or default_items[0]["ref"] != default_destination_ref:
        raise ValueError("connector descriptor default destination is inconsistent")

    return {
        "schema_version": CONNECTOR_DESCRIPTOR_SCHEMA_VERSION,
        "connector": connector,
        "reported_at": normalize_reported_at(descriptor.get("reported_at")),
        "configured": configured,
        "default_destination_ref": default_destination_ref,
        "destinations": destinations,
    }


def build_connector_descriptor(
    *,
    connector: str,
    configured: bool,
    destinations: Iterable[tuple[str, str]],
    reported_at: str | datetime | None = None,
    default_destination_ref: str | None | object = _DEFAULT_DESTINATION_REF_UNSET,
) -> dict[str, Any]:
    connector_descriptor_topic(connector)
    normalized_destinations: list[tuple[str, str]] = []
    labels: set[str] = set()
    for label, transport in destinations:
        normalized_label = normalize_destination_label(label)
        normalized_transport = str(transport or "").strip().lower()
        if not normalized_label or not normalized_transport:
            raise ValueError("connector destinations require a label and transport")
        if normalized_label in labels:
            raise ValueError("connector descriptor has duplicate normalized destination labels")
        labels.add(normalized_label)
        normalized_destinations.append((normalized_label, normalized_transport))

    items = [
        {
            "ref": destination_ref(connector, label, transport),
            "label": label,
            "transport": transport,
            "is_default": label == "default",
        }
        for label, transport in sorted(normalized_destinations)
    ]
    computed_default_destination_ref = next((item["ref"] for item in items if item["is_default"]), None)
    selected_default_destination_ref = (
        computed_default_destination_ref
        if default_destination_ref is _DEFAULT_DESTINATION_REF_UNSET
        else default_destination_ref
    )
    return validate_connector_descriptor({
        "schema_version": CONNECTOR_DESCRIPTOR_SCHEMA_VERSION,
        "connector": connector,
        "reported_at": normalize_reported_at(reported_at),
        "configured": configured,
        "default_destination_ref": selected_default_destination_ref,
        "destinations": items,
    })


def build_connector_descriptor_snapshot(connector: str, descriptor: dict[str, Any]) -> dict[str, Any]:
    topic_name = connector_descriptor_topic(connector)
    normalized_descriptor = validate_connector_descriptor(descriptor)
    if normalized_descriptor["connector"] != connector:
        raise ValueError("connector descriptor does not match the requested topic")
    body = {
        "topic_name": topic_name,
        "publisher_peer_id": f"{connector}-bridge",
        "published_at": normalized_descriptor["reported_at"],
        "record_count": 1,
        "records": [normalized_descriptor],
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        **body,
        "snapshot_hash": hashlib.sha256(canonical).hexdigest(),
        "signature": "unsigned_local",
    }
