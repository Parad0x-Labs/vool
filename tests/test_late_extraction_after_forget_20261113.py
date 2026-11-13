"""A late extraction writes nothing it learned from words a forget has since erased.

Found by the v0.8 Intel package smoke (2026-11-13, CI on 271e90da): the person said "Remember that my sister Ilona's
favourite tree is the silver birch.", then "Forget what I told you about Ilona's favourite tree." The forget
completed, and still "silver birch" was in vool_memory.db afterwards. The remember turn's background extraction had
waited out its quiet window and was still in its model call when the forget ran; the model answered after it, with a
paraphrase ("Sister Ilona's favorite tree: silver birch") that carries none of the forgotten words, so the fenced
learned-block write let it through and the learned blocks and fact nodes kept the erased fact.

Law (``core.fact_extractor.FactExtractor._run``): the extraction's source decides. If the words it read now carry a
forgotten token, from a completed forget or one still under way, it writes nothing. The check and the writes hold
the forget fence together, so a forget that is running finishes first and none can land between them. An extraction
from words no forget touched writes as before.

All names and sentences are invented for this contract.
"""
from __future__ import annotations

import json
import threading
import time

import pytest

from tests.test_agent_turns_teach_the_owner_nothing_20261112 import OWNER_CHAT, _context, env  # fixtures
from tests.test_question_date_time_leg_20261002 import _hash_backend  # fixture

pytestmark = pytest.mark.usefixtures("_hash_backend")

REMEMBERED = "Remember that my sister Ilona's favourite tree is the silver birch."
FORGET = "Forget what I told you about Ilona's favourite tree."
#: What the model answered on the package smoke: the fact, in words the forget did not name.
PARAPHRASES = (
    "Sister Ilona's favorite tree: silver birch",
    "Ilona (sister) loves the silver birch",
)
TOKEN = "ilona's favourite tree"


def _say(text: str) -> str:
    from core.persistent_memory import maybe_handle_memory_command

    handled, reply = maybe_handle_memory_command(text, session_id=OWNER_CHAT, source_context=_context(OWNER_CHAT))
    assert handled, text
    return reply


def _source() -> list[dict]:
    return [{"role": "user", "content": REMEMBERED}, {"role": "assistant", "content": "Locked in. I'll remember that."}]


def _extract(home, content: str) -> list:
    """One extraction of the remember turn, whose model answers ``content``."""
    from core.context_retrieval import SEMANTIC_MEMORY_AGENT_ID
    from core.fact_extractor import FactExtractor
    from core.vool_memory import VoolMemory

    memory = VoolMemory(runtime_home=str(home), agent_id=SEMANTIC_MEMORY_AGENT_ID)
    try:
        extractor = FactExtractor(memory=memory, ollama_url="http://127.0.0.1:9", session_id=OWNER_CHAT)
        extractor._call_model = lambda _conversation: json.dumps(
            {"facts": [{"action": "ADD", "block": "user_profile", "content": content}]})
        return extractor.run_sync(_source())
    finally:
        memory.close()


def _memory_db_carries(home, words: bytes) -> bool:
    folder = home / "data" / "memory"
    return any(words in path.read_bytes() for path in folder.glob("vool_memory.db*") if path.is_file())


def _learned(home) -> str:
    from core.context_retrieval import SEMANTIC_MEMORY_AGENT_ID
    from core.vool_memory import VoolMemory

    memory = VoolMemory(runtime_home=str(home), agent_id=SEMANTIC_MEMORY_AGENT_ID)
    try:
        return memory.block_read("user_profile") or ""
    finally:
        memory.close()


def _remembered() -> None:
    from core.context_namespace import ensure_chat_namespace

    ensure_chat_namespace(OWNER_CHAT, grant_current_receipts=False)
    assert "Locked in" in _say(REMEMBERED)


@pytest.mark.parametrize("paraphrase", PARAPHRASES)
def test_an_extraction_finishing_after_the_forget_writes_nothing(env, paraphrase):
    home = env["home"]
    _remembered()
    assert _say(FORGET).startswith("Forget applied"), "the forget itself"

    assert _extract(home, paraphrase) == []

    assert "silver birch" not in _learned(home)
    assert not _memory_db_carries(home, b"silver birch")


def test_without_the_forget_the_same_extraction_writes(env):
    home = env["home"]
    _remembered()

    applied = _extract(home, PARAPHRASES[0])

    assert [fact.content for fact in applied] == [PARAPHRASES[0]]
    assert PARAPHRASES[0] in _learned(home)


def test_a_forget_of_other_words_leaves_the_extraction_alone(env):
    home = env["home"]
    _remembered()
    assert "Locked in" in _say("Remember that my gate code is 4417.")
    assert _say("Forget my gate code is 4417.").startswith("Forget applied")

    applied = _extract(home, PARAPHRASES[0])

    assert [fact.content for fact in applied] == [PARAPHRASES[0]]


def test_a_forget_still_under_way_writes_nothing(env):
    from core.memory import revocation

    home = env["home"]
    _remembered()
    operation = revocation.begin(OWNER_CHAT, TOKEN, home)
    try:
        assert _extract(home, PARAPHRASES[0]) == []
    finally:
        revocation.complete(operation, home)

    assert "silver birch" not in _learned(home)


