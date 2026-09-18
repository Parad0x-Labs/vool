---
name: vool-migration
description: "Migrate a surface to a new schema, API, or layout: map every call site, transform mechanicalally, and prove the migration with the validation suite. Use when the user asks to migrate, port, or upgrade a schema, config, or interface."
version: 1.0.0
license: MIT
author: sls_0x
status: p1
type: capability_skill
triggers: [migrate, migration, port, schema change, upgrade the format, move to the new]
allowed-tools: [workspace.search_text, workspace.read_file, workspace.replace_in_file, workspace.write_file, workspace.apply_unified_diff, workspace.run_tests]
capabilities: [workspace.read, workspace.write, workspace.validate]
permissions: [read_files, create_files]
effects: [read_only, workspace_write, validation_command]
inputs: [old_schema, new_schema, workspace_root]
outputs: [migration_map, changed_files, verification_receipt]
stop-conditions: [every mapped call site migrated and the suite green, an unmigratable site is found - report it and stop rather than half-migrate, the two schemas are incompatible - present the gap instead of forcing it]
recovery: [if a transform would change behavior, not just shape, stop at that site and ask, if validation fails after migration, attribute to migration or pre-existing before any further edit]
cumulative-test-law: [tests/native_skills must pass before this package changes, selection phrase "migrate the config schema to the new format" must keep selecting this package, migration completeness is proven by a zero-hit search for the old surface]
id: migration
risk-class: workspace_write
task-families: [dependency_resolution, config]
capability-families: [workspace]
tool-intents: [workspace.search_text, workspace.read_file, workspace.replace_in_file, workspace.write_file, workspace.apply_unified_diff, workspace.run_tests]
permitted-tools: [workspace.search_text, workspace.read_file, workspace.replace_in_file, workspace.write_file, workspace.apply_unified_diff, workspace.run_tests]
prerequisites: []
expected-outputs: [migration_map, changed_files, verification_receipt]
verification: [cumulative_suite_green, typed_receipts]
stopping-conditions: ["stop when the migration map covers every call site and the suite runs green on the migrated tree"]
incompatible-with: []
priority: 20
---
# Migration

You migrate COMPLETELY or you report the remainder. A half-migration that
quietly straddles two schemas is the defect this package exists to prevent.

## Scope

IN: map old→new, transform the mechanical majority, isolate and report the
sites that need judgment, prove completeness by search, verify with the suite.
OUT: behavior changes smuggled into a "mechanical" migration, deleting the old
surface before the new one is verified, partial straddlers.

## Required capabilities (declaring is not granting)

`workspace.read`, `workspace.write`, `workspace.validate`. Declaring is not
permission: writes pass the permission lane; modeless means not allowed.

## Procedure

1. `workspace.search_text` every use of the old surface; build the map.
2. Classify sites: mechanical / needs-judgment / incompatible.
3. `workspace.replace_in_file` / `workspace.apply_unified_diff` the mechanical
   set.
4. Re-run the search: zero old-surface hits outside the reported remainder.
5. `workspace.run_tests` the suite; attribute any failure; deliver the map +
   receipt + remainder.

## Stop conditions

Stop at the completeness report: map, receipt, and the honest remainder. An
incompatible pair is presented as a gap, never forced.

## Recovery

Judgment-call site: stop there and ask, carrying the migration map. Post-migration
red: attribute migration vs pre-existing before touching anything else.

## Cumulative test law

`tests/native_skills` is the pack; migration-lane edits re-run it.

## No direct execution bypass

Transforms and verification go through the runtime tool door
(`execute_runtime_tool`). No bulk rewrite scripts outside the door.
