"""Documents must reach the selected model byte-faithfully -- and the document gate must be a
DOCUMENT policy, not the bug-report egress policy.

Ported from build/vool-working-mark-20260902 (d3eaa837) onto the canonical chat-owned document
architecture: the same laws, now pinned against `stage_document` (the ONE interceptor's server
half) and the bounded, relevance-ranked delivery of a document over its allowance.

STORAGE FIDELITY -- `stage_document` writes the exact bytes; the manifest records their sha256.

MODEL-CONSUMPTION FIDELITY -- what `apply_to_provider_messages` actually hands the selected model
for the turn: the real rendered text parts, captured and asserted to carry the required source
spans (beginning / middle / end), tabs, CR-LF, blank lines, indentation and Unicode exactly;
table rows a value can be computed from; the exact line that carries an intentional bug; and a
disclosure whenever anything is cut -- never silent omission. Over the allowance the model gets
EXACT windows: the first and the last always, the question's own windows in between, the omitted
line ranges stated inline -- never first-N truncation alone.

SCANNER AUDIT -- `scan_text` judges OUTBOUND bug reports (an email, an IP, a labelled value fail
it). Documents get their own policy, `scan_document_text`: credential FORMATS only, the documented
example keys allowed as placeholders, typed refusal, never a mutation of the source bytes.
"""

from __future__ import annotations

import hashlib

import pytest

from core import chat_attachments as ca
from core import runtime_paths
from core.chat_attachments import (
    MAX_TEXT_CHARS_PER_FILE,
    AttachmentRefused,
    apply_to_provider_messages,
    bind_to_turn,
    model_attachments_for_turn,
    read_staged_bytes,
    stage_attachment,
    stage_document,
)

SESSION = "openclaw:d0c0d0c0d0c0d0c0f1de"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


def _stage(name: str, text: str) -> dict:
    """A dropped/pasted document with its own name: the ONE interceptor's server half."""
    return stage_document(session_id=SESSION, data=text.encode("utf-8"), declared_name=name)


def _model_payload_for(docs: list[dict], user_text: str, turn: str = "turn-fidelity") -> str:
    """Bind the docs to a turn and capture the EXACT provider text parts the model would get."""
    bind_to_turn(session_id=SESSION, turn_id=turn, attachment_ids=[d["id"] for d in docs])
    entries = model_attachments_for_turn(session_id=SESSION, turn_id=turn, question=user_text)
    messages, _receipts = apply_to_provider_messages([{"role": "user", "content": user_text}], entries, supports_images=False)
    user = messages[-1]
    assert isinstance(user["content"], list) and len(user["content"]) >= 2, "no document parts bound"
    return "\n".join(str(p.get("text") or "") for p in user["content"] if isinstance(p, dict))


# ------------------------------------------- 1–5. structured families reach the model whole

PYTHON_DOC = '''# budget report generator — v2
def total(rows):
    t = 0
    for r in rows:
        t += r[1]
    return t

def buggy_window(rows):
    # intentional off-by-one: range(1, len(rows)) skips the first row
    return [rows[i][0] for i in range(1, len(rows))]

if __name__ == "__main__":
\tprint(total([("q1", 100), ("q2", 250), ("q3", 40)]))
'''

SQL_DOC = "SELECT region, SUM(amount) AS total\nFROM sales\nGROUP BY region\nORDER BY total DESC;\n"
YAML_DOC = "service:\n  name: ledger\n  ports:\n    - 8080\n    - 8443\ncache: {enabled: true}\n"
TABLE_CSV = "region,amount\nnorth,1250\nsouth,875\neast,499\nwest,1320\n"
JS_DOC = "const rates = { eu: 1, us: 1.07 };\nfunction convert(usd) { return usd * rates.us; }\n"
SHELL_DOC = "#!/bin/sh\nset -eu\nexport DEPLOY_TARGET=stage\n/usr/bin/rsync -a ./build/ target:/srv/app/\n"
MD_TABLE = "| region | amount |\n|--------|--------|\n| north  | 1250   |\n| south  | 875    |\n| east   | 499    |\n| west   | 1320   |\n"


