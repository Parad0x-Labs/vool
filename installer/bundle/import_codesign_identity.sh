#!/usr/bin/env bash
# Import VOOL's publisher signing certificate into a throwaway keychain on a macOS build runner,
# and hand its identity to build_macos_app.sh through GITHUB_ENV (VOOL_CODESIGN_IDENTITY).
#
# One certificate signs every Mac build, so macOS keeps VOOL's privacy permissions across updates
# (an ad-hoc signature is new on every build, and macOS drops each permission with it). The
# certificate is made once by the publisher (tools/make_vool_signing_certificate.sh) and kept as
# the repository secrets VOOL_CODESIGN_P12 (base64) and VOOL_CODESIGN_P12_PASSWORD.
#
# With no secret (a fork, a pull request from outside) the build is signed ad hoc and says so.
set -euo pipefail

if [[ -z "${VOOL_CODESIGN_P12:-}" ]]; then
  echo "::warning::No publisher signing certificate in this run: the Mac build is signed ad hoc, and macOS will drop VOOL's permissions on update."
  exit 0
fi
[[ -n "${VOOL_CODESIGN_P12_PASSWORD:-}" ]] || { echo "VOOL_CODESIGN_P12_PASSWORD is missing" >&2; exit 1; }
: "${RUNNER_TEMP:?RUNNER_TEMP is not set}"
: "${GITHUB_ENV:?GITHUB_ENV is not set}"

keychain="${RUNNER_TEMP}/vool-codesign.keychain-db"
p12="${RUNNER_TEMP}/vool-codesign.p12"
pass="$(/usr/bin/openssl rand -hex 24)"
trap 'rm -f "${p12}"' EXIT

security create-keychain -p "${pass}" "${keychain}"
security set-keychain-settings -lut 21600 "${keychain}"
security unlock-keychain -p "${pass}" "${keychain}"
printf '%s' "${VOOL_CODESIGN_P12}" | /usr/bin/base64 --decode > "${p12}"
security import "${p12}" -k "${keychain}" -f pkcs12 -P "${VOOL_CODESIGN_P12_PASSWORD}" -T /usr/bin/codesign >/dev/null
security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "${pass}" "${keychain}" >/dev/null
# shellcheck disable=SC2046
security list-keychains -d user -s "${keychain}" $(security list-keychains -d user | tr -d '"')

# A self-signed certificate is listed without -v (macOS does not trust it as a CA, and need not:
# the signature only has to stay the same from build to build).
identity="$(security find-identity -p codesigning "${keychain}" | awk '/^ *[0-9]+\) [0-9A-F]{40} /{print $2; exit}')"
[[ -n "${identity}" ]] || { echo "the publisher certificate has no code-signing identity" >&2; exit 1; }
echo "VOOL_CODESIGN_IDENTITY=${identity}" >> "${GITHUB_ENV}"
echo "VOOL_CODESIGN_KEYCHAIN=${keychain}" >> "${GITHUB_ENV}"
echo "Publisher signing identity ready: ${identity:0:8}..."
