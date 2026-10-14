# Deployment and admission operations

Production activation is a separate reviewed action. No deployment, repository creation, installs, production key generation or paid scanner calls are performed by the local build.

Use the existing VPS for the static website, API and private account/quarantine database. Candidate execution belongs on isolated disposable workers. Keep the public GitHub artifact cache and signed catalog checkout separate from private state, the website deploy directory and signer custody. Back up each directory and configuration before every change; site deploys must not delete registry data.

## Activation checklist

1. Create the two community repositories from the reviewed blueprints, protect main and require independent review plus an authenticated admission check from its expected external GitHub App (or an independently protected workflow with verified workload identity). Candidate-run blueprint checks are advisory. Follow [ADMISSION-SERVICE.md](ADMISSION-SERVICE.md) and inspect the applied required-check source. Protect workflows, policies, roots and CODEOWNERS. A PR's own modified workflow is not trustworthy admission evidence. Published content must retain its original author and licence.
2. Configure a pinned scanner policy for the actual latest released VOOL build and supported platforms. Supply all eleven required reports from trusted workers. Unsupported coverage remains held. Bind workload identity to protected workflow/code revisions; never expose review/signing credentials to candidate execution.
3. Establish root, admission, review and online-catalog key custody. With automatic checks, the signing Mac holds the root, admission and review keys and the server holds only the online catalog key (see [Automatic checks with the signing Mac](#automatic-checks-with-the-signing-mac)). A deployment can require two or more signers per role instead. Publish only public keys in a reviewed trust anchor. The web process holds no admission or review private key.
4. Private upload → signed archive/secret prepublication reports → author consent to the exact checksum → isolated publisher export → GitHub PR → final exact-commit reports → protected merge → offline admission signatures → offline catalog authorization → short-lived online catalog signature → canonical catalog PR/merge. Rejects remain visible only to the author. Direct GitHub PRs were public before scanning.
5. Register GitHub login with minimal public identity scope, Google with OpenID only, and Apple with a Services ID associated with a configured Sign in with Apple app. Configure exact callback URLs. The provider login token has no publication authority. Use a separate GitHub App with narrowly scoped, short-lived publisher credentials in the isolated publisher process.
6. Configure the service origin and immutable catalog/artifact inputs. Run `vool-registry check-config --config CONFIG` before starting it. The command fails if root, catalog, artifacts or requested provider registrations are missing/stale. Never use test fixtures as a production root or report.
7. Reverse proxy only `/registry/api/` to the localhost service. Preserve the existing relay/routes. Disable access, error, proxy, journal and backup request logging that could retain addresses or callback codes. Audit hosting/provider retention separately; do not claim no party ever processes an IP.
8. Use private directory mode 0700 and database mode 0600. Encrypt off-host backups, restrict operators, expire private quarantine after seven days and verify an actual restore. The restored catalog must meet signature/freshness/rollback rules; old account backups must respect the deletion retention policy.
9. The team's publisher name `vool-team` is reserved. With automatic checks, the signing Mac's `team` command names its members by GitHub account number; members publish under it and its profile shows as official. Without them, sign in with the team's own GitHub account, choose the name on its profile, then list it in `official_namespaces` in `registry_service/curation.json` through a reviewed pull request.
10. Verify actual GitHub/Google/Apple login, account linking/deletion, secret rejection, failed scans, public-consent export, exact GitHub commit verification, signed browse, install/enable, update recovery, tamper/revocation and stats with production registrations on disposable accounts/profiles before opening the service publicly.

## Automatic checks with the signing Mac

The owner's Mac is the signing authority. It checks every upload made with the creator's MIT agreement, signs what passes, and sends the signed list to the service. No person approves uploads; a person only adds the "Tested by Parad0x Labs" mark, the Highlighted spot, suspensions, team members and removals, each behind a code from an authenticator app.

1. On the server, create the online catalog key once: `vool-registry catalog-key --config CONFIG`. It writes `catalog.key` (mode 0600) in the private directory and prints the public key. An existing key is kept.
2. On the Mac, write the released VOOL builds to a file such as `targets.json` (`[{"version", "commit", "platform"}, ...]`), then run `python -m registry_service.mac_authority setup --dir FOLDER --server https://SITE --catalog-key PUBLIC_KEY --targets targets.json --provider openrouter --model MODEL --cap 20`. `FOLDER` must not exist yet; it is created with mode 0700. Choose a passphrase of at least 12 characters for the main (root) key. Add the printed `otpauth://` line to the authenticator app (as a QR code or by typing the key), then type the code it shows to confirm. `--provider ollama` uses a local model at `127.0.0.1:11434` instead, with no spend.
3. Keep a copy of `FOLDER/root.pem` and its passphrase offline. Copy `FOLDER/trusted-root.json` to the server's `trusted_root` path, and into the app release as `core/registry/trusted-root.json`. A build without it keeps the Skill Codex closed.
4. Add `"authority": true` to the service configuration and restart it. Until the Mac's first run there is no list, and Browse says the catalog is not configured. The service re-stamps the list with the online key every six hours.
5. Keep `python -m registry_service.mac_authority run --dir FOLDER --every 300` running on the Mac, with the review key in `VOOL_AUTHORITY_OPENROUTER_KEY` for the paid model. Each run takes waiting uploads, checks them again on the exact bytes and asks the review model; it renews entries 5 days before they expire (after 30 days) and the signed list 3 days before it expires (after 7 days). `--once` runs a single pass, for a scheduler.
6. After each VOOL release, `python -m registry_service.mac_authority targets --dir FOLDER --file targets.json`. The next run re-checks and re-signs every skill for the new builds; a skill today's rules refuse leaves the list.

Hand actions ask for the authenticator code, and a code works once:

```sh
python -m registry_service.mac_authority mark PUBLISHER/NAME --dir FOLDER        # "Tested by Parad0x Labs" on the version listed now
python -m registry_service.mac_authority unmark PUBLISHER/NAME --dir FOLDER
python -m registry_service.mac_authority highlight PUBLISHER/NAME --dir FOLDER   # the Highlighted spot
python -m registry_service.mac_authority unhighlight PUBLISHER/NAME --dir FOLDER
python -m registry_service.mac_authority suspend PUBLISHER --dir FOLDER          # refuses their uploads and waiting submissions
python -m registry_service.mac_authority unsuspend PUBLISHER --dir FOLDER
python -m registry_service.mac_authority team vool-team --github-id ID --github-id ID --dir FOLDER
python -m registry_service.mac_authority remove PUBLISHER/NAME --dir FOLDER      # takes every version out of the list and revokes it
python -m registry_service.mac_authority status --dir FOLDER                     # no code needed
```

What happens when something stops:

- The Mac is off for more than seven days: the signed list expires, and browsing, downloads and installs stop with "catalog freshness expired" until the next run. Nothing unsigned is served.
- The review model is unreachable: the upload keeps waiting, and the outage never counts against it. A model that answers in the wrong shape gets three tries, then the upload is refused with a plain reason. The monthly cap (`monthly_cap` in `FOLDER/config.json`) stops paid calls; uploads then wait for the next month.
- One publisher name sends many uploads: each name gets 30 model reviews a month (`REVIEWS_PER_CREATOR` in `registry_service/mac_authority.py`; the team's name has no limit), then its uploads are refused until the next month. The same refused bytes sent again reuse the first answer.
- A suspended creator's waiting uploads are refused at the next run, and new ones at once.
- A package makes the checks fail in a way nobody planned for: that upload is refused with "Our checks couldn't read this skill", the run goes on with the others, and the Mac's log shows `Refused <checksum> after an unexpected <error>`. Send that line to whoever maintains the checks.

The authenticator code protects the hand-action commands. The admission and review keys are files readable only by the Mac account that runs the signer; the root key is encrypted with its passphrase. Anyone who controls that Mac account can sign, so keep it for this job, with FileVault and a login password. Removing a skill revokes its versions in every VOOL at the next Browse; downloads already made are not taken back.

Limits the owner chose, so say nothing stronger:

- One signature per role is enough, and the admission and review keys have no passphrase so the signer runs unattended. Whoever controls the Mac account can sign.
- The Mac's signed list lasts 7 days. Someone who took over the server could keep serving the last list the Mac signed, without later removals, until those 7 days run out.
- The signed record names the check suite and its major version, not the exact rule text. Tightening a rule does not mark older entries by itself; each renewal checks them again under today's rules.
- One review model reads each skill, and its refusal reasons go back to the creator word for word, as the automatic checks' do. A skill written to fool that one model can pass the model step; the automatic checks still apply to it, and reports and removal are the backstop.

The signed list keeps every version of every skill. VOOL reads a list of at most 4 MB, and one short skill's entry is about 1.3 KB, so plan to take older versions out of the list before it reaches a few thousand versions.

## Service configuration

`vool.registry.service.v1` requires `origin`, `private_dir`, `trusted_root`, `catalog_file`, `artifact_dir`, `target`, `providers`. Target is `{version,commit,platform}` for the released app. Catalog file is the canonical directory containing the signed registry projection. Artifacts are named by full transport checksum. Provider names are `github`, `google`, `apple`; their registrations come from `VOOL_REGISTRY_PROVIDER_CLIENT_ID` and `VOOL_REGISTRY_PROVIDER_CLIENT_SECRET` with PROVIDER uppercase. Apple's client secret is the developer-managed signed JWT and needs its own renewal process.

Start with `vool-registry serve --config CONFIG --port 8791` under a dedicated unprivileged account. It binds 127.0.0.1, ignores forwarded IP headers, disables access/error request logs and returns JSON errors without provider tokens. It imports verified snapshots and public artifacts; its database is a cache, not catalog admission authority. A stale snapshot stops new browse/download/install rather than reusing an unsigned list.

The proxy needs TLS, a 24 MiB body limit, strict host/origin routing, no credentials in URLs and a per-client request limiter. Integrate these excerpts into the existing `http` context and named TLS server; retain its certificates and website/relay routes:

```nginx
# http context: transient client-address state, not a request log.
limit_req_zone $binary_remote_addr zone=registry_per_client:10m rate=5r/s;

server {
    listen 80 default_server;
    server_name _;
    access_log off;
    error_log /dev/null crit;
    return 444;
}
server {
    listen 443 ssl default_server;
    server_name _;
    access_log off;
    error_log /dev/null crit;
    ssl_reject_handshake on;
    return 444;
}

# Inside the existing TLS server with server_name vool.dev:
location /registry/api/ {
    access_log off;
    error_log /dev/null crit;
    client_max_body_size 24m;
    limit_req zone=registry_per_client burst=20 nodelay;
    limit_req_status 429;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For "";
    proxy_pass http://127.0.0.1:8791;
}
```

The website's Skill Codex pages are static files, so nginx sends their framing rule. Add `add_header Content-Security-Policy "frame-ancestors 'none'" always;` to the location that serves `/codex/skills/`, so no other site can frame the report, account, publishing or consent pages and trick someone into pressing a button. An `add_header` inside a location replaces the ones it would inherit, so repeat there any security headers the server block sets.

Review and run `nginx -t` on the complete server configuration before applying it. Mirror the rejecting defaults for any IPv6 listeners. The TLS rejection directive requires nginx 1.19.4 or later. Passing `$host` preserves the app's Host check; replacing every incoming Host with `vool.dev` would make that check ineffective. Origin and CSRF checks remain required. The per-client zone processes addresses in shared memory; do not persist them or claim they are never processed. See the nginx [request limiter](https://nginx.org/en/docs/http/ngx_http_limit_req_module.html), [TLS rejection](https://nginx.org/en/docs/http/ngx_http_ssl_module.html#ssl_reject_handshake) and [default-server routing](https://nginx.org/en/docs/http/request_processing.html) documentation. Request limiting, TLS and log retention grant no package permissions. Keep diagnostics as aggregate health/error counters without identities or request payloads.

## v0.7.1 service limits

- GitHub publisher-to-account linking (`POST /publisher-link`, see [SKILL-CODEX.md](SKILL-CODEX.md)) proves ownership only by the GitHub owner ID of every source repository; organisation-owned repositories and mixed or unknown sources go to a maintainer. A self-declared GitHub handle is not publisher proof. Direct GitHub imports suppress unlinked website creator profiles and uploads, even if the account claimed the name earlier. Only an exact, consented service-created draft can establish its account's publication association. Legacy imported publications have no inferred account owner. Operators must call `Store.reserve_publication(package_id)` when a pending external publication becomes known, before account claims. Unobserved public PRs cannot be reserved automatically; connect the protected admission service's pending-publication feed before promising universal preclaim protection. Namespace transfer requires maintainer review and has no automatic route.
- Download tickets expire after 15 minutes and are authenticated by a private HMAC key in the account database. Issuance stores no ticket rows and has no global capacity cap. Only served-artifact attempts increment `download_requests`; the same ticket's retries count once. This is not a unique-person or completed-byte metric. Tickets issued before this format upgrade must be refreshed; losing/rotating the HMAC key also requires fresh tickets.
- Login flows expire after ten minutes. A browser/provider pair may have eight pending flows; another browser or provider remains available. Cookie churn can still create many flows, so the per-client proxy limiter is required. Existing state, PKCE, nonce, flow-cookie binding and one-use checks remain active.
- Upload admission checks namespace ownership and the ten-submissions-per-24-hours quota before reading either artifact or draft bodies, and before inspecting/scanning/packing direct Store calls. SQLite leases allow four active uploads across workers and one per account; refused requests consume no body. API body reads time out after 30 seconds. Lease cleanup runs on success, refusal and cancellation; a crashed worker's lease expires after five minutes. Expired or revoked leases cannot commit an upload. Size limits and the proxy limiter are still required.
- The secure, host-only session cookie is `vool_registry`, scoped to `/registry/api`; it does not use the `__Host-` prefix. Deploy with **no untrusted subdomains** beneath the service's parent domain. Do not treat path scoping as protection against a hostile sibling subdomain.
- Stars are one per authenticated account/package, not one per person. Throwaway accounts can inflate stars; ranking is not a trust or admission signal.
- The website account-link POST returns a 303 redirect to the provider. A `fetch()` call may not navigate the page. Before website handover, test complete browser navigation through linking, callback, cancellation and return to the account screen with real provider registrations.

## Picture review

A skill picture shows only after an operator approves that exact file for that skill: add `{"id": "<publisher>/<name>", "sha256": "<checksum>"}` to `approved_pictures` in `registry_service/curation.json` through a reviewed pull request. `check-config` validates the curation file, and the service reads it at start, so restart the service after the change.

To see what is waiting, run `vool-registry pictures-pending --config CONFIG --target NEWDIR`. It prints every picture of every published version that has no approval for its skill (id, version, kind, checksum, size) and writes each file into the new folder as `<sha256>.png`. It refuses a folder that already exists; without `--target` it only prints the list. Look at each file at full size and at about 52 pixels. Approve it only when:

- it claims no VOOL approval, verification, safety or official status, and has no badge, tick or shield that reads that way;
- it has no QR code, wallet address, URL, handle or phone number, and does not ask for a wallet recovery phrase or key;
- it uses no other brand's logo or trademark and impersonates no person, project or the VOOL team;
- it has no adult, violent, hateful or shocking content, and nothing styled as VOOL's own interface (buttons, warnings, prices);
- any text in it is short and fits the skill.

An approval covers one skill. The same file in another skill needs its own look, because a picture that is fine for one skill can mislead in another. Taking an entry out of `approved_pictures` hides that picture after the next start; browsers may keep a copy for up to five minutes, and a running VOOL drops it at its next Browse. Approving a picture admits nothing: packages still come only from the signed catalog.

A results page asks for many pictures at once, so give `/registry/api/pictures/` its own limiter zone instead of the general one:

```nginx
# http context
limit_req_zone $binary_remote_addr zone=registry_pictures:10m rate=20r/s;

# Inside the TLS server, next to location /registry/api/:
location /registry/api/pictures/ {
    access_log off;
    error_log /dev/null crit;
    client_max_body_size 1k;
    limit_req zone=registry_pictures burst=60 nodelay;
    limit_req_status 429;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For "";
    proxy_pass http://127.0.0.1:8791;
}
```

A VOOL build that predates the `icon`, `banner`, `languages` or `needs` metadata fields refuses a whole catalog in which any entry carries one, including builds whose owner installed a trust anchor through `VOOL_REGISTRY_ROOT_FILE`. Ship the first registry trust anchor only in a build that knows every field the catalog will carry. Upgrade the registry service before signing any entry with pictures, because an older service refuses that catalog too. Builds without a trust anchor never read the catalog, so they are unaffected.

## Skill reports

Read open reports with `vool-registry reports-open --config CONFIG` (see [SKILL-CODEX.md](SKILL-CODEX.md#skill-reports)). It lists every skill with open reports or a pause, including a pause whose reports are gone. Each report shows its id, reason, note, time, whether it counted toward a pause and `reporter`, an opaque handle that is the same for every report from one account. Read the reported version from `<artifact_dir>/<sha256>.json` (downloads of a paused skill are refused) and check the claim against its files: instructions that ask for money, passwords, keys or a recovery phrase, behaviour the description does not mention, a copy of someone else's work, a misleading picture, or a skill that does not work.

- Nothing wrong: `reports-close` closes the reports, wipes their notes and ends a pause the reports caused.
- Harmful or stolen: `skill-pause` keeps downloads paused, then revoke the version in the next signed catalog so VOOL stops listing it. A pause alone does not change what VOOL lists. `skill-resume` ends an operator's pause.
- A bad picture: remove its entry from `approved_pictures` and restart the service, then `reports-close`.
- Reports in bad faith: `reporter-mute --target REPORT_ID` for each report. That account's open and later reports stop counting and its comments are held. A closed report never counts again either: the same account cannot pause that skill a second time.

A paused skill stays paused until an operator acts. Check the list every day while the registry is open.

## Native activation and release limits

Ship the reviewed public trust anchor as `core/registry/trusted-root.json` in the actual v0.7.1 release, or install an operator-controlled anchor explicitly through local configuration. The native client verifies the existing packaged `BUILD_MANIFEST.json`: exact SHA/version, clean/release verdict, native verification and architecture. It does not accept a target, trust root or fetch URL from website input. Current released-manifest discovery covers macOS; other platform identity formats require their own real release integration before claiming support.

Do not change v0.7's release metadata or installed app while staging v0.7.1. The SDK/service wheel entry points add no new dependency. New registry UI labels are translated in all 17 app locales; website labels remain the frontend maintainer's work.

Production scanner adapters, provider registrations, protected repository settings, root custody, live compatibility and production smoke are required activation work. The local test suite proves mechanisms with ephemeral keys/provider/scanner fixtures; it does not certify a real submitted package or live OAuth account.

Root rotation accepts exactly the next version. An app update that skips a root version leaves an existing registry cache refused; retries cannot bridge the missing version. Ship and apply every intermediate dual-signed root in order. The current client has no automatic intermediate-root retrieval, so release integration must prove a multi-version update before offering it.

Native catalog refresh currently performs O(n²) signature verification by resolving entries against the catalog repeatedly. Keep catalog size within measured release budgets; this pass does not replace that algorithm or claim large-catalog performance.
