# PROVENANCE — notes-store-reading-spec

- **Source:** untracked files of worktree apple-notes-object-reading-20260915 (base 1d58a641 of the calendar-notes correction-3 repo); the worktree was archived as dirty/apple-notes-object-reading-20260915.untracked.tgz on 2026-11-11 and recovered 2026-11-12
- **Historical path:** tests/test_apple_notes_object_names_the_store.py (parser and destination seam), tests/pa_beta_gate/test_served_apple_notes_object_reading.py (served turns with a synthetic Notes runner), tests/operator_notes_store_census.py (before/after census of every wording the suite uses; not a test module)
- **Maturity:** SPEC ONLY. The tests were written first and the fix was never written.
- **Historically wired:** no
- **Historically tested:** these ARE the tests; they describe failures measured at 1d58a641
- **Current VOOL equivalent:** the gap is still open on the line: core/operator/parser.py reads an Apple note only after read/show/open/display, and parse_notes_destination knows only "Apple Notes" and "Notes.app". So 'find my Apple note "Plan"' is still a workspace search, and 'create an Apple note titled ...' still saves to the workspace. names_apple_notes() does not exist yet.
- **Recovery recommendation:** P1 fix: implement the store reading these tests describe, then move them into tests/.
- **Runtime status:** NOT REGISTERED (tests only)

Vault copy is PRESERVATION only — NOT imported or registered by the runtime (guarded by tests/test_recovery_vault_isolation.py). Identity/machine paths scrubbed.

## Ported 2026-11-12

- The store reading these tests describe is implemented: `core.operator.apple_notes.names_apple_notes` (the store words, outside quotes, data values and paths, "apple" with one typo) and `note_read_object` (what a read verb takes), used by `core.operator.parser._apple_notes_read` and by `parse_notes_destination`.
- Both test files now live in tests/ (tests/test_apple_notes_object_names_the_store.py, tests/pa_beta_gate/test_served_apple_notes_object_reading.py). The census helper stays here only.
- The served run also needed two front-door stand-downs: the machine read lane and the web-off lookup refusal leave notes reads to the Notes lane.
