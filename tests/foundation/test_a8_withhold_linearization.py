"""A8 — WITHHOLD must linearize against every public-surface writer.

F18 (`test_a8_final_freeze_served::test_f18_concurrent_writers_vs_withhold_never_serve`) fails
roughly a third of its runs: after WITHHOLD has definitively won, the served hive listing still
contains the governed bytes. A pass rate is not a diagnosis, so this module makes the window
DETERMINISTIC and records what each participant saw, in order.

The window
----------
`set_availability(fid, WITHHELD)` collects the derivative bindings for the meet surfaces with a
READ-ONLY scan (`governed_meet_derivative_keys`) and only afterwards opens the guarded
transaction that flips the row to WITHHELD and binds those keys. The public write fence
(`create_post_record`) takes `A8_TRAVERSAL_LOCK`, consults `writer_may_publish_public_text`, and
writes -- but the WITHHELD transition never took that lock. So a writer can land a post in the
gap between the scan and the commit:

    scan (post not yet written)  ->  post committed, quote unbound  ->  CAS flips to WITHHELD

After the flip the fence refuses new writes, so nothing ever binds that post's quote. Its body is
not byte-equal to the payload, so the exact-hash gate misses it; with no derivative binding the
serve gate resolves it as ungoverned legacy and serves it forever. The narrowness of the window
is the whole reason this reads as ~35-40% rather than as a constant leak.

The barrier
-----------
`governed_meet_derivative_keys` is wrapped so the transition parks between its scan and its
commit while a second thread drives a REAL post through the REAL served frontdoor. The wait is
bounded, and the bound is what makes the test terminate under BOTH behaviours:

* unlinearized -- the writer completes inside the window, and the leak is observed on the wire;
* linearized   -- the writer blocks on the lock the transition now holds, the barrier times out
                  (that timeout IS the proof of serialization), the writer then meets a WITHHELD
                  payload and is refused.

The bounded wait belongs to the harness. The fix under test uses no sleep, no poll and no retry.
"""
from __future__ import annotations

import contextlib
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

import storage.db as sdb

P_EXACT = "the vault passphrase is 7F3-QX9-2210"
P_QUOTE = "Analysis follows: " + P_EXACT + " — treat as the canonical finding."
OTHER_EXACT = "the spare passphrase is QQ2-LL8-4417"
OTHER_QUOTE = "Unrelated note: " + OTHER_EXACT + " — different lineage entirely."
AGENT = "agent-linear-000000000001"

# The barrier's bound. Long enough that an unlinearized writer always finishes inside it (the
# unfixed path completes in single-digit milliseconds), short enough that a linearized run is
# never slow. Nothing in the product waits on this number.
BARRIER_TIMEOUT_S = 5.0


# --------------------------------------------------------------------------- trace


