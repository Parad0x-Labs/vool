"""Every context item must carry the provenance the provider seal requires.

On 2026-07-29 an audit turn on the owner's real project failed on **every** ranked lane —
`ollama-local:qwen3:8b`, `ollama-local:qwen3:14b`, and the OpenRouter free lane — each in about
0.1s, before any network call. The user was told only *"I couldn't get a live model response in this
run"*. The persisted ledger carried the real reason on all three:

    reason: "selected context source 6 is missing content_hash"

`_validate_selected_sources` (`core/provider_invocation_gateway.py`) required six fields on every
selected context item and raised on the first gap. Because the selected sources are a property of
the **request**, the same gap failed every provider identically — which is exactly why it looked
like "all models are broken" rather than "one context item is under-described".

Eight of eleven `ContextItem` producers in `core/tiered_context_loader.py` never set
`content_hash`. Three of those are reachable on an ordinary chat turn, which is why the failure
looked intermittent: a fresh `Hey` retrieves no memory and survives, while a turn that retrieves a
memory, a heuristic, or a prior-session summary dies.

The prompt budget was NOT the cause and was never close: the same ledger rows record
`available_prompt_tokens: 22136` with nothing dropped.

These tests pin the producers. The seal's own degrade-instead-of-raise behaviour is pinned in
`tests/test_provider_invocation_gateway_direct.py`.
"""
from __future__ import annotations

import hashlib

from core import tiered_context_loader


def _policy():
    """The scope policy the loader passes through to each producer."""

    from core.context_scope import ContextScopePolicy

    return ContextScopePolicy(chat_id="chat-1", project_id="proj-1")


# The six fields the provider seal requires of every selected source.
REQUIRED = ("item_id", "source_type", "scope", "source_id", "content_hash", "reason")


def _sealed_fields(item) -> dict[str, str]:
    """The view the seal takes of an item: the record, flattened the way the gateway reads it."""

    record = item.to_record(included=True, reason=item.include_reason or "included")
    metadata = dict(record.get("metadata") or {})
    provenance = dict(record.get("provenance") or {})
    flat: dict[str, str] = {}
    for key in REQUIRED:
        value = (
            record.get(key)
            or metadata.get(key)
            or provenance.get(key)
            # the gateway's documented last resort for source_id
            or (record.get("item_id") if key == "source_id" else "")
        )
        flat[key] = str(value or "").strip()
    return flat


# --------------------------------------------------------------------------------------
# The three producers reachable on an ordinary chat turn
# --------------------------------------------------------------------------------------


def test_persistent_memory_items_are_sealable(monkeypatch) -> None:
    """`runtime-memory-*` — the producer behind the measured failure."""

    monkeypatch.setattr(
        tiered_context_loader,
        "search_relevant_memory",
        lambda *a, **k: [
            {
                "text": "the owner's project is a static marketplace site",
                "category": "fact",
                "score": 0.9,
                "confidence": 0.8,
                "scope": "chat",
                "source": "runtime_memory",
                "source_id": "memory-1",
                "status": "active",
            }
        ],
    )
    items = tiered_context_loader._persistent_memory_items(
        "what is this project", [], session_id="s", policy=_policy()
    )
    assert items, "fixture must produce an item or this test proves nothing"
    for item in items:
        missing = [key for key, value in _sealed_fields(item).items() if not value]
        assert not missing, f"runtime_memory item is unsealable, missing {missing}"


def test_user_heuristic_items_are_sealable(monkeypatch) -> None:
    monkeypatch.setattr(
        tiered_context_loader,
        "search_user_heuristics",
        lambda *a, **k: [
            {
                "text": "prefers short answers",
                "category": "style",
                "signal": "brevity",
                "score": 0.7,
                "confidence": 0.6,
                "mentions": 3,
                "source_id": "heuristic-1",
                "status": "active",
            }
        ],
    )
    items = tiered_context_loader._user_heuristic_items(
        "anything", [], session_id="s", policy=_policy()
    )
    assert items
    for item in items:
        missing = [key for key, value in _sealed_fields(item).items() if not value]
        assert not missing, f"user_heuristic item is unsealable, missing {missing}"


