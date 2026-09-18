---
name: vool-performance
description: "Profile and improve a hot path with measured before/after evidence: run the bounding benchmark, change the owning seam, and prove the improvement or report that there is none. Use when the user asks to speed something up, profile, or fix slow performance."
version: 1.0.0
license: MIT
author: sls_0x
status: p1
type: capability_skill
triggers: [performance, profile, slow, speed up, optimize, hot path, faster]
allowed-tools: [workspace.search_text, workspace.read_file, workspace.run_tests, sandbox.run_command]
capabilities: [workspace.read, workspace.validate, sandbox.command]
permissions: [read_files, create_files]
effects: [read_only, validation_command, sandbox_command]
inputs: [hot_path_or_symptom, workload optional, workspace_root]
outputs: [baseline_measurement, change, after_measurement, verdict]
stop-conditions: [after measurement compared against baseline with a verdict, no measurable problem exists - report that instead of optimizing blind, change regresses - revert and report]
recovery: [if timing noise swamps the signal, increase the sample count and say so, if the improvement cannot be proven, revert the change rather than shipping a maybe]
cumulative-test-law: [tests/native_skills must pass before this package changes, selection phrase "profile the hot path and improve performance" must keep selecting this package, no performance claim without a measured before and after]
id: performance
risk-class: read_only
task-families: [debugging]
capability-families: [workspace, sandbox]
tool-intents: [workspace.search_text, workspace.read_file, workspace.run_tests, sandbox.run_command]
permitted-tools: [workspace.search_text, workspace.read_file, workspace.run_tests, sandbox.run_command]
prerequisites: []
expected-outputs: [baseline_measurement, change, after_measurement, verdict]
verification: [benchmark_measured, deterministic_evidence]
stopping-conditions: ["stop when before and after numbers exist and the verdict says whether the change earned its complexity"]
incompatible-with: []
priority: 45
---
# Performance

You make it FASTER, provably, or you report that you could not. Baseline first,
change second, measurement last — never a speed claim without both numbers.

## Scope

IN: measure the baseline, locate the owning seam, change it, re-measure,
verdict with numbers.
OUT: micro-"optimizations" without a measurement, complexity rewrites without
a profiled reason, shipping a maybe.

## Required capabilities (declaring is not granting)

`workspace.read`, `workspace.validate`, `sandbox.command`. Declaring is not
permission: benchmark runs pass the permission lane, bounded, output-capped.

## Procedure

1. `workspace.search_text` / `workspace.read_file` the named hot path.
2. `sandbox.run_command` a bounded baseline measurement; record the number and
   the command.
3. Change the owning seam — smallest change that addresses the measurement.
4. Re-run the identical measurement; `workspace.run_tests` the affected
   selection to prove no correctness regression.
5. Verdict: improved / neutral / regressed, with both numbers. Regressed:
   revert.

## Stop conditions

Stop at the measured verdict. No measurable problem: report that honestly
instead of optimizing blind.

## Recovery

Noise swamps signal: raise sample count and say so. Unprovable improvement:
revert, report, hand back.

## Cumulative test law

`tests/native_skills` is the pack; performance-lane edits re-run it.

## No direct execution bypass

Benchmarks go through the runtime tool door (`execute_runtime_tool`) — the
sandboxed, bounded command lane. No direct timing harness around it.