class Trace:
    """Ordered observations. Every entry names the thread that made it, so a claim about who
    saw what is read off the tape rather than assumed from the code's shape."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.events: list[dict] = []
        self._t0 = time.monotonic()

    def record(self, event: str, **fields) -> None:
        with self._lock:
            self.events.append(
                {
                    "seq": len(self.events),
                    "ms": round((time.monotonic() - self._t0) * 1000, 3),
                    "event": event,
                    "thread": threading.current_thread().name,
                    "thread_id": threading.get_ident(),
                    **fields,
                }
            )

    def names(self) -> list[str]:
        with self._lock:
            return [e["event"] for e in self.events]

    def first(self, event: str) -> dict | None:
        with self._lock:
            return next((e for e in self.events if e["event"] == event), None)

    def index(self, event: str) -> int:
        names = self.names()
        return names.index(event) if event in names else -1

    def dump(self) -> str:
        with self._lock:
            return "\n".join(json.dumps(e, sort_keys=True) for e in self.events)


# --------------------------------------------------------------------------- rig


@pytest.fixture()
def a8_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_MIRROR_DATA_DIR", str(home / "relay_mirror"))
    from core.runtime_paths import configure_runtime_home

    configure_runtime_home(home)
    sdb.configure_default_db_path(tmp_path / "a8-linear.db")
    from storage.migrations import run_migrations

    run_migrations()
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path

    configure_runtime_continuity_db_path(active_default_db_path())
    from core.conductor.obligation_ledger import clear_active_set

    clear_active_set()
    from core.semantic.semantic_result_seam import reset_admission

    reset_admission()
    yield home
    from core.semantic.semantic_admissions import clear_execution_context

    clear_execution_context()
    sdb.configure_default_db_path(None)
    configure_runtime_home(None)


@contextlib.contextmanager
def _request_scope(request_id):
    from core.semantic.semantic_admissions import _CURRENT_REQUEST_ID, set_request_context

    token = set_request_context(request_id)
    try:
        yield
    finally:
        _CURRENT_REQUEST_ID.reset(token)


_turn_seq = [0]


def _admit_finalize(text, request_id=""):
    from core.finalization import finalize_answer
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

    reset_admission()
    _turn_seq[0] += 1
    turn = f"lin-t{_turn_seq[0]}"
    admit_semantic_result({"response": text, "route_reason": "model_lane"}, turn_id=turn)
    with _request_scope(request_id):
        return finalize_answer(turn_id=turn, canonical_content=text)


class _MeetHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        from apps.meet_and_greet_server import dispatch_request as meet_dispatch

        parsed = urlparse(self.path)
        status, payload = meet_dispatch("GET", parsed.path, parse_qs(parsed.query), {}, None)
        self._write(status, payload)

    def do_POST(self) -> None:
        from apps.meet_and_greet_server import dispatch_request as meet_dispatch

        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {}
        status, payload = meet_dispatch("POST", parsed.path, {}, body, None)
        self._write(status, payload)

    def _write(self, status, payload) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args) -> None:
        pass


@contextlib.contextmanager
def _served():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _MeetHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True, name="meet-server")
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _get(base: str, path: str) -> tuple[int, str]:
    req = urllib.request.Request(base + path)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return int(resp.status), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8", "replace")


def _post(base: str, path: str, body: dict) -> tuple[int, str]:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return int(resp.status), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8", "replace")


_topic_seq = [0]


def _create_topic(base: str) -> str:
    # The hive refuses a duplicate title from the same agent, so each topic gets its own.
    _topic_seq[0] += 1
    status, body = _post(
        base,
        "/v1/hive/topics",
        {
            "created_by_agent_id": AGENT,
            "title": f"A8 linearization topic {_topic_seq[0]}",
            "summary": "Linearization reproof topic summary",
        },
    )
    assert status == 200, body
    return str((json.loads(body).get("result") or {}).get("topic_id") or "")


def _post_quote(base: str, topic: str, quote: str) -> tuple[int, str]:
    return _post(
        base,
        "/v1/hive/posts",
        {"topic_id": topic, "author_agent_id": AGENT, "body": quote},
    )


# --------------------------------------------------------------------------- the barrier


def _park_transition_between_scan_and_commit(monkeypatch, trace, scanned, writer_done):
    """Wrap the meet-derivative scan so the WITHHELD transition parks between its read-only
    collection and its guarded commit — the exact window a public writer must not fit inside."""
    import core.finalization as fin

    real = fin.governed_meet_derivative_keys

    def _wrapped(content_hash, plaintext=""):
        trace.record("withhold.scan.start", content_hash=str(content_hash)[:16])
        keys = real(content_hash, plaintext)
        trace.record("withhold.scan.done", bound_keys=len(keys))
        scanned.set()
        entered = writer_done.wait(timeout=BARRIER_TIMEOUT_S)
        trace.record(
            "withhold.window.closed",
            writer_completed_inside_window=bool(entered),
            serialized=not entered,
        )
        return keys

    monkeypatch.setattr(fin, "governed_meet_derivative_keys", _wrapped)
    return real


# --------------------------------------------------------------------------- P0


def test_a_writer_cannot_land_a_quote_inside_the_withhold_window(a8_env, monkeypatch):
    """THE root-cause test. A post is driven through the real served frontdoor at the precise
    moment the WITHHOLD transition sits between its derivative scan and its commit.

    Whatever the scheduler does, one of exactly two things must be true afterwards, and both are
    asserted from the served wire, not from store internals:

      * the writer was refused (the transition had already won), or
      * the writer's post was admitted AND its quote is bound, so the serve gate withholds it.

    The defect is the third outcome: admitted, unbound, and served forever.
    """
    from core.finalization import AVAILABILITY_WITHHELD, set_availability

    trace = Trace()
    scanned = threading.Event()
    writer_done = threading.Event()
    writer_result: dict = {}

    with _served() as meet:
        topic = _create_topic(meet)
        commit = _admit_finalize(P_EXACT, request_id="linear-p0")
        fid = commit["finalization_id"]

        status, before = _get(meet, f"/v1/hive/topics/{topic}/posts")
        trace.record("baseline.read", status=status, carries_payload=P_EXACT in before)

        def _writer():
            # Wait for the transition to reach its window, then drive a REAL served write.
            if not scanned.wait(timeout=BARRIER_TIMEOUT_S):
                trace.record("writer.abandoned", reason="scan never signalled")
                writer_done.set()
                return
            trace.record("writer.request.start")
            code, body = _post_quote(meet, topic, P_QUOTE)
            writer_result["status"] = code
            writer_result["body"] = body
            trace.record("writer.request.done", status=code, admitted=code == 200)
            writer_done.set()

        writer = threading.Thread(target=_writer, name="racing-writer")
        _park_transition_between_scan_and_commit(monkeypatch, trace, scanned, writer_done)

        writer.start()
        trace.record("withhold.call.start", finalization_id=fid)
        transitioned = set_availability(fid, AVAILABILITY_WITHHELD, reason="linearization")
        trace.record("withhold.call.done", transitioned=bool(transitioned))
        writer.join(timeout=30)
        assert not writer.is_alive(), f"writer never finished — unbounded wait\n{trace.dump()}"
        assert transitioned, f"WITHHOLD refused\n{trace.dump()}"

        status, served = _get(meet, f"/v1/hive/topics/{topic}/posts")
        trace.record(
            "final.read",
            status=status,
            carries_payload=P_EXACT in served,
            observable=P_EXACT in served,
        )

    # The window closed before the read: the bytes' observability is decided after the transition.
    assert trace.index("withhold.call.done") < trace.index("final.read"), trace.dump()
    assert status == 200, trace.dump()

    admitted = writer_result.get("status") == 200
    if admitted:
        # Admitted inside the window is legal ONLY if its quote was bound: the serve gate must
        # withhold it. This is the assertion the unlinearized runtime fails.
        assert P_EXACT not in served, (
            "A post admitted inside the WITHHOLD window was served after WITHHOLD won — its "
            "quote was never bound to the governing finalization.\n" + trace.dump()
        )
    else:
        assert P_EXACT not in served, (
            "The writer was refused, yet governed bytes still served.\n" + trace.dump()
        )

    # Whichever branch ran, the payload is not externally observable.
    assert P_EXACT not in served, trace.dump()
    assert trace.first("withhold.window.closed") is not None, trace.dump()


def test_the_withhold_window_is_closed_to_public_writers(a8_env, monkeypatch):
    """The linearization point itself, stated positively: a writer that starts inside the window
    cannot COMPLETE inside it. Post-fix the barrier times out because the writer is parked on the
    same authority the transition holds; that timeout is the observation, not a delay."""
    from core.finalization import AVAILABILITY_WITHHELD, set_availability

    trace = Trace()
    scanned = threading.Event()
    writer_done = threading.Event()

    with _served() as meet:
        topic = _create_topic(meet)
        fid = _admit_finalize(P_EXACT, request_id="linear-window")["finalization_id"]

        def _writer():
            if not scanned.wait(timeout=BARRIER_TIMEOUT_S):
                writer_done.set()
                return
            trace.record("writer.request.start")
            code, _ = _post_quote(meet, topic, P_QUOTE)
            trace.record("writer.request.done", status=code)
            writer_done.set()

        writer = threading.Thread(target=_writer, name="racing-writer")
        _park_transition_between_scan_and_commit(monkeypatch, trace, scanned, writer_done)
        writer.start()
        assert set_availability(fid, AVAILABILITY_WITHHELD, reason="window")
        writer.join(timeout=30)
        assert not writer.is_alive(), f"writer never finished\n{trace.dump()}"

        status, served = _get(meet, f"/v1/hive/topics/{topic}/posts")

    closed = trace.first("withhold.window.closed")
    assert closed is not None, trace.dump()
    assert closed["serialized"] is True, (
        "A public write COMPLETED between the WITHHOLD scan and its commit. That gap is the "
        "defect: the scan cannot see the post, and after the commit the fence refuses to write "
        "it, so nothing ever binds its quote.\n" + trace.dump()
    )
    assert status == 200 and P_EXACT not in served, trace.dump()


def test_the_scan_and_the_commit_are_one_authority(a8_env, monkeypatch):
    """Connection/thread identity and commit visibility, recorded rather than assumed: the
    transition's scan and its commit run on ONE thread, and the payload is unreadable to a
    reader on a DIFFERENT connection the moment the call returns."""
    import core.finalization as fin
    from core.finalization import AVAILABILITY_WITHHELD, set_availability

    trace = Trace()
    fid_holder: dict = {}
    real = fin.governed_meet_derivative_keys

    def _wrapped(content_hash, plaintext=""):
        from storage.db import get_connection

        conn = get_connection()
        try:
            trace.record("scan.connection", conn_id=id(conn))
        finally:
            conn.close()
        return real(content_hash, plaintext)

    monkeypatch.setattr(fin, "governed_meet_derivative_keys", _wrapped)

    with _served() as meet:
        topic = _create_topic(meet)
        fid_holder["fid"] = _admit_finalize(P_EXACT, request_id="linear-ident")["finalization_id"]
        assert _post_quote(meet, topic, P_QUOTE)[0] == 200

        status, before = _get(meet, f"/v1/hive/topics/{topic}/posts")
        assert status == 200 and P_EXACT in before, "AVAILABLE payload must serve before WITHHOLD"
        trace.record("pre.visible", carries_payload=True)

        assert set_availability(fid_holder["fid"], AVAILABILITY_WITHHELD, reason="ident")
        trace.record("commit.returned")

        # A reader on its own connection, in another thread, immediately after the commit.
        seen: dict = {}

        def _reader():
            code, body = _get(meet, f"/v1/hive/topics/{topic}/posts")
            seen["status"] = code
            seen["carries_payload"] = P_EXACT in body
            trace.record("reader.result", **seen)

        reader = threading.Thread(target=_reader, name="post-commit-reader")
        reader.start()
        reader.join(timeout=30)
        assert not reader.is_alive(), trace.dump()

    scan_event = trace.first("scan.connection")
    assert scan_event is not None, trace.dump()
    assert scan_event["thread"] == trace.first("pre.visible")["thread"], (
        "the scan ran off the transition's own thread\n" + trace.dump()
    )
    assert seen["status"] == 200, trace.dump()
    assert seen["carries_payload"] is False, (
        "commit visibility: a reader on another connection still saw the payload after the "
        "transition returned\n" + trace.dump()
    )


# --------------------------------------------------------------------------- invariants


def test_available_only_reads_still_serve(a8_env):
    """The gate withholds; it does not simply refuse. An AVAILABLE payload and an unrelated
    body both serve normally, before and after an unrelated transition."""
    with _served() as meet:
        topic = _create_topic(meet)
        _admit_finalize(P_EXACT, request_id="linear-avail")
        assert _post_quote(meet, topic, P_QUOTE)[0] == 200
        status, served = _get(meet, f"/v1/hive/topics/{topic}/posts")
        assert status == 200 and P_EXACT in served, "AVAILABLE payload must serve"

        plain = "An ordinary post with no governed bytes at all."
        assert _post_quote(meet, topic, plain)[0] == 200
        status, served = _get(meet, f"/v1/hive/topics/{topic}/posts")
        assert status == 200 and plain in served and P_EXACT in served


def test_withheld_dominates_available_for_identical_hashes(a8_env):
    """Two finalizations over the SAME bytes: withholding either one withholds the bytes. The
    serve gate resolves the payload, not whichever sibling row it happened to read first."""
    from core.finalization import (
        AVAILABILITY_WITHHELD,
        payload_availability_for_text,
        set_availability,
    )

    with _served() as meet:
        topic = _create_topic(meet)
        first = _admit_finalize(P_EXACT, request_id="linear-dom-1")["finalization_id"]
        second = _admit_finalize(P_EXACT, request_id="linear-dom-2")["finalization_id"]
        assert first != second
        assert _post_quote(meet, topic, P_QUOTE)[0] == 200

        status, served = _get(meet, f"/v1/hive/topics/{topic}/posts")
        assert status == 200 and P_EXACT in served

        # Withhold the SECOND: the first row is still AVAILABLE and must not outvote it.
        assert set_availability(second, AVAILABILITY_WITHHELD, reason="dominance")
        assert payload_availability_for_text(P_EXACT) == AVAILABILITY_WITHHELD, (
            "an AVAILABLE sibling outvoted a WITHHELD one for identical bytes"
        )
        status, served = _get(meet, f"/v1/hive/topics/{topic}/posts")
        assert status == 200, served
        assert P_EXACT not in served, "identical-hash sibling disclosed WITHHELD bytes"


def test_separate_hashes_and_sessions_stay_isolated(a8_env):
    """Withholding one payload must not withhold a different one, and must not take an
    unrelated topic's posts down with it."""
    from core.finalization import AVAILABILITY_WITHHELD, set_availability

    with _served() as meet:
        topic_a = _create_topic(meet)
        topic_b = _create_topic(meet)
        governed = _admit_finalize(P_EXACT, request_id="linear-iso-a")["finalization_id"]
        _admit_finalize(OTHER_EXACT, request_id="linear-iso-b")

        assert _post_quote(meet, topic_a, P_QUOTE)[0] == 200
        assert _post_quote(meet, topic_b, OTHER_QUOTE)[0] == 200

        assert set_availability(governed, AVAILABILITY_WITHHELD, reason="isolation")

        status_a, served_a = _get(meet, f"/v1/hive/topics/{topic_a}/posts")
        status_b, served_b = _get(meet, f"/v1/hive/topics/{topic_b}/posts")

    assert status_a == 200 and P_EXACT not in served_a, "governed payload leaked"
    assert status_b == 200, served_b
    assert OTHER_EXACT in served_b, "an unrelated payload was withheld by association"
    assert P_EXACT not in served_b


