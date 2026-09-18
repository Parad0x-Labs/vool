# PROVENANCE — kernel-laws-enforcement

- **Source:** dangling commits 32c7740f (+ e288f48a, 819cd464) in vool-checkout; also on-disk at-risk-recovery-20260829/vool-engine-kernel
- **Historical path:** core/kernel/* (wasm_sandbox, broker, compartments, counterfactual, plugin_contract, plugin_lifecycle, envelope, flow, boundary, continuation, stability, composition, council_flow)
- **Maturity:** SUBSTANTIAL + TESTED (dedicated test_kernel_* + compiled vool_plugin.wasm fixture)
- **Historically wired:** experimental lab only (never on the served path)
- **Historically tested:** yes
- **Current VOOL equivalent:** PARTIAL — canonical adopted only the kernel-laws FOUNDATION (evidence_types/capabilities/effects/obligations); NONE of the enforcement modules
- **Recovery recommendation:** P0 recover + STRONGLY consider rewiring; privacy compartments + leak_scan first (private-vs-capable mission). Sole surviving copy is this vault + the git-dangling commits.
- **Runtime status:** DISABLED / NOT REGISTERED

Vault copy is PRESERVATION only — NOT imported or registered by the runtime (guarded by tests/test_recovery_vault_isolation.py). Identity/machine paths scrubbed.
