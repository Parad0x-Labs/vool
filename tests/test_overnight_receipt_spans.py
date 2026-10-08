"""Receipt-offset development proofs; no whitespace normalization of evidence."""
import pytest

from core.context_retrieval import _evidence_clause_windows
from tests.test_overnight_source_structure import _recall, _store, source_env


@pytest.mark.parametrize("body,question", [
    ("Overview of calibration.\n\n  | Tool | Pressure (kPa) | \n | --- | --- |\n | Elder | 82-97 |  \n", "From our previous chat, what pressure did the chart list for Elder?"),
    ("Overview of bird boxes.\n   - Roof screws (eight): bronze finish.  \n - Hinges (two): brass finish.\n", "From our previous chat, how many roof screws were on the list?"),
    ("The workshop key is brass.   The shed label reads violet.\n", "Which label did I give the shed?"),
])
def test_window_offsets_match_the_exact_displayed_trimmed_source(body,question):
    windows=_evidence_clause_windows(question,body)
    assert windows
    for w in windows:
        assert w["text"]==w["text"].strip(),w
        assert body[w["start"]:w["end"]]==w["text"],w


def test_real_capsule_receipt_equals_source_slice_with_internal_whitespace(source_env):
    from core.context_retrieval import get_last_retrieval_telemetry
    body="Planning notes for calibration.\n\n  | Tool | Pressure (kPa) | \n | --- | --- |\n | Elder | 82-97 |  \n | Hazel | 55-62 |\n"
    _store(source_env,"receipt-spaces","Prepare the calibration sheet.",body)
    context=_recall(source_env,"receipt-spaces","From our previous chat, what pressure did the chart list for Elder?")
    assert "82-97" in context
    # store_turn strips only the outside of the complete body.
    stored=body.strip()
    delivered=[r for r in get_last_retrieval_telemetry()["evidence_refs"] if r.get("delivered")]
    assert delivered
    for r in delivered:
        span=r["span"]
        assert stored[span["start"]:span["end"]]==span["text"],r
