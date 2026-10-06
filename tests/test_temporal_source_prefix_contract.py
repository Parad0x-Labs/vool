"""A future statement cannot re-enter as an attributed prefix. Contributor: sls_0x."""
from datetime import datetime, timezone

from core import context_retrieval as cr, temporal_selection as ts
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from tests.test_requested_source_authority_contract import source_home, _capsule
from tests.test_temporal_voolble_asof_contract import SURVEY_FIRST, SURVEY_SECOND

CHAT = "source-prefix-cutoff"
QUESTION = ("On 2024-07-10, which of Elena Ruiz and Julian Voss was assigned to the estuary survey? "
            "Explain using their recorded intervals rather than assuming that their assignments were shared.")


def seed(home):
    ensure_chat_namespace(CHAT, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=CHAT)
    ids = []
    for body, stated in [(SURVEY_FIRST, "2024-07-01"), (SURVEY_SECOND, "2025-03-01")]:
        receipt = cr.store_turn(CHAT, body, "", access_policy=policy,
                                source_context={"runtime_home": home,
                                    "statement_at": datetime.fromisoformat(stated).replace(tzinfo=timezone.utc).timestamp()})
        assert receipt["status"] in {"stored", "retained"}
        assert len(receipt["occurrence_ids"]) == 1
        ids.append(receipt["occurrence_ids"][0])
    return ids


def capture_verdicts(monkeypatch):
    owner = ts.apply_temporal_selection
    observed = {}
    def capture(candidates, **kwargs):
        result = owner(candidates, **kwargs)
        observed.update(result)
        return result
    monkeypatch.setattr(ts, "apply_temporal_selection", capture)
    return observed


def test_past_multi_actor_intervals_remain_readable_from_real_ingestion(source_home, monkeypatch):
    first, future = seed(source_home)
    verdicts = capture_verdicts(monkeypatch)
    capsule = _capsule(source_home, QUESTION, chat=CHAT)
    assert verdicts[first].eligible
    assert not verdicts[future].eligible
    assert verdicts[future].reason == "future-relative-to-as-of"
    assert SURVEY_FIRST in capsule
    for endpoint in ("2024-03-01", "2024-06-30", "2024-01-15", "2024-10-15"):
        assert endpoint in capsule


def test_false_future_verdict_cannot_reenter_as_value_free_prefix(source_home, monkeypatch):
    first, future = seed(source_home)
    verdicts = capture_verdicts(monkeypatch)
    capsule = _capsule(source_home, QUESTION, chat=CHAT)
    assert verdicts[first].eligible
    assert verdicts[future].reason == "future-relative-to-as-of"
    assert "resumed the estuary survey" not in capsule
    assert not any(r.get("delivered") and r.get("occurrence_id") == future
                   for r in cr.get_last_retrieval_telemetry().get("evidence_refs", []))
