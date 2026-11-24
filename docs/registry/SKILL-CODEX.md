# Skill Codex server routes

The website's Skill Codex pages use these routes in addition to [WEBSITE-HANDOVER.md](WEBSITE-HANDOVER.md). The same rules apply: same-origin requests, `X-Vool-CSRF` on every change, plain-text rendering of every user-written field, and `{error}` with HTTP 400 on refusal.

The service serves current signed targets without a per-release config edit; native installation
still matches its actual released version, exact commit and platform. Automatic checks cover package
compatibility and known threat patterns; they do not guarantee freedom from malicious behavior.
Community skills are provided as is, without warranty. Inspect the files and use them at your own
risk, subject to the complete package licence and applicable law.

## Publishing with automatic checks

When the service runs with the automatic signer (on GitHub or on the signing Mac; see [OPERATIONS.md](OPERATIONS.md#automatic-checks-on-github)), a creator publishes without waiting for a person. Before the first upload the page shows this agreement with an unticked box, and sends nothing until the creator ticks it:

> I made this skill, or have the right to share it, and I release it under the MIT licence. Anyone may use, copy, change and share it.

The ticked box becomes `"agreement": "mit-own-work-v1"`:

- The written-skill form sends `POST /drafts` with exactly `{metadata, skill_markdown, agreement}`. `metadata.license` must be `MIT`; the service writes the MIT licence text in the publisher's name, so the form has no licence field.
- An uploaded package (`POST /submissions`) carries the header `X-Vool-Agreement: mit-own-work-v1`. The package must hold the MIT licence text and an upload source `{upload_id, namespace}`. A package that names a GitHub repository is refused with "this package names a GitHub source; publish it with GitHub import instead".

The agreement records rights, not publication consent. A clean upload returns `state: checking` and `consent: 0`. Show its exact `sha256` and a separate unticked public-history consent. Only an explicit `POST /submissions/{id}/consent` with `{sha256, public: true}` puts those bytes in the automatic signer queue. An unchanged checksum is required; the state stays `checking` until the signer acts. New imports follow the same checksum decision. Without the agreement the routes retain the separately reviewed legacy flow.

The server checks every upload at once. A clean one stays private in `checking` until its author consents to the returned exact checksum. After consent the signer checks it again and asks a review model to read it. Then it becomes `refused` with reasons, or `admitted` and soon `published` in the category the creator chose. A refusal's `findings` are `{check, path, reason, line?, quote?, rule?}`. Show `reason` as written, with the file and line, and `quote` as plain monospace text so the creator sees the words that tripped the check. `rule` is a stable code for the reason, for a page that shows it in the reader's language: `install`, `download`, `address`, `secret-ask`, `wallet`, `injection`, `hidden`, `letters`, `consent`, `voice`, `link-data`, `card-ask`, `phishing`, `invisible`, `escapes`, `secret`, `reserved-name`, `reserved-summary`, `summary`, `short`, `endorsed` and `too-long`. A finding without `rule` is shown by its `reason` alone. A line that looks like a key, password or recovery phrase is never quoted back, whichever check names it. Skill Codex skills:

- hold Markdown or plain-text files under `skills/`, the licence, `registry.json`, the manifest and optional pictures, and nothing that runs: no scripts, programs, executable files, manifest commands, permissions or credential bindings, and no `vool` section in the manifest (VOOL writes that itself to mark a pack as its owner's own, and refuses to install a download that carries one);
- never ask anyone to install, download or run software, paste commands into a terminal, or open a short link, raw address or paste site;
- never ask for a recovery phrase, key, password or code, however the ask is worded ("ask the user for", "request", "read out", "this skill needs your", "what is your"), contain a wallet address, or carry hidden comments, invisible characters or encoded blobs;
- never say the person already agreed or that asking can be skipped, speak as VOOL or as a new set of rules, show a picture from a web address or put the person's words into a link, ask for card numbers, expiry dates or PINs, or carry a sign-in link or a web address dressed as VOOL's. These are the rules VOOL applies to a skill sent as a file; a published skill is held to all of them, and VOOL applies them again before it installs one;
- never try to change VOOL's own rules, spending limits or approvals, or hide what they do from the user;
- carry the MIT licence text with its copyright lines and nothing else, and have instructions long enough to be useful;
- keep the words that read as the Skill Codex vouching for a skill out of their name, title, summary and the description a listing shows (such as verified, official, certified, endorsed, "tested by", "safe to use", "security checked", "Editor's Choice", "staff pick" or "made by VOOL"), and out of every file (such as "verified by VOOL", "tested by Parad0x Labs", "official Skill Codex", "recommended by VOOL" or "VOOL team"); only the team's own skills may use them. A title or an id also may not carry VOOL's or the Skill Codex's name, the words featured, recommended, trusted or approved, or a check mark, star or medal. A summary may name VOOL ("Helps VOOL plan your week");
- keep all their text under 120,000 characters ("This skill is too long to check. Keep all its text under 120,000 characters.").

The checks read the instructions, summary and manifest the way a person would: a command split over lines with `\` is read as one, even in the middle of a word, and full-width letters, joiners and look-alike letters from other alphabets are read as the plain letters they imitate. A word that mixes alphabets is refused on its own. Names and summaries are also read with digits and signs that stand in for letters ("0fficial", "V00L"), apostrophes and extra spaces taken out. Commands are refused even inside advice about them; write the advice in words instead.

The review model also refuses a skill that does something its summary does not say, sends anything somewhere the user did not choose, moves money, or is harmful. If the model cannot give a clear answer after three tries, the skill is refused with "Our checks couldn't finish reading this skill. Make it shorter and plainer, then upload it again." While the monthly review budget is used up, or the review service cannot be reached, uploads wait in `checking`; they are never published unchecked, and the wait never counts against them. An upload still waiting 35 days after it arrived expires, and its creator uploads it again. The same refused bytes sent again get the same answer without another review.

Each publisher name gets 30 model reviews a month (the team's name has no limit). Past that, an upload is refused with "This account has used this month's checks. Upload it again next month." A package the checks cannot read at all is refused with "Our checks couldn't read this skill. Make it plainer, then upload it again."

A version number names one package for good. While one version waits for checks, another upload of the same or a lower version is refused with "this version is already waiting for checks; use a higher version number". If two uploads of one version still arrive together (two team members at once), the first is listed and the other is refused with "Version X is not higher than X, which is already listed. Use a higher version number."

Other refusals the page shows as they come: "accept the Skill Codex agreement to publish", "Skill Codex skills use the MIT licence", "new accounts can publish 14 days after they start; yours can from YYYY-MM-DD" (counted from the older of the GitHub account and the Skill Codex account) and "this creator can't publish right now" (a suspended creator).

Catalog rows carry two marks the team sets by hand. `tested: true` means the team marked this exact version; it releases the version's pictures and is not a badge, so draw no label from it (see [Team badges](#team-badges)). An update drops the mark until the team marks the new version. `highlighted: true` puts the skill in the Highlighted spot. Neither is set by stars, downloads or the creator. Pictures of an automatically admitted skill show once that version is marked Tested; until then its creator sees them in `pictures_pending` and everyone else sees the placeholder tile. Team members publish under the team's name (`vool-team`), whose profile has `origin: "official"`; nobody else can choose that name.

## Team badges

`GET /registry/api/badges` (no parameters, public) returns exactly `{issuers, badges, stamp}`. Each is a signed envelope `{signed, signatures: [{keyid, sig}]}`, or `null` before the first badge document. This is the only source of the two badges, "Tested by VOOL" and "Editor's Choice". `/catalog`, `/presentation` and `/snapshot` are unchanged and carry no badge.

- `issuers.signed` is `{schema: "vool.registry.badge_issuers.v1", version, expires, keys: {keyid: public key}, threshold}`, signed by the trust root's `root` role. `threshold` is never below 2.
- `badges.signed` is `{schema: "vool.registry.badges.v1", sequence, previous_sha256, issued_at, expires, badges, team_releases, revoked}`, signed by at least `threshold` different keys of `issuers`. Each entry of `badges` is `{badge: "tested" | "editors_choice", kind: "skill" | "plugin", id, version, digest_type: "artifact" | "package" | "skill-file", sha256, issued_at, expires, targets: [{version, platform}], evidence_sha256, issued_by: [keyid, keyid], note}`. `revoked` lists withdrawn checksums.
- `stamp.signed` is `{schema: "vool.registry.badges_stamp.v1", sequence, badges_sha256, issued_at, expires}`, signed by the `admission` role; `badges_sha256` is the SHA-256 of the canonical JSON of `badges.signed`, and `expires` is at most seven days after `issued_at`.

A reader shows a badge for a row only when all of this holds: the issuer list verifies against the trust root and has not run out; the document carries valid signatures from two different listed keys and has not run out; the stamp verifies, names this document and has not run out; the entry has not run out, both keys in its `issued_by` are on the list and its `sha256` is not in `revoked`; and the entry's `kind`, `id`, `version` and `sha256` equal the row's (`digest_type: "artifact"` is the row's `sha256`). "Tested by VOOL" is shown to a VOOL app only when its own version and platform are in `targets`; a website shows it with the tested builds. A signature is Ed25519 over the canonical JSON of `signed`: keys sorted, no spaces, ASCII with `\u` escapes. A new version of a skill has another checksum and so no badge.

The signer delivers the document with `POST /registry/api/authority/badges` (authority request signature, body `{issuers, badges, stamp}`, answer `{sequence}`). The service verifies all three as above and takes a document only when it follows the one in place: the next number, naming the stored document in `previous_sha256`. The same document with a newer stamp is taken; an older issuer list, an older stamp or another document under a used number is refused with `{error}`. How the team issues, renews and withdraws a badge is in [OPERATIONS.md](OPERATIONS.md#the-two-team-badges).

## Catalog search, filters, sort and labels

`GET /registry/api/catalog` accepts `q` (up to 100 characters), `category`, `subcategory`, `origin` (`official` or `community`) and `sort` (`stars`, default; `updated`; `downloads`; `newest`). Unknown or repeated parameters are refused. Search matches every word against the package name, summary, and category and subcategory labels, so a job description such as "summarize meetings" finds a meeting skill whose name differs. Results are not paginated.

Each row adds `published_at` (first listed version), `origin`, `featured`, `editors_pick` and `community_reviews: [{reviewer, role, version, date}]`. `featured` and `editors_pick` choose what a home page puts first; neither is a badge. Only reviews of the row's exact version are listed. `role` is `team` or `community`. `reviewed: true` means the automated admission checks passed; it is not a statement that a person reviewed the skill.

Rows carry the optional `title` when the author set one; show it as plain text and fall back to the package name. For a signed-in viewer every catalog and profile row also has `starred: true|false`; signed out, the field is absent.

## Languages, needs and tested builds

All of these come from the signed entry, so the website and the app show the same facts.

- `languages` and `needs` are the author's signed metadata. Versions signed before the fields existed have neither; show nothing for them rather than a guess.
- `requirements: {network, credentials, permissions, dependencies}` is read from the package itself, not declared by the author. `network` is true when one of the package's own tools reaches the network. `credentials` lists credential binding names only, never values. `dependencies` names registry packages by package id and other required plugins by their plugin id.
- `targets: [{version, commit, platform}]` lists the VOOL builds covered by the signed compatibility checks; this does not establish observed execution on each binary/platform. `platform` is one of `macos-arm64`, `macos-x86_64`, `linux-x86_64` or `windows-x86_64`.

`GET /registry/api/requirements` returns `{requirements: [{id, label, kind, providers}]}`. `kind` is `provider` when the need is met by one of the listed services' keys in VOOL, or `vool` when VOOL itself provides it or the user allows it. A skill that needs a keyed Solana data service such as Helius declares `chain_data`; `solana_rpc` means it uses VOOL's own Solana connection, whatever RPC address the user set.

`GET /registry/api/categories` returns the fixed category and subcategory list with display labels; read it rather than copying it, because new slugs may be added (existing slugs are never renamed or removed). `GET /registry/api/profiles/{namespace}` returns `{profile, origin, packages}`, where packages are that publisher's listed catalog rows.

## Pictures

A skill may carry one square `icon` and one wide `banner`; both are optional. They are PNG files inside the signed package, never SVG, GIF, JPEG, WebP, animation or a link to another site. The package metadata names each one by its path and checksum, so the signature covers the exact bytes. The icon is square, 128 to 1024 pixels, at most 256 KB. The banner is 960 to 3000 pixels wide, 240 to 1200 tall and 2.5 to 4 times as wide as tall (1500 x 500 works), at most 1 MB. VOOL decodes every picture in full without an image library and checks its size from the header first. Hidden notes saved inside the file (text, camera details, location, editor data), colour profiles, animation, data after the image and damaged image data are refused. WebP stays off until VOOL can check its picture data the same way.

A picture shows after a person approves its exact skill/checksum with `picture publisher/name sha256` in `team-steps.txt`, or the fallback `approved_pictures` entry in `curation.json` (see [OPERATIONS.md](OPERATIONS.md#picture-review)). An approval belongs to one skill: another skill carrying the same file needs its own. Until then the row has no field for that picture. When the skill's creator is signed in, their own rows also carry `pictures_pending`, for example `["icon", "banner"]`, so the website can tell them; other rows never carry it. This keeps pages tidy rather than secret: the signed snapshot names every picture's checksum. The creator is the account whose public profile holds the publisher name, or the account whose website submission was published as that skill. Once approved, the row has `icon` and/or `banner` as `{sha256, type, width, height, url}`.

`GET /registry/api/pictures/{publisher}/{name}/{sha256}` returns the bytes with type `image/png`, `Cache-Control: public, max-age=300` and `Cross-Origin-Resource-Policy: same-origin`. It serves only an approved picture of that skill while the current signed catalog lists a version carrying it; anything else gets HTTP 400 `{"error": "picture not found"}`. Show a row's `url` in an `<img>`; never build a picture address from any other field. Removing a signed `picture` line hides that approval without a restart; fallback-file changes take effect on restart. Browsers drop cached copies within five minutes.

Where a skill has no approved icon, show the placeholder tile instead: [assets/skill-placeholder.svg](assets/skill-placeholder.svg) on light pages and [assets/skill-placeholder-dark.svg](assets/skill-placeholder-dark.svg) on dark ones, and [assets/skill-banner-placeholder.svg](assets/skill-banner-placeholder.svg) or [assets/skill-banner-placeholder-dark.svg](assets/skill-banner-placeholder-dark.svg) for a skill page without a banner. They use warm neutral colours; a page that puts the file's markup inline can recolour it with the CSS properties `--skill-placeholder-bg`, `--skill-placeholder-shape` (banner only) and `--skill-placeholder-mark`. The VOOL app shows approved icons on Skill Codex cards only after the person presses Browse. It fetches each listed icon through VOOL, checks it against the signed listing and the rules above, keeps it in memory only and shows its own tile otherwise. Searching sends no picture requests, and a picture whose approval was taken away disappears from VOOL at the next Browse. VOOL never shows creator pictures on the install review screen. The banner is for the website's skill page.

## Curation

`registry_service/curation.json` supplies fallback curation. The signer's `team-steps.txt` supplies a complete signed replacement: `official`, `featured`, `editors-pick`, `reviewer`, `review` and `picture` lines. Once signed curation exists, the service applies it without a restart; clearing the last curation line leaves an empty signed set instead of resurrecting old fallback approvals. Copy retained static labels/approvals into the file on first migration. Only the appointed reviewer, exact version and date appear; creators cannot review their own skill. Curation never admits a package. The app verifies the marks before showing editorial placement, and signed marks sequence checks refuse rollback. Popularity is separate display data.

## Comments

| Request | Contract |
|---|---|
| `GET /comments?id=publisher/name` | `{comments:[{id, author, created, body}], can_comment}`. A signed-in viewer also receives `mine:[{id, state, created, body}]`, which includes their own held comments, and, when `can_comment` is false, `can_comment_reason`: `no_public_name`, `not_downloaded` or `muted`. Signed out, there is no reason code (`GET /me` says the viewer is signed out). |
| `POST /comments` | `{id, body}`. Returns HTTP 201 `{id, state, reason}` with state `published` or `held`, or HTTP 400 `{state: "refused", reason}`. |
| `POST /comments/{id}/report` | `{status: "reported"}`. One report per account; authors cannot report themselves. Reports from three different people hold the comment for an operator. A report counts only from an account at least 14 days old that is not muted; every report is kept for the operator either way. |

Only a signed-in account with a public name, which downloaded that package through the website while signed in, can comment. Native-app and anonymous downloads do not count. Each account can make five comment attempts per day, refused attempts included. A refused comment's text is not stored.

The server filter in `registry_service/moderation.py` refuses links, domains (also spelled out, such as "example dot com slash page", "(dot)" and "[dot]"), email addresses, @handles, phone numbers, crypto addresses, seed or key requests, giveaways and other scam phrases, promotions, cursing, slurs, sexual content and illegal sales, including spaced, dotted, leetspeak and zero-width-character obfuscation. It holds for review: text mostly outside Latin script (the term lists are English), shouting, character floods, and every comment from an account younger than one day. Three reports hide a published comment until an operator acts.

The filter lists are English and heuristic. Held comments and reports are the backstop, not a guarantee.

Operators use signed GitHub `team-steps.txt` actions (`comment-publish`, `comment-remove`, `mute`, `unmute`) without a service restart. Browser sessions have no operator authority. The private server CLI remains a fallback:

```sh
vool-registry comments-held --config CONFIG
vool-registry comment-publish --config CONFIG --target COMMENT_ID
vool-registry comment-remove --config CONFIG --target COMMENT_ID   # keeps no text
vool-registry mute --config CONFIG --target NAMESPACE                # hides their comments
vool-registry unmute --config CONFIG --target NAMESPACE
```

## Skill reports

Anyone signed in can report a skill from the website. A report holds the skill's ID, one reason and an optional note the person typed, and it is tied to the sender's account and the time it was sent; the account is never shown, not even to operators. The website sends nothing else. The VOOL app only opens the report page (`/codex/skills/report?id=publisher%2Fname`) in the browser and sends nothing itself.

| Request | Contract |
|---|---|
| `GET /reports?id=publisher/name` | `{reasons:[{id, label}], note_limit, can_report}`. When `can_report` is false, `can_report_reason` says why: `signed_out` (also for an ended session), `own_skill`, `already_reported` or `daily_limit`. |
| `POST /reports` | Same-origin JSON `{id, reason, note?}`. Returns HTTP 201 `{status: "received"}`, or HTTP 400 `{error}`. |

Both answer HTTP 400 `{"error": "this skill is not listed"}` for a skill the catalog does not list right now, including one that was published and later taken out of the listing, `{"error": "catalog freshness expired"}` while the catalog itself is out of date, and `{"error": "approved catalog is not configured"}` before the server has one. `POST` checks the sign-in, origin and request body first.

The reasons are `scam`, `harmful`, `stolen`, `picture`, `broken` and `other`; read their labels from `GET /reports`. `other` needs a note. A note is plain text of at most 500 characters and may name a link, for example to the original of a copied skill. A note that looks like it holds a key, password or recovery phrase (also numbered, split up or with a typo) is refused and not stored. This check can miss things, so the form asks people not to paste them. Each account can report a skill once while its earlier report is open, at most ten reports in any 24 hours, and never a skill it publishes.

Reports are private. No page shows how many reports a skill has, their reasons or who sent them. Open `scam`, `harmful` or `stolen` reports from three different people pause downloads of every version of that skill. A report counts only from an account at least 14 days old, that is not muted, from a person who has not already reported that skill in a report an operator closed. One person counts once: an account with several sign-ins is one person, and so is someone who deleted their account and signed in again. A pause that only reports caused ends by itself 48 hours after it began, unless the team confirmed it with `skill-pause`; it does not start again until an operator has closed those reports. Two kinds of skill are never paused by reports alone: a skill under a team's publisher name, and a skill whose listed version carries the Tested mark. Their reports are kept and shown to the operator, who can pause by hand. A pause refuses new download tickets and tickets already handed out, with HTTP 400 `{"error": "downloads of this skill are paused while a person looks into it"}`, and the skill's catalog row carries `downloads_paused: true`. The skill stays listed, because only the signed catalog decides what is listed. Other reports reach the operator but never pause a skill. VOOL says in the person's language that downloads are paused when someone tries to download or install a paused skill, including one downloaded for inspection before the pause. Installing that inspected copy asks the registry again, so it waits until VOOL can reach the registry.

Operators receive private aggregate counts and opaque report IDs through signed authority HTTP requests. Signed `team-steps.txt` actions close reports, pause or resume downloads, and mute a reporter by opaque ID. Browser sessions have no operator authority. Full private report text stays in the server CLI; reporter identity is never returned. `reports-open` shows, for each skill: `paused` (the cause while downloads are paused now), `paused_since`, and for a pause from reports `pause_lapses` (when it ends) and `pause_lapsed: true` once it has ended; `counting`, the number of different people whose reports count; and `protected: "team"` or `"tested"` when that is why nothing was paused. Each report shows `account_deleted: true` when its sender has since deleted their account. The CLI also provides these fallback actions:

```sh
vool-registry reports-open --config CONFIG                           # every skill with open reports or a pause
vool-registry reports-close --config CONFIG --target PUBLISHER/NAME  # looked at: closes them, wipes notes, ends a pause they caused
vool-registry skill-pause --config CONFIG --target PUBLISHER/NAME    # an operator's pause; reports-close does not end it
vool-registry skill-resume --config CONFIG --target PUBLISHER/NAME   # ends any pause and closes open reports
vool-registry reporter-mute --config CONFIG --target REPORT_ID       # the person behind that report stops counting
```

## Deleting an account

`DELETE /session` deletes the account, its sign-ins, sessions, uploads that were not published, comments, stars and the notes typed into reports. Published skills stay public under their MIT licence, and the public name stays reserved.

One row stays for each sign-in the account had. It is keyed by a hash that only this service can make from the provider and the provider's account number, with a private key the service keeps. The row holds: when the account was deleted, whether the person was muted, the times of their uploads and comments in the last day, and a hash of each public name they held, made with the same private key. It holds no name in words, no provider id, no email and no text. The person's reports about skills stay as a reason and a count under the same hash, so a pause can always be traced and nobody counts twice.

The row is used for four things when the same sign-in comes back:

- A mute returns with it. `unmute` ends it as usual.
- A suspension follows the old name. While a name the person held is on the suspended list, they cannot publish under any name, with "this creator can't publish right now". Suspending the old name after they left still reaches them; taking the old name off the list lifts it.
- The daily limits for uploads, comments and reports continue where they stood.
- They wait 30 days from the deletion before choosing a new public name. `PUT /profile` answers HTTP 400 `{"error": "an account with this sign-in was deleted; you can choose a public name from YYYY-MM-DD"}`; show it as it comes. Linking a name that already publishes through GitHub (below) has no wait, because that is the person's old name, not a new one.

A row is forgotten 30 days after the deletion, at the service's next clean-up, unless the person was muted or a name they held is suspended at that moment. A suspension given after the row is gone no longer finds the person; suspend within those 30 days.

## Publishing from VOOL

In the VOOL app, My add-ons > Skills shows a quiet "Publish to Skill Codex" button on each skill the person made: one in their own skill folders (`local-skills`, or a chat's or project's) that was not installed from an imported file. Skills that ship with VOOL, skills in any other package (including packs brought in from a file), skills reached through a link, skills whose version history says they were imported (from VOOL 0.7.2 on; earlier imports cannot be told apart, and the website's agreement covers them), skills too long for the form and skills marked `local-only: true` get no button. The button runs the written-skill form's own checks on the device, in a temporary folder it removes, with placeholder package details: `initialize`, `pack` and `preflight` as `POST /drafts` does, then the automatic checks the service runs on a draft sent with the agreement (see [Publishing with automatic checks](#publishing-with-automatic-checks)). Their reasons are shown in their own words, with the SKILL.md line and the words that tripped them, as the website shows them. As on the service, text that looks like a secret stops the check there. A skill folder holding other files besides SKILL.md is refused, because the form carries one SKILL.md. VOOL's own version history next to SKILL.md is not counted. When the checks pass, VOOL opens this link in the browser and sends nothing itself:

```
ORIGIN/codex/skills/publish#skill=<base64url of UTF-8 JSON, no padding>
JSON: {"v":1,"name":"<skill folder name>","title":"<display name>","summary":"<description>","skill_markdown":"<SKILL.md text>"}
```

`ORIGIN` is the Skill Codex address in `core/registry/client.py`. The fragment never reaches a server. The publish page decodes it, then at once removes it from the address bar with `history.replaceState`, so the skill text does not stay in the page address. It fills the written-skill form (stable name from `name`, `title`, `summary`, SKILL.md from `skill_markdown`), and the person signs in, chooses a category, ticks the MIT agreement and sends it as `POST /drafts` with `"agreement": "mit-own-work-v1"`. The page must not send the fragment anywhere else or log it, and must ignore an unknown `v`. Sign-in leaves the page, so only for that round trip the page may keep the draft in `sessionStorage` under `vool-codex-handoff`, and it deletes it as soon as the page loads again; nothing goes in `localStorage`, cookies or analytics. VOOL calls a skill ready only when the request the page sends for it, with the person's publisher name and category, fits the 32,768 bytes `POST /drafts` takes (text in Japanese, for example, needs three bytes a letter); the page still counts the body as for any draft. VOOL's check uses no account, so the account checks (account age, publisher name, suspension) and the model review still apply on the website.

## Updates

Public package metadata is checked with the package text: ID/name, title, summary, release notes, licence label, categories, languages, requirements, source links and picture descriptors. `@metadata.json` identifies outer metadata findings and is included in model review. Unknown fields, invalid types, oversized values and malformed Unicode are refused before signing. Public metadata is always prose for instruction checks, including text beginning with `./`; package-path exemptions never apply to these fields. Secret checks read decoded metadata text, so JSON escaping does not hide it; findings never quote detected secret fragments. Verified picture checksum slots are excluded only from secret detection; the same text elsewhere is still checked.

VOOL rechecks the same public text before installation. It reads the installed receipt against the root recorded at installation, so a valid key rotation permits a higher version without admitting reused or lower versions. An unreadable or inconsistent receipt refuses the update and preserves the installation. Skill Codex can open with an expired shipped root only when a valid, unexpired persisted successor chains from it; that availability check reads local state without connecting or writing.

An update keeps the stable package ID and must use a higher version than every published one. The server refuses a reused or lower version before scanning. Stars, download requests and comments stay with the stable ID.

## GitHub import

`POST /registry/api/imports` takes `{repository, ref, path, metadata, publish_when_checks_pass}`. It requires a linked GitHub sign-in, and the public repository must be owned by that GitHub account (owner ID match). The server resolves `ref` to an exact commit and downloads that commit's archive from GitHub. It unpacks only `path` as inert data, refusing links, special files, ambiguous paths and oversized folders. It packages the folder's `SKILL.md`, other files and `LICENSE` with source provenance `{repository, commit, path}`, then submits it like an upload. The response is the submission plus `source`. `metadata` may name `icon` and `banner` by their path inside the imported folder, for example `"icon": "assets/icon.png"`; the server records each file's checksum. A skill written in the website form (`POST /drafts`) has no picture files and refuses these fields.

`publish_when_checks_pass` remains an accepted compatibility field and never grants consent. Every import starts without automatic consent. Show the returned exact checksum and obtain a separate unticked Publish decision through `POST /submissions/{id}/consent` with `{sha256, public: true}`. Only those consented bytes enter automatic admission. Server-built imports establish the account's publisher association on publication, just as website drafts do.

Import is off unless the service configuration sets `"github_import": true`. It contacts only `api.github.com` and `codeload.github.com`, refuses redirects, and reads at most 16 MiB. It uses no GitHub token, so GitHub's anonymous rate limit applies per server address. Private repositories and organisation-owned repositories are not supported in this version.

## GitHub publisher link

A name that already publishes through GitHub (a direct pull request to the community repository) is reserved: nobody can choose it on their profile, and drafts from an unlinked account are refused with "this publisher name already publishes through GitHub; link it with the GitHub account that owns those repositories". The page should then offer the link step.

`POST /registry/api/publisher-link` takes `{namespace}` and returns `{namespace, verified: true}`. The account must be signed in with (or have linked) GitHub. Every published version under that name must come from a public repository whose GitHub owner is that GitHub account, or from bytes the server built for this same account. The account must not already use a different name, and no other account may hold the name. Anything else is refused and left to a maintainer, as are organisation-owned repositories. After linking, the name is the account's verified publisher name, its profile shows on the name's page, and drafts and imports work.

The route uses the same read-only GitHub lookups as import and is off unless `"github_import": true` is set. It makes one owner lookup per distinct source repository (at most 20) and downloads nothing.
