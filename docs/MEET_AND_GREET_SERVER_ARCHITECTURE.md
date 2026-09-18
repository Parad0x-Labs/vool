# Meet And Greet Server Architecture

## Purpose

The meet-and-greet server is the first shared entry point for trusted multi-node VOOL alpha.

Its job is to make local and friend-to-friend multi-node joining simple, redundant, and safe without turning the live coordination loop into a heavy archive system.

This service is not the product center.

It is the coordination layer that helps agents:

- appear online,
- discover each other,
- advertise safe metadata,
- locate relevant knowledge,
- observe holder and version state,
- and coordinate fetch and payment status at a high level.

## Core Design Rule

Use three separate planes:

1. Hot coordination plane
2. Content plane
3. Payment and proof plane

Those planes should interact, but they should not collapse into one storage model.

## Operational Safety Rule

The meet layer must be treated as coordination infrastructure, not an open public bulletin board.

Current default posture:

- local meet nodes bind to loopback by default,
- non-loopback deployment must use an explicit auth token,
- write requests are body-size capped,
- and write traffic is rate-limited.

This is a safer default for local and trusted multi-node deployment. It is still not a complete hostile-internet security story.

## Plane 1: Hot Coordination Plane

This is the actual meet-and-greet service.

It owns fast-changing metadata:

- presence leases,
- last heartbeat,
- reachable endpoints,
- transport mode,
- capability summary,
- shard manifest metadata,
- holder maps,
- current shard version,
- freshness and TTL,
- replication counts,
- fetch routes,
- trust and status flags,
- and high-level payment status markers.

This plane should stay:

- plain,
- canonical,
- mutable,
- cheap to query,
- cheap to update,
- and easy to expire.

It should not depend on unpacking compressed vaults during normal coordination operation.

## Plane 2: Content Plane

This is where Liquefy belongs.

Liquefy should be used for:

- full knowledge shard bodies,
- larger manifests,
- task and result bundles,
- replicated shard payloads,
- audit and proof bundles,
- exported coordination snapshots,
- and historical archives.

This fits the current VOOL and Liquefy shape because the project already has:

- content-addressed storage,
- chunked payload handling,
- manifest tracking,
- deduplicated blobs,
- and optional Liquefy sidecar integration.

The content plane should be able to serve:

- on-demand shard fetch,
- snapshot export,
- forensic replay,
- and cheap storage growth through deduplication.

## Plane 3: Payment And Proof Plane

This is where DNA-related receipts and proof artifacts belong.

The DNA sidecar should own:

- payment event export,
- signed receipt capture,
- settlement proof artifacts,
- and eventual premium-service accounting trails.

This plane must be asynchronous relative to the hot coordination plane.

The meet-and-greet service may expose high-level payment state such as:

- unpaid,
- reserved,
- paid,
- disputed,
- failed,

but it should not wait on DNA proof packing just to route or index a live peer.

## Storage Rule

Metadata stays plain.
Payloads get packed.
Proofs get archived.

That means:

- live index rows remain directly queryable,
- larger content objects can be packed into Liquefy-backed or CAS-backed containers,
- and proof artifacts become part of a slower audit trail.

## What Should Be Stored Plain

Keep these in the hot index:

- agent identifier,
- display name,
- agent summary,
- status,
- last heartbeat,
- lease expiry,
- endpoint list,
- transport mode,
- capability list,
- shard identifier,
- content hash,
- version,
- topic tags,
- summary digest,
- holder peer identifier,
- freshness timestamp,
- TTL,
- replication count,
- fetch route,
- trust score,
- and payment state marker.

These are small, mutable, and hot.

## What Should Be Packed

Pack these into CAS and, where useful, Liquefy:

- full shard content,
- large shard manifests,
- result bundles,
- audit exports,
- trace bundles,
- replicated content snapshots,
- payment proof batches,
- and compressed historical index snapshots.

## Compression Policy

Do not compress everything.

Use compression only where it helps.

Good candidates:

- full content blobs,
- multi-record bundles,
- large manifests,
- proof exports,
- snapshot archives.

Bad candidates for default compression:

- heartbeats,
- lease rows,
- holder counters,
- routing metadata,
- tiny updates,
- and hot lookup keys.

Compression is a transport and storage optimization.
It should not become the identity or lookup model for the live index.

## Liquefy Handoff Thresholds

The meet-and-greet service should only hand data off to Liquefy when at least one of these is true:

- payload is larger than the hot-index threshold,
- payload is expected to be replicated,
- payload should be archived,
- payload is a proof or audit bundle,
- payload is a snapshot export,
- or payload should benefit from CAS deduplication.

Recommended rule for phase one:

- metadata records stay inline,
- shard bodies and bundle payloads go to CAS,
- Liquefy packing is used for archives, exports, replication bundles, and proof history.

## Redundancy Model

For a small or early global multi-node alpha, run three meet-and-greet nodes.

For heavier global testing across 10 or more agent machines, expand to three or five meet-and-greet nodes rather than trying to make every agent machine a coordinator.

Each meet node should keep:

- the hot metadata index,
- an append-only delta log,
- a periodic compacted snapshot,
- and enough cache state to rebuild after restart.

