"""File-claim identity and runtime-ledger regression cases."""
import uuid

import pytest

from core.agent_runtime.action_honesty_validator import enforce_final_action_honesty


def receipt(name, ok=True, tool="file.deliver", fmt=None, key="filename"):
    return {"tool_name": tool, "status": "executed" if ok else "failed", "execution": {"executed": True, "ok": ok}, "observation": {key: name, "format": fmt or name.rsplit(".", 1)[-1]}}


def R(*names):
    return [receipt(name) for name in names]

CASES = [
 ("named_match", "I saved notes.md for you.", "receipts", R("notes.md"), True),
 ("named_mismatch", "I saved notes.md for you.", "receipts", R("other.md"), False),
 ("two_names_one_written", "I saved notes.md and data.json for you.", "receipts", R("notes.md"), False),
 ("three_names_list_one_written", "I created report.md, summary.md and data.csv.", "receipts", R("report.md"), False),
 ("passive_adverb", "notes.md was successfully saved.", "none", [], False),
 ("passive_has_now_been", "notes.md has now been saved.", "none", [], False),
 ("passive_backticked", "`notes.md` has been saved.", "none", [], False),
 ("passive_bold", "**notes.md** is ready.", "none", [], False),
 ("passive_plain", "notes.md is ready.", "none", [], False),
 ("past_count_three_one_written", "I've created three files: a.md, b.md and c.md.", "receipts", R("a.md"), False),
 ("past_count_digit", "I've created 3 files for you.", "receipts", R("a.md"), False),
 ("promise_two_two_written", "I'll create two files for you:", "receipts", R("a.md", "b.md"), True),
 ("promise_two_dup", "I'll create two files for you:", "receipts", R("a.md", "a.md"), False),
 ("promise_two_samebase_diffdir", "I'll create two files for you:", "receipts", [receipt("x/a.md", key="path"), receipt("y/a.md", key="path")], True),
 ("promise_two_one_failed", "I'll create two files for you:", "receipts", [receipt("a.md"), receipt("b.md", ok=False)], False),
 ("promise_zeropad", "I'll create 002 files for you:", "receipts", R("a.md", "b.md"), True),
 ("promise_huge", "I'll create " + "9" * 5000 + " files for you:", "receipts", R("a.md"), False),
 ("promise_19digits_padded_small", "I'll create " + "0" * 30 + "2 files for you:", "receipts", R("a.md", "b.md"), True),
 ("promise_arabic_digit", "I'll create ٢ files for you:", "receipts", R("a.md", "b.md"), True),
 ("conditional_offer", "If you want, I can create two files.", "none", [], True),
 ("educational", "I explained how to create two files.", "none", [], True),
 ("user_uploaded", "The two files you uploaded contain meeting notes.", "none", [], True),
 ("name_case_differs", "I saved Notes.MD for you.", "receipts", R("notes.md"), True),
 ("name_with_space", "I saved my notes.md for you.", "receipts", R("my notes.md"), True),
 ("name_fullpath_claim", "I saved exports/notes.md for you.", "receipts", R("exports/notes.md"), True),
 ("fabricated_backtick_wrote", "I wrote `notes.md` for you.", "none", [], False),
 ("fabricated_backtick_saved", "I saved `report.csv` with the totals.", "none", [], False),
 ("fabricated_bold_saved", "I saved **report.csv** with the totals.", "none", [], False),
 ("fabricated_plain_saved", "I saved report.csv with the totals.", "none", [], False),
 ("fabricated_quoted_saved", "I saved \"report.csv\" with the totals.", "none", [], False),
 ("backtick_named_mismatch", "I saved `notes.md` for you.", "receipts", R("other.md"), False),
 ("facts_named_match", "I saved notes.md for you.", "facts", ["notes.md"], True),
 ("facts_promise_two", "I'll create two files for you:", "facts", ["a.md", "b.md"], True),
 ("facts_promise_one_generic", "I'll create a file with the list.", "facts", ["list.txt"], True),
 ("facts_generic_saved", "I saved the file for you.", "facts", ["list.txt"], True),
]

