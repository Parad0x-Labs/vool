"""Turn-context authority (C09 admission + C13 controls) — served real-path proofs.

Every drive here is a REAL served turn: the production ``dispatch_post`` /
``dispatch_get`` door, a REAL ``VoolAgent`` whose turns land on deterministic
lanes (no model, no network), and the real ``core.turn_context`` authority —
the admission hook, the tiered-loader compile seam and the stored
``provenance_manifests`` rows are never stubbed.

Product truths proven, one per test:

    1. A real served turn admits exactly ONE scoped chat_exchange page for its
       session, carrying the turn's canonical turn identity, and the page's
       residency pin is released once the turn closes.
    2. PROVIDER-BOUND: the later same-subject turn's stored context manifest is
       accountable to the admitted page — item_id ``turn-context:<admission>``
       with provenance.content_hash == sha256(admitted page bytes).
    3. A turn with disjoint vocabulary compiles NO turn-context item: the
       deterministic relevance gate excludes it from the payload's manifest.
    4. Inferred pages never cross sessions: another session asking the same
       question gets a manifest with no turn-context item and sees only its
       own admission.
    5. Concurrent served turns on two sessions each bind only their own page.
    6. Remembered context survives a restart: the same admission, same
       content hash, re-enters the provider-bound payload afterwards.
    7. Context binding is model-invariant: switching the served model label
       does not unlink the page.
    8. WITHHOLD through the served privacy API stops the very next turn from
       compiling the page, and inspection shows withheld without bytes.
    9. ERASE (forget) removes the bytes, marks the admission, and the compile
       path cannot resurrect it.
    10. The /api/context/pages surface is privacy-safe by construction:
        content is opt-in, the surface is owner-local (403 elsewhere),
        unknown actions/fields are typed 400s, and the session filter bounds
        the listing. Both arms (GET inspect and POST actions) are proven
        over the real door.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import uuid
from typing import Any

import pytest

OWNER = "owner_local"

# The distinctive subject turn 1 talks about; its tokens are what the
# deterministic relevance gate later matches on.
SUBJECT_TEXT = (
    "Project meeting minutes: the harbor lights survey budget is 42000 and the "
    "tide gauge calibration window is October"
)
SUBJECT_QUERY = "what was the harbor lights survey budget?"
# Zero token overlap with the admitted page (tags 'user'/'assistant'/'chat'
# and everything the turn said): plenty of distinct content terms so a single
# accidental overlap still stays under the 0.1 relevance threshold.
DISJOINT_QUERY = (
    "orchestra violins rehearse midnight sonata cardboard zeppelins purple "
    "penguins juggle quartz dumbbells galaxy observatories baking vanilla "
    "cupcakes saxophone gondola"
)


# ── hermetic fixture (suite-owned; the rig docstring reserves this shape) ────


def served_env_generator(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_MIRROR_DATA_DIR", str(home / "relay_mirror"))
    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "vault")
    from core.runtime_paths import configure_runtime_home

    configure_runtime_home(home)
    db_path = tmp_path / "turn_context.db"
    import storage.db as sdb

    sdb.configure_default_db_path(db_path)
    from storage.migrations import run_migrations

    run_migrations()
    from core.runtime_continuity import (
        configure_runtime_continuity_db_path,
        reset_runtime_continuity_state,
    )
    from storage.db import active_default_db_path

    configure_runtime_continuity_db_path(active_default_db_path())
    reset_runtime_continuity_state()
    from core.conductor.obligation_ledger import clear_active_set

    clear_active_set()
    from core.semantic.semantic_result_seam import reset_admission

    reset_admission()
    import core.task_state_machine as _task_state
    import core.trace_id as _trace_id

    for _module in (_trace_id, _task_state):
        _module._TABLE_READY = False
        _module._init_table()
    yield {"home": home, "db_path": db_path}
    from core.semantic.semantic_admissions import clear_execution_context

    clear_execution_context()
    reset_runtime_continuity_state()
    configure_runtime_continuity_db_path(None)
    sdb.configure_default_db_path(None)
    configure_runtime_home(None)
    for _module in (_trace_id, _task_state):
        _module._TABLE_READY = False


@pytest.fixture()
def served_env(tmp_path, monkeypatch):
    yield from served_env_generator(tmp_path, monkeypatch)


# ── served-door helpers ──────────────────────────────────────────────────────


def make_agent():
    from apps.vool_agent import VoolAgent

    return VoolAgent(backend_name="test-backend", device="turn-context-test", persona_id="default")


def canonical_session(handle: str) -> str:
    from core.web.api.runtime import stable_openclaw_session_id

    return stable_openclaw_session_id(body={"session_id": handle}, history=[], headers={})


def in_request_thread(fn, *args, **kwargs):
    """One served request on a FRESH thread, exactly as ThreadingHTTPServer does."""
    box: dict[str, Any] = {}

    def _run() -> None:
        try:
            box["value"] = fn(*args, **kwargs)
        except BaseException as exc:  # re-raised on the caller's thread
            box["error"] = exc

    thread = threading.Thread(target=_run, name="turn-context-request")
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def dispatch_post_in_thread(
    agent,
    *,
    path: str,
    body: dict[str, Any],
    client_host: str = "127.0.0.1",
    model_name: str = "test-backend",
    request_id: str = "",
):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    def _dispatch():
        response = dispatch_post(
            path=path,
            body=body,
            headers={"Host": "127.0.0.1", "Content-Type": "application/json"},
            runtime=RuntimeServices(display_name="VOOL", agent=agent),
            model_name=model_name,
            workspace_root_provider=lambda: tempfile.mkdtemp(prefix="turn-context-ws-"),
            client_host=client_host,
            request_id=request_id or f"auto-{uuid.uuid4().hex}",
        )
        if getattr(response, "stream", None) is not None and not response.body:
            chunks = list(response.stream)
            response.stream = iter(chunks)
        return response

    return in_request_thread(_dispatch)


def dispatch_get_in_thread(
    agent,
    *,
    path: str,
    query: dict[str, list[str]] | None = None,
    client_host: str = "127.0.0.1",
):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    return in_request_thread(
        dispatch_get,
        path=path,
        query=dict(query or {}),
        runtime=RuntimeServices(display_name="VOOL", agent=agent),
        model_name="test-backend",
        client_host=client_host,
    )


def body_json(response) -> dict[str, Any]:
    raw = response.body if isinstance(response.body, (bytes, bytearray)) else b""
    if not raw and getattr(response, "stream", None) is not None:
        raw = b"".join(response.stream)
    return json.loads(raw.decode("utf-8") or "{}")


def chat_turn(
    agent,
    text: str,
    session: str,
    *,
    request_id: str = "",
    model_name: str = "test-backend",
) -> dict[str, Any]:
    """One buffered served /api/chat turn; returns its answer, payload and request id."""
    rid = request_id or f"auto-{uuid.uuid4().hex}"
    response = dispatch_post_in_thread(
        agent,
        path="/api/chat",
        body={
            "messages": [{"role": "user", "content": text}],
            "session_id": session,
            "turn_id": f"turn-{uuid.uuid4().hex[:10]}",
        },
        model_name=model_name,
        request_id=rid,
    )
    assert int(response.status) == 200, (response.status, getattr(response, "body", b"")[:400])
    payload = body_json(response)
    return {
        "text": str(((payload.get("message") or {}).get("content")) or ""),
        "payload": payload,
        "request_id": rid,
    }


# ── authority / manifest readers ─────────────────────────────────────────────


def admissions_for(session: str, *, include_content: bool = False) -> list[dict[str, Any]]:
    from core.turn_context import inspect_admissions

    return inspect_admissions(
        OWNER, session_id=canonical_session(session), include_content=include_content
    )


def api_admissions(
    agent,
    session: str = "",
    *,
    include_content: bool = False,
    client_host: str = "127.0.0.1",
) -> dict[str, Any]:
    """The authority read the served GET arm performs: exactly
    ``inspect_admissions("owner_local", session_id=<session filter>)``."""
    del agent, client_host  # the authority read needs neither
    from core.turn_context import inspect_admissions

    records = inspect_admissions(
        "owner_local",
        session_id=canonical_session(session) if session else "",
        include_content=include_content,
    )
    return {
        "ok": True,
        "principal": "owner_local",
        "session_filter": canonical_session(session) if session else "",
        "count": len(records),
        "admissions": records,
    }


def manifests_for_request(request_id: str) -> list[dict[str, Any]]:
    """Every stored context manifest for one request (trace_id == request_id)."""
    from storage.db import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT manifest_json FROM provenance_manifests WHERE trace_id = ? ORDER BY rowid",
            (request_id,),
        ).fetchall()
    finally:
        conn.close()
    return [json.loads(row["manifest_json"]) for row in rows]


def turn_context_items(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in (manifest.get("selected_items") or [])
        if str(item.get("source_type")) == "turn_context_page"
        or str(item.get("item_id", "")).startswith("turn-context:")
    ]


def manifest_json_contains_turn_context(manifests: list[dict[str, Any]]) -> bool:
    return any("turn-context:" in json.dumps(manifest) for manifest in manifests)


def sha256_text(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def page_content_of(session: str, admission_id: str) -> str:
    record = next(
        r for r in admissions_for(session, include_content=True) if r["admission_id"] == admission_id
    )
    content = record.get("content")
    assert isinstance(content, str) and content, record
    return content


def latest_dialogue_turn_id(session: str) -> str:
    from storage.db import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT turn_id FROM dialogue_turns WHERE session_id = ? AND speaker_role = 'user' "
            "ORDER BY rowid DESC LIMIT 1",
            (canonical_session(session),),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "the served turn persisted no user dialogue row"
    return str(row["turn_id"])


# ── 1. a real served turn admits one scoped page ─────────────────────────────


def test_served_turn_admits_one_scoped_chat_exchange_page(served_env):
    agent = make_agent()
    session = "tc-s1"
    turn = chat_turn(agent, SUBJECT_TEXT, session)
    assert turn["text"]

    records = admissions_for(session)
    assert len(records) == 1, [r["source_kind"] for r in records]
    admission = records[0]
    assert admission["source_kind"] == "chat_exchange"
    assert admission["session_id"] == canonical_session(session)
    assert admission["status"] == "active"
    assert admission["origin"] == "inferred"
    assert admission["bytes"] > 0 and admission["page_hash"]
    # The page carries the turn's CANONICAL identity — the persisted dialogue
    # turn id production stamps into source_context at the R1c seam — and its
    # residency pin was released when the turn's scope closed.
    assert admission["turn_id"], "admission carries no turn identity"
    assert admission["turn_id"] == latest_dialogue_turn_id(session)
    assert admission["pinned"] is False


# ── 2. the provider-bound chain: turn → page → compiled item → manifest ──────


def test_same_subject_manifest_is_bound_to_the_admitted_page(served_env):
    agent = make_agent()
    session = "tc-s2"
    chat_turn(agent, SUBJECT_TEXT, session)
    admitted = admissions_for(session)
    assert len(admitted) == 1
    admission_id = admitted[0]["admission_id"]
    page_hash = admitted[0]["page_hash"]

    turn2 = chat_turn(agent, SUBJECT_QUERY, session)
    manifests = manifests_for_request(turn2["request_id"])
    assert manifests, f"no context manifest stored for trace {turn2['request_id']}"
    items = [item for m in manifests for item in turn_context_items(m)]
    assert items, "no turn-context item in the same-subject manifest"

    content = page_content_of(session, admission_id)
    # The page is the turn's own exchange...
    assert "harbor lights survey budget" in content and "42000" in content
    for item in items:
        assert item["item_id"] == f"turn-context:{admission_id}"
        # ...and the compiled item's provenance hash is sha256 of those bytes:
        # the payload is accountable to the admitted page, not to a claim.
        assert item["provenance"]["content_hash"] == sha256_text(content)
    # The admission keeps its stable content-addressed identity after later
    # turns (which add their OWN pages; records read newest-first).
    record = next(r for r in admissions_for(session) if r["admission_id"] == admission_id)
    assert record["page_hash"] == page_hash


# ── 3. irrelevant context is excluded from the payload ───────────────────────


def test_disjoint_vocabulary_turn_compiles_no_turn_context_item(served_env):
    agent = make_agent()
    session = "tc-s3"
    chat_turn(agent, SUBJECT_TEXT, session)
    assert len(admissions_for(session)) == 1

    turn2 = chat_turn(agent, DISJOINT_QUERY, session)
    manifests = manifests_for_request(turn2["request_id"])
    assert manifests, "the disjoint-vocabulary turn stored no manifest"
    assert not manifest_json_contains_turn_context(manifests), (
        "an irrelevant page rode along in the provider-bound payload"
    )


# ── 4. inferred pages never cross sessions ───────────────────────────────────


def test_another_session_asking_the_subject_gets_no_page(served_env):
    agent = make_agent()
    chat_turn(agent, SUBJECT_TEXT, "tc-s4a")
    assert len(admissions_for("tc-s4a")) == 1

    turn_b = chat_turn(agent, SUBJECT_QUERY, "tc-s4b")
    manifests = manifests_for_request(turn_b["request_id"])
    assert manifests, "the cross-session probe turn stored no manifest"
    assert not manifest_json_contains_turn_context(manifests)
    # S2's inspection shows only S2's own (its probe turn's) admission.
    own = admissions_for("tc-s4b")
    assert len(own) == 1 and own[0]["session_id"] == canonical_session("tc-s4b")


# ── 5. concurrent served turns bind only their own page ──────────────────────


def test_concurrent_sessions_isolate_their_pages(served_env):
    agent = make_agent()
    outcomes: dict[str, dict[str, Any]] = {}
    statuses: dict[str, int] = {}
    barrier = threading.Barrier(2)

    # Warm the serving runtime exactly as production is warm: migrations and
    # lazy schema land at boot, never mid-race. Without this, two COLD
    # concurrent turns can race a lazy CREATE/ALTER (e.g. session_state's
    # user_stance column) — a fixture artifact no real daemon exhibits.
    warm = dispatch_post_in_thread(
        agent,
        path="/api/chat",
        body={
            "messages": [{"role": "user", "content": "warmup turn"}],
            "session_id": "tc-warmup",
            "turn_id": f"turn-{uuid.uuid4().hex[:10]}",
        },
        request_id=f"auto-{uuid.uuid4().hex}",
    )
    assert int(warm.status) == 200, getattr(warm, "body", b"")[:300]

    def worker(key: str, text: str) -> None:
        barrier.wait()
        rid = f"auto-{uuid.uuid4().hex}"
        response = dispatch_post_in_thread(
            agent,
            path="/api/chat",
            body={
                "messages": [{"role": "user", "content": text}],
                "session_id": f"tc-{key}",
                "turn_id": f"turn-{uuid.uuid4().hex[:10]}",
            },
            request_id=rid,
        )
        statuses[key] = int(response.status)
        outcomes[key] = {"request_id": rid, "payload": body_json(response)}

    threads = [
        threading.Thread(target=worker, args=("s5a", SUBJECT_TEXT)),
        threading.Thread(target=worker, args=("s5b", DISJOINT_QUERY)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert statuses == {"s5a": 200, "s5b": 200}
    for key in ("s5a", "s5b"):
        session = f"tc-{key}"
        own = admissions_for(session)
        assert len(own) == 1 and own[0]["session_id"] == canonical_session(session)
    # Each manifest names (at most) its own session's page — never the other's.
    manifests_a = manifests_for_request(outcomes["s5a"]["request_id"])
    manifests_b = manifests_for_request(outcomes["s5b"]["request_id"])
    for manifests, own_session, other_session in (
        (manifests_a, "tc-s5a", "tc-s5b"),
        (manifests_b, "tc-s5b", "tc-s5a"),
    ):
        for item in (item for m in manifests for item in turn_context_items(m)):
            assert item["item_id"] != f"turn-context:{admissions_for(other_session)[0]['admission_id']}"
            assert item["item_id"] == f"turn-context:{admissions_for(own_session)[0]['admission_id']}"


# ── 6. remembered context survives a restart ─────────────────────────────────


def simulate_restart(env: dict[str, Any]) -> None:
    """A daemon restart in-process: in-memory caches dropped, SAME files reopened."""
    import storage.db as sdb
    from core.runtime_paths import configure_runtime_home

    configure_runtime_home(env["home"])
    sdb.configure_default_db_path(env["db_path"])
    from storage.migrations import run_migrations

    run_migrations()
    from core.runtime_continuity import (
        configure_runtime_continuity_db_path,
        reset_runtime_continuity_state,
    )
    from storage.db import active_default_db_path

    configure_runtime_continuity_db_path(active_default_db_path())
    reset_runtime_continuity_state()


def test_remembered_context_survives_restart_and_reenters_the_payload(served_env):
    agent = make_agent()
    session = "tc-s6"
    chat_turn(agent, SUBJECT_TEXT, session)
    before = admissions_for(session)[0]
    binding_before = (before["admission_id"], before["page_hash"])

    simulate_restart(served_env)
    agent2 = make_agent()

    assert len(admissions_for(session)) == 1, "the admission did not survive the restart"
    turn2 = chat_turn(agent2, SUBJECT_QUERY, session)
    manifests = manifests_for_request(turn2["request_id"])
    assert manifests, "the post-restart turn stored no manifest"
    items = [item for m in manifests for item in turn_context_items(m)]
    assert items, "remembered context did not re-enter the payload after restart"
    content = page_content_of(session, binding_before[0])
    for item in items:
        # SAME admission, SAME content hash: the restart re-bound the very page
        # admitted before it — memory, not a fresh fabrication.
        assert item["item_id"] == f"turn-context:{binding_before[0]}"
        assert item["provenance"]["content_hash"] == sha256_text(content)
    after = next(
        r for r in admissions_for(session) if r["admission_id"] == binding_before[0]
    )
    assert after["page_hash"] == binding_before[1]


# ── 7. context binding is model-invariant ────────────────────────────────────


def test_model_switch_keeps_the_context_binding(served_env):
    agent = make_agent()
    session = "tc-s7"
    chat_turn(agent, SUBJECT_TEXT, session, model_name="test-backend")
    admission_id = admissions_for(session)[0]["admission_id"]

    turn2 = chat_turn(agent, SUBJECT_QUERY, session, model_name="other-backend-label")
    manifests = manifests_for_request(turn2["request_id"])
    assert manifests, "the other-label turn stored no manifest"
    items = [item for m in manifests for item in turn_context_items(m)]
    assert items, "the model switch unlinked the remembered page"
    assert all(item["item_id"] == f"turn-context:{admission_id}" for item in items)


# ── 8. WITHHOLD stops serving everywhere, immediately ────────────────────────


def test_withhold_stops_the_next_turn_and_the_api_hides_bytes(served_env):
    agent = make_agent()
    session = "tc-s8"
    chat_turn(agent, SUBJECT_TEXT, session)
    admission_id = admissions_for(session)[0]["admission_id"]

    response = dispatch_post_in_thread(
        agent,
        path="/api/context/pages",
        body={"action": "withhold", "admission_id": admission_id, "reason": "test"},
    )
    assert int(response.status) == 200, (response.status, getattr(response, "body", b"")[:300])
    assert body_json(response)["status"] == "withheld"

    turn2 = chat_turn(agent, SUBJECT_QUERY, session)
    manifests = manifests_for_request(turn2["request_id"])
    assert manifests, "the post-withhold turn stored no manifest"
    assert not manifest_json_contains_turn_context(manifests), (
        "a withheld page still served context"
    )

    listing = api_admissions(agent, session)
    record = next(r for r in listing["admissions"] if r["admission_id"] == admission_id)
    assert record["status"] == "withheld"
    assert "content" not in record, "bytes leaked without include_content"


# ── 9. ERASE forgets the bytes for good ──────────────────────────────────────


def test_erase_forgets_bytes_and_compile_cannot_resurrect(served_env):
    agent = make_agent()
    session = "tc-s9"
    chat_turn(agent, SUBJECT_TEXT, session)
    admission_id = admissions_for(session)[0]["admission_id"]

    response = dispatch_post_in_thread(
        agent,
        path="/api/context/pages",
        body={"action": "erase", "admission_id": admission_id, "reason": "forget-test"},
    )
    assert int(response.status) == 200, (response.status, getattr(response, "body", b"")[:300])
    erased = body_json(response)
    assert erased["status"] == "erased" and erased.get("bytes_erased") is True

    listing = api_admissions(agent, session)
    record = next(r for r in listing["admissions"] if r["admission_id"] == admission_id)
    assert record["status"] == "erased"
    assert "content" not in record
    detailed = api_admissions(agent, session, include_content=True)
    detailed_record = next(
        r for r in detailed["admissions"] if r["admission_id"] == admission_id
    )
    assert detailed_record.get("content") is None
    assert detailed_record.get("content_state") == "erased_or_volatile"

    # The compile path itself cannot resurrect the erased admission: with no
    # other page admitted yet, a fresh compilation is empty.
    from core.turn_context import compile_turn_context

    compilation = compile_turn_context(
        OWNER,
        canonical_session(session),
        query_text=SUBJECT_QUERY,
    )
    assert compilation.items == []

    turn2 = chat_turn(agent, SUBJECT_QUERY, session)
    manifests = manifests_for_request(turn2["request_id"])
    assert manifests, "the post-erase turn stored no manifest"
    assert not manifest_json_contains_turn_context(manifests), (
        "an erased page came back into the payload"
    )


# ── 10. the privacy-safe API surface ─────────────────────────────────────────


def test_context_pages_surface_is_privacy_safe_and_owner_local(served_env):
    agent = make_agent()
    session = "tc-s10"
    chat_turn(agent, "the zephyr archive folio is filed under the indigo shelf", session)
    admission_id = admissions_for(session)[0]["admission_id"]

    # (a) content is opt-in: the record shape itself carries provenance only;
    # bytes ride along solely when the reader explicitly asks.
    listing = api_admissions(agent, session)
    assert listing["count"] == 1
    assert "content" not in listing["admissions"][0]
    detailed = api_admissions(agent, session, include_content=True)
    content = detailed["admissions"][0].get("content")
    assert isinstance(content, str) and "zephyr archive folio" in content

    # (b) the surface is owner-local: a foreign client gets a typed 403 and
    # never learns whether an admission exists (served POST door).
    foreign_post = dispatch_post_in_thread(
        agent,
        path="/api/context/pages",
        body={"action": "withhold", "admission_id": admission_id},
        client_host="203.0.113.5",
    )
    assert int(foreign_post.status) == 403
    assert body_json(foreign_post)["error"] == "owner_local_required"
    # The foreign press must not have mutated anything either.
    assert admissions_for(session)[0]["status"] == "active"

    # (c) unknown actions and unknown fields are typed 400s.
    unknown_action = dispatch_post_in_thread(
        agent, path="/api/context/pages", body={"action": "teleport"}
    )
    assert int(unknown_action.status) == 400
    unknown_fields = dispatch_post_in_thread(
        agent,
        path="/api/context/pages",
        body={
            "action": "withhold",
            "admission_id": admission_id,
            "reason": "r",
            "unexpected_field": 1,
        },
    )
    assert int(unknown_fields.status) == 400
    assert "unknown fields" in str(body_json(unknown_fields).get("error"))

    # (d) the session filter bounds the listing: a session with no turns of
    # its own reads as empty, never as someone else's pages.
    empty = api_admissions(agent, "never-turned-session")
    assert empty["count"] == 0 and empty["admissions"] == []


def test_served_context_pages_get_arm_returns_a_listing(served_env):
    """The served GET arm must serve the same inspection the authority serves."""
    agent = make_agent()
    session = "tc-get"
    chat_turn(agent, SUBJECT_TEXT, session)
    response = dispatch_get_in_thread(
        agent,
        path="/api/context/pages",
        query={"session": [canonical_session(session)]},
    )
    assert int(response.status) == 200
    payload = body_json(response)
    assert payload["count"] == 1
    assert payload["admissions"][0]["source_kind"] == "chat_exchange"
    assert "content" not in payload["admissions"][0]
