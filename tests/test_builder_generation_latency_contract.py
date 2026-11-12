"""First-dispatch choices must use actual capabilities, not names or rejected probes."""
from types import SimpleNamespace

from core.agent_runtime.builder import controller
from storage.model_provider_manifest import ModelProviderManifest


def test_equal_basic_coding_manifests_do_not_select_by_lexical_model_name():
    # Captured registry shape: both models are general and declare only basic coding.
    def manifest(name):
        return ModelProviderManifest(
            provider_name='ollama-local', model_name=name, source_type='http',
            license_name='Apache-2.0', license_reference='https://example.org/license',
            runtime_dependency='ollama', runtime_config={'base_url':'http://localhost:11434'},
            capabilities=['summarize','classify','format','extract','structured_json','code_basic','tool_intent'],
            metadata={'bundle_role':'general','orchestration_role':'drone'},
        )
    smaller, larger = manifest('qwen3:4b'), manifest('qwen2.5:7b')
    agent = SimpleNamespace(memory_router=SimpleNamespace(registry=SimpleNamespace(
        list_manifests=lambda **kw: [smaller, larger])))
    assert controller.select_auto_builder_manifest(agent).model_name == larger.model_name
    # Same law on unrelated names, not a Qwen special case.
    smaller.model_name, larger.model_name = 'zeta:3b', 'alpha:9b'
    assert controller.select_auto_builder_manifest(agent).model_name == larger.model_name
