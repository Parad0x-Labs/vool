# Registry website integration contract

The website owns browsing, private author forms, account/profile views and exact-byte public-history consent. VOOL owns native installation, enabling and runtime authority. The API cannot grant admission by changing a database flag. Scanner workers, review signing, GitHub publishing and catalog admission have separate custody.

This document describes implemented API behavior and identifies activation work separately. It does not establish that a production endpoint, provider registration or community repository is active.

## Pages and five user journeys

| Journey | Minimum flow | Recovery and authority boundary |
|---|---|---|
| Nontechnical skill author | Sign in → choose namespace → paste skill + licence → save privately → review private findings → consent to exact public checksum | Preserve unsent draft across login/error. No source SHA field: server assigns upload provenance. No checked-by-default publication consent. |
| Developer with SDK/fork | Read complete example → stage clean distribution → init/lint/pack/check → choose private upload or public fork PR | `.git` and secrets are refused, never silently omitted. Public GitHub pushes already publish data. |
| Author delegating to assistant | Copy scoped assignment and workflow contract → prepare files/report → owner reviews exact action and bytes | Preparation does not authorize upload, PR creation, public consent, local install or enabling. |
| Anonymous discovery/install | Browse reviewed catalog or separately labelled upstream discovery → inspect → open local app instructions | Browsing/download needs no account. Website cannot send an install POST into VOOL. |
| Update/rejected resubmission | Show current published version → prepare new bytes/version → fresh review/consent → independent admission | Published versions immutable. Preserve earlier approval and edited local installs. Never replay old consent onto changed bytes. |

The auth callback currently redirects to `/codex/skills/account`; provide a frontend route there unless the backend callback destination changes. Other page routes are frontend design choices, not API endpoints. Keep account state separate from public browse so a login outage does not hide available anonymous catalog data.

## Same-origin authentication

API prefix: `/registry/api`. Use same-origin fetch with browser-managed credentials. The service validates the Host against the configured origin. Mutation requests require that exact Origin and `X-Vool-CSRF`; obtain the token from authenticated `GET /me`. Do not persist session/provider tokens in localStorage, URLs, draft files or skills. Keep CSRF in page memory and refresh after session renewal.

| Request | Implemented result/requirements |
|---|---|
| `GET /auth/github/start` | Redirects through configured GitHub state/PKCE flow. |
| `GET /auth/google/start`, `GET /auth/apple/start` | Redirects through configured OIDC flow. Apple callback may POST. Provider registration is activation work. |
| `POST /auth/{provider}/start` | Begins linking a provider to the authenticated account; same-origin mutation checks apply. No email-based linking. |
| `GET /me` | Authenticated `{profile, csrf}`. Profile includes account ID/namespace and chosen public profile fields. |
| `PUT /profile` | Exactly `{namespace, bio, handles}`; no optional field omission. |
| `POST /session` | Signs out current session and clears cookie. Mutation checks apply. |
| `DELETE /session` | Deletes private account and clears cookie. Mutation checks apply. Public Git history/licensed publications survive. |

Profile namespace uses lowercase `[a-z0-9][a-z0-9-]{0,63}` and excludes `vool`, `parad0x-labs`, `official`, `native`. Once chosen, the current profile route refuses a different namespace. Bio is at most 500 characters. Handles may include only `github`, `x`, `mastodon`, `website`; values are at most 200 characters. GitHub/X handles are plain handle strings; Mastodon/website values must be HTTPS URLs without credentials. These handles are author-supplied assertions; never show a verification badge based on them.

**Namespace ownership:** the backend permanently reserves each chosen name independently of private account data. Account deletion removes provider/account/session records and leaves only the public name reservation. Current live accounts and surviving public publications are backfilled; direct GitHub publication reserves its publisher too. Deleted unpublished names lost before migration cannot be reconstructed. A reserved name with no live account cannot be self-claimed: later linking or transfer requires a separate verified maintainer process. Public handles establish no entitlement.

## Private skill form and artifact upload

The simple skill form sends exactly three body fields. Read the actual selected licence file; do not replace it with a label or illustrative sentence:

