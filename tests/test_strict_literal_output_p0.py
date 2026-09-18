"""P0 strict literal responses must be byte-exact.

Measured at base 84bf8b6a on the Served Reality case ``strict-literal-output``: the typed
contract bound the right bytes while the legacy API-boundary rewriter
(``web.api.response_control``) re-derived the target ``this and nothing else:
VERBATIM-PROOF-2291`` -- cue residue -- and OVERWROTE the correct fast-path answer. The typed
parser is the one interpretation authority; when it binds, its bytes are served verbatim
(no markdown, prefix, suffix, quote or whitespace mutation, zero model calls), and when it
recognizes a strict-output instruction but refuses the bytes, no second extractor may invent
them.
"""

from __future__ import annotations

from core.raw_output_contract import parse_raw_output_contract
from core.web.api.response_control import apply_exact_response_control, exact_response_target

SERVED_CASE = "Respond with exactly this and nothing else: VERBATIM-PROOF-2291"


def _bound_literal(message: str) -> str | None:
    contract = parse_raw_output_contract(message)
    return None if contract is None else contract.exact_text


def test_served_reality_case_binds_and_serves_only_the_payload() -> None:
    # The interpreter binds only the payload; the final boundary serves exactly those bytes.
    assert _bound_literal(SERVED_CASE) == "VERBATIM-PROOF-2291"
    served = apply_exact_response_control({"response": "VERBATIM-PROOF-2291"}, SERVED_CASE)
    assert served["response"] == "VERBATIM-PROOF-2291"
    # The overwrite never happens: a boundary that rewrote would leave its marker behind.
    assert "response_control" not in served


def test_a_wrong_model_answer_is_replaced_by_the_typed_bytes_not_cue_residue() -> None:
    served = apply_exact_response_control({"response": "Sure! Here it is:\n\nVERBATIM-PROOF-2291"}, SERVED_CASE)
    assert served["response"] == "VERBATIM-PROOF-2291"
    assert served["response_control"]["target"] == "VERBATIM-PROOF-2291"


def test_varied_punctuation_is_served_verbatim() -> None:
    for message, payload in [
        ("Respond with exactly this and nothing else: Yes!! Really?", "Yes!! Really?"),
        ("Say exactly this and nothing else: wait -- no, stop; ok?", "wait -- no, stop; ok?"),
        ("reply with exactly: R5-CANARY and nothing else", "R5-CANARY"),
        ("respond exactly: PONG", "PONG"),
    ]:
        assert _bound_literal(message) == payload, message
        assert exact_response_target(message) == payload, message


def test_multiline_payload_inside_quotes_is_byte_exact() -> None:
    message = 'Reply with exactly "line one\nline\ttwo — ünïcode" and nothing else'
    assert _bound_literal(message) == "line one\nline\ttwo — ünïcode"
    served = apply_exact_response_control({"response": "placeholder"}, message)
    assert served["response"] == "line one\nline\ttwo — ünïcode"


def test_unicode_payload_is_byte_exact() -> None:
    message = "Say exactly this and nothing else: héllo — wörld, tëst!"
    assert _bound_literal(message) == "héllo — wörld, tëst!"
    served = apply_exact_response_control({"response": "placeholder"}, message)
    assert served["response"] == "héllo — wörld, tëst!"


def test_interior_whitespace_is_never_collapsed() -> None:
    # Double spaces inside the payload are payload bytes; collapsing them is mutation.
    message = "Say exactly this and nothing else: keep  these   gaps"
    assert _bound_literal(message) == "keep  these   gaps"


def test_code_and_url_payloads_are_served_verbatim() -> None:
    for message, payload in [
        ("Output exactly this and nothing else: x = (a+b)*2; return x // 3", "x = (a+b)*2; return x // 3"),
        ("Return exactly this and nothing else: https://vool.dev/x?a=1&b=2#frag", "https://vool.dev/x?a=1&b=2#frag"),
    ]:
        assert _bound_literal(message) == payload, message
        served = apply_exact_response_control({"response": "placeholder"}, message)
        assert served["response"] == payload, message


def test_empty_payload_refuses_literal_mode_instead_of_serving_the_cue() -> None:
    # Nothing follows the delimiter: there is no payload to serve, and the cue text must
    # never become the answer.
    for message in (
        "Respond with exactly this and nothing else:",
        "Respond with exactly this and nothing else:   ",
        "reply with exactly:",
    ):
        assert _bound_literal(message) is None, message
        served = apply_exact_response_control({"response": "MODEL ANSWER"}, message)
        assert served["response"] == "MODEL ANSWER", message
        assert "response_control" not in served, message


def test_ambiguous_and_multiline_colon_payloads_fail_closed() -> None:
    # A colon payload continuing onto further lines is ambiguous against a following
    # sentence: the typed owner refuses, and the legacy rewriter may not step in.
    multiline = "say exactly this and nothing else: PAYLOAD follows\non a second line"
    assert _bound_literal(multiline) is None
    served = apply_exact_response_control({"response": "MODEL ANSWER"}, multiline)
    assert served["response"] == "MODEL ANSWER"


def test_adversarial_near_misses_bind_no_literal() -> None:
    for message in (
        "The output was fine and nothing else broke",  # prose predicate, not an instruction
        "explain exactly how the parser works",  # 'exactly' qualifies the explanation
        "Reply with exactly three bullets and nothing else",  # shape contract, not bytes
        "Return exactly the second line of notes.txt. Calculate 37 x 19.",  # executable demand
        "Respond with exactly this and nothing else broke: X",  # cue continues into a predicate
    ):
        assert _bound_literal(message) is None, message
        assert exact_response_target(message) == "", message


def test_ordinary_requests_never_enter_literal_mode() -> None:
    for message in (
        "What is the capital of France?",
        "Can you summarize the meeting notes for me?",
        "Write me a haiku about gravity.",
        "the output was fine and nothing else broke: seriously",
    ):
        contract = parse_raw_output_contract(message)
        assert contract is None or contract.exact_text is None, message
        served = apply_exact_response_control({"response": "MODEL ANSWER"}, message)
        assert served["response"] == "MODEL ANSWER", message


def test_typed_refusal_after_strict_cue_blocks_the_legacy_extractor() -> None:
    # A strict-output instruction the typed parser declined to bind must not be re-extracted
    # by the legacy regex with different bytes.
    message = "Respond with exactly this and nothing else:   "
    assert exact_response_target(message) == ""
