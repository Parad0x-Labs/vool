"""Platform availability is explicit, including on hosts without OS confinement."""
import json
import sys

from core import runtime_paths
from tests.asgi_harness import asgi_request


def test_fresh_profile_reports_capabilities_without_starting_a_worker(tmp_path, monkeypatch):
    from apps.vool_api_server import create_app
    from core.web.api.runtime import RuntimeServices

    old = runtime_paths._VOOL_HOME_OVERRIDE
    monkeypatch.delenv('VOOL_DROP', raising=False)
    home = tmp_path/'fresh'
    runtime_paths.configure_runtime_home(home)
    try:
        app = create_app(RuntimeServices(display_name='VOOL'))
        status, _, raw = asgi_request(app, method='POST', path='/api/lend',
            headers={'Host':'127.0.0.1', 'Content-Type':'application/json'}, body=b'{"action":"status"}')
        data = json.loads(raw)
        assert status == 200 and data['ok']
        assert data['enabled'] is False and data['drop_enabled'] is False
        assert data['reviewed'] is False
        assert not (home/'lend').exists()
        if sys.platform != 'darwin':
            assert not any(data['capabilities'].values())
        assert data['capabilities']['summary'] is False and data['capabilities']['translation'] is False
    finally:
        runtime_paths.configure_runtime_home(old)
