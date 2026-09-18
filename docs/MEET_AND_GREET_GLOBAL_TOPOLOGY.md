# Meet And Greet Global Topology

## Purpose

This document defines the first serious global topology for VOOL meet-and-greet coordination infrastructure.

It is designed for:

- mixed Windows, Linux, and macOS agents,
- 10 or more test machines,
- friend-to-friend and controlled global multi-node growth,
- and future scale without rebuilding the coordination model.

## Core Rule

Do not scale by turning every machine into a meet node.

Scale by:

- keeping most machines as agents,
- keeping a small number of meet nodes as infrastructure,
- keeping hot metadata small,
- and using local caches everywhere.

## First Global Shape

For the first global test, use:

- `seed-eu-1`
- `seed-us-1`
- `seed-apac-1`

All other machines act as agents.

That gives:

- basic geographic spread,
- lower latency for home-region agents,
- redundancy without coordination explosion,
- and a clean path to regional federation later.

## Home Region Model

Every meet node has a fixed `region`.

Every agent should also have a `home_region`.

That `home_region` is the default place where the agent's:

- presence is treated as detailed truth,
- knowledge ads are first-class local detail,
- lease refreshes are most frequent,
- and holder updates are most detailed.

An agent may also expose a `current_region` if it is running away from its usual home.

## Replication Rule

Use two sync shapes:

### In-Region Replication

Use:

- detailed snapshots
- detailed deltas

Mode:

- `regional_detail`

This is how nodes inside the same region converge on detailed hot state.

### Cross-Region Replication

Use:

- summarized snapshots
- avoid full-fidelity delta churn by default

Mode:

- `global_summary`

This is how regions exchange routing truth without pretending every remote region is a local database.

## What Stays Detailed

Inside a region, detailed state includes:

- active presence leases,
- peer endpoints,
- holder-level shard records,
- freshness,
- direct fetch routes,
- and detailed meet sync state.

## What Becomes Summarized Across Regions

Across regions, summarized state includes:

- presence with endpoint detail removed where appropriate,
- knowledge holder groups collapsed to regional routing hints,
- region-level replication counts,
- priority-region hints,
- and “ask that region” style fetch pointers instead of direct remote hot-state fanout.

## Why This Shape Scales Better

This prevents:

- one giant global meet brain,
- global full-fidelity heartbeat flood,
- every region mirroring every tiny detail,
- and runaway coordination noise as the coordination layer grows.

It also keeps a path open toward:

- 3 regions x 3 meet nodes later,
- regional failover,
- and eventually a global summary layer above regional clusters.

## Recommended Machine Roles

### Meet Nodes

Prefer:

- always-on Linux hosts,
- stable macOS hosts,
- or other machines with reliable uptime and network posture.

Meet nodes should not be casual disposable clients.

### Agents

Windows, Linux, and macOS are all valid agent platforms.

Agents should:

- keep local fallback behavior,
- keep local caches,
- know their nearest/home meet nodes,
- and continue local work if meet infrastructure is temporarily unavailable.

Phones should join this topology, when used, as companion agents or presence mirrors rather than meet nodes.

## Failure Assumptions

This topology should survive:

- one meet node disappearing,
- a delayed regional sync,
- reconnect storms,
- agent churn,
- stale lease expiry,
- and temporary inability to fetch remote-region detail.

That is why:

- every agent keeps local functionality,
- meet nodes replicate by snapshot/delta,
- and cross-region replication stays summarized.

## Expansion Path

### Stage 1

- 3 meet nodes total
- global test cluster

### Stage 2

- 3 regions x 3 meet nodes
- stronger failover and maintenance windows

### Stage 3

- regional detailed clusters
- global summary federation
- content and proof layers remain separate

## What Not To Do

Do not:

- make every agent a meet node
- replicate every lease row globally in full detail
- store full shard bodies in the meet layer
- put DNA proof flow on the hot path
- treat Liquefy vaults as the only live index

## Deployment Pack

The repository now includes a phase-one 3-node config pack under:

- `config/meet_clusters/global_3node/`

That pack is meant for the first real deployment rehearsal and should be the baseline before adding more meet nodes.

For closed production-style testing with hard separation between meet coordination and public watcher traffic, there is also:

- `config/meet_clusters/separated_watch_4node/`

This second pack adds one dedicated watch-edge host and keeps it separate from the three meet nodes.

For fastest first deployment before DNS/TLS, there is now an IP-first DigitalOcean pack:

- `config/meet_clusters/do_ip_first_4node/`

This keeps the same 3 meet + 1 watch shape and lets operators start with direct IP URLs, then migrate to hostnames later without changing node roles.
