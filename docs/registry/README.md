# Skills and plugin registry for v0.7.1

The registry reuses VOOL's native package identity, lifecycle, tool contracts, skill loader and permission gates. Public approval identifies reviewed bytes; installation and enabling remain separate owner decisions.

The existing `core/addon_directory.json` remains the catalog authority. Its existing entries, source pins, licence checksums, `discovery_only` and `import_ready` flags are unchanged. A protected catalog change adds a `registry` field containing the signed reviewed projection. The server reads that file and caches immutable GitHub artifacts. Its database owns accounts, private quarantine, stars and request counters; it cannot admit a package by changing a database flag.

Existing built-in and upstream discovery sources continue to supply discovery. Import compatibility is not registry approval. Existing installations are not silently migrated, renamed or reapproved.

## Author workflow

Use the complete [author guide](AUTHOR-GUIDE.md), [website integration contract](WEBSITE-HANDOVER.md), [descriptive agent workflow](agent-workflow.json) and [licensed example](examples/meeting-brief/SKILL.md).

Developers use `vool-community` or `python -m tools.community_skills`:

1. Prepare metadata naming a stable `publisher/name`, semantic version, kind, summary, category, subcategory, licence and exact upstream GitHub commit/path. Website drafts receive immutable upload provenance instead.
2. Run `init DIRECTORY --metadata metadata.json --license-file LICENSE`. It creates a native package with a `registry-` identity. Edit `skills/NAME/SKILL.md`; plugin authors extend the existing native manifest contract described in the plugin documentation.
3. Run `lint DIRECTORY --scratch SCRATCH`, then `pack DIRECTORY --scratch SCRATCH --output package.vool.json`. The output includes every file, empty directory and executable bit. Links, credential filenames and ambiguous paths are refused rather than omitted.
4. Submit the file through the website, or put it at `packages/SHA256.json` in a fork of the matching community repository and open a PR. `submission-link FILE --github-user USER --branch BRANCH` prints the prefilled comparison link and performs no remote write.

Local preflight checks native format, bounded complete input, likely secrets and likely native-rule conflicts. It never certifies admission. Public PRs are already public: do not submit credentials there. The website's simple skill form accepts SKILL.md and licence text; plugins use the complete SDK artifact upload. The API contract is in [WEBSITE-HANDOVER.md](WEBSITE-HANDOVER.md).

## Private upload and publication

Uploads remain private and unlisted. Local secret findings produce a redacted rejection and discard the payload. Other submissions are quarantined. Independent signed archive and secret reports are required before the author can consent to permanent public GitHub history. The consent binds the exact artifact checksum. Export rechecks report freshness and policy, then current account ownership, consent, state and exact artifact/review bytes. Already-exported files require the separately activated publisher receipt protocol. It creates an outbox candidate, not public approval.

The isolated GitHub publisher places those exact bytes in the community repository and opens a PR. Final review binds the immutable GitHub commit and artifact path. A protected, maintainer-reviewed merge is followed by offline admission signatures and a protected canonical catalog update. Only that signed catalog projection appears in the approved browser, category lists and rankings. A failed new version does not replace a previous approved version.

`registry_service.worker` processes signed reviews, exports consented candidates and prunes expired quarantine. It performs no remote writes, signing or package execution. Known secret-bearing artifacts are removed; unsent quarantine expires after seven days. Public Git history and licensed publications survive private account deletion.

## Required admission evidence

The pinned policy requires complete source, archive, secrets, code, dependency closure, licence, two independent instruction-provider reviews, native-boundary review, compatibility and quality reports. Reports bind the exact transport hash, native digest, GitHub publication, scanner version/configuration, policy and target release. Their bytes must match the declared report hashes. Missing, skipped, unsupported, stale, contradictory or unresolved findings block admission. Severity warnings count as unresolved findings.

Compatibility identifies the exact released VOOL version, source commit and platform. `tools.registry_ci` verifies the latest stable GitHub release and checks complete signed reviews against the candidate commit. A format-only green job is not an admission verdict. This initial native client reads the existing verified macOS release manifest; development checkouts and unsupported release identity formats cannot claim compatibility.

