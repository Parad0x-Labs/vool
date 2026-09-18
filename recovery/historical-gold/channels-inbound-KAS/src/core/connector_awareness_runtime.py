from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

from core.bootstrap_adapters import BootstrapMirrorAdapter, HttpJsonMirrorAdapter
from core.connector_awareness import (
    connector_descriptor_topic,
    destination_ref,
    normalize_reported_at,
    validate_connector_descriptor,
)

MAX_CONNECTOR_AWARENESS_SNAPSHOT_BYTES = 65_536
MAX_CONNECTOR_DESTINATIONS = 64
MAX_CONNECTOR_LABEL_CHARS = 128
MAX_CONNECTOR_PROJECTED_RESULT_BYTES = 32_768

_SNAPSHOT_FIELDS = {
    "topic_name",
    "publisher_peer_id",
    "published_at",
    "record_count",
    "records",
    "snapshot_hash",
    "signature",
}
_DESCRIPTOR_FIELDS = {
    "schema_version",
    "connector",
    "reported_at",
    "configured",
    "default_destination_ref",
    "destinations",
}
_DESTINATION_FIELDS = {"ref", "label", "transport", "is_default"}


@dataclass(frozen=True)
class ConnectorAwarenessRead:
    connector: str
    report_state: str
    descriptor: dict[str, Any] | None = None


@dataclass(frozen=True)
class ConnectorAwarenessExecution:
    ok: bool
    status: str
    response_text: str
    details: dict[str, Any]


def _default_adapter() -> BootstrapMirrorAdapter:
    mirror_url = os.environ.get("VOOL_MIRROR_URL", "http://127.0.0.1:8787").rstrip("/")
    return HttpJsonMirrorAdapter(mirror_url, max_response_bytes=MAX_CONNECTOR_AWARENESS_SNAPSHOT_BYTES)


def _require_exact_json_keys(value: dict[str, Any], expected: set[str], message: str) -> None:
    if len(value) != len(expected) or any(type(key) is not str for key in value) or set(value) != expected:
        raise ValueError(message)


def _preflight_connector_descriptor_bounds(raw_descriptor: object, expected_connector: str) -> None:
    """Reject non-JSON or oversized descriptor data before full semantic validation."""
    if type(raw_descriptor) is not dict:
        raise ValueError("connector awareness descriptor is not a JSON object")
    _require_exact_json_keys(raw_descriptor, _DESCRIPTOR_FIELDS, "connector awareness descriptor has an invalid shape")
    if (
        type(raw_descriptor["schema_version"]) is not int
        or type(raw_descriptor["connector"]) is not str
        or type(raw_descriptor["reported_at"]) is not str
        or type(raw_descriptor["configured"]) is not bool
        or (
            raw_descriptor["default_destination_ref"] is not None
            and type(raw_descriptor["default_destination_ref"]) is not str
        )
    ):
        raise ValueError("connector awareness descriptor has invalid JSON scalar types")

    ref_prefix = f"{expected_connector}-dest-"
    ref_length = len(ref_prefix) + 24
    ref_pattern = re.compile(rf"{re.escape(ref_prefix)}[0-9a-f]{{24}}")
    default_ref = raw_descriptor["default_destination_ref"]
    if default_ref is not None and (len(default_ref) != ref_length or ref_pattern.fullmatch(default_ref) is None):
        raise ValueError("connector awareness descriptor default ref is malformed")

    destinations = raw_descriptor["destinations"]
    if type(destinations) is not list:
        raise ValueError("connector awareness destinations are not a JSON array")
    if len(destinations) > MAX_CONNECTOR_DESTINATIONS:
        raise ValueError("connector awareness destination count exceeds the consumer limit")
    for raw_destination in destinations:
        if type(raw_destination) is not dict:
            raise ValueError("connector awareness destination is not a JSON object")
        _require_exact_json_keys(raw_destination, _DESTINATION_FIELDS, "connector awareness destination has an invalid shape")
        label = raw_destination["label"]
        ref = raw_destination["ref"]
        if type(label) is not str or len(label) > MAX_CONNECTOR_LABEL_CHARS or not label.isprintable():
            raise ValueError("connector awareness destination label is unsafe")
        if type(ref) is not str or len(ref) != ref_length or ref_pattern.fullmatch(ref) is None:
            raise ValueError("connector awareness destination ref is malformed")
        if type(raw_destination["transport"]) is not str or type(raw_destination["is_default"]) is not bool:
            raise ValueError("connector awareness destination has invalid JSON scalar types")


def _validate_consumer_descriptor(connector: str, descriptor: dict[str, Any]) -> dict[str, Any]:
    destinations = descriptor["destinations"]
    if len(destinations) > MAX_CONNECTOR_DESTINATIONS:
        raise ValueError("connector awareness destination count exceeds the consumer limit")
    ref_pattern = re.compile(rf"{re.escape(connector)}-dest-[0-9a-f]{{24}}")
    for item in destinations:
        label = item["label"]
        transport = item["transport"]
        ref = item["ref"]
        if len(label) > MAX_CONNECTOR_LABEL_CHARS or not label.isprintable():
            raise ValueError("connector awareness destination label is unsafe")
        if ref_pattern.fullmatch(ref) is None or ref != destination_ref(connector, label, transport):
            raise ValueError("connector awareness destination ref is invalid")
    return descriptor


