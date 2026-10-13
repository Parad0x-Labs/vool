# Skill Codex server routes

The website's Skill Codex pages use these routes in addition to [WEBSITE-HANDOVER.md](WEBSITE-HANDOVER.md). The same rules apply: same-origin requests, `X-Vool-CSRF` on every change, plain-text rendering of every user-written field, and `{error}` with HTTP 400 on refusal.

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

`GET /registry/api/categories` returns the fixed ten-category list with display labels. `GET /registry/api/profiles/{namespace}` returns `{profile, origin, packages}`, where packages are that publisher's listed catalog rows.

## Curation

`registry_service/curation.json` names the VOOL team's publishing namespaces, the featured and editor's-pick package IDs, the appointed reviewers and their reviews of exact versions. It changes only through a reviewed pull request and is validated at startup; a creator cannot review their own skill. Curation labels never admit a package: only rows from the signed catalog appear. Before listing a namespace as official, claim it with the team's own account so nobody else can hold it.

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

## Updates

An update keeps the stable package ID and must use a higher version than every published one. The server refuses a reused or lower version before scanning. Stars, download requests and comments stay with the stable ID.

## GitHub import

`POST /registry/api/imports` takes `{repository, ref, path, metadata, publish_when_checks_pass}`. It requires a linked GitHub sign-in, and the public repository must be owned by that GitHub account (owner ID match). The server resolves `ref` to an exact commit and downloads that commit's archive from GitHub. It unpacks only `path` as inert data, refusing links, special files, ambiguous paths and oversized folders. It packages the folder's `SKILL.md`, other files and `LICENSE` with source provenance `{repository, commit, path}`, then submits it like an upload. The response is the submission plus `source`.

When `publish_when_checks_pass` is `true`, the author's consent is bound to the exact server-built checksum, and the submission moves to `ready_for_github` as soon as the prepublication checks pass. Otherwise the normal consent step follows. Server-built imports establish the account's publisher association on publication, just as website drafts do.

Import is off unless the service configuration sets `"github_import": true`. It contacts only `api.github.com` and `codeload.github.com`, refuses redirects, and reads at most 16 MiB. It uses no GitHub token, so GitHub's anonymous rate limit applies per server address. Private repositories and organisation-owned repositories are not supported in this version.

## GitHub publisher link

A name that already publishes through GitHub (a direct pull request to the community repository) is reserved: nobody can choose it on their profile, and drafts from an unlinked account are refused with "this publisher name already publishes through GitHub; link it with the GitHub account that owns those repositories". The page should then offer the link step.

`POST /registry/api/publisher-link` takes `{namespace}` and returns `{namespace, verified: true}`. The account must be signed in with (or have linked) GitHub. Every published version under that name must come from a public repository whose GitHub owner is that GitHub account, or from bytes the server built for this same account. The account must not already use a different name, and no other account may hold the name. Anything else is refused and left to a maintainer, as are organisation-owned repositories. After linking, the name is the account's verified publisher name, its profile shows on the name's page, and drafts and imports work.

The route uses the same read-only GitHub lookups as import and is off unless `"github_import": true` is set. It makes one owner lookup per distinct source repository (at most 20) and downloads nothing.
