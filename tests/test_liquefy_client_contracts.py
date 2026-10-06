"""Liquefy CLI adapter response-path contracts (F15 repair).

Confirmed at e821457d in core/liquefy_client.py:
- ``_run_json`` had no timeout — a hung external binary would hang the caller
- the wrapper's ``ok`` accepted exit 0 even when the parsed payload carried
  ``ok: false`` — the typed results inherited the lie through ``payload["ok"]``
- stdout was parsed with no size bound
- ``available`` reported ANY binary, which callers could mistake for
  operation-specific readiness (pack/restore/search each check their own bin)
These tests use only safe, locally-written fake executables.
"""
from __future__ import annotations

import json
import os
import stat
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from core.liquefy_client import LiquefyClientV1


def _fake_bin(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _client(pack_body: str, *, extra_env: dict | None = None, **kwargs) -> tuple[LiquefyClientV1, Path]:
    tmp = Path(tempfile.mkdtemp(prefix="lq-client-"))
    pack_bin = tmp / "liquefy-pack"
    _fake_bin(pack_bin, pack_body)
    env = {"PATH": os.environ.get("PATH", ""), "VOOL_LIQUEFY_PACK_BIN": str(pack_bin)}
    env.update(extra_env or {})
    return LiquefyClientV1(env=env, **kwargs), tmp


class ClientContractTests(unittest.TestCase):
    def test_exit_zero_with_ok_false_is_not_success(self) -> None:
        client, tmp = _client(
            """\
            #!/usr/bin/env python3
            import json
            print(json.dumps({"ok": False, "error": "engine reported failure"}))
            """
        )
        self.addCleanup(lambda: _rmtree(tmp))
        proof = client.pack_run_bundle(tmp / "in", tmp / "out", "vool", {})
        self.assertFalse(proof.ok)
        self.assertEqual(proof.exit_code, 0)
        self.assertIn("failure", json.dumps(proof.payload) + proof.error)

    def test_exit_zero_with_ok_true_is_success(self) -> None:
        client, tmp = _client(
            """\
            #!/usr/bin/env python3
            import json
            print(json.dumps({"ok": True, "result": {"packed": 1}}))
            """
        )
        self.addCleanup(lambda: _rmtree(tmp))
        proof = client.pack_run_bundle(tmp / "in", tmp / "out", "vool", {})
        self.assertTrue(proof.ok)

    def test_hanging_binary_times_out(self) -> None:
        client, tmp = _client(
            """\
            #!/usr/bin/env python3
            import time
            time.sleep(30)
            """,
            default_timeout_seconds=0.5,
        )
        self.addCleanup(lambda: _rmtree(tmp))
        started = time.monotonic()
        proof = client.pack_run_bundle(tmp / "in", tmp / "out", "vool", {})
        elapsed = time.monotonic() - started
        self.assertFalse(proof.ok)
        self.assertLess(elapsed, 10.0, "a hanging binary must not hang the caller")
        self.assertIn("timed out", proof.error.lower())

    def test_excessive_output_is_refused(self) -> None:
        client, tmp = _client(
            """\
            #!/usr/bin/env python3
            print('{"ok": true, "blob": "' + 'x' * (3 * 1024 * 1024) + '"}')
            """,
            max_output_bytes=2 * 1024 * 1024,
        )
        self.addCleanup(lambda: _rmtree(tmp))
        proof = client.pack_run_bundle(tmp / "in", tmp / "out", "vool", {})
        self.assertFalse(proof.ok)
        self.assertIn("exceeded", proof.error.lower())

    def test_missing_binary_for_this_operation_is_unavailable_not_crash(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="lq-client-"))
        self.addCleanup(lambda: _rmtree(tmp))
        restore_bin = tmp / "liquefy-restore"
        _fake_bin(restore_bin, """\
            #!/usr/bin/env python3
            import json
            print(json.dumps({"ok": True}))
            """
        )
        client = LiquefyClientV1(env={
            "PATH": os.environ.get("PATH", ""),
            "VOOL_LIQUEFY_RESTORE_BIN": str(restore_bin),
        })
        self.assertTrue(client.available)  # legacy: any bin
        self.assertFalse(client.pack_available)
        self.assertTrue(client.restore_available)
        self.assertFalse(client.search_available)
        proof = client.pack_run_bundle(tmp / "in", tmp / "out", "vool", {})
        self.assertFalse(proof.ok)
        self.assertEqual(proof.exit_code, 127)

    def test_search_exit_one_with_ok_true_is_accepted(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="lq-client-"))
        self.addCleanup(lambda: _rmtree(tmp))
        search_bin = tmp / "liquefy-search"
        _fake_bin(search_bin, """\
            #!/usr/bin/env python3
            import json, sys
            print(json.dumps({"ok": True, "match_count": 0, "matches": []}))
            sys.exit(1)
            """
        )
        client = LiquefyClientV1(env={
            "PATH": os.environ.get("PATH", ""),
            "VOOL_LIQUEFY_SEARCH_BIN": str(search_bin),
        })
        result = client.search_bundle(tmp / "bundle", "needle", 5)
        self.assertTrue(result.ok)
        self.assertEqual(result.exit_code, 1)

    def test_search_exit_one_with_ok_false_is_failure(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="lq-client-"))
        self.addCleanup(lambda: _rmtree(tmp))
        search_bin = tmp / "liquefy-search"
        _fake_bin(search_bin, """\
            #!/usr/bin/env python3
            import json, sys
            print(json.dumps({"ok": False, "error": "bundle unreadable"}))
            sys.exit(1)
            """
        )
        client = LiquefyClientV1(env={
            "PATH": os.environ.get("PATH", ""),
            "VOOL_LIQUEFY_SEARCH_BIN": str(search_bin),
        })
        result = client.search_bundle(tmp / "bundle", "needle", 5)
        self.assertFalse(result.ok)

    def test_self_test_surfaces_tool_reported_failure(self) -> None:
        client, tmp = _client(
            """\
            #!/usr/bin/env python3
            import json
            print(json.dumps({"ok": False, "error": "engine degraded"}))
            """
        )
        self.addCleanup(lambda: _rmtree(tmp))
        result = client.self_test()
        self.assertFalse(result.ok)


def _rmtree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