def test_a_forget_that_starts_first_finishes_before_the_extraction_decides(env):
    """The forget holds the fence for its whole run; an extraction arriving meanwhile waits, then sees it."""
    from core.memory import revocation

    home = env["home"]
    _remembered()
    started, release = threading.Event(), threading.Event()
    result: dict = {}

    def forget_in_progress() -> None:
        with revocation.serialized(home):
            started.set()
            release.wait(timeout=30)
            operation = revocation.begin(OWNER_CHAT, TOKEN, home)
            revocation.complete(operation, home)

    def extraction() -> None:
        result["applied"] = _extract(home, PARAPHRASES[0])

    forget = threading.Thread(target=forget_in_progress)
    forget.start()
    assert started.wait(timeout=30)
    extracting = threading.Thread(target=extraction)
    extracting.start()
    time.sleep(0.5)
    assert extracting.is_alive(), "the extraction waits for the running forget"
    release.set()
    forget.join(timeout=30)
    extracting.join(timeout=60)

    assert result.get("applied") == []
    assert "silver birch" not in _learned(home)


# ---------------------------------------------------------------------------------------------------------------
# What was learned BEFORE the forget goes with it, reworded or not.
#
# Law (``core.vool_memory.VoolMemory._erase_learned_from``): the extractor records, for every block line and fact
# node it writes, the words that one fact came from (the clauses of the person's turns that share the most content
# words with it; the turns the model cites when none do). A forget removes every line and node whose own source
# carries the forgotten words, and nothing learned from other words, even in the same extraction or the same message.
# Lines and fact nodes from before that record existed carry no source: they go only when they hold the forgotten
# fact itself (``_carries_forgotten_fact``); a line about the same subject that holds another fact stays.


def _extract_from(home, messages: list[dict], content, turns=None) -> list:
    """One extraction of ``messages`` whose model answers ``content`` (one fact or a list), citing ``turns``."""
    from core.context_retrieval import SEMANTIC_MEMORY_AGENT_ID
    from core.fact_extractor import FactExtractor
    from core.vool_memory import VoolMemory

    contents = [content] if isinstance(content, str) else list(content)
    cited = list(turns) if turns is not None else [None] * len(contents)
    facts = [{"action": "ADD", "block": "user_profile", "content": text, **({"turns": cite} if cite else {})}
             for text, cite in zip(contents, cited, strict=True)]
    memory = VoolMemory(runtime_home=str(home), agent_id=SEMANTIC_MEMORY_AGENT_ID)
    try:
        extractor = FactExtractor(memory=memory, ollama_url="http://127.0.0.1:9", session_id=OWNER_CHAT)
        extractor._call_model = lambda _conversation: json.dumps({"facts": facts})
        return extractor.run_sync(messages)
    finally:
        memory.close()


def _turn(user: str) -> list[dict]:
    return [{"role": "user", "content": user}, {"role": "assistant", "content": "Locked in. I'll remember that."}]


def _rows(home, sql: str) -> list:
    import sqlite3

    conn = sqlite3.connect(home / "data" / "memory" / "vool_memory.db")
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _fact_nodes(home) -> list[str]:
    return [row[0] for row in _rows(home, "SELECT content FROM memory_nodes")]


GATE = "Remember that my gate code is 4417."
GATE_LEARNED = "Gate code: 4417"


@pytest.mark.parametrize("paraphrase", PARAPHRASES)
def test_a_reworded_line_learned_before_the_forget_goes_with_it(env, paraphrase):
    from core.persistent_memory import append_conversation_event

    home = env["home"]
    _remembered()
    append_conversation_event(session_id=OWNER_CHAT, user_input=REMEMBERED, assistant_output="Locked in.",
                              source_context=_context(OWNER_CHAT))
    assert [fact.content for fact in _extract(home, paraphrase)] == [paraphrase]
    assert "Locked in" in _say(GATE)
    assert [fact.content for fact in _extract_from(home, _turn(GATE), GATE_LEARNED)] == [GATE_LEARNED]
    assert paraphrase in _learned(home) and any("silver birch" in node for node in _fact_nodes(home))

    assert _say(FORGET).startswith("Forget applied"), "the forget is verified"

    assert "silver birch" not in _learned(home)
    assert not any("silver birch" in node for node in _fact_nodes(home))
    assert not _memory_db_carries(home, b"silver birch")
    # What was learned from other words stays, with its source.
    assert GATE_LEARNED in _learned(home)
    assert any(GATE_LEARNED in node for node in _fact_nodes(home))
    assert [row[0] for row in _rows(home, "SELECT line FROM memory_learned_items WHERE line != ''")] == [GATE_LEARNED]


#: The same subject, another fact (and another subject, the same kind of fact): never the forgotten one.
SAME_SUBJECT_OTHER_FACTS = ("Sister Ilona's favourite flower: tulip", "Favourite tree: oak",
                            "Sister Ilona works as a nurse", "Ilona's birthday: May 3")


