from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from tests._credential_intelligence_support import isolated_home, vault_home
from tests.test_x_api_reads import _connect


def _results(posts):
    from core.runtime_execution_tools import execute_runtime_tool

    return execute_runtime_tool('x.results', {'posts': posts})


@pytest.fixture
def social_x(vault_home, monkeypatch):
    from core import x_api

    state = {'requests': [], 'status': 200, 'deleted': set(), 'foreign': set(), 'reply_pages': [], 'metrics': {'like_count': 7, 'bookmark_count': 2}}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            state['requests'].append(self.path)
            split = urlsplit(self.path)
            query = parse_qs(split.query)
            code = state['status']
            if code != 200:
                payload = {'title': 'rate limited'}
            elif split.path == '/2/tweets':
                pid = query['ids'][0]
                if pid in state['deleted']:
                    code = 404
                    payload = {'title': 'Not Found Error'}
                else:
                    payload = {'data': [{'id': pid, 'author_id': '22' if pid in state['foreign'] else '1234567890',
                                        'created_at': datetime.fromtimestamp(time.time()-86400, timezone.utc).isoformat(),
                                        'text': 'This platform text must never be persisted.',
                                        'public_metrics': state['metrics']}]}
            elif split.path == '/2/tweets/search/recent':
                payload = state['reply_pages'].pop(0) if state['reply_pages'] else {'data': [], 'meta': {}}
            else:
                payload = {'data': []}
            raw = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('x-rate-limit-reset', '2000000000')
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    monkeypatch.setattr(x_api, 'API_ORIGIN', f'http://127.0.0.1:{server.server_port}')
    _connect()
    try:
        yield state
    finally:
        server.shutdown(); server.server_close(); thread.join(3)


def test_own_observations_missing_metrics_and_deleted_cleanup(social_x):
    from core import x_results
    from core.runtime_paths import data_path

    out = _results(['556'])
    assert out.ok
    post = out.details['posts'][0]
    assert post['snapshots'][0]['metrics']['likes'] == 7
    assert post['snapshots'][0]['metrics']['bookmarks'] == 2
    assert post['snapshots'][0]['metrics']['views'] is None
    assert post['availability']['views'] == 'not available'
    assert post['snapshots'][0]['age_seconds'] == 86400
    stored = data_path('x_results.json').read_text()
    assert 'platform text' not in stored and 'text' not in stored
    social_x['deleted'].add('556')
    assert _results(['556']).details['deleted'] == ['556']
    assert '556' not in data_path('x_results.json').read_text()


def test_foreign_posts_and_limit_fail_closed(social_x):
    from core import x_results, x_usage
    from core.runtime_paths import data_path

    social_x['foreign'].add('555')
    out = _results(['555'])
    assert not out.ok and out.status == 'not_yours'
    assert not data_path('x_results.json').exists()
    x_usage.set_limits(monthly_reads=0)
    before = len(social_x['requests'])
    out = _results(['556'])
    assert not out.ok and out.status == 'usage_limit_reached'
    assert len(social_x['requests']) == before


def test_rate_limit_returns_reset_and_stops_without_retry(social_x):
    from core import x_results

    social_x['status'] = 429
    out = _results(['556', '557'])
    assert not out.ok and out.status == 'rate_limited'
    assert len(social_x['requests']) == 1
    assert out.details['reset_at'] == 2000000000


def test_equal_ages_compared_and_unequal_ages_never_called_trends(social_x):
    from core import x_results

    out = _results(['556', '557'])
    assert out.ok and len(out.details['comparisons']) == 1
    left = {'post_id': '1', 'snapshots': [{'age_seconds': 86400, 'metrics': {'likes': 1}}]}
    right = {'post_id': '2', 'snapshots': [{'age_seconds': 86300, 'metrics': {'likes': 2}}]}
    assert x_results._comparable([left, right]) == []


def test_not_connected_sends_nothing(vault_home):
    from core import x_results

    out = _results(['556'])
    assert not out.ok and out.status == 'disabled'
    assert 'X is not connected' in out.response_text
    from core import x_usage

    assert x_usage.snapshot()['requests'] == 0
