#!/usr/bin/env bash
# Build a relocatable CPython + locked lean runtime on Linux x86_64.
# Usage: bash installer/bundle/build_linux_bundle.sh [--out DIRECTORY]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${ROOT}/dist"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT="${2:?--out requires a directory}"; shift 2 ;;
    -h|--help) printf '%s\n' 'Usage: build_linux_bundle.sh [--out DIRECTORY]'; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] \
  || { echo 'Build on Linux x86_64 (GitHub ubuntu-22.04), not a cross-build host.' >&2; exit 1; }
command -v uv >/dev/null || { echo 'uv is required for python-build-standalone.' >&2; exit 1; }
cd "${ROOT}"
[[ -z "$(git status --porcelain)" ]] || { echo 'Refusing dirty release source.' >&2; exit 1; }
SHA="$(git rev-parse HEAD)"
VERSION="$(git show "${SHA}:config/release/update_channel.json" | python3 -c 'import json; print(json.load(__import__("sys").stdin)["release_version"])')"
[[ "${VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.-]+)?$ ]] || { echo 'Invalid release version.' >&2; exit 1; }
mkdir -p "${OUT}"
OUT="$(cd "${OUT}" && pwd)"
NAME="vool-${VERSION}-linux-x86_64"
TARBALL="${NAME}.tar.gz"
[[ ! -e "${OUT}/${TARBALL}" && ! -e "${OUT}/${TARBALL}.sha256" ]] \
  || { echo 'Release output already exists; refusing to overwrite.' >&2; exit 1; }
STAGE="$(mktemp -d "${OUT}/.linux-build.XXXXXX")"
trap 'rm -rf -- "${STAGE}"' EXIT
BUNDLE="${STAGE}/${NAME}"
mkdir -p "${BUNDLE}/app"
# Exact commit bytes: source, first-party plugins/skills, the Media Studio worker and license notices.
git archive "${SHA}" apps core adapters storage network relay retrieval sandbox tools ops installer config skills plugins vendor/nebula-media \
  pyproject.toml uv.lock LICENSE README.md third_party | tar -xf - -C "${BUNDLE}/app"
# Same python-build-standalone approach as macOS SELF_CONTAINED; isolate uv's
# interpreter store so a developer's shared site-packages can never ride along.
export UV_PYTHON_INSTALL_DIR="${STAGE}/managed-python"
uv python install cpython-3.12.13-linux-x86_64-gnu
PY="$(uv python find --managed-python --system cpython-3.12.13-linux-x86_64-gnu)"
PY="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "${PY}")"
DIST="$(dirname "$(dirname "${PY}")")"
[[ ! -f "${DIST}/pyvenv.cfg" && -f "${DIST}/lib/python3.12/os.py" ]] || { echo 'Not a standalone CPython distribution.' >&2; exit 1; }
cp -a "${DIST}" "${BUNDLE}/python"
find "${BUNDLE}/python" -name EXTERNALLY-MANAGED -type f -delete
# Resolve ONLY from the checked-in lock. No training stack, GUI bindings, sdists,
# build tools, host Python, or install-time package download is needed by users.
(cd "${BUNDLE}/app" && uv export --locked --no-dev --no-default-groups --no-emit-project --no-header --no-annotate \
  --extra companion --extra evm --extra x402 --extra keys --output-file "${BUNDLE}/requirements-linux.txt" >/dev/null)
uv pip sync --python "${BUNDLE}/python/bin/python3" --no-build --require-hashes --strict \
  --no-compile-bytecode "${BUNDLE}/requirements-linux.txt"
# Private web helpers: pinned, SHA-256-checked tor and wireproxy into app/third_party/<tool>/linux-x64/
# (licence notice already in third_party/NOTICES). wireproxy is built with Go from the pinned module,
# or VOOL_WIREPROXY_FILE names a prebuilt file that must match the pinned SHA-256.
"${BUNDLE}/python/bin/python3" -I "${BUNDLE}/app/installer/private_web_helpers.py" --platform linux-x64 \
  --dest "${BUNDLE}/app" --cache "${STAGE}/private-web-cache" \
  ${VOOL_WIREPROXY_FILE:+--wireproxy-file "${VOOL_WIREPROXY_FILE}"}
# VOOL's own window (window_host.py) runs on the system python3, because the distribution builds its GTK
# and WebKitGTK bindings for that interpreter only (the .deb Depends on python3-gi, gir1.2-gtk-3.0 and
# gir1.2-webkit2-4.1). pywebview and its pure-Python dependencies ship here in window-lib/, exactly as
# uv.lock pins them, from wheels only and by digest: proxy-tools has no published wheel, so its wheel is
# built from the locked sdist by the recorded recipe and accepted by its recorded sha256.
"${BUNDLE}/python/bin/python3" -I "${BUNDLE}/app/ops/macos_build_inputs.py" proxy-wheel \
  --cache "${STAGE}/window-cache" --out "${STAGE}/window-wheels"
"${BUNDLE}/python/bin/python3" -I "${BUNDLE}/app/installer/bundle/linux_window_lib.py" requirements \
  --lock "${BUNDLE}/app/uv.lock" --built "${BUNDLE}/app/installer/bundle/built_wheels.json" \
  --out "${STAGE}/requirements-window.txt"
# --no-config: the project's resolver floors ([tool.uv] constraint-dependencies) are for the server's set;
# applied here they made uv refuse the hashed install over packages this folder does not carry.
# --link-mode=copy: the server's packages above are hard-linked from the same uv cache, so a package in both
# sets (typing_extensions) became one file under two names, a hard link in the tarball the .deb build refuses.
uv pip install --no-config --link-mode=copy --python "${BUNDLE}/python/bin/python3" --target "${BUNDLE}/window-lib" \
  --no-deps --no-build --require-hashes --no-compile-bytecode --find-links "${STAGE}/window-wheels" \
  -r "${STAGE}/requirements-window.txt"
# Never used by the Linux window: bottle's command-line script, the Windows WebView2 and Android
# binaries pywebview's one wheel carries for its other platforms, and uv's install lock.
rm -rf -- "${BUNDLE}/window-lib/bin" "${BUNDLE}/window-lib/webview/lib" "${BUNDLE}/window-lib/.lock"
"${BUNDLE}/python/bin/python3" -I "${BUNDLE}/app/installer/bundle/linux_window_lib.py" check \
  --target "${BUNDLE}/window-lib" --requirements "${STAGE}/requirements-window.txt"
# The window's whole import closure loads on this build host's system python3 (ubuntu-22.04: 3.10, the
# oldest the window supports) without GTK: anything beyond the standard library and window-lib fails here.
python3 -I -B -c 'import sys; sys.path[0:0] = [sys.argv[1] + "/window-lib", sys.argv[1] + "/app"]
import webview, webview.util  # noqa: E401
from installer.bundle import vool_window
from core.updater import linux, worker  # noqa: F401  (the update handoff)' "${BUNDLE}"
cp "${BUNDLE}/app/installer/bundle/linux/"* "${BUNDLE}/"
cp "${BUNDLE}/app/installer/assets/vool.png" "${BUNDLE}/vool.png"
printf '%s\n' "${SHA}" > "${BUNDLE}/SOURCE_COMMIT"
printf '%s\n' "${VERSION}" > "${BUNDLE}/VERSION"
python3 "${BUNDLE}/app/installer/stamp_build_source.py" --root "${ROOT}" --out "${BUNDLE}/app/config/build-source.json"
[[ "$(git rev-parse HEAD)" == "${SHA}" && -z "$(git status --porcelain)" ]] \
  || { echo 'Source changed during the build; refusing mixed provenance.' >&2; exit 1; }
# Probe with the relocated interpreter and staged source, never the checkout.
(cd "${BUNDLE}/app" && "${BUNDLE}/python/bin/python3" -E -s -c \
  'from core.runtime_dependency_preflight import preflight_served_imports_or_exit; preflight_served_imports_or_exit()')
chmod +x "${BUNDLE}/vool" "${BUNDLE}/install.sh" "${BUNDLE}/uninstall.sh"
# Remove build-time absolute paths from bytecode and package-manager executables.
find "${BUNDLE}" -type d -name __pycache__ -prune -exec rm -rf {} +
find "${BUNDLE}/python/bin" -type f ! -name 'python*' -delete
(cd "${BUNDLE}" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
tar -czf "${STAGE}/${TARBALL}" -C "${STAGE}" "${NAME}"
mv "${STAGE}/${TARBALL}" "${OUT}/${TARBALL}"
(cd "${OUT}" && sha256sum "${TARBALL}" > "${TARBALL}.sha256")
printf 'Built %s from %s\n' "${TARBALL}" "${SHA}"
