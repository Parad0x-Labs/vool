#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# VOOL.app — macOS counterpart to build_bundle.ps1 / vool.iss.
#
# Produces a double-clickable VOOL.app whose long-lived native host owns the exact local runtime
# lifecycle and opens the chat through installer/bundle/vool_window.py (pywebview -> WKWebView).
# Browser substitution is fail-closed unless an explicit development opt-in is set. The Dock icon
# is the VOOL mark from installer/assets/vool.icns.
#
# SCOPE: this wraps an existing local install (the repo + its .venv), which is what makes it
# testable today. It is NOT yet the fully self-contained bundle the Windows installer ships
# (embedded relocatable Python + bundled ollama binary + signed/notarized .dmg) — that needs
# python-build-standalone plus an Apple Developer ID for notarization, per installer/bundle/README.md.
#
# Usage:
#   bash installer/bundle/build_macos_app.sh                     # -> ./dist/VOOL.app
#   bash installer/bundle/build_macos_app.sh --out ~/Applications
#   bash installer/bundle/build_macos_app.sh --dmg               # also build dist/VOOL.dmg
#
# Provenance contract (2026-09-02): a build from a DIRTY tree is refused — uncommitted source
# used to ride inside the bundle while BUILD_MANIFEST claimed a committed SHA it did not contain
# (the demo artifact shipped source_tree_clean:false). VOOL_ALLOW_DIRTY_BUILD=1 is the explicit
# developer override: the build then stages the WORKING TREE and the manifest stamps the artifact
# NON-RELEASE ("release": false). Stable long-lived artifacts belong in a gitignored directory
# (e.g. artifacts/app), never only in /tmp.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
OUT_DIR="${PROJECT_ROOT}/dist"
MAKE_DMG=0
SELF_CONTAINED=0
BUNDLE_MODE="wrapper"
PY_VERSION="3.11"
# ── Architecture and deployment floor ─────────────────────────────────────────────────────
# ARCHITECTURE IS DECLARED, NEVER INHERITED. Every arch-bearing step below (the embedded
# CPython, the wheels, the Swift device-auth helper) used to take whatever the build host
# happened to be, and the plist advertised a floor nobody derived. Two consequences shipped in
# the 0.5.0 bundles at 45a22cf9:
#   * Info.plist carried no LSArchitecturePriority. The bundle's main executable is a shell
#     SCRIPT, so LaunchServices has no Mach-O header to read and FORGES the priority with
#     x86_64 first -- the app then launches under Rosetta on Apple Silicon, which is what
#     macOS reports as an "Intel-based component". Measured on macOS 26.5.1/M4: a script-main
#     bundle with no key launched proc_translated=1; the same bundle with the key launched
#     proc_translated=0.
#   * vool-devauth was compiled with no -target and inherited the host's macOS 26.0 while the
#     plist advertised 12.0, so on any older Mac it is a dyld failure, not a typed refusal.
# The floor is the highest deployment target the bundle's own dependencies actually require
# (ollama needs macOS 14.0); the gate at the end of this build re-derives it from the staged
# bytes and refuses the artifact if the declared value drifts from what was shipped.
TARGET_ARCH="${VOOL_TARGET_ARCH:-$(uname -m)}"
MACOS_MIN_VERSION="${VOOL_MACOS_MIN_VERSION:-14.0}"
# uv spells the Apple Silicon slice "aarch64" in a python request key and "aarch64-apple-darwin"
# as a wheel platform; macOS, lipo and Mach-O all call it "arm64". Both spellings are set from
# TARGET_ARCH in one place (verified against uv 0.11.1: `uv python list` keys, and the
# --python-platform value list) so no step has to reinvent the mapping.
case "${TARGET_ARCH}" in
  arm64)  UV_ARCH="aarch64"; UV_PLATFORM="aarch64-apple-darwin" ;;
  x86_64) UV_ARCH="x86_64";  UV_PLATFORM="x86_64-apple-darwin" ;;
  *)      UV_ARCH=""; UV_PLATFORM="" ;;
esac
# The same top-level runtime packages the Windows bundle ships (see build_bundle.ps1).
# `skills` (the native skill library core/native_skill_library.py resolves from the app root)
# and `plugins` (the first-party bundled packs, e.g. vool-database) are runtime CONTENT, not
# repositories of developer conveniences: a bundle without them ships a runtime whose catalog
# and library are silently empty (measured on the e1034c9b bundle: neither directory present).
SRC_PACKAGES=(apps core adapters storage network relay retrieval sandbox tools ops installer config skills plugins)

# Version the bundle from the SHARED release manifest so it tracks the release instead of a
# hardcoded 1.0. The Apple-numeric short version drops the channel suffix (0.4.2-closed-test ->
# 0.4.2). VOOL_BUILD_ID stamps a platform-tagged, per-commit id into Info.plist so the macOS
# release is uniquely identifiable versus the Windows .exe (which carries its own Inno AppId).
RELEASE_VERSION="$(python3 -c "import json;print(json.load(open('${PROJECT_ROOT}/config/release/update_channel.json')).get('release_version',''))" 2>/dev/null || echo "")"
SHORT_VERSION="${RELEASE_VERSION%%-*}"; [[ -n "${SHORT_VERSION}" ]] || SHORT_VERSION="0.0.0"

say() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# ── Build provenance gate ─────────────────────────────────────────────────────────────────
# The bundle's bytes must come from ONE commit. A dirty tree cannot prove that: uncommitted
# files would ride the staging copy while BUILD_MANIFEST and Info.plist claim the committed
# SHA. The probe covers tracked modifications AND uncommitted new files (ignored files are
# excluded — build outputs must never gate a build). With no git repo at all (fixture
# installs), provenance is unknowable: the build proceeds but the manifest stamps it
# honestly NON-RELEASE with source_tree_clean:false.
GIT_COMMIT_FULL="$(git -C "${PROJECT_ROOT}" rev-parse HEAD 2>/dev/null || echo nogit)"
GIT_COMMIT="${GIT_COMMIT_FULL:0:12}"
# The build id names the ARCHITECTURE. This lane's chosen approach is separate per-architecture
# artifacts; two of them carrying the same id and the same DMG filename are not separately
# labelled, and an operator holding both cannot tell them apart from the artifact alone.
# TARGET_ARCH is resolved above, before this line.
# NOTE: recomputed after argument parsing -- see below. TARGET_ARCH is only its default here.
VOOL_BUILD_ID="${RELEASE_VERSION:-unknown}+macos+${TARGET_ARCH}+${GIT_COMMIT}"
GIT_PRESENT=0; [[ "${GIT_COMMIT_FULL}" =~ ^[0-9a-f]{40}$ ]] && GIT_PRESENT=1
TREE_STATUS="$(git -C "${PROJECT_ROOT}" status --porcelain 2>/dev/null || true)"
SOURCE_TREE_CLEAN=false
RELEASE_BUILD=false
if [[ "${GIT_PRESENT}" -eq 1 && -z "${TREE_STATUS}" ]]; then
  SOURCE_TREE_CLEAN=true
  RELEASE_BUILD=true
fi
if [[ "${GIT_PRESENT}" -eq 1 && -n "${TREE_STATUS}" ]]; then
  if [[ "${VOOL_ALLOW_DIRTY_BUILD:-0}" == "1" ]]; then
    say "WARNING: VOOL_ALLOW_DIRTY_BUILD=1 — staging the WORKING TREE; uncommitted changes ride along."
    say "         This artifact is stamped NON-RELEASE (\"release\": false) and must never ship."
  else
    say "ERROR: refusing to build from a DIRTY source tree (${PROJECT_ROOT}):" >&2
    printf '  %s\n' ${TREE_STATUS} | head -10 >&2
    say "Uncommitted source would ride inside the bundle while the manifest claims ${GIT_COMMIT}." >&2
    say "Commit or stash first; or set VOOL_ALLOW_DIRTY_BUILD=1 to build an explicitly NON-RELEASE artifact." >&2
    exit 1
  fi
fi
[[ "${GIT_PRESENT}" -eq 1 ]] || say "NOTE: no git repo at ${PROJECT_ROOT} — provenance 'nogit'; this artifact is NON-RELEASE."

# Native-window capability probe, shared by the build-time preflight in verify_bundle() below.
# The wrapper LAUNCHER restates this logic inline (an unquoted heredoc cannot call parent-shell
# functions), so the pair is kept deliberately small and mirrored; the packaging tests execute
# the emitted launcher copy verbatim so a silent divergence fails CI.
webview_capable() { "$1" -c 'import webview' >/dev/null 2>&1; }

