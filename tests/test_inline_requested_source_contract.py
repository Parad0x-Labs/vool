"""Complete inline procedures remain original source units. Contributor: sls_0x."""
from __future__ import annotations

import pytest

from core import context_retrieval as cr
from core import temporal_selection
from tests.test_recall_evidence_merge_law_20260929 import _live
from tests.test_requested_source_authority_contract import _capsule, source_home

BODY = ("I run the Alder-31 ticket-folding batch. The ordinary procedure is ordered: "
        "first cut three cards, then stamp A31 on their backs, then soak the cards for "
        "9 minutes, then hang them vertically, and finally attach a yellow tag. "
        "Do not swap stamping and soaking.")
QUESTION = "For Alder-31, give the full procedure in the correct order, including the duration and tag."


def test_inline_ordered_source_has_one_exact_complete_unit():
    windows = cr._requested_collection_windows(QUESTION, BODY)
    assert len(windows) == 1
    assert windows[0]["text"] == BODY
    assert BODY[windows[0]["start"]:windows[0]["end"]] == BODY
    assert windows[0]["complete_requested"] and windows[0]["structure_bound"]


def test_inline_collection_keeps_subject_and_episode_boundary():
    other = BODY.replace("Alder-31", "Spruce-52").replace("A31", "S52").replace("yellow", "purple")
    assert not cr._requested_collection_windows(QUESTION, other)
    joined = BODY + "\n\n" + other
    windows = cr._requested_collection_windows(QUESTION, joined)
    assert len(windows) == 1 and windows[0]["text"] == BODY
    assert "Spruce-52" not in windows[0]["text"]


@pytest.mark.parametrize("question", [
    "What is the price of the Alder-31 procedure?",
    "Which step comes first in the Alder-31 procedure?",
])
def test_noncomplete_lookup_does_not_request_whole_inline_source(question):
    assert not cr._requested_collection_windows(question, BODY)


def test_inline_source_selection_reaches_real_capsule_and_receipt(source_home):
    _live(source_home, "inline-source", [("user", BODY)])
    capsule = _capsule(source_home, QUESTION, chat="inline-source")
    assert BODY in capsule
    refs = cr.get_last_retrieval_telemetry().get("evidence_refs", [])
    assert any(r.get("delivered") and r.get("source_complete")
               and r.get("source_preserving") and r.get("span", {}).get("text") == BODY for r in refs)


def test_temporal_owner_failure_is_captured_without_unadjudicated_evidence(source_home, monkeypatch):
    _live(source_home, "inline-source", [("user", "The Alder-31 archive access code is Q-594.")])
    def fail(*args, **kwargs):
        raise RuntimeError("authored temporal owner failure")
    monkeypatch.setattr(temporal_selection, "apply_temporal_selection", fail)
    capsule = _capsule(source_home, "What is the Alder-31 archive access code?", chat="inline-source")
    assert "Q-594" not in capsule
    telemetry = cr.get_last_retrieval_telemetry()
    assert telemetry.get("reason_code") == "temporal_selection_failed"
    assert telemetry.get("error_class") == "RuntimeError"
    assert telemetry.get("decision_owner") == "core.context_retrieval._capsule_v2_inject_retrieved:temporal_eligibility"
