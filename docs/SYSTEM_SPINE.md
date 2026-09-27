# VOOL System Spine

VOOL is one system:

`local VOOL agent -> memory + tools -> optional trusted helpers -> visible results`

If a surface or module does not fit that sentence, treat it as support infrastructure, not as a separate product.

## What The Repo Actually Is

At its core, VOOL is a local-first agent runtime that can:

- run on one machine
- keep memory and context
- use tools and do bounded research
- publish or coordinate work through optional shared surfaces

The local runtime is the product. Hive, meet-and-greet and watch/dashboard code is
retained research, not the default production networking path. OpenClaw-specific
integration is retired product scope with residual installer wiring still to remove.
See the [subsystem status map](REPOSITORY_SCOPE.md) for evidence and boundaries.

## The Main Layers

### 1. Local runtime

This is the center of gravity.

- [`apps/vool_agent.py`](../apps/vool_agent.py): main runtime brain
- [`apps/vool_api_server.py`](../apps/vool_api_server.py): local API entrypoint
- [`core/`](../core/): routing, tool execution, memory, research, Hive logic, and public-web renderers

### 2. Retained research: shared coordination

These services implement experimental peer discovery and shared work. They are
not normal desktop startup requirements; see [research activation](../research/README.md).

- [`apps/meet_and_greet_server.py`](../apps/meet_and_greet_server.py): meet service plus public routes
- [`apps/brain_hive_watch_server.py`](../apps/brain_hive_watch_server.py): public read edge for Hive/watch surfaces
- [`network/`](../network/): transport, signer, protocol, peer models

### 3. Retained research: public Hive inspection surfaces

These describe Hive service views, not a claim that a public service is deployed.

- `Worklog`: public work and research drops
- `Tasks`: open, partial, solved work
- `Operators`: who did what
- `Proof`: work worth checking
- `Coordination`: the denser dashboard and shared task-state view

### 4. Future / partial layers

These exist, but they are not the main claim today.

- WAN routing hardening
- broader multi-node proof
- local credit/accounting evolution and any future settlement hooks
- marketplace or plugin distribution

Do not read these layers as the product center.

## Historically Grown Names

Some names are historically grown and can make the repo look wider than it is.

- `Brain Hive`: task and research commons
- `Meet And Greet`: coordination and presence layer
- `VoolBook`: public web presentation layer
- `Watch`: read-only dashboard/read edge

Those names survived because they describe real sub-surfaces, but they all sit on the same system spine.

## How To Read The Top Level

Start with these product and engineering entrypoints:

1. [`README.md`](../README.md)
2. [`docs/STATUS.md`](STATUS.md)
3. [Repository scope and subsystem status](REPOSITORY_SCOPE.md)
4. [`CONTRIBUTING.md`](../CONTRIBUTING.md)
5. then the architecture/API docs if you are changing a subsystem

Ignore the historical wrappers and archived handovers until you need them.

## What Outside Contributors Should Assume

- The local runtime is the product center.
- Hive, watch, and peer public-web surfaces are retained research; local chat and
  local execution receipts are separate current runtime surfaces.
- Implementation status and release readiness are distinct; consult the current
  release documentation rather than inferring support from source presence.
- If you touch behavior, cumulative regression is mandatory.
- If you touch messaging, reduce ambiguity instead of adding more nouns.


## Related ecosystem projects

These are separate Parad0x Labs projects. Their role in the ecosystem does not imply
that every capability is enabled in the VOOL runtime.

### Dark Null Protocol

[Dark Null Protocol](https://github.com/Parad0x-Labs/Dark-Null-Protocol) is the ecosystem's
privacy-preserving settlement project, using Groth16 zero-knowledge proofs. Its protocol
and deployment details belong to that project's documentation. VOOL's own wallet and
payment permissions are governed by its runtime configuration and spending policy.
