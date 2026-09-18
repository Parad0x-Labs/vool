"""Cold browser -> real daemon -> scripted provider, with a per-request client timeline.

Usage: python tools/profile_chat_page_requests.py <repo-root> <scratch-home>

Prints, for each generation (cold, reload), the moment Send was clicked, every request the page
issued (start offset, duration, path) and the moment `view.run.released` became true, so the
client's share of the Hi latency is attributed request by request instead of guessed. Needs the
playwright chromium the served-browser lane uses; no cloud call and no local model launch.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from tests import _reader_served_rig as rig
from tests import served_browser
from tests.test_chat_startup_served import _seed_history

work = Path(sys.argv[2])
with rig.CapturingProvider(default="Hello! How can I help you today?") as provider:
    daemon = rig.ServedDaemon(work, provider=provider, env_extra={"VOOL_INSTALL_PROFILE": "hybrid-fallback"})
    daemon.start(timeout=120)
    try:
        _seed_history(daemon.home)
        cert = daemon.certify(timeout=120)
        assert cert.get("state") == "verified", cert
        manager, browser = served_browser.launch_chromium()
        try:
            page = browser.new_page()
            page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(daemon.base_url) else route.abort())
            starts: dict = {}
            timeline: list = []
            clock = {"t0": time.monotonic()}
            def on_request(request):
                starts[request] = time.monotonic()
            def on_response(response):
                req = response.request
                t = starts.pop(req, None)
                if t is None:
                    return
                timeline.append((round(t - clock["t0"], 3), round(time.monotonic() - t, 3), req.method, req.url.replace(daemon.base_url, "")))
            page.on("request", on_request)
            page.on("requestfinished", lambda request: None)
            page.on("response", on_response)
            for generation in range(2):
                timeline.clear()
                clock["t0"] = time.monotonic()
                if generation:
                    page.reload(wait_until="domcontentloaded")
                else:
                    page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
                page.wait_for_selector("#input", timeout=10000)
                settled = round(time.monotonic() - clock["t0"], 3)
                page.locator("#input").fill("Hi")
                clicked = round(time.monotonic() - clock["t0"], 3)
                page.locator("#send").click()
                page.wait_for_function("view.run && view.run.released && view.run.status === 'completed'", timeout=30000)
                released = round(time.monotonic() - clock["t0"], 3)
                # let late requests (proof chips, ledger polls) land
                time.sleep(2.5)
                print(json.dumps({"generation": generation, "page_ready_s": settled, "send_clicked_s": clicked,
                                  "released_s": released, "hi_latency_s": round(released - clicked, 3)}))
                for row in sorted(timeline):
                    print(f"    start {row[0]:7.3f}  dur {row[1]:6.3f}  {row[2]} {row[3][:90]}")
                if generation == 0:
                    page.locator("#input").fill("Write one friendly sentence about a harbor lantern.")
                    page.locator("#send").click()
                    page.wait_for_function("[...document.querySelectorAll('.msg.assistant .msg-text')].at(-1).textContent.includes('Hello!')", timeout=30000)
                    page.wait_for_function("view.run && view.run.released", timeout=10000)
        finally:
            browser.close()
            manager.stop()
    finally:
        daemon.stop()
