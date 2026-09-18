from __future__ import annotations

import pytest

from core.context_namespace import ensure_chat_namespace
from core.context_scope import ContextAccessPolicy
from core.memory.entries import (
    add_memory_fact,
    forget_memory,
    keyword_tokens_filtered,
    recency_score,
    recent_conversation_events,
    replace_name_memory,
    search_relevant_memory,
    summarize_memory,
)
from core.memory.files import (
    conversation_log_path,
    memory_entries_path,
    memory_path,
    operator_dense_profile_path,
    session_summaries_path,
    user_heuristics_path,
)
from core.runtime_paths import configure_runtime_home


@pytest.fixture(autouse=True)
def isolated_memory_home(tmp_path):
    configure_runtime_home(tmp_path / "runtime-home")
    memory_path().parent.mkdir(parents=True, exist_ok=True)
    for path in (
        conversation_log_path(),
        memory_entries_path(),
        session_summaries_path(),
        user_heuristics_path(),
        operator_dense_profile_path(),
    ):
        if path.exists():
            path.unlink()
    memory_path().write_text(
        "# VOOL Persistent Memory\n\n"
        "## Identity\n\n"
        "- **My name**: VOOL\n"
        "- **Owner's name**: unknown\n\n"
        "## Privacy Pact\n\n"
        "- Not set yet.\n\n"
        "## Learned Knowledge\n\n"
        "<!-- New memories append below -->\n",
        encoding="utf-8",
    )
    yield
    configure_runtime_home(None)


def test_memory_entries_roundtrip_and_forget() -> None:
    ensure_chat_namespace("s1")
    assert add_memory_fact("Operator uses Python and official docs.", category="fact", session_id="s1") is True
    assert add_memory_fact("Operator uses Python and official docs.", category="fact", session_id="s1") is False

    summary = "\n".join(summarize_memory(chat_id="s1", limit=8)).lower()
    assert "python" in summary
    hits = search_relevant_memory(
        "python docs",
        access_policy=ContextAccessPolicy.for_request(
            session_id="s1",
            source_context={"surface": "local"},
        ),
        topic_hints=["python"],
        limit=4,
    )
    assert hits

    removed = forget_memory("python", chat_id="s1")
    assert removed == 1
    assert "python" not in "\n".join(
        summarize_memory(chat_id="s1", limit=8)
    ).lower()


def test_name_replacement_supersedes_old_profile_memory() -> None:
    from core.context_namespace import grant_context_import

    ensure_chat_namespace("s2")
    grant_context_import(
        "s2",
        scope="user_profile",
        source_id="profile:confirmed",
    )
    assert add_memory_fact(
        "Operator name is Pedro.",
        category="name",
        session_id="s2",
        scope="user_profile",
    ) is True
    assert replace_name_memory("Operator name is SLS.", chat_id="s2") == 1

    active = "\n".join(summarize_memory(chat_id="s2", limit=20))
    assert "SLS" in active
    assert "Pedro" not in active


def test_recent_conversation_events_and_scoring_helpers() -> None:
    conversation_log_path().write_text(
        '{"session_id":"alpha","user":"one","assistant":"a"}\n'
        '{"session_id":"beta","user":"two","assistant":"b"}\n'
        '{"session_id":"alpha","user":"three","assistant":"c"}\n',
        encoding="utf-8",
    )

    events = recent_conversation_events("alpha", limit=2)
    assert [row["user"] for row in events] == ["one", "three"]
    assert keyword_tokens_filtered("build a brutally honest python telegram bot")[:2]
    assert recency_score("") == 0.35


