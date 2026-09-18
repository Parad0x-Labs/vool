# PROVENANCE — i18n

- **Source:** branch build/vool-i18n-docs-p1-20260904 @ a284f1b9
- **Historical path:** core/i18n/* (catalog, locales, page_bundle + 18 locale catalogs) + tools/i18n/check_translations.py
- **Maturity:** SUBSTANTIAL (ICU-lite plural/select, SHA-pinned drift, HTML-escape, deterministic fallback; en.json 367 keys + 16 locales)
- **Historically wired:** served UI picker on-branch, not in 613
- **Historically tested:** yes
- **Current VOOL equivalent:** NONE (canonical has only response_language_policy = which language the model answers in)
- **Recovery recommendation:** P0/P1 recover; reconcile with vool_localization (pick ONE localization engine) before any rewire.
- **Runtime status:** NOT REGISTERED

Vault copy is PRESERVATION only — NOT imported or registered by the runtime (guarded by tests/test_recovery_vault_isolation.py). Identity/machine paths scrubbed.
