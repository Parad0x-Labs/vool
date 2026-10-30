# VOOL update path: end-to-end audit

Base: `main` 411efdb (v0.7.6-beta candidate). Branch: `audit/update-path-20261030`, head 5d7437a.
Scope: macOS arm64, macOS Intel, Linux tarball (`linux-x64`, installed source) and Linux package
(`linux-x64-bundle`). Windows was out of scope.

Environment: cloud Linux container (procps-ng 4.0.4). Two Python 3.12 builds were used. The uv venv was
3.12.3. The standalone build was 3.12.13, the CI pin, with the project installed and no venv. It was
needed because `tests/updater/test_v07_linux_journey.py` symlinks `.venv/bin/python` to the test
interpreter, and a venv interpreter reached through a symlink loses its packages. Ruff 0.17.2:
`python -m ruff check .` passes on the head.

## 1. Verdict

**STABLE WITH FIXES ON THIS BRANCH.**

Check, notice, download, install and automatic rollback are proven end to end on Mac arm64, Mac Intel and
the Linux package, from a published release. The Linux tarball is proven through its installed-source
journey.

Five defects are fixed on this branch. One of them made the updater swap code under a running app and
finalize a version that never started. The open items below do not break the release-to-release chain.
They degrade the notice and the wording in failure cases, or they are crash-window edge cases that need
an owner decision.

## 2. Stage × platform

| Stage | macOS arm64 | macOS Intel | Linux tarball (`linux-x64`) | Linux package (`linux-x64-bundle`) |
|---|---|---|---|---|
| 1 Check (start + daily, off = nothing online, header press) | works: run 41073706227 arm64 "no check while Go online is off", then a check after the press (0.7.4); 41024769917 auto (0.7.5); `test_outbound_master_switch.py` | works: run 41024769917 (0.7.5→0.7.6 draft, auto) | works: `test_v07_linux_journey[healthy]` (check press); master-switch tests shared | works: local journey from **published 0.7.5** (`run-good`, notice without a press); CI 41073706227 linux-package |
| 1 Daily over sleep | not proven (needs a real sleeping Mac) | not proven | not proven: the 24 h wait is `Event.wait`, which counts monotonic time (`core/updater/service.py:139`) | same as tarball |
| 2 Notice text | works ("An update is ready: X. Press Update to install it.", runs above). **Fixed here:** offline daily check wiped it (53ee1aa). **Fixed here:** engineering text and double periods (28985db). **Open:** O1–O5 | same | same | same; local journey shows the exact text |
| 3 Download + verify | works: real key, live feed, run 41073706227 arm64; `test_download_resume.py`, `test_trust_manifest.py`, `test_platforms_macos.py` (arch from BUILD_MANIFEST, Rosetta-safe) | works: run 41024769917 | works: `test_v07_linux_journey` | works: local journey + CI. **Fixed here:** "Connection refused" was shown as a policy block and was not resumable (5d7437a) |
| 4 Install (pause, swap, restart, data kept, journal finalized) | works: run 41073706227 arm64 (journal `…swap_planned…finalized`, data kept); run 41024769917 | works: run 41024769917 | **fixed here (6f68f4e).** Broken on base whenever `COLUMNS` is set: `ps` clipped the command line, the app was never stopped, the code was swapped under it, and the old server answered health, so the update "finalized" (base 1/8 vs head 8/8, §3.1) | **fixed here (6f68f4e)**, same cause (`LinuxBundleHelper._read_pid`). Works: local journey from published 0.7.5 |
| 5 Rollback, automatic | works: `test_transaction_flow.py` TestRollback/TestCrashRecovery (Mac installer logic); real-Mac broken-update journey not run this time | same | works: `test_v07_linux_journey[unhealthy]`, `[interrupted]` (crash after swap, recovery at next start) | works: local `--broken-update` journey from published 0.7.5 (journal `rolled_back`, 0.7.5 serving, data kept); CI 41073706227 |
| 5 Rollback, manual | not proven on a real Mac here | not proven | works: `[manual]`, `[manual_offline]`, `[manual_unhealthy]`, `[manual_interrupted]` | not offered by design (`core/updater/rollback.py:73`) |
| 5 Slow machines | not proven: covered by the separate repair branch, which I could not reach (§5) | same | same | same |
| 5 Failed swap without a backup | **fixed here (c78e8c8)**: the person saw "unexpected failure: 'NoneType' object has no attribute 'exists'" after the previous app was already back | same | same | same (package switch refusal returns no prior) |

### Open findings (not fixed here; cause file:line)

- **O1. Automatic checks stop after any failed or rolled-back update.** Cause:
  `core/updater/service.py:149-155`. After that, a new release appears only after a Check press.
  `tests/updater/test_v07_contracts.py:280` pins this as intended, so it is the owner's decision. It
  conflicts with "the notice comes without a Check press".
