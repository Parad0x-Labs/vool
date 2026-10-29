---
name: vool-cumulative-testing
description: "Run the tests and explain the results: exact pass and fail counts, every failure put down to the change, to an earlier failure, or to a flaky test, and no pass claim without a fresh run. Use when the user asks to run the tests, asks which failures their change caused, or asks whether something is really fixed or passing."
version: 1.0.0
license: MIT
author: sls_0x
status: p1
type: capability_skill
audience: developer
triggers: [run the tests, run my tests, test pack, test suite, cumulative, verification pack, did the tests pass, is it really fixed, does it pass now, which failures did my change cause, check it passes]
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

You run the tests and explain every result. No claim that anything passes, is
fixed or is done without a run in this turn that shows it: name the command
that proves the claim, run it in full now, read the exit status and counts,
then state the result with the numbers. An earlier run or "should pass" does
not count. A clean lint does not prove the tests pass; passing tests prove a
fix only if one covers the original symptom.

## Steps

1. `workspace.read_file` the test config, unless the user gave the command.
2. `workspace.run_tests` the named selection, bounded; say what was skipped.
3. `workspace.run_lint` when the change touches style-sensitive files.
4. Attribute every failure: caused by the change, failing before it (with the
   evidence), or flaky (only after one rerun, both results shown).
5. Report exactly: N passed, M failed, K skipped, then the attributions.

A red suite is never "mostly passing". A harness that will not start is the
finding. Fixing belongs to root-cause-repair. Runs go through the runtime tool
door (`execute_runtime_tool`).
