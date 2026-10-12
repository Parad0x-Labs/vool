# Deployment and admission operations

Production activation is a separate reviewed action. No deployment, repository creation, installs, production key generation or paid scanner calls are performed by the local build.

Use the existing VPS for the static website, API and private account/quarantine database. Candidate execution belongs on isolated disposable workers. Keep the public GitHub artifact cache and signed catalog checkout separate from private state, the website deploy directory and signer custody. Back up each directory and configuration before every change; site deploys must not delete registry data.

## Activation checklist

1. Create the two community repositories from the reviewed blueprints, protect main and require independent review plus an authenticated admission check from its expected external GitHub App (or an independently protected workflow with verified workload identity). Candidate-run blueprint checks are advisory. Follow [ADMISSION-SERVICE.md](ADMISSION-SERVICE.md) and inspect the applied required-check source. Protect workflows, policies, roots and CODEOWNERS. A PR's own modified workflow is not trustworthy admission evidence. Published content must retain its original author and licence.
2. Configure a pinned scanner policy for the actual latest released VOOL build and supported platforms. Supply all eleven required reports from trusted workers. Unsupported coverage remains held. Bind workload identity to protected workflow/code revisions; never expose review/signing credentials to candidate execution.
3. Establish separate root, admission, review and online-catalog key custody. Root/admission/review thresholds are at least two. Publish only public keys in a reviewed trust anchor. Use encrypted offline keys/hardware custody; the web process holds no admission or review private key.
4. Private upload → signed archive/secret prepublication reports → author consent to the exact checksum → isolated publisher export → GitHub PR → final exact-commit reports → protected merge → offline admission signatures → offline catalog authorization → short-lived online catalog signature → canonical catalog PR/merge. Rejects remain visible only to the author. Direct GitHub PRs were public before scanning.
5. Register GitHub login with minimal public identity scope, Google with OpenID only, and Apple with a Services ID associated with a configured Sign in with Apple app. Configure exact callback URLs. The provider login token has no publication authority. Use a separate GitHub App with narrowly scoped, short-lived publisher credentials in the isolated publisher process.
6. Configure the service origin and immutable catalog/artifact inputs. Run `vool-registry check-config --config CONFIG` before starting it. The command fails if root, catalog, artifacts or requested provider registrations are missing/stale. Never use test fixtures as a production root or report.
7. Reverse proxy only `/registry/api/` to the localhost service. Preserve the existing relay/routes. Disable access, error, proxy, journal and backup request logging that could retain addresses or callback codes. Audit hosting/provider retention separately; do not claim no party ever processes an IP.
8. Use private directory mode 0700 and database mode 0600. Encrypt off-host backups, restrict operators, expire private quarantine after seven days and verify an actual restore. The restored catalog must meet signature/freshness/rollback rules; old account backups must respect the deletion retention policy.
9. Verify actual GitHub/Google/Apple login, account linking/deletion, secret rejection, failed scans, public-consent export, exact GitHub commit verification, signed browse, install/enable, update recovery, tamper/revocation and stats with production registrations on disposable accounts/profiles before opening the service publicly.

## Service configuration

`vool.registry.service.v1` requires `origin`, `private_dir`, `trusted_root`, `catalog_file`, `artifact_dir`, `target`, `providers`. Target is `{version,commit,platform}` for the released app. Catalog file is the canonical directory containing the signed registry projection. Artifacts are named by full transport checksum. Provider names are `github`, `google`, `apple`; their registrations come from `VOOL_REGISTRY_PROVIDER_CLIENT_ID` and `VOOL_REGISTRY_PROVIDER_CLIENT_SECRET` with PROVIDER uppercase. Apple's client secret is the developer-managed signed JWT and needs its own renewal process.

Start with `vool-registry serve --config CONFIG --port 8791` under a dedicated unprivileged account. It binds 127.0.0.1, ignores forwarded IP headers, disables access/error request logs and returns JSON errors without provider tokens. It imports verified snapshots and public artifacts; its database is a cache, not catalog admission authority. A stale snapshot stops new browse/download/install rather than reusing an unsigned list.

The proxy needs TLS, a 24 MiB body limit, strict host/origin routing, no credentials in URLs and a global request limiter keyed by the service name rather than client addresses. Example location settings:

```nginx
location /registry/api/ {
    access_log off;
    error_log /dev/null crit;
    client_max_body_size 24m;
    proxy_set_header Host vool.dev;
    proxy_set_header X-Forwarded-For "";
    proxy_pass http://127.0.0.1:8791;
}
```

Review the complete server configuration before applying this block. Request limiting, TLS and log retention are hosting policy, not permission to install packages. Keep diagnostics as aggregate health/error counters without identities or request payloads.

## Native activation and release limits

Ship the reviewed public trust anchor as `core/registry/trusted-root.json` in the actual v0.7.1 release, or install an operator-controlled anchor explicitly through local configuration. The native client verifies the existing packaged `BUILD_MANIFEST.json`: exact SHA/version, clean/release verdict, native verification and architecture. It does not accept a target, trust root or fetch URL from website input. Current released-manifest discovery covers macOS; other platform identity formats require their own real release integration before claiming support.

Do not change v0.7's release metadata or installed app while staging v0.7.1. The SDK/service wheel entry points add no new dependency. New registry UI labels are translated in all 17 app locales; website labels remain the frontend maintainer's work.

Production scanner adapters, provider registrations, protected repository settings, root custody, live compatibility and production smoke are required activation work. The local test suite proves mechanisms with ephemeral keys/provider/scanner fixtures; it does not certify a real submitted package or live OAuth account.