select_capable_wrapper_python() {
  # Same candidate order the shipped wrapper launcher uses at run time (install venv first, then a
  # system 3.10+). A candidate qualifies ONLY when it can import webview — version alone picked an
  # interpreter that could not open the window while one that could sat further down the list.
  local root="$1" c
  if [[ -x "${root}/.venv/bin/python" ]] && webview_capable "${root}/.venv/bin/python"; then
    printf '%s' "${root}/.venv/bin/python"
    return 0
  fi
  for c in python3.13 python3.12 python3.11 python3.10 python3; do
    command -v "$c" >/dev/null 2>&1 || continue
    if webview_capable "$(command -v "$c")"; then printf '%s' "$(command -v "$c")"; return 0; fi
  done
  return 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT_DIR="${2:?--out needs a directory}"; shift 2 ;;
    --dmg) MAKE_DMG=1; shift ;;
    --self-contained) SELF_CONTAINED=1; BUNDLE_MODE="self-contained"; shift ;;
    --python) PY_VERSION="${2:?--python needs a version}"; shift 2 ;;
    --arch) TARGET_ARCH="${2:?--arch needs arm64 or x86_64}"; shift 2 ;;
    --min-macos) MACOS_MIN_VERSION="${2:?--min-macos needs a version}"; shift 2 ;;
    -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
# Resolve the output directory ONCE, absolutely. verify_bundle's served-import probe runs the
# embedded interpreter from inside the staged app tree (`cd .../Resources/app && python3 ...`),
# so a relative --out made every path derived from it dangle after that cd and the probe failed
# with "No such file or directory" against a bundle that was in fact complete.
mkdir -p "${OUT_DIR}" || die "cannot create output directory ${OUT_DIR}"
OUT_DIR="$(cd "${OUT_DIR}" && pwd)"

# Wrapper/demo apps must not collide with a separately installed production VOOL.app in
# LaunchServices. Release/self-contained builds keep the production identity; callers may override
# either value explicitly for signed channels.
if [[ -n "${VOOL_MACOS_BUNDLE_ID:-}" ]]; then
  BUNDLE_IDENTIFIER="${VOOL_MACOS_BUNDLE_ID}"
elif [[ "${SELF_CONTAINED}" -eq 1 ]]; then
  BUNDLE_IDENTIFIER="ai.nulla.desktop"
else
  BUNDLE_IDENTIFIER="ai.nulla.desktop.local"
fi
if [[ -n "${VOOL_MACOS_URL_SCHEME:-}" ]]; then
  BUNDLE_URL_SCHEME="${VOOL_MACOS_URL_SCHEME}"
elif [[ "${SELF_CONTAINED}" -eq 1 ]]; then
  BUNDLE_URL_SCHEME="vool"
else
  BUNDLE_URL_SCHEME="vool-local"
fi
[[ "${BUNDLE_IDENTIFIER}" =~ ^[A-Za-z0-9.-]+$ ]] || die "invalid macOS bundle identifier: ${BUNDLE_IDENTIFIER}"
[[ "${BUNDLE_URL_SCHEME}" =~ ^[A-Za-z][A-Za-z0-9+.-]*$ ]] || die "invalid macOS URL scheme: ${BUNDLE_URL_SCHEME}"

[[ "$(uname)" == "Darwin" ]] || die "VOOL.app can only be built on macOS (got $(uname))."

case "${TARGET_ARCH}" in
  arm64|x86_64) : ;;
  *) die "unsupported --arch: ${TARGET_ARCH} (expected arm64 or x86_64)" ;;
esac
[[ "${MACOS_MIN_VERSION}" =~ ^[0-9]+(\.[0-9]+){0,2}$ ]] || die "invalid --min-macos: ${MACOS_MIN_VERSION}"
# CROSS-ARCH IS NOT SILENTLY ALLOWED. uv can fetch a CPython for the other architecture and pip
# can resolve its wheels, but nothing here can RUN that interpreter to compile bytecode or to
# probe the staged tree, so a cross build would be verified by nothing. Refuse it explicitly
# rather than emit an artifact whose checks were skipped.
HOST_ARCH="$(uname -m)"
# CROSS-ARCHITECTURE IS ALLOWED ONLY WHERE THE TARGET CAN ACTUALLY EXECUTE, AND IS LABELLED.
# The bundle's own interpreter has to RUN on the build machine -- it compiles the bytecode and
# answers the served-import conformance probe -- so a target that cannot execute here produces an
# artifact nothing verified. On an Apple Silicon host with Rosetta present, an x86_64 interpreter
# CAN execute, so refusing outright was stricter than necessary. What must never happen is such a
# build passing for a natively-verified one: it is opt-in, and the manifest says so in the
# artifact itself. A translated build is BUILT, never TESTED -- only genuine Intel hardware can
# promote it, and nothing here will claim otherwise.
CROSS_BUILD=0
BUILD_HOSTED_UNDER_TRANSLATION=false
if [[ "${TARGET_ARCH}" != "${HOST_ARCH}" ]]; then
  CROSS_BUILD=1
  if [[ "${HOST_ARCH}" == "arm64" && "${TARGET_ARCH}" == "x86_64" ]]; then
    [[ -x /usr/bin/arch ]] && /usr/bin/arch -x86_64 /usr/bin/true >/dev/null 2>&1 \
      || die "cross build to x86_64 needs Rosetta on this arm64 host, and it is not available (\`arch -x86_64\` failed). Install Rosetta, or build on an Intel runner."
    BUILD_HOSTED_UNDER_TRANSLATION=true
  else
    die "cross-architecture build refused: this host is ${HOST_ARCH} and cannot execute ${TARGET_ARCH} binaries, so the staged interpreter could not compile bytecode or answer the conformance probe here. Build ${TARGET_ARCH} on a ${TARGET_ARCH} runner."
  fi
  [[ "${VOOL_ALLOW_CROSS_ARCH_BUILD:-0}" == "1" ]] \
    || die "cross-architecture build refused: targeting ${TARGET_ARCH} on a ${HOST_ARCH} host would run every verification step under translation. Set VOOL_ALLOW_CROSS_ARCH_BUILD=1 to produce a clearly-labelled, NOT-natively-verified artifact."
  say "CROSS BUILD: ${TARGET_ARCH} on a ${HOST_ARCH} host, hosted under Rosetta."
  say "  the artifact will be stamped natively_verified:false -- it is BUILT, not TESTED."
fi
# The build id is recomputed HERE, after argument parsing: it is first assigned near the top of
# the script, before `--arch` has been seen, so a build invoked with an explicit --arch would
# otherwise stamp the default architecture into the id, the plist and the manifest.
VOOL_BUILD_ID="${RELEASE_VERSION:-unknown}+macos+${TARGET_ARCH}+${GIT_COMMIT}"
# The uv architecture spellings are derived near the top of this script too, from the DEFAULT
# TARGET_ARCH -- so `--arch x86_64` reached the plist and the manifest but not the interpreter
# request, and uv duly resolved the host's arm64 CPython. Anything derived from TARGET_ARCH has
# to be recomputed here, after parsing, or it silently describes the wrong architecture.
case "${TARGET_ARCH}" in
  arm64)  UV_ARCH="aarch64"; UV_PLATFORM="aarch64-apple-darwin" ;;
  x86_64) UV_ARCH="x86_64";  UV_PLATFORM="x86_64-apple-darwin" ;;
esac
say "Target: ${TARGET_ARCH}, minimum macOS ${MACOS_MIN_VERSION}"
say "Build id: ${VOOL_BUILD_ID}"

# macOS TCC: processes spawned by Finder/LaunchServices (and launchd agents) cannot read
# ~/Desktop, ~/Documents or ~/Downloads without explicit user permission. A VOOL.app whose
# PROJECT_ROOT lives in one of those fails at launch with
#   PermissionError: [Errno 1] Operation not permitted: <root>/.venv/pyvenv.cfg
# even though the same venv runs fine from Terminal. Refuse to build a known-broken bundle.
case "${PROJECT_ROOT}/" in
  "${HOME}/Desktop/"*|"${HOME}/Documents/"*|"${HOME}/Downloads/"*)
    say ""
    say "WARNING: ${PROJECT_ROOT}"
    say "         is inside a macOS TCC-protected folder (Desktop / Documents / Downloads)."
    say "         A Finder-launched VOOL.app — and the launchd keep-alive agent — cannot read"
    say "         files there, so the app dies with 'Operation not permitted' on the venv."
    say "         Install VOOL somewhere unprotected instead, e.g. ~/Applications/vool or ~/vool,"
    say "         and rebuild; or grant the launcher Full Disk Access in System Settings > Privacy."
    say ""
    [[ "${ALLOW_TCC_ROOT:-0}" == "1" ]] || die "refusing to build a bundle that cannot launch (set ALLOW_TCC_ROOT=1 to override)"
    say "ALLOW_TCC_ROOT=1 set — building anyway; expect launch failures until access is granted."
    ;;