def test_session_summary_items_are_sealable(monkeypatch) -> None:
    """This one lacked BOTH `content_hash` and `source_id`."""

    monkeypatch.setattr(
        tiered_context_loader,
        "search_session_summaries",
        lambda *a, **k: [
            {
                "summary": "earlier the owner audited the landing page",
                "session_id": "openclaw:abcdef0123456789abcd",
                "project_id": "proj_1",
                "score": 0.5,
                "turn_count": 4,
            }
        ],
    )
    # A prior-session summary is cross-chat by definition, so the producer returns [] unless the
    # scope policy permits it — and `allow_cross_chat` is derived from real cross-chat grants whose
    # source namespace must load. Standing that whole machinery up would test the grant system, not
    # the provenance stamp. The producer reads only this one flag and hands the policy to the
    # already-stubbed search, so a stand-in is the honest scope here.
    class _CrossChatPolicy:
        allow_cross_chat = True
        chat_id = "chat-1"
        project_id = "proj-1"

    items = tiered_context_loader._session_summary_items(
        "anything", [], session_id="s", policy=_CrossChatPolicy()
    )
    assert items, "fixture must produce an item or this test proves nothing"
    for item in items:
        missing = [key for key, value in _sealed_fields(item).items() if not value]
        assert not missing, f"session_summary item is unsealable, missing {missing}"


# --------------------------------------------------------------------------------------
# The hash must describe the content, not merely exist
# --------------------------------------------------------------------------------------


def test_the_hash_is_of_the_content_that_was_actually_included(monkeypatch) -> None:
    """A hash that does not match the included text would be provenance theatre."""

    text = "the owner's project is a static marketplace site"
    monkeypatch.setattr(
        tiered_context_loader,
        "search_relevant_memory",
        lambda *a, **k: [
            {"text": text, "category": "fact", "score": 0.9, "confidence": 0.8,
             "scope": "chat", "source": "runtime_memory", "source_id": "memory-1"}
        ],
    )
    item = tiered_context_loader._persistent_memory_items(
        "q", [], session_id="s", policy=_policy()
    )[0]
    assert item.metadata["content_hash"] == hashlib.sha256(item.content.encode()).hexdigest()


def test_two_different_memories_do_not_share_a_hash(monkeypatch) -> None:
    entries = [
        {"text": "first fact", "category": "fact", "score": 0.9, "confidence": 0.8,
         "scope": "chat", "source": "runtime_memory", "source_id": "m1"},
        {"text": "second fact", "category": "fact", "score": 0.8, "confidence": 0.8,
         "scope": "chat", "source": "runtime_memory", "source_id": "m2"},
    ]
    monkeypatch.setattr(tiered_context_loader, "search_relevant_memory", lambda *a, **k: entries)
    items = tiered_context_loader._persistent_memory_items(
        "q", [], session_id="s", policy=_policy()
    )
    hashes = {item.metadata["content_hash"] for item in items}
    assert len(hashes) == len(items) == 2


# --------------------------------------------------------------------------------------
# The whole class, not the three that happened to bite
# --------------------------------------------------------------------------------------


def test_no_context_item_producer_omits_content_hash() -> None:
    """A source-level sweep, so a NEW producer cannot reintroduce the outage.

    Asserted on the source because most producers need a live store to exercise; the property under
    test is "every construction site stamps the field", which is exactly a source property.
    """

    import pathlib
    import re

    lines = (
        pathlib.Path(__file__).resolve().parents[1] / "core" / "tiered_context_loader.py"
    ).read_text(encoding="utf-8").splitlines()

    offenders: list[str] = []
    for index, line in enumerate(lines):
        if "ContextItem(" not in line:
            continue
        window = "\n".join(lines[max(0, index - 35): min(len(lines), index + 42)])
        if "content_hash" in window:
            continue
        identity = next(
            (m.group(1) for l in lines[index + 1: index + 8] if (m := re.search(r"item_id=(.+?),", l))),
            f"line {index + 1}",
        )
        offenders.append(f"{index + 1}: {identity}")

    assert not offenders, (
        "every ContextItem must carry a content_hash or it can fail a turn on every provider; "
        f"unstamped: {offenders}"
    )