def test_no_deadlock_when_writers_and_transitions_interleave(a8_env):
    """Writers and transitions contend for the same authority from both directions. The whole
    interleaving must complete well inside a bound; a lock-order inversion would hang here."""
    from core.finalization import AVAILABILITY_WITHHELD, set_availability

    done = threading.Event()
    errors: list[str] = []

    with _served() as meet:
        topic = _create_topic(meet)
        fids = [
            _admit_finalize(f"{P_EXACT} #{i}", request_id=f"linear-dead-{i}")["finalization_id"]
            for i in range(6)
        ]
        for i in range(6):
            assert _post_quote(meet, topic, f"Quote: {P_EXACT} #{i} — noted.")[0] == 200

        def _writers():
            try:
                for i in range(20):
                    _post_quote(meet, topic, f"ordinary body {i}")
            except Exception as exc:  # pragma: no cover - surfaced via errors
                errors.append(f"writer: {type(exc).__name__}: {exc}")
            finally:
                done.set()

        thread = threading.Thread(target=_writers, name="interleaved-writer")
        started = time.monotonic()
        thread.start()
        for fid in fids:
            set_availability(fid, AVAILABILITY_WITHHELD, reason="interleave")
        thread.join(timeout=60)
        elapsed = time.monotonic() - started

    assert not thread.is_alive(), "writer thread never completed — deadlock or unbounded wait"
    assert done.is_set()
    assert not errors, errors
    assert elapsed < 60, f"interleaving took {elapsed:.1f}s — unbounded wait"


