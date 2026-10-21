# Skill Codex website integration contract

This describes the JSON service implemented by `registry_service/api.py`, `store.py`, `auth.py`,
`community.py` and `reports.py`. Website pages are a separate application. Local backend tests
do not establish a deployed website, registered providers, active repositories or a shipped anchor.

## Pages and visible facts

Use `/codex/skills` for discovery, `/codex/skills/publish` for drafts/package upload,
`/codex/skills/account` for private submissions and updates, and
`/codex/skills/report?id=publisher%2Fname` for reports. Authentication redirects to the account page.
Cards show title/name, summary, version, category/subcategory, languages, needs, supported targets,
website stars and download requests. Use two linked taxonomy selectors from `/categories`, without
recursive nesting. Give editor picks a distinct label; popularity never grants admission.

Display this notice on discovery, details and before native-install instructions:

> Automated checks cover package compatibility and known threat patterns for the listed VOOL
> builds. They do not guarantee that a skill is free of malicious behavior. Community skills are
> provided as is, without warranty. Inspect the files and use them at your own risk.

Link the complete package licence. MIT contains warranty/liability limitations; this notice is not
a claim of immunity from applicable law. Compatibility checks include package/native-format
validation. Do not describe them as observed execution on every release/platform. The separate
`tested` mark records a team test of exact artifact bytes.

## Common transport and errors

Prefix every route below with `/registry/api`. Use same-origin fetch and browser-managed cookies.
Changes require the configured exact `Origin` and `X-Vool-CSRF` from `GET /me`. Host must match
the configured origin. Keep CSRF in page memory; never store session/provider credentials in
URLs, skills, packages, localStorage or analytics. Render user fields as text, never HTML.

Most refusals are HTTP 400 `{"error":"specific reason"}`. Do not invent 401/413/429 distinctions.
Comment-filter refusals instead return 400 `{state:"refused",reason}`. Unknown paths/methods use
the router's 404/405. Distinguish transport failures from parsed refusals and retain the unsent draft.
Common errors include `CSRF token required`, `same-origin request required`, authentication/session
refusals, `JSON content type required`, `JSON object required`, `invalid JSON document`,
`request exceeds byte limit`, and `upload body timed out`. Show the returned reason.
Private findings never enter public logs/telemetry. Responses default to no-store, nosniff,
no-referrer and restrictive CSP; approved pictures have a five-minute cache exception.

## Authentication and profiles

| Method/path | Request | Success | Refusal/recovery |
| --- | --- | --- | --- |
| GET `/auth/github/start` | Navigation, no body | 303 provider redirect and state/PKCE flow cookie | Unconfigured provider or invalid flow: 400 `{error}`. |
| GET `/auth/google/start` | Navigation, no body | 303 configured OIDC redirect | Google sign-in is separate from standalone email-link login. |
| GET `/auth/apple/start` | Navigation, no body | 303 configured OIDC redirect | Requires a real Apple registration. |
| POST `/auth/{provider}/start` | Session, Origin, CSRF; no body needed | 200 `{url}` for explicit provider linking | No implicit email-based linking; an identity linked elsewhere is refused. |
| GET `/auth/{provider}/callback` | Provider `state`, `code`, flow cookie | 303 `/codex/skills/account`, secure HttpOnly session cookie | Expired/reused state, duplicate fields or invalid provider reply: 400 `{error}`. |
| POST `/auth/apple/callback` | Form `state`, `code`, flow cookie; ≤16,384 bytes | Same redirect/session | Other POST providers: `unsupported callback method`. |
| GET `/me` | Optional session | `{profile,csrf}`; signed out/expired: `{profile:null,csrf:null}` | Expired cookie is cleared; keep anonymous browsing available. |
| PUT `/profile` | JSON exactly `{namespace,bio,handles}`, session/CSRF | Profile plus `providers` | Invalid/claimed/reserved namespace, namespace change or bad handles: 400 `{error}`. |
| POST `/session` | Session/CSRF, no body needed | `{status:"signed_out"}`, clears cookie | Authentication/CSRF errors use 400. |
| DELETE `/session` | Session/CSRF and explicit deletion gesture | `{status:"deleted"}`, clears cookie | Public licensed Git history survives deletion. |

Profile includes private account `id`, stable namespace, bio, handles and linked provider names,
never provider subject IDs. Namespace: lowercase letters/digits/hyphens, starts with a letter or
digit, 1–64 characters. `vool`, `vool-team`, `parad0x-labs`, `official`, `native` are reserved. Chosen names cannot
change through this route and remain reserved after deletion. Bio ≤500 characters. Handles:
`github`, `x`, `mastodon`, `website` only, each ≤200 characters. GitHub/X must match
`[A-Za-z0-9_.-]{1,64}`; the other two are HTTPS URLs without credentials. Handles are author assertions, not verification badges.
GitHub, Google and Apple are implemented providers. Standalone email/password or email-link login
is not implemented. Show only configured choices; provider registration is deployment work.

