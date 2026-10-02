"""Open the real Linux native chat, exercise its composer, then prove owned shutdown.

Run under a real display or xvfb with pywebview and GTK/WebKitGTK installed.
This checks desktop delivery, not model quality or paid provider acceptance.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-commit", required=True)
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("This acceptance check requires Linux; a simulated platform is not acceptance.")
    root = Path(__file__).resolve().parents[1]
    if args.profile.exists():
        parser.error("Use a new disposable profile, never an existing user profile.")
    args.profile.mkdir(parents=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    os.environ.update({"VOOL_HOME": str(args.profile.resolve()), "VOOL_PROJECT_ROOT": str(root),
                       "VOOL_RUNTIME_MODE": "self-contained", "VOOL_NATIVE_API_URL": origin,
                       "VOOL_NATIVE_REQUIRE_OWNED_RUNTIME": "1", "VOOL_EXPECTED_COMMIT": args.expected_commit,
                       "VOOL_NATIVE_STARTUP_TIMEOUT": "90", "PYWEBVIEW_GUI": "gtk"})
    sys.path.insert(0, str(root))
    import webview

    from installer.bundle import vool_window

    evidence: dict = {"platform": sys.platform, "expected_commit": args.expected_commit, "ok": False}
    original_create = webview.create_window
    inspected = threading.Event()

    def create(*positional, **keywords):
        window = original_create(*positional, **keywords)

        def inspect():
            try:
                with urllib.request.urlopen(f"{origin}/healthz", timeout=5) as reply:
                    evidence["health"] = json.load(reply)
                for _ in range(100):
                    proof = window.evaluate_js("""(() => {
                      const el = document.querySelector('textarea#input');
                      if (!el || el.disabled || el.getBoundingClientRect().height === 0) return null;
                      el.focus();
                      return {path: location.pathname, title: document.title};
                    })()""")
                    if proof:
                        evidence["composer"] = proof
                        break
                    time.sleep(0.1)
                if not evidence.get("composer"):
                    raise RuntimeError("The actual chat composer did not become usable.")
                if evidence["composer"]["path"] != "/chat":
                    raise RuntimeError("The window did not load the chat route.")
                # Exercise native keyboard delivery, not just a JavaScript value assignment.
                keyboard: dict = {}
                ids = subprocess.check_output(["xdotool", "search", "--onlyvisible", "--name", "^VOOL$"],
                                              text=True, timeout=10).splitlines()
                keyboard["window_ids"] = ids
                if not ids:
                    raise RuntimeError("No visible window titled VOOL was found for keyboard delivery.")
                subprocess.run(["xdotool", "windowfocus", "--sync", ids[0]], check=True, timeout=10)
                # Keys are routed to the X input-focus window, so delivery is only attempted once
                # the focus actually sits on the target window (windowfocus --sync acknowledges
                # the request; the poll proves it landed). Every fact is recorded: the 24.04 run
                # failed the value check with no keyboard/focus timeline to diagnose
                # (run 36982838739, job 110761047078).
                focused = ""
                for _ in range(20):
                    focused = subprocess.check_output(["xdotool", "getwindowfocus"],
                                                      text=True, timeout=10).strip()
                    if focused == ids[0]:
                        break
                    time.sleep(0.1)
                keyboard["x_focused_window"] = focused
                keyboard["dom_active_element"] = window.evaluate_js(
                    "document.activeElement ? (document.activeElement.id || document.activeElement.tagName) : null")
                keyboard["composer_focused"] = keyboard["dom_active_element"] == "input"
                if focused != ids[0]:
                    evidence["composer"]["keyboard"] = keyboard
                    raise RuntimeError(
                        f"Keyboard focus never reached the VOOL window (focused={focused!r}, target={ids[0]!r}).")
                subprocess.run(["xdotool", "type", "--clearmodifiers", "--delay", "1",
                                "Linux desktop acceptance"], check=True, timeout=10)
                value = window.evaluate_js("document.querySelector('textarea#input').value")
                keyboard["native_keyboard_value"] = value
                evidence["composer"]["keyboard"] = keyboard
                if value != "Linux desktop acceptance":
                    raise RuntimeError("Native keyboard input did not reach the chat composer.")
                inspected.set()
            except Exception as exc:
                evidence["error"] = str(exc)
            finally:
                window.destroy()

        window.events.loaded += inspect
        return window

    webview.create_window = create
    try:
        evidence["exit_code"] = vool_window.main()
        # main() must synchronously tear down the runtime it started.
        with socket.socket() as probe:
            probe.settimeout(2)
            evidence["listener_closed"] = probe.connect_ex(("127.0.0.1", port)) != 0
        evidence["ok"] = inspected.is_set() and evidence["exit_code"] == 0 and evidence["listener_closed"]
    finally:
        webview.create_window = original_create
        args.output.write_text(json.dumps(evidence, indent=2) + "\n")
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
