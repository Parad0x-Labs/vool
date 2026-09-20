# Linux sandbox findings — why code-task steps do not execute on GitHub's Linux runners

Diagnosis note from the prep mission on `prep/phase2-hardening` (DIAGNOSIS ONLY;
`sandbox/job_runner.py` was not modified — it is the main worker's active
battlefield). Everything below is backed by dispatched workflow runs, not
local simulation.

Instrument: `tools/dev/probe_sandbox_linux.py` (D1) exercises the REAL
`sandbox/job_runner.py` — its own `_backend_usable` probes, its own prefix
builders, its own child environment, and the full `JobRunner.run()` entry
point. Workflow: `.github/workflows/debug-linux.yml` (D2).

Evidence runs (Parad0x-Labs/vool, branch `prep/phase2-hardening`):

| run | what it proved |
| --- | --- |
| 35537608791 (jobs 106149323578 / -461 / -320) | bare-leg refusal strings captured; first pytest leg invalid (19 `ModuleNotFoundError: zstandard` collection errors — `.[dev]` alone is insufficient, needs `.[dev,companion]`) |
| 35537786930 (jobs 106149814447 / -426 / -487) | the A/B proof below; artifacts `debug-linux-{bare,profile,bare-arm}` uploaded and verified to carry the refusal strings |

## The exact failing backend and error strings

