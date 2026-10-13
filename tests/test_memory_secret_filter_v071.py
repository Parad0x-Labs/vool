"""v0.7.1 memory secret filter: card PINs, card numbers and passwords never reach memory.

THE MEASURED DEFECT (main dbaeb28)
----------------------------------
"My card PIN is 4433." and "My bank card number is 4111 1111 1111 1111." stored through
``store_turn`` were written in plain text to ``vool_memory.db`` and served back in the capsule
for every card question; "my password is ..." the same. The persistence redactor only knew
``label: value`` / ``label=value`` shapes and had no card-number rule.

THE CONTRACT UNDER TEST
-----------------------
* The redactor masks a value only when its clause names it a PIN, passcode, security code,
  password or card number, or the digits are a grouped Luhn-valid card number.
* A chat turn carrying one is told plainly it was not saved, and no model, memory file or log
  receives the value; any other demand in the same message is still answered.
* A later ask finds no record of the value (NO RECORD = REFUSE): nothing to recall.
* A home written before these rules keeps its bytes (nothing deleted or rewritten), every
  serving door withholds the value, and forget still erases it from disk.
"""
from __future__ import annotations

import contextlib
import sqlite3
from unittest import mock

import pytest

from core.secret_redaction import contains_secret, redact_secrets, redactable_personal_kinds
from tests.test_overnight_source_structure import _recall, _store, source_env

PIN_TURN = "My card PIN is 8264."
CARD_TURN = "My bank card number is 4539 1488 0343 6467."
EMAIL_LOGIN_TURN = "My email password is Qx7-plover-91."
FILTERED_VALUES = ("8264", "4539 1488 0343 6467", "4539148803436467", "Qx7-plover-91")
PLAIN_TURN = "My sister lives in Lisbon."
LAST4_TURN = "The last 4 digits on my bank card are 7781."


@pytest.mark.parametrize("text,masked,kinds", [
    ("My card PIN is 4433.", "My card PIN is [redacted].", ("pin",)),
    ("my pin code was 0912", "my pin code was [redacted]", ("pin",)),
    ("PIN: 551203", "PIN: [redacted]", ("pin",)),
    ("the CVV is 123", "the CVV is [redacted]", ("pin",)),
    ("My bank card number is 4111 1111 1111 1111.", "My bank card number is [redacted-card-number].", ("card_number",)),
    ("visa 4111-1111-1111-1111 exp 03/28", "visa [redacted-card-number] exp 03/28", ("card_number",)),
    ("use 4111 1111 1111 1111 for it", "use [redacted-card-number] for it", ("card_number",)),
    ("my credit card is 1234567812345678", "my credit card is [redacted-card-number]", ("card_number",)),
    ("my password is hunter22.", "my password is [redacted].", ("password",)),
    ("The password for my email is Sunsh1ne!", "The password for my email is [redacted]", ("password",)),
    ("wifi passcode is sunflower", "wifi passcode is [redacted]", ("password",)),
])
def test_named_personal_secrets_are_masked(text, masked, kinds):
    assert redact_secrets(text) == masked
    assert contains_secret(text)
    assert redactable_personal_kinds(text) == kinds


@pytest.mark.parametrize("text", [
    "My padlock number is 4433.",
    "The last 4 digits on my bank card are 7781.",
    "My bank card expires in March 2028.",
    "my password is the same as my email",
    "the password is required",
    "your password was changed yesterday",
    "Call me on +44 7700 900123",
    "Order 1234567890123456 shipped",
    "ts=1760000000000",
    "set pin 1 high, version 1.2.3",
    "the meeting is at 15:00 on 2026-10-12",
    "Price 1299.99 for 4 units",
    "invoice 4111 1111 1111 1112 is due",
    # A label ending its line does not own the next line's first word (bug-report text whose
    # value was already masked and the marker removed).
    "admin password is  \nsession cookie  \n",
])
def test_ordinary_numbers_and_words_stay(text):
    assert redact_secrets(text) == text
    assert not contains_secret(text)
    assert redactable_personal_kinds(text) == ()


def test_masked_text_is_stable():
    once = redact_secrets(f"{PIN_TURN} {CARD_TURN} {EMAIL_LOGIN_TURN}")
    assert redact_secrets(once) == once
    assert not any(value in once for value in FILTERED_VALUES)


# ======================================================================
# real memory store + recall (the path a chat turn's memory write takes)
# ======================================================================


def _home_bytes(home) -> bytes:
    """Every byte VOOL wrote under the home, SQLite pages and WAL included."""
    return b"".join(path.read_bytes() for path in sorted(home.rglob("*")) if path.is_file())


def _store_story(home, chat):
    for day, text in enumerate((PIN_TURN, CARD_TURN, EMAIL_LOGIN_TURN, PLAIN_TURN, LAST4_TURN)):
        _store(home, chat, text, "", stated=1780000000 + day * 86400)


def test_fresh_home_never_writes_or_serves_the_values(source_env):
    _store_story(source_env, "secret-fresh")
    on_disk = _home_bytes(source_env)
    for value in FILTERED_VALUES:
        assert value.encode() not in on_disk, value
    for question in ("What's my card PIN?", "What is my full bank card number?", "What is my email password?"):
        capsule = _recall(source_env, "secret-fresh", question)
        assert not any(value in capsule for value in FILTERED_VALUES), capsule
    assert "Lisbon" in _recall(source_env, "secret-fresh", "Where does my sister live?")
    assert "7781" in _recall(source_env, "secret-fresh", "What are the last 4 digits of my bank card?")


