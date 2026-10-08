"""Cold browser → real daemon → certified scripted provider, with existing history.

Set VOOL_STARTUP_APP_ROOT to a built Contents/Resources/app to exercise the
packaged source and interpreter. No cloud calls or local model launches.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from urllib.request import urlopen

from core.agent_runtime import fast_paths_utility as greetings
from tests import _reader_served_rig as rig
from tests import served_browser

# The greeting pool also includes "Hello!". Match the scripted provider's own
# reply so a greeting cannot satisfy the wait before the queued turn executes.
PROVIDER_REPLY = "Hello! How can I help you today?"
PROVIDER_REPLY_MARK = "How can I help you today?"


def _production_greeting_prefixes(phrase: str, monkeypatch) -> tuple[str, ...]:
    """Every opener the production selector can draw for ``phrase`` at any hour of the day.

    ``build_greeting_reply`` may weave the operator's address before the terminal mark, append a
    task tail or an emoji, so the reply is matched on the opener stripped of its final mark.
    The pools and the per-period slice come from the production selector, not a copied subset.
    """
    openers: set[str] = set()
    for period in ("morning", "afternoon", "evening", "night"):
        monkeypatch.setattr(greetings, "_time_of_day", lambda now=None, _p=period: _p)
        openers.update(greetings._openers_for(phrase))
    monkeypatch.undo()
    return tuple(sorted({opener.rstrip(".!?") for opener in openers if opener.rstrip(".!?")}))


def _seed_history(home):
    data = home / "data"
    rows = []
    with sqlite3.connect(data / "vool_web0_v2.db") as conn:
        for i in range(12):
            sid, cp = f"history-{i}", f"checkpoint-{i}"
            conn.execute("INSERT INTO runtime_checkpoints "
                         "(checkpoint_id,session_id,request_text,status,source_context_json,created_at,updated_at) "
                         "VALUES (?,?,?,'completed',?,'2026-09-04','2026-09-04')",
                         (cp, sid, "previous task", json.dumps({"archived_material": "z" * 2_000_000})))
            conn.execute("INSERT INTO runtime_sessions "
                         "(session_id,started_at,updated_at,status,last_checkpoint_id) "
                         "VALUES (?,'2026-09-04','2026-09-04','completed',?)", (sid, cp))
            conn.executemany("INSERT INTO runtime_session_events "
                             "(session_id,seq,event_type,message,details_json,created_at) "
                             "VALUES (?,?,'task_progress','historical progress',?,'2026-09-04')",
                             [(sid, n, json.dumps({"status": "completed", "stage": str(n)}))
                              for n in range(1, 121)])
            rows.extend({"session_id": sid, "user": "previous message", "assistant": "previous response",
                         "ts": "2026-09-04"} for _ in range(8))
    (data / "conversation_log.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_cold_page_with_history_delivers_hi_and_survives_reload(tmp_path, monkeypatch):
    opener_prefixes = _production_greeting_prefixes("hi", monkeypatch)
    artifact = os.environ.get("VOOL_STARTUP_APP_ROOT")
    if artifact:
        app = Path(artifact).resolve()
        monkeypatch.setattr(rig, "REPO_ROOT", app)
        monkeypatch.setattr(rig.sys, "executable", str(app.parent / "python/bin/python3"))
    with rig.CapturingProvider(default=PROVIDER_REPLY) as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider,
                                  env_extra={"VOOL_INSTALL_PROFILE": "hybrid-fallback"})
        try:
            daemon.start(timeout=60)
            _seed_history(daemon.home)
            certification = daemon.certify(timeout=30)
            assert certification.get("state") == "verified", certification
            timings = {}
            for route in ("/api/chat/attachments/limits", "/api/chat/sessions", "/api/runtime/sessions"):
                started = time.monotonic()
                with urlopen(daemon.base_url + route, timeout=3) as response:
                    payload = json.load(response)
                timings[route] = round(time.monotonic() - started, 3)
                assert payload
                assert timings[route] < 2, timings
            manager, browser = served_browser.launch_chromium()
            try:
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                sends = []
                page.on("request", lambda request: sends.append(request.post_data)
                        if request.url == daemon.base_url + "/api/chat" else None)
                # The browser uses only the isolated daemon and scripted provider.
                page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(daemon.base_url)
                           else route.abort())
                for generation in range(2):
                    if generation:
                        page.reload(wait_until="domcontentloaded")
                    else:
                        page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
                    page.wait_for_selector("#input", timeout=5000)
                    provider.reset()
                    page.locator("#input").fill("Hi")
                    started = time.monotonic()
                    page.locator("#send").click()
                    try:
                        page.wait_for_function("view.run && view.run.released && view.run.status === 'completed'",
                                               timeout=15000)
                    except Exception:
                        print("HI_DIAGNOSTIC", generation, page.evaluate("({text:inputEl.value, chat:displayedChat, run:view.run && {status:view.run.status,text:view.run.text,released:view.run.released}})"),
                              page.locator(".msg.assistant .msg-text").all_text_contents(), sends, errors, flush=True)
                        raise
                    greeting = page.locator(".msg.assistant .msg-text").last.inner_text()
                    body = greeting.split("\n\n", 1)[0].strip()
                    assert any(body.startswith(prefix) for prefix in opener_prefixes), greeting
                    assert "smalltalk_fast_path" in greeting, greeting
                    timings[f"hi_{generation}"] = round(time.monotonic() - started, 3)
                    assert timings[f"hi_{generation}"] < 15, timings
                    # Hi uses the production deterministic greeting. A separate
                    # turn proves the provider path rather than mislabelling it.
                    page.locator("#input").fill("Write one friendly sentence about a harbor lantern.")
                    started = time.monotonic()
                    page.locator("#send").click()
                    try:
                        page.wait_for_function("text => [...document.querySelectorAll('.msg.assistant .msg-text')].at(-1).textContent.includes(text)", arg=PROVIDER_REPLY_MARK,
                                               timeout=15000)
                    except Exception:
                        print("BROWSER_DIAGNOSTIC", page.locator(".msg.assistant .msg-text").all_text_contents(),
                              "PROVIDER_CALLS", len(provider.calls), "ERRORS", errors, flush=True)
                        raise
                    timings[f"provider_{generation}"] = round(time.monotonic() - started, 3)
                    assert provider.calls, "no provider call for the model-backed turn"
                    page.wait_for_function("view.run && view.run.released", timeout=5000)
                    print("GENERATION", generation, timings, flush=True)
                assert not errors, errors
                # A hung model-selection read must neither dispatch nor trap
                # the composer. Exercise the actual timeout and Stop button.
                # Keep the real queue-claim request pending long enough to exercise
                # the previous terminal run coexisting with a new composer action.
                page.evaluate("""() => {
                    const original = queueOp;
                    queueOp = async function(op, chatId, extra) {
                        if (op === 'claim') await new Promise(resolve => setTimeout(resolve, 750));
                        return original(op, chatId, extra);
                    };
                    pumpQueue(displayedChat);
                }""")
                held = []
                page.route("**/api/cloud/model?*", lambda route: held.append(route))
                for cancel in (False, True):
                    # A released run can still own a pending queue claim. Do not
                    # mistake that run's terminal status for this button press.
                    page.wait_for_function("!isChatBusy(displayedChat)", timeout=5000)
                    previous_turn = page.evaluate("view.run && view.run.turnId")
                    before = len(sends)
                    held_before = len(held)
                    page.locator("#input").fill("Hi")
                    started = time.monotonic()
                    page.locator("#send").click()
                    page.wait_for_function(
                        "previous => view.run && view.run.turnId !== previous && !view.run.released",
                        arg=previous_turn, timeout=5000,
                    )
                    if cancel:
                        page.locator(".tc-stop").last.click(timeout=5000)
                    page.wait_for_function(
                        "previous => view.run && view.run.turnId !== previous && view.run.released",
                        arg=previous_turn, timeout=7000,
                    )
                    assert len(held) > held_before, "model-selection fault was never exercised"
                    state = page.evaluate("({status:view.run.status,error:view.run.error})")
                    assert state["status"] == ("cancelled" if cancel else "failed"), state
                    if not cancel:
                        assert "did not respond" in state["error"], state
                    assert len(sends) == before, "unreadable model state dispatched a turn"
                    timings["cancel" if cancel else "timeout"] = round(time.monotonic() - started, 3)
                page.unroute("**/api/cloud/model?*")
                print("STARTUP_TIMINGS " + json.dumps(timings), flush=True)
            finally:
                browser.close()
                manager.stop()
        finally:
            daemon.stop()
        assert daemon.process is None or daemon.process.poll() is not None


def test_a_send_inside_the_post_turn_claim_window_is_still_run(tmp_path, monkeypatch):
    """A message sent while the post-turn queue pump holds the chat's slot across its claim
    round-trip is enqueued server-side, and that pump -- whose claim was already answered
    empty -- is the item's only wakeup. This is the CI race that stranded the provider turn on
    green content (PR96 run 36570530721 and main run 36603722599 attempt 1: one assistant
    message, zero provider calls, 15s wait_for_function timeout, three different greeting
    variants). The first claim is held until the enqueue has been stored, then answered empty,
    reproducing that window deterministically; the recovery claim goes to the real queue door.
    """
    artifact = os.environ.get("VOOL_STARTUP_APP_ROOT")
    if artifact:
        app = Path(artifact).resolve()
        monkeypatch.setattr(rig, "REPO_ROOT", app)
        monkeypatch.setattr(rig.sys, "executable", str(app.parent / "python/bin/python3"))
    with rig.CapturingProvider(default=PROVIDER_REPLY) as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider,
                                  env_extra={"VOOL_INSTALL_PROFILE": "hybrid-fallback"})
        try:
            daemon.start(timeout=60)
            certification = daemon.certify(timeout=30)
            assert certification.get("state") == "verified", certification
            manager, browser = served_browser.launch_chromium()
            try:
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(daemon.base_url)
                           else route.abort())
                page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
                page.wait_for_selector("#input", timeout=5000)
                # Instrument the queue door at its seam: claim #1 (the greeting turn's
                # post-release pump) is answered empty only AFTER the next enqueue has been
                # stored server-side -- exactly the interleaving the CI runners hit.
                page.evaluate("""() => {
                    const original = queueOp;
                    const trace = [];
                    window.__queueTrace = trace;
                    let releaseFirstClaim = null;
                    queueOp = async function(op, chatId, extra) {
                        if (op === 'claim' && !trace.includes('claim')) {
                            trace.push('claim');
                            await new Promise((resolve) => { releaseFirstClaim = resolve; });
                            return { item: null };
                        }
                        const res = await original(op, chatId, extra);
                        trace.push(op);
                        if (op === 'enqueue' && releaseFirstClaim) releaseFirstClaim();
                        return res;
                    };
                }""")
                provider.reset()
                page.locator("#input").fill("Hi")
                page.locator("#send").click()
                page.wait_for_function("view.run && view.run.released && view.run.status === 'completed'",
                                       timeout=15000)
                # The greeting turn released; its pump's claim is now held open. A send in this
                # window must be ENQUEUED (the slot is busy), never dropped or run twice.
                page.wait_for_function("window.__queueTrace.includes('claim')", timeout=5000)
                page.locator("#input").fill("Write one friendly sentence about a harbor lantern.")
                page.locator("#send").click()
                page.wait_for_function("window.__queueTrace.includes('enqueue')", timeout=5000)
                # The held claim resolves empty with the item already stored. Someone must
                # re-claim it and run the provider turn.
                try:
                    page.wait_for_function(
                        "text => [...document.querySelectorAll('.msg.assistant .msg-text')].at(-1).textContent.includes(text)", arg=PROVIDER_REPLY_MARK,
                        timeout=15000)
                except Exception:
                    print("CLAIM_WINDOW_DIAGNOSTIC",
                          page.evaluate("window.__queueTrace"),
                          page.locator(".msg.assistant .msg-text").all_text_contents(),
                          "PROVIDER_CALLS", len(provider.calls), "ERRORS", errors, flush=True)
                    raise
                assert len(provider.calls) == 1, "the queued turn must run exactly once"
                trace = page.evaluate("window.__queueTrace")
                assert trace.count("claim") >= 2, f"the stranded item was never re-claimed: {trace}"
                # The complete op is fire-and-forget at run release, so the drained-queue read
                # follows the release and then polls the server's own active-item state.
                page.wait_for_function("view.run && view.run.released", timeout=5000)
                session_id = page.evaluate("displayedChat")
                deadline = time.monotonic() + 5
                while True:
                    with urlopen(daemon.base_url + "/api/chat/queue?session=" + session_id, timeout=3) as response:
                        queue_state = json.load(response)
                    if not queue_state.get("queue") or time.monotonic() >= deadline:
                        break
                    time.sleep(0.1)
                assert queue_state.get("queue") == [], queue_state
                assert not errors, errors
            finally:
                browser.close()
                manager.stop()
        finally:
            daemon.stop()


# The companion pet against the chat controls: the hit-area law, driven like a user drives it.
_PET_BOX = "(() => { const r = document.querySelector('#companionLayer .vool-ninja').getBoundingClientRect();" \
           " return {left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height}; })()"
_CENTER = "sel => { const r = document.querySelector(sel).getBoundingClientRect();" \
          " return [r.left + r.width / 2, r.top + r.height / 2]; }"
_HIT_IS = "([sel, x, y]) => { const el = document.querySelector(sel); const hit = document.elementFromPoint(x, y);" \
          " return !!hit && (hit === el || el.contains(hit)); }"
# A point of the pet's own hit area that no chat control owns, or null when the pet has none.
_PET_GRAB_POINT = """() => {
    // Where a person grabs the pet: its own body over page content that is not a chat control.
    // A point over a control is the control's (the hit mask hands it over as soon as it is
    // re-clipped), so grabbing there would race the mask; it is used only when nothing else is left.
    const pet = document.querySelector('#companionLayer .vool-ninja');
    const layer = document.getElementById('companionLayer');
    const controls = 'button, input:not([type="hidden"]), textarea, select, a[href], summary, [role="button"], '
        + '[role="link"], [role="menuitem"], [role="checkbox"], [role="switch"], [role="tab"], [contenteditable=""], [contenteditable="true"]';
    const r = pet.getBoundingClientRect();
    let fallback = null;
    for (let y = r.top + 8; y < r.bottom - 4; y += 8) {
        for (let x = r.left + 8; x < r.right - 4; x += 8) {
            const hit = document.elementFromPoint(x, y);
            if (!hit || !pet.contains(hit)) continue;
            const under = document.elementsFromPoint(x, y).find(e => !layer.contains(e));
            if (!under || !under.closest(controls)) return [x, y];
            fallback = fallback || [x, y];
        }
    }
    return fallback;
}"""
# The pet's box once it has stopped moving (a docked pet re-homes when the footer grows).
_PET_SETTLED = """() => new Promise(done => { let last = '', same = 0;
    const tick = () => { const r = document.querySelector('#companionLayer .vool-ninja').getBoundingClientRect();
        const now = [r.left, r.top].join(',');
        same = now === last ? same + 1 : 0; last = now;
        if (same >= 3) done(true); else setTimeout(() => requestAnimationFrame(tick), 60); };
    tick(); })"""


_PET_STACK = """() => {
    const r = document.querySelector('#companionLayer .vool-ninja').getBoundingClientRect();
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    return {pet: [r.left, r.top, r.width, r.height],
            stack: document.elementsFromPoint(x, y).slice(0, 5).map(e => e.tagName + '#' + e.id + '.' + e.className)};
}"""


# Diagnostic only: what the page itself saw of a drag gesture (first/last events per type).
_POINTER_LOG = """() => { const log = window.__ptrlog = {n: {}, first: {}, last: {}};
    if (window.__ptrlogBound) return; window.__ptrlogBound = true;
    for (const t of ['pointerdown', 'pointermove', 'pointerup', 'pointercancel', 'lostpointercapture']) {
        window.addEventListener(t, e => { const l = window.__ptrlog; l.n[t] = (l.n[t] || 0) + 1;
            const r = [Math.round(e.clientX), Math.round(e.clientY), String(e.target && (e.target.id || e.target.className) || '').slice(0, 40)];
            if (!l.first[t]) l.first[t] = r; l.last[t] = r; }, true);
    } }"""


def _drag(page, start, end):
    page.mouse.move(*start)
    page.mouse.down()
    steps = 12
    for i in range(1, steps + 1):
        page.mouse.move(start[0] + (end[0] - start[0]) * i / steps, start[1] + (end[1] - start[1]) * i / steps)
    page.mouse.up()


def _park_pet_over(page, selector, target=None):
    """Drag the pet by its own body until it covers ``selector``'s centre (or ``target``, where
    that control will appear); the drop is final."""
    page.evaluate(_PET_SETTLED)
    if page.evaluate("window.VoolCompanion.pos().mode") != "free":
        # Take the pet off its docked home first, by its body, to the top of the window. At its
        # home it re-homes itself away from composer controls (vnAvoidCritical), so a drag measured
        # from the docked box can start from a different place than the one measured.
        grab = page.evaluate(_PET_GRAB_POINT)
        assert grab, f"the docked pet offers no grab point at all: {page.evaluate(_PET_STACK)}"
        box = page.evaluate(_PET_BOX)
        size = page.viewport_size
        _drag(page, grab, (grab[0] + size["width"] / 2 - (box["left"] + box["width"] / 2),
                           grab[1] + 140 - (box["top"] + box["height"] / 2)))
        page.wait_for_function("() => window.VoolCompanion.pos().mode === 'free'", timeout=3000)
        page.evaluate(_PET_SETTLED)
    grab = page.evaluate(_PET_GRAB_POINT)
    assert grab, f"the pet offers no grab point at all: {page.evaluate(_PET_STACK)}"
    box = page.evaluate(_PET_BOX)
    target = target or page.evaluate(_CENTER, selector)
    # Put the pet's centre on the control's centre (the drop clamps to the window only).
    end = (grab[0] + target[0] - (box["left"] + box["width"] / 2),
           grab[1] + target[1] - (box["top"] + box["height"] / 2))
    before = box
    page.evaluate(_POINTER_LOG)
    _drag(page, grab, end)
    page.wait_for_function("() => window.VoolCompanion.pos().mode === 'free'", timeout=3000)
    box = page.evaluate(_PET_BOX)
    if not (box["left"] < target[0] < box["right"] and box["top"] < target[1] < box["bottom"]):
        print("PARK_DIAGNOSTIC", json.dumps({"selector": selector, "before": before, "grab": grab, "end": end, "after": box,
              "target": target, "target_now": page.evaluate(_CENTER, selector) if page.locator(selector).is_visible() else None,
              "pos": page.evaluate("window.VoolCompanion.pos()"), "pointer": page.evaluate("window.__ptrlog")}))
    assert box["left"] < target[0] < box["right"] and box["top"] < target[1] < box["bottom"], \
        f"the pet must sit directly over {selector}'s place: pet={box} point={target}"
    return box, target


def _assert_pet_over(page, selector):
    box = page.evaluate(_PET_BOX)
    target = page.evaluate(_CENTER, selector)
    assert box["left"] < target[0] < box["right"] and box["top"] < target[1] < box["bottom"], \
        f"the pet must sit directly over {selector}: pet={box} control={target}"
    return box, target


def test_a_pet_parked_on_send_or_approve_never_takes_their_click(tmp_path):
    """The enlarged pet sat on the turn card's Stop button at its docked home and swallowed the
    click (CI run 36981567750, shard 4). A pet dropped DIRECTLY on Send, then on Allow once: an
    ordinary click (no force) reaches the control, the served turn really runs, and the pet is
    still dragged by its own body. Phone and desktop widths, with and without reduced motion."""
    with rig.CapturingProvider(default=PROVIDER_REPLY) as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider,
                                  env_extra={"VOOL_INSTALL_PROFILE": "hybrid-fallback"})
        try:
            daemon.start(timeout=60)
            certification = daemon.certify(timeout=30)
            assert certification.get("state") == "verified", certification
            manager, browser = served_browser.launch_chromium()
            try:
                for width, motion in ((390, "no-preference"), (1280, "no-preference"), (1280, "reduce")):
                    page = browser.new_page(viewport={"width": width, "height": 844}, reduced_motion=motion)
                    try:
                        errors = []
                        page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                        mode_posts = []
                        page.on("request", lambda request, mode_posts=mode_posts: mode_posts.append(request.post_data or "")
                                if request.url == daemon.base_url + "/api/mode" and request.method == "POST" else None)
                        page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(daemon.base_url)
                                   else route.abort())
                        page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
                        page.wait_for_selector("#input", timeout=5000)
                        page.wait_for_selector("#companionLayer .vool-ninja", timeout=5000)
                        reduced = page.evaluate("document.body.classList.contains('vn-motion-reduced')")
                        assert reduced is (motion == "reduce"), (width, motion, reduced)

                        # SEND under the pet.
                        _park_pet_over(page, "#send")
                        send = page.evaluate(_CENTER, "#send")
                        page.wait_for_function(_HIT_IS, arg=["#send", send[0], send[1]], timeout=3000)
                        provider.reset()
                        page.locator("#input").fill("Write one friendly sentence about a harbor lantern.")
                        page.locator("#send").click(timeout=5000)
                        page.wait_for_function(
                            "text => [...document.querySelectorAll('.msg.assistant .msg-text')].at(-1).textContent.includes(text)",
                            arg=PROVIDER_REPLY_MARK, timeout=15000)
                        assert len(provider.calls) == 1, (width, motion, len(provider.calls))
                        page.wait_for_function("view.run && view.run.released", timeout=5000)

                        # The pet is still dragged by its own body while it covers Send.
                        page.evaluate(_PET_SETTLED)
                        before = page.evaluate(_PET_BOX)
                        grab = page.evaluate(_PET_GRAB_POINT)
                        assert grab, f"a pet on Send kept no grab point of its own: {before}"
                        page.evaluate(_POINTER_LOG)
                        _drag(page, grab, (grab[0] - 60, grab[1] - 140))
                        after = page.evaluate(_PET_BOX)
                        assert after["top"] < before["top"] - 100, (before, after, grab, page.evaluate("window.__ptrlog"))
                        assert page.evaluate("window.VoolCompanion.pos().mode") == "free"

                        # APPROVE under the pet: the permission bar's Allow once. While an answer is
                        # pending the raised footer covers the pet, so the pet is parked first on the
                        # spot where Allow once appears, then the bar opens on top of it.
                        show_bar = """() => showPermBar(displayedChat, { approval: {
                            approval_id: 'pet-hit-area-probe', action: 'write notes.txt',
                            affected_resources: ['notes.txt'], expected_side_effects: 'creates a file',
                            reversible: true, intent: 'workspace.write_file', scope_options: ['once'] } })"""
                        page.evaluate(show_bar)
                        page.wait_for_selector("#permOnce", state="visible", timeout=3000)
                        spot = page.evaluate(_CENTER, "#permOnce")
                        page.evaluate("hidePermBar()")
                        page.wait_for_selector("#permOnce", state="hidden", timeout=3000)
                        _park_pet_over(page, "#permOnce", target=spot)
                        page.evaluate(show_bar)
                        page.wait_for_selector("#permOnce", state="visible", timeout=3000)
                        _assert_pet_over(page, "#permOnce")
                        allow = page.evaluate(_CENTER, "#permOnce")
                        page.wait_for_function(_HIT_IS, arg=["#permOnce", allow[0], allow[1]], timeout=3000)
                        page.locator("#permOnce").click(timeout=5000)
                        deadline = time.monotonic() + 5
                        while not any("pet-hit-area-probe" in body for body in mode_posts) and time.monotonic() < deadline:
                            page.wait_for_timeout(50)
                        resolved = [json.loads(body) for body in mode_posts if "pet-hit-area-probe" in body]
                        assert resolved and resolved[0].get("op") == "resolve_approval", (width, motion, mode_posts)
                        assert resolved[0].get("decision") == "allow", resolved
                        assert not errors, errors
                    finally:
                        page.close()
            finally:
                browser.close()
                manager.stop()
        finally:
            daemon.stop()


# Controls the pet covers only in part. Injected into the REAL served page (the product's own
# fragment and stylesheet) under the companion layer, as a log card or a sidebar would sit: a
# control clipped by its container, one whose right side another surface covers, and
# one whose midpoint is covered while both ends show. A midpoint-only filter was measured
# handing these whole rectangles back to the pet (1 passed / 2 failed geometry cases).
_PARTIAL_CONTROLS = """() => {
    const host = document.createElement('div');
    host.id = 'partialHost';
    host.style.cssText = 'position:fixed;left:20px;top:150px;width:300px;height:200px;z-index:20;background:#141416';
    host.innerHTML =
        '<div style="position:absolute;left:0;top:0;width:60px;height:50px;overflow:hidden">' +
        '<button id="pcClip" style="width:140px;height:50px">Clipped</button></div>' +
        '<button id="pcCover" style="position:absolute;left:0;top:70px;width:140px;height:50px">Covered</button>' +
        '<div style="position:absolute;left:60px;top:70px;width:80px;height:50px;background:#222"></div>' +
        '<button id="pcMid" style="position:absolute;left:0;top:140px;width:140px;height:50px">Split</button>' +
        '<div style="position:absolute;left:50px;top:130px;width:40px;height:70px;background:#222"></div>';
    document.body.appendChild(host);
    window.__pc = {pcClip: 0, pcCover: 0, pcMid: 0, pcLate: 0};
    for (const id of ['pcClip', 'pcCover', 'pcMid']) document.getElementById(id).onclick = () => window.__pc[id]++;
}"""
# [id, x, y] visible points, relative to the host's top-left, each owned by its control.
_PARTIAL_VISIBLE = [["pcClip", 10, 25], ["pcClip", 58, 45], ["pcCover", 10, 95], ["pcCover", 58, 72],
                    ["pcMid", 10, 165], ["pcMid", 130, 165]]
# Points of those controls hidden by clipping or by the other surface: the pet keeps them.
_PARTIAL_HIDDEN = [[100, 25], [100, 95], [70, 165]]
_ID_AT = "([x, y]) => { const e = document.elementFromPoint(x, y); return e ? (e.id || e.className) : null; }"


def _host_point(page, dx, dy):
    r = page.evaluate("() => { const r = document.getElementById('partialHost').getBoundingClientRect(); return [r.left, r.top]; }")
    return [r[0] + dx, r[1] + dy]


def _assert_partial_controls_keep_their_clicks(page, label):
    for control, dx, dy in _PARTIAL_VISIBLE:
        x, y = _host_point(page, dx, dy)
        page.wait_for_function(_HIT_IS, arg=["#" + control, x, y], timeout=3000)
        before = page.evaluate("id => window.__pc[id]", control)
        page.mouse.click(x, y)
        assert page.evaluate("id => window.__pc[id]", control) == before + 1, (label, control, dx, dy)
    for dx, dy in _PARTIAL_HIDDEN:
        x, y = _host_point(page, dx, dy)
        assert page.evaluate(_ID_AT, [x, y]) == "vn-hit", (label, "a hidden part must stay the pet's", dx, dy)


def test_controls_the_pet_covers_in_part_keep_every_visible_part(tmp_path):
    """Partly clipped, partly covered and midpoint-covered controls on the served chat page: every
    VISIBLE part takes an ordinary click with the pet on top, the hidden parts stay the pet's, and
    the pet is still dragged by its own body. Re-checked after a resize and after a control appears
    under the resting pet. Phone and desktop widths, with and without reduced motion."""
    with rig.CapturingProvider(default=PROVIDER_REPLY) as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider,
                                  env_extra={"VOOL_INSTALL_PROFILE": "hybrid-fallback"})
        try:
            daemon.start(timeout=60)
            manager, browser = served_browser.launch_chromium()
            try:
                for width, motion in ((390, "no-preference"), (1280, "no-preference"), (1280, "reduce")):
                    page = browser.new_page(viewport={"width": width, "height": 844}, reduced_motion=motion)
                    try:
                        errors = []
                        page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                        page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(daemon.base_url)
                                   else route.abort())
                        page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
                        page.wait_for_selector("#input", timeout=5000)
                        page.wait_for_selector("#companionLayer .vool-ninja", timeout=5000)
                        assert page.evaluate("document.body.classList.contains('vn-motion-reduced')") is (motion == "reduce")
                        page.evaluate(_PARTIAL_CONTROLS)
                        label = (width, motion)

                        # Drop the pet by its own body so it covers all three controls.
                        _park_pet_over(page, "#partialHost", target=_host_point(page, 75, 95))
                        box = page.evaluate(_PET_BOX)
                        for _control, dx, dy in _PARTIAL_VISIBLE + [[None, *p] for p in _PARTIAL_HIDDEN]:
                            x, y = _host_point(page, dx, dy)
                            assert box["left"] < x < box["right"] and box["top"] < y < box["bottom"], (label, dx, dy, box)
                        _assert_partial_controls_keep_their_clicks(page, label)

                        # A control that appears under the resting pet (a turn card's Stop) is clickable
                        # without the pet moving.
                        page.evaluate("""() => { const b = document.createElement('button'); b.id = 'pcLate';
                            b.style.cssText = 'position:absolute;left:145px;top:15px;width:15px;height:20px';
                            b.textContent = 'Stop'; b.onclick = () => window.__pc.pcLate++;
                            document.getElementById('partialHost').appendChild(b); }""")
                        x, y = _host_point(page, 152, 25)
                        assert box["left"] < x < box["right"] and box["top"] < y < box["bottom"], (label, box)
                        page.wait_for_function(_HIT_IS, arg=["#pcLate", x, y], timeout=3000)
                        page.mouse.click(x, y)
                        assert page.evaluate("window.__pc.pcLate") == 1, label

                        # A resize re-clips; the visible parts still take their clicks.
                        page.set_viewport_size({"width": width, "height": 800})
                        page.wait_for_timeout(100)
                        assert page.evaluate(_PET_BOX) == box, (label, "a free pet does not move on this resize")
                        _assert_partial_controls_keep_their_clicks(page, (*label, "resized"))

                        # The pet still drags by its own body while it covers the controls.
                        before = page.evaluate(_PET_BOX)
                        page.evaluate(_PET_SETTLED)
                        grab = page.evaluate(_PET_GRAB_POINT)
                        assert grab, (label, "a pet on the controls kept no grab point of its own", before)
                        _drag(page, grab, (grab[0] + 40, grab[1] + 120))
                        after = page.evaluate(_PET_BOX)
                        assert after["top"] > before["top"] + 80, (label, before, after)
                        assert page.evaluate("window.VoolCompanion.pos().mode") == "free"
                        assert not errors, errors
                    finally:
                        page.close()
            finally:
                browser.close()
                manager.stop()
        finally:
            daemon.stop()


# Many controls under the pet at once, injected into the REAL served page under the companion
# layer. Each fully visible control costs the hit mask 5 probes, so 144 of them (or 130 stacked
# ones) spend its per-update probe budget (600) before the last controls in DOM order are probed.
# Measured on d6e3929: the pet kept 24 of the 144 grid buttons and the late Send, 0 of 3 clicks.
_DENSE_GRID = """() => {
    const host = document.createElement('div');
    host.id = 'denseHost';
    host.style.cssText = 'position:fixed;left:60px;top:150px;width:166px;height:166px;z-index:20;background:#141416';
    let html = '';
    for (let r = 0; r < 12; r++) for (let c = 0; c < 12; c++)
        html += '<button id="g' + r + '_' + c + '" style="position:absolute;left:' + (c * 14) + 'px;top:' + (r * 14)
            + 'px;width:12px;height:12px;padding:0;border:0;background:#2a2a30"></button>';
    host.innerHTML = html;
    document.body.appendChild(host);
    window.__dg = {};
    for (const b of host.querySelectorAll('button')) b.onclick = () => { window.__dg[b.id] = (window.__dg[b.id] || 0) + 1; };
}"""
# The grid buttons whose centre lies inside the pet box, and those of them the pet takes.
_GRID_UNDER_PET = """box => { const under = [], taken = [];
    for (const b of document.querySelectorAll('#denseHost button')) {
        const r = b.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
        if (!(box.left < x && x < box.right && box.top < y && y < box.bottom)) continue;
        under.push(b.id); if (document.elementFromPoint(x, y) !== b) taken.push(b.id); }
    return {under, taken}; }"""
_STACKED_CONTROLS = """() => {
    const host = document.createElement('div');
    host.id = 'stackHost';
    host.style.cssText = 'position:fixed;left:60px;top:360px;width:166px;height:166px;z-index:20;background:#141416';
    let html = '';
    for (let i = 0; i < 130; i++)
        html += '<button style="position:absolute;left:10px;top:10px;width:60px;height:40px">Slide ' + i + '</button>';
    host.innerHTML = html + '<button id="stLate" style="position:absolute;left:90px;top:100px;width:60px;height:40px">Send</button>';
    document.body.appendChild(host);
    window.__stLate = 0;
    document.getElementById('stLate').onclick = () => { window.__stLate++; };
}"""


def test_controls_past_the_probe_budget_keep_their_clicks_under_the_pet(tmp_path):
    """The pet parked on more controls than one hit-mask update can probe: a dense grid of small
    buttons, and a Send placed after 130 stacked controls. Every visible button under the pet takes
    an ordinary click with the pet on top (the last ones in DOM order included), and the pet is still
    dragged by its own body. Desktop width, and phone width with reduced motion."""
    with rig.CapturingProvider(default=PROVIDER_REPLY) as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider,
                                  env_extra={"VOOL_INSTALL_PROFILE": "hybrid-fallback"})
        try:
            daemon.start(timeout=60)
            manager, browser = served_browser.launch_chromium()
            try:
                for width, motion in ((1280, "no-preference"), (390, "reduce")):
                    page = browser.new_page(viewport={"width": width, "height": 844}, reduced_motion=motion)
                    try:
                        errors = []
                        page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                        page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(daemon.base_url)
                                   else route.abort())
                        page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
                        page.wait_for_selector("#input", timeout=5000)
                        page.wait_for_selector("#companionLayer .vool-ninja", timeout=5000)
                        assert page.evaluate("document.body.classList.contains('vn-motion-reduced')") is (motion == "reduce")
                        label = (width, motion)

                        # DENSE GRID: 144 visible 12px buttons, the pet dropped on their middle.
                        page.evaluate(_DENSE_GRID)
                        box, _ = _park_pet_over(page, "#denseHost")
                        last = page.evaluate(_CENTER, "#g11_11")
                        page.wait_for_function(_HIT_IS, arg=["#g11_11", last[0], last[1]], timeout=3000)
                        grid = page.evaluate(_GRID_UNDER_PET, box)
                        # More fully visible controls under the pet than one update can probe (5 each, 600).
                        assert len(grid["under"]) >= 130, (label, len(grid["under"]), box)
                        assert grid["taken"] == [], (label, "visible buttons the pet took", grid["taken"])
                        for cell in ("g11_11", "g11_0", "g6_6", "g0_0"):
                            x, y = page.evaluate(_CENTER, "#" + cell)
                            assert box["left"] < x < box["right"] and box["top"] < y < box["bottom"], (label, cell, box)
                            page.mouse.click(x, y)
                            assert page.evaluate("id => window.__dg[id] || 0", cell) == 1, (label, cell)
                        page.evaluate("document.getElementById('denseHost').remove()")

                        # STACKED: 130 stacked controls, then a visible Send later in DOM order.
                        page.evaluate(_STACKED_CONTROLS)
                        box, _ = _park_pet_over(page, "#stackHost")
                        x, y = page.evaluate(_CENTER, "#stLate")
                        assert box["left"] < x < box["right"] and box["top"] < y < box["bottom"], (label, box)
                        page.wait_for_function(_HIT_IS, arg=["#stLate", x, y], timeout=3000)
                        page.mouse.click(x, y)
                        assert page.evaluate("window.__stLate") == 1, label

                        # The pet still drags by its own body while it covers them.
                        page.evaluate(_PET_SETTLED)
                        before = page.evaluate(_PET_BOX)
                        grab = page.evaluate(_PET_GRAB_POINT)
                        assert grab, (label, "a pet on the stacked controls kept no grab point of its own", before)
                        _drag(page, grab, (grab[0] + 40, grab[1] + 120))
                        after = page.evaluate(_PET_BOX)
                        assert after["top"] > before["top"] + 80, (label, before, after)
                        assert page.evaluate("window.VoolCompanion.pos().mode") == "free"
                        assert not errors, errors
                    finally:
                        page.close()
            finally:
                browser.close()
                manager.stop()
        finally:
            daemon.stop()
