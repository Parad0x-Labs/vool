"""Blackbox: the byte-exact flight recorder for authorized workspace file effects.

- ``recorder``  wraps one authorized mutation: INTENDED before bytes move, TERMINAL before any
                caller hears "done", before/after bytes in a verified CAS
- ``rollback``  explicit, all-or-nothing, idempotent, resumable rollback of one turn, through the
                authorized execution boundary
- ``authority`` operator tokens a model cannot mint or carry
- ``store``     journal + blobs + recovery + retention + status
- ``operator``  the entry points an operator (or ``python -m core.blackbox``) uses
"""
from core.blackbox.operator import list_turns, rollback_turn, status, verify
from core.blackbox.store import BlackboxStore, default_store, reset_default_store, store_root

__all__ = [
    "BlackboxStore",
    "default_store",
    "list_turns",
    "reset_default_store",
    "rollback_turn",
    "status",
    "store_root",
    "verify",
]
