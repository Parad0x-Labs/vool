from __future__ import annotations

import base64
import json
from dataclasses import asdict, replace

import pytest

from core.context_manifest import build_context_manifest
from core.context_relevance_ranker import (
    rank_context_items,
    retrieval_confidence,
)
from core.prompt_assembly_report import ContextItem
from core.provenance_store import load_manifest, store_manifest

_API_KEY = "[redacted-anthropic-key]"
_AUTH_TOKEN = "AUTHORIZATION-MANIFEST-RAW-999"
_CALLBACK_CODE = "CALLBACK-CODE-MANIFEST-777"
_CALLBACK_STATE = "CALLBACK-STATE-MANIFEST-888"
_PROMPT_TEXT = "PROMPT-DUPLICATE-MANIFEST-555"
_PRIVATE_PATH = r"<user-home>\private\credential.txt"


def _unsafe_item(*, included: bool) -> dict:
    return {
        "item_id": "safe-item-1",
        "layer": "relevant",
        "source_type": "dialogue_memory",
        "title": f"{_PROMPT_TEXT} api_key={_API_KEY}",
        "path": _PRIVATE_PATH,
        "content": _PROMPT_TEXT,
        "prompt": _PROMPT_TEXT,
        "headers": {"Authorization": f"Bearer {_AUTH_TOKEN}"},
        "callback": {
            "code": _CALLBACK_CODE,
            "state": _CALLBACK_STATE,
        },
        "chars": 120,
        "tokens": 30,
        "priority": 0.9,
        "confidence": 0.8,
        "must_keep": False,
        "included": included,
        "reason": "current_chat_scope",
        "metadata": {
            "scope": "chat",
            "source_id": "chat:manifest-test",
            "content_hash": "content-hash-1",
            "status": "active",
            "title": _PROMPT_TEXT,
            "path": _PRIVATE_PATH,
            "api_key": _API_KEY,
            "authorization": _AUTH_TOKEN,
            "callback_code": _CALLBACK_CODE,
            "state": _CALLBACK_STATE,
            "raw_prompt": _PROMPT_TEXT,
        },
        "provenance": {
            "kind": "server_assembled_context",
            "source_id": "chat:manifest-test",
            "content_hash": "content-hash-1",
            "title": _PROMPT_TEXT,
            "path": _PRIVATE_PATH,
            "credential": _API_KEY,
            "authorization": _AUTH_TOKEN,
            "callback_code": _CALLBACK_CODE,
            "state": _CALLBACK_STATE,
            "prompt": _PROMPT_TEXT,
        },
    }


def _build_unsafe_manifest(monkeypatch):
    monkeypatch.setattr(
        "core.context_manifest.signer.sign",
        lambda _payload: base64.b64encode(b"x" * 64).decode("ascii"),
    )
    monkeypatch.setattr(
        "core.context_manifest.signer.verify",
        lambda _payload, _signature, _peer_id: True,
    )
    monkeypatch.setattr(
        "core.context_manifest.signer.get_local_peer_id",
        lambda: "11" * 32,
    )
    return build_context_manifest(
        task_id="task-manifest-redaction",
        trace_id="trace-manifest-redaction",
        evidence_items=[_PROMPT_TEXT, _API_KEY],
        source_metadata=[
            {
                "item_id": "safe-item-1",
                "source_id": "chat:manifest-test",
                "source_type": "dialogue_memory",
                "scope": "chat",
                "reason": "current_chat_scope",
                "content_hash": "content-hash-1",
                "title": _PROMPT_TEXT,
                "path": _PRIVATE_PATH,
                "content": _PROMPT_TEXT,
                "headers": {
                    "Authorization": f"Bearer {_AUTH_TOKEN}",
                },
                "callback_code": _CALLBACK_CODE,
                "state": _CALLBACK_STATE,
                "provenance": {
                    "source_id": "chat:manifest-test",
                    "content_hash": "content-hash-1",
                    "credential": _API_KEY,
                },
            }
        ],
        redaction_markers=[
            "context_scope_default_deny",
            f"authorization={_AUTH_TOKEN}",
        ],
        truncation_markers=[
            f"{_PROMPT_TEXT} api_key={_API_KEY}",
        ],
        chat_id="manifest-chat",
        project_id="manifest-project",
        context_scopes=["chat"],
        selected_items=[_unsafe_item(included=True)],
        excluded_items=[_unsafe_item(included=False)],
        capsule_version="none",
        input_tokens=30,
        reserved_output_tokens=90,
        provider="ollama",
        model="qwen3:8b",
        access_policy={
            "chat_id": "manifest-chat",
            "project_id": "manifest-project",
            "namespace_state": "active",
            "grant_count": 2,
            "project_context_allowed": True,
        },
        candidate_items=[_unsafe_item(included=False)],
    )


