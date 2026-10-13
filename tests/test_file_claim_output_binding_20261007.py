import pytest

from core.agent_runtime.action_honesty_validator import completion_claim_kind, enforce_final_action_honesty


@pytest.mark.parametrize(
    "text",
    [
        "I'll create two files for you:",
        "I've created two files for you.",
        "notes.md is ready.",
        "I saved results.json for you.",
        "I’ll create two files for you:",
    ],
)
def test_unbacked_files_are_not_shipped(text):
    out = enforce_final_action_honesty(
        {"response": text}, user_input="Give me the files", session_id="additional-file-claims", source_context={}
    )
    assert out["response"] != text


@pytest.mark.parametrize(
    "text",
    [
        "The two files you uploaded contain meeting notes.",
        "If you want, I can create two files.",
        "I explained how to create two files.",
    ],
)
def test_context_or_offer_is_preserved(text):
    out = enforce_final_action_honesty(
        {"response": text}, user_input="Explain the files", session_id="additional-file-claims", source_context={}
    )
    assert out["response"] == text


def receipt(filename):
    return {
        "tool_name": "file.deliver",
        "status": "executed",
        "execution": {"executed": True, "ok": True},
        "observation": {"format": "md", "filename": filename},
    }


@pytest.mark.parametrize("reply", ["notes.md is ready.", "I saved notes.md for you."])
@pytest.mark.parametrize("written,kept", [("notes.md", True), ("other.md", False)])
def test_named_output_matches_actual_written_file(reply, written, kept):
    out = enforce_final_action_honesty(
        {"response": reply},
        user_input="Give me the file",
        session_id="claim-identity-additional",
        source_context={"tool_receipts": [receipt(written)]},
    )
    assert (out["response"] == reply) is kept
    if not kept:
        assert "nothing was generated" not in out["response"].lower()
        assert "did not create or save a file" not in out["response"].lower()


@pytest.mark.parametrize(
    "written,kept", [(["one.md"], False), (["one.md", "one.md"], False), (["one.md", "two.md"], True)]
)
def test_two_file_promise_needs_two_distinct_written_outputs(written, kept):
    reply = "I'll create two files for you:"
    out = enforce_final_action_honesty(
        {"response": reply},
        user_input="Give me both files",
        session_id="claim-count-additional",
        source_context={"tool_receipts": [receipt(x) for x in written]},
    )
    assert (out["response"] == reply) is kept
    if not kept:
        assert "nothing was generated" not in out["response"].lower()
        assert "did not create or save a file" not in out["response"].lower()


@pytest.mark.parametrize(
    "text",
    [
        "I'll create a guide about two files.",
        "If you want, I’ll create two files for you.",
        "I explained how to create two files.",
    ],
)
def test_educational_or_conditional_context_stays(text):
    out = enforce_final_action_honesty(
        {"response": text}, user_input="Explain the files", session_id="file-claim-context", source_context={}
    )
    assert out["response"] == text


def test_failed_writer_cannot_prove_a_named_output():
    failed = receipt("notes.md")
    failed["execution"]["ok"] = False
    reply = "notes.md is ready."
    out = enforce_final_action_honesty(
        {"response": reply},
        user_input="Give me the file",
        session_id="file-claim-failed-output",
        source_context={"tool_receipts": [failed]},
    )
    assert out["response"] != reply


def test_refusal_is_final_and_does_not_deny_an_unrelated_successful_write():
    context = {"tool_receipts": [receipt("other.md")]}
    out = enforce_final_action_honesty(
        {"response": "notes.md is ready."},
        user_input="Give me the file",
        session_id="file-claim-idempotence",
        source_context=context,
    )
    assert completion_claim_kind(out["response"]) == ""
    again = enforce_final_action_honesty(
        out, user_input="Give me the file", session_id="file-claim-idempotence", source_context=context
    )
    assert again["response"] == out["response"]
    assert "nothing was generated" not in out["response"].lower()


@pytest.mark.parametrize("count,kept", [("9" * 5000, False), ("0" * 5000 + "2", True)], ids=["oversized", "leading-zeroes"])
def test_large_count_text_stays_bounded_and_evidence_bound(count, kept):
    reply = "I'll create " + count + " files for you:"
    out = enforce_final_action_honesty(
        {"response": reply}, user_input="Give me files", session_id="large-file-count",
        source_context={"tool_receipts": [receipt("one.md"), receipt("two.md")]},
    )
    assert (out["response"] == reply) is kept