## Author submissions and updates

Every public package metadata field receives the package's deterministic checks, paid-review inclusion and native install recheck. Render `@metadata.json` findings as metadata findings. Release notes have no bypass. A service failure or exhausted review budget holds the batch without admission; the signer retries with already paid verdicts retained and keeps renewing the list already signed meanwhile. Import's `publish_when_checks_pass` field never replaces explicit checksum consent.

The MIT rights agreement and public-history consent are separate. First save and inspect the
private submission. Then show its exact checksum and obtain a separate, unticked Publish decision.
Automatic model review/public listing waits for that checksum consent. Agreement text:

> I made this skill, or have the right to share it, and I release it under the MIT licence.
> Anyone may use, copy, change and share it.

| Method/path | Request | Success | Refusal/recovery |
| --- | --- | --- | --- |
| POST `/drafts` | JSON exactly `{metadata,skill_markdown,agreement}` or legacy `{metadata,skill_markdown,license_text}`; complete UTF-8 body ≤32,768 bytes | 201 private submission | `invalid skill draft`, native/secret findings, quota, namespace or age refusal. Inspect state even on 201. |
| POST `/submissions` | Unmodified canonical bytes, `application/vnd.vool.registry+json`; optional `X-Vool-Agreement: mit-own-work-v1`; body ≤24 MiB, native payload ≤16 MiB | 201 private submission | No multipart or JSON reserialization. Agreed packages naming GitHub source are refused with import guidance. |
| POST `/imports` | JSON `{repository,ref,path,metadata,publish_when_checks_pass}` and optional `agreement` | 201 submission plus resolved `{source:{repository,commit,path}}` | Requires configured import and linked GitHub identity owning a public repository. Invalid ownership/ref/layout/limits refused. |
| POST `/publisher-link` | JSON exactly `{namespace}`, session/CSRF and linked GitHub identity | `{namespace,verified:true}` | No public publications, wrong owner or claimed name: 400 `{error}`. |
| GET `/submissions` | Session | `{submissions:[...]}`, ≤100 newest | Authentication refusal: 400 `{error}`. |
| GET `/submissions/{identifier}` | Owner session | One private submission | Other accounts: `submission not found`. |
| POST `/submissions/{identifier}/consent` | JSON `{sha256,public:true}`, owner session/CSRF | Updated submission | Wrong checksum/owner/state or unreviewed legacy bytes: 400 `{error}`. |

```javascript
const draft = {
  metadata: {
    id:'your-namespace/meeting-brief', kind:'skill', version:'1.0.0',
    summary:'Summarize supplied notes with decisions and action owners.',
    category:'productivity', subcategory:'meetings', license:'MIT', languages:['en'], needs:[],
    release_notes:'Adds a separate unresolved questions section.'
  },
  skill_markdown:skillEditor.value, agreement:'mit-own-work-v1'
};
// Explicit Save gesture after accepting the rights agreement:
const response = await fetch('/registry/api/drafts', {
  method:'POST', headers:{'Content-Type':'application/json','X-Vool-CSRF':csrf},
  body:JSON.stringify(draft)
});
const saved = await response.json();
// Check HTTP status/state, show saved.sha256 and private findings, then obtain a separate Publish gesture:
const consent = await fetch(`/registry/api/submissions/${saved.id}/consent`, {
  method:'POST', headers:{'Content-Type':'application/json','X-Vool-CSRF':csrf},
  body:JSON.stringify({sha256:saved.sha256,public:true})
});
```

Draft/import metadata omits `source`: the service assigns it. Required: `id`, `kind`, `version`,
`summary`, `category`, `subcategory`, `license`, `languages`. Optional: `title`, `needs`, `icon`,
`banner`, `release_notes`. Draft/import kind is `skill`. Automatic skills use MIT; the service
writes the draft licence in the publisher's name. Legacy `license_text` follows the reviewed flow.
Title: trimmed plain text ≤60 characters, without angle brackets. Summary: ≤500 characters.
Release notes: trimmed plain text, 1–2,000 characters, without controls/angle brackets; part of the
exact signed artifact and its safety checks. Notes changes require a new package/version.
Versions: `major.minor.patch`, optional lowercase prerelease, no build suffix. Languages: 1–30 codes
or exactly `["any"]`. Needs and category/subcategory pairings come from the fixed API lists.
Import refs resolve to real source commits; picture fields name relative paths inside the folder.
Written drafts have no pictures. Import's legacy `publish_when_checks_pass` field does not replace
automatic submissions' separate checksum consent.

