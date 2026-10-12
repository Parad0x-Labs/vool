"""Displayed completion claims retain their output evidence obligations."""
import uuid

import pytest

from core.agent_runtime.action_honesty_validator import enforce_final_action_honesty


def receipt(path):
    return {"tool_name": "file.deliver", "status": "executed", "execution": {"executed": True, "ok": True},
            "observation": {"path": path, "format": path.rsplit(".", 1)[-1]}}


@pytest.mark.parametrize("reply,paths,kept", [
    ("I saved [notes.md](sandbox:/mnt/data/notes.md) for you.", [], False),
    ("I saved [notes.md](sandbox:/mnt/data/notes.md) for you.", ["notes.md"], True),
    ("I saved *report.csv* with totals.", [], False),
    ("I saved *report.csv* with totals.", ["report.csv"], True),
    ("Files created:\n- `a.md`\n- `b.md`", ["a.md"], False),
    ("Files created:\n- `a.md`\n- `b.md`", ["a.md", "b.md"], True),
    ("Files saved:\n1. a.md\n2. b.md\n\nI did not save c.md.", ["a.md", "b.md"], True),
    ("I've created seven files.", ["a.md"], False),
    ("I've created a dozen files.", ["a.md"], False),
    ("Both files are saved.", ["a.md"], False),
    ("Both files are saved.", ["a.md", "b.md"], True),
    ("I'll create two files for you:", ["./a.md", "a.md"], False),
    ("I'll create two files for you:", ["x/../a.md", "a.md"], False),
    ("I'll create two files for you:", ["x/a.md", "y/a.md"], True),
    ("I saved notes.md, but data.json failed to save.", ["notes.md"], True),
    ("I saved notes.md, but data.json failed to save.", ["other.md"], False),
    ("I saved notes.md; data.json failed to save.", ["notes.md"], True),
    ("I saved notes.md and data.json.", ["notes.md"], False),
])
def test_completion_binds_displayed_outputs(reply, paths, kept):
    context = {"tool_receipts": [receipt(path) for path in paths]}
    kwargs = dict(user_input="Give me the files", session_id="display-binding-" + uuid.uuid4().hex,
                  source_context=context)
    result = enforce_final_action_honesty({"response": reply}, **kwargs)
    assert (result["response"] == reply) is kept
    assert enforce_final_action_honesty(dict(result), **kwargs)["response"] == result["response"]


@pytest.mark.parametrize("count,total", [("seven", 7), ("a dozen", 12), ("twelve", 12), ("Both", 2)])
def test_counted_claim_has_exact_distinct_evidence(count, total):
    text = f"I've created {count.lower()} files." if count != "Both" else "Both files are saved."
    paths = [f"f{i}.md" for i in range(total)]
    result = enforce_final_action_honesty({"response": text}, user_input="x", session_id=uuid.uuid4().hex,
        source_context={"tool_receipts": [receipt(path) for path in paths]})
    assert result["response"] == text
