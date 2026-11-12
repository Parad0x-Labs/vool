"""Private copies of approved X requests and their results, sealed at rest.

A documented X mutation waits for Settings approval with its exact request, and its answer can
hold private content (message text, account details). Neither may sit in plaintext next to the
action list. Both go to an encrypted blob store under the active data directory, reusing the
existing encrypted CAS and its keyring; only that small keyring lives in the credential store.

Each blob is wrapped with the action it belongs to and whether it is the request or the result,
so equal requests in two actions are separate blobs (erasing one never erases the other) and a
blob from one action can never be read back as another's. A blob's reference is keyed by the
keyring's id key: it cannot be used to test guesses about the content.

There is no plaintext fallback: without a keyring every call raises, and callers refuse.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from core.runtime_paths import active_data_dir

MAX_BYTES = 20 * 1024 * 1024
_SCHEMA = 'x-private-call-v1'
_KINDS = frozenset({'request', 'result'})


def _root():
    return active_data_dir() / 'x_private_calls'


def _store():
    from core.blackbox.coverage.cas_keys import resolve_keyring
    from storage.blackbox.cas import EncryptedBlobStore

    return EncryptedBlobStore(_root(), resolve_keyring())


def _encode(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def is_ref(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(ch in '0123456789abcdef' for ch in value)


def digest(value: Any) -> str:
    """A keyed identity of a request for receipts: equal requests match, guesses can't be tested."""
    return _store().id_for(hashlib.sha256(_encode(value)).hexdigest())


@dataclass(frozen=True)
class PrivateBlob:
    """A sealed copy that is ready to write. Its reference is known before it is written, so the
    action list can record it first (a crash then leaves a known reference, never an orphan)."""

    ref: str
    encoded: bytes = field(repr=False)
    store: Any = field(repr=False)

    def write(self) -> None:
        ref, _size = self.store.put(self.encoded, max_bytes=MAX_BYTES)
        if ref != self.ref:
            raise RuntimeError('The private X copy changed while it was saved.')


def prepare(action_id: str, kind: str, value: Any) -> PrivateBlob:
    if kind not in _KINDS or not isinstance(action_id, str) or not action_id.startswith('xa_'):
        raise ValueError('Invalid private X copy.')
    encoded = _encode({'schema': _SCHEMA, 'action_id': action_id, 'kind': kind, 'value': value})
    if len(encoded) > MAX_BYTES:
        raise ValueError('The X request or result is larger than VOOL keeps.')
    store = _store()
    return PrivateBlob(store.id_for(hashlib.sha256(encoded).hexdigest()), encoded, store)


def get(ref: str, action_id: str, kind: str) -> Any:
    """The value saved for exactly this action and kind. Raises when it is missing, tampered with,
    sealed under another key or saved for another action."""
    if not is_ref(ref):
        raise ValueError('Invalid private X copy reference.')
    envelope = json.loads(_store().get(ref))
    if (not isinstance(envelope, dict) or envelope.get('schema') != _SCHEMA
            or envelope.get('action_id') != action_id or envelope.get('kind') != kind):
        raise ValueError('That private X copy belongs to another action.')
    return envelope['value']


def delete(ref: str) -> bool:
    """Erase one copy. True only when it is gone afterwards."""
    if not is_ref(ref):
        return True
    if not _root().exists():
        return True
    store = _store()
    store.delete(ref)
    return not store.has(ref)


def sweep(keep: set[str]) -> int:
    """Erase copies no action refers to (left by a crash between saving a copy and the list).
    Run only under the action list lock. Needs no key and never creates a keyring or a folder."""
    root = _root()
    if not root.is_dir():
        return 0
    erased = 0
    for shard in sorted(root.iterdir()):
        if not shard.is_dir():
            continue
        for item in sorted(shard.iterdir()):
            if is_ref(item.name) and item.name not in keep and item.is_file():
                try:
                    item.unlink()
                    erased += 1
                except FileNotFoundError:
                    pass
    return erased


__all__ = ['MAX_BYTES', 'PrivateBlob', 'delete', 'digest', 'get', 'is_ref', 'prepare', 'sweep']