def test_list_conversation_sessions_groups_by_session_newest_first() -> None:
    from core.memory.entries import list_conversation_sessions

    conversation_log_path().write_text(
        '{"ts":"2026-07-14T10:00:00Z","session_id":"openclaw:aaaa","user":"first question about drives","assistant":"a"}\n'
        '{"ts":"2026-07-14T10:05:00Z","session_id":"openclaw:aaaa","user":"follow up","assistant":"b"}\n'
        '{"ts":"2026-07-14T11:00:00Z","session_id":"openclaw:bbbb","user":"second thread opener","assistant":"c"}\n',
        encoding="utf-8",
    )

    sessions = list_conversation_sessions(limit=10)
    # Newest-active first for transcript-backed sessions. Empty server-created
    # namespaces may also be present.
    ids = [row["session_id"] for row in sessions]
    assert "openclaw:bbbb" in ids
    assert "openclaw:aaaa" in ids
    assert ids.index("openclaw:bbbb") < ids.index("openclaw:aaaa")
    alpha = next(
        row for row in sessions if row["session_id"] == "openclaw:aaaa"
    )
    assert alpha["title"] == "first question about drives"  # first user message is the title
    assert alpha["turn_count"] == 2
    assert alpha["updated_at"] == "2026-07-14T10:05:00Z"
    assert alpha["archived"] is False


def test_session_meta_rename_and_archive_reflected_in_list() -> None:
    from core.memory.entries import list_conversation_sessions, set_session_meta

    conversation_log_path().write_text(
        '{"ts":"2026-07-14T10:00:00Z","session_id":"openclaw:aaaa","user":"first msg","assistant":"a"}\n'
        '{"ts":"2026-07-14T11:00:00Z","session_id":"openclaw:bbbb","user":"other thread","assistant":"b"}\n',
        encoding="utf-8",
    )
    before = {row["session_id"]: row for row in list_conversation_sessions()}
    assert before["openclaw:aaaa"]["title"] == "first msg"
    assert before["openclaw:aaaa"]["archived"] is False

    ensure_chat_namespace("openclaw:aaaa")
    ensure_chat_namespace("openclaw:bbbb")
    set_session_meta("openclaw:aaaa", title="My renamed chat")
    set_session_meta("openclaw:bbbb", archived=True)

    after = {row["session_id"]: row for row in list_conversation_sessions()}
    assert after["openclaw:aaaa"]["title"] == "My renamed chat"  # custom title wins over first message
    assert after["openclaw:aaaa"]["archived"] is False
    assert after["openclaw:bbbb"]["archived"] is True

    # A blank title clears the override and falls back to the first user message.
    set_session_meta("openclaw:aaaa", title="   ")
    reverted = {row["session_id"]: row for row in list_conversation_sessions()}
    assert reverted["openclaw:aaaa"]["title"] == "first msg"


def test_session_emoji_set_clear_and_surfaced_in_list() -> None:
    from core.memory.entries import list_conversation_sessions, set_session_meta

    conversation_log_path().write_text(
        '{"ts":"2026-07-14T10:00:00Z","session_id":"openclaw:aaaa","user":"first msg","assistant":"a"}\n',
        encoding="utf-8",
    )
    # A chat starts with no emoji marker.
    assert {r["session_id"]: r for r in list_conversation_sessions()}["openclaw:aaaa"]["emoji"] == ""

    # Setting an emoji surfaces it in the sidebar list and leaves the title intact.
    entry = set_session_meta("openclaw:aaaa", emoji="🧠")
    assert entry["emoji"] == "🧠"
    row = {r["session_id"]: r for r in list_conversation_sessions()}["openclaw:aaaa"]
    assert row["emoji"] == "🧠" and row["title"] == "first msg"

    # A blank emoji clears the marker (title/archived untouched).
    set_session_meta("openclaw:aaaa", emoji="")
    assert {r["session_id"]: r for r in list_conversation_sessions()}["openclaw:aaaa"]["emoji"] == ""

    # An emoji-only meta on a chat with no transcript still appears in the sidebar.
    set_session_meta("openclaw:cccc", emoji="🚀")
    only = {r["session_id"]: r for r in list_conversation_sessions()}
    assert "openclaw:cccc" in only and only["openclaw:cccc"]["emoji"] == "🚀"