esac

# Stage a relocatable runtime INSIDE the bundle: embedded CPython + lean deps + VOOL source +
# the ollama binary. This is the macOS analogue of build_bundle.ps1's staging step, so the app
# runs with no system Python, no repo checkout and no separately-installed Ollama.
stage_self_contained() {
  local res="$1"
  command -v uv >/dev/null 2>&1 || die "--self-contained needs uv (astral.sh) to supply a relocatable CPython"

  say "  [1/4] embedded CPython ${PY_VERSION} (python-build-standalone via uv)"
  local pybin dist_root
  # `--system` is load-bearing. Without it `uv python find` reports the first interpreter it
  # would USE, and that includes a `.venv` found in the working directory or any parent -- on the
  # build machine `~/.venv` sits above every worktree. The 2026-09-05 ALPHA_1 bundle was staged
  # from exactly that: its "embedded" tree was a venv shell (pyvenv.cfg, no stdlib) whose `import
  # os` resolved to ~/.local/share/uv/python/... on the machine that built it, so the artifact was
  # self-contained only there. A venv can never be embedded; only the standalone distribution can.
  # The request names the architecture. Without it uv resolves for the host, which is only
  # accidentally right -- and silently wrong the moment this script runs on another runner.
  local py_request="cpython-${PY_VERSION}-macos-${UV_ARCH}-none"
  pybin="$(uv python find --system "${py_request}" 2>/dev/null || true)"
  if [[ -z "${pybin}" ]]; then
    uv python install "${py_request}" >/dev/null 2>&1 || die "could not provision CPython ${PY_VERSION} for ${TARGET_ARCH}"
    pybin="$(uv python find --system "${py_request}" 2>/dev/null || true)"
  fi
  [[ -n "${pybin}" && -x "${pybin}" ]] || die "no usable CPython ${PY_VERSION} for ${TARGET_ARCH} from uv (requested ${py_request})"
  # CHECK THE SLICE BEFORE COPYING ~100 MB. uv resolved this path from a request that NAMES the
  # architecture, but a request that cannot be satisfied does not always surface as a failure --
  # an x86_64 build attempted with no x86_64 distribution available got all the way through
  # staging on an arm64 interpreter and was only stopped by the check on the staged copy. Failing
  # here says which architecture is actually missing, before any bytes move.
  local resolved_archs
  resolved_archs="$(lipo -archs "${pybin}" 2>/dev/null || echo unknown)"
  case " ${resolved_archs} " in
    *" ${TARGET_ARCH} "*) : ;;
    *) die "uv resolved CPython ${PY_VERSION} to ${pybin}, which is ${resolved_archs}, not ${TARGET_ARCH}. Install the target distribution first: uv python install ${py_request}" ;;
  esac
  dist_root="$(cd "$(dirname "${pybin}")/.." && pwd)"
  [[ ! -f "${dist_root}/pyvenv.cfg" ]] \
    || die "uv resolved CPython ${PY_VERSION} to a virtual environment (${dist_root}); a venv shell carries no standard library and cannot be embedded"
  compgen -G "${dist_root}/lib/python*/os.py" >/dev/null \
    || die "no standard library under ${dist_root}; refusing to embed an interpreter that resolves its stdlib elsewhere"
  rm -rf "${res}/python"; mkdir -p "${res}/python"
  (cd "${dist_root}" && tar cf - .) | (cd "${res}/python" && tar xf -) || die "copying the CPython tree failed"
  local embedded="${res}/python/bin/python3"
  [[ -x "${embedded}" ]] || die "embedded python missing at ${embedded}"
  # uv's own selection is not taken on trust: read the slices off the staged interpreter.
  local embedded_archs
  embedded_archs="$(lipo -archs "${embedded}" 2>/dev/null || echo unknown)"
  case " ${embedded_archs} " in
    *" ${TARGET_ARCH} "*) : ;;
    *) die "embedded interpreter is ${embedded_archs}, which does not carry the target ${TARGET_ARCH}" ;;
  esac
  say "      embedded interpreter slices: ${embedded_archs}"

  # The shared uv distribution is NOT guaranteed clean — anything pip-installed into it directly
  # (torch, transformers, playwright, pyarrow ... ~1.5 GB here) would otherwise ride along. Start
  # from an empty site-packages so the bundle carries only the lean runtime set.
  local sp
  for sp in "${res}"/python/lib/python*/site-packages; do
    [[ -d "${sp}" ]] && rm -rf "${sp:?}"/* 2>/dev/null || true
  done
  # python-build-standalone distributions now carry PEP 668 EXTERNALLY-MANAGED markers, which
  # make `uv pip install --python <embedded>` refuse to touch the interpreter. The staged tree
  # is OUR copy, not uv's shared install -- the marker describes the wrong owner here. Remove
  # it from the copy only; uv's managed distribution on the build machine is never modified.
  find "${res}/python" -name "EXTERNALLY-MANAGED" -type f -delete 2>/dev/null || true
  # Drop what a runtime bundle never needs: headers, static libs, test suites, IDLE, caches.
  rm -rf "${res}/python/include" "${res}/python/share/man" 2>/dev/null || true
  find "${res}/python" -type d \( -name test -o -name tests -o -name idlelib -o -name __pycache__ \) \
       -prune -exec rm -rf {} + 2>/dev/null || true
  find "${res}/python" -name '*.a' -delete 2>/dev/null || true

  say "  [2/4] lean runtime deps (no torch/transformers — those are training-only)"
  # Mirrors build_bundle.ps1's list; pythonnet is Windows-only (WebView2), so on macOS pywebview
  # rides pyobjc/WKWebView instead, pulled in as pywebview's own platform dependency. uv installs
  # straight into the embedded interpreter, so no pip bootstrap is needed after the wipe above.
  # --no-compile-bytecode: install-time .pyc embeds the source mtime, which would make two
  # builds of one SHA differ byte-for-byte; bytecode is (re)generated deterministically below.
  # OPTIONAL LOCAL WHEELHOUSE. Set VOOL_BUNDLE_WHEELHOUSE to a directory of .whl files to
  # build without reaching PyPI -- required on a machine whose supply-chain posture forbids
  # package downloads, and the only way to build this bundle air-gapped. Combine with
  # UV_OFFLINE=1 to make a network fetch impossible rather than merely unnecessary. Wheels are
  # resolved from the directory first; anything not there still comes from the cache or the
  # index exactly as before, so an unset variable changes nothing.
  wheelhouse_args=()
  if [[ -n "${VOOL_BUNDLE_WHEELHOUSE:-}" ]]; then
    [[ -d "${VOOL_BUNDLE_WHEELHOUSE}" ]] \
      || die "VOOL_BUNDLE_WHEELHOUSE is set but is not a directory: ${VOOL_BUNDLE_WHEELHOUSE}"
    wheelhouse_args=(--find-links "${VOOL_BUNDLE_WHEELHOUSE}")
    say "  local wheelhouse: ${VOOL_BUNDLE_WHEELHOUSE}"
  fi
  # DEPENDENCY PROVENANCE. A package manager accepting a wheel is not provenance: during this
  # lane's preflight, two pins were supplied from wheels RE-ZIPPED out of unpacked ~/.cache/uv
  # directories -- contents intact, RECORD verified, and their own sha256 matching nothing in
  # uv.lock -- and both uv and pip installed them without complaint. Every bundle therefore
  # carries a manifest grading each artifact against the lock, and a RELEASE build refuses
  # anything that is not release-grade.
  #
  # Set VOOL_REQUIRE_RELEASE_PROVENANCE=1 for a release build. Without it the build proceeds
  # and the manifest records the grade honestly, which is what a diagnostic build needs.
  if [[ -n "${VOOL_BUNDLE_WHEELHOUSE:-}" ]]; then
    # Audited HERE so a wheelhouse that cannot be vouched for stops the build before a single
    # dependency is installed. The manifest is parked outside the bundle because step [3/4]
    # `rm -rf`s the staged app tree; it is copied in after that, below.
    PROVENANCE_TMP="$(mktemp -t vool-provenance)"
    if [[ "${VOOL_REQUIRE_RELEASE_PROVENANCE:-0}" == "1" ]]; then
      PYTHONPATH="${PROJECT_ROOT}" "${SYS_PY:-python3}" -m ops.bundle_dependency_provenance \
          --wheelhouse "${VOOL_BUNDLE_WHEELHOUSE}" --out "${PROVENANCE_TMP}" --strict >/dev/null \
        || die "dependency provenance refused the wheelhouse under VOOL_REQUIRE_RELEASE_PROVENANCE=1; see ${PROVENANCE_TMP}"
    else
      PYTHONPATH="${PROJECT_ROOT}" "${SYS_PY:-python3}" -m ops.bundle_dependency_provenance \
          --wheelhouse "${VOOL_BUNDLE_WHEELHOUSE}" --out "${PROVENANCE_TMP}" >/dev/null \
        || die "dependency provenance could not audit the wheelhouse"
    fi
  fi
  # The wallet's EVM lanes resolve eth-abi/eth-utils/eth-account at use (core/wallet/evm.py)
  # and answer a typed wallet_dependency_unavailable refusal when any is missing. The bundle
  # ships them so the artifact's Base/Ethereum journeys are capability-real, not refusals;
  # the packaging test fails the build if this list drops them again.
  # bash-3.2-safe: an empty array under set -u is "unbound" unless guarded
  # --python-platform pins wheel selection to the target. A host cache holding the other
  # architecture's wheels cannot contaminate the bundle: an arm64 CPython that loads an
  # x86_64-only extension is an ImportError at the user's first launch, not at build time.
  # --no-build: THE BUNDLE IS ASSEMBLED FROM WHEELS, NEVER COMPILED FROM SOURCE. Without it uv is
  # free to pick a version that has no wheel for the target and fall back to an sdist -- which on
  # this machine meant it ran maturin and cargo to build cryptography from source during an
  # x86_64 build. That is arbitrary build code executing on the build host, it is not
  # reproducible, and it silently needs a Rust toolchain. With --no-build the resolver simply
  # picks versions that ship a wheel for the target, which it can: the same dependency set
  # resolves wheel-only for both architectures.
  uv pip install --python "${embedded}" --python-platform "${UV_PLATFORM}" --no-build \
      --no-compile-bytecode --quiet \
      ${wheelhouse_args[@]+"${wheelhouse_args[@]}"} \
      pydantic "cryptography>=50.0.0" "anyio>=4.14.2" requests pynacl keyring psutil pyyaml \
      "starlette>=1.3.1,<2.0" "uvicorn>=0.30,<1.0" solders pywebview "pypdf==6.19.0" "xlrd==2.0.1" "reportlab==5.0.1" "markdown-it-py==4.2.0" \
      zstandard \
      "eth-abi>=6.0" "eth-utils>=6.0" "eth-account>=0.13" \
    || die "lean dependency install failed"

  # Deterministic bytecode for the embedded runtime: wipe any install-time caches, then compile
  # in unchecked-hash invalidation mode — the .pyc embeds the source HASH, not a timestamp, so
  # two builds of one SHA produce identical bytes. (The staged APP tree is baked in step [3/4],
  # after it actually exists.)
  say "  [2b/4] deterministic bytecode (hash-based .pyc, no install-time mtimes)"
  find "${res}/python" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
  PYTHONHASHSEED=0 "${embedded}" -m compileall --invalidation-mode unchecked-hash -q -j 0 \
    "${res}/python" >/dev/null \
    || die "deterministic bytecode compilation failed"

  say "  [3/4] VOOL source"
  rm -rf "${res}/app"; mkdir -p "${res}/app"
  # EVERY DECLARED PACKAGE MUST EXIST, AND THE CHECK RUNS BEFORE EITHER STAGING PATH.
  #
  # `channels` was deleted from the tree in e7b1bd31 and stayed in this list, so every
  # self-contained build since then died in the middle of stage 3 with `fatal: pathspec
  # 'channels' did not match any files` -- a git error that names neither this list nor the
  # bundle. The two paths also disagreed about it: `git archive` fails on a missing pathspec
  # while the working-tree copy below skips it silently, so the same drift could instead have
  # shipped a bundle quietly missing a package. One check, ahead of both, resolves that.
  local missing_pkgs=() pkg
  for pkg in "${SRC_PACKAGES[@]}"; do
    git -C "${PROJECT_ROOT}" cat-file -e "${GIT_COMMIT_FULL}:${pkg}" 2>/dev/null \
      || missing_pkgs+=("${pkg}")
  done
  if (( ${#missing_pkgs[@]} )); then
    die "SRC_PACKAGES names ${#missing_pkgs[@]} package(s) absent from commit ${GIT_COMMIT_FULL}: ${missing_pkgs[*]}. Update SRC_PACKAGES in this script -- a bundle must never be staged from a stale package list."
  fi
  if [[ "${SOURCE_TREE_CLEAN}" == "true" ]]; then
    # Release bytes: extract the EXACT commit. Even a clean probed tree could drift between the
    # probe and the copy (a racing editor, a stray save); `git archive <sha>` makes "bundle bytes
    # == commit bytes" structural instead of trusted.
    say "        bytes from commit ${GIT_COMMIT_FULL} (git archive — no worktree drift)"
    git -C "${PROJECT_ROOT}" archive "${GIT_COMMIT_FULL}" -- "${SRC_PACKAGES[@]}" \
      | (cd "${res}/app" && tar xf -) || die "could not stage commit ${GIT_COMMIT_FULL} into the bundle"
  else
    # Non-release override / nogit fixture: copy the working tree, exactly as the manifest admits.
    say "        NON-RELEASE: copying the WORKING TREE (uncommitted changes ride along)"
    local d
    for d in "${SRC_PACKAGES[@]}"; do
      [[ -d "${PROJECT_ROOT}/${d}" ]] && cp -R "${PROJECT_ROOT}/${d}" "${res}/app/${d}"
    done
  fi
  find "${res}/app" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
  # Bake the app tree's bytecode NOW that it exists ([2b] runs before staging): unchecked-hash
  # .pyc, PYTHONHASHSEED pinned for byte-identical marshalled constants. The first boot then
  # imports without writing anything into the (possibly read-only) bundle.
  PYTHONHASHSEED=0 "${embedded}" -m compileall --invalidation-mode unchecked-hash -q -j 0 \
    "${res}/app" >/dev/null \
    || die "app-tree bytecode bake failed"
  # A bundle has no .git: stamp config/build-source.json so the packaged daemon can report its
  # EXACT source SHA at /healthz and /api/runtime/version, and the native supervisor's
  # exact-identity gate can verify the runtime it owns (the launcher exports the same SHA from
  # the plist). Without the stamp the daemon reports commit=unknown and the window never opens.
  python3 "${PROJECT_ROOT}/installer/stamp_build_source.py" \
    --root "${PROJECT_ROOT}" \
    --out "${res}/app/config/build-source.json" \
    || die "could not stamp config/build-source.json into the bundled app"
  # The dependency provenance manifest, audited before install, now lands beside the build
  # stamp so the shipped artifact carries its own answer to "where did these bytes come from".
  if [[ -n "${PROVENANCE_TMP:-}" && -f "${PROVENANCE_TMP}" ]]; then
    cp "${PROVENANCE_TMP}" "${res}/app/config/dependency-provenance.json" \
      || die "could not place the dependency provenance manifest into the bundle"
    rm -f "${PROVENANCE_TMP}"
    say "  dependency provenance: Contents/Resources/app/config/dependency-provenance.json"
  fi

  say "  [4/4] bundled ollama"
  mkdir -p "${res}/ollama"
  local oll resolved
  oll="$(command -v ollama || true)"
  if [[ -n "${oll}" ]]; then
    resolved="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "${oll}" 2>/dev/null || echo "${oll}")"
    if [[ -f "${resolved}" ]]; then
      cp -f "${resolved}" "${res}/ollama/ollama" && chmod +x "${res}/ollama/ollama"
      say "        bundled: ${resolved}"
    fi
  fi
  [[ -x "${res}/ollama/ollama" ]] || say "        WARNING: no ollama binary bundled; the app will use a system ollama if present"

  # ---- BUILDER-FOOTPRINT SCRUB (must run before any code signature) ------------------------
  # A distributed bundle must not carry the BUILD MACHINE's home directory: compiled .pyc
  # bytecode records the path it was compiled at, uv's Python carries its install prefix in
  # _sysconfigdata and libpython's install name, and console-script shebangs point into the
  # builder's home (found by the release scan of the 0.6.0-beta DMG: 3,907 files embedded
  # /Users/<builder>/...). Four repairs, all before signing -- signed bytes never change.
  local scrubbed_prefix="/install"
  # (a) uv's prefix inside _sysconfigdata: replace FIRST so the full recompile below
  #     regenerates its .pyc from the scrubbed source.
  local sysconf
  sysconf="$(find "${res}/python/lib" -maxdepth 3 -name '_sysconfigdata*.py' -type f | head -1 || true)"
  if [[ -n "${sysconf}" ]]; then
    sed -i '' "s|${HOME}|${scrubbed_prefix}|g" "${sysconf}" \
      || die "could not scrub the builder home out of ${sysconf}"
    rm -f "${sysconf}c"
    say "        scrubbed _sysconfigdata prefix"
  fi
  # (b) libpython's install name (and every dependent load of it) -> @rpath.
  local libpython old_id macho
  for libpython in "${res}/python/lib"/libpython*.dylib; do
    [[ -f "${libpython}" ]] || continue
    old_id="$(otool -D "${libpython}" 2>/dev/null | tail -1 | sed 's/^ *//')"
    if [[ -n "${old_id}" && "${old_id}" == "${HOME}"* ]]; then
      install_name_tool -id "@rpath/$(basename "${libpython}")" "${libpython}" \
        || die "could not rewrite libpython install name"
      while IFS= read -r -d '' macho; do
        install_name_tool -change "${old_id}" "@rpath/$(basename "${libpython}")" "${macho}" 2>/dev/null || true
      done < <(find "${res}/python" -type f \( -name 'python3*' -o -name '*.dylib' -o -name '*.so' \) -print0)
      say "        rewrote libpython install name to @rpath"
    fi
  done
  # (c) console scripts in python/bin embed the interpreter path they were generated with, which
  #     is under $HOME whenever the artifact is built to (or the interpreter staged from) a home-
  #     nested path. TWO forms exist and both must die:
  #       * the classic absolute shebang  -> rewritten to an env-based shebang;
  #       * uv's newer #!/bin/sh exec-wrapper, whose SECOND line is a python string constant
  #         ('''exec' '<abs interpreter>' "$0" "$@") -- invisible to a shebang rewrite, carried
  #         into any bytecode compiled from the script, and a text grep over line 1 misses it.
  #         The quoted interpreter path becomes 'python3' (sh resolves it from PATH; python
  #         still sees a harmless string). The scrubbed scripts are then recompiled by (d), so
  #         bytecode regenerated from them carries no home bytes either.
  while IFS= read -r -d '' macho; do
    if ! head -c 400 "${macho}" 2>/dev/null | grep -q -F "${HOME}"; then
      continue
    fi
    if [[ "$(head -1 "${macho}")" == "#!/bin/sh" ]]; then
      sed -i '' "s|'${HOME}[^']*'|'python3'|g" "${macho}" \
        || die "could not scrub the exec-wrapper interpreter path in ${macho}"
    else
      sed -i '' "1s|.*|#!/usr/bin/env python3|" "${macho}"
    fi
  done < <(find "${res}/python/bin" -maxdepth 1 -type f -print0)
  # (d) ONE full recompile of python + app bytecode with the recorded prefix REWRITTEN:
  #     -s strips this build's absolute path, -p stamps the canonical install location.
  #     Hash-based invalidation as before, so the first boot never rewrites the bundle.
  PYTHONHASHSEED=0 "${embedded}" -m compileall --invalidation-mode unchecked-hash -q -f -j 0 \
    -s "${APP}" -p /Applications/VOOL.app "${res}" >/dev/null \
    || die "footprint-scrubbing recompile failed"
  # (e) THE GATE: no file in the bundle may carry the builder's home bytes. Binary-safe
  #     (--binary-files=text) because .pyc/.dylib are binary; a text-only grep is exactly
  #     how the 0.6.0-beta leak slipped through.
  if grep -r -a -l -F "${HOME}" "${APP}" >/dev/null 2>&1; then
    grep -r -a -l -F "${HOME}" "${APP}" | head -5 >&2
    die "the bundle still carries the builder's home path (${HOME}); refusing to ship"
  fi
  say "        builder-footprint scrub verified (no ${HOME} bytes in the bundle)"
}


