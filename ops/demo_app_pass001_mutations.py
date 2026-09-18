#!/usr/bin/env python3
"""full-a11-demo-app PASS001 mutation-proof runner (M1–M10).

For every load-bearing invariant this lane ports or built, the runner:
  1. mechanically mutates the REAL production guard in place (a targeted string edit),
  2. runs the named fence tests and expects them to FAIL (RED) for the right reason,
  3. restores the file byte-identically (sha256 verified),
  4. re-runs the fences and expects GREEN on the pristine tree at the end.

Usage:  python ops/demo_app_pass001_mutations.py [pytest-binary]
Exit 0 = every mutation caught; exit 1 otherwise.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

PYTEST = sys.argv[1] if len(sys.argv) > 1 else sys.executable

REPO = Path(__file__).resolve().parent.parent

COMPANION = "tests/test_chat_companion_laws.py"
COUNCIL = "tests/test_chat_council_laws.py"
PROV = "tests/test_model_provenance_strip.py"
PACKAGING = "tests/test_macos_app_packaging.py"
REQ_TOKEN = "tests/test_served_req_token_blocker_pass001.py"
STREAM_INGRESS = "tests/test_served_stream_ingress_pass001.py"
A11 = "tests/test_a11_pass002_authority.py"
A11_MIN = "tests/test_a11_minimum_product_truth.py"

# (id, file, old, new, fence tests, why)
MUTATIONS = [
    (
        # The ONE typed-event door into the companion layer. Dropping it starves the
        # companion of every event; the anchor-uniqueness fence and the live-update
        # coupling law both go red.
        "M1-companion-event-door-removed",
        "core/vool_chat_page.py",
        "if (typeof window !== 'undefined' && window.VoolCompanion) window.VoolCompanion.consume(run.chatId, ev);",
        "",
        [f"{COMPANION}::test_companion_anchors_present_exactly_once"],
        "companion consumes nothing; typed events never reach the presentation layer",
    ),
    (
        # Timer-driven status: make paint derive instead of assert. The M2 law catches
        # any reducer/view movement that comes from time alone.
        "M2-timer-derives-status",
        "core/companion_presentation_fragment.py",
        "function vnPaintNow(now) {\n  if (!vnLayer) return;",
        "function vnPaintNow(now) {\n  if (!vnLayer) return;\n  { const v = vnView(vnLastChat); v.catN += 1; v.events += 1; }",

        [f"{COMPANION}::test_law[M2.no_timer_advancement]"],
        "a timer can advance presentation state — time-derived status becomes possible",
    ),
    (
        # PASS green gate: a completed turn without a passed review must never resolve
        # to PASS. Removing the review-state branch makes every completion green.
        "M3-pass-green-gate-removed",
        "core/companion_presentation_fragment.py",
        '''      const rs = view.reviewState;
      view.terminal = rs === "passed" ? "PASS"
        : rs === "flagged" ? "FLAGGED"
        : (rs === "blocked" || rs === "degraded" || rs === "runtime_failed") ? "VERIFIER_INCOMPLETE"
        : "UNKNOWN";''',
        '''      view.terminal = "PASS";''',
        [f"{COMPANION}::test_law[T5.override_priority_stack]"],
        "every completed turn resolves PASS-green without a passed reviewer verdict",
    ),
    (
        # task.finalizing truth: the allowlist row is the ONLY door to the FINALIZING
        # category (producer S1 emits it). Dropping the row makes "assembling the
        # answer" unreachable — the FINISH gate fence and the S1 seam both go red.
        "M4-task-finalizing-row-removed",
        "core/task_event_model.py",
        '"task_finalizing": ("task.finalizing", None, "running"),',
        "",
        [f"{COMPANION}::test_task_finalizing_allowlist_row",
         "tests/test_chat_companion_laws.py::test_law[T8.finish_unreachable_without_task_finalizing]"],
        "task.finalizing no longer maps through the typed allowlist; FINISH gate dead",
    ),
    (
        # Council fake-seat guard: render an animated seat canvas into the council
        # surface. The C1 law exists precisely to catch a fake council.
        "M5-council-fake-seat-inserted",
        "core/vool_chat_page.py",
        "let html = '<div class=\"council-live\"><div class=\"cl-state\">NO ACTIVE COUNCIL SESSION</div>'",
        "let html = '<canvas class=\"council-seat\" width=\"40\" height=\"40\"></canvas>'; html += '<div class=\"council-live\"><div class=\"cl-state\">NO ACTIVE COUNCIL SESSION</div>'",
        [f"{COUNCIL}::test_council_truth_laws"],
        "a fake animated seat appears in the council surface — invented council workers",
    ),
    (
        # Model provenance truth: an unclassified pin must render UNKNOWN; defaulting
        # the spend class to a comforting word is the exact lie the strip exists to
        # prevent.
        "M6-provenance-unknown-becomes-comforting",
        "core/vool_chat_page.py",
        "const costWord = cost === 'paid' ? 'PAID — may spend provider credits' : cost === 'free' ? 'free' : cost ? 'UNKNOWN' : 'not classified (local/Auto)';",
        "const costWord = cost === 'paid' ? 'PAID — may spend provider credits' : 'free';",
        [f"{PROV}::test_provenance_strip_laws"],
        "the popover strip reclassifies unknown/absent spend as free",
    ),
    (
        # Native fail-closed guard: pywebview failure must exit non-zero (no silent
        # browser fallback). Reverting to the old fallback-and-exit-0 shape reopens the
        # exact historical Chrome-fallback failure.
        "M7-native-browser-fallback-reopened",
        "installer/bundle/vool_window.py",
        '''    if sys.platform == "win32":
        _log(f"native window unavailable ({reason!r}); using Edge fallback")
        _fallback()
        return 0
    if _browser_fallback_allowed():
        _log(f"native window unavailable ({reason!r}); VOOL_ALLOW_BROWSER_FALLBACK set, using browser")
        _fallback()
        return 0
    _log("ERROR: native window unavailable "
         f"({reason!r}); refusing silent browser fallback (set VOOL_ALLOW_BROWSER_FALLBACK=1 to allow)")
    return 1''',
        '''    _log(f"native window unavailable ({reason!r}); using browser fallback")
    _fallback()
    return 0''',
        [f"{PACKAGING}::test_missing_pywebview_on_packaged_native_launch_fails_closed",
         f"{PACKAGING}::test_browser_fallback_returns_only_behind_the_explicit_flag"],
        "a packaged launch without pywebview silently opens a browser and exits 0",
    ),
    (
        # Exact bundle/SHA identity: verify_bundle must refuse to print OK over a
        # wrapper whose runtime starter is missing. Loosening it lets a dead bundle
        # ship as "OK" — the historical stale-bundle confusion.
        "M8-bundle-verify-refuses-nothing",
        "installer/bundle/build_macos_app.sh",
        '''    if [[ ! -f "${PROJECT_ROOT}/Start_VOOL.sh" ]]; then
      die "wrapper bundle cannot start its runtime: ${PROJECT_ROOT}/Start_VOOL.sh is missing (gitignored output of installer/install_vool.sh). Run the installer against this tree first, or rebuild with --self-contained."
    fi''',
        "",
        [f"{PACKAGING}::test_wrapper_build_never_claims_success_without_the_runtime_starter"],
        "a wrapper bundle without a runtime starter still prints OK",
    ),
    (
        # A11 paid authority: the one enforcement point inside set_cloud_model. Dead
        # gate => every surface persists an unconfirmed paid pin again.
        "M9-a11-paid-authority-disabled",
        "core/cloud_model_control.py",
        'if classification["cost_state"] != COST_STATE_FREE and not confirm_paid:',
        "if False and classification[\"cost_state\"] != COST_STATE_FREE and not confirm_paid:",
        [f"{A11}::test_paid_pin_refused_on_every_surface_identically"],
        "unconfirmed paid pins persist on every surface again",
    ),
    (
        # Served stream ingress contract: the apps provider must forward
        # ingress_context. Dropping it reopens the bare-500 silent death of every
        # streamed served turn.
        "M10-served-stream-ingress-dropped",
        "apps/vool_api_server.py",
        "        stream_agent_with_events_provider=lambda runtime, text, *, session_id, source_context, model, include_runtime_events=False, emit_task_events=False, ingress_context=None: _stream_agent_with_events(",
        "        stream_agent_with_events_provider=lambda runtime, text, *, session_id, source_context, model, include_runtime_events=False, emit_task_events=False: _stream_agent_with_events(",
        [f"{STREAM_INGRESS}::test_apps_server_streamed_chat_accepts_ingress_context",
         f"{REQ_TOKEN}::StreamedApiChatParityTests"],
        "the served UI's streamed turns die as bare 500s again (ingress context dropped)",
    ),
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_pytest(targets: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        [PYTEST, "-m", "pytest", "-q", *targets],
        cwd=REPO, capture_output=True, text=True, timeout=900,
    )
    return proc.returncode, (proc.stdout + proc.stderr)[-1200:]


def main() -> int:
    failures: list[str] = []
    for mid, rel, old, new, fences, why in MUTATIONS:
        path = REPO / rel
        original = path.read_text()
        before = sha256(path)
        if old not in original:
            failures.append(f"{mid}: MUTATION ANCHOR NOT FOUND in {rel} (instrument stale)")
            continue
        path.write_text(original.replace(old, new, 1))
        try:
            code, tail = run_pytest(fences)
        finally:
            path.write_text(original)
        restored = sha256(path) == before
        red_ok = code != 0
        status = "CAUGHT" if (red_ok and restored) else "PROBLEM"
        print(f"[{status}] {mid}: pytest exit={code} restored_byte_identical={restored} — {why}")
        if not red_ok:
            failures.append(f"{mid}: sabotage SURVIVED — invariant unprotected: {fences}\n{tail[-400:]}")
        if not restored:
            failures.append(f"{mid}: file NOT restored byte-identically — restore manually from git")
    pristine = run_pytest([
        COMPANION, COUNCIL, PROV,
        "tests/test_chat_page_boots_under_node.py",
    ])
    print(f"pristine fence rerun: exit={pristine[0]}")
    if pristine[0] != 0:
        failures.append("pristine rerun not green: " + pristine[1][-500:])
    if failures:
        print("\n".join("PROBLEM: " + f for f in failures))
        return 1
    print("ALL MUTATIONS CAUGHT — every ported/built invariant is protected by a named fence.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
