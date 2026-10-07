#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Developer ID signing + notarization for a built VOOL.app. FAIL-CLOSED.
#
# A release DMG leaves this script only when ALL of these hold:
#   * every Mach-O in the bundle, every nested bundle and the app itself are signed with a
#     "Developer ID Application" identity, the hardened runtime and a secure timestamp;
#   * the signatures verify strictly and carry the pinned Team ID;
#   * Apple's notary service ACCEPTED the app, and the ticket is stapled to it;
#   * the DMG built from that stapled app is itself signed, notarized, stapled;
#   * Gatekeeper (spctl) reports "Notarized Developer ID" for both.
# Any missing credential or failed step exits non-zero, deletes the partial DMG, and writes
# no receipt. Without a receipt, installer/release_sign.py refuses to seal the DMG into a
# release.
#
# Usage:
#   sign_macos_release.sh --preflight
#   sign_macos_release.sh --app dist/VOOL.app --release-dir DIR --name VOOL-0.7.0-macos-arm64
#
# Credentials come from the environment and the keychain, never from arguments or files in
# this repository (see docs/releases/0.7-readiness.md, "Signed releases"):
#   VOOL_MACOS_SIGN_IDENTITY   the "Developer ID Application: ... (TEAMID)" identity name, or
#                              its 40-hex SHA-1 hash, as `security find-identity` lists it
#   VOOL_MACOS_TEAM_ID         the 10-character Apple Team ID the signature must carry
#   VOOL_NOTARY_PROFILE        a notarytool keychain profile (xcrun notarytool store-credentials)
#     or, instead of a profile (CI):
#   VOOL_NOTARY_KEY_FILE, VOOL_NOTARY_KEY_ID, VOOL_NOTARY_ISSUER
#                              an App Store Connect API key (.p8 outside the repo), its key id
#                              and issuer id
#   VOOL_SIGN_KEYCHAIN         optional: the keychain holding the identity (CI temp keychain)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ENTITLEMENTS="${SCRIPT_DIR}/VOOL.entitlements"

say() { printf '%s\n' "$*"; }
die() { printf 'RELEASE SIGNING REFUSED: %s\n' "$*" >&2; exit 1; }

MODE=""
APP=""
RELEASE_DIR=""
NAME=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --preflight) MODE="preflight"; shift ;;
    --app) APP="${2:?--app needs a path}"; MODE="sign"; shift 2 ;;
    --release-dir) RELEASE_DIR="${2:?--release-dir needs a directory}"; shift 2 ;;
    --name) NAME="${2:?--name needs an artifact base name}"; shift 2 ;;
    -h|--help) sed -n '2,32p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
[[ -n "${MODE}" ]] || die "nothing to do: pass --preflight, or --app with --release-dir and --name"