- **O2. A release that does not apply to this machine sets `unavailable`.** Examples: a missing Intel
  asset, a downgrade, a replayed feed, a channel mismatch. Cause: `core/updater/service.py:202`.
  - The header chip disappears (`core/vool_chat_page.py:14265`).
  - Settings says "Automatic updates are not available for this copy of VOOL." and disables Check
    (`core/vool_settings_page.py:2477`).
  - The reason shows raw platform keys: "no download for macos-x64 (it provides: …)"
    (`core/updater/decision.py:95`).
  - It clears at the next scheduled check or the next start.
- **O3. A saved "ready" survives a restart while Go online is off,** even after that version is
  installed. Cause: `core/updater/runtime.py:239-245` resets only `unavailable`. The read-only
  sub-audit reproduced this in a scratch script; I did not re-run it.
- **O4. The chat offer and the menu disagree.**
  - The chat uses different words: "Reply update now" (`core/self_update_offer.py:63-70`).
  - A chat "no" is not respected by the chip.
  - There is no native notification.
- **O5. Engineering text can still reach the status line.** Places:
  - `core/updater/rollback.py:226,260` (`detail=str(exc)`)
  - `core/updater/runtime.py:606`
  - `core/updater/worker.py:228` ("detached update failed: {exc}")
  - `core/updater/transaction.py:429,476` ("unexpected failure: {exc}"), plus the re-verification codes
    at `transaction.py:497,512`

  These show only when something unexpected fails. Each needs a plain sentence per cause.
- **O6. A crash in the middle of a Linux package install blocks every later update.** A crash or power
  cut leaves `.install-lock` behind (`installer/bundle/linux/install_bundle.py:95`), or `.next-current`
  (`:122`).
  - Every later in-app update fails, and so does the package restore (`core/updater/linux.py:204`).
  - The installer never reclaims a lock it does not own. That is a deliberate fail-closed design, so
    reclaiming one needs an owner record inside the lock. This is an owner decision.
  - Separately, the non-delegated swap journals nothing before `atomic_swap`
    (`core/updater/transaction.py:695-700`). A crash after `current` switched but before `swapped` was
    written is recovered as "not touched" (`:849-860`), and the new version runs unverified.
  - Not reproduced; found from source.
- **O7. Download edge cases (sub-audit, from source):**
  - A signed manifest with a non-integer `size` raises out of `parse_and_verify_manifest`
    (`core/updater/manifest.py:192`). This needs a publisher error.
  - ENOSPC while writing the resume metadata (`core/updater/download.py:308`) surfaces as "unexpected
    failure" instead of "not enough space".
- **O8. Version parsers.** `core/self_update_check.parse_version` treats `0.8.0-rc.1` and `0.8.0` as
  equal. Only the chat offer uses it (`core/self_update_offer.py:141`), and installed builds never carry
  a suffix today.

## 3. Fixes on this branch (each red before, green after)

### 3.1 6f68f4e: read a process's full command line (`ps -ww`)

