---
name: vool-git-worktrees
description: "Set up, list, and clean git worktrees so parallel lanes get isolated checkouts without disturbing the main tree. Use when the user asks for a worktree, a parallel checkout, or to isolate an experiment lane."
version: 1.0.0
license: MIT
author: sls_0x
status: p1
type: capability_skill
triggers: [worktree, worktrees, parallel checkout, isolate an experiment, git branch lane]
allowed-tools: [workspace.git_status, workspace.git_diff, workspace.read_file, sandbox.run_command]
capabilities: [workspace.read, workspace.git, sandbox.command]
permissions: [read_files, create_files]
effects: [read_only, sandbox_command]
inputs: [workspace_root, lane_name, base_ref optional]
outputs: [worktree_path, branch_name, command_receipts]
stop-conditions: [worktree created and reported with its path and branch, target path already exists - report and stop, git refuses - report the refusal verbatim]
recovery: [if creation fails midway, report the exact git state and the undo command, never prune or remove a worktree without an explicit user instruction naming it]
cumulative-test-law: [tests/native_skills must pass before this package changes, selection phrase "set up a git worktree for this experiment" must keep selecting this package, the served workflow must keep listing real worktrees through the sandbox door]
id: git-worktrees
risk-class: workspace_write
task-families: [shell_guidance]
capability-families: [sandbox, workspace]
tool-intents: [workspace.git_status, workspace.git_diff, workspace.read_file, sandbox.run_command]
permitted-tools: [workspace.git_status, workspace.git_diff, workspace.read_file, sandbox.run_command]
prerequisites: ["binary:git"]
expected-outputs: [worktree_path, branch_name, command_receipts]
verification: [typed_receipts, deterministic_evidence]
stopping-conditions: ["stop when the worktree exists, is on its branch, and the receipts name every command run"]
incompatible-with: []
priority: 20
---
# Git Worktrees

You give each lane its own checkout. The main tree is never disturbed: every
mutation is a git command through the sandbox door, with a receipt.

## Scope

IN: create a worktree at a named path on a named branch, list existing
worktrees, report status/diff per lane, hand back exact undo commands.
OUT: deleting or pruning worktrees unless the user names them explicitly,
pushing anything, rebasing lanes.

## Required capabilities (declaring is not granting)

`workspace.read`, `workspace.git`, `sandbox.command`. Declaring is not
permission: command runs pass the permission lane, network blocked, output
bounded, and a refusal is reported verbatim.

## Procedure

1. `workspace.git_status` the main tree; confirm it is a sane base.
2. Choose `base_ref` (default: the current HEAD) and say it out loud.
3. `sandbox.run_command` the `git worktree add` for the named lane.
4. `sandbox.run_command` `git worktree list` to verify the result.
5. Deliver: worktree path, branch, receipts, and the undo command.

## Stop conditions

Stop after the verified creation report. Existing target path: report and stop
— never overwrite.

## Recovery

Mid-failure: report exact git state plus the undo command. Cleanup only on an
explicit instruction naming the worktree.

## Cumulative test law

`tests/native_skills` is the pack; worktree-behaviour edits re-run it.

## No direct execution bypass

Git goes through the runtime tool door (`execute_runtime_tool`) — the
sandboxed command lane with its guard and caps. No direct git subprocess
spawned around it.