# ── credential preflight ────────────────────────────────────────────────────────────────
# Runs first in both modes, and the bundle build runs it before its long staging phase, so a
# missing certificate costs seconds rather than a full build.
KEYCHAIN_ARGS=()
NOTARY_AUTH=()
preflight() {
  [[ "$(uname)" == "Darwin" ]] || die "Developer ID signing and notarization need macOS (got $(uname))."
  local tool
  for tool in /usr/bin/codesign /usr/bin/security /usr/bin/xcrun /usr/bin/hdiutil /usr/bin/ditto /usr/sbin/spctl; do
    [[ -x "${tool}" ]] || die "${tool} is missing; install the Xcode command line tools."
  done
  xcrun --find notarytool >/dev/null 2>&1 || die "xcrun cannot find notarytool (Xcode 13 or later is required)."
  xcrun --find stapler >/dev/null 2>&1 || die "xcrun cannot find stapler."
  [[ -f "${ENTITLEMENTS}" ]] || die "entitlements file missing at ${ENTITLEMENTS}"

  local identity="${VOOL_MACOS_SIGN_IDENTITY:-}" team="${VOOL_MACOS_TEAM_ID:-}"
  [[ -n "${identity}" ]] || die "VOOL_MACOS_SIGN_IDENTITY is not set (the Developer ID Application identity)."
  [[ "${identity}" != "-" ]] || die "an ad-hoc identity ('-') is not a release signature."
  [[ "${team}" =~ ^[A-Z0-9]{10}$ ]] || die "VOOL_MACOS_TEAM_ID must be the 10-character Apple Team ID."

  if [[ -n "${VOOL_SIGN_KEYCHAIN:-}" ]]; then
    [[ -f "${VOOL_SIGN_KEYCHAIN}" ]] || die "VOOL_SIGN_KEYCHAIN names a keychain that does not exist."
    KEYCHAIN_ARGS=(--keychain "${VOOL_SIGN_KEYCHAIN}")
  fi
  # The identity must be a VALID code-signing identity in the keychain, of the Developer ID
  # Application kind, for the pinned team. "Apple Development", "Mac Developer" and
  # "Developer ID Installer" certificates sign fine locally and are rejected by the notary
  # service or by Gatekeeper, so they are refused here.
  local listing match
  listing="$(security find-identity -v -p codesigning ${VOOL_SIGN_KEYCHAIN:+"${VOOL_SIGN_KEYCHAIN}"} 2>/dev/null || true)"
  match="$(printf '%s\n' "${listing}" | grep -F -- "${identity}" | head -1 || true)"
  [[ -n "${match}" ]] || die "no valid code-signing identity matching VOOL_MACOS_SIGN_IDENTITY in the keychain (expired, revoked, or missing its private key?)."
  [[ "${match}" == *'"Developer ID Application: '* ]] || die "the identity is not a Developer ID Application certificate; Gatekeeper accepts only that kind outside the App Store."
  [[ "${match}" == *"(${team})\""* ]] || die "the identity does not belong to Team ID ${team}."

  if [[ -n "${VOOL_NOTARY_PROFILE:-}" ]]; then
    NOTARY_AUTH=(--keychain-profile "${VOOL_NOTARY_PROFILE}")
    [[ -z "${VOOL_SIGN_KEYCHAIN:-}" ]] || NOTARY_AUTH+=(--keychain "${VOOL_SIGN_KEYCHAIN}")
  elif [[ -n "${VOOL_NOTARY_KEY_FILE:-}${VOOL_NOTARY_KEY_ID:-}${VOOL_NOTARY_ISSUER:-}" ]]; then
    [[ -n "${VOOL_NOTARY_KEY_FILE:-}" && -n "${VOOL_NOTARY_KEY_ID:-}" && -n "${VOOL_NOTARY_ISSUER:-}" ]] \
      || die "an App Store Connect API key needs all of VOOL_NOTARY_KEY_FILE, VOOL_NOTARY_KEY_ID and VOOL_NOTARY_ISSUER."
    [[ -f "${VOOL_NOTARY_KEY_FILE}" ]] || die "VOOL_NOTARY_KEY_FILE does not exist."
    local key_real; key_real="$(cd "$(dirname "${VOOL_NOTARY_KEY_FILE}")" && pwd)/$(basename "${VOOL_NOTARY_KEY_FILE}")"
    [[ "${key_real}" != "${PROJECT_ROOT}/"* ]] || die "the notary API key must live OUTSIDE this repository."
    NOTARY_AUTH=(--key "${VOOL_NOTARY_KEY_FILE}" --key-id "${VOOL_NOTARY_KEY_ID}" --issuer "${VOOL_NOTARY_ISSUER}")
  else
    die "no notary credentials: set VOOL_NOTARY_PROFILE (keychain profile) or the VOOL_NOTARY_KEY_* trio."
  fi
  # A cheap authenticated call proves the notary credentials work before anything is uploaded.
  xcrun notarytool history "${NOTARY_AUTH[@]}" --output-format json >/dev/null 2>&1 \
    || die "notarytool could not authenticate with the configured notary credentials."
  say "  release-signing preflight: Developer ID identity for team ${team} and notary credentials verified"
}

