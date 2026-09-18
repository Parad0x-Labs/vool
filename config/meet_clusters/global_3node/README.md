# Global 3-Node Meet Cluster Pack

## Purpose

This pack defines the first serious global meet-and-greet deployment shape for VOOL.

It is designed for:

- one seed in Europe,
- one seed in North America,
- one seed in Asia-Pacific,
- and many agents across Windows, Linux, and macOS.

## Pack Contents

- `cluster_manifest.json`
- `seed-eu-1.json`
- `seed-us-1.json`
- `seed-apac-1.json`
- `agent-bootstrap.sample.json`

## Design Rule

This pack assumes:

- each seed node is a meet-and-greet infrastructure node,
- each seed node owns detailed truth for its own region,
- cross-region sync uses summarized snapshots by default,
- and agents keep local caches and local fallback behavior.
- each public bind must replace the placeholder `auth_token` before deployment.
- the `*.example.vool` hostnames are placeholders and must be replaced before deployment.

## Region Mapping

- `seed-eu-1` -> `eu`
- `seed-us-1` -> `us`
- `seed-apac-1` -> `apac`

## Why Only 3 Meet Nodes

This pack is intentionally small.

The goal is to prove:

- regional federation,
- cross-region summary replication,
- and resilient agent bootstrap

without creating unnecessary coordination noise.

## Agent Rule

Agents should:

- declare a `home_region`,
- prefer the closest regional seed first,
- keep the other two seeds as failover awareness,
- and continue functioning locally if all meet nodes are temporarily unavailable.

## Expansion Rule

Use this pack first.

Only add more meet nodes after:

- regional sync is proven,
- failover is proven,
- and agent bootstrap behavior is stable across your real multi-machine test swarm.

## Placeholder Warning

This pack is intentionally a sample handoff pack, not a ready-to-expose internet deployment.

Replace at least these before real use:

- placeholder public base URLs,
- placeholder auth tokens,
- any sample bind host or port values that do not match the target machine,
- and any sample seed-peer metadata that does not match the real topology.
