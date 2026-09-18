"""PA Contacts: the one owner of the people and services the owner saved, and of their exact destinations.

* ``store``     -- durable contacts, aliases, endpoints, suggestions, sources and the change journal;
* ``endpoints`` -- validation and identity of email, phone, messaging and wallet endpoints (wallet rules are the wallet owner's);
* ``names``     -- deterministic Unicode-aware name matching;
* ``resolver``  -- one resolution path for every consumer, with snapshots consumers bind into their own approvals.

Consumers (email drafts, transfer proposals, calendar attendees) keep their own sending, signing and receipts.
"""
from __future__ import annotations

from core.contacts.resolver import Resolution, SnapshotCheck, describe_snapshot, resolve, verify_snapshot
from core.contacts.store import ACTOR_MODEL, ACTOR_MODEL_APPROVED, ACTOR_OWNER, ContactsError

__all__ = [
    "ACTOR_MODEL", "ACTOR_MODEL_APPROVED", "ACTOR_OWNER", "ContactsError", "Resolution", "SnapshotCheck", "describe_snapshot", "resolve", "verify_snapshot",
]