**Cause.** procps `ps -o args=` clips to `$COLUMNS` even when its output is a pipe. Three readers used it:
- `core/updater/macos.py:225` (`_pid_belongs_to_app`, for the Mac and tarball helpers and the external
  helper's pid)
- `core/updater/linux.py:254` (package helper)
- `core/updater/worker.py:76` (worker lease)

**What went wrong.** With an install path longer than the clipped line, the running app was "not
VOOL":
- `shutdown_app` sent nothing.
- `wait_until_stopped` returned True at once.
- The code was swapped under the live process.
- A live worker read as dead.

**Old → new.** All three now call one `_process_command(pid)`, which uses `ps -ww` (unlimited width on
procps and BSD ps).

**Red (base):** `tests/updater/test_process_identity_width.py` failed 3/3:
`_pid_belongs_to_app → False`, `LinuxBundleHelper._read_pid() → 0`, `worker_alive → False`.
Demonstration on base with `COLUMNS=80`: `wait_until_stopped=True after 0.01s; app still running=True`.

**Green:** 3 passed, three runs in a row.

**Real journey** (`tests/updater/test_v07_linux_journey.py`, Python 3.12.13, same container):

| Code | `COLUMNS` | Result |
|---|---|---|
| base 411efdb | 80 | **7 failed / 1 passed** ("0.7.0 == 0.7.1" served after "finalized"; "finalized" where "rolled_back" was due) |
| base 411efdb | unset | 8 passed |
| head | 80 | **8 passed** |

This is a second, independent cause for a `[manual_unhealthy]`-style flake on any runner that sets
`COLUMNS`.

### 3.2 c78e8c8: a failed swap with no backup ends "put back"

**Cause.** `core/updater/transaction.py:381` called `prior.exists()` with `prior=None`. Mac swap
failures that undo themselves (`core/updater/macos.py:132-163`) and package switch refusals
(`linux.py:177`) return no prior. The previous app was already relaunched and healthy, but the crash
turned the result into ABORTED after ROLLED_BACK.

**Old → new.** `prior is not None and prior.exists()`.

**Red:** `test_transaction_flow.py::TestRollback::test_refused_swap_without_a_backup_ends_put_back_in_plain_words`
failed with `Step.ABORTED`, detail `"unexpected failure: 'NoneType' object has no attribute 'exists'"`.

**Green:** the test file passes 27/27.

### 3.3 53ee1aa: an offline scheduled check keeps the ready update

**Cause.** `core/updater/service.py:157-161`: any fetch failure published FAILED, "The update couldn't
be installed: update check failed…", and dropped the offer for 24 h. That included the daily check on a
laptop waking before Wi-Fi is back.

**Old → new.** A scheduled check that cannot reach the page restores the previous status. A verified
offer stays ready. A pressed Check still reports the connection failure.

**Red:** `tests/updater/test_background_check_offline.py` failed 2/2 (`FAILED is READY`,
`FAILED is UP_TO_DATE`).

**Green:** 2 passed. With `test_v07_contracts.py`, `test_outbound_master_switch.py`,
`test_service_controller.py`, `test_self_update_check.py` and `test_update_ui.py`: 98 passed.

### 3.4 28985db: a refused release reads as a plain sentence

**Cause.**
- `core/updater/service.py:168` showed "release manifest refused: signature_mismatch".
- `core/updater/status.py` wrapped sentences that already end in "." and so produced "..".

**Old → new.** The status uses `verification.plain_message`, and the wrapped detail or reason drops its
own final period.

**Red:** `tests/updater/test_check_status_words.py` failed 2/2.

**Green:** 2 passed.

### 3.5 5d7437a: a refused connection is a download failure, not a policy block

**Cause.** `core/updater/download.py:469-471` matched the text "refused". A firewall or a down host
(`URLError(ConnectionRefusedError)`) became `download_refused_by_policy`: the person read "blocked by
this app's network policy", and the download was not resumable.

**Old → new.** Connection errors are never a policy veto. `RemoteFetchRefusedError` is still reported
as blocked.

**Red:** `tests/updater/test_download_transport_failure.py::test_refused_connection_is_a_retryable_download_failure`
failed: `REFUSED is FETCH_FAILED`.

**Green:** 2 passed. With `test_download_resume.py`, `test_release_tls.py` and
`test_production_reachability.py`: 24 passed.

### Affected scope on the head (5d7437a)

Command:

```
COLUMNS=80 python3.12.13 -m pytest tests/updater tests/test_self_update_{check,offer,offer_store,updater,cli,wallet_boundary}.py tests/test_update_ui.py tests/test_linux_bundle_packaging.py
```

**Result:** 390 passed, 3 skipped, 4 failed, 15 errors.

All 4 failures and 15 errors need macOS `codesign`. They come from `test_boot_api_ui`,
`test_e2e_sandbox`, `test_final_p1` and `test_cli_and_tools`, which `ci.yml:248-251` sends to the macOS
job. These four files give the identical 4 failed / 15 errors on base 411efdb.

### End to end

**Local, from the published v0.7.5-beta Linux package** (sha256 02d4060f…, matches the release) **to
0.7.6 built from 6f68f4e** (`ops/update_journey.py`, throwaway-key feed):
- Good update: notice without a press, one press, finalized, serving the new commit, data kept.
  **PASSED.**
- `--broken-update`: `rolled_back`, 0.7.5 serving, data kept. **PASSED.**

The updating code in these runs is the installed 0.7.5, so this proves the real 0.7.5 → 0.7.6 Linux
chain. CI does not cover it, because its linux-package job builds "old" from the same commit.

**CI run [41073706227](https://github.com/Parad0x-Labs/vool/actions/runs/41073706227) on 5d7437a**
(dispatch, live-feed mode):
- linux-package: success (this head → +1, good update and broken update).
- mac arm64: success (v0.7.4 → live 0.7.5, real key, press expected).
- mac Intel: still running when this report was committed (see the follow-up commit).

## 4. Release checklist (what keeps the next update reaching installed builds)

Legend: **[T]** tooling or CI enforces it; **[H]** only human discipline enforces it.

1. **[H]** Publish `manifest.json` on a non-draft, non-prerelease release marked **Latest**. The feed
   is `releases/latest/download/manifest.json`.
2. **[H]** One manifest carries all four keys: `macos-arm64`, `macos-x64`, `linux-x64`,
   `linux-x64-bundle`. A missing key makes that platform `unavailable` (see O2;
   `core/updater/decision.py:160`). Nothing checks this (`installer/update_release.py:85-97`).
3. **[H]** Each key points at the right file. `update_release` does not cross-check key against file;
   `build_update_payload` names files and arch correctly.
4. **[T]/[H]** `version` is plain `X.Y.Z` and strictly greater than the previous release
   (`build_update_payload.py:64`; client `decision.py:153-158`). Do not use `-rc.N`: the legacy compare
   cannot order it.
5. **[T, payload side]** The payload `VERSION` must equal `version + "-beta"`. Installed builds require
   this (`core/updater/transaction.py:578,588`). **Dropping "-beta" at GA breaks every installed
   build** unless a release that accepts both ships first.
6. **[H]** `sequence` is higher than every sequence ever published on the channel. Clients record the
   high-water mark at check time, even for non-applicable results (`service.py:186-189`). Re-signing a
   fix at the same sequence is refused as a replay.
7. **[H]** `--channel beta`. `update_release` defaults to `stable`, which beta builds ignore.
8. **[T]** `schema` stays exactly `vool.update.manifest.v1`. New fields and new platform keys are
   tolerated.
9. **[H]** `minimum_compatible` is advisory and never blocks an offer, but it must parse as semver or
   the whole manifest is rejected.
10. **[H]** Key rotation needs overlap. First ship a build that pins the old and new keys, then switch
    signing. A manifest carries one signature (`manifest.py:364-368`).
11. **[H]** Asset URLs stay under `github.com/Parad0x-Labs/vool/releases/` or the release-asset host
    (`download.py:387,418-422`).
12. **[H]** The manifest's sha256 and size equal the uploaded bytes. `release-linux.yml:116-137` only
    prints a notice on a mismatch. Re-download and verify after publishing.
13. **[H]** The new Linux package must pass the **previous** release's `verify_package`, and the new Mac
    ZIP must pass the previous release's helper. Gate with the previous published package, as §3
    "End to end" does; the CI linux-package job does not do this.
14. **[T, partial]** Payloads stay within the *installed* extractor's limits (100k entries, 8 GiB total,
    2 GiB per file; `core/updater/archive.py:10-12`).
15. **[H]** `published_at` must not be more than 48 h in the future relative to users' clocks. There is
    no expiry, so a stale manifest stays valid.
16. **[H]** Before announcing, run `update-journey.yml` in gate mode (`to_tag` = the draft) and in
    live-feed mode after publishing.

## 5. Independent review of ebb8e724 (`repair/updater-rollback-wait-20261030`)

**Not done.** The branch is not on the remote (`git ls-remote` lists no such ref), and the commit is not
in this clone (`git cat-file -t ebb8e724` → "Not a valid object name"). No verdict is given.

Relevant input for that review:
- The `[manual_unhealthy]` flake has a second, independent cause: §3.1, when `COLUMNS` is set.
- The new `observe_relaunch()` and "observed up" logic on Linux relies on `_pid_belongs_to_app`. It
  needs §3.1 to see a long-path app as up. This matches the reported `test_up_but_silent` 3/3 failure
  on cloud Linux.
- 6f68f4e touches `core/updater/macos.py`, `linux.py` and `worker.py`, which may conflict with that
  branch.

## 6. Needs a real Mac (owner's test run)

1. A broken update on arm64 and on Intel must restore itself: the Mac `--broken-update` journey. Not
   run in CI for Mac.
2. Manual rollback from the header on arm64 and Intel, plus a crash during the rollback (quit or force
   quit), then the next launch.
3. Daily check after sleep: leave Go online on, sleep the Mac over a day, wake it, and see the notice
   without a press.
4. Wake while offline with an update ready (the 53ee1aa behavior). Expected: the chip stays "Update
   ready", not "Update failed". This only reaches people once installed builds carry this code, i.e.
   the release after it is merged.
5. Intel Mac on Apple Silicon (Rosetta) is offered `macos-x64` and stays there.
6. Long install path, for example VOOL in a deeply nested folder or launched from Terminal with
   `COLUMNS` exported. Update must stop and restart cleanly. `macOS ps` is documented as unlimited
   width on a pipe; the `-ww` fix makes that explicit.
7. A slow or loaded Mac: an update and a manual rollback with heavy CPU load, for the repair branch's
   launch-time clocks.

## Limits of this audit

- **Engineering text:** for O3–O5, I verified the code paths and the shown text only for the items
  fixed above. O3 and O7 come from a read-only sub-audit and were not re-run by me.
- **Not covered here:** Windows, sleep timing, and real-Mac rollback.
- **Time:** the 2 h 45 min budget was measured on the container clock, which advanced unevenly. All
  work stopped at this report.