On `ubuntu-latest` (GitHub's 24.04 image, kernel 6.17.0-1022-azure, x86_64)
with `apt-get install -y bubblewrap` and NOTHING else:

| backend probe (the runner's own argv) | exit | exact stderr |
| --- | ---: | --- |
| `bwrap --unshare-net --ro-bind / / --tmpfs /tmp -- true` | 1 | `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted` |
| `bwrap --ro-bind / / --tmpfs /tmp -- true` (deny_network=False variant) | 1 | `bwrap: setting up uid map: Permission denied` |
| `unshare -n -- true` | 1 | `unshare: unshare failed: Operation not permitted` |
| firejail | — | not on PATH |

Read-only kernel facts from the same host:

```
/proc/sys/kernel/unprivileged_userns_clone = 1        # the kernel allows userns
/proc/sys/user/max_user_namespaces = 63786            # and has room for them
/proc/sys/kernel/apparmor_restrict_unprivileged_userns = 1   # AppArmor gates it
/sys/module/apparmor/parameters/enabled = Y
```

`ubuntu-24.04-arm` (job 106149814487) fails identically — not arch-specific.

## Most probable root cause

**Ubuntu 24.04's AppArmor unprivileged-userns restriction, not a defect in
`sandbox/job_runner.py`.** The kernel would allow the namespace
(`unprivileged_userns_clone=1`, `max_user_namespaces>0`), but
`kernel.apparmor_restrict_unprivileged_userns=1` denies unprivileged binaries
without an AppArmor profile the right to create one. bubblewrap's apt package
ships no profile, so:

1. `_linux_bwrap_prefix` (`sandbox/job_runner.py:682`) probes bwrap via
   `_backend_usable` (`sandbox/job_runner.py:71`); the probe exits 1 with the
   strings above, so both the `deny_network=True` and `deny_network=False`
   cache keys resolve False (`sandbox/job_runner.py:697-701`).
2. `_linux_unshare_prefix` (`sandbox/job_runner.py:715`) probes False the same
   way; firejail is absent.
3. `_kernel_network_isolation_prefix` (`sandbox/job_runner.py:630`) therefore
   returns `None` for every backend.
4. `_with_network_isolation` (`sandbox/job_runner.py:547`) raises
   `KernelIsolationUnavailableError` with `_no_kernel_isolation_message()`
   (`sandbox/job_runner.py:604`, message at `:606`) — every journaled code-task
   step answers `sandbox_unavailable`, which is the ~500-test CI cluster.

The runner is failing CLOSED, as designed. The environment is the defect.

## The A/B proof that the ci.yml profile fixes it

`ci.yml` already carries the mitigation (`Provision the kernel sandbox backend
(bubblewrap)`, `.github/workflows/ci.yml:68-93`): an AppArmor profile granting
ONLY `/usr/bin/bwrap` the `userns` right, with a global
`apparmor_restrict_unprivileged_userns=0` sysctl only as fallback.

Run 35537786930, three legs, same commit, one variable each:

| leg | provisioning | probe verdict | targeted test file |
| --- | --- | --- | --- |
| bare (x86_64) | `apt-get install bubblewrap` only | no backend; `run(['echo','ok'])` REFUSED | **18 failed, 1 passed** |
| profile (x86_64) | + ci.yml AppArmor profile | `/usr/bin/bwrap` selected; `run(['echo','ok'])` ok; write outside workspace denied (`exit=2, leaked=False`) | **19 passed** |
| bare-arm | `apt-get install bubblewrap` only | no backend | **18 failed, 1 passed** |

On the profile leg `apparmor_parser -r` succeeded and the sysctl fallback did
NOT fire (`apparmor_restrict_unprivileged_userns` stayed 1) — the scoped
per-binary profile alone is sufficient, and bwrap genuinely confines, not
merely runs. The single bare-leg pass is the test that asserts a refusal;
refusals hold trivially when everything is refused.

**Implication for the main worker:** the mechanism and the fix are proven in
isolation on GitHub's actual runners. If ~500 failures persist in full shard
runs, the next questions are (a) does every shard job actually run the
provisioning step before tests, (b) on which runs does `apparmor_parser`
fail AND the sysctl fallback fail, and (c) how much of the remainder is a
different cluster. The 5-minute way to ask is: push, watch `debug-linux`
fire, read the leg summary.

## Candidate repairs (with trade-offs — NOT applied here)

1. **Surface the probe's stderr at the owner (recommended, small).**
   `_backend_usable` (`sandbox/job_runner.py:84-93`) captures stdout/stderr and
   throws both away; the operator sees only the generic
   `_no_kernel_isolation_message()` text and never the one line that names the
   cause (`bwrap: setting up uid map: Permission denied`). Caching
   `(usable, stderr)` and including the failing probe's stderr in the
   `KernelIsolationUnavailableError` message turns the next occurrence into a
   one-line read instead of a 2-hour CI diagnosis. No behavior change; the
   refusal still fails closed.
2. **Keep the scoped AppArmor profile as primary (already on main).** The
   global sysctl fallback (`ci.yml:91`) relaxes userns for EVERY unprivileged
   binary on the runner — a strictly larger surface than a profile that grants
   `userns` to `/usr/bin/bwrap` alone. If a future runner image refuses the
   profile, prefer failing the provisioning step loudly (its final
   `bwrap --unshare-net ... -- true` check, `ci.yml:93`, already does) over
   defaulting to the global relaxation.
3. **Ship the profile as a reviewed file.** The profile currently lives inline
   in the workflow heredoc (`ci.yml:81-88`). A checked-in
   `ops/apparmor/bwrap.profile` installed by the step would make the security-
   relevant bytes reviewable like any other contract. Cosmetic-to-moderate;
   no behavior change.

## Incidental finding: CI artifacts have been silently empty

`actions/upload-artifact@v7` defaults to `include-hidden-files: false`, which
excludes the dot-prefixed `.verification-logs/` DIRECTORY — completed `ci.yml`
runs on the repo expose **no artifacts at all** (verified via the artifacts
API on 2026-09-20), and the debug workflow reproduced the empty upload until
`include-hidden-files: true` was set (run 35537786930 uploads fine; run
35537608791 did not). Recommended: add `include-hidden-files: true` to the
three upload steps at `ci.yml:145`, `:188`, `:215` — otherwise shard-failure
diagnoses can only be done from live logs, which expire.

## Untested / limitations

- The Docker repro (`tools/dev/linux_repro.sh`, D3) is EXPERIMENTAL and was
  never executed on this Mac: the Docker CLI is installed but the colima
  daemon is stopped, another worker's VM (`null-rh-sandbox`) is running, and
  only ~3.7GiB was free — starting the default VM profile under those
  conditions risks a disk-full that would kill active work. A plain container
  would also reproduce a DIFFERENT mechanism (Docker seccomp on userns
  syscalls) rather than GitHub's AppArmor; the CI runs above are the real
  evidence.
- The census loop (`tools/dev/census_after_push.sh`, D4) was validated on a
  tiny green scope plus a synthetic failed/killed-shard artifact root; it has
  not run a full census by design (the main worker owns heavy runs).