APP="${OUT_DIR}/VOOL.app"
ICNS_SRC="${PROJECT_ROOT}/installer/assets/vool.icns"

say "Project root: ${PROJECT_ROOT}"
say "Building:     ${APP}"

rm -rf "${APP}"
mkdir -p "${APP}/Contents/MacOS" "${APP}/Contents/Resources"

# --- icon ---------------------------------------------------------------------------------
if [[ -f "${ICNS_SRC}" ]]; then
  cp -f "${ICNS_SRC}" "${APP}/Contents/Resources/vool.icns"
else
  say "WARNING: ${ICNS_SRC} missing; the app will use the generic icon."
fi

# --- external updater helper --------------------------------------------------------------
# The signed-manifest updater's OUT-OF-PROCESS swap helper (2026-09-01 amendment): it
# waits for this app to exit, swaps the bundle atomically, keeps the prior for
# rollback, and relaunches. Shipped inside the bundle so the daemon can hand the final
# swap to a process that is not the one being replaced.
UPDATE_HELPER_SRC="${PROJECT_ROOT}/installer/update/mac_update_helper.sh"
if [[ -f "${UPDATE_HELPER_SRC}" ]]; then
  mkdir -p "${APP}/Contents/Resources/update"
  cp -f "${UPDATE_HELPER_SRC}" "${APP}/Contents/Resources/update/mac_update_helper.sh"
  chmod +x "${APP}/Contents/Resources/update/mac_update_helper.sh"
  say "  bundled update helper: Contents/Resources/update/mac_update_helper.sh"