preflight
[[ "${MODE}" == "preflight" ]] && exit 0

# ── signing ─────────────────────────────────────────────────────────────────────────────
[[ -d "${APP}" && -f "${APP}/Contents/Info.plist" ]] || die "no app bundle at ${APP}"
[[ -n "${RELEASE_DIR}" ]] || die "--release-dir is required"
[[ "${NAME}" =~ ^VOOL-[0-9A-Za-z.+-]+-macos-(arm64|x86_64)$ ]] || die "--name must look like VOOL-<version>-macos-<arm64|x86_64> (got '${NAME}')"
APP="$(cd "$(dirname "${APP}")" && pwd)/$(basename "${APP}")"
mkdir -p "${RELEASE_DIR}"; RELEASE_DIR="$(cd "${RELEASE_DIR}" && pwd)"
case "${RELEASE_DIR}/" in "${APP}/"*) die "--release-dir must not be inside the app bundle" ;; esac

# Only a clean-tree build may become a release: the manifest is the build's own verdict.
python3 - "${APP}/Contents/Resources/BUILD_MANIFEST.json" <<'PY' || die "BUILD_MANIFEST.json does not stamp a clean-tree release build; refusing to sign it as a release"
import json, sys
m = json.load(open(sys.argv[1]))
assert m.get("release") is True and m.get("source_tree_clean") is True
assert m.get("distribution_signing") == "developer-id", "build was not made with --release-sign"
PY

IDENTITY="${VOOL_MACOS_SIGN_IDENTITY}"
TEAM_ID="${VOOL_MACOS_TEAM_ID}"
DMG="${RELEASE_DIR}/${NAME}.dmg"
RECEIPT="${DMG}.signing.json"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/vool-sign-XXXXXX")"
SUCCESS=0
cleanup() {
  rm -rf "${WORK}"
  if [[ "${SUCCESS}" -ne 1 ]]; then
    rm -f "${DMG}" "${RECEIPT}"
  fi
}
trap cleanup EXIT
rm -f "${DMG}" "${RECEIPT}"

sign_one() {  # sign_one <path> [entitlements]
  local args=(--force --timestamp --options runtime --sign "${IDENTITY}" ${KEYCHAIN_ARGS[@]+"${KEYCHAIN_ARGS[@]}"})
  [[ -z "${2:-}" ]] || args+=(--entitlements "$2")
  codesign "${args[@]}" "$1" >/dev/null 2>"${WORK}/codesign.err" \
    || die "codesign failed on ${1#"${APP}/"}: $(tr '\n' ' ' <"${WORK}/codesign.err")"
}

# Inside-out: nested code must be signed before whatever seals it. `codesign --deep` is not
# used for signing (Apple: it applies one set of options to everything and skips code it does
# not recognise as nested), only for verifying.
say "  signing Mach-O files (Developer ID, hardened runtime, secure timestamp)"
# Mach-O detection by magic number, not by name: a native module shipped without a .so or
# .dylib suffix must be signed too (the notary service rejects any unsigned Mach-O it finds).
python3 - "${APP}" >"${WORK}/macho.lst" <<'PY' || die "could not enumerate the bundle's Mach-O files"
import os, struct, sys
root = sys.argv[1]
THIN = {b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe"}
found = []
for base, _dirs, files in os.walk(root):
    for name in files:
        path = os.path.join(base, name)
        if os.path.islink(path):
            continue
        with open(path, "rb") as fh:
            head = fh.read(8)
        if head[:4] in THIN:
            found.append(path)
        elif head[:4] == b"\xca\xfe\xba\xbe" and 0 < struct.unpack(">I", head[4:8])[0] < 20:
            found.append(path)  # universal binary (a Java class file shares the magic, not the count)
for path in sorted(found, key=lambda p: (-p.count(os.sep), p)):
    print(f"{path.count(os.sep)}\t{path}")
PY
MACHO_COUNT=0
while IFS=$'\t' read -r _depth f; do
  case "${f}" in
    "${APP}/Contents/Resources/python/bin/python3"*) sign_one "${f}" "${ENTITLEMENTS}" ;;
    *) sign_one "${f}" ;;
  esac
  MACHO_COUNT=$((MACHO_COUNT + 1))