Submission fields: `id`, `created` (Unix seconds), `package_id`, `version`, `sha256`, `state`,
`consent`, `github_url`, `findings`, `needs_notices`, `via`, `agreement`; import also returns source.
Via is `draft`, `upload`, `import` or null for older rows. `github_url` can be null; never fabricate
a PR link. Automatic publication mirrors artifacts into the signer repository; it does not open a PR.

| State | Meaning/action |
| --- | --- |
| `checking`, consent 0 | Private automatic upload passed instant checks. Inspect checksum; explicitly publish or keep private. |
| `checking`, consent 1 | Awaiting signer/model checks. Poll status rather than resubmitting. |
| `refused` | Automatic refusal. Show private reasons; revise and submit a higher version. |
| `admitted` | Signed admission recorded; public listing pending. |
| `published` | Public version; live catalog freshness/withdrawal still govern availability. |
| `quarantined` | Legacy private upload awaits independent evidence. |
| `held` | Legacy checks need changes or missing coverage. |
| `rejected` | Secret-bearing payload discarded; show redacted findings. |
| `preflighted`, `reviewed` | Legacy signed evidence permits exact-checksum consent. |
| `ready_for_github` | Legacy consented outbox; independent publication is separate. |
| `expired` | Private artifact/review expired; submit fresh bytes. |

Findings: `{check,path,reason,line?,quote?,rule?}`. Render safe quotes as plain monospace text; never
recover redacted content. `needs_notices:[{need,label,reason}]` is advisory. Automatic checking
retention: 35 days; legacy unsent quarantine: seven days. Quota: ten submissions/account/day.
Pending equal/lower versions are refused. Publishing age is 14 days from the older of the GitHub
account or registry account. There is no individual edit/delete, cancel-export, revoke-consent
or retry-scanner route. New bytes need new review/consent; published versions stay immutable.
Native updates refuse unchanged/lower versions and refuse to overwrite edited local files.

## Public catalog, files and metrics

| Method/path | Request | Answer/refusal |
| --- | --- | --- |
| GET `/catalog` | Unique optional `q`, `category`, `subcategory`, `origin`, `sort` | `{channel:"approved",entries,target:null,targets,sort,ranking}`; latest approved version/ID across signed targets. Missing/stale catalog: 400 `{error}`. |
| GET `/catalog?channel=discovery` | Explicit discovery choice | `{channel:"discovery",entries,notice}`; import compatibility is not reviewed admission. |
| GET `/snapshot` | None | Signed native envelope `{signed,signatures}`; missing/stale snapshot: 400 `{error}`. |
| GET `/presentation` | No query | `{marks:<signed envelope or null>,metrics:[{id,stars,download_requests}]}`; display data, never install authority. |
| GET `/categories` | None | `{categories:[{id,label,subcategories:[{id,label}]}]}`. |
| GET `/requirements` | None | `{requirements:[{id,label,kind,providers}]}`. |
| GET `/profiles/{namespace}` | Namespace | `{profile,origin,packages}` using the same active rows. |
| GET `/download?id=publisher%2Fname&version=1.0.0` | Listed exact ID/version | `{sha256,length,url,metric:"download_requests"}`; pause/withdrawal/stale catalog/missing version: 400 `{error}`. |
| GET `/artifacts/{sha256}?ticket={ticket}` | Returned ticket | Canonical attachment bytes and `X-Content-SHA256`; invalid/expired ticket, pause/withdrawal: 400 `{error}`. Retry the same ticket to avoid double counting. |
| PUT `/stars` | JSON `{id,version,starred:true|false}`, session/CSRF | `{stars,download_requests}` for the stable ID; unauthenticated/unlisted: 400 `{error}`. |
| GET `/pictures/{publisher}/{name}/{sha256}` | Catalog row's supplied URL | Approved PNG, image/png, same-origin resource policy, cache ≤300 seconds; otherwise `picture not found`. |

Search `q` ≤100 characters matches words against name/summary/category labels. Origin: `official`
or `community`. Sort: `stars` (default), `updated`, `downloads`, `newest`. Approved catalog rejects
unknown/repeated parameters; results are not paginated. Discovery stays separately labelled.
Rows include signed metadata, sha256, publication, reviewed, creator, requirements, targets,
authority_notice, native_control_overlaps, policy_sha256, updated_at, published_at, stars,
download_requests, global_rank, category_rank, origin, featured, editors_pick, community_reviews,
tested, highlighted, discovery_only:false, import_ready:true. Signed-in rows add `starred`.
Only a creator's own rows can contain `pictures_pending`; paused rows have `downloads_paused:true`.

