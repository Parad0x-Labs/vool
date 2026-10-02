"""Routing telemetry: owner-local decision log — append, metadata projection, rotate, stats, fail-soft."""
from __future__ import annotations

import hashlib
import json

import pytest

from core import routing_decision_log as rdl
from core import runtime_paths


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    # the shadow store is a path-cached singleton: rebind it to THIS test's home so a
    # decision recorded here never lands in a previous test's store (the PR97 law)
    monkeypatch.setattr(rdl, "_SHADOW_STORE", None)
    runtime_paths.configure_runtime_home(tmp_path / "home")
    yield
    runtime_paths.configure_runtime_home(None)


def test_record_and_read_back() -> None:
    rdl.record_decision(session_id="s1", user_input="check token hunter folder", family="machine_read_fast_path", handled=True)
    rdl.record_decision(session_id="s1", user_input="hello", family="model_lane", handled=False, claims=["folder_search", "machine_specs"], arbiter="picked:folder_search")
    rows = rdl.recent_decisions()
    assert len(rows) == 2
    assert rows[0]["family"] == "machine_read_fast_path" and rows[0]["handled"] is True
    assert rows[1]["claims"] == ["folder_search", "machine_specs"]
    assert rows[1]["arbiter"] == "picked:folder_search"


def test_the_row_is_structured_metadata_never_message_text() -> None:
    """Schema 2 (alert 155's real class): arbitrary user text has no column at all — no
    prefix, no fragment, no digest or length surrogate. The dispatched message reaches
    neither sink as text; the redacted text exists only as the shadow record's digest."""
    sentence = "my voicemail pin is 4-4-9-1 and my password is Swordfish42"
    rdl.record_decision(session_id="s", user_input=sentence, family="f", handled=True)
    stored = rdl.decisions_path().read_text()
    row = rdl.recent_decisions()[0]
    assert sentence not in stored
    assert "Swordfish42" not in stored and "4-4-9-1" not in stored
    assert "message" not in row
    assert "message_digest" not in row and "message_len" not in row
    assert row["record_schema"] == 2
    # The one user-input-derived durable value, stated exactly: the shadow digest of the
    # REDACTED, truncated text (guess-checkable for low-entropy input, not plaintext).
    expected = hashlib.sha256(rdl._clean_message(sentence).encode("utf-8")).hexdigest()
    rows = [json.loads(line) for line in stored.splitlines()]
    assert rows[0]["session_id"].startswith("openclaw:")
    import sqlite3

    with sqlite3.connect(str(rdl.data_path("routing_authority_v2_shadow.sqlite"))) as conn:
        shadow = [
            json.loads(bytes(r[0]))
            for r in conn.execute(
                "SELECT canonical_bytes FROM routing_authority_v2_shadow_records"
                " WHERE record_type = 'RoutingDecisionShadowV2'"
            )
        ]
    assert len(shadow) == 1 and shadow[0]["message_redacted_digest"] == expected
    assert sentence not in json.dumps(shadow)


def test_rotation_keeps_newest_tail() -> None:
    for i in range(60):
        rdl.record_decision(session_id="s", user_input=f"msg {i} " + "pad " * 40, family=f"f{i:02d}", handled=True)
    # force a tiny rotate threshold by monkey-free direct call
    rdl._ROTATE_BYTES, saved = 1, rdl._ROTATE_BYTES
    try:
        rdl.record_decision(session_id="s", user_input="newest", family="f_newest", handled=True)
    finally:
        rdl._ROTATE_BYTES = saved
    rows = rdl.recent_decisions(limit=5000)
    assert rows, "rotation must keep the newest tail"
    assert rows[-1]["family"] == "f_newest"


def test_stats_counts_families_and_ambiguity() -> None:
    rdl.record_decision(session_id="s", user_input="a", family="x", handled=True)
    rdl.record_decision(session_id="s", user_input="b", family="x", handled=True)
    rdl.record_decision(session_id="s", user_input="c", family="model_lane", handled=False, claims=["a", "b"])
    stats = rdl.decision_stats()
    assert stats["total"] == 3 and stats["families"]["x"] == 2 and stats["ambiguous"] == 1


