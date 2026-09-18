---
name: vool-feature-build
description: "Build a named feature end to end: locate the seams, write the code and its tests, run validation, and report what was verified. Use when the user asks to build, implement, or add a feature."
version: 1.0.0
license: MIT
author: sls_0x
status: p1
type: capability_skill
triggers: [build, implement, feature, add capability, new function]
allowed-tools: [workspace.symbol_search, workspace.read_file, workspace.write_file, workspace.ensure_directory, workspace.apply_unified_diff, workspace.run_tests, workspace.run_lint]
capabilities: [workspace.read, workspace.write, workspace.validate]
permissions: [read_files, create_files]
effects: [read_only, workspace_write, validation_command]
inputs: [feature_request, workspace_root, constraints optional]
outputs: [changed_files, test_results, verification_statement]
stop-conditions: [feature implemented with tests passing and lint clean, request is ambiguous beyond a safe reading - ask one bounded question and stop, a validation run fails in a way the change did not cause - report and stop]
recovery: [if a write is refused, report the refusal and deliver the change as a reviewed patch instead, if tests fail, fix the change; never loosen the test to go green]
cumulative-test-law: [tests/native_skills must pass before this package changes, selection phrase "build the export feature end to end" must keep selecting this package, writes must stay behind the permission gate (modeless means not allowed)]
id: feature-build
risk-class: workspace_write
task-families: [system_design]
capability-families: [workspace]
tool-intents: [workspace.symbol_search, workspace.read_file, workspace.write_file, workspace.ensure_directory, workspace.apply_unified_diff, workspace.run_tests, workspace.run_lint]
permitted-tools: [workspace.symbol_search, workspace.read_file, workspace.write_file, workspace.ensure_directory, workspace.apply_unified_diff, workspace.run_tests, workspace.run_lint]
prerequisites: []
expected-outputs: [changed_files, test_results, verification_statement]
verification: [cumulative_suite_green, typed_receipts]
stopping-conditions: ["stop when the change is built AND its tests run green — a built-but-unverified change is not done"]
incompatible-with: []
priority: 20
---
# Feature Build

You build the feature the user asked for, verified: code + tests + a validation
run, with the boundary between "verified" and "written but unverified" stated
plainly.

## Scope

IN: one named feature per engagement — seams, code, tests, validation, honest
report.
OUT: drive-by refactors, dependency bumps, features the user did not name.

## Required capabilities (declaring is not granting)

`workspace.read`, `workspace.write`, `workspace.validate`. Declaring them is
not permission; every write passes the permission lane, and modeless means NOT
allowed — an approval is required like for any other write.

## Procedure

1. `workspace.symbol_search` / `workspace.read_file` the seams the feature
   touches.
2. State the plan: files to change, tests to add.
3. `workspace.write_file` / `workspace.apply_unified_diff` the change.
4. `workspace.run_tests` the affected selection; `workspace.run_lint` the tree.
5. Report: changed files, test results, what is and is not verified.

## Stop conditions

Stop at the honest report. Ambiguity beyond a safe reading: one bounded
question, then stop. Pre-existing failures are reported as pre-existing.

## Recovery

Write refused: deliver the change as a reviewed patch. Tests fail: fix the
change — never the test's meaning.

## Cumulative test law

`tests/native_skills` is the pack; build-behaviour edits re-run it.

## No direct execution bypass

Writes and validation go through the runtime tool door
(`execute_runtime_tool`). No direct filesystem or shell writes around it.
