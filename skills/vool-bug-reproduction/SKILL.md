---
name: vool-bug-reproduction
description: "Turn a bug report into a minimal, running reproduction: a failing test or command that demonstrates the defect on this machine. Use when the user asks to reproduce, confirm, or build a repro for a reported bug."
version: 1.0.0
license: MIT
author: sls_0x
status: p1
type: capability_skill
triggers: [reproduce, repro, reproduction, bug report, confirm the defect]
allowed-tools: [workspace.read_file, workspace.search_text, workspace.run_tests, sandbox.run_command, workspace.write_file]
capabilities: [workspace.read, workspace.write, workspace.validate, sandbox.command]
permissions: [read_files, create_files]
effects: [read_only, validation_command, sandbox_command, workspace_write]
inputs: [bug_report, expected_behavior, workspace_root]
outputs: [reproduction_test_or_command, observed_vs_expected]
stop-conditions: [reproduction runs and shows the defect, defect cannot be reproduced on this machine - report the exact steps tried, the report describes intended behavior not a defect - say so]
recovery: [if the reproduction cannot be minimized, deliver the smallest failing form that runs, if a write is refused, fall back to a command-level demonstration and label it weaker]
cumulative-test-law: [tests/native_skills must pass before this package changes, selection phrase "reproduce this bug report" must keep selecting this package, the served workflow pattern (select then execute real tools) must stay green]
id: bug-reproduction
risk-class: workspace_write
task-families: [debugging]
capability-families: [workspace, sandbox]
tool-intents: [workspace.read_file, workspace.search_text, workspace.run_tests, sandbox.run_command, workspace.write_file]
permitted-tools: [workspace.read_file, workspace.search_text, workspace.run_tests, sandbox.run_command, workspace.write_file]
prerequisites: []
expected-outputs: [reproduction_test_or_command, observed_vs_expected]
verification: [failing_test_reproduces, deterministic_evidence]
stopping-conditions: ["stop when the bug reproduces deterministically or is honestly reported as not reproduced"]
incompatible-with: []
priority: 30
---
# Bug Reproduction

You convert a report into a REPRODUCTION: something that runs here and shows
expected vs actual. "I can see how that could happen" is not a reproduction.

## Scope

IN: distil the report, build the smallest failing test or command, run it,
report observed vs expected exactly.
OUT: fixing the defect (that is root-cause-repair / feature-build territory),
speculating about the mechanism beyond what the repro shows.

## Required capabilities (declaring is not granting)

`workspace.read`, `workspace.write`, `workspace.validate`, `sandbox.command`.
Declaring is not permission: writes and command runs still pass the permission
lane, and a refusal is reported, never routed around.

## Procedure

1. Extract expected vs actual from the report; name the environment claims.
2. `workspace.search_text` / `workspace.read_file` the named surfaces.
3. Build the smallest demonstration: a failing test where a harness exists,
   else one sandboxed command.
4. Run it through the door. Capture the real output.
5. Deliver: repro + observed vs expected + how to reset.

## Stop conditions

Stop when the repro runs and shows the defect — or when honest attempts say it
does not reproduce here, with the exact steps tried.

## Recovery

Cannot minimize: deliver the smallest form that RUNS and say it is not minimal.
Write refused: fall back to a command-level demonstration and label it weaker.

## Cumulative test law

`tests/native_skills` is the pack; reproduction-behaviour edits re-run it.

## No direct execution bypass

Repro commands go through the runtime tool door (`execute_runtime_tool`) and
its sandbox — network blocked, bounded, permission-gated. Never around it.
