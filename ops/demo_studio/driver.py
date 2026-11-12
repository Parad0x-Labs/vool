"""The browser driver: real interactions with the real page, recorded.

Playwright chromium with screen recording and reduced motion pinned. Steps
come from the typed journey specs; the driver's page access reads only
public UI state (selectors, displayedChat). There is no seam here through
which a scripted response could enter: the page does what it does, and the
driver waits for what actually happens.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ops.demo_studio.journeys import PAGE_ACTION_ALLOWLIST, StepSpec
from ops.demo_studio.wire import DemoWireClient


@dataclass
class StepOutcome:
    ok: bool
    step_id: str
    reason: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class JourneyDriver(Protocol):
    """What the runner needs from a driver (browser or test double)."""

    def session(self) -> str: ...

    def drain_events(self) -> list[dict[str, Any]]: ...

    def observe_box(self, selector: str) -> dict[str, int] | None: ...

    def execute_step(self, step: StepSpec) -> StepOutcome: ...

    def finish_video(self) -> Path | None: ...

    def shutdown(self) -> None: ...


def browser_context_options(*, record_video_dir: str) -> dict[str, Any]:
    """The studio's browser context: recorded, and calm by law.

    ``reduced_motion='reduce'`` is pinned for two reasons: accessibility
    (viewers and CI runs get no animation storms) and determinism (animations
    are non-deterministic timing noise around the events the studio waits
    for).
    """
    return {
        "record_video_dir": record_video_dir,
        "record_video_size": {"width": 1280, "height": 800},
        "reduced_motion": "reduce",
        "viewport": {"width": 1280, "height": 800},
    }


def validate_browser_context_options(options: dict[str, Any]) -> None:
    if options.get("reduced_motion") != "reduce":
        raise ValueError(
            "browser context must pin reduced_motion='reduce' — the studio "
            "records calm, accessible captures and deterministic timing"
        )
    if not any(str(key).startswith("record") for key in options):
        raise ValueError(
            "browser context records no video (no record_* options) — a studio "
            "context that cannot produce a recording is misconfigured"
        )
    if not options.get("record_video_size"):
        raise ValueError("browser context must pin record_video_size for stable framing")


class PlaywrightDriver:
    """Real chromium driving the real served page."""

    def __init__(
        self,
        *,
        wire: DemoWireClient,
        run_dir: Path,
        base_url: str,
        headless: bool = True,
        video: bool = True,
        blackbox_runner: Callable[[list[str]], dict[str, Any]] | None = None,
        attach_dir: Path | None = None,
        workspace_root: Path | None = None,
    ) -> None:
        from playwright.sync_api import sync_playwright

        self.wire = wire
        self.base_url = base_url.rstrip("/")
        self.run_dir = run_dir
        self.attach_dir = attach_dir
        self.workspace_root = workspace_root
        self._blackbox_runner = blackbox_runner
        self.captures: dict[str, Any] = {}
        self._pw = sync_playwright().start()
        try:
            video_dir = run_dir / "video"
            if video:
                video_dir.mkdir(parents=True, exist_ok=True)
                options = browser_context_options(record_video_dir=str(video_dir))
            else:
                options = {
                    "reduced_motion": "reduce",
                    "viewport": {"width": 1280, "height": 800},
                }
            validate_browser_context_options(options)
            self._browser = self._pw.chromium.launch(headless=headless)
            self._context = self._browser.new_context(**options)
            self.page = self._context.new_page()
        except Exception:
            self._pw.stop()
            raise
        self._video_finalized = False

    # -- lifecycle -------------------------------------------------------
    def rebase(self, base_url: str) -> None:
        """Point the driver at a new daemon origin (restart journeys)."""
        self.base_url = base_url.rstrip("/")

    def open(self, url: str) -> None:
        self.page.goto(url, wait_until="domcontentloaded", timeout=60_000)

    def session(self) -> str:
        try:
            return str(
                self.page.evaluate(
                    "() => (typeof displayedChat !== 'undefined' && displayedChat) ? displayedChat : ''"
                )
                or ""
            )
        except Exception:  # a navigation (e.g. the page's own
            # reload on daemon death) can destroy the execution context
            # mid-read; that is a skipped cycle, not a journey failure.
            return ""

    def drain_events(self) -> list[dict[str, Any]]:
        """The public typed event trace for the displayed session (full read;
        the runner de-duplicates by seq)."""
        session_id = self.session()
        if not session_id:
            return []
        return self.wire.events(session_id)

    def observe_box(self, selector: str) -> dict[str, int] | None:
        try:
            locator = self.page.locator(selector).first
            if locator.count() == 0 or not locator.is_visible():
                return None
            box = locator.bounding_box()
        except Exception:  # a mid-animation element is not a defect
            return None
        if not box:
            return None
        return {
            "x": int(max(0, box["x"])),
            "y": int(max(0, box["y"])),
            "width": int(box["width"]),
            "height": int(box["height"]),
        }

    def finish_video(self) -> Path | None:
        if self._video_finalized:
            return None
        self._video_finalized = True
        try:
            self._context.close()
        except Exception:  # already closed
            return None
        video_dir = self.run_dir / "video"
        recordings = sorted(video_dir.glob("*.webm")) if video_dir.is_dir() else []
        if not recordings:
            return None
        target = self.run_dir / "video-source.webm"
        recordings[0].replace(target)
        return target

    def shutdown(self) -> None:
        self.finish_video()
        with contextlib.suppress(Exception):
            self._browser.close()
        self._pw.stop()

    # -- step execution ---------------------------------------------------
    def execute_step(self, step: StepSpec) -> StepOutcome:
        try:
            return self._execute(step)
        except Exception as exc:  # a step failure is a journey failure
            return StepOutcome(ok=False, step_id=step.id, reason=f"{type(exc).__name__}: {exc}")

    def _execute(self, step: StepSpec) -> StepOutcome:
        action = step.action
        if action == "goto":
            self.open(self.base_url + (step.target or "/chat"))
            return StepOutcome(ok=True, step_id=step.id)
        if action == "fill":
            self.page.fill(step.target, step.value, timeout=step.budget_s * 1000)
            return StepOutcome(ok=True, step_id=step.id)
        if action == "click":
            self.page.click(step.target, timeout=step.budget_s * 1000)
            return StepOutcome(ok=True, step_id=step.id)
        if action == "reload":
            self.page.reload(wait_until="domcontentloaded", timeout=step.budget_s * 1000)
            return StepOutcome(ok=True, step_id=step.id)
        if action == "wait_visible":
            self.page.wait_for_selector(step.target, state="visible", timeout=step.budget_s * 1000)
            return StepOutcome(ok=True, step_id=step.id)
        if action == "wait_hidden":
            self.page.wait_for_selector(step.target, state="hidden", timeout=step.budget_s * 1000)
            return StepOutcome(ok=True, step_id=step.id)
        if action == "wait_seconds":
            self.page.wait_for_timeout(int(float(step.value or 1) * 1000))
            return StepOutcome(ok=True, step_id=step.id)
        if action == "expect_text":
            self._expect_text(step)
            return StepOutcome(ok=True, step_id=step.id)
        if action == "send_message":
            return self._send_message(step)
        if action == "wait_turn_complete":
            self._wait_turn_complete(step.budget_s)
            return StepOutcome(ok=True, step_id=step.id)
        if action == "attach_files":
            return self._attach_files(step)
        if action == "select_model":
            return self._select_model(step)
        if action == "set_mode":
            return self._set_mode(step)
        if action == "approve":
            self.page.wait_for_selector("#permBar", state="visible", timeout=step.budget_s * 1000)
            self.page.click(step.target or "#permOnce", timeout=10_000)
            return StepOutcome(ok=True, step_id=step.id)
        if action == "deny":
            self.page.wait_for_selector("#permBar", state="visible", timeout=step.budget_s * 1000)
            self.page.click(step.target or "#permDeny", timeout=10_000)
            return StepOutcome(ok=True, step_id=step.id)
        if action == "council_open":
            self.page.click(step.target or "#councilBtn", timeout=step.budget_s * 1000)
            return StepOutcome(ok=True, step_id=step.id)
        if action == "council_scorecard":
            return self._council_scorecard(step)
        if action == "run_blackbox":
            return self._run_blackbox(step)
        if action == "page_action":
            return self._page_action(step)
        if action == "restart_daemon":
            # Handled by the runner (it owns the daemon handle); reaching the
            # driver means the runner did not intercept it.
            return StepOutcome(
                ok=False, step_id=step.id, reason="restart_daemon must be intercepted by the runner"
            )
        return StepOutcome(ok=False, step_id=step.id, reason=f"unknown driver action {action!r}")

    # -- step internals ----------------------------------------------------
    def _expect_text(self, step: StepSpec) -> None:
        deadline_ms = int(step.budget_s * 1000)
        self.page.wait_for_function(
            "([sel, text]) => { const el = document.querySelector(sel); return !!el && (el.innerText || '').includes(text); }",
            arg=[step.target, step.value],
            timeout=deadline_ms,
        )

    def _send_message(self, step: StepSpec) -> StepOutcome:
        self.page.wait_for_selector("#input", state="visible", timeout=30_000)
        self.page.fill("#input", step.value)
        self.page.click("#send")
        self._wait_turn_complete(step.budget_s)
        return StepOutcome(ok=True, step_id=step.id)

    def _wait_turn_complete(self, budget_s: float) -> None:
        self.page.wait_for_function(
            """() => {
              const send = document.querySelector('#send');
              const pending = document.querySelector('.msg.assistant.pending');
              const card = document.querySelector('.task-card');
              const terminal = !card || card.classList.contains('done')
                || card.classList.contains('failed') || card.classList.contains('cancelled')
                || card.classList.contains('awaiting_approval');
              return !!send && !send.disabled && !pending && terminal;
            }""",
            timeout=int(budget_s * 1000),
        )

    def _attach_files(self, step: StepSpec) -> StepOutcome:
        names = [n.strip() for n in (step.value or "").split(",") if n.strip()]
        if not self.attach_dir:
            return StepOutcome(ok=False, step_id=step.id, reason="no attach_dir configured")
        paths = [str(self.attach_dir / name) for name in names]
        self.page.set_input_files("#attachInput", paths, timeout=step.budget_s * 1000)
        ready = self.page.wait_for_function(
            """(want) => {
              const chips = [...document.querySelectorAll('#attachStrip .att-chip')];
              return chips.length >= want && chips.every((c) => c.dataset.state === 'ready');
            }""",
            arg=len(paths),
            timeout=int(step.budget_s * 1000),
        )
        return StepOutcome(ok=bool(ready), step_id=step.id,
                           reason="" if ready else "attachment chips never reached ready state")

    def _select_model(self, step: StepSpec) -> StepOutcome:
        # Idempotent open: a prior journey step may already have opened the
        # menu; clicking again would toggle it closed.
        if self.page.get_attribute("#modelBtn", "aria-expanded") != "true":
            self.page.click("#modelBtn", timeout=10_000)
        self.page.wait_for_selector("#modelPop", state="visible", timeout=10_000)
        # The menu carries two kinds of rows: the run modes (Offline, Computer + web,
        # Local first -- `.run-choice[data-run-model]`) and the model rows
        # (`.pop-item[data-model]`). Both are the product's own buttons; the journey
        # names the value and the page decides which row carries it.
        row = (
            f'#modelPop .pop-item[data-model="{step.value}"], '
            f'#modelPop .run-choice[data-run-model="{step.value}"]'
        )
        self.page.click(row, timeout=step.budget_s * 1000)
        # The pick counts only once the page itself shows it checked: a click the
        # page ignored must fail the step, not film an unchanged lane.
        self.page.wait_for_function(
            """(value) => [...document.querySelectorAll('#modelPop [data-model], #modelPop [data-run-model]')]
              .some((el) => (el.getAttribute('data-model') === value || el.getAttribute('data-run-model') === value)
                && el.getAttribute('aria-checked') === 'true')""",
            arg=step.value,
            timeout=10_000,
        )
        return StepOutcome(ok=True, step_id=step.id)

    def _set_mode(self, step: StepSpec) -> StepOutcome:
        if self.page.get_attribute("#modeBtn", "aria-expanded") != "true":
            self.page.click("#modeBtn", timeout=10_000)
        self.page.wait_for_selector("#modePop", state="visible", timeout=10_000)
        row = f'.pop-item[data-mode="{step.value}"]'
        self.page.click(row, timeout=step.budget_s * 1000)
        return StepOutcome(ok=True, step_id=step.id)

    def _page_action(self, step: StepSpec) -> StepOutcome:
        name = step.value.strip()
        if name not in PAGE_ACTION_ALLOWLIST:
            return StepOutcome(
                ok=False,
                step_id=step.id,
                reason=f"page action {name!r} is not on the product entry-point allowlist",
            )
        self.page.evaluate(f"() => window.VoolPageActions.{name}()")
        return StepOutcome(ok=True, step_id=step.id)

    def _council_scorecard(self, step: StepSpec) -> StepOutcome:
        runs = self.wire.council_runs()
        if not runs:
            return StepOutcome(ok=False, step_id=step.id, reason="no council run appeared")
        run_id = str(runs[0].get("run_id") or runs[0].get("run") or "")
        scorecard = self.wire.council_scorecard(run_id)
        ok = bool(scorecard.get("ok")) and bool(scorecard.get("scorecard"))
        self.captures["council_run_id"] = run_id
        self.captures["scorecard"] = scorecard
        return StepOutcome(ok=ok, step_id=step.id, reason="" if ok else "scorecard not ready")

    def _run_blackbox(self, step: StepSpec) -> StepOutcome:
        if self._blackbox_runner is None:
            return StepOutcome(ok=False, step_id=step.id, reason="no blackbox runner configured")
        if step.value == "turns":
            payload = self._blackbox_runner(["turns"])
            turns = payload.get("turns") or []
            if turns:
                first = turns[0]
                self.captures["blackbox_turn_id"] = str(
                    first.get("turn_id") or first.get("turn") or ""
                )
            self.captures["blackbox_turns"] = payload
            return StepOutcome(ok=payload.get("rc") == 0, step_id=step.id,
                               reason="" if payload.get("rc") == 0 else "blackbox turns failed")
        if step.value == "rollback":
            turn_id = str(self.captures.get("blackbox_turn_id") or "")
            root = str(self.workspace_root or "")
            if not turn_id or not root:
                return StepOutcome(ok=False, step_id=step.id,
                                   reason="no recorded turn id or workspace root for rollback")
            payload = self._blackbox_runner(
                ["rollback", turn_id, "--root", root, "--approve", "--operator", "demo-studio"]
            )
            self.captures["blackbox_rollback"] = payload
            return StepOutcome(ok=payload.get("rc") == 0 and payload.get("ok") is True,
                               step_id=step.id,
                               reason="" if payload.get("rc") == 0 else "blackbox rollback failed")
        args = [a for a in str(step.value).split() if a]
        payload = self._blackbox_runner(args)
        return StepOutcome(ok=payload.get("rc") == 0, step_id=step.id, reason=json.dumps(payload)[:400])
