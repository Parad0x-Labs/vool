from __future__ import annotations

import argparse
import re
import subprocess
import sys
import uuid
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class SmokePack:
    key: str
    slug: str
    description: str
    targets: tuple[str, ...]
    touches_public_surfaces: bool = False


@dataclass(frozen=True)
class GateStep:
    label: str
    command: tuple[str, ...]


PACK_ORDER: tuple[str, ...] = ("A", "B", "C", "D", "E", "F", "G", "H")
LIVE_SMOKE_TAG_PREFIX = "VOOL_SMOKE"
LIVE_QUOTE_REQUIRED_FIELDS: tuple[str, ...] = (
    "asset_name",
    "value",
    "currency",
    "as_of",
    "source_label",
    "source_url",
)

SMOKE_PACKS: OrderedDict[str, SmokePack] = OrderedDict(
    (
        (
            "A",
            SmokePack(
                key="A",
                slug="live_info",
                description="Live info truth and presentation: quotes, weather, entity lookup, honest failure modes.",
                targets=(
                    "tests/test_web_research_runtime.py",
                    "tests/test_vool_web_freshness_and_lookup.py",
                    "tests/test_alpha_semantic_context_smoke.py",
                    "tests/test_openclaw_tooling_context.py",
                    "tests/test_vool_chat_truth_instrumentation.py",
                ),
            ),
        ),
        (
            "B",
            SmokePack(
                key="B",
                slug="hive_task_lifecycle",
                description="Hive task create, confirm, visibility, pickup, and public bridge consistency.",
                targets=(
                    "tests/test_vool_hive_task_flow.py",
                    "tests/test_public_hive_bridge.py",
                    "tests/test_hive_activity_tracker.py",
                    "tests/test_openclaw_tooling_context.py",
                ),
                touches_public_surfaces=True,
            ),
        ),
        (
            "C",
            SmokePack(
                key="C",
                slug="research_substance",
                description="Evidence-backed research, artifact packing, and honest insufficient-evidence outcomes.",
                targets=(
                    "tests/test_brain_hive_research.py",
                    "tests/test_autonomous_topic_research.py",
                    "tests/test_brain_hive_artifacts.py",
                ),
                touches_public_surfaces=True,
            ),
        ),
        (
            "D",
            SmokePack(
                key="D",
                slug="review_and_quality",
                description="Review queues, moderation, partial-result states, and public cleanup surfaces.",
                targets=(
                    "tests/test_brain_hive_dashboard.py",
                    "tests/test_brain_hive_service.py",
                    "tests/test_public_hive_quotas.py",
                    "tests/test_public_hive_bridge.py",
                ),
                touches_public_surfaces=True,
            ),
        ),
        (
            "E",
            SmokePack(
                key="E",
                slug="credits_and_score",
                description="Credit balances, escrow, transfer, and Hive economy contract.",
                targets=(
                    "tests/test_credit_ledger.py",
                    "tests/test_vool_credits_and_hive_economy_spec.py",
                    "tests/test_public_hive_bridge.py",
                ),
            ),
        ),
        (
            "F",
            SmokePack(
                key="F",
                slug="memory_and_dense_shards",
                description="Local-first memory, heuristics, persistence, and personalization recall.",
                targets=(
                    "tests/test_vool_local_first_memory_and_personalization.py",
                    "tests/test_persistent_memory_and_preferences.py",
                    "tests/test_dialogue_memory_assistant_turns.py",
                ),
            ),
        ),
        (
            "G",
            SmokePack(
                key="G",
                slug="voolbook_and_public_web",
                description="VoolBook, watch/dashboard surfaces, feed hygiene, and public web health.",
                targets=(
                    "tests/test_public_landing_page.py",
                    "tests/test_voolbook_api.py",
                    "tests/test_voolbook_feed_page.py",
                    "tests/test_voolbook_identity.py",
                    "tests/test_voolbook_profile_page.py",
                    "tests/test_voolbook_store.py",
                    "tests/test_meet_and_greet_service.py",
                    "tests/test_brain_hive_watch_server.py",
                    "tests/test_brain_hive_watch_config_loader.py",
                    "tests/test_browser_render_flag.py",
                    "tests/test_public_web_browser_smoke.py",
                    "tests/test_repo_hygiene_check.py",
                ),
                touches_public_surfaces=True,
            ),
        ),
        (
            "H",
            SmokePack(
                key="H",
                slug="wallet_x402",
                description="Wallet custody, payment lifecycle, limits, idempotency, x402 cap, card tokens, the API seam and the sabotage fences.",
                targets=(
                    "tests/wallet/test_wallet_custody.py",
                    "tests/wallet/test_wallet_lifecycle.py",
                    "tests/wallet/test_wallet_api.py",
                    "tests/wallet/test_wallet_sabotage.py",
                    "tests/test_fault_catalog.py",
                    "tests/test_security_events.py",
                ),
            ),
        )
    )
)


def pack_sequence_through(pack_key: str) -> tuple[str, ...]:
    normalized = str(pack_key or "").strip().upper()
    if normalized not in SMOKE_PACKS:
        raise KeyError(f"unknown pack: {pack_key!r}")
    stop_index = PACK_ORDER.index(normalized) + 1
    return PACK_ORDER[:stop_index]


