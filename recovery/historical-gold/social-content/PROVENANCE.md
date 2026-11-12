# PROVENANCE — social-content

- **Source:** branch build/social-content-manager-p1-20260904 @ bfa92578
- **Historical path:** core/social_content/* (research, x_session_research, adaptation, observations, review, publishing, platform_policy) + storage/social_content_store.py + command group
- **Maturity:** SUBSTANTIAL (multi-platform)
- **Historically wired:** branch only; the X-specific variant DID merge (x_editorial.py)
- **Historically tested:** partial
- **Current VOOL equivalent:** PARTIAL — X-narrow x_editorial/x_platform_policy merged; general multi-platform pipeline absent
- **Recovery recommendation:** VAULT-ONLY for now; harvest any reusable capability the current tree lost (P1/P2), do not baseline the product.
- **Runtime status:** NOT REGISTERED

Vault copy is PRESERVATION only — NOT imported or registered by the runtime (guarded by tests/test_recovery_vault_isolation.py). Identity/machine paths scrubbed.

## Added 2026-11-12 (same source, bfa92578)

- core/command_registry/groups/social_content_group.py (the command group), docs/SOCIAL_CONTENT_MANAGER_CONTRACT_20260904.md, and the native skill, stored as skills/vool-social-content-manager/SKILL.md.txt so no skill scanner can load it
- 11 tests: adaptation, commands, observations, publishing, research, review, sabotage, served, store, x_session, and native_skills/test_social_content_manager_selection.py
- The vaulted core/social_content/* and storage/social_content_store.py were checked against the branch tip: byte-identical.
