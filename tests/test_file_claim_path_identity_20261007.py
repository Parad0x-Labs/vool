"""A completion claim must name the output actually written this turn."""
import uuid

import pytest

from core.agent_runtime.action_honesty_validator import enforce_final_action_honesty


@pytest.mark.parametrize("claim,written,kept", [
    ("reports/a.md", "other/a.md", False),
    ("reports/a.md", "reports/a.md", True),
    ("reports/a.md", "reports/x/../a.md", True),
    ("./a.md", "other/a.md", False),
    ("./a.md", "a.md", True),
    ("reports/a.md", "a.md", False),
    ("reports/a.md", "/workspace/reports/a.md", False),
    ("/workspace/reports/a.md", "reports/a.md", False),
    ("/workspace/reports/a.md", "/workspace/reports/a.md", True),
    ("Reports/a.md", "reports/a.md", False),
    ("a.md", "reports/a.md", True),
    ("my notes.md", "exports/my notes.md", True),
    ("exports/my notes.md", "elsewhere/my notes.md", False),
])
@pytest.mark.parametrize("style", ["active", "passive", "list", "link"])
def test_completion_requires_the_written_directory(claim, written, kept, style):
    if style == "active":
        text = f"I saved {claim}."
    elif style == "passive":
        text = f"{claim} was saved."
    elif style == "list":
        text = f"Files saved:\n- {claim}"
    else:
        text = f"I saved [{claim}](download)."
    receipt = {"tool_name": "file.deliver", "status": "executed", "execution": {"executed": True, "ok": True},
               "observation": {"path": written, "format": "md"}}
    kwargs = dict(user_input="Save the file", session_id=uuid.uuid4().hex,
                  source_context={"tool_receipts": [receipt]})
    result = enforce_final_action_honesty({"response": text}, **kwargs)
    assert (result["response"] == text) is kept
    assert enforce_final_action_honesty(dict(result), **kwargs)["response"] == result["response"]


@pytest.mark.parametrize("claim,written,root,kept", [
    ("reports/a.md", "/workspace/reports/a.md", "/workspace", True),
    ("reports/a.md", "/different/reports/a.md", "/workspace", False),
    ("/workspace/reports/a.md", "reports/a.md", "/workspace", True),
    ("reports/a.md", "/workspace/reports/a.md", "/different", False),
])
def test_runtime_workspace_root_binds_absolute_and_relative_paths(claim, written, root, kept):
    text = f"I saved {claim}."
    result = enforce_final_action_honesty({"response": text}, user_input="Save the file", session_id=uuid.uuid4().hex,
        source_context={"workspace_root": root, "tool_receipts": [{"tool_name": "file.deliver", "status": "executed",
        "execution": {"executed": True, "ok": True}, "observation": {"path": written, "format": "md"}}]})
    assert (result["response"] == text) is kept


def test_display_filename_cannot_override_actual_output_path():
    text = "I saved reports/a.md."
    result = enforce_final_action_honesty({"response": text}, user_input="Save the file", session_id=uuid.uuid4().hex,
        source_context={"tool_receipts": [{"tool_name": "file.deliver", "status": "executed",
        "execution": {"executed": True, "ok": True}, "observation": {"path": "other/a.md", "filename": "reports/a.md", "format": "md"}}]})
    assert result["response"] != text


def test_real_runtime_writer_binds_qualified_path(make_agent, tmp_path):
    from core.execution_truth import recent_session_facts

    session = "path-write-" + uuid.uuid4().hex
    context = {"surface": "openclaw", "session_id": session, "workspace": str(tmp_path),
               "workspace_root": str(tmp_path), "operating_mode": "auto"}
    make_agent().run_once("create reports/a.md containing hello", session_id_override=session, source_context=context)
    assert (tmp_path / "reports/a.md").read_text() == "hello"
    assert any(f.ok and f.name == "workspace.write_file" for f in recent_session_facts(session, limit=64))
    for claim, kept in [("reports/a.md", True), (str(tmp_path / "reports/a.md"), True), ("other/a.md", False)]:
        text = f"I saved {claim}."
        result = enforce_final_action_honesty({"response": text}, user_input="Save the file", session_id=session,
                                             source_context=context)
        assert (result["response"] == text) is kept


