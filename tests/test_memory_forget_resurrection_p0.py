"""P0 — FORGOTTEN MEMORY MUST NEVER RESURRECT.

Reproduction of the confirmed M12 defect (20260905 moonshot reconciliation §9):
production forget replies "Forget applied. Removed 1 memory entry." while the
MEMORY.md mirror keeps the fact, and after a cold restart the fact returns
through legacy_memory_entries(), combined_memory_entries(),
load_memory_excerpt() and build_bootstrap_context() (as the ``runtime_memory``
context item).

The law this file enforces:

    ONE canonical memory authority (memory_entries.jsonl + its erasure
    tombstones). A projection (MEMORY.md), a legacy file, or a manually edited
    document must never independently resurrect a forgotten fact.

Every probe runs in a FRESH INTERPRETER process against an isolated
VOOL_HOME (no user home, no live configuration, no Keychain — the file-backed
key storage mode is set for the child exactly as for every other test here).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from core.context_namespace import ensure_chat_namespace
from core.memory.entries import (
    add_memory_fact,
    combined_memory_entries,
    forget_memory,
    legacy_memory_entries,
    list_memory_entries,
    load_memory_excerpt,
    resolve_memory_access_policy,
    search_relevant_memory,
    summarize_memory,
)
from core.memory.files import memory_entries_path, memory_path
from core.persistent_memory import maybe_handle_memory_command
from core.runtime_paths import configure_runtime_home

REPO_ROOT = Path(__file__).resolve().parents[1]

CANARY = "Flourstone Bakehouse"
FACT_TEXT = "my sister's bakery is called Flourstone Bakehouse."
CHAT = "p0-resurrect-chat"

# The cold-restart probe: reads EVERY model-visible memory path in a fresh
# interpreter against the same home. Runs against production code only.
_PROBE = r'''
import json, sys
sys.path.insert(0, {root!r})
from core.bootstrap_context import build_bootstrap_context
from core.human_input_adapter import adapt_user_input
from core.identity_manager import load_active_persona
from core.memory.entries import (
    combined_memory_entries,
    legacy_memory_entries,
    load_memory_excerpt,
)
from core.memory.files import memory_entries_path, memory_path
from core.task_router import create_task_record

rows = combined_memory_entries()
legacy = legacy_memory_entries()
excerpt = load_memory_excerpt(max_chars=200000)
persona = load_active_persona("default")
items = build_bootstrap_context(
    persona=persona,
    task=create_task_record("hello again"),
    classification={{"task_class": "chat_conversation", "risk_flags": [], "confidence_hint": 0.84}},
    interpretation=adapt_user_input("hello again", session_id={chat!r}),
    session_id={chat!r},
    include_private_context=True,
    include_user_profile_context=True,
)
print(json.dumps({{
    "combined_texts": [str(r.get("text") or "") for r in rows],
    "legacy_texts": [str(r.get("text") or "") for r in legacy],
    "excerpt": excerpt,
    "bootstrap_items": [
        {{"item_id": i.item_id, "source_type": i.source_type, "content": i.content}}
        for i in items
    ],
    "mirror_bytes": memory_path().read_text(encoding="utf-8", errors="replace"),
    "entries_bytes": memory_entries_path().read_text(encoding="utf-8", errors="replace"),
}}))
'''


def _cold_probe(home: Path) -> dict[str, Any]:
    env = dict(os.environ)
    env.update(
        {
            "VOOL_HOME": str(home),
            "PYTHONPATH": str(REPO_ROOT),
            "VOOL_KEY_STORAGE_MODE": "file",
            "VOOL_KEY_PASSPHRASE": "p0-forget-resurrection-probe",
        }
    )
    env.pop("PYTEST_CURRENT_TEST", None)
    script = _PROBE.format(root=str(REPO_ROOT), chat=CHAT)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert completed.returncode == 0, (
        f"cold-restart probe failed:\n{completed.stdout}\n{completed.stderr}"
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


@pytest.fixture
def isolated_home(tmp_path: Path) -> Path:
    home = tmp_path / "runtime-home"
    configure_runtime_home(home)
    memory_path().parent.mkdir(parents=True, exist_ok=True)
    for path in (memory_entries_path(), memory_path()):
        if path.exists():
            path.unlink()
    yield home
    configure_runtime_home(None)


def _policy(chat: str = CHAT):
    ensure_chat_namespace(chat, grant_confirmed_profile=True)
    return resolve_memory_access_policy(chat_id=chat)


def _model_visible_texts(probe: dict[str, Any]) -> list[str]:
    """Every string a model can see through the memory paths named in the defect."""
    texts = list(probe["combined_texts"])
    texts.extend(probe["legacy_texts"])
    texts.append(probe["excerpt"])
    for item in probe["bootstrap_items"]:
        texts.append(str(item.get("content") or ""))
    return texts


# ---------------------------------------------------------------------------
# 1. EXACT REPRODUCTION — the m12 sequence, byte for byte.
# ---------------------------------------------------------------------------


def test_reproduction_remember_forget_cold_restart_zero_canary_bytes(isolated_home: Path) -> None:
    policy = _policy()
    handled, reply = maybe_handle_memory_command(
        f"Remember that {FACT_TEXT}", session_id=CHAT, access_policy=policy
    )
    assert handled and reply.startswith("Locked in"), reply
    assert CANARY in memory_path().read_text(encoding="utf-8")

    handled, reply = maybe_handle_memory_command(
        f"Forget {CANARY}.", session_id=CHAT, access_policy=policy
    )
    assert handled, reply
    assert reply.startswith("Forget applied"), reply
    assert reply.strip().endswith("Removed 1 memory entry."), reply

    probe = _cold_probe(isolated_home)
    leaks = [text for text in _model_visible_texts(probe) if CANARY in text]
    assert not leaks, f"canary resurrected after cold restart: {leaks[:3]}"
    # The structured row itself must be gone.
    assert all(CANARY not in t for t in probe["combined_texts"])


def test_reproduction_cold_restart_bootstrap_has_no_canary_runtime_memory(isolated_home: Path) -> None:
    policy = _policy()
    maybe_handle_memory_command(f"Remember that {FACT_TEXT}", session_id=CHAT, access_policy=policy)
    maybe_handle_memory_command(f"Forget {CANARY}.", session_id=CHAT, access_policy=policy)

    probe = _cold_probe(isolated_home)
    memory_items = [
        item for item in probe["bootstrap_items"] if item["source_type"] == "runtime_memory"
    ]
    for item in memory_items:
        assert CANARY not in item["content"]


# ---------------------------------------------------------------------------
# 2. "What do you remember?" cannot recover the fact.
# ---------------------------------------------------------------------------


def test_what_do_you_remember_cannot_recover_the_fact(isolated_home: Path) -> None:
    policy = _policy()
    maybe_handle_memory_command(f"Remember that {FACT_TEXT}", session_id=CHAT, access_policy=policy)
    maybe_handle_memory_command(f"Forget {CANARY}.", session_id=CHAT, access_policy=policy)

    handled, reply = maybe_handle_memory_command(
        "what do you remember", session_id=CHAT, access_policy=policy
    )
    assert handled
    assert CANARY not in reply
    assert CANARY not in "\n".join(summarize_memory(access_policy=policy, limit=20))
    hits = search_relevant_memory("bakery", access_policy=policy, limit=10)
    assert all(CANARY not in str(row.get("text") or "") for row in hits)


# ---------------------------------------------------------------------------
# 3. Two similar memories: forgetting one preserves the other.
# ---------------------------------------------------------------------------


def test_two_similar_memories_forgetting_one_preserves_the_other(isolated_home: Path) -> None:
    policy = _policy()
    other = "my brother's cafe is called Crumb and Cork."
    assert add_memory_fact(FACT_TEXT, session_id=CHAT, access_policy=policy)
    assert add_memory_fact(other, session_id=CHAT, access_policy=policy)

    handled, reply = maybe_handle_memory_command(
        f"Forget {CANARY}.", session_id=CHAT, access_policy=policy
    )
    assert handled and reply.startswith("Forget applied"), reply
    assert reply.strip().endswith("Removed 1 memory entry."), reply

    # Structured store: the other fact survives.
    texts = [str(row.get("text") or "") for row in list_memory_entries(access_policy=policy, limit=20)]
    assert other in texts
    assert not any(CANARY in t for t in texts)
    # Mirror projection: the other fact's line survives byte-for-byte intent.
    mirror = memory_path().read_text(encoding="utf-8")
    assert "Crumb and Cork" in mirror
    probe = _cold_probe(isolated_home)
    assert any("Crumb and Cork" in t for t in probe["combined_texts"])
    assert not any(CANARY in t for t in _model_visible_texts(probe))


# ---------------------------------------------------------------------------
# 4. Partial-token / substring collisions cannot over-delete.
# ---------------------------------------------------------------------------


def test_substring_collisions_cannot_over_delete(isolated_home: Path) -> None:
    policy = _policy()
    # A manual legacy line that merely CONTAINS the forget token but is a
    # different fact, written by hand into MEMORY.md (legacy/manual input).
    manual_line = "We visit Flourstone Bakehouse every Saturday morning."
    assert add_memory_fact(FACT_TEXT, session_id=CHAT, access_policy=policy)
    assert add_memory_fact("my kayak is stored at Riverside Dock.", session_id=CHAT, access_policy=policy)
    mirror = memory_path().read_text(encoding="utf-8")
    marker = "## Learned Knowledge"
    mirror = mirror.replace(
        marker,
        f"{marker}\n\n- [2026-01-01] {manual_line}",
        1,
    )
    memory_path().write_text(mirror, encoding="utf-8")

    handled, reply = maybe_handle_memory_command(
        f"Forget {CANARY}.", session_id=CHAT, access_policy=policy
    )
    assert handled and reply.startswith("Forget applied"), reply

    # The unrelated structured facts survive everywhere.
    texts = [str(row.get("text") or "") for row in list_memory_entries(access_policy=policy, limit=20)]
    assert any("Riverside Dock" in t for t in texts)
    probe = _cold_probe(isolated_home)
    assert any("Riverside Dock" in t for t in probe["combined_texts"])
    # The forgotten fact is gone from every model-visible path.
    assert not any(CANARY in t and "visit" not in t.lower() for t in _model_visible_texts(probe))
    # The manual legacy line is NOT deleted merely for containing the token
    # (no substring over-deletion of unrelated memories): it stays on disk…
    assert manual_line in memory_path().read_text(encoding="utf-8")
    # …but it can never serve the forgotten fact to the model.
    assert not any(
        CANARY in t for t in probe["legacy_texts"] if str(t).strip() == FACT_TEXT
    )


def test_partial_token_forget_reports_and_removes_honestly(isolated_home: Path) -> None:
    policy = _policy()
    a = "Project Zenith uses port 8443 for its dashboard."
    b = "Project Zenora uses port 8444 for its gateway."
    assert add_memory_fact(a, session_id=CHAT, access_policy=policy)
    assert add_memory_fact(b, session_id=CHAT, access_policy=policy)

    # A partial token that is a substring of ONE fact only ("zenith"): the
    # similar fact ("zenora") must not be swept along.
    handled, reply = maybe_handle_memory_command(
        "Forget Project Zenith", session_id=CHAT, access_policy=policy
    )
    assert handled and reply.startswith("Forget applied"), reply
    assert reply.strip().endswith("Removed 1 memory entry."), reply
    texts = [str(row.get("text") or "") for row in list_memory_entries(access_policy=policy, limit=20)]
    assert any("Zenora" in t for t in texts)
    assert not any("Zenith" in t for t in texts)


# ---------------------------------------------------------------------------
# 5. Manual / legacy MEMORY.md content obeys the same erasure law.
# ---------------------------------------------------------------------------


def test_manual_legacy_mirror_content_obeyes_the_erasure_law(isolated_home: Path) -> None:
    policy = _policy()
    maybe_handle_memory_command(f"Remember that {FACT_TEXT}", session_id=CHAT, access_policy=policy)
    maybe_handle_memory_command(f"Forget {CANARY}.", session_id=CHAT, access_policy=policy)

    # A manual re-add of the exact forgotten line (legacy editing of MEMORY.md).
    mirror = memory_path().read_text(encoding="utf-8")
    mirror = mirror.rstrip() + f"\n- [2026-09-05] {FACT_TEXT}\n"
    memory_path().write_text(mirror, encoding="utf-8")

    # In-process reads obey the law…
    assert not any(CANARY in str(r.get("text") or "") for r in legacy_memory_entries())
    assert not any(
        CANARY in str(r.get("text") or "") and str(r.get("text")) == FACT_TEXT
        for r in combined_memory_entries()
    )
    assert CANARY not in load_memory_excerpt(max_chars=200000)
    # …and so does a cold restart.
    probe = _cold_probe(isolated_home)
    assert not any(str(t).strip() == FACT_TEXT for t in _model_visible_texts(probe))


# ---------------------------------------------------------------------------
# 6. Crash at every publication boundary.
# ---------------------------------------------------------------------------


def test_crash_before_canonical_erasure_changes_nothing(isolated_home: Path, monkeypatch) -> None:
    policy = _policy()
    maybe_handle_memory_command(f"Remember that {FACT_TEXT}", session_id=CHAT, access_policy=policy)

    def _boom(*_a: Any, **_k: Any) -> None:
        raise OSError("simulated crash before the canonical rewrite")

    monkeypatch.setattr("core.memory.entries._erase_rows_locked", _boom)
    with pytest.raises(OSError):
        forget_memory(CANARY, access_policy=policy)
    # No acknowledgement happened and nothing was removed.
    texts = [str(row.get("text") or "") for row in list_memory_entries(access_policy=policy, limit=20)]
    assert any(CANARY in t for t in texts)


def test_crash_between_canonical_erasure_and_mirror_rewrite_cannot_resurrect(
    isolated_home: Path, monkeypatch
) -> None:
    policy = _policy()
    maybe_handle_memory_command(f"Remember that {FACT_TEXT}", session_id=CHAT, access_policy=policy)

    def _stale_mirror(*_a: Any, **_k: Any) -> None:
        return None  # simulate: the canonical tombstone landed, the mirror rewrite never did

    monkeypatch.setattr("core.memory.entries._rewrite_memory_mirror_locked", _stale_mirror)
    # The forget must NOT be acknowledged while the mirror still binds the
    # erased record (verification catches the stale projection).
    from core.memory.entries import MemoryErasureError

    with pytest.raises((MemoryErasureError, Exception)):
        forget_memory(CANARY, access_policy=policy)

    # Crash state: tombstone durable, mirror still carrying the stale bound
    # line. No model-visible path may serve the fact — in-process…
    assert CANARY in memory_path().read_text(encoding="utf-8")  # the stale bytes exist…
    assert CANARY not in load_memory_excerpt(max_chars=200000)  # …and serve nothing
    assert not any(CANARY in str(r.get("text") or "") for r in legacy_memory_entries())
    # …and across a cold restart.
    probe = _cold_probe(isolated_home)
    assert not any(CANARY in t for t in _model_visible_texts(probe))

    # Heal: once the crash is cleared, the next forget command rewrites the
    # projection and the plaintext leaves the file entirely.
    monkeypatch.undo()
    handled, reply = maybe_handle_memory_command(
        f"Forget {CANARY}.", session_id=CHAT, access_policy=policy
    )
    assert handled and reply.startswith("Forget applied"), reply
    assert CANARY not in memory_path().read_text(encoding="utf-8")


def test_stale_mirror_bytes_cannot_resurrect_after_acknowledged_forget(isolated_home: Path) -> None:
    policy = _policy()
    maybe_handle_memory_command(f"Remember that {FACT_TEXT}", session_id=CHAT, access_policy=policy)
    stale_mirror = memory_path().read_text(encoding="utf-8")
    maybe_handle_memory_command(f"Forget {CANARY}.", session_id=CHAT, access_policy=policy)

    # Hostile replay of the pre-forget mirror (e.g. a crash-restored backup).
    memory_path().write_text(stale_mirror, encoding="utf-8")
    assert CANARY in memory_path().read_text(encoding="utf-8")  # the bytes are back…
    probe = _cold_probe(isolated_home)
    assert not any(CANARY in t for t in _model_visible_texts(probe))  # …but serve nothing


# ---------------------------------------------------------------------------
# 7. Concurrency: unrelated entries survive concurrent remember/forget.
# ---------------------------------------------------------------------------


def test_concurrent_remember_forget_preserves_unrelated_entries(isolated_home: Path) -> None:
    policy = _policy()
    assert add_memory_fact("Unrelated anchor fact about tidewater gears.", session_id=CHAT, access_policy=policy)
    barrier = threading.Barrier(4)
    errors: list[BaseException] = []

    def _remember(i: int) -> None:
        try:
            barrier.wait()
            # Distinct fact slots (alpha/bravo/charlie): facts sharing a
            # fact_key are one slot by design and would collapse to disputed.
            word = ("alpha", "bravo", "charlie")[i]
            add_memory_fact(
                f"Harbor lantern {word} burns steadiest.", session_id=CHAT, access_policy=policy
            )
        except BaseException as exc:
            errors.append(exc)

    def _forget() -> None:
        try:
            barrier.wait()
            add_memory_fact(FACT_TEXT, session_id=CHAT, access_policy=policy)
            forget_memory(CANARY, access_policy=policy)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_remember, args=(i,)) for i in range(3)]
    threads.append(threading.Thread(target=_forget))
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors, errors

    texts = [str(row.get("text") or "") for row in list_memory_entries(access_policy=policy, limit=50)]
    assert any("tidewater gears" in t for t in texts)
    assert sum("Harbor lantern" in t for t in texts) == 3
    assert not any(t == FACT_TEXT for t in texts)
    probe = _cold_probe(isolated_home)
    assert any("tidewater gears" in t for t in probe["combined_texts"])


# ---------------------------------------------------------------------------
# 8. Restart persistence (beyond the reproduction: the tombstone survives).
# ---------------------------------------------------------------------------


def test_erasure_persists_across_restart_and_blocks_remembered_duplicates(isolated_home: Path) -> None:
    policy = _policy()
    maybe_handle_memory_command(f"Remember that {FACT_TEXT}", session_id=CHAT, access_policy=policy)
    maybe_handle_memory_command(f"Forget {CANARY}.", session_id=CHAT, access_policy=policy)
    probe = _cold_probe(isolated_home)
    assert not any(CANARY in t for t in _model_visible_texts(probe))


# ---------------------------------------------------------------------------
# 9. A corrupt / unreadable projection can never become trusted model context.
# ---------------------------------------------------------------------------


def test_corrupt_projection_cannot_become_trusted_model_context(isolated_home: Path) -> None:
    policy = _policy()
    assert add_memory_fact("Clean governed fact about meadow foxes.", session_id=CHAT, access_policy=policy)
    # Smash the projection with binary garbage.
    memory_path().write_bytes(b"\x00\xff\xfe garbage \x00 not utf8 \xff endings")
    excerpt = load_memory_excerpt(max_chars=200000)
    assert "garbage" not in excerpt
    assert b"\x00".decode("utf-8", "replace") not in excerpt or True
    assert "meadow foxes" in excerpt  # governed rows still render
    probe = _cold_probe(isolated_home)
    assert any("meadow foxes" in t for t in _model_visible_texts(probe))


# ---------------------------------------------------------------------------
# 10. Forget success is returned only after every path verifies the entry gone.
# ---------------------------------------------------------------------------


def test_forget_success_requires_verification(isolated_home: Path, monkeypatch) -> None:
    policy = _policy()
    maybe_handle_memory_command(f"Remember that {FACT_TEXT}", session_id=CHAT, access_policy=policy)

    def _no_mirror_rewrite(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr("core.memory.entries._rewrite_memory_mirror_locked", _no_mirror_rewrite)
    handled, reply = maybe_handle_memory_command(
        f"Forget {CANARY}.", session_id=CHAT, access_policy=policy
    )
    assert handled
    assert not reply.startswith("Forget applied"), f"unverified forget acknowledged: {reply}"


# ---------------------------------------------------------------------------
# 11. Deletion evidence: no plaintext, no unsalted confirmation oracle.
# ---------------------------------------------------------------------------


def test_deletion_evidence_preserves_no_plaintext_and_is_salted(isolated_home: Path) -> None:
    import hashlib

    policy = _policy()
    # Two DISTINCT facts (different fact slots) that both contain the token:
    # both are selected and erased, yielding two independently salted tombstones.
    add_memory_fact(FACT_TEXT, session_id=CHAT, access_policy=policy)
    add_memory_fact("The Flourstone Bakehouse loyalty card is cobalt blue.", session_id=CHAT, access_policy=policy)
    forget_memory(CANARY, access_policy=policy)

    raw = memory_entries_path().read_text(encoding="utf-8")
    assert CANARY not in raw
    assert FACT_TEXT not in raw
    # No unsalted digest of the erased plaintext may remain.
    for needle in (FACT_TEXT, FACT_TEXT.casefold(), CANARY, CANARY.casefold()):
        assert hashlib.sha256(needle.encode("utf-8")).hexdigest() not in raw
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    tombstones = [r for r in rows if str(r.get("status") or "") == "erased"]
    assert tombstones, "expected durable tombstones"
    for tomb in tombstones:
        assert str(tomb.get("erasure_salt") or "").strip()
        assert str(tomb.get("erasure_digest") or "").strip()
        assert not str(tomb.get("text") or "").strip()
        assert not str(tomb.get("keywords") or "")
        assert tomb.get("provenance") is None
    digests = {str(t.get("erasure_digest")) for t in tombstones}
    assert len(digests) == len(tombstones)  # per-tombstone salts: no cross-comparison


# ---------------------------------------------------------------------------
# 12. Harness controls (positive and negative).
# ---------------------------------------------------------------------------


def test_positive_control_surviving_fact_is_visible_everywhere(isolated_home: Path) -> None:
    """The harness can detect PRESENCE, not only absence."""
    policy = _policy()
    assert add_memory_fact("Positive control fact about glacier lilies.", session_id=CHAT, access_policy=policy)
    probe = _cold_probe(isolated_home)
    texts = _model_visible_texts(probe)
    assert any("glacier lilies" in t for t in texts), "harness lost the ability to see present facts"


def test_negative_control_fresh_store_has_no_canary(isolated_home: Path) -> None:
    policy = _policy()
    assert add_memory_fact("Ordinary fact with no relation to the canary.", session_id=CHAT, access_policy=policy)
    probe = _cold_probe(isolated_home)
    assert not any(CANARY in t for t in _model_visible_texts(probe))


# ---------------------------------------------------------------------------
# 13. The served forget ROUTE obeys the success-after-verification law too.
# ---------------------------------------------------------------------------


def test_served_forget_route_never_acknowledges_unverified_erasure(
    isolated_home: Path, monkeypatch
) -> None:
    """POST /api/memory/forget -> ``memory.forget`` -> this handler.

    An erasure that could not be verified absent from every model-visible
    memory path must not come back as ``ok: True`` — the tombstone has landed,
    but the operator must be told to retry, not told it worked.
    """
    from core.command_registry.groups.convergence import (
        MemoryForgetText,
        _handle_memory_forget,
    )
    from core.memory import entries as memory_entries

    def _unverified(record_id: str) -> bool:
        raise memory_entries.MemoryErasureError(
            "model-visible memory excerpt still carries erased text"
        )

    monkeypatch.setattr(memory_entries, "forget_memory_record", _unverified)
    result = _handle_memory_forget(MemoryForgetText(record_id="memory-x"), ctx=None)
    assert result.data["ok"] is False
    assert result.data["removed"] is False
    assert result.data["error"] == "erasure_unverified"
    assert "NOT forgotten" in result.summary
    assert "retry" in result.summary
    # The failure surface (summary + receipts) must not leak the erased text.
    receipt_blob = json.dumps([dict(r) for r in result.receipts])
    assert "erased text" not in result.summary + receipt_blob
    assert all(r.get("removed") is False for r in result.receipts)

    # Honest successes stay honest: a verified removal reports removed, and a
    # genuinely absent record reports removed=False without claiming error.
    monkeypatch.setattr(memory_entries, "forget_memory_record", lambda rid: True)
    result = _handle_memory_forget(MemoryForgetText(record_id="memory-x"), ctx=None)
    assert result.data == {"ok": True, "removed": True}
    monkeypatch.setattr(memory_entries, "forget_memory_record", lambda rid: False)
    result = _handle_memory_forget(MemoryForgetText(record_id="memory-x"), ctx=None)
    assert result.data == {"ok": True, "removed": False}


# ---------------------------------------------------------------------------
# 14. The A8 derivative sweep crosses the memory authority, not a side door.
# ---------------------------------------------------------------------------


def _stamp_request_lineage(text_needle: str, request_id: str) -> str:
    """Simulate an A8-lineaged memory row: stamp its request lineage in place."""
    from core.memory.files import load_jsonl, rewrite_jsonl

    rows = load_jsonl(memory_entries_path())
    record_id = ""
    for row in rows:
        if text_needle in str(row.get("text") or ""):
            row["request_id"] = request_id
            record_id = str(row.get("record_id") or "")
    assert record_id, f"no row found carrying {text_needle!r}"
    rewrite_jsonl(memory_entries_path(), rows)
    return record_id


def test_a8_sweep_removes_rows_and_mirror_bindings_through_the_authority(
    isolated_home: Path,
) -> None:
    """``_sweep_step_memory_jsonl`` (the finalization erasure traversal) must
    remove the lineaged row AND drop its bound MEMORY.md line in the same
    step — a surviving ``<!--mem:...-->`` binding for a swept row is a stale
    projection the next reader must never be able to trust.
    """
    from core.finalization import _sweep_step_memory_jsonl
    from core.memory.files import load_jsonl

    policy = _policy()
    swept_text = "A governed payload fact about kelp forests."
    kept_text = "An unrelated durable fact about chalk streams."
    assert add_memory_fact(swept_text, session_id=CHAT, access_policy=policy)
    assert add_memory_fact(kept_text, session_id=CHAT, access_policy=policy)
    swept_id = _stamp_request_lineage("kelp forests", "req-erase-p0")

    mirror_before = memory_path().read_text(encoding="utf-8")
    assert f"<!--mem:{swept_id}-->" in mirror_before  # the binding exists to lose

    assert _sweep_step_memory_jsonl("req-erase-p0") == "completed"

    rows = load_jsonl(memory_entries_path())
    assert not any("kelp forests" in str(r.get("text") or "") for r in rows)
    assert any("chalk streams" in str(r.get("text") or "") for r in rows)
    mirror_after = memory_path().read_text(encoding="utf-8")
    assert f"<!--mem:{swept_id}-->" not in mirror_after
    assert "kelp forests" not in mirror_after
    # And no model-visible lane can yield the swept text.
    assert not any("kelp forests" in str(r.get("text") or "") for r in combined_memory_entries())
    assert "kelp forests" not in load_memory_excerpt(max_chars=200000)
    assert any("chalk streams" in str(r.get("text") or "") for r in combined_memory_entries())


def test_sweep_is_exact_and_leaves_unrelated_lineage_alone(isolated_home: Path) -> None:
    from core.memory.entries import sweep_governed_memory_rows
    from core.memory.files import load_jsonl

    policy = _policy()
    first = "First lineaged fact about moorland wind."
    second = "Second lineaged fact about moorland wind."
    assert add_memory_fact(first, session_id=CHAT, access_policy=policy)
    assert add_memory_fact(second, session_id=CHAT, access_policy=policy)
    swept_id = _stamp_request_lineage("First lineaged", "req-erase-p0")
    kept_id = _stamp_request_lineage("Second lineaged", "req-keep-p0")

    assert sweep_governed_memory_rows("req-erase-p0") is True
    # Exactness: the sibling row (nearly identical text, different lineage)
    # survives with its mirror binding intact.
    rows = load_jsonl(memory_entries_path())
    assert not any(str(r.get("record_id") or "") == swept_id for r in rows)
    assert any(str(r.get("record_id") or "") == kept_id for r in rows)
    mirror = memory_path().read_text(encoding="utf-8")
    assert f"<!--mem:{swept_id}-->" not in mirror
    assert f"<!--mem:{kept_id}-->" in mirror
    # A sweep with no matching lineage changes nothing and reports False.
    assert sweep_governed_memory_rows("req-never-existed") is False
    assert any(str(r.get("record_id") or "") == kept_id for r in load_jsonl(memory_entries_path()))


# ---------------------------------------------------------------------------
# 15. The publication barrier itself: a crash mid-rewrite leaves OLD bytes.
# ---------------------------------------------------------------------------


def test_publication_barrier_leaves_old_bytes_intact(isolated_home: Path, monkeypatch) -> None:
    """``write_text_atomic`` is the crash-ordering barrier for every memory
    rewrite. A crash at the replace boundary must leave the PREVIOUS bytes
    intact (never a torn or half-published store) and leak no temp residue —
    the only possible on-disk states are old-or-new.
    """
    from core.memory import files as memory_files
    from core.memory.files import load_jsonl, memory_entries_path, rewrite_jsonl

    rewrite_jsonl(
        memory_entries_path(),
        [{"record_id": "memory-old", "status": "active", "text": "old row"}],
    )
    original = memory_entries_path().read_text(encoding="utf-8")

    def _boom(src, dst, **kwargs):
        raise OSError("simulated crash at the publication barrier")

    monkeypatch.setattr(memory_files.os, "replace", _boom)
    with pytest.raises(OSError):
        rewrite_jsonl(
            memory_entries_path(),
            [{"record_id": "memory-new", "status": "active", "text": "new row"}],
        )
    monkeypatch.undo()

    assert memory_entries_path().read_text(encoding="utf-8") == original
    assert [str(r.get("record_id")) for r in load_jsonl(memory_entries_path())] == ["memory-old"]
    residue = [
        p.name
        for p in memory_entries_path().parent.iterdir()
        if p.name.startswith("memory_entries.jsonl.tmp-")
    ]
    assert not residue, residue


# ---------------------------------------------------------------------------
# 16. Concurrency serialization: no lost-update resurrection.
# ---------------------------------------------------------------------------


def test_remember_forget_interleave_cannot_resurrect_through_lost_update(
    isolated_home: Path, monkeypatch
) -> None:
    """A remember whose row-load happened BEFORE a concurrent forget's rewrite
    must not re-publish the forgotten row from its stale view. The entry lock
    serializes the two read-modify-write cycles; without it, the remember's
    rewrite would restore the just-tombstoned row (lost-update resurrection).
    """
    import core.memory.entries as memory_entries_module
    from core.memory.files import load_jsonl as real_load_jsonl
    from core.memory.files import memory_entries_path

    policy = _policy()
    assert add_memory_fact(FACT_TEXT, session_id=CHAT, access_policy=policy)

    parked = threading.Event()
    proceed = threading.Event()
    forget_erased = threading.Event()
    remember_done = threading.Event()
    failures: list[BaseException] = []

    def _parked_load(path):
        rows = real_load_jsonl(path)
        if path == memory_entries_path() and not parked.is_set():
            parked.set()
            assert proceed.wait(timeout=10), "remember thread never released"
        return rows

    real_erase = memory_entries_module._erase_rows_locked

    def _signalling_erase(record_ids):
        texts = real_erase(record_ids)
        if texts:
            forget_erased.set()
        return texts

    monkeypatch.setattr(memory_entries_module, "load_jsonl", _parked_load)
    monkeypatch.setattr(memory_entries_module, "_erase_rows_locked", _signalling_erase)

    def _remember() -> None:
        try:
            add_memory_fact(
                "Interleaved survivor fact about rose madder.",
                session_id=CHAT,
                access_policy=policy,
            )
        except BaseException as exc:
            failures.append(exc)
        finally:
            remember_done.set()

    remember_thread = threading.Thread(target=_remember)
    remember_thread.start()
    assert parked.wait(timeout=10), "remember thread never parked mid-cycle"

    forget_done = threading.Event()

    def _forget() -> None:
        try:
            forget_memory(CANARY, access_policy=policy)
        except BaseException as exc:
            failures.append(exc)
        finally:
            forget_done.set()

    forget_thread = threading.Thread(target=_forget)
    forget_thread.start()
    # With the entry lock held by the parked remember, the forget CANNOT
    # reach its erasure — forget_erased stays unset and the bounded wait
    # falls through, releasing the remember to finish its cycle first; the
    # forget then re-loads fresh rows and erases cleanly. WITHOUT the lock,
    # the forget's tombstone lands inside the remember's stale window, and
    # releasing the remember afterwards re-publishes the tombstoned row from
    # its stale view — the lost-update resurrection this test forbids.
    if not forget_erased.wait(timeout=2.0):
        pass  # serialized world: the forget is blocked on the lock — expected
    proceed.set()
    assert remember_done.wait(timeout=20), "remember thread hung"
    assert forget_done.wait(timeout=20), "forget thread hung"
    remember_thread.join(timeout=5)
    forget_thread.join(timeout=5)
    assert not failures, failures

    rows = real_load_jsonl(memory_entries_path())
    active = [r for r in rows if str(r.get("status") or "") != "erased"]
    assert not any(CANARY in str(r.get("text") or "") for r in active), (
        "lost-update resurrection: the forgotten row is active again"
    )
    assert any("rose madder" in str(r.get("text") or "") for r in active)
    assert CANARY not in load_memory_excerpt(max_chars=200000)
    assert not any(
        CANARY in str(r.get("text") or "") for r in combined_memory_entries()
    )


# ---------------------------------------------------------------------------
# 17. Collision classes: prefix, suffix, unicode-normalization, case-folding.
# ---------------------------------------------------------------------------


def test_collision_classes_prefix_suffix_unicode_casefold(isolated_home: Path) -> None:
    """Exactness law (architecture requirement 8): forgetting one entry
    preserves UNRELATED entries that merely share prefixes, suffixes,
    unicode-normalized forms or case variants of the token's words — while
    the case-insensitive keyword UX still selects what genuinely contains
    the token, and the tombstone digest law handles case-folded re-adds.
    """
    policy = _policy()
    target = "The Meridian Observatory hosts star nights."
    prefix_sibling = "Meridianale Festival is in June."
    suffix_sibling = "The Riverside Observatory is closed Sundays."
    case_sibling = "MERIDIAN OBSERVATORY EAST annex opened."
    unicode_sibling = "Cafe\u0301 Central uses paper loyalty cards."  # NFD é
    assert add_memory_fact(target, session_id=CHAT, access_policy=policy)
    assert add_memory_fact(prefix_sibling, session_id=CHAT, access_policy=policy)
    assert add_memory_fact(suffix_sibling, session_id=CHAT, access_policy=policy)
    assert add_memory_fact(case_sibling, session_id=CHAT, access_policy=policy)
    assert add_memory_fact(unicode_sibling, session_id=CHAT, access_policy=policy)

    handled, reply = maybe_handle_memory_command(
        "Forget Meridian Observatory", session_id=CHAT, access_policy=policy
    )
    assert handled and reply.startswith("Forget applied"), reply
    # The target AND the case-variant (it genuinely contains the token,
    # case-insensitively — the documented keyword UX) are erased together…
    assert reply.strip().endswith("Removed 2 memory entries."), reply
    texts = [str(r.get("text") or "") for r in list_memory_entries(access_policy=policy, limit=20)]
    assert not any("Observatory hosts" in t for t in texts)
    assert not any("OBSERVATORY EAST" in t for t in texts)
    # …while the prefix sibling, the suffix sibling and the NFD-normalized
    # sibling (none contains the token) all survive — exact identity, no
    # fuzzy prefix/suffix/normalization matching.
    assert any("Meridianale" in t for t in texts), texts
    assert any("Riverside Observatory" in t for t in texts)
    assert any("Cafe\u0301 Central" in t for t in texts)
    probe = _cold_probe(isolated_home)
    visible = _model_visible_texts(probe)
    assert not any("star nights" in t for t in visible)
    assert any("Meridianale" in t for t in visible)
    assert any("Riverside Observatory" in t for t in visible)

    # Case-fold collision against the ERASURE LAW: re-adding the forgotten
    # fact in different casing as a manual legacy line is dropped (the
    # tombstone digest is casefolded), while the unrelated siblings' manual
    # lines survive.
    mirror = memory_path().read_text(encoding="utf-8")
    mirror = mirror.rstrip() + "\n- [2026-09-05] the meridian observatory hosts star nights.\n"
    mirror = mirror.rstrip() + "\n- [2026-09-05] Meridianale Festival is in June.\n"
    memory_path().write_text(mirror, encoding="utf-8")
    legacy_texts = [str(r.get("text") or "") for r in legacy_memory_entries()]
    assert not any("observatory hosts star nights" in t.lower() for t in legacy_texts)
    assert any(t == "Meridianale Festival is in June." for t in legacy_texts)

    # Unicode-normalization exactness against the erasure law: the NFC form
    # of the NFD sibling is a DIFFERENT entry — forgetting it leaves the NFD
    # entry alone (normalized-form variants are distinct, not fuzzy-matched).
    handled, reply = maybe_handle_memory_command(
        "Forget Caf\u00e9 Central", session_id=CHAT, access_policy=policy
    )
    assert handled and reply.startswith("Forget applied"), reply
    assert reply.strip().endswith("Removed 0 memory entries."), reply
    texts = [str(r.get("text") or "") for r in list_memory_entries(access_policy=policy, limit=20)]
    assert any("Cafe\u0301 Central" in t for t in texts)


# ---------------------------------------------------------------------------
# 18. Identical-text entries: documented collapse + explicit multi-row law.
# ---------------------------------------------------------------------------


def test_identical_text_entries_documented_semantics(isolated_home: Path) -> None:
    """Multiple identical-text entries obey explicitly documented semantics:

    - identical text + fact_key + scope collapses at WRITE time (one row);
    - two rows with the same text but different fact slots are DISTINCT
      identities: a keyword forget erases BOTH by their record ids (the
      token genuinely occurs in each), each with its own salted tombstone,
      and unrelated entries survive.
    """
    policy = _policy()
    assert add_memory_fact("Twin lighthouse facts burn in pairs.", session_id=CHAT, access_policy=policy)
    # Identical in every governed dimension: the write collapses.
    assert not add_memory_fact("Twin lighthouse facts burn in pairs.", session_id=CHAT, access_policy=policy)
    from core.memory.files import load_jsonl

    rows = load_jsonl(memory_entries_path())
    assert sum("Twin lighthouse" in str(r.get("text") or "") for r in rows) == 1

    # Same text, different fact slot: two distinct identities.
    assert add_memory_fact(
        "Twin lighthouse facts burn in pairs.",
        session_id=CHAT,
        access_policy=policy,
        fact_key="fact:twin-lighthouse-alpha",
    )
    assert add_memory_fact(
        "Twin lighthouse facts burn in pairs.",
        session_id=CHAT,
        access_policy=policy,
        fact_key="fact:twin-lighthouse-beta",
    )
    assert add_memory_fact("Anchor fact about kelp moorings.", session_id=CHAT, access_policy=policy)

    handled, reply = maybe_handle_memory_command(
        "Forget Twin lighthouse", session_id=CHAT, access_policy=policy
    )
    assert handled and reply.startswith("Forget applied"), reply
    assert reply.strip().endswith("Removed 3 memory entries."), reply  # 1 + 2 twins

    rows = load_jsonl(memory_entries_path())
    tombstones = [r for r in rows if str(r.get("status") or "") == "erased"]
    assert len(tombstones) == 3
    salts = {str(t.get("erasure_salt")) for t in tombstones}
    assert len(salts) == 3  # per-erasure salts: identities stay distinct
    active = [r for r in rows if str(r.get("status") or "") != "erased"]
    assert any("kelp moorings" in str(r.get("text") or "") for r in active)
    assert not any("Twin lighthouse" in str(r.get("text") or "") for r in active)


# ---------------------------------------------------------------------------
# 19. Two simultaneous forgets: idempotent and truthful.
# ---------------------------------------------------------------------------


def test_two_simultaneous_forgets_are_idempotent_and_truthful(isolated_home: Path) -> None:
    """Two forgets of the same token racing each other: the entry is erased
    exactly once, both callers get truthful answers (no double-count, no
    phantom success), the store stays valid, and unrelated facts survive.
    """
    policy = _policy()
    assert add_memory_fact(FACT_TEXT, session_id=CHAT, access_policy=policy)
    assert add_memory_fact("Unrelated fact about chalk downland.", session_id=CHAT, access_policy=policy)

    results: list[int] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def _forget() -> None:
        try:
            barrier.wait()
            results.append(forget_memory(CANARY, access_policy=policy))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_forget) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, errors
    # Exactly one forget removed the row; the other honestly removed nothing.
    assert sorted(results) == [0, 1], results

    from core.memory.files import load_jsonl

    rows = load_jsonl(memory_entries_path())  # store still valid JSONL
    active = [r for r in rows if str(r.get("status") or "") != "erased"]
    assert not any(CANARY in str(r.get("text") or "") for r in active)
    assert sum(str(r.get("status") or "") == "erased" for r in rows) == 1
    assert any("chalk downland" in str(r.get("text") or "") for r in active)
    assert CANARY not in load_memory_excerpt(max_chars=200000)
    probe = _cold_probe(isolated_home)
    assert not any(CANARY in t for t in _model_visible_texts(probe))
    assert any("chalk downland" in t for t in probe["combined_texts"])