```javascript
const draftBody = {
  metadata: {
    id: 'your-namespace/meeting-brief', kind: 'skill', version: '1.0.0',
    summary: 'Summarize supplied meeting notes with decisions and action owners.',
    category: 'productivity', subcategory: 'meetings', license: 'MIT'
  },
  skill_markdown: skillEditor.value,
  license_text: await selectedLicenseFile.text()
};
```

| Request | Contract |
|---|---|
| `POST /drafts` | `application/json`, whole UTF-8 JSON body ≤32,768 bytes, exact keys `metadata`, `skill_markdown`, `license_text`. Metadata omits `source`, `kind` is `skill`. Server assigns upload provenance and packages the draft. |
| `POST /submissions` | Unaltered canonical SDK bytes with `application/vnd.vool.registry+json`; body ≤24 MiB, native package payload ≤16 MiB. No JSON reserialization or multipart envelope. |
| `GET /submissions` | Authenticated `{submissions:[...]}`; at most 100 newest entries. |
| `GET /submissions/{id}` | Only the account owning the submission receives private state/findings. |
| `POST /submissions/{id}/consent` | JSON `{sha256:<exact returned checksum>, public:true}`. Allowed only from `preflighted`/`reviewed` with unchanged checksum and authenticated ownership. |

Submission response fields: `id`, `created` (Unix seconds), `package_id`, `version`, `sha256`, `state`, `consent`, `github_url`, `findings`. `github_url` may be null; the current source defines the field but no publisher callback populates it. Present “awaiting publisher” instead of a fabricated PR link. Authenticated upload can return HTTP 201 and a rejected/held state. Inspect the response state before selecting success copy.

The form should prefill stable namespace, name-derived skill path, `kind: skill`, version `1.0.0` and a small complete example. Offer a licence choice with full text and author-rights confirmation; an SPDX label alone is insufficient. Explain source/rights when importing upstream content. Category/subcategory must be slugs from the fixed list served by `GET /categories` (`{categories:[{id,label,subcategories:[{id,label}]}]}`); offer them as two linked choices and display the labels. The server refuses any other value.

Count the full serialized draft in UTF-8 with `TextEncoder`. Do not promise two independent 32 KiB text uploads: the entire request shares 32 KiB. Count skill body separately for the 8,000-character native limit. Keep the local draft editable when a payload fails. Do not persist text suspected to contain credentials into public analytics, crash reporting or default browser storage.

## State copy and actions

| State | Suggested label | Allowed visible next action |
|---|---|---|
| `quarantined` | Saved privately; review pending | View checksum/status; wait for signed reports. |
| `held` | Review needs changes or missing coverage | View redacted private findings; revise/resubmit clean bytes. |
| `rejected` | Secret detected; uploaded payload discarded | Explain removal/resubmission; preserve only a safe local draft under the author's control. |
| `preflighted` | Private prepublication checks passed | Show exact checksum, licence and permanent-public-history consent. Full review remains pending. |
| `reviewed` | Final review recorded; catalog admission pending | May consent if still private. Do not label installed/approved from this state alone. |
| `ready_for_github` | Public submission authorized; publisher pending | Show consented checksum and outbox/publisher state. |
| `published` | Admitted catalog version | Link only actual publication metadata; inspect live compatible catalog/revocation state. |
| `expired` | Private upload expired | Explain that artifact/review were removed after seven days; resubmit a fresh candidate. |

Show private findings only on authenticated author pages, using plain text rendering rather than HTML. Findings can include `check`, `path`, `reason`; avoid relying on one fixed schema beyond the implemented response. Never echo hidden artifact contents or provider transport exceptions. A `held` record can be reviewed again by the worker; an author cannot sign it approved through HTTP.

The backend currently returns `{error:<message>}` with HTTP 400 for most refusals, including authentication, quota and body validation; do not implement recovery solely from nonexistent 401/413/429 status distinctions. Proposed structured error codes and retry hints require a backend change. Distinguish a transport failure from a parsed refused result, and preserve the last known state. The API has no idempotency key for upload: after a lost response inspect submissions before retrying to avoid duplicate quota consumption.