def test_manifest_structurally_excludes_sensitive_and_prompt_fields(
    monkeypatch,
) -> None:
    manifest = _build_unsafe_manifest(monkeypatch)
    store_manifest(manifest)

    loaded = load_manifest(manifest.manifest_id)
    assert loaded is not None
    persisted = json.dumps(loaded, sort_keys=True)
    for forbidden in (
        _API_KEY,
        _AUTH_TOKEN,
        _CALLBACK_CODE,
        _CALLBACK_STATE,
        _PROMPT_TEXT,
        _PRIVATE_PATH,
        "Authorization",
        "callback_code",
        "raw_prompt",
    ):
        assert forbidden not in persisted

    assert loaded["source_metadata"] == [
        {
            "content_hash": "content-hash-1",
            "item_id": "safe-item-1",
            "reason": "current_chat_scope",
            "scope": "chat",
            "source_id": "chat:manifest-test",
            "source_type": "dialogue_memory",
        }
    ]
    selected = loaded["selected_items"][0]
    assert "title" not in selected
    assert "path" not in selected
    assert "content" not in selected
    assert selected["metadata"] == {
        "content_hash": "content-hash-1",
        "scope": "chat",
        "source_id": "chat:manifest-test",
        "status": "active",
    }
    assert selected["provenance"] == {
        "content_hash": "content-hash-1",
        "kind": "server_assembled_context",
        "source_id": "chat:manifest-test",
    }
    assert "context_manifest_allowlist_v1" in loaded[
        "redaction_markers"
    ]
    assert loaded["evidence_hashes"]
    assert loaded["access_policy"] == {
        "chat_id": "manifest-chat",
        "project_id": "manifest-project",
        "namespace_state": "active",
        "grant_count": 2,
        "project_context_allowed": True,
    }
    assert loaded["candidate_items"][0]["item_id"] == "safe-item-1"
    assert "content" not in loaded["candidate_items"][0]


@pytest.mark.parametrize(
    "file_uri",
    (
        "file:" + "///C:/Users/Example/private.txt",
        "FILE:" + "//localhost/C:/Users/Example/private.txt",
        "file:" + "///var/lib/vool/private.txt",
        "file:" + "//server/share/private.txt",
        "file:" + "/C:/Users/Example/private.txt",
    ),
)
def test_manifest_hashes_file_uri_identifiers(
    monkeypatch,
    file_uri: str,
) -> None:
    monkeypatch.setattr(
        "core.context_manifest.signer.sign",
        lambda _payload: base64.b64encode(b"x" * 64).decode("ascii"),
    )
    manifest = build_context_manifest(
        task_id=file_uri,
        trace_id=file_uri,
        evidence_items=[],
        source_metadata=[
            {
                "item_id": file_uri,
                "source_id": file_uri,
                "source_type": file_uri,
                "content_hash": file_uri,
            }
        ],
        chat_id=file_uri,
        project_id=file_uri,
        selected_items=[
            {
                "item_id": file_uri,
                "layer": file_uri,
                "source_type": file_uri,
                "metadata": {
                    "source_id": file_uri,
                    "content_hash": file_uri,
                },
                "provenance": {
                    "source_id": file_uri,
                    "content_hash": file_uri,
                },
            }
        ],
        capsule_version=file_uri,
        provider=file_uri,
        model=file_uri,
    )

    serialized = json.dumps(asdict(manifest), sort_keys=True)
    assert file_uri not in serialized
    assert "_sha256:" in serialized


