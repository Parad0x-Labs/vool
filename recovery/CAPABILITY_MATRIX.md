# VOOL Capability Matrix — 2026-09-08

Two parts: (1) the LIVE runtime tool census (generated from `core/capability_census.py`, the
authoritative instrument — `recovery/TOOL_CENSUS.json`), and (2) the historical-gold rollup
(`recovery/HISTORICAL_GOLD.json`). Canonical running runtime = `613a8f53`; canonical worktree
HEAD = `30bf73dc`. Nothing in the gold vault is wired into the runtime.

## Part 1 — Live runtime capability census (what ships today)

- **Declared contracts:** 138  ·  **registered:** 138  ·  **model-selectable/executable:** 104  ·  **not-model-facing (policy-gated):** 34
- **Permission split (of 104 executable):** allow 52  ·  require-approval 52

| Surface | Contracts | Executable | Require-approval | Policy-disabled |
|---|--:|--:|--:|--:|
| web | 25 | 3 | 0 | 22 |
| repo_ops | 18 | 18 | 11 | 0 |
| workspace | 17 | 17 | 8 | 0 |
| machine | 14 | 14 | 3 | 0 |
| code_task | 8 | 8 | 2 | 0 |
| media | 8 | 6 | 5 | 2 |
| web0 | 8 | 8 | 8 | 0 |
| operator | 7 | 7 | 3 | 0 |
| wallet | 7 | 1 | 1 | 6 |
| skill | 6 | 6 | 3 | 0 |
| email | 3 | 0 | 0 | 3 |
| marketplace | 3 | 3 | 2 | 0 |
| profile | 3 | 3 | 2 | 0 |
| pdf | 2 | 2 | 0 | 0 |
| set | 2 | 2 | 1 | 0 |
| capability | 1 | 1 | 0 | 0 |
| code | 1 | 1 | 0 | 0 |
| demo | 1 | 1 | 0 | 0 |
| orchestration | 1 | 1 | 1 | 0 |
| runtime | 1 | 1 | 1 | 0 |
| sandbox | 1 | 1 | 1 | 0 |
| x | 1 | 0 | 0 | 1 |
| **TOTAL** | **138** | **104** | **52** | **34** |

`executed`/`receipted` are session-scoped and 0 in this static census (no live turns) — expected.

## Part 2 — Historical gold rollup (recovered to vault, NOT runtime)

| Family | Maturity | Current VOOL equivalent | Runtime status | Recommendation |
|---|---|---|---|---|
| **kernel-laws-enforcement** | SUBSTANTIAL + TESTED (dedicated test_kernel_* + compiled voo | PARTIAL — canonical adopted only the kernel-laws FOUNDATION (evidence_ | DISABLED / NOT REGISTERED | P0 recover + STRONGLY consider rewiring; privacy compartments + leak_scan first  |
| **toolbelt** | SUBSTANTIAL (typed per-tool presence/scope/health/auth state | WEAKER — ad-hoc shutil.which scattered across ~15 modules + machine_di | NOT REGISTERED | P0/P1 recover + consider rewiring; the CredentialHandle secrets-as-handles desig |
| **i18n** | SUBSTANTIAL (ICU-lite plural/select, SHA-pinned drift, HTML- | NONE (canonical has only response_language_policy = which language the | NOT REGISTERED | P0/P1 recover; reconcile with vool_localization (pick ONE localization engine) b |
| **localization-vool_localization** | SUBSTANTIAL (6 platform adapters, plurals, bidi, locale regi | NONE (distinct from core/i18n git gold) | NOT REGISTERED | P0/P1 recover; DEDUPE with i18n — choose the stronger engine, vault the other. T |
| **skill-identity-invocation** | PARTIAL (collision + identity-conflict law) | WEAKER — only a plain content sha256 survives in skill_tools.py; the i | NOT REGISTERED | P0/P1 recover the collision/identity-conflict protection; skill system otherwise |
| **presentation-render** | EXPERIMENT | SUPERSEDED for selection by core/presentation_selection.py (C19, stron | NOT REGISTERED | P1/P2 recover render capabilities ONLY if they improve answer output WITHOUT cha |
| **companion-lifeform** | WIP (gamification/progression layer) | WEAKER fragment pet survives (companion_*_fragment.py); the progressio | NOT REGISTERED | VAULT-ONLY for now (net-new product surface); do not baseline. Later product bra |
| **social-content** | SUBSTANTIAL (multi-platform) | PARTIAL — X-narrow x_editorial/x_platform_policy merged; general multi | NOT REGISTERED | VAULT-ONLY for now; harvest any reusable capability the current tree lost (P1/P2 |
| **school-edition** | WIP (same-day; a complete classroom product surface) | NONE | NOT REGISTERED | VAULT-ONLY for now (net-new product; still live on its branch). Later product br |
| **channels-inbound-KAS** | SUBSTANTIAL (46-commit real-time websocket Discord Gateway + | WEAKER — canonical has outbound-only relay/bridge_workers/discord_brid | NOT REGISTERED | PRESERVE + ANALYZE ONLY. **KAS owns external channels/integrations** — do NOT wi |

### Standalone repos (preserved, evaluated separately)

| Repo | Maturity | Recommendation |
|---|---|---|
| **nullcap** | EXPERIMENTAL + 104-green sabotage suite; needs `cryptography` | Evaluate whether AEAD/HPKE/Merkle/wallet-binding STRENGTHEN the existing receipt/proof/pri |
| **jury-control-plane** | SUBSTANTIAL, stdlib-only; server-side gates, reveal firewall, hash-cha | Compare server-side gates / reveal firewall / tamper-evident tape / minority-finding visib |

## Part 3 — The one capability that is a BUILD, not a recovery

The model-first interpreter (`SemanticResolver`) is the highest-value gap and was **never**
implemented — scaffolded three times and left inert (`core/semantic/types.py`,
`answer_coverage.py` `origin="heuristic"` only, `routing_authority_v2` pinned NON_EXECUTABLE_SHADOW).
It is not in this vault because there is no historical implementation to recover. Phase C.5 builds it.