def test_an_older_learned_line_without_a_source_goes_only_when_it_holds_the_forgotten_fact(env):
    """Written before learned lines recorded their source (block lines are shared by every chat)."""
    from core.context_retrieval import SEMANTIC_MEMORY_AGENT_ID
    from core.persistent_memory import append_conversation_event
    from core.vool_memory import VoolMemory

    home = env["home"]
    _remembered()
    append_conversation_event(session_id=OWNER_CHAT, user_input=REMEMBERED, assistant_output="Locked in.",
                              source_context=_context(OWNER_CHAT))
    memory = VoolMemory(runtime_home=str(home), agent_id=SEMANTIC_MEMORY_AGENT_ID)
    try:
        for line in (*PARAPHRASES, *SAME_SUBJECT_OTHER_FACTS, "Prefers short answers"):
            memory.block_append("user_profile", line)
    finally:
        memory.close()
    assert not _rows(home, "SELECT 1 FROM memory_learned_items")

    assert _say(FORGET).startswith("Forget applied")

    learned = _learned(home)
    assert "silver birch" not in learned
    for kept in (*SAME_SUBJECT_OTHER_FACTS, "Prefers short answers"):
        assert kept in learned, (kept, learned)
    assert not _memory_db_carries(home, b"silver birch")


def test_facts_learned_together_keep_their_own_sources(env):
    """One extraction read both turns; forgetting one fact keeps the other (and a fact placed only by the
    model's citation follows the turn it cited)."""
    from core.persistent_memory import append_conversation_event

    home = env["home"]
    _remembered()
    assert "Locked in" in _say(GATE)
    append_conversation_event(session_id=OWNER_CHAT, user_input=REMEMBERED, assistant_output="Locked in.",
                              source_context=_context(OWNER_CHAT))
    messages = [*_turn(REMEMBERED), *_turn(GATE)]
    applied = _extract_from(home, messages, [PARAPHRASES[0], GATE_LEARNED, "Has family who love nature",
                                             "Keeps a keypad entry at home"], turns=[None, None, [1], [2]])
    assert len(applied) == 4

    assert _say(FORGET).startswith("Forget applied")

    learned = _learned(home)
    assert "silver birch" not in learned and "Has family who love nature" not in learned
    assert GATE_LEARNED in learned and "Keeps a keypad entry at home" in learned
    assert any(GATE_LEARNED in node for node in _fact_nodes(home))
    assert not _memory_db_carries(home, b"silver birch")


def test_two_facts_in_one_message_keep_their_own_sources(env):
    from core.persistent_memory import append_conversation_event

    home = env["home"]
    both = "Remember that my sister Ilona's favourite tree is the silver birch and my gate code is 4417."
    _remembered()
    append_conversation_event(session_id=OWNER_CHAT, user_input=both, assistant_output="Locked in.",
                              source_context=_context(OWNER_CHAT))
    assert len(_extract_from(home, _turn(both), [PARAPHRASES[0], GATE_LEARNED])) == 2

    assert _say(FORGET).startswith("Forget applied")

    learned = _learned(home)
    assert "silver birch" not in learned
    assert GATE_LEARNED in learned


def test_a_late_extraction_still_writes_the_facts_of_other_words(env):
    """A forget lands while the model answers: only the fact from the forgotten words is dropped."""
    home = env["home"]
    _remembered()
    assert _say(FORGET).startswith("Forget applied")

    applied = _extract_from(home, [*_turn(REMEMBERED), *_turn(GATE)], [PARAPHRASES[0], GATE_LEARNED])

    assert [fact.content for fact in applied] == [GATE_LEARNED]
    assert "silver birch" not in _learned(home) and GATE_LEARNED in _learned(home)


def test_a_deleted_chat_keeps_no_copy_of_its_words_as_a_learned_source(env):
    from core.memory.entries import delete_conversation_session

    home = env["home"]
    _remembered()
    _extract(home, PARAPHRASES[0])
    assert _rows(home, "SELECT 1 FROM memory_learned_runs")

    delete_conversation_session(OWNER_CHAT)

    assert not _rows(home, "SELECT 1 FROM memory_learned_runs")
    assert not _rows(home, "SELECT 1 FROM memory_learned_items")


def test_a_whole_erase_leaves_no_learned_line_or_source(env):
    home = env["home"]
    _remembered()
    _extract(home, PARAPHRASES[0])
    assert "Locked in" in _say(GATE)
    _extract_from(home, _turn(GATE), GATE_LEARNED)

    reply = _say("forget everything you know about me")

    assert reply.startswith("Forget applied"), reply
    assert not _learned(home).strip()
    assert not _rows(home, "SELECT 1 FROM memory_learned_runs")
    assert not _rows(home, "SELECT 1 FROM memory_learned_items")
    # Only the forget list keeps the words (memory_revocations), as every forget does.
    for table in ("memory_blocks", "memory_nodes", "memory_fts", "memory_learned_runs", "memory_learned_items"):
        assert not any("silver birch" in repr(row) or "4417" in repr(row)
                       for row in _rows(home, f"SELECT * FROM {table}")), table
