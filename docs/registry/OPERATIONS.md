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

Review and run `nginx -t` on the complete server configuration before applying it. Mirror the rejecting defaults for any IPv6 listeners. The TLS rejection directive requires nginx 1.19.4 or later. Passing `$host` preserves the app's Host check; replacing every incoming Host with `vool.dev` would make that check ineffective. Origin and CSRF checks remain required. The per-client zone processes addresses in shared memory; do not persist them or claim they are never processed. See the nginx [request limiter](https://nginx.org/en/docs/http/ngx_http_limit_req_module.html), [TLS rejection](https://nginx.org/en/docs/http/ngx_http_ssl_module.html#ssl_reject_handshake) and [default-server routing](https://nginx.org/en/docs/http/request_processing.html) documentation. Request limiting, TLS and log retention grant no package permissions. Keep diagnostics as aggregate health/error counters without identities or request payloads.

## v0.7.1 service limits

- External GitHub publisher-to-account linking is not implemented. A login or a self-declared GitHub handle is not publisher proof. Direct GitHub imports suppress unlinked website creator profiles and uploads, even if the account claimed the name earlier. Only an exact, consented service-created draft can establish its account's publication association. Legacy imported publications have no inferred account owner. Operators must call `Store.reserve_publication(package_id)` when a pending external publication becomes known, before account claims. Unobserved public PRs cannot be reserved automatically; connect the protected admission service's pending-publication feed before promising universal preclaim protection. Namespace transfer requires maintainer review and has no automatic route.
- Download tickets expire after 15 minutes and are authenticated by a private HMAC key in the account database. Issuance stores no ticket rows and has no global capacity cap. Only served-artifact attempts increment `download_requests`; the same ticket's retries count once. This is not a unique-person or completed-byte metric. Tickets issued before this format upgrade must be refreshed; losing/rotating the HMAC key also requires fresh tickets.
- Login flows expire after ten minutes. A browser/provider pair may have eight pending flows; another browser or provider remains available. Cookie churn can still create many flows, so the per-client proxy limiter is required. Existing state, PKCE, nonce, flow-cookie binding and one-use checks remain active.
- Upload admission checks namespace ownership and the ten-submissions-per-24-hours quota before reading either artifact or draft bodies, and before inspecting/scanning/packing direct Store calls. SQLite leases allow four active uploads across workers and one per account; refused requests consume no body. API body reads time out after 30 seconds. Lease cleanup runs on success, refusal and cancellation; a crashed worker's lease expires after five minutes. Expired or revoked leases cannot commit an upload. Size limits and the proxy limiter are still required.
- The secure, host-only session cookie is `vool_registry`, scoped to `/registry/api`; it does not use the `__Host-` prefix. Deploy with **no untrusted subdomains** beneath the service's parent domain. Do not treat path scoping as protection against a hostile sibling subdomain.
- Stars are one per authenticated account/package, not one per person. Throwaway accounts can inflate stars; ranking is not a trust or admission signal.
- The website account-link POST returns a 303 redirect to the provider. A `fetch()` call may not navigate the page. Before website handover, test complete browser navigation through linking, callback, cancellation and return to the account screen with real provider registrations.

## Native activation and release limits

Ship the reviewed public trust anchor as `core/registry/trusted-root.json` in the actual v0.7.1 release, or install an operator-controlled anchor explicitly through local configuration. The native client verifies the existing packaged `BUILD_MANIFEST.json`: exact SHA/version, clean/release verdict, native verification and architecture. It does not accept a target, trust root or fetch URL from website input. Current released-manifest discovery covers macOS; other platform identity formats require their own real release integration before claiming support.

Do not change v0.7's release metadata or installed app while staging v0.7.1. The SDK/service wheel entry points add no new dependency. New registry UI labels are translated in all 17 app locales; website labels remain the frontend maintainer's work.

Production scanner adapters, provider registrations, protected repository settings, root custody, live compatibility and production smoke are required activation work. The local test suite proves mechanisms with ephemeral keys/provider/scanner fixtures; it does not certify a real submitted package or live OAuth account.

Root rotation accepts exactly the next version. An app update that skips a root version leaves an existing registry cache refused; retries cannot bridge the missing version. Ship and apply every intermediate dual-signed root in order. The current client has no automatic intermediate-root retrieval, so release integration must prove a multi-version update before offering it.

Native catalog refresh currently performs O(n²) signature verification by resolving entries against the catalog repeatedly. Keep catalog size within measured release budgets; this pass does not replace that algorithm or claim large-catalog performance.
