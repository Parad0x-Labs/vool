"""An audit of several named files nominates from each of them, not only from the last one named.

Real-model audit of 3.21, attempts 3 and 4 (qwen3:8b through /api/chat, 2026-11-12/13). Asked "Audit orders.py,
pricing.py and stock.py for bugs", the audit read all three ("Opened 3 source files") and reported
"Target: `stock.py`": every nomination showed stock.py alone with numbered lines and asked what is wrong with
THIS file, because the nomination target was the last file named. In 12 turns no row named orders.py or
pricing.py, though both hold keyed bugs (partial cancel leaves stock reserved; the member coupon applied twice).
The read-only ceiling is three nominations, so three named files get one each, in the order named.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

from core.agent_runtime.stepped_audit import run_stepped_audit

FIXTURE = Path(__file__).with_name("fixtures") / "multi_file_review"
SOURCES = {p.name: p.read_text() for p in FIXTURE.glob("*.py")}
MANIFEST = SimpleNamespace(provider_id="test:pinned", model_name="test-model")
ASKED = "Audit orders.py, pricing.py and stock.py for bugs. List every bug you find. Do not change anything."
FILE_SHOWN_RE = re.compile(r"File `([^`]+)` \(")


class _Router:
    """Records which file each nomination shows; answers every call with an empty batch."""

    def __init__(self) -> None:
        self.files_shown: list[str] = []

    def _requested_model_manifest(self, _context):
        return MANIFEST

    def _invoke_manifest(self, *, manifest, request, output_mode, task, source_context):
        match = FILE_SHOWN_RE.search(str(getattr(request, "prompt", "") or ""))
        if match:
            self.files_shown.append(match.group(1))
        return None, SimpleNamespace(output_text=json.dumps({"findings": []}),
                                     usage={"prompt_tokens": 60, "completion_tokens": 20},
                                     provider_id=manifest.provider_id, model_name=manifest.model_name,
                                     model_call_id="call-1", response_id="resp-1"), None


def _audit(asked: str, session: str) -> _Router:
    router = _Router()
    agent = SimpleNamespace(
        memory_router=router,
        _execute_tool_intent=lambda *a, **k: SimpleNamespace(ok=True, handled=True, response_text="", details={},
                                                             status="executed"),
        hive_activity_tracker=None, public_hive_bridge=None,
    )
    context = {
        "workspace_audit_evidence_collected": True,
        "workspace_audit_evidence": {"all_paths": tuple(SOURCES), "inspected_paths": tuple(SOURCES), "sources": SOURCES,
                                     "workspace_root": f"/tmp/{session}", "incomplete_files": (),
                                     "scoped_target": "stock.py"},
        "requested_model": MANIFEST.model_name,
    }
    run_stepped_audit(agent, task=SimpleNamespace(task_id=f"task-{session}"), effective_input=asked,
                      source_context=context, session_id=session)
    return router


def test_each_named_file_gets_a_nomination_in_the_order_named():
    router = _audit(ASKED, "every-named-file")
    assert router.files_shown == ["orders.py", "pricing.py", "stock.py"], router.files_shown


def test_a_single_named_file_keeps_every_nomination_attempt():
    router = _audit("Audit stock.py for bugs. Do not change anything.", "one-named-file")
    assert set(router.files_shown) == {"stock.py"} and len(router.files_shown) >= 2, router.files_shown
