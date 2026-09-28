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
    """The staged-base verification gate proves checkpoint-index confinement on the FILESYSTEM
    (the traversal class behind accelerate's GHSA-4j2p-28q2-5m79, guarded at VOOL's own
    untrusted-download boundary), and refuses unsafe special files BEFORE any loader runs."""

    @staticmethod
    def _stage(tmp: Path, *, weight_map, shard_writer=None, index_name="model.safetensors.index.json") -> Path:
        model_dir = tmp / "model"
        model_dir.mkdir(parents=True)
        (model_dir / "config.json").write_text("{}", encoding="utf-8")
        (model_dir / index_name).write_text(
            json.dumps({"metadata": {"total_size": 1}, "weight_map": weight_map}), encoding="utf-8"
        )
        if shard_writer:
            shard_writer(model_dir)
        return model_dir

    def _verify_through_controlled_loader(self, model_dir: Path):
        """Run the REAL _verify_model_dir with a recording fake transformers module.
        self._loader_opened records every path the controlled loader touched."""
        import sys
        import types

        opened = self._loader_opened = []

        class _FakeAuto:
            def __init__(self, tag):
                self.tag = tag

            @classmethod
            def from_pretrained(cls, path, **kwargs):
                opened.append(str(path))
                return cls("loaded")

            def parameters(self):
                return [types.SimpleNamespace(numel=lambda: 1)]

        fake = types.ModuleType("transformers")
        fake.AutoModelForCausalLM = _FakeAuto
        fake.AutoTokenizer = _FakeAuto
        real = sys.modules.get("transformers")
        sys.modules["transformers"] = fake
        try:
            from core.trainable_base_manager import _verify_model_dir

            result = _verify_model_dir(model_dir=model_dir, trust_remote_code=False)
            return result, opened
        finally:
            if real is not None:
                sys.modules["transformers"] = real
            else:
                sys.modules.pop("transformers", None)

    def test_a_genuine_staged_layout_loads_through_the_real_gate(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            def shards(model_dir):
                (model_dir / "model-00001-of-00002.safetensors").write_bytes(b"weights-1")
                (model_dir / "model-00002-of-00002.safetensors").write_bytes(b"weights-2")

            model_dir = self._stage(
                Path(tmpdir),
                weight_map={"model.layers.0": "model-00001-of-00002.safetensors", "lm_head.weight": "model-00002-of-00002.safetensors"},
                shard_writer=shards,
            )
            result, opened = self._verify_through_controlled_loader(model_dir)
            self.assertEqual(result["parameter_count"], 1)  # the recording loader ran
            self.assertEqual(len(opened), 2)  # tokenizer + model, both inside model_dir

    def test_an_escaping_or_special_file_shard_is_refused_before_any_load(self) -> None:
        import os
        import tempfile
        from pathlib import Path

        def make_symlink_outside(model_dir):
            outside = model_dir.parent / "outside.safetensors"
            outside.write_bytes(b"secret")
            (model_dir / "shard.safetensors").symlink_to(outside)

        def make_fifo(model_dir):
            os.mkfifo(model_dir / "shard.safetensors")

        def make_hardlink(model_dir):
            outside = model_dir.parent / "outside.bin"
            outside.write_bytes(b"secret")
            os.link(outside, model_dir / "shard.safetensors")

        fixtures = {
            "symlink outside": make_symlink_outside,
            "fifo": make_fifo,
            "hardlink outside": make_hardlink,
        }
        for label, writer in fixtures.items():
            with tempfile.TemporaryDirectory() as tmpdir, self.subTest(shard=label):
                model_dir = self._stage(Path(tmpdir), weight_map={"model.layers.0": "shard.safetensors"}, shard_writer=writer)
                with self.assertRaises(ValueError) as raised:
                    self._verify_through_controlled_loader(model_dir)
                self.assertIn("shard", str(raised.exception))
                self.assertEqual(self._loader_opened, [], "the loader must not run on a refused fixture")
        for label, shard in {"traversal": "../outside.safetensors", "absolute": "/etc/passwd", "windows-drive": "C:outside.bin", "backslash": "..\\win.safetensors", "subdir": "sub/shard.safetensors", "empty": ""}.items():
            with tempfile.TemporaryDirectory() as tmpdir, self.subTest(shard=label):
                model_dir = self._stage(Path(tmpdir), weight_map={"model.layers.0": shard})
                with self.assertRaises(ValueError) as raised:
                    self._verify_through_controlled_loader(model_dir)
                self.assertIn("outside the model dir", str(raised.exception))
                self.assertEqual(self._loader_opened, [], "the loader must not run on a refused fixture")

    def test_a_symlinked_or_oversized_index_is_refused(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            real_index = Path(tmpdir) / "real.index.json"
            real_index.write_text(json.dumps({"weight_map": {"a": "shard.safetensors"}}), encoding="utf-8")
            model_dir = Path(tmpdir) / "model"
            model_dir.mkdir()
            (model_dir / "config.json").write_text("{}", encoding="utf-8")
            (model_dir / "shard.safetensors").write_bytes(b"w")
            (model_dir / "model.safetensors.index.json").symlink_to(real_index)
            with self.assertRaises(ValueError) as raised:
                self._verify_through_controlled_loader(model_dir)
            self.assertIn("symbolic link", str(raised.exception))

    def test_a_dir_with_no_index_or_a_non_string_shard(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            model_dir = Path(tmpdir) / "model"
            model_dir.mkdir()
            (model_dir / "config.json").write_text("{}", encoding="utf-8")
            (model_dir / "model.safetensors").write_bytes(b"w")  # unsharded: nothing to check
            from core.trainable_base_manager import _sharded_index_confinement_error

            self.assertEqual(_sharded_index_confinement_error(model_dir), "")
            (model_dir / "model.safetensors.index.json").write_text(
                json.dumps({"weight_map": {"a": 7}}), encoding="utf-8"
            )
            self.assertIn("non-string shard", _sharded_index_confinement_error(model_dir))