done <"${WORK}/macho.lst"
[[ "${MACHO_COUNT}" -gt 0 ]] || die "found no Mach-O code in ${APP}; this is not a built bundle"

# Nested bundles (the notification helper app, any framework), deepest first, then the app.
while IFS= read -r -d '' b; do
  sign_one "${b}"
done < <(find "${APP}/Contents" -depth -type d \( -name '*.app' -o -name '*.framework' -o -name '*.bundle' -o -name '*.xpc' \) -print0)
sign_one "${APP}" "${ENTITLEMENTS}"
say "  signed ${MACHO_COUNT} Mach-O files, nested bundles and the app"

verify_signature() {  # verify_signature <path>
  codesign --verify --deep --strict --verbose=2 "$1" >/dev/null 2>"${WORK}/verify.err" \
    || die "signature does not verify on $1: $(tr '\n' ' ' <"${WORK}/verify.err")"
  local info; info="$(codesign -dv --verbose=4 "$1" 2>&1 || true)"
  grep -q "^TeamIdentifier=${TEAM_ID}\$" <<<"${info}" || die "$1 is not signed by Team ID ${TEAM_ID}"
  grep -q '^Authority=Developer ID Application: ' <<<"${info}" || die "$1 is not signed by a Developer ID Application certificate"
  grep -q '^Timestamp=' <<<"${info}" || die "$1 carries no secure timestamp"
}
verify_signature "${APP}"
# Every Mach-O individually: hardened runtime + our team. --deep verification alone would
# not catch a nested binary signed by someone else's valid identity or without the runtime.
while IFS=$'\t' read -r _depth f; do
  info="$(codesign -dv --verbose=4 "${f}" 2>&1 || true)"
  grep -q "^TeamIdentifier=${TEAM_ID}\$" <<<"${info}" || die "${f#"${APP}/"} is not signed by Team ID ${TEAM_ID}"
  grep -Eq '^CodeDirectory .*flags=0x[0-9a-f]*\(.*runtime' <<<"${info}" || die "${f#"${APP}/"} lacks the hardened runtime"
done <"${WORK}/macho.lst"

notarize() {  # notarize <file> <label>
  local out="${WORK}/notary-$2.json" status id
  say "  submitting $2 to the Apple notary service (waits for the verdict)" >&2
  xcrun notarytool submit "$1" "${NOTARY_AUTH[@]}" --wait --output-format json >"${out}" 2>"${WORK}/notary.err" || true
  status="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("status",""))' "${out}" 2>/dev/null || true)"
  id="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("id",""))' "${out}" 2>/dev/null || true)"
  if [[ "${status}" != "Accepted" ]]; then
    if [[ -n "${id}" ]]; then
      xcrun notarytool log "${id}" "${NOTARY_AUTH[@]}" "${RELEASE_DIR}/${NAME}.notary-$2-rejected.log" >/dev/null 2>&1 || true
      die "notarization of $2 ended '${status:-unknown}' (submission ${id}); Apple's log: ${RELEASE_DIR}/${NAME}.notary-$2-rejected.log"
    fi
    die "notarization of $2 did not complete: $(tr '\n' ' ' <"${WORK}/notary.err")"
  fi
  printf '%s' "${id}"
}

