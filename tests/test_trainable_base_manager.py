from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.trainable_base_manager import stage_trainable_base, trainable_base_status


class TrainableBaseManagerTests(unittest.TestCase):
    def test_stage_trainable_base_writes_metadata_and_policy_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            target_root = tmp / "models"
            config_root = tmp / "config"

            def _fake_download(*, spec, target_dir):
                del spec
                target_dir.mkdir(parents=True, exist_ok=True)
                (target_dir / "config.json").write_text("{}", encoding="utf-8")
                (target_dir / "model.safetensors").write_bytes(b"ok")
                (target_dir / "tokenizer.json").write_text("{}", encoding="utf-8")

            with mock.patch("core.trainable_base_manager._download_model_snapshot", side_effect=_fake_download), \
                 mock.patch("core.trainable_base_manager._verify_model_dir", return_value={"tokenizer_class": "FakeTokenizer", "parameter_count": 12345}), \
                 mock.patch("core.trainable_base_manager._register_staged_base_manifest"), \
                 mock.patch("core.trainable_base_manager._default_policy_path", return_value=config_root / "default_policy.yaml"):
                payload = stage_trainable_base(target_root=target_root, activate=True, verify_load=True)

            self.assertTrue(payload["ok"])
            model_dir = Path(payload["local_path"])
            self.assertTrue((model_dir / "vool_trainable_base.json").exists())
            metadata = json.loads((model_dir / "vool_trainable_base.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["model_id"], "Qwen/Qwen2.5-0.5B-Instruct")
            self.assertEqual(metadata["verification"]["parameter_count"], 12345)

            policy_file = config_root / "default_policy.yaml"
            self.assertTrue(policy_file.exists())
            policy_text = policy_file.read_text(encoding="utf-8")
            self.assertIn("Qwen2.5-0.5B-Instruct", policy_text)

    def test_trainable_base_status_reports_staged_bases(self) -> None:
        with mock.patch(
            "core.trainable_base_manager.list_staged_trainable_bases",
            return_value=[{"model_name": "Qwen2.5-0.5B-Instruct", "exists": True}],
        ):
            payload = trainable_base_status()
        self.assertIn("active_policy", payload)
        self.assertEqual(len(payload["staged_bases"]), 1)


if __name__ == "__main__":
    unittest.main()


class ShardedIndexConfinementTests(unittest.TestCase):
    """The staged-base verification gate refuses a checkpoint index whose weight_map names
    anything but a bare filename in the model dir (the accelerate-advisory traversal class,
    GHSA-4j2p-28q2-5m79, guarded at VOOL's own untrusted-download boundary)."""

    @staticmethod
    def _dir_with_index(tmp: Path, weight_map: dict) -> Path:
        model_dir = tmp / "model"
        model_dir.mkdir(parents=True)
        (model_dir / "config.json").write_text("{}", encoding="utf-8")
        (model_dir / "model.safetensors.index.json").write_text(
            json.dumps({"metadata": {"total_size": 1}, "weight_map": weight_map}), encoding="utf-8"
        )
        return model_dir

    def test_a_escaping_weight_map_is_refused_before_any_loader_import(self) -> None:
        from core.trainable_base_manager import _verify_model_dir

        with tempfile.TemporaryDirectory() as tmpdir:
            for n, bad in enumerate(("../outside.safetensors", "/etc/passwd", "..\\win.safetensors", ".", "", "sub/shard.safetensors")):
                with self.subTest(shard=bad):
                    model_dir = self._dir_with_index(Path(tmpdir) / f"case-{n}", {"model.layers.0": bad})
                    with self.assertRaises(ValueError) as raised:
                        _verify_model_dir(model_dir=model_dir, trust_remote_code=False)
                    self.assertIn("outside the model dir", str(raised.exception))

    def test_a_confined_weight_map_passes_the_gate_check(self) -> None:
        from core.trainable_base_manager import _sharded_index_confinement_error

        with tempfile.TemporaryDirectory() as tmpdir:
            model_dir = self._dir_with_index(Path(tmpdir), {"model.layers.0": "model-00001-of-00002.safetensors", "lm_head.weight": "model-00002-of-00002.safetensors"})
            self.assertEqual(_sharded_index_confinement_error(model_dir), "")
            # a directory with no index at all is simply not sharded
            (model_dir / "model.safetensors.index.json").unlink()
            self.assertEqual(_sharded_index_confinement_error(model_dir), "")
