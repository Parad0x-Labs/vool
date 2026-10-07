"""v14.6 port: three kernel-on leaks the port's own time-leg and native-contract laws found. A hedged clause dates no
event (the receipts honour the hedged-back-reference law); a date inside quotation marks is not the question's frame;
a record dated after the asked day is not near it (the as-of law); a date claim binds only to a record about the asked
person. Authored cases. Contributor: sls_0x."""
from datetime import date

import pytest

from core.evidence_kernel.temporal_binder import _actor_mismatch, bind_temporal_claims
from core.memory_receipts import extract_facts, match_receipts, resolve_event_day

T = 1749632400.0   # 2025-06-11 09:00Z


@pytest.mark.parametrize("sentence", ["Maybe yesterday the swarm went to the old chestnut, I am not sure.", "Perhaps two days ago I moved the hive.", "I think it was last week, not sure."])
def test_a_hedged_clause_dates_nothing(sentence):
    assert resolve_event_day(sentence, date(2025, 6, 11)) == (None, "hedged")
    assert all(f.event_at is None for f in extract_facts(sentence, T, "user"))


def test_a_plain_clause_still_dates_its_event():
    day, grain = resolve_event_day("Yesterday the swarm went to the old chestnut.", date(2025, 6, 11))
    assert day == date(2025, 6, 10) and grain == "day"


def test_a_record_dated_after_the_asked_day_is_not_near_it():
    before = {"receipt_id": "r:1", "role": "user", "statement_at": 1675245600.0, "facts": [{"slot": ["glaze", "mug", "celadon"], "value_type": "state", "norm": "glaze=celadon", "event_at": 1675245600.0, "sentence": "The studio glaze for the mugs is celadon."}]}
    after = {"receipt_id": "r:2", "role": "user", "statement_at": 1678356000.0, "facts": [{"slot": ["glaze", "mug", "tenmoku"], "value_type": "state", "norm": "glaze=tenmoku", "event_at": 1678356000.0, "sentence": "Correction: the studio glaze for the mugs is now tenmoku."}]}
    q = "What was the studio glaze for the mugs on March 8, 2023?"
    plain = {r["receipt_id"]: s for s, r, f in match_receipts([before, after], q)}
    dated = {r["receipt_id"]: s for s, r, f in match_receipts([before, after], q, target_day=date(2023, 3, 8))}
    assert dated["r:1"] > plain["r:1"], (plain, dated)          # three days before the day: near, boosted
    assert dated["r:2"] == plain["r:2"], (plain, dated)         # the day after: not near, no boost


@pytest.mark.parametrize("question, record, mismatch", [
    ("When did Neri finish the gallery mural?", "Theo finished his gallery mural on 19 May 2025.", True),
    ("When did Neri finish the gallery mural?", "Neri: I finished the gallery mural today.", False),
    ("When did I finish the mural?", "Theo finished his gallery mural on 19 May 2025.", False),      # no name asked: no actor law
    ("When did Neri and Theo paint?", "Theo finished his gallery mural on 19 May 2025.", False),
])
def test_the_actor_law(question, record, mismatch):
    assert _actor_mismatch(question, record) is mismatch


def test_a_date_claim_does_not_bind_to_another_persons_dated_record():
    facts = [{"receipt_id": "r:1", "sentence": "Theo finished his gallery mural on 19 May 2025.", "statement_at": 1747990800.0, "event_at": 1747612800.0, "value_type": "event"},
             {"receipt_id": "r:2", "sentence": "Neri: I finished the gallery mural today.", "statement_at": 1747990800.0, "event_at": 1747990800.0, "value_type": "event"}]
    b = bind_temporal_claims(question="When did Neri finish the gallery mural?", reply="Neri finished the gallery mural on 19 May 2025.", packet_facts=facts, reference_day=date(2025, 6, 1))
    assert not b.restored, b.as_dict()
    b2 = bind_temporal_claims(question="When did Neri finish the gallery mural?", reply="Neri finished the gallery mural on 23 May 2025.", packet_facts=facts, reference_day=date(2025, 6, 1))
    assert b2.restored, b2.as_dict()
