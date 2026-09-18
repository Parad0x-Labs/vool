# Safe Bug Reporter — privacy-safe reproducible bug reporting (2026-09-01)

**Branch:** `build/safe-bug-reporter-20260901` · **Base:** `b3f5117f479d0f3d1770099a38334a4d2a23861a`
**Commits:** implementation `b28719eb`, sabotage-pinned tests `e5a46c3b`, this doc (see `git log`).
**Author:** sls_0x. **Local only** — nothing pushed, merged, or submitted; **no real GitHub issue
exists anywhere** (GitHub is fully mocked in every test).

---

## 1. What this is

An **opt-in** bug-reporting pipeline whose first artifact is always a **local sanitized draft**,
and which can never silently publish raw logs or conversations. The ordered stages, each a module
in `core/bug_report/`:

| Stage | Module | Guarantee |
|---|---|---|
| 1. Capture bounded diagnostics | `capture.py` | hard caps (200 lines/source, 64 KiB total logs, ≤8 sources, 4 000-char prose, 12 repro steps); conversation-kind sources **refused at the door**; component ids must match `^[a-z0-9][a-z0-9_.-]{0,63}$` |
| 2. Deterministic local redaction | `redaction.py` | model-free, idempotent regex families: vendor secrets (with `core.secret_redaction` as a counted backstop + a longer-run `AKIA` rule), cookies, auth headers, home paths/usernames, emails, IPv4/IPv6, labelled secrets (incl. "password is X" prose form), high-entropy runs, hex blobs |
| 3. Allowlist reconstruction | `allowlist.py` | typed structure survives: tracebacks become `file basename/line/function` frames; log lines only keep structured shapes (timestamp/level prefixes — **no bare `component: message` shape, it is the shape of a chat turn**); prose is normalized + redacted + capped |
| 4. Secret scan | `scanner.py` | the fail-closed outbound gate over the **exact bytes**: all redaction families plus machine-local dynamic checks (current home dir, `$USER`, hostname); two spans exempt by construction (the fingerprint marker line and JSON digest fields — content-free hashes of sanitized material) |
| 5. Reproduction synthesis | `synthesis.py` | local, model-free, deterministic markdown. Cloud restructuring is opt-in (`VOOL_BUG_REPORT_CLOUD_SYNTHESIS`, default **OFF**), accepts only `SanitizedMaterial` (a type whose constructor re-runs the scanner — dirty input fails closed), may only carry sections the local rendering built, and is re-scanned; anything unsafe falls back to local formatting. **The LLM never owns sanitization.** |
| 6. Exact preview | `preview.py` | `payload_sha256` is the sha256 of the exact issue JSON the adapter will send; field/attachment removal rebuilds the payload (new hash ⇒ prior approval invalid); outbound hard-capped at 256 KiB |
| 7. Explicit approval | `service.py` | consent manifest binds `(report_id, payload_sha256, removals, destination)`; requires `confirm=True`; submission rebuilds the preview from the draft and refuses on any mismatch (disk tamper, forged consent, destination swap, cross-report consent) |
| 8. GitHub submission adapter | `github_adapter.py` | sends **exactly** the previewed bytes; credential from `NULLA_BUG_REPORT_GITHUB_TOKEN`/`GITHUB_TOKEN` read at call time, lives only in the Authorization header; **no credential, no network**; dedup (local receipt store, then destination search on the fingerprint marker) before create; real transport goes through the repo's one outbound door under `named_background_effect_scope("bug_report.submit")` |
| 9. Durable receipt | `receipts.py` | append-only hash-chained JSONL recording **what left** (ids, hashes, sizes, field/attachment names, destination, issue URL, credential env-var **name**) — never content, never a credential value |

