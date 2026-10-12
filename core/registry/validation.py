"""Validate the complete registry inventory through the existing native contracts."""
from __future__ import annotations

from pathlib import Path


def native_format_findings(root: Path, artifact: dict) -> list[dict]:
    from core.plugin_skills import load_skills
    from core.plugin_tools import load_manifest
    from core.skill_tools import validate_skill

    # Manifest parsing creates no subprocess or registered permission/contract.
    loaded = load_manifest(root / ".codex-plugin" / "plugin.json")
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
