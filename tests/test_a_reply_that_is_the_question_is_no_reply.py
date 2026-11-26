"""The person's own message handed back as the reply is not a reply.

iMac gate 44 on b92da7e3c (qwen3:8b): "would a kangaroo on a pogo stick outrun a postman?" came back word for word as
the answer, through respond.direct."""
from __future__ import annotations

from core.agent_runtime.response_policy_classification import tool_intent_direct_message


def _direct(message: str) -> dict:
    return {"intent": "respond.direct", "arguments": {"message": message}}


def test_the_question_handed_back_is_refused_whatever_its_case_or_spacing():
    asked = "could a snail on a skateboard beat a tortoise in a race?"
    assert tool_intent_direct_message(_direct(asked), request_text=asked) is None
    assert tool_intent_direct_message(_direct("Could a snail on a skateboard  beat a tortoise in a race"), request_text=asked) is None


def test_the_same_in_another_language():
    asked = "ar sraigė ant riedlentės aplenktų vėžlį?"
    assert tool_intent_direct_message(_direct("Ar sraigė ant riedlentės aplenktų vėžlį?"), request_text=asked) is None


def test_an_answer_that_repeats_part_of_the_question_is_still_an_answer():
    asked = "could a snail on a skateboard beat a tortoise in a race?"
    reply = "A snail on a skateboard could beat a tortoise in a race, if the hill is steep enough."
    assert tool_intent_direct_message(_direct(reply), request_text=asked) == reply


def test_without_the_request_nothing_changes():
    assert tool_intent_direct_message(_direct("The relay listens on 8787.")) == "The relay listens on 8787."
