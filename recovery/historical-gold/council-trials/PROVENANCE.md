# PROVENANCE — council-trials

- **Source:** branch experiment/council-mode @ 8a852ff6 (separate experiment clone, never pushed); recovered 2026-11-12
- **Historical path:** scripts/council_demo.py, council_live_trial.py, council_live_trial2.py, council_trial3.py, council_trial4.py, trial3_score.py, trial3_tasks.py, trial4_score.py, trial4_truth.py
- **Maturity:** experiment harness (four live trials of real hosted models seated through the council runtime, with scoring)
- **Historically wired:** scripts only; they need an OpenRouter key from the environment and spend credits
- **Historically tested:** n/a (they are the trials)
- **Current VOOL equivalent:** the council runtime itself is on the line (core/council/*, 89-100% of the branch lines), and the claim gauntlet, sealed judge and provider degradation tests from this branch were ported with it. The trial harnesses are not.
- **Not kept here:** the trial result JSON files (raw model output); they stay in the owner's Mac export archive (L18-council-mode).
- **Recovery recommendation:** VAULT-ONLY; rerun only with the owner's ok, since a run spends API credits.
- **Runtime status:** NOT REGISTERED

Vault copy is PRESERVATION only — NOT imported or registered by the runtime (guarded by tests/test_recovery_vault_isolation.py). Identity/machine paths scrubbed.