def test_code_and_table_documents_reach_the_model_with_their_source_spans() -> None:
    docs = [
        _stage("pasted.py", PYTHON_DOC),
        _stage("pasted.sql", SQL_DOC),
        _stage("pasted.yaml", YAML_DOC),
        _stage("pasted.csv", TABLE_CSV),
        _stage("pasted.js", JS_DOC),
        _stage("pasted.sh", SHELL_DOC),
        _stage("pasted.md", MD_TABLE),
    ]
    assert [d["media_type"] for d in docs] == ["text/x-python", "application/sql", "application/yaml", "text/csv", "text/javascript", "text/x-sh", "text/markdown"]
    payload = _model_payload_for(docs, "Which line holds the off-by-one, and what is north+south?")
    assert "range(1, len(rows))" in payload, "the buggy line did not reach the model"
    assert "\tprint(total([(" in payload, "the tab-indented line was not preserved"
    assert "SUM(amount) AS total" in payload and "ORDER BY total DESC;" in payload
    assert "- 8443" in payload and "cache: {enabled: true}" in payload
    assert "north,1250" in payload and "west,1320" in payload, "table rows missing from the payload"
    assert "rates.us" in payload and "rsync -a ./build/" in payload
    assert "| east   | 499    |" in payload, "markdown table row lost"
    assert "Which line holds the off-by-one" in payload, "the user's own words must ride along"
    assert "[Pasted document: pasted.py (text/x-python" in payload
    # No duplicate prompt content: each document's text enters the payload exactly once.
    assert payload.count(PYTHON_DOC) == 1 and payload.count(TABLE_CSV) == 1 and payload.count("[Pasted document: pasted.py") == 1


def test_indentation_tabs_crlf_and_blank_lines_survive_to_the_payload() -> None:
    raw = "start line\r\n\r\n\tindented with a tab\r\n    four spaces keep their count\r\n\r\n末尾 unicode ✅\r\n"
    doc = _stage("pasted.txt", raw)
    payload = _model_payload_for([doc], "summarise the end")
    assert "start line\r\n" in payload, "CR-LF was normalised"
    assert "\tindented with a tab\r\n" in payload
    assert "\r\n\r\n" in payload, "blank lines were collapsed"
    assert "末尾 unicode ✅" in payload


def test_beginning_middle_and_end_evidence_is_all_present_within_the_allowance() -> None:
    lines = [f"event {i:04d} ok" for i in range(400)]
    lines[0] = "BEGINNING: boot marker 2026-09-02"
    lines[200] = "MIDDLE: the retry storm started here"
    lines[-1] = "END: final shutdown complete"
    doc = _stage("pasted.log", "\n".join(lines) + "\n")
    payload = _model_payload_for([doc], "what happened first, in the middle, and last?")
    assert "BEGINNING: boot marker" in payload and "MIDDLE: the retry storm" in payload and "END: final shutdown complete" in payload
    assert "omitted" not in payload, "a document within its allowance is delivered whole"


# ------------------------------------ over the allowance: ranked exact windows, never head-only


def _oversize_log() -> tuple[str, int]:
    """~360,000 characters (3× the per-file allowance) with facts at the head, the middle and the end."""
    lines = [f"2026-09-02T10:{(i // 3600) % 60:02d}:{(i // 60) % 60:02d}Z INFO worker heartbeat {i:05d} ok, queue depth {i % 17}" for i in range(6000)]
    lines[0] = "BEGINNING: process boot, build 0.5.0, node atlas-3"
    lines[2999] = "2026-09-02T11:30:00Z ERROR worker ORDER-7731 failed with ECONNRESET while calling the payments gateway"
    lines[3000] = "2026-09-02T11:30:30Z INFO worker ORDER-7731 retry 1 succeeded, shipment booked on carrier route R-4471"
    lines[-1] = "END: final shutdown complete, 6000 events flushed"
    text = "\n".join(lines) + "\n"
    assert len(text) > 2 * MAX_TEXT_CHARS_PER_FILE
    return text, len(lines)


def test_a_document_over_the_allowance_delivers_beginning_middle_and_end_by_relevance() -> None:
    text, total_lines = _oversize_log()
    doc = _stage("pasted.log", text)
    payload = _model_payload_for([doc], "What happened to ORDER-7731 with the payments gateway, which carrier route was booked, and how did the process start and end?")
    assert "BEGINNING: process boot" in payload, "the first window is always delivered"
    assert "END: final shutdown complete" in payload, "the last window is always delivered"
    assert "ORDER-7731 failed with ECONNRESET" in payload and "carrier route R-4471" in payload, "the question's own windows were not ranked in"
    assert "omitted (" in payload and "characters not shown" in payload, "an omitted range must be stated inline"
    assert "over the allowance" in payload and "exact windows" in payload, "the header must disclose the selection"
    delivered = sum(len(p) for p in payload.split("\n")) - len("What happened")
    assert delivered <= MAX_TEXT_CHARS_PER_FILE + 4000, "delivery must stay within the allowance plus its headers"
    # The selection record names the ranges, so a receipt can say what the model saw.
    entries = model_attachments_for_turn(session_id=SESSION, turn_id="turn-fidelity", question="ORDER-7731 payments gateway carrier route")
    selection = entries[0]["selection"]
    assert selection["included_ranges"][0][0] == 1 and selection["included_ranges"][-1][1] == total_lines
    assert any(a <= 3000 <= b for a, b in selection["included_ranges"]), selection["included_ranges"]
    assert selection["omitted_ranges"], "nothing was recorded as omitted although the document was cut"
    assert selection["delivered_chars"] < selection["total_chars"] == len(text)
    # Every delivered character is the document's own: no window is rewritten.
    for a, b in selection["included_ranges"]:
        window = "\n".join(text.split("\n")[a - 1 : b])
        assert window in payload, f"window {a}-{b} was not delivered verbatim"


