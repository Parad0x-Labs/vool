---
name: vool-release-gate
description: "Run the release gate: the bounded verification battery a change must pass before it ships, with every gate verdict recorded and any red gate blocking the release. Use when the user asks to run the release gate, verify shippability, or cut a release."
version: 1.0.0
license: MIT
author: sls_0x
status: p1
type: capability_skill
triggers: [release, gate, ship, shippable, cut a release, release check]
allowed-tools: [workspace.run_tests, workspace.run_lint, workspace.git_status, workspace.git_diff, sandbox.run_command]
capabilities: [workspace.read, workspace.validate, workspace.git, sandbox.command]
permissions: [read_files, create_files]
effects: [validation_command, read_only, sandbox_command]
inputs: [workspace_root, release_ref optional]
outputs: [gate_report, verdict ship or block, per-gate receipts]
stop-conditions: [every gate executed with a recorded verdict and an overall verdict issued, any red gate - the release is BLOCKED and reported so, the tree is dirty - report the dirt and stop before gates]
recovery: [if a gate cannot execute, the verdict for that gate is UNVERIFIED, never silently green, if results look flaky, re-run that gate once sequentially and report both outcomes]
cumulative-test-law: [tests/native_skills must pass before this package changes, selection phrase "run the release gate before we ship" must keep selecting this package, a green overall verdict with any gate unexecuted is the exact defect this package forbids]
id: release-gate
risk-class: elevated
task-families: [integration_orchestration]
capability-families: [workspace, sandbox]
tool-intents: [workspace.run_tests, workspace.run_lint, workspace.git_status, workspace.git_diff, sandbox.run_command]
permitted-tools: [workspace.run_tests, workspace.run_lint, workspace.git_status, workspace.git_diff, sandbox.run_command]
prerequisites: []
expected-outputs: [gate_report, ship_or_block_verdict, per_gate_receipts]
verification: [cumulative_suite_green, served_proof, sabotage_proof]
stopping-conditions: ["every gate executed with a recorded verdict and an overall SHIP or BLOCK", "any red or unexecuted gate blocks the release — a green verdict from partial evidence is the exact defect forbidden here"]
incompatible-with: []
priority: 15
---
# Release Gate

You run the gates and RECORD the verdicts. The release state is what the gates
say — green means every gate executed green, not "no news".

## Scope

IN: dirty-tree check, test gate, lint gate, git-state gate, tagged build
command; a recorded verdict per gate; an overall SHIP or BLOCK.
OUT: fixing what the gates find (hand to root-cause-repair), publishing or
pushing anything, declaring green from partial evidence.

## Required capabilities (declaring is not granting)

`workspace.read`, `workspace.validate`, `workspace.git`, `sandbox.command`.
Declaring is not permission: gate runs pass the permission lane like any
execution.

## Procedure

1. `workspace.git_status` — a dirty tree is reported and BLOCKS before gates.
2. `workspace.git_diff` — what would ship; state it in one paragraph.
3. `workspace.run_tests` the bounded pack; record the verdict.
4. `workspace.run_lint`; record the verdict.
5. `sandbox.run_command` the named build command; record the verdict.
6. Deliver the gate report: per-gate verdict + overall SHIP or BLOCK.

## Stop conditions

Any red or unexecuted gate: overall verdict is BLOCK, reported plainly. Never
ship-patch around a red gate.

## Recovery

Gate cannot execute: its verdict is UNVERIFIED and the release blocks. Flaky
gate: one sequential re-run, both outcomes recorded.

## Cumulative test law

`tests/native_skills` is the pack; gate-lane edits re-run it.

## No direct execution bypass

Every gate runs through the runtime tool door (`execute_runtime_tool`) with its
permission lane and receipts. No direct gate scripts around it.
