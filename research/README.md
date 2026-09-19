# Research systems

> **Experimental research. Not part of the standard VOOL production runtime.
> Not shipped or enabled in official releases. Security assumptions, APIs and
> architecture may change or be discarded.**

This directory documents VOOL's research/production boundary. The research
systems still live in their original locations (moving ~40 interconnected
modules would rewrite hundreds of imports across the test suite for zero
behavioral gain); the boundary is a **runtime gate** instead — an equally hard
one, enforced by `core/runtime_mode.py` and proven by
`tests/test_production_research_boundary.py`.

## What is research

| System | Where it lives | What it does |
|---|---|---|
| Mesh daemon (UDP/TCP transport) | `core/agent_runtime/daemon.py`, `core/daemon/`, `network/transport.py`, `apps/vool_node.py` | Peer-to-peer UDP 49152 / TCP 49153 mesh with envelopes, presence, knowledge shards and remote tasks |
| Public hive presence bridge | `core/public_hive/`, `core/public_hive_bridge.py` | Presence heartbeats, commons updates and topic writes to configured hive seeds (`meet-*.parad0xlabs.com` clusters) |
| Meet-and-greet swarm | `apps/meet_and_greet_node.py`, `apps/meet_and_greet_server.py`, `core/meet_and_greet_*.py` | F2F swarm joining, seed clusters, global topology |
| Brain Hive watch | `apps/brain_hive_watch_server.py` | Watch server over the hive |
| Autonomous peer work ("hive tasks") | `core/daemon/tasks.py`, `sandbox/helper_worker.py`, `core/daemon/mesh.py` | Executes TASK_ASSIGN capsules from remote peers (trust/capability-token guarded) |
| Idle commons / autonomous research | `core/agent_runtime/presence.py`, `core/curiosity_roamer.py` | Idle-time hive posts under the user identity; pulls the public research queue |
| Swarm query shards | `retrieval/swarm_query.py`, `core/shard_synthesizer.py`, `core/daemon/messages.py` | Broadcasts QUERY_SHARD to peers; serves learned summaries back |

## The boundary

A production build **never** starts any of the above. Every production choke
point consults `core/runtime_mode.py`:

- the API runtime does not boot the mesh daemon (no UDP 49152 / TCP 49153
  listener, no STUN public-endpoint probe);
- the agent starts no presence heartbeat, no idle-commons loop, no autonomous
  hive-research thread, and performs no startup presence sync;
- turns never broadcast swarm QUERY_SHARDs;
- a port bind conflict never kills the holding process — production falls back
  to an ephemeral port;
- the public-hive bridge is never invoked by the agent.

## Explicit research invocation

```bash
VOOL_RESEARCH_NETWORKING=1 python -m apps.vool_daemon          # mesh daemon CLI
VOOL_RESEARCH_NETWORKING=1 python -m apps.meet_and_greet_node  # swarm node
VOOL_RESEARCH_NETWORKING=1 python -m apps.brain_hive_watch_server
```

The variable is an environment opt-in only. It is deliberately **not** a
preference, config-file key or product-edition flag, so it cannot be switched
on accidentally through normal user settings. The research runners above set
it themselves when invoked through their own tooling.

Tests exercise the research systems directly (constructing daemons and
transports against loopback ports inside the test tree); that is a research
invocation of the library code and does not affect shipped builds.

## Verification

`tests/test_production_research_boundary.py` proves, for a default
production boot: no mesh listener on UDP 49152 or TCP 49153, no presence
heartbeat threads, no swarm dispatch, no stale-port kills, no STUN probe —
including a live subprocess boot of the real API server.
