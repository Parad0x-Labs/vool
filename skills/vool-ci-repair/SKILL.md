---
name: vool-ci-repair
description: "Repair a red pipeline: read the failing job, reproduce the failure locally, and fix the pipeline definition or the code it exposes. Use when the user says the CI is red, the pipeline is broken, or a build must be made green."
version: 1.0.0
license: MIT
author: sls_0x
status: p1
type: capability_skill
triggers: [red build, pipeline, broken build, fix the build, pipeline repair]
allowed-tools: [workspace.read_file, workspace.search_text, workspace.run_tests, workspace.run_lint, sandbox.run_command]
capabilities: [workspace.read, workspace.validate, sandbox.command]
permissions: [read_files, create_files]
effects: [read_only, validation_command, sandbox_command]
inputs: [ci_log_or_job_ref, pipeline_config_path optional, workspace_root]
outputs: [failure_attribution, pipeline_fix, local_verification_receipt]
stop-conditions: [failure reproduced locally and fixed with a green local run, failure is environment-only - report the exact infra gap instead of patching around it, log unreadable or absent - ask for the job reference and stop]
recovery: [if the local run cannot mirror the job, say exactly which step diverges before proposing a fix, if the fix would flake-pass by loosening a check, refuse and say why]
cumulative-test-law: [tests/native_skills must pass before this package changes, selection phrase "the CI is red, repair the pipeline" must keep selecting this package, a repair without a green local verification run must not be reported as fixed]
id: ci-repair
risk-class: workspace_write
task-families: [debugging, integration_orchestration]
capability-families: [workspace, sandbox]
tool-intents: [workspace.read_file, workspace.search_text, workspace.run_tests, workspace.run_lint, sandbox.run_command]
permitted-tools: [workspace.read_file, workspace.search_text, workspace.run_tests, workspace.run_lint, sandbox.run_command]
prerequisites: []
expected-outputs: [failure_attribution, pipeline_fix, local_verification_receipt]
verification: [failing_test_reproduces, cumulative_suite_green]
stopping-conditions: ["stop when the pipeline failure is reproduced locally and the fix's verification receipt exists"]
incompatible-with: []
priority: 40
---
# CI Repair

You repair the pipeline honestly: reproduce the job's failure locally, then fix
the definition or the code it exposes. "Probably passes now" is not a repair.

## Scope

IN: read the failing job/log, mirror the failing step locally, fix the pipeline
definition or the underlying code, verify with a green local run.
OUT: loosening checks to go green, retry-spamming the remote service, editing
runner infrastructure you cannot see.

## Required capabilities (declaring is not granting)

`workspace.read`, `workspace.validate`, `sandbox.command`. Declaring is not
permission: local verification runs pass the permission lane like any command.

## Procedure

1. `workspace.read_file` the pipeline definition; `workspace.search_text` the
   failing step's targets.
2. Mirror the failing step locally through `workspace.run_tests` /
   `sandbox.run_command`, bounded to the failing selection.
3. Attribute: code defect, pipeline defect, or environment-only gap.
4. Fix the owning layer — code to code, pipeline definition to definition.
5. Re-run the mirrored step; deliver the green receipt with the fix.

## Stop conditions

Stop at the green receipt — or at the honest "environment-only" report naming
the infra gap. Never at "should pass now".

## Recovery

Local cannot mirror the job: name the diverging step before proposing anything.
A fix that only loosens a check: refused, with the reason stated.

## Cumulative test law

`tests/native_skills` is the pack; repair-behaviour edits re-run it.

## No direct execution bypass

Reproduction and verification go through the runtime tool door
(`execute_runtime_tool`) — sandboxed, bounded, gated. No direct runner calls
around it.
