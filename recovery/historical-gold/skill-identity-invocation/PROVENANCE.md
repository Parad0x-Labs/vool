# PROVENANCE — skill-identity-invocation

- **Source:** branch origin/exp/skill-system-pass1 @ 6c194fc6
- **Historical path:** core/skill_identity.py (two-digest PACKAGE vs EFFECTIVE authenticity, parse_skill_md, strip_hidden_blocks) + core/skill_invocation.py (APPLIED/PARTIAL/BLOCKED invocation truth)
- **Maturity:** PARTIAL (collision + identity-conflict law)
- **Historically wired:** experiment
- **Historically tested:** partial
- **Current VOOL equivalent:** WEAKER — only a plain content sha256 survives in skill_tools.py; the identity/collision law dropped
- **Recovery recommendation:** P0/P1 recover the collision/identity-conflict protection; skill system otherwise stronger in canonical.
- **Runtime status:** NOT REGISTERED

Vault copy is PRESERVATION only — NOT imported or registered by the runtime (guarded by tests/test_recovery_vault_isolation.py). Identity/machine paths scrubbed.
