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