The shipped trust root pins the policy's scanner/rule/configuration descriptor. The invocation target matrix is independently bound by the signed compatibility report and admission. It is deliberately excluded from the policy descriptor hash, so embedding a root in an app does not require that app to contain its own future commit hash. Changing a target matrix still requires fresh, signed exact-target results; it never reuses a compatibility verdict by matching a policy hash alone.

Scanner adapters and independent review signers must be configured outside the web process. Unknown uploaded code runs only in disposable, isolated remote workers with no user credentials, signing keys or production access. Never execute it on the website server or an operator's installed app. The candidate-run repository blueprint workflow is advisory and refuses missing release pins, policy or signed reviews. Candidate-editable workflow results cannot authorize admission. The [trusted admission-service activation contract](ADMISSION-SERVICE.md) requires a protected check with authenticated source identity and independently observed scanner evidence.

## Native rules have priority

A skill supplies instructions, not a spending limit, currency authorization, permission grant or approval. Native effect budgets, spend authorization, owner settings, supported payment routes, access controls and consent requirements remain authoritative. Skills cannot mint operator budget authority or approve their own actions.

Inspection shows the controls a skill overlaps with, for example: “Spending instructions overlap VOOL's native budgets and spend approval. Configured limits, freezes and approval requirements prevail.” Such overlap is informational; instructions requesting an override are blocked. The signed native-boundary report records semantic overlaps, including other languages. Local keyword notices are heuristic and do not replace independent review or the actual native effect gates.

The native Add-ons page offers Skill Codex → Browse Skill Codex → Download for inspection → Install without enabling / Install and enable. The complete file inventory, instructions, requested permissions and source are visible before acceptance. The website cannot POST an install into the local app. Account login is not required to browse or install.

## Signing and update security

This is a versioned VOOL protocol, not a claim of TUF conformance. It applies threshold trust, role separation, immutable target binding, expiration and durable rollback protection; see the [TUF specification](https://theupdateframework.github.io/specification/latest/) for the wider update threat model. It needs independent review before production activation.

Root, admission and review roles each require at least two distinct Ed25519 keys. The online catalog role is separate. Each catalog also carries an offline authorization binding its exact admissions and revocations with a monotonic epoch. Online keys can renew a short-lived authorized catalog but cannot replace its entries or revocations. Authorization expires within 24 hours; admissions within 30 days. Known revocations remain sticky across refreshes and root rotations. Rotation requires consecutive versions and both old and new root thresholds.

Production keys are not generated by the SDK or held by the website process. Offline signing accepts an encrypted key file with an interactive passphrase. No test root or test scanner result ships as a production trust anchor. Until the real anchor and provider registrations are supplied, startup/install fail clearly rather than fabricating a usable registry.

Installation verifies the complete native digest and exact recursive dependency approvals, then uses the existing lifecycle. Registry packages without a current lifecycle record are not offered. Edited or revoked packages are removed from the actual skill/tool offer path. Updates preserve an edited local installation instead of overwriting it; owned updates journal the previous package, receipt and enabled state for rollback/recovery. Valid enabled packages work offline; new installs require fresh metadata and known revocations still block use.

## Accounts and metrics

GitHub authorization code with state/PKCE, and Google/Apple OIDC with signature/issuer/audience/nonce/expiry checks, provide stable provider identities. Emails are not account-linking keys. Linking requires an authenticated same-origin session plus a new verified provider flow. Passwords and provider access/refresh tokens are not stored; short-lived VOOL sessions use hashed identifiers and essential secure cookies.

Public profiles contain only the chosen namespace, bio and optional HTTPS/social handles. Chosen names stay reserved after private account deletion; direct public publication also reserves its publisher. Migration backfills current accounts and surviving publications; deleted unpublished names lost before migration cannot be reconstructed. Unknown ownership refuses self-claiming or transfer. Handles are author-supplied; they do not confer a verified publisher badge. One account can star a stable package once. Version updates keep the same stars and download totals. Global/category ranks use stars with a deterministic ID tie-break. Downloads count request tickets served once, including anonymous use; retries reuse a ticket. These are download requests, not unique people or installed copies. Separate ratings, written reviews and install telemetry are not implemented in this version.

The API has no IP/user-agent retention fields and disables request logs. Hosting and identity providers process network connections; their retention policies must be configured and disclosed separately. Deployment instructions cover proxy logging, private permissions, encrypted backups and restore verification in [OPERATIONS.md](OPERATIONS.md).
