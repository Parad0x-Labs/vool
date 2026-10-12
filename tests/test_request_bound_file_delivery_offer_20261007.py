"""Request-specific delivery constraints reach text and native offers together."""
from copy import deepcopy

import pytest

from core.cloud_tool_call_contract import build_cloud_tool_definitions
from core.tool_offer_assembly import _scope_download_specs, assemble_tool_offer


def test_a_zip_request_offers_a_zip_writer_not_loose_html():
    offer = assemble_tool_offer(user_text="Write an HTML page and CSS file, then zip them together. Do not run scripts.", source_context={})
    tool = next(t for t in build_cloud_tool_definitions(offer.specs) if t.intent == "file.deliver")
    assert tool.parameters["properties"]["format"]["enum"] == ["zip"]
    assert "files" in tool.parameters["required"]
    assert "ONE archive call" in tool.description
    spec = next(s for s in offer.specs if s["intent"] == "file.deliver")
    assert "required" in spec["arguments"]["files"]


def test_two_requested_output_formats_remain_available():
    offer = assemble_tool_offer(user_text="Give me a report as a PDF and raw numbers as a CSV file", source_context={})
    tool = next(t for t in build_cloud_tool_definitions(offer.specs) if t.intent == "file.deliver")
    assert tool.parameters["properties"]["format"]["enum"] == ["pdf", "csv"]
    assert "files" not in tool.parameters["required"]

@pytest.mark.parametrize("text", ["Explain how to make a PDF accessible.", "Write a note in my workspace then zip it up.", "Give me a .rar file.", "hello"])
def test_non_download_or_unsupported_requests_do_not_narrow_the_registry(text):
    specs = [{"intent": "file.deliver", "json_schema": {"properties": {"format": {"type": "string"}}}}]
    assert _scope_download_specs(specs, text) == specs


def test_narrowing_never_mutates_registry_or_seats_an_unavailable_tool():
    specs = [{"intent": "file.deliver", "arguments": {}, "json_schema": {"properties": {"format": {"type": "string"}}, "required": ["filename"]}}, {"intent": "workspace.write_file", "arguments": {"path": "string"}}]
    before = deepcopy(specs)
    scoped = _scope_download_specs(specs, "Give me a ZIP file")
    assert specs == before
    assert scoped[1] == specs[1]
    assert _scope_download_specs(specs[1:], "Give me a ZIP file") == specs[1:]

def test_native_archive_members_have_one_format_authority():
    offer = assemble_tool_offer(user_text="Give me an HTML page and CSS file as a zip archive", source_context={})
    tool = next(t for t in build_cloud_tool_definitions(offer.specs) if t.intent == "file.deliver")
    members = tool.parameters["properties"]["files"]["items"]
    assert "format" not in members["properties"]
    assert members["additionalProperties"] is False
