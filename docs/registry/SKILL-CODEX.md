# Skill Codex server routes

The website's Skill Codex pages use these routes in addition to [WEBSITE-HANDOVER.md](WEBSITE-HANDOVER.md). The same rules apply: same-origin requests, `X-Vool-CSRF` on every change, plain-text rendering of every user-written field, and `{error}` with HTTP 400 on refusal.

## Publishing with automatic checks

When the service runs with the signing Mac (see [OPERATIONS.md](OPERATIONS.md#automatic-checks-with-the-signing-mac)), a creator publishes without waiting for a person. Before the first upload the page shows this agreement with an unticked box, and sends nothing until the creator ticks it:

> I made this skill, or have the right to share it, and I release it under the MIT licence. Anyone may use, copy, change and share it.

The ticked box becomes `"agreement": "mit-own-work-v1"`:

- The written-skill form sends `POST /drafts` with exactly `{metadata, skill_markdown, agreement}`. `metadata.license` must be `MIT`; the service writes the MIT licence text in the publisher's name, so the form has no licence field.
- An uploaded package (`POST /submissions`) carries the header `X-Vool-Agreement: mit-own-work-v1`. The package must hold the MIT licence text and an upload source `{upload_id, namespace}`. A package that names a GitHub repository is refused with "this package names a GitHub source; publish it with GitHub import instead".

Without the agreement both routes keep the older reviewed flow, and nothing is published automatically.

The server checks every upload at once. A clean one has state `checking` while the signing Mac checks it again and asks a review model to read it. Then it becomes `refused` with reasons, or `admitted` and soon `published` in the category the creator chose. A refusal's `findings` are `{check, path, reason, line?, quote?}`. Show `reason` as written, with the file and line, and `quote` as plain monospace text so the creator sees the words that tripped the check. A line that looks like a key, password or recovery phrase is never quoted back. Skill Codex skills:

- hold Markdown or plain-text files under `skills/`, the licence, `registry.json`, the manifest and optional pictures, and nothing that runs: no scripts, programs, executable files, manifest commands, permissions or credential bindings;
- never ask anyone to install, download or run software, paste commands into a terminal, or open a short link, raw address or paste site;
- never ask for a recovery phrase, key, password or code, contain a wallet address, or carry hidden comments, invisible characters or encoded blobs;
- never try to change VOOL's own rules, spending limits or approvals, or hide what they do from the user;
- carry the MIT licence text with only the creator's copyright line, have instructions long enough to be useful, and never call themselves verified, official, tested or endorsed (only the team's own skills may);
- keep all their text under 120,000 characters ("This skill is too long to check. Keep all its text under 120,000 characters.").

The checks read the instructions, summary and manifest the way a person would: a command split over lines with `\` is read as one, and full-width letters, joiners and look-alike letters from other alphabets are read as the plain letters they imitate. A word that mixes alphabets is refused on its own. Commands are refused even inside advice about them; write the advice in words instead.

The review model also refuses a skill that does something its summary does not say, sends anything somewhere the user did not choose, moves money, or is harmful. If the model cannot give a clear answer after three tries, the skill is refused with "Our checks couldn't finish reading this skill. Make it shorter and plainer, then upload it again." While the monthly review budget is used up, or the review service cannot be reached, uploads wait in `checking`; they are never published unchecked, and the wait never counts against them. The same refused bytes sent again get the same answer without another review.

Each publisher name gets 30 model reviews a month (the team's name has no limit). Past that, an upload is refused with "This account has used this month's checks. Upload it again next month." A package the checks cannot read at all is refused with "Our checks couldn't read this skill. Make it plainer, then upload it again."

A version number names one package for good. While one version waits for checks, another upload of the same or a lower version is refused with "this version is already waiting for checks; use a higher version number". If two uploads of one version still arrive together (two team members at once), the first is listed and the other is refused with "Version X is not higher than X, which is already listed. Use a higher version number."

Other refusals the page shows as they come: "accept the Skill Codex agreement to publish", "Skill Codex skills use the MIT licence", "new accounts can publish 14 days after they start; yours can from YYYY-MM-DD" (counted from the older of the GitHub account and the Skill Codex account) and "this creator can't publish right now" (a suspended creator).

Catalog rows carry two marks the team sets by hand. `tested: true` means the team tested this exact version; show it as "Tested by Parad0x Labs". An update drops the mark until the team tests the new version. `highlighted: true` puts the skill in the Highlighted spot. Neither is set by stars, downloads or the creator. Pictures of an automatically admitted skill show once that version is marked Tested; until then its creator sees them in `pictures_pending` and everyone else sees the placeholder tile. Team members publish under the team's name (`vool-team`), whose profile has `origin: "official"`; nobody else can choose that name.

## Catalog search, filters, sort and labels

`GET /registry/api/catalog` accepts `q` (up to 100 characters), `category`, `subcategory`, `origin` (`official` or `community`) and `sort` (`stars`, default; `updated`; `downloads`; `newest`). Unknown or repeated parameters are refused. Search matches every word against the package name, summary, and category and subcategory labels, so a job description such as "summarize meetings" finds a meeting skill whose name differs. Results are not paginated.

Each row adds `published_at` (first listed version), `origin`, `featured`, `editors_pick` and `community_reviews: [{reviewer, role, version, date}]`. Only reviews of the row's exact version are listed. `role` is `team` or `community`. `reviewed: true` means the automated admission checks passed; it is not a statement that a person reviewed the skill.

Rows carry the optional `title` when the author set one; show it as plain text and fall back to the package name. For a signed-in viewer every catalog and profile row also has `starred: true|false`; signed out, the field is absent.

## Languages, needs and tested builds

All of these come from the signed entry, so the website and the app show the same facts.

- `languages` and `needs` are the author's signed metadata. Versions signed before the fields existed have neither; show nothing for them rather than a guess.
- `requirements: {network, credentials, permissions, dependencies}` is read from the package itself, not declared by the author. `network` is true when one of the package's own tools reaches the network. `credentials` lists credential binding names only, never values. `dependencies` names registry packages by package id and other required plugins by their plugin id.
- `targets: [{version, commit, platform}]` lists the VOOL builds the signed entry was tested with. `platform` is one of `macos-arm64`, `macos-x86_64`, `linux-x86_64` or `windows-x86_64`.

`GET /registry/api/requirements` returns `{requirements: [{id, label, kind, providers}]}`. `kind` is `provider` when the need is met by one of the listed services' keys in VOOL, or `vool` when VOOL itself provides it or the user allows it. A skill that needs a keyed Solana data service such as Helius declares `chain_data`; `solana_rpc` means it uses VOOL's own Solana connection, whatever RPC address the user set.

`GET /registry/api/categories` returns the fixed category and subcategory list with display labels; read it rather than copying it, because new slugs may be added (existing slugs are never renamed or removed). `GET /registry/api/profiles/{namespace}` returns `{profile, origin, packages}`, where packages are that publisher's listed catalog rows.

## Pictures

A skill may carry one square `icon` and one wide `banner`; both are optional. They are PNG files inside the signed package, never SVG, GIF, JPEG, WebP, animation or a link to another site. The package metadata names each one by its path and checksum, so the signature covers the exact bytes. The icon is square, 128 to 1024 pixels, at most 256 KB. The banner is 960 to 3000 pixels wide, 240 to 1200 tall and 2.5 to 4 times as wide as tall (1500 x 500 works), at most 1 MB. VOOL decodes every picture in full without an image library and checks its size from the header first. Hidden notes saved inside the file (text, camera details, location, editor data), colour profiles, animation, data after the image and damaged image data are refused. WebP stays off until VOOL can check its picture data the same way.

A picture shows only after a person has looked at that exact file for that skill and added `{"id": "<publisher>/<name>", "sha256": "<checksum>"}` to `approved_pictures` in `curation.json` (see [OPERATIONS.md](OPERATIONS.md#picture-review)). An approval belongs to one skill: another skill carrying the same file needs its own. Until then the row has no field for that picture. When the skill's creator is signed in, their own rows also carry `pictures_pending`, for example `["icon", "banner"]`, so the website can tell them; other rows never carry it. This keeps pages tidy rather than secret: the signed snapshot names every picture's checksum. The creator is the account whose public profile holds the publisher name, or the account whose website submission was published as that skill. Once approved, the row has `icon` and/or `banner` as `{sha256, type, width, height, url}`.

`GET /registry/api/pictures/{publisher}/{name}/{sha256}` returns the bytes with type `image/png`, `Cache-Control: public, max-age=300` and `Cross-Origin-Resource-Policy: same-origin`. It serves only an approved picture of that skill while the current signed catalog lists a version carrying it; anything else gets HTTP 400 `{"error": "picture not found"}`. Show a row's `url` in an `<img>`; never build a picture address from any other field. Taking an approval away hides the picture once the service restarts, and browsers drop a cached copy within five minutes.

Where a skill has no approved icon, show the placeholder tile instead: [assets/skill-placeholder.svg](assets/skill-placeholder.svg) on light pages and [assets/skill-placeholder-dark.svg](assets/skill-placeholder-dark.svg) on dark ones, and [assets/skill-banner-placeholder.svg](assets/skill-banner-placeholder.svg) or [assets/skill-banner-placeholder-dark.svg](assets/skill-banner-placeholder-dark.svg) for a skill page without a banner. They use warm neutral colours; a page that puts the file's markup inline can recolour it with the CSS properties `--skill-placeholder-bg`, `--skill-placeholder-shape` (banner only) and `--skill-placeholder-mark`. The VOOL app shows approved icons on Skill Codex cards only after the person presses Browse. It fetches each listed icon through VOOL, checks it against the signed listing and the rules above, keeps it in memory only and shows its own tile otherwise. Searching sends no picture requests, and a picture whose approval was taken away disappears from VOOL at the next Browse. VOOL never shows creator pictures on the install review screen. The banner is for the website's skill page.

## Curation

`registry_service/curation.json` names the VOOL team's publishing namespaces, the featured and editor's-pick package IDs, the appointed reviewers and their reviews of exact versions, and the checksums of approved skill pictures (`approved_pictures`). It changes only through a reviewed pull request and is validated at startup; a creator cannot review their own skill. Curation labels never admit a package: only rows from the signed catalog appear. Before listing a namespace as official, claim it with the team's own account so nobody else can hold it.

## Comments

| Request | Contract |
|---|---|
| `GET /comments?id=publisher/name` | `{comments:[{id, author, created, body}], can_comment}`. A signed-in viewer also receives `mine:[{id, state, created, body}]`, which includes their own held comments, and, when `can_comment` is false, `can_comment_reason`: `no_public_name`, `not_downloaded` or `muted`. Signed out, there is no reason code (`GET /me` says the viewer is signed out). |
| `POST /comments` | `{id, body}`. Returns HTTP 201 `{id, state, reason}` with state `published` or `held`, or HTTP 400 `{state: "refused", reason}`. |
| `POST /comments/{id}/report` | `{status: "reported"}`. One report per account; authors cannot report themselves. |

Only a signed-in account with a public name, which downloaded that package through the website while signed in, can comment. Native-app and anonymous downloads do not count. Each account can make five comment attempts per day, refused attempts included. A refused comment's text is not stored.

The server filter in `registry_service/moderation.py` refuses links, domains (also spelled out, such as "example dot com slash page", "(dot)" and "[dot]"), email addresses, @handles, phone numbers, crypto addresses, seed or key requests, giveaways and other scam phrases, promotions, cursing, slurs, sexual content and illegal sales, including spaced, dotted, leetspeak and zero-width-character obfuscation. It holds for review: text mostly outside Latin script (the term lists are English), shouting, character floods, and every comment from an account younger than one day. Three reports hide a published comment until an operator acts.

The filter lists are English and heuristic. Held comments and reports are the backstop, not a guarantee.

Operators moderate on the server, never over HTTP:

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

Reports are private. No page shows how many reports a skill has, their reasons or who sent them. Three open `scam`, `harmful` or `stolen` reports pause downloads of every version of that skill. They count only from accounts older than one day, that are not muted, and that have not already reported that skill in a report an operator closed. A pause refuses new download tickets and tickets already handed out, with HTTP 400 `{"error": "downloads of this skill are paused while a person looks into it"}`, and the skill's catalog row carries `downloads_paused: true`. The skill stays listed, because only the signed catalog decides what is listed. Other reports reach the operator but never pause a skill. VOOL says in the person's language that downloads are paused when someone tries to download or install a paused skill, including one downloaded for inspection before the pause. Installing that inspected copy asks the registry again, so it waits until VOOL can reach the registry.

Operators read and act on reports on the server, never over HTTP:

```sh
vool-registry reports-open --config CONFIG                           # every skill with open reports or a pause
vool-registry reports-close --config CONFIG --target PUBLISHER/NAME  # looked at: closes them, wipes notes, ends a pause they caused
vool-registry skill-pause --config CONFIG --target PUBLISHER/NAME    # an operator's pause; reports-close does not end it
vool-registry skill-resume --config CONFIG --target PUBLISHER/NAME   # ends any pause and closes open reports
vool-registry reporter-mute --config CONFIG --target REPORT_ID       # the account behind that report stops counting
```

## Updates

An update keeps the stable package ID and must use a higher version than every published one. The server refuses a reused or lower version before scanning. Stars, download requests and comments stay with the stable ID.

## GitHub import

`POST /registry/api/imports` takes `{repository, ref, path, metadata, publish_when_checks_pass}`. It requires a linked GitHub sign-in, and the public repository must be owned by that GitHub account (owner ID match). The server resolves `ref` to an exact commit and downloads that commit's archive from GitHub. It unpacks only `path` as inert data, refusing links, special files, ambiguous paths and oversized folders. It packages the folder's `SKILL.md`, other files and `LICENSE` with source provenance `{repository, commit, path}`, then submits it like an upload. The response is the submission plus `source`. `metadata` may name `icon` and `banner` by their path inside the imported folder, for example `"icon": "assets/icon.png"`; the server records each file's checksum. A skill written in the website form (`POST /drafts`) has no picture files and refuses these fields.

When `publish_when_checks_pass` is `true`, the author's consent is bound to the exact server-built checksum, and the submission moves to `ready_for_github` as soon as the prepublication checks pass. Otherwise the normal consent step follows. Server-built imports establish the account's publisher association on publication, just as website drafts do.

Import is off unless the service configuration sets `"github_import": true`. It contacts only `api.github.com` and `codeload.github.com`, refuses redirects, and reads at most 16 MiB. It uses no GitHub token, so GitHub's anonymous rate limit applies per server address. Private repositories and organisation-owned repositories are not supported in this version.

## GitHub publisher link

A name that already publishes through GitHub (a direct pull request to the community repository) is reserved: nobody can choose it on their profile, and drafts from an unlinked account are refused with "this publisher name already publishes through GitHub; link it with the GitHub account that owns those repositories". The page should then offer the link step.

`POST /registry/api/publisher-link` takes `{namespace}` and returns `{namespace, verified: true}`. The account must be signed in with (or have linked) GitHub. Every published version under that name must come from a public repository whose GitHub owner is that GitHub account, or from bytes the server built for this same account. The account must not already use a different name, and no other account may hold the name. Anything else is refused and left to a maintainer, as are organisation-owned repositories. After linking, the name is the account's verified publisher name, its profile shows on the name's page, and drafts and imports work.

The route uses the same read-only GitHub lookups as import and is off unless `"github_import": true` is set. It makes one owner lookup per distinct source repository (at most 20) and downloads nothing.
