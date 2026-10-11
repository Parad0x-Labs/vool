# VOOL Drop — local prototype

**UNREVIEWED: not for real messages.** Branch `repair/vool-drop-fit-20261011`; base `50ee1e6716aeb701410ad542d75b96ad064b56b0`. FIT was committed first at `44c1596a0990afd1659936057d88d92bfee00749`. Exact final head and counts are in `status-fit.json` beside the task brief.

The separate `python -m core.drop_fit` CLI is off unless `VOOL_DROP_FIT=1`. Two fresh VOOL homes exchange ciphertext through a private SSD directory. One Drop carries one note, one selected file and one locally approved SHA-256 task. Sender text stays data; the recipient's real permission owner checks approval and execution. There is no shell, model, payment, registrar, phone or production inbox integration.

| Demo step | Observed result |
| --- | --- |
| 1. Invite | PASS — short local link, independent identity pins, authenticated one-use acceptance. |
| 2. Offline note/file | PASS — B closed during send; restart receives exactly 10,485,760 bytes across 257 authenticated frames; digest matches. |
| 3. Task before approval | PASS — displayed pending; execution refused; zero executions. |
| 4. Approve/result | PASS — fresh selected-file digest returned to A; one execution. |
| 5. Duplicate | PASS — identical ciphertext and re-encrypted logical duplicate survive restart without a second claim. |
| 6. Extra permissions | PASS — refusal, no approval or execution. |
| 7. Expiry/delete | PASS — eight actual reader families had readable synthetic controls before erasure, then withheld or absent content; selected bytes, semantic/source embeddings and receipt rows removed. |
| 8. Bad input | PASS — AEAD tamper, wrong vault key, 10 MiB + 1 byte, expired invite and missing relay refused; wrong-key attempt leaves vault state unchanged. |

Transcript: `fit-logs/demo.log`. Separate-process CLI and persisted handshake/file-assembly interruption evidence: `fit-logs/stage5.log`. Tests use the reused Python 3.12.13 environment and Node 26.1.0; no installs. Commands below run from the isolated checkout under the task resource guard, with scratch VOOL_HOME/cache/temp configuration and `nice -n 10`:

```sh
# PY/NODE are existing SSD binaries; SCRATCH is task scratch; DEMO is a fresh directory.
export VOOL_DROP_NODE="$NODE" VOOL_HOME="$SCRATCH/home-test" VOOL_WORKSPACE_ROOT="$SCRATCH/home-test/workspace" TMPDIR="$SCRATCH/tmp" XDG_CACHE_HOME="$SCRATCH/cache" VOOL_KEYCHAIN_ALLOWED=0
nice -n 10 "$PY" -m pytest -q -p no:cacheprovider tests/test_mode_permission_policy.py tests/test_forget_reader_gate_r7.py tests/test_forget_receipt_boundaries_r9.py tests/test_honesty_receipt.py tests/test_drop_fit.py
nice -n 10 env VOOL_DROP_FIT=1 "$PY" -m ops.drop_fit_demo --scratch "$DEMO"
nice -n 10 "$NODE" --test --test-concurrency=1 third_party/drop-session/test/*.test.mjs
nice -n 10 "$PY" -m ruff check --no-cache core/drop_fit tests/test_drop_fit.py ops/drop_fit_demo.py
```

Baseline existing tests: **51/51 pass, 0 fail, 0 skip** (`base-existing.log`). Head with `VOOL_DROP_FIT=0`: **51/51 pass, 0 fail, 0 skip** (`head-flag-off.log`). All original base files remain byte-identical; no production route imports this experiment. Cumulative prototype: **70/70 pass**, including 19 new cases. Pinned library: **32/32 pass** (`vendor-complete.log`); changed-file ruff passes (`ruff-final.log`). Logs live in `fit-logs/` beside the task brief; intermediate failures remain preserved. No full suite, release build or release acceptance is claimed.

Before a real release: independent cryptographic composition/storage review; the unchanged legacy **1 CRITICAL + 9 HIGH** audit blockers before names/paid paths; UI, general task/planner integration, phone support, hosted relay/admission and automatic expiry scheduling. The pending-erasure scope mismatch is contained with a home-wide fence: incomplete cleanup withholds the whole scratch home. That is conservative containment, not a repair of the existing raw/hashed scope contract. Local bodies/files and vault keys rely on file permissions; device theft, arbitrary backups and SSD sanitization are outside this proof. Interrupted execution stays uncertain and never auto-runs again; general streaming resume and external-effect reconciliation remain absent. No push, PR, transaction, spend, paid model call or v0.7 change.