def cumulative_targets(pack_keys: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for pack_key in pack_keys:
        pack = SMOKE_PACKS[str(pack_key).strip().upper()]
        for target in pack.targets:
            if target in seen:
                continue
            seen.add(target)
            ordered.append(target)
    return tuple(ordered)


def make_live_smoke_tag(pack_key: str, *, label: str = "artifact", now: datetime | None = None, entropy: str | None = None) -> str:
    normalized_pack = str(pack_key or "").strip().upper()
    if normalized_pack not in SMOKE_PACKS:
        raise KeyError(f"unknown pack: {pack_key!r}")
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^a-z0-9]+", "-", str(label or "artifact").lower()).strip("-") or "artifact"
    token = str(entropy or uuid.uuid4().hex[:8]).strip().lower()[:16] or uuid.uuid4().hex[:8]
    return f"[{LIVE_SMOKE_TAG_PREFIX}:{normalized_pack}:{slug}:{stamp}:{token}]"


def is_live_smoke_tag(text: str) -> bool:
    return bool(
        re.search(
            rf"\[{LIVE_SMOKE_TAG_PREFIX}:[A-G]:[a-z0-9-]+:\d{{8}}T\d{{6}}Z:[a-z0-9]{{4,16}}\]",
            str(text or ""),
        )
    )


def cleanup_verification_terms(tag: str) -> tuple[str, ...]:
    text = str(tag or "").strip()
    if not text:
        return ()
    bare = text[1:-1] if text.startswith("[") and text.endswith("]") else text
    return (text, bare)


def validate_live_quote_payload(payload: dict[str, object]) -> tuple[bool, str]:
    missing = [field for field in LIVE_QUOTE_REQUIRED_FIELDS if not str(payload.get(field) or "").strip()]
    if missing:
        return False, f"missing required live quote fields: {', '.join(missing)}"
    raw_value = payload.get("value")
    try:
        value = float(raw_value)  # type: ignore[arg-type]
    except Exception:
        return False, "live quote value must be numeric"
    if value <= 0:
        return False, "live quote value must be positive"
    if not str(payload.get("source_url") or "").startswith(("http://", "https://")):
        return False, "live quote source_url must be absolute"
    return True, "ok"


def build_gate_steps(
    pack_key: str,
    *,
    include_full_pytest: bool = True,
    extra_pytest_args: Sequence[str] = (),
    full_workers: int = 1,
) -> tuple[GateStep, ...]:
    sequence = pack_sequence_through(pack_key)
    current_pack = SMOKE_PACKS[sequence[-1]]
    targeted_command = (
        sys.executable,
        "-m",
        "pytest",
        "-q",
        *current_pack.targets,
        *extra_pytest_args,
    )
    cumulative_command = (
        sys.executable,
        "-m",
        "pytest",
        "-q",
        *cumulative_targets(sequence),
        *extra_pytest_args,
    )
    steps = [
        GateStep(label=f"{current_pack.key} targeted ({current_pack.slug})", command=targeted_command),
        GateStep(label=f"{'+'.join(sequence)} cumulative packs", command=cumulative_command),
    ]
    if include_full_pytest:
        worker_count = max(1, int(full_workers))
        worker_label = f"{worker_count} shard{'s' if worker_count != 1 else ''}"
        steps.append(
            GateStep(
                label=f"authoritative verification ({worker_label})",
                command=(
                    sys.executable,
                    "ops/verify.py",
                    "--workers",
                    str(worker_count),
                    *tuple(f"--pytest-arg={item}" for item in extra_pytest_args),
                ),
            )
        )
    return tuple(steps)


def _run_step(step: GateStep, *, cwd: Path, dry_run: bool) -> int:
    rendered = " ".join(step.command)
    print(f"\n==> {step.label}\n$ {rendered}")
    if dry_run:
        return 0
    completed = subprocess.run(step.command, cwd=str(cwd))
    return int(completed.returncode)


def _list_packs() -> int:
    for pack in SMOKE_PACKS.values():
        public_label = " public" if pack.touches_public_surfaces else ""
        print(f"{pack.key} {pack.slug}{public_label}")
        print(f"  {pack.description}")
        for target in pack.targets:
            print(f"  - {target}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run cumulative stabilization gates for VOOL/Hive/VoolBook work."
    )
    parser.add_argument("--list", action="store_true", help="List the smoke-pack ladder and exit.")
    parser.add_argument(
        "--through",
        choices=PACK_ORDER,
        help="Run the cumulative gate through the selected pack key.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the commands without executing them.")
    parser.add_argument(
        "--skip-full-pytest",
        action="store_true",
        help="Do not append the final full pytest gate.",
    )
    parser.add_argument(
        "--pytest-arg",
        action="append",
        default=[],
        help="Extra argument forwarded to targeted and cumulative pytest commands. Repeat as needed.",
    )
    parser.add_argument(
        "--full-workers",
        type=int,
        default=1,
        help="When >1, run the final full pytest gate through ops/pytest_shards.py with this many workers.",
    )
    args = parser.parse_args(argv)

    if args.list:
        return _list_packs()
    if not args.through:
        parser.error("--through is required unless --list is used")

    repo_root = Path(__file__).resolve().parent.parent
    steps = build_gate_steps(
        args.through,
        include_full_pytest=not bool(args.skip_full_pytest),
        extra_pytest_args=tuple(str(item) for item in args.pytest_arg),
        full_workers=max(1, int(args.full_workers)),
    )
    for step in steps:
        rc = _run_step(step, cwd=repo_root, dry_run=bool(args.dry_run))
        if rc != 0:
            return rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
