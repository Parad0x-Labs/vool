# DigitalOcean IP-First 4-Node Pack

## Purpose

This pack is the fastest low-cost way to run closed production-style testing on DigitalOcean before DNS naming.

It now expects IP-based HTTPS with a private closed-test CA, not raw public HTTP.

> **Note:** the configs in this pack ship with **example IPs** (`203.0.113.x`, RFC 5737 TEST-NET-3, non-routable). Replace them with your real node IPs before deploying, or export `EU_IP`/`US_IP`/`APAC_IP`/`WATCH_IP` for `do_ip_first_bootstrap.sh`. Auth tokens are env placeholders (`set-via-env-before-startup`) injected at deploy time from `CLUSTER_MEET_TOKEN`.

It uses:

- 3 meet seed nodes (`eu`, `us`, `apac`)
- 1 separate watcher edge node for Brain Hive read-only UI

All URLs are IP-based first. You can move to domain names later without changing topology.
For closed tests, bootstrap generates a private CA and per-node IP certificates automatically.

## Region Layout

Recommended first layout:

- EU meet: Amsterdam
- US meet: New York
- APAC meet: Singapore
- Watch edge: pick your primary audience region (usually EU or US)

## Droplet Size

Recommended minimum:

- `Basic` / `1 GB RAM` per node

You can run on `512 MB`, but it is tighter for soak tests and logs.

## Required Edits Before Startup

Replace all placeholders in every JSON file:

- `<EU_PUBLIC_IP>`
- `<US_PUBLIC_IP>`
- `<APAC_PUBLIC_IP>`
- `<WATCH_PUBLIC_IP>`

Set strong meet auth tokens before startup.
For fastest closed-test federation, use one strong shared cluster token across all meet seeds and the watch edge.
If you require per-node tokens, set `replication_config.auth_tokens_by_base_url` on meet nodes and `auth_tokens_by_base_url` on watch-edge.

For agents outside the droplets:

- preferred: distribute the generated cluster CA and point `tls_ca_file` at it
- fallback: use `tls_insecure_skip_verify=true` only for short closed tests

## Firewall Baseline

On each meet node:

- allow inbound `22/tcp` from your admin IPs
- allow inbound `8766/tcp` from:
  - the other meet nodes,
  - the watcher edge,
  - and trusted agent networks only
- deny broad world access to meet write surface during closed test

On watcher node:

- allow inbound `22/tcp` from your admin IPs
- allow inbound `8788/tcp` from your tester/read audience
- allow outbound `8766/tcp` to all three meet nodes

## Startup Commands

Meet nodes:

- `python3 ops/run_meet_node_from_config.py --config config/meet_clusters/do_ip_first_4node/seed-eu-1.json`
- `python3 ops/run_meet_node_from_config.py --config config/meet_clusters/do_ip_first_4node/seed-us-1.json`
- `python3 ops/run_meet_node_from_config.py --config config/meet_clusters/do_ip_first_4node/seed-apac-1.json`

Watcher node:

- `python3 ops/run_brain_hive_watch_from_config.py --config config/meet_clusters/do_ip_first_4node/watch-edge-1.json`

## One-Shot Bootstrap (Recommended)

Run from local repo root:

- `bash ops/do_ip_first_bootstrap.sh ~/Desktop/vool-ssh/vool_do_ed25519_v2`

Optional encrypted mesh mode for closed tests:

- set a shared 32-byte key (base64) in local env before bootstrap:
- `export VOOL_MESH_PSK_B64="<base64-32-byte-key>"`
- the bootstrap script forwards this into meet/watch runtime env on all four nodes.

This script does:

- SSH connectivity checks to all four droplets
- repo sync to `/opt/Decentralized_VOOL`
- Python venv + dependency install
- shared cluster meet auth-token generation + replication/watch token wiring
- private CA + per-node IP certificate generation and sync
- meet + watch startup with dedicated `VOOL_HOME`
- HTTPS health and watcher dashboard checks

## Verification

On each meet node, verify:

- `GET /v1/health` over HTTPS
- `GET /v1/cluster/nodes`
- `GET /v1/cluster/sync-state`

On watcher node, verify:

- `GET /health` over HTTPS
- `GET /api/dashboard` over HTTPS
- `GET /brain-hive` over HTTPS

Cross-region proof should show all three seed nodes converging in `cluster/nodes` and `sync-state`.

## Upgrade To Domain Later

When ready:

- replace IP URLs with DNS hostnames in the same files
- replace the closed-test CA/IP certs with proper domain certificates
- keep node IDs and region labels unchanged

No topology changes are needed for this migration.
