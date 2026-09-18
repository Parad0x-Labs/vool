# Process confinement — the exact claim, and its limits

Owner: Apple/Linux lane. Branch `build/job-runner-real-confinement-20260902`, base
`a6c8e3c4eaa70f30ccd77a9a750560286da34f2c`. Verified on **macOS 26.5.1, arm64**, with
`/usr/bin/sandbox-exec` (Seatbelt).

## The defect

`JobRunner._with_network_isolation` is the only function in the tree that applies a
confinement profile. It opened with:

```python
if self.policy.allow_network_egress:
    return argv
```

as its first statement. The method is named for network isolation and is in fact the entire
boundary around a child process, so trusting a command with the network ran it with no
Seatbelt profile at all — no write confinement, no read denial, no secret-directory rule.

`SandboxRunner` handed exactly that policy to `pip pip3 npm npx yarn pnpm cargo` by base
command name. Install subcommands are refused earlier by the network guard, so what actually
ran unwrapped was the half that executes other people's code: `cargo build` (build.rs and
proc macros), `npm run <script>` (package.json lifecycle scripts), `pip uninstall`. In front
of them stood `path_args_within_roots`, which reads argument STRINGS and cannot see what a
process does once it is running.

## What is claimed now

> On macOS with `sandbox-exec` present and usable, every command that reaches `JobRunner.run`
> is executed under a kernel-enforced Seatbelt profile. There is no code path that runs a
> child process without one. A host where no backend can be applied refuses the job with
> `KernelIsolationUnavailableError`, surfaced as `status: "sandbox_unavailable"`.

Enforced by the kernel, per job:

| | Mechanism |
|---|---|
| Writes confined to the writable roots | `(deny file-write*)` + `(allow file-write* <roots> "/dev")` |
| Operator's home unreadable | `(deny file-read-data (subpath $HOME))` |
| Interpreter/toolchain readable | `(allow file-read-data <read_roots + writable roots>)` |
| Credential roots fully denied | `(deny file-read* <ssh/aws/gnupg/gcloud/gh/solana/VOOL home>)`, last rule so it wins |
| Network denied unless policy says otherwise | `(deny network*)` |
| Job cannot create a hard link | measured: `os.link` fails under the profile |

Enforced by the runtime, per job:

| | Mechanism |
|---|---|
| Environment sanitized | allow-list of 9 variables; `HOME`/`TMPDIR`/`XDG_*` point inside the job's own root |
| Whole process tree torn down | `start_new_session=True` + `os.killpg` SIGTERM→SIGKILL on timeout, cancellation **and** the success path |
| Cancellation is prompt | the wait is sliced at 0.2s against a `threading.Event`, not noticed at completion |
| Pre-planted hard link refused | `st_nlink > 1` pre-flight over the writable roots |
| Outcomes distinguished | `executed` · `timed_out` · `cancelled` · `blocked_by_hard_link` · `sandbox_unavailable` · `blocked_by_policy` |

Network egress is a policy field. It is never granted by a base command's name, and granting
it never relaxes filesystem confinement — the two were one boolean and are now two.

## Limits — read these before relying on it

1. **This is macOS Seatbelt, not a container or a VM.** It is a kernel MAC policy applied to
   one process tree. It is not a security boundary against a local privilege escalation or a
   kernel bug, and it does not isolate CPU, memory or PIDs. `max_memory_mb` on the policy is
   still not applied by anything.
2. **Reads outside `$HOME` and outside the credential roots remain open** — `/usr`, `/opt`,
   `/etc`, `/tmp`, other users' world-readable files. The profile is `(allow default)` with
   named denials, because a read allow-list breaks every interpreter's stdlib loading. A job
   can read `/etc/passwd`. It cannot read anything under the operator's home.
3. **The home denial is `file-read-data`, not `file-read*`.** Measured: the starred form also
   denies read-METADATA, which every path lookup needs on each ancestor directory, so
   `execvp()` of an interpreter living under the home failed with "Operation not permitted"
   and no job could start. The consequence is honest and worth stating: a job can `stat` and
   list inside the home and learn that a file EXISTS and how big it is. It cannot read a byte
   of one. The credential roots are denied with the starred form, so names there are hidden too.
4. **Pre-planted hard links are caught by a pre-flight scan, not by the kernel.** Path-based
   confinement decides by path, and a hard link is a second path to one inode; measured on
   macOS 26.5.1, a write through a pre-planted link succeeds under
   `(allow file-write* (subpath ws))`, and `(deny file-link)` does not change it (that rule
   governs creating links). The scan is `st_nlink` over the writable roots — an OS fact, not
   a string heuristic — costing 0.02s for 4,511 files, and it refuses above 200,000 files
   rather than reporting a confinement it did not verify. The kernel does close the other
   half: a confined job cannot create a link.
5. **Linux is implemented but not verified here.** `bwrap` gets `--ro-bind / /` plus writable
   binds and `--unshare-net`; `unshare`/`firejail` express network isolation only and cannot
   satisfy "confine writes, permit network", so a network-trusted job on a host with only
   those refuses. None of this was exercised on a Linux host in this lane — it is
   `implemented`, not `verified`.
6. **Windows has no backend and no override.** Executable tools refuse there. The previous
   `heuristic_only` escape hatch is gone.
7. **`heuristic_only` no longer means what its name says.** It relaxes NETWORK trust only.
   It cannot relax filesystem confinement and it refuses on a host with no backend. The name
   is kept because an environment variable and a policy field carry it.

## What must never be said about this

The static argv guard (`path_args_within_roots`) is a backstop that inspects argument
strings. It is not confinement and no receipt, log line or document may describe it as a
sandbox. Same for the network guard, which classifies command lines. The kernel profile is
the boundary; everything else is a check in front of it.

## Evidence

- RED at base: `validation-logs/job-runner-real-confinement-20260902/RED-at-a6c8e3c4.txt`
  (14 failed, 1 control passed).
- Malicious-fixture drive: `.../malicious-fixture-drive.txt` — eleven hostile payloads through
  the public door with stub `npm`/`cargo`/`node`; operator checkout and `$HOME` byte-identical,
  process count unchanged, zero surviving grandchildren.
- Sabotage: `.../sabotage-sweep.txt` — eight mutations of the wrapper, the fail-closed
  fallbacks, the environment, the process group, the hard-link pre-flight, the home denial and
  the package-command grant. Every one turns named tests red.
