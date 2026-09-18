from __future__ import annotations

import json
import os
import stat
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from core.liquefy_bridge import (
    _export_task_bundle_sync,
    apply_local_execution_safety,
    load_packed_bytes,
    pack_bytes_artifact,
)
from core.liquefy_client import LiquefyClientV1
from core.liquefy_models import ProofBundleV1
from storage.db import get_connection


def _write_fake_liquefy_script(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class LiquefyPhase1Tests(unittest.TestCase):
    def test_liquefy_client_uses_cli_json_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pack_bin = root / "liquefy-pack"
            restore_bin = root / "liquefy-restore"
            search_bin = root / "liquefy-search"
            script_body = textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json
                import sys
                from pathlib import Path

                name = Path(sys.argv[0]).name
                if name == "liquefy-pack":
                    if "--self-test" in sys.argv:
                        print(json.dumps({"schema_version": "liquefy.tracevault.cli.v1", "tool": "tracevault_pack", "command": "self_test", "ok": True}))
                    else:
                        run_dir = Path(sys.argv[1])
                        out_dir = Path(sys.argv[sys.argv.index("--out") + 1])
                        out_dir.mkdir(parents=True, exist_ok=True)
                        metadata_seen = (run_dir / "run_metadata.json").exists()
                        print(json.dumps({"schema_version": "liquefy.tracevault.cli.v1", "tool": "tracevault_pack", "command": "pack", "ok": True, "result": {"metadata_seen": metadata_seen, "out_dir": str(out_dir)}}))
                elif name == "liquefy-restore":
                    print(json.dumps({"schema_version": "liquefy.tracevault.restore.cli.v1", "tool": "tracevault_restore", "command": "restore", "ok": True, "result": {"restored": True}}))
                elif name == "liquefy-search":
                    print(json.dumps({"schema_version": "liquefy.tracevault.search.cli.v1", "tool": "tracevault_search", "command": "search", "ok": True, "match_count": 1, "matches": [{"file": "task_bundle.json", "line": 1, "text": "needle"}]}))
                else:
                    raise SystemExit(2)
                """
            )
            for candidate in (pack_bin, restore_bin, search_bin):
                _write_fake_liquefy_script(candidate, script_body)

            input_dir = root / "input"
            input_dir.mkdir(parents=True, exist_ok=True)
            (input_dir / "bundle.json").write_text('{"hello": "world"}', encoding="utf-8")
            env = {
                "PATH": os.environ.get("PATH", ""),
                "VOOL_LIQUEFY_PACK_BIN": str(pack_bin),
                "VOOL_LIQUEFY_RESTORE_BIN": str(restore_bin),
                "VOOL_LIQUEFY_SEARCH_BIN": str(search_bin),
            }
            client = LiquefyClientV1(env=env)

            self_test = client.self_test()
            self.assertTrue(self_test.ok)
            self.assertEqual(self_test.schema_version, "liquefy.tracevault.cli.v1")

            proof = client.pack_run_bundle(input_dir, root / "vault", "vool", {"trace_id": "task-1"})
            self.assertTrue(proof.ok)
            self.assertTrue(proof.payload["result"]["metadata_seen"])

            restored = client.restore_bundle(root / "vault", root / "restored")
            self.assertTrue(restored.ok)
            self.assertEqual(restored.schema_version, "liquefy.tracevault.restore.cli.v1")

            searched = client.search_bundle(root / "vault", "needle", 5)
            self.assertTrue(searched.ok)
            self.assertEqual(searched.schema_version, "liquefy.tracevault.search.cli.v1")
            self.assertEqual(searched.payload["match_count"], 1)

    def test_liquefy_client_degrades_cleanly_when_cli_is_missing(self) -> None:
        client = LiquefyClientV1(env={"PATH": ""})

        self.assertFalse(client.available)
        self.assertFalse(client.self_test().ok)
        self.assertFalse(client.pack_run_bundle("/tmp/in", "/tmp/out", "vool", {}).ok)
        self.assertFalse(client.restore_bundle("/tmp/bundle", "/tmp/out").ok)
        self.assertFalse(client.search_bundle("/tmp/bundle", "needle", 5).ok)

    def test_pack_and_load_packed_bytes_roundtrip(self) -> None:
        payload = b"proof bundle bytes"
        packed = pack_bytes_artifact(artifact_id="artifact-1", payload=payload, category="tests")

        self.assertIn(packed["storage_backend"], {"liquefy", "local_archive"})
        restored = load_packed_bytes(payload=packed["compressed_payload"], storage_backend=str(packed["storage_backend"]))
        self.assertEqual(restored, payload)

    def test_apply_local_execution_safety_fails_closed_on_unserializable_payload(self) -> None:
        self.assertFalse(apply_local_execution_safety({"workspace": "/tmp"}, {"bad": object()}))

    def test_export_task_bundle_sync_uses_client_when_available(self) -> None:
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO local_tasks (
                    task_id, session_id, task_class, task_summary, redacted_input_hash,
                    environment_os, environment_shell, environment_runtime, environment_version_hint,
                    plan_mode, share_scope, confidence, outcome, harmful_flag, created_at, updated_at
                ) VALUES (
                    'task-proof', 'session-proof', 'research', 'Summarize proof bundle state', 'hash',
                    'macOS', 'zsh', 'python', '3.9',
                    'default', 'local_only', 0.9, 'success', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                INSERT INTO finalized_responses (
                    parent_task_id, raw_synthesized_text, rendered_persona_text, status_marker, confidence_score
                ) VALUES (
                    'task-proof', 'raw proof', 'rendered proof', 'success', 0.9
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

        captured: dict[str, object] = {}

        class FakeClient:
            available = True

            def pack_run_bundle(self, input_dir, out_dir, org, metadata):
                captured["input_dir"] = str(input_dir)
                captured["out_dir"] = str(out_dir)
                captured["metadata"] = dict(metadata)
                captured["bundle"] = json.loads((Path(input_dir) / "task_bundle.json").read_text(encoding="utf-8"))
                return ProofBundleV1(
                    ok=True,
                    schema_version="liquefy.tracevault.cli.v1",
                    tool="tracevault_pack",
                    command="pack",
                    source_dir=str(input_dir),
                    out_dir=str(out_dir),
                    metadata=dict(metadata),
                    payload={"ok": True},
                )

        with tempfile.TemporaryDirectory() as tmpdir, mock.patch("core.liquefy_bridge._VOOL_VAULT", Path(tmpdir)), mock.patch(
            "core.liquefy_bridge.get_liquefy_client",
            return_value=FakeClient(),
        ):
            _export_task_bundle_sync("task-proof")

        self.assertEqual(captured["metadata"]["trace_id"], "task-proof")
        self.assertEqual(captured["bundle"]["trace_id"], "task-proof")
        self.assertEqual(captured["bundle"]["final_response"]["rendered_persona_text"], "rendered proof")

    def test_export_task_bundle_sync_falls_back_to_local_archive(self) -> None:
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO local_tasks (
                    task_id, session_id, task_class, task_summary, redacted_input_hash,
                    environment_os, environment_shell, environment_runtime, environment_version_hint,
                    plan_mode, share_scope, confidence, outcome, harmful_flag, created_at, updated_at
                ) VALUES (
                    'task-fallback', 'session-fallback', 'research', 'Fallback proof bundle state', 'hash',
                    'macOS', 'zsh', 'python', '3.9',
                    'default', 'local_only', 0.9, 'success', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                INSERT INTO finalized_responses (
                    parent_task_id, raw_synthesized_text, rendered_persona_text, status_marker, confidence_score
                ) VALUES (
                    'task-fallback', 'raw proof', 'rendered proof', 'success', 0.9
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

        class FakeUnavailableClient:
            available = False

        with tempfile.TemporaryDirectory() as tmpdir, mock.patch("core.liquefy_bridge._VOOL_VAULT", Path(tmpdir)), mock.patch(
            "core.liquefy_bridge.get_liquefy_client",
            return_value=FakeUnavailableClient(),
        ):
            _export_task_bundle_sync("task-fallback")
            bundle_dir = Path(tmpdir) / "bundles"
            self.assertTrue(any(path.suffix in {".gz", ".zst"} for path in bundle_dir.iterdir()))


if __name__ == "__main__":
    unittest.main()
