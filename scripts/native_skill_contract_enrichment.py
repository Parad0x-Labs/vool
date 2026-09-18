#!/usr/bin/env python3
"""One-time enrichment: give every native SKILL.md the typed contract keys.

The 13 vool-* packages were authored by a concurrent sibling lane with a prose-leaning
frontmatter (triggers, stop-conditions, recovery, cumulative-test-law — all preserved
verbatim). This migration ADDS the typed contract block the library's load-time law reads:

    id, risk-class, task-families, capability-families, tool-intents, permitted-tools,
    prerequisites, expected-outputs, verification, stopping-conditions, incompatible-with,
    priority

Idempotent: keys already present are left exactly as they are. Nothing else in the file is
touched — bodies and the sibling's keys stay byte-identical.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# skill directory -> typed contract additions (id is the canonical, unprefixed skill id).
CONTRACTS: dict[str, dict] = {
    "vool-repo-onboarding": {
        "id": "repo-onboarding", "risk-class": "read_only", "priority": 10,
        "task-families": ["workspace_audit", "file_inspection"], "capability-families": ["workspace"],
        "prerequisites": [], "expected-outputs": ["onboarding_brief"],
        "verification": ["evidence_cited", "deterministic_evidence"],
        "stopping-conditions": ["stop when the brief answers how the tree is organised and where change lands"],
        "incompatible-with": [],
    },
    "vool-root-cause-repair": {
        "id": "root-cause-repair", "risk-class": "read_only", "priority": 25,
        "task-families": ["debugging"], "capability-families": ["workspace"],
        "prerequisites": [],
        "expected-outputs": ["root_cause_statement", "owning_seam", "evidence_chain"],
        "verification": ["failing_test_reproduces", "root_cause_state_confirmed", "evidence_cited"],
        "stopping-conditions": [
            "cause named with a mechanism and an evidence chain",
            "symptom-only closure is not a stopping condition — a suppressed symptom without a "
            "mechanism is an open diagnosis, reported as such",
        ],
        "incompatible-with": [],
    },
    "vool-bug-reproduction": {
        "id": "bug-reproduction", "risk-class": "workspace_write", "priority": 30,
        "task-families": ["debugging"], "capability-families": ["workspace", "sandbox"],
        "prerequisites": [],
        "expected-outputs": ["reproduction_test_or_command", "observed_vs_expected"],
        "verification": ["failing_test_reproduces", "deterministic_evidence"],
        "stopping-conditions": ["stop when the bug reproduces deterministically or is honestly reported as not reproduced"],
        "incompatible-with": [],
    },
    "vool-feature-build": {
        "id": "feature-build", "risk-class": "workspace_write", "priority": 20,
        "task-families": ["system_design"], "capability-families": ["workspace"],
        "prerequisites": [],
        "expected-outputs": ["changed_files", "test_results", "verification_statement"],
        "verification": ["cumulative_suite_green", "typed_receipts"],
        "stopping-conditions": ["stop when the change is built AND its tests run green — a built-but-unverified change is not done"],
        "incompatible-with": [],
    },
    "vool-cumulative-testing": {
        "id": "cumulative-testing", "risk-class": "read_only", "priority": 35,
        "task-families": ["debugging", "workspace_audit"], "capability-families": ["workspace"],
        "prerequisites": [],
        "expected-outputs": ["test_results", "failure_attribution"],
        "verification": ["cumulative_suite_green", "deterministic_evidence"],
        "stopping-conditions": ["stop when every failure is attributed: pre-existing, in-scope, or flaky — with the evidence for the label"],
        "incompatible-with": [],
    },
    "vool-code-review": {
        "id": "code-review", "risk-class": "read_only", "priority": 30,
        "task-families": ["workspace_audit"], "capability-families": ["workspace"],
        "prerequisites": [],
        "expected-outputs": ["review_findings", "verdict"],
        "verification": ["evidence_cited"],
        "stopping-conditions": ["stop when every finding carries its file:line and the verdict names the decision"],
        "incompatible-with": [],
    },
    "vool-security-audit": {
        "id": "security-audit", "risk-class": "read_only", "priority": 15,
        "task-families": ["security_hardening"], "capability-families": ["workspace"],
        "prerequisites": [],
        "expected-outputs": ["security_findings"],
        "verification": ["evidence_cited", "sabotage_proof"],
        "stopping-conditions": ["stop when each finding states the class, the seam, and the exploit path sketch — or the audit surface is exhausted and said so"],
        "incompatible-with": [],
    },
    "vool-git-worktrees": {
        "id": "git-worktrees", "risk-class": "workspace_write", "priority": 20,
        "task-families": ["shell_guidance"], "capability-families": ["sandbox", "workspace"],
        "prerequisites": ["binary:git"],
        "expected-outputs": ["worktree_path", "branch_name", "command_receipts"],
        "verification": ["typed_receipts", "deterministic_evidence"],
        "stopping-conditions": ["stop when the worktree exists, is on its branch, and the receipts name every command run"],
        "incompatible-with": [],
    },
    "vool-ci-repair": {
        "id": "ci-repair", "risk-class": "workspace_write", "priority": 40,
        "task-families": ["debugging", "integration_orchestration"], "capability-families": ["workspace", "sandbox"],
        "prerequisites": [],
        "expected-outputs": ["failure_attribution", "pipeline_fix", "local_verification_receipt"],
        "verification": ["failing_test_reproduces", "cumulative_suite_green"],
        "stopping-conditions": ["stop when the pipeline failure is reproduced locally and the fix's verification receipt exists"],
        "incompatible-with": [],
    },
    "vool-browser-qa": {
        "id": "browser-qa", "risk-class": "read_only", "priority": 30,
        "task-families": ["integration_orchestration"], "capability-families": ["web"],
        "prerequisites": [],
        "expected-outputs": ["qa_checklist_results", "fetched_page_evidence"],
        "verification": ["browser_verified", "evidence_cited"],
        "stopping-conditions": ["stop when every checklist row carries observed evidence — a row with no evidence is UNVERIFIED, never green"],
        "incompatible-with": [],
    },
    "vool-performance": {
        "id": "performance", "risk-class": "read_only", "priority": 45,
        "task-families": ["debugging"], "capability-families": ["workspace", "sandbox"],
        "prerequisites": [],
        "expected-outputs": ["baseline_measurement", "change", "after_measurement", "verdict"],
        "verification": ["benchmark_measured", "deterministic_evidence"],
        "stopping-conditions": ["stop when before and after numbers exist and the verdict says whether the change earned its complexity"],
        "incompatible-with": [],
    },
    "vool-migration": {
        "id": "migration", "risk-class": "workspace_write", "priority": 20,
        "task-families": ["dependency_resolution", "config"], "capability-families": ["workspace"],
        "prerequisites": [],
        "expected-outputs": ["migration_map", "changed_files", "verification_receipt"],
        "verification": ["cumulative_suite_green", "typed_receipts"],
        "stopping-conditions": ["stop when the migration map covers every call site and the suite runs green on the migrated tree"],
        "incompatible-with": [],
    },
    "vool-release-gate": {
        "id": "release-gate", "risk-class": "elevated", "priority": 15,
        "task-families": ["integration_orchestration"], "capability-families": ["workspace", "sandbox"],
        "prerequisites": [],
        "expected-outputs": ["gate_report", "ship_or_block_verdict", "per_gate_receipts"],
        "verification": ["cumulative_suite_green", "served_proof", "sabotage_proof"],
        "stopping-conditions": [
            "every gate executed with a recorded verdict and an overall SHIP or BLOCK",
            "any red or unexecuted gate blocks the release — a green verdict from partial evidence is the exact defect forbidden here",
        ],
        "incompatible-with": [],
    },
    "media-studio": {
        "id": "media-studio", "risk-class": "workspace_write", "priority": 100,
        "task-families": [], "capability-families": ["media"],
        "prerequisites": [],
        "expected-outputs": ["media_project_state", "exported_media_file", "typed_receipt"],
        "verification": ["typed_receipts", "evidence_cited"],
        "stopping-conditions": ["stop when the edit's typed receipt names the operation and the export path — never claim an export without its receipt"],
        "incompatible-with": [],
    },
    "vool-hive-mind": {
        "id": "vool-hive-mind", "risk-class": "read_only", "priority": 100,
        "task-families": [], "capability-families": ["hive", "network", "orchestration"],
        "prerequisites": [],
        "expected-outputs": ["bridge_guidance"],
        "verification": ["evidence_cited"],
        "stopping-conditions": ["stop when the bridge/mesh question is answered from the document"],
        "incompatible-with": [],
    },
}

TOOL_INTENTS: dict[str, list[str]] = {
    "vool-repo-onboarding": ["workspace.identity", "workspace.list_tree", "workspace.read_file", "workspace.search_text", "workspace.symbol_search", "workspace.git_summary"],
    "vool-root-cause-repair": ["workspace.search_text", "workspace.read_file", "workspace.git_diff", "workspace.symbol_search", "workspace.run_tests"],
    "vool-bug-reproduction": ["workspace.read_file", "workspace.search_text", "workspace.run_tests", "sandbox.run_command", "workspace.write_file"],
    "vool-feature-build": ["workspace.symbol_search", "workspace.read_file", "workspace.write_file", "workspace.ensure_directory", "workspace.apply_unified_diff", "workspace.run_tests", "workspace.run_lint"],
    "vool-cumulative-testing": ["workspace.run_tests", "workspace.run_lint", "workspace.run_formatter", "workspace.read_file"],
    "vool-code-review": ["workspace.git_diff", "workspace.read_file", "workspace.search_text", "workspace.run_lint"],
    "vool-security-audit": ["workspace.search_text", "workspace.read_file", "workspace.git_diff", "workspace.symbol_search"],
    "vool-git-worktrees": ["workspace.git_status", "workspace.git_diff", "workspace.read_file", "sandbox.run_command"],
    "vool-ci-repair": ["workspace.read_file", "workspace.search_text", "workspace.run_tests", "workspace.run_lint", "sandbox.run_command"],
    "vool-browser-qa": ["web.fetch", "web.search"],
    "vool-performance": ["workspace.search_text", "workspace.read_file", "workspace.run_tests", "sandbox.run_command"],
    "vool-migration": ["workspace.search_text", "workspace.read_file", "workspace.replace_in_file", "workspace.write_file", "workspace.apply_unified_diff", "workspace.run_tests"],
    "vool-release-gate": ["workspace.run_tests", "workspace.run_lint", "workspace.git_status", "workspace.git_diff", "sandbox.run_command"],
    "media-studio": ["media.open", "media.inspect", "media.edit", "media.undo", "media.redo", "media.export"],
    "vool-hive-mind": [],
}


def _yaml_value(value):
    if isinstance(value, list):
        return "[" + ", ".join(_yaml_value(v) for v in value) + "]"
    text = str(value)
    if re.fullmatch(r"[a-z0-9_./\-]+", text) or re.fullmatch(r"\d+", text):
        return text
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'


def enrich(path: Path, typed: dict, tool_intents: list[str]) -> bool:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if not match:
        print(f"SKIP (no frontmatter): {path}", file=sys.stderr)
        return False
    header, body = match.group(1), match.group(2)
    header_map: dict[str, str] = {}
    for line in header.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            header_map[k.strip()] = v.strip()
    additions: list[str] = []
    contract = dict(typed)
    contract.pop("id", None)
    # Identity and the ordering law are REWRITTEN in place when they differ; everything else
    # is added only when missing. A repeated key in YAML silently takes the last value, so
    # appending duplicates would be sloppy even when parse-equivalent.
    if header_map.get("id", "").strip('"') != typed["id"]:
        additions.append(f"id: {typed['id']}")
    for key in ("risk-class", "task-families", "capability-families", "tool-intents",
                "permitted-tools", "prerequisites", "expected-outputs", "verification",
                "stopping-conditions", "incompatible-with", "priority"):
        if key == "priority":
            if header_map.get("priority", "").strip() != str(typed.get("priority", 100)):
                additions.append(f"priority: {typed.get('priority', 100)}")
            continue
        if key in header_map:
            continue
        if key in ("tool-intents", "permitted-tools"):
            value = tool_intents
        else:
            value = contract.get(key)
            if value is None:
                continue
        additions.append(f"{key}: {_yaml_value(value)}")
    if not additions:
        print(f"ok (already typed): {path}")
        return True
    new_header = header.rstrip() + "\n" + "\n".join(additions) + "\n"
    path.write_text(f"---\n{new_header}---\n{body}", encoding="utf-8")
    print(f"enriched: {path} (+{len(additions)} keys)")
    return True


def main() -> int:
    skills_root = REPO / "skills"
    failures = 0
    for slug, typed in sorted(CONTRACTS.items()):
        path = skills_root / slug / "SKILL.md"
        if not path.is_file():
            print(f"MISSING: {path}", file=sys.stderr)
            failures += 1
            continue
        failures += 0 if enrich(path, typed, TOOL_INTENTS.get(slug, [])) else 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
