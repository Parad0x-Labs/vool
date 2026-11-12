"""The studio runner: journey in, honest deliverables out (or a loud abort).

Capture flow: stage the exact SHA → boot the isolated daemon (gated) →
verify healthz + capability manifest again at this layer → drive the real
page through the journey's typed steps while draining the public event trace
→ build captions from allowlisted events → render from the real recording →
bind everything into the evidence manifest → verify → tear everything down.

Dry-run flow: validate the journey and emit a deterministic plan manifest.
It boots nothing, records nothing, and claims nothing.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ops.demo_studio.boot import BootReport, verify_served_boot
from ops.demo_studio.captions import build_captions
from ops.demo_studio.evidence import (
    DRY_RUN_EPOCH,
    MANIFEST_NAME,
    build_evidence_manifest,
    utc_now_iso,
    verify_evidence_manifest,
    write_json,
)
from ops.demo_studio.focus import plan_focus_from_events, validate_focus_plan
from ops.demo_studio.journeys import (
    JourneySpec,
    journey_spec,
    spec_hash,
)
from ops.demo_studio.redact import RedactionEngine
from ops.demo_studio.render import render_deliverables
from ops.demo_studio.wire import DemoWireClient


class StudioRefusal(RuntimeError):
    """The studio refuses this run outright (config/journey/environment)."""


class JourneyAborted(Exception):
    """A journey step failed or an expected event never arrived."""

    def __init__(
        self,
        *,
        journey: str,
        step_id: str,
        reason: str,
        report_path: Path,
        missing_expected_events: list[str],
    ) -> None:
        super().__init__(f"journey {journey} aborted at step {step_id!r}: {reason}")
        self.journey = journey
        self.step_id = step_id
        self.reason = reason
        self.report_path = report_path
        self.missing_expected_events = list(missing_expected_events)


@dataclass(frozen=True)
class StudioMode:
    DRY_RUN: str = "dry-run"
    CAPTURE: str = "capture"


@dataclass(frozen=True)
class StudioConfig:
    repo: Path
    out_dir: Path
    mode: str = StudioMode.DRY_RUN
    sha: str = "HEAD"
    python_bin: str = ""
    live_models: bool = False
    headless: bool = True
    video: bool = True
    allowed_requirements: tuple[str, ...] = ()
    boot_timeout_s: float = 240.0
    turn_budget_s: float = 180.0
    driver_factory: Callable[..., Any] | None = None
    #: Lane in the heads manifest (ops/build_identity.py) the film must show.
    #: Set, the studio stages that lane's head and films only a daemon the
    #: line's own build guard passes; empty, no lane is claimed.
    lane: str = ""
    #: Heads manifest for ``lane`` (default: the guard's VOOL_HEADS_MANIFEST).
    heads_manifest: str = ""


@dataclass
class RunOutcome:
    verdict: str
    journey: str
    run_dir: Path
    manifest_path: str
    detail: str = ""


@dataclass
class StepOutcome:
    ok: bool
    step_id: str
    reason: str = ""
    data: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Boot seam


def _boot_staged_app(config: StudioConfig, run_dir: Path, spec: JourneySpec) -> BootBundle:
    """Stage the exact SHA and boot the isolated, gated daemon."""
    from ops.served_reality.appstage import stage_app
    from ops.served_reality.daemon import launch_daemon
    from ops.served_reality.provider_stub import STUB_MODEL_ID, ProviderStub, StubRule
    from ops.served_reality.wire import WireLog

    staged = stage_app(config.repo, config.sha, run_dir)
    stub: ProviderStub | None = None
    if not config.live_models:
        stub = ProviderStub(wire_log_path=str(run_dir / "provider-stub-wire.jsonl"))
        stub.start()
        stub.set_rules(
            [StubRule(content=content, match=match or None) for match, content in spec.stub_rules]
        )
    home = run_dir / "home"
    workspace = run_dir / "workspace"
    # Select the stub as the bundled model exactly the way the served-reality
    # bench does (VOOL_BUNDLE_MANIFEST): without this, AUTO ranks the default
    # local catalog and the turn completes with honest lane-failure answers
    # instead of the deterministic scripted lane.
    bundle_manifest = home / "bundle_manifest.json"
    bundle_manifest.parent.mkdir(parents=True, exist_ok=True)
    # One model identity everywhere: bundle selection, the vLLM lane, and the
    # names the stub itself reports on /api/tags and /api/ps (which the product
    # seeds its ollama-local rows from). The scripted lane is named as what it
    # is, so a video never shows a real model's name over scripted answers.
    # Live captures certify the operator's own default local model instead.
    bundle_model = "qwen2.5:7b" if config.live_models else STUB_MODEL_ID
    if not config.live_models:
        # Stub-lane only wiring (served-reality bench mechanism). In live mode
        # the operator's own catalog and endpoints serve the turn untouched.
        bundle_manifest.write_text(json.dumps({"selected_model": bundle_model}), encoding="utf-8")
    # The deterministic "cloud" lane, exactly as the served-reality bench
    # wires it: a tether-remote manifest pointed at the SAME stub, giving
    # AUTO a second, proven /v1 lane next to the pinned ollama seam.
    extra_env: dict[str, str] = {}
    if stub is not None and stub.port is not None:
        extra_env.update(
            {
                "TETHER_API_KEY": "sr-demo-studio-stub-key-not-a-real-credential",
                "TETHER_BASE_URL": f"http://127.0.0.1:{stub.port}/v1",
                "TETHER_MODEL": "served-reality/cloud-stub",
            }
        )
    # The stub port goes into the FIRST boot, as the served-reality bench does:
    # it aims every model-lane seam at the stub and contains the daemon's
    # network (external fetches get the stub's recorded 502). Every relaunch
    # reuses these exact arguments, so a restarted daemon serves the same
    # lanes, bundle selection and containment as the one it replaced.
    launch_kwargs: dict[str, Any] = {
        "app_dir": staged.path,
        "home": home,
        "workspace": workspace,
        "python_bin": config.python_bin or sys.executable,
        "run_dir": run_dir,
        "stub_port": stub.port if stub is not None else None,
        "stub_model": bundle_model,
        "bundle_manifest": bundle_manifest,
        "expected_sha": staged.sha_full,
        "boot_timeout_s": config.boot_timeout_s,
        "extra_env": extra_env,
    }
    handle = launch_daemon(wire_log=WireLog(run_dir, "boot"), **launch_kwargs)
    certification: dict | None = None
    if config.live_models:
        # Live capture: run the product's OWN tool-certification flow for the
        # boot-seeded default lane — the same step an operator performs at
        # install. The authorship fence refuses uncertified authors, so a
        # failed certification means the capture will honestly film the
        # product's refusal; the manifest records which happened.
        from ops.served_reality.wire import WireClient

        cert_client = WireClient(f"http://127.0.0.1:{handle.port}", WireLog(run_dir, "certify"))
        cert_exchange = cert_client.post(
            "/api/model-tool-certification/run",
            {"provider_name": "ollama-local", "model_name": bundle_model, "timeout_seconds": 120},
            timeout_s=180.0,
        )
        try:
            cert_payload = cert_exchange.response_json() or {}
        except Exception:
            cert_payload = {}
        certification = {
            "endpoint": "/api/model-tool-certification/run",
            "provider_name": "ollama-local",
            "model_name": bundle_model,
            "http_status": cert_exchange.status,
            "certified": bool(
                cert_payload.get("certified")
                or str(cert_payload.get("outcome") or "").lower() in {"certified", "pass", "passed"}
            ),
            "raw": str(cert_payload)[:600],
        }
    lane_wiring = "operator" if stub is None else "env"
    if stub is not None and stub.port is not None and not _ollama_lanes_aimed_at(
        handle.home, f"http://127.0.0.1:{stub.port}"
    ):
        # Commits that seed ollama-local with a hardcoded 127.0.0.1:11434 ignore
        # the stub environment (v0.8 seeds from it, so this branch is skipped there).
        # On those commits the bundle-selected model's manifest is seeded LAZILY on
        # the first chat turn (measured live: it landed after the post-boot repoint,
        # rewriting base_url back to the hardcoded 11434). Warm the lane
        # with one throwaway turn in a side session — its events stay out of
        # the journey's trace — then repoint; the seeder's preserve-branch
        # then leaves the repointed base_url alone.
        from ops.served_reality.wire import WireClient

        lane_wiring = "repoint"
        warm_client = WireClient(f"http://127.0.0.1:{handle.port}", WireLog(run_dir, "warmup"))
        warm_client.post_chat(
            "studio warm-up",
            chat_id="studio-warmup",
            turn_id="turn-studio-warmup",
            timeout_s=60.0,
        )
        repointed = _repoint_ollama_lanes_at_stub(handle.home, stub.port)
        if not repointed:
            handle.terminate()
            stub.stop()
            raise StudioRefusal(
                "could not repoint the seeded ollama-local manifests at the deterministic "
                "stub lane; a hermetic capture would film honest lane failures instead "
                "of the scripted lane it claims to label"
            )
    return BootBundle(
        staged=staged, handle=handle, stub=stub, home=home, workspace=workspace,
        certification=certification, launch_kwargs=launch_kwargs, lane_wiring=lane_wiring,
    )


def relaunch_daemon(bundle: BootBundle, run_dir: Path) -> Any:
    """Stop the filmed daemon and boot the same staged app with the first boot's arguments.

    The stub lanes, the bundle selection, the cloud stub lane and the network
    containment all survive the restart; a subset would film another product.
    """
    from ops.served_reality.daemon import launch_daemon
    from ops.served_reality.wire import WireLog

    bundle.handle.terminate()
    bundle.handle = launch_daemon(wire_log=WireLog(run_dir, "boot"), **bundle.launch_kwargs)
    return bundle.handle


def _runtime_db_path(home: Path) -> Path | None:
    """The product's own resolution (storage/db.py resolve_runtime_db_filename): the canonical
    vool_web0_v2.db, or the pre-rename nulla_web0_v2.db when only that one exists."""
    data_dir = home / "data"
    for name in ("vool_web0_v2.db", "nulla_web0_v2.db"):
        if (data_dir / name).is_file():
            return data_dir / name
    return None


def _ollama_lanes_aimed_at(home: Path, base_url: str) -> bool:
    """True when the product itself seeded ollama-local rows, all of them at ``base_url``.

    Read-only: the studio only looks at what the product persisted. A missing
    store, no rows, or any row aimed elsewhere answers False (fail closed).
    """
    import json as _json
    import sqlite3 as _sqlite3

    db_path = _runtime_db_path(home)
    if db_path is None:
        return False
    try:
        conn = _sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT runtime_config_json FROM model_provider_manifests "
                "WHERE provider_name = 'ollama-local'"
            ).fetchall()
        finally:
            conn.close()
    except _sqlite3.Error:
        return False
    if not rows:
        return False
    want = base_url.rstrip("/")
    for (config_json,) in rows:
        try:
            config = _json.loads(config_json or "{}")
        except ValueError:
            return False
        if str((config or {}).get("base_url") or "").rstrip("/") != want:
            return False
    return True


# ---------------------------------------------------------------------------
# Build identity gate (the line's own guard, ops/build_identity.py)

BUILD_IDENTITY_SCRIPT = Path(__file__).resolve().parents[1] / "build_identity.py"


def _build_identity(config: StudioConfig, *args: str) -> subprocess.CompletedProcess[str]:
    if not BUILD_IDENTITY_SCRIPT.is_file():
        raise StudioRefusal(
            f"lane {config.lane} was asked for, but this checkout has no ops/build_identity.py "
            "to prove the served build is that lane's head"
        )
    command = [sys.executable, str(BUILD_IDENTITY_SCRIPT)]
    if config.heads_manifest:
        command += ["--manifest", config.heads_manifest]
    try:
        return subprocess.run(
            [*command, *args], capture_output=True, text=True, timeout=60, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise StudioRefusal(f"the build identity guard did not answer within 60s: {exc}") from exc


def lane_head(config: StudioConfig) -> str:
    """The lane's head commit, read through the guard's own manifest loader."""
    proc = _build_identity(config, "--json", "show")
    if proc.returncode != 0:
        raise StudioRefusal(
            "the heads manifest could not be read: "
            + (proc.stderr.strip() or proc.stdout.strip())[:400]
        )
    try:
        lanes = json.loads(proc.stdout).get("lanes") or {}
    except (ValueError, AttributeError) as exc:
        raise StudioRefusal(f"the heads manifest is not readable JSON: {exc}") from exc
    head = str((lanes.get(config.lane) or {}).get("head") or "")
    if not head:
        known = ", ".join(sorted(lanes)) or "none"
        raise StudioRefusal(f"lane {config.lane} is not in the heads manifest (lanes: {known})")
    return head


def check_build_identity(config: StudioConfig, base_url: str) -> dict[str, Any]:
    """Run ``build_identity.py check --lane`` against the daemon about to be filmed.

    Anything but the guard's OK (wrong commit, wrong release, dirty, stale
    lane, unreachable, unreadable manifest) refuses the film.
    """
    proc = _build_identity(config, "check", "--url", base_url, "--lane", config.lane)
    output = (proc.stdout.strip() or proc.stderr.strip())[:1200]
    if proc.returncode != 0:
        raise StudioRefusal(
            f"the build identity guard refused lane {config.lane} at {base_url} "
            f"(exit {proc.returncode}): {output}"
        )
    return {
        "lane": config.lane,
        "guard": "ops/build_identity.py check --lane",
        "exit_code": proc.returncode,
        "output": output,
    }


def _repoint_ollama_lanes_at_stub(home: Path, stub_port: int) -> bool:
    """Aim the seeded ollama-local manifests' base_url at the loopback stub.

    Why this exists (root cause, measured live): the answering lane reads the
    PERSISTED manifest's runtime_config.base_url — no env var moves it — and
    the seeder hardcodes http://127.0.0.1:11434. In the contained studio
    environment nothing listens there, so every ollama lane fails its health
    probe and the turn completes with honest lane-failure answers instead of
    the deterministic scripted lane the manifest labels. The registry reads
    the store per call (no cache), so repointing the rows after boot is seen
    by the very next turn. Stub mode only; live captures never touch this.
    """
    import json as _json
    import sqlite3 as _sqlite3

    db_path = _runtime_db_path(home)
    if db_path is None:
        return False
    stub_base = f"http://127.0.0.1:{stub_port}"
    try:
        conn = _sqlite3.connect(str(db_path))
        try:
            rows = conn.execute(
                "SELECT provider_name, model_name, runtime_config_json "
                "FROM model_provider_manifests WHERE provider_name = 'ollama-local'"
            ).fetchall()
            if not rows:
                return False
            for provider_name, model_name, config_json in rows:
                try:
                    config = _json.loads(config_json or "{}")
                except ValueError:
                    config = {}
                config["base_url"] = stub_base
                conn.execute(
                    "UPDATE model_provider_manifests SET runtime_config_json = ? "
                    "WHERE provider_name = ? AND model_name = ?",
                    (_json.dumps(config), provider_name, model_name),
                )
            conn.commit()
        finally:
            conn.close()
    except _sqlite3.Error:
        return False
    return True


@dataclass
class BootBundle:
    staged: Any
    handle: Any
    stub: Any | None
    home: Path
    workspace: Path
    certification: dict | None = None
    #: The exact launch_daemon arguments of the first boot (wire log aside);
    #: a restart relaunches with these, never with a subset.
    launch_kwargs: dict[str, Any] = field(default_factory=dict)
    #: How the scripted lane reached the stub: "env" (the product seeded its
    #: lanes from the stub environment), "repoint" (older commits), or
    #: "operator" (live models, no stub).
    lane_wiring: str = ""

    base_url_override: str | None = None

    @property
    def base_url(self) -> str:
        if self.base_url_override:
            return self.base_url_override
        return f"http://127.0.0.1:{self.handle.port}"

    def teardown(self) -> dict[str, Any]:
        facts: dict[str, Any] = {}
        try:
            unchanged, drifted = self.staged.verify_unchanged()
            facts["staging_unchanged"] = unchanged
            facts["drifted"] = drifted[:20]
        except Exception as exc:  # teardown must survive anything
            facts["staging_check_error"] = str(exc)
        if self.stub is not None:
            with contextlib.suppress(Exception):
                self.stub.stop()
        with contextlib.suppress(Exception):
            self.handle.terminate()
        return facts


# ---------------------------------------------------------------------------
# Dry run


def _dry_run_plan(spec: JourneySpec, out_dir: Path) -> Path:
    plan_dir = out_dir / "dry-run" / spec.id
    plan = {
        "schema": "demo-studio/plan@1",
        "mode": "dry-run",
        "generated_at": DRY_RUN_EPOCH,
        "journey": {
            "id": spec.id,
            "title": spec.title,
            "summary": spec.summary,
            "requires": list(spec.requires),
            "capabilities_required": list(spec.capabilities_required),
            "spec_hash": spec_hash(spec),
            "steps": [s.to_public_dict() for s in spec.steps],
            "expected_events": list(spec.expected_events),
            "assertions": [
                {"kind": a.kind, "target": a.target, "note": a.note} for a in spec.assertions
            ],
        },
        "plan": {
            "boot": "stage the exact SHA (git archive), boot the isolated daemon, "
            "gate on /healthz commit + dirty=false + capability manifest",
            "driver": "real chromium interactions with screen recording, reduced motion pinned",
            "captions": "derived only from the public typed runtime events",
            "render": ["demo.mp4", "demo.webm", "captions.srt", "poster.png", "events.jsonl"],
        },
        "honesty": {"captured": False, "video": False},
    }
    plan_path = plan_dir / "plan.manifest.json"
    write_json(plan_path, plan)
    return plan_path


# ---------------------------------------------------------------------------
# Capture


def _check_assertions(spec: JourneySpec, workspace: Path) -> list[str]:
    failures: list[str] = []
    for assertion in spec.assertions:
        target = workspace / assertion.target
        if assertion.kind == "workspace_file_exists" and not target.is_file():
            failures.append(f"workspace assertion failed: {assertion.target} does not exist")
        elif assertion.kind == "workspace_file_absent" and target.exists():
            failures.append(f"workspace assertion failed: {assertion.target} still exists")
    return failures


def execute_capture(
    *,
    spec: JourneySpec,
    run_dir: Path,
    wire: DemoWireClient,
    driver: Any,
    engine: RedactionEngine,
    boot_report: BootReport | None,
    model_lane: str,
    video_t0_epoch: float | None = None,
    restart: Callable[[], None] | None = None,
    turn_budget_s: float = 180.0,
    certification: dict | None = None,
    out_dir: Path | None = None,
    boot_extra: dict[str, Any] | None = None,
) -> RunOutcome:
    """The orchestration core: steps in, deliverables out, or a loud abort."""
    events: list[dict[str, Any]] = []
    boxes: dict[int, dict[str, int] | None] = {}
    last_seq = 0
    steps_executed: list[dict[str, Any]] = []
    session_id = ""

    def drain() -> None:
        nonlocal last_seq
        # A page that has not minted its session id yet has no trace to read;
        # skip this cycle rather than poll the wrong session.
        if not (driver.session() or ""):
            return
        rows: list[dict] | None = None
        last_error = ""
        # The public events read is served from the same store the turn is
        # writing; a transient serve failure (measured live: intermittent
        # HTTP 500 on /api/runtime/events under concurrent turn load) gets a
        # bounded, transparent retry. Persistent failure still aborts.
        for attempt in range(8):
            try:
                rows = driver.drain_events()
                break
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                time.sleep(1.0 * (attempt + 1))
        if rows is None:
            _abort(spec, run_dir, step_id=session_id or "events",
                   reason=f"event trace unreadable after retries: {last_error}",
                   events=events, engine=engine, missing=[], driver=driver)
        for row in rows:
            seq = int(row.get("seq") or 0)
            if seq <= last_seq:
                continue
            last_seq = seq
            events.append(row)
            from ops.demo_studio.focus import FOCUS_RULES

            rule = FOCUS_RULES.get(str(row.get("event_type") or ""))
            if rule is not None:
                boxes[seq] = driver.observe_box(rule.selector)

    try:
        for step in spec.steps:
            if step.action == "restart_daemon":
                if restart is None:
                    _abort(spec, run_dir, step.id, reason="no restart hook configured",
                           events=events, engine=engine, missing=[], driver=driver)
                try:
                    restart()
                except Exception as exc:
                    _abort(spec, run_dir, step.id, reason=f"daemon restart failed: {exc}",
                           events=events, engine=engine, missing=[], driver=driver)
                steps_executed.append({"id": step.id, "action": step.action, "ok": True})
                continue
            outcome = driver.execute_step(step)
            drain()
            steps_executed.append(
                {"id": step.id, "action": step.action, "ok": outcome.ok, "reason": outcome.reason}
            )
            if not outcome.ok:
                _abort(spec, run_dir, step.id, reason=outcome.reason or "step failed",
                       events=events, engine=engine, missing=[], driver=driver)
            if step.action == "send_message":
                # The turn exists only once the page has a session; wait for
                # the id (bounded) so the event trace is read from the right
                # session instead of an empty one. Then wait until the trace
                # actually carries the turn's acceptance — task_received is
                # written synchronously at turn intake, so an empty trace
                # after completion means we are reading the wrong session
                # (measured live: the page can regenerate its session id);
                # re-read the session and retry, bounded.
                deadline = time.monotonic() + 15.0
                while time.monotonic() < deadline:
                    session_id = driver.session() or ""
                    if session_id:
                        break
                    time.sleep(0.3)
                drain()
                trace_deadline = time.monotonic() + 30.0
                while time.monotonic() < trace_deadline and not events:
                    time.sleep(1.0)
                    session_id = driver.session() or session_id
                    drain()
        drain()

        have = {str(e.get("event_type") or "") for e in events}
        missing = [t for t in spec.expected_events if t not in have]
        if missing:
            _abort(
                spec, run_dir, "<expected-events>",
                reason=f"expected public events never arrived: {', '.join(missing)}",
                events=events, engine=engine, missing=missing, driver=driver,
            )
        assertion_failures = _check_assertions(spec, run_dir / "workspace")
        if assertion_failures:
            _abort(spec, run_dir, "<workspace-assertions>",
                   reason="; ".join(assertion_failures),
                   events=events, engine=engine, missing=[], driver=driver)

        t0 = video_t0_epoch
        if t0 is None and events:
            t0 = float(events[0].get("created_at") or 0.0)
        captions = build_captions(events, engine, video_t0_epoch=t0)
        focus_plan = plan_focus_from_events(events, boxes, video_t0_epoch=t0 or 0.0)
        validate_focus_plan(focus_plan, event_seqs={int(e.get("seq") or 0) for e in events})

        video = driver.finish_video()
        render_report = None
        if video is not None:
            render_report = render_deliverables(
                video_source=video,
                captions=captions,
                focus_plan=[e.to_public_dict() for e in focus_plan],
                out_dir=run_dir,
                poster_at_s=0.5,
            )
        # Public event mirror (redacted): the auditable caption/evidence source.
        mirror = [
            {
                "seq": e.get("seq"),
                "event_type": e.get("event_type"),
                "message": engine.redact(str(e.get("message") or "")),
                "created_at": e.get("created_at"),
            }
            for e in events
            if str(e.get("event_type") or "")
        ]
        (run_dir / "events.jsonl").write_text(
            "".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in mirror),
            encoding="utf-8",
        )

        events_types: dict[str, int] = {}
        for e in events:
            t = str(e.get("event_type") or "")
            events_types[t] = events_types.get(t, 0) + 1
        redaction_counts: dict[str, int] = {}
        for e in events:
            original = str(e.get("message") or "")
            redacted = engine.redact(original)
            if redacted != original:
                redaction_counts["event_messages"] = redaction_counts.get("event_messages", 0) + 1

        product = dict(boot_report.runtime_stamp) if boot_report else {}
        journey_block = {
            "id": spec.id,
            "title": spec.title,
            "spec_hash": spec_hash(spec),
            "steps": [s.to_public_dict() for s in spec.steps],
            "expected_events": list(spec.expected_events),
        }
        manifest = build_evidence_manifest(
            mode="capture",
            journey=journey_block,
            product={
                "commit_full": str(product.get("commit_full") or (boot_report.commit_full if boot_report else "")),
                "commit": str(product.get("commit") or (boot_report.commit if boot_report else "")),
                "app_version": str(product.get("app_version") or (boot_report.app_version if boot_report else "")),
                "dirty": product.get("dirty"),
                "branch": product.get("branch"),
            },
            render_report=render_report,
            captions=captions,
            focus_plan=[e.to_public_dict() for e in focus_plan],
            timeline={
                "t0_epoch": t0 or 0.0,
                "t1_epoch": time.time(),
                "alignment": "event created_at minus capture t0; ±0.5s against video frames",
            },
            honesty={
                "captured": True,
                "model_lane": model_lane,
                "ui": "real browser interactions against the served product; no scripted UI responses",
                "captions_source": "public typed runtime events only; model_output_chunk excluded",
                "model_certified": bool((certification or {}).get("certified")),
            },
            out_dir=run_dir,
            boot={
                "healthz_verified": bool(boot_report),
                "commit_full": boot_report.commit_full if boot_report else "",
                "verified_capabilities": list(boot_report.verified_capabilities) if boot_report else [],
                "model_certification": certification or {},
                **(boot_extra or {}),
            },
            steps_executed=steps_executed,
            events_summary={"count": len(events), "types": events_types, "session_events": session_id != ""},
            redaction_summary={
                "engine": "ops.demo_studio.redact.RedactionEngine",
                "counts": redaction_counts,
                "scan": "clean",
            },
        )
        violations = verify_evidence_manifest(
            manifest,
            run_dir,
            expected_sha=boot_report.commit_full if boot_report else None,
            scan_secrets=engine.scan_secrets,
            workspace_root=engine.workspace_root,
            runtime_home=engine.runtime_home,
        )
        manifest["verification"] = {"violations": violations}
        write_json(run_dir / MANIFEST_NAME, manifest)
        if violations:
            _abort(spec, run_dir, "<evidence-verification>",
                   reason=f"evidence manifest failed verification: {violations}",
                   events=events, engine=engine, missing=[], driver=driver, quiet=True)
        return RunOutcome(
            verdict="pass",
            journey=spec.id,
            run_dir=run_dir,
            manifest_path=str(
                (run_dir / MANIFEST_NAME).relative_to(out_dir) if out_dir else MANIFEST_NAME
            ),
            detail=f"{len(events)} public events, {len(captions)} captions, "
            f"{len(focus_plan)} justified zooms",
        )
    finally:
        with contextlib.suppress(Exception):
            driver.shutdown()


def _abort(
    spec: JourneySpec,
    run_dir: Path,
    step_id: str,
    *,
    reason: str,
    events: Sequence[dict],
    engine: RedactionEngine,
    missing: list[str],
    driver: Any,
    quiet: bool = False,
) -> None:
    del quiet
    report = {
        "schema": "demo-studio/abort-report@1",
        "verdict": "aborted",
        "journey": spec.id,
        "failed_step": step_id,
        "reason": engine.redact(reason),
        "missing_expected_events": list(missing),
        "events": [
            {
                "seq": e.get("seq"),
                "event_type": e.get("event_type"),
                "message": engine.redact(str(e.get("message") or "")),
            }
            for e in events
        ],
        "generated_at": utc_now_iso(),
    }
    report_path = run_dir / "abort-report.json"
    write_json(report_path, report)
    raise JourneyAborted(
        journey=spec.id,
        step_id=step_id,
        reason=reason,
        report_path=report_path,
        missing_expected_events=missing,
    )


# ---------------------------------------------------------------------------
# Entry


def run_journey(journey_id: str, config: StudioConfig) -> RunOutcome:
    try:
        spec = journey_spec(journey_id)
    except LookupError as exc:
        raise StudioRefusal(str(exc)) from exc

    unallowed = [r for r in spec.requires if r not in config.allowed_requirements]
    if unallowed:
        raise StudioRefusal(
            f"journey {journey_id} requires {', '.join(unallowed)} which this run does not "
            "allow; allow it explicitly (allowed_requirements) or run where the dependency "
            "exists — the studio will not fake it"
        )

    if config.mode == StudioMode.DRY_RUN:
        plan_path = _dry_run_plan(spec, config.out_dir)
        return RunOutcome(
            verdict="planned",
            journey=journey_id,
            run_dir=plan_path.parent,
            manifest_path=str(plan_path.relative_to(config.out_dir)),
            detail="dry-run plan written; nothing was booted, recorded, or claimed",
        )
    if config.mode != StudioMode.CAPTURE:
        raise StudioRefusal(f"unknown studio mode {config.mode!r}")

    if config.lane:
        # A lane film stages exactly the lane's head. An explicit --sha that is
        # some other commit is refused before anything boots.
        head = lane_head(config)
        if config.sha in ("", "HEAD"):
            config = replace(config, sha=head)
        else:
            from ops.served_reality.appstage import resolve_sha

            _short, full, _branch = resolve_sha(config.repo, config.sha)
            if full != head:
                raise StudioRefusal(
                    f"--sha {config.sha} is {full[:8]}, but lane {config.lane} head is "
                    f"{head[:8]}; film the lane head or drop --lane"
                )

    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    run_dir = config.out_dir / f"{journey_id}-{stamp}"
    if run_dir.exists():
        run_dir = config.out_dir / f"{journey_id}-{stamp}-{int(time.time()) & 0xFFFF:04x}"
    run_dir.mkdir(parents=True)

    bundle = _boot_staged_app(config, run_dir, spec)
    try:
        wire = DemoWireClient(bundle.base_url)
        boot_report = verify_served_boot(
            wire, expected_sha=bundle.staged.sha_full, required_capabilities=spec.capabilities_required
        )
        # Every daemon the film shows passes the lane guard: the first boot
        # here, each relaunch in _restart. The manifest keeps every verdict.
        build_identity_checks: list[dict[str, Any]] = []
        if config.lane:
            build_identity_checks.append(check_build_identity(config, bundle.base_url))
        attach_dir = run_dir / "attachments"
        attach_dir.mkdir(exist_ok=True)
        _materialize_attachments(spec, attach_dir)

        def _blackbox(args: list[str]) -> dict[str, Any]:
            return _run_blackbox_cli(
                config.python_bin or sys.executable, bundle.staged.path, bundle.home, args
            )

        def _restart() -> None:
            new_handle = relaunch_daemon(bundle, run_dir)
            # The relaunched daemon binds a NEW port: repoint the driver and
            # open the page there, or the journey's reload would knock on the
            # dead port (measured live as ERR_CONNECTION_REFUSED).
            new_base = f"http://127.0.0.1:{new_handle.port}"
            bundle.base_url_override = new_base
            if config.lane:
                build_identity_checks.append(check_build_identity(config, new_base))
            wire.update_base_url(new_base)
            driver.rebase(new_base)
            driver.open(new_base + "/chat")

        driver: Any
        if config.driver_factory is not None:
            driver = config.driver_factory(wire=wire, run_dir=run_dir, base_url=bundle.base_url)
        else:
            from ops.demo_studio.driver import PlaywrightDriver

            driver = PlaywrightDriver(
                wire=wire,
                run_dir=run_dir,
                base_url=bundle.base_url,
                headless=config.headless,
                video=config.video,
                blackbox_runner=_blackbox,
                attach_dir=attach_dir,
                workspace_root=bundle.workspace,
            )
        try:
            driver.open(bundle.base_url + "/chat")
        except Exception as exc:
            bundle.teardown()
            engine = RedactionEngine(
                workspace_root=str(bundle.workspace), runtime_home=str(bundle.home)
            )
            _abort(
                spec, run_dir, "open-chat",
                reason=f"the real page never opened: {exc}",
                events=[], engine=engine, missing=[], driver=driver,
            )
        t0 = time.time()
        model_lane = "operator_models" if config.live_models else "scripted_stub"
        outcome = execute_capture(
            spec=spec,
            run_dir=run_dir,
            wire=wire,
            driver=driver,
            engine=RedactionEngine(
                workspace_root=str(bundle.workspace),
                runtime_home=str(bundle.home),
            ),
            boot_report=boot_report,
            model_lane=model_lane,
            video_t0_epoch=t0,
            restart=_restart,
            turn_budget_s=config.turn_budget_s,
            certification=bundle.certification,
            out_dir=config.out_dir,
            boot_extra={
                "stub_lane_wiring": bundle.lane_wiring,
                "build_identity": build_identity_checks,
            },
        )
        facts = bundle.teardown()
        if not facts.get("staging_unchanged", True):
            outcome.detail += " (WARNING: staged app drifted during the run: "
            outcome.detail += ", ".join(map(str, facts.get("drifted", []))[:5]) + ")"
        return outcome
    except JourneyAborted:
        bundle.teardown()
        raise
    except Exception:
        bundle.teardown()
        raise


def _materialize_attachments(spec: JourneySpec, attach_dir: Path) -> None:
    """Deterministic attachment fixtures for journeys that attach files."""
    for step in spec.steps:
        if step.action != "attach_files":
            continue
        for name in [n.strip() for n in step.value.split(",") if n.strip()]:
            target = attach_dir / name
            if target.exists():
                continue
            if name.endswith(".png"):
                # Canonical 1x1 transparent PNG, constant bytes.
                import base64 as _base64

                target.write_bytes(
                    _base64.b64decode(
                        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
                        "AAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
                    )
                )
            else:
                target.write_text(
                    "Demo note: this text file was staged by the VOOL demo studio "
                    "for the attachment journey.\n",
                    encoding="utf-8",
                )


def _run_blackbox_cli(python_bin: str, staged_path: Path, home: Path, args: list[str]) -> dict[str, Any]:
    """Run the real operator CLI against the isolated home; capture JSON."""
    import json as _json
    import os as _os
    import subprocess as _subprocess

    env = dict(_os.environ)
    env["VOOL_HOME"] = str(home)
    env["VOOL_CREDENTIAL_STORE"] = "vault"
    proc = _subprocess.run(
        [python_bin, "-m", "core.blackbox", *args],
        cwd=str(staged_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    payload: dict[str, Any] = {"rc": proc.returncode, "stdout": proc.stdout[-4000:], "stderr": proc.stderr[-2000:]}
    try:
        payload["json"] = _json.loads(proc.stdout)
    except (ValueError, IndexError):
        payload["json"] = None
    turns = ((payload.get("json") or {}).get("turns") if isinstance(payload.get("json"), dict) else None) or []
    if turns:
        payload["turns"] = turns
    if args and args[0] == "rollback" and isinstance(payload.get("json"), dict):
        payload["ok"] = bool(payload["json"].get("ok"))
    return payload
