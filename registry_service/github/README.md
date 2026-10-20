# Skill Codex signer

This repository runs the Skill Codex signer for VOOL. Every few minutes a GitHub Actions job checks new
skill uploads, signs the ones that pass and refuses the rest with the exact reasons, and keeps the
signed list fresh. Once a day it runs every listed skill through the checks again and follows VOOL's
newest releases.

- [Browse the listed skills](authority/CATALOG.md). [Machine-readable directory](authority/registry.json).
- Only exact consented bytes that passed checks and reached the signed list enter `authority/packages/`.
  Private pending/refused uploads and their findings never enter public files. Removal/suspension withdraws
  the current directory and package copy; earlier public Git history remains.
- `authority/state.json` is the signed list, the marks and the VOOL builds it is signed for.
- `authority/private.bin` is sealed. It holds private verdicts, counts, spending and a digest binding the
  public state. Hand-edited state stops before requests. Existing setup state is validated and upgraded.
- `authority/trusted-root.json` is the public trust anchor that VOOL ships with. The main key that
  signs it is kept offline.
- `team-steps.txt` holds the team's hand steps: the Tested mark, the Highlighted spot, suspending a
  creator/skill, removing a skill, comment/report moderation, pauses, picture approvals, editorial picks,
  the team's members and VOOL builds to test before their release. Its comments list every form. Edit it on
  GitHub; the next run applies it through signed requests, with no server restart.
- The checks themselves are VOOL's own, from the commit named in `authority/vool-commit`.
- The signing keys live only in this repository's Actions secrets. The run log shows counts, never
  what an upload contains.

The service follows the builds in signed admissions without editing its old target setting. The signer
follows the newest published releases, rechecks listed skills daily, and keeps model review capped.
Unavailable review never admits unchecked bytes. Public logs show counts and operator-line problems;
`authority/state.json` also contains opaque moderation IDs/counts, never private text or reporter identities.

A malformed operator line or failed release discovery stops the whole run before state changes, admissions or catalog publication. The log names the line or source failure and the next run retries. An unreadable moderation summary stops the run the same way. A malformed upload queue or upload bytes that differ hold the whole batch before review. Review downtime or the monthly cap holds the batch, retaining completed costs and verdicts in sealed state. Unconfirmed or malformed decision, marks and catalog acknowledgments stop further requests and keep recovery state. While uploads are held, the run still applies team steps, rechecks listed skills and renews the list already signed, so browsing never lapses; the run then exits non-zero. A listed skill whose bytes cannot be read affects only itself. Before every new paid review the signer checks the remote state and operator instructions for a superseding commit; it checkpoints each completed upload check before the next review.

Every public package metadata field, including release notes, is checked with the package and included in model review. Credential-bearing HTTP and Git transports refuse redirects rather than forwarding credentials. Existing setup/configuration fields, secret names and operator line forms remain valid.

Automated checks cover package compatibility and known threat patterns; they can miss malicious behavior.
Community skills are provided as is, without warranty. Inspect their files and licence before use. Website
stars and download requests are popularity signals, separate from safety checks and the team's editor picks.

Authors: read the [SDK and author guide](https://github.com/Parad0x-Labs/vool/blob/main/docs/registry/AUTHOR-GUIDE.md)
for complete packages, signed release notes, private upload, GitHub import, checksum consent and immutable
updates. Public fork PRs require the separately configured protected community-repository CI; this signer's
website intake/mirroring does not activate that independent workflow. No skill's own code or workflow grants
admission. VOOL's permissions and approval requirements remain authoritative after installation.
