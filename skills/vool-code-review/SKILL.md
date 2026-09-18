---
name: vool-code-review
description: "Review a diff before it lands: correctness at the owning seams, test coverage of the new behaviour, and honesty of the claims. Use when the user asks to review a diff, a patch, or a change before push."
version: 1.0.0
license: MIT
author: sls_0x
status: p1
type: capability_skill
triggers: [review, diff, patch, before push, look over my change]
allowed-tools: [workspace.git_diff, workspace.read_file, workspace.search_text, workspace.run_lint]
capabilities: [workspace.read, workspace.git, workspace.validate]
permissions: [read_files]
effects: [read_only, validation_command]
inputs: [workspace_root, diff_ref optional]
outputs: [review_findings, verdict]
stop-conditions: [review delivered with findings and a verdict, no diff exists to review - say so, findings list exhausted - do not pad with style nits]
recovery: [if the diff is empty, review the named files directly and label it a file review not a diff review, if lint cannot run, review without it and mark that limit in the verdict]
cumulative-test-law: [tests/native_skills must pass before this package changes, selection phrase "review my diff before I push" must keep selecting this package, review findings must cite the diff hunks they came from]
id: code-review
risk-class: read_only
task-families: [workspace_audit]
capability-families: [workspace]
tool-intents: [workspace.git_diff, workspace.read_file, workspace.search_text, workspace.run_lint]
permitted-tools: [workspace.git_diff, workspace.read_file, workspace.search_text, workspace.run_lint]
prerequisites: []
expected-outputs: [review_findings, verdict]
verification: [evidence_cited]
stopping-conditions: ["stop when every finding carries its file:line and the verdict names the decision"]
incompatible-with: []
priority: 30
---
# Code Review

You review the CHANGE, not the world. Findings cite hunks; the verdict states
land / fix-first / reject and why.

## Scope

IN: the diff — correctness at its seams, coverage of new behaviour, honesty of
claims (tests that pin the change, no silent widening), lint signal.
OUT: rewrites of untouched code, style bikeshedding beyond the linter's own
rules, approving by vibes.

## Required capabilities (declaring is not granting)

`workspace.read`, `workspace.git`, `workspace.validate`. Declaring is not
permission; reads stay read-class and gated.

## Procedure

1. `workspace.git_diff` the change; if empty, ask which files or fall back to
   the named files, labelled as a file review.
2. `workspace.read_file` each hunk's owning file with context.
3. `workspace.search_text` for the callers the change can break.
4. `workspace.run_lint` for mechanical signal.
5. Deliver findings (each citing its hunk) + verdict: land / fix-first /
   reject.

## Stop conditions

Stop when findings are delivered. No padding: a missing finding is honest, a
manufactured one is noise.

## Recovery

Lint unavailable: say the verdict carries no lint signal. Diff unreadable:
report the exact failure, do not review from imagination.

## Cumulative test law

`tests/native_skills` is the pack; review-behaviour edits re-run it.

## No direct execution bypass

Diff and lint go through the runtime tool door (`execute_runtime_tool`). No
direct git or linter invocation around it.
