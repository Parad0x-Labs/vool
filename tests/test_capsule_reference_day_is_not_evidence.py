"""The capsule header names today's date for the reader; that date is never evidence of an event.

The reference-day sentence sits inside the capsule header (one line, before the records) so the
reader counts "how many days/weeks ago" from today instead of from a record's stated date. The
past-time guard reads records, not the header: an answer that dates an event to today from the
header alone is still withdrawn. Names and dates are authored for this contract.
"""
from core.context_retrieval import _header_with_reference_day
from core.model_output_guard import replace_unsupported_past_time_claims, stated_past_time_claims

HEADER = ("Distilled local facts. Answer from these records: you may combine them and state a direct "
          "inference from them, marked as inferred, but never add a fact the records do not state.")


def test_the_header_names_the_reference_day_and_keeps_its_first_sentence():
    header = _header_with_reference_day(HEADER)
    assert header.startswith(HEADER)
    assert "Today is " in header and "\n" not in header


def _capsule(header: str) -> str:
    return ("<retrieved_context>\n" + header + "\n"
            "- user said (stated 2023-11-01): I finished glazing the blue serving bowl at the Aarhus studio today.\n"
            "- user said (stated 2023-11-20): The kiln at the Aarhus studio fired my blue serving bowl on November 18.\n"
            "</retrieved_context>")


def test_the_reference_day_in_the_header_supports_no_event_date():
    with_day = _capsule(HEADER + " Today is 2024-02-01: count days, weeks and months back from this date, "
                                 "never from a record's stated date.")
    question = "When did the kiln fire my blue serving bowl?"
    assert stated_past_time_claims("The kiln fired your blue serving bowl on February 1, 2024.", question=question,
                                   evidence_texts=[with_day])


def test_the_reference_day_sentence_changes_no_guard_decision():
    with_day = _capsule(HEADER + " Today is 2024-02-01: count days, weeks and months back from this date, "
                                 "never from a record's stated date.")
    without = _capsule(HEADER)
    question = "When did the kiln fire my blue serving bowl?"
    for answer in ("The kiln fired your blue serving bowl on November 18, 2023.",
                   "The kiln fired your blue serving bowl on February 1, 2024.",
                   "On November 18.", "Today, February 1, 2024."):
        assert (stated_past_time_claims(answer, question=question, evidence_texts=[with_day])
                == stated_past_time_claims(answer, question=question, evidence_texts=[without])), answer
