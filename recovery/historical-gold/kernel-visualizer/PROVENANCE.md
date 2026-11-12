# PROVENANCE — kernel-visualizer

- **Source:** branch experiment/kernel-laws-poc @ c811ffef ("rescue: commit uncommitted 3D kernel visualizer + drive harnesses (sole copy)"), Mac only, never pushed; recovered 2026-11-12
- **Historical path:** vool_visualizer/* (journal observer, graph model, local HTTP server, 2D and 3D web views), core/kernel/vis_events.py, tests/test_vollow_visualizer.py, and four drive scripts (drive_fresh_set, drive_mixed_regression, redrive_confirm, redrive_full) that replay the 40-question mixed regression set through the kernel REPL
- **Maturity:** lab tool
- **Historically wired:** lab only (reads REPL session journals)
- **Historically tested:** one test, which reads a local journal that is not part of this copy
- **Current VOOL equivalent:** NONE
- **Not kept:** static/vendor/three.module.min.js and static/vendor/OrbitControls.js (three.js r160, MIT; see src/vool_visualizer/static/vendor-README.txt). Absolute journal and output paths were rewritten to relative review-bus/... paths.
- **Recovery recommendation:** VAULT-ONLY; a developer view of kernel turns if the kernel lab returns.
- **Runtime status:** NOT REGISTERED

Vault copy is PRESERVATION only — NOT imported or registered by the runtime (guarded by tests/test_recovery_vault_isolation.py). Identity/machine paths scrubbed.
