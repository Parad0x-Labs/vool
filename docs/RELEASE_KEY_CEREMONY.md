# Publisher keys and beta releases

The release owner generates the Ed25519 publisher key. Run the task's
`make-publisher-key.sh`: it writes the private half outside the workspace in
`~/.vool-release-secrets/`, mode 0600, and prints only the public hex and JSON pin.
Never paste, commit, upload or log the private half. Keep an offline backup.

Run `pin-public-key.sh <public-hex>` on the repair branch. Review its public-only
commit, satisfy the exact-head publication gate, push and merge. Build every
platform from that clean merged head. Empty shipped pins offer no updates.

For the complete executable release sequence, use the private task's
`RELEASE-STEPS.md`. The release contains separate `macos-arm64`, `macos-x64` and
`linux-x64` update ZIPs, signed by `installer.update_release`, plus its signed
`manifest.json`. The manifest version is the numeric runtime version, currently
`0.7.0`; the channel is `beta`, the tag is `v0.7.0-beta`, and the per-channel
sequence must increase for each changed release document. Never reuse a sequence
for different signed bytes.

Publish as a normal release marked **Latest**. Drafts and prereleases do not advance
`https://github.com/Parad0x-Labs/vool/releases/latest/download/manifest.json`.
Only this repository's release URLs and GitHub's release-asset download host are
allowed through the updater's outbound policy.

macOS beta bundles may use ad-hoc codesign. Production installation still requires
the pinned manifest signature, artifact signature, SHA-256, bounded extraction
and `codesign --verify --deep --strict`. A valid certificate-signed running app
continues to require Gatekeeper acceptance. The sandbox waiver is not a release
policy. The updater clears quarantine only inside a fully verified extraction.

To rotate keys, first ship an update signed by the currently trusted key that adds
the next public pin. Keep the current key until those users have that pin. Then
sign subsequent releases with the new key. Retiring a key before clients receive
its replacement leaves them unable to verify the feed and requires a manual
install. Record key IDs, public keys, release heads and sequence numbers in the
owner's release log.
