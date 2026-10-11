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
STAGE="$(mktemp -d "${OUT}/.linux-build.XXXXXX")"
trap 'rm -rf -- "${STAGE}"' EXIT
NAME="vool-${VERSION}-linux-x86_64"
BUNDLE="${STAGE}/${NAME}"
mkdir -p "${BUNDLE}/app"
# Exact commit bytes: source, first-party plugins/skills and license notices.
git archive "${SHA}" apps core adapters storage network relay retrieval sandbox tools ops installer config skills plugins \
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
  --extra companion --extra evm --output-file "${BUNDLE}/requirements-linux.txt" >/dev/null)
uv pip sync --python "${BUNDLE}/python/bin/python3" --no-build --require-hashes --strict \
  --no-compile-bytecode "${BUNDLE}/requirements-linux.txt"
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
TARBALL="${NAME}.tar.gz"
tar -czf "${STAGE}/${TARBALL}" -C "${STAGE}" "${NAME}"
mv "${STAGE}/${TARBALL}" "${OUT}/${TARBALL}"
(cd "${OUT}" && sha256sum "${TARBALL}" > "${TARBALL}.sha256")
printf 'Built %s from %s\n' "${TARBALL}" "${SHA}"