@pytest.mark.parametrize("prefix", ["very_long_directory/" * 30, "very long directory/" * 30])
def test_unparsed_long_directory_cannot_be_reduced_to_a_basename(prefix):
    text = f"I saved {prefix}a.md."
    result = enforce_final_action_honesty({"response": text}, user_input="Save the file", session_id=uuid.uuid4().hex,
        source_context={"tool_receipts": [{"tool_name": "file.deliver", "status": "executed",
        "execution": {"executed": True, "ok": True}, "observation": {"path": "a.md", "format": "md"}}]})
    assert result["response"] != text


@pytest.mark.parametrize("location,kept", [("reports/a.md", True), ("other/a.md", False)])
def test_display_name_and_parenthesized_location_both_need_proof(tmp_path, location, kept):
    text = f"I wrote a.md (at {tmp_path / location})."
    result = enforce_final_action_honesty({"response": text}, user_input="Save the file", session_id=uuid.uuid4().hex,
        source_context={"workspace_root": str(tmp_path), "tool_receipts": [{"tool_name": "file.deliver", "status": "executed",
        "execution": {"executed": True, "ok": True}, "observation": {"path": "reports/a.md", "format": "md"}}]})
    assert (result["response"] == text) is kept


@pytest.mark.parametrize("claim", ["reports/and a.md", "reports/my and a.md", "reports/to a.md", "reports/saved a.md"])
@pytest.mark.parametrize("same_path", [False, True])
def test_directory_words_cannot_be_treated_as_prose_boundaries(claim, same_path):
    text = f"I saved {claim}."
    written = claim if same_path else "a.md"
    result = enforce_final_action_honesty({"response": text}, user_input="Save the file", session_id=uuid.uuid4().hex,
        source_context={"tool_receipts": [{"tool_name": "file.deliver", "status": "executed",
        "execution": {"executed": True, "ok": True}, "observation": {"path": written, "format": "md"}}]})
    assert (result["response"] == text) is same_path


@pytest.mark.parametrize("sentence,kept", [
    ("The report was saved at {root}/reports/a.md.", True),
    ("I saved the report at reports/a.md.", True),
    ("I saved the report at other/a.md.", False),
])
def test_a_location_introduced_by_a_preposition_is_the_claimed_path(sentence, kept, tmp_path):
    # "saved at <path>": the preposition introduces the location; it is not part of the path, so
    # a true claim about the written file is kept and a different directory is still refused.
    text = sentence.format(root=tmp_path)
    result = enforce_final_action_honesty({"response": text}, user_input="Save the file", session_id=uuid.uuid4().hex,
        source_context={"workspace_root": str(tmp_path), "tool_receipts": [{"tool_name": "file.deliver", "status": "executed",
        "execution": {"executed": True, "ok": True}, "observation": {"path": "reports/a.md", "format": "md"}}]})
    assert (result["response"] == text) is kept


@pytest.mark.parametrize("written,kept", [
    ("~/Desktop/MarchTest/weekly notes.txt", True),
    ("~/Desktop/Other/weekly notes.txt", False),
])
def test_the_write_lanes_created_file_report_names_the_path_it_wrote(written, kept):
    # The machine write lane reports "Created file <path>."; "file" names what the path is.
    text = "Created file ~/Desktop/MarchTest/weekly notes.txt."
    result = enforce_final_action_honesty({"response": text, "mode": "tool_executed"}, user_input="save it as txt",
        session_id=uuid.uuid4().hex, source_context={"tool_receipts": [{"tool_name": "file.deliver",
        "status": "executed", "execution": {"executed": True, "ok": True},
        "observation": {"path": written, "format": "txt"}}]})
    assert (result["response"] == text) is kept
