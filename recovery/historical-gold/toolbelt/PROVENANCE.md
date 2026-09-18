# PROVENANCE — toolbelt

- **Source:** tag preserve/exp-toolbelt-ninja-pass3-20260829 (+ pass1/2 tags, origin/exp/toolbelt-ninja-pass4)
- **Historical path:** core/toolbelt/* (models, operations, resolve, evidence, identities, exe_identity, ssh_signing, probes, inventory, credentials, runtime_manifest, version_spec)
- **Maturity:** SUBSTANTIAL (typed per-tool presence/scope/health/auth state machine; install plans; secrets-as-handles CredentialHandle invariant)
- **Historically wired:** experiment lane
- **Historically tested:** partial
- **Current VOOL equivalent:** WEAKER — ad-hoc shutil.which scattered across ~15 modules + machine_diagnostics/install_recommendations (GPU/deps focus), NOT the unified typed layer
- **Recovery recommendation:** P0/P1 recover + consider rewiring; the CredentialHandle secrets-as-handles design is high value.
- **Runtime status:** NOT REGISTERED

Vault copy is PRESERVATION only — NOT imported or registered by the runtime (guarded by tests/test_recovery_vault_isolation.py). Identity/machine paths scrubbed.
