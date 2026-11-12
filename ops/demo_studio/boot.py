"""The boot gate: an exact-SHA, clean, capability-verified served app.

The studio never films "whatever is running on the port". This module is the
STUDIO-side second opinion (defense in depth): the staged boot itself is
already gated by ``ops.served_reality.launch_daemon``; here the served
/healthz and the dedicated capability manifest are verified independently —
with three separately-written SHA checks so blinding any one of them still
refuses a lying daemon.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ops.demo_studio.wire import DemoWireClient, WireError


class StudioBootRefused(RuntimeError):
    """The served app failed the studio's boot gate; no demo may be filmed."""


@dataclass
class BootReport:
    commit_full: str
    commit: str
    dirty: bool
    app_version: str
    verified_capabilities: tuple[str, ...]
    runtime_stamp: dict[str, Any] = field(default_factory=dict)
    capabilities_raw: dict[str, Any] = field(default_factory=dict)


def _full_sha_matches(reported: str, expected: str) -> bool:
    """Layer A: the daemon's commit_full is the exact staged commit."""
    return bool(reported) and reported == expected


def _short_matches_expected(runtime: dict[str, Any], expected: str) -> bool:
    """Layer B1: the short commit field agrees with the EXPECTED commit.

    Written against different fields than layer A on purpose: a daemon (or a
    sabotaged checker) that lies about only one of the two fields cannot pass
    both.
    """
    short = str(runtime.get("commit") or "")
    return bool(short) and short == expected[: len(short)] and len(short) >= 7


def _short_consistent(runtime: dict[str, Any]) -> bool:
    """Layer B2: the short commit is a prefix of the reported commit_full."""
    short = str(runtime.get("commit") or "")
    full = str(runtime.get("commit_full") or "")
    return bool(short) and bool(full) and full.startswith(short)


def verify_served_boot(
    wire: DemoWireClient,
    *,
    expected_sha: str,
    required_capabilities: Iterable[str] = (),
) -> BootReport:
    """Verify healthz truth + capability manifest, or refuse with the cause."""
    try:
        healthz = wire.healthz()
    except WireError as exc:
        raise StudioBootRefused(f"served app did not answer a verifiable /healthz: {exc}") from exc

    if healthz.get("ok") is not True:
        raise StudioBootRefused(f"/healthz does not report ok=true: {healthz!r:.400}")

    runtime = dict(healthz.get("runtime") or {})
    reported_full = str(runtime.get("commit_full") or "")
    reported_short = str(runtime.get("commit") or "")

    if runtime.get("dirty") is True:
        raise StudioBootRefused(
            "served app reports dirty=true; the demo must film an exact, clean commit"
        )
    if not _full_sha_matches(reported_full, expected_sha):
        raise StudioBootRefused(
            "boot gate: full-SHA mismatch — expected "
            f"{expected_sha[:12]}…, daemon reported commit_full {reported_full[:12] or '<none>'}…"
        )
    if not _short_matches_expected(runtime, expected_sha):
        raise StudioBootRefused(
            "boot gate: short commit disagrees with the expected staged commit "
            f"(commit={reported_short or '<none>'!r}, expected~{expected_sha[:12]}…) — "
            "the daemon's identity fields are inconsistent with the staging"
        )
    if not _short_consistent(runtime):
        raise StudioBootRefused(
            "boot gate: commit fields contradict each other "
            f"(commit={reported_short or '<none>'!r}, commit_full={reported_full[:12] or '<none>'}…)"
        )

    try:
        capabilities = wire.capabilities()
    except WireError as exc:
        raise StudioBootRefused(
            f"served app did not answer a verifiable capability manifest: {exc}"
        ) from exc
    rows = [row for row in (capabilities.get("capabilities") or []) if isinstance(row, dict)]
    implemented = {
        str(row.get("name"))
        for row in rows
        if str(row.get("state") or "") == "implemented"
    }
    missing = [name for name in required_capabilities if name not in implemented]
    if missing:
        raise StudioBootRefused(
            "capability manifest is missing rows the journey depends on: "
            + ", ".join(sorted(missing))
            + f" (implemented rows: {sorted(implemented)})"
        )

    return BootReport(
        commit_full=reported_full,
        commit=reported_short,
        dirty=False,
        app_version=str(runtime.get("app_version") or ""),
        verified_capabilities=tuple(sorted(set(required_capabilities))),
        runtime_stamp=runtime,
        capabilities_raw=capabilities,
    )
