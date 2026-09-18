# VOOL Historical Provenance — Recovered Gold (2026-09-08)

Human rollup of every recovered family and standalone repo. Machine-readable twin:
`recovery/HISTORICAL_GOLD.json`. Per-family detail: `recovery/historical-gold/<family>/PROVENANCE.md`.

**Containment:** everything below is PRESERVATION. None of it is imported or registered by the
runtime; `tests/test_recovery_vault_isolation.py` fails closed if that is ever weakened. Recovery
into the live runtime is a separate, evidence-gated decision (Phase C/D).

**Preservation snapshots (outside this tree, whole-tree copies):**
`~/Desktop/innovation/VOOL-ARCHAEOLOGY-20260908/preservation-snapshots/` (kernel-laws ×3 + discord
ingress, ~1,417 files) and `.../A3/gold-repo-snapshots/` (nullcap 8.6M, jury-control-plane 480K).

## Recovered families (in-tree vault: `recovery/historical-gold/`)

### kernel-laws-enforcement
- **Source:** dangling commits 32c7740f (+ e288f48a, 819cd464) in OX-VOOL/code; also on-disk at-risk-recovery-20260829/vool-engine-kernel
- **Historical path:** core/kernel/* (wasm_sandbox, broker, compartments, counterfactual, plugin_contract, plugin_lifecycle, envelope, flow, boundary, continuation, stability, composition, council_flow)
- **Maturity:** SUBSTANTIAL + TESTED (dedicated test_kernel_* + compiled vool_plugin.wasm fixture)
- **Historically wired:** experimental lab only (never on the served path)
- **Historically tested:** yes
- **Current VOOL equivalent:** PARTIAL — canonical adopted only the kernel-laws FOUNDATION (evidence_types/capabilities/effects/obligations); NONE of the enforcement modules
- **Runtime status:** DISABLED / NOT REGISTERED
- **Recovery recommendation:** P0 recover + STRONGLY consider rewiring; privacy compartments + leak_scan first (private-vs-capable mission). Sole surviving copy is this vault + the git-dangling commits.

### toolbelt
- **Source:** tag preserve/exp-toolbelt-ninja-pass3-20260829 (+ pass1/2 tags, origin/exp/toolbelt-ninja-pass4)
- **Historical path:** core/toolbelt/* (models, operations, resolve, evidence, identities, exe_identity, ssh_signing, probes, inventory, credentials, runtime_manifest, version_spec)
- **Maturity:** SUBSTANTIAL (typed per-tool presence/scope/health/auth state machine; install plans; secrets-as-handles CredentialHandle invariant)
- **Historically wired:** experiment lane
- **Historically tested:** partial
- **Current VOOL equivalent:** WEAKER — ad-hoc shutil.which scattered across ~15 modules + machine_diagnostics/install_recommendations (GPU/deps focus), NOT the unified typed layer
- **Runtime status:** NOT REGISTERED
- **Recovery recommendation:** P0/P1 recover + consider rewiring; the CredentialHandle secrets-as-handles design is high value.

### i18n
- **Source:** branch build/vool-i18n-docs-p1-20260904 @ a284f1b9
- **Historical path:** core/i18n/* (catalog, locales, page_bundle + 18 locale catalogs) + tools/i18n/check_translations.py
- **Maturity:** SUBSTANTIAL (ICU-lite plural/select, SHA-pinned drift, HTML-escape, deterministic fallback; en.json 367 keys + 16 locales)
- **Historically wired:** served UI picker on-branch, not in 613
- **Historically tested:** yes
- **Current VOOL equivalent:** NONE (canonical has only response_language_policy = which language the model answers in)
- **Runtime status:** NOT REGISTERED
- **Recovery recommendation:** P0/P1 recover; reconcile with vool_localization (pick ONE localization engine) before any rewire.

### localization-vool_localization
- **Source:** on-disk backup ~/vool/archive/at-risk-recovery-20260829/swarm-localization_localization (DISK-ONLY, absent from git)
- **Historical path:** vool_localization/* (catalog, message, plural, bidi, formatting + adapters: gettext/voice/android/windows/web_json/apple + registries + tests)
- **Maturity:** SUBSTANTIAL (6 platform adapters, plurals, bidi, locale registries, tests)
- **Historically wired:** no
- **Historically tested:** yes
- **Current VOOL equivalent:** NONE (distinct from core/i18n git gold)
- **Runtime status:** NOT REGISTERED
- **Recovery recommendation:** P0/P1 recover; DEDUPE with i18n — choose the stronger engine, vault the other. This was the sole (disk) copy; now vaulted.

### skill-identity-invocation
- **Source:** branch origin/exp/skill-system-pass1 @ 6c194fc6
- **Historical path:** core/skill_identity.py (two-digest PACKAGE vs EFFECTIVE authenticity, parse_skill_md, strip_hidden_blocks) + core/skill_invocation.py (APPLIED/PARTIAL/BLOCKED invocation truth)
- **Maturity:** PARTIAL (collision + identity-conflict law)
- **Historically wired:** experiment
- **Historically tested:** partial
- **Current VOOL equivalent:** WEAKER — only a plain content sha256 survives in skill_tools.py; the identity/collision law dropped
- **Runtime status:** NOT REGISTERED
- **Recovery recommendation:** P0/P1 recover the collision/identity-conflict protection; skill system otherwise stronger in canonical.

### presentation-render
- **Source:** branch/commits b92ba5b7 (+ 33737782)
- **Historical path:** core/presentation/* (RenderDocument model, render_markdown, render_plain, charts, links, intent, voice, companion_render, status)
- **Maturity:** EXPERIMENT
- **Historically wired:** experiment
- **Historically tested:** partial
- **Current VOOL equivalent:** SUPERSEDED for selection by core/presentation_selection.py (C19, stronger); the structured render-document + renderers are unique
- **Runtime status:** NOT REGISTERED
- **Recovery recommendation:** P1/P2 recover render capabilities ONLY if they improve answer output WITHOUT changing current UX; otherwise vault.

### companion-lifeform
- **Source:** branch feature/voolemon-foundation-20260906 @ 73b239aa
- **Historical path:** core/companion/lifeform/* (schema, genesis, progression, appearance, competition, signals) + command_registry/groups/lifeform_group.py
- **Maturity:** WIP (gamification/progression layer)
- **Historically wired:** branch only
- **Historically tested:** partial
- **Current VOOL equivalent:** WEAKER fragment pet survives (companion_*_fragment.py); the progression layer is absent
- **Runtime status:** NOT REGISTERED
- **Recovery recommendation:** VAULT-ONLY for now (net-new product surface); do not baseline. Later product branch.

### social-content
- **Source:** branch build/social-content-manager-p1-20260904 @ bfa92578
- **Historical path:** core/social_content/* (research, x_session_research, adaptation, observations, review, publishing, platform_policy) + storage/social_content_store.py + command group
- **Maturity:** SUBSTANTIAL (multi-platform)
- **Historically wired:** branch only; the X-specific variant DID merge (x_editorial.py)
- **Historically tested:** partial
- **Current VOOL equivalent:** PARTIAL — X-narrow x_editorial/x_platform_policy merged; general multi-platform pipeline absent
- **Runtime status:** NOT REGISTERED
- **Recovery recommendation:** VAULT-ONLY for now; harvest any reusable capability the current tree lost (P1/P2), do not baseline the product.

### school-edition
- **Source:** branch feat/vool-school-20260908 @ 419805a0 (same-day WIP)
- **Historical path:** core/school/* (store, service, api, pages, policy, quota, submission, assistance, ingress, session) + core/product_edition.py
- **Maturity:** WIP (same-day; a complete classroom product surface)
- **Historically wired:** branch only
- **Historically tested:** partial
- **Current VOOL equivalent:** NONE
- **Runtime status:** NOT REGISTERED
- **Recovery recommendation:** VAULT-ONLY for now (net-new product; still live on its branch). Later product branch decision.

### channels-inbound-KAS
- **Source:** dangling KAS-049 lane 175d378d (pre-rebase b708fd38) in OX-VOOL/code
- **Historical path:** core/external_ingress.py, connector_awareness*.py, discord_recent_*.py + relay/bridge_workers/* (discord_gateway_ingress/state, discord_command_parser, telegram bridges, webhook_ingress)
- **Maturity:** SUBSTANTIAL (46-commit real-time websocket Discord Gateway + governed connectors; canonical is polling-only)
- **Historically wired:** lane only, never merged
- **Historically tested:** yes (~5k lines of tests on-lane)
- **Current VOOL equivalent:** WEAKER — canonical has outbound-only relay/bridge_workers/discord_bridge.py
- **Owner:** KAS lane (external channels)
- **Runtime status:** NOT REGISTERED
- **Recovery recommendation:** PRESERVE + ANALYZE ONLY. **KAS owns external channels/integrations** — do NOT wire into VOOL if it violates the VOOL/KAS boundary. Hand to the KAS lane.

## Standalone repositories (preserved, not vaulted into this tree — whole own-git repos)

### nullcap
- **Source:** standalone repo ~/vool/workspaces/proof-capsule-lab-20260825 (own git; snapshot A3/gold-repo-snapshots/proof-capsule-lab.tgz)
- **Maturity:** EXPERIMENTAL + 104-green sabotage suite; needs `cryptography`
- **Current VOOL equivalent:** canonical has receipts/anchoring but no nullcap AEAD/HPKE/Merkle/wallet-binding
- **Runtime status:** STANDALONE REPO (not in runtime)
- **Recommendation:** Evaluate whether AEAD/HPKE/Merkle/wallet-binding STRENGTHEN the existing receipt/proof/privacy architecture vs create DUPLICATE crypto. Preserve the standalone repo regardless.

### jury-control-plane
- **Source:** standalone repo ~/vool/jury-control-plane (own git, port 8787; snapshot A3/gold-repo-snapshots/jury-control-plane.tgz)
- **Maturity:** SUBSTANTIAL, stdlib-only; server-side gates, reveal firewall, hash-chained tape, minority-finding visibility, PARTIAL/FULL modes
- **Current VOOL equivalent:** canonical has in-runtime core/council/* but NOT this as a service
- **Runtime status:** STANDALONE REPO (not in runtime)
- **Recommendation:** Compare server-side gates / reveal firewall / tamper-evident tape / minority-finding visibility vs current Council; integrate concepts only if stronger + compatible. Preserve the standalone repo regardless.

