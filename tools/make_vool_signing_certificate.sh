#!/bin/bash
# Make VOOL's publisher signing certificate once, on the publisher's own Mac, and hand it to the
# build workflows as two GitHub repository secrets.
#
# One certificate signs every Mac build, so macOS keeps VOOL's permissions (Calendar, Contacts,
# Files, microphone) when the app updates. It is self-signed: it only has to stay the same from
# build to build. Nothing here touches a keychain. The certificate and its password are kept in
# ~/.vool-publisher (owner-only). Keep a backup of that folder: a lost certificate means one more
# update after which users allow the permissions again.
set -euo pipefail

repo="Parad0x-Labs/vool"
dir="${HOME}/.vool-publisher"
name="vool-codesign-2026-11"
ssl=/usr/bin/openssl

if [[ -e "${dir}/${name}.p12" ]]; then
  echo "A certificate already exists at ${dir}/${name}.p12; using it (nothing new was made)."
else
  umask 077
  mkdir -p "${dir}"
  chmod 700 "${dir}"
  work="$(mktemp -d)"
  trap 'rm -rf "${work}"' EXIT
  cat > "${work}/cert.cnf" <<'CNF'
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = VOOL Publisher (Parad0x Labs LLC)
O = Parad0x Labs LLC
[ext]
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature
extendedKeyUsage = critical, codeSigning
subjectKeyIdentifier = hash
CNF
  "${ssl}" req -x509 -newkey rsa:3072 -nodes -sha256 -days 3650 \
    -keyout "${work}/key.pem" -out "${dir}/${name}.cert.pem" -config "${work}/cert.cnf" 2>/dev/null
  printf '%s' "$("${ssl}" rand -hex 24)" > "${dir}/${name}.password"  # no newline: it is the secret's exact value
  "${ssl}" pkcs12 -export -inkey "${work}/key.pem" -in "${dir}/${name}.cert.pem" -name "VOOL Publisher" \
    -out "${dir}/${name}.p12" -passout "file:${dir}/${name}.password"
  chmod 600 "${dir}/${name}".*
  echo "Made the certificate: ${dir}/${name}.p12"
fi
"${ssl}" x509 -in "${dir}/${name}.cert.pem" -noout -subject -enddate -fingerprint -sha1

if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  /usr/bin/base64 -i "${dir}/${name}.p12" | gh secret set VOOL_CODESIGN_P12 -R "${repo}"
  gh secret set VOOL_CODESIGN_P12_PASSWORD -R "${repo}" < "${dir}/${name}.password"
  echo "DONE: both secrets are set on ${repo}."
else
  echo
  echo "Add two secrets at https://github.com/${repo}/settings/secrets/actions/new"
  /usr/bin/base64 -i "${dir}/${name}.p12" | tr -d '\n' | pbcopy
  read -r -p "1) Name: VOOL_CODESIGN_P12   Value: paste (it is on the clipboard now). Press Enter when saved. " _
  tr -d '\n' < "${dir}/${name}.password" | pbcopy
  read -r -p "2) Name: VOOL_CODESIGN_P12_PASSWORD   Value: paste (on the clipboard now). Press Enter when saved. " _
  printf '' | pbcopy
  echo "DONE: the clipboard is cleared."
fi
