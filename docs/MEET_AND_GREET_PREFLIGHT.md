# Meet And Greet Preflight

## Purpose

This document defines the minimum work that must be true before the meet-and-greet server becomes the next build target for VOOL.

The meet-and-greet server is not just a chat surface. It is the first shared entry point for:

- first-run identity creation,
- safe default sharing posture,
- shared presence visibility,
- knowledge-presence awareness,
- and friend-to-friend local multi-node bootstrapping.

That means it must sit on top of proven local behavior rather than trying to compensate for missing runtime fundamentals.

## What Is Already Ready

The following foundations now exist and are strong enough to support the next phase:

- standalone local VOOL remains valid and usable without the shared coordination layer,
- LAN mesh orchestration exists,
- the knowledge-presence layer exists,
- the shared coordination layer can track presence, holders, freshness, replication metadata, and fetch routes,
- task lifecycle and trace primitives exist,
- sandbox controls exist,
- local append-only audit and hash-chaining exist,
- persona storage already exists,
- and a human-input adaptation layer now normalizes shorthand, typo-heavy prompts, and session references before routing.
- the meet-and-greet contract and first server scaffold now exist locally.
- meet-node registry and pull-based snapshot/delta replication now exist locally.

This means the meet-and-greet phase can build on real product infrastructure instead of pure architecture.

## What Is Not Yet Proven Enough

The following items still need live proof before the meet-and-greet server should be treated as truly runtime-ready:

- cross-machine knowledge advertisement,
- replica acquisition and re-advertisement,
- presence lease expiry and offline prune behavior,
- knowledge version update propagation,
- reconnect and resync convergence,
- cross-machine parent/helper proof,
- helper-death recovery proof,
- event-chain tamper proof,
- protocol replay proof,
- and live transport integrity proof.

These are not design questions anymore. They are proof questions.

## Human Product Preflight

Before meet-and-greet, VOOL must be able to handle messy humans well enough that onboarding does not feel brittle.

The required baseline is:

- obvious shorthand and common typo normalization,
- session-aware reference handling,
- confidence-aware interpretation instead of fake certainty,
- natural response framing when the input is ambiguous,
- and clear separation between what VOOL understood and what it is still inferring.

This baseline now exists locally. It is still lightweight, but it is real and integrated.

## Server Scope For Phase One

The first meet-and-greet server should stay narrow.

Its job should be:

- create or load a local VOOL identity,
- let the user pick a name, personality, and lore or skip to a safe default,
- explain what VOOL shares by default,
- explain what stays local by default,
- show whether the node is online,
- show high-level shared presence,
- show high-level knowledge-presence metadata,
- and make friend-to-friend joining of a small trusted local cluster easy.

Its job should not be:

- real token or credit trading,
- public hostile-world participation,
- trustless payment settlement,
- global marketplace claims,
- or full autonomous internet behavior.

## Required Defaults Before Server Build

The meet-and-greet layer should not ship without these defaults being explicit:

- local-first mode is valid on its own,
- full knowledge stays local by default,
- only metadata is advertised by default,
- no silent remote execution,
- no silent web retrieval,
- no silent secret exposure,
- no mandatory sidecar dependency,
- and no economy-first onboarding.

## Packaging Preflight

Before sharing this with friends and secondary machines, the repo should be treated as a handoff package, not an internal scratch tree.

That means:

- the current docs must stay accurate,
- simulated systems must stay labeled simulated,
- startup assumptions must stay minimal,
- defaults must remain safe on a home or office LAN,
- and the first user experience must not depend on private local knowledge.

## Go Or No-Go

The current state supports a partial go:

- go for designing the meet-and-greet server contract,
- go for using the current meet-and-greet HTTP scaffold as the base implementation,
- go for testing with a small set of meet nodes and a larger set of agent machines,
- go for building the first-run onboarding flow,
- go for packaging the project for friend-to-friend local sharing.

The current state is still a no-go for stronger claims:

- no-go for calling the meet-and-greet layer production-ready,
- no-go for calling the shared knowledge index fully proven,
- no-go for introducing real credits or payment rails,
- and no-go for treating the system as hostile-internet-ready.

## Immediate Entry Gate

The next phase should start only after these two conditions are treated as mandatory:

1. The LAN proof checklist includes the knowledge-presence proof pack and is run on the real Windows and iMac mesh.
2. The meet-and-greet design keeps onboarding, identity, sharing posture, and presence visibility ahead of economics and public-network claims.
