"""Explicit scanner plugins add a remote report to VOOL's local installation checks."""
from __future__ import annotations

import hashlib

import requests

from core import json_schema_lite
from core.addon_security import AddonError, validate_report
from core.plugin_services import ServiceRefused, declaration, enabled, native_request


def scanners() -> list[dict]:
    from core.credential_store import credential_is_indexed

    result = []
    for plugin in enabled("scanner"):
        slot = plugin.vool["scanner"]["credential_slot"]
        result.append({"id": plugin.plugin_id, "name": plugin.vool["name"],
                       "configured": credential_is_indexed(f"plugin.{plugin.plugin_id}.{slot}")})
    return result


def _template(value, variables):
    if isinstance(value, dict):
        return {key: _template(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [_template(item, variables) for item in value]
    if isinstance(value, str):
        for name, replacement in variables.items():
            value = value.replace("{" + name + "}", replacement)
    return value


def _field(value, path):
    for name in path.split("."):
        if not isinstance(value, dict) or name not in value:
            raise AddonError("scanner_invalid_report", "The scanner report is missing required evidence. Nothing was installed.", 502)
        value = value[name]
    return value


def request_api(endpoint: str, payload=None, *, plugin_id="") -> tuple[dict, str]:
    choices = scanners()
    if not plugin_id:
        if len(choices) != 1:
            raise AddonError("scanner_selection_required", "Enable one scanner plugin in Add-ons before requesting this scan.")
        plugin_id = choices[0]["id"]
    _record, plugin = declaration(plugin_id, "scanner")
    spec = plugin.vool["scanner"]
    path = spec["test_path"] if payload is None else spec["scan_path"]
    if endpoint not in {"/v1/version", "/v1/scan"}:
        raise AddonError("scanner_operation_refused", "This scanner operation is not supported.")
    try:
        response = native_request(plugin_id, "scanner", "GET" if payload is None else "POST", spec["base_url"].rstrip("/") + path,
                                  payload=payload)
        try:
            value = response.json()
            if not isinstance(value, dict):
                raise ValueError()
            return value, ""
        finally:
            response.close()
    except requests.exceptions.Timeout:
        raise AddonError("scanner_timeout", "The scanner service timed out. No automatic retry was made; nothing was installed.", 502) from None
    except ServiceRefused as exc:
        raise AddonError("scanner_refused", str(exc), 502) from None
    except (ValueError, UnicodeError):
        raise AddonError("scanner_invalid_report", "The scanner service returned an unreadable report. Nothing was installed.", 502) from None


def scan_skill(name: str, content: str) -> dict:
    choices = scanners()
    if not choices:
        return _local_report(name, content)
    if len(choices) != 1:
        raise AddonError("scanner_selection_required", "Keep one scanner enabled in Add-ons so the installation check has a clear service choice.")
    plugin_id = choices[0]["id"]
    record, plugin = declaration(plugin_id, "scanner")
    spec = plugin.vool["scanner"]
    payload = _template(spec["input_template"], {"name": name, "content": content})
    value, quota = request_api("/v1/scan", payload, plugin_id=plugin_id)
    if json_schema_lite.validate(value, spec["response_schema"]):
        raise AddonError("scanner_invalid_report", "The scanner response differs from its reviewed contract. Nothing was installed.", 502)
    report = {key: _field(value, path) for key, path in spec["response_map"].items()}
    report = validate_report(name, report)
    declaration(plugin_id, "scanner", expected_approval=record.approval_digest)
    report.update(quota_remaining=quota, scanner_plugin=plugin_id, scanner_name=plugin.vool["name"],
                  scan_notice="Checked by the scanner plugin you enabled. A scan is not a safety guarantee.")
    return report


def _local_report(name: str, content: str) -> dict:
    from core.local_scan import scan_bytes

    raw = scan_bytes(name, content.encode())
    complete = raw["status"] in {"clean", "findings"}
    if not complete:
        raise AddonError("local_scan_incomplete", "The check on this device could not finish. Nothing was installed.")
    findings = [dict(finding) for finding in raw["findings"]]
    report = {"kind": "scan", "engine": str(raw["scanner_version"]), "service": "VOOL local check",
              "verdict": "fail" if findings else "pass", "lockfile": {"ruleset": raw["ruleset_version"]},
              "artifacts": [{"name": name, "digest": hashlib.sha256(content.encode()).hexdigest()}],
              "findings": findings, "policy": {"violations": []}, "local_report": raw,
              "scanner_name": "VOOL local check", "scan_notice": "Checked on this device only. No remote scanner was contacted."}
    return validate_report(name, report)
