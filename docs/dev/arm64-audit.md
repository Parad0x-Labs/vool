# Apple Silicon (arm64) packaging audit — 2026-11-02

Two questions, both about the macOS app built by `installer/bundle/build_macos_app.sh --self-contained`:

1. Can an x86_64-only component get into the arm64 app? (macOS shows "Support Ending for
   Intel-Based Apps" as soon as one of an app's processes runs under Rosetta.)
2. Did the 0.7.7 build install Python packages at versions other than `uv.lock` pins?

Base: `main` at `8af687ca5ce62882dbca48c8a26ba0d596ace5f1` (v0.7.7-beta). Everything below was checked on
Linux. No Mac was available, so the limits are listed at the end.

## Evidence from the shipped 0.7.7 artifacts

Both macOS update payloads of the `v0.7.7-beta` release were downloaded and checked against their
published `.sha256` files:

| Asset | sha256 |
|---|---|
| `VOOL-0.7.7-beta-macos-arm64-update.zip` | `37c56b2fe7b631d8ca7fb6146daafc78469c7dd2147ad889cecea468d5159a55` |
| `VOOL-0.7.7-beta-macos-x86_64-update.zip` | `e5dcd222f8e873db03f1f34519847a8e63ab1288303708514855d3aaf131e1f3` |

`BUILD_MANIFEST-macos-arm64.json` says the arm64 app was built from `8af687ca` on a native arm64 host
(`build_host_arch: arm64`, `built_under_translation: false`). No CI job builds the arm64 app, so it
was built outside CI.

### Architecture of the arm64 app

`installer/bundle/macos_arch_census.py` (pure-Python Mach-O reader), and separately the new
`installer/bundle/macos_slice_check.sh` (`file` plus `llvm-lipo -archs`, the same interface as `lipo`),
were run on the extracted `VOOL.app`:

| Where | arm64 only | arm64 + x86_64 | x86_64 only |
|---|---|---|---|
| `Contents/MacOS/VOOL` (native launch shim) | 1 | 0 | 0 |
| `Contents/Resources/bin` (`vool-devauth`, `VOOL Notifications.app`) | 2 | 0 | 0 |
| `Contents/Resources/ollama` | 2 (`llama-server`, `llama-quantize`, thinned) | **1 (`ollama` itself)** | 0 |
| `Contents/Resources/python` (CPython, `lib-dynload`) | 13 | 0 | 0 |
| `site-packages` (extension modules) | 52 | 65 (universal2 wheels: pyobjc, pycryptodome, nacl, solders, charset_normalizer) | 0 |
| **Total (136 Mach-O, dSYM debug companions excluded)** | 70 | 66 | **0** |

- The launch contract is right: the main executable is an arm64 Mach-O and `Info.plist` declares
  `LSArchitecturePriority = [arm64]`, `LSMinimumSystemVersion = 14.0`.
- `macos_arch_gate.py --arch arm64 --min-os 14.0` passes; `macos_slice_check.sh … arm64` passes
  (227 Mach-O including the 91 universal dSYM companions); `macos_slice_check.sh … x86_64` fails it as
  expected, naming the 70 arm64-only files (negative control).
- So the 0.7.7 arm64 app has **no x86_64-only Mach-O**. The one avoidable x86_64 payload is the x86_64
  slice of `Contents/Resources/ollama/ollama`: the build thinned ollama's runner and libraries to the
  target but copied the executable itself whole.

## Every place an x86_64 component can enter the arm64 app

| # | Entry point | State at 0.7.7 | Now |
|---|---|---|---|
| 1 | **Ollama executable** (`ops/macos_build_inputs.py` downloads the universal `ollama-darwin.tgz`) | Copied whole: arm64 + x86_64 | **Fixed.** `bundle_ollama_runtime` thins it to the target slice; a missing slice, an unreadable file or a failed thin stops the build. |
| 2 | Ollama runner and libraries (`llama-server`, `llama-quantize`, `lib*.dylib`, `lib*.so`) | x86_64-only libraries left out, universal ones thinned | Unchanged (correct). |
| 3 | **Wrapper app's interpreter** (non-`--self-contained` build; `.venv/bin/python` or `python3.x` on `PATH`) | Any interpreter that imports `webview` was accepted. An Intel Homebrew Python under `/usr/local`, or a venv made from one, runs under Rosetta | **Fixed.** The build preflight (`webview_capable`) and the emitted launcher (`py_ok`) accept an interpreter only if `platform.machine()` reports the bundle's architecture. A translated process reports `x86_64`, so a universal2 interpreter running translated is refused too. |
| 4 | Embedded CPython (python-build-standalone via `uv python install cpython-<v>-macos-aarch64-none`) | Architecture requested explicitly and checked with `lipo -archs` before and after staging | Unchanged (correct). |
| 5 | Python wheels (`uv pip … --python-platform aarch64-apple-darwin --no-build`) | Platform pinned; universal2 wheels carry arm64 | Unchanged platform pinning; the set now comes from `uv.lock` (Task 2 below). |
| 6 | `VOOL_BUNDLE_WHEELHOUSE` and `built_wheels.json` (Intel-only `cryptography` wheel) | Never selected for arm64 (`--python-platform`) | Now also refused by digest: a built wheel joins only its own target's locked pin. |
| 7 | Native launch shim (`clang -arch ${TARGET_ARCH}`) | arm64 | Unchanged (correct). |
| 8 | Swift helpers `vool-devauth`, `VOOL Notifications.app` (`swiftc -target ${arch}-apple-macos${min}`) | arm64 | Unchanged (correct). |
| 9 | Build host running under Rosetta (`uname -m` reads `x86_64`) | Builds a consistently labelled x86_64 app; an explicit `--arch arm64` from a translated shell is refused as a cross build | Unchanged. The new CI job also refuses a translated runner (`sysctl.proc_translated`). |
| 10 | The arm64 package was built outside CI | Only the internal gate guarded it | **New CI job** `.github/workflows/macos-arm64.yml` builds natively on `macos-15` (arm64) and fails on any Mach-O without an arm64 slice (`file` + `lipo`), and on dependency drift. |

Not part of the app, but able to start an Intel process from a running arm64 VOOL:

- **Fixed:** `core/artifact_readers/video.py` tried `/opt/homebrew/bin/ffmpeg`, then
  `/usr/local/bin/ffmpeg` (the Intel Homebrew prefix), then `PATH`. On an Apple Silicon Mac with only an
  Intel Homebrew ffmpeg, reading a video started ffmpeg/ffprobe under Rosetta. On Apple Silicon it now
  reads each candidate's Mach-O slices and takes only an arm64 or universal binary; with only an x86_64
  one, video reading reports itself unavailable (`tests/test_video_reader_skips_intel_ffmpeg.py`).
- Not changed: `core/bug_report/machine.py` runs `ollama --version` from `PATH` for a bug report.
- The live browser lane starts the user's own installed Chrome-family app.

## Task 2: the 0.7.7 Mac build did not install from the lock

The self-contained build ran `uv pip install` with a hand-written list (`pydantic`, `requests`,
`"starlette>=1.3.1,<2.0"`, `"uvicorn>=0.30,<1.0"` …). That resolves against the live index; `uv.lock`
was never read. `ops/macos_locked_requirements.py check-app` on the shipped apps:

| App | Installed | Differ from the lock |
|---|---|---|
| 0.7.7 arm64 | 80 | 32, e.g. starlette 1.10.0 (lock 1.3.1), uvicorn 0.54.0 (0.49.0), pydantic 2.14.2 (2.13.4), cryptography 50.0.2 (50.0.1), websockets 17.4 (15.0), pyobjc-core/-cocoa/-quartz/-security/-webkit 13.0 (12.2.2, while EventKit/Contacts stayed 12.2.2) |
| 0.7.7 x86_64 | 80 | 32, the same set plus ckzg 2.1.5 (lock 2.1.8) |

Fix:

- `ops/macos_locked_requirements.py export` writes `uv export --locked` for the base dependencies plus
  the extras the app ships (`companion`, `evm`, `x402`, `desktop`), evaluates the markers for the
  target architecture and the embedded CPython, and admits this project's built wheels
  (`built_wheels.json`) only at the lock's version and by their recorded sha256.
- The build installs that file with `uv pip sync --require-hashes --no-build --strict` and then runs
  `macos_locked_requirements.py check`, which fails unless the installed set equals the pinned set in
  both directions (no extra package, no missing package, no other version).
- `xxhash` (also in the `companion` extra, as in the Linux bundle) is now shipped; `pip` is not.
- `uv.lock` had no installable Intel pins for two packages: ckzg 2.1.8 and cryptography 50.0.1 publish no
  Intel macOS wheel. `pyproject.toml` now constrains Intel macOS only
  (`platform_machine == 'x86_64' and sys_platform == 'darwin'`) to `ckzg<2.1.6` (resolves to 2.1.5, the
  last release with an Intel wheel, which 0.7.7 Intel already shipped) and `cryptography==50.0.2` (the
  version whose sdist the recorded Intel wheel is built from). `uv lock` added exactly those two versions;
  no other platform's pin moved (verified by diffing every `name version` pair).
- `ops/bundle_dependency_provenance.py` reads the declared set from `pyproject.toml` instead of the
  removed shell line, and keeps every version's digests for a package the lock pins twice (toolz,
  ckzg, cryptography).