Each agent should also keep a local cache of the knowledge-presence view so shared coordination does not become useless if every meet node is temporarily offline.

This gives:

- cheap redundancy,
- low operating cost,
- graceful degradation,
- and easier friend-to-friend sharing.

Recommended global shape:

- one meet node in Europe,
- one meet node in North America,
- one meet node in Asia-Pacific,
- optional fourth and fifth nodes for failover rather than more write throughput.

## Replication Model

The meet-and-greet service should replicate metadata using:

- append-only deltas,
- periodic snapshots,
- versioned change identifiers,
- and lease-based expiry.

The current implementation uses pull-based replication first.

That is the safer default for a mixed Windows, Linux, and macOS test cluster because it reduces inbound dependency assumptions and works better across uneven NAT and firewall conditions.

The current federation rule is:

- same-region sync uses `regional_detail`
- cross-region sync uses `global_summary`

That means:

- detailed hot truth stays regional,
- cross-region state is summarized into routing-grade metadata,
- and remote regions are not treated as if they were local high-fidelity hot state.

The goal is not perfect global consensus.
The goal is fast practical convergence for a trusted local or friend-to-friend cluster.

## Region Model

Every meet node has a fixed `region`.

Every agent should expose:

- `home_region`
- and optionally `current_region`

The region model exists so the system can prefer:

- detailed local truth,
- smaller global summaries,
- and a later path to regional clusters without changing the core contract.

## Hot Index Schema

The hot index needs these logical record types:

### Agent Presence

- agent_id
- display_name
- status
- transport_mode
- endpoints
- capability_summary
- trust_score
- last_heartbeat_at
- lease_expires_at

### Knowledge Manifest

- shard_id
- content_hash
- version
- topic_tags
- summary_digest
- size_bytes
- access_mode
- canonical_holder_hint

### Knowledge Holder

- shard_id
- holder_peer_id
- version
- freshness_ts
- expires_at
- trust_weight
- fetch_route
- status

### Replication State

- shard_id
- current_version
- replication_count
- live_holder_count
- stale_holder_count
- last_updated_at

### Payment Status Marker

- task_or_transfer_id
- payer_peer_id
- payee_peer_id
- status
- receipt_reference
- updated_at

## Required API Surface

The meet-and-greet service should expose a small contract:

- register presence
- refresh heartbeat
- withdraw presence
- advertise knowledge manifest
- advertise holder possession
- withdraw holder possession
- fetch holder map
- fetch relevant manifests by tags or problem class
- fetch delta stream since version N
- fetch compact snapshot
- publish payment status marker

This is a metadata service contract, not a full blob-delivery contract.

## Search Model

Search should happen in two stages.

Stage one:

- query the hot metadata index by tags, digest, problem class, holder count, trust, and freshness.

Stage two:

- fetch actual content through the content plane only if needed.

This keeps search fast while still letting Liquefy-backed content stay compressed.

## Failure Rules

The service must fail in ways that preserve local usefulness.

If a meet-and-greet node is down:

- local VOOL still works,
- cached coordination metadata still works,
- shard fetch can still work peer-to-peer if routes are known,
- and new presence updates can be retried later.

If Liquefy is unavailable:

- the hot index must still function,
- live metadata updates must continue,
- and archive packing should degrade gracefully.

If DNA proof export is unavailable:

- payment state may remain pending or unproven,
- but live coordination must not halt.

If one meet node is stale:

- lease expiry and delta versioning should eventually correct the view,
- and snapshot refresh should repair drift.

## Security Rules

The meet-and-greet layer must never become a privacy leak by convenience.

It should:

- advertise metadata only by default,
- keep full content local unless fetched,
- avoid raw private prompt storage in the hot index,
- avoid open unauthenticated public write deployment,
- require explicit operator intent before binding publicly,
- cap request bodies and throttle write clients,
- avoid silent secret propagation,
- treat fetch routes as mutable and expirable,
- and mark stale or missing holders clearly.

## Integration With Current VOOL

This design fits the current codebase directly:

- knowledge presence already exists,
- holder and manifest storage already exists,
- CAS already exists,
- chunk transport already exists,
- Liquefy is already an optional sidecar,
- DNA payment hooks already exist as simulated or sidecar-based plumbing,
- and standalone local mode remains valid.

So the meet-and-greet server should be built as a thin coordination layer over the existing knowledge-presence and storage foundations, not as a replacement for them.

## Phase-One Scope

Phase one should deliver:

- redundant metadata index nodes,
- presence lease registration,
- knowledge manifest and holder advertisement,
- delta and snapshot replication,
- local cache fallback,
- fetch route lookup,
- and optional handoff to CAS and Liquefy for payloads and archives.

Phase one should not try to deliver:

- public trustless routing,
- mandatory Liquefy dependency,
- mandatory DNA dependency,
- full marketplace settlement,
- or hostile-internet guarantees.

## Final Rule

The meet-and-greet service should be the shared coordination layer's live directory, not its entire brain and not its entire vault.

Plain metadata for coordination.
Packed content for storage and transfer.
Asynchronous proofs for payment and audit.
