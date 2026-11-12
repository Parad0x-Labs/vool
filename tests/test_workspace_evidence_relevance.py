"""A project binding does not make unrelated files evidence for a question."""
import pytest

from tests.test_workspace_subject_evidence import enter_turn
from tests.test_workspace_subject_evidence import isolated_home as isolated_home


@pytest.mark.parametrize("question", ["Who wrote Hamlet?", "Explain how a setup.py works"])
def test_general_knowledge_does_not_collect_unrelated_project_content(tmp_path, question):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "quarterly-notes.txt").write_text("Internal fixture margin: 17 percent.\n")
    _, context = enter_turn(question, workspace)
    observations = context.get("runtime_tool_observations", [])
    assert not any(row.get("intent") == "workspace.read_file" for row in observations), observations
    assert "Internal fixture margin" not in str(observations), observations
