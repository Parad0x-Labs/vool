"""Repair 5 (Mnemosyne review, 2026-08-06): the classifier's length guard alone did not stop a
short-but-unrelated message from matching a bare substring trigger. Confirmed false positives
through real dispatch:

  "please summarise the original message from the customer" -> incorrectly retried the persisted operation
  "which assets should I buy this year?"                    -> incorrectly listed old attempt entities
  "which cities have the best weather in Europe?"            -> incorrectly listed old attempt entities
  "what went wrong with the Challenger shuttle"               -> incorrectly explained an unrelated attempt

And a confirmed false negative: "what did I originally ask?" was not classified at all.

Every family is now a fully anchored regex (^...$) -- the whole normalized message must be an
exact-or-near-exact operational utterance, never a phrase appearing partway through an unrelated
sentence. This file proves the exact required control matrix, plus a sabotage mutation reproducing
the pre-repair substring-matching defect.
"""

from __future__ import annotations

import unittest

from core.agent_runtime.attempt_followup import (
    EXPLAIN_ATTEMPT_FAILURE,
    LIST_ORIGINAL_ENTITIES,
    REPEAT_ORIGINAL_REQUEST,
    RETRY_ATTEMPT,
    classify_followup_intent,
)

_MUST_RESOLVE = (
    ("why?", EXPLAIN_ATTEMPT_FAILURE),
    ("why did that fail?", EXPLAIN_ATTEMPT_FAILURE),
    ("what went wrong?", EXPLAIN_ATTEMPT_FAILURE),
    ("retry", RETRY_ATTEMPT),
    ("run that again", RETRY_ATTEMPT),
    ("what did I originally ask?", LIST_ORIGINAL_ENTITIES),
    ("which assets and cities did I ask for?", LIST_ORIGINAL_ENTITIES),
    ("check the original message", REPEAT_ORIGINAL_REQUEST),
)

_MUST_NOT_RESOLVE = (
    "what went wrong with the Challenger shuttle?",
    "which assets should I buy this year?",
    "which cities have the best weather in Europe?",
    "summarise the original message from the customer",
    "please summarise the original message from the customer",
    "how do I retry a failed HTTP request in Python?",
    "explain why retries fail in distributed systems",
)


class ClassifierPrecisionControlMatrixTests(unittest.TestCase):
    def test_required_positive_matrix(self) -> None:
        for text, expected in _MUST_RESOLVE:
            with self.subTest(text=text):
                self.assertEqual(classify_followup_intent(text), expected)

    def test_required_negative_matrix(self) -> None:
        for text in _MUST_NOT_RESOLVE:
            with self.subTest(text=text):
                self.assertIsNone(classify_followup_intent(text), text)

    def test_confirmed_false_positive_no_longer_routes_into_a_retry(self) -> None:
        """The specific incident: "original message" as a bare substring routed an unrelated
        customer-support request into REPEAT_ORIGINAL_REQUEST, which -- for a non-SUCCEEDED
        attempt -- falls through to an actual retry of the wrong persisted operation."""
        text = "please summarise the original message from the customer"
        self.assertIsNone(classify_followup_intent(text))

    def test_ambiguous_but_related_phrasing_falls_through_honestly(self) -> None:
        """Not in either required list, but the same discipline applies: something that merely
        MENTIONS retrying, without being an operational retry request itself, must not fire."""
        self.assertIsNone(classify_followup_intent("I read online that you should always retry on 500 errors"))


class SabotageSubstringMatchingTests(unittest.TestCase):
    """Sabotage: reproduce the pre-Repair-5 unanchored substring design and confirm it DOES
    misclassify the confirmed incidents -- proving the anchoring in the real code is load-bearing,
    not merely stylistic."""

    @staticmethod
    def _sabotaged_classify(text: str) -> str | None:
        normalized = " ".join(str(text or "").strip().lower().split()).strip(" \t\n\r?!.,")
        if not normalized or len(normalized.split()) > 16:
            return None
        list_entities_substrings = ("which assets", "which cities", "originally ask for")
        explain_failure_substrings = ("why did that", "why did it", "what went wrong")
        original_request_substrings = ("original message", "original question", "original request")
        if any(s in normalized for s in list_entities_substrings):
            return LIST_ORIGINAL_ENTITIES
        if any(s in normalized for s in explain_failure_substrings):
            return EXPLAIN_ATTEMPT_FAILURE
        if any(s in normalized for s in original_request_substrings):
            return REPEAT_ORIGINAL_REQUEST
        return None

    def test_sabotage_reproduces_every_confirmed_false_positive(self) -> None:
        cases = [
            ("what went wrong with the Challenger shuttle?", EXPLAIN_ATTEMPT_FAILURE),
            ("which assets should I buy this year?", LIST_ORIGINAL_ENTITIES),
            ("which cities have the best weather in Europe?", LIST_ORIGINAL_ENTITIES),
            ("please summarise the original message from the customer", REPEAT_ORIGINAL_REQUEST),
        ]
        for text, sabotaged_intent in cases:
            with self.subTest(text=text):
                self.assertEqual(
                    self._sabotaged_classify(text), sabotaged_intent,
                    "sabotage should have reproduced the false-positive misclassification",
                )
                # Control: the REAL classifier correctly declines every one of these.
                self.assertIsNone(classify_followup_intent(text))


if __name__ == "__main__":
    unittest.main()
