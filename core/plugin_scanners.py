"""Explicit scanner plugins add a remote report to VOOL's local installation checks."""
from __future__ import annotations

import hashlib

from core import json_schema_lite
from core.addon_security import AddonError, validate_report
from core.plugin_services import ServiceRefused, ServiceTimeout, declaration, enabled, native_request


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
    except ServiceTimeout:
        raise AddonError("scanner_timeout", "The scanner service timed out. No automatic retry was made; nothing was installed.", 502) from None
    except ServiceRefused as exc:
        raise AddonError("scanner_refused", str(exc), 502) from None
    except (ValueError, UnicodeError):
        raise AddonError("scanner_invalid_report", "The scanner service returned an unreadable report. Nothing was installed.", 502) from None


def scan_skill(name: str, content: str) -> dict:
    # VOOL's own check always runs. A scanner plugin can add findings or block an
    # installation; its verdict can never replace the local check or approve on its own.
    local = _local_report(name, content)
    choices = scanners()
    if not choices:
        return local
    if len(choices) != 1:
        raise AddonError("scanner_selection_required", "Keep one scanner enabled in Add-ons so the installation check has a clear service choice.")
    plugin_id = choices[0]["id"]
    record, plugin = declaration(plugin_id, "scanner")
    spec = plugin.vool["scanner"]
    payload = _template(spec["input_template"], {"name": name, "content": content})
    value, quota = request_api("/v1/scan", payload, plugin_id=plugin_id)
    if json_schema_lite.validate(value, spec["response_schema"]):
        raise AddonError("scanner_invalid_report", "The scanner response differs from its reviewed contract. Nothing was installed.", 502)
    remote = {key: _field(value, path) for key, path in spec["response_map"].items()}
    remote = validate_report(name, remote)
    digest = hashlib.sha256(content.encode()).hexdigest()
    if not any(isinstance(a, dict) and a.get("name") == name and a.get("digest") == digest for a in remote["artifacts"]):
        raise AddonError("scanner_no_coverage", "The scanner report is for different content than this skill. Nothing was installed.", 502)
    declaration(plugin_id, "scanner", expected_approval=record.approval_digest)
    findings = [*local["findings"], *remote["findings"]]
    violations = [*local["policy"]["violations"], *remote["policy"]["violations"]]
    report = dict(local)
    report.update(verdict="pass" if local["verdict"] == remote["verdict"] == "pass" else "fail",
                  findings=findings, policy={"violations": violations}, plugin_report=remote,
                  quota_remaining=quota, scanner_plugin=plugin_id,
                  scanner_name="VOOL local check and " + plugin.vool["name"],
                  scan_notice="Checked on this device and by the scanner plugin you enabled. A scan is not a safety guarantee.")
    return validate_report(name, report)


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
