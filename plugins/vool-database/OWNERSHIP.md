# vool-database — ownership record (written before any edit, 2026-09-03)

Lane: `build/db-skill-20260903` (worktree `/Users/example-user/vool/worktrees/db-skill-20260903`),
base EXACT `3802a3f0`. Goal: PB06 + PB04 composition — real database skill through the EXISTING
plugin authority. Native-skills tip `8860963a` read as immutable compatibility reference (its
integration is owned by CP2); its skill frontmatter shape is mirrored, its loader untouched.

## Package location decision (inspected before choosing)

The existing discovery rules (`core/plugin_catalog.plugins_root`, `core/plugin_tools.discover_manifests`)
scan exactly `<VOOL_PLUGINS_DIR>/plugins/<plugin_id>/.codex-plugin/plugin.json`. The repository
ships in-tree skills at `skills/<name>/SKILL.md` (media-studio, vool-hive-mind) and has no
in-repo `plugins/` root yet. The repository-native location the existing rules actually support is
therefore:

- plugin package: `plugins/vool-database/` in this repo — a pack that any isolated run discovers by
  setting `VOOL_PLUGINS_DIR=<repo>` (or by copying the pack into an isolated plugins root, which is
  what the isolated install flow and the served proof do);
- native skill: `skills/vool-database/SKILL.md` — the repo's in-tree skill convention;
- the pack carries its own copy at `plugins/vool-database/skills/vool-database/SKILL.md` so an
  installed pack is self-contained.

## Owned by this lane

- `plugins/vool-database/**` — manifest, handler, pack skill copy, ownership record.
- `skills/vool-database/SKILL.md` — the in-repo native DB skill.
- `tests/test_vool_database_plugin.py`, `tests/_vool_database_pack.py` (test-only pack installer).
- Evidence: `docs/DB_SKILL_P1_EVIDENCE_20260903.md`, ledger entry appended to
  `VOOL-DELIVERY/RUNTIME_UPGRADE_LEDGER.md`.

## Repaired at the owning seam (base defects, live-proven before editing)

Both repairs are inside the plugin lane files this goal integrates through; both were verified RED
at base `3802a3f0` before the edit (see evidence doc for the live probes):

1. `core/plugin_tools.py::contract_from_tool` — never read a manifest `mutation` block, so no
   plugin tool with a local-mutation side-effect class could register (tool_registry refuses
   `mutation=None`); the shipped `tests/test_p0_plugin_dispatch_boundaries.py` fixture errors on
   exactly this at base. Repair: pass `spec["mutation"]` through to `RuntimeToolContract.mutation`
   (registry validates contents fail-closed; absent block stays `None` and is still refused).
2. `core/plugin_executor.py::_kernel_confinement_prefix` — returned `None` for empty writable
   roots, so the 2026-09-02 read-only amendment (read_only child gets NO writable root) made every
   read-only plugin tool answer `confinement_unavailable` (live-probed at base). Repair: build the
   deny-all-writes kernel profile when the caller deliberately passes no writable roots; the
   Seatbelt/bwrap profile builders already express zero write roots.
3. `tests/_toolchain_fixtures.py::default_tools` — `pack.touch` gained the `mutation` declaration
   the CP1 registration gate requires (base drift between the shipped fixture and the gate).

## Explicitly not touched

`core/native_skill_library.py` (absent at this base; lives on 8860963a), `core/plugin_skills.py`,
`core/skill_tools.py`, `core/tool_registry.py` (registry), central runtime/service/migrations,
code-assistant and user plugin directories. No second skill creator is built: PB04 reuses
`skill.create`/`validate`/`install` as-is.

## Safety laws this lane keeps

- All disposable databases live under the plugin scratch root (`VOOL_PLUGIN_SCRATCH_ROOT`, or
  `$TMPDIR/vool-plugin-scratch`), one isolated root per run; never an operator path, never HOME.
- Read-only tools open SQLite with `mode=ro` at the engine level; SELECT-shaped writes fail inside
  SQLite, not only in shape classification. Row/time limits enforced in-child.
- Mutating tools declare Blackbox `mutation` coverage, require explicit approval, back up before
  mutating, and state engine rollback truth (SQLite DDL is transactional with named exceptions;
  a file snapshot restores the LOCAL file only, never a remote or attached database).
- No credentials are collected; SQLite needs none; PostgreSQL is refused as `engine_unavailable`
  until a safe local sandbox exists (verified: no usable local PostgreSQL on this host at run time).
- Handler is stdlib-only Python compatible with 3.9+ (the confined child PATH resolves
  `/usr/bin/python3`); the pack is discovered/registered/executed only through the existing
  plugin catalog, registry, permission gate, executor, receipt and Blackbox lanes.
