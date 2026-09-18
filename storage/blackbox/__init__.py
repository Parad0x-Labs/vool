"""Blackbox durable storage: a verified blob CAS and a hash-chained, MAC'd, crash-ordered journal.

Deliberately independent of the shared SQLite database and of ``storage.cas`` (which stores
chunks in that database for the knowledge/swarm lanes and is being reworked by another lane): a
rollback of a user's file must not depend on the runtime database being open, migrated or intact.
"""
from storage.blackbox.blobs import BlobCorruptError, BlobError, BlobMissingError, BlobRef, BlobStore, BlobTooLargeError
from storage.blackbox.journal import ChainReport, Journal, JournalError, JournalIntegrityError

__all__ = [
    "BlobCorruptError",
    "BlobError",
    "BlobMissingError",
    "BlobRef",
    "BlobStore",
    "BlobTooLargeError",
    "ChainReport",
    "Journal",
    "JournalError",
    "JournalIntegrityError",
]