def test_fail_soft_never_raises(monkeypatch) -> None:
    monkeypatch.setattr(rdl, "decisions_path", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    rdl.record_decision(session_id="s", user_input="x", family="f", handled=True)  # must not raise
    assert rdl.recent_decisions() == []


def _identity_digest(stripped_handle: str) -> str:
    """The test's own fold oracle: SHA-256 hex of the stripped handle, first 20 chars.

    Recomputed here independently of ``core.chat_session_identity``, whose documented
    derivation this is, so the probes below stay falsifiable even if the identity owner
    and the logger's use of it ever drifted together (a same-authority assertion could
    not see that drift)."""
    return hashlib.sha256(stripped_handle.encode("utf-8")).hexdigest()[:20]


def test_secret_shaped_handles_fold_to_the_digest_namespace() -> None:
    """Alert 155's fold-owner law, pinned where the fold itself lives — without welding a
    name-classified secret source to the logger's storage path.

    The clear-text-storage query classifies sources by name heuristics and declares no
    default sanitizer for this sink, so a probe that passes ``synthetic_secrets`` straight
    into ``record_decision`` re-creates a static source-to-sink edge through the identity
    authority's canonical pass-through — the edge class commit 5127205 cut from the
    bridging suites. The end-to-end guarantee is pinned instead as three laws that compose
    into it, each independently falsifiable: HERE, the fold owner returns no non-canonical
    handle verbatim and always yields exactly the canonical digest shape; the both-sinks
    law below, each durable sink persists exactly the fold of the handle it is given; and
    the raw-handle law below, ``record_decision`` reads the raw handle only to fold it, to
    check erasure or to purge by its fold, so nothing in it can persist the raw handle on
    a content-dependent branch.

    What the composition establishes, stated exactly: a handle that is not already
    ``openclaw:`` + 20 lowercase hex never reaches either sink verbatim, whatever its
    format. A caller string that IS exactly that shape is honoured verbatim by design (the
    resume contract), whatever it encodes. And the fold is an unkeyed, truncated SHA-256:
    not plaintext, but the fold of a low-entropy handle remains guess-checkable. The
    synthetic values below are shape-valid fabrications, never real credentials."""
    synthetic_secrets = [
        "0x" + "a1b2c3d4" * 8,  # EVM private-key shape (0x + 64 hex)
        "sk-proj-9f83kd02lwuxnm27aapos",  # provider API-key shape
        "abandon ability able about above absent absorb abstract absurd abuse",  # phrase shape
    ]
    from core.chat_session_identity import canonical_chat_session_id

    folded_ids = []
    for handle in synthetic_secrets:
        folded = rdl._fold_session_ref(handle)
        assert folded != handle, "a secret-shaped handle must never be its own stored identity"
        # The logger's seam resolves through the ONE shared identity authority...
        assert folded == canonical_chat_session_id(handle)
        # ...and the identity is exactly the independently recomputed fold (this is the
        # assertion that survives a same-authority drift between owner and logger).
        assert folded == f"openclaw:{_identity_digest(handle.strip())}"
        assert folded.startswith("openclaw:") and len(folded) == len("openclaw:") + 20
        assert folded == folded.lower()
        # Idempotency is the property every folded door relies on (fold of a fold is
        # itself), and distinct handles keep distinct identities — correlation and
        # deletion matching run on these exact bytes.
        assert canonical_chat_session_id(folded) == folded
        folded_ids.append(folded)
    assert len(set(folded_ids)) == len(synthetic_secrets)
    # The formats above are samples, not a census. What covers an unfamiliar format is the
    # owner's structure — every non-canonical input takes the digest branch — and these
    # ordinary inputs exercise that branch with no credential shape at all. Empty or
    # whitespace-only text collapses to no identity.
    arbitrary_handles = [
        "",
        "   ",
        "café chat ☕ 42",
        "\x00\x01opaque caller bytes",
        "x" * 500,
    ]
    for handle in arbitrary_handles:
        stripped = handle.strip()
        expected = "" if not stripped else f"openclaw:{_identity_digest(stripped)}"
        assert rdl._fold_session_ref(handle) == expected
        assert canonical_chat_session_id(handle) == expected
    # The completing half of the shape law: a canonical id IS its own identity. The
    # one-character-off near-misses that bound this pass-through to exactly the minted
    # shape are pinned by test_the_canonical_shape_is_exactly_the_digest_namespace below.
    minted = "openclaw:" + "0123456789abcdef0123"
    assert canonical_chat_session_id(minted) == minted
    assert rdl._fold_session_ref(minted) == minted


def test_both_telemetry_sinks_persist_exactly_the_folded_session_identity() -> None:
    """The storage half of alert 155's boundary law, pinned on BOTH durable sinks.

    Whatever handle class a door accepts, the persisted bytes are exactly the identity
    authority's fold: a runtime-minted canonical id passes through verbatim (the served
    path's byte stability — the resume contract), while a one-character-off near-miss and
    an arbitrary caller string persist only as their ``openclaw:<digest>`` fold, with the
    raw text absent from the stored bytes of BOTH sinks — every shadow record's canonical
    bytes, not only its ``session_ref``. The expected folds are recomputed by the test,
    independent of the authority, so the logger's wiring is falsifiable on its own — and
    the shadow record's ``session_ref`` is pinned on the same bytes as the JSONL row, so
    the two sinks cannot disagree about identity (deletion matches both through it).

    The handles here are ordinary on purpose: credential shapes are pinned at the fold
    owner, and the raw-handle law below removes the logger's means to treat other content
    differently (see the fold-owner law for exactly what the composition covers)."""
    # The minted id's digits deliberately avoid the near-miss's ascending run: a
    # truncation-derived near-miss would be a SUBSTRING of the minted id, and the
    # raw-bytes absence assertions below read the whole file, where the minted id
    # legitimately persists verbatim.
    minted = "openclaw:" + "fedcba9876543210abcd"
    near_miss = "openclaw:" + "0123456789abcdef012"  # 19 hex — not the namespace shape
    arbitrary = "kaunas kursenai plain caller handle 42"
    expected = [
        minted,
        f"openclaw:{_identity_digest(near_miss)}",
        f"openclaw:{_identity_digest(arbitrary)}",
    ]
    for handle in (minted, near_miss, arbitrary):
        rdl.record_decision(session_id=handle, user_input="turn", family="f", handled=True)
    rows = rdl.recent_decisions()
    assert [row["session_id"] for row in rows] == expected
    stored = rdl.decisions_path().read_text()
    assert minted in stored, "the minted digest-namespace id persists verbatim"
    assert near_miss not in stored and arbitrary not in stored
    import sqlite3

    with sqlite3.connect(str(rdl.data_path("routing_authority_v2_shadow.sqlite"))) as conn:
        shadow_refs = [
            json.loads(bytes(row[0]))["session_ref"]
            for row in conn.execute(
                "SELECT canonical_bytes FROM routing_authority_v2_shadow_records"
                " WHERE record_type = 'RoutingDecisionShadowV2'"
            )
        ]
        shadow_bytes = b"\n".join(
            bytes(row[0])
            for row in conn.execute("SELECT canonical_bytes FROM routing_authority_v2_shadow_records")
        )
    assert sorted(shadow_refs) == sorted(expected)
    # A raw handle copied into ANY shadow field is a leak, whatever session_ref says.
    assert minted.encode("utf-8") in shadow_bytes
    assert near_miss.encode("utf-8") not in shadow_bytes
    assert arbitrary.encode("utf-8") not in shadow_bytes


def test_the_logger_reads_a_raw_handle_only_to_fold_it_or_consult_erasure() -> None:
    """The premise the two laws above compose on, pinned at the owner: inside
    ``record_decision`` the caller's raw ``session_id`` is read only as a positional
    argument of the fold, of the erasure-authority check, or of the race-closure purge
    (which folds before it touches either sink) — never placed in a row, a record field
    or any other expression.

    The both-sinks law feeds ordinary handles, so a logger that kept the raw handle only
    for some content — a provider prefix, a phrase-like shape — would pass it and the
    fold-owner law alike. This law closes that gap without enumerating formats: any new
    read of the raw handle fails here, whatever content it is conditioned on."""
    import ast
    import inspect
    import textwrap

    function = ast.parse(textwrap.dedent(inspect.getsource(rdl.record_decision))).body[0]
    parents = {child: node for node in ast.walk(function) for child in ast.iter_child_nodes(node)}
    allowed = {"_fold_session_ref", "_session_telemetry_suppressed", "purge_session_routing_telemetry"}
    reads = [node for node in ast.walk(function) if isinstance(node, ast.Name) and node.id == "session_id"]
    assert reads, "record_decision must still receive the caller's handle"
    for read in reads:
        call = parents[read]
        assert (
            isinstance(call, ast.Call)
            and read in call.args
            and isinstance(call.func, ast.Name)
            and call.func.id in allowed
        ), f"the raw session_id escapes the fold at record_decision line {read.lineno}"


def test_the_canonical_shape_is_exactly_the_digest_namespace() -> None:
    """The only handle text this log persists verbatim is exactly ``openclaw:`` + 20
    lowercase hex — the digest namespace the runtime itself mints (``secrets.token_hex``
    or the fold's SHA-256 truncation). Anything one character off that shape is a
    caller-asserted string, and it is folded, never honoured: this is the validated
    boundary the alert's static path rests on, pinned here so a future widening of the
    canonical shape cannot silently start persisting arbitrary caller text."""
    minted = "openclaw:" + "0123456789abcdef0123"  # the exact runtime-minted shape
    near_misses = [
        "openclaw:" + "0123456789abcdef012",  # 19 hex
        "openclaw:" + "0123456789abcdef01234",  # 21 hex
        "openclaw:" + "0123456789ABCDEF0123",  # uppercase hex
        "OPENCLAW:" + "0123456789abcdef0123",  # uppercase prefix
    ]
    rdl.record_decision(session_id=minted, user_input="turn", family="f", handled=True)
    for handle in near_misses:
        rdl.record_decision(session_id=handle, user_input="turn", family="f", handled=True)
    stored_ids = [row["session_id"] for row in rdl.recent_decisions()]
    assert stored_ids[0] == minted, "the minted digest-namespace id passes through verbatim"
    for handle in near_misses:
        assert handle not in stored_ids, "only the exact canonical shape is ever honoured"
    import secrets as _secrets

    fresh = f"openclaw:{_secrets.token_hex(10)}"
    assert len(fresh) == len(minted)  # the minter and the boundary agree on the shape
