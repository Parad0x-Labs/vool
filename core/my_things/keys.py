"""A household-record key purpose in the existing credential authority.

The cipher and keyring implementation are the existing encrypted-store APIs. A distinct
credential prevents the flight recorder's independent rotation or reset from orphaning private
household records. Resolution is called while the records SQLite write lock is held; concurrent
first writes cannot replace each other's newly minted key.
"""
from __future__ import annotations

from pathlib import Path

KEY_NAME = "my_things.encryption.keys"


def resolve(root: Path):
    from core import credential_store
    from core.blackbox.coverage.cas_keys import CasKeyring

    try:
        with credential_store.strict_reads():
            stored = credential_store.get_credential(KEY_NAME)
            if stored:
                return CasKeyring.from_json(stored)
            # Missing and unreadable are not a licence to replace a live data key.
            if credential_store.credential_is_indexed(KEY_NAME) or any(path.is_file() for path in (root / "objects").rglob("*")):
                return None
            minted = CasKeyring.mint()
            credential_store.store_credential(KEY_NAME, minted.to_json(), label="My things encryption")
            if credential_store.get_credential(KEY_NAME) != minted.to_json():
                return None
            return minted
    except Exception:
        return None
