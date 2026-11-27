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

# codesign builds the signer's chain and refuses a root nobody trusts for code signing, so the
# runner (never a user's Mac) trusts this one root for code signing only.
cert="${RUNNER_TEMP}/vool-codesign.cert.pem"
security find-certificate -a -p -c "VOOL Publisher" "${keychain}" > "${cert}"
[[ -s "${cert}" ]] || { echo "the publisher certificate is not in the imported keychain" >&2; exit 1; }
sudo -n security add-trusted-cert -d -r trustRoot -p codeSign -k /Library/Keychains/System.keychain "${cert}"
rm -f "${cert}"

# A self-signed certificate is listed without -v (macOS does not trust it as a CA, and need not:
# the signature only has to stay the same from build to build).
identity="$(security find-identity -p codesigning "${keychain}" | awk '/^ *[0-9]+\) [0-9A-F]{40} /{print $2; exit}')"
[[ -n "${identity}" ]] || { echo "the publisher certificate has no code-signing identity" >&2; exit 1; }

# Prove the identity signs here, before the long build: sign a scratch copy of a system tool, the
# way the build will (its own HOME, the keychain named), and print codesign's own words if not.
echo "Valid code-signing identities:"
security find-identity -v -p codesigning "${keychain}" | sed 's/^/  /'
probe="${RUNNER_TEMP}/vool-sign-probe"
builder_home="${RUNNER_TEMP}/vool-builder-home"
mkdir -p "${builder_home}"
signed_with=""
for how in keychain-runner-home keychain-builder-home search-list-builder-home; do
  cp /usr/bin/true "${probe}"
  case "${how}" in
    keychain-runner-home) cmd=(codesign --force --sign "${identity}" --keychain "${keychain}" "${probe}") ;;
    keychain-builder-home) cmd=(env HOME="${builder_home}" codesign --force --sign "${identity}" --keychain "${keychain}" "${probe}") ;;
    search-list-builder-home) cmd=(env HOME="${builder_home}" codesign --force --sign "${identity}" "${probe}") ;;
  esac
  if "${cmd[@]}" 2>"${probe}.err" && codesign --verify --strict "${probe}" 2>>"${probe}.err"; then
    echo "  signing probe ${how}: ok"
    signed_with="${signed_with:-${how}}"
  else
    echo "  signing probe ${how}: FAILED: $(tr '\n' ' ' < "${probe}.err")"
  fi
done
rm -f "${probe}" "${probe}.err"
# The build signs the way the first probe does: the keychain named, under this step's HOME.
[[ "${signed_with}" == "keychain-runner-home" ]] || { echo "the publisher identity cannot sign on this runner (see the probes above)" >&2; exit 1; }
echo "VOOL_CODESIGN_IDENTITY=${identity}" >> "${GITHUB_ENV}"
echo "VOOL_CODESIGN_KEYCHAIN=${keychain}" >> "${GITHUB_ENV}"
echo "VOOL_CODESIGN_HOME=${HOME}" >> "${GITHUB_ENV}"
echo "Publisher signing identity ready: ${identity:0:8}..."