else
  say "WARNING: ${UPDATE_HELPER_SRC} missing; the app cannot hand off self-update swaps."
fi

# --- Info.plist ---------------------------------------------------------------------------
cat >"${APP}/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>VOOL</string>
  <key>CFBundleDisplayName</key>
  <string>VOOL</string>
  <key>CFBundleExecutable</key>
  <string>VOOL</string>
  <key>CFBundleIdentifier</key>
  <string>${BUNDLE_IDENTIFIER}</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>${SHORT_VERSION}</string>
  <key>CFBundleVersion</key>
  <string>${SHORT_VERSION}</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleIconFile</key>
  <string>vool</string>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>LSMinimumSystemVersion</key>
  <string>${MACOS_MIN_VERSION}</string>
  <!-- THE LAUNCH CONTRACT. CFBundleExecutable is a shell script, so LaunchServices has no
       Mach-O header to read; with this key absent it forges (x86_64, arm64) and launches the
       app under Rosetta on Apple Silicon. Declaring the single architecture this bundle was
       actually staged for is what keeps the process tree native. -->
  <key>LSArchitecturePriority</key>
  <array>
    <string>${TARGET_ARCH}</string>
  </array>
  <!-- Purpose strings: macOS kills the process outright when a TCC-gated API is reached with
       no usage description. The voice lane reaches both; without these the first use is a
       crash, not a prompt. Present regardless of whether the lane is enabled at runtime. -->
  <key>NSMicrophoneUsageDescription</key>
  <string>VOOL uses the microphone only when you start a voice request, and the audio never leaves this Mac unless you send it.</string>
  <key>NSSpeechRecognitionUsageDescription</key>
  <string>VOOL transcribes what you dictate so it can act on it. Transcription runs on this Mac by default.</string>
  <key>CFBundleURLTypes</key>
  <array>
    <dict>
      <key>CFBundleURLName</key>
      <string>${BUNDLE_IDENTIFIER}.${BUNDLE_URL_SCHEME}</string>
      <key>CFBundleURLSchemes</key>
      <array>
        <string>${BUNDLE_URL_SCHEME}</string>
      </array>
    </dict>
  </array>
  <key>NULLABuildId</key>
  <string>${VOOL_BUILD_ID}</string>
  <key>NULLASourceSHA</key>
  <string>${GIT_COMMIT_FULL}</string>
  <key>NULLAReleasePlatform</key>
  <string>macos</string>
  <key>NULLABundleMode</key>
  <string>${BUNDLE_MODE}</string>
  <key>NULLAUpdateHelper</key>
  <string>Contents/Resources/update/mac_update_helper.sh</string>
</dict>
</plist>
PLIST
say "  bundle version ${SHORT_VERSION}  (build id ${VOOL_BUILD_ID})"

