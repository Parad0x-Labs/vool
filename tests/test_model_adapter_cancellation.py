from __future__ import annotations

import sys
import time
from types import SimpleNamespace
from unittest import mock

import pytest

from adapters.base_adapter import ModelRequest, ModelResponse
from adapters.local_subprocess_adapter import LocalSubprocessAdapter
from core.memory_first_router import MemoryFirstRouter
from storage.model_provider_manifest import ModelProviderManifest


def test_local_subprocess_cancellation_terminates_running_process_tree() -> None:
    manifest = ModelProviderManifest(
        provider_name="cancel-test",
        model_name="local-test",
        source_type="subprocess",
        adapter_type="local_subprocess",
        license_name="test",
        license_reference="test",
        weight_location="external",
        runtime_dependency="python",
        capabilities=["summarize"],
        runtime_config={
            "command": [
                sys.executable,
                "-c",
                "import json,sys,time; json.load(sys.stdin); time.sleep(10)",
            ],
            "timeout_seconds": 20,
        },
        metadata={"deployment_class": "local"},
    )
    started = time.monotonic()
    request = ModelRequest(
        task_kind="summarization",
        prompt="cancel this call",
        cancel_check=lambda: time.monotonic() - started > 0.2,
    )

    started_processes = []
    real_popen = __import__("subprocess").Popen

    def _capture_process(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        started_processes.append(process)
        return process

    with mock.patch("adapters.local_subprocess_adapter.subprocess.Popen", side_effect=_capture_process):
        with pytest.raises(RuntimeError, match="model_call_cancelled"):
            LocalSubprocessAdapter(manifest).invoke(request)

    assert started_processes and started_processes[0].poll() is not None
    # Windows uses taskkill /T /F with a five-second timeout, followed by a bounded wait.
    # A three-second wall-clock assertion was stricter than the implementation and failed only
    # when the four-worker gate saturated the host. Keep the bound below the call timeout while
    # proving the real child is gone before invoke returns.
    assert time.monotonic() - started < 8.0


def test_verifier_required_call_is_buffered_before_publication() -> None:
    from core.final_answer_authorship import AuthorCertification
    manifest = ModelProviderManifest(
        provider_name="ollama-local",
        model_name="qwen2.5:7b",
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen2.5",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["summarize"],
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={"deployment_class": "local"},
    )
    adapter = mock.Mock()
    adapter.health_check.return_value = {"ok": True}
    adapter.supports_streaming.return_value = True
    adapter.run_text_task.return_value = ModelResponse(output_text="buffered answer")
    adapter.stream_text_task.side_effect = AssertionError("unverified output must not stream")
    request = ModelRequest(
        task_kind="normalization_assist",
        prompt="review this",
        metadata={"defer_stream_until_verified": True},
    )
    router = MemoryFirstRouter()

    with mock.patch.object(router.registry, "build_adapter", return_value=adapter), mock.patch(
        "core.final_answer_authorship.author_certification",
        return_value=AuthorCertification(True, "passed", "measured_tool_certification", manifest.provider_id),
    ):
        _, response, error = router._invoke_manifest(
            manifest=manifest,
            request=request,
            output_mode="plain_text",
            task=SimpleNamespace(task_id="task-inline-verifier"),
            source_context={"stream_response": True},
        )

    assert error is None
    assert response is not None
    assert response.output_text == "buffered answer"
    adapter.run_text_task.assert_called_once()
    adapter.stream_text_task.assert_not_called()
