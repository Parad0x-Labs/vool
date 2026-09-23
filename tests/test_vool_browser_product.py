"""C06 — the vool-browser product lane through the REAL runtime tool door.

Every test drives `core.tool_intent_executor.execute_tool_intent` exactly as a
turn does: contract lookup, permission gate, executor, receipt observation. The
browser is never imported as a library by a test and page content is never
trusted: the injection page shouts instructions and the assertions pin that
page text changed nothing.

What this file proves, per the C06 contract:

- TYPED OPS: navigate / inspect / click / type / assert / screenshot over a real
  Chromium on loopback journey pages.
- REDIRECT CONTROL: same-origin redirects ride; cross-origin redirects refuse
  under the default policy and only ride when the target origin was granted.
- BOUNDED DOWNLOAD/UPLOAD: downloads land under the session scratch with a size
  bound (oversize refuses and leaves nothing), uploads come from the session's
  staged area only (never operator paths).
- CANCELLATION AND TIMEOUTS: a hung page is a typed timeout with partial
  evidence; an in-flight journey is cancelled and the engine actually stops.
- BUDGETS: a session op budget exhausts into a typed refusal.
- RECEIPTS: every op returns a receipt with op id, origin, final URL, bounds and
  outcome; receipts persist as files the operator can read.
- PROMPT INJECTION: the injection page's grants/approvals change nothing; page
  text reaches the model wrapped as untrusted evidence.
- CONCURRENT SESSIONS: two sessions on the same origin hold independent cookies.

`served` is NOT set here: these are in-process door tests; the served /api/chat
journey lives in test_vool_browser_chat_journey.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._toolchain_fixtures import executor_kwargs, internal_scope, reset_toolchain_state
from tests._vool_browser_support import JourneyWorld, enable_browser_policy

PLUGIN = "vool-browser"


@pytest.fixture()
def browser_world(tmp_path, monkeypatch):
    """Isolated browser home per test; the runtime flag lane on."""
    from core.mode_permission_policy import reset_mode_permission_state
    from core.runtime_flags import override

    monkeypatch.delenv("VOOL_MCP_CONFIG", raising=False)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_BROWSER_SCRATCH_ROOT", str(tmp_path / "browser-scratch"))
    monkeypatch.setenv("PLAYWRIGHT_ENABLED", "1")
    enable_browser_policy(monkeypatch)
    reset_toolchain_state()
    reset_mode_permission_state()
    with override("plugin_runtime_tools", True):
        yield tmp_path
    from core.vool_browser.sessions import close_all_for_test

    close_all_for_test()
    reset_mode_permission_state()
    reset_toolchain_state()


def _run(intent: str, arguments: dict, session: str = "bx", scope=None, **context):
    """One gated tool call. Unless an explicit scope is passed, the call carries
    a bounded internal authority for THIS intent (these are door-level tests;
    the gate's own pend/deny behaviour lives in test_vool_browser_gate.py)."""
    from core.mode_permission_policy import PermissionAction, grant_internal_authority
    from core.tool_intent_executor import execute_tool_intent

    kwargs = executor_kwargs(session, **context)
    if scope is not None:
        kwargs["source_context"] = dict(scope)
    elif not context:
        token = grant_internal_authority(
            label="test.browser.lane",
            actions=(PermissionAction.CREATE_FILES, PermissionAction.USE_BROWSER),
            duration_seconds=300,
            intents=(intent,),
        )
        kwargs["source_context"] = {"internal_authority_token": token}
    return execute_tool_intent({"intent": intent, "arguments": arguments}, **kwargs)


def _open(world, site_base, session: str = "s1", **extra):
    scope = internal_scope(
        "test.browser.open", "create_files", "use_browser_or_web_retrieval",
                      intents=(f"{PLUGIN}.session.open",))
    result = _run(
        f"{PLUGIN}.session.open",
        {"session": session, "start_url": site_base + "/", **extra},
        **scope,
    )
    assert result.ok, (result.status, result.response_text[:300])
    return result


# ---------------------------------------------------------------------------
# Typed ops
# ---------------------------------------------------------------------------


def test_navigate_inspect_assert_over_a_real_page(browser_world) -> None:
    with JourneyWorld() as world:
        _open(browser_world, world.a.base)
        nav = _run(f"{PLUGIN}.navigate",
                   {"session": "s1", "url": world.a.base + "/product/1"})
        assert nav.ok, (nav.status, nav.response_text[:300])
        receipt = nav.details["observation"]["receipt"]
        assert receipt["op"] == "navigate"
        assert receipt["origin"] == world.a.origin
        assert receipt["final_url"].endswith("/product/1")

        seen = _run(f"{PLUGIN}.inspect", {"session": "s1"})
        assert seen.ok, (seen.status, seen.response_text[:300])
        page = seen.details["observation"]
        assert "WIDGET" in page["text"].upper()
        assert page["content_class"] == "untrusted_page_evidence"
        assert page["authority"] == "none"

        checks = _run(f"{PLUGIN}.assert", {"session": "s1", "checks": [
            {"kind": "url", "contains": "/product/1"},
            {"kind": "text", "contains": "19.99"},
            {"kind": "element", "selector": "h1"},
        ]})
        assert checks.ok, (checks.status, checks.response_text[:300])
        outcomes = checks.details["observation"]["checks"]
        assert [o["pass"] for o in outcomes] == [True, True, True]


def test_click_and_type_drive_real_forms(browser_world) -> None:
    with JourneyWorld() as world:
        _open(browser_world, world.a.base)
        nav = _run(f"{PLUGIN}.navigate", {"session": "s1", "url": world.a.base + "/form"})
        assert nav.ok, (nav.status, nav.response_text[:300])
        typed = _run(f"{PLUGIN}.type",
                     {"session": "s1", "selector": "input[name=answer]", "text": "42"})
        assert typed.ok, (typed.status, typed.response_text[:300])
        clicked = _run(f"{PLUGIN}.click",
                       {"session": "s1", "selector": "button[type=submit]"})
        assert clicked.ok, (clicked.status, clicked.response_text[:300])
        seen = _run(f"{PLUGIN}.inspect", {"session": "s1"})
        assert "answer was: 42" in seen.details["observation"]["text"]


def test_typed_secrets_are_masked_in_receipts(browser_world) -> None:
    with JourneyWorld() as world:
        _open(browser_world, world.a.base)
        _run(f"{PLUGIN}.navigate", {"session": "s1", "url": world.a.base + "/login"})
        secret = "hunter2-secret-value"
        typed = _run(f"{PLUGIN}.type", {
            "session": "s1", "selector": "input[name=password]", "text": secret,
            "secret": True,
        })
        assert typed.ok, (typed.status, typed.response_text[:300])
        flattened = json.dumps(json.dumps(typed.details, default=str)) + typed.response_text
        assert secret not in flattened, "a secret:true value leaked into the receipt"
        _run(f"{PLUGIN}.click", {"session": "s1", "selector": "button[type=submit]"})
        seen = _run(f"{PLUGIN}.inspect", {"session": "s1"})
        assert "welcome" in seen.details["observation"]["text"].lower()
        # the site got the real value; only the RECEIPT is masked
        login_rows = [row for row in world.a.uploaded if row.get("kind") == "login"]
        assert login_rows and login_rows[-1]["fields"]["password_len"] == len(secret)


def test_screenshot_is_bounded_and_receipted(browser_world) -> None:
    with JourneyWorld() as world:
        _open(browser_world, world.a.base)
        _run(f"{PLUGIN}.navigate", {"session": "s1", "url": world.a.base + "/"})
        shot = _run(f"{PLUGIN}.screenshot", {"session": "s1"})
        assert shot.ok, (shot.status, shot.response_text[:300])
        obs = shot.details["observation"]
        receipt = obs["receipt"]
        path = Path(receipt["path"])
        assert path.is_file() and path.stat().st_size == receipt["bytes"]
        assert receipt["sha256"] and receipt["bytes"] > 0
        assert str(browser_world) in str(path), "screenshot landed outside the browser scratch"


# ---------------------------------------------------------------------------
# Redirect control and cross-origin law
# ---------------------------------------------------------------------------


def test_same_origin_redirect_rides_and_cross_origin_redirect_refuses(browser_world) -> None:
    with JourneyWorld() as world:
        _open(browser_world, world.a.base)
        same = _run(f"{PLUGIN}.navigate",
                    {"session": "s1", "url": world.a.base + "/redirect/same"})
        assert same.ok, (same.status, same.response_text[:300])
        assert same.details["observation"]["receipt"]["final_url"].endswith("/target")

        cross = _run(f"{PLUGIN}.navigate",
                     {"session": "s1", "url": world.a.base + "/redirect/cross"})
        assert not cross.ok
        assert cross.status == "cross_origin_redirect_refused", cross.status
        assert world.b.origin in cross.response_text, "the refusal must name the target origin"
        # the engine never landed on origin B
        here = _run(f"{PLUGIN}.assert", {"session": "s1", "checks": [
            {"kind": "url", "contains": world.a.origin}]})
        assert here.ok, here.response_text[:300]


def test_a_granted_target_origin_allows_the_cross_redirect(browser_world) -> None:
    with JourneyWorld() as world:
        _open(browser_world, world.a.base)
        grant = _run(f"{PLUGIN}.permission.grant", {
            "session": "s1", "origin": world.b.origin, "permission": "navigation"})
        assert grant.ok, (grant.status, grant.response_text[:300])
        cross = _run(f"{PLUGIN}.navigate",
                     {"session": "s1", "url": world.a.base + "/redirect/cross"})
        assert cross.ok, (cross.status, cross.response_text[:300])
        assert cross.details["observation"]["receipt"]["final_url"].startswith(world.b.base)


def test_navigation_to_a_file_url_is_refused_by_name(browser_world) -> None:
    with JourneyWorld() as world:
        _open(browser_world, world.a.base)
        bad = _run(f"{PLUGIN}.navigate", {"session": "s1", "url": "file:///etc/passwd"})
        assert not bad.ok
        assert "file" in bad.response_text.lower() or "scheme" in bad.response_text.lower()


# ---------------------------------------------------------------------------
# Bounded transfers
# ---------------------------------------------------------------------------


def test_download_is_bounded_receipted_and_oversize_refuses(browser_world) -> None:
    with JourneyWorld() as world:
        _open(browser_world, world.a.base)
        got = _run(f"{PLUGIN}.download", {
            "session": "s1", "url": world.a.base + "/download.bin", "max_bytes": 200000})
        assert got.ok, (got.status, got.response_text[:300])
        receipt = got.details["observation"]["receipt"]
        path = Path(receipt["path"])
        assert path.is_file() and receipt["bytes"] == 65536
        assert str(browser_world) in str(path)

        huge = _run(f"{PLUGIN}.download", {
            "session": "s1", "url": world.a.base + "/download/huge.bin", "max_bytes": 1000000})
        assert not huge.ok
        assert "bound" in huge.response_text.lower() or "max_bytes" in huge.response_text.lower()
        leftovers = [p for p in (browser_world / "browser-scratch").rglob("*.bin")
                     if p.stat().st_size > 1000000]
        assert not leftovers, f"an oversize download left residue: {leftovers}"
        # the small, legitimate download from the same session is still kept
        kept = [p for p in (browser_world / "browser-scratch").rglob("*.bin")
                if p.stat().st_size == 65536]
        assert kept, "the bounded download artifact went missing"


def test_upload_comes_from_the_staged_area_only(browser_world) -> None:
    with JourneyWorld() as world:
        _open(browser_world, world.a.base)
        _run(f"{PLUGIN}.navigate", {"session": "s1", "url": world.a.base + "/form"})
        staged = _run(f"{PLUGIN}.upload.stage", {
            "session": "s1", "name": "notes.txt",
            "content_b64": __import__("base64").b64encode(b"staged bytes").decode(),
        })
        assert staged.ok, (staged.status, staged.response_text[:300])
        # a PATH-shaped upload argument is refused by the schema before anything runs
        pathy = _run(f"{PLUGIN}.upload", {
            "session": "s1", "selector": "input[type=file]",
            "staged": ["~/important/passwords.txt"]})
        assert not pathy.ok
        assert "staged" in pathy.response_text.lower() or "path" in pathy.response_text.lower()
        # an unknown staged name is refused by name
        missing = _run(f"{PLUGIN}.upload", {
            "session": "s1", "selector": "input[type=file]", "staged": ["nope.txt"]})
        assert not missing.ok and "nope.txt" in missing.response_text


# ---------------------------------------------------------------------------
# Timeouts, cancellation, budgets
# ---------------------------------------------------------------------------


def test_a_hung_page_is_a_typed_timeout_with_partial_evidence(browser_world) -> None:
    with JourneyWorld() as world:
        _open(browser_world, world.a.base)
        slow = _run(f"{PLUGIN}.navigate",
                    {"session": "s1", "url": world.a.base + "/slow", "timeout_seconds": 2})
        assert not slow.ok
        assert slow.status == "timeout", slow.status
        session_status = _run(f"{PLUGIN}.session.status", {"session": "s1"})
        assert session_status.ok, session_status.response_text[:300]
        assert session_status.details["observation"]["session"]["state"] in {
            "idle", "ready", "navigated"}, session_status.details["observation"]["session"]


def test_cancel_stops_the_engine_and_the_session_closes(browser_world) -> None:
    import threading

    with JourneyWorld() as world:
        _open(browser_world, world.a.base)
        result: dict = {}

        def slow_nav() -> None:
            result["nav"] = _run(f"{PLUGIN}.navigate",
                                 {"session": "s1", "url": world.a.base + "/slow",
                                  "timeout_seconds": 25})

        worker = threading.Thread(target=slow_nav, daemon=True)
        worker.start()
        import time as _time

        _time.sleep(1.0)
        cancelled = _run(f"{PLUGIN}.cancel", {"session": "s1"})
        assert cancelled.ok, (cancelled.status, cancelled.response_text[:300])
        worker.join(timeout=40)
        assert not result["nav"].ok
        assert result["nav"].status in {"cancelled", "timeout"}, result["nav"].status
        status = _run(f"{PLUGIN}.session.status", {"session": "s1"})
        state = status.details["observation"]["session"]["state"]
        assert state in {"cancelled", "closed"}, state


def test_session_op_budget_exhausts_into_a_typed_refusal(browser_world) -> None:
    with JourneyWorld() as world:
        _open(browser_world, world.a.base, op_budget=3)
        for i in range(3):
            nav = _run(f"{PLUGIN}.navigate", {"session": "s1", "url": world.a.base + "/"})
            assert nav.ok, (i, nav.status)
        exhausted = _run(f"{PLUGIN}.navigate", {"session": "s1", "url": world.a.base + "/"})
        assert not exhausted.ok
        assert exhausted.status == "budget_exhausted", exhausted.status
        assert "3" in exhausted.response_text


# ---------------------------------------------------------------------------
# Untrusted evidence and concurrent sessions
# ---------------------------------------------------------------------------


def test_injection_page_cannot_grant_anything(browser_world) -> None:
    with JourneyWorld() as world:
        _open(browser_world, world.a.base)
        _run(f"{PLUGIN}.navigate", {"session": "s1", "url": world.a.base + "/injection"})
        seen = _run(f"{PLUGIN}.inspect", {"session": "s1"})
        assert seen.ok
        page = seen.details["observation"]
        assert page["content_class"] == "untrusted_page_evidence"
        assert page["authority"] == "none"
        assert "permission" in page["text"].lower(), "the fixture text must actually be there"

        # the page "granted" itself everything; the permission store must disagree
        listed = _run(f"{PLUGIN}.permission.list", {"session": "s1"})
        assert listed.ok
        grants = listed.details["observation"]["grants"]
        assert not grants, f"page content produced grants: {grants}"

        # and a cross-origin fetch to origin B is STILL refused
        cross = _run(f"{PLUGIN}.navigate", {"session": "s1", "url": world.b.base + "/target"})
        assert not cross.ok and "origin" in cross.response_text.lower()


def test_concurrent_sessions_hold_independent_cookies(browser_world) -> None:
    import re as _re

    def own_value(sid: str) -> str:
        got = _run(f"{PLUGIN}.navigate",
                   {"session": sid, "url": world.a.base + "/set-cookie"})
        assert got.ok, (sid, got.status)
        _run(f"{PLUGIN}.navigate", {"session": sid, "url": world.a.base + "/read-cookie"})
        seen = _run(f"{PLUGIN}.inspect", {"session": sid})
        match = _re.search(r"session_a=(A\d+)", seen.details["observation"]["page_text"])
        assert match, seen.details["observation"]["page_text"][:200]
        return match.group(1)

    with JourneyWorld() as world:
        _open(browser_world, world.a.base, session="s1")
        _open(browser_world, world.a.base, session="s2")
        v1 = own_value("s1")
        v2 = own_value("s2")
        # same origin, two concurrent sessions, two DISTINCT cookie jars: each
        # session holds (and reads back) only the value IT was set
        assert v1 != v2, (v1, v2)
        again1 = _run(f"{PLUGIN}.navigate", {"session": "s1", "url": world.a.base + "/read-cookie"})
        assert again1.ok
        seen1 = _run(f"{PLUGIN}.inspect", {"session": "s1"})
        assert v1 in seen1.details["observation"]["page_text"]
        assert v2 not in seen1.details["observation"]["page_text"]


def test_session_close_deletes_the_disposable_profile_and_frees_the_name(browser_world) -> None:
    with JourneyWorld() as world:
        result = _open(browser_world, world.a.base, session="s1")
        profile = Path(result.details["observation"]["session"]["profile_dir"])
        assert profile.is_dir()
        assert str(browser_world) in str(profile), "the profile must live in the browser scratch"
        closed = _run(f"{PLUGIN}.session.close", {"session": "s1"})
        assert closed.ok, (closed.status, closed.response_text[:300])
        assert not profile.exists(), "a disposable profile survived close"
        status = _run(f"{PLUGIN}.session.status", {"session": "s1"})
        assert not status.ok and "unknown_session" in (status.status or "")


def test_receipts_persist_as_readable_files(browser_world) -> None:
    with JourneyWorld() as world:
        opened = _open(browser_world, world.a.base, session="s1")
        session = opened.details["observation"]["session"]
        _run(f"{PLUGIN}.navigate", {"session": "s1", "url": world.a.base + "/product/1"})
        receipts_path = Path(session["receipts_path"])
        assert receipts_path.is_file()
        rows = [json.loads(line) for line in receipts_path.read_text().splitlines() if line.strip()]
        ops = [row["op"] for row in rows]
        assert "session.open" in ops and "navigate" in ops, ops
        for row in rows:
            assert "op_id" in row and "ts" in row and "outcome" in row


# ---------------------------------------------------------------------------
# Start-page fail-soft (unit): the two exception shapes a start navigation can die of
# ---------------------------------------------------------------------------


class _FakeHandle:
    """The session.open surface api.handle_intent touches after open_session."""

    def __init__(self) -> None:
        self.session = "s-failsoft"
        self.primary_origin = "https://start.example"
        self.current_url = ""
        self.engine_binary = "fake-engine"
        self.headless = True
        self.profile_dir = "/tmp/fake-profile"
        self.op_budget = 200

    def touch(self, origin: str) -> None:
        self.touched = origin

    def submit(self, fn, wait_seconds: float):
        raise self.error

    def describe(self) -> dict:
        return {"session": self.session}


@pytest.mark.parametrize(
    ("error", "expected_note", "expected_outcome"),
    [
        pytest.param(
            __import__("core.vool_browser.sessions", fromlist=["OpTimeout"]).OpTimeout(
                "operation did not finish within 30s"
            ),
            "start page not reached (timeout)",
            "timeout",
            id="a-hung-start-page-is-a-typed-timeout-note",
        ),
        pytest.param(
            __import__("core.vool_browser.sessions", fromlist=["OpFailed"]).OpFailed(
                "net_refused", "the page refused the load"
            ),
            "start page not reached (net_refused)",
            "net_refused",
            id="a-refusing-start-page-keeps-its-typed-status",
        ),
    ],
)
def test_session_open_survives_a_failed_start_navigation_with_a_typed_note(
    monkeypatch, error, expected_note, expected_outcome
) -> None:
    """Fail-soft contract: a start page that refuses OR HANGS costs the journey its
    start page, never the session. The handler used to reach for ``.status``/
    ``.message`` on BOTH exception shapes — OpTimeout carries neither — so a hung
    start page crashed the fail-soft note itself with AttributeError instead of
    returning the open session with a typed note (CI shard 9, run 35869013317,
    tests/test_vool_browser_isolation.py::test_every_session_gets_a_fresh_profile...)."""
    from core.vool_browser import api

    handle = _FakeHandle()
    handle.error = error
    monkeypatch.setattr(api, "open_session", lambda **kwargs: handle)
    recorded: list[dict] = []
    monkeypatch.setattr(
        api, "record_receipt", lambda h, **receipt: recorded.append(receipt) or receipt
    )

    result = api.handle_intent(
        "vool-browser.session.open",
        {"session": "s-failsoft", "start_url": "https://start.example/"},
    )

    assert result.ok is True, (result.status, result.response_text[:300])
    assert result.status == "executed"
    assert expected_note in result.response_text
    nav = next(r for r in recorded if r.get("op") == "navigate")
    assert nav["outcome"] == expected_outcome
