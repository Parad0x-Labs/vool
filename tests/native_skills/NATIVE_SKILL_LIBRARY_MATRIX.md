# Native Skill Library — IMPLEMENTED / SERVED / PARTIAL / ABSENT matrix

Lane: `build/native-skill-library-p1-20260902` · base `a7b78e2a` · 2026-09-02
Pack: `tests/native_skills/` — 71 tests, all green (see `evidence/`).

Verdict vocabulary, used honestly:

- **IMPLEMENTED** — the thing exists as real code/packages and its unit law is pinned green.
- **SERVED** — the thing was executed against the real runtime seams in this pack (real loader,
  real tool door `execute_runtime_tool`, real files), not only asserted.
- **PARTIAL** — some real piece exists; the named remainder is honest and stated.
- **ABSENT** — not built in this lane at this base. No prose claiming otherwise.

## Per-package contract matrix (13 first-party `skills/vool-*/` packages)

Every package: full frontmatter contract (version, triggers, allowed-tools, capabilities,
permissions, effects, inputs, outputs, stop-conditions, recovery, cumulative-test-law), tools all
real contract intents, capabilities resolvable+available in the bootstrapped graph, permission
declarations covering what its tools require, and the no-bypass door statement in its body.
These are IMPLEMENTED + SERVED for all thirteen via `test_native_skill_library.py` and the
selection pack.

| package                | contract | auto-selection | capability gate | permission declaration | served tool run |
| ---------------------- | -------- | -------------- | --------------- | ---------------------- | --------------- |
| vool-repo-onboarding   | IMPLEMENTED | SERVED | SERVED | SERVED (read_files, list_directories) | SERVED (list_tree + read_file, real tree) |
| vool-root-cause-repair | IMPLEMENTED | SERVED | SERVED | SERVED (read_files)   | PARTIAL (components served individually; no end-to-end diagnosis workflow in pack) |
| vool-bug-reproduction  | IMPLEMENTED | SERVED | SERVED | SERVED (read_files, create_files) | PARTIAL (components served individually) |
| vool-feature-build     | IMPLEMENTED | SERVED | SERVED | SERVED (read_files, create_files) | PARTIAL (components served individually) |
| vool-cumulative-testing| IMPLEMENTED | SERVED | SERVED | SERVED (read_files)   | SERVED (workspace.run_tests executes real pytest, "2 passed") |
| vool-code-review       | IMPLEMENTED | SERVED | SERVED | SERVED (read_files)   | PARTIAL (components served individually) |
| vool-security-audit    | IMPLEMENTED | SERVED | SERVED | SERVED (read_files)   | PARTIAL (components served individually) |
| vool-git-worktrees     | IMPLEMENTED | SERVED | SERVED | SERVED (read_files, create_files) | SERVED (git_status + sandbox read-only run + approval refusal, real repo) |
| vool-ci-repair         | IMPLEMENTED | SERVED | SERVED | SERVED (read_files, create_files) | PARTIAL (components served individually) |
| vool-browser-qa        | IMPLEMENTED | SERVED | SERVED | SERVED (read_files)   | PARTIAL (web lane served at capability level; no live fetch in pack — no network in tests) |
| vool-performance       | IMPLEMENTED | SERVED | SERVED | SERVED (read_files, create_files) | PARTIAL (components served individually) |
| vool-migration         | IMPLEMENTED | SERVED | SERVED | SERVED (read_files, create_files) | PARTIAL (components served individually) |
| vool-release-gate      | IMPLEMENTED | SERVED | SERVED | SERVED (read_files, create_files) | PARTIAL (components served individually) |

"Components served individually" = the package's declared tools are the same real intents proven
executing in the served workflows (workspace read/validate/git/sandbox families), and selection +
gating for the package is served; the pack does not re-drive a full bespoke workflow per package.

## Proof-level matrix (the P1 required proofs)

| proof | verdict | evidence |
| ------------------------------ | -------- | -------- |
| Discoverable/versioned packages | SERVED | 13 packages parse with versions via the ONE loader seam (`plugin_skills.native_skills`); `skill.list` reports a `native_skills` section |
| Automatic selection             | SERVED | 13/13 phrasing→package cases green; keyword-stuffed decoy loses to a precise trigger; no-match case selects nothing |
| Explicit invocation             | SERVED | `skill.validate` + `skill.list` through `execute_runtime_tool` reach native packages by bare name (`_skill_locations` gained the native root) |
| Refusal when capability absent  | SERVED | Unknown capability refused by name; known capability with every implementation unavailable refused; empty graph refuses everything; policy-disabled web lane refuses vool-browser-qa |
| Model switching                 | SERVED | Selection + `skill.validate` byte-identical under `VOOL_LOCAL_MODELS_ENABLED=0` vs `1` |
| Plugin/MCP parity               | SERVED (plugin) / PARTIAL (MCP) | Native twin vs installed plugin twin: equal records, one rank call, equal narrowing, install lifecycle walks; MCP-sourced SKILL packages do not exist at this base — nothing to be parity with (see ABSENT) |
| Three served workflows          | SERVED | repo-onboarding (real tree, real reads), cumulative-testing (real pytest subprocess), git-worktrees (real repo, real sandbox run + typed approval refusal) |
| No direct execution bypass      | SERVED | Machine-checked: bodies must state the `execute_runtime_tool` door; bypass vocabulary absent; the served git mutation refusal shows the door failing closed for real |
| Sabotage: selection             | SERVED | Neutralising `_absent_capabilities` flips refusal→selection (mutation detected); decoy-ranking sabotage pinned |
| Sabotage: permission guards     | SERVED | Under-declared permissions fail the REAL validator; modeless `skill.install` decision is not ALLOW |
| Cumulative test law             | SERVED | Every package declares it; `tests/native_skills/` (71 green) is the bounded cumulative pack |

## Honest ABSENT / PARTIAL rows (the remainder)

- **ABSENT — served-turn prompt injection.** At this base no production prompt assembly injects
  matched skill bodies into model turns (2026-09-02 toolchain census finding, still true here).
  This lane owns selection + the typed selection record whose `instructions` field is the
  injection fragment; wiring it into the served turn path is a separate lane (a concurrent lane's
  `core/native_skill_library.py` design targets exactly this via `tool_offer_assembly`).
- **PARTIAL — MCP parity.** Native and plugin packages are one object to the loader; there are no
  MCP-SOURCED skill packages at this base to include in the parity set.
- **PARTIAL — interactive browser QA.** `web.browser_render` has a contract but no handler at
  this base; vool-browser-qa declares `web.read` honestly and marks interactive checks UNTESTED.
- **ABSENT — machine lane capability.** `machine.read` bootstraps with zero available
  implementations here, so no package declares it (declaring it would be refused by the gate).

## Concurrency note

Mid-lane, a sibling agent committed on this shared branch: `ebf64a99` (its own RED pack at
`tests/test_native_skill_library.py`, pinning a `core.native_skill_library` design) and
`e21c7956` (a verbatim preservation of THIS lane's in-flight files). The untracked
`core/native_skill_library.py` in the worktree belongs to that sibling lane, not this one; this
lane neither reviewed nor relies on it. Reconciliation between the two designs is the sibling
lane's declared next step.
