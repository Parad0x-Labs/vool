"""The PEFT lane's local-checkpoint loading paths run the SAME sharded-index confinement
gate as the staged-base lane, BEFORE any ML dependency is imported or any weight is read.

This is CONTAINMENT for the GHSA-4j2p-28q2-5m79 traversal class, not a dependency repair:
no fixed accelerate release exists (the advisory carries no first-patched version, both
upstream fix PRs were closed unmerged, and the 1.15.0 wheel still joins weight_map entries
to the checkpoint folder unchecked). VOOL never reaches accelerate's advisory entrypoints
(no ``device_map`` anywhere), but a local sharded checkpoint serves its crafted index to
whatever loader resolves it — so every local directory this adapter loads (the base model
AND the LoRA adapter) is proven confined first, by the staged-base lane's own gate.

Synthetic fixtures only: every index/shard here is fabricated bytes, no model download.
"""
from __future__ import annotations

import importlib.machinery
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from adapters.peft_lora_adapter import PeftLoRAAdapter
from storage.model_provider_manifest import ModelProviderManifest


class _FakeTensor:
    def __init__(self, length: int) -> None:
        self.shape = (1, length)

    def to(self, _device):
        return self


class _FakeTokenizer:
    pad_token = None
    eos_token = "eos"
    unk_token = None
    pad_token_id = 0
    eos_token_id = 1

    def __call__(self, prompt, **_kwargs):
        return {"input_ids": _FakeTensor(3)}

    def decode(self, _ids, skip_special_tokens=True):
        return "synthetic answer"


class _FakeIdsRow:
    def __init__(self, values: list) -> None:
        self._values = values

    def tolist(self):
        return list(self._values)

    def __getitem__(self, key):
        return self._values[key]


class _FakeIds:
    def __init__(self, values: list) -> None:
        self._values = values

    def __getitem__(self, key):
        if isinstance(key, int):
            return _FakeIdsRow(self._values)
        return _FakeIds(self._values[key])


class _FakeModel:
    def __init__(self, calls: list) -> None:
        self._calls = calls

    def to(self, _device):
        return self

    def eval(self):
        return self

    def generate(self, **_kwargs):
        self._calls.append("generate")
        return _FakeIds([1, 2, 3, 4, 5])


def _install_fake_ml_stack() -> dict[str, types.ModuleType | None]:
    """Fakes for torch/transformers/peft that RECORD every load they would perform.

    Returned mapping is the caller's restore set for sys.modules."""
    calls: list = []

    class _AutoModel:
        @classmethod
        def from_pretrained(cls, path, **_kwargs):
            calls.append(f"load:{path}")
            return _FakeModel(calls)

    class _AutoTokenizer:
        @classmethod
        def from_pretrained(cls, path, **_kwargs):
            calls.append(f"load:{path}")
            return _FakeTokenizer()

    class _PeftModel:
        @classmethod
        def from_pretrained(cls, model, path, **_kwargs):
            calls.append(f"load:{path}")
            return model

    torch_mod = types.ModuleType("torch")
    torch_mod.no_grad = lambda: mock.MagicMock()
    torch_mod.cuda = types.SimpleNamespace(is_available=lambda: False)
    torch_mod.backends = types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: False))

    transformers_mod = types.ModuleType("transformers")
    transformers_mod.AutoModelForCausalLM = _AutoModel
    transformers_mod.AutoTokenizer = _AutoTokenizer

    peft_mod = types.ModuleType("peft")
    peft_mod.PeftModel = _PeftModel

    installed: dict[str, types.ModuleType] = {
        "torch": torch_mod,
        "transformers": transformers_mod,
        "peft": peft_mod,
    }
    for name, module in installed.items():
        module.__spec__ = importlib.machinery.ModuleSpec(name, None)  # find_spec() compatibility
    saved = {name: sys.modules.get(name) for name in installed}
    sys.modules.update(installed)
    _install_fake_ml_stack.recorded_calls = calls
    return saved


def _recorded_calls() -> list:
    return _install_fake_ml_stack.recorded_calls


