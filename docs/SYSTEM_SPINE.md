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

The public web, Hive, OpenClaw, and watch/dashboard lanes are not separate startups hiding in one repo.
They are different ways to access or inspect the same runtime.

## The Main Layers

### 1. Local runtime

This is the center of gravity.

- [`apps/vool_agent.py`](../apps/vool_agent.py): main runtime brain
- [`apps/vool_api_server.py`](../apps/vool_api_server.py): local API and OpenClaw-facing entrypoint
- [`core/`](../core/): routing, tool execution, memory, research, Hive logic, and public-web renderers

### 2. Shared coordination

This is how agents discover, coordinate, and expose shared work.

- [`apps/meet_and_greet_server.py`](../apps/meet_and_greet_server.py): meet service plus public routes
- [`apps/brain_hive_watch_server.py`](../apps/brain_hive_watch_server.py): public read edge for Hive/watch surfaces
- [`network/`](../network/): transport, signer, protocol, peer models

### 3. Public proof and inspection surfaces

These make work legible to humans.

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

The top level is bigger than it should be, but the useful path is short:

1. [`README.md`](../README.md)
2. [`docs/STATUS.md`](STATUS.md)
3. [`docs/PROOF_PATH.md`](STATUS.md)
4. [`CONTRIBUTING.md`](../CONTRIBUTING.md)
5. then the architecture/API docs if you are changing a subsystem

Ignore the historical wrappers and archived handovers until you need them.

## What Outside Contributors Should Assume

- The local runtime is the product center.
- Hive, watch, and public web are proof and coordination surfaces around that center.
- The repo is alpha-serious, not production-finished.
- If you touch behavior, cumulative regression is mandatory.
- If you touch messaging, reduce ambiguity instead of adding more nouns.
