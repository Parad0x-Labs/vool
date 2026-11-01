# Review: Linux PDF reader — `delivery/linux-pdf-read-20261029`

Verdict: **ACCEPT** — the two reader commits are safe to land on main. Cherry-picks apply cleanly, all reader tests pass on macOS and on Linux under bubblewrap, macOS behaviour is unchanged. Three non-blocking follow-ups are listed at the end.

- Reviewed head: `c949ee18` (`delivery/linux-pdf-read-20261029`)
- `git merge-base` with main (`411efdb2`): **`8e0a887b`** — the branch sits on the 0.7.6 train base, as expected, not on main.
- The branch carries 16 commits since that base; the PDF reader itself is exactly two of them (everything else is find-file / privacy / version work that main already has in other form):
  - `c2229c4e` "Read PDFs on Linux, in a bubblewrap sandbox" — `core/artifact_readers/_sandbox.py` (bwrap backend), `core/pdf_tools.py` (portable extract route), `tests/test_linux_pdf_reading.py` (new), 3 existing pdf test files touched.
  - `c949ee18` "Linux reader sandbox: no writes outside scratch, no root escape, memory ceiling" — `_sandbox.py` (remount-ro, cap-drop, disable-userns), `_limits.py` (RLIMIT_AS), `core/artifact_readers/video.py` (no symlinked frame reads), `tests/test_linux_pdf_reading.py` (+6 tests).
- `1cbf433d` ("the install folder is VOOL's source") is a *prerequisite*, not part of the reader; it touches only `installer/bundle/linux/launch.py`. Its content is already on main as `659ab019` (+ test-undo `411efdb2`); `launch.py` is identical between the two — nothing to pick for it.

## What the commits do

Off macOS there is no PDFKit, so `pdf_tools.extract_text` failed with "needs the macOS system Python". These commits route non-macOS through the reader lane's pypdf fallback (`core/artifact_readers/pdf.py::_text_layer_pypdf`), which was already confined — and add the missing Linux backend to that confinement: bubblewrap with every namespace unshared, system trees read-only, the scratch directory the only writable place, no network, no home, no capabilities, memory ceiling, and honest fail-closed replies when bubblewrap is missing or refused.

## Review by criterion

### 1. Sandbox escape / command injection via file names — PASS

