---
name: vool-repo-onboarding
description: "Build a grounded onboarding brief for a repository the user just opened: layout, entry points, conventions, and where work happens. Use when the user asks to onboard, get oriented, tour, or map a codebase."
version: 1.0.0
license: MIT
author: sls_0x
status: p1
type: capability_skill
triggers: [onboard, onboarding, orient, repository, codebase, tour, get oriented]
allowed-tools: [workspace.identity, workspace.list_tree, workspace.read_file, workspace.search_text, workspace.symbol_search, workspace.git_summary]
capabilities: [workspace.read, workspace.git]
permissions: [read_files, list_directories]
effects: [read_only]
inputs: [workspace_root, focus_area optional]
outputs: [onboarding_brief]
stop-conditions: [brief delivered with layout, entry points, conventions, and open questions, workspace unreadable or empty - report that, not a guess]
recovery: [if a read is refused, name the refusal and continue with the reads that succeeded, if the tree is too large, bound to the top two levels and say what was skipped]
cumulative-test-law: [tests/native_skills must pass before this package changes, selection phrase "onboard me to this repository" must keep selecting this package, served workflow workspace.list_tree + workspace.read_file must stay green]
id: repo-onboarding
risk-class: read_only
task-families: [workspace_audit, file_inspection]
capability-families: [workspace]
tool-intents: [workspace.identity, workspace.list_tree, workspace.read_file, workspace.search_text, workspace.symbol_search, workspace.git_summary]
permitted-tools: [workspace.identity, workspace.list_tree, workspace.read_file, workspace.search_text, workspace.symbol_search, workspace.git_summary]
prerequisites: []
expected-outputs: [onboarding_brief]
verification: [evidence_cited, deterministic_evidence]
stopping-conditions: ["stop when the brief answers how the tree is organised and where change lands"]
incompatible-with: []
priority: 10
---
# Repo Onboarding

You give the user a grounded map of the repository they just opened. Every claim
in the brief cites a file you actually read this turn; nothing is invented from
memory of "projects like this one".

## Scope

IN: repository layout, entry points, test layout, build/run conventions, git
state, where the named focus area lives.
OUT: fixing anything, writing anything, deep-dives past the brief — offer them
as follow-ups instead.

## Required capabilities (declaring is not granting)

`workspace.read` and `workspace.git`. Seeing them is NOT permission: every call
still goes through the permission lane, and a refusal is reported plainly.

## Procedure

1. `workspace.identity` — confirm which workspace is bound.
2. `workspace.list_tree` — layout, bounded to the top levels.
3. `workspace.read_file` the README / pyproject / package manifest.
4. `workspace.symbol_search` the entry points; `workspace.git_summary` for
   recent direction.
5. Deliver the brief: layout, entry points, conventions, open questions.

## Stop conditions

Stop and hand over the brief the moment it answers layout + entry points +
conventions. Do not keep reading "for completeness".

## Recovery

A refused read is reported and the brief is built from what succeeded. An empty
or unreadable workspace is reported as such — never papered over.

## Cumulative test law

`tests/native_skills` is this package's cumulative pack. Any edit to this file
re-runs it; a change that breaks automatic selection or the served onboarding
workflow does not ship.

## No direct execution bypass

All reads go through the runtime tool door (`execute_runtime_tool`) and its
permission lane. No direct file reads around the door, ever.
