---
name: vool-security-audit
description: "Audit a workspace for security defects: secrets in the tree, injection seams, fail-open error handling, and permission gaps — reported with file-and-line evidence, read-only. Use when the user asks for a security audit or a vulnerability pass."
version: 1.0.0
license: MIT
author: sls_0x
status: p1
type: capability_skill
triggers: [security, audit, vulnerability, secrets scan, security pass]
allowed-tools: [workspace.search_text, workspace.read_file, workspace.git_diff, workspace.symbol_search]
capabilities: [workspace.read, workspace.git]
permissions: [read_files]
effects: [read_only]
inputs: [workspace_root, scope optional]
outputs: [findings with file, line, class, severity, exploit_path sketch]
stop-conditions: [audit delivered with every finding evidenced, scope unreadable - report that, no findings - report the negative result with what was checked]
recovery: [if a scope cannot be read, mark it unexamined in the report rather than skipping silently, if a finding cannot be confirmed, report it as suspected with the reason]
cumulative-test-law: [tests/native_skills must pass before this package changes, selection phrase "audit this module for security issues" must keep selecting this package, every finding must carry file-and-line evidence or be labelled suspected]
id: security-audit
risk-class: read_only
task-families: [security_hardening]
capability-families: [workspace]
tool-intents: [workspace.search_text, workspace.read_file, workspace.git_diff, workspace.symbol_search]
permitted-tools: [workspace.search_text, workspace.read_file, workspace.git_diff, workspace.symbol_search]
prerequisites: []
expected-outputs: [security_findings]
verification: [evidence_cited, sabotage_proof]
stopping-conditions: ["stop when each finding states the class, the seam, and the exploit path sketch — or the audit surface is exhausted and said so"]
incompatible-with: []
priority: 15
---
# Security Audit

You find and EVIDENCE security defects. A finding without file-and-line is a
rumour; this package ships findings, not rumours. Read-only by contract.

## Scope

IN: secrets committed in the tree, injection seams at trust boundaries,
fail-open error handling, missing permission checks on dangerous surfaces,
dependency red flags visible from manifests.
OUT: exploiting anything, writing fixes (hand to feature-build), touching
operator credentials — a suspected secret is REPORTED, never echoed.

## Required capabilities (declaring is not granting)

`workspace.read`, `workspace.git`. Read-only: this package declares no write
and no command effects, and must never gain them casually.

## Procedure

1. `workspace.search_text` the secret and dangerous-API patterns, scoped.
2. `workspace.read_file` each hit with context; classify: confirmed /
   suspected.
3. `workspace.git_diff` recent changes for freshly introduced seams.
4. `workspace.symbol_search` the guards that should own each seam.
5. Deliver findings: file, line, class, severity, exploit-path sketch, fix
   direction.

## Stop conditions

Stop at the report — including the honest negative result naming what was
checked and found clean.

## Recovery

Unreadable scope: marked unexamined in the report, never skipped silently.
Unconfirmable finding: labelled suspected, with the reason.

## Cumulative test law

`tests/native_skills` is the pack; audit-behaviour edits re-run it.

## No direct execution bypass

All evidence gathering goes through the runtime tool door
(`execute_runtime_tool`) read lanes. No direct tree walking around it.
