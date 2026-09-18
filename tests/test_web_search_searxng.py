from __future__ import annotations

import io
import json
import unittest
from unittest import mock

from tools.web.searxng_client import SearXNGClient


class _Response:
    def __init__(self, payload: dict) -> None:
        self._body = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self) -> bytes:
        return self._body.read()

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class SearXNGClientTests(unittest.TestCase):
    def test_searxng_parsing(self) -> None:
        payload = {"results": [{"title": "A", "url": "https://a.example", "content": "snippet", "engine": "test", "score": 1.0}]}
        with mock.patch("urllib.request.urlopen", return_value=_Response(payload)) as urlopen:
            client = SearXNGClient(base_url="http://127.0.0.1:8080", timeout_s=3)
            results = client.search("hello", max_results=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].url, "https://a.example")
        request = urlopen.call_args.args[0]
        self.assertIn("format=json", request.full_url)


if __name__ == "__main__":
    unittest.main()
