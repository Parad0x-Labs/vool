"""User-facing CLI for the signed atomic updater.

    python -m installer.update_cli check    --manifest-url URL [--channel stable] ...
    python -m installer.update_cli status   [--data-dir DIR]
    python -m installer.update_cli apply    --app-path PATH --manifest-url URL \
        --i-pressed-update [--data-dir DIR] [--health-url URL] [--pid-file FILE] \
        [--relaunch-cmd "..."] [--resolve-destructive id1,id2 --because TEXT]
    python -m installer.update_cli recover  --app-path PATH [--data-dir DIR] [--health-url URL]

`apply` is the terminal form of the explicit Update press: the flag's name is
deliberately not something an automation passes by accident. check/status/recover
never install anything. Every command prints plain-language output; exit code 0 iff
the operation succeeded.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.updater.download import StagedDownloader
from core.updater.health import HttpHealthProbe
from core.updater.macos import MacProcessHelper
from core.updater.migrations import MigrationCatalog
from core.updater.platforms import installer_for
from core.updater.platforms import platform_key as live_platform_key
from core.updater.runtime import UpdateSubsystem, boot_update_subsystem
from core.updater.service import UpdateCheckService, UpdateController, UserGesture
from core.updater.state import UpdaterPaths
from core.updater.status import StatusStore
from core.updater.transaction import UpdateFlow, recover_interrupted_update
from core.updater.trust import TrustedPublishers
from core.updater.work import DestructiveWorkResolution, WorkCoordinator


def _data_dir(args: argparse.Namespace) -> Path:
    if getattr(args, "data_dir", ""):
        return Path(args.data_dir).expanduser().resolve()
    from core.runtime_paths import active_data_dir

    return active_data_dir()


def _platform(args: argparse.Namespace) -> str:
    return getattr(args, "platform", "") or live_platform_key()


def _installed_version() -> str:
    from core.app_version import installed_version

    return installed_version()


def cmd_check(args: argparse.Namespace) -> int:
    from dataclasses import replace

    from core.updater.feed import load_feed_config

    feed = load_feed_config()
    if args.manifest_url:
        feed = replace(feed, manifest_url=args.manifest_url)
    if args.channel:
        feed = replace(feed, channel=args.channel)
    subsystem = UpdateSubsystem(feed=feed, data_dir=_data_dir(args), platform=_platform(args))
    payload = subsystem.trigger_check()
    if payload.get("message"):
        print(payload["message"])
    if not payload.get("configured"):
        print(payload.get("unavailable_plain") or "Updates are unavailable.")
        return 1
    return 0 if payload.get("check_ok") else 1


def cmd_restart(args: argparse.Namespace) -> int:
    """The second press: resume the parked transaction through swap + helper + health."""
    subsystem = boot_update_subsystem()
    if not args.i_pressed_update:
        print("Refusing to restart: no explicit press (--i-pressed-update).")
        return 2
    import time

    resolution = None
    if args.resolve_destructive:
        resolution = DestructiveWorkResolution(
            operator_ack=args.because or "operator acknowledged via CLI",
            work_ids=tuple(args.resolve_destructive.split(",")),
        )
    outcome = subsystem.press_restart(UserGesture(pressed_at=time.time(), origin="cli"), resolution=resolution)
    print(outcome.detail or "")
    return 0 if outcome.accepted else 1


def cmd_status(args: argparse.Namespace) -> int:
    store = StatusStore(UpdaterPaths.for_data_dir(_data_dir(args)).status_file)
    status = store.load()
    if status is None:
        print("No update status yet — nothing has checked for updates.")
        return 0
    print(status.message)
    if args.json:
        print(json.dumps(status.to_dict(), sort_keys=True, indent=2))
    return 0


def _build_flow(args: argparse.Namespace, coordinator: WorkCoordinator) -> UpdateFlow:
    helper = MacProcessHelper(
        app_path=Path(args.app_path).expanduser().resolve(),
        pid_file=Path(args.pid_file).expanduser() if getattr(args, "pid_file", "") else None,
        relaunch_cmd=shlex_split(getattr(args, "relaunch_cmd", "") or ""),
    )
    probe = HttpHealthProbe(args.health_url)
    return UpdateFlow(
        data_dir=_data_dir(args),
        app_path=Path(args.app_path).expanduser().resolve(),
        platform=_platform(args),
        installed_version=_installed_version(),
        channel=args.channel,
        trust=TrustedPublishers.load(),
        downloader=StagedDownloader(),
        coordinator=coordinator,
        migration_catalog=MigrationCatalog(),  # steps register with the catalog as they exist
        helper=helper,
        health_probe=probe.probe,
        installer=installer_for(_platform(args)),
    )


def shlex_split(text: str) -> list[str]:
    import shlex

    return shlex.split(text) if text.strip() else []


def cmd_apply(args: argparse.Namespace) -> int:
    if not args.i_pressed_update:
        print("Refusing to install: no explicit update press (--i-pressed-update).")
        return 2
    coordinator = WorkCoordinator()
    service = UpdateCheckService(
        manifest_url=args.manifest_url,
        trust=TrustedPublishers.load(),
        installed_version=_installed_version(),
        channel=args.channel,
        platform=_platform(args),
        data_dir=_data_dir(args),
    )
    outcome = service.check_once()
    if not outcome.ok or service.current_offer() is None:
        decision = outcome.decision
        print(decision.plain_message if decision is not None else (outcome.detail or "The update check didn't complete."))
        return 1
    controller = UpdateController(
        offer_loader=service.current_offer,
        flow_factory=lambda: _build_flow(args, coordinator),
    )
    resolution = None
    if args.resolve_destructive:
        resolution = DestructiveWorkResolution(
            operator_ack=args.because or "operator acknowledged via CLI",
            work_ids=tuple(args.resolve_destructive.split(",")),
        )
    result = controller.press_update(UserGesture(pressed_at=__import__("time").time(), origin="cli"), resolution=resolution)
    store = StatusStore(UpdaterPaths.for_data_dir(_data_dir(args)).status_file)
    status = store.load()
    print((status.message if status else "") or result.detail)
    return 0 if result.ok else 1


def cmd_rollback(args: argparse.Namespace) -> int:
    """Ask a live app first (its work gate); otherwise use the detached local helper."""
    import time
    import urllib.error
    import urllib.parse
    import urllib.request
    from dataclasses import replace

    from core.updater.feed import load_feed_config

    if not args.i_pressed_rollback:
        print('Refusing rollback: use --i-pressed-rollback for an explicit request.')
        return 2
    feed = load_feed_config()
    feed = replace(feed, app_path=args.app_path or feed.app_path,
                   health_url=args.health_url or feed.health_url)
    address = urllib.parse.urlsplit(feed.health_url)
    if address.scheme not in ('http', 'https') or address.hostname not in ('localhost', '127.0.0.1', '::1'):
        print('Rollback requires a loopback health URL.')
        return 2
    origin = urllib.parse.urlunsplit((address.scheme, address.netloc, '', '', ''))
    request = urllib.request.Request(origin + '/api/update/rollback', data=json.dumps({
        'expected_app_path': feed.app_path, 'expected_data_dir': str(_data_dir(args)),
    }).encode(),
                                     headers={'Content-Type': 'application/json', 'Origin': origin})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            outcome = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        print(f'Running app refused rollback ({exc.code}); its work gate was respected.')
        return 1
    except urllib.error.URLError:
        subsystem = UpdateSubsystem(feed=feed, data_dir=_data_dir(args), platform=_platform(args))
        outcome = subsystem.press_rollback(UserGesture(pressed_at=time.time(), origin='cli')).to_dict()
    if not outcome.get('accepted'):
        print(outcome.get('detail') or 'Rollback could not start; current data was kept.')
        return 1
    print('Rollback started. Current chats, memory, keys and settings are kept.')
    if not args.wait:
        return 0
    state = _data_dir(args) / 'update_v2/manual_rollbacks' / outcome['txid'] / 'state.json'
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        from core.updater.state import _read_json

        result = _read_json(state)
        phase = result.get('phase') if isinstance(result, dict) else ''
        if phase in ('completed', 'restored', 'aborted', 'rollback_failed'):
            print(f'Rollback result: {phase}')
            return 0 if phase == 'completed' else 1
        time.sleep(.2)
    print('Rollback is still pending; inspect update status before trying again.')
    return 1


def cmd_recover(args: argparse.Namespace) -> int:
    helper = MacProcessHelper(
        app_path=Path(args.app_path).expanduser().resolve(),
        pid_file=Path(args.pid_file).expanduser() if getattr(args, "pid_file", "") else None
    )
    probe = HttpHealthProbe(args.health_url)
    results = recover_interrupted_update(
        data_dir=_data_dir(args),
        app_path=Path(args.app_path).expanduser().resolve(),
        helper=helper,
        health_probe=probe.probe,
        installed_version=_installed_version(),
        channel=args.channel,
    )
    if not results:
        print("No unfinished updates found — nothing to recover.")
        return 0
    for item in results:
        verb = {
            "aborted": "An unfinished update was cleaned up (the app was never changed).",
            "rolled_back": "An unfinished update was rolled back and the previous version restarted.",
            "finalized": "An update that had been interrupted mid-install turned out healthy and is now complete.",
        }.get(item.action, item.action)
        print(verb)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vool-update")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-dir", default="", help="user-data dir (default: the active VOOL data dir)")
    common.add_argument("--platform", default="", help="override platform key (default: this machine)")

    check = sub.add_parser("check", parents=[common], help="check for an update (never installs)")
    check.add_argument("--channel", default=None, choices=("stable", "beta"))
    # Optional on purpose: cmd_check reads the feed from the shipped config (and the
    # VOOL_UPDATE_* staging overrides), never from this flag. Requiring it argparse-blocked
    # the documented honest state — a feed-less install must be able to run `check`, learn
    # "Updates are unavailable" and exit 1, not be rejected for a missing URL.
    check.add_argument("--manifest-url", default="")
    check.set_defaults(func=cmd_check)

    status = sub.add_parser("status", parents=[common], help="print the current update status")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)

    applyp = sub.add_parser("apply", parents=[common], help="install the ready update (requires an explicit press)")
    applyp.add_argument("--manifest-url", required=True)
    applyp.add_argument("--app-path", required=True, help="the .app bundle to replace")
    applyp.add_argument("--i-pressed-update", action="store_true", help="the explicit user gesture")
    applyp.add_argument("--health-url", default="http://127.0.0.1:11435/healthz")
    applyp.add_argument("--pid-file", default="", help="pidfile of the running app (for shutdown)")
    applyp.add_argument("--relaunch-cmd", default="", help="explicit relaunch command (default: open -n <app>)")
    applyp.add_argument("--resolve-destructive", default="", help="comma-separated work ids the operator chose to override")
    applyp.add_argument("--because", default="", help="operator's reason for an override")
    applyp.set_defaults(func=cmd_apply)

    restartp = sub.add_parser("restart", parents=[common], help="restart into the downloaded update (requires an explicit press)")
    restartp.add_argument("--i-pressed-update", action="store_true", help="the explicit user gesture")
    restartp.add_argument("--resolve-destructive", default="", help="comma-separated work ids the operator chose to override")
    restartp.add_argument("--because", default="", help="operator's reason for an override")
    restartp.set_defaults(func=cmd_restart)

    recover = sub.add_parser("recover", parents=[common], help="recover an interrupted update at startup")
    recover.add_argument("--app-path", required=True)
    recover.add_argument("--health-url", default="http://127.0.0.1:11435/healthz")
    recover.add_argument("--pid-file", default="")
    recover.set_defaults(func=cmd_recover)

    rollback = sub.add_parser('rollback', parents=[common], help='return to retained previous code; keep current user data')
    rollback.add_argument('--i-pressed-rollback', action='store_true')
    rollback.add_argument('--app-path', default='', help='app bundle or receipt-bound source installation')
    rollback.add_argument('--health-url', default='')
    rollback.add_argument('--wait', action='store_true', help='wait up to 180 seconds for the durable result')
    rollback.set_defaults(func=cmd_rollback)

    for command in (status, applyp, restartp, recover):
        command.add_argument("--channel", default="stable", choices=("stable", "beta"))

    return parser


def main(argv: list[str] | None = None) -> int:
    # C15: the ONE bounded unattended preflight — update operations are unattended runs.
    from core.unattended_preflight import preflight

    preflight("installer.update_cli")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
