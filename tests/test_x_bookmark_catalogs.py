"""Bookmark setup and approval copy exists in every shipped locale."""
from __future__ import annotations

import json
from pathlib import Path


def test_bookmark_setup_and_approval_copy_is_complete_and_current():
    from core.i18n.catalog import source_catalog_sha256

    root = Path(__file__).resolve().parents[1] / 'core/i18n/catalogs'
    expected = {'xbook.' + key for key in ('title', 'help', 'client_id', 'sign_in', 'disconnect',
                'continue', 'connected', 'off', 'failed', 'read', 'add', 'remove', 'first_page', 'next_page')}
    catalogs = list(root.glob('*.json'))
    assert len(catalogs) == 17
    for path in catalogs:
        data = json.loads(path.read_text())
        assert expected <= data['messages'].keys(), path.name
        assert '{redirect}' in data['messages']['xbook.help'], path.name
        assert '{count}' in data['messages']['xbook.read'], path.name
        assert '{post}' in data['messages']['xbook.add'], path.name
        assert '{post}' in data['messages']['xbook.remove'], path.name
        if path.stem != 'en':
            assert data['source_catalog_sha256'] == source_catalog_sha256(), path.name
