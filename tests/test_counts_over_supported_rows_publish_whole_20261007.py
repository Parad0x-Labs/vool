"""Synthetic supported lists/counts publish whole; invented siblings remain withheld."""
import pytest

from core.grounding_lifecycle import GroundingLifecycle, TurnIdentity
from core.grounding_publication import UNSUPPORTED_WORK_NOTICE_LEAD, _trim_unsupported_spans, publication_verdict
from core.memory_grounding import memory_record_rows

PURCHASE_Q = "How many kitchen items have I bought this year?"
PURCHASES = "\n".join([
    "- user said (stated 2025-02-02): I bought a kettle for $40.",
    "- user said (stated 2025-03-06): I bought a pan for $25.",
    "- user said (stated 2025-05-19): I bought a mug for $8.",
])
CITIES = "\n".join(["- user said (stated 2025-03-11): I travelled to Porto.",
                     "- user said (stated 2025-05-17): I travelled to Ghent."])


def publish(question, records, answer):
    lifecycle = GroundingLifecycle(lifecycle_id="synthetic-aggregate-turn", identity=TurnIdentity(),
                                   request_text=question, retrieval_outcome="no_sources", model_authored=True,
                                   memory_records=tuple(memory_record_rows(records)))
    return publication_verdict(lifecycle, answer)


def test_three_supported_purchase_rows_publish_the_count_and_whole_list():
    answer = "You bought 3 items: a kettle ($40), a pan ($25), and a mug ($8)."
    verdict = publish(PURCHASE_Q, PURCHASES, answer)
    assert verdict.content == answer
    assert verdict.state == "published"
    assert UNSUPPORTED_WORK_NOTICE_LEAD not in verdict.content
    assert any("count over supported rows" in row["reasons"] for row in verdict.claim_support["claims"])


def test_two_supported_city_rows_publish_the_whole_list():
    answer = "Porto and Ghent."
    verdict = publish("Which cities have I travelled to this year?", CITIES, answer)
    assert verdict.content == answer
    assert verdict.state == "published"
    assert UNSUPPORTED_WORK_NOTICE_LEAD not in verdict.content
    assert any("list over supported rows" in row["reasons"] for row in verdict.claim_support["claims"])


@pytest.mark.parametrize("answer", [
    "You bought 4 items: a kettle ($40), a pan ($25), a mug ($8), and a wok ($61).",
    "You bought 4 items: a kettle ($40), a pan ($25), and a mug ($8).",
    "You bought 3 items: a kettle ($40), a pan ($26), and a mug ($8).",
])
def test_invented_item_count_or_price_remains_withheld_without_a_fragment(answer):
    verdict = publish(PURCHASE_Q, PURCHASES, answer)
    assert verdict.content != answer
    assert verdict.withheld_claims
    assert UNSUPPORTED_WORK_NOTICE_LEAD in verdict.content or verdict.state == "refused"
    body = verdict.content.split(UNSUPPORTED_WORK_NOTICE_LEAD)[0]
    assert "wok" not in body
    for line in body.splitlines():
        assert not line.lstrip().lower().startswith((",", "and "))


def test_an_unrecorded_city_is_withheld():
    verdict = publish("Which cities have I travelled to this year?", CITIES, "Porto and Zurich.")
    assert "Zurich" not in verdict.content.split(UNSUPPORTED_WORK_NOTICE_LEAD)[0]
    assert verdict.withheld_claims


@pytest.mark.parametrize("line,unsupported", [
    ("Three: a kettle ($40), a pan ($25), and a mug ($8).", ["Three: a kettle ($40), a pan ($25)"]),
    ("Porto, Ghent, and Zurich.", ["Porto, Ghent"]),
    ("You bought a wok, and a mug.", ["You bought a wok,"]),
])
def test_trim_never_starts_in_the_middle_of_a_sentence(line, unsupported):
    assert _trim_unsupported_spans(line, unsupported) is None


def test_trim_can_keep_a_complete_later_sentence():
    assert _trim_unsupported_spans("You bought a wok. You bought a mug.", ["You bought a wok."]) == "You bought a mug."


def test_a_spelled_out_count_has_the_same_supported_operands():
    answer = "Three: a kettle ($40), a pan ($25), and a mug ($8)."
    verdict = publish(PURCHASE_Q, PURCHASES, answer)
    assert verdict.content == answer
    assert any("count over supported rows" in row["reasons"] for row in verdict.claim_support["claims"])


def test_a_wrong_spelled_out_count_is_withdrawn():
    answer = "Four: a kettle ($40), a pan ($25), and a mug ($8)."
    verdict = publish(PURCHASE_Q, PURCHASES, answer)
    assert verdict.content != answer
    assert verdict.withheld_claims