# --- build manifest -----------------------------------------------------------------------
# Machine-readable provenance beside the human-facing plist stamps: exact source SHA, release
# version, UTC build time, mode, whether the source tree carried uncommitted changes, and the
# release verdict. The clean/release flags are the provenance gate's verdict computed BEFORE any
# byte is staged: a real git tree must be committed-clean to be release:true; an override build
# or a nogit fixture install stamps release:false + source_tree_clean:false (honest unknown).
BUILD_TIME_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
cat >"${APP}/Contents/Resources/BUILD_MANIFEST.json" <<MANIFEST
{
  "schema": "vool-build-manifest/1",
  "exact_sha": "${GIT_COMMIT_FULL}",
  "version": "${RELEASE_VERSION}",
  "build_id": "${VOOL_BUILD_ID}",
  "build_time_utc": "${BUILD_TIME_UTC}",
  "bundle_mode": "${BUNDLE_MODE}",
  "platform": "macos",
  "arch": "${TARGET_ARCH}",
  "macos_min_version": "${MACOS_MIN_VERSION}",
  "build_host_arch": "${HOST_ARCH}",
  "built_under_translation": ${BUILD_HOSTED_UNDER_TRANSLATION},
  "natively_verified": $([[ "${CROSS_BUILD}" -eq 1 ]] && echo false || echo true),
  "source_tree_clean": ${SOURCE_TREE_CLEAN},
  "release": ${RELEASE_BUILD}
}
MANIFEST
python3 -c "import json,sys;json.load(open('${APP}/Contents/Resources/BUILD_MANIFEST.json'))" \
  || die "BUILD_MANIFEST.json is not valid JSON"
say "  build manifest: ${GIT_COMMIT_FULL:0:12} @ ${BUILD_TIME_UTC} (clean=${SOURCE_TREE_CLEAN} release=${RELEASE_BUILD})"

# --- launcher -----------------------------------------------------------------------------
if [[ "${SELF_CONTAINED}" -eq 1 ]]; then
  say "Staging a self-contained runtime inside the bundle..."
  stage_self_contained "${APP}/Contents/Resources"
  # The wallet's device-authentication helper (Touch ID / password through a Keychain user-presence item). Compiled
  # here so end users need no Swift toolchain; the runtime finds it at Contents/Resources/bin/vool-devauth. Without
  # swiftc on the build host the bundle still works by PIN and reports export as unavailable, typed.
  if [[ -x /usr/bin/swiftc ]]; then
    say "  [2c/4] device-authentication helper (LocalAuthentication + Security)"
    PYTHONPATH="${PROJECT_ROOT}" "${SYS_PY:-python3}" -m core.wallet.device_auth --build "${APP}/Contents/Resources/bin/vool-devauth" \
      --arch "${TARGET_ARCH}" --min-macos "${MACOS_MIN_VERSION}" >/dev/null \
      || die "the device-authentication helper could not be compiled"
  else
    say "  [2c/4] no swiftc on this build host: the device-authentication helper is not shipped (export reports unavailable)"
  fi

  # Self-contained launcher: embedded Python + bundled ollama. Needs no repo, no system Python
  # and no separate Ollama install. All writable state lives under ~/Library/Application Support.
  cat >"${APP}/Contents/MacOS/VOOL" <<'LAUNCHER'
#!/usr/bin/env bash
set -uo pipefail
RES="$(cd "$(dirname "$0")/../Resources" && pwd)"
SUPPORT="${HOME}/Library/Application Support/VOOL"
mkdir -p "${SUPPORT}"
exec >>"${SUPPORT}/app.log" 2>&1
echo "--- VOOL.app (self-contained) launch $(date) ---"

PY="${RES}/python/bin/python3"
export PYTHONPATH="${RES}/app"
# Respect a caller-provided VOOL_HOME. The runtime's own contract (core/runtime_paths.py)
# honours the env var; overwriting it here silently defeated every isolated home a launcher
# or validation rig set, routing the daemon's data to the installed home instead (measured
# 2026-09-08: an isolated acceptance home was never read and never written).
#
# UPGRADE REUSE (2026-09-19): a user upgrading from a pre-rename build has their whole
# profile — database, conversations, credentials, wallet references, permissions — under
# ~/Library/Application Support/NULLA/runtime. The canonical home is chosen ONLY when it
# already exists or no legacy home does; a legacy-only machine keeps its data (never a
# second empty profile beside the old one). When BOTH exist the canonical home wins
# deterministically (same rule as core.runtime_paths.user_runtime_default and the
# .vool_local/.nulla_local reuse) and the decision names both directories in the log, so
# the conflict is visible instead of silent. See docs/VOOL_IDENTITY_COMPATIBILITY_MAP.md.
if [[ -z "${VOOL_HOME:-}" ]]; then
  CANONICAL_RUNTIME="${SUPPORT}/runtime"
  LEGACY_RUNTIME="${HOME}/Library/Application Support/NULLA/runtime"
  if [[ -d "${CANONICAL_RUNTIME}" ]]; then
    export VOOL_HOME="${CANONICAL_RUNTIME}"
    if [[ -d "${LEGACY_RUNTIME}" ]]; then
      echo "NOTE: profiles exist at both ${CANONICAL_RUNTIME} (used) and ${LEGACY_RUNTIME} (not used)."
    fi
  elif [[ -d "${LEGACY_RUNTIME}" ]]; then
    export VOOL_HOME="${LEGACY_RUNTIME}"
    echo "reusing the pre-rename runtime home: ${LEGACY_RUNTIME}"
  else
    export VOOL_HOME="${CANONICAL_RUNTIME}"
  fi
fi
# Reuse an existing Ollama model store when the machine already has one, so a first launch does
# not re-download several GB; otherwise keep the models inside the app's own support dir.
if [[ -d "${HOME}/.ollama/models" ]]; then
  export OLLAMA_MODELS="${HOME}/.ollama/models"
else
  export OLLAMA_MODELS="${SUPPORT}/models"
fi
mkdir -p "${VOOL_HOME}" "${OLLAMA_MODELS}"

# Local Ollama, resolved the way the runtime resolves it (installer/bundle/launcher_ollama.sh, mirroring
# core/ollama_endpoint.py). The bundled server starts only for the default local endpoint when nothing answers
# there; a launch that points the endpoint elsewhere (an isolated profile at a dead port) is left alone.
source "${RES}/app/installer/bundle/launcher_ollama.sh"
vool_ensure_bundled_ollama "${RES}/ollama/ollama" "${SUPPORT}/ollama.log"

# Runtime ownership belongs to the long-lived native host. The shell launcher survives only
# through this exec, so it must never detach the API and disappear. The host rejects a stale or
# unknown occupant, starts an owned child, gates on /healthz + exact identity, and tears it down.
# The full exact source identity is baked separately from the human-readable build id.
BUNDLED_COMMIT="$(defaults read "${RES}/../Info.plist" NULLASourceSHA 2>/dev/null)"
export VOOL_PROJECT_ROOT="${RES}/app"
export VOOL_RUNTIME_MODE="self-contained"
# A SELF-CONTAINED BUNDLE MUST NOT REWRITE ITSELF AT RUNTIME. The app tree is compiled at
# build time with hash-based .pyc (step [2b/4]), so runtime bytecode writing gains nothing --
# and it costs the bundle's integrity: measured on this build, driving the app wrote 73
# __pycache__ directories and 1,475 .pyc files INTO Contents/Resources/app, changing the
# bundle's own digest after first launch. Once the bundle is signed that is a broken seal, and
# it also makes "same commit -> same bundle" untrue for anyone verifying a shipped artifact.
export PYTHONDONTWRITEBYTECODE=1
export VOOL_EXPECTED_COMMIT="${BUNDLED_COMMIT}"
export VOOL_NATIVE_API_URL="${VOOL_NATIVE_API_URL:-http://127.0.0.1:11435}"
export VOOL_NATIVE_REQUIRE_OWNED_RUNTIME="1"

echo "opening the native window"
exec "${PY}" "${RES}/app/installer/bundle/vool_window.py"
LAUNCHER

else
# Wrapper launcher: drives an existing local install (repo + .venv).
# The native host starts, identity-gates, owns, and stops that install's runtime.
# Record the install root WITHOUT baking the build machine's username: prefer a $HOME-relative path
# (re-expanded on the user's machine at run time); fall back to absolute only if the repo is outside $HOME.
if [[ "${PROJECT_ROOT}" == "${HOME}/"* ]]; then
  PROJECT_ROOT_TEMPLATE="\${HOME}/${PROJECT_ROOT#"${HOME}"/}"
else
  PROJECT_ROOT_TEMPLATE="${PROJECT_ROOT}"