Report schema (`schema.py`, strict unknown-key refusal everywhere): version + source SHA +
OS/arch/Python (environment), expected/actual, minimal reproduction steps, sanitized error/stack,
involved lanes/tools/models, selected bounded logs, issue fingerprint, redaction summary,
consent manifest, attachments (derived kinds only: sanitized stack, log excerpts, flags snapshot —
**there is no arbitrary-file attachment path**), destination repo.

Deduplication (`fingerprint.py`): sha256[:32] over category, exception type, top-5 frames
(**without line numbers**), sorted component ids, noise-normalized repro steps (dates,
`req_/sess_/turn_…` ids, ≥2-digit runs collapse). Title is excluded — presentation, not cause —
so the same crash reported twice with drifted titles dedups.

## 2. Usable surfaces (not an orphan module)

- **HTTP API** (`core/web/api/service.py`): `POST /api/bug-report/{draft,preview,approve,submit}`,
  `GET /api/bug-report/{status,receipts}` — owner-local, JSON content-type, origin and loopback
  guards, unknown-body-field rejection, the same accept-once invocation ledger as every other POST.
- **CLI** (`apps/nulla_cli.py`): `nulla bug-report draft|preview|approve|submit|status|receipts`
  (`--log name:path` reads bounded log sources; `--json` everywhere).
- Drafts live under `<NULLA_HOME>/data/bug_reports/drafts/<id>.json`; receipts at
  `<NULLA_HOME>/data/bug_reports/receipts.jsonl`.

## 3. Verification — what was actually run

- **RED:** all 9 test files written first; on the untouched base every one failed with
  `ModuleNotFoundError: No module named 'core.bug_report'`.
- **GREEN:** 131 tests across 9 files (incl. `tests/adversarial/test_bug_report_adversarial.py`
  and disk fixtures under `tests/fixtures/bug_report/`): `python -m pytest <the 9 files> -q` →
  `131 passed`.
- **Cumulative selection** (candidate): the 9 files + `test_architecture_composition_boundary`,
  `test_secret_redaction`, `test_cloud_status_endpoint`, `test_nulla_api_server`,
  `test_runtime_flags`, `test_app_version` → **291 passed, 2 failed**; the 2
  (`test_test_endpoint_probes_live_and_reports_green`, `test_test_endpoint_reports_red_on_401`)
  reproduce **identically on the untouched base worktree at `b3f5117f`** (live probe returns
  `failed` in this environment) — pre-existing, not caused by this lane.
  `test_runtime_flags.py::test_only_the_stated_flags_ship_off` also failed at base
  (`derived_taint` was never registered in `SHIPS_OFF`); this lane registered both
  `derived_taint` and `bug_report_cloud_synthesis` with comments — pre-existing drift fixed.
- **Sabotage matrix** (mutate → named test fails → restore byte-identical via `git checkout`):

  | # | Guard sabotaged | Named test that went RED |
  |---|---|---|
  | S1a | entropy rule disabled | `test_high_entropy_family_is_masked` |
  | S1b | entropy rule disabled | `test_adversarial_stack_fixture_is_clean_after_redaction` |
  | S2 | home-path rule removed | `test_home_path_family_is_masked` |
  | S3 | log-shape allowlist disabled | `test_log_lines_outside_safe_shapes_are_dropped_not_kept` |
  | S4a | per-source line cap raised | `test_log_lines_are_capped_per_source_with_honest_truncation` |
  | S4b | total byte cap raised | `test_log_total_bytes_are_capped_across_sources` |
  | S5 | consent gate removed | `test_submit_without_any_approval_is_refused` |
  | S6 | local fingerprint dedup removed | `test_second_submission_of_same_fingerprint_is_a_duplicate` |
  | S7 | failed submit deletes the draft | `test_failed_submission_retains_the_local_draft` |
  | S8 | receipt records issue body | `test_receipt_records_what_left_but_not_content` |

  Round 1 honestly reported two RED-misses: the byte cap absorbed the line-cap sabotage, and the
  fixture test did not name entropy-only poisons. Both tests were tightened to pin absolute
  bounds/poisons (commit `e5a46c3b`) and re-proven RED. That is the §6b.4 discipline working:
  the backstop was real, but each layer now fails its own named test.
