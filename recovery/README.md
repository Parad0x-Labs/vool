# recovery/ — Historical Gold Vault (PRESERVATION, NOT RUNTIME)

This tree holds sanitized, provenance-rich copies of genuinely-lost historical VOOL capabilities
recovered in the 2026-09-08 archaeology. **It is not runtime.** Vaulted code is never imported or
registered — enforced by `tests/test_recovery_vault_isolation.py` (no runtime dir imports `recovery`;
the vault is not a Python package; no scanner root points into it). It intentionally contains whole
re-homed trees (`historical-gold/*/src/core/...`) that WOULD shadow canonical modules if ever placed
on `sys.path` — hence the hard containment.

Each `historical-gold/<family>/` has `src/` (preserved source, identity-scrubbed) and `PROVENANCE.md`.
The machine-readable manifest is `HISTORICAL_GOLD.json`. Integration into the live runtime is a
separate, gated decision (Phase C/D) — recovery here means the SOURCE is safe, not that a feature is on.
