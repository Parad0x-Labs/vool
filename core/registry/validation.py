"""Validate the complete registry inventory through the existing native contracts."""
from __future__ import annotations

from pathlib import Path

from core.registry.codec import decode, require


def manifest_authority(manifest: dict) -> dict:
    """Consent inventory from the native parser, without registering any tool."""
    from core.plugin_tools import parse_manifest

    loaded = parse_manifest(manifest, path=Path(".codex-plugin/plugin.json"))
    permissions = manifest.get("permissions", [])
    credentials = manifest.get("credential_bindings", [])
    for field, values in (("permissions", permissions), ("credential bindings", credentials)):
        require(isinstance(values, list) and len(values) <= 128 and
                all(isinstance(item, str) and 0 < len(item) <= 128 and
                    all(ord(char) >= 32 for char in item) for item in values) and
                len(set(values)) == len(values), "invalid manifest " + field)
    actions = {action for contract in loaded.contracts for action in contract.permission_actions}
    require(actions <= set(permissions), "manifest permissions must cover every tool permission action")
    tools = []
    for contract in loaded.contracts:
        handler = decode(contract.handler.encode())
        tools.append({"intent": contract.intent, "side_effect_class": contract.side_effect_class,
                      "approval_requirement": contract.approval_requirement,
                      "permission_actions": list(contract.permission_actions),
                      "handler": {"kind": handler["kind"], "entry": handler["entry"],
                                  "network": bool(handler.get("network", False)),
                                  "env_allowlist": list(handler.get("env_allowlist") or [])}})
    return {"permissions": list(permissions), "credential_bindings": list(credentials), "tools": tools}


def native_format_findings(root: Path, artifact: dict) -> list[dict]:
    from core.plugin_skills import load_skills
    from core.plugin_tools import load_manifest
    from core.skill_tools import validate_skill

    # Manifest parsing creates no subprocess or registered permission/contract.
    loaded = load_manifest(root / ".codex-plugin" / "plugin.json")
    manifest_authority(artifact["manifest"])
    inventory = {name for name in artifact["files"]
                 if name.startswith("skills/") and Path(name).name == "SKILL.md"}
    findings = []
    for name in sorted(inventory):
        result = validate_skill(str(root / name), candidate_contracts=tuple(loaded.contracts))
        if result.get("status") != "ok":
            # A reason-only refusal must not silently become a format pass.
            problems = result.get("problems") or ["native skill validation refused this file"]
            findings.extend({"check": "native-format", "path": name, "reason": problem}
                            for problem in problems)
    if findings:
        return findings
    exposed = {Path(skill.path).relative_to(root).as_posix() for skill in load_skills(root)}
    for name in sorted(inventory - exposed):
        findings.append({"check": "native-format", "path": name,
                         "reason": "native loader cannot expose this skill; use skills/SKILL.md or skills/name/SKILL.md"})
    if artifact["metadata"]["kind"] == "skill" and not exposed:
        findings.append({"check": "native-format", "path": "skills",
                         "reason": "skill package must expose at least one validated native skill"})
    return findings
