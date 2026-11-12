"""The rejection receipt stores known codes verbatim and hashes anything else.

Review finding 6: `_code()` sanitized any string into code shape, so a future free-text warning
or a `:`-less contract error would be stored as underscore-joined words. The receipt's code
fields are allowlisted to the vocabularies today's producers emit; an unknown value is stored as
a short hash -- distinguishable, never as words.
"""
from __future__ import annotations

import json

from core.agent_runtime.reply_rejection import reply_rejection_receipt
from core.memory_first_router import ModelExecutionDecision

MARKER = "KESTREL-RECEIPT-PROBE-9931"


def _decision(**overrides) -> ModelExecutionDecision:
    fields = {"source": "provider_execution", "task_hash": "t", "used_model": True,
              "validation_state": "contract_failed", "output_text": ""}
    fields.update(overrides)
    return ModelExecutionDecision(**fields)


def test_known_warning_and_contract_codes_keep_their_spelling():
    receipt = reply_rejection_receipt(
        _decision(details={"warnings": ["foreign_tool_markers_stripped"],
                           "contract_error": "invalid_json:Expecting value: line 1 column 1"}),
        rendered_via="output_contract_failed_refused",
        response_reason="model_output_contract_failed",
    )
    assert receipt["warnings"] == ["foreign_tool_markers_stripped"]
    assert receipt["contract_error"] == "invalid_json"


def test_missing_keys_keep_only_schema_key_names():
    receipt = reply_rejection_receipt(
        _decision(details={"contract_error": "missing_keys:summary,steps"}),
        rendered_via="output_contract_failed_refused",
        response_reason="x",
    )
    assert receipt["contract_error"] == "missing_keys:summary,steps"


def test_an_unknown_warning_is_hashed_never_stored_as_words():
    receipt = reply_rejection_receipt(
        _decision(details={"warnings": [f"something wholly new went wrong near {MARKER}"]}),
        rendered_via="output_contract_failed_refused",
        response_reason="x",
    )
    stored = receipt["warnings"][0]
    assert stored.startswith("h_") and len(stored) == 14, stored
    blob = json.dumps(receipt).lower()
    for word in ("something", "wholly", "went", "wrong", "near"):
        assert word not in blob, word
    assert MARKER.lower() not in blob


def test_an_unknown_contract_error_name_is_hashed_never_stored_as_words():
    receipt = reply_rejection_receipt(
        _decision(details={"contract_error": f"a novel failure wording {MARKER}"}),
        rendered_via="output_contract_failed_refused",
        response_reason="x",
    )
    stored = receipt["contract_error"]
    assert stored.startswith("h_") and len(stored) == 14, stored
    blob = json.dumps(receipt).lower()
    for word in ("novel", "failure", "wording"):
        assert word not in blob, word
    assert MARKER.lower() not in blob


def test_a_missing_keys_detail_outside_the_schema_vocabulary_is_hashed():
    receipt = reply_rejection_receipt(
        _decision(details={"contract_error": "missing_keys:summary,freeform_extra"}),
        rendered_via="output_contract_failed_refused",
        response_reason="x",
    )
    stored = receipt["contract_error"]
    assert stored.startswith("missing_keys:summary,h_"), stored
    assert "freeform" not in stored
