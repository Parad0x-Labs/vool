"""``python -m ops.demo_studio`` — the operator CLI for the Demo Studio.

Exit codes: 0 pass/planned · 2 refusal (config, journey, or boot gate) ·
3 journey aborted (abort report on disk). An abort prints a visible banner —
a failed demo never looks like a success.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ops.demo_studio.journeys import JOURNEYS, journey_spec
from ops.demo_studio.runner import (
    JourneyAborted,
    StudioConfig,
    StudioRefusal,
    run_journey,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ops.demo_studio",
        description="Record the real served VOOL product (never a stand-in).",
    )
    parser.add_argument("--list", action="store_true", help="list the typed demo journeys")
    parser.add_argument("--journey", default="", help="journey id (see --list)")
    parser.add_argument("--mode", default="capture", choices=["dry-run", "capture"])
    parser.add_argument("--sha", default="HEAD",
                        help="exact commit to stage and film (with --lane: the lane head)")
    parser.add_argument("--repo", default="", help="product repository (default: this checkout)")
    parser.add_argument("--out", default="", help="output directory for runs and plans")
    parser.add_argument("--python", default=sys.executable, help="python that boots the staged app")
    parser.add_argument("--live-models", action="store_true",
                        help="use the operator's real model lanes instead of the scripted stub lane")
    parser.add_argument("--no-video", action="store_true", help="drive without screen recording")
    parser.add_argument("--headed", action="store_true", help="show the browser window")
    parser.add_argument("--allow", action="append", default=[],
                        help="allow a journey requirement (e.g. --allow live_network)")
    parser.add_argument("--lane", default="",
                        help="film this lane's head only (heads manifest of ops/build_identity.py); "
                        "every daemon filmed must pass `build_identity.py check --lane`")
    parser.add_argument("--heads-manifest", default="",
                        help="heads manifest for --lane (default: $VOOL_HEADS_MANIFEST)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.list:
        for jid in JOURNEYS:
            spec = journey_spec(jid)
            requires = f"  requires: {', '.join(spec.requires)}" if spec.requires else ""
            print(f"{spec.id:36s} {spec.title}{requires}")
        return 0
    if not args.journey:
        print("error: --journey is required (see --list)", file=sys.stderr)
        return 2

    repo = Path(args.repo).resolve() if args.repo else Path(__file__).resolve().parents[2]
    out = Path(args.out).resolve() if args.out else Path("/tmp/vool-demo-studio")
    config = StudioConfig(
        repo=repo,
        out_dir=out,
        mode=args.mode,
        sha=args.sha,
        python_bin=args.python,
        live_models=args.live_models,
        headless=not args.headed,
        video=not args.no_video,
        allowed_requirements=tuple(args.allow),
        lane=args.lane,
        heads_manifest=args.heads_manifest,
    )
    try:
        outcome = run_journey(args.journey, config)
    except StudioRefusal as exc:
        print(f"\nREFUSED: {exc}\n", file=sys.stderr)
        return 2
    except JourneyAborted as exc:
        print("\n================ DEMO JOURNEY ABORTED ================", file=sys.stderr)
        print(f"journey : {exc.journey}", file=sys.stderr)
        print(f"step    : {exc.step_id}", file=sys.stderr)
        print(f"reason  : {exc.reason}", file=sys.stderr)
        print(f"report  : {exc.report_path}", file=sys.stderr)
        if exc.missing_expected_events:
            print(f"missing : {', '.join(exc.missing_expected_events)}", file=sys.stderr)
        print("======================================================\n", file=sys.stderr)
        return 3
    print(f"{outcome.verdict}: {outcome.journey}")
    print(f"  run dir : {outcome.run_dir}")
    print(f"  manifest: {out / outcome.manifest_path}")
    print(f"  {outcome.detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
