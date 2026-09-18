"""The chat memory writer and every reader must open the same partition.

MEASURED ON THE LIVE STORE, 2026-08-18:

    memory_nodes    'vool_chat' 132   |  'vool' 1
    memory_blocks   'vool_chat'   3   |  'vool' 1
    block_read("user_profile") under the writer's id -> "...Name: Alex..."
    block_read("user_profile") under the readers' id -> None

`memory_blocks` is keyed PRIMARY KEY (block_name, agent_id) and `memory_nodes` is filtered by it,
so a default that disagrees with the writer's is not a preference -- it is a different store.

FOUR DEFAULTS FOR ONE ID. Every chat write passed `SEMANTIC_MEMORY_AGENT_ID` ("vool_chat")
explicitly, while `VoolMemory.__init__`, `core/memory_prompt_builder.py`,
`core/prompt_normalizer.py` and `core/web/api/runtime.py` each defaulted to a bare "vool" literal.
The operator's name was in the store the whole time; the readers opened an empty shelf and the lane
answered "I don't have a name saved for you."

There is one definition now, in the store module, and `context_retrieval` aliases it. An explicit
caller-supplied agent_id still wins -- only the DEFAULT moved.
"""

from __future__ import annotations

from core.context_retrieval import SEMANTIC_MEMORY_AGENT_ID
from core.vool_memory import DEFAULT_AGENT_ID, VoolMemory


def test_the_writer_and_the_store_default_agree() -> None:
    assert DEFAULT_AGENT_ID == SEMANTIC_MEMORY_AGENT_ID


def test_no_reader_carries_its_own_partition_literal() -> None:
    """A bare "vool" default anywhere is a fifth definition waiting to drift back apart."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    offenders = []
    for rel in (
        "core/vool_memory.py",
        "core/memory_prompt_builder.py",
        "core/prompt_normalizer.py",
        "core/web/api/runtime.py",
    ):
        body = (root / rel).read_text(encoding="utf-8")
        for line in body.splitlines():
            if "agent_id" not in line or line.lstrip().startswith("#"):
                continue
            if '"vool"' in line or "'vool'" in line:
                offenders.append(f"{rel}: {line.strip()}")

    assert not offenders, "a partition id is hardcoded again:\n" + "\n".join(offenders)


def test_a_default_write_is_readable_by_a_default_read(tmp_path) -> None:
    """The property that was false: seed with the default, read with the default, find it."""
    with VoolMemory(runtime_home=tmp_path) as writer:
        writer.block_write("user_profile", "Name: Loop")

    with VoolMemory(runtime_home=tmp_path) as reader:
        assert "Name: Loop" in str(reader.block_read("user_profile") or "")


def test_the_chat_writers_id_is_readable_by_a_default_read(tmp_path) -> None:
    """Seeded exactly as the chat lane writes -- explicit SEMANTIC_MEMORY_AGENT_ID -- and read by a
    caller that passes nothing. This is the live shape of the defect."""
    with VoolMemory(runtime_home=tmp_path, agent_id=SEMANTIC_MEMORY_AGENT_ID) as writer:
        writer.block_write("user_profile", "Name: Alex")

    with VoolMemory(runtime_home=tmp_path) as reader:
        assert "Name: Alex" in str(reader.block_read("user_profile") or "")


def test_an_explicit_partition_still_isolates(tmp_path) -> None:
    """Only the default moved. A caller that names a partition still gets that one alone, which is
    what keeps the id a real boundary rather than a decoration."""
    with VoolMemory(runtime_home=tmp_path, agent_id="someone_else") as other:
        other.block_write("user_profile", "Name: NotYou")

    with VoolMemory(runtime_home=tmp_path) as reader:
        assert "NotYou" not in str(reader.block_read("user_profile") or "")


def test_sabotage_restoring_the_old_default_hides_the_stored_name(tmp_path) -> None:
    """Revert one reader to the bare literal and the name becomes unreadable again."""
    with VoolMemory(runtime_home=tmp_path, agent_id=SEMANTIC_MEMORY_AGENT_ID) as writer:
        writer.block_write("user_profile", "Name: Alex")

    with VoolMemory(runtime_home=tmp_path, agent_id="vool") as reverted:
        assert reverted.block_read("user_profile") in (None, "")