- Both macOS package workflows run `check-app` on the built app, and the Intel job's helper venv and
  pip-audit now use the same locked export.

Dry runs with uv's resolver (`uv pip sync --dry-run --python-version 3.11 --no-build --require-hashes`):
arm64 installs all 81 locked packages wheel-only, including the recorded proxy-tools wheel. x86_64
installs all 80 non-cryptography packages wheel-only; its cryptography wheel only exists on the Intel
runner (it is built there by the recorded recipe), so that one was not dry-run here.

## Tests

- `tests/test_macos_arm64_native_components.py`: the lipo/file check on real Mach-O objects compiled
  with clang (pass for arm64 and universal; fail for an x86_64-only file whatever its name or mode,
  for an unreadable Mach-O, for a bundle without Mach-O, for a missing `lipo`); the build and the
  launcher refuse an interpreter that reports `x86_64` for an arm64 app and take a native one.
- `tests/test_macos_bundled_ollama_runtime.py`: ollama is thinned for both targets; a missing slice,
  an unreadable executable and a failed thin stop the build.
- `tests/test_macos_locked_requirements.py`: marker evaluation per target, exact-pin and hash parsing,
  built-wheel admission, the drift check in both directions (including the 0.7.7 case and the other
  architecture's fork), the build wiring, the Intel forks against the recorded cryptography build, and
  the real `uv export` for both architectures when `uv` is installed.

## Limits (not verifiable without a Mac)

- No macOS build was run. The new `macos-arm64.yml` job and the edited `macos-intel.yml` steps have
  not executed yet: they trigger on a pull request that touches these paths (or a dispatch once on
  the default branch).
- The census shows no x86_64-only code in the 0.7.7 arm64 app and a native launch contract, so which
  process made macOS show the notice on the reporting Mac is not established. Candidates: a wrapper
  app built from a local install with an Intel Python (entry 3), an Intel ffmpeg read through the video
  reader (now refused), or an older (0.5.0-era) arm64 bundle whose script main executable launched translated (fixed earlier by
  `LSArchitecturePriority`). `installer/bundle/macos_rosetta_probe.py` run on that Mac while VOOL is
  open names the translated process.
- `lipo -thin` on the real universal `ollama` and the resulting app's model replies were not exercised;
  the tests drive the build function with a header-reading `lipo` stand-in.
- The 17 failures and 5 errors of `tests/test_macos_app_packaging.py` on Linux are the same set on
  `main` (they need macOS `clang -arch`, `plutil`, `codesign`); that file runs in CI's macOS job.
