"""Inert, licensed store snapshots shipped with the application, never auto-installed."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path

from core.eyebrow_client import AddonError


@lru_cache(maxsize=1)
def _files() -> dict:
    try:
        bundle = json.loads(Path(__file__).with_suffix('.json').read_text(encoding='utf-8'))
        if bundle['schema'] != 1 or not isinstance(bundle['files'], dict):
            raise ValueError()
        return bundle['files']
    except (OSError, ValueError, KeyError):
        raise AddonError('addon_bundle_missing', 'The built-in add-on files are missing or damaged. '
                         'Repair or update VOOL, then try again.') from None


def source(digest: str) -> bytes:
    text = _files().get(digest)
    if not isinstance(text, str) or hashlib.sha256(text.encode('utf-8')).hexdigest() != digest:
        raise AddonError('addon_source_changed', 'The built-in add-on file failed its integrity check. '
                         'Repair or update VOOL. Nothing was sent to Eyebrow or installed.')
    return text.encode('utf-8')