def test_without_a_question_the_ranking_is_deterministic_and_still_frames_the_document() -> None:
    text, _total = _oversize_log()
    first = ca.select_document_text(text, budget=MAX_TEXT_CHARS_PER_FILE, question="")
    second = ca.select_document_text(text, budget=MAX_TEXT_CHARS_PER_FILE, question="")
    assert first == second, "the selection must be a pure function of (text, budget, question)"
    rendered, truncated, selection = first
    assert truncated is True and rendered.startswith("BEGINNING: process boot") and "END: final shutdown complete" in rendered
    assert len(rendered) <= MAX_TEXT_CHARS_PER_FILE
    first_window = selection["included_ranges"][0]
    assert first_window[0] == 1 and 1 <= first_window[1] <= ca.CHUNK_LINES


def test_a_picked_text_file_over_the_allowance_keeps_its_head_cut_and_says_so() -> None:
    doc = stage_attachment(session_id=SESSION, declared_name="big.txt", declared_type="text/plain", data=("x" * 99 + "\n").encode() * ((MAX_TEXT_CHARS_PER_FILE // 100) + 50))
    payload = _model_payload_for([doc], "what is in the file?")
    assert "truncated to the first" in payload, "a picked file's cut must still be disclosed"


# --------------------------------------- 6. retry/resume: identity and usable content


def test_restaging_the_same_text_keeps_content_identity() -> None:
    first = _stage("pasted.py", PYTHON_DOC)
    again = _stage("pasted.py", PYTHON_DOC)
    assert again["sha256"] == first["sha256"] == hashlib.sha256(PYTHON_DOC.encode()).hexdigest()
    assert again["size_bytes"] == first["size_bytes"]
    payload = _model_payload_for([again], "what does the doc say?")
    assert "range(1, len(rows))" in payload


# --------------------------------------------- 7. the audit: document policy ≠ egress policy


def test_harmless_placeholders_and_source_code_are_not_falsely_rejected() -> None:
    harmless = [
        ("config.txt", "API_KEY=YOUR_API_KEY\nendpoint=https://api.example.com/v1\n"),
        ("code.py", 'password = os.environ.get("DB_PASSWORD")\nemail = user.email\nhost = "127.0.0.1"\n'),
        ("log.txt", "2026-09-02T10:00:00 INFO request from 10.0.0.4 took 12ms\n" * 5),
        ("notes.md", "# notes\ncontact: me@example.com see docs\n"),
        ("example.txt", "aws access key id = AKIAIOSFODNN7EXAMPLE (the documented placeholder)\n"),
    ]
    for name, text in harmless:
        record = _stage(name, text)
        assert record["sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()
    # The same policy at the picked-file door.
    picked = stage_attachment(session_id=SESSION, declared_name="creds.txt", declared_type="text/plain", data=b"password=hunter2-super-secret\n")
    assert picked["kind"] == "text"


def test_real_credential_shaped_material_is_refused_typed_and_never_mutated() -> None:
    real = [
        ("keys.txt", "AKIA4T7NCXKDTL5TQX3P\n" + "filler line\n" * 50),
        ("id_rsa.txt", "-----BEGIN RSA PRIVATE KEY-----\nMIIBOAIBAAJg\n" + "filler\n" * 50),
        ("tokens.txt", "github token: ghp_RsKoYtUoBmQzWvNxJdCaFqPwLeHyTrUi23AB\n" + "filler\n" * 50),
        ("env.txt", "OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz0123456789\n" + "filler\n" * 50),
    ]
    for name, text in real:
        with pytest.raises(AttachmentRefused) as excinfo:
            _stage(name, text)
        assert excinfo.value.code == "secret_detected" and excinfo.value.http_status == 422
        assert "nothing was saved" in excinfo.value.message
    with pytest.raises(AttachmentRefused) as picked:
        stage_attachment(session_id=SESSION, declared_name="keys.txt", declared_type="text/plain", data=b"AKIA4T7NCXKDTL5TQX3P\n")
    assert picked.value.code == "secret_detected"
    assert not list(ca.stage_dir().glob("*.bin")), "a refused document left bytes behind"
    clean = _stage("clean.txt", "nothing secret here\n")
    _record, raw = read_staged_bytes(session_id=SESSION, attachment_id=clean["id"])
    assert raw.decode("utf-8") == "nothing secret here\n"
