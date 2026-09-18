# network/

This package owns helper/peer transport and network boundaries.

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