@pytest.mark.parametrize("attempt", range(30))
def test_adversarial_concurrent_repetition_never_serves_governed_bytes(a8_env, attempt):
    """30 adversarial repetitions of F18's own shape: three hammering writers against a
    transition, driven over real sockets. Every repetition must end with the bytes unobservable.
    One flake here is a failure — this is the pass rate F18 could not hold."""
    from core.finalization import AVAILABILITY_WITHHELD, set_availability

    state = {"withheld": False, "leak_after": False}

    with _served() as meet:
        topic = _create_topic(meet)
        fid = _admit_finalize(P_EXACT, request_id=f"linear-adv-{attempt}")["finalization_id"]

        def _hammer():
            for _ in range(12):
                _post_quote(meet, topic, P_QUOTE)
                status, served = _get(meet, f"/v1/hive/topics/{topic}/posts")
                if state["withheld"] and status == 200 and P_EXACT in served:
                    state["leak_after"] = True
                    return

        threads = [threading.Thread(target=_hammer, name=f"hammer-{i}") for i in range(3)]
        for t in threads:
            t.start()
        assert set_availability(fid, AVAILABILITY_WITHHELD, reason="adversarial")
        state["withheld"] = True
        for t in threads:
            t.join(timeout=30)
        assert not any(t.is_alive() for t in threads), "hammer thread hung"

        status, served = _get(meet, f"/v1/hive/topics/{topic}/posts")

    assert status == 200
    assert P_EXACT not in served, f"attempt {attempt}: governed bytes served after WITHHOLD won"
    assert not state["leak_after"], f"attempt {attempt}: governed bytes served mid-race"
