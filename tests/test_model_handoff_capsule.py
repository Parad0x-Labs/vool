from __future__ import annotations

import pytest

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core.model_handoff_capsule import (
    CapsuleItem,
    build_local_return_handoff,
    build_model_handoff_capsule,
    select_return_local_lane,
    validate_paid_result,
)
from core.runtime_paths import configure_runtime_home
from storage.model_provider_manifest import ModelProviderManifest


@pytest.fixture(autouse=True)
def _reset_runtime_home():
    yield
    configure_runtime_home(None)


def _build(tmp_path, items: tuple[CapsuleItem, ...]):
    configure_runtime_home(tmp_path / "runtime")
    return build_model_handoff_capsule(
        task_id="task-1",
        turn_id="turn-1",
        subtask_id="subtask-diagnosis",
        user_goal="Repair the failing parser",
        blocked_subtask="Diagnose one parser ambiguity",
        rules=("Do not edit files",),
        plan_state=("Local reproduction complete",),
        expected_output_schema={"required": ["diagnosis", "proposed_files"]},
        forbidden_operations=("network writes",),
        verification_criteria=("local tests reproduce the fix",),
        items=items,
        approved_workspace_roots=(str(tmp_path),),
        num_ctx=8192,
    )


def test_capsule_is_minimal_inspectable_and_result_bound(tmp_path) -> None:
    source = tmp_path / "parser.py"
    capsule = _build(
        tmp_path,
        (
            CapsuleItem(
                item_id="excerpt-1",
                kind="source_excerpt",
                content="def parse(value): return value",
                provenance="parser.py:1 selected by blocked-subtask retrieval",
                source_path=str(source),
            ),
        ),
    )
    summary = capsule.payload_summary()
    assert summary["item_count"] == 1
    assert summary["paths"] == [str(source.resolve())]
    assert summary["num_ctx"] == 8192

    valid, errors = validate_paid_result(
        {
            "task_id": "task-1",
            "turn_id": "turn-1",
            "subtask_id": "subtask-diagnosis",
            "diagnosis": "The parser needs an explicit branch.",
            "proposed_files": [str(source)],
        },
        capsule=capsule,
        allowed_files=(str(source),),
    )
    assert valid is True
    assert errors == ()


@pytest.mark.parametrize(
    ("path_name", "content"),
    [
        (".env", "SAFE=value"),
        ("phantom-wallet.txt", "public metadata"),
        ("safe.txt", "api_key=test-only-secret-value"),
        ("safe.txt", "private_key=test-only-private-key-value"),
    ],
)
def test_capsule_rejects_secrets_wallet_material_and_excluded_files(tmp_path, path_name: str, content: str) -> None:
    with pytest.raises(PermissionError):
        _build(
            tmp_path,
            (
                CapsuleItem(
                    item_id="blocked",
                    kind="source_excerpt",
                    content=content,
                    provenance="security test",
                    source_path=str(tmp_path / path_name),
                ),
            ),
        )


def test_paid_result_with_stale_identity_or_out_of_scope_file_is_rejected(tmp_path) -> None:
    source = tmp_path / "parser.py"
    capsule = _build(
        tmp_path,
        (CapsuleItem("excerpt", "source_excerpt", "safe", "parser.py:1", str(source)),),
    )
    valid, errors = validate_paid_result(
        {
            "task_id": "newer-task",
            "turn_id": "turn-1",
            "subtask_id": "subtask-diagnosis",
            "diagnosis": "stale",
            "proposed_files": [str(tmp_path / "other.py")],
        },
        capsule=capsule,
        allowed_files=(str(source),),
    )
    assert valid is False
    assert "task_id_mismatch" in errors
    assert any(error.startswith("file_out_of_scope:") for error in errors)


def test_num_ctx_from_capsule_is_applied_but_never_exceeds_manifest_limit() -> None:
    manifest = ModelProviderManifest(
        provider_name="ollama-local",
        model_name="qwen2.5:7b",
        source_type="http",
        adapter_type="openai_compatible",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen2.5",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["summarize"],
        runtime_config={"base_url": "http://127.0.0.1:11434", "context_window": 8192},
        metadata={"deployment_class": "local", "runtime_family": "ollama"},
    )
    adapter = OpenAICompatibleAdapter(manifest)
    requested = adapter._build_ollama_payload(
        ModelRequest(task_kind="summary", prompt="x", metadata={"num_ctx": 6144}),
        force_json=False,
        stream=False,
    )
    clamped = adapter._build_ollama_payload(
        ModelRequest(task_kind="summary", prompt="x", metadata={"num_ctx": 32768}),
        force_json=False,
        stream=False,
    )
    assert requested["options"]["num_ctx"] == 6144
    assert clamped["options"]["num_ctx"] == 8192


def test_return_to_local_is_deterministic_and_drops_paid_provider_state() -> None:
    assert select_return_local_lane(remaining_work="edits", context_tokens=4000, heavy_available=True) == "LOCAL_DAILY"
    assert select_return_local_lane(remaining_work="summary", context_tokens=4000, heavy_available=True) == "LOCAL_FAST"
    handoff = build_local_return_handoff(
        decision="Use an explicit parser branch",
        proposed_changes=("Update parser.py",),
        constraints=("No unrelated refactor",),
        verification_steps=("Run parser tests",),
        unresolved_concerns=(),
    )
    assert handoff["paid_provider_active"] is False
