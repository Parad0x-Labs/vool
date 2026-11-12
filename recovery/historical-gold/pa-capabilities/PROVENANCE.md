# PROVENANCE — pa-capabilities

- **Source:** branch build/pa-capabilities-20260909 @ 53383844 (Mac worktree only, never pushed); recovered 2026-11-12 from the owner's Mac export
- **Historical path:** core/pa_tasks.py (the user's own todo list: durable rows, typed transitions), core/pa_reminders.py + core/pa_reminder_runtime.py (reminders and their daemon runtime), core/email_approval.py (approval gate before a send), core/email_send_ledger.py (one send per approval), a timezone change to core/operator/calendar.py (kept as patches/core/operator/calendar.py.diff), and 8 tests
- **Maturity:** SUBSTANTIAL for the todo store and reminders (journeys, daemon end-to-end, store-isolation regression tests)
- **Historically wired:** branch only
- **Historically tested:** yes (src/tests)
- **Current VOOL equivalent:** reminders were rebuilt as core/operator/reminders.py + reminder_dispatcher.py; email send safety lives in core/email_tools.py (approval binding and reservation) + core/email_work_state.py. The user todo list (pa_tasks) has NO equivalent on the line.
- **Lost:** commit dfb6e939 from the same lane is in no archive (Mac live folders and the 2026-11-11 worktree archive were both searched).
- **Recovery recommendation:** owner call: ship a user todo list in a later release (pa_tasks is the starting point); the reminder and email pieces are superseded.
- **Runtime status:** NOT REGISTERED

Vault copy is PRESERVATION only — NOT imported or registered by the runtime (guarded by tests/test_recovery_vault_isolation.py). Identity/machine paths scrubbed.
