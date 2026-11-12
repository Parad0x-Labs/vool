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

## Updated 2026-11-12 to the branch tip

- **Tip:** exp/skill-system-pass1 @ a4d07699. core/skill_identity.py was updated to the tip (the change from 6c194fc6 is patches/6c194fc6-to-a4d07699.skill_identity.diff); core/skill_invocation.py is byte-identical.
- Kept as patches (they edit files the line has rewritten): patches/core/plugin_catalog.py.diff, patches/core/plugin_skills.py.diff.
- Added: experimental/tests/test_skill_system.py, tests/test_skill_convergence.py, tests/test_skill_legacy_trust.py, scripts/skill_convergence_demo.py, and six skill fixtures (three hostile, three ordinary), stored as SKILL.md.txt so no skill scanner can load them.
