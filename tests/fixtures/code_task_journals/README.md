# Recorded code-task journals (migration and rollback fixtures)

These journals were WRITTEN BY OLDER RUNTIMES, not by hand. `generate_legacy_journals.py` ran
ordinary task flows through the production door (`execute_runtime_tool`) of an untouched checkout
and copied each persisted journal plus the final workspace bytes.

| Set | Runtime that wrote it | Journal schema |
|---|---|---|
| `v1_df49/` | `df49f096cce927c830f8b68f12727a5d944cf3f2` (pinned integration base) | 1 (no version field) |
| `v2_e064/` | `e06407eb1e5bcfe34b831d7c3f4def7696d7d6b2` (coding revision 2) | 2 (`diagnoses`, `verifications`) |

Scenarios: `approved_with_hash`, `approved_without_hash`, `completed`,
`stale_failure_after_corrected_mutation` (the df49 runtime refuses the recovery there, and the
journal records that refusal), `two_pending_units`, `narrow_passed` (the repair landed and its
focused check passed; the full check has not run, so the task is at `cumulative`; recorded
separately, see `MANIFEST_narrow_passed.json`), and `v1_df49/approved_unread_sibling` (an
approval over a file the task never read). Each `MANIFEST*.json` lists the exact calls, statuses
and the recorded journal SHA256.

## The one transform applied

Absolute paths of the disposable recording folders were replaced, prefix only, by
`/sanitized/revision-3`. No other byte changed. Tests that execute a recorded approval recreate the
workspace from `workspace/` and point `workspace_root` at it.

Regenerate against an untouched checkout (never against a tree carrying newer runtime code):

    python generate_legacy_journals.py --repo <checkout> --out <new dir> --label v1_df49
