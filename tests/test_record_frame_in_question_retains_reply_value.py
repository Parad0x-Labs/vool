"""A concise reply to a record question keeps its value when the record frame is in the question.

Measured on the official LongMemEval_S run 1 (2026-10-06, q48c0fce9504f8410): the reader answered
"132 points (set on 2023/05/25)" to "What is my current highest score in Ticket to Ride?" and the
current-claim check withdrew it because the reply clause carried no record vocabulary, although the
admitted capsule held the user's own statement of that record. The frame is the question-answer pair.
"""
from core.unsourced_current_claim import recorded_state_retention

CAPSULE = (
    "<retrieved_context>\n"
    "Distilled local facts. Answer from these records: you may combine them and state a direct "
    "inference from them, marked as inferred, but never add a fact the records do not state.\n"
    "- user said: By the way, speaking of building and creating things, I just got my highest score in Ticket to Ride - 132 points! (stated: Session date: 2023/05/25; stated: 2023-05-25)\n- user said: Also, by the way, I've been crushing it in Ticket to Ride lately - my highest score so far is 124 points, and I'm eager to keep improving. (stated: Session date: 2023/05/23; stated: 2023-05-23)\n"
    "</retrieved_context>"
)
QUESTION = "What is my current highest score in Ticket to Ride?"


def test_value_only_reply_is_retained_when_the_question_carries_the_record_frame():
    assert recorded_state_retention("132 points (set on 2023/05/25).", CAPSULE, question_text=QUESTION)


def test_value_only_reply_still_needs_the_record_frame_somewhere():
    assert not recorded_state_retention("132 points (set on 2023/05/25).", CAPSULE)


def test_a_value_the_records_do_not_state_is_not_retained_through_the_question_frame():
    assert not recorded_state_retention("150 points.", CAPSULE, question_text=QUESTION)


def test_a_record_question_about_another_subject_does_not_bind_the_value():
    assert not recorded_state_retention(
        "132 points.", CAPSULE, question_text="What is my highest bowling score?"
    )


BOWLING = (
    "<retrieved_context>\n"
    "Distilled local facts. Answer from these records: you may combine them and state a direct "
    "inference from them, marked as inferred, but never add a fact the records do not state.\n"
    "- user said: Bowling night update - my highest score at the Kestrel Lanes league is now 187 points. (stated: 2024-09-14)\n"
    "- user said: My highest score at the Kestrel Lanes league was 161 points back in spring. (stated: 2024-04-02)\n"
    "</retrieved_context>"
)


def test_an_unrelated_record_question_keeps_its_value_only_reply():
    question = "whats my current highest score at kestrel lanes league"
    assert recorded_state_retention("187 points.", BOWLING, question_text=question)


def test_an_unrelated_record_question_does_not_keep_a_value_no_record_states():
    question = "What is my current highest score at the Kestrel Lanes league?"
    assert not recorded_state_retention("192 points.", BOWLING, question_text=question)


def test_a_question_with_no_record_frame_lends_no_frame():
    assert not recorded_state_retention("187 points.", BOWLING, question_text="How do people score a spare?")
