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

## Added 2026-11-12: round 019 (single semantic ownership)

- **Source:** exp/skill-system-pass1 @ a4d07699 and exp/toolbelt-ninja-pass4 @ 93ec937c (identical files in both).
- patches/core/kernel/repl.py.diff: the branch's change to the kernel REPL from origin/main 9541b617. About 86 of its lines, the FIRST version of the round 019 ownership laws (a formatting wrapper is demoted to a format order; a covering obligation coordinates and never co-authors its covered siblings), are not on the line in these words. The line carries the rewritten round 019 r2 instead: rules R1-R3 in core/kernel/repl.py, tests/test_round019_r2_semantic_ownership.py (every case of this version, A to G, and ten more, H to Q) and tests/test_round019_r2_sabotage.py. This round 019 copy is kept for history only, not as a gap.
- Added tests: tests/test_round019_sabotage.py, tests/test_round019_semantic_ownership.py (the first versions, superseded by the r2 files on the line).