def _projected_result_fits(details: dict[str, Any]) -> bool:
    try:
        encoded = json.dumps(details, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        return False
    return len(encoded) <= MAX_CONNECTOR_PROJECTED_RESULT_BYTES


def validate_connector_awareness_snapshot(connector: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    if type(snapshot) is not dict:
        raise ValueError("connector awareness snapshot is not a JSON object")
    expected_topic = connector_descriptor_topic(connector)
    _require_exact_json_keys(snapshot, _SNAPSHOT_FIELDS, "connector awareness snapshot has an invalid shape")
    if type(snapshot["topic_name"]) is not str or snapshot["topic_name"] != expected_topic:
        raise ValueError("connector awareness snapshot topic is invalid")
    if type(snapshot["publisher_peer_id"]) is not str or snapshot["publisher_peer_id"] != f"{connector}-bridge":
        raise ValueError("connector awareness snapshot publisher is invalid")
    if type(snapshot["record_count"]) is not int or snapshot["record_count"] != 1:
        raise ValueError("connector awareness snapshot record count is invalid")
    records = snapshot["records"]
    if type(records) is not list or len(records) != 1 or type(records[0]) is not dict:
        raise ValueError("connector awareness snapshot records are invalid")
    if type(snapshot["snapshot_hash"]) is not str or not snapshot["snapshot_hash"].strip():
        raise ValueError("connector awareness snapshot hash is invalid")
    if type(snapshot["signature"]) is not str or not snapshot["signature"].strip():
        raise ValueError("connector awareness snapshot signature is invalid")
    if type(snapshot["published_at"]) is not str:
        raise ValueError("connector awareness snapshot timestamp is invalid")
    raw_descriptor = records[0]
    _preflight_connector_descriptor_bounds(raw_descriptor, connector)
    normalize_reported_at(snapshot["published_at"])
    descriptor = validate_connector_descriptor(raw_descriptor)
    if descriptor["connector"] != connector:
        raise ValueError("connector awareness snapshot connector is invalid")
    return _validate_consumer_descriptor(connector, descriptor)


def read_connector_awareness(
    connector: str,
    *,
    adapter: BootstrapMirrorAdapter | None = None,
) -> ConnectorAwarenessRead:
    try:
        topic_name = connector_descriptor_topic(connector)
    except ValueError:
        return ConnectorAwarenessRead(connector=str(connector or ""), report_state="invalid")
    snapshot = (adapter or _default_adapter()).fetch_snapshot(topic_name)
    if snapshot is None:
        return ConnectorAwarenessRead(connector=connector, report_state="unknown")
    if type(snapshot) is not dict:
        return ConnectorAwarenessRead(connector=connector, report_state="invalid")
    try:
        descriptor = validate_connector_awareness_snapshot(connector, snapshot)
    except (TypeError, UnicodeError, ValueError):
        return ConnectorAwarenessRead(connector=connector, report_state="invalid")
    return ConnectorAwarenessRead(connector=connector, report_state="reported", descriptor=descriptor)


def execute_connector_awareness(intent: str, arguments: dict[str, Any] | None = None) -> ConnectorAwarenessExecution:
    from core import plugin_catalog
    from core.runtime_flags import flag_enabled
    from core.runtime_tool_contracts import connector_awareness_contract_for_intent

    contract = connector_awareness_contract_for_intent(intent)
    if contract is None:
        return ConnectorAwarenessExecution(False, "unsupported", "That connector awareness tool is not available.", {})
    connector = str(contract.handler).rsplit(":", 1)[-1]
    if not flag_enabled("plugin_runtime_tools") or not plugin_catalog.plugin_enabled(connector):
        return ConnectorAwarenessExecution(False, "disabled", f"{connector.title()} is disabled.", {"connector": connector})
    if arguments:
        return ConnectorAwarenessExecution(
            False,
            "invalid_arguments",
            "This connector awareness tool does not accept arguments.",
            {"connector": connector},
        )

    read = read_connector_awareness(connector)
    if read.report_state == "unknown":
        details = {
            "connector": connector,
            "report_state": "unknown",
            "configured": None,
            "reported_at": None,
            "destination_count": None,
            "default_destination_ref": None,
        }
        if intent.endswith(".list_destinations"):
            details["destinations"] = []
        return ConnectorAwarenessExecution(
            True,
            "unknown",
            f"No usable {connector.title()} bridge configuration report is available. This does not establish whether {connector.title()} is configured.",
            details,
        )
    if read.report_state != "reported" or read.descriptor is None:
        return ConnectorAwarenessExecution(
            False,
            "invalid_connector_report",
            f"The {connector.title()} bridge configuration report was invalid and was not used.",
            {"connector": connector, "report_state": "invalid", "error": "invalid_connector_report"},
        )

    descriptor = read.descriptor
    configured = bool(descriptor["configured"])
    destinations = [
        {
            "ref": item["ref"],
            "label": item["label"],
            "transport": item["transport"],
            "is_default": item["is_default"],
        }
        for item in descriptor["destinations"]
    ]
    details = {
        "connector": connector,
        "report_state": "reported",
        "configured": configured,
        "reported_at": descriptor["reported_at"],
        "destination_count": len(destinations),
        "default_destination_ref": descriptor["default_destination_ref"],
    }
    if intent.endswith(".list_destinations"):
        details["destinations"] = destinations
    if not _projected_result_fits(details):
        return ConnectorAwarenessExecution(
            False,
            "invalid_connector_report",
            f"The {connector.title()} bridge configuration report was invalid and was not used.",
            {"connector": connector, "report_state": "invalid", "error": "invalid_connector_report"},
        )
    state = "configured" if configured else "unconfigured"
    response = f"{connector.title()} bridge reported {state}. Last reported at {descriptor['reported_at']}."
    if intent.endswith(".list_destinations"):
        response = f"{connector.title()} bridge reported {len(destinations)} configured destinations. Last reported at {descriptor['reported_at']}."
    return ConnectorAwarenessExecution(True, "reported", response, details)
