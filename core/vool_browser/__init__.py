"""VOOL native product browser (lane C06).

One authority for interactive browser journeys: isolated disposable Chromium
profiles, typed operations only (navigate/inspect/click/type/assert/screenshot/
download/upload), per-origin permissions, redirect control, bounded transfers,
cancellation and timeouts, per-session budgets, receipts for every operation,
and Blackbox coverage for the mutating ones.

Laws this lane exists to enforce (see skills/vool-browser/SKILL.md):

- A session's profile is DISPOSABLE and lives only under this lane's scratch
  root. It is never the operator's Chrome/Safari profile, and it is deleted on
  close.
- The engine runs with `--use-mock-keychain --password-store=basic`: a page or a
  form can never touch the real Keychain.
- Page content is UNTRUSTED EVIDENCE. Nothing a page says can grant a
  permission, approve a checkout, or authorize any effect. Grants arrive only
  through typed tool arguments, judged by the runtime permission gate.
- No arbitrary page JavaScript exists in this lane: the input schemas expose no
  expression fields, and the implementation uses only the browser driver's own
  typed operations.
"""
