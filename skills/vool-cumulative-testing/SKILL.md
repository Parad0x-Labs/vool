---
name: vool-cumulative-testing
description: "Run and interpret the cumulative test pack: the full suite plus the pack's own bounded selection, attributing every failure to its change or to a pre-existing base failure. Use when the user asks to run the tests, the pack, or cumulative verification."
version: 1.0.0
license: MIT
author: sls_0x
status: p1
type: capability_skill
triggers: [cumulative, test pack, suite, run the tests, verification pack]
allowed-tools: [workspace.run_tests, workspace.run_lint, workspace.run_formatter, workspace.read_file]
capabilities: [workspace.read, workspace.validate]
permissions: [read_files]
effects: [validation_command, read_only]
inputs: [workspace_root, selection optional]
outputs: [test_results, failure_attribution]
stop-conditions: [pack executed and every failure attributed or reported unattributed, the harness itself will not run - report that as the finding]
recovery: [if a selection is too large, bound it and say what was skipped, if results are flaky, re-run once sequentially and report both outcomes]
cumulative-test-law: [tests/native_skills must pass before this package changes, selection phrase "run the cumulative test pack" must keep selecting this package, a green claim without a real executed run is the defect this package exists to prevent]
id: cumulative-testing
risk-class: read_only
task-families: [debugging, workspace_audit]
capability-families: [workspace]
tool-intents: [workspace.run_tests, workspace.run_lint, workspace.run_formatter, workspace.read_file]
permitted-tools: [workspace.run_tests, workspace.run_lint, workspace.run_formatter, workspace.read_file]
prerequisites: []
expected-outputs: [test_results, failure_attribution]
verification: [cumulative_suite_green, deterministic_evidence]
stopping-conditions: ["stop when every failure is attributed: pre-existing, in-scope, or flaky — with the evidence for the label"]
incompatible-with: []
priority: 35
---
# Cumulative Testing

You run the pack and ATTRIBUTE the results. A green claim without an executed
run is the exact defect this package exists to prevent; so is a red suite
reported as "mostly passing".

## Scope

IN: run the bounded pack (full suite or named selection), attribute each
failure (caused by the change / pre-existing at base / flaky with evidence),
summarize pass/fail counts exactly.
OUT: fixing failures (hand to root-cause-repair), re-running until green
(selective re-runs are allowed once, reported as such).

## Required capabilities (declaring is not granting)

`workspace.read`, `workspace.validate`. Declaring is not permission; the run
goes through the runtime permission lane like any execution.

## Procedure

1. `workspace.read_file` the harness config to learn the pack's bounds.
2. `workspace.run_tests` the named selection — bounded, never unbounded.
3. `workspace.run_lint` when the change touches style-sensitive surfaces.
4. Attribute every failure: caused / pre-existing / flake, with evidence.
5. Report counts exactly: N passed, M failed, K skipped, attribution list.

## Stop conditions

Stop at the attribution report. A harness that will not start is reported as
the finding, not worked around silently.

## Recovery

Selection too large: bound it and say what was skipped. Flaky result: one
sequential re-run, both outcomes reported.

## Cumulative test law

`tests/native_skills` is the pack; testing-behaviour edits re-run it.

## No direct execution bypass

Test runs go through the runtime tool door (`execute_runtime_tool`) and its
validation lane with output caps. No direct runner invocation around it.