- **Lint:** `ruff check` clean on `core/bug_report/` and all 9 test files. Touched legacy files
  (`core/web/api/service.py`, `apps/nulla_cli.py`, `core/runtime_flags.py`) carry exactly their
  5 base findings (3×N811, 1×I001, 1×F821 — all pre-existing; the one new SIM115 I introduced was
  fixed). `git diff --check` clean.
- **Secret scan of the diff:** the only token-shaped strings in the staged diff are the synthetic
  adversarial fixtures (deliberately fake values in `tests/fixtures/bug_report/` and test bodies);
  the operator's real username was replaced with `fixtureuser` in all new files.
- **Boundary:** `test_architecture_composition_boundary` green — `core/` still never imports
  `apps/`; the CLI is a thin shell over `core.bug_report`.

## 4. Exact remaining risks (read before relying on this)

1. **No live submission has happened.** The adapter's real path (network door, scope, GitHub
   201 parsing, remote fingerprint search behaviour) is mock-proven only. First real submission
   is the operator's step; expect search-tokenization quirks on the marker line.
2. **Split/joined secrets:** a secret broken across lines can defeat regex redaction (proved in
   `test_split_token_across_lines_is_visible_in_the_exact_preview`). The compensating control is
   the exact-bytes preview + consent hash — the user always sees precisely what will leave. A
   bare username in prose (not in a path or a label) is likewise not machine-detectable.
3. **Scanner exemptions are load-bearing:** the fingerprint-marker line and JSON digest fields
   are exempt by construction. They are generated from sanitized material; do not widen them.
4. **Cloud synthesis, when opted into, trusts the section filter + re-scan.** A model can still
   rephrase sanitized prose within an allowed section; it cannot add sections or content that
   survives the scan. Default OFF.
5. **Receipts chain is append-only, not access-controlled** — it sits in `NULLA_HOME` with the
   rest of the runtime's local data.
6. **`ops/verify.py` did not run here** (3.12.13 pin vs this environment; known operator-lane
   constraint) — the authoritative full-shard gate remains the operator's pre-push step.
7. **Chat-UI button not added**; the usable surfaces are the API and the CLI. A `/chat` affordance
   is a small follow-up once the operator blesses the flow.

## 5. Cherry-pick safety

Both commits are additive: one new package, one guarded endpoint family appended to
`dispatch_post`/`dispatch_get`, one CLI subcommand + an optional `argv` parameter on `main()`,
one runtime flag, `SHIPS_OFF` registration. No existing behaviour changed except the
pre-existing `SHIPS_OFF` drift fix. Safe to cherry-pick: **yes**.

---

## Amendment 1 (2026-09-02): the flow inside the real chat UI — commit `076d6c36`

The reporter is no longer API/CLI-only. **Where it lives now:**

- **Failed task cards** — `paintFinishedCard` appends a `Report a problem` button on every
  `failed` card, wired to `reportProblemForFailedRun(run)` (the live turn id pre-selects the
  matching candidate).
- **Sidebar footer** — `#reportBtn`, always available.
- **Settings** — next to "Copy build info" (whose copy already told users to quote the build
  when reporting).

**The flow** (`#bugReportOverlay`, vanilla JS in the page's own idiom — no framework, no
console calls, `esc()` on every interpolated server string):

1. Affected-turn selection from `GET /api/bug-report/candidates?session=<chat>` — failed turns
   derived SERVER-SIDE from `runtime_session_events` (grouped by `turn_key`, failure types,
   sanitized summary/error, shape-safe log lines, identifier-validated lanes/tools/models).
   Conversation content is never consulted; the browser receives only sanitized material.
2. Diagnostics checkboxes (error/stack, runtime event log, components) + expected/actual/repro/
   title/destination editing.