def test_storage_rejects_hand_constructed_noncanonical_manifest(
    monkeypatch,
) -> None:
    safe = _build_unsafe_manifest(monkeypatch)
    unsafe = replace(
        safe,
        selected_items=[
            {
                "item_id": "safe-item-1",
                "title": f"api_key={_API_KEY}",
            }
        ],
    )

    with pytest.raises(
        ValueError,
        match="non-canonical persistence data",
    ):
        store_manifest(unsafe)

    assert load_manifest(unsafe.manifest_id) is None


def test_storage_rejects_a_structurally_valid_but_unverified_signature(
    monkeypatch,
) -> None:
    manifest = _build_unsafe_manifest(monkeypatch)
    monkeypatch.setattr(
        "core.context_manifest.signer.verify",
        lambda _payload, _signature, _peer_id: False,
    )

    with pytest.raises(
        ValueError,
        match="signature verification failed",
    ):
        store_manifest(manifest)

    assert load_manifest(manifest.manifest_id) is None


def test_non_finite_context_scores_are_neutralized_before_persistence(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "core.context_manifest.signer.sign",
        lambda _payload: base64.b64encode(b"x" * 64).decode("ascii"),
    )
    monkeypatch.setattr(
        "core.context_manifest.signer.verify",
        lambda _payload, _signature, _peer_id: True,
    )
    monkeypatch.setattr(
        "core.context_manifest.signer.get_local_peer_id",
        lambda: "11" * 32,
    )
    selected = _unsafe_item(included=True)
    selected["priority"] = float("nan")
    selected["confidence"] = float("inf")

    manifest = build_context_manifest(
        task_id="task-non-finite-score",
        trace_id="trace-non-finite-score",
        evidence_items=[],
        source_metadata=[],
        selected_items=[selected],
        candidate_items=[selected],
    )
    store_manifest(manifest)

    loaded = load_manifest(manifest.manifest_id)
    assert loaded is not None
    for field in ("selected_items", "candidate_items"):
        assert loaded[field][0]["priority"] == 0.0
        assert loaded[field][0]["confidence"] == 0.0
    json.dumps(loaded, allow_nan=False)


def test_non_finite_context_scores_cannot_poison_ranking_or_records() -> None:
    item = ContextItem(
        item_id="non-finite-score",
        layer="relevant",
        source_type="runtime_memory",
        title="Relevant memory",
        content="A finite context candidate.",
        priority=float("inf"),
        confidence=float("nan"),
    )

    record = item.to_record(included=True, reason="test")
    ranked = rank_context_items(
        [item],
        query_text="relevant memory",
        topic_hints=[],
        task_class="conversation",
    )

    assert record["priority"] == 0.0
    assert record["confidence"] == 0.0
    assert ranked[0].priority > 0.0
    assert ranked[0].confidence == 0.0
    assert retrieval_confidence(ranked)[1] == ranked[0].priority


def test_generated_identifier_hash_is_stable_when_digest_looks_base58(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "core.context_manifest.signer.sign",
        lambda _payload: base64.b64encode(b"x" * 64).decode("ascii"),
    )
    monkeypatch.setattr(
        "core.context_manifest.signer.verify",
        lambda _payload, _signature, _peer_id: True,
    )
    monkeypatch.setattr(
        "core.context_manifest.signer.get_local_peer_id",
        lambda: "11" * 32,
    )
    source_id = "summary:source+144"
    digest = (
        "source_id_sha256:"
        "d341ad3f36ec8c1dcea1dff913df4aae89a8475baeee94798d124213849cae5d"
    )
    selected = _unsafe_item(included=True)
    selected["metadata"]["source_id"] = source_id
    selected["provenance"]["source_id"] = source_id

    manifest = build_context_manifest(
        task_id="task-hashed-source-id",
        trace_id="trace-hashed-source-id",
        evidence_items=[],
        source_metadata=[],
        selected_items=[selected],
    )
    store_manifest(manifest)

    loaded = load_manifest(manifest.manifest_id)
    assert loaded is not None
    assert loaded["selected_items"][0]["metadata"]["source_id"] == digest
    assert loaded["selected_items"][0]["provenance"]["source_id"] == digest
