# Deployment and admission operations

Production activation is a separate reviewed action. No deployment, repository creation, installs, production key generation or paid scanner calls are performed by the local build.

Use the existing VPS for the static website, API and private account/quarantine database. Candidate execution belongs on isolated disposable workers. Keep the public GitHub artifact cache and signed catalog checkout separate from private state, the website deploy directory and signer custody. Back up each directory and configuration before every change; site deploys must not delete registry data.

## Activation checklist

VOOL preserves the root used to verify each installed receipt, allowing higher-version updates after key rotation. Missing or inconsistent installation evidence refuses without replacing files. An expired shipped anchor opens only with a valid, unexpired persisted successor and a verified rotation chain; availability checks are local and read-only. Every public package metadata field receives the same text checks as the package before admission and again at native install.

1. Create the two community repositories from the reviewed blueprints, protect main and require independent review plus an authenticated admission check from its expected external GitHub App (or an independently protected workflow with verified workload identity). Candidate-run blueprint checks are advisory. Follow [ADMISSION-SERVICE.md](ADMISSION-SERVICE.md) and inspect the applied required-check source. Protect workflows, policies, roots and CODEOWNERS. A PR's own modified workflow is not trustworthy admission evidence. Published content must retain its original author and licence.
2. Configure a pinned scanner policy for the actual latest released VOOL build and supported platforms. Supply all eleven required reports from trusted workers. Unsupported coverage remains held. Bind workload identity to protected workflow/code revisions; never expose review/signing credentials to candidate execution.
3. Establish root, admission, review and online-catalog key custody. With automatic checks, the signer holds the admission and review keys, the root key stays offline or on the signing Mac, and the server holds only the online catalog key (see [Automatic checks on GitHub](#automatic-checks-on-github) and [Automatic checks with the signing Mac](#automatic-checks-with-the-signing-mac)). A deployment can require two or more signers per role instead. Publish only public keys in a reviewed trust anchor. The web process holds no admission or review private key.
4. Private upload → signed archive/secret prepublication reports → author consent to the exact checksum → isolated publisher export → GitHub PR → final exact-commit reports → protected merge → offline admission signatures → offline catalog authorization → short-lived online catalog signature → canonical catalog PR/merge. Rejects remain visible only to the author. Direct GitHub PRs were public before scanning.
5. Register GitHub login with minimal public identity scope, Google with OpenID only, and Apple with a Services ID associated with a configured Sign in with Apple app. Configure exact callback URLs. The provider login token has no publication authority. Use a separate GitHub App with narrowly scoped, short-lived publisher credentials in the isolated publisher process.
6. Configure the service origin and immutable catalog/artifact inputs. Run `vool-registry check-config --config CONFIG` before starting it. The command fails if root, catalog, artifacts or requested provider registrations are missing/stale. Never use test fixtures as a production root or report.
7. Reverse proxy only `/registry/api/` to the localhost service. Preserve the existing relay/routes. Disable access, error, proxy, journal and backup request logging that could retain addresses or callback codes. Audit hosting/provider retention separately; do not claim no party ever processes an IP.
8. Use private directory mode 0700 and database mode 0600. Encrypt off-host backups, restrict operators, expire private quarantine after seven days and verify an actual restore. The restored catalog must meet signature/freshness/rollback rules; old account backups must respect the deletion retention policy.
9. The team's publisher name `vool-team` is reserved. With automatic checks, a `team` line in the signer's `team-steps.txt` (or the signing Mac's `team` command) names its members by GitHub account number; members publish under it and its profile shows as official. Each upload under the team's name then waits until a team member's `team-admit` step names its exact bytes (see below): the signer cannot know who uploaded, so it signs nothing as the team's by itself. Without them, sign in with the team's own GitHub account, choose the name on its profile, then list it in `official_namespaces` in `registry_service/curation.json` through a reviewed pull request.
10. Verify actual GitHub/Google/Apple login, account linking/deletion, secret rejection, failed scans, public-consent export, exact GitHub commit verification, signed browse, install/enable, update recovery, tamper/revocation and stats with production registrations on disposable accounts/profiles before opening the service publicly.

## Automatic checks on GitHub

The signer parses all operator lines and reads release discovery and the moderation summary before changing state or sending decisions. A bad line, failed release discovery or unreadable moderation summary prints `Stopped: ...`, exits non-zero and leaves `state.json` and `private.bin` byte-identical. The next run retries. An upload batch that cannot be read whole (a malformed queue, bytes that differ from the queue, a queue row whose skill name or version differs from the one inside its package, or a refused request) waits whole: nothing in it is reviewed, decided or listed. A decision an earlier run already made on the exact bytes is still sent and listed. An unavailable review or exhausted cap also holds the whole batch without admissions; completed review costs and verdicts remain sealed for retry. Unconfirmed admission/refusal responses stop later decisions; the next run sends them again without another paid review. In each of these held cases the run still applies the team's steps, follows releases, rechecks listed skills and renews the list already signed, so browsing never lapses while uploads wait; it then prints `Stopped: ...` and exits non-zero. A signer that has never published a list waits for its first complete run. A listed skill whose bytes cannot be read stays listed and is checked again in the next daily recheck; if its signature runs out first, only that skill leaves the list. Malformed catalog or marks acknowledgments stop before further requests; retries retain spent epochs and pending decisions. Corrupt private state or a public-state digest mismatch refuses before requests.

Immediately before each uncached paid review, the signer fetches the remote state and operator instructions and stops if either has changed. Each completed upload check is checkpointed before the next review. This is a freshness check, not an atomic reservation across Git and the review provider.

Every credential-bearing HTTP transport refuses 301, 302, 303, 307 and 308 responses with a specific redirect reason, including authority signatures, review authorization, provider credentials and the Actions token. Git disables redirects too. Correct the configured endpoint instead of relying on a redirect. Browser sign-in navigation remains separate from these server transports.

The signer can run as a GitHub Actions job instead of on a Mac, so no computer has to stay on. It is the same signer, with the same checks, review model, limits and hand steps; only where it keeps its keys and state differs. It runs in its own public repository that only the team can change. Runner billing and provider quotas depend on the repository's active service terms.

Set it up once:

1. On the server, create the online catalog key: `vool-registry catalog-key --config CONFIG`. It prints the public key.
2. Create an empty public repository for the signer, for example `skill-codex-signer`. Give only the team write access, turn on two-factor sign-in for every team account, and clone it.
3. From a VOOL checkout at the commit whose checks the signer should use, on a computer where `gh` is signed in as an owner of that repository, run `python -m registry_service.github_authority setup --repo CLONE --github OWNER/NAME --catalog-key PUBLIC_KEY --model MODEL --cap 20 --offline FOLDER`. `FOLDER` must be a new folder outside the clone. Choose a passphrase of at least 12 characters for the main (root) key.
   - The admission key, the review key and the key to the sealed state go straight into the repository's Actions secrets. They are never shown or written to disk.
   - `FOLDER` gets `root.pem` (the main key, encrypted with the passphrase) and `trusted-root.json`.
   - The clone gets `authority/` (the trust root, the review settings, the pinned VOOL commit and the state), `team-steps.txt`, the workflow and a README.
   - Without `--targets FILE` it signs for the macOS Apple Silicon and Intel builds of VOOL's newest three releases.
4. Add the last two secrets yourself; each command asks for its value: `gh secret set SKILL_CODEX_SERVER --repo OWNER/NAME` (the service's https address) and `gh secret set OPENROUTER_API_KEY --repo OWNER/NAME` (the review model's key).
5. Commit and push the clone. Keep `FOLDER` and its passphrase offline, for example on a USB stick. The main key is needed only to change the other keys.
6. Copy `authority/trusted-root.json` to the server's `trusted_root` path and into the app release as `core/registry/trusted-root.json`. Add `"authority": true` to the service configuration and restart it.

Don't require pull requests on the signer's `main` branch, because the job commits its state there with the workflow's own token. Block force-pushes and deletion of `main` instead.

The job runs every five minutes (GitHub sometimes starts scheduled jobs late), right after a change to `team-steps.txt`, and from the Actions tab with Run workflow. Each run:

- applies `team-steps.txt`;
- once a day, signs for VOOL's newest three releases and runs every listed skill through today's checks again; a skill that fails them leaves the list;
- takes only exact-checksum-consented uploads, checks the bytes, asks the review model, and signs or refuses them;
- mirrors listed bytes to `authority/packages/`, active discovery to `authority/registry.json` and `authority/CATALOG.md`;
- applies signed moderation/curation and records opaque pending IDs/counts without private text;
- renews entries and the 7-day list as the Mac does.

Before anything is sent to the service, the run commits and pushes its state, so a run that stops halfway never signs the same list number twice, and what it spent before each send stays counted. Each time it saves, it looks at GitHub first: a run that finds another run's change stops before it sends anything, and the next run carries on. `authority/state.json` holds the signed list, the marks and the builds, which are public anyway. `authority/private.bin` is sealed with AES-GCM; it holds what is not public: signed uploads not listed yet, the review model's verdicts, the counts per creator and the spending. The public run log shows counts and safe operator-line problems, never private uploads/findings or raw service refusal text. The sealed state binds the public state digest; hand edits stop before requests. Existing setup state is validated and gains a binding on its next successful save. A malformed queue holds the whole upload batch while the signed list is still renewed; corrupt sealed state refuses before requests. A semantic operator failure rolls back the service transaction before the signer saves withdrawals.

Only the "Sign" step holds the keys or can push. Before it, the job checks that `authority/vool-commit` is on VOOL's own main branch, and installs VOOL's dependencies only at the versions and hashes in VOOL's lock file (`registry_service/github/requirements.txt`; after a change to `uv.lock`, a test fails until that file is made again with the command at its top).

The team's hand steps are lines in `team-steps.txt`; its template lists every form. Signed comment/report moderation, download pauses, picture approvals, official namespaces, features, editor picks and reviewer/version records apply without a service restart. Read-only `comments-held`, `reports-open`, `pictures-pending` are counts/opaque IDs under `authority/state.json` → `moderation`; full private text/image export remains in the fallback CLI. Moderation commands apply once while a line remains present; use their inverse to undo, or remove then re-add to act again. Curation lines describe the complete desired signed set. Edit the file on GitHub (the pencil button works on a phone) and commit; the signer applies it within a few minutes. GitHub's two-factor sign-in protects it. Delete a declarative mark/curation line to withdraw that mark. One-shot moderation is undone with its inverse, not deletion. Withdrawn package versions remain revoked: deleting a `removed` line lets new uploads of the skill in again, but the versions it took out stay revoked, so the skill comes back only with a higher version number.

```text
tested PUBLISHER/NAME 1.2.0      "Tested by Parad0x Labs" on exactly that version
highlighted PUBLISHER/NAME       the Highlighted spot
suspended PUBLISHER              refuses their uploads and waiting submissions
removed PUBLISHER/NAME           takes every version out of the list and revokes it; new uploads of it are refused
team vool-team ID ID             a team name and its members' GitHub account numbers
team-admit vool-team/NAME SHA256 sign this one upload under the team's name; without the line it waits
build 0.7.2-beta COMMIT          also sign for this VOOL build (version and full commit id) before it is released, to test it
```

Publishing a team skill takes two steps, because being in the upload queue proves nothing about who uploaded. First upload it: on the website as a team member, or on the server with `vool-registry team-upload --config CONFIG --github-id ID --packages agenda.json`. The command prints the upload and the step for its exact bytes:

```text
{"consent": 1, "package_id": "vool-team/agenda", "sha256": "5d41402abc4b2a76b9719d911017c5925d41402abc4b2a76b9719d911017c592", "state": "checking", "version": "1.0.0"}
  It waits for a team member's step. On GitHub, add this line to team-steps.txt:
    team-admit vool-team/agenda 5d41402abc4b2a76b9719d911017c5925d41402abc4b2a76b9719d911017c592
  or, with the signing Mac:
    python -m registry_service.mac_authority team-admit vool-team/agenda@5d41402abc4b2a76b9719d911017c5925d41402abc4b2a76b9719d911017c592 --dir DIR
```

Then add that line to `team-steps.txt` and commit. The next run checks those bytes, reviews them and signs them if they pass. Until then the upload is not checked and costs no review; the run log counts it under `waiting` and `team_step_needed`. The checksum is the package's sha256, so a line for other bytes releases nothing and version 1.0.1 needs its own line. A `team-admit` line whose publisher is not a team is reported as a problem and does nothing. Uploads under a team's name are queued after everyone else's, so a waiting batch never holds up other creators.

If a run stops with `Stopped: the service named an upload differently than its package does; the uploads wait for the next run`, a row in the service's queue disagrees with the package it carries. The signer signs nothing from that batch and refuses nothing either, because the fault is the service's and not the creator's. Look at the service and its database before anything else; the batch goes through once the row and the package agree again.

If a line can't be read, nothing in the file is applied until it is fixed, and the removals already in force keep holding. A `tested` line for a version that isn't listed, or a `highlighted` line for a skill that isn't listed, waits, and the run log names it.

`authority/vool-commit` pins the VOOL commit whose checks the job runs. To use newer checks, commit there the full id of a newer commit on VOOL's main branch.

What happens when something stops:

- GitHub Actions is down: as when the Mac is off, the signed list runs out six to seven days after the last run, and nothing unsigned is served.
- The service doesn't answer: the run says so and stays green while more than two days of the signed list are left, then fails, so GitHub sends its failure email to whoever last changed the workflow's schedule.
- The `OPENROUTER_API_KEY` secret is missing: every run fails at its start and nothing is reviewed or refused, so no creator is told a reason that is the team's.
- The service no longer has a listed skill's bytes (after a restore from an older backup, say): that skill leaves the list at the next daily check, and the rest stay listed.
- GitHub's release list can't be read: the run keeps the builds it has and asks again next time.
- GitHub turns off scheduled jobs in a public repository after 60 days without activity. The signer commits every day, but if the schedule is ever turned off, turn it back on from the Actions tab.
- A package makes the checks fail in a way nobody planned for: that upload is refused with "Our checks couldn't read this skill", as on the Mac.

Limits, so say nothing stronger:

- Whoever can change the signer's repository can change its workflow and reach the online keys. Keep write access to the team, with two-factor sign-in, and look at every change to the workflow.
- The other limits of the signing Mac apply as written below: one signature per role, a 7-day list, the check suite named by its major version, and one review model.

## The two team badges

"Tested by VOOL" and "Editor's Choice" are lines in one signed file, the badge document. A line names one exact package: its kind, name, version and checksum. Nothing else makes a badge: not a line in `team-steps.txt`, not a flag on a catalog row, not a word or a picture an author supplies. The older `tested`, `highlighted`, `featured` and `editors-pick` steps still order the list and release pictures; they draw no badge.

Who holds what:

- The owner holds the main (root) key offline. With it he signs the list of the people he appoints, `badge-issuers.json`. He signs no badge and need not be one of the people.
- Each appointed person has a key of their own, in a file protected by a passphrase, on their own computer. A badge document counts once two different people on the list have signed it.
- The signer (the GitHub job or the signing Mac) holds no badge key. It passes the signed files on to the service and signs a stamp, "this document, still current", good for seven days. Whoever can write to the signer's repository can therefore issue no badge.
- The service checks the list, the two signatures, the number and the chain before it stores a document, and serves it at `GET /registry/api/badges`. Every app and the website check all of it again.

A badge key never goes into a workflow file, an Actions secret, an environment variable or onto the server. The tool reads keys from files and passphrases from the keyboard only.

### Appoint the people (owner)

1. Each person makes their key and sends the owner the public line it prints:

```text
$ python -m registry_service.badges keygen --out ana.pem
Choose a passphrase for your badge key:
Type it again:
Made ana.pem. Keep it and its passphrase on your own computer only.
Public line (send this to the owner): 30698e6a884550b38f47b5008b740a094f7f49956533e8b553863c0b67956444
Key id: 9c2605372d33df5161daf511c293e78e186ec6df328f18e9243f32446bfbe8e6
```

2. The owner signs the list with the main key, on the computer that holds the offline folder (`root.pem` and `trusted-root.json` from the signer's setup). Name at least two people; a third as a spare is sensible:

```text
$ python -m registry_service.badges issuers --root-key usb/root.pem --trusted-root usb/trusted-root.json \
    --key 30698e6a...6444 --key 1e70ab2c...752c --key 1cb89fd0...9524 --out badges/badge-issuers.json
Passphrase for root.pem:
Issuer list 1, good until 2028-02-11. Appointed keys:
  9c2605372d33df5161daf511c293e78e186ec6df328f18e9243f32446bfbe8e6
  cbeb1f07aa3834fdc4bff609483efbd75b4e3e0d9f3ba5ed7a7f70c248389b8d
  ebde5f3df59fcfc7a253663d67e7933b2842dcb88cf2b5652b872504e1b538b0
Signed with the main key and written to badge-issuers.json.
```

3. Commit `badges/badge-issuers.json` to the signer's repository (on the signing Mac: put it in `FOLDER/badges/`).

The list is good for 365 days (`--days`, at most 730); sign a new one before it runs out, or every badge stops showing. To change the people, run `issuers` again with `--previous badges/badge-issuers.json` and the new set of `--key` lines: the list number goes up and an older list is refused from then on. A badge that was signed for by someone who is no longer on the list stops showing until two current people issue it again (`--renew`).

### Issue a badge (two appointed people)

Keep the test record of each "Tested by VOOL" badge (who ran it, when, on which build, what was done) in a private place; only its checksum goes into the document. Write the badges into `badges/badges.txt` in a checkout of the signer's repository, one per line. The file is the complete set: a line you take out drops that badge.

```text
tested alice/notes 1.0.0 0.7.1 macos-arm64 /private/records/notes-1.0.0-test-record.json
editors-choice alice/notes 1.0.0 "Short, clear, asks before it sends"
```

`tested` names the skill, its version, the VOOL version or versions it was run on (`0.8.0,0.8.1`), the platform or platforms (`macos-arm64,macos-x86_64`) and the record. `editors-choice` names the skill, its version and why. The checksum is read from the signer's own list (`authority/state.json`), so it is the listed package's and nobody types it. A plugin, or a skill that ships inside VOOL, is not in that list; give its checksum after the version: `tested plugin:PLUGIN-ID 1.0.0 sha256:<64 hex> 0.8.0 macos-arm64 record.json` (the checksum VOOL computes over the plugin's files: `core.plugin_package.capture_package(FOLDER).digest`) or `editors-choice bundled:SKILL-ID 1.0.0 sha256:<64 hex> "why"` (the `sha256` of its row in `core/addon_directory.json`).

One of the two people proposes, naming both signers by key id (the first 8 characters or more):

```text
$ python -m registry_service.badges propose --file badges/badges.txt --issuers badges/badge-issuers.json \
    --state authority/state.json --previous badges/badges.json --by 9c2605372d33 --by cbeb1f07aa38 --out badges/badges.json
Badge document 1 written to badges.json, unsigned. It runs out on 2027-05-12.
  new: Tested by VOOL  alice/notes 1.0.0
  new: Editor's Choice  alice/notes 1.0.0
Next: each of the two people named with --by runs `sign --key THEIR-KEY badges.json` on their own computer.
```

Leave `--previous` out for the very first document. Then each of the two signs on their own computer and passes the file on:

```text
$ python -m registry_service.badges sign --key ana.pem badges/badges.json
Passphrase for ana.pem:
Signed badge document 1: 1 of 2 signatures. Send the file to the second person to sign.

$ python -m registry_service.badges sign --key ben.pem badges/badges.json
Passphrase for ben.pem:
Signed badge document 1: 2 of 2 signatures. It is complete. Run check, then put it in the signer repository as badges/badges.json through a pull request.

$ python -m registry_service.badges check badges/badges.json --issuers badges/badge-issuers.json --trusted-root authority/trusted-root.json
Badge document 1 holds: two appointed people signed it. It runs out on 2027-05-12.
  Tested by VOOL  alice/notes 1.0.0 on VOOL 0.7.1  (180 days left)
  Editor's Choice  alice/notes 1.0.0  (365 days left)
  withdrawn checksums: 0
```

Merge the pull request that carries `badges/badges.json` (and `badges/badges.txt`). The next signer run passes the document on; its report then carries `"badges": {"sequence": 1, "badges": 2}`. A document that does not verify is not sent, and the public run log shows `"badges_waiting": true`; `check` says why:

```text
$ python -m registry_service.badges check badges/badges.json --issuers badges/badge-issuers.json --trusted-root authority/trusted-root.json
Refused: 1 of 2 appointed people have signed this document. Each one runs: sign --key THEIR-KEY badges.json
```

Other refusals name the line or the cause, for example `Refused: badges.txt line 1: alice/notes 2.0.0 is not in the Skill Codex list`, `Refused: this key is not on the issuer list, so its signature would count for nothing` and `Refused: --by names the same person twice; a badge takes two different people`.

### Renew, re-test and withdraw

- **Every 90 days at the latest**, propose and sign a new document, even when nothing changed: a badge document runs out 90 days after it is made, and from then on no badge shows. `propose` with the same `badges.txt` carries every badge over as it is, with its own end date.
- **A badge runs out by itself**: Tested by VOOL after 180 days, Editor's Choice after 365. `propose` then refuses with the date and asks for `--renew NAME`, which issues it again from today, or for the line to be taken out. Renewing is a new statement by two people, so test or look again first.
- **A new version of a skill carries no badge.** A badge is for one checksum. Test the new version, change the version on its line, propose and sign.
- **A new VOOL release**: "Tested by VOOL" shows only on the VOOL builds named on its line. List what waits:

```text
$ python -m registry_service.badges propose --retest 0.9.0 --previous badges/badges.json --issuers badges/badge-issuers.json
Not yet tested on VOOL 0.9.0: 1
alice/notes 1.0.0 (tested on 0.7.1)
Nothing was changed. Test each one, add the version to its line in badges.txt, then propose.
```

- **Withdraw a badge**: take its line out and add `withdrawn <64 hex checksum>`, then propose and sign as above. Apps drop the badge at their next Browse or daily check. An app that saw the withdrawal never shows a badge for that checksum again, so a withdrawn package gets a badge back only as a new version. Taking the line out without `withdrawn` also removes the badge, and leaves room to issue it again later.

```text
$ python -m registry_service.badges propose --file badges/badges.txt --issuers badges/badge-issuers.json \
    --state authority/state.json --previous badges/badges.json --by cbeb1f07aa38 --by ebde5f3df59f --out badges/badges.json
Badge document 2 written to badges.json, unsigned. It runs out on 2027-05-12.
  dropped: Tested by VOOL  alice/notes 1.0.0
  dropped: Editor's Choice  alice/notes 1.0.0
  withdrawn: a91e8d3de54316becd5cc8bea9dec49ccd03d54e0c92dc5957bedee1ee345c72
Next: each of the two people named with --by runs `sign --key THEIR-KEY badges.json` on their own computer.
```

Limits to know:

- Each document must follow the one in place: the service takes number N+1 only when it names document N. Always propose with `--previous` set to the file the signer's repository holds.
- If the signer stops running, the stamp runs out after seven days and badges stop showing until it runs again. A taken-over server can hold back a withdrawal for at most those seven days.
- A VOOL release can carry its own copy of the two files as `core/registry/badges.json` and `core/registry/badge-issuers.json`, for the skills that ship inside VOOL. That copy counts past its own date until the app has verified a newer document. No release carries one yet.
- Removing a skill from the Skill Codex does not need a badge step: a removed version is not listed, so nothing is drawn for it.

## Automatic checks with the signing Mac

Instead of the GitHub job, the owner's Mac can be the signing authority. It checks every upload made with the creator's MIT agreement, signs what passes, and sends the signed list to the service. No person approves uploads; a person only adds the "Tested by Parad0x Labs" mark, the Highlighted spot, suspensions, team members, the step that admits each team upload, and removals, each behind a code from an authenticator app.

1. On the server, create the online catalog key once: `vool-registry catalog-key --config CONFIG`. It writes `catalog.key` (mode 0600) in the private directory and prints the public key. An existing key is kept.
2. On the Mac, write the released VOOL builds to a file such as `targets.json` (`[{"version", "commit", "platform"}, ...]`), then run `python -m registry_service.mac_authority setup --dir FOLDER --server https://SITE --catalog-key PUBLIC_KEY --targets targets.json --provider openrouter --model MODEL --cap 20`. `FOLDER` must not exist yet; it is created with mode 0700. Choose a passphrase of at least 12 characters for the main (root) key. Add the printed `otpauth://` line to the authenticator app (as a QR code or by typing the key), then type the code it shows to confirm. `--provider ollama` uses a local model at `127.0.0.1:11434` instead, with no spend.
3. Keep a copy of `FOLDER/root.pem` and its passphrase offline. Copy `FOLDER/trusted-root.json` to the server's `trusted_root` path, and into the app release as `core/registry/trusted-root.json`. A build without it keeps the Skill Codex closed.
4. Add `"authority": true` to the service configuration and restart it. Until the Mac's first run there is no list, and Browse says the catalog is not configured. The service stamps the list with the online key for six hours at a time and stamps it again once less than three hours are left (it looks every ten minutes), for as long as the Mac's own signature on the list holds.
5. Keep `python -m registry_service.mac_authority run --dir FOLDER --every 300` running on the Mac, with the review key in `VOOL_AUTHORITY_OPENROUTER_KEY` for the paid model. Each run takes waiting uploads, checks them again on the exact bytes and asks the review model; it renews entries 5 days before they expire (after 30 days) and signs the 7-day list again whenever less than 6 days are left, so about once a day. `--once` runs a single pass, for a scheduler.
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
python -m registry_service.mac_authority team-admit vool-team/NAME@SHA256 --dir FOLDER  # sign this one team upload; it waits without it
python -m registry_service.mac_authority remove PUBLISHER/NAME --dir FOLDER      # takes every version out of the list and revokes it
python -m registry_service.mac_authority status --dir FOLDER                     # no code needed
```

What happens when something stops:

- The Mac is off: the signed list runs out six to seven days after its last run, and browsing, downloads and installs then stop with "catalog freshness expired" until the next run. Nothing unsigned is served.
- The review model is unreachable: the upload keeps waiting, and the outage never counts against it. A model that answers in the wrong shape gets three tries, then the upload is refused with a plain reason. The monthly cap (`monthly_cap` in `FOLDER/config.json`) stops paid calls; uploads then wait for the next month. An upload still waiting 35 days after it arrived expires, and its creator uploads it again.
- One publisher name sends many uploads: each name gets 30 model reviews a month (`REVIEWS_PER_CREATOR` in `registry_service/mac_authority.py`; the team's name has no limit), then its uploads are refused until the next month. The same refused bytes sent again reuse the first answer.
- A suspended creator's waiting uploads are refused at the next run, and new ones at once. Deleting the account and signing in again does not end it: the suspension follows the old name to the same sign-in under any new name, and `unsuspend OLDNAME` (or removing the `suspended` line) lifts it.
- A team upload nobody admitted: the run reports it under `waiting` and prints `team_step_needed` with `publisher/name@checksum`, the exact target for `team-admit`. Everyone else's uploads are signed as usual meanwhile.
- A queue row that names its bytes differently than the package does: the run stops with "the service named an upload differently than its package does" and the whole batch waits, signed list renewed as in the other held cases.
- A package makes the checks fail in a way nobody planned for: that upload is refused with "Our checks couldn't read this skill", the run goes on with the others, and the Mac's log shows `Refused <checksum> after an unexpected <error>`. Send that line to whoever maintains the checks.

The authenticator code protects the hand-action commands. The admission and review keys are files readable only by the Mac account that runs the signer; the root key is encrypted with its passphrase. Anyone who controls that Mac account can sign, so keep it for this job, with FileVault and a login password. Removing a skill revokes its versions in every VOOL at the next Browse; downloads already made are not taken back.

Limits the owner chose, so say nothing stronger:

- One signature per role is enough, and the admission and review keys have no passphrase so the signer runs unattended. Whoever controls the Mac account can sign.
- The Mac's signed list lasts 7 days. Someone who took over the server could keep serving the last list the Mac signed, without later removals, until those 7 days run out.
- The signed record names the check suite and its major version, not the exact rule text. Tightening a rule does not mark older entries by itself; each renewal checks them again under today's rules.
- One review model reads each skill, and its refusal reasons go back to the creator word for word, as the automatic checks' do. A skill written to fool that one model can pass the model step; the automatic checks still apply to it, and reports and removal are the backstop.

The signed list keeps every version of every skill. VOOL reads a list of at most 4 MB, and one short skill's entry is about 1.3 KB, so plan to take older versions out of the list before it reaches a few thousand versions.

## Service configuration

`vool.registry.service.v1` requires `origin`, `private_dir`, `trusted_root`, `catalog_file`, `artifact_dir`, `target`, `providers`. The legacy `target: {version,commit,platform}` key remains accepted. Public distribution resolves any target in each current signed admission; it does not filter by this old configured target. A release needs no service config edit or restart. Native installation still requires its actual exact released build/platform. Removed or retired-only entries absent from the current signed list are not served. Catalog file is the canonical directory containing the signed registry projection. Artifacts are named by full transport checksum. Provider names are `github`, `google`, `apple`; their registrations come from `VOOL_REGISTRY_PROVIDER_CLIENT_ID` and `VOOL_REGISTRY_PROVIDER_CLIENT_SECRET` with PROVIDER uppercase. Apple's client secret is the developer-managed signed JWT and needs its own renewal process.

Start with `vool-registry serve --config CONFIG --port 8791` under a dedicated unprivileged account. It binds 127.0.0.1, ignores forwarded IP headers, disables access/error request logs and returns JSON errors without provider tokens. It imports verified snapshots and public artifacts; its database is a cache, not catalog admission authority. A stale snapshot stops new browse/download/install rather than reusing an unsigned list.

The proxy needs TLS, a 24 MiB body limit, strict host/origin routing, no credentials in URLs and a per-client request limiter. Integrate these excerpts into the existing `http` context and named TLS server; retain its certificates and website/relay routes:

```nginx
# http context: top level of a site file included by http, never inside server.
# Transient client-address state, not a request log.
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
- The website account-link POST returns HTTP 200 `{url}`. The page navigates to that URL after the successful response. Ordinary sign-in starts with HTTP 303. Linking, callback, cancellation and return to the account screen require a configured provider.

## Picture review

Approve a skill picture with `picture publisher/name sha256` in `team-steps.txt`; removing the line withdraws it without a restart. The fallback is an `approved_pictures` entry in `registry_service/curation.json` through reviewed source changes and a restart. Signed curation replaces the full fallback set after the first signed curation line; retain existing approvals explicitly when migrating. Approval belongs to the skill and exact checksum, never every package using that file.

To see what is waiting, run `vool-registry pictures-pending --config CONFIG --target NEWDIR`. It prints every picture of every published version that has no approval for its skill (id, version, kind, checksum, size) and writes each file into the new folder as `<sha256>.png`. It refuses a folder that already exists; without `--target` it only prints the list. Look at each file at full size and at about 52 pixels. Approve it only when:

- it claims no VOOL approval, verification, safety or official status, and has no badge, tick or shield that reads that way;
- it has no QR code, wallet address, URL, handle or phone number, and does not ask for a wallet recovery phrase or key;
- it uses no other brand's logo or trademark and impersonates no person, project or the VOOL team;
- it has no adult, violent, hateful or shocking content, and nothing styled as VOOL's own interface (buttons, warnings, prices);
- any text in it is short and fits the skill.

An approval covers one skill. The same file in another skill needs its own look, because a picture that is fine for one skill can mislead in another. Removing a signed picture line hides it without restarting; a fallback-file edit applies after the next start; browsers may keep a copy for up to five minutes, and a running VOOL drops it at its next Browse. Approving a picture admits nothing: packages still come only from the signed catalog.

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

## What stays after an account is deleted

Deleting an account removes the account row, its sign-ins, sessions, unpublished uploads, comments, stars and report notes. One row per sign-in stays in `retired_identities`, keyed by a hash made with a private key in the service's database (`identity-hmac`). It holds the deletion time, whether the person was muted, the times of their last day's uploads and comments, and a hash of each public name they held. It holds no name in words, no provider id and no text. The person's skill reports stay as a reason and a count under the same hash.

The row makes a returning sign-in the same person again: a mute returns, a suspension of a name they held applies under any new name, the daily limits continue, and they choose no new public name for 30 days ("an account with this sign-in was deleted; you can choose a public name from YYYY-MM-DD"). The service's clean-up at start forgets a row 30 days after the deletion unless the person was muted or a name they held is suspended then. So suspend a creator who left within 30 days of the deletion; a later suspension still closes the old name, but no longer follows the person to a new one. The key and the rows live in the same `accounts.sqlite`, so back it up and restore it whole: rows restored without their key match nobody.

## Skill reports

Read open reports with `vool-registry reports-open --config CONFIG` (see [SKILL-CODEX.md](SKILL-CODEX.md#skill-reports)). It lists every skill with open reports or a pause. Each report shows its id, reason, note, time, whether it counted toward a pause and `reporter`, an opaque handle that is the same for every report from one person. A report whose sender deleted their account stays in the list with `account_deleted: true` and an empty note, so a pause can always be traced to its reports. Each skill shows `counting` (how many different people's reports count), `paused` (the cause while downloads are paused now), `paused_since`, and for a pause from reports `pause_lapses` and, once it has ended, `pause_lapsed: true`. `protected: "team"` or `"tested"` marks a skill that reports alone never pause. Read the reported version from `<artifact_dir>/<sha256>.json` (downloads of a paused skill are refused) and check the claim against its files: instructions that ask for money, passwords, keys or a recovery phrase, behaviour the description does not mention, a copy of someone else's work, a misleading picture, or a skill that does not work.

- Nothing wrong: `reports-close` closes the reports, wipes their notes and ends a pause the reports caused.
- Harmful or stolen: `skill-pause` keeps downloads paused, then revoke the version in the next signed catalog so VOOL stops listing it. A pause alone does not change what VOOL lists. `skill-resume` ends an operator's pause.
- A bad picture: remove its signed `picture` line (or change/restart the fallback curation), then add `reports-close publisher/name`.
- Reports in bad faith: `reporter-mute --target REPORT_ID` for each report. That person's open and later reports stop counting and their comments are held. It also works on a report whose sender deleted their account: the mute lands on the account they came back with, or waits for them. A closed report never counts again either: the same person cannot pause that skill a second time, also not by deleting their account and signing in again.

What pauses a skill: `scam`, `harmful` or `stolen` reports from three different people, each with an account at least 14 days old and not muted. A pause that only reports caused ends by itself after 48 hours. To keep a skill paused, add `skill-pause publisher/name` within those 48 hours; that pause has no end date. After a pause has ended, the same open reports do not pause the skill again; close them once you have looked. A skill under a team's publisher name, and a skill whose listed version carries the Tested mark, are never paused by reports alone: their reports wait in this list with `protected`, and `skill-pause` still pauses them. A comment is held the same way: by reports from three different people with accounts at least 14 days old.

Check the list every day while the registry is open: a pause from reports gives you 48 hours to confirm it.

## Ratings, listing and account steps

These are signed team steps like the others: a line in `team-steps.txt` on the GitHub signer, or a command on the signing Mac. No website session can do any of them. Every step writes one row in the service's `moderation_log` (marks number, step digest, action, target, reason code, time, rows affected) in the same transaction, and the public reads the action, reason sentence, date and count from `GET /moderation/notices`. The record holds no actor yet: who committed a line is in the signer repository's history.

Reason codes (required where shown; any other word stops the whole file): `fake`, `automated`, `self_rating`, `paid`, `abusive`, `off_topic`, `harmful`, `stolen`, `misleading`, `creator_request`, `mistake_reversed`.

| Line in `team-steps.txt` | What it does | How to undo |
| --- | --- | --- |
| `delisted publisher/name REASON` | Off the catalog, the public directory and VOOL's Browse; no new downloads; ratings closed. Nothing is revoked, so installed copies keep working | delete the line |
| `removed publisher/name REASON` | As `removed` before (revoked; installed copies stop at their next list check), and the notice carries the reason. The reason is optional so older lines still read | deleting the line lets a higher version in; revoked versions stay revoked |
| `review-text-remove RATING_ID REASON` | Removes the words of one rating; its stars stay. The words are kept 30 days for a second look, then erased | `rating-restore RATING_ID` within 30 days |
| `rating-void RATING_ID REASON` | Takes one rating out of the list and the average; it counts in "ratings removed by the VOOL team". Its writer cannot rewrite or delete it | `rating-restore RATING_ID` |
| `ratings-void-account PUBLISHER-or-HANDLE REASON` | Voids every rating of one account | `rating-restore` each id |
| `ratings-void-window publisher/name 2026-01-31T00 2026-01-31T12 REASON` | Voids every rating on that add-on created from the first hour up to, not including, the second (UTC) | `rating-restore` each id |
| `ratings-freeze publisher/name REASON` | No new ratings or edits on that add-on | `ratings-unfreeze publisher/name` |
| `account-ban PUBLISHER-or-HANDLE REASON` | No ratings, comments or stars from that account; its ratings read as removed, its reports stop counting, its stars are deleted. Uploads are stopped with `suspended` | `account-unban PUBLISHER-or-HANDLE` (deleted stars do not return) |
| `rating-restore RATING_ID` | Publishes a rating that was voided or held, and puts back removed words while they are kept | `rating-void` again |

These are one-shot while the line stays, like the other moderation lines; `delisted` and `removed` describe the complete wanted set, like `tested`. An account without a public name is named by the 12-character handle the operator views show. Rating ids are in `authority/state.json` under `moderation_ratings` (held ratings, open appeals: ids and counts only) and in the public `GET /ratings` list.

On the signing Mac: `python -m registry_service.mac_authority step "rating-void RATING_ID fake" --dir DIR` takes any of the rating and account lines; `delist publisher/name --reason REASON`, `relist publisher/name` and `remove publisher/name --reason REASON` do the listing steps. Each asks for the authenticator code.

Operator views on the server (read only): `python -m registry_service.cli ratings-held --config FILE` lists ratings waiting for a look with their text, reports and repeats; publish one with `rating-restore`, remove one with `rating-void`.

Appeals: the creator or the rater presses "Ask for another look" on the website. `python -m registry_service.cli appeals-open --config FILE` lists open appeals with the step they are about; `appeal-close --config FILE --target APPEAL_ID --outcome upheld|rejected` answers one and erases its text. Closing changes nothing else: to undo a step, use the undo in the table.

Upgrade order: the service first, then the signer, then the app. An older service refuses marks that carry `delisted` or `withdrawals`; an older signer keeps working against the new service, because the older moderation answer keeps its shape and the new list is a route of its own (`GET /authority/moderation/ratings`). Shipped apps accept marks with the two new lists: they read only the curation part.

## Native activation and release limits

Ship the reviewed public trust anchor as `core/registry/trusted-root.json` in the actual v0.7.1 release, or install an operator-controlled anchor explicitly through local configuration. The native client verifies the existing packaged `BUILD_MANIFEST.json`: exact SHA/version, clean/release verdict, native verification and architecture. It does not accept a target, trust root or fetch URL from website input. Current released-manifest discovery covers macOS; other platform identity formats require their own real release integration before claiming support.

Do not change v0.7's release metadata or installed app while staging v0.7.1. The SDK/service wheel entry points add no new dependency. New registry UI labels are translated in all 17 app locales; website labels remain the frontend maintainer's work.

Production scanner adapters, provider registrations, protected repository settings, root custody, live compatibility and production smoke are required activation work. The local test suite proves mechanisms with ephemeral keys/provider/scanner fixtures; it does not certify a real submitted package or live OAuth account.

Root rotation accepts exactly the next version. An app update that skips a root version leaves an existing registry cache refused; retries cannot bridge the missing version. Ship and apply every intermediate dual-signed root in order. The current client has no automatic intermediate-root retrieval, so release integration must prove a multi-version update before offering it.

Native catalog refresh currently performs O(n²) signature verification by resolving entries against the catalog repeatedly. Keep catalog size within measured release budgets; this pass does not replace that algorithm or claim large-catalog performance.

## Signed operator forms and public projection

The service upgrades unpublished automatic uploads from the earlier implicit-consent flow to
private `checking` with consent unset. Legacy generic publish choices return to `preflighted`.
Authors confirm the exact checksum again. This migration runs once; fresh explicit consent survives
restart. Already published versions and their public history remain unchanged.

The complete syntax is in `registry_service/github/team-steps.txt`. Routine actions are:

| Action | Form | Effect |
| --- | --- | --- |
| Read pending work | `authority/state.json` → `moderation` | Counts and opaque comment/report IDs; pending picture skill/version/checksums. No private text/reporters. |
| Publish/remove comment | `comment-publish ID`, `comment-remove ID` | Publish held text or delete it. |
| Mute/unmute | `mute namespace`, `unmute namespace` | Hold/prevent comments, or permit future comments. |
| Close reports | `reports-close publisher/name` | Wipe notes and end reports-caused pauses. |
| Pause/resume | `skill-pause publisher/name`, `skill-resume publisher/name` | Close reports and change download availability. |
| Mute report sender | `reporter-mute REPORT_ID` | Remove counting authority and hold comments without exposing identity. |
| Withdraw | `removed publisher/name`, `suspended publisher/name` | Remove every listed version and revoke their checksums. Restoration needs a higher version after clearing the line. |
| Suspend creator | `suspended namespace` | Withdraw listed skills; refuse pending/new uploads, also from the same sign-in under a new name after an account deletion. Remove the line to lift it. |
| Admit a team upload | `team-admit publisher/name SHA256` | Check and sign this one upload under a team's name; without the line it waits. |
| Approve picture | `picture publisher/name SHA256` | Exact skill/picture approval; remove line to withdraw it. |
| Curate | `official namespace`, `featured publisher/name`, `editors-pick publisher/name` | Maintainer labels, separate from admission/popularity. |
| Appoint/review | `reviewer namespace team|community`, `review publisher/name VERSION reviewer YYYY-MM-DD` | Exact-version public reviewer record; no self-review. |

Malformed syntax applies no file changes. Conflicting operator actions are refused. The resolved
signed marks envelope is limited to 65,536 bytes before any checkpoint/request; reduce retained
marks if this limit is reached. Service actions
share a transaction and are idempotent while present. Existing Tested/Highlighted/team/build forms
remain accepted. A paid review outage or used-up cap leaves the upload checking; it never grants
admission. The cap stops additional reviews after recorded spend reaches the limit; provider-side
hard billing limits are separate, because a single billed response can exceed the remaining amount.

The generated repository directory contains only current listed skills and their signed metadata,
release notes, targets and display metrics. Withdrawals remove current projection files; public Git
history remains. A stale optional metric feed preserves previous counters and does not stop the
signer. Only listed package bytes are mirrored, after publication succeeds. Private pending/refused
bytes and review text remain sealed/private. README links in an existing signer repository are an
owner source update; new setup uses the current template.

Checks use the implementation pinned by `authority/vool-commit`. Following release target metadata
does not itself update that pin or prove actual execution on each binary/platform. Keep the pin
reviewed and compatible; automated native-format validation and the exact-byte human Tested mark
are separate evidence. Direct PR CI/publisher protection in another community repository, standalone
email delivery/login, website pages, provider registrations and production smoke require activation.
The local backend and generated directory do not establish that those live services are active.