# Notarize the app on its own first so its ticket can be stapled INTO the bundle: a copy
# dragged out of the DMG then opens offline without Gatekeeper having to look the ticket up.
ditto -c -k --keepParent "${APP}" "${WORK}/app.zip" || die "could not zip the app for notarization"
APP_SUBMISSION="$(notarize "${WORK}/app.zip" app)"
xcrun stapler staple "${APP}" >/dev/null 2>&1 || die "could not staple the notarization ticket to the app"
xcrun stapler validate "${APP}" >/dev/null 2>&1 || die "the stapled app ticket does not validate"
spctl --assess --type execute -vv "${APP}" 2>"${WORK}/spctl.app" || die "Gatekeeper rejects the app: $(tr '\n' ' ' <"${WORK}/spctl.app")"
grep -q 'source=Notarized Developer ID' "${WORK}/spctl.app" || die "Gatekeeper does not report the app as Notarized Developer ID"

say "  building ${NAME}.dmg from the stapled app"
mkdir -p "${WORK}/dmg"
ditto "${APP}" "${WORK}/dmg/VOOL.app" || die "could not stage the app for the DMG"
ln -s /Applications "${WORK}/dmg/Applications"
hdiutil create -volname "VOOL" -srcfolder "${WORK}/dmg" -ov -format UDZO "${DMG}" >/dev/null \
  || die "hdiutil failed to build ${DMG}"
codesign --force --timestamp --sign "${IDENTITY}" ${KEYCHAIN_ARGS[@]+"${KEYCHAIN_ARGS[@]}"} "${DMG}" >/dev/null 2>"${WORK}/codesign.err" \
  || die "could not sign the DMG: $(tr '\n' ' ' <"${WORK}/codesign.err")"
DMG_SUBMISSION="$(notarize "${DMG}" dmg)"
xcrun stapler staple "${DMG}" >/dev/null 2>&1 || die "could not staple the notarization ticket to the DMG"
xcrun stapler validate "${DMG}" >/dev/null 2>&1 || die "the stapled DMG ticket does not validate"
spctl --assess --type open --context context:primary-signature -vv "${DMG}" 2>"${WORK}/spctl.dmg" \
  || die "Gatekeeper rejects the DMG: $(tr '\n' ' ' <"${WORK}/spctl.dmg")"
grep -q 'source=Notarized Developer ID' "${WORK}/spctl.dmg" || die "Gatekeeper does not report the DMG as Notarized Developer ID"

# The receipt is what installer/release_sign.py requires before it will seal a DMG into a
# release. It records only public facts (team id, submission ids, digests), never the
# certificate holder's name.
python3 - "${RECEIPT}" "${DMG}" "${APP}/Contents/Resources/BUILD_MANIFEST.json" "${TEAM_ID}" \
  "${APP_SUBMISSION}" "${DMG_SUBMISSION}" "${MACHO_COUNT}" <<'PY' || die "could not write the signing receipt"
import hashlib, json, os, sys
from datetime import datetime, timezone
receipt, dmg, manifest_path, team, app_id, dmg_id, macho = sys.argv[1:8]
digest = hashlib.sha256()
with open(dmg, "rb") as fh:
    for block in iter(lambda: fh.read(1 << 20), b""):
        digest.update(block)
manifest = json.load(open(manifest_path))
doc = {
    "schema": "vool-macos-signing-receipt/1",
    "artifact": os.path.basename(dmg),
    "sha256": digest.hexdigest(),
    "team_id": team,
    "certificate_kind": "Developer ID Application",
    "hardened_runtime": True,
    "macho_files_signed": int(macho),
    "notarization": {"app_submission": app_id, "dmg_submission": dmg_id, "status": "Accepted"},
    "stapled": {"app": True, "dmg": True},
    "gatekeeper": "Notarized Developer ID",
    "exact_sha": manifest.get("exact_sha"),
    "version": manifest.get("version"),
    "arch": manifest.get("arch"),
    "signed_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}
with open(receipt, "w", encoding="utf-8") as fh:
    json.dump(doc, fh, indent=2, sort_keys=True)
    fh.write("\n")
PY
SUCCESS=1
say "OK: ${DMG}  (Developer ID signed, notarized, stapled; receipt ${RECEIPT##*/})"
