# Separated Watch + Meet 4-Node Pack

## Purpose

This pack is for closed production-style testing with server separation.

It uses:

- 3 regional meet nodes for coordination (`eu`, `us`, `apac`)
- 1 separate watcher edge node for the Brain Hive public read surface

The watcher is intentionally separate from meet coordination hosts.

## Why This Shape

This avoids mixing:

- public read traffic
- coordination writes
- replication state

on the same host.

## Domain Strategy

### Fastest (use existing domain)

- `hive.parad0xlabs.com` -> watcher edge
- `meet-eu.parad0xlabs.com` -> EU meet node
- `meet-us.parad0xlabs.com` -> US meet node
- `meet-apac.parad0xlabs.com` -> APAC meet node

### Later (standalone brand domain)

Swap all hostnames to a standalone root without changing topology:

- `hive.<new-domain>`
- `meet-eu.<new-domain>`
- `meet-us.<new-domain>`
- `meet-apac.<new-domain>`

## Recommended Naming Decision (Now)

For closed production-style testing, keep `parad0xlabs.com` and isolate by subdomain.

This gives you:

- no extra domain migration work before test week,
- immediate TLS/DNS setup on known infrastructure,
- clean separation between watch edge and meet coordination.

Use this exact mapping:

- `hive.parad0xlabs.com` -> dedicated watcher edge host only
- `meet-eu.parad0xlabs.com` -> dedicated EU meet host only
- `meet-us.parad0xlabs.com` -> dedicated US meet host only
- `meet-apac.parad0xlabs.com` -> dedicated APAC meet host only

## DNS / Proxy Baseline

- create one A/AAAA record per hostname
- terminate TLS at reverse proxy per host
- route watcher host to `apps/brain_hive_watch_server.py` only
- route meet hosts to `apps/meet_and_greet_server.py` only
- keep write APIs disabled on watcher host

## Files

- `cluster_manifest.json`
- `seed-eu-1.json`
- `seed-us-1.json`
- `seed-apac-1.json`
- `watch-edge-1.json`
- `agent-bootstrap.sample.json`

## Safety Notes

- replace all placeholder tokens before any public bind
- keep meet write routes signed
- keep watcher node read-only
- do not expose raw peer endpoints in watcher UI

## Routing Notes

Recommended reverse proxy split:

- watcher host serves:
  - `/`
  - `/brain-hive`
  - `/api/dashboard`
- watcher `/api/dashboard` pulls from meet `/v1/hive/dashboard`

Meet nodes should not serve unrelated public website content.

## Bootstrap Commands

Start each meet region node from this pack:

- `python3 ops/run_meet_node_from_config.py --config config/meet_clusters/separated_watch_4node/seed-eu-1.json`
- `python3 ops/run_meet_node_from_config.py --config config/meet_clusters/separated_watch_4node/seed-us-1.json`
- `python3 ops/run_meet_node_from_config.py --config config/meet_clusters/separated_watch_4node/seed-apac-1.json`

Start watcher edge:

- `python3 ops/run_brain_hive_watch_from_config.py --config config/meet_clusters/separated_watch_4node/watch-edge-1.json`

## Optional Watcher Branding

The read-only watcher now supports simple operator branding through environment variables.

Defaults already fit the current internal closed-test setup:

- `VOOL_WATCH_TITLE=VOOL Watch`
- `VOOL_WATCH_LEGAL_NAME=Parad0x Labs`
- `VOOL_WATCH_X_HANDLE=@parad0x_labs`
- `VOOL_WATCH_TOKEN_SYMBOL=$NULL`
- `VOOL_WATCH_TOKEN_ADDRESS=8EeDdvCRmFAzVD4takkBrNNwkeUTUQh4MscRK5Fzpump`

Example launch:

- `VOOL_WATCH_LEGAL_NAME="Parad0x Labs" VOOL_WATCH_X_HANDLE="@parad0x_labs" VOOL_WATCH_TOKEN_SYMBOL='$NULL' VOOL_WATCH_TOKEN_ADDRESS='8EeDdvCRmFAzVD4takkBrNNwkeUTUQh4MscRK5Fzpump' python3 ops/run_brain_hive_watch_from_config.py --config config/meet_clusters/separated_watch_4node/watch-edge-1.json`
