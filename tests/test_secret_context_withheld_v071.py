"""v0.7.1: a PIN, card number or password typed in chat never reaches a model on a later turn.

THE MEASURED DEFECT (Set A head 486b5c5, served daemon, fresh home)
------------------------------------------------------------------
"Remember that my bank card PIN is 4821." was answered "I didn't save your PIN", and memory held
only "[redacted]". But the turn's exchange was admitted as a turn-context page (CAS chunk and
``turn_context_admissions.title``) with the PIN as typed, and on the next turn ("What is my bank
card PIN?") that page was compiled into the answer model's prompt as Relevant Context. The
browser also re-sends every earlier message as typed, and the fact-extraction model lane read
the raw conversation. A cloud model would have received the PIN.

THE CONTRACT UNDER TEST
-----------------------
* Over the real ``/api/chat`` door, with the history the chat page sends, no request any model
  receives carries the value, and no turn-context page holds it.
* The question asked beside a secret is still answered by the model.
* A page admitted before this rule keeps its bytes; every turn-context read door serves it masked.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from unittest import mock
from urllib.request import Request, urlopen

from tests._authorship_served_rig import REPO_ROOT, ServedDaemon, seed_in_home
from tests.test_c18_language_parity_served import _SEED, CountingProvider
from tests.test_turn_context_authority import PRINCIPAL, _scope, turn_ctx_env

LOCAL = "secret-context-local-1.5b"
PAID = "secret-context-paid-1.5b"
MODEL_REPLY = "MODEL-ANSWER-ENTROPY"
PIN = "4821"
EMAIL_LOGIN = "Qx7-plover-91"
TURNS = (
    "Remember that my bank card PIN is 4821.",
    "What is my bank card PIN?",
    "My email password is Qx7-plover-91. Also explain entropy briefly.",
)


@contextmanager
def _served_home():
    home = Path(tempfile.mkdtemp(prefix="secret-context-served-"))
    local = CountingProvider({LOCAL: MODEL_REPLY})
    paid = CountingProvider({PAID: "PAID"})
    daemon = ServedDaemon(home, env_extra={
        "VOOL_MODEL_LOAD_FLOOR_GB": "0",
        "VOOL_DISABLE_WEB": "1",
        "OLLAMA_HOST": local.base_url,
        "VOOL_OLLAMA_URL": local.base_url,
        "VOOL_RAW_OLLAMA_API_URL": local.base_url,
        "VOOL_OLLAMA_CHAT_URL": f"{local.base_url}/api/chat",
    })
    local.__enter__()
    paid.__enter__()
    try:
        daemon.start(timeout=300)
        seed_in_home(home, _SEED.format(root=REPO_ROOT, local_base=local.base_url,
                                        paid_base=paid.base_url, local=LOCAL, paid=PAID))
        time.sleep(3.0)
        local.reset()
        yield home, daemon, local, paid
    finally:
        daemon.stop()
        local.__exit__(None, None, None)
        paid.__exit__(None, None, None)


def _chat_like_the_page(daemon, texts) -> list[str]:
    """POST /api/chat the way the chat page does: every earlier message is re-sent as typed."""
    messages: list[dict] = []
    answers: list[str] = []
    for text in texts:
        messages.append({"role": "user", "content": text})
        body = {"messages": messages, "stream": False, "session_id": "secret-context-chat", "model": LOCAL}
        request = Request(f"{daemon.base_url}/api/chat", data=json.dumps(body).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=180) as response:
            reply = json.loads(response.read().decode("utf-8"))
        answer = str((reply.get("message") or {}).get("content") or reply.get("response") or "")
        answers.append(answer)
        messages.append({"role": "assistant", "content": answer})
    return answers


def test_no_model_request_and_no_turn_context_page_carries_a_typed_secret():
    with _served_home() as (home, daemon, local, paid):
        answers = _chat_like_the_page(daemon, TURNS)
        time.sleep(2.0)  # the fact-extraction lane runs after the reply
        calls = list(local.calls) + list(paid.calls)

    assert "didn't save your PIN" in answers[0], answers[0]
    assert PIN not in answers[1], answers[1]
    assert "didn't save your password" in answers[2], answers[2]
    assert MODEL_REPLY in answers[2], f"the question beside the password was dropped: {answers[2]!r}"

    prompts = [call for call in calls if "/api/chat" in str(call.get("path")) or "/v1/" in str(call.get("path"))]
    assert len(prompts) >= 3, "the model lanes never ran, so this test proves nothing"
    for call in prompts:
        wire = json.dumps(call)
        assert PIN not in wire and EMAIL_LOGIN not in wire, f"{call.get('model')} received a typed secret"

    data = home / "data"
    for chunk in (data / "cas_chunks").rglob("*"):
        if chunk.is_file():
            raw = chunk.read_bytes()
            assert f"PIN is {PIN}".encode() not in raw and EMAIL_LOGIN.encode() not in raw, chunk
    conn = sqlite3.connect(next(data.glob("vool_web0_v2*.db")))
    try:
        titles = [row[0] for row in conn.execute("SELECT title FROM turn_context_admissions")]
    finally:
        conn.close()
    assert titles, "no turn-context page was admitted, so this test proves nothing"
    assert not any(PIN in title or EMAIL_LOGIN in title for title in titles), titles


def test_a_page_admitted_before_the_rule_keeps_its_bytes_and_is_served_masked(turn_ctx_env):
    from core.turn_context import admit_turn_context, compile_turn_context, inspect_admissions, recall_page

    raw = "[user]\nRemember that my bank card PIN is 4821.\n[/user]\n[assistant]\nOkay.\n[/assistant]"
    with mock.patch("core.turn_context._secrets_withheld", side_effect=lambda text: text):
        admitted = admit_turn_context(raw, scope=_scope("chat-old"), title="Remember that my bank card PIN is 4821.")
    plain = admit_turn_context("[user]\nMy sister lives in Lisbon.\n[/user]", scope=_scope("chat-old"),
                               title="My sister lives in Lisbon.")
    stored = b"".join(path.read_bytes() for path in turn_ctx_env["home"].rglob("*") if path.is_file())
    assert f"PIN is {PIN}".encode() in stored, "the old writer did not store the PIN, so this test proves nothing"

    compiled = compile_turn_context(PRINCIPAL, "chat-old", query_text="bank card PIN")
    served = [item.content + item.title for item in compiled.items]
    served += [entry["title"] for entry in compiled.exclusions + compiled.truncations]
    recalled = recall_page(PRINCIPAL, admitted["admission_id"])
    listed = inspect_admissions(PRINCIPAL, session_id="chat-old", include_content=True)
    joined = json.dumps([served, recalled, listed])
    assert PIN not in joined, joined
    assert "my bank card PIN is [redacted]" in recalled["content"]
    assert recall_page(PRINCIPAL, plain["admission_id"])["content"] == "[user]\nMy sister lives in Lisbon.\n[/user]"
    # Withholding is read-only: the old page's bytes are where they were.
    after = b"".join(path.read_bytes() for path in turn_ctx_env["home"].rglob("*") if path.is_file())
    assert f"PIN is {PIN}".encode() in after


def test_the_fact_extraction_lane_never_reads_a_typed_secret():
    from core.fact_extractor import FactExtractor

    conversation = FactExtractor.__new__(FactExtractor)._format_conversation([
        {"role": "user", "content": "Remember that my bank card PIN is 4821."},
        {"role": "user", "content": "My email password is Qx7-plover-91 and my sister lives in Lisbon."},
    ])
    assert PIN not in conversation and EMAIL_LOGIN not in conversation, conversation
    assert "Lisbon" in conversation