def test_session_color_set_normalized_and_cleared() -> None:
    from core.memory.entries import list_conversation_sessions, set_session_meta

    conversation_log_path().write_text(
        '{"ts":"2026-07-14T10:00:00Z","session_id":"openclaw:aaaa","user":"first msg","assistant":"a"}\n',
        encoding="utf-8",
    )
    assert {r["session_id"]: r for r in list_conversation_sessions()}["openclaw:aaaa"]["color"] == ""

    # A valid #rrggbb is stored lowercase; title stays intact.
    entry = set_session_meta("openclaw:aaaa", color="#3E63DD")
    assert entry["color"] == "#3e63dd"
    row = {r["session_id"]: r for r in list_conversation_sessions()}["openclaw:aaaa"]
    assert row["color"] == "#3e63dd" and row["title"] == "first msg"

    # Anything that isn't strict #rrggbb clears it (never arbitrary CSS).
    set_session_meta("openclaw:aaaa", color="red; background:url(x)")
    assert {r["session_id"]: r for r in list_conversation_sessions()}["openclaw:aaaa"]["color"] == ""

    # A colour-only meta on a transcript-less chat still surfaces in the sidebar.
    set_session_meta("openclaw:dddd", color="#46a758")
    only = {r["session_id"]: r for r in list_conversation_sessions()}
    assert "openclaw:dddd" in only and only["openclaw:dddd"]["color"] == "#46a758"


def test_set_session_meta_is_concurrency_safe() -> None:
    import threading

    from core.memory.entries import load_session_meta, set_session_meta

    n = 24
    barrier = threading.Barrier(n)
    for i in range(n):
        ensure_chat_namespace(f"openclaw:{i:020x}")

    def worker(i: int) -> None:
        barrier.wait()  # maximize contention on the shared meta file
        set_session_meta(f"openclaw:{i:020x}", title=f"title-{i}", archived=(i % 2 == 0))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    meta = load_session_meta()
    assert len(meta) == n, f"lost updates under concurrency: {len(meta)}/{n}"
    for i in range(n):
        entry = meta[f"openclaw:{i:020x}"]
        assert entry["title"] == f"title-{i}"
        assert entry["archived"] == (i % 2 == 0)


def test_load_session_meta_recovers_from_backup_on_corruption() -> None:
    from core.memory.entries import load_session_meta, set_session_meta
    from core.memory.files import chat_session_meta_path

    ensure_chat_namespace("openclaw:aaaa")
    set_session_meta("openclaw:aaaa", title="keep me")  # writes the main file + a .bak
    path = chat_session_meta_path()
    path.write_text("{ this is not valid json", encoding="utf-8")  # corrupt the main file

    recovered = load_session_meta()
    assert recovered.get("openclaw:aaaa", {}).get("title") == "keep me"  # restored from .bak
    assert list(path.parent.glob(path.name + ".corrupt-*")), "corrupt file was not preserved"
    # main file is now valid again
    assert load_session_meta().get("openclaw:aaaa", {}).get("title") == "keep me"


def test_load_session_meta_empty_when_corrupt_and_no_backup() -> None:
    from core.memory.entries import load_session_meta
    from core.memory.files import chat_session_meta_path

    path = chat_session_meta_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("garbage{", encoding="utf-8")  # corrupt, no .bak present
    assert load_session_meta() == {}
    assert list(path.parent.glob(path.name + ".corrupt-*")), "corrupt file was not preserved"


def test_session_meta_persists_across_reload() -> None:
    from core.memory.entries import load_session_meta, set_session_meta

    ensure_chat_namespace("openclaw:bbbb")
    set_session_meta("openclaw:bbbb", title="Persistent", archived=True)
    # A fresh load (simulating a server/app restart re-reading the on-disk file) sees it.
    again = load_session_meta()
    assert again["openclaw:bbbb"]["title"] == "Persistent"
    assert again["openclaw:bbbb"]["archived"] is True
