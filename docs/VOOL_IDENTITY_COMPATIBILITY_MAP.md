# VOOL identity — compatibility map

The product's user-facing identity is **VOOL**. The internal identifier `vool` is **frozen** in the
places listed below. This map records what was renamed, what must stay, and why, so nobody
"finishes the rename" by breaking an installed runtime.

Rule of thumb: **rename what a user or a model reads. Freeze what a machine keys on.**

## Renamed — user-facing identity (done)

| Where | Was | Now | Why safe |
|---|---|---|---|
| `core/onboarding.py` `get_agent_display_name()` fallback | `VOOL` | `VOOL` | Display only. Nothing keys on it. |
| `core/onboarding.py` `ensure_bootstrap_identity(default_agent_name=)` | `VOOL` | `VOOL` | New installs only; stored values untouched. |
| `core/prompt_normalizer.py` system prompt | `You are VOOL running on…` | `You are VOOL…` | The model was literally told it was VOOL — this was the root cause of "I am Vool" replies. |
| `core/prompt_normalizer.py` persona fallback | `VOOL` | `VOOL` | Display only. |
| `core/runtime_tool_contracts.py` `workspace.rollback_last_change` description | `Rollback the last VOOL-tracked…` | `…VOOL-tracked…` | Tool *description*, read by the model, not an identifier. |
| `core/execution/capabilities.py` `operator.list_tools` description | `…available in OpenClaw right now` | `…available in VOOL right now` | Same. Also removed a second product's name from our own prompt. |
| `core/execution/capabilities.py` `sell.quote` description | `Quote VOOL's own compute` | `Quote VOOL's own compute` | Same. |

Measured effect: the tool catalogue injected into every tool-intent prompt went from
**1× OpenClaw + 3× VOOL** to **0 + 0**, with `get_agent_display_name()` now returning `VOOL`.

## Legacy stored values — mapped at read, never rewritten

Installs bootstrapped before the rename persisted `agent_name: "VOOL"` in the identity store.
`get_agent_display_name()` maps that **legacy default** to `VOOL` at read time.

- The stored value is **never rewritten** — no migration, no write path, fully reversible.
- A name the user deliberately chose (anything other than the legacy default) is returned as-is.
- Only the exact legacy default is mapped, case-insensitively.

## Frozen — do NOT rename

Each of these is keyed on by something that outlives a code change: an installed bundle, a stored
row, a filesystem path, a peer registry, or an existing user's configuration.

| Identifier | Kind | Why frozen |
|---|---|---|
| `ai.nulla.desktop` | macOS bundle identifier | Changing it makes the OS treat it as a different app: new prefs, new TCC grants, LaunchServices confusion for installed users. |
| `CFBundleExecutable: VOOL`, `Contents/MacOS/VOOL`, `~/Applications/VOOL.app` | Filesystem paths | Installed layout. The launcher and the daemon plist reference these. |
| `VOOL_*` environment variables | Runtime config | **2026-09-19:** `VOOL_*` is canonical; the legacy `NULLA_*` spelling is read as a lower-precedence fallback (`core/env_compat.py`), so existing shells and launch agents keep working. |
| `vool-local-product` | Repository name | Remotes, CI, clone paths, both lanes' tooling. |
| `vool_memory.db`, `~/.vool_runtime/`, `.vool_local/` | Data paths | Canonical names reuse pre-rename data: a home holding only `nulla_memory.db` / `~/.nulla_runtime` / `.nulla_local` is opened as-is, never duplicated (`storage/db.py`, `core/runtime_paths.py`). |
| `agent_name_registry` / `voolbook_identity` claimed names | Peer-network identity | A *claimed* name in a distributed registry. Renaming breaks identity continuity with peers. |
| `core/vool_*.py`, `apps/vool_*.py` | Module names | **Renamed 2026-09-19** in the public VOOL tree (was `nulla_*`; previously frozen for two-lane merge cost). Internal import surface only — no persistence keys on module names. |
| `_VOOL_CHAT_HTML`, `data-model="vool"`, `vool.*` localStorage keys | Client-side keys | Existing browsers hold these keys; renaming silently drops user settings. |

## Completed by the 2026-09-19 public-tree migration

- Launcher echoes, installers and bootstrap scripts now say VOOL (a legacy `~/nulla-local`
  install directory is reused when present, never duplicated).
- Backend strings, docs and examples swept against this map.
- Canonical data names carry legacy reuse; see docs/UPGRADE_NULLA_TO_VOOL.md.

## How to extend this map

Before changing any `vool` occurrence, classify it:

1. **User-facing branding** — a human or a model reads it → rename.
2. **Safe internal name** — code-only, no persistence → rename only if it earns the merge cost.
3. **Persistent compatibility identifier** — a stored value, path, bundle id, env var, or peer
   registry key → freeze, and map at read time if it is user-visible.
4. **Database or migration dependency** → freeze; a rename requires a tested migration.
