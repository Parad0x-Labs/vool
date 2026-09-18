---
name: vool-root-cause-repair
description: "Diagnose a failure to its root cause before any fix is attempted: reproduce the symptom, locate the owning seam, name the mechanism. Use when the user reports a failing test, a regression, or a bug and wants the cause found."
version: 1.0.0
license: MIT
author: sls_0x
status: p1
type: capability_skill
triggers: [root, cause, diagnose, diagnosis, failing, regression, why broken]
allowed-tools: [workspace.search_text, workspace.read_file, workspace.git_diff, workspace.symbol_search, workspace.run_tests]
capabilities: [workspace.read, workspace.git, workspace.validate]
permissions: [read_files]
effects: [read_only, validation_command]
inputs: [symptom, failing_test_or_trace optional, workspace_root]
outputs: [root_cause_statement, owning_seam, evidence_chain]
stop-conditions: [cause named with a mechanism and an evidence chain, symptom cannot be reproduced - report that honestly, budget of evidence reads exhausted without a mechanism - say so]
recovery: [if the test run itself is broken, report the harness failure as the finding, if two mechanisms fit, present both and name the experiment that separates them]
cumulative-test-law: [tests/native_skills must pass before this package changes, selection phrase "find the root cause of this failing test" must keep selecting this package, sabotage proof: neutralising the capability gate must flip selection to refused-package detection]
id: root-cause-repair
risk-class: read_only
task-families: [debugging]
capability-families: [workspace]
tool-intents: [workspace.search_text, workspace.read_file, workspace.git_diff, workspace.symbol_search, workspace.run_tests]
permitted-tools: [workspace.search_text, workspace.read_file, workspace.git_diff, workspace.symbol_search, workspace.run_tests]
prerequisites: []
expected-outputs: [root_cause_statement, owning_seam, evidence_chain]
verification: [failing_test_reproduces, root_cause_state_confirmed, evidence_cited]
stopping-conditions: ["cause named with a mechanism and an evidence chain", "symptom-only closure is not a stopping condition — a suppressed symptom without a mechanism is an open diagnosis, reported as such"]
incompatible-with: []
priority: 25
---
# Root-Cause Repair

You find the CAUSE, not a nearby symptom. A fix without a mechanism is a
coincidence; this package does not ship coincidences.

## Scope

IN: reproduce/observe the failure, locate the owning seam, state the mechanism,
propose the minimal repair and the experiment that would falsify it.
OUT: applying the fix (hand over to feature-build or the operator), refactoring
around the cause, "probably a flake" without evidence.

## Required capabilities (declaring is not granting)

`workspace.read`, `workspace.git`, `workspace.validate`. Declaring them is not
permission; every execution still goes through the runtime permission lane.

## Procedure

1. Restate the symptom as an observable with expected vs actual.
2. `workspace.run_tests` the failing selection; capture the real trace.
3. `workspace.search_text` / `workspace.symbol_search` to the owning seam.
4. `workspace.git_diff` / history: what changed near this seam.
5. State the mechanism: "X happens because Y, evidenced by Z."
6. Propose the minimal repair and the falsifying experiment.

## Stop conditions

Stop when the cause has a named mechanism and an evidence chain — or when the
evidence honestly says "cannot reproduce". Never stop at "looks like a flake".

## Recovery

A broken harness IS a finding: report it. Two candidate mechanisms: present
both plus the separating experiment; never blend them into one confident claim.

## Cumulative test law

`tests/native_skills` is the pack. Diagnosis-behaviour edits re-run it; a change
that makes selection skip the capability gate does not ship.

## No direct execution bypass

Test runs and reads go through the runtime tool door (`execute_runtime_tool`)
with its sandbox and permission lanes. No shelling around the door.
