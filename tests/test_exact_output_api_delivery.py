"""Exact deliverables retain their content while provenance travels separately."""
from __future__ import annotations
from unittest import mock
import pytest
from core.raw_output_contract import parse_raw_output_contract
from core.web.api import runtime as api

@pytest.mark.parametrize("directive",[
    "Return the complete saved lab dispatch manifest exactly as stored. Do not add a heading, footer, attribution or any other text.",
    "Repeat the saved invoice. No footer.",
    "Reproduce the letter without extra text.",
])
def test_explicit_presentation_prohibitions_create_a_raw_contract(directive):
    contract=parse_raw_output_contract(directive)
    assert contract is not None and contract.raw_only
    assert contract.exact_text is None
    assert not contract.no_title

@pytest.mark.parametrize("directive",[
    'Explain the instruction "Do not add a heading, footer or attribution".',
    "Explain why an invoice without a footer is useful.",
])
def test_quoted_or_descriptive_footer_language_is_not_authority(directive):
    assert parse_raw_output_contract(directive) is None

USAGE={"prompt_tokens":37,"output_tokens":11,"total_tokens":48,"provider_id":"offline-local","model_id":"controlled-reader","cost_class":"local"}
ACCOUNTING={"model_calls":1,"lanes":["local"],"models":["controlled-reader"]}

def finalize(raw,context):
    result={"response":raw,"model_calls":1,"model_selected":"controlled-reader","model_execution":{"used_model":True,"provider_id":"offline-local","locality":"local"},"route":"model_minimal:controlled-reader"}
    with mock.patch("core.memory_first_router.get_turn_usage",return_value=USAGE),mock.patch("core.turn_model_call_ledger.turn_call_accounting",return_value=ACCOUNTING),mock.patch("core.auto_local_only_mode.turn_is_local_only",return_value=False):
        return api._finalize_turn_usage(result,context)

@pytest.mark.parametrize(("directive","raw"),[
    ("Output JSON only.",'{ "summary" : "source", "steps" : ["one", "two"] }'),
    ("Output only the exact table and nothing else.","| Unit | Exception |\n|---|---|\n| A | Keep 3 mm |"),
    ("Output raw text only.","Original source heading:\n- condition one\n- condition two"),
])
def test_api_finalizer_preserves_exact_body_and_accounting(directive,raw):
    contract=parse_raw_output_contract(directive)
    assert contract is not None
    result=finalize(raw,{"raw_output_contract":contract.to_dict()})
    assert result["response"]==raw
    assert result["usage_summary"]==USAGE
    assert result["model_call_accounting"]==ACCOUNTING
    assert result["answer_provenance"]["model_ran"]


def test_ordinary_api_reply_keeps_truthful_provenance():
    result=finalize("The complete answer.",{})
    assert result["response"].startswith("The complete answer.\n\n`local | controlled-reader |")
    assert result["answer_provenance"]["model_ran"]


def test_transport_commit_does_not_strip_source_text_resembling_a_footer():
    raw="Literal example from the source:\n\n`local | archive-reader | tokens unreported`"
    contract=parse_raw_output_contract("Output raw text only.")
    result=finalize(raw,{"raw_output_contract":contract.to_dict()})
    with mock.patch("core.finalization.finalize_answer",side_effect=lambda **kwargs:kwargs):
        committed=api._response_commit(result,source_context={"raw_output_contract":contract.to_dict()})
    assert committed["canonical_content"]==raw
    assert committed["display_metadata"]["provenance"]["model_ran"]
    assert committed["display_metadata"]["provenance_footer"]==""
