# network/

This package owns helper/peer transport and network boundaries.

The mesh, DHT and peer-discovery stack is **retained experimental research**, not
normal production networking. See [research activation](../research/README.md).
Shared signing, authentication and transport helpers can have callers outside the
daemon; preserve them and their tests. [Repository scope](../docs/REPOSITORY_SCOPE.md)
distinguishes local orchestration from remote peer work.

It should separate:

- transport
- protocol/envelope
- auth/signing
- routing
- rate limit / quarantine

from higher-level product behavior.

## What Lives Here

- transport and chunking:
  - `transport.py`
  - `stream_transport.py`
  - `chunk_protocol.py`
  - `transfer_manager.py`
- routing and peer layers:
  - `assist_router.py`
  - `knowledge_router.py`
  - `peer_manager.py`
- auth / integrity:
  - `signer.py`
  - `pow_hashcash.py`
  - `quarantine.py`
  - `rate_limiter.py`

## Boundary Rule

Business logic should not hide in transport code.

If a change is really about Hive/task behavior, it should land in `core/`, not in the network transport layer.
