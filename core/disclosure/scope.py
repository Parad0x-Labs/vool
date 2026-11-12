"""Invalidate preview caches against the existing chat and erasure authorities.

This version check governs an existing preview's lifetime. It neither classifies
new request content nor substitutes for the retrieval authority's own admission.
"""
from __future__ import annotations

import hashlib


def current_version(chat_id: str, project_id: str) -> str | None:
    if not chat_id:
        return None
    from core.context_namespace import load_chat_namespace

    namespace = load_chat_namespace(chat_id)
    if namespace is None:
        # Legacy internal helpers have no canonical chat and cannot retain a
        # durable copy. Their byte envelope is still consumed exactly once.
        return None
    if namespace.lifecycle_state != 'active' or namespace.project_id != project_id:
        raise ValueError('This chat changed. Nothing was sent.')
    from core.memory.revocation import generation, serialized, snapshot

    with serialized():
        snapshot(chat_id)  # Incomplete or unreadable erasure fails closed.
        epoch = generation(chat_id)
    return hashlib.sha256((namespace.project_id + '\n' + epoch).encode()).hexdigest()