fi
cat >"${APP}/Contents/MacOS/VOOL" <<LAUNCHER
#!/usr/bin/env bash
set -uo pipefail
# Resolve the install root at run time: prefer this .app's own location (relocatable when the bundle
# sits in the repo's dist/), else the recorded \$HOME-relative path. No build-machine path/username baked.
SELF_ROOT="\$(cd "\$(dirname "\$0")/../../../.." 2>/dev/null && pwd || true)"
if [[ -n "\${SELF_ROOT}" && -f "\${SELF_ROOT}/installer/bundle/vool_window.py" ]]; then
  PROJECT_ROOT="\${SELF_ROOT}"
else
  PROJECT_ROOT="${PROJECT_ROOT_TEMPLATE}"
fi
LOG_DIR="\${HOME}/Library/Application Support/VOOL"
mkdir -p "\${LOG_DIR}"
exec >>"\${LOG_DIR}/app.log" 2>&1
echo "--- VOOL.app launch \$(date) ---"

# Resolve a python by CAPABILITY, not version: the native window needs pywebview. A candidate
# qualifies only when it can 'import webview' — preferring 3.12 over a webview-capable 3.9 sent
# launches into the browser fallback on machines that already had a working interpreter installed.
py_ok() { "\$1" -c 'import webview' >/dev/null 2>&1; }
PY=""
VENV_PY="\${PROJECT_ROOT}/.venv/bin/python"
if [[ -x "\${VENV_PY}" ]] && py_ok "\${VENV_PY}"; then
  PY="\${VENV_PY}"
else
  for c in python3.13 python3.12 python3.11 python3.10 python3; do
    command -v "\$c" >/dev/null 2>&1 || continue
    cand="\$(command -v "\$c")"
    if py_ok "\${cand}"; then PY="\${cand}"; break; fi
  done
fi
if [[ -z "\${PY}" ]]; then
  if [[ "\${VOOL_ALLOW_BROWSER_FALLBACK:-0}" == "1" ]]; then
    PY="\${VENV_PY}"
    [[ -x "\${PY}" ]] || PY="python3"
    echo "WARNING: no interpreter with pywebview found; degraded browser-fallback launch (VOOL_ALLOW_BROWSER_FALLBACK=1)"
  else
    echo "ERROR: no interpreter that can 'import webview' found for \${PROJECT_ROOT}; native window impossible."
    exit 1
  fi
fi

# The long-lived native host owns runtime startup and teardown. Export the live checkout identity;
# the host refuses to create a window unless /healthz stamps this exact commit.
export VOOL_PROJECT_ROOT="\${PROJECT_ROOT}"
export VOOL_RUNTIME_MODE="wrapper"
export VOOL_EXPECTED_COMMIT="\$(git -C "\${PROJECT_ROOT}" rev-parse HEAD 2>/dev/null || true)"
export VOOL_NATIVE_API_URL="\${VOOL_NATIVE_API_URL:-http://127.0.0.1:11435}"
export VOOL_NATIVE_REQUIRE_OWNED_RUNTIME="1"
# The notification helper this bundle ships (core/notifications_macos.py); the host runs it while the window runs.
export VOOL_NOTIFICATIONS_HELPER="\$(cd "\$(dirname "\$0")/.." && pwd)/Resources/bin/VOOL Notifications.app"

echo "opening the native window"
exec "\${PY}" "\${PROJECT_ROOT}/installer/bundle/vool_window.py"
LAUNCHER
fi

chmod +x "${APP}/Contents/MacOS/VOOL"
touch "${APP}"  # nudge Finder to refresh the bundle icon

# --- macOS notification helper ------------------------------------------------------------
# VOOL Notifications.app (core/notifications_macos.py): the small Swift app that hands VOOL's notification requests
# to the macOS notification center. Built for both bundle modes and signed ad hoc under its own identifier. A build
# host without swiftc ships the bundle without it, and Settings then says macOS notifications are unavailable.
if [[ -x /usr/bin/swiftc && -x /usr/bin/codesign ]]; then
  say "  notification helper (UserNotifications) -> Contents/Resources/bin/VOOL Notifications.app"
  PYTHONPATH="${PROJECT_ROOT}" "${SYS_PY:-python3}" -m core.notifications_macos --build "${APP}/Contents/Resources/bin/VOOL Notifications.app" \
    --bundle-id "${BUNDLE_IDENTIFIER}.notifications" --arch "${TARGET_ARCH}" --min-macos "${MACOS_MIN_VERSION}" >/dev/null \
    || die "the notification helper could not be compiled"
else
  say "  no swiftc on this build host: the notification helper is not shipped"
fi

# --- verify -------------------------------------------------------------------------------
# The verdict below must describe THIS mode's launch contract, not just plist syntax. A wrapper
# bundle built in a never-installed worktree used to print OK while its launcher pointed at files
# that could not exist; each arm now verifies what its own launcher will actually name.
verify_bundle() {
  plutil -lint "${APP}/Contents/Info.plist" >/dev/null || die "Info.plist failed plutil -lint"
  [[ -x "${APP}/Contents/MacOS/VOOL" ]] || die "launcher is not executable"
  case "${BUNDLE_MODE}" in
    wrapper|self-contained) : ;;
    *) die "unknown bundle mode: ${BUNDLE_MODE}" ;;
  esac
  if [[ "${SELF_CONTAINED}" -eq 1 ]]; then
    local embedded="${APP}/Contents/Resources/python/bin/python3"
    [[ -x "${embedded}" ]] || die "self-contained bundle is missing its embedded python at ${embedded}"
    [[ -f "${APP}/Contents/Resources/app/installer/bundle/vool_window.py" ]] \
      || die "self-contained bundle is missing installer/bundle/vool_window.py"
    [[ -f "${APP}/Contents/Resources/app/installer/bundle/native_runtime_supervisor.py" ]] \
      || die "self-contained bundle is missing installer/bundle/native_runtime_supervisor.py"
    "${embedded}" -c 'import webview' >/dev/null 2>&1 \
      || die "embedded python at ${embedded} cannot import webview; the native window cannot start"
    # SELF-CONTAINMENT IS STRUCTURAL, NOT ASSUMED. The interpreter must resolve its own standard
    # library from inside Contents/Resources/python. A copied venv passes every check above (it
    # runs, it imports webview) and still depends on the build machine's Python install.
    [[ ! -f "${APP}/Contents/Resources/python/pyvenv.cfg" ]] \
      || die "embedded python is a virtual-environment shell (pyvenv.cfg present); it resolves its stdlib outside the bundle"
    "${embedded}" -c 'import os, sys; prefix = os.path.realpath(sys.prefix) + os.sep; stdlib = os.path.realpath(os.__file__); assert stdlib.startswith(prefix), (stdlib, prefix)' >/dev/null 2>&1 \
      || die "embedded python at ${embedded} imports its standard library from outside Contents/Resources/python; the bundle is not self-contained"
    # SERVED-IMPORT CONFORMANCE — the bundle must satisfy the SAME import closure the
    # runtime's boot gate checks (core/runtime_dependency_preflight.py). The Command Registry
    # imports every group module on first use, so a dependency missing from the lean list above
    # does not surface at build time and does not surface at boot -- it surfaces as an HTTP 500
    # on the first request across the registry. Since that gate now refuses to serve, a bundle
    # that fails this check would not start at all.
    #
    # This runs the registry build in the embedded interpreter against the STAGED app tree, with
    # a throwaway home and file-backed key storage so the check can never touch operator
    # credential state or raise a Keychain prompt. It is the reason the lean list is not a
    # hand-maintained guess: drift fails the build here, naming the missing module.
    local conf_home conf_out
    conf_home="$(mktemp -d "${TMPDIR:-/tmp}/vool-bundle-conformance-XXXXXX")"
    if ! conf_out="$(
        cd "${APP}/Contents/Resources/app" && \
        VOOL_HOME="${conf_home}/home" \
        HOME="${conf_home}/home" \
        VOOL_KEY_STORAGE_MODE=file \
        VOOL_KEY_PASSPHRASE=bundle-import-conformance \
        VOOL_CREDENTIAL_STORE=vault \
        PYTHONDONTWRITEBYTECODE=1 \
        "${embedded}" -c 'from core.command_registry.registry import registry; registry()' 2>&1
      )"; then
      rm -rf "${conf_home}"
      die "embedded python cannot build the Command Registry -- this bundle would refuse to serve at boot. Add the missing distribution to the lean dependency install above. Child output:
${conf_out}"
    fi
    rm -rf "${conf_home}"
    say "  served-import conformance: the embedded runtime builds the Command Registry"
  else
    # Wrapper: this app drives an existing local install — verify THAT install can satisfy it.
    [[ -f "${PROJECT_ROOT}/installer/bundle/vool_window.py" ]] \
      || die "wrapper bundle needs ${PROJECT_ROOT}/installer/bundle/vool_window.py in the install it wraps"
    [[ -f "${PROJECT_ROOT}/installer/bundle/native_runtime_supervisor.py" ]] \
      || die "wrapper bundle needs ${PROJECT_ROOT}/installer/bundle/native_runtime_supervisor.py in the install it wraps"
    if [[ ! -f "${PROJECT_ROOT}/Start_VOOL.sh" ]]; then
      die "wrapper bundle cannot start its runtime: ${PROJECT_ROOT}/Start_VOOL.sh is missing (gitignored output of installer/install_vool.sh). Run the installer against this tree first, or rebuild with --self-contained."
    fi
    local capable
    capable="$(select_capable_wrapper_python "${PROJECT_ROOT}")" || true
    # (the "|| true" matters: with set -e, a failing selection would otherwise kill the build
    # silently BEFORE the legible die below ever runs)
    [[ -n "${capable}" ]] || die "no interpreter that can 'import webview' found for ${PROJECT_ROOT}; the native window cannot start. Install pywebview for a candidate interpreter (python3.13/3.12/3.11/3.10/3 or .venv), or rebuild with --self-contained."
    say "  wrapper preflight: runtime starter + native interpreter (${capable}) verified"
  fi

  # ARCHITECTURE / DEPLOYMENT GATE. The checks above prove the bundle RUNS on this machine;
  # they say nothing about which architecture it runs AS, or whether it can start at all on the
  # oldest macOS it advertises. Both shipped wrong at 45a22cf9: the app launched translated
  # because a script main executable makes LaunchServices forge an x86_64-first priority, and
  # vool-devauth required macOS 26.0 inside a bundle advertising 12.0. The gate re-derives both
  # from the staged bytes and names the exact offending path.
  local gate="${SCRIPT_DIR}/macos_arch_gate.py"
  if [[ -f "${gate}" ]]; then
    "${SYS_PY:-python3}" "${gate}" "${APP}" --arch "${TARGET_ARCH}" --min-macos "${MACOS_MIN_VERSION}" \
      || die "the bundle failed the architecture/deployment gate (see the paths above)"
    # Ship the census BESIDE the artifact. The gate answers pass/fail; the census is the per-file
    # record -- slices, deployment targets, code signing, resolved load paths, reachability --
    # that lets someone audit this artifact later without rebuilding it. Produced by the build so
    # every artifact has one, rather than by a manual run so only the audited one does.
    local census="${SCRIPT_DIR}/macos_arch_census.py"
    if [[ -f "${census}" ]]; then
      # Beside the app, derived from APP rather than OUT_DIR: verify_bundle is also driven by the
      # packaging tests against hand-staged bundles that never define OUT_DIR, and an unset OUT_DIR
      # sent this write to /ARCH_CENSUS.json, which fails closed and took the whole verify with it.
      # It must also stay OUTSIDE the .app -- writing into Contents would change the bundle's own
      # digest after the immutability contract has been checked.
      local census_out; census_out="$(cd "$(dirname "${APP}")" && pwd)/ARCH_CENSUS.json"
      "${SYS_PY:-python3}" "${census}" "${APP}" --json "${census_out}" >/dev/null \
        || die "the architecture census could not be produced for ${APP}"
      say "  architecture census: ${census_out}"
    fi
  else
    die "architecture gate missing at ${gate}"
  fi
}
verify_bundle

# --- ad-hoc code signature ------------------------------------------------------------------
# The whole bundle signs ad hoc (no identity, stable local seal): every Mach-O inside --
# the shell launcher carries none, but the embedded interpreter, wheels' extension modules,
# the ollama binary, the devauth helper and the notification app do. A signed bundle makes
# Gatekeeper's first-open flow the predictable "unidentified developer" warning with a
# working right-click -> Open, rather than the harsher treatment fully-unsigned downloads
# can get on macOS 13+. Notarization still needs an Apple Developer ID (see README).
if [[ -x /usr/bin/codesign ]]; then
  if codesign --force --deep --sign - "${APP}" >/dev/null 2>&1; then
    say "  ad-hoc code signature applied to the bundle"
    codesign --verify --deep --strict "${APP}" >/dev/null 2>&1 \
      || die "ad-hoc signature did not verify after signing"
  else
    say "WARNING: ad-hoc codesign failed; the bundle ships unsigned."
  fi
else
  say "WARNING: codesign unavailable; the bundle ships unsigned."
fi

# Build-manifest contract: the bundle must carry a manifest that stamps THIS build's exact SHA,
# a real mode and a UTC build time, plus the provenance verdict. Kept OUTSIDE verify_bundle because
# the packaging tests exercise verify_bundle against hand-staged partial bundles that intentionally
# lack a manifest.
python3 - "${APP}/Contents/Resources/BUILD_MANIFEST.json" "${GIT_COMMIT_FULL}" <<'MANIFEST_CHECK' \
  || die "BUILD_MANIFEST.json is missing, invalid, or stamps a different SHA than this build"
import json, sys
m = json.load(open(sys.argv[1]))
assert m["exact_sha"] == sys.argv[2], f"manifest sha {m['exact_sha']!r} != build sha {sys.argv[2]!r}"
assert m["bundle_mode"] in ("wrapper", "self-contained")
assert m["build_time_utc"] and m["version"]
assert isinstance(m["source_tree_clean"], bool)
assert isinstance(m["release"], bool), "manifest must stamp a release verdict"
assert (not m["release"]) or m["source_tree_clean"], "a release artifact must come from a clean tree"
MANIFEST_CHECK

# Build-identity agreement (healthz <-> manifest): the packaged daemon reports its identity at
# /healthz from config/build-source.json + config/release/update_channel.json. Both must stamp the
# SAME sha and version as BUILD_MANIFEST, byte for byte, or /healthz would misattribute the build.
if [[ "${SELF_CONTAINED}" -eq 1 && "${GIT_PRESENT}" -eq 1 ]]; then
  python3 - "${APP}/Contents/Resources" "${GIT_COMMIT_FULL}" "${RELEASE_VERSION}" <<'AGREEMENT' \
    || die "bundled build-source/release stamps disagree with BUILD_MANIFEST; /healthz would misattribute this build"
import json, sys
res, sha, version = sys.argv[1], sys.argv[2], sys.argv[3]
source = json.load(open(f"{res}/app/config/build-source.json"))
assert source.get("commit_full") == sha, \
    f"bundled commit_full {source.get('commit_full')!r} != manifest sha {sha!r}"
channel = json.load(open(f"{res}/app/config/release/update_channel.json"))
assert channel.get("release_version") == version, \
    f"bundled release_version {channel.get('release_version')!r} != manifest version {version!r}"
manifest = json.load(open(f"{res}/BUILD_MANIFEST.json"))
assert manifest["exact_sha"] == sha and manifest["version"] == version
AGREEMENT
  say "  identity agreement: build-source / update_channel / BUILD_MANIFEST all stamp ${GIT_COMMIT_FULL:0:12} @ ${RELEASE_VERSION}"
fi

say "OK: ${APP}  [${BUNDLE_MODE}]"

# --- optional dmg -------------------------------------------------------------------------
if [[ "${MAKE_DMG}" -eq 1 ]]; then
  if [[ "${SELF_CONTAINED}" -ne 1 ]]; then
    say "WARNING: wrapper-mode DMG — this drives a local repo install and is NOT relocatable/distributable."
    say "         For a DMG you can hand to other machines, rebuild with --self-contained."
  fi
  DMG="${OUT_DIR}/VOOL-${TARGET_ARCH}.dmg"
  rm -f "${DMG}"
  STAGE="$(mktemp -d)"
  cp -R "${APP}" "${STAGE}/"
  ln -s /Applications "${STAGE}/Applications" 2>/dev/null || true
  hdiutil create -volname "VOOL" -srcfolder "${STAGE}" -ov -format UDZO "${DMG}" >/dev/null \
    || die "hdiutil failed to build ${DMG}"
  rm -rf "${STAGE}"
  say "OK: ${DMG}  (unsigned — notarization needs an Apple Developer ID; see installer/bundle/README.md)"
fi

say ""
if [[ "${SELF_CONTAINED}" -eq 1 ]]; then
  say "Double-click ${APP} to launch VOOL in a native window (fully self-contained)."
else
  say "${APP} is a WRAPPER around the local install at ${PROJECT_ROOT}:"
  say "launching it requires that install's Start_VOOL.sh and a webview-capable interpreter — both were verified at build time."
  say "Double-click ${APP} to launch VOOL in a native window."
fi