The author API presently has no edit/delete individual submission, retry-scanner, cancel-export or revoke-consent route. Do not display buttons claiming those actions work. Unsent quarantine expires after seven days; submission quota is ten per account per day. Provide a draft-download/edit path before submission and clear resubmission guidance. Exact retention/retry timing is future API metadata, not a timestamp the frontend should fabricate.

Consent modal text should state that public GitHub history is durable, the submitted licence remains attached, account deletion does not erase public content, and consent applies to the displayed checksum. Keep publication consent separate from authentication, private upload and local installation. Neither a green badge nor user inertia is consent.

## Anonymous catalog, inspection and metrics

| Request | Public contract |
|---|---|
| `GET /catalog` | `{channel:'approved', entries, target, ranking}`. Latest compatible approved version per ID. |
| `GET /catalog?channel=discovery` | Existing upstream discovery rows and an import-compatibility notice; not reviewed registry rows. |
| `GET /snapshot` | Signed registry snapshot for native verification, not a frontend admission flag to mint. |
| `GET /download?id=publisher%2Fname&version=1.0.0` | Verified entry → request ticket `{sha256,length,url,metric}`. |
| `GET /artifacts/{sha256}?ticket={ticket}` | Exact canonical artifact bytes; `X-Content-SHA256` and attachment header. Retry same ticket for the same request. |
| `PUT /stars` | Authenticated same-origin `{id,version,starred:true/false}`; one account star per stable package ID. |

Catalog rows include stable ID/version/kind/summary/category/subcategory/licence/source, checksum, `creator`, `publication`, `reviewed`, `discovery_only`, `import_ready`, `policy_sha256`, `updated_at`, `authority_notice`, `native_control_overlaps`, stats and ranks. Use the source's actual result fields; do not invent ratings, review text, install telemetry or a verified-user count.

Stars rank descending with stable ID tie-break; requests do not determine rank. Display “Download requests,” not “Installed users.” Filters/search may operate locally on returned rows: current API has no documented paginated search, category query or profile-detail endpoint. Admission is tied to exact release/platform `target`; show that target and refuse to imply universal compatibility. A stale/absent approved catalog produces a refusal; present recovery while preserving the distinct discovery route.

Render content/handles as untrusted text and vetted HTTPS links. Display publication repository/commit/path separately from metadata's original source. Do not collapse those into one ambiguous “source SHA.” The transport checksum binds the whole package; local preflight and generic import-ready discovery flags never substitute for signed reviewed admission.

## Local install handoff

Provide concise instructions to open VOOL's local Add-ons page and choose Skill Codex → Browse Skill Codex → Download for inspection → Install without enabling / Install and enable. No native website install bridge exists. Never POST to guessed localhost addresses, embed a fetch URL/trust root, or copy a website session into the app.

The local page owns explicit network/download consent, full content/permission inspection, exact checksum review and local install/enable choice. Native rules, budgets, currency handling, settings, permissions and approvals remain authoritative. Overlap notices are informative and grant no access. An edited package must be preserved, and revocation/compatibility refusal needs an clear explanation.

## Activation work and acceptance

The service loader, review/export worker and blueprints exist. Real provider registrations, independent scanner adapters/worker isolation, signing custody, protected community repositories, the isolated GitHub publisher, PR-link callback, release trust anchor and production smoke require separately authorized activation. The current queue worker exports exact consented bytes; it does not open a PR. Final signed reports must be independently attached to a protected exact candidate commit; candidate-modified workflows cannot certify themselves.

Frontend acceptance should cover all five journeys together after each integration stage: login/link expiry, namespace refusal, empty/large/Unicode form bodies, native-body limit, private rejected/held findings, lost upload response, absent publisher URL, unchanged-checksum consent and checksum mismatch, immutable update, anonymous stale catalog/discovery, star toggle and retried download ticket. Validate with disposable accounts and fixtures under authorized test conditions; do not claim production smoke from local tests.

Recommended backend additions are structured error codes, upload idempotency, safe retention/retry metadata, public-version conflict preflight and an authenticated publisher receipt/status channel. They require owning source changes and cumulative security regression; the frontend must not emulate them by guessing.