def _manifest(base: str, adapter: str) -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name="peft-lora-confinement-test",
        model_name="TinyLoRA",
        source_type="local_path",
        adapter_type="peft_lora_adapter",
        license_name="Test",
        license_reference="https://example.test/license",
        runtime_config={"base_model_ref": base, "adapter_path": adapter, "max_new_tokens": 4},
    )


def _stage_sharded_dir(root: Path, *, index_name: str, weight_map: dict) -> Path:
    """A model dir with ONE index of the given weight_map; bare-named shards get synthetic
    bytes, every other spelling is left exactly as written (the gate judges the index)."""
    model_dir = root / "model"
    model_dir.mkdir(parents=True)
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    (model_dir / index_name).write_text(json.dumps({"weight_map": weight_map}), encoding="utf-8")
    for shard in weight_map.values():
        if isinstance(shard, str) and shard and Path(shard).name == shard:
            (model_dir / shard).write_bytes(b"synthetic-weights")
    return model_dir


class PeftLoRAAdapterConfinementTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._saved_modules: dict[str, types.ModuleType | None] | None = None

    def tearDown(self) -> None:
        self._restore_modules()
        self._tmp.cleanup()

    def _restore_modules(self) -> None:
        if self._saved_modules is None:
            return
        for name, module in self._saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        self._saved_modules = None

    def _adapter_with_fake_stack(self, base: str, adapter: str) -> PeftLoRAAdapter:
        self._saved_modules = _install_fake_ml_stack()
        return PeftLoRAAdapter(_manifest(base, adapter))

    def _invoke(self, adapter: PeftLoRAAdapter):
        with mock.patch("adapters.peft_lora_adapter.seal_provider_invocation") as seal:
            seal.return_value.consume.return_value = {
                "prompt": "synthetic",
                "max_new_tokens": 4,
                "temperature": 0.5,
            }
            return seal, adapter.invoke(mock.MagicMock(task_kind="generate", prompt="hi"))

    def test_a_confined_local_base_and_adapter_load_through_the_gate(self) -> None:
        base = _stage_sharded_dir(
            self.root / "base",
            index_name="model.safetensors.index.json",
            weight_map={"a": "model-00001.safetensors", "b": "model-00002.safetensors"},
        )
        adapter_dir = self.root / "adapter"
        adapter_dir.mkdir()
        (adapter_dir / "adapter_model.safetensors").write_bytes(b"lora")
        peft_adapter = self._adapter_with_fake_stack(str(base), str(adapter_dir))
        seal, response = self._invoke(peft_adapter)
        self.assertEqual(response.output_text, "synthetic answer")
        seal.assert_called_once()
        flat = _recorded_calls()
        self.assertIn(f"load:{base}", flat)
        self.assertIn(f"load:{adapter_dir}", flat)
        self.assertIn("generate", flat)

    def test_a_traversal_sharded_index_is_refused_before_any_loader_runs(self) -> None:
        base = _stage_sharded_dir(
            self.root / "base",
            index_name="model.safetensors.index.json",
            weight_map={"a": "../outside.safetensors"},
        )
        adapter_dir = self.root / "adapter"
        adapter_dir.mkdir()
        peft_adapter = self._adapter_with_fake_stack(str(base), str(adapter_dir))
        with mock.patch("adapters.peft_lora_adapter.seal_provider_invocation") as seal:
            with self.assertRaises(RuntimeError) as raised:
                peft_adapter.invoke(mock.MagicMock(task_kind="generate", prompt="hi"))
        self.assertIn("base_model_ref refused", str(raised.exception))
        self.assertIn("outside the model dir", str(raised.exception))
        self.assertEqual(_recorded_calls(), [], "no loader may run on a refused fixture")
        seal.assert_not_called()

    def test_every_escaping_shard_spelling_is_refused(self) -> None:
        adapter_dir = self.root / "adapter"
        adapter_dir.mkdir()
        for label, shard in {
            "absolute": "/etc/passwd",
            "backslash": "..\\win.safetensors",
            "subdir": "sub/shard.safetensors",
            "empty": "",
            "non-string": 7,
        }.items():
            with self.subTest(shard=label):
                base = _stage_sharded_dir(
                    self.root / f"base-{label}",
                    index_name="model.safetensors.index.json",
                    weight_map={"a": shard},
                )
                peft_adapter = self._adapter_with_fake_stack(str(base), str(adapter_dir))
                with self.assertRaises(RuntimeError) as raised:
                    peft_adapter.invoke(mock.MagicMock(task_kind="generate", prompt="hi"))
                # A bare-name refusal names a non-string shard; every spelling that escapes
                # the directory is refused as "outside the model dir".
                self.assertRegex(str(raised.exception), "outside the model dir|non-string shard")
                self.assertEqual(_recorded_calls(), [])
                self._restore_modules()

    def test_special_file_shards_are_refused_before_any_load(self) -> None:
        adapter_dir = self.root / "adapter"
        adapter_dir.mkdir()

        def symlink_outside(model_dir: Path, shard: str) -> None:
            outside = model_dir.parent / "outside.safetensors"
            outside.write_bytes(b"secret")
            (model_dir / shard).symlink_to(outside)

        def fifo(model_dir: Path, shard: str) -> None:
            os.mkfifo(model_dir / shard)

        def hardlink_outside(model_dir: Path, shard: str) -> None:
            outside = model_dir.parent / "outside.bin"
            outside.write_bytes(b"secret")
            os.link(outside, model_dir / shard)

        for label, writer in {
            "symlink": symlink_outside,
            "fifo": fifo,
            "hardlink": hardlink_outside,
        }.items():
            with self.subTest(shard=label), tempfile.TemporaryDirectory() as tmpdir:
                base = _stage_sharded_dir(
                    Path(tmpdir) / "base",
                    index_name="model.safetensors.index.json",
                    weight_map={"a": "shard.safetensors"},
                )
                (base / "shard.safetensors").unlink()  # the writer plants its own kind of shard
                writer(base, "shard.safetensors")
                peft_adapter = self._adapter_with_fake_stack(str(base), str(adapter_dir))
                with self.assertRaises(RuntimeError) as raised:
                    peft_adapter.invoke(mock.MagicMock(task_kind="generate", prompt="hi"))
                self.assertIn("base_model_ref refused", str(raised.exception))
                self.assertEqual(_recorded_calls(), [])
                self._restore_modules()

    def test_an_unconfined_adapter_dir_is_refused_too(self) -> None:
        base = self.root / "base"
        base.mkdir()
        (base / "config.json").write_text("{}", encoding="utf-8")
        (base / "model.safetensors").write_bytes(b"w")
        adapter_dir = self.root / "adapter"
        adapter_dir.mkdir()
        (adapter_dir / "adapter_model.safetensors.index.json").write_text(
            json.dumps({"weight_map": {"lora.a": "../../elsewhere.safetensors"}}), encoding="utf-8"
        )
        peft_adapter = self._adapter_with_fake_stack(str(base), str(adapter_dir))
        with self.assertRaises(RuntimeError) as raised:
            peft_adapter.invoke(mock.MagicMock(task_kind="generate", prompt="hi"))
        self.assertIn("adapter_path refused", str(raised.exception))
        self.assertEqual(_recorded_calls(), [])

    def test_hub_repo_refs_are_outside_the_local_gate(self) -> None:
        adapter_dir = self.root / "adapter"
        adapter_dir.mkdir()
        peft_adapter = self._adapter_with_fake_stack(
            "Qwen/Qwen2.5-0.5B-Instruct", str(adapter_dir)
        )
        _seal, response = self._invoke(peft_adapter)
        self.assertEqual(response.output_text, "synthetic answer")
        self.assertIn("load:Qwen/Qwen2.5-0.5B-Instruct", _recorded_calls())

    def test_validate_runtime_names_a_refused_checkpoint(self) -> None:
        base = self.root / "base"
        base.mkdir()
        (base / "config.json").write_text("{}", encoding="utf-8")
        (base / "model.safetensors.index.json").write_text(
            json.dumps({"weight_map": {"a": "../outside.safetensors"}}), encoding="utf-8"
        )
        adapter_dir = self.root / "adapter"
        adapter_dir.mkdir()
        warnings = PeftLoRAAdapter(_manifest(str(base), str(adapter_dir))).validate_runtime()
        self.assertTrue(
            any("base_model_ref refused" in w and "outside the model dir" in w for w in warnings),
            warnings,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