@pytest.mark.parametrize("cid,reply,kind,evidence,keep", CASES, ids=[row[0] for row in CASES])
def test_file_claim_matches_current_turn_outputs(cid, reply, kind, evidence, keep):
    session = "file-evidence-" + uuid.uuid4().hex
    if kind == "receipts":
        context = {"tool_receipts": evidence}
    elif kind == "facts":
        from core.execution_truth import note_runtime_event
        context = {"turn_key": "turn-" + uuid.uuid4().hex}
        for i, name in enumerate(evidence):
            note_runtime_event(context, event_type="tool_executed", session_id=session,
                details={"tool_name": "workspace.write_file", "status": "executed", "receipt_id": str(i), "path": name})
    else:
        context = {}
    output = enforce_final_action_honesty({"response": reply}, user_input="Give me the files", session_id=session, source_context=context)
    assert (output["response"] == reply) is keep
    repeated = enforce_final_action_honesty(dict(output), user_input="Give me the files", session_id=session, source_context=context)
    assert repeated["response"] == output["response"]


def test_canonical_fact_keeps_output_identity_but_not_input_or_content():
    from core.execution_truth import classify_runtime_event
    facts = classify_runtime_event("tool_executed", {"tool_name": "workspace.write_file", "observation": {"path": "notes.md", "format": "md", "content": "private body"}, "arguments": {"path": "other.md", "content": "private input"}})
    assert facts[0].detail["observation"] == {"path": "notes.md", "format": "md"}
    assert "arguments" not in facts[0].detail


@pytest.mark.parametrize("reply", ["I saved other notes.md for you.", "I saved `other notes.md` for you."])
def test_shared_suffix_is_not_the_named_file(reply):
    output = enforce_final_action_honesty({"response": reply}, user_input="Give me the file", session_id="different-spaced-name", source_context={"tool_receipts": [receipt("my notes.md")]})
    assert output["response"] != reply


@pytest.mark.parametrize("filename,content", [("notes.md", "hello"), ("notes.txt", "hello"), ("data.csv", "a,b"), ("results.json", "{}")])
def test_real_workspace_write_keeps_its_evidence_bound_reply(make_agent, tmp_path, filename, content):
    session = "real-file-write-" + uuid.uuid4().hex
    context = {"surface": "openclaw", "session_id": session, "workspace": str(tmp_path), "workspace_root": str(tmp_path), "operating_mode": "auto"}
    payload = make_agent().run_once(f"create {filename} containing {content}", session_id_override=session, source_context=context)
    result = payload.get("result", payload)
    assert (tmp_path / filename).read_text() == content
    assert "cannot verify" not in str(result.get("response", "")).lower()
    assert "wrote" in str(result.get("response", "")).lower()


@pytest.mark.parametrize("reply", [
    "I created " + "a/" * 20000 + "x.py",
    "I saved " + "a." * 20000 + "md",
    "I saved " + "a-" * 30000 + ".md",
    "I saved " + ", ".join(f"f{i}.md" for i in range(3000)) + ".",
], ids=["long-path", "long-dotted", "long-hyphen", "many-names"])
def test_long_file_claim_inputs_finish_within_the_worker_budget(reply, tmp_path):
    from tests.test_action_honesty_final_validator import _run_bounded_claim_worker
    script = (
        "from core.agent_runtime.action_honesty_validator import enforce_final_action_honesty; "
        f"enforce_final_action_honesty({{'response': {reply!r}}}, user_input='Give me a file', session_id='bounded-input', source_context={{}})"
    )
    _run_bounded_claim_worker(script, tmp_path)


@pytest.mark.parametrize("kind", ["receipt", "canonical-fact"])
def test_one_output_with_path_and_filename_aliases_does_not_count_as_two(kind):
    session = "alias-count-" + uuid.uuid4().hex
    observation = {"path": "exports/notes.md", "filename": "notes.md", "format": "md"}
    if kind == "receipt":
        context = {"tool_receipts": [{**receipt("notes.md"), "observation": observation}]}
    else:
        from core.execution_truth import note_runtime_event
        context = {"turn_key": uuid.uuid4().hex}
        note_runtime_event(context, event_type="tool_executed", session_id=session,
                           details={"tool_name": "workspace.write_file", "status": "executed", "observation": observation})
    reply = "I've created two files for you."
    output = enforce_final_action_honesty({"response": reply}, user_input="Give me two files", session_id=session, source_context=context)
    assert output["response"] != reply