- No file name ever reaches a shell. The only shell string is the fixed rlimit wrapper (`_sandbox.py:459-464`); the decoder and its arguments travel as quoted argv elements after `exec "$0" "$@"`. The bwrap argv is built from fixed system dirs, `_resolved()` real directories only (`_sandbox.py:373-384`), and a `mkdtemp` scratch path.
- The user's PDF is never passed into the sandbox by its original path: it is copied into the scratch first (`pdf_tools.py:199-200`, `pdf.py:233-235`), and the scratch is the only writable bind (`--bind scratch scratch`, everything else `--ro-bind`).
- Root was writable in the first cut; the second commit closes it and the test proves it: `--remount-ro /` and `/dev`, no `/tmp` tmpfs, `TMPDIR` = scratch (`_sandbox.py:200-215`); `test_the_linux_sandbox_writes_nowhere_but_its_scratch` asserts writes to `/`, `/usr`, `/dev`, `/dev/shm`, `/tmp` all fail and only the scratch succeeds.
- Root escape: `--cap-drop ALL` (bwrap keeps root's capabilities otherwise — measured in the commit message), and `--unshare-user --disable-userns` when bwrap supports it (≥0.8.0), refusing nested user namespaces; `test_a_decoder_cannot_make_itself_a_user_namespace` asserts `unshare -Ur` fails.
- `run_confined` refuses non-absolute or non-executable decoder paths before spawning (`_sandbox.py:591-597`) and execs the interpreter by `os.path.realpath` so a venv symlink outside the bind scope cannot smuggle reads in (`_sandbox.py:607-612`).
- Symlink egress through decoder-written files is closed in the video lane: frames are re-opened by the parent with `O_NOFOLLOW`, must be regular files, size-capped (`video.py:178-192`); `test_a_frame_the_decoder_links_elsewhere_is_not_read` proves a decoder that symlinks its output at `~/.ssh/id_rsa`-shaped files yields nothing.
- Decoder environment is empty of secrets: fixed `PATH`, no proxy vars, `HOME` = `TMPDIR` = scratch (`_sandbox.py:620-625`).

### 2. Size / time limits — PASS

All in `core/artifact_readers/_limits.py`, enforced while the decoder runs, not after:

- Wall clock 90 s per decoder (shortened to the budget remaining, `_sandbox.py:600-602`), 180 s per attachment (`ExtractionBudget`), and `_supervise` keeps watching after pipes close — the historic "unwatched run" hole is guarded (`_sandbox.py:498-538`).
- CPU `ulimit -t 60` both platforms; **new on Linux: `ulimit -v` 4 GB address space** (`_limits.py:121-124`, applied at `_sandbox.py:459` — Linux only, since macOS refuses RLIMIT_AS; the comment says exactly that, honestly). `test_a_decoder_cannot_take_more_memory_than_its_ceiling` proves a 6 GB allocation fails.
- Output caps enforced mid-stream with process-group kill: 64 MB stdout / 2 MB stderr (`_sandbox.py:551-574`); per-file `ulimit -f` 512 MB; open files 256; scratch quota 512 MB polled every 0.25 s with a bounded, symlink-safe scan where "unmeasurable = over budget" (`_sandbox.py:387-419`).
- Pages capped (200 via `pdf_tools`, 400 in the reader lane), per-page and per-document character caps; a longer document is *reported as omitted*, never silently cut (`pdf.py:243-245`).
- Chat attachment intake is already capped (10 MiB/file, 25 MiB/turn, `core/chat_attachments.py:66-68`), which bounds uploaded PDFs before the reader ever sees them.

### 3. Malformed / encrypted / huge PDFs fail closed with a plain message — PASS

- Encrypted: the pypdf script tries an empty password and otherwise reports `password_protected` (`pdf.py:96-103`); surfaced as "The PDF is password-protected; its contents were not read. Remove the password, or attach an unprotected copy." (`pdf.py:202-209`).
- Malformed: every pypdf exception becomes `pdf_unreadable` → "The PDF could not be opened; it may be corrupt." (`pdf.py:210-216`); unparseable output from the child fails closed too (`pdf.py:198-201`).
- Missing decoder: `require_confinement` runs *before* any spawn (`_sandbox.py:590`, `pdf_tools.py:197`), so "no decoder" can never hide why. Without bubblewrap the status is `missing_dependency` and the words name the fix ("install bubblewrap … sudo apt install bubblewrap") without mentioning macOS (`_sandbox.py:270-284`). Proven on Linux: `test_without_bubblewrap_the_reason_says_what_to_install` passes in a container where the namespace probe fails.
- Missing pypdf itself: `pdf_decoder_unavailable` → "Paste the text from the PDF into your message instead." (`pdf.py:162-168`).
- Over-limit outcomes are typed and plain: quota ("produced more than N MB of intermediate data; it was stopped"), decoder timeout, output-too-large ("appears designed to exhaust the reader"), extraction timeout (`_sandbox.py:323-345, 521-574`).
- A scanned PDF still gets the honest "no text layer — run pdf.ocr" answer rather than a silent empty string (`pdf_tools.py:232-237`).

### 4. Missing pdftotext / poppler on Linux — NOT APPLICABLE BY DESIGN

The Linux route spawns no poppler binary at all. The text layer is read by pypdf, a pinned Python dependency (`pypdf==6.21.2`, `pyproject.toml:33`, identical on main and on the branch base), running inside the same confinement as every other decoder. There is no pdftotext to be missing. The one thing that *is* absent on Linux is OCR (Vision is macOS-only): `toolchain_available()`/`ocr_available()` are Darwin-gated (`vision_tool.py:194-198`), so scanned pages are marked `blocked` with the plain warning "no text layer, and no OCR engine is available on this machine, so this page was NOT read" (`pdf.py:262-267`) — fail-closed, not faked.

### 5. macOS unaffected — PASS

- `pdf_tools.extract_text` keeps the PDFKit route byte-for-byte: `_uses_pdfkit()` gates it (`pdf_tools.py:224-227`); the only helper change is the new `_uses_pdfkit` seam, which the existing tests now pin so the macOS-helper tests keep testing the macOS route on any machine.
- The Darwin confinement branch, its profile, its rlimits and its wording are unchanged (`_sandbox.py:613-616`, `_sandbox.py:269-273`); `ulimit -v` is added only on Linux.
- `tests/test_artifact_readers_confinement.py` pytestmark is now explicitly "Darwin and confinement available" — same effective behaviour as before on a Mac (it could only ever run where confinement exists; it now just names where the Linux assertions live instead). Nothing skipped, deleted or weakened: the macOS sandbox suite ran and passed in this review.
- One deliberate cross-platform hardening: `video._read_decoder_output` also applies on macOS — it only rejects symlinked/oversized frame files, which honest decoders never produce.

## Test evidence

Environment: pytest 9.1.1 and pypdf 6.21.2 (both pinned in `pyproject.toml`); Linux runs in an Ubuntu 24.04 container, bubblewrap 0.9.0, Python 3.12.

| Branch | Files | Environment | Result |
| --- | --- | --- | --- |
| `c949ee18` (delivery) | the 4 touched pdf test files | macOS (no Linux sandbox) | **78 passed, 10 skipped, 0 failed** (skips = the Linux-gated file) |
| `c949ee18` (delivery) | `tests/test_linux_pdf_reading.py` | Linux container, default seccomp (bwrap cannot unshare) | **1 passed** (fail-closed reply), 9 skipped |
| `c949ee18` (delivery) | `tests/test_linux_pdf_reading.py` | Linux container, bubblewrap able to unshare | **10 passed, 0 failed** |
| main + both commits (`5cb95768`, `c6550923`) | the 4 touched pdf test files | macOS | **78 passed, 10 skipped, 0 failed** |
| main + both commits | the 4 touched pdf test files | Linux container + bubblewrap | **44 passed, 44 skipped** (skips = macOS-only suites, by design) |
| main + both commits | `tests/test_linux_pdf_reading.py` | Linux container + bubblewrap | **10 passed, 0 failed** |

The 10 Linux tests cover: a PDF is actually read on Linux via the confined fallback; the sandbox sees only its scratch; no network (loopback listener and 1.1.1.1 both refused); writes outside the scratch impossible; sockets unopenable; nested user namespaces refused; the 4 GB memory ceiling enforced; symlinked decoder output not read; installing bubblewrap works without a restart.

`ruff` 0.17.2 (the version pinned in `pyproject.toml:141`): **clean** on all nine files the commits touch, on both the delivery branch and on main with the cherry-picks applied.

## Cherry-pick onto main

- `git cherry-pick c2229c4e c949ee18` onto main (`411efdb2`): **both applied with zero conflicts**; scratch branch `5cb95768`, `c6550923`.
- Nothing under `core/artifact_readers/`, `core/pdf_tools.py` or the pdf test files changed on main since the merge-base `8e0a887b` — main's intervening work (find-file, screen privacy, launcher) touched none of them.
- The launcher prerequisite is already on main (`659ab019`, plus test-undo `411efdb2`), so the cherry-picked reader is complete on main with no extra commits.
- All reader tests and ruff pass on that scratch branch (table above). No new dependency is introduced: `pypdf==6.21.2` is already declared on main.

## Non-blocking follow-ups (none blocks the merge)

1. `pdf_tools._extract_portable` buffers the whole file with `path.read_bytes()` (`pdf_tools.py:200`) before any size check; the macOS PDFKit route streams from disk instead. A pre-copy size ceiling (with the same plain words) would keep a giant workspace file from being fully buffered by the serving process. Exposure is limited: uploads are capped at intake, so this needs the `pdf.extract_text` tool pointed at an oversized local file.
2. `extract_text(timeout=...)` is not consulted on the non-macOS route — the extraction budget governs there (`pdf_tools.py:217` docstring could say so).
3. `_fallback_read_scope` read-binds the pypdf package's parent directory — the venv's site-packages — into the sandbox (`pdf.py:142`); the adjacent comment claims "not site-packages at large". The import needs that parent, it is read-only, and it is not the repository — but the comment should state the true scope.

## Provenance

Reviewed from a detached checkout of `c949ee18` and a scratch branch on main; no product file was modified by this review. macOS runs used the pinned pytest/pypdf/ruff; Linux runs used a disposable Ubuntu 24.04 container (no VPN, stock docker). No PR opened; only this branch carries this file.
