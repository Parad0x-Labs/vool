"""V-043: `pdf.ocr` is offered on macOS only, and nothing calls it where it cannot run.

Recognition runs on Apple's Vision. On Linux the tool was still offered to the model, and the PDF
fast path chained into it on every scan, so a Linux chat answered a scanned PDF with "I ran
on-device OCR on it as well and that did not recover text either: ModuleNotFoundError: No module
named 'objc'". Off macOS the tool is now not offered, an OCR ask reads the text layer instead, and
a scan gets one plain sentence.
"""
from __future__ import annotations

import types

import pytest

from core import pdf_tools
from core.agent_runtime.fast_paths_pdf import maybe_handle_pdf_read
from core.runtime_execution_tools import (
    execute_runtime_tool,
    runtime_execution_capability_ledger,
    runtime_execution_tool_specs,
)
from core.runtime_tool_contracts import runtime_tool_contract_map


class _Agent:
    def _fast_path_result(self, *, response: str, reason: str, **_: object) -> dict[str, object]:
        return {"response": response, "reason": reason}

    def _emit_runtime_event(self, _source_context: object, **_: object) -> None:
        return None


@pytest.fixture
def scan(tmp_path, monkeypatch):
    home = tmp_path / "home"
    for name in ("Desktop", "Downloads", "Documents"):
        (home / name).mkdir(parents=True)
    monkeypatch.setattr("core.runtime_execution_tools._machine_home", lambda: home)
    target = home / "Downloads" / "scanned_letter.pdf"
    target.write_bytes(b"%PDF-1.4\n")
    return target


@pytest.fixture
def calls(monkeypatch):
    """The intents the fast path runs, each answered the way the real tool answers a scan."""

    ran: list[str] = []

    def fake(intent, arguments, *, source_context=None, **_):
        ran.append(intent)
        if intent == "pdf.extract_text":
            return types.SimpleNamespace(
                ok=False, status="no_text_layer", response_text="", details={"reason": "scanned"}
            )
        return types.SimpleNamespace(
            ok=True, status="ok", response_text="", details={"text": "[page 1]\nINVOICE 88412"}
        )

    monkeypatch.setattr("core.authorized_tool_execution.execute_authorized_runtime_tool", fake)
    return ran


@pytest.mark.parametrize("phrasing", ["read {p}", "what does {p} say?", "run ocr on {p}"])
def test_off_macos_a_scan_is_answered_without_calling_ocr(scan, calls, monkeypatch, phrasing) -> None:
    monkeypatch.setattr(pdf_tools, "ocr_available", lambda: False)

    result = maybe_handle_pdf_read(_Agent(), phrasing.format(p=scan), session_id="t", source_context={})

    assert result is not None
    assert calls == ["pdf.extract_text"], calls
    reply = str(result["response"])
    assert "scanned PDF" in reply
    assert "not available on this computer" in reply
    assert "ran on-device OCR" not in reply
    assert result["reason"] == "pdf_read_scanned_ocr_not_available"


def test_on_macos_a_scan_still_chains_into_ocr(scan, calls, monkeypatch) -> None:
    monkeypatch.setattr(pdf_tools, "ocr_available", lambda: True)

    maybe_handle_pdf_read(_Agent(), f"read {scan}", session_id="t", source_context={})

    assert calls == ["pdf.extract_text", "pdf.ocr"], calls


def test_off_macos_the_contract_is_unsupported_and_not_offered(monkeypatch) -> None:
    monkeypatch.setattr(pdf_tools, "ocr_available", lambda: False)

    contract = runtime_tool_contract_map()["pdf.ocr"]
    assert contract.supported is False
    assert "macOS" in contract.unsupported_reason
    offered = {spec["intent"] for spec in runtime_execution_tool_specs()}
    assert "pdf.ocr" not in offered and "pdf.extract_text" in offered
    # The capability stays listed, as partial, with the reason a model or Settings can show.
    (pdf_read,) = [row for row in runtime_execution_capability_ledger() if row["capability_id"] == "pdf.read"]
    assert pdf_read["support_level"] == "partial"
    assert pdf_read["dispatchable_intents"] == ["pdf.extract_text"]
    assert "macOS" in pdf_read["unsupported_reason"]


def test_off_macos_a_direct_call_is_refused_before_anything_runs(scan, monkeypatch) -> None:
    """A model that names the tool anyway (an old prompt, a guess) starts nothing."""

    monkeypatch.setattr(pdf_tools, "ocr_available", lambda: False)
    started: list[object] = []
    monkeypatch.setattr(pdf_tools, "_run_helper", lambda *a, **k: started.append(a) or {})

    result = execute_runtime_tool("pdf.ocr", {"path": str(scan)}, source_context={"surface": "api"})

    assert result is not None and not result.ok
    assert result.status == "disabled"
    assert started == []


def test_with_reading_off_the_reading_reason_wins(monkeypatch) -> None:
    """Off macOS with file reading disabled, the reason names the setting the owner can change."""

    from core import policy_engine

    monkeypatch.setattr(pdf_tools, "ocr_available", lambda: False)
    real_get = policy_engine.get
    monkeypatch.setattr(
        policy_engine,
        "get",
        lambda key, default=None: False if key == "filesystem.allow_read_workspace" else real_get(key, default),
    )

    contract = runtime_tool_contract_map()["pdf.ocr"]
    assert contract.supported is False
    assert contract.unsupported_reason == "Local file reading is disabled on this runtime."


def _model_reads_a_scan(tmp_path, monkeypatch, *, recogniser: bool):
    """The model path: a model asks for pdf.extract_text on a scan, through the real executor."""

    from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
    from core.tool_intent_executor import execute_tool_intent

    home = tmp_path / "home"
    for name in ("Desktop", "Downloads", "Documents"):
        (home / name).mkdir(parents=True)
    monkeypatch.setattr("core.runtime_execution_tools._machine_home", lambda: home)
    target = home / "Documents" / "scanned-receipt.pdf"
    target.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(pdf_tools, "ocr_available", lambda: recogniser)
    monkeypatch.setattr(
        pdf_tools, "_run_helper",
        lambda *_a, **_k: {"status": "ok", "pages_total": 1, "pages_read": 1, "page_text": [""]},
    )
    monkeypatch.setattr(pdf_tools, "_pypdf_text_layer", lambda *_a, **_k: None, raising=False)
    monkeypatch.setattr(pdf_tools, "_uses_pdfkit", lambda: True, raising=False)
    return execute_tool_intent(
        {"intent": "pdf.extract_text", "arguments": {"path": str(target)}},
        task_id="v043",
        session_id="v043-session",
        source_context={"workspace": str(tmp_path / "ws"), "operating_mode": "auto"},
        hive_activity_tracker=HiveActivityTracker(
            config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None)
        ),
    )


def test_off_macos_a_scan_read_by_the_model_ends_on_a_plain_sentence(tmp_path, monkeypatch) -> None:
    execution = _model_reads_a_scan(tmp_path, monkeypatch, recogniser=False)

    assert execution.status == "no_text_layer"
    shown = execution.user_safe_response_text
    assert "scanned" in shown and "not available on this computer" in shown
    assert "pdf.ocr" not in shown


def test_on_macos_the_scan_result_is_left_for_the_model_to_chain(tmp_path, monkeypatch) -> None:
    execution = _model_reads_a_scan(tmp_path, monkeypatch, recogniser=True)

    assert execution.status == "no_text_layer"
    assert "pdf.ocr" in execution.response_text
    assert execution.user_safe_response_text == ""