Stars are one authenticated account per stable package ID, not a five-point rating or GitHub
repository stars. Downloads count requests, not installed users. Reviewed means automated
admission, not human endorsement. Tested binds an exact checksum; updates lose it. Editorial
labels come from maintainers. Community review rows are reviewer/role/version/date, not arbitrary
review text. Requirements expose network use, credential binding names, permissions and dependencies,
never values. Targets name exact VOOL version/commit/platform combinations. The service accepts the
legacy config `target`; distribution follows signed admissions, hence response `target:null`.

Pictures are optional signed PNG bytes. Show placeholders until that skill/checksum is approved
or its exact automatic version is tested. Authors cannot approve pictures. Withdrawn approvals
hide on the next Browse; browser caches expire within five minutes. Use the light/dark assets
in `docs/registry/assets/` and supplied picture URLs, without constructing arbitrary remote links.

## Comments and reports

| Method/path | Request | Success/refusal |
| --- | --- | --- |
| GET `/comments?id=publisher%2Fname` | Optional session | `{comments:[{id,author,created,body}],can_comment}`; signed-in adds `mine:[{id,state,created,body}]` and optional `can_comment_reason`. |
| POST `/comments` | JSON exactly `{id,body}`, session/CSRF | 201 `{id,state,reason}`: published/held; filter refusal: 400 `{state:"refused",reason}`. |
| POST `/comments/{identifier}/report` | Session/CSRF, no body needed | `{status:"reported"}`; own/missing published comment: 400 `{error}`. |
| GET `/reports?id=publisher%2Fname` | Optional session | `{reasons:[{id,label}],note_limit:500,can_report,can_report_reason?}`; unlisted/stale: 400 `{error}`. |
| POST `/reports` | JSON `{id,reason,note?}`, session/CSRF | 201 `{status:"received"}`; invalid note/reason, own/repeat report or quota: 400 `{error}`. |

Comments require a namespace and an authenticated website download. Reasons: `no_public_name`,
`not_downloaded`, `muted`. Five attempts/day, refusals included; new-account comments are held.
Three reports hold a comment. Filters are heuristic. Report eligibility reasons: `signed_out`,
`own_skill`, `already_reported`, `daily_limit`. Read reason choices from the API; “other” needs
a note. Never attach chat/files/diagnostics. Secret-looking notes are refused. Three counting
safety reports pause downloads without independently revoking admission. Public pages expose no
private report text, counts or reporter identities.

## Native handoff, operators and activation

VOOL opens `/codex/skills/publish#skill=<base64url UTF-8 JSON without padding>` carrying exactly
`{v:1,name,title,summary,skill_markdown}`. Parse bounded untrusted data, prefill locally, and send
nothing until Save. Never turn it into HTML or fetch URLs. Fragments are not sent to the server;
remove the fragment from browser history after prefill and preserve the draft deliberately through
sign-in. No chat, memory, provider key or account token belongs in it.

Install stays in VOOL: Skill Codex → Browse → Download for inspection → inspect exact files,
permissions and instructions → Install without enabling / Install and enable. Website pages never
POST to guessed localhost ports, supply anchors/paths or approve local installation. Without a
valid local anchor VOOL offers no Publish button and registry gestures refuse calmly. Valid copies
can work offline; known revocations block them after the next explicit Browse.

Operator routes are absent unless authority mode is configured and require admission-key signed
`X-Vool-Authority` requests with method/path/body binding, nonce and clock window. Browser sessions
cannot use GET `/authority/queue`, GET/POST `/authority/submissions/{identifier}`,
GET `/authority/artifacts/{sha256}`, POST `/authority/catalog`, POST `/authority/marks`,
GET `/authority/moderation`. See [OPERATIONS.md](OPERATIONS.md) and the `team-steps.txt` template.

Routine admission, daily rechecks and release following run in GitHub Actions. Only consented,
listed bytes enter `authority/packages/`, `authority/registry.json`, `authority/CATALOG.md` in the
signer repository. Removal/suspension withdraws the current projection; history persists. A direct
PR workflow in a separate community repository requires its own protected CI/publisher activation.
Import and mirroring do not establish that this independent PR workflow is active.

Frontend acceptance covers provider expiry/link conflicts, namespace refusal, Unicode/oversize
forms, unsafe private findings, lost upload responses, absent checksum consent, checksum mismatch,
immutable updates with notes, stale/withdrawn catalog, star toggles, ticket retries, placeholders,
report refusals and native handoff without implicit upload. Test these separate pages with
disposable accounts; backend fixture passes do not prove deployment.
