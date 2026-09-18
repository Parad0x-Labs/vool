# storage/

This package owns persistence.

Split it mentally into three layers:

1. foundational primitives
2. memory/knowledge persistence
3. feature-specific stores

## Foundational

- `db.py`
- `migrations.py`
- `cas.py`
- `chunk_store.py`
- `blob_index.py`
- `manifest_store.py`
- `event_log.py`

## Memory / Knowledge

- `dialogue_memory.py`
- `swarm_memory.py`
- `knowledge_index.py`
- `knowledge_manifests.py`
- `swarm_knowledge_archive.py`

## Feature Stores

- `brain_hive_store.py`
- `voolbook_store.py`
- `adaptation_store.py`
- `dna_wallet_store.py`
- `useful_output_store.py`

## Boundary Rule

Feature stores should depend on persistence primitives, not entangle directly with each other.

If two features need shared storage behavior, move that behavior downward into a primitive or shared storage helper instead of cross-linking feature stores.
