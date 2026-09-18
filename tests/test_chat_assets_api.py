import hashlib
import json

import pytest

from core.web.api.chat_assets_api import ASSETS, ROOT, handle_chat_asset_get
from core.web.api.service import dispatch_get


@pytest.mark.parametrize('name', sorted(ASSETS))
def test_shipped_asset_matches_pinned_manifest_through_get(name):
    response = dispatch_get(path='/chat-assets/'+name, query={}, runtime=None, model_name='')
    assert response.status == 200
    assert response.content_type.startswith('text/javascript')
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    manifest = json.loads((ROOT / 'manifest.json').read_text())
    expected = next(p['files'][name] for p in manifest.values() if name in p['files'])
    assert len(response.body) == expected['bytes']
    assert hashlib.sha256(response.body).hexdigest() == expected['sha256']
    assert all(p['license'] == 'MIT' for p in manifest.values())


@pytest.mark.parametrize('path', [
    '/chat-assets/../../config.json', '/chat-assets/%2e%2e/secret',
    '/chat-assets/manifest.json', '/chat-assets/chart-4.5.1.min.js/extra',
    '/chat-assets//chart-4.5.1.min.js', '/etc/passwd',
])
def test_static_door_is_an_exact_allowlist(path):
    assert handle_chat_asset_get(path).status == 404