3. `POST /api/bug-report/draft` creates the sanitized LOCAL draft; the review screen shows the
   exact outbound bytes (`#brPreviewPre`), redaction summary, fingerprint, destination,
   outbound size, payload sha256, per-field and per-attachment removal checkboxes.
4. Any removal or edit refreshes the preview and resets approval; `POST /api/bug-report/update`
   edits fields and CLEARS the consent manifest server-side (sabotage S10 proves the guard).
5. `Approve these exact bytes` binds the current payload hash; `Submit report to GitHub` stays
   disabled until approved, then submits exactly those bytes.
6. States: draft → submitting → **submitted** (issue link + receipt note) / **duplicate**
   (existing issue link) / **failed** (explicit "draft still stored locally" + Retry).

**Adapter override:** `NULLA_BUG_REPORT_GITHUB_API_ROOT` points the submission adapter at a
self-hosted GitHub root (also the local fake in the proof). The door, named scope
(`bug_report.submit`), credential handling and dedup rules are unchanged for any root.

**Browser proof** (`ops/bug_report_browser_proof.py` — run as `python -m ops.bug_report_browser_proof`;
script mode imports the foreign main checkout, the known ops-tool trap): the production
Starlette app served by uvicorn on loopback, isolated `NULLA_HOME`, a seeded failed turn whose
diagnostics carry poison secrets (`ghp_PROOFpoison…`, `/Users/proofsecretuser/…`), and a local
fake GitHub recording every request. Driven in real Chrome:

- live failed turn (real `POST /api/chat` through the production stack → "Turn ended without an
  answer") → its card's **Report a problem** → seeded turn pre-selected → draft →
  approval invalidated by removing `flags` → invalidated again by editing `actual` →
  re-approved → submitted → fake-GitHub issue link;
- second report: fake armed `fail_once` → failed screen with "still stored locally" → **Retry**
  → submitted;
- **sweeps:** full live DOM (826 KB, dialog open) contains neither poison; the fake's request
  log shows no poison in any body/URL with `[redacted-api-key]`/`[redacted-user]` markers
  present in the payload and the fingerprint marker intact; receipts are metadata-only with the
  credential recorded as `env:NULLA_BUG_REPORT_GITHUB_TOKEN`; the only poison hit under the
  isolated home is the app's own sqlite event store — the raw source candidates sanitizes,
  which never leaves the machine;
- **screenshots** at 1280×800 and 390×844: `docs/evidence/bug-report-browser-20260901/`
  (6 PNGs: failed turn + affordance, dialog, exact preview, submitted, failed-with-retry,
  mobile dialog).

**New guards, sabotage-proven:** S9 (remove redaction from candidates →
`test_candidates_endpoint_returns_sanitized_failed_turns` fails), S10 (update keeps stale
consent → `test_update_endpoint_edits_fields_and_clears_consent` fails). Twelve sabotage rows
now cover the whole lane.

**Cumulative state after the amendment:** bug-report suite 142/142 green (131 prior + 11 new);
UI/API/chat/boundary pack 410 passed / 3 failed — all three verified identical on the untouched
base worktree (`cloud-status` ×2, `message-timestamps` ×1, documented in CURRENT_STATE.json).
Lane files ruff-clean; `git diff --check` clean; staged-diff secret scan sees only the
synthetic proof poisons.

**Amendment risks (additions to §4):** the candidates endpoint trusts the runtime event store's
shape — exotic future event payloads surface only after redaction, but a lane that starts
embedding secrets in event `message` fields relies on those same redaction families; the UI
draft form holds the user's own typing in textarea state (their words, not diagnostics); mobile
entry is the failed-card button (the sidebar is off-canvas under 720 px, by the page's existing
design); `reportProblemForFailedRun` appears on cards of live failures only — reloaded history
renders text, not cards, so the sidebar/settings entries are the durable affordances there.
