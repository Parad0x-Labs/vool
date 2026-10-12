"""Artifact orders, workspace destinations, and download-only side effects."""
from __future__ import annotations

import pytest


@pytest.mark.parametrize("text,expected", [
    ("Zip the CSV and the PDF together.", {"zip"}),
    ("Put the report in a zip. Also give me a PDF copy.", {"zip", "pdf"}),
    ("Send me a PDF summary, and put the raw CSVs in a zip.", {"pdf", "zip"}),
    ("Make a CSV and a PDF, then zip them up.", {"zip"}),
    ("Give me an Excel sheet plus a zip of the source files.", {"xlsx", "zip"}),
    ("Compress the HTML and CSS into a tarball, and export a CSV of the colors.", {"tar.gz", "csv"}),
    ("I want a PDF report and a zip with the charts as PNGs.", {"pdf", "zip"}),
])
def test_separate_outputs(text, expected):
    from core.deliverables import requested_formats
    got = set(requested_formats(text))
    assert got == expected

@pytest.mark.parametrize("text,download_only", [
    ("Make a PDF about the folder structure of a typical Rails app.", True),
    ("Create a CSV listing what usually goes in each folder of a Django project.", True),
    ("I need a CSV of the planets.", True),
    ("Export the table to Excel.", True),
    ("Save the CSV to my reports folder.", False),
    ("Drop a PDF in the docs directory of the repo.", False),
    ("Create a CSV of the planets inside the data folder.", False),
    ("Put a markdown summary under the notes folder.", False),
])
def test_workspace_ownership(text, download_only):
    from core.deliverables import downloadable_request_without_workspace_target
    got = downloadable_request_without_workspace_target(text)
    assert got is download_only

@pytest.mark.parametrize("text,is_order", [
    ("Explain how to make a PDF and make me one?", True),
    ("How do PDFs work? Make me a sample one.", True),
    ("Tell me how to zip files and zip these for me.", True),
    ("Explain how to make a CSV, export it, and share it.", False),
    ("Explain how to make a CSV and why people make one.", False),
    ("Show me how to export a CSV and then how to import one.", False),
])
def test_instructional(text, is_order):
    from core.tool_demand_signals import asks_for_a_file
    got = asks_for_a_file(text)
    assert got is is_order
    if is_order:
        from core.tool_demand_signals import resolve_demand_signals
        assert "file.deliver" in resolve_demand_signals(text).explicit_intents

MUT = [("workspace.build_scaffold", {"name": "app", "files": [{"path": "a.py", "content": "x"}]}),
       ("workspace.run_formatter", {"path": "a.py"}), ("workspace.rollback_last_change", {}),
       ("sandbox.run_command", {"command": "echo hi > stray.txt"}), ("operator.move_path", {"source": "a", "destination": "b"}),
       ("machine.write_file", {"path": "guard_probe.txt", "content": "x"})]
@pytest.mark.parametrize("intent,args", MUT, ids=[m[0] for m in MUT])
def test_mutators_under_download_only(intent, args, monkeypatch):
    from core import tool_intent_executor
    from core.deliverables import DOWNLOAD_ONLY_KEY
    from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
    reached = []
    def dispatch(*a, **k):
        reached.append(1); raise RuntimeError("dispatch reached (probe stop)")
    monkeypatch.setattr(tool_intent_executor, "execute_authorized_runtime_tool", dispatch)
    try:
        ex = tool_intent_executor._execute_tool_intent_impl({"intent": intent, "arguments": args}, task_id="download-ownership", session_id="download-ownership",
             source_context={DOWNLOAD_ONLY_KEY: True, "operating_mode": "auto"},
             hive_activity_tracker=HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False)))
        status = ex.status
    except RuntimeError as e:
        status = f"raised:{e}"
    assert not reached, status


@pytest.mark.parametrize("text,expected", [
    ("Explain how to make a CSV, export it, and share it?", False),
    ("Can you explain how to make a PDF?", False),
    ("I need a CSV response inline.", False),
    ("How do CSV files work? Also make me a sample one.", True),
    ("Explain how to make a PDF and why people make one?", False),
    ("Can you explain how to export a CSV and create an Excel workbook?", False),
    ("Explain how to export a CSV and create an Excel workbook.", False),
    ("Explain how to export a CSV, and create an Excel workbook.", False),
    ("Explain how to export a CSV. Create an Excel workbook.", True),
    ("Explain how to export a CSV and then create an Excel workbook.", True),
    ("Explain how to export a CSV and create an Excel workbook for me.", True),
])
def test_tutorials_and_explicit_execution_stay_distinct(text, expected):
    from core.tool_demand_signals import asks_for_a_file
    assert asks_for_a_file(text) is expected