@contextlib.contextmanager
def _pre_v071_writer():
    """The write path exactly as v0.7.0 shipped it: every redaction rule except the new
    plain-word card, PIN and password rules, which did not exist yet."""
    with mock.patch("core.secret_redaction._redact_personal_secrets", side_effect=lambda value: value), \
         mock.patch("core.secret_redaction._redactable_personal_spans", return_value=[]):
        yield


def _db_rows_with(home, value: str) -> int:
    count = 0
    for db_path in home.rglob("*.db"):
        conn = sqlite3.connect(db_path)
        try:
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            for table in tables:
                try:
                    rows = conn.execute(f'SELECT * FROM "{table}"').fetchall()
                except sqlite3.DatabaseError:
                    continue
                count += sum(1 for row in rows if any(isinstance(cell, str) and value in cell for cell in row))
        finally:
            conn.close()
    return count


def test_upgraded_home_keeps_its_bytes_and_serves_no_value(source_env):
    with _pre_v071_writer():
        _store_story(source_env, "secret-upgrade")
    # The old build really stored them (otherwise this test proves nothing).
    assert _db_rows_with(source_env, "4539 1488 0343 6467") > 0
    before = _db_rows_with(source_env, "8264"), _db_rows_with(source_env, "Qx7-plover-91")
    assert all(before)

    for question in ("What's my card PIN?", "What is my full bank card number?", "What is my email password?",
                     "What do you remember about my bank card?"):
        capsule = _recall(source_env, "secret-upgrade", question)
        assert not any(value in capsule for value in FILTERED_VALUES), capsule
    # Ordinary memory in the same home is still served.
    assert "Lisbon" in _recall(source_env, "secret-upgrade", "Where does my sister live?")
    assert "7781" in _recall(source_env, "secret-upgrade", "What are the last 4 digits of my bank card?")
    # Withholding is read-only: the stored rows are exactly where they were.
    assert (_db_rows_with(source_env, "8264"), _db_rows_with(source_env, "Qx7-plover-91")) == before


def test_upgraded_home_serving_doors_withhold_the_value(source_env):
    from core.memory_receipts import receipts_for_scope
    from core.vool_memory import VoolMemory

    with _pre_v071_writer():
        _store_story(source_env, "secret-doors")
    served = []
    with VoolMemory(runtime_home=source_env) as mem:
        served.extend(node.content for node in mem.recent_nodes(limit=50))
        served.extend(node.content for node in mem.session_nodes(session_id=None, limit=50))
        served.extend(hit[0].body for hit in mem.occurrence_search("card PIN password", chat_scope="secret-doors"))
        served.extend(str(receipt) for receipt in receipts_for_scope(mem, "secret-doors"))
    assert served, "the doors served nothing, so this test proves nothing"
    joined = "\n".join(served)
    assert not any(value in joined for value in FILTERED_VALUES), joined
    assert "[redacted" in joined


def test_forget_still_erases_a_withheld_value_from_disk(source_env):
    from core.persistent_memory import maybe_handle_memory_command

    with _pre_v071_writer():
        _store_story(source_env, "secret-forget")
    assert _db_rows_with(source_env, "Qx7-plover-91") > 0
    handled, reply = maybe_handle_memory_command(
        "forget Qx7-plover-91", session_id="secret-forget",
        source_context={"chat_id": "secret-forget", "runtime_home": str(source_env), "client_turn_id": "forget-turn"},
    )
    assert handled and "Forget applied" in reply, reply
    assert _db_rows_with(source_env, "Qx7-plover-91") == 0
    assert "Lisbon" in _recall(source_env, "secret-forget", "Where does my sister live?")


# ======================================================================
# a real chat turn: the user is told, the model never sees the value
# ======================================================================


@pytest.fixture
def harness(request):
    from tests.test_turn_attempt_chain import _Harness

    h = _Harness(f"sess-v071-{request.node.name[:44]}")
    try:
        yield h
    finally:
        h.close()


def _chat_turn(harness, text):
    """One turn through VoolAgent.run_once with the R1e model stand-in; every request the
    model lane is handed is captured so the test can prove what the model saw."""
    from tests.test_secret_demand_preservation_r1g2 import _ENTROPY_STAND_IN, _REMOTE_CONTEXT, _model_stand_in
    from tests.test_turn_attempt_chain import _incident_mocks

    seen: list[str] = []
    with _incident_mocks(), _model_stand_in(harness.agent, _ENTROPY_STAND_IN):
        resolve = harness.agent.memory_router.resolve
        original = resolve.side_effect

        def _capture(*args, **kwargs):
            seen.append(repr(args) + repr(kwargs))
            return original(*args, **kwargs)

        resolve.side_effect = _capture
        result = harness.agent.run_once(
            text, source_context=dict(_REMOTE_CONTEXT), session_id_override=harness.session_id
        )
    return result, seen, _ENTROPY_STAND_IN


def test_a_pin_statement_is_answered_with_not_saved(harness):
    result, seen, _ = _chat_turn(harness, PIN_TURN)
    answer = str(result.get("response") or "")
    assert "didn't save your PIN" in answer, answer
    assert "8264" not in answer
    assert not any("8264" in request for request in seen)


def test_a_password_beside_a_question_is_not_saved_and_the_question_is_answered(harness):
    result, seen, stand_in = _chat_turn(
        harness, "My email password is Qx7-plover-91. Also explain entropy briefly."
    )
    answer = str(result.get("response") or "")
    assert "didn't save your password" in answer, answer
    assert stand_in in answer, f"the question beside the secret was dropped: {answer[:200]!r}"
    assert "Qx7-plover-91" not in answer
    assert seen, "the question never reached the model lane"
    assert not any("Qx7-plover-91" in request for request in seen), "the model saw the password"
    from core.runtime_paths import active_vool_home

    assert b"Qx7-plover-91" not in _home_bytes(active_vool_home()), "the password reached a file"
